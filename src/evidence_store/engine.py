"""
ResearchEngine: wraps the agent loop with session context and citation tracking.
"""

import ast, json, os, re, time
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

from .tools import TOOL_IMPLS, SCHEMA
from .session import ResearchSession

TOOL_DESCRIPTIONS = "\n\n".join(
    f"## {name}\n{s['description']}\n\nParameters: {json.dumps(s['parameters'], indent=2)}"
    for name, s in sorted(SCHEMA.items())
)

BASE_PROMPT = f"""You are an evidence retrieval agent. You have ZERO prior knowledge of the data in this store. Every number, rate, and finding you report MUST come from a tool result. If you haven't called a tool, you cannot know the value — do not guess or write numbers from your training memory.

The evidence store contains structured data extracted from 10 agricultural research papers about phosphorus and nitrogen management. The data is only accessible through tool calls — you have no access to it otherwise.

You have these tools available:

{TOOL_DESCRIPTIONS}

To call tools, respond with one or more <toolcall> blocks on their own lines:
<toolcall> {{"tool": "tool_name", "args": {{"arg1": "value1"}}}} </toolcall>
<toolcall> {{"tool": "tool_name2", "args": {{"arg2": "value2"}}}} </toolcall>

When citing a specific numeric MEASUREMENT from a tool result (yield, mean, etc.), use the Evidence block (see below). Do NOT use <val> tags.

Decision tree for tool selection:
- If the user asks about treatment rates, doses, levels themselves (e.g. "what rates were tested", "compare the phosphorus rates") → use get_experiment_schema or get_dimension_levels. These are experimental inputs, not measured outcomes — they do NOT need Evidence entries.
- If the user asks about measured outcomes (e.g. "what was the yield at each rate", "compare yields") → use get_measurements with varying_factor set to the factor you want to compare across. Add optional factor_filters to narrow to specific levels of other factors. The tool returns all matching rows grouped by the varying factor level, each with its full factor context and measurement_id for the Evidence block.

Treatment level values (rates tested, doses, variety names) do NOT need Evidence entries — they are experimental inputs, not measured outcomes.

Whenever you mention or list an experiment, paper, or source by ID, wrap it in a <ref> tag so the user can click it for details:
  <ref type="experiment" ids="P-04_E-1" label="P-04 · Bread wheat · Vertisol"/>
  <ref type="comparison" ids="P-04_E-1,P-10_E-1" label="P-04 vs P-10 · phosphorus response"/>
  <ref type="source" ids="Table 3" label="Table 3 · Grain yield by treatment" experiment="P-04_E-1"/>
  <ref type="observation" ids="O-0214" label="Observation O-0214"/>
  <ref type="paper" ids="P-04" label="Paper P-04"/>
This applies to every ID mention — do not leave an experiment/source/paper ID as bare text. Always use <ref>.

The label must only contain metadata from tool results (crop, soil type, location, source name). Never include a numeric value or measurement in the label.

Only emit <ref> tags for IDs that were actually returned by a tool call during this answer — never invent IDs.

If you're unsure which experiment or paper the user means by "this one", "that one", "it", or similar ambiguous references, ask for clarification. Never guess.

After your prose answer, list each measurement_id you used as an Evidence block:

Evidence:
[1] M-1244
[2] M-0662

Every number you report in the prose must correspond to an entry in the Evidence block. The block is checked — entries with IDs not returned by your tools will be stripped.

Treatment level values (rates tested, doses, variety names) do NOT need Evidence entries — they are experimental inputs, not measured outcomes.

Rules about MEASUREMENT DATA:

- To get measurement data, call get_measurements with varying_factor set to the factor you want to compare across. You can optionally narrow to specific levels of other factors via factor_filters. The tool returns all matching rows grouped by the varying factor level — each row includes the full factor context (factor_values) and a measurement_id you can cite in the Evidence block.
- If any tool response includes non-empty unrecognized_filters, note to the user that those filter keys were not recognized for that experiment.

General rules:
- Every specific measured number you report — yield amount, mean, percentage — must have a corresponding entry in the Evidence block with the correct measurement_id.
- Each Evidence entry's ID must match a record returned by a tool call during this answer.
- CRITICAL: Never write a number from your training knowledge. Every yield value, percentage, or statistic you state MUST come from a tool result in this conversation. If you don't have the data, say "I don't have data for that" — do not make up plausible numbers.
- Cite paper IDs and source identifiers when reporting findings.
- Use multiple tool calls when a question requires gathering information from different tools. When making claims that span multiple experiments, your tool calls must return data for all of them — a single call filtered to one experiment does not support claims about others.
- When comparing studies, every synthesized claim must name which specific returned field(s) it is built from (e.g., "P-04's yield at 46P+253N vs. P-10's yield at 92P+138N"). A claim may characterize what the compared studies show, together or in tension. It must never: (a) generalize beyond the specific studies compared into a category-wide rule, (b) recommend an action or rate, (c) assert an economic or environmental judgment. If the user asks directly for a recommendation, state plainly that the evidence store reports findings not agronomic advice, and offer to show the comparison instead.
- Never average, sum, or compute statistics on factor levels returned by get_dimension_levels. Those are treatment inputs (what was tested), not outcome values (what was measured). Computing an "average phosphorus rate" across experiments is meaningless — averaging settings across different experimental designs does not produce a useful number."""


