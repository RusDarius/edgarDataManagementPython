from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


class PathwayFormulaTests(unittest.TestCase):
    def test_percentiles_ties_and_empty(self):
        from generic_utils.pathway import percentiles

        self.assertEqual(percentiles([3, 1, None, 2, 2]), [1.0, 0.0, None, 0.5, 0.5])
        self.assertEqual(percentiles([7]), [0.5])
        self.assertEqual(percentiles([None]), [None])

    def test_exposure_after_add_and_trim(self):
        from generic_utils.pathway import exposure_after, weighted_mean

        pairs = [(9000.0, 0.9), (1000.0, 0.5)]
        self.assertAlmostEqual(weighted_mean(pairs), 0.86)
        self.assertAlmostEqual(exposure_after(pairs, 5000.0, 0.1), (8600 + 500) / 15000)
        self.assertAlmostEqual(exposure_after(pairs, -3000.0, 0.9), (8600 - 2700) / 7000)
        self.assertIsNone(exposure_after(pairs, 5000.0, None))
        self.assertIsNone(weighted_mean([]))

    def test_split_return_parts_add_up(self):
        from generic_utils.pathway import split_return

        parts = split_return(-12.0, 3.4, -11.5)
        self.assertEqual(parts, {"rel": -15.4, "ind_part": -14.9, "stock_part": -0.5})
        self.assertAlmostEqual(parts["ind_part"] + parts["stock_part"], parts["rel"])
        self.assertEqual(split_return(5.0, 1.0, None), {"rel": 4.0, "ind_part": None, "stock_part": None})


class PathwayBuildTests(unittest.TestCase):
    def test_build_on_a_small_store_and_pack(self):
        import duckdb

        from generic_utils.pathway import build_pathway, load_recipe

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store.duckdb"
            con = duckdb.connect(str(store))
            con.execute("""CREATE TABLE features (symbol VARCHAR, as_of DATE, ind VARCHAR, mcap DOUBLE, beta DOUBLE, p_dvc DOUBLE,
                           r_1d DOUBLE, r_1m DOUBLE, r_3m DOUBLE, vs200 DOUBLE, rvol_20 DOUBLE, hi_prox DOUBLE)""")
            day = date(2026, 10, 29)
            for d in range(3):
                for i in range(10):
                    ind = "Chips" if i < 5 else "Insurance"
                    r1m = -10.0 + i if ind == "Chips" else 5.0 + i
                    con.execute("INSERT INTO features VALUES (?, ?, ?, 5e9, ?, ?, 0.5, ?, ?, 1.0, 2.0, -10.0)",
                                [f"NYSE:S{i}", day - timedelta(days=2 - d), ind, float(i), float(10 - i), r1m, r1m])
            con.close()
            pack = root / "pack"
            pack.mkdir()
            book = duckdb.connect(str(pack / "briefing_pack.duckdb"))
            book.execute("CREATE TABLE book (symbol VARCHAR, value_usd DOUBLE, wt_nav DOUBLE, vs_cost DOUBLE)")
            book.executemany("INSERT INTO book VALUES (?, ?, ?, ?)", [("NYSE:S4", 9000.0, 9.0, -5.0), ("NYSE:S2", 1000.0, 1.0, 0.0), ("XETR:VUAA", 5000.0, 5.0, 40.0)])
            book.close()
            recipe = load_recipe()
            recipe.update(factors=["beta", "p_dvc"], move_factors=["beta", "p_dvc"], industry_min_n=3, regime_days=10,
                          joins=[j for j in recipe["joins"] if j["name"] == "book"], industry_join=None)
            payload = build_pathway(pack, recipe=recipe, ids=["S9"], store=store)
        exp = {r["factor"]: r for r in payload["exposure"]}
        # S4 sits at beta rank 4 of 0..9 (0.444), S2 at 2 (0.222): weighted 0.422
        self.assertAlmostEqual(exp["beta"]["book_pctl"], round((9000 * 4 / 9 + 1000 * 2 / 9) / 10000, 3))
        self.assertLess(exp["beta"]["gap"], 0)
        self.assertIsNone(exp["beta"].get("ic_month"))  # no signal lab in this pack
        lines = {r["symbol"]: r for r in payload["lines"]}
        self.assertFalse(lines["XETR:VUAA"]["in_store"])
        s4 = lines["NYSE:S4"]
        # floor r_1m: chips -10..-6, insurance 10..14 -> median 2.0; chips median -8.0; S4 = -6.0
        self.assertEqual((s4["mkt_r_1m"], s4["ind_r_1m"], s4["ind_part_r_1m"], s4["stock_part_r_1m"]), (2.0, -8.0, -10.0, 2.0))
        att = {r["horizon"]: r for r in payload["attribution"]}["r_1m"]
        self.assertEqual(att["coverage_pct"], round(100 * 10000 / 15000, 1))
        cands = {r["symbol"]: r for r in payload["candidates"]}
        self.assertNotIn("NYSE:S4", cands)
        self.assertTrue(cands["NYSE:S9"]["named"])
        self.assertGreater(cands["NYSE:S9"]["d_beta_add"], 0)   # the highest-beta name raises the Book's beta
        self.assertLess(cands["NYSE:S0"]["d_beta_add"], 0)
        self.assertEqual(len(payload["regime"]), 3)
        self.assertEqual(payload["regime"][-1]["breadth_1d"], 1.0)


if __name__ == "__main__":
    unittest.main()
