from __future__ import annotations

import unittest


class TestBeatBar(unittest.TestCase):
    def _events(self):
        # 40 beats that rise 2 pp and 20 misses that fall 3 pp, half of them early
        rows = []
        for i in range(60):
            beat = i < 40
            rows.append({"symbol": f"X:{i % 7}", "print_date": f"2026-0{4 + i % 2}-15", "eps_surprise": 5.0 if beat else -4.0,
                         "surprise_mix": "B/B" if beat and i % 2 else ("B/M" if beat else "M/M"),
                         "react_fx": (2.0 if beat else -3.0) + (0.5 if i % 2 else -0.5),
                         "hold_fx_7d": 1.0 if beat else -1.0, "hold_fx_28d": 4.0 if beat else -2.0})
        return rows

    def test_outcome_split(self):
        from generic_utils.beat_bar import outcome_stats

        out = outcome_stats(self._events(), [("react_fx", "rx", ""), ("hold_fx_28d", "h28", "")], field="eps_surprise", above=0.0)
        self.assertEqual((out["n_beat"], out["n_miss"]), (40, 20))
        self.assertAlmostEqual(out["beat_rate"], 0.667, places=3)
        self.assertAlmostEqual(out["rx_beat"], 2.0, places=3)
        self.assertAlmostEqual(out["rx_miss"], -3.0, places=3)
        self.assertAlmostEqual(out["rx_premium"], 5.0, places=3)
        self.assertEqual(out["beat_sold"], 0.0)
        self.assertEqual(out["rx_beat_hit"], 1.0)
        self.assertAlmostEqual(out["h28_beat"], 4.0, places=3)

    def test_bar_is_first_positive_quintile(self):
        from generic_utils.beat_bar import bar_reads

        events = [{"eps_surprise": s, "react_fx": (-2.0 if s < 3 else 1.5)} for s in [-10, 0, 5, 12, 30] for _ in range(15)]
        out = bar_reads(events, [-3.72, 3.3, 9.7, 24.04], field="eps_surprise", measure="react_fx", min_n=12)
        self.assertEqual(out["bar_q"], 3)
        self.assertAlmostEqual(out["bar_eps"], 3.3, places=1)
        self.assertEqual(out["s1_n"], 15)

    def test_level_and_label(self):
        from generic_utils.beat_bar import cell_label, level_of

        self.assertEqual(level_of(-5, [-1.0, 4.0]), 1)
        self.assertEqual(level_of(2, [-1.0, 4.0]), 2)
        self.assertEqual(level_of(4, [-1.0, 4.0]), 3)
        self.assertIsNone(level_of(None, [-1.0, 4.0]))
        self.assertEqual(cell_label(["runup", "r_3m"], (1, 3)), "runup:T1 & r_3m:T3")

    def test_pick_cell_falls_back_when_thin(self):
        from generic_utils.beat_bar import _pick_cell

        grid = {"axes": ["a", "b"], "fallbacks": [["a"]], "min_beats": 30, "min_misses": 12}
        cells = {("a+b", "a:T1 & b:T1"): {"cell": "a:T1 & b:T1", "n_beat": 10, "n_miss": 3},
                 ("a", "a:T1"): {"cell": "a:T1", "n_beat": 90, "n_miss": 30}}
        cell, depth = _pick_cell({"a": -5, "b": -5}, cells, {"a": [0.0, 1.0], "b": [0.0, 1.0]}, grid)
        self.assertEqual((cell["cell"], depth), ("a:T1", 1))


if __name__ == "__main__":
    unittest.main()
