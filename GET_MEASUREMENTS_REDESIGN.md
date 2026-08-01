# Evidence Discovery System — Technical Context: `get_measurements` Redesign

## Current Date
July 28, 2026

---

## The Problem

### Observed Failure
The LLM hallucinates attribute bindings when generating answers from `get_measurements` output.

**Concrete example:**
```
LLM wrote:  "Yield at 69 kg P/ha: 4,470 kg/ha (combined locations, 207 kg N/ha)"
Actual DB:  4,470 kg/ha is at 46 kg P/ha, not 69 kg P/ha. 69P returns null.
```

### Root Cause (Diagnosis)
`get_measurements` returns the **base relation** — a flat list of all measurement records (40–60 rows per experiment). Each row is shaped like:

```json
{"computed_value": 4470.0, "factor_values": {"P_rate": "46", "N_rate": "207", "Location": "Combined"}, "measurement_id": "M-0720", ...}
{"computed_value": null,    "factor_values": {"P_rate": "69", "N_rate": "207", "Location": "Combined"}, "measurement_id": "M-0722", ...}
{"computed_value": 4891.0, "factor_values": {"P_rate": "46", "N_rate": "253", "Location": "Combined"}, "measurement_id": "M-0726", ...}
```

To answer "what's the yield at 69P+207N?" the LLM must:
1. Scan 50+ rows
2. Find rows WHERE P_rate=69 AND N_rate=207 AND Location=Combined
3. SELECT computed_value

This is a relational query — `SELECT computed_value FROM measurements WHERE P_rate=69 AND N_rate=207 AND Location=Combined` — which LLMs are demonstrably bad at. The model pattern-matches by proximity in the JSON array (seeing 4470 near 69 in the list) instead of by actual factor values.

### Why the 3-Layer Architecture Didn't Catch This
- **Layer 1 (prompt hardening):** Tells the LLM what to do, can't enforce correctness
- **Layer 2 (pre-classify & harvest):** Ensures data is available, doesn't ensure correct use
- **Layer 3 (Evidence block):** Checks measurement IDs exist, not whether the LLM correctly described what the record says

All three are **detective** — they catch errors after generation. The problem is **architectural** — the tool forces the LLM to do something it can't do reliably.

---

## Proposed Solution: Two-Tool Design

### Design Principle
Tools should expose **retrieval semantics** not **storage semantics**. Instead of "here are all measurements," they should answer "retrieve the measurements matching these scientific constraints."

The safety property must be **lexical** — the tool name itself signals the contract. A parameter that changes safety (raw list vs. grouped response) invites the same forgetting failure that caused the original bug.

### Key Innovation: `varying_factor` Parameter
When the user wants to compare across levels of a factor (e.g., "compare P rates"), the LLM specifies which factor varies. The tool groups observations by that factor deterministically via SQL.

### The Two Tools

#### Tool A: `get_measurements` (safe — LLM may cite from this)
Returns a response surface or `ambiguous` status. Never returns raw flat lists.

```python
get_measurements(
    experiment_id="P-04_E-1",
    metric="Grain yield",
    varying_factor="Phosphorus_rate",
    factor_filters={"Nitrogen_rate": "207", "Location": "Combined"}
)
```

**Outcome 1: Complete** (all non-varying factors pinned in `factor_filters`)

```json
{
  "varying_factor": "Phosphorus_rate",
  "fixed": {"Nitrogen_rate": "207", "Location": "Combined"},
  "recognized_filters": ["Nitrogen_rate", "Location"],
  "unrecognized_filters": [],
  "results": [
    {"level": "0",  "computed_value": null, "measurement_id": null},
    {"level": "46", "computed_value": 4470, "measurement_id": "M-0720"},
    {"level": "69", "computed_value": null, "measurement_id": null},
    {"level": "92", "computed_value": null, "measurement_id": null}
  ]
}
```

The LLM reads: "Only 46P has data. 69P and 92P are null." No hallucination possible.

**Outcome 2: Ambiguous** (missing factor_filters for non-varying factors)

