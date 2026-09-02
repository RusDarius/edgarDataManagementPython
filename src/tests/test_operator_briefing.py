import unittest

from operator_briefing.continuity import continuity_action, is_polar_flip, map_mix_to_naive_book_action
from operator_briefing.sleeves import (
    continuation_paid,
    leftover_pct,
    range_position_pct,
    short_limited_upside,
    unpaid_eligible,
)


class TestLeftover(unittest.TestCase):
    def test_max_of_street_and_edge(self):
        self.assertAlmostEqual(leftover_pct(100, 160, 40), 60)
        self.assertAlmostEqual(leftover_pct(100, 110, 40), 40)
        self.assertIsNone(leftover_pct(None, 110, None))

    def test_range_position(self):
        self.assertAlmostEqual(range_position_pct(50, 0, 100), 50)
        self.assertIsNone(range_position_pct(50, 80, 80))


class TestUnpaidFilter(unittest.TestCase):
    def test_rejects_biotech_leftover_dump(self):
        row = {
            "symbol": "NASDAQ:CAPR",
            "close": 20,
            "mcap": 3_000_000_000,
            "left": 90,
            "rsi": 45,
            "ind": "Biotechnology",
            "bo": 0.05,
            "cont": 0.0,
            "fwd": 0.0,
            "opp": 80,
        }
        self.assertFalse(unpaid_eligible(row))

    def test_keeps_memory_leftover_with_live_profile(self):
        row = {
            "symbol": "NASDAQ:SNDK",
            "close": 1500,
            "mcap": 200_000_000_000,
            "left": 64,
            "rsi": 52,
            "ind": "Computer Peripherals",
            "bo": 1.05,
            "cont": 1.19,
            "fwd": 1.23,
            "opp": 8,
        }
        self.assertTrue(unpaid_eligible(row))

    def test_book_bypasses_industry_skip(self):
        row = {
            "symbol": "NYSE:UNH",
            "close": 400,
            "mcap": 350_000_000_000,
            "left": 23,
            "rsi": 48,
            "ind": "Managed Health Care",
            "bo": 0.7,
        }
        self.assertTrue(unpaid_eligible(row, book_symbols={"NYSE:UNH"}))

    def test_rejects_stretched_rsi(self):
        row = {
            "symbol": "NYSE:MPC",
            "close": 380,
            "mcap": 50_000_000_000,
            "left": 40,
            "rsi": 78,
            "ind": "Oil Refining/Marketing",
            "bo": 1.5,
            "opp": 5,
        }
        self.assertFalse(unpaid_eligible(row))


class TestPaidAndShorts(unittest.TestCase):
    def test_refiner_is_paid_chase(self):
        row = {"bo": 1.56, "rsi": 78, "rng": 100, "left": -12}
        self.assertTrue(continuation_paid(row))

    def test_short_rejects_high_leftover(self):
        row = {"left": 91, "d5": -2, "bo": 0, "cont": 0}
        self.assertFalse(short_limited_upside(row))

    def test_short_accepts_failed_leader_limited_leftover(self):
        row = {"left": 11, "d5": -12, "bo": 0.1, "cont": 0.2, "vs50": -3.6}
        self.assertTrue(short_limited_upside(row))


class TestContinuity(unittest.TestCase):
    def test_polar_is_add_to_exit(self):
        self.assertTrue(is_polar_flip("ADD", "EXIT"))
        self.assertFalse(is_polar_flip("HOLD", "EXIT"))

    def test_trap_on_yesterday_add_is_blocked(self):
        naive = map_mix_to_naive_book_action("avoid_value_trap")
        result = continuity_action(
            prior={"action": "ADD", "invalidation": 18.5},
            naive=naive,
            close=21.66,
            vs_cost_pct=-1.5,
            weeks_ras=0.42,
            months_ras=0.40,
            lost_sma50=False,
            leftover=60,
            rsi=56,
            rng=33,
        )
        self.assertTrue(result["polar_blocked"])
        self.assertEqual(result["action"], "HOLD_NO_ADD")

    def test_deep_vs_cost_and_lost_sma50_allows_exit(self):
        result = continuity_action(
            prior={"action": "ADD", "invalidation": 26},
            naive="EXIT",
            close=27.86,
            vs_cost_pct=-22.9,
            weeks_ras=0.05,
            months_ras=0.07,
            lost_sma50=True,
            leftover=91,
            rsi=34,
            rng=27,
        )
        self.assertTrue(result["thesis_kill"])
        self.assertEqual(result["action"], "EXIT")

    def test_prior_exit_does_not_resurrect_on_mix_hold(self):
        result = continuity_action(
            prior={"action": "EXIT", "invalidation": 26},
            naive="HOLD",
            close=27.86,
            vs_cost_pct=-22.9,
            weeks_ras=0.05,
            months_ras=0.07,
            lost_sma50=True,
            leftover=91,
            rsi=34,
            rng=27,
        )
        self.assertEqual(result["action"], "EXIT")


if __name__ == "__main__":
    unittest.main()
