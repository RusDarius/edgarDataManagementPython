from pathlib import Path

from edge_research_tools.unified_edge_highlights import (
    build_unified_edge_highlight_rows,
    build_unified_edge_highlights_store,
    compute_probabilistic_edge_fit_score,
    compute_unified_outlook_fields,
    write_unified_edge_highlights_duckdb,
)


def test_compute_unified_outlook_fields_picks_best_outlook() -> None:
    fields = compute_unified_outlook_fields(
        {
            "big_mover_score": 0.6,
            "confidence_score": 0.7,
            "upside_prediction_score": 0.9,
            "forward_upside_mode": "valuation",
            "forward_valuation_upside_pct": 40.0,
            "forward_upside_score": 0.8,
            "tradeable_safety_blend_score": 0.55,
            "safety_companion_score": 0.65,
        },
        population=100,
        ranking_horizon=5,
    )
    assert fields["unified_best_outlook_name"] == "upside_prediction"
    assert fields["outlook_upside_prediction_score"] == 0.9
    assert fields["unified_edge_highlight_score"] > 0


def test_probabilistic_edge_fit_score_uses_lane_bootstrap_fields() -> None:
    score = compute_probabilistic_edge_fit_score(
        {
            "lane_leader_median_fwd_5d_ci_low": 4.0,
            "lane_leader_median_fwd_5d_ci_width": 6.0,
            "lane_leader_median_fwd_5d_bootstrap_sample_count": 40,
            "lane_median_fwd_5d": 5.0,
            "median_fwd_5d_in_setup": 8.0,
            "win_rate_5d_in_setup": 0.7,
        },
        ranking_horizon=5,
    )
    assert score > 0.4


def test_build_unified_rows_assigns_highlight_rank() -> None:
    rows = build_unified_edge_highlight_rows(
        highlight_rows=[
            {
                "symbol": "NASDAQ:A",
                "industry": "Semiconductors",
                "lane_group_by": "industry",
                "lane_group_value": "Semiconductors",
                "big_mover_score": 0.8,
                "confidence_score": 0.7,
                "composite_score": 0.75,
                "median_fwd_5d_in_setup": 6.0,
                "win_rate_5d_in_setup": 0.6,
                "lane_median_fwd_5d": 4.0,
            },
            {
                "symbol": "NASDAQ:B",
                "industry": "Software",
                "lane_group_by": "industry",
                "lane_group_value": "Software",
                "big_mover_score": 0.5,
                "confidence_score": 0.5,
                "composite_score": 0.5,
                "median_fwd_5d_in_setup": 2.0,
                "win_rate_5d_in_setup": 0.4,
                "lane_median_fwd_5d": 1.0,
            },
        ],
        safety_by_symbol={},
        upside_by_symbol={
            "NASDAQ:A": {"upside_prediction_score": 0.85, "upside_prediction_rank": 1},
            "NASDAQ:B": {"upside_prediction_score": 0.4, "upside_prediction_rank": 2},
        },
        forward_by_symbol={
            "NASDAQ:A": {
                "forward_upside_mode": "valuation",
                "forward_valuation_upside_pct": 25.0,
                "forward_upside_score": 0.7,
                "forward_upside_rank": 1,
            }
        },
        tradeable_by_symbol={},
        lane_leader_by_group={
            "Semiconductors": {
                "group_value": "Semiconductors",
                "median_fwd_5d_ci_low": 3.0,
                "median_fwd_5d_ci_high": 8.0,
                "median_fwd_5d_ci_width": 5.0,
                "median_fwd_5d_bootstrap_sample_count": 30,
            }
        },
        edge_group_by_key={},
        ranking_horizon=5,
    )
    assert rows[0]["symbol"] == "NASDAQ:A"
    assert rows[0]["unified_edge_highlight_rank"] == 1
    assert rows[0]["lane_leader_median_fwd_5d_ci_low"] == 3.0
    assert rows[0]["forward_valuation_upside_pct"] == 25.0


