from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

RECIPE = Path(__file__).resolve().parents[2] / "config" / "generic_utils" / "event_risk.json"


def _recipe() -> dict:
    return json.loads(RECIPE.read_text(encoding="utf-8"))


class EventRiskMathTests(unittest.TestCase):
    def test_compound_and_z(self):
        from generic_utils.event_risk import compound, multi_z, z_last

        self.assertAlmostEqual(compound([10.0, -10.0]), -1.0)
        self.assertIsNone(compound([]))
        series = [1.0, -1.0] * 20 + [-6.0]
        self.assertAlmostEqual(z_last(series, 40, 40), -6.0)  # sd of +-1 is 1
        self.assertLess(multi_z(series[:-1] + [-3.0, -3.0, -3.0, -3.0, -3.0], 44, 5, 30), -2.0)
        self.assertIsNone(z_last(series, 3, 40))  # not enough history

    def test_letters_and_levels_follow_the_recipe(self):
        from generic_utils.event_risk import letters_of, warn_level

        recipe = _recipe()
        order = recipe["letters"]["order"]
        self.assertEqual(letters_of({"$": True, "B": True, "P": True, "T": True}, order), "PBT$")
        self.assertEqual(letters_of({}, order), "-")
        self.assertEqual(warn_level("PBT$", recipe["levels"]), ("WARN_STRONG", "PBT$"))
        self.assertEqual(warn_level("RBTV$", recipe["levels"])[0], "WARN_STRONG")
        self.assertEqual(warn_level("PB$", recipe["levels"]), ("WARN", "PB$"))
        self.assertEqual(warn_level("T$", recipe["levels"]), ("WATCH", "T$"))
        self.assertEqual(warn_level("UT", recipe["levels"]), (None, None))
        self.assertEqual(warn_level("-", recipe["levels"]), (None, None))


class EventTargetTests(unittest.TestCase):
    THEMES = {"build": {"ids": ["NASDAQ:STRL", "FIX"]}, "solar": {"ids": ["NASDAQ:FSLR"], "industries": ["Alternative Power Generation"]}}

    def test_symbol_theme_and_crowd_share(self):
        from generic_utils.event_risk import event_targets

        ev = {"date": "2026-11-03", "kind": "policy", "exposure": {"down": ["build"], "up": ["solar"]}}
        self.assertEqual(event_targets(ev, "NASDAQ:STRL", "Engineering & Construction", [], self.THEMES, 0.4), ("down", "theme build"))
        self.assertEqual(event_targets(ev, "NYSE:FIX", None, [], self.THEMES, 0.4), ("down", "theme build"))  # bare id in the theme
        self.assertEqual(event_targets(ev, "NYSE:XYZ", "Alternative Power Generation", [], self.THEMES, 0.4)[0], "up")
        crowd = [("NASDAQ:STRL", None, 6.0), ("NYSE:FIX", None, 4.0), ("NYSE:OTHER", None, 5.0)]
        side, how = event_targets(ev, "NYSE:EME", "Engineering & Construction", crowd, self.THEMES, 0.4)
        self.assertEqual(side, "down")
        self.assertIn("crowd 67%", how)
        self.assertEqual(event_targets(ev, "NYSE:EME", None, crowd, self.THEMES, 0.8), (None, None))
        self.assertEqual(event_targets({"symbols": ["NYSE:EME"]}, "NYSE:EME", None, [], self.THEMES, 0.4), ("either", "symbol"))

    def test_binary_event_without_map_is_p_and_mapped_down_is_e(self):
        from generic_utils.event_risk import _event_fields

        vote = {"date": "2026-11-03", "kind": "policy", "event": "US midterm elections", "_market_wide": True,
                "_odds": {"value": 66, "unit": "%", "date": date(2026, 10, 29)}}
        out = _event_fields("NASDAQ:STRL", None, [], [vote], self.THEMES, 0.4)
        self.assertEqual(out["_flags"], {"E": False, "P": True})
        self.assertEqual(out["ev_odds"], "66% (2026-10-29)")
        mapped = dict(vote, exposure={"down": ["build"], "up": ["solar"]})
        out = _event_fields("NASDAQ:STRL", None, [], [mapped], self.THEMES, 0.4)
        self.assertEqual((out["_flags"], out["ev_side"]), ({"E": True, "P": False}, "down"))
        out = _event_fields("NASDAQ:FSLR", None, [], [mapped], self.THEMES, 0.4)
        self.assertEqual((out["_flags"], out["ev_side"]), ({"E": False, "P": False}, "up"))

    def test_event_steps_take_the_landing_file_and_the_next(self):
        from generic_utils.event_risk import event_steps

        ends = [date(2026, 10, 30), date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)]
        self.assertEqual(event_steps(ends, date(2026, 11, 3)), [2, 3])
        self.assertEqual(event_steps(ends, date(2026, 11, 1)), [1, 2])  # weekend -> Monday's file
        self.assertEqual(event_steps(ends, date(2026, 11, 5)), [])


