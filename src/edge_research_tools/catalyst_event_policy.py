"""Catalyst event policy scoring for all prediction-universe tickers.

Produced data contract:
- Existing timing tables remain the WHO-limited edge 50/100 timing/action book.
- This module emits all-ticker catalyst-event classifications for the prediction
  universe into `catalyst_event_policy_scores` and
  `catalyst_event_action_recommendations`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class CatalystEventPolicyConfig:
    near_earnings_days_max: int = 14
    post_earnings_days_min: int = -2
    book_a_max_conviction_rank: int = 800
    book_a_min_conviction_score: float = 0.55
    book_a_max_upside_rank: int = 200
    book_b_drawdown_min_pct: float = 15.0
    book_b_perf_1m_min_pct: float = -15.0
    book_b_expected_move_min_pct: float = 15.0


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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _contains_token(value: Any, tokens: set[str]) -> bool:
    lowered = _text(value).lower()
    return any(token in lowered for token in tokens)


def evaluate_catalyst_event_row(
    row: Mapping[str, Any],
    *,
    checkpoint: str,
    config: CatalystEventPolicyConfig = CatalystEventPolicyConfig(),
) -> tuple[dict[str, Any], dict[str, Any]]:
    symbol = _text(row.get("symbol"))
    entry_state = _text(row.get("entry_state")).upper()
    conviction_rank = _integer(row.get("rank_overall"))
    conviction_score = _number(row.get("conviction_score"))
    upside_prediction_rank = _integer(row.get("upside_prediction_rank"))
    earnings_days_until = _integer(row.get("earnings_days_until"))

    in_earnings_priority_lens = _truthy(row.get("in_earnings_priority_lens_flag"))
    near_earnings = in_earnings_priority_lens or (
        earnings_days_until is not None
        and config.post_earnings_days_min <= earnings_days_until <= config.near_earnings_days_max
    )

    quality_entry_state = entry_state in {"ARMED", "ENTER_STARTER"}
    has_move_support = any(
        (
            conviction_rank is not None and 0 < conviction_rank <= config.book_a_max_conviction_rank,
            conviction_score is not None and conviction_score >= config.book_a_min_conviction_score,
            upside_prediction_rank is not None and 0 < upside_prediction_rank <= config.book_a_max_upside_rank,
        )
    )

    drawdown_from_high_pct = _number(row.get("drawdown_from_high_pct"))
    perf_1m = _number(row.get("perf_1m"))
    expected_move_proxy_pct = _number(row.get("expected_move_proxy_pct"))

    deep_drawdown = (
        drawdown_from_high_pct is not None
        and drawdown_from_high_pct <= -config.book_b_drawdown_min_pct
    ) or (
        perf_1m is not None and perf_1m <= config.book_b_perf_1m_min_pct
    )
    high_expected_move = (
        expected_move_proxy_pct is not None
        and expected_move_proxy_pct >= config.book_b_expected_move_min_pct
    )

    caution_or_speculative_context = any(
        (
            _contains_token(row.get("safety_bucket"), {"speculative", "caution"}),
            _contains_token(row.get("directional_lean"), {"caution", "avoid", "speculative"}),
            _contains_token(row.get("opportunity_tier"), {"speculative", "binary"}),
            entry_state in {"REJECT", "WATCH", ""},
        )
    )

    book_a_hold = quality_entry_state and near_earnings and has_move_support
    book_a_derisk = quality_entry_state and near_earnings and not has_move_support
    book_b_spec = (
        near_earnings
        and deep_drawdown
        and high_expected_move
        and caution_or_speculative_context
    )

    if book_a_hold:
        event_action = "BOOK_A_HOLD_THROUGH"
        event_book = "BOOK_A"
        normalized_risk_units = 1.0
        size_note = "Full event size allowed while support remains intact."
        pre_print_trim_rule = "Trim only if move support weakens before print."
        invalidation_rule = "Downgrade on support break or unresolved guide risk."
    elif book_a_derisk:
        event_action = "BOOK_A_DERISK_INTO_PRINT"
        event_book = "BOOK_A"
        normalized_risk_units = 0.5
        size_note = "Reduce exposure into print and keep a managed stub only."
        pre_print_trim_rule = "Trim 30-50% before print unless support improves."
        invalidation_rule = "Exit stub on guide cut, supply shock, or support collapse."
    elif book_b_spec:
        event_action = "BOOK_B_SPEC_EARN"
        event_book = "BOOK_B"
        normalized_risk_units = 0.25
        size_note = "Speculative sleeve only; size small."
        pre_print_trim_rule = "Take profits quickly into sharp squeeze moves."
        invalidation_rule = "Exit on failed reaction or 2-5 day time stop."
    elif near_earnings:
        event_action = "WATCH_CATALYST"
        event_book = "WATCH"
        normalized_risk_units = 0.0
        size_note = "Monitor catalyst while waiting for cleaner Book A/B gates."
        pre_print_trim_rule = "No pre-print adds until a classified setup appears."
        invalidation_rule = "Promote only after Book A/B gates trigger."
    else:
        event_action = "PASS"
        event_book = "NONE"
        normalized_risk_units = 0.0
        size_note = "No event allocation."
        pre_print_trim_rule = "None."
        invalidation_rule = "None."

    gate_checks = {
        "near_earnings": near_earnings,
        "in_earnings_priority_lens": in_earnings_priority_lens,
        "quality_entry_state": quality_entry_state,
        "has_move_support": has_move_support,
        "deep_drawdown": deep_drawdown,
        "high_expected_move": high_expected_move,
        "caution_or_speculative_context": caution_or_speculative_context,
        "book_a_hold": book_a_hold,
        "book_a_derisk": book_a_derisk,
        "book_b_spec": book_b_spec,
    }
    matched_gates = [name for name, enabled in gate_checks.items() if enabled]

    score = 0.0
    score += 30.0 if near_earnings else 0.0
    score += 20.0 if quality_entry_state else 0.0
    score += 20.0 if has_move_support else 0.0
    score += 15.0 if deep_drawdown else 0.0
    score += 15.0 if high_expected_move else 0.0
    score += 10.0 if caution_or_speculative_context else 0.0
    event_policy_score = round(_clamp(score), 2)

    audit_trail_json = json.dumps(gate_checks, sort_keys=True, separators=(",", ":"))
    evidence = {
        "entry_state": entry_state,
        "trade_plan_rank": _integer(row.get("trade_plan_rank")),
        "trade_plan_score": _number(row.get("trade_plan_score")),
        "safety_bucket": _text(row.get("safety_bucket")),
        "directional_lean": _text(row.get("directional_lean")),
        "opportunity_tier": _text(row.get("opportunity_tier")),
        "earnings_days_until": earnings_days_until,
        "conviction_rank": conviction_rank,
        "conviction_score": conviction_score,
        "upside_prediction_rank": upside_prediction_rank,
        "drawdown_from_high_pct": drawdown_from_high_pct,
        "perf_1m": perf_1m,
        "expected_move_proxy_pct": expected_move_proxy_pct,
        "expected_move_horizon_days": _integer(row.get("expected_move_horizon_days")),
        "current_day_pct": _number(row.get("current_day_pct")),
        "gap_pct": _number(row.get("gap_pct")),
        "atrp": _number(row.get("atrp")),
        "adrp_percentile": _number(row.get("adrp_percentile")),
        "same_day_scan_count": _integer(row.get("same_day_scan_count")),
        "existing_conviction_state": _text(row.get("conviction_state")),
    }

    score_row = {
        "symbol": symbol,
        "checkpoint": checkpoint,
        "event_policy_score": event_policy_score,
        "event_action": event_action,
        "event_book": event_book,
        "matched_gates": "|".join(matched_gates),
        "gate_count": len(matched_gates),
        "audit_trail_json": audit_trail_json,
        **evidence,
        **{key: int(value) for key, value in gate_checks.items()},
    }

    policy_row = {
        "symbol": symbol,
        "checkpoint": checkpoint,
        "event_action": event_action,
        "event_book": event_book,
        "event_policy_score": event_policy_score,
        "normalized_risk_units": normalized_risk_units,
        "size_note": size_note,
        "pre_print_trim_rule": pre_print_trim_rule,
        "invalidation_rule": invalidation_rule,
        "matched_gates": "|".join(matched_gates),
        "audit_trail_json": audit_trail_json,
        **evidence,
    }
    return score_row, policy_row


def build_catalyst_event_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    checkpoint: str,
    config: CatalystEventPolicyConfig = CatalystEventPolicyConfig(),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    score_rows: list[dict[str, Any]] = []
    policy_rows: list[dict[str, Any]] = []
    for row in rows:
        score_row, policy_row = evaluate_catalyst_event_row(
            row,
            checkpoint=checkpoint,
            config=config,
        )
        score_rows.append(score_row)
        policy_rows.append(policy_row)

    score_rows.sort(
        key=lambda item: (
            float(item.get("event_policy_score") or 0.0),
            str(item.get("symbol") or ""),
        ),
        reverse=True,
    )

    action_priority = {
        "BOOK_A_HOLD_THROUGH": 5,
        "BOOK_A_DERISK_INTO_PRINT": 4,
        "BOOK_B_SPEC_EARN": 3,
        "WATCH_CATALYST": 2,
        "PASS": 1,
    }
    policy_rows.sort(
        key=lambda item: (
            action_priority.get(str(item.get("event_action") or ""), 0),
            float(item.get("event_policy_score") or 0.0),
            str(item.get("symbol") or ""),
        ),
        reverse=True,
    )
    for rank, row in enumerate(policy_rows, start=1):
        row["event_policy_rank"] = rank
    return score_rows, policy_rows
