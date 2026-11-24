"""Prune the all-fields day files: at most ``keep`` scans (runs) per day (default 3), of which at most ``max_full``
full-catalog scans (default 2); drop the rest, then rewrite the file so the space comes back.

Day files are what ``export_all_tradingview_fields_duckdb`` and ``export_focused_tradingview_fields_duckdb`` write:
``logs/tradingview_analysis/trading_view_all_fields_data/DD_MM_YYYY/tradingview_all_fields_DD_MM_YYYY.duckdb``, every
intraday run appended and kept apart by ``run_id`` (tables ``run_metadata``, ``all_fields_rows``, ``parquet_exports``,
``generated_reports``, ``profile_config_snapshots``), plus ``parquet/`` (each table as a whole) and ``runs/<run_id>/``.

Which runs stay (in this order, until ``keep`` are chosen):

1. The newest ``max_full`` full-catalog runs (``FULL_ALL_FIELDS_MIN_COLUMN_COUNT`` columns or more): the same-day
   fallback for the columns a focused run does not fetch (``resolve_latest_and_full_all_fields_run_ids``). Older full
   runs beyond ``max_full`` are dropped.
2. The newest run, when it is a focused run: every reader takes it (panel store, pack compile, financial projection).
3. Focused runs fill the slots left: ``strategy="spread"`` (default) takes the last focused run of each other US
   session phase seen that day (premarket, cash, after-hours, overnight; ``session_marks.us_phase``), newest phase
   first, so a premarket scan and the close survive for session diffs; then the newest focused runs.
   ``strategy="newest"`` takes the newest focused runs only.

So with the defaults: 2 full + 1 focused, 1 full + 2 focused, or 3 focused. The newest run and the newest full run
are never dropped, even with ``keep`` below 2.

What this does not do: no field is changed, no row of a kept run is touched, no day within both limits is opened
for writing. It skips today's UTC day (an export may still append), a day with a writer lock (an export is
running) and a day with a ``.wal`` beside the file (a writer did not close cleanly). Default is a dry run; ``apply``
rewrites the file into a temp copy with only the kept runs, checks the row counts per table, swaps it in, re-exports
``parquet/`` when the day has one, removes ``runs/<run_id>/`` of the dropped runs and appends one line per day to
``_prune_log.jsonl`` in the data folder.

``estimate_all_fields_prune`` (CLI ``--estimate``) writes nothing and reports the space a prune would free per day;
``--measure`` rebuilds each day's kept runs in a temp folder instead of scaling, sizes it and deletes it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from db.trading_view_all_fields_duckdb import FULL_ALL_FIELDS_MIN_COLUMN_COUNT  # noqa: E402
from generic_utils.session_marks import us_phase  # noqa: E402

DATA_DIR = SRC_DIR.parent / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
DEFAULT_KEEP = 3
DEFAULT_MAX_FULL = 2
PRUNE_LOG_NAME = "_prune_log.jsonl"
Strategy = Literal["spread", "newest"]


@dataclass
class RunInfo:
    run_id: str
    created_at_utc: datetime | None
    n_columns: int
    rows: int | None

    @property
    def full(self) -> bool:
        """Full-catalog run. A run without ``api_request_columns_json`` (the CSV backfills before the focused export
        existed) counts as full: those were full-catalog exports."""
        return self.n_columns == 0 or self.n_columns >= FULL_ALL_FIELDS_MIN_COLUMN_COUNT

    @property
    def phase(self) -> str | None:
        return us_phase(self.created_at_utc)


@dataclass
class DayPlan:
    day: str
    database_path: Path
    runs: list[RunInfo] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)
    drop: list[str] = field(default_factory=list)
    why: dict[str, str] = field(default_factory=dict)
    skipped: str | None = None
    size_before: int = 0
    size_after: int | None = None


def _column_count(payload: Any) -> int:
    if not payload:
        return 0
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError):
        return 0
    return len(parsed) if isinstance(parsed, list) else 0


def _parse_day(label: str) -> datetime | None:
    try:
        return datetime.strptime(label, "%d_%m_%Y")
    except ValueError:
        return None


def read_runs(database_path: Path) -> list[RunInfo]:
    """Runs of one day file, oldest first (read-only)."""
    import duckdb

    con = duckdb.connect(str(database_path), read_only=True)
    try:
        cols = {r[0] for r in con.execute("DESCRIBE run_metadata").fetchall()}
        cols_json = "api_request_columns_json" if "api_request_columns_json" in cols else "CAST(NULL AS VARCHAR)"
        count = "scan_data_count" if "scan_data_count" in cols else "CAST(NULL AS BIGINT)"
        rows = con.execute(
            f"SELECT run_id, created_at_utc, {cols_json}, {count} FROM run_metadata "
            "ORDER BY created_at_utc NULLS FIRST, run_id"
        ).fetchall()
    finally:
        con.close()
    return [RunInfo(str(r[0]), r[1], _column_count(r[2]), r[3]) for r in rows]


def choose_runs(runs: Sequence[RunInfo], keep: int = DEFAULT_KEEP, strategy: Strategy = "spread",
                max_full: int = DEFAULT_MAX_FULL) -> dict[str, str]:
    """run_id -> why it stays. Every run not in the result is dropped."""
    newest_first = sorted(runs, key=lambda r: (r.created_at_utc or datetime.min, r.run_id), reverse=True)
    chosen: dict[str, str] = {}
    if not newest_first:
        return chosen
    fulls = [r for r in newest_first if r.full][: max(1, min(max_full, keep))]
    for i, run in enumerate(fulls):
        chosen[run.run_id] = "newest_full" if i == 0 else f"full_{i + 1}"
    if newest_first[0].run_id not in chosen and not newest_first[0].full:
        chosen[newest_first[0].run_id] = "newest"
    focused = [r for r in newest_first if not r.full]
    if strategy == "spread":
        seen = {r.phase for r in newest_first if r.run_id in chosen}  # a kept full run covers its phase too
        for run in focused:
            if len(chosen) >= keep:
                break
            if run.phase and run.phase not in seen and run.run_id not in chosen:
                chosen[run.run_id] = f"last_{run.phase}"
                seen.add(run.phase)
    for run in focused:
        if len(chosen) >= keep:
            break
        if run.run_id not in chosen:
            chosen[run.run_id] = "newest_rest"
    return chosen


def day_files(data_dir: Path = DATA_DIR, days: Sequence[str] | None = None) -> list[Path]:
    """Day DuckDB files (DD_MM_YYYY folders), oldest day first."""
    wanted = set(days or [])
    out = []
    for folder in data_dir.iterdir():
        stamp = _parse_day(folder.name) if folder.is_dir() else None
        if stamp is None or (wanted and folder.name not in wanted):
            continue
        path = folder / f"tradingview_all_fields_{folder.name}.duckdb"
        if path.exists():
            out.append((stamp, path))
    return [p for _, p in sorted(out)]


def plan_day(database_path: Path, keep: int = DEFAULT_KEEP, strategy: Strategy = "spread",
             today: str | None = None, max_full: int = DEFAULT_MAX_FULL) -> DayPlan:
    day = database_path.parent.name
    plan = DayPlan(day=day, database_path=database_path, size_before=database_path.stat().st_size)
    if day == (today or datetime.now(timezone.utc).strftime("%d_%m_%Y")):
        plan.skipped = "today (an export may still append)"
        return plan
    if database_path.with_name(database_path.name + ".write.lock").exists():
        plan.skipped = "writer lock present"
        return plan
    if database_path.with_name(database_path.name + ".wal").exists():
        plan.skipped = "wal file present (writer open or not closed cleanly)"
        return plan
    try:
        plan.runs = read_runs(database_path)
    except Exception as exc:  # a file that does not open is reported, never rewritten
        plan.skipped = f"unreadable: {str(exc)[:120]}"
        return plan
    if len(plan.runs) <= keep and sum(1 for r in plan.runs if r.full) <= max_full:
        plan.keep = [r.run_id for r in plan.runs]
        return plan
    plan.why = choose_runs(plan.runs, keep, strategy, max_full)
    plan.keep = [r.run_id for r in plan.runs if r.run_id in plan.why]
    plan.drop = [r.run_id for r in plan.runs if r.run_id not in plan.why]
    return plan


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _rewrite(database_path: Path, keep: Sequence[str], target: Path | None = None) -> dict[str, int]:
    """Copy the file with only the kept runs into ``target`` (default ``<file>.prune.tmp``); returns rows per table
    in the copy. The source is opened read-only."""
    import duckdb

    tmp = target or database_path.with_name(database_path.name + ".prune.tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"ATTACH '{database_path.as_posix()}' AS src (READ_ONLY)")
        con.execute(f"ATTACH '{tmp.as_posix()}' AS dst")
        con.execute("COPY FROM DATABASE src TO dst (SCHEMA)")
        con.execute("CREATE TEMP TABLE keep_ids AS SELECT unnest(?::VARCHAR[]) AS run_id", [list(keep)])
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name = 'src' AND schema_name = 'main' "
            "ORDER BY table_name").fetchall()]
        counts: dict[str, int] = {}
        for table in tables:
            cols = [r[0] for r in con.execute(
                "SELECT column_name FROM duckdb_columns() WHERE database_name = 'src' AND schema_name = 'main' "
                "AND table_name = ? ORDER BY column_index", [table]).fetchall()]
            col_sql = ", ".join(_quote(c) for c in cols)
            where = " WHERE run_id IN (SELECT run_id FROM keep_ids)" if "run_id" in cols else ""
            con.execute(f"INSERT INTO dst.{_quote(table)} ({col_sql}) SELECT {col_sql} FROM src.{_quote(table)}{where}")
            want = con.execute(f"SELECT count(*) FROM src.{_quote(table)}{where}").fetchone()[0]
            got = con.execute(f"SELECT count(*) FROM dst.{_quote(table)}").fetchone()[0]
            if want != got:
                raise RuntimeError(f"{table}: copied {got} rows, expected {want}")
            counts[table] = int(got)
        kept_runs = {r[0] for r in con.execute("SELECT run_id FROM dst.run_metadata").fetchall()}
        if kept_runs != set(keep):
            raise RuntimeError(f"run_metadata in the copy holds {sorted(kept_runs)}, expected {sorted(keep)}")
        con.execute("CHECKPOINT dst")
        con.execute("DETACH dst")
        con.execute("DETACH src")
    except Exception:
        con.close()
        tmp.unlink(missing_ok=True)
        raise
    con.close()
    return counts


def _reexport_parquet(database_path: Path, run_id: str) -> int:
    parquet_dir = database_path.parent / "parquet"
    if not parquet_dir.is_dir():
        return 0
    from db.trading_view_all_fields_duckdb import TradingViewAllFieldsDuckDBStore

    with TradingViewAllFieldsDuckDBStore(database_path=database_path, parquet_dir=parquet_dir) as store:
        return len(store.export_tables_to_parquet(run_id=run_id, parquet_dir=parquet_dir))


def _newest_run_id(database_path: Path) -> str:
    runs = read_runs(database_path)
    return max(runs, key=lambda r: (r.created_at_utc or datetime.min, r.run_id)).run_id


def rewrite_day(plan: DayPlan, *, keep_backup: bool = False) -> dict[str, Any]:
    """Rewrite one day file to its kept runs under the exporter's writer lock and re-export ``parquet/``. Run
    folders are left alone (``remove_run_folders``, after the checks pass)."""
    from data_analysis_scripts.trading_view_export_all_tdfields import _duckdb_daily_writer_lock

    db = plan.database_path
    with _duckdb_daily_writer_lock(db):
        counts = _rewrite(db, plan.keep)
        tmp = db.with_name(db.name + ".prune.tmp")
        if keep_backup:
            os.replace(db, db.with_name(db.name + ".prune.bak"))
        os.replace(tmp, db)
        n_parquet = _reexport_parquet(db, _newest_run_id(db))
    plan.size_after = db.stat().st_size
    return {"rows_after": counts, "parquet_tables": n_parquet}


def remove_run_folders(plan: DayPlan) -> int:
    removed = 0
    for run_id in plan.drop:
        folder = plan.database_path.parent / "runs" / run_id
        if folder.is_dir():
            shutil.rmtree(folder)
            removed += 1
    return removed


def _log(data_dir: Path, record: dict[str, Any]) -> None:
    with (data_dir / PRUNE_LOG_NAME).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def apply_plan(plan: DayPlan, *, keep_backup: bool = False, keep_run_folders: bool = False,
               data_dir: Path = DATA_DIR, manifest_dir: Path | None = None) -> dict[str, Any]:
    """Snapshot, rewrite, validate one day; remove the dropped runs' folders only once the data checks pass. On a
    failed check the day is rolled back from its backup when one was kept, and the result says so."""
    from data_analysis_scripts import trading_view_all_fields_prune_validate as val

    if plan.skipped or not plan.drop:
        return {"day": plan.day, "status": plan.skipped or "nothing_to_drop", "checks": 0, "failed": []}
    before = val.snapshot_day(plan.database_path, plan.keep, plan.drop)
    if manifest_dir is not None:
        (manifest_dir / f"{plan.day}.before.json").write_text(json.dumps(before, indent=1), encoding="utf-8")
    done = rewrite_day(plan, keep_backup=keep_backup)
    findings = val.validate_day(before, check_folders=False)
    removed = 0
    if not val.failed(findings) and not keep_run_folders:
        removed = remove_run_folders(plan)
        findings += val.validate_folders(before)
    bad = val.failed(findings)
    rollback = val.rollback_day(plan.database_path) if bad and keep_backup else None
    record = {
        "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "day": plan.day,
        "status": "pruned" if not bad else "FAILED",
        "kept": {r: plan.why.get(r, "") for r in plan.keep},
        "dropped": plan.drop,
        **done,
        "bytes_before": plan.size_before,
        "bytes_after": plan.size_after,
        "run_folders_removed": removed,
        "backup": keep_backup,
        "checks": len(findings),
        "failed": bad,
        "rollback": rollback,
    }
    if manifest_dir is not None:
        (manifest_dir / f"{plan.day}.checks.json").write_text(json.dumps(findings, indent=1), encoding="utf-8")
    _log(data_dir, record)
    return record


def _dir_bytes(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else 0


def focused_row_bytes(plans: Sequence[DayPlan]) -> tuple[float, float]:
    """Bytes per row of a focused run in the DuckDB file and in ``parquet/``: the median over day files that hold
    focused runs only (each with a recorded column list), so the estimate is calibrated on this data folder, not on a
    column count. ``parquet/`` counts only days whose ``all_fields_rows.parquet`` exists."""
    import statistics

    db_unit, pq_unit = [], []
    for plan in plans:
        rows = sum(int(r.rows or 0) for r in plan.runs)
        if not plan.runs or rows <= 0 or any(r.full for r in plan.runs):
            continue
        db_unit.append(plan.size_before / rows)
        if (plan.database_path.parent / "parquet" / "all_fields_rows.parquet").exists():
            pq_unit.append(_dir_bytes(plan.database_path.parent / "parquet") / rows)
    return (statistics.median(db_unit) if db_unit else 0.0, statistics.median(pq_unit) if pq_unit else 0.0)


def _kept_share(plan: DayPlan, before: int, unit: float) -> float:
    """Kept runs' share of one file: a focused run costs ``unit`` x its rows, full runs split what is left by rows."""
    rows = {r.run_id: float(r.rows or 0) for r in plan.runs}
    fulls = [r.run_id for r in plan.runs if r.full]
    if not before or not sum(rows.values()):
        return 1.0
    if not fulls or unit <= 0:
        return sum(rows[r] for r in plan.keep) / sum(rows.values())
    focused_bytes = {r.run_id: unit * rows[r.run_id] for r in plan.runs if not r.full}
    left = max(before - sum(focused_bytes.values()), 0.0)
    full_rows = sum(rows[r] for r in fulls) or 1.0
    cost = {**focused_bytes, **{r: left * rows[r] / full_rows for r in fulls}}
    return min(sum(cost[r] for r in plan.keep) / (sum(cost.values()) or 1.0), 1.0)


