from __future__ import annotations

import unittest


class TestRecoveryClock(unittest.TestCase):
    def test_km_counts_censored_starts_while_seen(self):
        from generic_utils.recovery_clock import km_reach

        # 4 reach the target on day 10, 4 are seen 50 days without reaching it, 2 reach it on day 60
        obs = [(10.0, True, f"A{i}", 100.0) for i in range(4)]
        obs += [(50.0, False, f"B{i}", 50.0) for i in range(4)]
        obs += [(60.0, True, f"C{i}", 120.0) for i in range(2)]
        out = km_reach(obs, [28, 91], min_rows=5, min_names=5)
        self.assertAlmostEqual(out["cp"][28]["p"], 0.4, places=4)
        # after the 4 censored starts leave, 2 of the 2 still at risk reach it: odds 1.0 by day 91
        self.assertEqual(out["cp"][91]["n_obs"], 6)
        self.assertAlmostEqual(out["cp"][91]["p"], 1.0, places=4)
        self.assertEqual(out["med_days"], 60.0)

    def test_km_leaves_a_checkpoint_empty_past_the_store(self):
        from generic_utils.recovery_clock import km_reach

        obs = [(5.0, True, f"A{i}", 40.0) for i in range(10)] + [(40.0, False, f"B{i}", 40.0) for i in range(10)]
        out = km_reach(obs, [28, 91], min_rows=10, min_names=5)
        self.assertAlmostEqual(out["cp"][28]["p"], 0.5, places=4)
        self.assertIsNone(out["cp"][91]["p"])
        self.assertEqual(out["cp"][91]["n_obs"], 0)

    def test_interp_reach_and_days(self):
        from generic_utils.recovery_clock import interp_days, interp_reach

        rises = [5, 10, 20]
        vals = {5.0: 0.9, 10.0: 0.7, 20.0: 0.3}
        self.assertEqual(interp_reach(-3, rises, vals), 1.0)
        self.assertAlmostEqual(interp_reach(15, rises, vals), 0.5, places=4)
        self.assertAlmostEqual(interp_reach(2.5, rises, vals), 0.95, places=4)
        self.assertIsNone(interp_reach(25, rises, vals))
        meds = {5.0: 10.0, 10.0: 30.0, 20.0: None}
        self.assertAlmostEqual(interp_days(7.5, rises, meds), 20.0, places=1)
        self.assertIsNone(interp_days(15, rises, meds))
        self.assertEqual(interp_days(0, rises, meds), 0.0)


if __name__ == "__main__":
    unittest.main()
