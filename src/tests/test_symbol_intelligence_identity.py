from pathlib import Path

import duckdb

from data_analysis_scripts.trading_view_symbol_intelligence_report import (
    _find_symbol_row,
    _load_weekly_history,
    _projection_symbol_rows,
)


def _write_all_fields_collision_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(path.as_posix())
    try:
        conn.execute(
            """
            CREATE TABLE run_metadata (
                run_id VARCHAR,
                created_at_utc TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE all_fields_rows (
                run_id VARCHAR,
                row_number BIGINT,
                symbol VARCHAR,
                close DOUBLE,
                market_cap_basic DOUBLE,
                "SMA200" DOUBLE,
                price_52_week_high DOUBLE
            )
            """
        )
        run_id = "focused_intraday_20261008_1700_utc_test"
        conn.execute(
            "INSERT INTO run_metadata VALUES (?, TIMESTAMP '2026-10-08 17:00:00')",
            [run_id],
        )
        conn.executemany(
            "INSERT INTO all_fields_rows VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (run_id, 1, "ASX:MMS", 18.30, 885_486_035, 17.88, 21.50),
                (run_id, 2, "GPW:MMS", 0.259, 768_715, 0.36, 0.50),
                (run_id, 3, "NYSE:MMS", 56.99, 2_983_868_903, 67.14, 100.00),
            ],
        )
    finally:
        conn.close()


def _write_projection_collision_db(path: Path) -> None:
    conn = duckdb.connect(path.as_posix())
    try:
        conn.execute(
            """
            CREATE TABLE projection_summary (
                symbol VARCHAR,
                scenario VARCHAR,
                close DOUBLE,
                market_cap_basic DOUBLE
            )
            """
        )
        conn.executemany(
            "INSERT INTO projection_summary VALUES (?, ?, ?, ?)",
            [
                ("ASX:MMS", "base", 18.30, 885_486_035),
                ("NYSE:MMS", "base", 56.99, 2_983_868_903),
                ("NYSE:MMS", "bear", 56.99, 2_983_868_903),
            ],
        )
    finally:
        conn.close()


def test_weekly_history_keeps_qualified_mms_on_nyse(tmp_path: Path) -> None:
    db_path = (
        tmp_path
        / "08_10_2026"
        / "tradingview_all_fields_08_10_2026.duckdb"
    )
    _write_all_fields_collision_db(db_path)

    history = _load_weekly_history(tmp_path, "NYSE:MMS", weeks=1)

    assert history is not None
    assert history["weeks_with_symbol"] == 1
    assert history["rows"][0]["resolved_symbol"] == "NYSE:MMS"
    assert history["rows"][0]["metrics"]["close"] == 56.99
    assert history["rows"][0]["metrics"]["market_cap_basic"] == 2_983_868_903


def test_csv_lookup_does_not_cross_exchange_for_qualified_symbol() -> None:
    rows = [
        {"symbol": "ASX:MMS", "value": "wrong"},
        {"symbol": "GPW:MMS", "value": "wrong"},
    ]

    assert _find_symbol_row(rows, "NYSE:MMS") is None
    assert _find_symbol_row(rows, "MMS") == rows[0]


def test_projection_lookup_keeps_only_requested_exchange(tmp_path: Path) -> None:
    db_path = tmp_path / "projection.duckdb"
    _write_projection_collision_db(db_path)

    rows = _projection_symbol_rows(
        db_path,
        "projection_summary",
        "NYSE:MMS",
        "MMS",
        order_by="ORDER BY scenario",
    )

    assert [row["symbol"] for row in rows] == ["NYSE:MMS", "NYSE:MMS"]
    assert {row["scenario"] for row in rows} == {"base", "bear"}
