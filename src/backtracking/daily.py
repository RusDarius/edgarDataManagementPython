"""Session closes for names already on the weekly panel.

This table is the day-to-day price path. It is not a selection input.
Forward returns stay off panel_rows.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from backtests.timeline import discover_all_fields_days
from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb

from .reporting import load_run_manifest, resolve_run_dir, save_run_manifest
from .timeline import parse_day_label


def attach_daily_closes(run_dir: str | Path) -> dict[str, Any]:
    run_path = resolve_run_dir(run_dir)
    manifest = load_run_manifest(run_path)
    window = manifest.get("window") or {}
    start = parse_day_label(window.get("start_day_label"))
    end = parse_day_label(window.get("end_day_label"))
    all_fields_root = Path(str((manifest.get("paths") or {}).get("all_fields_root") or ""))
    if not all_fields_root.exists():
        raise FileNotFoundError(f"all_fields_root is missing: {all_fields_root}")

    db_path = run_path / "backtracking.duckdb"
    symbols = {
        str(row.get("symbol") or "")
        for row in query_move_prediction_duckdb(
            db_path,
            "SELECT DISTINCT symbol FROM panel_rows",
        )
        if str(row.get("symbol") or "").strip()
    }
    refs = discover_all_fields_days(
        all_fields_root=all_fields_root,
        start_day_label=window.get("start_day_label"),
        end_day_label=window.get("end_day_label"),
        max_days=None,
    )

    import duckdb

    conn = duckdb.connect(db_path.as_posix())
    try:
        conn.execute("DROP TABLE IF EXISTS daily_closes")
        conn.execute(
            """
            CREATE TABLE daily_closes (
                session_date DATE,
                symbol VARCHAR,
                close DOUBLE
            )
            """
        )
        sessions = 0
        stored = 0
        for session_day in sorted(refs):
            if start is not None and session_day < start:
                continue
            if end is not None and session_day > end:
                continue
            ref = refs[session_day]
            if not ref.run_id:
                continue
            fetched = query_move_prediction_duckdb(
                ref.database,
                """
                SELECT symbol, TRY_CAST(close AS DOUBLE) AS close
                FROM all_fields_rows
                WHERE run_id = ?
                """,
                [ref.run_id],
            )
            batch = [
                (session_day, str(row.get("symbol") or ""), float(row["close"]))
                for row in fetched
                if str(row.get("symbol") or "") in symbols and _positive(row.get("close"))
            ]
            if batch:
                conn.executemany("INSERT INTO daily_closes VALUES (?, ?, ?)", batch)
                stored += len(batch)
            sessions += 1
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_daily_closes_symbol ON daily_closes(symbol)"
        )
    finally:
        conn.close()

    updated = dict(manifest)
    counts = dict(updated.get("counts") or {})
    counts["daily_sessions"] = sessions
    counts["daily_close_rows"] = stored
    updated["counts"] = counts
    save_run_manifest(run_path, updated)
    return {
        "run_dir": run_path.as_posix(),
        "sessions": sessions,
        "rows": stored,
        "symbols": len(symbols),
    }


def _positive(value: Any) -> bool:
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number > 0


def load_close_series(run_dir: str | Path) -> dict[str, list[tuple[date, float]]]:
    db_path = resolve_run_dir(run_dir) / "backtracking.duckdb"
    rows = query_move_prediction_duckdb(
        db_path,
        """
        SELECT session_date, symbol, close
        FROM daily_closes
        ORDER BY symbol, session_date
        """,
    )
    series: dict[str, list[tuple[date, float]]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        session = row.get("session_date")
        if hasattr(session, "date") and not isinstance(session, date):
            session = session.date()
        if isinstance(session, str):
            session = date.fromisoformat(session[:10])
        price = row.get("close")
        if not symbol or not isinstance(session, date) or not _positive(price):
            continue
        series.setdefault(symbol, []).append((session, float(price)))
    return series
