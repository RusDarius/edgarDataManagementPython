from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


def _recipe() -> dict:
    from generic_utils.name_dossier import load_recipe

    return load_recipe()


class RuleTests(unittest.TestCase):
    def test_sign_rules_and_counts(self):
        from generic_utils.name_dossier import _cond, evidence

        self.assertTrue(_cond(1.5, ">0"))
        self.assertFalse(_cond(None, ">0"))
        self.assertTrue(_cond(-11, "<-10"))
        self.assertTrue(_cond("WARN", "in WARN_STRONG|WARN"))
        self.assertFalse(_cond("WATCH", "in WARN_STRONG|WARN"))
        recipe = {"evidence": {"rules": [
            {"horizon": "mid", "item": "a", "field": "x", "up": ">0", "down": "<0"},
            {"horizon": "mid", "item": "b", "field": "y", "up": ">0", "down": "<0"},
            {"horizon": "long", "item": "c", "field": "z", "up": ">0", "down": "<0"},
            {"horizon": "short", "item": "d", "field": "w", "down": "in WARN"}]}}
        rows, counts = evidence({"symbol": "NYSE:DOW", "x": 2.0, "y": -1.0, "w": "WARN"}, recipe)
        self.assertEqual(counts, {"mid_up": 1, "mid_dn": 1, "short_dn": 1})
        self.assertEqual([r["direction"] for r in rows], ["up", "down", "n/a", "down"])

    def test_repo_recipe_rules_parse(self):
        from generic_utils.name_dossier import _cond

        for rule in _recipe()["evidence"]["rules"]:
            for side in ("up", "down"):
                if rule.get(side):
                    _cond(1.0, rule[side])  # raises on a malformed rule


class MovementTests(unittest.TestCase):
    def test_split_step_is_not_a_crash_and_anatomy_dates(self):
        from generic_utils.name_dossier import anatomy, step_returns

        rows = [{"as_of": f"2026-04-0{i + 1}", "close": c, "split_seq": s, "sma50": 9.0, "sma200": 11.0, "relvol": rv}
                for i, (c, s, rv) in enumerate([(10, 0, 1), (12, 0, 3), (6, 1, 1), (4.5, 1, 1), (6.3, 1, 2.5)])]
        rets = step_returns(rows)
        self.assertIsNone(rets[2])  # 12 -> 6 is a 2-for-1 split
        row, moves = anatomy(rows, [{"react_file": "2026-04-05"}], {"moves": {"top_n": 3, "spike_relvol": 2.0}})
        self.assertEqual((row["max_dd_peak"], row["max_dd_trough"]), ("2026-04-02", "2026-04-04"))
        self.assertAlmostEqual(row["max_dd_pct"], -25.0, places=2)
        self.assertEqual((row["spikes_up"], row["spikes_dn"]), (2, 0))
        self.assertTrue(any(m["print_day"] and m["as_of"] == "2026-04-05" for m in moves))

    def test_levels_distances(self):
        from generic_utils.name_dossier import levels

        lv = levels({"symbol": "NYSE:DOW", "close": 31.65, "kill_px": 30.05, "street_px": 35.5, "first_target": None})
        self.assertAlmostEqual(lv["to_kill_pct"], -5.06, places=2)
        self.assertAlmostEqual(lv["to_street_pct"], 12.16, places=2)
        self.assertIsNone(lv["to_target_pct"])
        self.assertEqual((lv["kill_live"], lv["kill_src"]), (30.05, "kill_px"))

    def test_broken_kill_falls_back_to_the_bounce_band(self):
        from generic_utils.name_dossier import levels

        # NASDAQ:GOOG on 6 Nov 2026: the entry book (329.79-335.09, kill 311.34) sat above the 284.51 close.
        lv = levels({"symbol": "NASDAQ:GOOG", "close": 284.51, "entry_low": 329.79, "entry_high": 335.09, "kill_px": 311.34,
                     "bounce_low": 273.18, "bounce_high": 279.21, "bounce_kill": 265.22})
        self.assertGreater(lv["to_kill_pct"], 0)  # the stale kill is above the close
        self.assertEqual((lv["band_live_low"], lv["band_live_high"], lv["kill_live"]), (273.18, 279.21, 265.22))
        self.assertTrue(lv["kill_src"].startswith("bounce_kill"))
        self.assertAlmostEqual(lv["to_kill_live_pct"], -6.78, places=2)
        none = levels({"symbol": "NASDAQ:AVGO", "close": 292.03, "entry_low": 347.86, "kill_px": 321.51})
        self.assertIsNone(none["kill_live"])
        self.assertEqual(none["band_src"], "none")


