import csv
import json
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_move_prediction_analysis import (
    _build_duckdb_run_id,
    _build_duckdb_weekly_storage_layout,
    run_full_analysis_suite,
    run_full_analysis_suite_duckdb,
    run_full_analysis_suite_with_earnings_priority,
    run_full_analysis_suite_with_earnings_priority_duckdb,
)
from db.trading_view_move_prediction_duckdb import (
    MovePredictionDuckDBStore,
    _is_duckdb_file_lock_error,
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

    def test_generated_run_ids_use_utc_minute_and_uuid_suffix(self):
        reference_time = datetime(2026, 5, 31, 14, 30, 45, tzinfo=timezone.utc)

        first_run_id = _build_duckdb_run_id(reference_time=reference_time)
        second_run_id = _build_duckdb_run_id(reference_time=reference_time)

        self.assertRegex(
            first_run_id,
            r"^move_prediction_20260531_1430_utc_[0-9a-f]{8}$",
        )
        self.assertNotEqual(first_run_id, second_run_id)
        labeled_run_id = _build_duckdb_run_id(
            "Replace Me", reference_time=reference_time
        )
        self.assertRegex(
            labeled_run_id,
            r"^replace_me_20260531_1430_utc_[0-9a-f]{8}$",
        )

    def test_reference_time_controls_historical_week_partition(self):
        reference_time = datetime(2026, 5, 31, 14, 30, tzinfo=timezone.utc)
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
            }
        ]

        with TemporaryDirectory() as temp_dir:
            result = run_full_analysis_suite_duckdb(
                scan_data=scan_data,
                profile_names=["breakout_long"],
                min_market_cap_usd=1_000_000_000,
                include_blind_spot_sections=False,
                output_dir=temp_dir,
                reference_time=reference_time,
                export_parquet=False,
                create_indexes=False,
            )

            self.assertIn("iso_year=2026", str(result["_duckdb_period_dir"]))
            self.assertIn("week=22", str(result["_duckdb_period_dir"]))

            metadata_rows = query_move_prediction_duckdb(
                result["_duckdb_database"],
                "SELECT iso_year, iso_week FROM run_metadata WHERE run_id = ?",
                [result["_duckdb_run_id"]],
            )
            self.assertEqual(len(metadata_rows), 1)
            self.assertEqual(metadata_rows[0]["iso_year"], 2026)
            self.assertEqual(metadata_rows[0]["iso_week"], 22)


