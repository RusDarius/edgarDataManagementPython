"""Module2 derived metrics layered on v1 derived fields and raw scan rows."""

from __future__ import annotations

from typing import Any, Mapping

from data_analysis_scripts._shared_analysis_utils import coerce_numeric, safe_ratio
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    _build_derived_metrics,
)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def build_module2_derived_metrics(row: Mapping[str, Any]) -> dict[str, float | None]:
    """Return v1 derived metrics plus module2 outlook composites."""
    row_dict = dict(row)
    v1_derived = _build_derived_metrics(row_dict)
    module2: dict[str, float | None] = dict(v1_derived)

    rsi = coerce_numeric(row.get("RSI"))
    rsi7 = coerce_numeric(row.get("RSI7"))
    stoch_rsi_k = coerce_numeric(row.get("Stoch.RSI.K"))
    bb_position = v1_derived.get("bb_position")
    range_position_52w = v1_derived.get("range_position_52w")

    oversold_inputs: list[float] = []
    if rsi is not None:
        oversold_inputs.append(_clamp((50.0 - rsi) / 25.0, -3.0, 3.0))
    if rsi7 is not None:
        oversold_inputs.append(_clamp((50.0 - rsi7) / 25.0, -3.0, 3.0))
    if stoch_rsi_k is not None:
        oversold_inputs.append(_clamp((50.0 - stoch_rsi_k) / 25.0, -3.0, 3.0))
    if bb_position is not None:
        oversold_inputs.append(_clamp(0.5 - bb_position, -3.0, 3.0) * 2.0)
    if range_position_52w is not None:
        oversold_inputs.append(_clamp(0.35 - range_position_52w, -3.0, 3.0) * 2.0)
    module2["regime_oversold_daily"] = _mean(oversold_inputs)

    near_52w_high = v1_derived.get("near_52w_high_score")
    trend_alignment = v1_derived.get("trend_alignment")
    perf_3m = coerce_numeric(row.get("Perf.3M"))
    perf_6m = coerce_numeric(row.get("Perf.6M"))
    extended_inputs: list[float] = []
    if near_52w_high is not None:
        extended_inputs.append(near_52w_high)
    if trend_alignment is not None:
        extended_inputs.append(trend_alignment)
    if perf_3m is not None and perf_3m > 0:
        extended_inputs.append(_clamp(perf_3m / 20.0, 0.0, 3.0))
    if perf_6m is not None and perf_6m > 0:
        extended_inputs.append(_clamp(perf_6m / 30.0, 0.0, 3.0))
    module2["regime_extended_tape"] = _mean(extended_inputs)

    perf_5d = coerce_numeric(row.get("Perf.5D"))
    perf_w = coerce_numeric(row.get("Perf.W"))
    relative_volume = coerce_numeric(row.get("relative_volume_10d_calc"))
    atrp = coerce_numeric(row.get("ATRP"))
    recommend_ma_1m = coerce_numeric(row.get("Recommend.MA|1M"))
    repair_inputs: list[float] = []
    if perf_5d is not None and perf_5d > 0:
        repair_inputs.append(_clamp(perf_5d / 10.0, 0.0, 3.0))
    if perf_w is not None and perf_w > 0:
        repair_inputs.append(_clamp(perf_w / 15.0, 0.0, 3.0))
    if relative_volume is not None and relative_volume > 1.0:
        repair_inputs.append(_clamp(relative_volume - 1.0, 0.0, 3.0))
    if atrp is not None:
        repair_inputs.append(_clamp(atrp / 5.0, 0.0, 3.0))
    monthly_oversold_only = False
    if recommend_ma_1m is not None and recommend_ma_1m < 0:
        monthly_oversold_only = True
    if stoch_rsi_k is not None and stoch_rsi_k < 30 and (perf_5d or 0) <= 0:
        monthly_oversold_only = True
    repair_score = _mean(repair_inputs)
    if monthly_oversold_only and repair_score is not None:
        repair_score *= 0.35
    module2["repair_confirmation_score"] = repair_score

    upside_inputs: list[float] = []
    distance_from_high = v1_derived.get("distance_from_52w_high")
    if distance_from_high is not None:
        upside_inputs.append(_clamp(-distance_from_high * 2.0, -3.0, 3.0))
    pt_upside = v1_derived.get("price_target_upside_median")
    if pt_upside is None:
        pt_upside = v1_derived.get("price_target_upside_average")
    if pt_upside is not None and pt_upside > 0:
        upside_inputs.append(_clamp(pt_upside * 2.0, 0.0, 3.0))
    close_vs_r1 = v1_derived.get("close_vs_camarilla_r1")
    if close_vs_r1 is not None and close_vs_r1 < 0:
        upside_inputs.append(_clamp(-close_vs_r1, 0.0, 3.0))
    donchian_position = v1_derived.get("donchian_position")
    if donchian_position is not None and donchian_position < 0.7:
        upside_inputs.append(_clamp(0.7 - donchian_position, 0.0, 3.0) * 2.0)
    module2["upside_room_score"] = _mean(upside_inputs)

    continuation_inputs: list[float] = []
    volume_trend = v1_derived.get("volume_trend")
    adx_spread = v1_derived.get("adx_directional_spread")
    aroon_spread = v1_derived.get("aroon_spread")
    if atrp is not None:
        continuation_inputs.append(_clamp(atrp / 4.0, 0.0, 3.0))
    if volume_trend is not None and volume_trend > 1.0:
        continuation_inputs.append(_clamp(volume_trend - 1.0, 0.0, 3.0))
    if adx_spread is not None and adx_spread > 0:
        continuation_inputs.append(_clamp(adx_spread / 20.0, 0.0, 3.0))
    if aroon_spread is not None and aroon_spread > 0:
        continuation_inputs.append(_clamp(aroon_spread / 50.0, 0.0, 3.0))
    short_trend = v1_derived.get("short_trend_emergence")
    if short_trend is not None and short_trend > 0:
        continuation_inputs.append(_clamp(short_trend, 0.0, 3.0))
    module2["continuation_strength"] = _mean(continuation_inputs)

    fundamental_inputs: list[float] = []
    for field_name in (
        "total_revenue_qoq_growth_fq",
        "free_cash_flow_qoq_growth_fq",
        "net_income_qoq_growth_fq",
        "ebitda_qoq_growth_fq",
    ):
        value = coerce_numeric(row.get(field_name))
        if value is not None:
            fundamental_inputs.append(_clamp(value / 25.0, -3.0, 3.0))
    eps_forward = v1_derived.get("eps_forward_growth")
    if eps_forward is not None:
        fundamental_inputs.append(_clamp(eps_forward * 2.0, -3.0, 3.0))
    turnaround = v1_derived.get("operating_turnaround_score")
    if turnaround is not None:
        fundamental_inputs.append(turnaround)
    module2["fundamental_inflection"] = _mean(fundamental_inputs)

    quality_inputs: list[float] = []
    for field_name in (
        "operating_margin",
        "return_on_invested_capital",
        "free_cash_flow_margin_ttm",
        "gross_margin",
    ):
        value = coerce_numeric(row.get(field_name))
        if value is not None:
            quality_inputs.append(_clamp(value / 20.0, -3.0, 3.0))
    fcf_yield = v1_derived.get("free_cash_flow_yield")
    if fcf_yield is not None:
        quality_inputs.append(_clamp(fcf_yield * 20.0, -3.0, 3.0))
    piotroski = coerce_numeric(row.get("piotroski_f_score_ttm"))
    if piotroski is not None:
        quality_inputs.append(_clamp((piotroski - 5.0) / 2.0, -3.0, 3.0))
    module2["quality_stability"] = _mean(quality_inputs)

    distress_inputs: list[float] = []
    altman = coerce_numeric(row.get("altman_z_score_ttm"))
    if altman is not None:
        distress_inputs.append(_clamp((altman - 1.8) / 1.5, -3.0, 3.0))
    debt_ebitda = coerce_numeric(row.get("total_debt_to_ebitda_fq"))
    if debt_ebitda is not None:
        distress_inputs.append(_clamp((3.0 - debt_ebitda) / 2.0, -3.0, 3.0))
    interest_cover = coerce_numeric(row.get("interst_cover_ttm"))
    if interest_cover is not None:
        distress_inputs.append(_clamp(interest_cover / 5.0, -3.0, 3.0))
    net_cash = v1_derived.get("net_cash_to_market_cap")
    if net_cash is not None:
        distress_inputs.append(_clamp(net_cash * 10.0, -3.0, 3.0))
    module2["distress_floor"] = _mean(distress_inputs)

    return module2


MODULE2_DERIVED_FIELD_NAMES: tuple[str, ...] = (
    "regime_oversold_daily",
    "regime_extended_tape",
    "repair_confirmation_score",
    "upside_room_score",
    "continuation_strength",
    "fundamental_inflection",
    "quality_stability",
    "distress_floor",
)