@dataclass
class Reference:
    type: str      # "experiment", "source", "comparison", "observation", "paper"
    ids: list[str]
    label: str


@dataclass
class ToolCall:
    tool: str
    args: dict
    result_summary: str  # e.g. "4 experiments found" or "0 results"


@dataclass
class Answer:
    text: str
    citations: list[dict] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    tool_trace: list[dict] = field(default_factory=list)
    # Each citation: {observation_id, source_id, page_number, paper_id, experiment_id}


def _extract_citations(result: dict) -> list[dict]:
    """Pull observation-level citations from a tool result."""
    cites = []
    if "measurements" in result:
        for m in result["measurements"]:
            cite = {"observation_id": None, "source_id": m.get("source_id"),
                    "page_number": None, "paper_id": None,
                    "experiment_id": m.get("experiment_id")}
            fv = m.get("factor_values", {})
            if fv:
                cite["provenance"] = m.get("provenance")
            cites.append(cite)
    if "observation_id" in result:
        cites.append({
            "observation_id": result.get("observation_id"),
            "source_id": result.get("source_id"),
            "page_number": result.get("page_number"),
            "paper_id": result.get("experiment_id", "").split("_")[0] if result.get("experiment_id") else None,
            "experiment_id": result.get("experiment_id"),
        })
    if "observations" in result:
        for o in result["observations"]:
            cites.append({
                "observation_id": o.get("observation_id"),
                "source_id": result.get("source_id"),
                "page_number": result.get("page_number"),
                "paper_id": result.get("experiment_id", "").split("_")[0] if result.get("experiment_id") else None,
                "experiment_id": result.get("experiment_id"),
            })
    return cites


