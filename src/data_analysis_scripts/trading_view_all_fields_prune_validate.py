"""Validation for ``trading_view_all_fields_prune_runs``: proves a prune removed only the runs it planned to remove,
left every kept run byte-for-byte the same in content, and touched nothing outside the pruned day files.

Before a day is rewritten, ``snapshot_day`` records per table its column names and types (in order), its row count
and, per run, the row count and a content fingerprint (``sum(hash(row))`` over every column of every row: order free,
any changed, lost or duplicated cell changes it), plus the run folders, the parquet files and which run readers
resolve as newest / newest full. ``snapshot_tree`` records size and mtime of every other file in the data folder.

After the rewrite, ``validate_day`` checks:

- the file opens; no ``.prune.tmp`` or writer lock is left behind;
- the same tables with the same columns, types and order;
- ``run_metadata`` holds exactly the kept runs; no dropped run is left in any table;
- every kept run has the same row count and fingerprint in every table as before (``parquet_exports``, the log the
  parquet re-export rewrites, is checked for dropped runs only);
- readers still resolve the same newest and newest full-catalog run;
- ``parquet/`` (when the day had one) holds exactly the kept runs, each matching the DuckDB fingerprint;
- dropped runs' folders are gone, kept runs' folders are still there.

``validate_tree`` checks that no other file in the data folder changed, appeared or went missing (today's day folder
is left out: an export may write there). ``rollback_day`` puts the ``.prune.bak`` back and re-exports parquet.

What this does not do: it changes nothing except in ``rollback_day``, never decides what to keep, never reads
``.vscode``. A check that fails is a finding; the prune stops on the first day with one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

MANIFEST_DIR_NAME = "_prune_manifests"
SKIP_TREE_NAMES = {MANIFEST_DIR_NAME, "_prune_log.jsonl"}
# bookkeeping the parquet re-export rewrites on purpose: checked for dropped runs only, not for identical content
EXPORT_LOG_TABLES = {"parquet_exports"}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(con: Any) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    for table, col, typ in con.execute(
        "SELECT table_name, column_name, data_type FROM duckdb_columns() "
        "WHERE schema_name = 'main' AND NOT internal ORDER BY table_name, column_index"
    ).fetchall():
        out.setdefault(table, []).append([col, typ])
    return out


def _fingerprints(con: Any, source: str, has_run_id: bool) -> dict[str, list[Any]]:
    """run_id -> [rows, fingerprint] (or {"*": [...]} without a run_id column). Fingerprint as text: it is a HUGEINT."""
    if has_run_id:
        rows = con.execute(f"SELECT run_id, count(*), CAST(sum(hash(t)) AS VARCHAR) FROM {source} t GROUP BY run_id").fetchall()
        return {str(r[0]): [int(r[1]), r[2] or "0"] for r in rows}
    row = con.execute(f"SELECT count(*), CAST(sum(hash(t)) AS VARCHAR) FROM {source} t").fetchone()
    return {"*": [int(row[0]), row[1] or "0"]}


def _resolve(con: Any) -> dict[str, Any]:
    from db.trading_view_all_fields_duckdb import resolve_latest_and_full_all_fields_run_ids

    try:
        return resolve_latest_and_full_all_fields_run_ids(con)
    except Exception as exc:  # reported as a finding by the comparison, not raised
        return {"error": str(exc)[:200]}


def _parquet_state(day_dir: Path, con: Any) -> dict[str, Any]:
    folder = day_dir / "parquet"
    if not folder.is_dir():
        return {}
    files = sorted(p.name for p in folder.glob("*.parquet"))
    state: dict[str, Any] = {"files": files}
    afr = folder / "all_fields_rows.parquet"
    if afr.exists():
        state["all_fields_rows"] = _fingerprints(con, f"read_parquet('{afr.as_posix()}')", True)
    meta = folder / "run_metadata.parquet"
    if meta.exists():
        state["run_metadata_runs"] = sorted(
            r[0] for r in con.execute(f"SELECT DISTINCT run_id FROM read_parquet('{meta.as_posix()}')").fetchall())
    return state


def snapshot_day(database_path: Path, keep: Sequence[str], drop: Sequence[str]) -> dict[str, Any]:
    """Everything ``validate_day`` compares against, read before the rewrite (read-only)."""
    import duckdb

    day_dir = database_path.parent
    con = duckdb.connect(str(database_path), read_only=True)
    try:
        tables = _tables(con)
        content = {t: _fingerprints(con, _q(t), any(c[0] == "run_id" for c in cols)) for t, cols in tables.items()}
        resolved = _resolve(con)
        parquet = _parquet_state(day_dir, con)
    finally:
        con.close()
    runs_dir = day_dir / "runs"
    return {
        "day": day_dir.name,
        "database_path": str(database_path),
        "taken_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bytes": database_path.stat().st_size,
        "keep": list(keep),
        "drop": list(drop),
        "schema": tables,
        "content": content,
        "resolved": resolved,
        "parquet": parquet,
        "run_folders": sorted(p.name for p in runs_dir.iterdir() if p.is_dir()) if runs_dir.is_dir() else [],
    }


def _check(findings: list[dict[str, Any]], name: str, ok: bool, detail: str = "") -> None:
    findings.append({"check": name, "ok": bool(ok), "detail": detail})


def validate_day(before: dict[str, Any], *, check_folders: bool = True) -> list[dict[str, Any]]:
    """One row per check: {"check", "ok", "detail"}."""
    import duckdb

    db = Path(before["database_path"])
    day_dir = db.parent
    keep, drop = set(before["keep"]), set(before["drop"])
    out: list[dict[str, Any]] = []
    _check(out, "no temp copy left", not db.with_name(db.name + ".prune.tmp").exists())
    _check(out, "no writer lock left", not db.with_name(db.name + ".write.lock").exists())
    try:
        con = duckdb.connect(str(db), read_only=True)
    except Exception as exc:
        _check(out, "file opens", False, str(exc)[:200])
        return out
    try:
        _check(out, "file opens", True)
        tables = _tables(con)
        lost = sorted(set(before["schema"]) - set(tables))
        _check(out, "no table lost", not lost, f"missing: {lost}")
        # the store's base schema (re-opened for the parquet export) creates its bookkeeping tables when absent
        added = sorted(set(tables) - set(before["schema"]))
        non_empty = [t for t in added if t != "parquet_exports" and con.execute(f"SELECT count(*) FROM {_q(t)}").fetchone()[0]]
        _check(out, "tables added are empty bookkeeping tables", not non_empty, f"added {added}, with rows {non_empty}")
        for table, cols in before["schema"].items():
            after_cols = tables.get(table)
            if after_cols is None:
                continue
            same = [list(c) for c in after_cols] == [list(c) for c in cols]
            _check(out, f"{table}: same columns, types and order", same,
                   "" if same else f"{len(cols)} columns before, {len(after_cols)} after")
        runs_now = {r[0] for r in con.execute("SELECT run_id FROM run_metadata").fetchall()}
        _check(out, "run_metadata holds exactly the kept runs", runs_now == keep,
               f"extra {sorted(runs_now - keep)}, missing {sorted(keep - runs_now)}")
        for table, prior in before["content"].items():
            if table not in tables:
                continue
            has_run = any(c[0] == "run_id" for c in tables[table])
            now = _fingerprints(con, _q(table), has_run)
            if not has_run:
                _check(out, f"{table}: unchanged (no run_id column)", now == prior, f"before {prior}, after {now}")
                continue
            left = sorted(set(now) & drop)
            _check(out, f"{table}: no dropped run left", not left, f"still there: {left}")
            stray = sorted(set(now) - keep - drop)
            _check(out, f"{table}: no unknown run", not stray, f"unexpected: {stray}")
            if table in EXPORT_LOG_TABLES:  # rewritten by the parquet re-export (new exported_at for the newest run)
                continue
            for run_id in sorted(keep):
                want, got = prior.get(run_id), now.get(run_id)
                _check(out, f"{table}: kept run {run_id} identical", want == got,
                       "" if want == got else f"before {want}, after {got}")
        resolved = _resolve(con)
        _check(out, "readers resolve the same newest / full run", resolved == before["resolved"],
               f"before {before['resolved']}, after {resolved}")
        if before["parquet"]:
            par = _parquet_state(day_dir, con)
            lost_files = sorted(set(before["parquet"].get("files", [])) - set(par.get("files", [])))
            _check(out, "parquet: no file lost", not lost_files, f"missing: {lost_files}")
            if "all_fields_rows" in before["parquet"] or "all_fields_rows" in par:
                db_rows = _fingerprints(con, "all_fields_rows", True)
                pq_rows = par.get("all_fields_rows", {})
                _check(out, "parquet: all_fields_rows holds exactly the kept runs", set(pq_rows) == keep,
                       f"parquet runs {sorted(pq_rows)}")
                _check(out, "parquet: all_fields_rows matches the DuckDB rows", pq_rows == db_rows)
            if "run_metadata_runs" in before["parquet"]:
                _check(out, "parquet: run_metadata holds exactly the kept runs",
                       set(par.get("run_metadata_runs", [])) == keep, f"{par.get('run_metadata_runs')}")
    finally:
        con.close()
    if check_folders:
        out.extend(validate_folders(before))
    return out


def validate_folders(before: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    runs_dir = Path(before["database_path"]).parent / "runs"
    now = {p.name for p in runs_dir.iterdir() if p.is_dir()} if runs_dir.is_dir() else set()
    had = set(before["run_folders"])
    left = sorted(now & set(before["drop"]))
    _check(out, "run folders: dropped runs removed", not left, f"still there: {left}")
    lost = sorted((had - set(before["drop"])) - now)
    _check(out, "run folders: no other folder removed", not lost, f"missing: {lost}")
    return out


def snapshot_tree(data_dir: Path, exclude_dirs: Iterable[str]) -> dict[str, list[int]]:
    """relative path -> [size, mtime_ns] for every file under ``data_dir`` outside ``exclude_dirs`` (top-level
    folder names) and the prune's own records."""
    skip = set(exclude_dirs) | SKIP_TREE_NAMES
    out: dict[str, list[int]] = {}
    for top in data_dir.iterdir():
        if top.name in skip:
            continue
        paths = [top] if top.is_file() else (p for p in top.rglob("*") if p.is_file())
        for path in paths:
            st = path.stat()
            out[path.relative_to(data_dir).as_posix()] = [st.st_size, st.st_mtime_ns]
    return out


