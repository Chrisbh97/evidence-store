"""Grounding check for statistical extraction output.

Verifies that the pipeline's stat_records are traceable to the source paper's
markdown.  This is the gate that would have caught the fabrication bug (s1-s9
invented F/df/p values) and the MS mislabeling (s10).

Checks per output file:
  - for every numeric statistic field (value, df_numerator, df_denominator,
    p_value, plus any numeric params values like sum_sq/mean_sq/n), the value
    must appear in the source markdown (word-boundary match, so 12.3 does NOT
    match inside 112.3)
  - star-only rows (significance set, all numerics null) are allowed and
    reported separately
  - contrast rows (source_type == "contrast") must have source of form
    "<factor>: <trend>"
  - p_value must never be one of {0.05, 0.01, 0.001, 0.005, 0.001...} derived
    from stars unless it literally appears in the source

Usage: python scripts/verify_grounding.py <output.json> <source.md>
"""

import io
import json
import re
import sys

FABRICATED_PVALS = {"0.05", "0.01", "0.001", "0.005", "0.10", "0.1"}
TOPLEVEL_NUMERIC = ("value", "df_numerator", "df_denominator", "p_value")
CONTRAST_TREND = re.compile(r"^(.+):\s*(Linear|Quadratic|Cubic)$", re.IGNORECASE)


def _word_boundary(value: str) -> str:
    """Escape and wrap so '12.3' doesn't match inside '112.3'."""
    return r"(?<![\d.])\b" + re.escape(str(value)) + r"\b(?![\d.])"


def _normalize(text: str) -> str:
    """Drop trailing zeros after a decimal point ('10.10' -> '10.1', '2.00' -> '2')
    so 10.1 in output matches 10.10 in source.  Numerically identical, never
    invents values: '112.3' still never normalizes to '12.3'."""
    t = re.sub(r"(\.\d*?)0+\b", r"\1", text)
    return re.sub(r"\.\b", "", t)


def ground_check(source_text: str, stat_records: list) -> dict:
    problems = []
    star_only = 0
    contrast = 0
    numeric_rows = 0
    checked = {"fields": 0, "found": 0}
    src_norm = _normalize(source_text)

    def present(value) -> bool:
        return re.search(_word_boundary(_normalize(str(value))), src_norm) is not None

    def numeric_values(r: dict) -> list:
        """Yield (field, value) pairs for every numeric quantity in a row,
        including nested params (mean_sq/sum_sq/n/...)."""
        out = []
        for f in TOPLEVEL_NUMERIC:
            v = r.get(f)
            if v is not None:
                out.append((f, v))
        for k, v in (r.get("params") or {}).items():
            if v is not None and not isinstance(v, (dict, list)):
                try:
                    float(v)
                    out.append((f"params.{k}", v))
                except (TypeError, ValueError):
                    pass
        # Legacy ANOVA rows still carry sum_sq/mean_sq/f_value at top level
        for f in ("sum_sq", "mean_sq", "f_value"):
            v = r.get(f)
            if v is not None:
                out.append((f, v))
        return out

    for sr in stat_records:
        for t in sr.get("tables", []):
            for r in t.get("rows", []):
                if not isinstance(r, dict):
                    continue
                nv = numeric_values(r)
                has_numeric = len(nv) > 0
                has_sig = r.get("significance") not in (None, "")
                if has_numeric:
                    numeric_rows += 1
                    for f, v in nv:
                        checked["fields"] += 1
                        s = str(v)
                        if f == "p_value" and s in FABRICATED_PVALS:
                            if not present(s):
                                problems.append(
                                    f"p_value {s} is a star-derived threshold and "
                                    f"does NOT appear in source (row {r.get('source')})"
                                )
                                continue
                        if present(s):
                            checked["found"] += 1
                        else:
                            problems.append(
                                f"{f}={v} NOT found in source (row {r.get('source')}, "
                                f"type={r.get('source_type')})"
                            )
                if has_sig and not has_numeric:
                    star_only += 1
                if r.get("source_type") == "contrast":
                    contrast += 1
                    if not CONTRAST_TREND.match(r.get("source", "")):
                        problems.append(
                            f"contrast row has non-trend source: {r.get('source')!r}"
                        )

    coverage = 100.0 * checked["found"] / checked["fields"] if checked["fields"] else 100.0
    return {
        "problems": problems,
        "star_only_rows": star_only,
        "contrast_rows": contrast,
        "numeric_rows": numeric_rows,
        "numeric_field_coverage_pct": coverage,
        "ok": not problems,
    }


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if len(sys.argv) < 3:
        print("usage: python scripts/verify_grounding.py <output.json> <source.md>")
        sys.exit(2)
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    source = open(sys.argv[2], encoding="utf-8").read()
    result = ground_check(source, d.get("stat_records", []))

    print(f"star-only rows:      {result['star_only_rows']}")
    print(f"contrast rows:       {result['contrast_rows']}")
    print(f"numeric rows:        {result['numeric_rows']}")
    print(f"numeric coverage:    {result['numeric_field_coverage_pct']:.1f}% "
          f"({result['numeric_field_coverage_pct']:.0f})")
    if result["problems"]:
        print(f"\nGROUNDING PROBLEMS ({len(result['problems'])}):")
        for p in result["problems"][:20]:
            print(f"  - {p}")
        sys.exit(1)
    print("\nOK — all numeric values traceable to source.")


if __name__ == "__main__":
    main()
