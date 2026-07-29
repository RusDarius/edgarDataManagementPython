import pytest

from edge_research_tools.catalyst_event_policy import (
    build_catalyst_event_rows,
    evaluate_catalyst_event_row,
)


def test_logn_like_row_derisks_into_print_when_move_support_is_weak() -> None:
    _, policy = evaluate_catalyst_event_row(
        {
            "symbol": "SIX:LOGN",
            "entry_state": "ARMED",
            "earnings_days_until": 6,
            "conviction_score": 0.41,
            "rank_overall": 1606,
            "upside_prediction_rank": 420,
            "drawdown_from_high_pct": -7.0,
            "expected_move_proxy_pct": 9.5,
            "safety_bucket": "balanced",
        },
        checkpoint="latest",
    )

    assert policy["event_action"] == "BOOK_A_DERISK_INTO_PRINT"
    assert policy["event_book"] == "BOOK_A"
    assert policy["normalized_risk_units"] == 0.5


def test_stx_be_like_row_is_classified_as_spec_event_book() -> None:
    _, policy = evaluate_catalyst_event_row(
        {
            "symbol": "NASDAQ:STX",
            "entry_state": "REJECT",
            "earnings_days_until": 2,
            "drawdown_from_high_pct": -27.0,
            "perf_1m": -24.0,
            "expected_move_proxy_pct": 26.0,
            "safety_bucket": "speculative",
            "directional_lean": "CAUTION_DOWNSIDE",
        },
        checkpoint="latest",
    )

    assert policy["event_action"] == "BOOK_B_SPEC_EARN"
    assert policy["event_book"] == "BOOK_B"
    assert policy["normalized_risk_units"] == 0.25


def test_strong_armed_setup_holds_through_event() -> None:
    _, policy = evaluate_catalyst_event_row(
        {
            "symbol": "NASDAQ:GOOD",
            "entry_state": "ENTER_STARTER",
            "earnings_days_until": 4,
            "conviction_score": 0.72,
            "rank_overall": 145,
            "upside_prediction_rank": 88,
            "drawdown_from_high_pct": -6.0,
            "expected_move_proxy_pct": 10.0,
        },
        checkpoint="latest",
    )

    assert policy["event_action"] == "BOOK_A_HOLD_THROUGH"
    assert policy["normalized_risk_units"] == pytest.approx(1.0)


def test_far_from_earnings_is_pass() -> None:
    _, policy = evaluate_catalyst_event_row(
        {
            "symbol": "NASDAQ:WAIT",
            "entry_state": "WATCH",
            "earnings_days_until": 45,
            "conviction_score": 0.33,
            "rank_overall": 1400,
            "upside_prediction_rank": 900,
        },
        checkpoint="latest",
    )

    assert policy["event_action"] == "PASS"
    assert policy["normalized_risk_units"] == 0.0


def test_policy_rows_are_ranked_by_action_priority_then_score() -> None:
    _, policy_rows = build_catalyst_event_rows(
        [
            {
                "symbol": "NASDAQ:PASS",
                "entry_state": "WATCH",
                "earnings_days_until": 60,
            },
            {
                "symbol": "NASDAQ:SPEC",
                "entry_state": "REJECT",
                "earnings_days_until": 3,
                "drawdown_from_high_pct": -20.0,
                "expected_move_proxy_pct": 20.0,
                "safety_bucket": "speculative",
            },
            {
                "symbol": "NASDAQ:HOLD",
                "entry_state": "ARMED",
                "earnings_days_until": 5,
                "conviction_score": 0.9,
                "rank_overall": 50,
            },
        ],
        checkpoint="latest",
    )

    assert policy_rows[0]["event_action"] == "BOOK_A_HOLD_THROUGH"
    assert policy_rows[1]["event_action"] == "BOOK_B_SPEC_EARN"
    assert policy_rows[-1]["event_action"] == "PASS"
    assert policy_rows[0]["event_policy_rank"] == 1
