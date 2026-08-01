"""
Inject page numbers into CER files by matching source_ids to the table→page index.
Matcher is purely deterministic: extract number from "Table N", look up in index.
"""

import json, re, sys
from pathlib import Path

INDEX_PATH = Path("data/table_page_index.json")
CER_DIR = Path("data/extractions")

TABLE_RE = re.compile(r"[Tt]able\s+(\d+)")


def load_index() -> dict[str, dict[str, int]]:
    return json.loads(INDEX_PATH.read_text())


def match_page(source_id: str, paper_index: dict[str, int]) -> int | None:
    """Match a source_id like 'Table 2' or 'Table 5 (Wheat columns)' to the page index."""
    m = TABLE_RE.search(source_id)
    if not m:
        return None
    num = m.group(1)
    candidates = [k for k in paper_index if TABLE_RE.search(k) and TABLE_RE.search(k).group(1) == num]
    if candidates:
        return paper_index[candidates[0]]
    return None


def main():
    index = load_index()
    total_injected = 0

    for cer_path in sorted(CER_DIR.glob("*_v3")):
        pid = cer_path.stem.replace("_v3", "")
        paper_index = index.get(pid, {})
        if not paper_index:
            print(f"SKIP {pid} — no page index", file=sys.stderr)
            continue

        raw = json.loads(cer_path.read_text())
        root = raw["cer"] if "cer" in raw else raw

        paper_updated = 0
        for exp in root.get("experiments", []):
            for src in exp.get("evidence_sources", []):
                page = match_page(src.get("source_id", ""), paper_index)
                if page is not None:
                    src["page_number"] = page
                    paper_updated += 1

        if paper_updated:
            total_injected += paper_updated
            cer_path.write_text(json.dumps(raw, indent=2))
            print(f"  {pid}: injected {paper_updated} page numbers", file=sys.stderr)
        else:
            print(f"  {pid}: no matches", file=sys.stderr)

    print(f"\nTotal page numbers injected: {total_injected}", file=sys.stderr)


if __name__ == "__main__":
    main()
