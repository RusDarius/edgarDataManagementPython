import unittest

from operator_briefing.continuity import (
    continuity_action,
    is_polar_flip,
    map_mix_to_naive_book_action,
)
from operator_briefing.sleeves import (
    attach_punished_classes,
    classify_punished_tape,
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

    def test_continuation_unpaid_keeps_live_leftover(self):
        from operator_briefing.sleeves import continuation_unpaid

        row = {
            "symbol": "NASDAQ:SNDK",
            "exchange": "NASDAQ",
            "close": 1500,
            "mcap": 200_000_000_000,
            "left": 64,
            "rsi": 52,
            "ind": "Computer Peripherals",
            "bo": 1.05,
            "cont": 1.19,
        }
        self.assertTrue(continuation_unpaid(row))

    def test_paid_refiner_is_not_continuation_unpaid(self):
        from operator_briefing.sleeves import continuation_unpaid

        row = {
            "symbol": "NYSE:MPC",
            "left": -12,
            "rsi": 78,
            "rng": 100,
            "bo": 1.56,
            "mcap": 50_000_000_000,
            "close": 180,
        }
        self.assertFalse(continuation_unpaid(row))

    def test_unpaid_sort_prefers_live_edge_over_leftover_dump(self):
        sndk = {
            "symbol": "NASDAQ:SNDK",
            "bo": 1.05,
            "cont": 1.19,
            "fwd": 1.23,
            "opp": 8,
            "left": 64,
        }
        junk = {
            "symbol": "NASDAQ:ZIONP",
            "bo": 0.0,
            "cont": 0.0,
            "fwd": 0.0,
            "opp": None,
            "left": 180,
        }
        ranked = sorted([junk, sndk], key=unpaid_sort_key)
        self.assertEqual(ranked[0]["symbol"], "NASDAQ:SNDK")

    def test_rejects_street_pt_leftover_dump(self):
        row = {
            "symbol": "NYSE:MBGL",
            "exchange": "NYSE",
            "close": 20,
            "mcap": 8_000_000_000,
            "left": 159,
            "rsi": 51,
            "ind": "Miscellaneous",
            "bo": 0.67,
            "opp": 35,
        }
        self.assertFalse(unpaid_eligible(row))

    def test_forming_rejects_microcap_range_junk(self):
        from operator_briefing.sleeves import forming_eligible

        junk = {
            "symbol": "NASDAQ:BGL",
            "exchange": "NASDAQ",
            "close": 2,
            "mcap": 80_000_000,
            "left": 5002,
            "rng": 0.1,
            "rsi": 42,
        }
        self.assertFalse(forming_eligible(junk))
        keep = {
            "symbol": "NASDAQ:TTD",
            "exchange": "NASDAQ",
            "close": 14,
            "mcap": 8_000_000_000,
            "left": 60,
            "rng": 4,
            "rsi": 46,
            "bo": 0.63,
        }
        self.assertTrue(forming_eligible(keep))

    def test_reward_risk_prefers_unused_range(self):
        from operator_briefing.sleeves import reward_risk

        unused = {"left": 60, "rng": 4}
        paid_range = {"left": 60, "rng": 80}
        dump = {"left": 159, "rng": 32}
        self.assertGreater(reward_risk(unused), reward_risk(paid_range))
        self.assertIsNone(reward_risk(dump))
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
        self.assertFalse(
            short_limited_upside({**row, "left": 11, "d5": -12, "cont": 0.1})
        )

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
        row = {
            "symbol": "NASDAQ:JD",
            "exchange": "NASDAQ",
            "close": 28,
            "mcap": 40_000_000_000,
            "left": 91,
            "d5": -2,
            "bo": 0,
            "cont": 0,
        }
        self.assertFalse(short_limited_upside(row))

    def test_short_rejects_microcap(self):
        row = {
            "symbol": "NASDAQ:ADSE",
            "exchange": "NASDAQ",
            "close": 12,
            "mcap": 400_000_000,
            "left": 5,
            "d5": -12,
            "bo": 0.1,
            "cont": 0.1,
            "vs50": -5,
        }
        self.assertFalse(short_limited_upside(row))

    def test_short_rejects_broken_pt_leftover(self):
        row = {
            "symbol": "NASDAQ:CHRN",
            "exchange": "NASDAQ",
            "close": 19,
            "mcap": 8_000_000_000,
            "left": -57,
            "d5": -12,
            "bo": 0.1,
            "cont": 0.1,
        }
        self.assertFalse(short_limited_upside(row))

    def test_short_accepts_failed_leader_limited_leftover(self):
        row = {
            "symbol": "NYSE:WEAK",
            "exchange": "NYSE",
            "close": 20,
            "mcap": 8_000_000_000,
            "left": 11,
            "d5": -12,
            "bo": 0.1,
            "cont": 0.2,
            "vs50": -3.6,
        }
        self.assertTrue(short_limited_upside(row))


class TestPunishedTape(unittest.TestCase):
    def test_continue_down_from_limited_upside(self):
        row = {
            "symbol": "NYSE:WEAK",
            "exchange": "NYSE",
            "close": 20,
            "mcap": 8_000_000_000,
            "left": 11,
            "d5": -12,
            "bo": 0.1,
            "cont": 0.2,
            "vs50": -3.6,
            "tail": "laggards",
        }
        self.assertEqual(classify_punished_tape(row), "CONTINUE_DOWN")

    def test_bounce_from_unused_range(self):
        row = {
            "symbol": "NASDAQ:CHEAP",
            "exchange": "NASDAQ",
            "close": 22,
            "mcap": 8_000_000_000,
            "left": 28,
            "rsi": 38,
            "rng": 18,
            "bo": 0.5,
            "cont": 0.4,
        }
        self.assertEqual(classify_punished_tape(row), "BOUNCE")

    def test_continue_down_from_avoid_mix_with_leftover(self):
        row = {
            "symbol": "NASDAQ:TRAP",
            "exchange": "NASDAQ",
            "close": 40,
            "mcap": 20_000_000_000,
            "left": 55,
            "mix": "avoid_value_trap",
            "rsi": 42,
            "rng": 20,
        }
        self.assertEqual(classify_punished_tape(row), "CONTINUE_DOWN")

    def test_mixed_when_neither_case_is_clean(self):
        row = {
            "symbol": "NASDAQ:MEH",
            "exchange": "NASDAQ",
            "close": 40,
            "mcap": 20_000_000_000,
            "left": 10,
            "rsi": 58,
            "rng": 55,
            "bo": 0.4,
            "cont": 0.4,
        }
        self.assertEqual(classify_punished_tape(row), "MIXED")

    def test_attach_only_stamps_laggards(self):
        rows = attach_punished_classes(
            [
                {"symbol": "NASDAQ:UP", "tail": "leaders", "left": 40, "rsi": 40, "rng": 20},
                {
                    "symbol": "NYSE:WEAK",
                    "exchange": "NYSE",
                    "close": 20,
                    "mcap": 8_000_000_000,
                    "left": 11,
                    "d5": -12,
                    "bo": 0.1,
                    "cont": 0.2,
                    "vs50": -3.6,
                    "tail": "laggards",
                },
            ]
        )
        self.assertIsNone(rows[0]["down_class"])
        self.assertEqual(rows[1]["down_class"], "CONTINUE_DOWN")


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

    def test_prior_derisk_sticks_while_print_is_near(self):
        result = continuity_action(
            prior={"action": "DERISK_INTO_PRINT"},
            naive="HOLD",
            close=17.9,
            vs_cost_pct=17.5,
            weeks_ras=0.5,
            months_ras=0.4,
            lost_sma50=False,
            leftover=-20,
            rsi=68,
            rng=81,
            dte=1,
        )
        self.assertEqual(result["action"], "DERISK_INTO_PRINT")

    def test_prior_trim_sticks_until_tape_repairs(self):
        result = continuity_action(
            prior={"action": "TRIM", "invalidation": 90},
            naive="HOLD",
            close=94.4,
            vs_cost_pct=-9.2,
            weeks_ras=0.05,
            months_ras=0.07,
            lost_sma50=True,
            leftover=75,
            rsi=37,
            rng=40,
        )
        self.assertEqual(result["action"], "TRIM")

    def test_prior_hold_no_add_does_not_silent_hold(self):
        result = continuity_action(
            prior={"action": "HOLD_NO_ADD"},
            naive="HOLD",
            close=282,
            vs_cost_pct=0.1,
            weeks_ras=0.4,
            months_ras=0.4,
            lost_sma50=False,
            leftover=9,
            rsi=60,
            rng=50,
            dte=8,
        )
        self.assertEqual(result["action"], "HOLD_NO_ADD")


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
            continuity={
                "action": "EXIT",
                "thesis_kill": True,
                "reason": "vs-cost and lost SMA50",
            },
        )
        self.assertEqual(result["stance"], "EXIT")
        self.assertTrue(
            any("not permission to overlay a short" in c for c in result["conflicts"])
        )

    def test_polar_hold_no_add_keeps_conflict(self):
        from operator_briefing.stance import suggest_stance

        row = {"symbol": "NASDAQ:PGY", "left": 60, "rsi": 56, "rng": 33, "bo": 0.7}
        result = suggest_stance(
            row,
            in_book=True,
            continuity={
                "action": "HOLD_NO_ADD",
                "polar_blocked": True,
                "prior_action": "ADD",
            },
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
            "close": 20,
            "mcap": 8_000_000_000,
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
            "close": 20,
            "mcap": 8_000_000_000,
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
            {
                "symbol": "NASDAQ:A",
                "ticker": "A",
                "bo_a": 1.0,
                "bo_b": 1.4,
                "d_bo": 0.4,
                "mix_a": "hold_quality",
                "mix_b": "add_long",
            },
            {
                "symbol": "NASDAQ:B",
                "ticker": "B",
                "bo_a": 1.2,
                "bo_b": 0.4,
                "d_bo": -0.8,
                "mix_a": "add_long",
                "mix_b": "avoid_value_trap",
            },
            {
                "symbol": "NASDAQ:C",
                "ticker": "C",
                "bo_a": None,
                "bo_b": 1.5,
                "d_bo": None,
                "mix_a": None,
                "mix_b": "add_long",
            },
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

    def test_session_prior_skips_same_day(self):
        from operator_briefing.compare import pick_session_prior, run_day_from_id

        current = "move_prediction_20260909_1748_utc_23e08bc0"
        priors = (
            "move_prediction_20260909_1516_utc_b92f0736",
            "move_prediction_20260908_1743_utc_b43dd416",
        )
        self.assertEqual(run_day_from_id(current), "20260909")
        self.assertEqual(
            pick_session_prior(priors, current),
            "move_prediction_20260908_1743_utc_b43dd416",
        )

    def test_filter_compare_universe_us_core(self):
        from operator_briefing.compare import filter_compare_universe

        rows = [
            {"symbol": "NASDAQ:MU", "ticker": "MU", "d_bo": 0.1},
            {"symbol": "OTC:MCEM", "ticker": "MCEM", "d_bo": 0.9},
            {"symbol": "LSE:ITM", "ticker": "ITM", "d_bo": 0.5},
        ]
        us = filter_compare_universe(rows, exchanges=("NASDAQ", "NYSE", "AMEX"))
        self.assertEqual([r["symbol"] for r in us], ["NASDAQ:MU"])
        named = filter_compare_universe(rows, ids=("MU", "ITM"))
        self.assertEqual({r["ticker"] for r in named}, {"MU", "ITM"})


class TestLookupResolve(unittest.TestCase):
    def test_ticker_or_full_symbol(self):
        from operator_briefing.lookup import lookup_symbols, resolve_symbols

        pack = {
            "run_id": "pack_test",
            "names": {
                "NASDAQ:MU": {"symbol": "NASDAQ:MU", "left": 30, "bo": 1.1, "rsi": 55}
            },
            "book": [
                {"symbol": "NASDAQ:MU", "ticker": "MU", "continuity": {"action": "ADD"}}
            ],
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


class TestEventPlay(unittest.TestCase):
    def test_late_sept_unpaid_is_buy_the_rumour(self):
        from operator_briefing.stance import classify_event_play

        row = {
            "symbol": "NASDAQ:FOO",
            "exchange": "NASDAQ",
            "close": 40,
            "mcap": 8_000_000_000,
            "left": 40,
            "rsi": 50,
            "rng": 40,
            "ind": "Packaged Software",
            "bo": 0.8,
            "dte": 28,
        }
        result = classify_event_play(row)
        self.assertEqual(result["play"], "BUY_THE_RUMOUR")

    def test_print_week_paid_book_is_derisk(self):
        from operator_briefing.stance import classify_event_play

        row = {
            "symbol": "NASDAQ:ZS",
            "left": -20,
            "rsi": 68,
            "rng": 90,
            "bo": 1.5,
            "dte": 1,
        }
        result = classify_event_play(row, in_book=True)
        self.assertEqual(result["play"], "DERISK_INTO_PRINT")

    def test_limited_leftover_near_print_is_short_pre(self):
        from operator_briefing.stance import classify_event_play

        row = {
            "symbol": "NYSE:WEAK",
            "exchange": "NYSE",
            "close": 20,
            "mcap": 8_000_000_000,
            "left": 11,
            "d5": -12,
            "bo": 0.1,
            "cont": 0.2,
            "vs50": -3.6,
            "rsi": 42,
            "dte": 6,
        }
        result = classify_event_play(row)
        self.assertEqual(result["play"], "SHORT_PRE")


class TestParseDate(unittest.TestCase):
    def test_unix_seconds_and_iso(self):
        from datetime import date

        from operator_briefing.extract import _parse_date

        path_print = _parse_date("1788466200")
        self.assertEqual(path_print, date(2026, 9, 3))
        mu_print = _parse_date(1790769600)
        self.assertIsNotNone(mu_print)
        self.assertGreater(mu_print, path_print)
        self.assertEqual(_parse_date("2026-09-15"), date(2026, 9, 15))
        self.assertEqual(_parse_date("03/09/2026"), date(2026, 9, 3))
        ms = _parse_date(1788466200000)
        self.assertEqual(ms, date(2026, 9, 3))


class TestDedupeByIndustry(unittest.TestCase):
    def _crowded_rows(self, n=8, industry="Packaged Software"):
        return [
            {"symbol": f"NASDAQ:SW{i}", "ind": industry, "left": 50 - i}
            for i in range(n)
        ]

    def test_caps_one_industry(self):
        from operator_briefing.sleeves import dedupe_by_industry

        rows = self._crowded_rows(8) + [
            {"symbol": "NYSE:MU", "ind": "Semiconductors", "left": 65}
        ]
        kept, overflow = dedupe_by_industry(rows, cap=5)
        kept_industries = [r["ind"] for r in kept]
        self.assertEqual(kept_industries.count("Packaged Software"), 5)
        self.assertEqual(kept_industries.count("Semiconductors"), 1)
        self.assertEqual(len(overflow), 3)
        self.assertTrue(all(o["industry"] == "Packaged Software" for o in overflow))

    def test_book_symbols_exempt_from_cap(self):
        from operator_briefing.sleeves import dedupe_by_industry

        rows = self._crowded_rows(7)
        kept, overflow = dedupe_by_industry(rows, cap=5, book_symbols={"NASDAQ:SW6"})
        kept_symbols = {r["symbol"] for r in kept}
        self.assertIn("NASDAQ:SW6", kept_symbols)
        self.assertEqual(len(overflow), 1)
        self.assertEqual(overflow[0]["symbol"], "NASDAQ:SW5")

    def test_preserves_input_order(self):
        from operator_briefing.sleeves import dedupe_by_industry

        rows = [
            {"symbol": "NASDAQ:A", "ind": "X", "left": 40},
            {"symbol": "NASDAQ:B", "ind": "Y", "left": 30},
            {"symbol": "NASDAQ:C", "ind": "X", "left": 20},
        ]
        kept, overflow = dedupe_by_industry(rows, cap=5)
        self.assertEqual(
            [r["symbol"] for r in kept], ["NASDAQ:A", "NASDAQ:B", "NASDAQ:C"]
        )
        self.assertEqual(overflow, [])


class TestCurationInCompile(unittest.TestCase):
    def _synthetic_names(self):
        names = {}
        for i in range(10):
            symbol = f"NASDAQ:SW{i}"
            names[symbol] = {
                "symbol": symbol,
                "exchange": "NASDAQ",
                "country": "United States",
                "close": 40.0,
                "mcap": 8_000_000_000.0,
                "left": 50.0 - i,
                "rsi": 55.0,
                "rng": 40.0,
                "ind": "Packaged Software",
                "bo": 1.0,
                "cont": 0.6,
                "fwd": 0.5,
                "opp": 10 + i,
            }
        names["NYSE:MU"] = {
            "symbol": "NYSE:MU",
            "exchange": "NYSE",
            "country": "United States",
            "close": 150.0,
            "mcap": 200_000_000_000.0,
            "left": 65.0,
            "rsi": 53.0,
            "rng": 73.0,
            "ind": "Semiconductors",
            "bo": 1.05,
            "cont": 1.23,
            "fwd": 1.35,
            "opp": 3,
        }
        return names

    def test_radar_curated_50_caps_industry_concentration(self):
        from operator_briefing.compile import _sleeve_lists
        from operator_briefing.sleeves import RADAR_INDUSTRY_CAP

        result = _sleeve_lists(self._synthetic_names(), book_symbols=set())
        self.assertIn("NASDAQ:SW0", {r["symbol"] for r in result["radar_upside_100"]})
        curated_industries = [r["ind"] for r in result["radar_curated_50"]]
        self.assertLessEqual(
            curated_industries.count("Packaged Software"), RADAR_INDUSTRY_CAP
        )
        self.assertIn("Semiconductors", curated_industries)
        overflow_symbols = {o["symbol"] for o in result["radar_industry_overflow"]}
        self.assertTrue(overflow_symbols)
        self.assertTrue(overflow_symbols.issubset({f"NASDAQ:SW{i}" for i in range(10)}))
        self.assertEqual(
            [r["symbol"] for r in result["radar_curated_25"]],
            [r["symbol"] for r in result["radar_curated_50"][:25]],
        )
        self.assertLessEqual(len(result["radar_curated_50"]), 50)

    def test_book_symbol_bypasses_radar_cap(self):
        from operator_briefing.compile import _sleeve_lists

        names = self._synthetic_names()
        result = _sleeve_lists(names, book_symbols={"NASDAQ:SW9"})
        curated_symbols = {r["symbol"] for r in result["radar_curated_50"]}
        self.assertIn("NASDAQ:SW9", curated_symbols)


class TestAttachSleeveTags(unittest.TestCase):
    def test_tags_and_count_joined_onto_rows(self):
        from operator_briefing.compile import _attach_sleeve_tags

        rows = [{"symbol": "NASDAQ:SNDK"}, {"symbol": "NASDAQ:NONE"}]
        stances = {"NASDAQ:SNDK": {"sleeves": ["unpaid", "continuation_unpaid"]}}
        _attach_sleeve_tags(rows, stances)
        self.assertEqual(rows[0]["sleeve_tags"], ["unpaid", "continuation_unpaid"])
        self.assertEqual(rows[0]["sleeve_count"], 2)
        self.assertEqual(rows[1]["sleeve_tags"], [])
        self.assertEqual(rows[1]["sleeve_count"], 0)


if __name__ == "__main__":
    unittest.main()