def test_build_store_writes_queryable_duckdb(tmp_path: Path) -> None:
    highlights_dir = tmp_path / "highlights"
    safety_dir = tmp_path / "safety"
    upside_dir = tmp_path / "upside"
    forward_dir = tmp_path / "forward"
    highlights_dir.mkdir()
    safety_dir.mkdir()
    upside_dir.mkdir()
    forward_dir.mkdir()

    shortlist = highlights_dir / "edge_name_shortlist.csv"
    lane_leaders = highlights_dir / "edge_lane_leaders.csv"
    safety_csv = safety_dir / "edge_safety_scored.csv"
    upside_csv = upside_dir / "edge_upside_prediction_ranked.csv"
    forward_csv = forward_dir / "edge_forward_upside_valuation_ranked.csv"

    shortlist.write_text(
        "symbol,industry,lane_group_by,lane_group_value,big_mover_score,confidence_score,"
        "median_fwd_5d_in_setup,win_rate_5d_in_setup,lane_median_fwd_5d\n"
        "NASDAQ:TEST,Semiconductors,industry,Semiconductors,0.8,0.7,6.0,0.6,4.0\n",
        encoding="utf-8",
    )
    lane_leaders.write_text(
        "group_value,median_fwd_5d,median_fwd_5d_ci_low,median_fwd_5d_ci_high,"
        "median_fwd_5d_ci_width,median_fwd_5d_bootstrap_sample_count\n"
        "Semiconductors,4.5,2.0,7.0,5.0,25\n",
        encoding="utf-8",
    )
    safety_csv.write_text(
        "symbol,safety_companion_score,balance_sheet_safety_score,safety_rank\n"
        "NASDAQ:TEST,0.72,0.8,10\n",
        encoding="utf-8",
    )
    upside_csv.write_text(
        "symbol,upside_prediction_score,upside_prediction_rank\n"
        "NASDAQ:TEST,0.82,1\n",
        encoding="utf-8",
    )
    forward_csv.write_text(
        "symbol,forward_upside_mode,forward_valuation_upside_pct,forward_upside_score,"
        "forward_upside_rank\n"
        "NASDAQ:TEST,valuation,30.0,0.76,1\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "unified"
    result = build_unified_edge_highlights_store(
        output_dir=output_dir,
        highlights_result={
            "shortlist_csv": shortlist,
            "lane_leaders_csv": lane_leaders,
            "ranking_horizon": 5,
        },
        safety_result={"scored_csv": safety_csv},
        scan_edge_result=None,
        tradeable_safety_result=None,
        upside_prediction_lens_result={"ranked_csv": upside_csv},
        forward_upside_valuation_lens_result={"ranked_csv": forward_csv},
    )

    duckdb = __import__("duckdb")
    conn = duckdb.connect(result["database_path"].as_posix())
    try:
        row = conn.execute("""
            SELECT symbol, unified_edge_highlight_rank, unified_best_outlook_name,
                   forward_valuation_upside_pct, safety_detail_safety_companion_score
            FROM symbol_unified_highlights
            WHERE symbol = 'NASDAQ:TEST'
            """).fetchone()
        assert row is not None
        assert row[0] == "NASDAQ:TEST"
        assert row[2] != ""
        assert float(row[3]) == 30.0
        manifest_count = conn.execute(
            "SELECT COUNT(*) FROM unified_run_manifest"
        ).fetchone()[0]
        assert int(manifest_count) > 0
    finally:
        conn.close()


def test_build_unified_rows_merges_tradeable_historical_validation_fields() -> None:
    rows = build_unified_edge_highlight_rows(
        highlight_rows=[
            {
                "symbol": "NASDAQ:WDC",
                "industry": "Computer Hardware",
                "lane_group_by": "industry",
                "lane_group_value": "Computer Hardware",
                "big_mover_score": 0.7,
                "confidence_score": 0.6,
                "composite_score": 0.65,
                "median_fwd_5d_in_setup": 5.0,
                "win_rate_5d_in_setup": 0.58,
                "lane_median_fwd_5d": 3.0,
            }
        ],
        safety_by_symbol={},
        upside_by_symbol={
            "NASDAQ:WDC": {
                "upside_prediction_score": 0.74,
                "upside_prediction_rank": 1,
            }
        },
        forward_by_symbol={
            "NASDAQ:WDC": {
                "forward_upside_mode": "valuation",
                "forward_valuation_upside_pct": 22.0,
                "forward_upside_score": 0.69,
                "forward_upside_rank": 1,
            }
        },
        tradeable_by_symbol={
            "NASDAQ:WDC": {
                "tradeable_safety_blend_score": 0.71,
                "tradeable_safety_rank": 1,
                "historical_validation_score": 0.77,
                "historical_validation_bucket": "strong",
                "historical_validation_pass": 1,
            }
        },
        lane_leader_by_group={},
        edge_group_by_key={},
        ranking_horizon=5,
    )

    assert rows[0]["historical_validation_score"] == 0.77
    assert rows[0]["historical_validation_bucket"] == "strong"
    assert rows[0]["historical_validation_pass"] == 1


def test_build_unified_rows_backfills_symbol_from_exchange_and_ticker() -> None:
    """Rows with a blank symbol but valid exchange/bare_ticker get a usable key."""
    rows = build_unified_edge_highlight_rows(
        highlight_rows=[
            {
                "symbol": "",
                "bare_ticker": "AGY",
                "exchange": "NASDAQ",
                "industry": "Pharmaceuticals: Other",
                "lane_group_by": "industry",
                "lane_group_value": "Pharmaceuticals: Other",
                "big_mover_score": 0.6,
                "confidence_score": 0.5,
                "composite_score": 0.55,
                "median_fwd_5d_in_setup": 3.0,
                "win_rate_5d_in_setup": 0.5,
                "lane_median_fwd_5d": 2.0,
            }
        ],
        safety_by_symbol={},
        upside_by_symbol={
            "NASDAQ:AGY": {"upside_prediction_score": 0.75, "upside_prediction_rank": 1}
        },
        forward_by_symbol={
            "NASDAQ:AGY": {
                "forward_upside_mode": "valuation",
                "forward_valuation_upside_pct": 20.0,
                "forward_upside_score": 0.6,
                "forward_upside_rank": 1,
            }
        },
        tradeable_by_symbol={},
        lane_leader_by_group={},
        edge_group_by_key={},
        ranking_horizon=5,
    )

    assert rows[0]["symbol"] == "NASDAQ:AGY"
    assert rows[0]["upside_prediction_score"] == 0.75
    assert rows[0]["forward_valuation_upside_pct"] == 20.0


def test_build_store_backfills_symbol_from_exchange_and_ticker(tmp_path: Path) -> None:
    """CSV inputs with blank symbols still produce a unified DB keyed by exchange:ticker."""
    highlights_dir = tmp_path / "highlights"
    safety_dir = tmp_path / "safety"
    upside_dir = tmp_path / "upside"
    forward_dir = tmp_path / "forward"
    highlights_dir.mkdir()
    safety_dir.mkdir()
    upside_dir.mkdir()
    forward_dir.mkdir()

    shortlist = highlights_dir / "edge_name_shortlist.csv"
    lane_leaders = highlights_dir / "edge_lane_leaders.csv"
    safety_csv = safety_dir / "edge_safety_scored.csv"
    upside_csv = upside_dir / "edge_upside_prediction_ranked.csv"
    forward_csv = forward_dir / "edge_forward_upside_valuation_ranked.csv"

    shortlist.write_text(
        "symbol,bare_ticker,exchange,industry,lane_group_by,lane_group_value,"
        "big_mover_score,confidence_score,median_fwd_5d_in_setup,"
        "win_rate_5d_in_setup,lane_median_fwd_5d\n"
        ",AGY,NASDAQ,Pharmaceuticals: Other,industry,Pharmaceuticals: Other,"
        "0.8,0.7,6.0,0.6,4.0\n",
        encoding="utf-8",
    )
    lane_leaders.write_text(
        "group_value,median_fwd_5d,median_fwd_5d_ci_low,median_fwd_5d_ci_high,"
        "median_fwd_5d_ci_width,median_fwd_5d_bootstrap_sample_count\n"
        "Pharmaceuticals: Other,4.5,2.0,7.0,5.0,25\n",
        encoding="utf-8",
    )
    safety_csv.write_text(
        "symbol,safety_companion_score,balance_sheet_safety_score,safety_rank\n"
        "NASDAQ:AGY,0.72,0.8,10\n",
        encoding="utf-8",
    )
    upside_csv.write_text(
        "symbol,upside_prediction_score,upside_prediction_rank\n"
        "NASDAQ:AGY,0.82,1\n",
        encoding="utf-8",
    )
    forward_csv.write_text(
        "symbol,forward_upside_mode,forward_valuation_upside_pct,forward_upside_score,"
        "forward_upside_rank\n"
        "NASDAQ:AGY,valuation,30.0,0.76,1\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "unified"
    result = build_unified_edge_highlights_store(
        output_dir=output_dir,
        highlights_result={
            "shortlist_csv": shortlist,
            "lane_leaders_csv": lane_leaders,
            "ranking_horizon": 5,
        },
        safety_result={"scored_csv": safety_csv},
        scan_edge_result=None,
        tradeable_safety_result=None,
        upside_prediction_lens_result={"ranked_csv": upside_csv},
        forward_upside_valuation_lens_result={"ranked_csv": forward_csv},
    )

    duckdb = __import__("duckdb")
    conn = duckdb.connect(result["database_path"].as_posix())
    try:
        row = conn.execute("""
            SELECT symbol, unified_edge_highlight_rank, forward_valuation_upside_pct
            FROM symbol_unified_highlights
            WHERE symbol = 'NASDAQ:AGY'
            """).fetchone()
        assert row is not None
        assert row[0] == "NASDAQ:AGY"
        assert float(row[2]) == 30.0

        csv_text = result["csv_path"].read_text(encoding="utf-8")
        assert "NASDAQ:AGY" in csv_text
    finally:
        conn.close()
