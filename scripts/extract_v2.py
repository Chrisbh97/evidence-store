"""Run pymupdf4llm v2 extraction (with rotated-table pipeline) on all fertilizer papers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.pdf_extractor import extract

PAPERS_DIR = Path("Papers/Fertiliser")
OUT_DIR = Path("data/comparison")
OUT_DIR.mkdir(parents=True, exist_ok=True)

for i in range(1, 11):
    pdf = PAPERS_DIR / f"s{i}.pdf"
    md = extract(str(pdf), engine="pymupdf4llm")
    out = OUT_DIR / f"s{i}_pymupdf4llm_v2.md"
    out.write_text(md, encoding="utf-8")
    print(f"s{i} -> {out}  ({len(md)} chars)", file=sys.stderr)
