"""Cap the per-day run folders of the edge / market-timing / financial-projection flows (default 3 per day).

Flows and their run folders:

- ``edge``    ``edge_research_tools/runs/edge_*`` (``run_upside_opportunity_scan.py --full-flow``; the upside scan lives
  inside the parent run), day = the manifest ``scan_day``.
- ``mtp``     ``market_timing_policy/runs/market_timing_policy_*`` (``run_market_timing_policy.py``), day = ``scan_day``.
- ``finproj`` ``financial_projection/<DD_MM_YYYY>/<fingrowth|finvalue|finproj>_<stamp>`` (``run_financial_projection.py``),
  day = the day folder.

Which runs stay in a day (in this order, until ``keep`` is reached; protected runs stay even past it):

1. every run a reader resolves today: the backtest timeline's pick per day (``backtests.timeline.discover_*``), the
   newest run by mtime (``run_resolution.discover_latest_edge_parent_run_dir`` / ``forward_value.discover_latest_projection_db``
   rule) and every run the newest briefing pack's ``sources`` point at;
2. the newest run of each kind in the day (finproj: growth, value, price);
3. the newest remaining runs.

A run folder is removed whole, so the dry run's byte count is exact. Days on or after today's UTC date and runs
written in the last hour are left alone. ``--apply`` fingerprints every file under each flow root and the readers'
picks first, removes the runs (or moves them to ``<root>/_prune_backup/<stamp>/`` with ``--keep-backup``), then checks
that only the dropped runs are gone, every other file is unchanged and every reader still resolves the same run.
What it does not do: rewrite a file, touch the edge foundation snapshot, or rank runs by content.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGS = PROJECT_ROOT / "logs" / "tradingview_analysis"
DEFAULT_KEEP = 3
LOG_NAME = "_flow_runs_prune_log.jsonl"
MANIFEST_DIR_NAME = "_flow_runs_prune_manifests"
BACKUP_DIR_NAME = "_prune_backup"
RECENT_SECONDS = 3600
FLOW_NAMES = ("edge", "mtp", "finproj")

_EDGE_MTP_STAMP = re.compile(r"_(\d{8})_(\d{4})_utc_[0-9a-f]+$")
_FINPROJ_NAME = re.compile(r"^(fingrowth|finvalue|finproj)_(\d{8}T\d{6})Z$")
_DAY_LABEL = re.compile(r"^\d{2}_\d{2}_\d{4}$")


@dataclass
class FlowRun:
    flow: str
    path: Path
    day: date
    kind: str
    created: datetime
    mtime: float
    bytes: int = 0
    files: int = 0

    @property
    def run_id(self) -> str:
        return self.path.name


@dataclass
class DayGroup:
    flow: str
    day: date
    runs: list[FlowRun]
    keep: dict[str, str] = field(default_factory=dict)  # run_id -> why
    drop: list[FlowRun] = field(default_factory=list)
    skipped: str = ""


def default_roots(logs: Path = LOGS) -> dict[str, Path]:
    return {
        "edge": logs / "edge_research_tools" / "runs",
        "mtp": logs / "market_timing_policy" / "runs",
        "finproj": logs / "financial_projection",
    }


def _dir_size(path: Path) -> tuple[int, int]:
    total = n = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(base, name)).st_size
                n += 1
            except OSError:
                pass
    return total, n


def _parse_date(text: Any) -> date | None:
    if not text:
        return None
    s = str(text)
    try:
        if _DAY_LABEL.match(s):
            return datetime.strptime(s, "%d_%m_%Y").date()
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _parse_dt(text: Any) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is None else dt.astimezone(timezone.utc).replace(tzinfo=None)


def _name_stamp(name: str) -> datetime | None:
    m = _EDGE_MTP_STAMP.search(name)
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M")
    m = _FINPROJ_NAME.match(name)
    if m:
        return datetime.strptime(m.group(2), "%Y%m%dT%H%M%S")
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def discover_runs(flow: str, root: Path, *, sizes: bool = True) -> tuple[list[FlowRun], list[Path]]:
    """Run folders of one flow, plus folders that look like runs but cannot be read (reported, never removed)."""
    runs: list[FlowRun] = []
    unreadable: list[Path] = []
    if not root.is_dir():
        return runs, unreadable
    if flow in ("edge", "mtp"):
        manifest_name = "parent_run_manifest.json" if flow == "edge" else "market_timing_policy_manifest.json"
        prefix = "edge_" if flow == "edge" else "market_timing_policy_"
        for path in sorted(root.iterdir()):
            if not path.is_dir() or not path.name.startswith(prefix):
                continue
            payload = _read_json(path / manifest_name)
            day = None if payload is None else (_parse_date(payload.get("scan_day"))
                                                or _parse_date(payload.get("end_day_label")))
            created = (_parse_dt((payload or {}).get("created_at_utc")) or _name_stamp(path.name))
            if day is None or created is None:
                unreadable.append(path)
                continue
            runs.append(FlowRun(flow, path, day, flow, created, path.stat().st_mtime))
    else:
        for day_dir in sorted(root.iterdir()):
            day = _parse_date(day_dir.name) if day_dir.is_dir() and _DAY_LABEL.match(day_dir.name) else None
            if day is None:
                continue
            for path in sorted(day_dir.iterdir()):
                m = _FINPROJ_NAME.match(path.name) if path.is_dir() else None
                if not m:
                    continue
                runs.append(FlowRun(flow, path, day, m.group(1), _name_stamp(path.name), path.stat().st_mtime))
    if sizes:
        for run in runs:
            run.bytes, run.files = _dir_size(run.path)
    return runs, unreadable


# ---------------------------------------------------------------- readers' picks


def _pack_sources(pack_json: Path | None) -> list[str]:
    if pack_json is None:
        packs = sorted((LOGS / "operator_briefing" / "runs").glob("briefing_pack_*/briefing_pack.json"))
        pack_json = packs[-1] if packs else None
    payload = _read_json(pack_json) if pack_json else None
    if not payload:
        return []
    return [str(v).replace("\\", "/").lower() for v in (payload.get("sources") or {}).values() if isinstance(v, str)]


def reader_picks(flow: str, root: Path, runs: list[FlowRun]) -> dict[str, str]:
    """run folder name -> which reader resolves it now. Uses the readers' own functions where they take a root."""
    picks: dict[str, str] = {}
    if not runs:
        return picks
    if flow == "edge":
        from backtests.timeline import discover_edge_parent_runs

        for day, path in discover_edge_parent_runs(root).items():
            picks[Path(path).name] = f"backtest_{day.isoformat()}"
        newest = max(runs, key=lambda r: r.mtime)  # run_resolution.discover_latest_edge_parent_run_dir rule
        picks[newest.run_id] = "latest_mtime"
    elif flow == "mtp":
        from backtests.timeline import discover_market_timing_runs

        for day, manifest in discover_market_timing_runs(root).items():
            picks[Path(manifest).parent.name] = f"backtest_{day.isoformat()}"
        picks[max(runs, key=lambda r: (r.created, r.run_id)).run_id] = "latest"
    else:
        from backtests.timeline import discover_financial_projection_runs

        for day, per_mode in discover_financial_projection_runs(root).items():
            for mode, ref in per_mode.items():
                picks[Path(ref.metadata_path).parent.name] = f"backtest_{day.isoformat()}_{mode}"
        for prefix in ("fingrowth_", "finvalue_", "finproj_"):  # forward_value.discover_latest_projection_db rule
            dbs = [p for p in root.rglob(f"{prefix}*.duckdb")]
            if dbs:
                picks[max(dbs, key=lambda p: p.stat().st_mtime).parent.name] = f"latest_{prefix[:-1]}"
    return picks


