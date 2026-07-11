from edge_research_tools.forward_upside_valuation import (
    build_forward_upside_valuation_rows,
    classify_company_style,
    extract_forward_upside_valuation_overlay_fields,
    forward_upside_valuation_primary_column_names,
    primary_valuation_upside_pct,
    project_forward_upside_valuation_row,
    ranking_horizon_valuation_upside_pct,
    select_active_lenses_for_company,
    sort_rows_by_forward_upside_valuation,
)


def test_select_active_lenses_growth_caps_to_four() -> None:
    availability = {
        "technical": True,
        "multiple": True,
        "trajectory": True,
        "range": True,
        "analyst": True,
        "book_value": True,
        "yield_dcf": True,
    }
    selected = select_active_lenses_for_company(
        company_style="growth",
        lens_availability=availability,
        max_active_lenses=4,
    )
    assert selected == ["trajectory", "multiple", "analyst", "technical"]
    assert len(selected) == 4


def test_company_style_routing_uses_expected_priority() -> None:
    style = classify_company_style(
        {"investment_style_profile": "value_reversion|balanced_core"}
    )
    availability = {
        "technical": True,
        "multiple": True,
        "trajectory": True,
        "range": True,
        "analyst": True,
        "book_value": True,
        "yield_dcf": False,
    }
    selected = select_active_lenses_for_company(
        company_style=style,
        lens_availability=availability,
        max_active_lenses=4,
    )
    assert selected[:2] == ["multiple", "book_value"]
    assert "analyst" in selected
    assert len(selected) <= 4


def test_extract_forward_upside_valuation_overlay_fields_defaults_empty() -> None:
    projected = extract_forward_upside_valuation_overlay_fields(None)
    assert projected["forward_valuation_upside_pct"] == ""
    assert projected["forward_upside_mode"] == ""


def test_extract_forward_upside_valuation_overlay_fields_projects_values() -> None:
    projected = extract_forward_upside_valuation_overlay_fields(
        {
            "forward_upside_rank": 3,
            "forward_upside_mode": "valuation",
            "forward_valuation_upside_pct": 42.5,
            "forward_selected_lenses": "multiple|analyst",
        }
    )
    assert projected["forward_upside_rank"] == 3
    assert projected["forward_upside_mode"] == "valuation"
    assert projected["forward_valuation_upside_pct"] == 42.5
    assert projected["forward_selected_lenses"] == "multiple|analyst"


def test_primary_valuation_upside_pct_blends_horizons() -> None:
    blended = primary_valuation_upside_pct(
        near_upside=10.0,
        medium_upside=20.0,
        long_upside=40.0,
    )
    assert blended == 29.0


def test_ranking_horizon_valuation_upside_pct_maps_short_horizon_to_near() -> None:
    assert (
        ranking_horizon_valuation_upside_pct(
            ranking_horizon=5,
            near_upside=8.0,
            medium_upside=15.0,
            long_upside=30.0,
        )
        == 8.0
    )


def test_build_rows_valuation_from_string_duckdb_fields() -> None:
    highlight_rows = [
        {
            "symbol": "NASDAQ:TEST",
            "big_mover_score": 0.7,
            "confidence_score": 0.6,
            "upside_prediction_score": 0.65,
            "win_rate_5d_in_setup": 0.5,
            "median_fwd_5d_in_setup": 3.0,
            "target_rate_5d_in_setup": 0.4,
            "median_mfe_5d_in_setup": 4.0,
            "median_mae_5d_in_setup": -2.0,
            "lane_win_rate_5d": 0.5,
            "lane_median_fwd_5d": 1.0,
        }
    ]
    source_rows = {
        "NASDAQ:TEST": {
            "symbol": "NASDAQ:TEST",
            "close": "100",
            "SMA10": "105",
            "SMA20": "102",
            "SMA50": "98",
            "SMA200": "90",
            "EMA10": "104",
            "EMA20": "101",
            "EMA50": "97",
            "EMA200": "88",
            "BB.upper": "110",
            "BB.lower": "95",
            "High.1M": "112",
            "Low.1M": "94",
            "High.3M": "115",
            "Low.3M": "92",
            "High.6M": "120",
            "Low.6M": "88",
            "price_52_week_high": "125",
            "price_52_week_low": "80",
            "price_target_median": "120",
            "price_earnings_ttm": "20",
            "price_sales_current": "5",
            "price_book_fq": "3",
            "market_cap_basic": "1000000000",
        },
        "NASDAQ:PEER": {
            "symbol": "NASDAQ:PEER",
            "close": "50",
            "price_earnings_ttm": "15",
            "price_sales_current": "4",
            "price_book_fq": "2.5",
            "market_cap_basic": "500000000",
        },
    }
    rows = build_forward_upside_valuation_rows(
        highlight_rows,
        source_rows_by_symbol=source_rows,
        ranking_horizon=5,
    )
    row = rows[0]
    assert row["forward_upside_mode"] == "valuation"
    assert row["forward_selected_lens_count"] >= 2
    assert row["forward_valuation_upside_pct"] != ""
    assert row["forward_rank_horizon_valuation_upside_pct"] != ""
    assert row["forward_long_term_base_upside_pct"] != ""
    assert row["forward_long_term_base_price"] != ""


