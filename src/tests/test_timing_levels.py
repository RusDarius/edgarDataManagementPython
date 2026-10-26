"""Timing entry, kill, and thesis-change prices."""

from generic_utils.risk import entry_exit_band
from generic_utils.timing_levels import (
    apply_caller_screen,
    attach_timing_levels,
    load_screen_recipe,
    load_timing_recipe,
)


def test_stop_below_kill_uses_timing_atr_not_the_book_1_5() -> None:
    recipe = load_timing_recipe()
    levels = attach_timing_levels(
        {"close": 100.0, "vs50": 2.0, "atrp": 4.0, "street_left": 40.0},
        recipe=recipe,
        stop_atr_units=1.0,
    )
    band = entry_exit_band(100.0, 2.0, atrp=4.0, mult=1.0, band_atr=0.5)
    assert levels["sma_role"] == "stop_below"
    assert levels["entry_low"] == band["entry_low"]
    assert levels["entry_high"] == band["entry_high"]
    assert levels["kill_px"] == band["exit_stop"]
    assert levels["kill_atr_units"] == 1.0
    assert levels["trim_state"] == "trim_at_sma"
    assert levels["trim_px"] == levels["entry_low"]
    book = entry_exit_band(100.0, 2.0, atrp=4.0, mult=1.5, band_atr=0.5)
    assert levels["kill_px"] != book["exit_stop"]
    assert levels["downside_atr_pct"] == 6.0
    assert levels["street_up_pct"] == 40.0
    assert levels["up_down_ratio"] == round(40.0 / 6.0, 2)


def test_reclaim_kill_sits_under_the_50_day() -> None:
    levels = attach_timing_levels(
        {"close": 90.0, "vs50": -10.0, "atrp": 4.0, "stop_atr_units": 0.75}
    )
    band = entry_exit_band(90.0, -10.0, atrp=4.0, mult=0.75, band_atr=0.5)
    assert levels["sma_role"] == "reclaim"
    assert levels["entry_low"] == band["entry_low"]
    assert levels["kill_px"] == band["exit_stop"]
    assert levels["kill_px"] < levels["entry_low"]
    assert levels["trim_state"] == "through_average"
    assert levels["trim_px"] is None


def test_sma_column_fills_vs50() -> None:
    levels = attach_timing_levels({"current_close": 110.0, "sma50": 100.0, "atrp": 2.0})
    assert levels["sma_role"] == "stop_below"
    assert levels["entry_high"] == 110.0


def test_drawdown_and_chase_lines() -> None:
    levels = attach_timing_levels(
        {
            "close": 80.0,
            "vs50": -5.0,
            "atrp": 3.0,
            "drawdown_from_high_pct": -20.0,
            "current_day_pct": 0.0,
        }
    )
    assert levels["thesis_drawdown_px"] == 90.0
    assert levels["thesis_drawdown_state"] == "in_drawdown"
    assert levels["thesis_chase_px"] == 84.0
    assert levels["thesis_chase_state"] == "under_chase"
    assert levels["thesis_chase_from"] == "session_open"


def test_chase_already_through_a_five_percent_day() -> None:
    levels = attach_timing_levels(
        {"close": 105.0, "vs50": 1.0, "atrp": 2.0, "current_day_pct": 5.0}
    )
    assert levels["thesis_chase_px"] == 105.0
    assert levels["thesis_chase_state"] == "through_chase"


def test_missing_close_stays_empty() -> None:
    levels = attach_timing_levels({"symbol": "NASDAQ:MU"})
    assert levels["entry_low"] is None
    assert levels["kill_px"] is None
    assert levels["sma_role"] == "unknown"
    assert levels["thesis_drawdown_state"] == "unknown"
    assert levels["up_down_ratio"] is None


def test_policy_row_carries_the_same_kill_as_the_generic_level() -> None:
    from edge_research_tools.market_timing_policy import evaluate_timing_policy_row

    _, policy = evaluate_timing_policy_row(
        {
            "symbol": "NASDAQ:DOX",
            "upside_prediction_rank": 4,
            "close": 60.0,
            "vs50": -1.0,
            "atrp": 2.0,
            "drawdown_from_high_pct": -12.0,
            "consecutive_down_days": 2,
            "perf_5d": -3.0,
            "perf_1m": 4.0,
            "current_day_pct": 1.0,
        },
        checkpoint="latest",
    )
    levels = attach_timing_levels(
        {
            "close": 60.0,
            "vs50": -1.0,
            "atrp": 2.0,
            "drawdown_from_high_pct": -12.0,
            "current_day_pct": 1.0,
            "stop_atr_units": policy["stop_atr_units"],
        }
    )
    assert policy["action"] in {"ENTER_SMALL", "ENTER_PROBE", "WATCH"}
    assert policy["kill_px"] == levels["kill_px"]
    assert policy["entry_low"] == levels["entry_low"]
    assert policy["thesis_drawdown_state"] == "in_drawdown"
    assert policy["thesis_chase_px"] == levels["thesis_chase_px"]


def test_caller_screen_is_the_json_where_not_a_hidden_cutoff() -> None:
    screen = load_screen_recipe()
    rows = [
        {
            "symbol": "NYSE:AAA",
            "ind": "Hotels",
            "close": 100,
            "vs50": 1,
            "atrp": 2,
            "street_left": 30,
            "rng": 20,
            "rsi": 50,
        },
        {
            "symbol": "NYSE:BBB",
            "ind": "Hotels",
            "close": 100,
            "vs50": 1,
            "atrp": 20,
            "street_left": 10,
            "rng": 20,
            "rsi": 50,
        },
    ]
    from generic_utils.timing_levels import attach_rows

    leveled = attach_rows(rows)
    focused = apply_caller_screen(leveled, screen)
    tickers = [row["symbol"] for row in focused["rows"]]
    assert tickers == ["NYSE:AAA"]
    assert "street_up_pct>=8" in screen["where"]
