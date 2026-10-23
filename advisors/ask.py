#!/usr/bin/env python3
"""Ask an advisor from the terminal.

  python -m advisors.ask CPA "What's our runway?"
  python -m advisors.ask Marketing "Which channel has the worst CAC?" --show-sql
"""
import argparse
import sys

from .personas import PERSONAS, build_advisor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("persona", choices=sorted(PERSONAS))
    ap.add_argument("question")
    ap.add_argument("--show-sql", action="store_true")
    args = ap.parse_args()
    log = []
    answer = build_advisor(args.persona, query_log=log)(args.question)
    if args.show_sql:
        for i, q in enumerate(log, 1):
            status = f"ERROR: {q.error}" if q.error else f"{q.total_rows} rows, {q.seconds}s"
            print(f"--- query {i} ({status})\n{q.sql}\n", file=sys.stderr)
    print(str(answer).strip())


if __name__ == "__main__":
    main()