def _mix(plan: DayPlan, ids: Sequence[str]) -> str:
    by_id = {r.run_id: r for r in plan.runs}
    n_full = sum(1 for r in ids if by_id[r].full)
    return f"{n_full}F+{len(ids) - n_full}f"


def estimate_day(plan: DayPlan, *, measure: bool = False, scratch_dir: Path | None = None,
                 units: tuple[float, float] = (0.0, 0.0)) -> dict[str, Any]:
    """Bytes a prune would free on one day, never touching the day folder.

    estimate: a focused run costs ``units`` (bytes per row in the DuckDB file / ``parquet/``, from
    ``focused_row_bytes``) and the full runs of the day split the rest of the file; ``runs/<id>/`` of dropped runs is
    counted exactly. A rewrite also drops free blocks left by earlier writes, so the real file is usually a little
    smaller. measure: the kept runs are copied into a file under ``scratch_dir`` (and its parquet export beside it),
    sized, then deleted.
    """
    db = plan.database_path
    parquet_before = _dir_bytes(db.parent / "parquet")
    runs_freed = sum(_dir_bytes(db.parent / "runs" / r) for r in plan.drop)
    db_share = _kept_share(plan, plan.size_before, units[0])
    pq_share = _kept_share(plan, parquet_before, units[1])
    out: dict[str, Any] = {
        "day": plan.day, "runs": len(plan.runs), "keep": len(plan.keep), "drop": len(plan.drop),
        "mix_before": _mix(plan, [r.run_id for r in plan.runs]), "mix_after": _mix(plan, plan.keep),
        "full_runs": sum(1 for r in plan.runs if r.full), "kept_share": round(db_share, 4),
        "db_before": plan.size_before, "parquet_before": parquet_before, "runs_freed": runs_freed,
        "db_after": int(plan.size_before * db_share), "parquet_after": int(parquet_before * pq_share),
        "method": "estimate",
    }
    if measure:
        import tempfile

        with tempfile.TemporaryDirectory(dir=scratch_dir) as tmp:
            target = Path(tmp) / db.name
            _rewrite(db, plan.keep, target)
            out["db_after"] = target.stat().st_size
            if parquet_before:
                newest = next((r for r, why in plan.why.items() if why == "newest"), plan.keep[-1])
                from db.trading_view_all_fields_duckdb import TradingViewAllFieldsDuckDBStore

                with TradingViewAllFieldsDuckDBStore(database_path=target, parquet_dir=Path(tmp) / "parquet") as store:
                    store.export_tables_to_parquet(run_id=newest, parquet_dir=Path(tmp) / "parquet")
                out["parquet_after"] = _dir_bytes(Path(tmp) / "parquet")
        out["method"] = "measured"
    before = out["db_before"] + out["parquet_before"]
    out["freed"] = before - out["db_after"] - out["parquet_after"] + runs_freed
    return out