class TestDuckDBOpenErrors(unittest.TestCase):
    def test_detects_duckdb_file_lock_error(self):
        lock_error = Exception(
            'IO Error: Cannot open file "move_prediction_2026_W23.duckdb": '
            "The process cannot access the file because it is being used by another process. "
            "File is already open in C:\\Program Files\\nodejs\\node.exe (PID 6572)"
        )
        unrelated_error = Exception("IO Error: Cannot open file: no such directory")

        self.assertTrue(_is_duckdb_file_lock_error(lock_error))
        self.assertFalse(_is_duckdb_file_lock_error(unrelated_error))


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

    def test_append_tabular_output_handles_embedded_quotes_in_text_fields(self):
        company_name = 'AO ""UK Kuzbassrazrezugol\'"" ORD'
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "move_prediction_2026_05.duckdb"

            with MovePredictionDuckDBStore(database_path=database_path) as store:
                store.append_tabular_output(
                    "raw_scan_rows",
                    ["symbol", "Company", "close"],
                    [["RUS:KZRU", company_name, 35.0]],
                    context={"run_id": "quoted_run"},
                )

                rows = store.conn.execute(
                    """
                    SELECT symbol, Company, close
                    FROM raw_scan_rows
                    WHERE run_id = ?
                    """,
                    ["quoted_run"],
                ).fetchall()

                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][0], "RUS:KZRU")
                self.assertEqual(rows[0][2], 35.0)
                self.assertIn("Kuzbassrazrezugol", rows[0][1])

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
        run_label = "csv_equivalence"
        request_payload = {
            "columns": ["symbol", "name", "close", "market_cap_basic"],
            "filter": [
                {
                    "left": "market_cap_basic",
                    "operation": "egreater",
                    "right": 1_000_000_000,
                }
            ],
            "markets": ["america", "europe"],
            "sort": {"sortBy": "relative_volume_10d_calc", "sortOrder": "desc"},
        }

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
                scan_data={
                    "data": deepcopy(scan_data),
                    "request_payload": deepcopy(request_payload),
                    "request_metadata": {
                        "url": "https://scanner.tradingview.com/global/scan?label-product=screener-stock",
                        "timeout_seconds": 30,
                    },
                },
                profile_names=profile_names,
                min_market_cap_usd=1_000_000_000,
                include_blind_spot_sections=False,
                output_dir=duckdb_output_dir,
                run_label=run_label,
                export_parquet=True,
                create_indexes=False,
            )

            database_path = duckdb_result["_duckdb_database"]
            run_id = duckdb_result["_duckdb_run_id"]

            metadata_rows = query_move_prediction_duckdb(
                database_path,
                """
                SELECT
                    run_label,
                    run_id_generated,
                    profile_config_hashes_json,
                    api_request_json,
                    api_request_payload_sha256,
                    api_request_markets_json,
                    code_version_json
                FROM run_metadata
                WHERE run_id = ?
                """,
                [run_id],
            )
            self.assertEqual(len(metadata_rows), 1)
            metadata = metadata_rows[0]
            self.assertEqual(metadata["run_label"], run_label)
            self.assertFalse(metadata["run_id_generated"])
            self.assertEqual(
                json.loads(metadata["api_request_markets_json"]),
                ["america", "europe"],
            )
            api_request_metadata = json.loads(metadata["api_request_json"])
            self.assertEqual(api_request_metadata["request_payload"], request_payload)
            self.assertTrue(metadata["api_request_payload_sha256"])
            self.assertIn("source_files", json.loads(metadata["code_version_json"]))

            profile_hashes = json.loads(metadata["profile_config_hashes_json"])
            self.assertEqual(sorted(profile_hashes), sorted(profile_names))
            profile_config_rows = query_move_prediction_duckdb(
                database_path,
                """
                SELECT profile_name, profile_config_hash, profile_config_json
                FROM profile_config_snapshots
                WHERE run_id = ?
                ORDER BY profile_name
                """,
                [run_id],
            )
            self.assertEqual(len(profile_config_rows), len(profile_names))
            for profile_config_row in profile_config_rows:
                self.assertEqual(
                    profile_hashes[profile_config_row["profile_name"]],
                    profile_config_row["profile_config_hash"],
                )
                config_json = json.loads(profile_config_row["profile_config_json"])
                self.assertIn("resolved_horizon_weights", config_json)
                self.assertEqual(
                    config_json["schema_version"],
                    "move_prediction_profile_config_v1",
                )

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

    def test_earnings_priority_duckdb_preserves_legacy_csv_equivalent_tables(self):
        reference_time = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
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
                "earnings_release_date": "2026-05-01T20:00:00+00:00",
                "earnings_release_next_date": "2026-06-04T20:00:00+00:00",
                "earnings_release_next_calendar_date": "2026-06-04",
                "earnings_release_next_time": "amc",
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
                "earnings_release_date": "2026-05-08T20:00:00+00:00",
                "earnings_release_next_date": "2026-06-12T20:00:00+00:00",
                "earnings_release_next_calendar_date": "2026-06-12",
                "earnings_release_next_time": "bmo",
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
                "earnings_release_date": "2026-05-20T20:00:00+00:00",
                "earnings_release_next_date": "2026-07-05T20:00:00+00:00",
                "earnings_release_next_calendar_date": "2026-07-05",
                "earnings_release_next_time": "amc",
            },
        ]
        profile_names = [
            "breakout_long",
            "quality_value_compounder",
            "fragility_short",
        ]
        request_payload = {
            "columns": ["symbol", "name", "close", "market_cap_basic"],
            "filter": [
                {
                    "left": "market_cap_basic",
                    "operation": "egreater",
                    "right": 1_000_000_000,
                }
            ],
            "markets": ["america"],
            "sort": {"sortBy": "relative_volume_10d_calc", "sortOrder": "desc"},
        }

        with TemporaryDirectory() as legacy_temp_dir, TemporaryDirectory() as duckdb_temp_dir:
            legacy_output_dir = Path(legacy_temp_dir)
            duckdb_output_dir = Path(duckdb_temp_dir)

            run_full_analysis_suite_with_earnings_priority(
                scan_data=deepcopy(scan_data),
                profile_names=profile_names,
                min_market_cap_usd=1_000_000_000,
                include_blind_spot_sections=False,
                output_dir=legacy_output_dir,
                reference_time=reference_time,
            )
            duckdb_result = run_full_analysis_suite_with_earnings_priority_duckdb(
                scan_data={
                    "data": deepcopy(scan_data),
                    "request_payload": deepcopy(request_payload),
                    "request_metadata": {
                        "url": "https://scanner.tradingview.com/global/scan?label-product=screener-stock",
                        "timeout_seconds": 30,
                    },
                },
                profile_names=profile_names,
                min_market_cap_usd=1_000_000_000,
                include_blind_spot_sections=False,
                output_dir=duckdb_output_dir,
                run_label="earnings_duckdb",
                reference_time=reference_time,
                export_parquet=True,
                create_indexes=False,
            )

            database_path = duckdb_result["_duckdb_database"]
            run_id = duckdb_result["_duckdb_run_id"]
            earnings_output_dir = legacy_output_dir / "earnings_priority"

            consensus_headers, consensus_rows = _read_csv(
                _single_file(
                    earnings_output_dir,
                    "tradingview_earnings_priority__*profile_consensus.csv",
                )
            )
            consensus_count = query_move_prediction_duckdb(
                database_path,
                """
                SELECT COUNT(*) AS row_count
                FROM earnings_priority_consensus_rows
                WHERE run_id = ? AND sort_flavour = 'default'
                """,
                [run_id],
            )[0]["row_count"]
            self.assertEqual(consensus_count, len(consensus_rows))

            profile_headers, profile_rows = _read_csv(
                _single_file(
                    earnings_output_dir,
                    "tradingview_earnings_priority__*profile_breakout_long.csv",
                )
            )
            profile_count = query_move_prediction_duckdb(
                database_path,
                """
                SELECT COUNT(*) AS row_count
                FROM earnings_priority_profile_rows
                WHERE run_id = ?
                  AND profile_name = 'breakout_long'
                  AND sort_flavour = 'default'
                """,
                [run_id],
            )[0]["row_count"]
            self.assertEqual(profile_count, len(profile_rows))

            flavour_count = query_move_prediction_duckdb(
                database_path,
                """
                SELECT COUNT(*) AS row_count
                FROM earnings_priority_consensus_rows
                WHERE run_id = ? AND sort_flavour = 'flavour_marketcap'
                """,
                [run_id],
            )[0]["row_count"]
            self.assertEqual(flavour_count, len(consensus_rows))

            import duckdb

            conn = duckdb.connect(str(database_path), read_only=True)
            try:
                consensus_columns = [
                    row[1]
                    for row in conn.execute(
                        'PRAGMA table_info("earnings_priority_consensus_rows")'
                    ).fetchall()
                ]
                profile_columns = [
                    row[1]
                    for row in conn.execute(
                        'PRAGMA table_info("earnings_priority_profile_rows")'
                    ).fetchall()
                ]
            finally:
                conn.close()

            self.assertEqual(
                consensus_columns,
                ["run_id", "sort_flavour", "row_number", *consensus_headers],
            )
            self.assertEqual(
                profile_columns,
                [
                    "run_id",
                    "profile_name",
                    "sort_flavour",
                    "row_number",
                    *profile_headers,
                ],
            )

            report_keys = query_move_prediction_duckdb(
                database_path,
                """
                SELECT report_key
                FROM generated_reports
                WHERE run_id = ?
                  AND report_key LIKE '_earnings_priority%'
                ORDER BY report_key
                """,
                [run_id],
            )
            self.assertEqual(
                len(report_keys),
                3 * (1 + len(profile_names)),
            )
            self.assertTrue(
                (
                    duckdb_result["_duckdb_parquet_dir"]
                    / "earnings_priority_consensus_rows.parquet"
                ).exists()
            )
            self.assertTrue(
                (
                    duckdb_result["_duckdb_parquet_dir"]
                    / "earnings_priority_profile_rows.parquet"
                ).exists()
            )
            self.assertIn("_earnings_priority_consensus", duckdb_result)
            self.assertIn(
                "_earnings_priority_breakout_long_flavour_score",
                duckdb_result,
            )


if __name__ == "__main__":
    unittest.main()
