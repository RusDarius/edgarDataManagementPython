from __future__ import annotations

import duckdb

from edge_research_tools.upside_move_potential_scanner import (
    build_upside_move_potential_scan,
    classify_move_tier,
    compute_catalyst_move_score,
    compute_expected_move_proxy_pct,
    compute_magnitude_component,
    compute_move_potential_fields,
    compute_upside_tilt_component,
    finalize_move_potential_rows,
)


def _build_all_fields_db(db_path, *, rows: list[dict]) -> None:
    conn = duckdb.connect(db_path.as_posix())
    try:
        conn.execute("""
            CREATE TABLE all_fields_rows (
                run_id VARCHAR,
                row_number BIGINT,
                symbol VARCHAR,
                beta_1_year DOUBLE,
                "Volatility.D" DOUBLE,
                "Volatility.W" DOUBLE,
                "Volatility.M" DOUBLE,
                "ATR" DOUBLE,
                "ATRP" DOUBLE,
                "ADX" DOUBLE,
                "RSI" DOUBLE,
                "Perf.5D" DOUBLE,
                "Perf.1M" DOUBLE,
                "Perf.3M" DOUBLE,
                "Recommend.All" DOUBLE,
                "SMA20" DOUBLE,
                "SMA50" DOUBLE,
                close DOUBLE
            )
        """)
        for index, row in enumerate(rows):
            conn.execute(
                """
                INSERT INTO all_fields_rows VALUES
                ('run_1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    index,
                    row["symbol"],
                    row.get("beta_1_year", 1.0),
                    row.get("volatility_d", 2.0),
                    row.get("volatility_w", 3.0),
                    row.get("volatility_m", 4.0),
                    row.get("atr", 2.0),
                    row.get("atrp", 3.0),
                    row.get("adx", 20.0),
                    row.get("rsi", 55.0),
                    row.get("perf_5d", 1.5),
                    row.get("perf_1m", 5.0),
                    row.get("perf_3m", 8.0),
                    row.get("recommend_all", 0.3),
                    row.get("sma20", 95.0),
                    row.get("sma50", 90.0),
                    row.get("close", 100.0),
                ],
            )
    finally:
        conn.close()


def test_magnitude_rewards_high_vol_and_adrp() -> None:
    strong = {
        "adrp_pct_today": 0.9,
        "relvol_pct_today": 0.9,
        "atrp": 5.0,
        "volatility_w": 9.0,
        "volatility_d": 4.0,
        "beta_1_year": 1.8,
    }
    weak = {
        "adrp_pct_today": 0.1,
        "relvol_pct_today": 0.1,
        "atrp": 0.5,
        "volatility_w": 1.0,
        "volatility_d": 0.5,
        "beta_1_year": 0.4,
    }
    assert compute_magnitude_component(strong) > compute_magnitude_component(weak)


def test_tilt_rewards_bullish_recommend_and_perf() -> None:
    strong = {
        "recommend_all_raw": 0.9,
        "mom_core_pct_today": 0.9,
        "perf_5d": 8.0,
        "perf_1m": 20.0,
        "perf_3m": 30.0,
        "rsi": 65.0,
        "sma20": 105.0,
        "sma50": 100.0,
        "close": 110.0,
        "adx": 30.0,
    }
    weak = {
        "recommend_all_raw": -0.9,
        "mom_core_pct_today": 0.1,
        "perf_5d": -8.0,
        "perf_1m": -20.0,
        "perf_3m": -30.0,
        "rsi": 30.0,
        "sma20": 90.0,
        "sma50": 100.0,
        "close": 85.0,
        "adx": 30.0,
    }
    assert compute_upside_tilt_component(strong) > compute_upside_tilt_component(weak)


def test_catalyst_near_earnings_is_move_fuel_not_penalty() -> None:
    assert compute_catalyst_move_score(5) > compute_catalyst_move_score(60)
    assert compute_catalyst_move_score(None) < compute_catalyst_move_score(15)


def test_expected_move_proxy_uses_atrp_sqrt_horizon() -> None:
    row = {"atrp": 2.0}
    five = compute_expected_move_proxy_pct(row, horizon_days=5)
    one = compute_expected_move_proxy_pct(row, horizon_days=1)
    assert one == 2.0
    assert five is not None and abs(five - round(2.0 * (5**0.5), 4)) < 1e-9


def test_finalize_ranks_all_symbols_and_preserves_opportunity_fields() -> None:
    rows = []
    for symbol, mag, tilt, catalyst in [
        ("A", 0.9, 0.9, 1.0),
        ("B", 0.2, 0.2, 0.15),
        ("C", 0.7, 0.8, 0.65),
    ]:
        rows.append(
            {
                "symbol": symbol,
                "move_magnitude_score": mag,
                "move_upside_tilt_score": tilt,
                "move_catalyst_score": catalyst,
                "upside_opportunity_rank": 99 if symbol == "A" else 1,
                "upside_opportunity_score": 0.1,
                "opportunity_tier": "MODERATE_UPSIDE",
                "directional_lean": "LEAN_UP",
            }
        )
    ordered = finalize_move_potential_rows(rows)
    assert [row["symbol"] for row in ordered] == ["A", "C", "B"]
    assert [row["move_upside_rank"] for row in ordered] == [1, 2, 3]
    assert ordered[0]["upside_opportunity_rank"] == 99
    assert ordered[0]["move_tier"] == "HIGH_MOVE_UPSIDE"


def test_classify_move_tier_catalyst_watch() -> None:
    assert (
        classify_move_tier(
            move_upside_score=0.40,
            catalyst_score=0.9,
            magnitude_score=0.5,
        )
        == "CATALYST_MOVE_WATCH"
    )


def test_build_upside_move_potential_scan_writes_separate_duckdb(tmp_path) -> None:
    db_path = tmp_path / "all_fields.duckdb"
    _build_all_fields_db(
        db_path,
        rows=[
            {
                "symbol": "NASDAQ:HOT",
                "atrp": 4.5,
                "volatility_w": 8.0,
                "recommend_all": 0.7,
                "perf_1m": 12.0,
                "rsi": 62.0,
            },
            {
                "symbol": "NASDAQ:COLD",
                "atrp": 0.8,
                "volatility_w": 1.5,
                "recommend_all": -0.5,
                "perf_1m": -8.0,
                "rsi": 35.0,
            },
        ],
    )
    unified = tmp_path / "unified.csv"
    unified.write_text(
        "symbol,adrp_pct_today,relvol_pct_today,mom_core_pct_today,vol_core_pct_today,"
        "sector,industry\n"
        "NASDAQ:HOT,0.9,0.85,0.8,0.7,Technology,Software\n"
        "NASDAQ:COLD,0.2,0.2,0.2,0.2,Utilities,Electric\n",
        encoding="utf-8",
    )
    earnings = tmp_path / "earn.csv"
    earnings.write_text(
        "symbol,earnings_days_until\nNASDAQ:HOT,7\nNASDAQ:COLD,90\n",
        encoding="utf-8",
    )
    opportunity = tmp_path / "opp.csv"
    opportunity.write_text(
        "symbol,upside_opportunity_rank,upside_opportunity_score,opportunity_tier,"
        "directional_lean,opp_momentum_component,opp_binary_risk_score,"
        "opp_downside_risk_score\n"
        "NASDAQ:HOT,2,0.55,MODERATE_UPSIDE,LEAN_UP,0.6,0.2,0.2\n"
        "NASDAQ:COLD,1,0.72,HIGH_CONVICTION_UPSIDE,STRONG_UP,0.7,0.1,0.1\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "upside_opportunity_scan"

    result = build_upside_move_potential_scan(
        output_dir=output_dir,
        unified_csv_path=unified,
        source_database_path=db_path,
        earnings_priority_csv_path=earnings,
        opportunity_candidates_csv_path=opportunity,
        top_count=5,
    )

    assert result["row_count"] == 2
    assert result["database_path"].name == "upside_move_potential_scan.duckdb"
    assert result["database_path"].exists()
    assert (output_dir / "upside_move_potential_candidates.csv").exists()

    conn = duckdb.connect(result["database_path"].as_posix())
    try:
        rows = conn.execute(
            """
            SELECT symbol, move_upside_rank, upside_opportunity_rank, move_tier
            FROM upside_move_potential_candidates
            ORDER BY move_upside_rank
            """
        ).fetchall()
    finally:
        conn.close()

    assert rows[0][0] == "NASDAQ:HOT"
    assert rows[0][1] == 1
    # Opportunity rank carried for aggregate; not used to order move rank.
    assert rows[0][2] == 2
    assert compute_move_potential_fields(
        {
            "adrp_pct_today": 0.9,
            "relvol_pct_today": 0.85,
            "atrp": 4.5,
            "volatility_w": 8.0,
            "recommend_all_raw": 0.7,
            "mom_core_pct_today": 0.8,
            "perf_1m": 12.0,
        },
        earnings_days_until=7,
    )["move_catalyst_score"] == 1.0
