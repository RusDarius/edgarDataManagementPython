import csv
from pathlib import Path

import duckdb

from edge_research_tools.blindspot_lane import run_edge_blindspot_lane


def _build_snapshot_database(database_path: Path) -> None:
    """Two symbols, ten sequential days: one persistently trending but currently
    quiet (not quant-hot), one persistently trending but currently quant-hot.
    """
    conn = duckdb.connect(database_path.as_posix())
    try:
        conn.execute("""
            CREATE TABLE symbol_day_feature_snapshot (
                symbol VARCHAR, bare_ticker VARCHAR, company_name VARCHAR,
                exchange VARCHAR, country VARCHAR, sector VARCHAR, industry VARCHAR,
                source_day_label VARCHAR, source_date DATE,
                close_price DOUBLE, perf_5d DOUBLE, perf_1m DOUBLE, perf_3m DOUBLE,
                volume DOUBLE, average_volume_30d_calc DOUBLE,
                relative_volume_10d_calc DOUBLE, value_traded DOUBLE,
                market_cap_basic DOUBLE
            )
            """)
        conn.execute("""
            CREATE TABLE symbol_day_sleeve_scores (
                source_date DATE, source_day_label VARCHAR, symbol VARCHAR,
                sleeve_name VARCHAR, avg_directional_universe_percentile DOUBLE
            )
            """)

        rows = []
        for day_index in range(1, 11):
            source_date = f"2026-01-{day_index:02d}"
            source_day_label = f"day{day_index:02d}"
            rows.append((
                "NASDAQ:QUIET", "QUIET", "Quiet Mover Inc",
                "NASDAQ", "US", "Transportation", "Airlines",
                source_day_label, source_date,
                100.0 + day_index, 2.0, 5.0, 8.0,
                500_000.0, 100_000.0 + day_index * 5_000.0,
                1.1, 5_000_000.0 + day_index * 100_000.0,
                5_000_000_000.0,
            ))
            rows.append((
                "NASDAQ:HOT", "HOT", "Hot Mover Inc",
                "NASDAQ", "US", "Technology", "Semiconductors",
                source_day_label, source_date,
                200.0 + day_index, 2.0, 5.0, 8.0,
                800_000.0, 150_000.0 + day_index * 5_000.0,
                1.1, 9_000_000.0 + day_index * 100_000.0,
                8_000_000_000.0,
            ))
        conn.executemany(
            "INSERT INTO symbol_day_feature_snapshot VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?::DATE, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

        conn.executemany(
            "INSERT INTO symbol_day_sleeve_scores VALUES (?::DATE, ?, ?, ?, ?)",
            [
                ("2026-01-10", "day10", "NASDAQ:QUIET", "volatility_core", 0.2),
                ("2026-01-10", "day10", "NASDAQ:QUIET", "liquidity_core", 0.2),
                ("2026-01-10", "day10", "NASDAQ:HOT", "volatility_core", 0.9),
                ("2026-01-10", "day10", "NASDAQ:HOT", "liquidity_core", 0.9),
            ],
        )
    finally:
        conn.close()


def test_run_edge_blindspot_lane_flags_persistent_but_not_hot_symbol(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "symbol_day_feature_snapshot.duckdb"
    _build_snapshot_database(database_path)

    result = run_edge_blindspot_lane(
        snapshot_database_path=database_path,
        output_root=tmp_path / "edge_output",
        as_of_date="2026-01-10",
        ranking_horizon=5,
        shortlist_symbols=["NASDAQ:HOT"],
        lookback_trading_days=5,
        persistence_window_days=3,
        persistence_threshold=0.65,
        hot_liquidity_pct_ceiling=0.55,
        hot_volatility_pct_ceiling=0.55,
        industry_min_symbol_count=1,
        top_count=10,
        duckdb_threads=2,
    )

    assert result["symbol_candidate_count"] == 2
    assert result["blindspot_flag_count"] == 1
    assert result["industry_row_count"] == 2

    with open(result["symbol_csv"], newline="", encoding="utf-8") as handle:
        symbol_rows = {row["symbol"]: row for row in csv.DictReader(handle)}

    quiet_row = symbol_rows["NASDAQ:QUIET"]
    hot_row = symbol_rows["NASDAQ:HOT"]

    assert quiet_row["currently_quant_hot_flag"] == "0"
    assert quiet_row["blindspot_flag"] == "1"
    assert quiet_row["in_shortlist_flag"] == "0"

    assert hot_row["currently_quant_hot_flag"] == "1"
    assert hot_row["blindspot_flag"] == "0"
    assert hot_row["in_shortlist_flag"] == "1"

    with open(result["industry_csv"], newline="", encoding="utf-8") as handle:
        industry_rows = {row["industry"]: row for row in csv.DictReader(handle)}

    assert "Airlines" in industry_rows
    assert "Semiconductors" in industry_rows
    assert industry_rows["Airlines"]["top_quiet_movers"] == "NASDAQ:QUIET"

    assert Path(result["report_md"]).exists()
    assert Path(result["database_path"]).exists()


def test_run_edge_blindspot_lane_raises_when_no_symbols_match_as_of_date(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "symbol_day_feature_snapshot.duckdb"
    _build_snapshot_database(database_path)

    try:
        run_edge_blindspot_lane(
            snapshot_database_path=database_path,
            output_root=tmp_path / "edge_output",
            as_of_date="2099-01-01",
        )
    except ValueError as exc:
        assert "No symbols matched" in str(exc)
    else:
        raise AssertionError("Expected ValueError for an as_of_date with no rows")
