"""Run v4 staged extraction on all 10 fertilizer papers."""
import subprocess, sys, time
from pathlib import Path

VENV_PY = Path("venv/Scripts/python.exe")
EXTRACTOR = Path("src/ai_extraction.py")
PAPERS_DIR = Path("papers/Fertiliser")
OUT_DIR = Path("data/extractions")
OUT_DIR.mkdir(parents=True, exist_ok=True)

for i in range(1, 11):
    pdf = PAPERS_DIR / f"s{i}.pdf"
    out = OUT_DIR / f"s{i}_v3"
    if out.exists():
        print(f"[SKIP] s{i} — {out} already exists", file=sys.stderr)
        continue
    cmd = [
        str(VENV_PY), str(EXTRACTOR),
        str(pdf),
        "--output", str(out),
        "--mode", "staged",
        "--pdf-engine", "pymupdf4llm",
        "--filter",
        "--filter-mode", "score",
        "--paper-id", f"s{i}",
    ]
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"[{i}/10] s{i}  |  {out.name}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=sys.stderr, stderr=sys.stderr)
    proc.wait()
    elapsed = time.time() - t0
    if proc.returncode != 0:
        print(f"  FAILED (exit {proc.returncode}) after {elapsed:.0f}s", file=sys.stderr)
    else:
        size = out.stat().st_size
        print(f"  OK — {size/1024:.0f} KB in {elapsed:.0f}s", file=sys.stderr)