class EventRiskStoreTests(unittest.TestCase):
    def test_industry_ex_self_and_point_in_time_letters(self):
        import numpy as np

        from generic_utils.event_risk import _state_arrays, industry_series

        rng = np.random.default_rng(7)
        T = 80
        base = rng.normal(0, 1.0, T)
        R = np.vstack([base * 1.5 + rng.normal(0, 0.2, T), base + rng.normal(0, 0.2, T), base + rng.normal(0, 0.2, T),
                       rng.normal(0, 1.0, T)])
        R[0, 30] = -12.0  # the high-beta name gaps
        R[1:3, -5:] = -4.0  # its industry slides over the last five steps
        R[0, -5:] = -6.0
        M = np.ones_like(R)
        data = {"R": R, "M": M, "floor": np.ones_like(R, dtype=bool), "syms": ["A:A", "A:B", "A:C", "A:D"],
                "ind": {"A:A": "x", "A:B": "x", "A:C": "x", "A:D": "y"}}
        ind = industry_series(data)
        self.assertTrue(np.allclose(ind["ex"][0], (R[1] + R[2]) / 2))  # without itself
        self.assertTrue(np.isnan(ind["ex"][3]).all())  # alone in its industry
        st = _state_arrays(R, ind["ex"], T - 1, _recipe())
        self.assertTrue(st["S"][0] and st["B"][0] and st["T"][0])
        self.assertFalse(st["S"][3])


class WarnFieldTests(unittest.TestCase):
    def test_token_is_empty_without_a_level(self):
        from generic_utils.event_risk import warn_token

        self.assertIsNone(warn_token(None))
        self.assertIsNone(warn_token({"warn_level": None, "er_letters": "T"}))
        self.assertEqual(warn_token({"warn_level": "WARN_STRONG", "er_letters": "RBTV$", "stress_5s_usd": -1616.0, "stress_5s_nav": -1.13,
                                     "ev_recent_z": -4.09}), "WARN_STRONG RBTV$ $-1616 (-1.13% NAV) evz -4.09")

    def test_lookup_reads_new_and_old_column_names_and_brief_rows_get_warn(self):
        import duckdb

        from generic_utils.event_risk import warn_lookup
        from operator_briefing.example_entry import _attach_warn

        with tempfile.TemporaryDirectory() as tmp:
            for name, col in (("new", "er_letters"), ("old", "letters")):
                folder = Path(tmp) / name / "event_risk"
                folder.mkdir(parents=True)
                con = duckdb.connect(str(folder / "event_risk.duckdb"))
                con.execute(f"CREATE TABLE names (symbol VARCHAR, warn_level VARCHAR, {col} VARCHAR, stress_5s_usd DOUBLE)")
                con.execute("INSERT INTO names VALUES ('NASDAQ:STRL', 'WARN_STRONG', 'RBTV$', -1616), ('NYSE:ALB', NULL, 'VD', -1066)")
                con.close()
                got = warn_lookup(Path(tmp) / name, ["NASDAQ:STRL"])
                self.assertEqual(list(got), ["NASDAQ:STRL"])
                self.assertEqual(got["NASDAQ:STRL"]["er_letters"], "RBTV$")
            self.assertEqual(warn_lookup(Path(tmp) / "missing"), {})
            rows = [{"symbol": "NASDAQ:STRL"}, {"symbol": "NYSE:ALB"}]
            _attach_warn(rows, Path(tmp) / "new" / "briefing_pack.json")
            self.assertEqual(rows[0]["warn"], "WARN_STRONG RBTV$ $-1616")
            self.assertNotIn("warn", rows[1])  # no level, no bytes


class WarnChangeTests(unittest.TestCase):
    def test_held_line_level_change_is_flagged(self):
        import duckdb

        from generic_utils.page_update import warn_changes

        with tempfile.TemporaryDirectory() as tmp:
            for name, level in (("old", None), ("new", "WARN_STRONG")):
                folder = Path(tmp) / name / "event_risk"
                folder.mkdir(parents=True)
                con = duckdb.connect(str(folder / "event_risk.duckdb"))
                con.execute("CREATE TABLE names (symbol VARCHAR, warn_level VARCHAR, letters VARCHAR, in_book BOOLEAN)")
                con.execute("INSERT INTO names VALUES ('NASDAQ:STRL', ?, 'RBTV$', true), ('NYSE:FIX', 'WARN', 'RBTV', false)",
                            [level])
                con.close()
            out = warn_changes(str(Path(tmp) / "old"), str(Path(tmp) / "new"))
            self.assertEqual(out["NASDAQ:STRL"], {"old": "-", "new": "WARN_STRONG", "letters": "RBTV$", "held": True})
            self.assertNotIn("NYSE:FIX", out)  # same level on both packs
            self.assertEqual(warn_changes(str(Path(tmp) / "missing"), str(Path(tmp) / "new"))["NYSE:FIX"]["old"], "-")


if __name__ == "__main__":
    unittest.main()