def validate_tree(data_dir: Path, before: dict[str, list[int]], exclude_dirs: Iterable[str]) -> list[dict[str, Any]]:
    now = snapshot_tree(data_dir, exclude_dirs)
    changed = sorted(k for k in before if k in now and now[k] != before[k])
    missing = sorted(set(before) - set(now))
    added = sorted(set(now) - set(before))
    out: list[dict[str, Any]] = []
    _check(out, f"other files: none changed ({len(before)} checked)", not changed, f"{changed[:10]}")
    _check(out, "other files: none missing", not missing, f"{missing[:10]}")
    _check(out, "other files: none added", not added, f"{added[:10]}")
    return out


def failed(findings: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in findings if not f["ok"]]


def rollback_day(database_path: Path) -> str:
    """Put ``<file>.prune.bak`` back over the pruned file and re-export ``parquet/`` from it. Run folders that were
    removed are not restored (they hold logs only, and the prune removes them only after the data checks pass)."""
    bak = database_path.with_name(database_path.name + ".prune.bak")
    if not bak.exists():
        return f"no backup at {bak}"
    os.replace(bak, database_path)
    from data_analysis_scripts.trading_view_all_fields_prune_runs import _newest_run_id, _reexport_parquet

    _reexport_parquet(database_path, _newest_run_id(database_path))
    return f"restored {database_path.name} from backup"


