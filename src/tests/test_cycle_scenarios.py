from __future__ import annotations

import unittest

RECIPE = {"horizon_years": 5, "boom_decay": 0.5, "boom_floor": 0.0, "boom_cap": 0.3, "bust_years": 1, "recover_years": 2,
          "norm_share_of_drop": 0.5, "cash_conversion": 0.8, "drop_solve_max": 1.5}


class TestCycleScenarios(unittest.TestCase):
    def test_eps_path_boom_bust_recovery(self):
        from generic_utils.cycle_scenarios import eps_path

        p = eps_path(100.0, 1.0, k=1, d=0.5, recipe=RECIPE)
        # year 2 grows at min(1.0 x 0.5, 0.3) = 30%; year 3 the trough; years 4-5 climb to the normalized level
        self.assertEqual(p["peak_year"], 2)
        self.assertAlmostEqual(p["eps"][1], 130.0)
        self.assertAlmostEqual(p["eps"][2], 65.0)
        self.assertAlmostEqual(p["norm"], 97.5)
        self.assertAlmostEqual(p["eps"][3], 81.25)
        self.assertAlmostEqual(p["eps"][4], 97.5)

    def test_priced_in_drop_returns_the_value_equal_to_close(self):
        from generic_utils.cycle_scenarios import priced_in_drop, scenario_value

        close = 1000.0
        d, state = priced_in_drop(close, 150.0, 0.5, 0.12, k=0, pe=12.0, recipe=RECIPE)
        self.assertEqual(state, "solved")
        fv = scenario_value(close, 150.0, 0.5, 0.12, k=0, d=d, pe=12.0, recipe=RECIPE)["fair_value"]
        self.assertAlmostEqual(fv, close, delta=0.01)
        _, state_hi = priced_in_drop(5000.0, 10.0, 0.1, 0.12, k=0, pe=12.0, recipe=RECIPE)
        self.assertEqual(state_hi, "needs_more")

    def test_pe_need_reprices_to_close(self):
        from generic_utils.cycle_scenarios import scenario_value

        s = scenario_value(400.0, 20.0, 0.2, 0.13, k=2, d=0.25, pe=15.0, recipe=RECIPE)
        again = scenario_value(400.0, 20.0, 0.2, 0.13, k=2, d=0.25, pe=s["pe_need"], recipe=RECIPE)
        self.assertAlmostEqual(again["fair_value"], 400.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()
