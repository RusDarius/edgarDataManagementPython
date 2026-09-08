import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class TestDerive(unittest.TestCase):
    def test_leftover_is_max_of_street_and_edge(self):
        from generic_utils.derive import leftover_pct

        self.assertAlmostEqual(leftover_pct(100, 160, 40), 60)
        self.assertAlmostEqual(leftover_pct(100, 110, 40), 40)
        self.assertIsNone(leftover_pct(None, 110, None))

    def test_leftover_to_range_has_no_default_dump_cutoff(self):
        from generic_utils.derive import leftover_to_range

        self.assertEqual(leftover_to_range(120, 20), 6.0)
        self.assertIsNone(leftover_to_range(120, 20, leftover_max=90))


class TestAggregations(unittest.TestCase):
    def test_numeric_summary(self):
        from generic_utils.aggregations import numeric_summary

        rows = [{"d5": 10}, {"d5": -5}, {"d5": None}]
        result = numeric_summary(rows, ["d5"])
        self.assertEqual(result["d5"]["n"], 2)
        self.assertEqual(result["d5"]["missing"], 1)
        self.assertEqual(result["d5"]["pct_positive"], 50.0)

    def test_group_stats_min_n_and_pct_positive(self):
        from generic_utils.aggregations import group_stats

        rows = (
            [{"ind": "A", "d5": 2, "day": 1}] * 3
            + [{"ind": "B", "d5": -4, "day": -1}] * 2
            + [{"ind": "C", "d5": 9, "day": 1}]
        )
        result = group_stats(
            rows, group_field="ind", metrics=["d5"], min_n=2, pct_positive_field="day"
        )
        names = [r["ind"] for r in result]
        self.assertEqual(names, ["A", "B"])
        self.assertEqual(result[0]["pct_positive"], 100.0)
        self.assertEqual(result[1]["n"], 2)

    def test_group_stats_pct_positive_denom_n(self):
        from generic_utils.aggregations import group_stats

        rows = [
            {"ind": "A", "day": 1},
            {"ind": "A", "day": None},
            {"ind": "A", "day": -1},
        ]
        clean = group_stats(
            rows, group_field="ind", metrics=["day"], pct_positive_field="day"
        )
        of_n = group_stats(
            rows,
            group_field="ind",
            metrics=["day"],
            pct_positive_field="day",
            pct_positive_denom="n",
        )
        self.assertEqual(clean[0]["pct_positive"], 50.0)
        self.assertEqual(of_n[0]["pct_positive"], 33.3)
        from generic_utils.aggregations import histogram

        rows = [{"rsi": v} for v in (10, 30, 50, 70, 90)]
        buckets = histogram(rows, field="rsi", bins=[0, 30, 70, 100])
        by_label = {b["label"]: b["n"] for b in buckets}
        self.assertEqual(by_label["0-30"], 1)
        self.assertEqual(by_label["30-70"], 2)
        self.assertEqual(by_label["70-100"], 2)

    def test_overlap(self):
        from generic_utils.aggregations import overlap

        result = overlap(["A", "B", "C"], ["B", "C", "D"])
        self.assertEqual(result["n_both"], 2)
        self.assertEqual(result["both"], ["B", "C"])


class TestPredicates(unittest.TestCase):
    def test_where_filters_without_baked_cutoffs(self):
        from generic_utils.predicates import filter_rows

        rows = [
            {"symbol": "A", "left": 80, "rsi": 40, "ind": "Software"},
            {"symbol": "B", "left": 10, "rsi": 40, "ind": "Software"},
            {"symbol": "C", "left": 80, "rsi": 80, "ind": "Software"},
            {"symbol": "D", "left": 80, "rsi": 40, "ind": "Biotechnology"},
        ]
        kept, clauses = filter_rows(rows, "left>=25,rsi<=68,ind!=Biotechnology")
        self.assertEqual([r["symbol"] for r in kept], ["A"])
        self.assertEqual(len(clauses), 3)

    def test_missing_numeric_fails_clause(self):
        from generic_utils.predicates import filter_rows

        kept, _ = filter_rows([{"left": None}], "left>=25")
        self.assertEqual(kept, [])


