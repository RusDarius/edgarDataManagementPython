"""The daily suites read their fields from the focused intraday export: what it must fetch, and how readers fall back."""
from __future__ import annotations

import csv
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class FocusedRequestTests(unittest.TestCase):
    @patch("data_analysis_scripts.trading_view_export_all_tdfields._verify_tradingview_scan_connectivity")
    @patch("data_analysis_scripts.trading_view_export_all_tdfields._fetch_scan_chunk")
    def test_required_fields_join_the_catalog_once(self, fetch_mock, _conn):
        from data_analysis_scripts.trading_view_export_all_tdfields import (
            FOCUSED_CHUNK_SIZE, FOCUSED_REQUIRED_FIELDS, export_all_tradingview_fields_duckdb,
        )

        requested: list[list[str]] = []

        def fake_fetch(columns, *args, **kwargs):
            requested.append(list(columns))
            return [{"symbol": "NASDAQ:AAA", **{c: 1 for c in columns}}]

        fetch_mock.side_effect = fake_fetch
        with TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "fields.csv"
            with catalog.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["Name"])
                writer.writeheader()
                for name in ("symbol", "close", "premarket_change", "price_book_ratio"):  # one required field already listed
                    writer.writerow({"Name": name})
            export_all_tradingview_fields_duckdb(field_catalog_csv=catalog, output_dir=Path(tmp), chunk_size=FOCUSED_CHUNK_SIZE,
                                                 run_label="focused_intraday", min_expected_rows=1, export_parquet=False,
                                                 reference_time=datetime(2026, 11, 18, 13, 0, tzinfo=timezone.utc),
                                                 extra_fields=FOCUSED_REQUIRED_FIELDS)
        self.assertEqual(len(requested), 1)  # still one request
        cols = requested[0]
        self.assertEqual(len(cols), len(set(cols)))  # price_book_ratio is not asked twice
        for name in ("premarket_close", "postmarket_close", "recommendation_total", "ex_dividend_date_upcoming"):
            self.assertIn(name, cols)
        self.assertNotIn("symbol", cols)
        self.assertLess(243 + len(FOCUSED_REQUIRED_FIELDS), FOCUSED_CHUNK_SIZE)  # the real catalog still fits one request


class FallbackTests(unittest.TestCase):
    def test_panel_field_reads_the_first_present_column(self):
        from generic_utils.panel_store import numeric_pick

        self.assertEqual(numeric_pick("close", {"close"}), 'TRY_CAST("close" AS DOUBLE)')
        self.assertEqual(numeric_pick(["price_book_ratio", "price_book_fq"], {"price_book_fq"}), 'TRY_CAST("price_book_fq" AS DOUBLE)')
        self.assertEqual(numeric_pick(["price_book_ratio", "price_book_fq"], {"price_book_ratio", "price_book_fq"}),
                         'COALESCE(TRY_CAST("price_book_ratio" AS DOUBLE), TRY_CAST("price_book_fq" AS DOUBLE))')
        self.assertEqual(numeric_pick(["a", "b"], set()), "CAST(NULL AS DOUBLE)")

    def test_pack_rows_carry_session_marks_and_pb(self):
        import duckdb

        from operator_briefing.discovery import LockedSources
        from operator_briefing.extract import AF_CANDIDATES, extract_all_fields

        self.assertEqual(AF_CANDIDATES["pb"][-1], "price_book_fq")
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "af.duckdb"
            con = duckdb.connect(str(db))
            con.execute("""CREATE TABLE all_fields_rows (run_id VARCHAR, symbol VARCHAR, exchange VARCHAR, country VARCHAR, type VARCHAR,
                close VARCHAR, change VARCHAR, premarket_change VARCHAR, premarket_volume VARCHAR, average_volume_10d_calc VARCHAR,
                "VWAP" VARCHAR, price_book_fq VARCHAR, market_cap_basic VARCHAR)""")
            con.execute("INSERT INTO all_fields_rows VALUES ('r1', 'NASDAQ:AAA', 'NASDAQ', 'United States', 'stock', '100', '-4', '-3', "
                        "'150000', '2000000', '102', '3.5', '5e9')")
            con.close()
            fields = {name: None for name in LockedSources.__dataclass_fields__}
            fields.update(all_fields_db=db, all_fields_run_id="r1", all_fields_day_label="18_11_2026",
                          all_fields_created_at_utc="2026-11-18 16:00:00")  # 11:00 New York: the cash session
            row = extract_all_fields(LockedSources(**fields))[0]
        self.assertEqual((row["us_phase"], row["pb"], row["pre_vol_x"]), ("cash", 3.5, 0.075))
        self.assertAlmostEqual(row["pre_px"], 100 / 0.96 * 0.97, places=2)  # prior close, not the live price
        self.assertAlmostEqual(row["vs_vwap"], -1.96, places=2)


if __name__ == "__main__":
    unittest.main()
