"""
Build a table → page number index for each paper.
Scans pymupdf4llm page-chunked markdown for table headers.
Output: JSON dict {paper_id: {table_name: page_number}}
"""

import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pymupdf4llm

PAPERS_DIR = Path("papers/Fertiliser")
OUT = Path("data/table_page_index.json")

TABLE_RE = re.compile(r"T\s*A\s*B\s*L\s*E\s+(\d+)\b", re.IGNORECASE)


def build_index(pdf_path: str) -> dict[str, int]:
    """Return {table_name: page_number} for one PDF."""
    chunks = pymupdf4llm.to_markdown(pdf_path, page_chunks=True)
    index = {}
    for chunk in chunks:
        page = chunk["metadata"]["page_number"]
        text = chunk["text"]
        for line in text.split("\n"):
            m = TABLE_RE.search(line)
            if m:
                key = f"Table {m.group(1)}"
                # Only keep first occurrence (table header, not in-text mention)
                if key not in index:
                    index[key] = page
    return index


def main():
    index = {}
    for pdf_path in sorted(PAPERS_DIR.glob("s*.pdf")):
        pid = pdf_path.stem  # e.g. "s1"
        print(f"Processing {pid}...", file=sys.stderr)
        try:
            pages = build_index(str(pdf_path))
            index[pid] = pages
            print(f"  Found {len(pages)} tables: {list(pages.keys())[:5]}...", file=sys.stderr)
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(index, indent=2))
    print(f"\nWritten to {OUT}", file=sys.stderr)
    print(f"Total papers: {len(index)}", file=sys.stderr)


if __name__ == "__main__":
    main()