class AnalogTests(unittest.TestCase):
    def test_nearest_other_names_and_nulls_are_nan(self):
        import numpy as np

        from generic_utils.name_dossier import _num, analogs_for

        masked = np.ma.masked_array([1.0, 2.0, 3.0], mask=[False, True, False])
        self.assertTrue(np.isnan(_num(masked)[1]))  # a NULL label must not become 0
        n = 40
        Q = np.vstack([np.full(3, 0.9)] + [np.full(3, 0.9) + 0.001 * i for i in range(1, 21)] + [np.full(3, 0.1)] * (n - 21))
        sym = np.array(["X:TGT"] + [f"X:N{i}" for i in range(1, n)], dtype=object)
        fwd = np.array([np.nan] + [5.0] * 20 + [-5.0] * (n - 21))
        frame = {"feats": ["a", "b", "c"], "horizons": [["7d", "fwd_7d"]], "Q": Q, "sym": sym,
                 "as_of": np.array(["2026-11-06"] + [f"2026-10-{(i % 9) + 10}" for i in range(1, n)], dtype=object),
                 "ind": np.array(["chem"] * n, dtype=object), "fwd": {"fwd_7d": fwd}, "xs": {"fwd_7d": fwd - 1.0}}
        recipe = {"analogs": {"k": 20, "k_ind": 20, "quantiles": [0.1, 0.5, 0.9]}}
        rows, flat = analogs_for(frame, "X:TGT", recipe)
        self.assertEqual(flat["an_7d_q50"], 5.0)  # the 20 nearest are the +5% rows, the target itself is excluded
        self.assertEqual(flat["an_7d_xs_q50"], 4.0)
        self.assertEqual({r["scope"] for r in rows}, {"all", "industry"})


class PageTests(unittest.TestCase):
    def test_skeleton_and_page_spec_follow_the_judgment(self):
        import duckdb

        from generic_utils.name_dossier import build_page_spec, judgment_skeleton

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "spec.json").write_text(json.dumps({"label": "t", "ids": ["NYSE:DOW", "NYSE:LYB"], "question": "Q?", "pack": "p",
                                                     "store": "s.duckdb", "store_span": ["2026-03-29", "2026-11-06"], "created_utc": "x"}),
                                         encoding="utf-8")
            (d / "levels.csv").write_text("symbol,band_live_low,band_live_high,kill_live,entry_low,entry_high,kill_px,first_target,fv_base_px\n"
                                          "NYSE:DOW,30.09,31.65,30.05,30.09,31.65,30.05,,33.07\n", encoding="utf-8")
            duckdb.connect(str(d / "dossier.duckdb")).close()
            sk = judgment_skeleton(d)
            self.assertEqual(sk["names"]["NYSE:DOW"]["levels"]["kill"], 30.05)
            self.assertEqual(sk["order"]["ids"], ["NYSE:DOW", "NYSE:LYB"])
            sk["order"] = {"by": "up to target_mid over down to the kill", "ids": ["NYSE:LYB", "NYSE:DOW"]}
            sk["names"]["NYSE:DOW"].update({"verdict": "WAIT", "thesis": "t"})
            spec = build_page_spec(d, sk)
            self.assertEqual([t["label"] for t in spec["tabs"]], ["Verdict", "LYB", "DOW", "Research"])
            suite = next(b for b in spec["tabs"][0]["blocks"] if b.get("title") == "Suite (judgment order)")
            self.assertEqual(suite["ids"], ["NYSE:LYB", "NYSE:DOW"])
            self.assertEqual(suite["extra"]["NYSE:DOW"]["Verdict"], "WAIT")


if __name__ == "__main__":
    unittest.main()
