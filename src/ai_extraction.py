"""
AI-powered extraction pipeline using the new factor/source/observation model.

Usage:
    python src/ai_extraction.py papers/s9.pdf --mode staged --output data/extractions/s9

Environment variables (or --api-key / --api-base / --model):
    API_KEY       default: sk-dummy
    API_BASE_URL  default: https://api.openai.com/v1
    MODEL         default: gpt-4o
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

_proj = Path(__file__).resolve().parent.parent
if str(_proj) not in sys.path:
    sys.path.insert(0, str(_proj))

from openai import OpenAI

from src.pdf_extractor import extract as extract_pdf
from src.prompts import discovery, extraction, statistical_extraction
from src.schemas.extraction_record import ExtractionRecord, RawObservation, RawMeasurement
from src.pipeline.compiler import Compiler, parse_discovery_json

HERE = Path(__file__).parent

_REF_START = re.compile(
    r"^(references|bibliography|literature\s*(cited|review)|works?\s*cited)",
    re.IGNORECASE,
)
_REF_ENTRY = re.compile(r"^\[?\d+\]?\s*[A-Z][a-z]+.*\d{4}")
_APPENDIX_START = re.compile(
    r"^(appendix|supplementary\s*(material|table|figure|data)|supporting\s*information)",
    re.IGNORECASE,
)


def filter_relevant_sections(text: str) -> str:
    """Strip references but preserve appendices/supplementary that follow them.

    Regex-based filter for use with any text (no pdfplumber available).
    """
    lines = text.split("\n")
    ref_start = None
    half = len(lines) // 2
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i > half and _REF_START.match(stripped):
            ref_start = i
            break
        if i > half and _REF_ENTRY.match(stripped):
            ref_count = sum(1 for j in range(i, min(i + 5, len(lines))) if _REF_ENTRY.match(lines[j].strip()))
            if ref_count >= 2:
                ref_start = i
                break
    if ref_start is None:
        return text

    # Keep everything before references
    kept = lines[:ref_start]

    # Scan after references for appendix/supplementary
    appendix_lines = []
    in_appendix = False
    for line in lines[ref_start:]:
        stripped = line.strip()
        if _APPENDIX_START.match(stripped):
            in_appendix = True
        if in_appendix:
            appendix_lines.append(line)

    if appendix_lines:
        kept.extend(appendix_lines)

    result = "\n".join(kept).strip()
    return result if result else text


def _find_true_references_in_pdf(pdf) -> tuple:
    """Scan pdfplumber pages for the real 'References' heading using structural data.

    Returns (page_number, line_text, line_index_in_page) of the first 'References'
    heading whose chars are NOT inside any detected table bounding box.
    """
    for page in pdf.pages:
        # Get all table bounding boxes on this page
        table_bboxes = [t.bbox for t in (page.find_tables() or [])]
        if not table_bboxes:
            continue

        text = page.extract_text()
        if not text:
            continue

        # Get word-level positions
        words = page.extract_words()
        # Build positions of each word: {lowercase_word: [(x0, top, x1, bottom), ...]}
        word_positions = {}
        for w in words:
            key = w["text"].strip().lower()
            if key:
                word_positions.setdefault(key, []).append((w["x0"], w["top"], w["x1"], w["bottom"]))

        lines = text.split("\n")
        for li, line in enumerate(lines):
            stripped = line.strip()
            if not _REF_START.match(stripped):
                continue
            # Get first word of the match
            first_word = stripped.split()[0].lower()
            positions = word_positions.get(first_word, [])
            if not positions:
                # No positional data — skip this page's check
                continue
            # Check if ANY occurrence of this first word is outside all tables
            for wx0, wtop, wx1, wbottom in positions:
                inside_any_table = any(
                    tb[0] <= (wx0 + wx1) / 2 <= tb[2] and tb[1] <= (wtop + wbottom) / 2 <= tb[3]
                    for tb in table_bboxes
                )
                if not inside_any_table:
                    # This "References" isn't inside any table — it's the real heading
                    return page.page_number, stripped, li
    return None, None, None


def filter_relevant_sections_structural(pdf_path: str, pre_extracted_text: str = None) -> str:
    """Filter references using pdfplumber structural data.

    Uses pdfplumber's per-character position data and table bboxes to
    distinguish the true 'References' section heading (not inside a table)
    from table footer cells like 'References 38 31 31'.
    If pre_extracted_text is given, applies the cut to it; otherwise
    re-extracts with pdfplumber.
    """
    import pdfplumber as _pdfplumber

    with _pdfplumber.open(pdf_path) as pdf:
        ref_page, ref_line_text, ref_line_idx = _find_true_references_in_pdf(pdf)

        if pre_extracted_text:
            lines = pre_extracted_text.split("\n")
        else:
            raw_parts = [_page_text_with_tables(p) for p in pdf.pages]
            lines = "\n\n".join(raw_parts).split("\n")

        ref_start = None
        half = len(lines) // 2
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if i > half and stripped == ref_line_text:
                ref_start = i
                break
            # Fallback: regex-based detection with is_table_ref_footer guard
            if i > half and _REF_START.match(stripped):
                if _is_table_ref_footer(stripped):
                    continue
                ref_start = i
                break
            if i > half and _REF_ENTRY.match(stripped):
                ref_count = sum(1 for j in range(i, min(i + 5, len(lines))) if _REF_ENTRY.match(lines[j].strip()))
                if ref_count >= 2:
                    ref_start = i
                    break

        if ref_start is None:
            return pre_extracted_text or "\n\n".join(raw_parts)

        kept = lines[:ref_start]
        appendix_lines = []
        in_appendix = False
        for line in lines[ref_start:]:
            stripped = line.strip()
            if _APPENDIX_START.match(stripped):
                in_appendix = True
            if in_appendix:
                appendix_lines.append(line)
        if appendix_lines:
            kept.extend(appendix_lines)

        result = "\n".join(kept).strip()
        return result if result else (pre_extracted_text or "\n\n".join(raw_parts))


def _score_ref_candidate(lines: list, idx: int) -> int:
    """Score a 'References' candidate line (idx) from 0-5.

    Higher = more likely to be the real section heading.
    """
    line = lines[idx].strip()
    score = 0

    # 1. Standalone short line
    if len(line) < 20:
        score += 1

    # 2. Next 3+ lines look like bibliography entries
    entry_count = 0
    for j in range(idx + 1, min(idx + 8, len(lines))):
        if _REF_ENTRY.match(lines[j].strip()):
            entry_count += 1
    if entry_count >= 3:
        score += 1

    # 3. Preceded by common pre-reference markers
    pre = "\n".join(lines[max(0, idx - 10):idx]).lower()
    if any(m in pre for m in ("data availability", "acknowledgement", "funding", "conflict", "code availability")):
        score += 1

    # 4. NOT followed by inline digits (rejects table footnotes like "References 38 31 31")
    rest = line[len("references"):].strip() if line.lower().startswith("references") else line
    if rest and not rest[0].isdigit():
        score += 1

    # 5. In last 25% of document
    if idx >= len(lines) * 0.75:
        score += 1

    return score


def _load_page_font_info(pdf_path: str) -> tuple:
    """Return (body_font_size, set of table-first-words) from pdfplumber.

    body_font_size is estimated as the most common char size across all pages.
    table_first_words are first words from detected table cells — used to
    flag candidates that likely originate inside a table, not as a heading.
    """
    import pdfplumber
    from collections import Counter

    try:
        with pdfplumber.open(pdf_path) as pdf:
            all_sizes = []
            table_words = set()
            for page in pdf.pages:
                for tbl in (page.extract_tables() or []):
                    for row in tbl:
                        for cell in row:
                            if cell and cell.strip():
                                table_words.add(cell.strip().split()[0].lower())
                for ch in page.chars:
                    s = ch.get("size", 0)
                    if 6 < s < 30:
                        all_sizes.append(round(s, 1))
            body_size = Counter(all_sizes).most_common(1)[0][0] if all_sizes else 10
            return body_size, table_words
    except Exception:
        return 10, set()


def filter_relevant_sections_score(pdf_path: str, pre_extracted_text: str = None) -> str:
    """Filter references using font size (if available) + text heuristics.

    Font check is a veto only — if the candidate's first word appears only
    in table cells (small font), it's rejected.  Text heuristics score 0-5
    make the final decision.
    """
    import pdfplumber as _pdfplumber

    if pre_extracted_text:
        lines = pre_extracted_text.split("\n")
    else:
        with _pdfplumber.open(pdf_path) as pdf:
            raw_parts = [_page_text_with_tables(p) for p in pdf.pages]
        lines = "\n\n".join(raw_parts).split("\n")

    body_size, table_first_words = _load_page_font_info(pdf_path)

    half = len(lines) // 2
    best = (0, None)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if not _REF_START.match(stripped):
            continue

        # Font veto: reject if first word is ONLY known from small-font table cells
        first_word = stripped.split()[0].lower() if stripped.split() else ""
        if first_word in table_first_words and body_size:
            score = _score_ref_candidate(lines, i)
            if score < 3:
                continue  # looks like table footer

        score = _score_ref_candidate(lines, i)
        if i > half and score >= 3:
            return _cut_at_reference(lines, i)
        if i > half and score > best[0]:
            best = (score, i)

    if best[1] is not None and best[0] >= 2:
        return _cut_at_reference(lines, best[1])

    return pre_extracted_text if pre_extracted_text else "\n\n".join(lines)


def _cut_at_reference(lines: list, ref_idx: int) -> str:
    """Cut lines at ref_idx, keeping appendix/supplementary after."""
    kept = lines[:ref_idx]
    appendix = []
    in_app = False
    for line in lines[ref_idx:]:
        s = line.strip()
        if _APPENDIX_START.match(s):
            in_app = True
        if in_app:
            appendix.append(line)
    if appendix:
        kept.extend(appendix)
    result = "\n".join(kept).strip()
    return result if result else "\n".join(lines)


def _is_table_ref_footer(stripped: str) -> bool:
    """Detect if a 'References' line is a table footer (e.g. 'References 38 31 31')."""
    rest = stripped[len("references"):].strip() if stripped.lower().startswith("references") else ""
    return bool(rest) and not rest[0].isalpha()


def _page_text_with_tables(page):
    """Rendered page text with tables as markdown (same format as pdfplumber engine)."""
    page_text = page.extract_text() or ""
    page_parts = []
    if page_text:
        page_parts.append(page_text)
    for tbl in (page.extract_tables() or []):
        if not tbl:
            continue
        lines = []
        for row in tbl:
            cells = [c.replace("\n", " ") if c else "" for c in row]
            lines.append("| " + " | ".join(cells) + " |")
        header = lines[0]
        sep = "| " + " | ".join("---" for _ in tbl[0]) + " |"
        page_parts.append(f"\n--- Table on page {page.page_number} ---\n" + "\n".join([header, sep] + lines[1:]))
    return "\n\n".join(page_parts)

    tables = page.extract_tables() or []
    for ti, tbl in enumerate(tables):
        if not tbl:
            continue
        lines = []
        for row in tbl:
            cells = [c.replace("\n", " ") if c else "" for c in row]
            lines.append("| " + " | ".join(cells) + " |")
        header = lines[0]
        sep = "| " + " | ".join("---" for _ in tbl[0]) + " |"
        label = f"\n--- Table on page {page.page_number} ---\n"
        page_parts.append(label + "\n".join([header, sep] + lines[1:]))

    result = "\n\n".join(page_parts)
    if cut_at_line is not None:
        # Only keep lines from page_text up to cut_at_line, plus all tables
        # We already handled the text cut; tables will still be included
        pass
    return result


_last_retry_total: float = 0.0


def call_llm(system_prompt: str, user_msg: str, **kwargs) -> str:
    import random
    global _last_retry_total
    request_timeout = kwargs.get("timeout", 300.0)
    client = OpenAI(
        api_key=kwargs.get("api_key", os.getenv("API_KEY", "sk-dummy")),
        base_url=kwargs.get("api_base", os.getenv("API_BASE_URL", "https://api.openai.com/v1")),
        max_retries=0,
        timeout=request_timeout,
    )
    model = kwargs.get("model", os.getenv("MODEL", "gpt-4o"))
    if _last_retry_total > 0:
        pre_wait = _last_retry_total * 0.4
        print(f"  Throttling: last call needed {_last_retry_total:.1f}s retry — waiting {pre_wait:.1f}s pre-emptively", file=sys.stderr)
        time.sleep(pre_wait)
    attempt = 0
    accumulated = 0.0
    while True:
        attempt += 1
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0,
            )
            _last_retry_total = accumulated
            return response.choices[0].message.content
        except Exception as e:
            err_str = str(e).lower()
            if hasattr(e, "response") and e.response is not None:
                retry_after = e.response.headers.get("retry-after")
                status = e.response.status_code
            else:
                retry_after = None
                status = getattr(e, "status_code", 0) or getattr(e, "http_status", 0)
            is_rate_limit = status == 429 or "rate limit" in err_str or "rate_limit" in err_str
            is_transient = status in (408, 429, 500, 502, 503, 504) or "timeout" in err_str
            if not (is_rate_limit or is_transient):
                _last_retry_total = 0.0
                raise
            if retry_after:
                wait = float(retry_after)
            else:
                wait = min(2 ** attempt + random.uniform(0, 1), 120)
            accumulated += wait
            print(f"  Rate limited (attempt {attempt}) — retrying in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)


def run_discovery(paper_text: str, **kwargs) -> str:
    """Run discovery prompt. Returns raw JSON text."""
    return call_llm(discovery, f"Full paper:\n\n{paper_text}", **kwargs)


def run_extraction(paper_text: str, experiment: dict, source: dict, **kwargs) -> str:
    """Run extraction prompt for one evidence source. Returns raw JSON text."""
    user_msg = (
        f"Full paper:\n\n{paper_text}\n\n"
        f"---\n\n"
        f"Experiment: {experiment.get('label', '')}\n"
        f"Source: {source['source_id']}\n"
        f"Observation grain: {source.get('observation_grain', [])}\n"
    )
    raw = call_llm(extraction, user_msg, **kwargs)
    return raw


def run_statistical_extraction(paper_text: str, experiment: dict, stat_source: dict, **kwargs) -> str:
    """Run statistical extraction prompt for an ANOVA table."""
    user_msg = (
        f"Full paper:\n\n{paper_text}\n\n"
        f"---\n\n"
        f"Experiment: {experiment.get('label', '')}\n"
        f"Source: {stat_source['source_id']}\n"
        f"Analysis type: {stat_source.get('type', 'anova')}\n"
        f"Response variables: {stat_source.get('response_variables', [])}\n"
    )
    raw = call_llm(statistical_extraction, user_msg, **kwargs)
    return raw


def _extract_json_block(text: str) -> str:
    """Extract the first valid JSON object or array from text."""
    text = text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # Try direct parse
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    # Find { ... } spanning the text
    brace_start = text.find("{")
    if brace_start != -1:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[brace_start:i+1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        pass
    # Find [ ... ] spanning the text
    bracket_start = text.find("[")
    if bracket_start != -1:
        depth = 0
        for i in range(bracket_start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    candidate = text[bracket_start:i+1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        pass
    # Fallback: strip trailing commas (common model error) and try again
    cleaned = text.strip().lstrip(",").rstrip(",")
    # Remove trailing commas before closing braces: "key": "val", } -> "key": "val" }
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass
    # Fallback: model returned key-value pairs without wrapping braces
    # e.g. '"experiment_id": "E-1", "observations": [...]'
    if cleaned.startswith('"') and ":" in cleaned[:20]:
        candidate = "{" + cleaned + "}"
        # Strip possible trailing comma before closing brace
        candidate = re.sub(r",\s*\}", "}", candidate)
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No valid JSON found in response. First 200 chars: {text[:200]}")


def parse_extraction_json_to_er(raw_text: str, experiment_id: str, source_id: str) -> ExtractionRecord:
    """Parse extraction JSON output into an ExtractionRecord."""
    cleaned = _extract_json_block(raw_text)
    data = json.loads(cleaned)

    # Handle both array and object responses
    if isinstance(data, dict):
        obs_list = data.get("observations", [])
    elif isinstance(data, list):
        obs_list = data
    else:
        obs_list = []

    observations = []
    for o in obs_list:
        fv = o.get("factor_values", {})
        ms_raw = o.get("measurements", [])
        ms = []
        for m in ms_raw:
            ms.append(RawMeasurement(
                metric_raw=m.get("metric_raw", m.get("metric", "")),
                construct_raw=m.get("construct_raw", m.get("construct", "")),
                value_raw=m.get("value_raw"),
                computed_value=m.get("computed_value"),
                significance_letter=m.get("significance_letter"),
                statistic=m.get("statistic", "mean"),
                unit_raw=m.get("unit_raw", m.get("unit")),
            ))
        observations.append(RawObservation(
            factor_values={k: str(v) for k, v in fv.items()},
            measurements=ms,
            provenance=o.get("provenance"),
            confidence=o.get("confidence", "unstated"),
        ))

    return ExtractionRecord(
        experiment_id=experiment_id,
        source_id=source_id,
        observations=observations,
        notes=data.get("notes") if isinstance(data, dict) else None,
    )


def _checkpoint_path(args) -> Path:
    if args.checkpoint:
        return Path(args.checkpoint)
    if args.output:
        return Path(args.output).with_suffix(".checkpoint")
    return Path("extraction.checkpoint")


def _load_checkpoint(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_checkpoint(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _er_to_dict(er: ExtractionRecord) -> dict:
    return {
        "experiment_id": er.experiment_id,
        "source_id": er.source_id,
        "observations": [
            {
                "factor_values": dict(o.factor_values),
                "measurements": [
                    {
                        "metric_raw": m.metric_raw,
                        "construct_raw": m.construct_raw,
                        "value_raw": m.value_raw,
                        "computed_value": m.computed_value,
                        "significance_letter": m.significance_letter,
                        "statistic": m.statistic,
                        "unit_raw": m.unit_raw,
                    }
                    for m in o.measurements
                ],
                "provenance": o.provenance,
                "confidence": o.confidence,
            }
            for o in er.observations
        ],
        "notes": er.notes,
    }


def _dict_to_er(data: dict) -> ExtractionRecord:
    return ExtractionRecord(
        experiment_id=data.get("experiment_id", ""),
        source_id=data.get("source_id", ""),
        observations=[
            RawObservation(
                factor_values=dict(o.get("factor_values", {})),
                measurements=[
                    RawMeasurement(**m) for m in o.get("measurements", [])
                ],
                provenance=o.get("provenance"),
                confidence=o.get("confidence", "unstated"),
            )
            for o in data.get("observations", [])
        ],
        notes=data.get("notes"),
    )


def _serialize(obj):
    """Recursively serialize a dataclass tree to JSON-compatible dicts."""
    if hasattr(obj, "__dataclass_fields__"):
        d = vars(obj)
        out = {}
        for k, v in d.items():
            if k.startswith("_"):
                continue
            if isinstance(v, list):
                out[k] = [_serialize(item) for item in v]
            elif isinstance(v, dict):
                out[k] = {kk: _serialize(vv) for kk, vv in v.items()}
            elif hasattr(v, "__dataclass_fields__"):
                out[k] = _serialize(v)
            else:
                out[k] = v
        return {k: v for k, v in out.items() if v or v is False or v == 0}
    return obj


def main():
    parser = argparse.ArgumentParser(description="Extract evidence from a paper PDF")
    parser.add_argument("pdf", help="Path to the paper PDF")
    parser.add_argument("--output", "-o", help="Output path for compiled CER (.json)")
    parser.add_argument("--mode", choices=["single", "staged"], default="single")
    parser.add_argument("--paper-id", help="Override paper ID")
    parser.add_argument("--checkpoint", help="Checkpoint file path")
    parser.add_argument("--pdf-engine", choices=["pypdf", "pdfplumber", "pymupdf4llm", "marker", "docling"], default="pypdf")
    parser.add_argument("--filter", action="store_true", help="Filter to methods+results sections (default: send full paper)")
    parser.add_argument("--filter-mode", choices=["regex", "structural", "score"], default="regex",
                        help="Filter method: regex (any engine), structural (table bbox), or score (font + heuristics)")
    parser.add_argument("--delay", type=float, default=15, help="Seconds to wait between extractions (default 15 for Mistral 0.07 RPS)")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--api-key")
    parser.add_argument("--api-base")
    parser.add_argument("--model")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    paper_id = args.paper_id or pdf_path.stem
    print(f"Extracting text from {pdf_path} (engine: {args.pdf_engine})...", file=sys.stderr)
    paper_text = extract_pdf(str(pdf_path), engine=args.pdf_engine)
    print(f"Extracted {len(paper_text)} characters", file=sys.stderr)
    if args.filter:
        if args.filter_mode == "structural":
            paper_text = filter_relevant_sections_structural(str(pdf_path), paper_text)
        elif args.filter_mode == "score":
            paper_text = filter_relevant_sections_score(str(pdf_path), paper_text)
        else:
            paper_text = filter_relevant_sections(paper_text)
        print(f"After section filter ({args.filter_mode}): {len(paper_text)} characters", file=sys.stderr)

    kwargs = {k: getattr(args, k) for k in ("api_key", "api_base", "model", "timeout") if getattr(args, k, None)}

    if args.mode == "staged":
        checkpoint_path = _checkpoint_path(args)
        completed = _load_checkpoint(checkpoint_path)
        n_ckpt = len(completed.get("records", []))
        print(f"Checkpoint: {n_ckpt} records already extracted", file=sys.stderr)

        if not completed:
            print("Stage 1: Discovery...", file=sys.stderr)
            raw_discovery = run_discovery(paper_text, **kwargs)
            discovery_data = parse_discovery_json(raw_discovery)
            total_sources = sum(
                len(exp.get("evidence_sources", []))
                for exp in discovery_data.get("experiments", [])
            )
            total_stats = sum(
                len(exp.get("statistical_analyses", []))
                for exp in discovery_data.get("experiments", [])
            )
            print(f"Discovered {len(discovery_data['experiments'])} experiments, {total_sources} evidence sources, {total_stats} statistical analyses", file=sys.stderr)
            _save_checkpoint(checkpoint_path, {
                "raw_discovery": raw_discovery,
                "discovery": discovery_data,
                "records": [],
                "stat_records": [],
            })
        else:
            raw_discovery = completed.get("raw_discovery", "")
            discovery_data = completed.get("discovery", {})
            total_sources = sum(
                len(exp.get("evidence_sources", []))
                for exp in discovery_data.get("experiments", [])
            )
            total_stats = sum(
                len(exp.get("statistical_analyses", []))
                for exp in discovery_data.get("experiments", [])
            )
            print(f"Loaded discovery: {len(discovery_data.get('experiments', []))} experiments, {total_sources} sources, {total_stats} statistical", file=sys.stderr)

        records = [_dict_to_er(r) for r in completed.get("records", [])]
        stat_records = completed.get("stat_records", [])
        done_keys = {(r.experiment_id, r.source_id) for r in records}
        stat_done_keys = {(sr.get("experiment_id"), sr.get("source_id")) for sr in stat_records}

        # Flatten (experiment, source) pairs for primary extraction
        all_sources = []
        for exp in discovery_data.get("experiments", []):
            for src in exp.get("evidence_sources", []):
                all_sources.append((exp, src))

        # Flatten (experiment, stat_source) pairs
        all_stats = []
        for exp in discovery_data.get("experiments", []):
            for sa in exp.get("statistical_analyses", []):
                all_stats.append((exp, sa))

        pending = [(exp, src) for exp, src in all_sources if (exp["experiment_id"], src["source_id"]) not in done_keys]
        total_done = len(records)
        print(f"Primary extraction: {total_done} done, {len(pending)} remaining", file=sys.stderr)

        for i, (exp, src) in enumerate(pending):
            if i > 0 and args.delay > 0:
                print(f"  Waiting {args.delay}s...", file=sys.stderr)
                time.sleep(args.delay)
            print(f"Extracting {exp['experiment_id']} / {src['source_id']} ({total_done + i + 1}/{len(all_sources)})...", file=sys.stderr)
            try:
                raw = run_extraction(paper_text, exp, src, **kwargs)
                er = parse_extraction_json_to_er(raw, exp["experiment_id"], src["source_id"])
                records.append(er)
                _save_checkpoint(checkpoint_path, {
                    "raw_discovery": raw_discovery,
                    "discovery": discovery_data,
                    "records": [_er_to_dict(r) for r in records],
                    "stat_records": stat_records,
                })
            except KeyboardInterrupt:
                print(f"\nInterrupted. Checkpoint saved.", file=sys.stderr)
                sys.exit(130)
            except Exception as e:
                print(f"  FAILED: {e} — skipping", file=sys.stderr)

        # --- Statistical extraction ---
        stat_pending = [(exp, sa) for exp, sa in all_stats if (exp["experiment_id"], sa["source_id"]) not in stat_done_keys]
        stat_done = len(stat_records)
        print(f"Statistical extraction: {stat_done} done, {len(stat_pending)} remaining", file=sys.stderr)

        for i, (exp, sa) in enumerate(stat_pending):
            if (len(pending) > 0 or i > 0) and args.delay > 0:
                print(f"  Waiting {args.delay}s...", file=sys.stderr)
                time.sleep(args.delay)
            print(f"Extracting stats {exp['experiment_id']} / {sa['source_id']} ({stat_done + i + 1}/{len(all_stats)})...", file=sys.stderr)
            try:
                raw = run_statistical_extraction(paper_text, exp, sa, **kwargs)
                parsed = json.loads(_extract_json_block(raw))
                parsed["experiment_id"] = exp["experiment_id"]
                stat_records.append(parsed)
                _save_checkpoint(checkpoint_path, {
                    "raw_discovery": raw_discovery,
                    "discovery": discovery_data,
                    "records": [_er_to_dict(r) for r in records],
                    "stat_records": stat_records,
                })
            except KeyboardInterrupt:
                print(f"\nInterrupted. Checkpoint saved.", file=sys.stderr)
                sys.exit(130)
            except Exception as e:
                print(f"  FAILED: {e} — skipping", file=sys.stderr)
    else:
        print("Single-pass extraction not implemented in new model. Use --mode staged.", file=sys.stderr)
        sys.exit(1)

    print("Compiling CER...", file=sys.stderr)
    compiler = Compiler()
    stat_records = locals().get("stat_records", [])
    result = compiler.compile(discovery_data, records, study_id=paper_id, statistical_results=stat_records if stat_records else None)

    output = {
        "records": [_er_to_dict(r) for r in records],
        "stat_records": stat_records,
        "cer": {
            "studies": [_serialize(s) for s in result.studies],
            "subjects": [_serialize(s) for s in result.subjects],
            "experiments": [_serialize(s) for s in result.experiments],
            "observations": [_serialize(s) for s in result.observations],
        },
    }

    if args.mode == "staged":
        output["discovery"] = discovery_data

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
        print(f"Written to {out_path}", file=sys.stderr)
    else:
        print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