```json
{
  "status": "ambiguous",
  "varying_factor": "Phosphorus_rate",
  "missing_factors": [
    {"name": "Nitrogen_rate", "levels": [69, 115, 161, 207, 253]},
    {"name": "Location", "levels": ["Gerba", "Woyramba", "Combined"]}
  ]
}
```

The LLM relays: "This experiment varied P across 5 N rates and 3 locations. Which N rate and location would you like to compare?"

#### Tool B: `harvest_measurements` (context-only — LLM must NOT cite from this)
Returns the raw flat list. Used by Layer 2 pre-harvesting for context injection. The prompt explicitly forbids quoting numbers from this tool — it exists only to give the LLM a preview of available data so it can form better `get_measurements` calls.

```python
harvest_measurements(
    paper_id="P-04",
    metric="Grain yield"
)
```

Returns the same flat list as the current `get_measurements`. The LLM can see what data exists, then call `get_measurements` with the right parameters to get a citable response.

### Additional Parameters (both tools)
- `sort_by`: `"value_asc"` or `"value_desc"` — orders results by computed_value (DB-level sort, deterministic)
- `limit`: max rows to return

### Filter Transparency
Both tools include `recognized_filters` and `unrecognized_filters` in the response envelope:

```json
{
  "recognized_filters": ["Nitrogen_rate", "Location"],
  "unrecognized_filters": ["Soil_type"],
  ...
}
```

This prevents silent semantic drift: if the LLM requests `factor_filters={"Soil_type": "Vertisol"}` on an experiment without that factor, it sees `unrecognized_filters: ["Soil_type"]` and knows the constraint wasn't applied. No error, no crash — just visibility.

