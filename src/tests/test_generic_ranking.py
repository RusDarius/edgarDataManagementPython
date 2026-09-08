import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class TestTopN(unittest.TestCase):
    def test_sorts_descending_by_default(self):
        from generic_utils.ranking import top_n

        rows = [
            {"symbol": "A", "left": 10},
            {"symbol": "B", "left": 40},
            {"symbol": "C", "left": 25},
        ]
        result = top_n(rows, field="left", n=2)
        self.assertEqual([r["symbol"] for r in result], ["B", "C"])

    def test_ascending_when_reverse_false(self):
        from generic_utils.ranking import top_n

        rows = [
            {"symbol": "A", "rank": 3},
            {"symbol": "B", "rank": 1},
            {"symbol": "C", "rank": 2},
        ]
        result = top_n(rows, field="rank", n=2, reverse=False)
        self.assertEqual([r["symbol"] for r in result], ["B", "C"])

    def test_missing_values_sort_last(self):
        from generic_utils.ranking import top_n

        rows = [{"symbol": "A", "left": None}, {"symbol": "B", "left": 5}]
        result = top_n(rows, field="left", n=2)
        self.assertEqual([r["symbol"] for r in result], ["B", "A"])

    def test_no_eligibility_filtering_applied(self):
        """A junk-looking row (huge value, no other field) still ranks purely by field."""
        from generic_utils.ranking import top_n

        rows = [{"symbol": "JUNK", "left": 9999}, {"symbol": "SNDK", "left": 64}]
        result = top_n(rows, field="left", n=1)
        self.assertEqual(result[0]["symbol"], "JUNK")

    def test_tie_breaker(self):
        """Tie-breaker sorts in the same direction as the primary field."""
        from generic_utils.ranking import top_n

        rows = [
            {"symbol": "A", "left": 50, "opp": 10},
            {"symbol": "B", "left": 50, "opp": 2},
        ]
        result = top_n(rows, field="left", n=2, tie_breaker="opp", reverse=True)
        self.assertEqual(result[0]["symbol"], "A")


class TestTopNBy(unittest.TestCase):
    def test_custom_key_function(self):
        from generic_utils.ranking import top_n_by

        rows = [{"symbol": "A", "a": 1, "b": 10}, {"symbol": "B", "a": 5, "b": 1}]
        result = top_n_by(rows, key_fn=lambda r: r["a"] + r["b"], n=1)
        self.assertEqual(result[0]["symbol"], "A")


class TestRankByWeights(unittest.TestCase):
    def test_weighted_composite_is_visible(self):
        from generic_utils.ranking import rank_by_weights

        rows = [
            {"symbol": "A", "bo": 1.0, "cont": 0.0},
            {"symbol": "B", "bo": 0.0, "cont": 1.0},
        ]
        result = rank_by_weights(rows, weights={"bo": 2.0, "cont": 1.0}, n=2)
        self.assertEqual(result[0]["symbol"], "A")
        self.assertEqual(result[0]["_composite_score"], 2.0)
        self.assertEqual(result[1]["_composite_score"], 1.0)


class TestGroupCappedTopN(unittest.TestCase):
    def test_generic_group_field_and_cap(self):
        from generic_utils.ranking import group_capped_top_n

        rows = [
            {"symbol": f"S{i}", "setup_type": "breakout", "score": 100 - i}
            for i in range(8)
        ]
        rows.append({"symbol": "S99", "setup_type": "reversal", "score": 50})
        kept, overflow = group_capped_top_n(
            rows, group_field="setup_type", cap=3, field="score"
        )
        self.assertEqual(sum(1 for r in kept if r["setup_type"] == "breakout"), 3)
        self.assertEqual(sum(1 for r in kept if r["setup_type"] == "reversal"), 1)
        self.assertEqual(len(overflow), 5)

    def test_exempt_ids_bypass_cap(self):
        from generic_utils.ranking import group_capped_top_n

        rows = [{"symbol": f"S{i}", "ind": "X", "score": 10 - i} for i in range(5)]
        kept, overflow = group_capped_top_n(
            rows, group_field="ind", cap=2, field="score", exempt={"S4"}
        )
        kept_symbols = {r["symbol"] for r in kept}
        self.assertIn("S4", kept_symbols)
        self.assertEqual(len(overflow), 2)

    def test_overflow_fields_copied(self):
        from generic_utils.ranking import group_capped_top_n

        rows = [
            {"symbol": f"S{i}", "ind": "X", "left": 90 - i, "score": 10 - i}
            for i in range(4)
        ]
        kept, overflow = group_capped_top_n(
            rows,
            group_field="ind",
            cap=2,
            field="score",
            overflow_fields=("left",),
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(overflow[0]["left"], 88)
        self.assertEqual(overflow[0]["symbol"], "S2")

    def test_no_default_cap_required_explicitly(self):
        import inspect

        from generic_utils.ranking import group_capped_top_n

        params = inspect.signature(group_capped_top_n).parameters
        self.assertNotIn("default", str(params["cap"]))
        self.assertEqual(params["cap"].default, inspect.Parameter.empty)


class TestPresets(unittest.TestCase):
    def test_top_upside_no_threshold(self):
        from generic_utils.ranking import top_upside

        rows = [{"symbol": "A", "left": 5}, {"symbol": "B", "left": 500}]
        result = top_upside(rows, n=2)
        self.assertEqual(result[0]["symbol"], "B")

    def test_top_movers_supports_losers_via_reverse(self):
        from generic_utils.ranking import top_movers

        rows = [{"symbol": "A", "d5": -20}, {"symbol": "B", "d5": 10}]
        gainers = top_movers(rows, n=1)
        losers = top_movers(rows, n=1, reverse=False)
        self.assertEqual(gainers[0]["symbol"], "B")
        self.assertEqual(losers[0]["symbol"], "A")

    def test_top_rankers_default_ascending(self):
        from generic_utils.ranking import top_rankers

        rows = [{"symbol": "A", "rank_overall": 50}, {"symbol": "B", "rank_overall": 1}]
        result = top_rankers(rows, field="rank_overall", n=1)
        self.assertEqual(result[0]["symbol"], "B")


class TestScanSourcesCsv(unittest.TestCase):
    def test_rows_from_csv_roundtrip(self):
        from generic_utils.scan_sources import rows_from_csv

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=["symbol", "left"])
                writer.writeheader()
                writer.writerow({"symbol": "NASDAQ:MU", "left": "65"})
                writer.writerow({"symbol": "NASDAQ:SNDK", "left": "63"})
            rows = rows_from_csv(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["symbol"], "NASDAQ:MU")


class TestScanSourcesDuckDB(unittest.TestCase):
    def test_rows_from_duckdb_roundtrip(self):
        duckdb = __import__("duckdb")
        from generic_utils.scan_sources import rows_from_duckdb

        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "sample.duckdb"
            conn = duckdb.connect(str(db_path))
            conn.execute("CREATE TABLE raw_scan_rows (symbol VARCHAR, left_pct DOUBLE)")
            conn.execute(
                "INSERT INTO raw_scan_rows VALUES ('NASDAQ:MU', 65.0), ('NASDAQ:SNDK', 63.0)"
            )
            conn.close()
            rows = rows_from_duckdb(
                db_path, "SELECT * FROM raw_scan_rows ORDER BY left_pct DESC"
            )
            self.assertEqual(rows[0]["symbol"], "NASDAQ:MU")
            self.assertEqual(rows[0]["left_pct"], 65.0)


if __name__ == "__main__":
    unittest.main()
