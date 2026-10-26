from generic_utils.payoff_focus import (
    allocation_sleeve,
    attach_payoff_row,
    capital_for_upside,
    focus_payoff,
    load_payoff_recipe,
)


def test_ten_percent_upside_is_a_ten_thousand_dollar_check() -> None:
    money = capital_for_upside(10)
    assert money["stake_per_1k_usd"] == 10000
    assert money["profit_on_10k_usd"] == 1000
    assert money["profit_on_5k_usd"] == 500


def test_fifty_percent_upside_is_a_two_thousand_dollar_check() -> None:
    money = capital_for_upside(50)
    assert money["stake_per_1k_usd"] == 2000
    assert money["profit_on_5k_usd"] == 2500
    assert money["profit_on_10k_usd"] == 5000


def test_discount_needs_momentum_and_premium_needs_strength() -> None:
    recipe = load_payoff_recipe()
    cheap_live = {"val_vs_ind": -20, "bo": 0.4}
    cheap_flat = {"val_vs_ind": -20, "bo": 0.0}
    rich_strong = {"peer": "PREMIUM", "bo": 0.8}
    rich_soft = {"val_vs_ind": 12, "bo": 0.2}
    assert allocation_sleeve(cheap_live, recipe) == "discount_momentum"
    assert allocation_sleeve(cheap_flat, recipe) == ""
    assert allocation_sleeve(rich_strong, recipe) == "premium_strength"
    assert allocation_sleeve(rich_soft, recipe) == ""


def test_band_payoff_uses_the_stop_not_the_52_week_range() -> None:
    row = attach_payoff_row(
        {
            "symbol": "NASDAQ:AAA",
            "ind": "Software",
            "close": 100,
            "vs50": 5,
            "atrp": 4,
            "street_px": 110,
            "val_vs_ind": -20,
            "bo": 0.4,
        }
    )
    assert row["alloc_sleeve"] == "discount_momentum"
    assert row["entry_high"] == 100.0
    assert row["exit_stop"] == 94.0
    assert row["exit_target"] == 110.0
    assert row["up_from_entry_pct"] == 10.0
    assert row["stake_per_1k_usd"] == 10000
    assert row["profit_on_10k_usd"] == 1000
    assert row["band_payoff"] == round((110 - 100) / (100 - 94), 2)


def test_tighter_stop_and_larger_upside_sorts_first() -> None:
    wide = {
        "symbol": "NASDAQ:WIDE",
        "ind": "Banks",
        "close": 100,
        "vs50": 5,
        "atrp": 4,
        "street_px": 110,
        "val_vs_ind": -12,
        "bo": 0.3,
    }
    tight = {
        "symbol": "NASDAQ:TIGHT",
        "ind": "Banks",
        "close": 100,
        "vs50": 5,
        "atrp": 2,
        "street_px": 150,
        "val_vs_ind": 12,
        "bo": 0.8,
    }
    built = focus_payoff([wide, tight], {**load_payoff_recipe(), "cap": None, "top": 10})
    assert [row["symbol"] for row in built["focus"]] == ["NASDAQ:TIGHT", "NASDAQ:WIDE"]
    assert built["focus"][0]["stake_per_1k_usd"] == 2000
    assert built["focus"][0]["profit_on_5k_usd"] == 2500
    assert built["focus"][0]["alloc_sleeve"] == "premium_strength"


def test_year1_value_outranks_a_loose_street_target() -> None:
    from generic_utils.payoff_focus import year_value_prices

    years = year_value_prices(100, 15, 10)
    assert years["value_y1_px"] == 150.0
    assert "value_y3_px" not in years
    assert "value_y5_px" not in years
    rich_street = attach_payoff_row(
        {
            "symbol": "NASDAQ:STREET",
            "ind": "Software",
            "close": 100,
            "vs50": 5,
            "atrp": 4,
            "street_px": 220,
            "fp_pe_y1": 40,
            "pe_fwd": 40,
            "pe_fwd_ind_med": 20,
            "val": 40,
            "val_ind_med": 20,
            "val_vs_ind": -20,
            "bo": 0.4,
        }
    )
    real_value = attach_payoff_row(
        {
            "symbol": "NASDAQ:VALUE",
            "ind": "Banks",
            "close": 100,
            "vs50": 5,
            "atrp": 4,
            "street_px": 110,
            "fp_pe_y1": 10,
            "pe_fwd": 10,
            "pe_fwd_ind_med": 15,
            "val_field": "evebitda",
            "val": 8,
            "val_ind_med": 10,
            "evrev": 4,
            "evrev_ind_med": 5,
            "val_vs_ind": -20,
            "bo": 0.4,
            "close_first": 80,
        }
    )
    assert rich_street["value_px"] == 50.0
    assert rich_street["exit_target"] == 220.0
    assert rich_street["band_payoff_source"] == "value_below"
    assert rich_street["band_payoff"] is None
    assert real_value["value_px"] == 150.0
    assert real_value["pe_discount_px"] == 150.0
    assert real_value["evrev_discount_px"] == 125.0
    assert real_value["evebit_discount_px"] is None
    assert real_value["discount_px"] == 125.0
    assert real_value["discount_source"] == "evrev"
    assert real_value["band_payoff_source"] == "value"
    assert real_value["band_payoff_street"] < real_value["band_payoff"]
    assert real_value["strength_state"] == "above_oldest"
    assert real_value["px_vs_oldest_pct"] == 25.0
    broken = attach_payoff_row(
        {
            "symbol": "NASDAQ:BROKEN",
            "ind": "Software",
            "close": 100,
            "vs50": 5,
            "atrp": 4,
            "street_px": 110,
            "pe_fwd": 0.4,
            "fp_pe_y1": 0.4,
            "pe_fwd_ind_med": 15,
            "val_vs_ind": -20,
            "bo": 0.4,
        }
    )
    assert broken["value_y1_px"] is None
    assert broken["band_payoff_source"] == "street"
    all_three = attach_payoff_row(
        {
            "symbol": "NASDAQ:ALL",
            "ind": "Software",
            "close": 100,
            "vs50": 5,
            "atrp": 4,
            "street_px": 110,
            "fp_pe_y1": 10,
            "pe_fwd_ind_med": 15,
            "evrev": 4,
            "evrev_ind_med": 5,
            "evebit": 8,
            "evebit_ind_med": 12,
            "val_vs_ind": -20,
            "bo": 0.4,
        }
    )
    assert all_three["pe_discount_px"] == 150.0
    assert all_three["evrev_discount_px"] == 125.0
    assert all_three["evebit_discount_px"] == 150.0


def test_industry_cap_leaves_overflow() -> None:
    rows = []
    for index in range(4):
        rows.append(
            {
                "symbol": f"NASDAQ:S{index}",
                "ind": "Software",
                "close": 100,
                "vs50": 5,
                "atrp": 2,
                "street_px": 150 - index,
                "val_vs_ind": -30,
                "bo": 0.6,
            }
        )
    built = focus_payoff(rows, {**load_payoff_recipe(), "cap": 2, "top": 10})
    assert len(built["focus"]) == 2
    assert built["spec"]["n_overflow"] == 2
    assert built["spec"]["n_discount_momentum"] == 4