def _protected(flow: str, root: Path, runs: list[FlowRun], pack_sources: list[str]) -> dict[str, str]:
    out = dict(reader_picks(flow, root, runs))
    for run in runs:
        needle = run.path.as_posix().lower()
        if any(s.startswith(needle + "/") or s == needle for s in pack_sources):
            out.setdefault(run.run_id, "briefing_pack")
    return out


# ---------------------------------------------------------------- plan


def choose_runs(runs: list[FlowRun], keep: int, protected: dict[str, str]) -> dict[str, str]:
    """run_id -> why, for one day's runs (see the module docstring for the order)."""
    newest_first = sorted(runs, key=lambda r: (r.created, r.run_id), reverse=True)
    chosen: dict[str, str] = {r.run_id: protected[r.run_id] for r in newest_first if r.run_id in protected}
    for r in newest_first:
        if len(chosen) >= keep:
            break
        if r.run_id not in chosen and not any(c.kind == r.kind for c in runs if c.run_id in chosen):
            chosen[r.run_id] = f"newest_{r.kind}"
    for r in newest_first:
        if len(chosen) >= keep:
            break
        chosen.setdefault(r.run_id, "newest")
    return chosen


def plan_flow(flow: str, root: Path, keep: int = DEFAULT_KEEP, *, today: date | None = None,
              pack_sources: list[str] | None = None, now: float | None = None,
              sizes: bool = True, recent_seconds: int = RECENT_SECONDS) -> tuple[list[DayGroup], list[Path], dict[str, str]]:
    runs, unreadable = discover_runs(flow, root, sizes=sizes)
    today = today or datetime.now(timezone.utc).date()
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    protected = _protected(flow, root, runs, pack_sources if pack_sources is not None else _pack_sources(None))
    by_day: dict[date, list[FlowRun]] = {}
    for run in runs:
        by_day.setdefault(run.day, []).append(run)
    groups = []
    for day in sorted(by_day):
        g = DayGroup(flow, day, by_day[day])
        if len(g.runs) <= keep:
            g.keep = {r.run_id: "within_limit" for r in g.runs}
        elif day >= today:
            g.skipped = "today or later"
        elif any(now - r.mtime < recent_seconds for r in g.runs):
            g.skipped = "a run was written in the last hour"
        else:
            g.keep = choose_runs(g.runs, keep, protected)
            g.drop = sorted((r for r in g.runs if r.run_id not in g.keep), key=lambda r: r.created)
        groups.append(g)
    return groups, unreadable, protected


