from generic_utils.discount_path import (
    apply_window_span,
    build_discount_tables,
    multiple_peer,
    path_label,
    strength_read,
    upside_to_median,
)


def test_upside_to_median_is_the_gap_not_a_blend() -> None:
    assert upside_to_median(10, 15) == 50.0
    assert upside_to_median(20, 15) == -25.0
    assert upside_to_median(-4, 15) is None
    assert upside_to_median(10, None) is None


def test_peer_bin_matches_the_five_percent_band() -> None:
    assert multiple_peer(-12, has_value=True, thin=False) == "DISCOUNT"
    assert multiple_peer(8, has_value=True, thin=False) == "PREMIUM"
    assert multiple_peer(1, has_value=True, thin=False) == "INLINE"
    assert multiple_peer(None, has_value=True, thin=True) == "THIN"
    assert multiple_peer(None, has_value=False, thin=False) == "NA"


def test_path_label_needs_enough_prints() -> None:
    assert path_label(-12, 8) == "CHEAPER"
    assert path_label(9, 8) == "RICHER"
    assert path_label(-1, 8) == "FLAT"
    assert path_label(-12, 2) == "THIN_HIST"


def test_strength_labels_stay_separate_from_the_multiple() -> None:
    assert strength_read(-100, 50, 20, 10) == "net_cash"
    assert strength_read(100, 80, 12, 4) == "covered"
    assert strength_read(500, 50, 30, 8) == "levered"
    assert strength_read(100, 80, 2, 4) == "open"
    assert strength_read(None, None, None, None) == "thin"


def test_tape_keeps_a_premium_and_the_reading_where_does_not() -> None:
    rows = [
        {
            "symbol": "NASDAQ:CHEAP",
            "ind": "Semiconductors",
            "close": 80,
            "pe": 10,
            "evrev": 2,
            "opm": 20,
            "net_debt": -5,
            "ebitda": 40,
            "fcf": 10,
            "rev_ttm": 100,
            "pt": 100,
        },
        {
            "symbol": "NASDAQ:RICH",
            "ind": "Semiconductors",
            "close": 120,
            "pe": 40,
            "evrev": 12,
            "opm": 20,
            "net_debt": -5,
            "ebitda": 40,
            "fcf": 10,
            "rev_ttm": 100,
            "pt": 130,
        },
        {
            "symbol": "NASDAQ:PEER",
            "ind": "Semiconductors",
            "close": 100,
            "pe": 20,
            "evrev": 6,
            "opm": 10,
            "net_debt": 10,
            "ebitda": 20,
            "fcf": 1,
            "rev_ttm": 50,
        },
        {
            "symbol": "NASDAQ:FOUR",
            "ind": "Semiconductors",
            "close": 90,
            "pe": 18,
            "evrev": 5,
            "opm": 11,
            "net_debt": 8,
            "ebitda": 20,
        },
        {
            "symbol": "NASDAQ:FIVE",
            "ind": "Semiconductors",
            "close": 95,
            "pe": 22,
            "evrev": 7,
            "opm": 9,
            "net_debt": 12,
            "ebitda": 20,
        },
        {
            "symbol": "NASDAQ:SIX",
            "ind": "Semiconductors",
            "close": 110,
            "pe": 24,
            "evrev": 8,
            "opm": 15,
            "net_debt": 6,
            "ebitda": 20,
        },
    ]
    span = [
        {
            "symbol": "NASDAQ:CHEAP",
            "n": 8,
            "first_as_of": "2026-07-15",
            "last_as_of": "2026-10-15",
            "pe_pct": -20,
            "pe_first": 12.5,
            "pe_last": 10,
            "evrev_pct": -15,
            "evrev_first": 2.4,
            "evrev_last": 2.0,
            "close_pct": -5,
        }
    ]
    tables = build_discount_tables(rows, span)
    tape = {row["symbol"]: row for row in tables["discount_tape"]}
    assert set(tape) == {row["symbol"] for row in rows}
    cheap = tape["NASDAQ:CHEAP"]
    assert cheap["pe_peer"] == "DISCOUNT"
    assert cheap["pe_up_pct"] is not None and cheap["pe_up_pct"] > 0
    assert cheap["pe_path"] == "CHEAPER"
    assert cheap["evrev_path"] == "CHEAPER"
    assert cheap["cheaper_count"] == 2
    assert cheap["strength_read"] == "net_cash"
    assert cheap["street_left"] == 25.0
    assert cheap["widest_up_pct"] == max(cheap["pe_up_pct"], cheap["evrev_up_pct"])
    assert tape["NASDAQ:RICH"]["pe_peer"] in {"PREMIUM", "INLINE", "DISCOUNT"}
    symbols = [row["symbol"] for row in tables["reading"]]
    assert symbols == ["NASDAQ:CHEAP"]
    assert tables["pe_path"][0]["symbol"] == "NASDAQ:CHEAP"


def test_a_negative_multiple_does_not_set_the_peer_median() -> None:
    rows = [
        {"symbol": f"NASDAQ:N{i}", "ind": "Banks", "pe": pe, "close": 10}
        for i, pe in enumerate([8, 9, 10, 10, 12, 14, -40])
    ]
    tape = {row["symbol"]: row for row in build_discount_tables(rows)["discount_tape"]}
    assert tape["NASDAQ:N6"]["pe_peer"] == "NA"
    assert tape["NASDAQ:N0"]["pe_ind_med"] == 10.0
    assert tape["NASDAQ:N0"]["pe_peer"] == "DISCOUNT"


def test_a_multiple_that_crosses_zero_is_not_cheaper() -> None:
    row = apply_window_span(
        [{"symbol": "NASDAQ:LOSS", "ind": "Banks", "pe": -4}],
        [{"symbol": "NASDAQ:LOSS", "n": 8, "pe_pct": -120, "pe_first": 20, "pe_last": -4}],
    )[0]
    assert row["pe_path"] == "THIN_HIST"
    assert row["cheaper_count"] == 0
    assert row["pe_pct"] == -120


def test_book_multiple_uses_the_quarterly_print_when_the_ratio_is_missing() -> None:
    from generic_utils.discount_path import prepare_discount_row

    row = prepare_discount_row(
        {"symbol": "NYSE:X", "price_book_fq": 1.4, "net_debt": 10, "ebitda": 20, "opm": 4}
    )
    assert row["pb"] == 1.4
    assert row["pb_source"] == "price_book_fq"


def test_a_field_print_count_can_clear_thin_history() -> None:
    row = apply_window_span(
        [{"symbol": "NASDAQ:MU", "ind": "Semiconductors", "pe": 11}],
        [{
            "symbol": "NASDAQ:MU",
            "n": 2,
            "pe_n": 8,
            "pe_pct": -20,
            "pe_first": 14,
            "pe_last": 11,
        }],
    )[0]
    assert row["pe_path"] == "CHEAPER"
    assert row["pe_n"] == 8
    row = apply_window_span(
        [{"symbol": "NASDAQ:MU", "ind": "Semiconductors", "pe": 11}],
        [],
    )[0]
    assert row["pe_path"] == "THIN_HIST"
    assert row["cheaper_count"] == 0
    assert row["hist_n"] is None