### What We Explicitly Chose NOT To Add
- No `aggregate` parameter (analysis, not retrieval)
- No implicit defaults for missing factors (the LLM shouldn't guess)
- No `collapse_missing_factors` parameter (avoids slippery slope toward analysis operations)
- No single tool with a parameter to toggle safety mode (the parameter-forgetting failure recreates the original bug)

---

## The Role of the LLM (Philosophy)

**The LLM is the mouth, not the brain.** It translates between user language and tool parameters. It never:
- Computes statistics (averages, sums, etc.)
- Fills in missing data
- Makes "judgment calls" about default values
- Simulates SQL operations

When the tool returns `ambiguous`, the LLM relays the available options verbatim. When it returns a response surface, the LLM reads the values and reports them. No reasoning over data, only translation.

---

## Current Codebase State (Before Implementation)

### Key Files
- `src/evidence_store/tools.py` — 9 tools including `get_measurements` (flat-list returns, 3,011 lines of raw output for LLM)
- `src/evidence_store/engine.py` — `ResearchEngine` with 3-layer validation (Evidence block enforcement, pre-classify & harvest)
- `src/evidence_store/static/index.html` — Frontend chat UI with reference chip rendering
- `scripts/load_evidence_store.py` — DB schema creation (papers, experiments, dimensions, observations, measurements tables)
- `data/evidence_store_v3.duckdb` — The database (10 papers, 11 experiments, 1,515 measurements)

### Current `get_measurements` Schema
```python
get_measurements(
    metric: Optional[str],
    experiment_id: Optional[str],
    paper_id: Optional[str],
    factor_filters: Optional[dict],
    limit: int = 500
)
```
Returns flat list: `{"measurements": [{measurement_id, metric, computed_value, unit, statistic, experiment_id, source_id, factor_values, provenance}, ...], "count": N}`

### Evidence Block Enforcement
Implemented in `engine.py`: LLM must include an `Evidence:` block at the end of its response listing measurement IDs used. Server validates IDs exist in tool results and auto-inserts `<ref>` tags matching prose numbers to Evidence entries. If no Evidence block but `kg/ha`/`t/ha`/`%` values detected in prose, the response is rejected with feedback.

---

## What Needs To Be Implemented

### 1. Rewrite `get_measurements` in `tools.py` — Safe Response Surface Tool
- Remove flat-list return
- Add `varying_factor: Optional[str]`
- Add `sort_by: Optional[Literal["value_asc", "value_desc"]]`
- Add filter transparency (`recognized_filters`/`unrecognized_filters` in response)
- Two-branch logic:
  - `varying_factor` set + all other factors in `factor_filters` → GROUP BY varying_factor, return response surface with recognized/unrecognized filters
  - `varying_factor` set + missing factors → query `dimensions` table for levels, return `ambiguous` with `missing_factors`

### 2. Create `harvest_measurements` in `tools.py` — Context-Only Raw Tool
- Same implementation as current `get_measurements` (flat list)
- Returns `{"measurements": [...], "count": N, "recognized_filters": [...], "unrecognized_filters": [...]}`
- Schema description includes: "This tool returns raw measurement data for exploration. NEVER cite numbers from this output directly. Use get_measurements for citable data."

### 3. Update Tool Schemas in `SCHEMA`
- Update `get_measurements` description to reflect safe retrieval semantics
- Add `harvest_measurements` to SCHEMA
- Add `varying_factor`, `sort_by` parameters
- Note filter transparency in parameter descriptions

### 4. Update `TOOL_IMPLS` in `tools.py`
- Add `harvest_measurements` to the implementation map

### 5. Update System Prompt in `engine.py`
- Add rule: "To get citable measurement data, call `get_measurements` with `varying_factor` set to the factor you want to compare across, and all other factors pinned in `factor_filters`"
- Add rule: "If `get_measurements` returns `ambiguous`, present the missing factors to the user and ask which they want"
- Add rule: "Never cite numbers from `harvest_measurements` — use it only for exploration"
- Add rule: "Never invent default values for missing factors"
- Add rule: "If `unrecognized_filters` is non-empty in any response, note to the user that those filter keys were not recognized"

### 6. Update Layer 2 Pre-Classifier (`engine.py`)
- Change `_pre_classify_and_harvest` to call `harvest_measurements` instead of `get_measurements`
- The pre-harvested data still injects into context for exploration, but the LLM is prompted not to cite from it

---

## Edge Cases To Handle

### What if factor_filters includes a factor not in the experiment?
Include it in `unrecognized_filters`. The tool returns the query as if that filter didn't exist, but the LLM can see it wasn't applied. No silent semantic drift.

### What if the varying_factor doesn't exist in the experiment?
Return empty results (no match). `recognized_filters` will show which filters were applied. The LLM reports the factor isn't available.

### What if all results are null?
Return the response surface with all-null values. The LLM reports "No yield data exists for those combinations."

### What if the user asks across experiments (e.g., "Compare P-04 and P-10")?
The LLM calls `get_measurements` twice, once per `experiment_id`. The prompt must teach this pattern.

### What if the LLM tries to cite from `harvest_measurements`?
The Evidence block validation checks measurement IDs against tool results. If the LLM cites a raw-list measurement without calling `get_measurements`, the ID will exist in tool results (from pre-harvesting), so the Evidence block would pass. This is a known gap — the prompt rule "never cite from harvest_measurements" is the only defense. A possible hardening would be to prefix `harvest_measurements` measurement IDs with a marker so the Evidence block validator can distinguish safe vs. context-only IDs.

### What if there are multiple valid fixed-factor combos?
The `ambiguous` response shows all available levels for each missing factor. The LLM presents them to the user as choices.

---

## Remaining Questions (Unsolved)

1. **Multi-turn UX**: "Compare P rates" → "Which N rate?" → "207" → "Only 46P has data" is 3 turns for a simple question. Is this acceptable?

2. **Cross-experiment comparisons**: Do we need a batch endpoint, or is multi-call fine?

3. **Evidence block hardening**: Should `harvest_measurements` IDs be prefixed (e.g., `C-M-0720` for "context-only") so the validator can reject Evidence entries referencing them? Or is the prompt rule sufficient?

4. **Provenance in response surface**: Should each result include `measurement_id` and `provenance` so the Evidence block can reference them?