# ---------------------------------------------------------------- snapshot / validate


def snapshot_files(root: Path, skip: set[Path]) -> dict[str, list[int]]:
    """relative path -> [size, mtime_ns] for every file under root outside ``skip`` folders and prune bookkeeping."""
    out: dict[str, list[int]] = {}
    skip_s = {str(p) for p in skip}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if os.path.join(base, d) not in skip_s and d != BACKUP_DIR_NAME]
        for name in files:
            full = os.path.join(base, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            out[os.path.relpath(full, root).replace("\\", "/")] = [st.st_size, st.st_mtime_ns]
    return out


def validate_flow(before: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """Checks after removal: dropped runs gone, every other file unchanged, readers resolve the same runs."""
    findings: list[dict[str, Any]] = []

    def add(check: str, ok: bool, detail: str = "") -> None:
        findings.append({"check": check, "ok": bool(ok), "detail": detail})

    left = [r for r in before["dropped"] if (root / r).exists()]
    add("dropped runs are gone", not left, ", ".join(left[:5]))
    drop_paths = {root / r for r in before["dropped"]}
    now = snapshot_files(root, drop_paths)
    then = before["files"]
    changed = [k for k in then if k in now and now[k] != then[k]]
    missing = [k for k in then if k not in now]
    added = [k for k in now if k not in then]
    add("kept files unchanged (size, mtime)", not changed, ", ".join(changed[:5]))
    add("no kept file missing", not missing, ", ".join(missing[:5]))
    add("no file added", not added, ", ".join(added[:5]))
    for run in before["kept"]:
        if not (root / run).is_dir():
            add(f"kept run {run} present", False, "folder missing")
    runs, _ = discover_runs(before["flow"], root, sizes=False)
    picks_now = reader_picks(before["flow"], root, runs)
    add("readers resolve the same runs", picks_now == before["picks"],
        json.dumps({k: v for k, v in picks_now.items() if before["picks"].get(k) != v})[:300])
    return findings


def failed(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in findings if not f["ok"]]


# ---------------------------------------------------------------- run


def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB"


def prune_flow_runs(keep: int = DEFAULT_KEEP, apply: bool = False, *, flows: Sequence[str] = FLOW_NAMES,
                    roots: dict[str, Path] | None = None, keep_backup: bool = False, logs_dir: Path = LOGS,
                    pack_json: Path | None = None, today: date | None = None, verbose: bool = True,
                    recent_seconds: int = RECENT_SECONDS,
                    printer: Callable[[str], None] = print) -> dict[str, Any]:
    """Dry run by default (exact bytes per dropped run folder). ``apply=True`` removes them and validates per flow."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    roots = roots or default_roots()
    sources = _pack_sources(pack_json)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    manifest_dir = logs_dir / MANIFEST_DIR_NAME / stamp if apply else None
    report: dict[str, Any] = {"keep": keep, "apply": apply, "flows": {}}
    for flow in flows:
        root = roots[flow]
        groups, unreadable, protected = plan_flow(flow, root, keep, today=today, pack_sources=sources,
                                                recent_seconds=recent_seconds)
        all_runs = [r for g in groups for r in g.runs]
        drops = [r for g in groups for r in g.drop]
        summary = {
            "root": str(root),
            "runs": len(all_runs),
            "days": len(groups),
            "days_over": sum(1 for g in groups if g.drop),
            "skipped": {f"{g.day}": g.skipped for g in groups if g.skipped},
            "runs_dropped": len(drops),
            "bytes_total": sum(r.bytes for r in all_runs),
            "bytes_dropped": sum(r.bytes for r in drops),
            "files_dropped": sum(r.files for r in drops),
            "unreadable": [p.name for p in unreadable],
            "per_day": [{"day": g.day.isoformat(), "runs": len(g.runs), "keep": g.keep,
                         "drop": [r.run_id for r in g.drop], "bytes_dropped": sum(r.bytes for r in g.drop)}
                        for g in groups if g.drop],
        }
        report["flows"][flow] = summary
        if verbose:
            printer(f"== {flow}: {root}")
            printer(f"   {summary['runs']} runs over {summary['days']} days ({_gb(summary['bytes_total'])}); "
                    f"{summary['days_over']} days over {keep}: drop {len(drops)} runs, {_gb(summary['bytes_dropped'])}")
            for g in groups:
                if g.drop:
                    printer(f"   {g.day}: {len(g.runs)} -> {len(g.keep)}  drop {_gb(sum(r.bytes for r in g.drop))}  "
                            f"keep {', '.join(f'{k[-26:]}({w})' for k, w in g.keep.items())}")
            for day, why in summary["skipped"].items():
                printer(f"   {day}: skipped ({why})")
            if unreadable:
                printer(f"   not a readable run (left alone): {', '.join(p.name for p in unreadable)}")
        if not apply or not drops:
            continue
        manifest_dir.mkdir(parents=True, exist_ok=True)
        drop_paths = {r.path for r in drops}
        before = {
            "flow": flow,
            "root": str(root),
            "dropped": [r.path.relative_to(root).as_posix() for r in drops],
            "kept": [r.path.relative_to(root).as_posix() for g in groups for r in g.runs if r.path not in drop_paths],
            "picks": reader_picks(flow, root, all_runs),
            "files": snapshot_files(root, drop_paths),
        }
        (manifest_dir / f"{flow}.before.json").write_text(json.dumps(before), encoding="utf-8")
        backup_root = root / BACKUP_DIR_NAME / stamp if keep_backup else None
        for r in drops:
            if backup_root is not None:
                target = backup_root / r.path.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(r.path, target)
            else:
                shutil.rmtree(r.path)
            if r.path.parent != root and r.path.parent.is_dir() and not any(r.path.parent.iterdir()):
                r.path.parent.rmdir()
        findings = validate_flow(before, root)
        (manifest_dir / f"{flow}.checks.json").write_text(json.dumps(findings, indent=1), encoding="utf-8")
        summary.update({"checks": len(findings), "failed": failed(findings),
                        "backup": str(backup_root) if backup_root else None})
        with (logs_dir / LOG_NAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at_utc": stamp, "flow": flow, "dropped": before["dropped"],
                                 "bytes_dropped": summary["bytes_dropped"], "backup": summary["backup"],
                                 "checks": len(findings), "failed": summary["failed"]}) + "\n")
        if verbose:
            printer(f"   removed {len(drops)} runs; {len(findings) - len(summary['failed'])}/{len(findings)} checks passed")
            for f in summary["failed"]:
                printer(f"   FAIL {f['check']}: {f['detail']}")
        if summary["failed"]:
            printer(f"stopped after {flow}: validation failed (backup: {summary['backup'] or 'none'})")
            break
    total = sum(f["bytes_dropped"] for f in report["flows"].values())
    report["bytes_dropped"] = total
    if verbose:
        printer(f"{'removed' if apply else 'dry run, would free'}: {_gb(total)} over "
                f"{sum(f['runs_dropped'] for f in report['flows'].values())} runs")
    return report


def drop_backups(roots: dict[str, Path] | None = None, printer: Callable[[str], None] = print) -> int:
    freed = 0
    for flow, root in (roots or default_roots()).items():
        b = root / BACKUP_DIR_NAME
        if b.is_dir():
            size, _ = _dir_size(b)
            shutil.rmtree(b)
            freed += size
            printer(f"{flow}: backup deleted ({_gb(size)})")
    return freed


def prune_flow_runs_local_main() -> dict[str, Any]:
    # ===== edit params here =====
    keep = DEFAULT_KEEP
    apply = False
    # ===== end params =====
    return prune_flow_runs(keep, apply)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    parser.add_argument("--flows", default=",".join(FLOW_NAMES), help="comma list of edge,mtp,finproj")
    parser.add_argument("--apply", action="store_true", help="remove the runs (default: dry run with exact bytes)")
    parser.add_argument("--keep-backup", action="store_true", help="move removed runs to <root>/_prune_backup/<stamp>/")
    parser.add_argument("--drop-backups", action="store_true", help="delete every <root>/_prune_backup/ folder")
    parser.add_argument("--json", default=None, help="write the report here")
    args = parser.parse_args(argv)
    if args.drop_backups:
        drop_backups()
        return 0
    flows = [f.strip() for f in args.flows.split(",") if f.strip()]
    report = prune_flow_runs(args.keep, args.apply, flows=flows, keep_backup=args.keep_backup)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return 1 if any(f.get("failed") for f in report["flows"].values()) else 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(main())
    prune_flow_runs_local_main()
