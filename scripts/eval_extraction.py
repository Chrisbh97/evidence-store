"""Extraction eval: compare stat_records against a hand-curated gold set.

This is the *gold-set* layer of the eval harness (the only layer that catches
mis-association, e.g. the wide-table star-swap). verify_grounding.py is the
cheap always-on gate; this is the reference-comparison layer.

Error classes (scorecard):
  - fabrication:      a stat row exists in extraction but not in gold at all
  - omission:         gold row missing from extraction entirely
  - mis_association:  row present in both, but significance differs (star swap /
                      attached to the wrong column/location)
  - dropped_significance: gold had a star/NS but extraction left it blank

Usage:
    python scripts/eval_extraction.py <gold.json> <extraction.json>
    python scripts/eval_extraction.py --all   # every gold + extraction pairing
"""

import argparse
import json
import re
import sys
from collections import defaultdict


def _norm(s: str) -> str:
    """Normalize identifiers for tolerant comparison."""
    if s is None:
        return ""
    s = s.lower()
    s = s.replace("\u00d7", "x").replace("\ufffd", "x")  # × and mojibake → x
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _sig(s) -> str:
    """Normalize a significance field to stars / ns / '' ."""
    if s is None:
        return ""
    s = str(s).strip()
    if s == "":
        return ""
    if s.lower() in ("ns", "n.s", "n s"):
        return "ns"
    return s


def _rv(rv: str) -> str:
    """Normalize a response-variable label (kept distinct from location)."""
    return _norm(rv)


