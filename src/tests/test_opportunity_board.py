from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path


class TestOpportunityBoard(unittest.TestCase):
    def test_band_position_labels_and_distances(self):
        from generic_utils.opportunity_board import band_position

        self.assertEqual(band_position(100, 90, 95, 85)["band_state"], "over_band")
        self.assertEqual(band_position(92, 90, 95, 85)["band_state"], "in_band")
        self.assertEqual(band_position(87, 90, 95, 85)["band_state"], "under_band")
        self.assertEqual(band_position(80, 90, 95, 85)["band_state"], "below_kill")
        out = band_position(100, 90, 95, 85)
        self.assertEqual(out["to_band_high_pct"], -5.0)
        self.assertEqual(out["kill_dist_pct"], -15.0)
        self.assertIsNone(band_position(None, 90, 95, 85)["band_state"])

    def test_cell_terciles_split_per_axis(self):
        from generic_utils.opportunity_board import cell_terciles

        axes = {"runup": "bar_runup", "r_3m": "bar_tape", "eps_rev_3m": "bar_rev"}
        self.assertEqual(cell_terciles("runup:T2 & r_3m:T1 & eps_rev_3m:T3", axes), {"bar_runup": "T2", "bar_tape": "T1", "bar_rev": "T3"})
        self.assertEqual(cell_terciles("runup:T1 & r_3m:T1", axes)["bar_rev"], None)
        self.assertEqual(cell_terciles(None, axes), {"bar_runup": None, "bar_tape": None, "bar_rev": None})

    def test_build_joins_by_symbol_and_keeps_group_order(self):
        import duckdb

        from generic_utils.opportunity_board import build_board

        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp)
            (pack / "outlook").mkdir()
            con = duckdb.connect(str(pack / "outlook" / "outlook.duckdb"))
            con.execute("CREATE TABLE names AS SELECT * FROM (VALUES ('NASDAQ:AAA', 100.0, 90.0, 95.0, 85.0, 5000.0), "
                        "('NYSE:BBB', 50.0, NULL, NULL, NULL, NULL)) t(symbol, close, bounce_low, bounce_high, bounce_kill, book_usd)")
            con.close()
            with open(pack / "extra.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["symbol", "gap_pct", "cell"])
                w.writerow(["NYSE:BBB", "12.5", "runup:T3 & r_3m:T3 & eps_rev_3m:T1"])
            recipe = {"base": {"duckdb": "{PACK}/outlook/outlook.duckdb", "table": "names"},
                      "joins": [{"name": "x", "csv": "{PACK}/extra.csv", "id_field": "symbol", "fields": {"gap_pct": "peer_gap_pct", "cell": "bb_cell"}}],
                      "cell_axes": {"r_3m": "bar_tape", "eps_rev_3m": "bar_rev"},
                      "column_groups": [["Name", ["symbol", "in_book", "named"]], ["Band", ["band_state", "to_band_high_pct"]],
                                        ["Bar", ["bar_tape", "bar_rev", "peer_gap_pct"]]]}
            out = build_board(pack, recipe=recipe, ids=["NASDAQ:ZZZ"])
            rows = {r["symbol"]: r for r in out["names"]}
            self.assertEqual(list(out["names"][0]), ["symbol", "in_book", "named", "band_state", "to_band_high_pct", "bar_tape", "bar_rev", "peer_gap_pct"])
            self.assertTrue(rows["NASDAQ:AAA"]["in_book"])
            self.assertEqual(rows["NASDAQ:AAA"]["band_state"], "over_band")
            self.assertEqual(rows["NYSE:BBB"]["bar_rev"], "T1")
            self.assertEqual(float(rows["NYSE:BBB"]["peer_gap_pct"]), 12.5)
            self.assertTrue(rows["NASDAQ:ZZZ"]["named"])
            self.assertEqual(out["spec"]["missing_ids"], ["NASDAQ:ZZZ"])


if __name__ == "__main__":
    unittest.main()
