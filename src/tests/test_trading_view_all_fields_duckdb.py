import csv
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from data_analysis_scripts.trading_view_export_all_tdfields import (
    _build_all_fields_daily_storage_layout,
    export_all_tradingview_fields_duckdb,
)
from db.trading_view_all_fields_duckdb import query_tradingview_all_fields_duckdb


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


class TestAllFieldsDuckDBStorageLayout(unittest.TestCase):
    def test_builds_daily_partition_and_run_directory(self):
        with TemporaryDirectory() as temp_dir:
            created_at_utc = datetime(2026, 6, 3, 14, 30, tzinfo=timezone.utc)
            layout = _build_all_fields_daily_storage_layout(
                run_id="tradingview_all_fields_20260603_1430",
                created_at_utc=created_at_utc,
                output_dir=temp_dir,
            )

            base_dir = Path(temp_dir)
            expected_period_dir = base_dir / "03_06_2026"

            self.assertEqual(layout.period_dir, expected_period_dir)
            self.assertEqual(
                layout.run_output_dir,
                expected_period_dir / "runs" / "tradingview_all_fields_20260603_1430",
            )
            self.assertEqual(
                layout.database_path,
                expected_period_dir / "tradingview_all_fields_03_06_2026.duckdb",
            )
            self.assertEqual(layout.parquet_dir, expected_period_dir / "parquet")


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestExportAllTradingViewFieldsDuckDB(unittest.TestCase):
    def _write_field_catalog(self, path: Path) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=["Name"])
            writer.writeheader()
            writer.writerow({"Name": "close"})
            writer.writerow({"Name": "name"})
            writer.writerow({"Name": "volume"})

    @patch("data_analysis_scripts.trading_view_export_all_tdfields._fetch_scan_chunk")
    def test_same_day_runs_append_to_same_database(self, fetch_chunk_mock):
        fetch_chunk_mock.side_effect = [
            [
                {"symbol": "NASDAQ:AAA", "close": 10.5, "name": "Alpha"},
                {"symbol": "NYSE:BBB", "close": 20.0, "name": "Beta"},
            ],
            [
                {"symbol": "NASDAQ:AAA", "volume": 1000},
                {"symbol": "NYSE:BBB", "volume": 2000},
            ],
            [
                {"symbol": "NASDAQ:AAA", "close": 11.5, "name": "Alpha 2"},
                {"symbol": "NYSE:BBB", "close": 21.0, "name": "Beta 2"},
            ],
            [
                {"symbol": "NASDAQ:AAA", "volume": 1100},
                {"symbol": "NYSE:BBB", "volume": 2100},
            ],
        ]

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            field_catalog = temp_path / "fields.csv"
            self._write_field_catalog(field_catalog)

            morning_result = export_all_tradingview_fields_duckdb(
                field_catalog_csv=field_catalog,
                output_dir=temp_path,
                chunk_size=2,
                timeout=30,
                run_label="morning",
                reference_time=datetime(2026, 6, 3, 9, 30, tzinfo=timezone.utc),
            )
            afternoon_result = export_all_tradingview_fields_duckdb(
                field_catalog_csv=field_catalog,
                output_dir=temp_path,
                chunk_size=2,
                timeout=30,
                run_label="afternoon",
                reference_time=datetime(2026, 6, 3, 15, 45, tzinfo=timezone.utc),
            )

            self.assertEqual(
                morning_result["_duckdb_database"],
                afternoon_result["_duckdb_database"],
            )
            self.assertEqual(morning_result["_duckdb_day_label"], "03_06_2026")
            self.assertTrue(morning_result["_duckdb_overview_log"].exists())
            self.assertTrue(
                (
                    morning_result["_duckdb_parquet_dir"] / "all_fields_rows.parquet"
                ).exists()
            )

            metadata_rows = query_tradingview_all_fields_duckdb(
                morning_result["_duckdb_database"],
                """
                SELECT run_label, scan_data_count
                FROM run_metadata
                ORDER BY created_at_utc
                """,
            )
            self.assertEqual(
                [row["run_label"] for row in metadata_rows],
                ["morning", "afternoon"],
            )
            self.assertEqual(metadata_rows[0]["scan_data_count"], 2)

            row_count = query_tradingview_all_fields_duckdb(
                morning_result["_duckdb_database"],
                "SELECT COUNT(*) AS row_count FROM all_fields_rows",
            )[0]["row_count"]
            self.assertEqual(row_count, 4)

            rows = query_tradingview_all_fields_duckdb(
                morning_result["_duckdb_database"],
                """
                SELECT symbol, close, name, volume
                FROM all_fields_rows
                WHERE run_id = ?
                ORDER BY symbol
                """,
                [morning_result["_duckdb_run_id"]],
            )
            self.assertEqual(rows[0]["symbol"], "NASDAQ:AAA")
            self.assertEqual(rows[0]["close"], "10.5")
            self.assertEqual(rows[0]["name"], "Alpha")
            self.assertEqual(rows[0]["volume"], "1000")
            self.assertEqual(rows[-1]["name"], "Beta")


if __name__ == "__main__":
    unittest.main()
