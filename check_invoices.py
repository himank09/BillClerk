"""Run invoices from the command line.

  python check_invoices.py invoices\\01_apex_happy_intrastate.pdf   check one invoice and save it to records
  python check_invoices.py --all                                    start fresh, run all 13 in order, print a score
  python check_invoices.py --reset                                  clear history and records
  add --mock to skip Gemini and use the saved correct answers
"""
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
if os.name == "nt":
    os.system("")  # turns on colour codes in the Windows terminal
    sys.stdout.reconfigure(encoding="utf-8")

ICON = {"pass": "\033[32m✔\033[0m", "warn": "\033[33m!\033[0m", "hold": "\033[33m⏸\033[0m",
        "reject": "\033[31m✘\033[0m", "skipped": "\033[90m–\033[0m"}
COLOUR = {"APPROVE": "\033[42;30m", "HOLD": "\033[43;30m", "REJECT": "\033[41;97m"}


def show_stage(st, verbose):
    print(f"  {ICON[st['status']]} {st['name']:<26} {st['summary']}  \033[90m{st['ms']} ms\033[0m")
    if verbose or st["status"] in ("hold", "reject"):
        for c in st["checks"]:
            mark = {True: "\033[32m✔\033[0m", False: "\033[31m✘\033[0m", None: "\033[90m·\033[0m"}[c["ok"]]
            print(f"       {mark} {c['label']}" + (f"  \033[90m{c.get('detail')}\033[0m" if c.get("detail") else ""))


def run_one(path, verbose=False):
    from app.checks import process
    print(f"\n\033[1m▶ {Path(path).name}\033[0m")
    res = process(path, save=True, on_stage=lambda st: show_stage(st, verbose),
                  on_progress=lambda m: print(f"    \033[90m… {m}\033[0m", flush=True))
    print(f"\n  {COLOUR[res['decision']]} {res['decision']} \033[0m  {res['headline']}")
    for r in res["reasons"] if res["decision"] != "APPROVE" else res["notes"]:
        print(f"    • {r}")
    for a in res["next_actions"]:
        print(f"    → {a}")
    for e in res["emails"]:
        print(f"    ✉ draft to {e['audience']} ({e['to']}): {e['subject']}")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="ignore cached readings and call Gemini again")
    ap.add_argument("-v", "--verbose", action="store_true", help="show every check, not just failing ones")
    a = ap.parse_args()
    if a.fresh:
        os.environ["NO_CACHE"] = "1"
    if a.mock:
        os.environ["EXTRACTOR"] = "mock"
    elif not os.environ.get("GEMINI_API_KEY"):
        sys.exit("No GEMINI_API_KEY found. Put it in a .env file in this folder, or add --mock to test without it.")

    from app import records
    if a.reset or a.all:
        records.reset()
        print("State cleared: no invoices processed, nothing billed against any PO.")

    if a.all:
        truth = sorted(json.loads((ROOT / "data/correct_answers.json").read_text(encoding="utf-8")),
                       key=lambda t: t["run_order"])
        score = []
        for t in truth:
            res = run_one(ROOT / "invoices" / t["file"], a.verbose)
            want = t["expected_decision"].split()[0]
            score.append((t["file"], want, res["decision"], want == res["decision"]))
        print("\n\033[1mSummary\033[0m")
        for f, want, got, ok in score:
            print(f"  {'✔' if ok else '✘'} {f:<40} expected {want:<8} got {got}")
        print(f"\n  {sum(s[3] for s in score)}/{len(score)} correct")
    for f in a.files:
        run_one(f, a.verbose)


if __name__ == "__main__":
    main()
