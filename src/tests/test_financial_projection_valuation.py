from __future__ import annotations

import json
import unittest
from pathlib import Path

RECIPE = Path(__file__).resolve().parents[2] / "config" / "financial_projection" / "value_v1.json"


def _recipe() -> dict:
    return json.loads(RECIPE.read_text(encoding="utf-8"))


def _row(**over) -> dict:
    row = {"symbol": "NYSE:TEST", "sector": "Technology Services", "industry": "Packaged Software", "close": 100.0,
           "market_cap_basic": 10e9, "total_revenue_ttm": 5e9, "revenue_forecast_next_fy": 5.5e9,
           "earnings_per_share_diluted_ttm": 5.0, "earnings_per_share_forecast_next_fy": 6.0, "net_margin_ttm": 10.0,
           "net_debt": 1e9, "enterprise_value_current": 11e9, "enterprise_value_to_revenue_ttm": 2.2,
           "free_cash_flow_ttm": 0.6e9, "net_income_ttm": 0.5e9, "beta_1_year": 1.0, "dividend_yield_recent": 1.0,
           "buyback_yield": 1.0, "price_earnings_ttm": 20.0, "total_shares_outstanding": 1e8}
    row.update(over)
    return row


MED = {"pe_fwd": 20.0, "ev_rev": 3.0, "net_margin": 0.12, "g1": 0.08, "cagr5": 0.08}


class ValuationMathTests(unittest.TestCase):
    def test_hurdle_growth_path_and_irr(self):
        from financial_projection.valuation import cost_of_equity, growth_path, internal_rate, present_value

        disc = _recipe()["discount"]
        self.assertAlmostEqual(cost_of_equity(1.0, disc), 0.0975)
        self.assertAlmostEqual(cost_of_equity(5.0, disc), 0.0475 + 2.0 * 0.05)  # beta capped
        path = growth_path(1.0, g_end=0.04, cap_after_y1=0.3, decay=0.5, horizon=5)
        self.assertEqual(path[0], 1.0)  # year 1 stays the street's
        self.assertAlmostEqual(path[1], 0.04 + (0.3 - 0.04) * 0.5)  # later years start from the cap
        divs, terminal = [2.0, 2.0, 2.0], 120.0
        price = present_value(divs, terminal, 0.08)
        self.assertAlmostEqual(internal_rate(price, divs, terminal), 0.08, places=6)


class ValuationNameTests(unittest.TestCase):
    def test_year_one_is_the_street_and_scenarios_are_ordered(self):
        from financial_projection.valuation import value_name

        wide, summaries, years = value_name(_row(), med=MED, recipe=_recipe())
        self.assertEqual(wide["eps1_source"], "street_eps")
        self.assertEqual(wide["eps1"], 6.0)
        self.assertAlmostEqual(wide["g1_pct"], 10.0)
        self.assertEqual(wide["lens"], "earnings")
        self.assertLessEqual(wide["fv_bear_px"], wide["fv_base_px"])
        self.assertLessEqual(wide["fv_base_px"], wide["fv_bull_px"])
        self.assertEqual(len(years), 15)  # 3 scenarios x 5 years
        base = next(s for s in summaries if s["scenario"] == "base")
        self.assertAlmostEqual(base["hurdle"], 0.0975)
        self.assertTrue(all(not k.startswith("_") for k in base))

    def test_priced_in_growth_reprices_today(self):
        from financial_projection.valuation import (
            _exit_price, _path, cost_of_equity, present_value, priced_in_cagr, value_inputs, year1_eps, street_growth)

        recipe = _recipe()
        inp = value_inputs(_row())
        cagr = priced_in_cagr(inp, med=MED, recipe=recipe)
        g1, _ = street_growth(inp, recipe["growth"], None)
        eps1, m1, _ = year1_eps(inp, g1)
        scen = recipe["scenarios"]["base"]
        path = _path(inp, name="base", g1=g1, eps1=eps1, m1=m1, med=MED, scen=scen, recipe=recipe, horizon=5, constant_growth=cagr)
        price_h = _exit_price(inp, path, eps1=eps1, med=MED, scen=scen, recipe=recipe)["price_h"]
        self.assertAlmostEqual(present_value(path["div"], price_h, cost_of_equity(1.0, recipe["discount"])), 100.0, places=3)

    def test_currency_mismatch_and_loss_maker_lens(self):
        from financial_projection.valuation import value_name

        wide, _, _ = value_name(_row(close=2000.0), med=MED, recipe=_recipe())  # price in another currency than EPS
        self.assertIn("currency_mismatch", wide["flags"])
        self.assertIsNone(wide.get("fv_exp_px"))
        loss = _row(earnings_per_share_diluted_ttm=-1.0, earnings_per_share_forecast_next_fy=-0.5, net_margin_ttm=-8.0,
                    net_income_ttm=-0.4e9, price_earnings_ttm=None)
        wide, _, _ = value_name(loss, med=MED, recipe=_recipe())
        self.assertEqual(wide["lens"], "sales")


