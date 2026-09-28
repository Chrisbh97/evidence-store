"""Run v4 staged extraction on all 10 fertilizer papers with verification."""
import subprocess, sys, time, json
from pathlib import Path

EXTRACTOR = Path("src/ai_extraction.py")
PAPERS_DIR = Path("papers/Fertiliser")
OUT_DIR = Path("data/extractions")
OUT_DIR.mkdir(parents=True, exist_ok=True)

def verify_extraction(path: Path) -> bool:
    """Run CER validation on extracted paper. Returns True if OK."""
    try:
        with open(path) as f:
            data = json.load(f)
        from src.pipeline.compiler import Compiler
        from src.pipeline.parsing import dict_to_er
        from src.validation.cer_validator import validate_cer_compilation

        compiler = Compiler()
        records = [dict_to_er(r) for r in data.get("records", [])]
        result = compiler.compile(data.get("discovery", {}), records, study_id=path.stem)
        val = validate_cer_compilation(result, data.get("discovery", {}))
        return val["ok"]
    except Exception as e:
        print(f"  VERIFY ERROR: {e}", file=sys.stderr)
        return False

for i in range(1, 11):
    pdf = PAPERS_DIR / f"s{i}.pdf"
    out = OUT_DIR / f"s{i}_v4"
    if out.exists():
        print(f"[SKIP] s{i} — {out} already exists", file=sys.stderr)
        continue
    cmd = [
        sys.executable, "-m", "src.ai_extraction",
        str(pdf),
        "--output", str(out),
        "--mode", "staged",
        "--pdf-engine", "pymupdf4llm",
        "--paper-id", f"s{i}",
    ]
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"[{i}/10] s{i}  |  {out.name}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - t0
    if proc.returncode != 0:
        print(f"  FAILED (exit {proc.returncode}) after {elapsed:.0f}s", file=sys.stderr)
        print(proc.stderr[-500:] if proc.stderr else "(no stderr)", file=sys.stderr)
        continue
    size = out.stat().st_size
    print(f"  Extracted — {size/1024:.0f} KB in {elapsed:.0f}s", file=sys.stderr)
    if verify_extraction(out):
        print(f"  VERIFY OK", file=sys.stderr)
    else:
        print(f"  VERIFY FAILED", file=sys.stderr)

print("\nDone.", file=sys.stderr)