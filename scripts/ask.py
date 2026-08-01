"""
CLI: ask questions about the evidence store in natural language.

Usage:
    python scripts/ask.py "Which papers studied phosphorus rate and measured grain yield?"
    python scripts/ask.py --model mistral-large-latest "What were the significant yield responses?"
    python scripts/ask.py --interactive
"""

import sys, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.evidence_store.engine import ResearchEngine, ResearchSession


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="Ask questions about the evidence store")
    parser.add_argument("question", nargs="?", help="Question in natural language")
    parser.add_argument("--model", "-m", default=None, help="Model to use")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--api-base", default=None, help="API base URL")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    parser.add_argument("--session", "-s", default=None, help="Session ID (resume investigation)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show tool calls as they happen")
    parser.add_argument("--delay", type=float, default=15.0, help="Min seconds between API calls (default 15 for 0.07 RPS)")

    args = parser.parse_args()

    if args.api_key:
        import os; os.environ["MISTRAL_API_KEY"] = args.api_key
    if args.api_base:
        import os; os.environ["MISTRAL_BASE_URL"] = args.api_base

    session = ResearchSession(id=args.session or "default")
    engine = ResearchEngine(session=session, model=args.model, verbose=args.verbose, min_interval=args.delay)

    if args.interactive:
        sid = session.id
        print(f"Session: {sid}  (research continues until you reset)")
        print("Commands: quit | reset | status")
        print("Follow-up questions automatically carry context forward.\n")
        while True:
            if session.filters or session.paper_ids:
                print(f"  [{session.summary}]")
            q = input("> ").strip()
            if q.lower() in ("quit", "exit", "q"):
                break
            if q.lower() == "reset":
                session.reset()
                print("(research state cleared)\n")
                continue
            if q.lower() == "status":
                print(session.to_context() or "(empty)")
                print()
                continue
            if not q:
                continue
            answer = engine.answer(q)
            print(f"\n{answer.text}\n")
    elif args.question:
        answer = engine.answer(args.question)
        print(answer.text)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
