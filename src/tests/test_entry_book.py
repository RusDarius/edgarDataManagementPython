from generic_utils.entry_book import (
    attach_entry_book,
    attach_later_mark,
    band_position,
    payoff_at_band,
)


def test_reclaim_band_is_above_the_mark_and_kill_is_under_it() -> None:
    rows = attach_entry_book(
        [
            {
                "ticker": "ITRI",
                "close": 100,
                "vs50": -10,
                "atrp": 4,
                "street_px": 140,
                "target_px": 150,
                "in_book": False,
            }
        ]
    )
    row = rows[0]
    assert row["sma_role"] == "reclaim"
    assert row["entry_low"] == 111.11
    assert row["entry_high"] == 113.11
    assert row["kill_px"] == 104.44
    assert row["kill_scope"] == "this_entry"
    assert row["exit_target"] == 140.0
    assert row["exit_target_pack"] == 150.0
    assert row["target_source"] == "street_px"
    assert row["mark_vs_band"] == "below_band"
    assert row["trim_px"] is None
    assert row["trim_state"] == "through_average"
    assert row["size_role"] == "starter"
    assert row["advised_usd"] == 5000
    assert row["rr_at_band"] is not None and row["rr_at_band"] > 0


def test_above_the_average_the_band_runs_up_to_the_mark() -> None:
    rows = attach_entry_book(
        [
            {
                "ticker": "TDC",
                "close": 100,
                "vs50": 5,
                "atrp": 4,
                "target_px": 130,
                "in_book": "true",
                "average_price": 120,
            }
        ]
    )
    row = rows[0]
    assert row["sma_role"] == "stop_below"
    assert row["entry_low"] == 95.24
    assert row["entry_high"] == 100.0
    assert row["mark_vs_band"] == "inside"
    assert row["kill_px"] == 94.0
    assert row["trim_px"] == 95.24
    assert row["trim_state"] == "trim_at_sma"
    assert row["cost_kill_px"] == 102.0
    assert row["size_role"] == "add"
    assert row["advised_usd"] == 1000
    assert row["target_source"] == "target_px"


def test_later_close_through_the_kill_does_not_move_the_band() -> None:
    rows = attach_entry_book([{"ticker": "X", "close": 100, "vs50": 5, "atrp": 4, "street_px": 130}])
    marked = attach_later_mark(rows, {"X": 90})
    assert marked[0]["entry_low"] == 95.24
    assert marked[0]["kill_px"] == 94.0
    assert marked[0]["close_now"] == 90
    assert marked[0]["band_state_now"] == "through_kill"


def test_band_position_and_payoff_stay_ratios() -> None:
    assert band_position(100, 95, 100) == "inside"
    assert band_position(101, 95, 100) == "above_band"
    assert band_position(90, 95, 100) == "below_band"
    payoff = payoff_at_band(100, 90, 130)
    assert payoff["rr_at_band"] == 3.0
    assert payoff["kill_from_entry_pct"] == -10.0
    assert payoff["target_from_entry_pct"] == 30.0