def estimate_all_fields_prune(
    keep: int = DEFAULT_KEEP,
    *,
    strategy: Strategy = "spread",
    days: Sequence[str] | None = None,
    data_dir: Path = DATA_DIR,
    max_full: int = DEFAULT_MAX_FULL,
    measure: bool = False,
    scratch_dir: Path | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Storage a prune to ``keep`` scans per day would free, per day and in total. Writes nothing in ``data_dir``."""
    rows = []
    gb = 1e9
    all_plans = [plan_day(path, keep, strategy, max_full=max_full) for path in day_files(data_dir)]
    units = focused_row_bytes([p for p in all_plans if not p.skipped])
    wanted = set(days or [])
    for plan in all_plans:
        if plan.skipped or not plan.drop or (wanted and plan.day not in wanted):
            continue
        row = estimate_day(plan, measure=measure, scratch_dir=scratch_dir, units=units)
        rows.append(row)
        if verbose:
            print(f"{row['day']}: {row['mix_before']:>6} -> {row['mix_after']:<5}"
                  f"  db {row['db_before'] / gb:5.2f} -> {row['db_after'] / gb:5.2f} GB"
                  f"  parquet {row['parquet_before'] / gb:5.2f} -> {row['parquet_after'] / gb:5.2f} GB"
                  f"  freed {row['freed'] / gb:5.2f} GB", flush=True)
    keys = ("db_before", "db_after", "parquet_before", "parquet_after", "runs_freed", "freed")
    total = {k: sum(r[k] for r in rows) for k in keys}
    all_files = day_files(data_dir)
    total["all_day_files_bytes"] = sum(p.stat().st_size + _dir_bytes(p.parent / "parquet") for p in all_files)
    summary = {"method": "measured" if measure else "estimate", "keep": keep, "max_full": max_full,
               "strategy": strategy, "focused_bytes_per_row": {"db": round(units[0], 1), "parquet": round(units[1], 1)},
               "days": len(rows), "runs_dropped": sum(r["drop"] for r in rows), "total": total, "per_day": rows}
    if verbose:
        before = total["db_before"] + total["parquet_before"]
        print(f"{summary['method']} (max {keep} scans, max {max_full} full; F = full, f = focused): {len(rows)} day(s), "
              f"{summary['runs_dropped']} run(s) dropped; "
              f"those days {before / gb:.1f} GB -> {(before - total['freed']) / gb:.1f} GB, "
              f"freed {total['freed'] / gb:.1f} GB "
              f"({total['freed'] / max(total['all_day_files_bytes'], 1):.1%} of all day files + parquet, "
              f"{total['all_day_files_bytes'] / gb:.0f} GB)")
    return summary


def _fmt_run(run: RunInfo, why: str | None) -> str:
    stamp = run.created_at_utc.strftime("%H:%M") if run.created_at_utc else "--:--"
    kind = "full" if run.full else "focused"
    return f"{stamp}Z {run.phase or '?':6} {kind:7} {'KEEP ' + why if why else 'drop'}  {run.run_id}"


def prune_all_fields_day_runs(
    keep: int = DEFAULT_KEEP,
    apply: bool = False,
    *,
    strategy: Strategy = "spread",
    days: Sequence[str] | None = None,
    data_dir: Path = DATA_DIR,
    max_full: int = DEFAULT_MAX_FULL,
    keep_backup: bool = False,
    keep_run_folders: bool = False,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Keep ``keep`` scans per day file where a day has more; dry run unless ``apply``. Returns one dict per day
    that has more than ``keep`` runs or was skipped."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    out: list[dict[str, Any]] = []
    total_before = total_after = 0
    manifest_dir = tree_exclude = tree_before = None
    if apply:
        from datetime import timedelta

        from data_analysis_scripts import trading_view_all_fields_prune_validate as val

        stamp = datetime.now(timezone.utc)
        manifest_dir = data_dir / val.MANIFEST_DIR_NAME / stamp.strftime("%Y%m%d_%H%M%S")
        manifest_dir.mkdir(parents=True, exist_ok=True)
        targets = [p.parent.name for p in day_files(data_dir, days)]
        tree_exclude = targets + [(stamp + timedelta(days=k)).strftime("%d_%m_%Y") for k in (0, 1)]
        tree_before = val.snapshot_tree(data_dir, tree_exclude)
        (manifest_dir / "tree.before.json").write_text(json.dumps(
            {"data_dir": str(data_dir), "exclude": tree_exclude, "files": tree_before}), encoding="utf-8")
        if verbose:
            print(f"manifest: {manifest_dir} ({len(tree_before)} other files fingerprinted)")
    for path in day_files(data_dir, days):
        plan = plan_day(path, keep, strategy, max_full=max_full)
        if not plan.drop and not plan.skipped:
            continue
        if plan.skipped:
            if verbose and plan.skipped != "today (an export may still append)":
                print(f"{plan.day}: skipped, {plan.skipped}")
            out.append({"day": plan.day, "status": "skipped", "reason": plan.skipped})
            continue
        if verbose:
            print(f"{plan.day}: {len(plan.runs)} runs -> keep {len(plan.keep)}, drop {len(plan.drop)} "
                  f"({plan.size_before / 1e9:.2f} GB)")
            for run in plan.runs:
                print("   ", _fmt_run(run, plan.why.get(run.run_id)))
        if apply:
            result = apply_plan(plan, keep_backup=keep_backup, keep_run_folders=keep_run_folders, data_dir=data_dir,
                                manifest_dir=manifest_dir)
            total_before += plan.size_before
            total_after += plan.size_after or plan.size_before
            if verbose:
                print(f"    rewritten: {plan.size_before / 1e9:.2f} GB -> {(plan.size_after or 0) / 1e9:.2f} GB, "
                      f"{result['checks'] - len(result['failed'])}/{result['checks']} checks passed", flush=True)
                for f in result["failed"]:
                    print(f"    FAIL {f['check']}: {f['detail']}")
            out.append(result)
            if result["failed"]:
                print(f"stopped: {plan.day} failed validation ({result['rollback'] or 'no backup kept'})")
                break
        else:
            out.append({"day": plan.day, "status": "dry_run", "kept": {r: plan.why.get(r, "") for r in plan.keep},
                        "dropped": plan.drop, "bytes_before": plan.size_before})
    if apply and tree_before is not None:
        tree_findings = val.validate_tree(data_dir, tree_before, tree_exclude)
        (manifest_dir / "tree.checks.json").write_text(json.dumps(tree_findings, indent=1), encoding="utf-8")
        out.append({"day": "other files", "status": "checked", "checks": len(tree_findings),
                    "failed": val.failed(tree_findings)})
        if verbose:
            for f in tree_findings:
                print(f"other files: {'ok  ' if f['ok'] else 'FAIL'} {f['check']} {'' if f['ok'] else f['detail']}")
    if verbose:
        n = sum(1 for r in out if r["status"] in ("dry_run", "pruned"))
        n_drop = sum(len(r.get("dropped", [])) for r in out)
        if apply:
            print(f"pruned {n} day file(s), {n_drop} run(s) dropped: {total_before / 1e9:.1f} GB -> "
                  f"{total_after / 1e9:.1f} GB")
        else:
            print(f"dry run: {n} day file(s) over {keep} runs, {n_drop} run(s) would go. Pass apply=True / --apply.")
    return out


def prune_all_fields_runs_local_main() -> None:
    """No-arg default: dry run over every day file with more than three scans."""
    prune_all_fields_day_runs(keep=DEFAULT_KEEP, apply=False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="scans to keep per day (default 3)")
    parser.add_argument("--apply", action="store_true", help="rewrite the files (default: dry run)")
    parser.add_argument("--max-full", type=int, default=DEFAULT_MAX_FULL,
                        help="full-catalog scans to keep per day, within --keep (default 2)")
    parser.add_argument("--strategy", choices=["spread", "newest"], default="spread",
                        help="focused slots: spread = newest + last scan of each US session phase; newest = newest N")
    parser.add_argument("--days", default="", help="comma list of DD_MM_YYYY folders (default: all)")
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--keep-backup", action="store_true", help="leave the original as <file>.prune.bak")
    parser.add_argument("--keep-run-folders", action="store_true", help="keep runs/<run_id>/ of dropped runs")
    parser.add_argument("--estimate", action="store_true",
                        help="storage a prune would free (file sizes x kept share); writes nothing")
    parser.add_argument("--measure", action="store_true",
                        help="with --estimate: rebuild each day's kept runs in a temp folder, size it, delete it")
    parser.add_argument("--scratch-dir", default=None, help="temp folder for --measure (default: system temp)")
    parser.add_argument("--rollback", action="store_true",
                        help="put <file>.prune.bak back for --days (or every day that has one), re-export parquet")
    parser.add_argument("--drop-backups", action="store_true",
                        help="delete <file>.prune.bak for --days (or every day that has one) once you are satisfied")
    args = parser.parse_args(argv)
    days = [d.strip() for d in args.days.split(",") if d.strip()] or None
    if args.rollback or args.drop_backups:
        from data_analysis_scripts.trading_view_all_fields_prune_validate import rollback_day

        for path in day_files(Path(args.data_dir), days):
            bak = path.with_name(path.name + ".prune.bak")
            if not bak.exists():
                continue
            if args.rollback:
                print(f"{path.parent.name}: {rollback_day(path)}")
            else:
                size = bak.stat().st_size
                bak.unlink()
                print(f"{path.parent.name}: backup deleted ({size / 1e9:.2f} GB)")
        return 0
    if args.estimate or args.measure:
        estimate_all_fields_prune(args.keep, strategy=args.strategy, days=days, data_dir=Path(args.data_dir),
                                  max_full=args.max_full, measure=args.measure, scratch_dir=Path(args.scratch_dir) if args.scratch_dir else None)
        return 0
    prune_all_fields_day_runs(args.keep, args.apply, strategy=args.strategy, days=days, data_dir=Path(args.data_dir),
                              max_full=args.max_full, keep_backup=args.keep_backup, keep_run_folders=args.keep_run_folders)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