class ResearchEngine:
    def __init__(self, session: Optional[ResearchSession] = None,
                 model: Optional[str] = None,
                 api_key: Optional[str] = None,
                 api_base: Optional[str] = None,
                 verbose: bool = False,
                 min_interval: float = 15.0):
        self.session = session
        self._api_key = api_key
        self._api_base = api_base
        self.model = model or os.getenv("EVIDENCE_MODEL") or "mistral-large-latest"
        self._client = None
        self.verbose = verbose
        self._min_interval = min_interval
        self._last_call = 0.0

    def _log(self, msg: str):
        if self.verbose:
            import sys
            print(f"[engine] {msg}", file=sys.stderr)

    def _get_client(self):
        if self._client is None:
            self._client = OpenAI(
                api_key=self._api_key or os.getenv("MISTRAL_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY"),
                base_url=self._api_base or os.getenv("MISTRAL_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("API_BASE_URL") or "https://api.mistral.ai/v1",
            )
        return self._client

    def _build_messages(self, question: str) -> list[dict]:
        prompt = BASE_PROMPT
        if self.session:
            ctx = self.session.to_context()
            if ctx and ctx != "(no context yet)":
                prompt += f"\n\nCurrent investigation context:\n{ctx}"
        messages = [{"role": "system", "content": prompt}]
        # Inject recent conversation window for anaphora resolution
        if self.session:
            for turn in self.session.conversation_window:
                messages.append(turn)
        messages.append({"role": "user", "content": question})
        return messages

    def _call_llm(self, messages: list[dict]) -> str:
        elapsed = time.time() - self._last_call
        if elapsed < self._min_interval:
            wait = self._min_interval - elapsed
            self._log(f"Rate limit: waiting {wait:.1f}s (last call {elapsed:.1f}s ago)")
            time.sleep(wait)
        self._last_call = time.time()
        response = self._get_client().chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.1,
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _summarize_result(result: dict) -> str:
        """Short human-readable summary of a tool result."""
        if "error" in result:
            return f"error: {result['error'][:60]}"
        # Check common result keys
        for key, label in [("experiments", "experiments"), ("papers", "papers"),
                            ("measurements", "measurements"), ("results", "results"),
                            ("dimensions", "dimensions"), ("observations", "observations"),
                            ("response_variables", "response variables")]:
            items = result.get(key)
            if items is not None:
                count = len(items)
                return f"{count} {label} found" if count else f"0 {label}"
        # Singleton results (get_experiment_context, trace_observation, etc.)
        if "experiment_id" in result:
            return f"experiment {result['experiment_id']}"
        if "observation_id" in result:
            obs_id = result["observation_id"]
            meas_count = result.get("measurement_count", 0)
            return f"observation {obs_id} ({meas_count} measurements)"
        if "source_id" in result:
            sid = result["source_id"]
            obs_count = result.get("observation_count", 0)
            return f"source {sid} ({obs_count} observations)"
        return f"{len(result)} fields"

    @staticmethod
    def _parse_json(value: str):
        """Parse JSON or Python dict literal from LLM output."""
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
        # Handle null → None, true/false → True/False for ast.literal_eval
        s2 = value.replace('null', 'None').replace('true', 'True').replace('false', 'False')
        try:
            return ast.literal_eval(s2)
        except (ValueError, SyntaxError, MemoryError):
            raise ValueError(f"Cannot parse: {value[:100]}")

    def _parse_toolcalls(self, text: str) -> tuple[list[dict], str]:
        """Parse all toolcalls from text. Supports <toolcall> blocks and tool_name {args} shorthand.
        Returns (list_of_specs, text_with_toolcalls_stripped)."""
        # Primary format: <toolcall> {"tool": "...", "args": {...}} </toolcall>
        pattern = r'<toolcall>\s*(\{.*?\})\s*</toolcall>'
        specs = []
        for m in re.finditer(pattern, text, re.DOTALL):
            try:
                spec = self._parse_json(m.group(1))
                if "tool" in spec:
                    specs.append(spec)
            except ValueError:
                pass
        if specs:
            remaining = re.sub(pattern, '', text, flags=re.DOTALL)
            return specs, remaining.strip()

        # Fallback: tool_name {"arg1": "val1", "arg2": "val2"} on its own line
        func_pattern = r'(?:^|\n)\s*(\w+)\s+(\{.*?\})\s*(?:\n|$)'
        for m in re.finditer(func_pattern, text):
            try:
                args = self._parse_json(m.group(2))
                specs.append({"tool": m.group(1), "args": args})
            except ValueError:
                pass
        if specs:
            remaining = re.sub(func_pattern, '', text)
            return specs, remaining.strip()

        return [], text

    def _parse_refs(self, text: str) -> tuple[list[dict], str]:
        """Extract <ref> tags from text. Returns (parsed_refs, text_with_tags_stripped)."""
        refs = []
        def _replace(m):
            tag = m.group(0)
            try:
                t = m.group(1)
                ids_raw = m.group(2)
                label = m.group(3)
                ids = [x.strip() for x in ids_raw.split(",") if x.strip()]
                refs.append({"type": t, "ids": ids, "label": label})
            except Exception:
                pass
            return ""
        cleaned = re.sub(
            r'<ref\s+type="(\w+)"\s+ids="([^"]+)"\s+label="([^"]*)"\s*/>',
            _replace, text
        )
        return refs, cleaned

    @staticmethod
    def _collect_known_ids(tool_results: list[tuple[str, dict, dict]]) -> set[str]:
        """Union of all object IDs seen in tool results for this turn."""
        known = set()
        for _name, _args, result in tool_results:
            # search_experiments
            for exp in result.get("experiments", []):
                eid = exp.get("experiment_id")
                if eid:
                    known.add(eid)
                pid = exp.get("paper_id")
                if pid:
                    known.add(pid)
            # list_papers
            for p in result.get("papers", []):
                pid = p.get("paper_id")
                if pid:
                    known.add(pid)
            # get_measurements
            for m in result.get("measurements", []):
                mid = m.get("measurement_id")
                if mid:
                    known.add(mid)
                eid = m.get("experiment_id")
                if eid:
                    known.add(eid)
                sid = m.get("source_id")
                if sid:
                    known.add(sid)
            # get_measurements (new response surface format)
            for r in result.get("results", []):
                mid = r.get("measurement_id")
                if mid:
                    known.add(mid)
                eid = r.get("experiment_id")
                if eid:
                    known.add(eid)
            # get_significant_findings
            for r in result.get("results", []):
                eid = r.get("experiment_id")
                if eid:
                    known.add(eid)
                pid = r.get("paper_id")
                if pid:
                    known.add(pid)
            # get_dimension_levels
            for d in result.get("dimensions", []):
                eid = d.get("experiment_id")
                if eid:
                    known.add(eid)
            # get_experiment_context
            eid = result.get("experiment_id")
            if eid:
                known.add(eid)
            pid = result.get("paper_id")
            if pid:
                known.add(pid)
            # trace_observation
            oid = result.get("observation_id")
            if oid:
                known.add(oid)
            eid = result.get("experiment_id")
            if eid:
                known.add(eid)
            sid = result.get("source_id")
            if sid:
                known.add(sid)
            # get_source_observations
            sid = result.get("source_id")
            if sid:
                known.add(sid)
            eid = result.get("experiment_id")
            if eid:
                known.add(eid)
            for o in result.get("observations", []):
                oid = o.get("observation_id")
                if oid:
                    known.add(oid)
            # list_response_variables — no IDs, just names
        return known

    def _validate_refs(self, refs: list[dict], tool_results: list[tuple[str, dict]]) -> list[dict]:
        """Strip refs whose IDs weren't returned by any tool call this turn."""
        known = self._collect_known_ids(tool_results)
        validated = []
        for ref in refs:
            missing = [rid for rid in ref["ids"] if rid not in known]
            if not missing:
                validated.append(ref)
            else:
                self._log(f"Stripping <ref> with unknown IDs: {missing}")
        return validated

    @staticmethod
    def _build_measurement_map(tool_results: list[tuple[str, dict, dict]]) -> dict:
        """Build {measurement_id: computed_value} from all tool results."""
        mapping = {}
        for _name, _args, result in tool_results:
            # result["measurements"][...]
            for m in result.get("measurements", []):
                mid = m.get("measurement_id")
                cv = m.get("computed_value")
                if mid and cv is not None:
                    mapping[mid] = cv
            # get_measurements shape: result["results"][...]
            for r in result.get("results", []):
                mid = r.get("measurement_id")
                cv = r.get("computed_value")
                if mid and cv is not None:
                    mapping[mid] = cv
        return mapping

    @staticmethod
    def _normalize_number(text: str) -> Optional[float]:
        """Parse a number from text, stripping commas and units."""
        text = text.strip()
        text = text.replace(",", "").replace("−", "-").replace("–", "-")
        m = re.match(r'(-?\d+\.?\d*)', text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
        return None

    @staticmethod
    def _parse_evidence_block(text: str) -> tuple[str, list[dict]]:
        """Split text into prose and Evidence block. Returns (prose, entries).
        Each entry: {label, ref, value_text}
        Evidence block is detected by scanning for [N] M-XXXX lines — any
        heading format (plain, bold, italic, markdown heading) is discarded.
        """
        lines = text.split('\n')
        entry_start = None
        for i, line in enumerate(lines):
            if re.match(r'\[(\d+)\]\s*(M-\d+)', line.strip()):
                entry_start = i
                break

        if entry_start is None:
            return text, []

        prose_lines = lines[:entry_start]
        while prose_lines and (prose_lines[-1].strip() == '' or
              re.match(r'^[\s#\*]*Evidence[\s#\*:]*$', prose_lines[-1].strip(), re.IGNORECASE)):
            prose_lines.pop()

        block = lines[entry_start:]
        entries = []
        for line in block:
            m = re.match(r'\[(\d+)\]\s*(M-\d+)', line.strip())
            if m:
                entries.append({
                    "label": m.group(1),
                    "ref": m.group(2),
                    "value_text": "",
                })

        return '\n'.join(prose_lines).strip(), entries

    @staticmethod
    def _validate_evidence_block(entries: list[dict], measurement_map: dict) -> tuple[list[dict], list[str]]:
        """Check each Evidence entry: ref exists in tool results. Returns (valid_entries, error_messages)."""
        valid = []
        errors = []
        for e in entries:
            ref = e["ref"]
            if ref not in measurement_map:
                errors.append(f'Evidence [{e["label"]}] ref="{ref}" — ID not returned by any tool call this turn. Value: "{e["value_text"]}"')
            else:
                valid.append(e)
        return valid, errors

    @staticmethod
    def _link_evidence_to_text(prose: str, valid_entries: list[dict]) -> str:
        """Post-process prose: find numbers matching Evidence entries, insert <ref> tags.
        Matching is by numeric value proximity within a context window."""
        if not valid_entries:
            return prose
        # Build value→entry map (first match wins for duplicates)
        value_map = {}
        for e in valid_entries:
            num = ResearchEngine._normalize_number(e["value_text"])
            if num is not None and num not in value_map:
                value_map[num] = e

        def _replace_number(m):
            full = m.group(0)
            num_str = m.group(1) or m.group(3)
            if not num_str:
                return full
            num_str = num_str.replace(",", "")
            try:
                num_val = float(num_str)
            except ValueError:
                return full
            # Find closest match within 1%
            best = None
            best_dist = float('inf')
            for cand_val, entry in value_map.items():
                dist = abs(num_val - cand_val) / max(abs(cand_val), 0.01)
                if dist < 0.01 and dist < best_dist:
                    best = entry
                    best_dist = dist
            if best:
                return f'<ref type="measurement" ids="{best["ref"]}" label="{full}"/>'
            return full

        # Match kg/ha, t/ha with word boundary; match % without trailing boundary
        pattern = r'\b(\d[\d,.]*)\s*(kg/ha|kg\s*ha[⁻¹ⁱ]|t/ha)\b|\b(\d[\d,.]*)\s*%'
        return re.sub(pattern, _replace_number, prose)

    @staticmethod
    def _format_evidence_errors(errors: list[str]) -> str:
        """Build feedback message when Evidence block has invalid entries."""
        parts = ["Your answer included an Evidence block with IDs that are not valid for citation. Fix these issues and rewrite:"]
        for e in errors:
            parts.append(f"  * {e}")
        parts.append("")
        parts.append("Only measurement_ids from get_measurements can appear in the Evidence block. If you don't have the data, say so instead of writing a number or including a fake entry.")
        return "\n".join(parts)

    @staticmethod
    def _prose_has_measurement_numbers(text: str) -> bool:
        """Check if text contains numbers with measurement units."""
        if re.search(r'\b\d[\d,.]*\s*(kg/ha|kg\s*ha[⁻¹ⁱ]|t/ha)\b', text):
            return True
        if re.search(r'\b\d[\d,.]*\s*%', text):
            return True
        return False

    def _update_session(self, results: list[tuple[str, dict, dict]]):
        """Update session state from tool call results."""
        if not self.session:
            return
        for tool_name, _args, result in results:
            if tool_name == "search_experiments":
                for exp in result.get("experiments", []):
                    self.session.focus_paper(exp.get("paper_id"))
                    self.session.focus_experiment(exp.get("experiment_id"))
            elif tool_name in ("get_measurements", "get_significant_findings"):
                items = result.get("measurements", []) or result.get("results", [])
                for item in items:
                    eid = item.get("experiment_id")
                    if eid:
                        self.session.focus_experiment(eid)
            elif tool_name == "get_experiment_context":
                eid = result.get("experiment_id")
                if eid:
                    self.session.focus_experiment(eid)
            elif tool_name == "get_source_observations":
                eid = result.get("experiment_id")
                if eid:
                    self.session.focus_experiment(eid)
            elif tool_name == "get_dimension_levels":
                for d in result.get("dimensions", []):
                    eid = d.get("experiment_id")
                    if eid:
                        self.session.focus_experiment(eid)

    @staticmethod
    def _collect_factor_levels(tool_results: list[tuple[str, dict, dict]]) -> set[float]:
        """Collect all factor level values from get_dimension_levels results."""
        levels = set()
        for _name, _args, result in tool_results:
            for d in result.get("dimensions", []):
                for level_str in d.get("levels", []):
                    try:
                        levels.add(float(level_str))
                    except (ValueError, TypeError):
                        pass
        return levels

    def _finalize_answer(self, content: str, tool_results: list[tuple[str, dict, dict]],
                          all_citations: list[dict], question: str, messages: list[dict]) -> Answer:
        """Shared finalization logic: parse/validate refs, build Answer."""
        refs_raw, _ = self._parse_refs(content)
        refs_valid = self._validate_refs(refs_raw, tool_results)
        if refs_raw and not refs_valid:
            self._log(f"All {len(refs_raw)} refs stripped — IDs not in tool results")
        valid_ids_set = {tuple(r["ids"]) for r in refs_valid}
        text_with_valid = content
        if len(refs_valid) < len(refs_raw):
            def _strip_invalid(m):
                ids = tuple(x.strip() for x in m.group(2).split(",") if x.strip())
                return m.group(0) if ids in valid_ids_set else ""
            text_with_valid = re.sub(
                r'<ref\s+type="(\w+)"\s+ids="([^"]+)"\s+label="([^"]*)"\s*/>',
                _strip_invalid, content
            )
        ref_objs = [Reference(**r) for r in refs_valid]
        trace = [{"tool": name, "args": a, "summary": self._summarize_result(res)}
                 for name, a, res in tool_results]
        if self.session:
            self._update_session(tool_results)
            self.session.add_turn(question, text_with_valid)
        return Answer(text=text_with_valid, citations=all_citations,
                      references=ref_objs, tool_trace=trace)

    @staticmethod
    def _make_fallback(question: str) -> str:
        """Safe fallback when validation retries are exhausted."""
        return ("I wasn't able to produce a verified numeric answer for this question. "
                "The evidence store confirms relevant data exists, but I could not "
                "produce a specific value that passed verification. You can inspect "
                "the tool trace to see what data was retrieved.")

    @staticmethod
    def _measurement_lookup(measurement_id: str) -> dict:
        """Look up measurement display info. Returns dict with display, source_id, experiment_id."""
        from .tools import _db
        con = _db()
        row = con.execute("""
            SELECT m.metric, m.computed_value, m.unit,
                   o.source_id, o.provenance, o.experiment_id
            FROM measurements m
            JOIN observations o ON m.observation_id = o.observation_id
            WHERE m.measurement_id = ?
        """, [measurement_id]).fetchone()
        con.close()
        if not row:
            return {"display": f"{measurement_id} (not found)", "source_id": None, "experiment_id": None}
        metric, val, unit, source_id, provenance, exp_id = row
        val_str = f"{val:,.0f} {unit or ''}".strip() if val is not None else "(no data)"
        ctx = provenance or source_id or ""
        display = f"{ctx}: {metric} = {val_str}"
        return {"display": display, "source_id": source_id, "experiment_id": exp_id}

    def _pre_classify_and_harvest(self, question: str, messages: list[dict],
                                   tool_results: list[tuple[str, dict, dict]],
                                   all_citations: list[dict]) -> bool:
        """Layer 2: deterministic pre-classifier for high-risk question patterns.
        If the question asks about yield values, rates, comparisons, or agreement,
        force-call get_measurements before the LLM generates anything.
        Returns True if pre-harvesting was triggered."""
        from .tools import search_experiments

        # Extract likely factor keywords from the question
        factor_map = {
            r'\bphosphorus\b|\bP\b(?!\d)|P\s*rate|P\s*level|Phosphorus': 'Phosphorus_rate',
            r'\bnitrogen\b|\bN\b(?!\d)|N\s*rate|N\s*level|Nitrogen': 'Nitrogen_rate',
        }
        factors = set()
        for pattern, factor_name in factor_map.items():
            if re.search(pattern, question, re.IGNORECASE):
                factors.add(factor_name)

        # If no specific factor found, check if question is generally about yields/rates
        if not factors:
            yield_pattern = re.compile(r'\byield\b|\bresponse\b|\brate\b|\beffect\b|\bhighest\b|\bmaximum\b|\baverage\b|\bmean\b|\bcompare\b|\boptimal\b|\bsynerg', re.IGNORECASE)
            if yield_pattern.search(question):
                factors = {'Phosphorus_rate', 'Nitrogen_rate'}
            else:
                # High-risk catch-all for remaining question patterns
                high_risk = re.compile(
                    r'(highest|maximum|best|average|mean|compare|agreement|agree|'
                    r'yield|response|rate|effect|optimal|synerg)', re.IGNORECASE
                )
                if not high_risk.search(question):
                    return False
                factors = {'Phosphorus_rate', 'Nitrogen_rate'}

        if not factors:
            return False

        # Determine which tools to call based on question type
        rates_q = re.search(r'\brate\b|\blevel\b|\bdose\b|\btreatment\b|compared|what.*tested|evaluated', question, re.IGNORECASE)
        yield_q = re.search(r'\byield\b|\bresponse\b|\beffect\b|\bhighest\b|\bmaximum\b|\baverage\b|\bmean\b|\bsignificant\b', question, re.IGNORECASE)
        call_measurements = bool(yield_q)
        call_dimensions = bool(rates_q)

        if not call_measurements and not call_dimensions:
            call_measurements = True  # default to measurements

        # Search for experiments matching these factors
        harvest_results = []
        for factor in factors:
            result = search_experiments(manipulated_factors=[factor], stratification_factors=[], paper_id=None)
            harvest_results.append(("search_experiments", {"manipulated_factors": [factor]}, result))
            self._log(f"Layer 2: auto-called search_experiments({factor}) → {result.get('count', 0)} experiments")

        # Collect experiment IDs from harvested experiments
        exp_ids = set()
        paper_ids = set()
        for _, _, result in harvest_results:
            for exp in result.get("experiments", []):
                eid = exp.get("experiment_id")
                if eid:
                    exp_ids.add(eid)
                pid = exp.get("paper_id")
                if pid:
                    paper_ids.add(pid)

        if call_measurements:
            from .tools import get_measurements
            # Try to narrow metric based on question keywords
            metric_filter = None
            if re.search(r'\byield\b', question, re.IGNORECASE):
                metric_filter = 'Grain yield'
            for eid in exp_ids:
                for f in factors:
                    meas = get_measurements(experiment_id=eid, varying_factor=f, metric=metric_filter)
                    harvest_results.append(("get_measurements", {"experiment_id": eid, "varying_factor": f, "metric": metric_filter}, meas))
                    self._log(f"Layer 2: auto-called get_measurements({eid}, {f}) → {meas.get('count', 0)} rows")

        if call_dimensions:
            for pid in paper_ids:
                dims = search_experiments(paper_id=pid, manipulated_factors=list(factors), stratification_factors=[])
                harvest_results.append(("search_experiments", {"paper_id": pid, "manipulated_factors": list(factors)}, dims))
                # Also get dimension levels for each found factor
                from .tools import get_dimension_levels
                for f in factors:
                    dl = get_dimension_levels(dimension_name=f, paper_id=pid)
                    harvest_results.append(("get_dimension_levels", {"dimension_name": f, "paper_id": pid}, dl))
                    self._log(f"Layer 2: auto-called get_dimension_levels({f}, {pid}) → {dl.get('count', 0)} dimensions")

        if not harvest_results:
            return False

        # Inject harvest results as user messages (pre-LLM context)
        for name, args, result in harvest_results:
            tool_results.append((name, args, result))
            all_citations.extend(_extract_citations(result))
            content = f"[REQUIRED DATA — USE THESE VALUES] Tool result for {name}:\n{json.dumps(result, default=str, indent=2)}"
            messages.append({
                "role": "user",
                "content": content
            })

        self._log(f"Layer 2: pre-harvested {len(harvest_results)} tool calls for question")
        return True

    def answer(self, question: str, max_tool_rounds: int = 5) -> Answer:
        all_citations = []
        tool_results: list[tuple[str, dict, dict]] = []  # (tool_name, args, result)

        # Layer 1: intercept raw tool calls in user input (e.g. `search_experiments {"subject": "faba bean"}`)
        specs, remaining = self._parse_toolcalls(question)
        if specs:
            for spec in specs:
                tool_name = spec.get("tool")
                tool_args = spec.get("args", {})
                impl = TOOL_IMPLS.get(tool_name)
                if impl:
                    self._log(f"User toolcall: {tool_name}({tool_args})")
                    result = impl(**tool_args)
                    tool_results.append((tool_name, tool_args, result))
                    all_citations.extend(_extract_citations(result))
            formatted = []
            for name, args, res in tool_results:
                formatted.append(f">> {name} {json.dumps(args)}")
                formatted.append(self._summarize_result(res))
                formatted.append(f"```json\n{json.dumps(res, indent=2, default=str)}\n```")
            content = "\n".join(formatted)
            return self._finalize_answer(
                content, tool_results, all_citations, question, []
            )

        messages = self._build_messages(question)

        # Layer 2: pre-harvest measurements for high-risk question patterns
        self._pre_classify_and_harvest(question, messages, tool_results, all_citations)

        for round_num in range(max_tool_rounds):
            content = self._call_llm(messages)
            specs, remaining = self._parse_toolcalls(content)

            if not specs:
                self._log(f"LLM final response ({len(content)} chars)")
                # Best-effort Evidence block linking (no rejection)
                prose, evidence_entries = self._parse_evidence_block(content)
                measurement_map = self._build_measurement_map(tool_results)
                valid_entries, _ = self._validate_evidence_block(evidence_entries, measurement_map)
                if valid_entries:
                    linked_prose = self._link_evidence_to_text(prose, valid_entries)
                    # Group by experiment_id for clean display
                    groups = {}
                    entry_map = {}
                    for e in valid_entries:
                        info = self._measurement_lookup(e["ref"])
                        entry_map[e["label"]] = info
                        exp_id = info["experiment_id"] or "unknown"
                        groups.setdefault(exp_id, []).append(e)
                    lines = []
                    for exp_id, entries in groups.items():
                        lines.append(f"\n── {exp_id} ──")
                        for e in entries:
                            info = entry_map[e["label"]]
                            label = f'[{e["label"]}] {info["display"]}'
                            if info["source_id"] and info["experiment_id"]:
                                lines.append(f'  <ref type="source" ids="{info["source_id"]}" label="{label}" experiment="{info["experiment_id"]}"/>')
                            else:
                                lines.append(f"  {label}")
                    block_text = "\n\nEvidence:" + "\n".join(lines)
                    content = linked_prose + block_text
                return self._finalize_answer(content, tool_results, all_citations, question, messages)

            messages.append({"role": "assistant", "content": content})

            for spec in specs:
                tool_name = spec.get("tool")
                tool_args = spec.get("args", {})
                impl = TOOL_IMPLS.get(tool_name)

                self._log(f"Round {round_num + 1}: calling {tool_name}({tool_args})")

                if impl:
                    try:
                        result = impl(**tool_args)
                    except Exception as e:
                        result = {"error": str(e)}
                else:
                    result = {"error": f"Unknown tool: {tool_name}"}

                result_count = 0
                for v in result.values():
                    if isinstance(v, (list, dict)):
                        result_count = max(result_count, len(v) if isinstance(v, list) else 1)
                self._log(f"  → {result_count} results")

                tool_results.append((tool_name, tool_args, result))
                all_citations.extend(_extract_citations(result))

                messages.append({
                    "role": "user",
                    "content": f"Tool result for {tool_name}:\n{json.dumps(result, default=str, indent=2)}"
                })

        # Max rounds exhausted without a clean finalize
        fallback = self._make_fallback(question)
        return self._finalize_answer(fallback, tool_results, all_citations, question, messages)
