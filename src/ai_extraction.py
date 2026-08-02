"""
AI-powered extraction pipeline using the new factor/source/observation model.

Thin CLI wrapper over the pipeline orchestrator (src/pipeline/run_pipeline.py).
All logic lives in the pipeline modules; this file only parses argv, calls the
orchestrator, and writes the compiled output.

Usage:
    python src/ai_extraction.py papers/s9.pdf --mode staged --output data/extractions/s9

Environment variables (or --api-key / --api-base / --model):
    API_KEY       default: sk-dummy
    API_BASE_URL  default: https://api.openai.com/v1
    MODEL         default: gpt-4o
"""

import argparse
import json
import sys
from pathlib import Path

_proj = Path(__file__).resolve().parent.parent
if str(_proj) not in sys.path:
    sys.path.insert(0, str(_proj))

from src.pipeline.run_pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(description="Extract evidence from a paper PDF")
    parser.add_argument("pdf", help="Path to the paper PDF")
    parser.add_argument("--output", "-o", help="Output path for compiled CER (.json)")
    parser.add_argument("--mode", choices=["single", "staged"], default="single")
    parser.add_argument("--paper-id", help="Override paper ID")
    parser.add_argument("--checkpoint", help="Checkpoint file path (legacy; superseded by versioned cache)")
    parser.add_argument("--pdf-engine", choices=["pypdf", "pdfplumber", "pymupdf4llm", "marker", "docling"], default="pypdf")
    parser.add_argument("--filter", action="store_true", help="Filter to methods+results sections (default: send full paper)")
    parser.add_argument("--filter-mode", choices=["regex", "structural", "score"], default="regex",
                        help="Filter method: regex (any engine), structural (table bbox), or score (font + heuristics)")
    parser.add_argument("--delay", type=float, default=15, help="Seconds to wait between extractions (default 15 for Mistral 0.07 RPS)")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--api-key")
    parser.add_argument("--api-base")
    parser.add_argument("--model")
    args = parser.parse_args()

    if args.mode == "single":
        print("Single-pass extraction not implemented in new model. Use --mode staged.", file=sys.stderr)
        sys.exit(1)

    if args.checkpoint:
        print("Note: --checkpoint is legacy; resume state now lives in data/staging/", file=sys.stderr)

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    output = run_pipeline(
        str(pdf_path),
        paper_id=args.paper_id,
        engine=args.pdf_engine,
        do_filter=args.filter,
        filter_mode=args.filter_mode,
        delay=args.delay,
        timeout=args.timeout,
        api_key=args.api_key,
        api_base=args.api_base,
        model=args.model,
    )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
        print(f"Written to {out_path}", file=sys.stderr)
    else:
        print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