def _strip_rv_location(rv: str) -> str:
    """Remove a trailing '(Rob Gebeya)'/' (Welmera)' style suffix from a label."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", rv.strip()).strip()


def _gold_tables(data: dict) -> list[dict]:
    tables = []
    for source_id, src in data["sources"].items():
        for t in src.get("tables", []):
            tables.append({
                "source_id": source_id,
                "response_variable": t["response_variable"],
                "location": t.get("location"),
                "rows": t.get("rows", []),
            })
    return tables


def _ext_tables(data: dict) -> list[dict]:
    tables = []
    for sa in data.get("stat_records", []):
        for t in sa.get("tables", []):
            tables.append({
                "source_id": sa.get("source_id"),
                "response_variable": t.get("response_variable", ""),
                "location": t.get("location"),
                "rows": t.get("rows", []),
            })
    return tables


def _skippable(r: dict) -> bool:
    return r.get("source_type") in ("error", "total") and not _sig(r.get("significance"))


def align_tables(gold_tables: list[dict], ext_tables: list[dict]) -> list[tuple[dict, dict | None]]:
    """Pair gold tables to extraction tables, tolerating naming drift.

    Scale: tiny (<= dozens); an O(n^2) greedy aligner is fine and legible.
    Preferred match: same source_id + same normalized response_variable + same
    (or compatible) location. Falls back to response_variable-only when a gold
    table has no location and extraction split by location, or when one side
    embeds location in the response_variable string.
    """
    gold_by_key = defaultdict(list)
    for gt in gold_tables:
        gold_by_key[(_norm(gt["source_id"]), _rv(gt["response_variable"]))].append(gt)

    ext_unused = ext_tables[:]
    pairs: list[tuple[dict, dict | None]] = []
    used = [False] * len(ext_unused)
    gold_used = set()

    # Pass 1: exact source_id + rv + location (or compatible empty/null)
    for i, gt in enumerate(gold_tables):
        gloc = _norm(gt["location"])
        best_idx = None
        for j, et in enumerate(ext_unused):
            if used[j]:
                continue
            if _norm(et["source_id"]) != _norm(gt["source_id"]):
                continue
            if _rv(et["response_variable"]) != _rv(gt["response_variable"]):
                continue
            eloc = _norm(et["location"])
            # compatible location: exact match, or gold location embedded in
            # extraction response_variable, or one side missing
            if gloc and eloc and gloc != eloc:
                if eloc not in _norm(et["response_variable"]):
                    continue
            if best_idx is None or (gloc and eloc and gloc == eloc):
                best_idx = j
                if gloc and eloc and gloc == eloc:
                    break
        if best_idx is not None:
            pairs.append((gt, ext_unused[best_idx]))
            used[best_idx] = True
            gold_used.add(i)

    # Pass 2: leftover gold tables that match only by source_id + rv
    for i, gt in enumerate(gold_tables):
        if i in gold_used:
            continue
        for j, et in enumerate(ext_unused):
            if used[j]:
                continue
            if _norm(et["source_id"]) == _norm(gt["source_id"]) and \
               _rv(et["response_variable"]) == _rv(gt["response_variable"]):
                pairs.append((gt, ext_unused[j]))
                used[j] = True
                gold_used.add(i)
                break

    for i, gt in enumerate(gold_tables):
        if i not in gold_used:
            pairs.append((gt, None))

    return pairs


def row_key(r: dict) -> str:
    return _norm(r.get("source", "")) + "|" + _norm(r.get("source_type", ""))


def compare_tables(gt: dict, et: dict | None) -> dict:
    """Return scorecard counts + problem details for one (gold, ext) pair."""
    result = {
        "pair": (gt["source_id"], gt["response_variable"]),
        "problems": [],
        "fabrication": 0,
        "omission": 0,
        "mis_association": 0,
        "dropped_significance": 0,
        "gold_rows": len([r for r in gt["rows"] if not _skippable(r)]),
    }
    if et is None:
        for r in gt["rows"]:
            result["omission"] += 1
            result["problems"].append(
                f"MISSING TABLE  {gt['source_id']} / {gt['response_variable']} "
                f"/ {gt.get('location') or ''} -> row: {r.get('source')}"
            )
        return result

    ext_by_key = {row_key(r): r for r in et["rows"]}
    gold_seen = set()
    for r in gt["rows"]:
        if _skippable(r):
            continue
        key = row_key(r)
        gold_seen.add(key)
        gsig = _sig(r.get("significance"))
        er = ext_by_key.get(key)
        if er is None:
            result["omission"] += 1
            result["problems"].append(
                f"OMITTED       {gt['source_id']} / {gt['response_variable']} / {gt.get('location') or ''} -> "
                f"{r.get('source_type')}:{r.get('source')} gold={gsig!r} missing in extraction"
            )
            continue
        esig = _sig(er.get("significance"))
        if esig and gsig and esig != gsig:
            result["mis_association"] += 1
            result["problems"].append(
                f"STAR SWAP     {gt['source_id']} / {gt['response_variable']} / {gt.get('location') or ''} -> "
                f"{r.get('source_type')}:{r.get('source')} gold={gsig!r} ext={esig!r}"
            )
        elif gsig and not esig:
            result["dropped_significance"] += 1
            result["problems"].append(
                f"BLANK SIGNIF  {gt['source_id']} / {gt['response_variable']} / {gt.get('location') or ''} -> "
                f"{r.get('source_type')}:{r.get('source')} gold={gsig!r} ext=<blank>"
            )

    for r in et["rows"]:
        if _skippable(r):
            continue
        key = row_key(r)
        if key not in gold_seen:
            result["fabrication"] += 1
            result["problems"].append(
                f"FABRICATED    {gt['source_id']} / {gt['response_variable']} -> "
                f"{r.get('source_type')}:{r.get('source')} sig={_sig(r.get('significance'))!r} not in gold"
            )
    return result


def flatten_gold(gold_path: str) -> list[dict]:
    with open(gold_path, encoding="utf-8") as f:
        data = json.load(f)
    return _gold_tables(data)


def flatten_ext(ext_path: str) -> list[dict]:
    with open(ext_path, encoding="utf-8") as f:
        data = json.load(f)
    return _ext_tables(data)


def run(gold_path: str, ext_path: str) -> dict:
    gold = flatten_gold(gold_path)
    ext = flatten_ext(ext_path)
    pairs = align_tables(gold, ext)

    totals = defaultdict(int)
    details = []
    for gt, et in pairs:
        res = compare_tables(gt, et)
        for k in ("fabrication", "omission", "mis_association", "dropped_significance", "gold_rows"):
            totals[k] += res[k]
        for p in res["problems"]:
            details.append(p)

    totals["paired_tables"] = sum(1 for _, et in pairs if et is not None)
    totals["gold_tables"] = len(gold)
    totals["ext_tables"] = len(ext)
    totals["error_rows"] = totals["fabrication"] + totals["omission"] + \
        totals["mis_association"] + totals["dropped_significance"]
    return {"totals": dict(totals), "problems": details, "paper": gold_path}


def print_report(rep: dict) -> None:
    t = rep["totals"]
    gold_rows = t.get("gold_rows", 0)
    print(f"paper:            {rep['paper']}")
    print(f"gold tables:      {t['gold_tables']}  (paired {t['paired_tables']}/{t['gold_tables']}, "
          f"extracted {t['ext_tables']})")
    print(f"gold rows:        {gold_rows}")
    print("-" * 60)
    print(f"fabrication:          {t['fabrication']}")
    print(f"omission:             {t['omission']}")
    print(f"mis_association:      {t['mis_association']}")
    print(f"dropped_significance: {t['dropped_significance']}")
    print(f"error rows (total):   {t['error_rows']}")
    print("-" * 60)
    if rep["problems"]:
        print(f"PROBLEMS ({len(rep['problems'])}):")
        for p in rep["problems"]:
            print(f"  {p}")
    else:
        print("OK — extraction matches gold set.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("gold", nargs="?", help="gold set JSON (data/gold/<paper>.json)")
    ap.add_argument("extraction", nargs="?", help="extraction output JSON")
    ap.add_argument("--all", action="store_true", help="run every data/gold/*.json against its s*_v* pairing")
    ap.add_argument("--json-out", help="write full report JSON to this path")
    args = ap.parse_args()

    if args.all:
        from pathlib import Path
        gold_dir = Path("data/gold")
        ext_dir = Path("data/extractions")
        combos = []
        for g in sorted(gold_dir.glob("*.json")):
            stem = g.stem
            cands = [ext_dir / f"{stem}_v3_v2", ext_dir / f"{stem}_v3"]
            hit = next((p for p in cands if p.exists()), None)
            if hit:
                combos.append((str(g), str(hit)))
        for gold_path, ext_path in combos:
            rep = run(gold_path, ext_path)
            print_report(rep)
            print()
        return

    if not args.gold or not args.extraction:
        ap.print_usage()
        sys.exit(2)

    rep = run(args.gold, args.extraction)
    print_report(rep)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2)
    sys.exit(1 if rep["totals"]["error_rows"] else 0)


if __name__ == "__main__":
    main()