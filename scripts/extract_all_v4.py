"""Run v4 staged extraction on all 10 fertilizer papers with verification."""
import subprocess, sys, time, json, re
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

def format_time(seconds: float) -> str:
    """Format seconds as human-readable time."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.0f}m"
    else:
        return f"{seconds/3600:.1f}h"

total_start = time.time()
completed = 0
passed = 0
failed = 0
failed_papers = []

for i in range(1, 11):
    pdf = PAPERS_DIR / f"s{i}.pdf"
    out = OUT_DIR / f"s{i}_v4"
    if out.exists():
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[{i}/10] s{i}  |  {out.name}  [SKIP]", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        completed += 1
        passed += 1
        continue

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"[{i}/10] s{i}  |  {out.name}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "src.ai_extraction", str(pdf),
         "--output", str(out), "--mode", "staged", "--pdf-engine", "pymupdf4llm",
         "--paper-id", f"s{i}"],
        capture_output=True, text=True
    )
    elapsed = time.time() - t0

    if proc.returncode != 0:
        print(f"  FAILED (exit {proc.returncode}) after {format_time(elapsed)}", file=sys.stderr)
        stderr_tail = proc.stderr[-500:] if proc.stderr else "(no stderr)"
        print(stderr_tail, file=sys.stderr)
        failed += 1
        failed_papers.append(f"s{i}")
        completed += 1
        continue

    size = out.stat().st_size
    print(f"  Extracted — {size/1024:.0f} KB in {format_time(elapsed)}", file=sys.stderr)

    if verify_extraction(out):
        print(f"  VERIFY OK", file=sys.stderr)
        passed += 1
    else:
        print(f"  VERIFY FAILED", file=sys.stderr)
        failed += 1
        failed_papers.append(f"s{i}")

    completed += 1

    total_elapsed = time.time() - total_start
    avg_per_paper = total_elapsed / completed
    remaining = 10 - completed
    eta = avg_per_paper * remaining

    print(f"  {'✓' if passed else '✗'} s{i} complete — {format_time(elapsed)}", file=sys.stderr)
    print(f"-"*60, file=sys.stderr)
    print(f"  Progress: {completed}/10 done | {passed} passed | {failed} failed", file=sys.stderr)
    if remaining > 0:
        print(f"  ETA: ~{format_time(eta)}", file=sys.stderr)
    print(f"-"*60, file=sys.stderr)

total_elapsed = time.time() - total_start
print(f"\n{'='*60}", file=sys.stderr)
print(f"SUMMARY", file=sys.stderr)
print(f"{'='*60}", file=sys.stderr)
print(f"  Total papers: {completed}/10", file=sys.stderr)
print(f"  Passed: {passed}", file=sys.stderr)
print(f"  Failed: {failed}", file=sys.stderr)
if failed_papers:
    print(f"  Failed papers: {', '.join(failed_papers)}", file=sys.stderr)
print(f"  Total time: {format_time(total_elapsed)}", file=sys.stderr)
print(f"{'='*60}", file=sys.stderr)