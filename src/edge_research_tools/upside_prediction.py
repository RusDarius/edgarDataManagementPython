from __future__ import annotations

from typing import Any, Mapping, Sequence


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def compute_upside_prediction_fields(
    row: Mapping[str, Any],
    *,
    ranking_horizon: int,
) -> dict[str, float]:
    """
    Rank-oriented upside prediction score for highlights shortlist names.

    Combines current expansion state, blended historical forward behavior
  (name-level when sample depth allows, otherwise lane base rates), and lane context.
    """
    horizon = int(ranking_horizon)
    big_mover_score = _safe_float(row.get("big_mover_score"))
    composite_score = _safe_float(row.get("composite_score"))
    lane_context_score = _safe_float(row.get("lane_context_score"))

    hist_win_rate = _safe_float(row.get(f"win_rate_{horizon}d_in_setup"))
    hist_median_fwd = _safe_float(row.get(f"median_fwd_{horizon}d_in_setup"))
    target_rate = _safe_float(row.get(f"target_rate_{horizon}d_in_setup"))
    hist_mfe = _safe_float(row.get(f"median_mfe_{horizon}d_in_setup"))
    hist_mae = _safe_float(row.get(f"median_mae_{horizon}d_in_setup"))
    hist_occurrences = _safe_int(row.get("hist_occurrence_count_in_setup"))

    lane_win_rate = _safe_float(row.get(f"lane_win_rate_{horizon}d"))
    lane_median_fwd = _safe_float(row.get(f"lane_median_fwd_{horizon}d"))

    hist_median_norm = _clamp(hist_median_fwd / 12.0)
    hist_mfe_norm = _clamp(hist_mfe / 15.0)
    lane_median_norm = _clamp(lane_median_fwd / 8.0)

    name_hist_signal = _clamp(
        (0.35 * hist_win_rate)
        + (0.30 * hist_median_norm)
        + (0.20 * target_rate)
        + (0.15 * hist_mfe_norm)
    )
    lane_hist_signal = _clamp((0.55 * lane_win_rate) + (0.45 * lane_median_norm))
    name_hist_weight = _clamp(hist_occurrences / 8.0)
    blended_hist_signal = _clamp(
        (name_hist_weight * name_hist_signal)
        + ((1.0 - name_hist_weight) * lane_hist_signal)
    )

    mae_penalty = _clamp(abs(min(0.0, hist_mae)) / 12.0) * 0.08
    upside_prediction_score = _clamp(
        (0.30 * big_mover_score)
        + (0.25 * blended_hist_signal)
        + (0.20 * composite_score)
        + (0.15 * lane_context_score)
        + (0.10 * target_rate)
        - mae_penalty
    )

    return {
        "upside_prediction_score": round(upside_prediction_score, 4),
        "upside_hist_signal_blend": round(blended_hist_signal, 4),
        "upside_name_hist_weight": round(name_hist_weight, 4),
        "upside_hist_win_rate": round(hist_win_rate, 4),
        "upside_hist_median_fwd_pct": round(hist_median_fwd, 4),
        "upside_target_before_stop_rate": round(target_rate, 4),
        "upside_hist_mfe_pct": round(hist_mfe, 4),
        "upside_hist_mae_pct": round(hist_mae, 4),
    }


def upside_prediction_primary_column_names(*, ranking_horizon: int) -> list[str]:
    horizon = int(ranking_horizon)
    return [
        "symbol",
        "bare_ticker",
        "company_name",
        "exchange",
        "country",
        "sector",
        "industry",
        "source_date",
        "lane_setup_name",
        "lane_group_by",
        "lane_group_value",
        "current_lane_flag",
        "upside_prediction_rank",
        "upside_prediction_score",
        "upside_hist_signal_blend",
        "upside_name_hist_weight",
        "upside_hist_win_rate",
        "upside_hist_median_fwd_pct",
        "upside_target_before_stop_rate",
        "upside_hist_mfe_pct",
        "upside_hist_mae_pct",
        "big_mover_score",
        "big_mover_rank",
        "confidence_score",
        "confidence_rank",
        "composite_score",
        "adrp_pct_today",
        "relvol_pct_today",
        "value_traded_pct_today",
        "vol_core_pct_today",
        "liq_core_pct_today",
        "mom_core_pct_today",
        "hist_occurrence_count_in_setup",
        f"median_fwd_{horizon}d_in_setup",
        f"win_rate_{horizon}d_in_setup",
        f"target_rate_{horizon}d_in_setup",
        f"median_mfe_{horizon}d_in_setup",
        f"median_mae_{horizon}d_in_setup",
        f"lane_sample_count_{horizon}d",
        f"lane_median_fwd_{horizon}d",
        f"lane_win_rate_{horizon}d",
        "lane_context_score",
        "stability_score",
        "any_setup_rate",
    ]


UPSIDE_PREDICTION_SAFETY_TRAILING_COLUMNS: tuple[str, ...] = (
    "balance_sheet_safety_score",
    "cash_generation_value_score",
    "safety_companion_score",
    "safety_bucket",
    "indicator_pass_count",
    "safety_rank_global",
    "safety_shortlist_flag",
    "safety_focus_flag",
    "safety_data_available",
)


def project_upside_prediction_row(
    row: Mapping[str, Any],
    *,
    ranking_horizon: int,
    include_safety: bool,
) -> dict[str, Any]:
    primary_names = upside_prediction_primary_column_names(ranking_horizon=ranking_horizon)
    trailing_names = (
        list(UPSIDE_PREDICTION_SAFETY_TRAILING_COLUMNS) if include_safety else []
    )
    ordered_names = primary_names + trailing_names
    return {name: row.get(name, "") for name in ordered_names}


def sort_rows_by_upside_prediction(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    materialized = [dict(row) for row in rows]
    materialized.sort(
        key=lambda row: (
            _safe_float(row.get("upside_prediction_score")),
            _safe_float(row.get(f"win_rate_{_infer_horizon_from_row(row)}d_in_setup")),
            _safe_float(
                row.get(f"median_fwd_{_infer_horizon_from_row(row)}d_in_setup")
            ),
            _safe_float(row.get("big_mover_score")),
        ),
        reverse=True,
    )
    for index, row in enumerate(materialized, start=1):
        row["upside_prediction_rank"] = index
    return materialized


def _infer_horizon_from_row(row: Mapping[str, Any]) -> int:
    for key in row:
        if key.startswith("median_fwd_") and key.endswith("d_in_setup"):
            suffix = key.removeprefix("median_fwd_").removesuffix("d_in_setup")
            try:
                return int(suffix)
            except ValueError:
                continue
    return 5
