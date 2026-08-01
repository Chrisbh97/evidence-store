# Evidence Discovery System — Full Project Context

**Date:** July 28, 2026
**Purpose:** Context document for an agentic model to inspect the system's design, diagnose remaining issues, and propose alternative approaches.

---

## 1. Project Goal

Build a chat-and-inspect system over structured agricultural research papers. The user asks natural-language questions like "What was the effect of phosphorus rate on bread wheat yield?" and receives a verified answer with clickable references back to the source data (experiment cards, tables, observations).

**The fundamental constraint:** The LLM must not hallucinate numbers. Every numeric claim in the answer must be traceable to a specific `measurement_id` in the database that was returned by a tool call during the same conversation turn.

**Database:** DuckDB at `data/evidence_store_v3.duckdb`, ~10 papers, ~11 experiments, ~1,515 measurements. Schema: `papers → experiments → dimensions, observations → measurements`. Each measurement has factor levels stored as JSON in `observations.factor_values_json` (e.g., `{"Nitrogen_rate": "207", "Phosphorus_rate": "46", "Location": "Combined"}`).

---

## 2. Architecture: 3-Layer Verification

### Layer 1 — Prompt Hardening (proactive, advisory)
- Tool descriptions use *answer-dependency framing*: "You need this tool whenever your answer will contain Y" not "Use this when the user asks about X"
- `get_dimension_levels` carries a warning: "CRITICAL: Never average, sum, or compute any statistic on these values"
- Prompt rules prohibit generalization, recommendations, and economic/environmental judgments
- **Weakness:** Cannot prevent hallucination — it's just advice

### Layer 2 — Deterministic Pre-Classifier (pre-LLM, automatic)
Implemented in `engine.py:_pre_classify_and_harvest()` (line 579)
- Pattern-matches question for high-risk keywords: `highest|maximum|yield|compare|effect|rate|...`
- Auto-calls `search_experiments` → `harvest_measurements` (formerly `get_measurements`) for matching papers
- Auto-calls `get_dimension_levels` when rates/levels/treatments/tests are mentioned
- Injects results as pre-LLM context messages so the LLM sees data before it generates
- **Weakness:** Cannot ensure the LLM correctly *uses* the data it sees

