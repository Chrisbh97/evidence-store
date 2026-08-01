"""
Batch test runner for planner evaluation.

Runs a canonical question set through the ResearchEngine one by one,
saves full transcripts (tool traces + final answer) to test_output/.

Usage:
    python scripts/test_planner.py
    python scripts/test_planner.py --model mistral-large-latest --delay 15

Output:
    test_output/01_question-short.txt
    test_output/02_question-short.txt
    ...
    test_output/_results.csv        (one-row summary per question)
"""

import sys, os, re, json, subprocess, csv, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

QUESTIONS = [
    # Single-tool questions (one tool call → summarize)
    ("Which papers are available in the evidence store?", ["list_papers"]),
    ("What response variables were measured in paper P-01?", ["list_response_variables"]),
    ("What nitrogen rates were tested across all experiments?", ["get_dimension_levels"]),

    # Two-tool chains (search → drill)
    ("Which experiments studied phosphorus rate on bread wheat?", ["search_experiments", "get_experiment_context"]),
    ("Did phosphorus have a significant effect on grain yield in P-01?", ["search_experiments", "get_significant_findings"]),
    ("What was the grain yield at 100 kg N/ha in P-01?", ["search_experiments", "get_measurements"]),

    # Drill-only (assumes caller already has IDs from prior knowledge)
    ("What soil conditions were reported for P-04 experiment E-1?", ["get_experiment_context"]),
    ("What data is in Table 6 of experiment P-04_E-1?", ["get_source_observations"]),
    ("Where does observation O-0214 come from?", ["trace_observation"]),

    # Multi-tool comparison
    ("Compare all experiments that measured grain yield — which had the highest mean yield?",
     ["search_experiments", "get_measurements"]),
]

OUT_DIR = ROOT / "test_output"
OUT_DIR.mkdir(exist_ok=True)
STATE_FILE = OUT_DIR / "_state.json"


def _fmt_tool_name(tool: str) -> str:
    return tool.replace("search_experiments", "search")


def _short_q(question: str, maxlen: int = 60) -> str:
    safe = question.lower().replace("?", "").replace("--", "-")
    safe = re.sub(r'[\\/*?:"<>|]', "_", safe)
    return safe.strip()[:maxlen].rstrip()


def run_question(num: int, question: str, model: str, delay: float) -> dict:
    short = _short_q(question)
    label = f"{num:02d}_{short}.txt"
    path = OUT_DIR / label

    env = os.environ.copy()
    # Ensure Python finds our modules
    python_path = env.get("PYTHONPATH", "")
    root_str = str(ROOT)
    if root_str not in python_path:
        env["PYTHONPATH"] = f"{root_str};{python_path}" if python_path else root_str

    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "ask.py"),
        "--verbose",
        "--delay", str(delay),
        question,
    ]
    if model:
        cmd.extend(["--model", model])

    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    elapsed = time.time() - start

    # Write transcript
    transcript = [
        f"=== Planner Test: Question {num} ===",
        f"Question: {question}",
        f"Model: {model or '(default)'}",
        f"Delay: {delay}s",
        f"Elapsed: {elapsed:.1f}s",
        f"Return code: {proc.returncode}",
        "",
        "--- Tool trace (stderr) ---",
        proc.stderr.strip() if proc.stderr else "(none)",
        "",
        "--- Final answer (stdout) ---",
        proc.stdout.strip() if proc.stdout else "(none)",
    ]
    path.write_text("\n".join(transcript), encoding="utf-8")

    return {
        "num": num,
        "question": question,
        "elapsed": round(elapsed, 1),
        "tool_trace": proc.stderr,
        "answer": proc.stdout,
        "returncode": proc.returncode,
        "path": str(path),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Batch test the planner")
    parser.add_argument("--model", default=None, help="Model to use")
    parser.add_argument("--delay", type=float, default=15.0, help="Min seconds between API calls")
    args = parser.parse_args()

    # Load resume state
    state = {}
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            state = json.load(f)
    completed = {int(k) for k, v in state.items() if v.get("done")}

    results = []
    total = len(QUESTIONS)
    pending = total - len(completed)
    if completed:
        print(f"Resuming: {len(completed)}/{total} already done, {pending} remaining\n")
    else:
        print(f"Running {total} questions (delay={args.delay}s)...\n")

    for i, (question, expected_tools) in enumerate(QUESTIONS, 1):
        if i in completed:
            print(f"[{i}/{total}] {_short_q(question)}  (already done, skipping)")
            # Re-read saved output for CSV
            saved = list(OUT_DIR.glob(f"{i:02d}_*.txt"))
            if saved:
                r = {"num": i, "question": question, "expected_tools": expected_tools,
                     "elapsed": 0, "tool_trace": "", "answer": "",
                     "returncode": 0, "path": str(saved[0])}
                results.append(r)
            continue
        print(f"[{i}/{total}] {_short_q(question)}")
        r = run_question(i, question, model=args.model, delay=args.delay)
        r["expected_tools"] = expected_tools
        results.append(r)
        state[str(i)] = {"done": True, "path": r["path"]}
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        print(f"  → {r['elapsed']}s  saved to {Path(r['path']).name}")

        # Extra delay between questions to stay well within rate limits
        if i < total:
            wait = max(args.delay, 10)
            print(f"  waiting {wait:.0f}s before next question...")
            time.sleep(wait)

    # Write summary CSV
    csv_path = OUT_DIR / "_results.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["num", "question", "expected_tools", "elapsed_s", "tool_trace_snippet", "answer_snippet", "returncode"])
        for r in results:
            w.writerow([
                r["num"],
                r["question"],
                ", ".join(r["expected_tools"]),
                r["elapsed"],
                (r["tool_trace"] or "")[:200],
                (r["answer"] or "")[:200],
                r["returncode"],
            ])

    print(f"\nDone. {total} transcripts in {OUT_DIR}/")
    print(f"Summary: {csv_path}")


if __name__ == "__main__":
    main()