class TestFocus(unittest.TestCase):
    def test_build_focus_echoes_spec(self):
        from generic_utils.focus import build_focus

        rows = [{"symbol": f"S{i}", "ind": "X", "left": 100 - i} for i in range(8)]
        payload = build_focus(
            rows, where="left>=95", field="left", n=10, group_field="ind", cap=2
        )
        self.assertEqual(payload["spec"]["filtered_rows"], 6)
        self.assertEqual(len(payload["rows"]), 2)
        self.assertEqual(payload["spec"]["overflow_n"], 4)

    def test_pack_path_rows(self):
        from generic_utils.focus import pack_path_rows

        pack = {"sleeves": {"radar_curated_25": [{"symbol": "NASDAQ:MU", "left": 55}]}}
        rows = pack_path_rows(pack, "sleeves.radar_curated_25")
        self.assertEqual(rows[0]["symbol"], "NASDAQ:MU")


class TestRecipesAndCli(unittest.TestCase):
    def test_recipe_requires_run_id(self):
        from generic_utils.recipes import render_recipe

        sql, params = render_recipe(
            "pred.profile_weeks_pivot", run_id="move_prediction_demo"
        )
        self.assertIn("breakout_long_v1", sql)
        self.assertEqual(params, ["move_prediction_demo"])

    def test_pack_focus_cli_writes_csv(self):
        from generic_utils.tv_scan_cli import main

        pack = {
            "run_id": "briefing_pack_demo",
            "sleeves": {
                "radar_curated_25": [
                    {"symbol": "NASDAQ:MU", "left": 55, "ind": "Semiconductors"}
                ]
            },
        }
        with TemporaryDirectory() as tmp:
            pack_path = Path(tmp) / "pack.json"
            out_path = Path(tmp) / "focus.csv"
            pack_path.write_text(json.dumps(pack), encoding="utf-8")
            payload = main(
                [
                    "pack-focus",
                    "--pack",
                    str(pack_path),
                    "--sleeve",
                    "sleeves.radar_curated_25",
                    "--out",
                    str(out_path),
                ]
            )
            self.assertEqual(payload["spec"]["n"], 1)
            self.assertEqual(payload["rows"], [])
            self.assertTrue(out_path.exists())
            text = out_path.read_text(encoding="utf-8-sig")
            self.assertIn("NASDAQ:MU", text)


class TestExtractStats(unittest.TestCase):
    def test_industry_breadth_preserves_pack_keys(self):
        from operator_briefing.extract import industry_breadth, universe_stats

        rows = [
            {
                "ind": "Software",
                "mcap": 3_000_000_000,
                "day": 1,
                "d5": 2,
                "m1": 3,
                "rsi": 50,
                "country": "United States",
                "exchange": "NASDAQ",
            }
        ] * 6
        rows.append(
            {
                "ind": "Software",
                "mcap": 100,
                "day": 9,
                "d5": 9,
                "m1": 9,
                "rsi": 90,
                "country": "United States",
                "exchange": "NASDAQ",
            }
        )
        breadth = industry_breadth(rows, min_mcap=2_000_000_000, min_n=6)
        self.assertEqual(len(breadth), 1)
        self.assertEqual(breadth[0]["n"], 6)
        self.assertEqual(breadth[0]["pct_up"], 100.0)
        self.assertEqual(breadth[0]["d5"], 2.0)
        stats = universe_stats(rows, min_mcap=2_000_000_000)
        self.assertEqual(stats["n"], 6)
        self.assertEqual(stats["pct_up"], 100.0)
        self.assertEqual(stats["rsi"], 50.0)


if __name__ == "__main__":
    unittest.main()
