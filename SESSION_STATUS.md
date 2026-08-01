# Evidence Discovery System — Session Status

## Project Goal
Build a chat-and-inspect system over structured agricultural research papers. Users ask natural-language questions about experiment results (yields, rates, comparisons) and get verified answers with clickable references back to source tables/figures.

## Core Problem
LLMs hallucinate numbers and cite non-existent records. The system must guarantee every numeric claim is traceable to a real measurement in the database.

---

## Architecture: 3-Layer Verification

### Layer 1 — Prompt Hardening (proactive)
- Tool descriptions use **answer-dependency framing**: "You need this tool whenever your answer will contain Y" not "Use this when the user asks about X"
- `get_dimension_levels` carries `CRITICAL: Never average, sum, or compute statistics on factor levels` in its description
- Prompt rule: "Never generalize beyond these studies; never recommend an action/rate; never assert economic/environmental judgment"

### Layer 2 — Deterministic Pre-Classifier (pre-LLM)
Method: `ResearchEngine._pre_classify_and_harvest()` in `engine.py:545`
- Pattern-matches high-risk question phrases: `highest|maximum|average|yield|compare|effect|optimal|synerg`
- Auto-calls `search_experiments` → `get_measurements` for matching papers
- Calls `get_dimension_levels` when question asks about rates/levels/treatments
- Injects harvested results as pre-LLM context messages

### Layer 3 — Evidence Block Validation (post-LLM)
LLM writes natural prose + an `Evidence:` block at the bottom:
```
The highest yield was 4,996 kg/ha from P-04.

Evidence:
[1] M-0662 · 4,996 kg/ha · P-04_E-1 · Table 4
```
Server flow in `ResearchEngine.answer()`:
1. `_parse_evidence_block()` — split text on `\nEvidence:\n`
2. `_validate_evidence_block()` — check each `measurement_id` against tool result IDs
3. `_link_evidence_to_text()` — match numbers in prose to Evidence entries by value (1% tolerance), insert `<ref type="measurement">` tags
4. Re-attach Evidence block to linked prose
5. If invalid entries + retries exhausted → generic fallback message

---

## Key Files

| File | Role |
|------|------|
| `engine.py` (~715 lines) | `ResearchEngine` — three-layer validation, Evidence block handling, `_finalize_answer` |
| `tools.py` (~578 lines) | 9 tools with answer-dependency descriptions |
| `server.py` (~116 lines) | FastAPI server: POST /ask, GET /api/experiments/, GET /, etc. |
| `static/index.html` (~260 lines) | Single-page frontend with chat UI, experiment cards, reference chips |

---

## Completed
- [x] Three-layer architecture implemented (engine.py: Layer 2 pre-classifier + Layer 3 Evidence block validation)
- [x] All 9 tool descriptions rewritten to answer-dependency framing (tools.py)
- [x] Evidence block parsing/validation/linking working (8/8 unit tests pass)
- [x] `<ref type="measurement">` frontend chip handler exists (basic implementation)
- [x] Fail-closed: invalid evidence → retry → fallback message

## Known Issues
1. [ ] **Crop filter broken** — `_pre_classify_and_harvest` line 601 checks `exp.get("crop")` but `search_experiments` doesn't return a `crop` field (no JOIN to papers table)
2. [ ] **No Evidence block enforcement** — if LLM writes numbers with units (`kg/ha`, `t/ha`, `%`) but no Evidence block, validation passes silently; no verification happens
3. [ ] **Dead code** — `_collect_factor_levels()`, `_find_bare_numbers()`, `_format_validation_errors()`, `_parse_val_tags()`, `_validate_val_tags()` are unused but still in engine.py
4. [ ] **Need end-to-end test** with real LLM + real database
5. [ ] **Frontend** — Evidence block rendered as raw text (could be a collapsible section); measurement ref chips show a basic message

## Recent Session Changes
- Replaced val-tag validation loop with Evidence block flow in `answer()`
- Added `_format_evidence_errors()` for feedback on invalid Evidence entries
- Removed dead val-to-ref conversion code from `_finalize_answer()`
- Fixed Evidence block being dropped after linking (re-attach logic added)
- Wrote and passed 8 unit tests covering parse/validate/link/normalize/bare-numbers

## Next Work
1. Fix crop filter (add `crop` to search_experiments JOIN or results)
2. Add Evidence block enforcement (reject responses with measurement units but no block)
3. Remove dead code
4. End-to-end test with real LLM
5. Frontend polish (Evidence block collapsible, tool trace display)