def test_build_rows_fallback_when_close_missing() -> None:
    highlight_rows = [
        {
            "symbol": "NASDAQ:MISS",
            "big_mover_score": 0.7,
            "confidence_score": 0.6,
            "composite_score": 0.5,
            "lane_context_score": 0.5,
            "upside_prediction_score": 0.65,
            "win_rate_5d_in_setup": 0.5,
            "median_fwd_5d_in_setup": 3.0,
            "target_rate_5d_in_setup": 0.4,
            "median_mfe_5d_in_setup": 4.0,
            "median_mae_5d_in_setup": -2.0,
            "lane_win_rate_5d": 0.5,
            "lane_median_fwd_5d": 1.0,
        }
    ]
    rows = build_forward_upside_valuation_rows(
        highlight_rows,
        source_rows_by_symbol={},
        ranking_horizon=5,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["forward_upside_mode"] == "fallback"
    assert row["forward_upside_fallback_reason"] == "missing_close_price"
    assert row["forward_selected_lens_count"] == 0


def test_build_rows_uses_broader_peer_universe_for_peer_stats() -> None:
    highlight_rows = [
        {
            "symbol": "NASDAQ:TEST",
            "big_mover_score": 0.5,
            "confidence_score": 0.5,
            "upside_prediction_score": 0.55,
            "win_rate_5d_in_setup": 0.55,
            "median_fwd_5d_in_setup": 4.0,
            "target_rate_5d_in_setup": 0.4,
            "median_mfe_5d_in_setup": 5.0,
            "median_mae_5d_in_setup": -2.0,
            "lane_win_rate_5d": 0.55,
            "lane_median_fwd_5d": 2.0,
            "sector": "Technology Services",
            "industry": "Software",
        }
    ]
    source_rows = {
        "NASDAQ:TEST": {
            "symbol": "NASDAQ:TEST",
            "sector": "Technology Services",
            "industry": "Software",
            "close": "100",
            "SMA10": "102",
            "SMA20": "101",
            "SMA50": "98",
            "SMA200": "95",
            "EMA10": "101",
            "EMA20": "100",
            "EMA50": "97",
            "EMA200": "94",
            "BB.upper": "108",
            "BB.lower": "92",
            "High.1M": "110",
            "Low.1M": "90",
            "High.3M": "115",
            "Low.3M": "88",
            "High.6M": "120",
            "Low.6M": "84",
            "price_52_week_high": "130",
            "price_52_week_low": "80",
            "price_target_median": "118",
            "price_target_low": "95",
            "price_target_high": "132",
            "AnalystRating": "buy",
            "price_earnings_ttm": "20",
            "price_sales_current": "5",
            "price_book_fq": "4",
            "book_value_per_share_fq": "25",
            "earnings_per_share_diluted_ttm": "12",
            "market_cap_basic": "1000000000",
        },
        "NASDAQ:PEER": {
            "symbol": "NASDAQ:PEER",
            "sector": "Technology Services",
            "industry": "Software",
            "close": "60",
            "price_earnings_ttm": "10",
            "price_sales_current": "2.5",
            "price_book_fq": "1.5",
            "book_value_per_share_fq": "20",
            "earnings_per_share_diluted_ttm": "6",
            "market_cap_basic": "500000000",
        },
    }

    narrow_rows = build_forward_upside_valuation_rows(
        highlight_rows,
        source_rows_by_symbol=source_rows,
        ranking_horizon=5,
    )
    broad_rows = build_forward_upside_valuation_rows(
        highlight_rows,
        source_rows_by_symbol=source_rows,
        peer_source_rows=list(source_rows.values()),
        ranking_horizon=5,
    )

    narrow_row = narrow_rows[0]
    broad_row = broad_rows[0]
    assert narrow_row["forward_upside_mode"] == "valuation"
    assert broad_row["forward_upside_mode"] == "valuation"
    assert (
        broad_row["forward_valuation_upside_pct"]
        < narrow_row["forward_valuation_upside_pct"]
    )


def test_build_rows_ignores_other_industries_when_industry_peers_exist() -> None:
    highlight_rows = [
        {
            "symbol": "NASDAQ:TEST",
            "big_mover_score": 0.5,
            "confidence_score": 0.5,
            "upside_prediction_score": 0.55,
            "win_rate_5d_in_setup": 0.55,
            "median_fwd_5d_in_setup": 4.0,
            "target_rate_5d_in_setup": 0.4,
            "median_mfe_5d_in_setup": 5.0,
            "median_mae_5d_in_setup": -2.0,
            "lane_win_rate_5d": 0.55,
            "lane_median_fwd_5d": 2.0,
            "sector": "Health Technology",
            "industry": "Biotechnology",
        }
    ]
    industry_rows = [
        {
            "symbol": "NASDAQ:TEST",
            "sector": "Health Technology",
            "industry": "Biotechnology",
            "close": "100",
            "SMA10": "102",
            "SMA20": "101",
            "SMA50": "98",
            "SMA200": "95",
            "EMA10": "101",
            "EMA20": "100",
            "EMA50": "97",
            "EMA200": "94",
            "BB.upper": "108",
            "BB.lower": "92",
            "High.1M": "110",
            "Low.1M": "90",
            "High.3M": "115",
            "Low.3M": "88",
            "High.6M": "120",
            "Low.6M": "84",
            "price_52_week_high": "130",
            "price_52_week_low": "80",
            "price_target_median": "118",
            "price_target_low": "95",
            "price_target_high": "132",
            "AnalystRating": "buy",
            "price_earnings_ttm": "20",
            "price_sales_current": "5",
            "price_book_fq": "4",
            "book_value_per_share_fq": "25",
            "earnings_per_share_diluted_ttm": "12",
            "market_cap_basic": "1000000000",
        },
        {
            "symbol": "NASDAQ:PEER",
            "sector": "Health Technology",
            "industry": "Biotechnology",
            "close": "60",
            "price_earnings_ttm": "40",
            "price_sales_current": "8",
            "price_book_fq": "5",
            "book_value_per_share_fq": "20",
            "earnings_per_share_diluted_ttm": "6",
            "market_cap_basic": "500000000",
        },
    ]
    cross_industry_rows = industry_rows + [
        {
            "symbol": "NYSE:OTHER1",
            "sector": "Electronic Technology",
            "industry": "Semiconductors",
            "close": "80",
            "price_earnings_ttm": "5",
            "price_sales_current": "1",
            "price_book_fq": "1",
            "book_value_per_share_fq": "30",
            "earnings_per_share_diluted_ttm": "8",
            "market_cap_basic": "700000000",
        },
        {
            "symbol": "NYSE:OTHER2",
            "sector": "Commercial Services",
            "industry": "Business Services",
            "close": "90",
            "price_earnings_ttm": "6",
            "price_sales_current": "1.2",
            "price_book_fq": "1.1",
            "book_value_per_share_fq": "35",
            "earnings_per_share_diluted_ttm": "9",
            "market_cap_basic": "900000000",
        },
    ]
    source_rows = {row["symbol"]: row for row in cross_industry_rows}

    industry_only = build_forward_upside_valuation_rows(
        highlight_rows,
        source_rows_by_symbol=source_rows,
        peer_source_rows=industry_rows,
        ranking_horizon=5,
    )[0]
    mixed_universe = build_forward_upside_valuation_rows(
        highlight_rows,
        source_rows_by_symbol=source_rows,
        peer_source_rows=cross_industry_rows,
        ranking_horizon=5,
    )[0]

    assert mixed_universe["forward_peer_scope"] == "industry"
    assert mixed_universe["forward_peer_scope_size"] == 2
    assert (
        mixed_universe["forward_valuation_upside_pct"]
        == industry_only["forward_valuation_upside_pct"]
    )


def test_sort_rows_assigns_forward_rank() -> None:
    highlight_rows = [
        {
            "symbol": "A",
            "big_mover_score": 0.5,
            "confidence_score": 0.5,
            "upside_prediction_score": 0.8,
            "win_rate_5d_in_setup": 0.6,
            "median_fwd_5d_in_setup": 4.0,
            "target_rate_5d_in_setup": 0.5,
            "median_mfe_5d_in_setup": 5.0,
            "median_mae_5d_in_setup": -2.0,
            "lane_win_rate_5d": 0.5,
            "lane_median_fwd_5d": 1.0,
        },
        {
            "symbol": "B",
            "big_mover_score": 0.5,
            "confidence_score": 0.5,
            "upside_prediction_score": 0.2,
            "win_rate_5d_in_setup": 0.4,
            "median_fwd_5d_in_setup": 1.0,
            "target_rate_5d_in_setup": 0.2,
            "median_mfe_5d_in_setup": 2.0,
            "median_mae_5d_in_setup": -3.0,
            "lane_win_rate_5d": 0.5,
            "lane_median_fwd_5d": 1.0,
        },
    ]
    rows = build_forward_upside_valuation_rows(
        highlight_rows,
        source_rows_by_symbol={},
        ranking_horizon=5,
    )
    ranked = sort_rows_by_forward_upside_valuation(rows)
    assert ranked[0]["symbol"] == "A"
    assert ranked[0]["forward_upside_rank"] == 1
    assert ranked[1]["forward_upside_rank"] == 2


def test_projection_schema_with_safety_columns_last() -> None:
    row = {
        "symbol": "NASDAQ:TEST",
        "forward_upside_rank": 1,
        "forward_upside_score": 0.75,
        "forward_upside_mode": "valuation",
        "forward_upside_fallback_reason": "",
        "forward_valuation_upside_pct": 31.0,
        "forward_rank_horizon_valuation_upside_pct": 10.0,
        "forward_valuation_bear_upside_pct": 5.0,
        "forward_valuation_bull_upside_pct": 55.0,
        "forward_selected_lenses": "trajectory|multiple|analyst|technical",
        "forward_selected_lens_count": 4,
        "forward_available_lens_count": 6,
        "forward_company_style": "growth",
        "forward_selected_coverage": 1.0,
        "forward_valuation_signal": 0.82,
        "forward_fallback_signal": 0.61,
        "forward_targets_cost_of_equity": 0.11,
        "forward_targets_blend_upside_cap_multiple": 4.0,
        "forward_near_term_base_price": 11.0,
        "forward_medium_term_base_price": 12.0,
        "forward_long_term_base_price": 14.0,
        "forward_near_term_bear_price": 10.0,
        "forward_near_term_bull_price": 12.0,
        "forward_medium_term_bear_price": 10.5,
        "forward_medium_term_bull_price": 13.0,
        "forward_long_term_bear_price": 11.0,
        "forward_long_term_bull_price": 16.0,
        "forward_near_term_base_upside_pct": 10.0,
        "forward_medium_term_base_upside_pct": 20.0,
        "forward_long_term_base_upside_pct": 40.0,
        "forward_near_term_opportunity_score": 8.0,
        "forward_medium_term_opportunity_score": 15.0,
        "forward_long_term_opportunity_score": 26.0,
        "forward_near_term_lens_coverage": 0.57,
        "forward_medium_term_lens_coverage": 0.57,
        "forward_long_term_lens_coverage": 0.57,
        "forward_long_term_lens_dispersion": 0.22,
        "forward_supporting_upside_prediction_score": 0.61,
        "big_mover_score": 0.7,
        "big_mover_rank": 2,
        "confidence_score": 0.8,
        "confidence_rank": 1,
        "composite_score": 0.66,
        "median_fwd_5d_in_setup": 8.5,
        "win_rate_5d_in_setup": 0.68,
        "target_rate_5d_in_setup": 0.52,
        "lane_sample_count_5d": 80,
        "lane_median_fwd_5d": 4.2,
        "lane_win_rate_5d": 0.6,
        "lane_context_score": 0.62,
        "stability_score": 0.58,
        "any_setup_rate": 0.09,
        "balance_sheet_safety_score": 0.7,
        "cash_generation_value_score": 0.66,
        "safety_companion_score": 0.68,
        "historical_validation_score": 0.73,
        "historical_validation_bucket": "strong",
        "historical_validation_pass": 1,
        "historical_validation_occurrence_count": 4,
        "historical_validation_win_rate": 0.68,
        "historical_validation_median_fwd_pct": 8.5,
        "historical_validation_any_setup_rate": 0.09,
        "historical_validation_stability_score": 0.58,
        "safety_bucket": "balanced",
        "indicator_pass_count": 5,
        "safety_rank_global": 12,
        "safety_shortlist_flag": 1,
        "safety_focus_flag": 1,
        "safety_data_available": 1,
    }
    projected = project_forward_upside_valuation_row(
        row,
        ranking_horizon=5,
        include_safety=True,
    )
    names = list(projected.keys())
    primary = forward_upside_valuation_primary_column_names(ranking_horizon=5)
    assert names[: len(primary)] == primary
    assert names[-3:] == [
        "safety_shortlist_flag",
        "safety_focus_flag",
        "safety_data_available",
    ]
