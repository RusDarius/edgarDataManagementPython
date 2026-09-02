import unittest

from operator_briefing.continuity import continuity_action, is_polar_flip, map_mix_to_naive_book_action
from operator_briefing.sleeves import (
    continuation_paid,
    leftover_pct,
    range_position_pct,
    short_limited_upside,
    unpaid_eligible,
    unpaid_sort_key,
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

    def test_unpaid_sort_prefers_live_edge_over_leftover_dump(self):
        sndk = {"symbol": "NASDAQ:SNDK", "bo": 1.05, "cont": 1.19, "fwd": 1.23, "opp": 8, "left": 64}
        junk = {"symbol": "NASDAQ:ZIONP", "bo": 0.0, "cont": 0.0, "fwd": 0.0, "opp": None, "left": 180}
        ranked = sorted([junk, sndk], key=unpaid_sort_key)
        self.assertEqual(ranked[0]["symbol"], "NASDAQ:SNDK")

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

    def test_rejects_non_us_prefix(self):
        row = {
            "symbol": "HKEX:2665",
            "exchange": "HKEX",
            "country": "Hong Kong",
            "close": 20,
            "mcap": 8_000_000_000,
            "left": 40,
            "rsi": 45,
            "bo": 0.8,
        }
        self.assertFalse(unpaid_eligible(row))
        self.assertFalse(short_limited_upside({**row, "left": 11, "d5": -12, "cont": 0.1}))

    def test_rejects_otc_even_if_country_is_us(self):
        row = {
            "symbol": "OTC:REEMF",
            "exchange": "OTC",
            "country": "United States",
            "close": 20,
            "mcap": 8_000_000_000,
            "left": 40,
            "rsi": 45,
            "bo": 0.8,
        }
        self.assertFalse(unpaid_eligible(row))


class TestPaidAndShorts(unittest.TestCase):
    def test_refiner_is_paid_chase(self):
        row = {"symbol": "NYSE:MPC", "bo": 1.56, "rsi": 78, "rng": 100, "left": -12}
        self.assertTrue(continuation_paid(row))

    def test_short_rejects_high_leftover(self):
        row = {"symbol": "NASDAQ:JD", "left": 91, "d5": -2, "bo": 0, "cont": 0}
        self.assertFalse(short_limited_upside(row))

    def test_short_accepts_failed_leader_limited_leftover(self):
        row = {"symbol": "NYSE:WEAK", "left": 11, "d5": -12, "bo": 0.1, "cont": 0.2, "vs50": -3.6}
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


class TestLeftoverFlagsAndStance(unittest.TestCase):
    def test_unpaid_live_memory_is_new(self):
        row = {
            "symbol": "NASDAQ:SNDK",
            "exchange": "NASDAQ",
            "country": "United States",
            "close": 1500,
            "mcap": 200_000_000_000,
            "left": 64,
            "rsi": 52,
            "rng": 40,
            "ind": "Computer Peripherals",
            "bo": 1.05,
            "cont": 1.19,
            "fwd": 1.23,
            "opp": 8,
        }
        from operator_briefing.stance import leftover_flags, suggest_stance

        flags = leftover_flags(row)
        self.assertTrue(flags["unpaid"])
        self.assertFalse(flags["chase"])
        result = suggest_stance(row, delta={"bo": 0.2})
        self.assertEqual(result["stance"], "NEW")
        self.assertGreaterEqual(result["suggested_conviction"], 0.55)
        self.assertLess(result["suggested_conviction"], 0.91)
        self.assertIn("Starter NEW leftover sleeve", result["course"])

    def test_paid_refiner_is_pass(self):
        from operator_briefing.stance import leftover_flags, suggest_stance

        row = {
            "symbol": "NYSE:MPC",
            "exchange": "NYSE",
            "left": -12,
            "rsi": 78,
            "rng": 100,
            "bo": 1.56,
            "ind": "Oil Refining/Marketing",
        }
        flags = leftover_flags(row)
        self.assertTrue(flags["paid"])
        self.assertTrue(flags["chase"])
        result = suggest_stance(row)
        self.assertEqual(result["stance"], "PASS")
        self.assertTrue(any("crowd" in c.lower() for c in result["conflicts"]))

    def test_exit_with_large_leftover_is_not_a_short(self):
        from operator_briefing.stance import suggest_stance

        row = {
            "symbol": "NASDAQ:JD",
            "left": 91,
            "rsi": 34,
            "rng": 27,
            "bo": 0.05,
            "d5": -2,
        }
        result = suggest_stance(
            row,
            in_book=True,
            continuity={"action": "EXIT", "thesis_kill": True, "reason": "vs-cost and lost SMA50"},
        )
        self.assertEqual(result["stance"], "EXIT")
        self.assertTrue(any("not permission to overlay a short" in c for c in result["conflicts"]))

    def test_polar_hold_no_add_keeps_conflict(self):
        from operator_briefing.stance import suggest_stance

        row = {"symbol": "NASDAQ:PGY", "left": 60, "rsi": 56, "rng": 33, "bo": 0.7}
        result = suggest_stance(
            row,
            in_book=True,
            continuity={"action": "HOLD_NO_ADD", "polar_blocked": True, "prior_action": "ADD"},
        )
        self.assertEqual(result["stance"], "HOLD_NO_ADD")
        self.assertTrue(any("Polar" in c for c in result["conflicts"]))

    def test_mtp_bounce_without_live_profile_is_wait(self):
        from operator_briefing.stance import suggest_stance

        row = {
            "symbol": "NASDAQ:FOO",
            "exchange": "NASDAQ",
            "country": "United States",
            "close": 40,
            "mcap": 8_000_000_000,
            "left": 40,
            "rsi": 50,
            "rng": 30,
            "ind": "Packaged Software",
            "bo": 0.2,
            "opp": 8,
            "mtp": "ENTER_SMALL",
        }
        result = suggest_stance(row, delta={"bo": 0.0})
        self.assertEqual(result["stance"], "WAIT")
        self.assertTrue(any("bounce climate" in c for c in result["conflicts"]))

    def test_short_on_bounce_is_stand_aside(self):
        from operator_briefing.stance import suggest_stance

        row = {
            "symbol": "NYSE:WEAK",
            "exchange": "NYSE",
            "left": 11,
            "d5": -12,
            "bo": 0.1,
            "cont": 0.2,
            "vs50": -3.6,
            "rsi": 42,
            "mtp": "ENTER_SMALL",
        }
        result = suggest_stance(row)
        self.assertEqual(result["stance"], "STAND_ASIDE")

    def test_short_limited_upside_is_short_wait(self):
        from operator_briefing.stance import suggest_stance

        row = {
            "symbol": "NYSE:WEAK",
            "exchange": "NYSE",
            "left": 11,
            "d5": -12,
            "bo": 0.1,
            "cont": 0.2,
            "vs50": -3.6,
            "rsi": 42,
            "frag": 0.9,
        }
        result = suggest_stance(row)
        self.assertEqual(result["stance"], "SHORT_WAIT")
        self.assertGreaterEqual(result["suggested_conviction"], 0.54)

    def test_crowd_industry_demotes_new_to_wait(self):
        from operator_briefing.stance import suggest_stance

        row = {
            "symbol": "NYSE:OIL",
            "exchange": "NYSE",
            "country": "United States",
            "close": 80,
            "mcap": 20_000_000_000,
            "left": 40,
            "rsi": 50,
            "rng": 40,
            "ind": "Oil Refining/Marketing",
            "bo": 0.8,
        }
        result = suggest_stance(row)
        self.assertEqual(result["stance"], "WAIT")
        self.assertTrue(any("crowd" in c.lower() for c in result["conflicts"]))

    def test_book_add_into_chase_becomes_hold_no_add(self):
        from operator_briefing.stance import suggest_stance

        row = {
            "symbol": "NASDAQ:HOT",
            "left": -5,
            "rsi": 78,
            "rng": 92,
            "bo": 1.5,
        }
        result = suggest_stance(
            row,
            in_book=True,
            continuity={"action": "ADD", "prior_action": "ADD"},
        )
        self.assertEqual(result["stance"], "HOLD_NO_ADD")


class TestCompareSummary(unittest.TestCase):
    def test_new_dropped_and_gainers(self):
        from operator_briefing.compare import summarize_run_compare

        rows = [
            {"symbol": "NASDAQ:A", "ticker": "A", "bo_a": 1.0, "bo_b": 1.4, "d_bo": 0.4, "mix_a": "hold_quality", "mix_b": "add_long"},
            {"symbol": "NASDAQ:B", "ticker": "B", "bo_a": 1.2, "bo_b": 0.4, "d_bo": -0.8, "mix_a": "add_long", "mix_b": "avoid_value_trap"},
            {"symbol": "NASDAQ:C", "ticker": "C", "bo_a": None, "bo_b": 1.5, "d_bo": None, "mix_a": None, "mix_b": "add_long"},
        ]
        summary = summarize_run_compare(rows, top_n=2, list_n=10)
        self.assertEqual(summary["gainers"][0]["symbol"], "NASDAQ:A")
        self.assertEqual(summary["losers"][0]["symbol"], "NASDAQ:B")
        new_syms = {r["symbol"] for r in summary["new_in_top"]}
        dropped = {r["symbol"] for r in summary["dropped_from_top"]}
        self.assertIn("NASDAQ:C", new_syms)
        self.assertIn("NASDAQ:B", dropped)
        self.assertEqual(summary["rising_actionable"][0]["symbol"], "NASDAQ:A")
        self.assertTrue(summary["mix_flips"])


class TestLookupResolve(unittest.TestCase):
    def test_ticker_or_full_symbol(self):
        from operator_briefing.lookup import lookup_symbols, resolve_symbols

        pack = {
            "run_id": "pack_test",
            "names": {"NASDAQ:MU": {"symbol": "NASDAQ:MU", "left": 30, "bo": 1.1, "rsi": 55}},
            "book": [{"symbol": "NASDAQ:MU", "ticker": "MU", "continuity": {"action": "ADD"}}],
            "progression": {"NASDAQ:MU": {"delta_vs_prior_run": {"bo": 0.1}}},
            "stances": {},
            "primary_course": {"bias": "mixed"},
        }
        resolved, missing = resolve_symbols(["MU", "NASDAQ:SNDK"], pack)
        self.assertEqual(resolved, ["NASDAQ:MU"])
        self.assertEqual(missing, ["NASDAQ:SNDK"])
        payload = lookup_symbols(["mu"], pack=pack)
        self.assertEqual(payload["dossiers"][0]["symbol"], "NASDAQ:MU")
        self.assertEqual(payload["dossiers"][0]["stance"]["stance"], "ADD")


if __name__ == "__main__":
    unittest.main()
