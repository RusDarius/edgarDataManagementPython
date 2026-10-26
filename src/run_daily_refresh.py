"""Daily refresh: scan-day producers -> compile -> suite dumps, as dependency graphs.

Two uses, one command:

MANUAL (a new briefing, no agent):
    python src/run_daily_refresh.py
    python src/run_daily_refresh.py --pages          # also data-only pages -> logs/canvases/<day>/<HHMM>_manual/
    python src/run_daily_refresh.py --no-producers   # reuse the newest scans, recompile + dumps only

AGENT UPDATE (refresh, then re-render today's pages with the same judgment + a delta report):
    python src/run_daily_refresh.py --update-day 20261022
    python src/run_daily_refresh.py --no-producers --no-compile --update-day 20261022   # pages + report only

Steps and their order live in config/generic_utils/daily_refresh.json. Independent
steps run in parallel (`max_workers`; `--serial` restores the one-at-a-time order).
No ranking, no courses: UPDATE writes update.md into logs/canvases/<day>/<HHMM>_update/ for the agent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SRC_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SRC_ROOT.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

RUNS = REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs"


def _newest_pack() -> str:
    packs = sorted(RUNS.glob("briefing_pack_*"), key=lambda p: p.name)
    if not packs:
        raise SystemExit("No briefing_pack_* folder; run with compile on.")
    return packs[-1].relative_to(REPO_ROOT).as_posix()


SOURCE_KEYS = ("prediction_run_id", "all_fields_run_id", "edge_parent_dir", "mtp_dir", "holdings_run_dir", "priors_path")


def newest_pack_is_current() -> bool:
    """True when the newest pack already locks the newest inputs (same run ids, priors / holdings not edited since)."""
    from operator_briefing.inspect_sources import inspect_locked_sources

    packs = sorted(RUNS.glob("briefing_pack_*"), key=lambda p: p.name)
    if not packs:
        return False
    pack_json = packs[-1] / "briefing_pack.json"
    locked = (json.loads(pack_json.read_text(encoding="utf-8")).get("sources") or {})
    latest = inspect_locked_sources().get("sources") or {}
    if any(str(locked.get(k) or "") != str(latest.get(k) or "") for k in SOURCE_KEYS):
        return False
    built = pack_json.stat().st_mtime
    for key in ("priors_path", "holdings_config"):
        path = Path(str(latest.get(key) or ""))
        if path.is_file() and path.stat().st_mtime > built:
            return False
    return True


def run_daily_refresh(
    *,
    producers: bool = True,
    compile_pack: bool = True,
    suites: bool = True,
    serial: bool = False,
    pages: bool = False,
    update_day: str | None = None,
    config_path: str | Path | None = None,
    force_compile: bool = False,
    run_dir: str | Path | None = None,
    producer_names: Sequence[str] | None = None,
    skip_suites: Sequence[str] = (),
) -> dict[str, Any]:
    """`run_dir`: canvas run folder for pages / the update report (a new one per purpose when omitted).
    `producer_names` runs only those producers (e.g. ["allfields"]); dependencies outside the set are dropped.
    `skip_suites` leaves those suite steps out (a caller that runs one itself, e.g. run_gmv_lens.py --full-flow)."""
    from generic_utils.canvas_runs import new_run_dir, write_index, write_run_record
    from generic_utils.page_update import load_refresh_config, run_page_update
    from generic_utils.task_dag import run_dag, substitute

    cfg = load_refresh_config(config_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M_utc")
    out_dir = RUNS / f"refresh_{stamp}"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC_ROOT), str(REPO_ROOT), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    workers = 1 if serial else int(cfg.get("max_workers") or 4)
    record: dict[str, Any] = {"started_utc": stamp, "max_workers": workers, "steps": []}

    def stage(name: str, steps: list[dict[str, Any]]) -> None:
        results = run_dag(steps, log_dir=out_dir / name, cwd=REPO_ROOT, env=env, max_workers=workers)
        record["steps"] += [dict(r, stage=name) for r in results]
        failed = [r["name"] for r in results if r.get("rc") not in (0, None) and not r.get("optional")]
        skipped = [r["name"] for r in results if r.get("rc") is None]
        if failed or skipped:
            raise SystemExit(f"{name}: failed {failed}, skipped {skipped}. Logs in {out_dir / name}")

    if producers:
        steps = cfg["producers"]
        if producer_names:
            wanted = set(producer_names)
            steps = [dict(s, after=[a for a in s.get("after") or [] if a in wanted]) for s in steps if s["name"] in wanted]
        stage("producers", steps)
    if compile_pack and not producers and not force_compile and newest_pack_is_current():
        # Same inputs as the newest pack: a new compile would only duplicate it (and its dumps).
        compile_pack = suites = False
        record["skipped"] = "newest pack already locks the newest inputs; compile and suites skipped (--force-compile to rebuild)"
    if compile_pack:
        stage("compile", [dict(cfg["compile"], after=[])])
    pack = _newest_pack()
    record["pack"] = pack
    if suites:
        skipped_suites = set(skip_suites)
        suite_steps = [dict(s, after=[a for a in s.get("after") or [] if a not in skipped_suites])
                       for s in cfg["suites"] if s["name"] not in skipped_suites]
        stage("suites", substitute(suite_steps, {"PACK": pack, "PLAYBOOK": f"{pack}/playbook"}))
    if pages:
        from generic_utils.canvas_page import write_template_page

        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        pages_run = Path(run_dir) if run_dir else new_run_dir(day, "manual")
        rendered = [write_template_page(t, pack=pack, day=day, out_dir=pages_run) for t in cfg.get("templates") or []]
        record["manual_pages"] = [r["html"] for r in rendered if r["html"]]
        record["skipped_pages"] = [r["skipped"] for r in rendered if r.get("skipped")]
        record["pages_run_dir"] = pages_run.as_posix()
    if update_day:
        update_run = Path(run_dir) if run_dir else new_run_dir(update_day, "update")
        record["update"] = run_page_update(update_day, new_pack=pack, config_path=config_path,
                                           log_dir=out_dir / "update_replay", run_dir=update_run)
    record["seconds_by_stage"] = {}
    for step in record["steps"]:
        record["seconds_by_stage"].setdefault(step["stage"], []).append((step["name"], step.get("seconds")))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "refresh.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    record["refresh_json"] = (out_dir / "refresh.json").as_posix()
    for folder in {record.get("pages_run_dir"), (record.get("update") or {}).get("run_dir")} - {None}:
        upd = record.get("update") or {}
        # keep_existing: a later replay into the same run (agent step) does not replace the run's own refresh / counts.
        write_run_record(folder, keep_existing=True, pack=pack, refresh_json=record["refresh_json"], skipped=record.get("skipped"),
                         n_review=upd.get("n_review") if folder == upd.get("run_dir") else None,
                         n_judged=upd.get("n_judged") if folder == upd.get("run_dir") else None)
    if record.get("pages_run_dir") or record.get("update"):
        write_index()
    return record


def run_daily_refresh_local_main() -> dict[str, Any]:
    """No-arg defaults: full refresh (producers + compile + suites), no pages, no update."""
    return run_daily_refresh(producers=True, compile_pack=True, suites=True, serial=False, pages=False, update_day=None)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    if argv is None and len(sys.argv) == 1:
        record = run_daily_refresh_local_main()
    else:
        parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
        parser.add_argument("--no-producers", action="store_true", help="Skip the scan producers (reuse the newest scans).")
        parser.add_argument("--no-compile", action="store_true", help="Skip compile (reuse the newest pack).")
        parser.add_argument("--no-suites", action="store_true", help="Skip the post-compile dumps.")
        parser.add_argument("--serial", action="store_true", help="One step at a time (old order).")
        parser.add_argument("--force-compile", action="store_true", help="Compile even when the newest pack already locks the newest inputs.")
        parser.add_argument("--pages", action="store_true", help="Also write data-only pages (*-manual.html).")
        parser.add_argument("--update-day", default=None, help="YYYYMMDD: re-render that day's agent pages + write the update report.")
        parser.add_argument("--run-dir", default=None, help="Canvas run folder for pages / the report (default: a new logs/canvases/<day>/<HHMM>_<mode>).")
        parser.add_argument("--config", default=None)
        args = parser.parse_args(argv)
        record = run_daily_refresh(
            producers=not args.no_producers,
            compile_pack=not args.no_compile,
            suites=not args.no_suites,
            serial=args.serial,
            pages=args.pages,
            update_day=args.update_day,
            config_path=args.config,
            force_compile=args.force_compile,
            run_dir=args.run_dir,
        )
    summary = {k: record.get(k) for k in ("pack", "skipped", "refresh_json", "seconds_by_stage", "pages_run_dir", "manual_pages")}
    if record.get("update"):
        summary["update"] = {k: record["update"].get(k) for k in ("run_dir", "update_md", "n_review", "n_judged", "pages")}
    print(json.dumps(summary, indent=1, default=str))
    return record


if __name__ == "__main__":
    main()
