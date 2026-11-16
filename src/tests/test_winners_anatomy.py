from __future__ import annotations

import unittest


class TestWinnersAnatomy(unittest.TestCase):
    def _frame(self):
        import numpy as np

        rng = np.random.default_rng(7)
        days, n = 12, 300
        x = rng.normal(size=(days * n, 2))
        # forward return driven by feature 0 only, so feat0:hi should lift the up tail and feat1 should not
        y = 2.0 * x[:, 0] + rng.normal(size=days * n)
        bounds = [(d * n, (d + 1) * n) for d in range(days)]
        return x, y, bounds

    def test_day_tails_share(self):
        from generic_utils.winners_anatomy import day_tails

        _, y, bounds = self._frame()
        t = day_tails(y, bounds, 0.05, 100, 1.0)
        self.assertAlmostEqual(t["up"].mean(), 0.05, places=2)
        self.assertAlmostEqual(t["dn"].mean(), 0.05, places=2)
        self.assertFalse((t["up"] & t["dn"]).any())

    def test_day_buckets_quintiles(self):
        from generic_utils.winners_anatomy import day_buckets

        x, _, bounds = self._frame()
        B = day_buckets(x, bounds, 5, 50)
        lo, hi = bounds[0]
        counts = [(B[lo:hi, 0] == b).sum() for b in range(1, 6)]
        self.assertEqual(sum(counts), hi - lo)
        self.assertTrue(all(c == (hi - lo) // 5 for c in counts))

    def test_single_lift_finds_the_driver(self):
        import numpy as np
        from generic_utils.winners_anatomy import day_buckets, day_counts, day_tails, leg_matrix, summarize_counts

        x, y, bounds = self._frame()
        B = day_buckets(x, bounds, 5, 50)
        L, legs = leg_matrix(B, ["f0", "f1"], 5, extremes_only=True)
        t = day_tails(y, bounds, 0.05, 100, 1.0)
        c = day_counts(L, t, bounds, [True] * len(bounds), pairs=False)
        s = summarize_counts(c, 2, float(t["up"].mean()), float(t["dn"].mean()), 10)
        lift = dict(zip(legs, s["lift_up"]))
        self.assertGreater(lift[("f0", 5)], 3.0)
        self.assertLess(lift[("f0", 1)], 0.2)
        self.assertLess(abs(lift[("f1", 5)] - 1.0), 0.5)
        t_up = dict(zip(legs, s["t_up"]))
        self.assertTrue(np.isfinite(t_up[("f0", 5)]) and t_up[("f0", 5)] > 2.0)

    def test_pair_counts_match_masks(self):
        from generic_utils.winners_anatomy import day_buckets, day_counts, day_tails, leg_matrix

        x, y, bounds = self._frame()
        B = day_buckets(x, bounds, 5, 50)
        L, _ = leg_matrix(B, ["f0", "f1"], 5, extremes_only=True)
        t = day_tails(y, bounds, 0.05, 100, 1.0)
        c = day_counts(L, t, bounds, [True] * len(bounds), pairs=True)
        expected = int((L[:, 1] & L[:, 3] & t["ok"]).sum())  # f0:hi & f1:hi
        self.assertEqual(int(c["tot"][:, 1, 3].sum()), expected)

    def test_parse_pattern(self):
        from generic_utils.winners_anatomy import parse_pattern

        legs = parse_pattern("r_1m:lo & pt_up:hi & dte<=28 & rsi:q3", ["r_1m", "pt_up", "dte", "rsi"])
        self.assertEqual(legs, [("r_1m", "lo", None), ("pt_up", "hi", None), ("dte", "<=", 28.0), ("rsi", "q3", None)])
        with self.assertRaises(ValueError):
            parse_pattern("nope:hi", ["r_1m"])

    def test_label_needs_excess(self):
        from generic_utils.winners_anatomy import label

        spec = {"strong_t": 3.0, "excess_t": 2.0}
        self.assertEqual(label("up", 3.5, 1.5, 1.6, 0.4, 0.5, 100, spec, t_need=2.0, min_names=25, t_fx=2.5), "STRONG")
        self.assertEqual(label("up", 3.5, 1.5, 1.6, 0.4, 0.5, 100, spec, t_need=2.0, min_names=25, t_fx=0.5), "REGIME")
        self.assertEqual(label("up", 3.5, 1.5, 1.6, 0.4, 0.5, 10, spec, t_need=2.0, min_names=25, t_fx=2.5), "THIN")
        self.assertEqual(label("dn", 2.5, 1.5, 1.6, -0.4, -0.5, None, spec, t_need=2.0, min_names=0, t_fx=-2.5), "STABLE")


if __name__ == "__main__":
    unittest.main()
