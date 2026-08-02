"""Parsing helpers — LLM raw output -> structured records.

Pure functions.  No I/O, no state.
"""

import json
import re

from src.schemas.extraction_record import ExtractionRecord, RawObservation, RawMeasurement


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


def er_to_dict(er: ExtractionRecord) -> dict:
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


def dict_to_er(data: dict) -> ExtractionRecord:
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


def serialize(obj):
    """Recursively serialize a dataclass tree to JSON-compatible dicts."""
    if hasattr(obj, "__dataclass_fields__"):
        d = vars(obj)
        out = {}
        for k, v in d.items():
            if k.startswith("_"):
                continue
            if isinstance(v, list):
                out[k] = [serialize(item) for item in v]
            elif isinstance(v, dict):
                out[k] = {kk: serialize(vv) for kk, vv in v.items()}
            elif hasattr(v, "__dataclass_fields__"):
                out[k] = serialize(v)
            else:
                out[k] = v
        return {k: v for k, v in out.items() if v or v is False or v == 0}
    return obj
