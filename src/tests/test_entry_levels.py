from generic_utils.risk import entry_exit_band, entry_levels


def test_above_the_50_day_the_average_is_a_stop() -> None:
    levels = entry_levels(100, 5, atrp=4)
    assert levels["sma_role"] == "stop_below"
    assert levels["sma50"] == 95.24
    assert levels["atr_stop"] == 94.0
    assert levels["c15"] is None


def test_reclaim_band_starts_at_the_50_day_and_keeps_two_exits() -> None:
    band = entry_exit_band(100, -10, atrp=4, target=140)
    assert band["sma_role"] == "reclaim"
    assert band["entry_low"] == 111.11
    assert band["entry_high"] == 113.11
    assert band["exit_stop"] == 104.44
    assert band["exit_target"] == 140.0


def test_above_the_average_the_band_is_the_average_to_the_mark() -> None:
    band = entry_exit_band(100, 5, atrp=4, target=130)
    assert band["sma_role"] == "stop_below"
    assert band["entry_low"] == 95.24
    assert band["entry_high"] == 100.0
    assert band["exit_stop"] == 94.0
    assert band["exit_target"] == 130.0
    levels = entry_levels(100, -10, atrp=4, cost=120)
    assert levels["sma_role"] == "reclaim"
    assert levels["sma50"] == 111.11
    assert levels["c15"] == 102.0
