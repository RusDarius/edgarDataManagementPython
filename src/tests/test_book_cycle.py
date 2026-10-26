from generic_utils.book_cycle import attach_book_cycle_row, build_book_cycle, cycle_read, range_seat
from generic_utils.series import series_span


def test_span_records_the_low_and_the_high() -> None:
    rows = [
        {"symbol": "NASDAQ:MU", "as_of": "2026-04-01", "close": 80},
        {"symbol": "NASDAQ:MU", "as_of": "2026-07-01", "close": 120},
        {"symbol": "NASDAQ:MU", "as_of": "2026-10-01", "close": 100},
    ]
    span = series_span(rows, id_field="symbol", fields=["close"])[0]
    assert span["close_first"] == 80
    assert span["close_last"] == 100
    assert span["close_min"] == 80
    assert span["close_min_as_of"] == "2026-04-01"
    assert span["close_max"] == 120
    assert span["close_max_as_of"] == "2026-07-01"


def test_seat_is_halfway_when_the_close_splits_the_range() -> None:
    seat = range_seat(100, 80, 120)
    assert seat["seat_pct"] == 50.0
    assert seat["dd_from_high_pct"] == round((100 / 120 - 1) * 100, 2)
    assert seat["up_from_low_pct"] == 25.0


def test_peak_margin_wins_over_revenue_growth() -> None:
    assert cycle_read(30, 40, 64) == "peak_margin"
    assert cycle_read(10, 80, 20) == "earn_leads"
    assert cycle_read(18, 22, 16) == "rev_carries"
    assert cycle_read(4, 6, 12) == "thin"


def test_resilient_needs_revenue_a_book_discount_and_room_under_the_high() -> None:
    held = attach_book_cycle_row(
        {
            "symbol": "NASDAQ:AAA",
            "ind": "Semiconductors",
            "close": 90,
            "close_min": 70,
            "close_max": 120,
            "pb": 3.2,
            "pb_vs_ind": -30,
            "rev_yoy": 18,
            "earn_yoy": 22,
            "net_margin": 16,
        }
    )
    assert held["cycle_read"] == "rev_carries"
    assert held["pb_read"] == "DISCOUNT"
    assert held["resilient"] is True

    at_high = attach_book_cycle_row(
        {
            "symbol": "NASDAQ:BBB",
            "close": 120,
            "close_min": 70,
            "close_max": 120,
            "pb_vs_ind": -20,
            "rev_yoy": 18,
            "earn_yoy": 20,
            "net_margin": 16,
        }
    )
    assert at_high["seat_pct"] == 100.0
    assert at_high["resilient"] is False


def test_a_900_percent_revenue_print_is_a_base_effect() -> None:
    row = attach_book_cycle_row(
        {
            "symbol": "NASDAQ:TINY",
            "close": 7,
            "close_min": 6,
            "close_max": 12,
            "pb_vs_ind": -40,
            "rev_yoy": 979,
            "earn_yoy": None,
            "net_margin": 20,
        }
    )
    assert row["base_effect"] is True
    assert row["resilient"] is False


def test_industry_list_keeps_the_named_set() -> None:
    rows = build_book_cycle(
        [
            {"symbol": "NASDAQ:MU", "ind": "Semiconductors", "close": 100, "pb_vs_ind": -10, "rev_yoy": 20, "earn_yoy": 22, "net_margin": 15, "close_min": 80, "close_max": 140},
            {"symbol": "NASDAQ:SHOP", "ind": "Packaged Software", "close": 50, "pb_vs_ind": -40, "rev_yoy": 30, "earn_yoy": 30, "net_margin": 20, "close_min": 40, "close_max": 60},
        ],
        recipe={"industries": ["Semiconductors"], "seat_max": 85},
    )
    assert [row["symbol"] for row in rows] == ["NASDAQ:MU"]
