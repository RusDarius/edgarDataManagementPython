"""Checkpoint-aware timing and policy decisions for edge watchlist candidates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

CHECKPOINTS = ("latest", "premarket", "after_open", "midday")


@dataclass(frozen=True)
class TimingPolicyConfig:
    sticky_upside_rank_max: int = 50
    coverage_screen_rank_max: int = 100
    high_adrp_percentile_min: float = 70.0
    high_atrp_min: float = 4.0
    drawdown_min_pct: float = 10.0
    gap_thrust_min_pct: float = 3.0
    industry_breadth_min_pct: float = 60.0
    entry_score_min: float = 70.0
    starter_score_min: float = 55.0


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _rank_at_most(row: Mapping[str, Any], field: str, maximum: int) -> bool:
    rank = _integer(row.get(field))
    return rank is not None and 0 < rank <= maximum


def _is_who_candidate(
    row: Mapping[str, Any], config: TimingPolicyConfig
) -> tuple[bool, bool]:
    sticky_upside = _truthy(row.get("sticky_upside_flag")) or _rank_at_most(
        row, "upside_prediction_rank", config.sticky_upside_rank_max
    )
    coverage_glue = any(
        (
            _rank_at_most(row, "screen_rank", config.coverage_screen_rank_max),
            _truthy(row.get("prior_30d_upside_member_flag")),
            _truthy(row.get("in_upside_watchlist_flag")),
        )
    )
    return sticky_upside, coverage_glue


def evaluate_timing_policy_row(
    row: Mapping[str, Any],
    *,
    checkpoint: str,
    config: TimingPolicyConfig = TimingPolicyConfig(),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return separate timing-score and action-policy rows for one candidate."""
    if checkpoint not in CHECKPOINTS:
        raise ValueError(
            f"Unsupported checkpoint {checkpoint!r}; expected one of {CHECKPOINTS}"
        )

    symbol = str(row.get("symbol") or "").strip()
    sticky_upside, coverage_glue = _is_who_candidate(row, config)
    who_candidate = sticky_upside or coverage_glue
    adrp_percentile = _number(row.get("adrp_percentile"))
    atrp = _number(row.get("atrp"))
    high_adr = (
        adrp_percentile is not None
        and adrp_percentile >= config.high_adrp_percentile_min
    ) or (atrp is not None and atrp >= config.high_atrp_min)

    drawdown_pct = _number(row.get("drawdown_from_high_pct"))
    deep_drawdown = (
        drawdown_pct is not None and drawdown_pct <= -config.drawdown_min_pct
    )
    consecutive_down_days = _integer(row.get("consecutive_down_days")) or 0
    two_down_days = consecutive_down_days >= 2
    gap_pct = _number(row.get("gap_pct"))
    industry_breadth_pct = _number(row.get("industry_breadth_up_3pct"))
    gap_confirmed = gap_pct is not None and gap_pct >= config.gap_thrust_min_pct
    breadth_confirmed = (
        industry_breadth_pct is not None
        and industry_breadth_pct >= config.industry_breadth_min_pct
    )
    perf_5d = _number(row.get("perf_5d"))
    perf_1m = _number(row.get("perf_1m"))
    prior_day_pct = _number(row.get("prior_day_pct"))
    current_day_pct = _number(row.get("current_day_pct"))
    prior_thrust = prior_day_pct is not None and prior_day_pct >= 5.0
    breadth_expanding = _truthy(row.get("industry_breadth_expanding_flag"))
    anti_continuation_block = prior_thrust and not breadth_expanding

    setup_checks = {
        "drawdown_bounce": who_candidate
        and high_adr
        and deep_drawdown
        and two_down_days,
        "gap_breadth_thrust": who_candidate and gap_confirmed and breadth_confirmed,
        "pullback_swing": (
            who_candidate
            and high_adr
            and two_down_days
            and perf_5d is not None
            and perf_5d <= 0
            and perf_1m is not None
            and perf_1m > 0
        ),
        "breakout_continuation": (
            who_candidate
            and not anti_continuation_block
            and perf_1m is not None
            and perf_1m > 0
            and (
                gap_confirmed
                or (
                    checkpoint != "premarket"
                    and current_day_pct is not None
                    and current_day_pct >= 3.0
                    and breadth_confirmed
                )
            )
        ),
    }
    matched_setups = [name for name, passed in setup_checks.items() if passed]

    score = 0.0
    score += 25.0 if who_candidate else 0.0
    score += 10.0 if sticky_upside else 0.0
    score += 10.0 if high_adr else 0.0
    score += 10.0 if deep_drawdown else 0.0
    score += 15.0 if two_down_days else 0.0
    score += 15.0 if gap_confirmed else 0.0
    score += 15.0 if breadth_confirmed else 0.0
    score += 10.0 if len(matched_setups) >= 2 else 0.0
    score -= 35.0 if anti_continuation_block else 0.0
    timing_score = round(_clamp(score), 2)

    if not who_candidate:
        action = "NOT_IN_TIMING_UNIVERSE"
    elif anti_continuation_block:
        action = "AVOID_CHASE"
    elif matched_setups and timing_score >= config.entry_score_min:
        action = "ENTER_SMALL"
    elif matched_setups and timing_score >= config.starter_score_min:
        action = "ENTER_PROBE"
    else:
        action = "WATCH"

    if action == "ENTER_SMALL":
        risk_units = 0.5
    elif action == "ENTER_PROBE":
        risk_units = 0.25
    else:
        risk_units = 0.0
    if len(matched_setups) >= 2 and action == "ENTER_SMALL":
        risk_units = 0.75

    primary_setup = matched_setups[0] if matched_setups else "none"
    max_hold_days = (
        2 if primary_setup in {"drawdown_bounce", "gap_breadth_thrust"} else 5
    )
    stop_atr_units = 0.75 if max_hold_days == 2 else 1.0
    checks = {
        "who_candidate": who_candidate,
        "sticky_upside": sticky_upside,
        "coverage_glue": coverage_glue,
        "high_adr": high_adr,
        "deep_drawdown": deep_drawdown,
        "two_down_days": two_down_days,
        "gap_confirmed": gap_confirmed,
        "breadth_confirmed": breadth_confirmed,
        "prior_thrust": prior_thrust,
        "breadth_expanding": breadth_expanding,
        "anti_continuation_block": anti_continuation_block,
        **{f"setup_{name}": passed for name, passed in setup_checks.items()},
    }
    audit_trail_json = json.dumps(checks, sort_keys=True, separators=(",", ":"))
    evidence = {
        "candidate_source": row.get("candidate_source"),
        "in_upside_watchlist_flag": _integer(row.get("in_upside_watchlist_flag")),
        "in_screen_coverage_flag": _integer(row.get("in_screen_coverage_flag")),
        "upside_prediction_rank": _integer(row.get("upside_prediction_rank")),
        "screen_rank": _integer(row.get("screen_rank")),
        "adrp_percentile": adrp_percentile,
        "atrp": atrp,
        "drawdown_from_high_pct": drawdown_pct,
        "consecutive_down_days": consecutive_down_days,
        "prior_day_pct": prior_day_pct,
        "current_day_pct": current_day_pct,
        "gap_pct": gap_pct,
        "industry_breadth_up_3pct": industry_breadth_pct,
        "industry_breadth_member_count": _integer(
            row.get("industry_breadth_member_count")
        ),
        "same_day_scan_count": _integer(row.get("same_day_scan_count")),
        "same_day_scan_run_ids": row.get("same_day_scan_run_ids"),
        "same_day_scan_change_delta_pct": _number(
            row.get("same_day_scan_change_delta_pct")
        ),
        "same_day_scan_gap_delta_pct": _number(row.get("same_day_scan_gap_delta_pct")),
        "same_day_scan_breadth_delta_pct": _number(
            row.get("same_day_scan_breadth_delta_pct")
        ),
        "latest_all_fields_created_at_utc": row.get("latest_all_fields_created_at_utc"),
        "perf_5d": perf_5d,
        "perf_1m": perf_1m,
        "conviction_score": _number(row.get("conviction_score")),
        "conviction_rank": _integer(row.get("rank_overall")),
        "backwards_weeks_direction": row.get("backwards_weeks_direction"),
        "backwards_weeks_score_delta": _number(row.get("backwards_weeks_score_delta")),
        "historical_validation_bucket": row.get("historical_validation_bucket"),
        "historical_validation_win_rate": _number(
            row.get("historical_validation_win_rate")
        ),
        "historical_validation_median_fwd_pct": _number(
            row.get("historical_validation_median_fwd_pct")
        ),
    }
    timing_row = {
        "symbol": symbol,
        "checkpoint": checkpoint,
        "timing_score": timing_score,
        "matched_setups": "|".join(matched_setups),
        "setup_count": len(matched_setups),
        "audit_trail_json": audit_trail_json,
        **evidence,
        **{key: int(value) for key, value in checks.items()},
    }
    entry_condition = {
        "drawdown_bounce": "two-down drawdown setup remains valid at checkpoint",
        "gap_breadth_thrust": "gap and breadth remain above configured thresholds",
        "pullback_swing": "pullback holds positive 1M trend without chase block",
        "breakout_continuation": "current thrust and breadth hold without day-after block",
        "none": "no entry condition is currently active",
    }[primary_setup]
    policy_row = {
        "symbol": symbol,
        "checkpoint": checkpoint,
        "action": action,
        "primary_setup": primary_setup,
        "matched_setups": "|".join(matched_setups),
        "timing_score": timing_score,
        "normalized_risk_units": risk_units,
        "stop_atr_units": stop_atr_units,
        "max_hold_days": max_hold_days,
        "entry_condition": entry_condition,
        "invalidation_rule": (
            f"exit on {stop_atr_units:.2f} ATR adverse move, setup failure, "
            f"or {max_hold_days}-day time limit"
        ),
        "existing_safety_state": str(row.get("safety_bucket") or ""),
        "existing_conviction_state": str(row.get("conviction_state") or ""),
        "audit_trail_json": audit_trail_json,
        **evidence,
    }
    return timing_row, policy_row


def build_timing_policy_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    checkpoint: str,
    config: TimingPolicyConfig = TimingPolicyConfig(),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    timing_rows: list[dict[str, Any]] = []
    policy_rows: list[dict[str, Any]] = []
    for row in rows:
        timing_row, policy_row = evaluate_timing_policy_row(
            row, checkpoint=checkpoint, config=config
        )
        timing_rows.append(timing_row)
        policy_rows.append(policy_row)
    timing_rows.sort(key=lambda item: float(item["timing_score"]), reverse=True)
    action_priority = {
        "ENTER_SMALL": 5,
        "ENTER_PROBE": 4,
        "WATCH": 3,
        "AVOID_CHASE": 2,
        "NOT_IN_TIMING_UNIVERSE": 1,
    }
    policy_rows.sort(
        key=lambda item: (
            action_priority[str(item["action"])],
            float(item["timing_score"]),
        ),
        reverse=True,
    )
    for rank, policy_row in enumerate(policy_rows, start=1):
        policy_row["policy_rank"] = rank
    return timing_rows, policy_rows
