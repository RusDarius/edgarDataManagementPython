from __future__ import annotations

import duckdb

from edge_research_tools.upside_opportunity_scanner import (
    build_upside_opportunity_scan,
    classify_directional_lean,
    classify_opportunity_tier,
    compute_binary_risk_score,
    compute_downside_risk_component,
    compute_historical_validation_component,
    compute_momentum_component,
    compute_opportunity_fields,
    compute_safety_quality_component,
    compute_valuation_component,
    load_supplementary_technical_fields,
    rank_opportunity_rows,
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


def test_load_supplementary_technical_fields_returns_expected_columns(tmp_path) -> None:
    db_path = tmp_path / "all_fields.duckdb"
    _build_all_fields_db(
        db_path,
        rows=[
            {"symbol": "NASDAQ:AAA", "beta_1_year": 1.2, "rsi": 60.0},
            {"symbol": "NASDAQ:BBB", "beta_1_year": 0.8, "rsi": 40.0},
        ],
    )

    result = load_supplementary_technical_fields(
        source_database_path=db_path,
        symbols=["NASDAQ:AAA", "NASDAQ:BBB", "NASDAQ:MISSING"],
    )

    assert set(result.keys()) == {"NASDAQ:AAA", "NASDAQ:BBB"}
    assert result["NASDAQ:AAA"]["beta_1_year"] == 1.2
    assert result["NASDAQ:AAA"]["rsi"] == 60.0
    assert result["NASDAQ:BBB"]["rsi"] == 40.0


def test_compute_momentum_component_rewards_strong_percentiles() -> None:
    strong_row = {
        "adrp_pct_today": 0.9,
        "relvol_pct_today": 0.9,
        "mom_core_pct_today": 0.9,
        "recommend_all_raw": 0.8,
        "perf_1m": 20.0,
    }
    weak_row = {
        "adrp_pct_today": 0.1,
        "relvol_pct_today": 0.1,
        "mom_core_pct_today": 0.1,
        "recommend_all_raw": -0.8,
        "perf_1m": -20.0,
    }
    assert compute_momentum_component(strong_row) > 0.8
    assert compute_momentum_component(weak_row) < 0.2


def test_compute_momentum_component_defaults_missing_optional_fields_neutral() -> None:
    row = {
        "adrp_pct_today": 0.5,
        "relvol_pct_today": 0.5,
        "mom_core_pct_today": 0.5,
    }
    # recommend_all_raw / perf_1m missing -> neutral 0.5 contribution each
    assert compute_momentum_component(row) == 0.5


def test_compute_historical_validation_component_uses_ci_low_nudge() -> None:
    base_row = {
        "upside_hist_win_rate": 0.6,
        "upside_hist_median_fwd_pct": 5.0,
        "historical_edge_hist_win_rate_5d": 0.6,
        "hist_occurrence_count_in_setup": 8,
    }
    positive_ci_row = dict(base_row, lane_leader_median_fwd_5d_ci_low=1.0)
    negative_ci_row = dict(base_row, lane_leader_median_fwd_5d_ci_low=-1.0)

    base_score = compute_historical_validation_component(base_row)
    positive_score = compute_historical_validation_component(positive_ci_row)
    negative_score = compute_historical_validation_component(negative_ci_row)

    assert positive_score == round(base_score + 0.05, 4)
    assert negative_score == round(base_score - 0.05, 4)


def test_compute_valuation_component_discounts_fallback_mode() -> None:
    valuation_mode_row = {
        "forward_valuation_upside_pct": 20.0,
        "forward_upside_mode": "valuation",
    }
    fallback_mode_row = {
        "forward_valuation_upside_pct": 20.0,
        "forward_upside_mode": "fallback",
    }
    missing_row: dict = {}

    assert compute_valuation_component(valuation_mode_row) == 0.5
    assert compute_valuation_component(fallback_mode_row) == round(0.5 * 0.7, 4)
    assert compute_valuation_component(missing_row) == 0.0


def test_compute_safety_quality_component_penalizes_missing_data() -> None:
    missing_row = {"safety_data_available": 0}
    present_row = {
        "safety_data_available": 1,
        "safety_companion_score": 0.8,
        "indicator_pass_count": 11,
    }

    assert compute_safety_quality_component(missing_row) == 0.20
    assert compute_safety_quality_component(present_row) == round(
        (0.65 * 0.8) + (0.35 * 1.0), 4
    )


def test_compute_binary_risk_score_reflects_all_components() -> None:
    row = {
        "safety_data_available": 0,
        "forward_upside_mode": "fallback",
        "forward_valuation_bear_upside_pct": -20.0,
        "forward_valuation_bull_upside_pct": 60.0,
        "vol_core_pct_today": 0.95,
        "sector": "Health Technology",
        "industry": "Biotechnology",
    }
    score = compute_binary_risk_score(row, earnings_days_until=3)
    assert score > 0.8

    clean_row = {
        "safety_data_available": 1,
        "forward_upside_mode": "valuation",
        "forward_valuation_bear_upside_pct": -5.0,
        "forward_valuation_bull_upside_pct": 10.0,
        "vol_core_pct_today": 0.2,
        "sector": "Technology",
        "industry": "Software",
    }
    clean_score = compute_binary_risk_score(clean_row, earnings_days_until=None)
    assert clean_score < 0.2


def test_compute_binary_risk_score_never_exceeds_bounds() -> None:
    row = {
        "safety_data_available": 0,
        "forward_upside_mode": "fallback",
        "forward_valuation_bear_upside_pct": -100.0,
        "forward_valuation_bull_upside_pct": 200.0,
        "vol_core_pct_today": 1.0,
        "sector": "biotechnology",
        "industry": "clinical-stage",
    }
    score = compute_binary_risk_score(row, earnings_days_until=1)
    assert 0.0 <= score <= 1.0


def test_compute_downside_risk_component_scales_with_weakness() -> None:
    weak_row = {"forward_valuation_bear_upside_pct": -30.0}
    strong_row = {"forward_valuation_bear_upside_pct": 10.0}

    weak_score = compute_downside_risk_component(
        weak_row, momentum_component=0.1, safety_quality_component=0.1
    )
    strong_score = compute_downside_risk_component(
        strong_row, momentum_component=0.9, safety_quality_component=0.9
    )
    assert weak_score > strong_score


def test_classify_directional_lean_and_tier_thresholds() -> None:
    assert (
        classify_directional_lean(opportunity_score=0.8, downside_risk_score=0.1)
        == "STRONG_UP"
    )
    assert (
        classify_directional_lean(opportunity_score=0.55, downside_risk_score=0.1)
        == "LEAN_UP"
    )
    assert (
        classify_directional_lean(opportunity_score=0.3, downside_risk_score=0.1)
        == "NEUTRAL"
    )
    assert (
        classify_directional_lean(opportunity_score=0.3, downside_risk_score=0.55)
        == "CAUTION_DOWNSIDE"
    )
    assert (
        classify_directional_lean(opportunity_score=0.2, downside_risk_score=0.7)
        == "AVOID_DOWNSIDE_RISK"
    )

    assert (
        classify_opportunity_tier(
            opportunity_score=0.8,
            binary_risk_score=0.1,
            directional_lean="STRONG_UP",
        )
        == "HIGH_CONVICTION_UPSIDE"
    )
    assert (
        classify_opportunity_tier(
            opportunity_score=0.5,
            binary_risk_score=0.6,
            directional_lean="LEAN_UP",
        )
        == "SPECULATIVE_UPSIDE_BINARY_RISK"
    )
    assert (
        classify_opportunity_tier(
            opportunity_score=0.5,
            binary_risk_score=0.1,
            directional_lean="LEAN_UP",
        )
        == "MODERATE_UPSIDE"
    )
    assert (
        classify_opportunity_tier(
            opportunity_score=0.2,
            binary_risk_score=0.1,
            directional_lean="NEUTRAL",
        )
        == "NEUTRAL_INSUFFICIENT_EDGE"
    )
    assert (
        classify_opportunity_tier(
            opportunity_score=0.2,
            binary_risk_score=0.1,
            directional_lean="AVOID_DOWNSIDE_RISK",
        )
        == "CAUTION_DOWNSIDE_RISK"
    )


def test_compute_opportunity_fields_never_rewrites_upstream_keys() -> None:
    row = {
        "symbol": "NASDAQ:AAA",
        "unified_edge_highlight_rank": 4,
        "adrp_pct_today": 0.8,
        "relvol_pct_today": 0.8,
        "mom_core_pct_today": 0.8,
        "upside_hist_win_rate": 0.7,
        "upside_hist_median_fwd_pct": 6.0,
        "forward_valuation_upside_pct": 25.0,
        "forward_upside_mode": "valuation",
        "safety_data_available": 1,
        "safety_companion_score": 0.7,
        "indicator_pass_count": 9,
    }
    fields = compute_opportunity_fields(row, earnings_days_until=5)

    assert "unified_edge_highlight_rank" not in fields
    assert 0.0 <= fields["upside_opportunity_score"] <= 1.0
    assert fields["directional_lean"] in {
        "STRONG_UP",
        "LEAN_UP",
        "NEUTRAL",
        "CAUTION_DOWNSIDE",
        "AVOID_DOWNSIDE_RISK",
    }
    assert fields["opportunity_tier"] in {
        "HIGH_CONVICTION_UPSIDE",
        "MODERATE_UPSIDE",
        "SPECULATIVE_UPSIDE_BINARY_RISK",
        "CAUTION_DOWNSIDE_RISK",
        "NEUTRAL_INSUFFICIENT_EDGE",
    }
    assert fields["earnings_days_until"] == 5


def test_rank_opportunity_rows_orders_by_score_without_rewriting_inherited_rank() -> (
    None
):
    rows = [
        {
            "symbol": "NASDAQ:LOW",
            "upside_opportunity_score": 0.3,
            "opp_historical_validation_component": 0.2,
            "unified_edge_highlight_rank": 1,
        },
        {
            "symbol": "NASDAQ:HIGH",
            "upside_opportunity_score": 0.9,
            "opp_historical_validation_component": 0.8,
            "unified_edge_highlight_rank": 5,
        },
        {
            "symbol": "NASDAQ:MID",
            "upside_opportunity_score": 0.6,
            "opp_historical_validation_component": 0.5,
            "unified_edge_highlight_rank": 2,
        },
    ]

    ordered = rank_opportunity_rows(rows)

    assert [row["symbol"] for row in ordered] == [
        "NASDAQ:HIGH",
        "NASDAQ:MID",
        "NASDAQ:LOW",
    ]
    assert [row["upside_opportunity_rank"] for row in ordered] == [1, 2, 3]
    high_row = next(row for row in ordered if row["symbol"] == "NASDAQ:HIGH")
    assert high_row["unified_edge_highlight_rank"] == 5


def test_build_upside_opportunity_scan_end_to_end(tmp_path) -> None:
    db_path = tmp_path / "all_fields.duckdb"
    _build_all_fields_db(
        db_path,
        rows=[
            {"symbol": "NASDAQ:STRONG", "rsi": 65.0},
            {"symbol": "NASDAQ:WEAK", "rsi": 35.0},
        ],
    )

    unified_csv = tmp_path / "unified.csv"
    unified_csv.write_text(
        "symbol,unified_edge_highlight_rank,adrp_pct_today,relvol_pct_today,"
        "mom_core_pct_today,vol_core_pct_today,upside_hist_win_rate,"
        "upside_hist_median_fwd_pct,forward_valuation_upside_pct,"
        "forward_upside_mode,safety_data_available,safety_companion_score,"
        "indicator_pass_count,sector,industry\n"
        "NASDAQ:STRONG,1,0.9,0.85,0.8,0.3,0.75,7.0,25.0,valuation,1,0.8,10,"
        "Technology,Software\n"
        "NASDAQ:WEAK,2,0.1,0.1,0.1,0.2,0.2,-2.0,-10.0,valuation,0,,,"
        "Health Technology,Biotechnology\n",
        encoding="utf-8",
    )

    earnings_csv = tmp_path / "earnings_priority.csv"
    earnings_csv.write_text(
        "symbol,earnings_days_until\nNASDAQ:WEAK,4\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "upside_opportunity_scan"
    result = build_upside_opportunity_scan(
        output_dir=output_dir,
        unified_csv_path=unified_csv,
        source_database_path=db_path,
        earnings_priority_csv_path=earnings_csv,
    )

    assert result["row_count"] == 2
    assert result["candidates_csv"].exists()
    assert result["database_path"].exists()
    assert result["report_md"].exists()
    assert result["manifest_path"].exists()

    conn = duckdb.connect(result["database_path"].as_posix(), read_only=True)
    try:
        rows = conn.execute(
            "SELECT symbol, upside_opportunity_rank, opportunity_tier, "
            "directional_lean FROM upside_opportunity_candidates "
            "ORDER BY upside_opportunity_rank"
        ).fetchall()
    finally:
        conn.close()

    assert rows[0][0] == "NASDAQ:STRONG"
    assert rows[1][0] == "NASDAQ:WEAK"
    # The weak/biotech/imminent-earnings row should never be a top-conviction tier.
    assert rows[1][2] != "HIGH_CONVICTION_UPSIDE"
