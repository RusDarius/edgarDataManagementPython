"""Decision journal in one command: record the newest pack with the day's judgment, review what followed, render the page.

    python src/run_journal.py                      # record + review + journal-YYYYMMDD page (into the newest run folder)
    python src/run_journal.py --backfill           # first run: every priors file, judgment and pack on disk, then the above
    python src/run_journal.py --note NASDAQ:INTU --kind outside --text "..." --source URL
    python src/run_journal.py --history NASDAQ:MU,NASDAQ:GOOG

It runs the tv_scan_cli.py journal / page commands; no choice of course, no cutoff (see generic_utils/journal.py).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "src/generic_utils/tv_scan_cli.py"]


def _run(args: list[str]) -> int:
    print("$ tv_scan_cli.py " + " ".join(args), flush=True)
    return subprocess.run(CLI + args, cwd=ROOT).returncode


def run_journal_local_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", default="latest")
    ap.add_argument("--day", default=None, help="YYYYMMDD for the page (default: the pack's day).")
    ap.add_argument("--out-dir", default=None, help="Page folder (default: the day's newest run folder, else logs/canvases/YYYYMMDD).")
    ap.add_argument("--backfill", action="store_true", help="Record all history on disk first.")
    ap.add_argument("--note", default=None, help="Ids for a note ('*' = the market); with --text, --kind, --source, --tone.")
    ap.add_argument("--kind", default="note")
    ap.add_argument("--text", default=None)
    ap.add_argument("--source", default=None)
    ap.add_argument("--tone", default=None)
    ap.add_argument("--history", default=None, help="Ids: print each one's calls, notes and outcomes, then stop.")
    ap.add_argument("--no-page", action="store_true")
    a = ap.parse_args(argv)
    if a.history:
        return _run(["journal", "history", "--ids", a.history])
    if a.note:
        if not a.text:
            ap.error("--note needs --text")
        extra = ["--source", a.source] if a.source else []
        extra += ["--tone", a.tone] if a.tone else []
        return _run(["journal", "note", "--ids", a.note, "--kind", a.kind, "--text", a.text] + extra)
    if a.backfill and _run(["journal", "backfill", "--quiet"]):
        return 1
    if _run(["journal", "record", "--pack", a.pack, "--review", "--quiet"]):
        return 1
    if a.no_page:
        return 0
    sys.path.insert(0, str(ROOT / "src"))
    from generic_utils.canvas_runs import day_dir, judgment_path
    from generic_utils.journal import RUNS_ROOT, pack_session

    pack = sorted(RUNS_ROOT.glob("briefing_pack_*"))[-1] if a.pack == "latest" else Path(a.pack)
    day = a.day or pack_session(pack)["day"].strftime("%Y%m%d")
    runs = sorted(p for p in day_dir(day).glob("*_*") if p.is_dir()) if day_dir(day).exists() else []
    out = a.out_dir or (runs[-1].as_posix() if runs else day_dir(day).as_posix())
    args = ["page", "--template", "journal", "--day", day, "--out-dir", out, "--strict"]
    if judgment_path(day).exists():
        args += ["--judgment", judgment_path(day).as_posix()]
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(run_journal_local_main())