def validate_manifest(manifest_dir: Path, *, verbose: bool = True) -> list[dict[str, Any]]:
    """Re-run every check of a prune run later from its manifest folder (``_prune_manifests/<stamp>/``)."""
    results = []
    for path in sorted(manifest_dir.glob("*.before.json")):
        if path.name == "tree.before.json":
            continue
        before = json.loads(path.read_text(encoding="utf-8"))
        findings = validate_day(before)
        results.append({"day": before["day"], "failed": failed(findings), "checks": len(findings)})
        if verbose:
            bad = failed(findings)
            print(f"{before['day']}: {len(findings) - len(bad)}/{len(findings)} checks passed"
                  + "".join(f"\n    FAIL {f['check']}: {f['detail']}" for f in bad))
    tree_path = manifest_dir / "tree.before.json"
    if tree_path.exists():
        tree = json.loads(tree_path.read_text(encoding="utf-8"))
        findings = validate_tree(Path(tree["data_dir"]), tree["files"], tree["exclude"])
        results.append({"day": "other files", "failed": failed(findings), "checks": len(findings)})
        if verbose:
            bad = failed(findings)
            print(f"other files: {len(findings) - len(bad)}/{len(findings)} checks passed"
                  + "".join(f"\n    FAIL {f['check']}: {f['detail']}" for f in bad))
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("manifest", help="a _prune_manifests/<stamp> folder written by the prune")
    args = parser.parse_args(argv)
    results = validate_manifest(Path(args.manifest))
    return 1 if any(r["failed"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