### Layer 3 — Evidence Block Validation (post-LLM, gatekeeping)
Implemented in `engine.py:answer()` (lines 670-753)
- LLM writes natural prose + an `Evidence:` block at the end listing each measurement used
- Server parses the block, validates each `measurement_id` against tool results from this turn
- If violations found, sends feedback to LLM for up to `max_tool_rounds - 1` retries
- If all retries exhausted, returns a safe fallback message
- Also rejects responses with measurement units (`kg/ha`, `t/ha`, `%`) but no Evidence block at all
- Valid entries get auto-linked: matching numbers in prose → `<ref type="measurement">` tags
- **Weakness:** Catches wrong IDs but cannot catch *correct IDs with wrong values* (e.g., cites M-0720 but says it's 4,996 kg/ha when M-0720 is actually 4,470 kg/ha)

---

## 3. Key Files

| File | Lines | Role |
|------|-------|------|
| `src/evidence_store/engine.py` | 753 | `ResearchEngine` — 3-layer verification, Evidence block flow, pre-classifier |
| `src/evidence_store/tools.py` | 808 | 9 tools + SCHEMA descriptions + TOOL_IMPLS dispatch |
| `src/evidence_store/server.py` | 116 | FastAPI server: POST /ask, GET /api/experiments/, GET / (static frontend) |
| `src/evidence_store/static/index.html` | 286 | Single-page frontend: chat UI, experiment cards, ref chip rendering |
| `src/evidence_store/session.py` | 48 | `ResearchSession` — lightweight state (scoped IDs, conversation window) |
| `src/evidence_store/agent.py` | 19 | Backward-compat wrapper around `ResearchEngine` |
| `GET_MEASUREMENTS_REDESIGN.md` | 245 | Prior design doc focused on the `get_measurements` tool redesign |
| `data/evidence_store_v3.duckdb` | — | DuckDB database (binary) |

---

## 4. Problem History — Everything We've Tried

### 4.1 The Original Problem: Flat-List Hallucination

**What we had:** `get_measurements` returned a flat list of 40–60 measurement records per experiment. Each row contained `factor_values` as a JSON dict alongside `computed_value`.

```
{"computed_value": 4470.0, "factor_values": {"P_rate": "46", "N_rate": "207", "Location": "Combined"}, ...}
{"computed_value": null,    "factor_values": {"P_rate": "69", "N_rate": "207", "Location": "Combined"}, ...}
{"computed_value": 4891.0, "factor_values": {"P_rate": "46", "N_rate": "253", "Location": "Combined"}, ...}
```

**What the LLM did:** To answer "yield at 69P + 207N," the LLM pattern-matched by proximity in the JSON array (seeing 4470 near 69 in the list) instead of checking the actual `factor_values`. Result: "Yield at 69 kg P/ha: 4,470 kg/ha" — wrong. 4,470 is at 46P, not 69P.

**Root cause diagnosis:** The tool exposed *storage semantics* (flat relation) and forced the LLM to do a relational lookup (`SELECT ... WHERE P_rate=X AND N_rate=Y`) — a task LLMs are demonstrably bad at.

### 4.2 First Attempt: `<val>` Tag System

**What we tried:** We introduced a `<val>` tag mechanism. The LLM was supposed to wrap every reported value in `<val id="M-0720" value="4470" unit="kg/ha"/>` tags. The server would then look up the measurement_id in the tool results and replace the tag with the correct rendered value.

**Why it failed:** The LLM invented measurement_ids that didn't exist in the database/tool results. When told to "look up M-0720 and render its value," it sometimes made up M-9999 instead. The problem shifted from "hallucinate the value" to "hallucinate the ID." The validator caught this but the UX was terrible — every other answer was "your IDs don't match, try again."

### 4.3 Second Attempt: Evidence Block with Prose + Measurement List

**What we tried:** Replaced `<val>` tags with the Evidence block pattern. LLM writes natural prose plus a separate block listing measurement IDs:
```
Evidence:
[1] M-0720 · 4,470 kg/ha · P-04_E-1 · Table 4
```

**What improved:** The validator could check measurement IDs against tool results. The prose was natural (no tags cluttering it). Auto-linking matched prose numbers to Evidence entries by value proximity and inserted `<ref>` tags for clickability.

**What didn't improve:** The LLM still hallucinated which measurement_id corresponded to which factor level. The Evidence block caught *wrong IDs* but couldn't catch the *right ID with wrong description* — the LLM would write "At 69P the yield was 4,470 kg/ha" and cite M-0720 (which is actually 46P). The ID exists in tool results, so Layer 3 passes. But the *attribute binding* is wrong.

**Key insight from this failure:** Layer 3 only validates that the ID came from a tool call, not that the LLM's description of what that ID represents is correct. The validation is syntactic (ID exists) not semantic (description matches).

### 4.4 Final Attempt: Two-Tool Redesign (Current State)

**Design principle:** Safety must be *lexical* — the tool name itself signals the contract. A parameter that toggles safety mode (raw list vs. grouped response) invites the same forgetting failure that caused the original bug.

**The two tools:**

| Tool | Purpose | LLM may cite? | Returns |
|------|---------|---------------|---------|
| `get_measurements` | Safe response surface | Yes | Grouped by `varying_factor`, or `ambiguous` with missing factors |
| `harvest_measurements` | Context-only raw access | **No** (prompt forbids it) | Flat list (same as old `get_measurements`) |

**`get_measurements` redesign:**
- **New required param:** `varying_factor` — the factor to compare across (e.g., `Phosphorus_rate`)
- **New required concept:** `factor_filters` — pin all non-varying factors to specific levels
- **Outcome 1 (Complete):** All non-varying factors pinned → response surface (one row per level of varying_factor)
- **Outcome 2 (Ambiguous):** Missing factors → returns `ambiguous` with `missing_factors` array showing available levels for each unpinned factor
- **Outcome 3 (Error):** No `varying_factor` → error: "varying_factor is required"
- **Added:** `recognized_filters` / `unrecognized_filters` in every response (no silent semantic drift)
- **Added:** `sort_by` = `value_asc` / `value_desc` (DB-level sort)

**`harvest_measurements` — context-only safety:**
- Layer 2 pre-classifier uses this to inject data context
- Prompt says: "NEVER cite a number from this tool's output"
- Evidence block validator checks IDs from this tool too (known gap — see section 5.3)

**Example flow:**
```
User: "Show me yield by phosphorus rate in P-04 at 207N combined locations"
LLM: get_measurements(experiment_id="P-04_E-1", varying_factor="Phosphorus_rate",
      factor_filters={"Nitrogen_rate": "207", "Location": "Combined"})
Tool: → {"results": [{"level": "46", "computed_value": 4470, ...}, {"level": "69", ...}]}
LLM: "At 46 kg P₂O₅/ha, yield was 4,470 kg/ha. At 69 kg P₂O₅/ha, no data."
```

**Why this fixes the original hallucination:** The LLM no longer reads a flat list and simulates SQL. It reads a pre-grouped response surface where each row already has the correct attribute binding (level → value). The GROUP BY is done in SQL, which is deterministic.

**New failure mode discovered** (see section 5.2): The LLM now needs to know the full factor structure of each experiment to construct valid `get_measurements` calls. If it doesn't know what other factors exist, it gets `ambiguous` and can't proceed.

---

## 5. Known Unsolved Problems

### 5.1 Cross-Study Comparison Blocked

**Question that fails:** "Compare the phosphorus rates evaluated across these studies."

**Failure trace:**
1. `search_experiments(manipulated_factors=Phosphorus_rate)` → 4 experiments (P-01, P-04, P-05, P-10)
2. LLM tries `search_experiments(paper_id=P-04, manipulated_factors=Phosphorus_rate)` → 0 (suspected bug with string vs array parameter)
3. LLM falls back to `get_dimension_levels(paper_id=P-04, dimension_name=Phosphorus_rate)` → gets the levels
4. LLM wants to get yield-by-phosphorus-rate for each experiment, but `get_measurements` requires pinning all non-varying factors. Different experiments have different factor structures. No common pin exists.
5. LLM cannot produce an Evidence-block-validatable answer → fallback "no verified answer"

**Why this is hard:** The `get_measurements` tool is inherently per-experiment (requires `experiment_id`). Cross-study comparison requires either:
- Multiple `get_measurements` calls (one per experiment), each with different factor_filters
- A batch endpoint that accepts multiple experiment_ids
- Or accepting that the LLM answers using only `get_dimension_levels` (treatment rates don't need Evidence entries per the prompt — but the LLM doesn't realize this)

**Diagnosis:**
- The LLM conflated "compare phosphorus rates" (a treatment-level comparison that needs only dimension levels) with "compare yield responses to phosphorus" (a measurement comparison that needs `get_measurements`)
- Even for the latter, the LLM lacks a way to discover the full factor structure of an experiment in one call (must call `get_dimension_levels` per paper, then filter per experiment)

### 5.2 Factor Structure Discovery Gap

**Problem:** To call `get_measurements(experiment_id="X", varying_factor="Y", factor_filters={...})`, the LLM must know ALL factors for experiment X. Currently it can:
1. `get_dimension_levels(dimension_name="some_factor", paper_id="P-04")` — returns levels for one factor across all experiments in the paper
2. Call `get_experiment_context("P-04_E-1")` — returns metadata but NOT dimensions

**Missing:** A tool or endpoint that returns "here are ALL factors (names, roles, levels) for experiment P-04_E-1" in one call. Without this, the LLM must make multiple probes to discover the factor structure, which it often fails to do correctly.

### 5.3 Evidence Block: Cannot Detect Attribute-Binding Errors

**Problem:** The Evidence block validator checks that measurement IDs exist in tool results. But the LLM can write:
```
At 69P, yield was 4,470 kg/ha.

Evidence:
[1] M-0720 · 4,470 kg/ha · P-04_E-1 · Table 4
```

M-0720 IS in the tool results (returned by `harvest_measurements`), and 4,470 IS the value of M-0720. But M-0720 represents yield at **46P**, not 69P. The validator passes because: (a) the ID is real, (b) the value matches, (c) it does not check factor-level context.

**This is the same attribute-binding hallucination as the original bug,** just shifted to the `harvest_measurements` context data. If `get_measurements` (safe tool) was used instead, the response surface has level+value as a single row, making this error impossible. The vulnerability exists when:
- The LLM cites from `harvest_measurements` (which the prompt forbids but the validator allows)
- The LLM uses `get_measurements` correctly but mis-describes the level context in prose (e.g., calls with `varying_factor=Phosphorus_rate, factor_filters={Nitrogen_rate: 207}` but then writes "138N" in prose)

### 5.4 `search_experiments` Parameter Fragility

**Problem:** When `manipulated_factors` is a single-element list, some LLMs pass a bare string `"Phosphorus_rate"` instead of `["Phosphorus_rate"]`. The function iterates over characters: `"P", "h", "o", "s", "p", "h", "o", "r", "u", "s", "_", "r", "a", "t", "e"` — 15 params for the SQL IN clause. The query returns 0 results because no dimension matches this impossible list.

**Possible fixes:**
- Accept both string and list in the function (duck-type: `if isinstance(v, str): v = [v]`)
- Add a schema-level `minItems: 1` constraint + rely on API validation

### 5.5 Prompt Ambiguity: Treatment Levels vs. Measured Outcomes

**Problem:** The prompt says: "Treatment level values (rates tested, doses, variety names) do NOT need Evidence entries — they are experimental inputs, not measured outcomes."

But the LLM still gets confused when a question asks about "compare rates" — it tries to use `get_measurements` instead of `get_dimension_levels`, then fails when it can't pin factors, and produces no answer.

**The prompt says Treatment levels don't need Evidence, but doesn't give the LLM a clear decision tree** for "when do I use `get_dimension_levels` vs `get_measurements` vs both."

### 5.6 Dead Code

The following methods in `engine.py` are vestiges of the `<val>` tag era and are never called:
- `_find_bare_numbers()`
- `_collect_factor_levels()`
- `_format_validation_errors()`
- `_parse_val_tags()`
- `_validate_val_tags()`

---

## 6. Design Philosophy (Converged Principles)

### Tool Semantics
Tools should expose **retrieval semantics** not **storage semantics**. Instead of "here are all measurements," they should answer "retrieve the measurements matching these scientific constraints."

### Safety is Lexical
The tool name itself must signal the contract. A parameter that toggles between safe and raw mode invites the same forgetting failure that caused the original bug. Two distinct tools (`get_measurements` vs `harvest_measurements`) is safer than one tool with a `safe_mode: bool` parameter.

### The LLM is the Mouth, Not the Brain
The LLM translates between user language and tool parameters. It never:
- Computes statistics (averages, sums, etc.)
- Fills in missing data
- Makes "judgment calls" about default values
- Simulates SQL operations over flat JSON arrays

When the tool returns `ambiguous`, the LLM relays the available options verbatim. When it returns a response surface, the LLM reads the values and reports them. No reasoning over data, only translation.

### Filter Transparency
Every tool that accepts `factor_filters` must return `recognized_filters` and `unrecognized_filters` in the response envelope. No silent semantic drift.

### Fail Closed
When the Evidence block has invalid entries after `max_tool_rounds - 1` retries, the system returns a generic fallback message rather than passing through an unverifiable answer.

---

## 7. Open Questions (Inviting Fresh Ideas)

### 7.1 How should cross-study comparison work?

Two options we've considered (both have trade-offs):

**A) Teach the LLM to make multiple `get_measurements` calls** (one per experiment) and synthesize. Pro: no new tools. Con: expensive (many LLM rounds), the LLM must handle different factor structures per experiment.

