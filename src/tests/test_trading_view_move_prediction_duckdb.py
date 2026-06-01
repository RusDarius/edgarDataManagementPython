import csv
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_move_prediction_analysis import (
    _build_duckdb_weekly_storage_layout,
    run_full_analysis_suite,
    run_full_analysis_suite_duckdb,
)
from db.trading_view_move_prediction_duckdb import (
    MovePredictionDuckDBStore,
    query_move_prediction_duckdb,
)


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.reader(csv_file)
        headers = next(reader)
        return headers, list(reader)


def _single_file(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise AssertionError(f"Expected exactly one file for {pattern}, got {matches}")
    return matches[0]


class TestDuckDBWeeklyStorageLayout(unittest.TestCase):
    def test_builds_weekly_partition_and_run_directory(self):
        with TemporaryDirectory() as temp_dir:
            created_at_utc = datetime(2026, 5, 31, 14, 30, tzinfo=timezone.utc)
            layout = _build_duckdb_weekly_storage_layout(
                run_id="move_prediction_20260531_143000",
                created_at_utc=created_at_utc,
                output_dir=temp_dir,
            )

            base_dir = Path(temp_dir)
            expected_period_dir = base_dir / "iso_year=2026" / "week=22"

            self.assertEqual(layout.period_dir, expected_period_dir)
            self.assertEqual(
                layout.run_output_dir,
                expected_period_dir / "runs" / "move_prediction_20260531_143000",
            )
            self.assertEqual(
                layout.database_path,
                expected_period_dir / "move_prediction_2026_W22.duckdb",
            )
            self.assertEqual(layout.parquet_dir, expected_period_dir / "parquet")


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestMovePredictionDuckDBStore(unittest.TestCase):
    def test_append_tabular_output_adds_columns_for_later_runs(self):
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "move_prediction_2026_05.duckdb"

            with MovePredictionDuckDBStore(database_path=database_path) as store:
                store.append_tabular_output(
                    "raw_scan_rows",
                    ["symbol", "Company", "field_a"],
                    [["AAA", "Alpha", "1"]],
                    context={"run_id": "run_a"},
                )
                store.append_tabular_output(
                    "raw_scan_rows",
                    ["symbol", "Company", "field_a", "field_b"],
                    [["BBB", "Beta", "2", "late_field"]],
                    context={"run_id": "run_b"},
                )

                self.assertEqual(
                    store._table_columns("raw_scan_rows"),
                    ["run_id", "row_number", "symbol", "Company", "field_a", "field_b"],
                )

                rows = store.conn.execute("""
                    SELECT run_id, symbol, field_a, field_b
                    FROM raw_scan_rows
                    ORDER BY run_id
                    """).fetchall()

                self.assertEqual(rows[0], ("run_a", "AAA", 1.0, None))
                self.assertEqual(rows[1], ("run_b", "BBB", 2.0, "late_field"))

    def test_delete_run_data_removes_prior_run_rows(self):
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "move_prediction_2026_05.duckdb"

            with MovePredictionDuckDBStore(database_path=database_path) as store:
                store.register_run(
                    run_id="repeat_run",
                    created_at_utc=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
                    suite_name="suite",
                    scan_data_count=1,
                    profile_names=["breakout_long"],
                    industries=["Technology"],
                    min_market_cap_usd=1_000_000_000,
                    max_market_cap_usd=None,
                    include_blind_spot_sections=False,
                    notes="test run",
                )
                store.register_report(
                    run_id="repeat_run",
                    report_key="breakout_long",
                    report_type="profile_log",
                    profile_name="breakout_long",
                    file_path=Path(temp_dir) / "report.log",
                )
                store.append_tabular_output(
                    "raw_scan_rows",
                    ["symbol", "Company"],
                    [["AAA", "Alpha"]],
                    context={"run_id": "repeat_run"},
                )

                deleted_counts = store.delete_run_data("repeat_run")

                self.assertEqual(deleted_counts["run_metadata"], 1)
                self.assertEqual(deleted_counts["generated_reports"], 1)
                self.assertEqual(deleted_counts["raw_scan_rows"], 1)

                self.assertEqual(
                    store.conn.execute(
                        "SELECT COUNT(*) FROM run_metadata WHERE run_id = ?",
                        ["repeat_run"],
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    store.conn.execute(
                        "SELECT COUNT(*) FROM generated_reports WHERE run_id = ?",
                        ["repeat_run"],
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    store.conn.execute(
                        "SELECT COUNT(*) FROM raw_scan_rows WHERE run_id = ?",
                        ["repeat_run"],
                    ).fetchone()[0],
                    0,
                )


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestRunFullAnalysisSuiteDuckDBTransition(unittest.TestCase):
    def test_preserves_legacy_csv_equivalent_tables_and_run_logs(self):
        scan_data = [
            {
                "symbol": "NASDAQ:AAA",
                "name": "Alpha Analytics",
                "sector": "Technology Services",
                "industry": "Software",
                "market": "america",
                "market_cap_basic": 2_500_000_000,
                "close": 42.5,
                "relative_volume_10d_calc": 1.35,
                "Value.Traded": 15_000_000,
                "Perf.W": 4.2,
                "Perf.1M": 8.5,
                "Perf.YTD": 18.0,
                "Perf.Y": 35.0,
                "Perf.5Y": 120.0,
                "change": 1.8,
                "price_earnings_ttm": 24.0,
                "total_revenue_yoy_growth_ttm": 14.0,
                "debt_to_equity": 0.25,
            },
            {
                "symbol": "NYSE:BBB",
                "name": "Beta Industrials",
                "sector": "Industrials",
                "industry": "Machinery",
                "market": "america",
                "market_cap_basic": 4_200_000_000,
                "close": 88.1,
                "relative_volume_10d_calc": 0.75,
                "Value.Traded": 9_000_000,
                "Perf.W": -1.1,
                "Perf.1M": 2.4,
                "Perf.YTD": 6.0,
                "Perf.Y": 12.0,
                "Perf.5Y": 55.0,
                "change": -0.4,
                "price_earnings_ttm": 16.0,
                "total_revenue_yoy_growth_ttm": 7.0,
                "debt_to_equity": 0.55,
            },
            {
                "symbol": "NASDAQ:CCC",
                "name": "Core Consumer",
                "sector": "Consumer Defensive",
                "industry": "Packaged Foods",
                "market": "america",
                "market_cap_basic": 1_800_000_000,
                "close": 21.4,
                "relative_volume_10d_calc": 1.05,
                "Value.Traded": 4_500_000,
                "Perf.W": 0.2,
                "Perf.1M": -3.0,
                "Perf.YTD": -7.0,
                "Perf.Y": -15.0,
                "Perf.5Y": 10.0,
                "change": 0.1,
                "price_earnings_ttm": 11.0,
                "total_revenue_yoy_growth_ttm": -2.0,
                "debt_to_equity": 1.1,
            },
            {
                "symbol": "NYSE:DDD",
                "name": "Delta Energy",
                "sector": "Energy Minerals",
                "industry": "Oil & Gas Production",
                "market": "america",
                "market_cap_basic": 6_100_000_000,
                "close": 63.2,
                "relative_volume_10d_calc": 2.1,
                "Value.Traded": 33_000_000,
                "Perf.W": 6.8,
                "Perf.1M": 12.5,
                "Perf.YTD": 22.0,
                "Perf.Y": 40.0,
                "Perf.5Y": 90.0,
                "change": 2.6,
                "price_earnings_ttm": 9.0,
                "total_revenue_yoy_growth_ttm": 18.0,
                "debt_to_equity": 0.4,
            },
        ]
        profile_names = [
            "breakout_long",
            "quality_value_compounder",
            "fragility_short",
        ]
        run_id = "csv_equivalence"

        with TemporaryDirectory() as legacy_temp_dir, TemporaryDirectory() as duckdb_temp_dir:
            legacy_output_dir = Path(legacy_temp_dir)
            duckdb_output_dir = Path(duckdb_temp_dir)

            run_full_analysis_suite(
                scan_data=deepcopy(scan_data),
                profile_names=profile_names,
                min_market_cap_usd=1_000_000_000,
                include_blind_spot_sections=False,
                output_dir=legacy_output_dir,
            )
            duckdb_result = run_full_analysis_suite_duckdb(
                scan_data=deepcopy(scan_data),
                profile_names=profile_names,
                min_market_cap_usd=1_000_000_000,
                include_blind_spot_sections=False,
                output_dir=duckdb_output_dir,
                run_label=run_id,
                export_parquet=True,
                create_indexes=False,
            )

            database_path = duckdb_result["_duckdb_database"]

            raw_headers, raw_rows = _read_csv(
                _single_file(
                    legacy_output_dir,
                    "tradingview_move_prediction__*profile_breakout_long__raw_data.csv",
                )
            )
            raw_count = query_move_prediction_duckdb(
                database_path,
                "SELECT COUNT(*) AS row_count FROM raw_scan_rows WHERE run_id = ?",
                [run_id],
            )[0]["row_count"]
            self.assertEqual(raw_count, len(raw_rows))

            profile_headers, profile_rows = _read_csv(
                _single_file(
                    legacy_output_dir,
                    "tradingview_move_prediction__*profile_breakout_long.csv",
                )
            )
            for profile_name in profile_names:
                profile_count = query_move_prediction_duckdb(
                    database_path,
                    """
                    SELECT COUNT(*) AS row_count
                    FROM profile_prediction_rows
                    WHERE run_id = ? AND profile_name = ?
                    """,
                    [run_id, profile_name],
                )[0]["row_count"]
                self.assertEqual(profile_count, len(profile_rows))

            consensus_headers, consensus_rows = _read_csv(
                _single_file(
                    legacy_output_dir,
                    "tradingview_consensus_aggregator__*.csv",
                )
            )
            consensus_count = query_move_prediction_duckdb(
                database_path,
                "SELECT COUNT(*) AS row_count FROM consensus_rows WHERE run_id = ?",
                [run_id],
            )[0]["row_count"]
            self.assertEqual(consensus_count, len(consensus_rows))

            import duckdb

            conn = duckdb.connect(str(database_path), read_only=True)
            try:
                raw_columns = [
                    row[1]
                    for row in conn.execute(
                        'PRAGMA table_info("raw_scan_rows")'
                    ).fetchall()
                ]
                profile_columns = [
                    row[1]
                    for row in conn.execute(
                        'PRAGMA table_info("profile_prediction_rows")'
                    ).fetchall()
                ]
                consensus_columns = [
                    row[1]
                    for row in conn.execute(
                        'PRAGMA table_info("consensus_rows")'
                    ).fetchall()
                ]
            finally:
                conn.close()

            self.assertEqual(raw_columns, ["run_id", "row_number", *raw_headers])
            self.assertEqual(
                profile_columns,
                ["run_id", "profile_name", "row_number", *profile_headers],
            )
            self.assertEqual(
                consensus_columns,
                ["run_id", "row_number", *consensus_headers],
            )

            profile_names_in_table = query_move_prediction_duckdb(
                database_path,
                """
                SELECT DISTINCT profile_name
                FROM profile_prediction_rows
                WHERE run_id = ?
                ORDER BY profile_name
                """,
                [run_id],
            )
            self.assertEqual(
                [row["profile_name"] for row in profile_names_in_table],
                sorted(profile_names),
            )

            run_output_dir = duckdb_result["_duckdb_run_output_dir"]
            self.assertTrue((run_output_dir / "_duckdb_run_overview.log").exists())
            report_keys = query_move_prediction_duckdb(
                database_path,
                """
                SELECT report_key
                FROM generated_reports
                WHERE run_id = ?
                ORDER BY report_key
                """,
                [run_id],
            )
            self.assertIn(
                "_duckdb_run_overview",
                [row["report_key"] for row in report_keys],
            )
            self.assertEqual(
                len(list(run_output_dir.glob("tradingview_move_prediction__*.log"))),
                len(profile_names),
            )
            self.assertEqual(
                len(
                    list(
                        run_output_dir.glob(
                            "tradingview_move_prediction_tracking__*.log"
                        )
                    )
                ),
                len(profile_names),
            )
            self.assertEqual(
                len(
                    list(run_output_dir.glob("tradingview_consensus_aggregator__*.log"))
                ),
                1,
            )

            parquet_dir = duckdb_result["_duckdb_parquet_dir"]
            self.assertEqual(parquet_dir.parent, duckdb_result["_duckdb_week_dir"])
            for table_name in [
                "raw_scan_rows",
                "profile_prediction_rows",
                "consensus_rows",
            ]:
                self.assertTrue((parquet_dir / f"{table_name}.parquet").exists())

            conn = duckdb.connect()
            try:
                parquet_report_keys = [
                    row[0]
                    for row in conn.execute(
                        "SELECT report_key FROM read_parquet(?)",
                        [str(parquet_dir / "generated_reports.parquet")],
                    ).fetchall()
                ]
            finally:
                conn.close()
            self.assertIn("_duckdb_run_overview", parquet_report_keys)


if __name__ == "__main__":
    unittest.main()