class PercentPointTests(unittest.TestCase):
    def test_small_percent_point_fields_are_not_read_as_fractions(self):
        from financial_projection.valuation import _path, street_growth, value_inputs, year1_eps

        recipe = _recipe()
        # A 2% net margin, a 1.7% buyback yield and a 1.2% dividend yield: all under the old 2.5 magnitude guess.
        thin = _row(net_margin_ttm=2.0, buyback_yield=1.7, dividend_yield_recent=None, dividends_yield_current=1.2,
                    earnings_per_share_diluted_ttm=1.0, earnings_per_share_forecast_next_fy=1.1, price_earnings_ttm=100.0,
                    net_income_ttm=0.1e9)
        inp = value_inputs(thin)
        self.assertAlmostEqual(inp.net_margin, 0.02)
        self.assertAlmostEqual(inp.buyback_yield, 0.017)
        self.assertAlmostEqual(inp.div_yield, 0.012)  # dividends_yield_current, the populated field
        g1, _ = street_growth(inp, recipe["growth"], None)
        eps1, m1, _ = year1_eps(inp, g1)
        self.assertLess(m1, 0.05)  # a margin, not 200%
        scen = recipe["scenarios"]["base"]
        path = _path(inp, name="base", g1=g1, eps1=eps1, m1=m1, med=MED, scen=scen, recipe=recipe, horizon=5)
        self.assertAlmostEqual(path["drift"], -0.017 * float(scen["buyback_weight"]))  # not the -5% floor
        self.assertGreater(path["payout"], 0.0)
        self.assertGreaterEqual(path["m_target"], m1)  # pulled up toward the 12% industry margin, not cut from 200%

    def test_dividend_yield_falls_back_to_the_older_field(self):
        from financial_projection.valuation import value_inputs

        self.assertAlmostEqual(value_inputs(_row()).div_yield, 0.01)  # only dividend_yield_recent on the row


class ForwardJoinTests(unittest.TestCase):
    def test_value_run_sets_forward_pe_and_fv_columns(self):
        from generic_utils.forward_fundamentals import attach_forward_fundamentals, context_from_parts

        ctx = context_from_parts(value_names=[{"symbol": "NYSE:TEST", "close": 100.0, "eps1": 6.0, "eps_y5_base": 10.0,
                                               "fv_exp_px": 120.0, "irr_exp_pct": 14.1, "hurdle_pct": 9.75, "lens": "earnings"}])
        rec = attach_forward_fundamentals([{"symbol": "NYSE:TEST", "close": 100.0}], context=ctx)[0]
        self.assertEqual((rec["fp_pe_y1"], rec["fp_pe_y5"], rec["pe_fwd_source"]), (16.67, 10.0, "fp_street"))
        self.assertEqual((rec["fv_exp_px"], rec["irr_exp_pct"], rec["fv_lens"]), (120.0, 14.1, "earnings"))


if __name__ == "__main__":
    unittest.main()