**B) Build a batch comparison tool.** Something like `compare_across_experiments(metric=..., varying_factor=..., experiment_ids=[...], factor_filters={...})` that normalizes factor structures. Pro: single call, deterministic. Con: what happens when experiments have incompatible factor sets? Returns nothing? Merges on overlapping factors only?

**C) Accept that cross-study comparison without measurements is valid.** The LLM answers "compare phosphorus rates" using only `get_dimension_levels` (treatment rates) — no Evidence block needed. Con: limits the depth of comparison (no yield data, just rates).

### 7.2 Should `harvest_measurements` IDs be explicitly prohibited from Evidence blocks?

Currently, the Evidence block validator accepts IDs from ANY tool result, including `harvest_measurements`. The prompt says "never cite from harvest_measurements" but the validator doesn't enforce this.

**Option:** Prefix harvest IDs with `C-` (context-only) so the validator can reject `M-0720` → but accept `C-M-0720`. Pro: enforceable. Con: breaks the flat ID namespace, adds complexity.

### 7.3 How can we detect attribute-binding errors in Evidence blocks?

The Evidence block checks ID-exists and value-matches, but not "does the LLM's prose context for this value match the factor levels in the database?"

**Option:** Store the factor_context in the Evidence block entry, e.g.:
```
[1] M-0720 · 4,470 kg/ha · P-04_E-1 · Table 4 · at 46P+207N+Combined
```
Then the validator could check that the factor levels in the Evidence entry match the database record's `factor_values` for that measurement_id. But this adds parsing complexity and the LLM would need to extract factor context for each measurement.

