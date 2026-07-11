from edge_research_tools.upside_prediction import (
    compute_upside_prediction_fields,
    project_upside_prediction_row,
    sort_rows_by_upside_prediction,
    upside_prediction_primary_column_names,
)


def test_compute_upside_prediction_prefers_strong_hist_and_mover() -> None:
    strong = {
        "big_mover_score": 0.9,
        "composite_score": 0.85,
        "lane_context_score": 0.7,
        "hist_occurrence_count_in_setup": 8,
        "win_rate_5d_in_setup": 0.9,
        "median_fwd_5d_in_setup": 12.0,
        "target_rate_5d_in_setup": 0.8,
        "median_mfe_5d_in_setup": 14.0,
        "median_mae_5d_in_setup": -2.0,
        "lane_win_rate_5d": 0.63,
        "lane_median_fwd_5d": 5.0,
    }
    weak = {
        **strong,
        "big_mover_score": 0.55,
        "win_rate_5d_in_setup": 0.4,
        "median_fwd_5d_in_setup": -1.0,
        "target_rate_5d_in_setup": 0.1,
        "median_mfe_5d_in_setup": 2.0,
        "median_mae_5d_in_setup": -9.0,
    }

    strong_score = compute_upside_prediction_fields(strong, ranking_horizon=5)[
        "upside_prediction_score"
    ]
    weak_score = compute_upside_prediction_fields(weak, ranking_horizon=5)[
        "upside_prediction_score"
    ]
    assert strong_score > weak_score


def test_sort_rows_assigns_upside_prediction_rank() -> None:
    rows = [
        {
            "symbol": "A",
            "upside_prediction_score": 0.5,
            "win_rate_5d_in_setup": 0.5,
            "median_fwd_5d_in_setup": 1.0,
            "big_mover_score": 0.4,
        },
        {
            "symbol": "B",
            "upside_prediction_score": 0.8,
            "win_rate_5d_in_setup": 0.7,
            "median_fwd_5d_in_setup": 3.0,
            "big_mover_score": 0.6,
        },
    ]
    ranked = sort_rows_by_upside_prediction(rows)
    assert ranked[0]["symbol"] == "B"
    assert ranked[0]["upside_prediction_rank"] == 1


def test_project_row_puts_safety_columns_last() -> None:
    row = {
        "symbol": "NASDAQ:TEST",
        "upside_prediction_rank": 1,
        "upside_prediction_score": 0.7,
        "balance_sheet_safety_score": 0.8,
        "cash_generation_value_score": 0.6,
        "safety_companion_score": 0.72,
        "safety_bucket": "balanced",
        "indicator_pass_count": 6,
        "safety_rank_global": 10,
        "safety_shortlist_flag": 1,
        "safety_focus_flag": 0,
        "safety_data_available": 1,
        "win_rate_5d_in_setup": 0.75,
        "median_fwd_5d_in_setup": 8.0,
        "target_rate_5d_in_setup": 0.5,
        "median_mfe_5d_in_setup": 9.0,
        "median_mae_5d_in_setup": -3.0,
        "lane_sample_count_5d": 50,
        "lane_median_fwd_5d": 4.0,
        "lane_win_rate_5d": 0.6,
    }
    row.update(
        compute_upside_prediction_fields(row, ranking_horizon=5),
    )
    projected = project_upside_prediction_row(
        row,
        ranking_horizon=5,
        include_safety=True,
    )
    names = list(projected.keys())
    primary = upside_prediction_primary_column_names(ranking_horizon=5)
    assert names[: len(primary)] == primary
    assert names[-3:] == [
        "safety_shortlist_flag",
        "safety_focus_flag",
        "safety_data_available",
    ]