### 7.4 Is the `get_measurements` ambiguous flow good UX?

"Compare P rates" → `ambiguous` → "Which N rate?" → "207" → "Only 46P has data at 207N" — this is 3 turns for a simple question. Is this acceptable for the target user (agricultural researcher)? Would they prefer a "show me everything and I'll filter" approach (risking hallucination) or the multi-turn narrowing?

### 7.5 Should there be a single "experiment schema" discovery tool?

Currently the LLM must probe with `get_dimension_levels` (per factor) and `get_experiment_context` (no dimensions) to understand an experiment's factor structure. A single `get_experiment_schema(experiment_id)` that returns all factors with roles and levels would close the discovery gap (section 5.2).

### 7.6 Is the two-tool split the right abstraction?

Alternative: a single `get_measurements` that always returns a response surface (no raw mode), with a separate `list_measurements` that returns summary stats (count, metric names, experiment coverage) without individual values. This avoids the "raw data for context" problem entirely — the LLM never sees individual values it could hallucinate from, only what's available.

---

## 8. What We Need From You

Focus areas where fresh thinking would be most valuable:

1. **Cross-study comparison** (section 7.1) — what's the right abstraction?
2. **Attribute-binding detection** (section 7.3) — can we catch the "right ID, wrong prose context" error?
3. **The context-only tool gap** (section 7.2) — should we enforce "never cite from harvest_measurements" at the validator level?
4. **LLM decision tree** (section 5.5) — how to make the "when to use which tool" prompt clearer
5. **Any architectural blind spots** — is the three-layer design fundamentally sound, or is there a better approach?
