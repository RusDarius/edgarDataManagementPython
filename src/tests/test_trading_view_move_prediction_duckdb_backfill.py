import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_move_prediction_duckdb_backfill import (
    BACKFILL_MANIFEST_HEADERS,
    BACKFILL_SUMMARY_LABEL,
    _build_backfill_run_label,
    _build_summary_manifest_row,
    _collect_unprocessed_interrupt_artifacts,
    _discover_candidate_csvs,
    _discover_pre_existing_covered_date_labels,
    _format_execution_seconds,
    _group_csv_inputs_by_iso_week,
    _is_backfill_csv_input_already_covered,
    _is_tdfields_chunk_csv,
    _is_week_already_covered,
    _iso_week_key,
    _normalize_target_date_labels,
    _prepare_backfill_csv_worker,
    _resolve_load_worker_cap,
    _resolve_parallel_worker_count,
    BackfillCsvInput,
    BackfillMemoryBudget,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    BACKSCAN_RAW_CSV_GLOB,
)


class TestDuckDBBackfillCsvDiscovery(unittest.TestCase):
    def test_chunk_csv_is_detected(self):
        chunk_path = Path(
            "tradingview_global_all_tdfields_29_03_2026_chunk_001.csv"
        )
        main_path = Path("tradingview_global_all_tdfields_29_03_2026.csv")

        self.assertTrue(_is_tdfields_chunk_csv(chunk_path))
        self.assertFalse(_is_tdfields_chunk_csv(main_path))

    def test_discover_candidate_csvs_ignores_chunk_exports(self):
        with TemporaryDirectory() as temp_dir:
            dated_folder = Path(temp_dir) / "29_03_2026"
            dated_folder.mkdir(parents=True)
            main_csv = (
                dated_folder / "tradingview_global_all_tdfields_29_03_2026.csv"
            )
            chunk_csv = (
                dated_folder
                / "tradingview_global_all_tdfields_29_03_2026_chunk_001.csv"
            )
            main_csv.write_text("symbol\nAAPL\n", encoding="utf-8")
            chunk_csv.write_text("symbol\nMSFT\n", encoding="utf-8")

            discovered = _discover_candidate_csvs(
                [dated_folder],
                csv_glob=BACKSCAN_RAW_CSV_GLOB,
            )

            self.assertEqual(discovered, [main_csv])

    def test_discover_candidate_csvs_honors_target_date_labels(self):
        with TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir)
            target_folder = root_dir / "13_04_2026"
            skipped_folder = root_dir / "14_04_2026"
            target_folder.mkdir(parents=True)
            skipped_folder.mkdir(parents=True)

            target_csv = (
                target_folder / "tradingview_global_all_tdfields_13_04_2026.csv"
            )
            skipped_csv = (
                skipped_folder / "tradingview_global_all_tdfields_14_04_2026.csv"
            )
            target_csv.write_text("symbol\nAAPL\n", encoding="utf-8")
            skipped_csv.write_text("symbol\nMSFT\n", encoding="utf-8")

            discovered = _discover_candidate_csvs(
                [root_dir],
                csv_glob=BACKSCAN_RAW_CSV_GLOB,
                target_date_labels={"13_04_2026"},
            )

            self.assertEqual(discovered, [target_csv])

    def test_target_date_labels_reject_invalid_format(self):
        with self.assertRaises(ValueError):
            _normalize_target_date_labels(["13-04-2026"])


class TestDuckDBBackfillCoverageHelpers(unittest.TestCase):
    def test_iso_week_key_uses_source_date(self):
        source_date = datetime(2026, 3, 29, tzinfo=timezone.utc)
        self.assertEqual(_iso_week_key(source_date), (2026, 13))

    def test_week_is_not_covered_when_partition_missing(self):
        with TemporaryDirectory() as temp_dir:
            period_dir = Path(temp_dir) / "iso_year=2026" / "week=13"
            self.assertFalse(_is_week_already_covered(period_dir))

    def test_week_is_covered_when_runs_and_database_exist(self):
        with TemporaryDirectory() as temp_dir:
            period_dir = Path(temp_dir) / "iso_year=2026" / "week=13"
            runs_dir = period_dir / "runs" / "sample_run"
            runs_dir.mkdir(parents=True)
            (period_dir / "move_prediction_2026_W13.duckdb").write_text("", encoding="utf-8")

            self.assertTrue(_is_week_already_covered(period_dir))

    def test_backfill_run_is_not_covered_when_only_other_days_exist(self):
        source_date = datetime(2026, 4, 20, tzinfo=timezone.utc)
        csv_input = BackfillCsvInput(
            csv_path=Path("ignored.csv"),
            source_date_utc=source_date,
            source_date_label="20_04_2026",
            source_folder=Path("20_04_2026"),
        )

        with TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            period_dir = output_root / "iso_year=2026" / "week=17"
            other_run_label = _build_backfill_run_label(
                "16_04_2026",
                "raw_csv_duckdb_backfill",
            )
            other_run_dir = period_dir / "runs" / f"{other_run_label}_20260416_1200_utc_abcd1234"
            other_run_dir.mkdir(parents=True)
            (other_run_dir / "_duckdb_run_overview.log").write_text("", encoding="utf-8")
            (period_dir / "move_prediction_2026_W17.duckdb").write_text("", encoding="utf-8")

            self.assertFalse(
                _is_backfill_csv_input_already_covered(
                    csv_input,
                    output_root,
                    "raw_csv_duckdb_backfill",
                )
            )

    def test_backfill_run_is_covered_when_matching_run_exists(self):
        source_date = datetime(2026, 4, 16, tzinfo=timezone.utc)
        csv_input = BackfillCsvInput(
            csv_path=Path("ignored.csv"),
            source_date_utc=source_date,
            source_date_label="16_04_2026",
            source_folder=Path("16_04_2026"),
        )

        with TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            period_dir = output_root / "iso_year=2026" / "week=16"
            run_label = _build_backfill_run_label(
                "16_04_2026",
                "raw_csv_duckdb_backfill",
            )
            run_dir = period_dir / "runs" / f"{run_label}_20260416_1200_utc_abcd1234"
            run_dir.mkdir(parents=True)
            (run_dir / "_duckdb_run_overview.log").write_text("", encoding="utf-8")

            self.assertTrue(
                _is_backfill_csv_input_already_covered(
                    csv_input,
                    output_root,
                    "raw_csv_duckdb_backfill",
                )
            )

    def test_discover_pre_existing_covered_date_labels_only_marks_existing_days(self):
        covered_input = BackfillCsvInput(
            csv_path=Path("16.csv"),
            source_date_utc=datetime(2026, 4, 16, tzinfo=timezone.utc),
            source_date_label="16_04_2026",
            source_folder=Path("16_04_2026"),
        )
        pending_input = BackfillCsvInput(
            csv_path=Path("20.csv"),
            source_date_utc=datetime(2026, 4, 20, tzinfo=timezone.utc),
            source_date_label="20_04_2026",
            source_folder=Path("20_04_2026"),
        )

        with TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            period_dir = output_root / "iso_year=2026" / "week=16"
            run_label = _build_backfill_run_label(
                "16_04_2026",
                "raw_csv_duckdb_backfill",
            )
            run_dir = period_dir / "runs" / f"{run_label}_20260416_1200_utc_abcd1234"
            run_dir.mkdir(parents=True)
            (run_dir / "_duckdb_run_overview.log").write_text("", encoding="utf-8")
            (period_dir / "move_prediction_2026_W16.duckdb").write_text("", encoding="utf-8")

            covered_date_labels = _discover_pre_existing_covered_date_labels(
                [covered_input, pending_input],
                output_root,
                "raw_csv_duckdb_backfill",
            )

            self.assertEqual(covered_date_labels, {"16_04_2026"})


class TestDuckDBBackfillParallelHelpers(unittest.TestCase):
    def test_group_csv_inputs_by_iso_week_keeps_days_serial_per_week(self):
        inputs = [
            BackfillCsvInput(
                csv_path=Path("c.csv"),
                source_date_utc=datetime(2026, 4, 5, tzinfo=timezone.utc),
                source_date_label="05_04_2026",
                source_folder=Path("05_04_2026"),
            ),
            BackfillCsvInput(
                csv_path=Path("a.csv"),
                source_date_utc=datetime(2026, 3, 29, tzinfo=timezone.utc),
                source_date_label="29_03_2026",
                source_folder=Path("29_03_2026"),
            ),
            BackfillCsvInput(
                csv_path=Path("b.csv"),
                source_date_utc=datetime(2026, 3, 30, tzinfo=timezone.utc),
                source_date_label="30_03_2026",
                source_folder=Path("30_03_2026"),
            ),
        ]

        grouped = _group_csv_inputs_by_iso_week(inputs)

        self.assertEqual([week_key for week_key, _ in grouped], [(2026, 13), (2026, 14)])
        self.assertEqual(
            [item.source_date_label for item in grouped[0][1]],
            ["29_03_2026"],
        )
        self.assertEqual(
            [item.source_date_label for item in grouped[1][1]],
            ["30_03_2026", "05_04_2026"],
        )

    def test_parallel_worker_count_is_capped_by_week_count(self):
        self.assertEqual(_resolve_parallel_worker_count(0, None), 1)
        self.assertEqual(_resolve_parallel_worker_count(1, 8), 1)
        self.assertEqual(_resolve_parallel_worker_count(2, 8), 2)
        self.assertEqual(_resolve_parallel_worker_count(5, 3), 3)

    def test_load_worker_cap_uses_explicit_parallel_limit(self):
        self.assertEqual(_resolve_load_worker_cap(3), 3)

    def test_memory_budget_blocks_parallel_loads_while_write_active(self):
        budget = BackfillMemoryBudget(max_gb=20.0)
        self.assertFalse(
            budget.can_schedule_load(
                active_loads=0,
                prepared_waiting=0,
                active_writes=1,
            )
        )
        self.assertTrue(
            budget.can_schedule_load(
                active_loads=0,
                prepared_waiting=0,
                active_writes=0,
            )
        )

    def test_memory_budget_blocks_more_than_one_prepared_snapshot(self):
        budget = BackfillMemoryBudget(max_gb=20.0)
        self.assertFalse(
            budget.can_schedule_load(
                active_loads=0,
                prepared_waiting=1,
                active_writes=0,
            )
        )

    def test_prepare_backfill_csv_worker_loads_and_filters_rows(self):
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "tradingview_global_all_tdfields_13_04_2026.csv"
            csv_path.write_text(
                "symbol,market_cap_basic\n"
                "AAPL,2000000000\n"
                "SMALL,100\n",
                encoding="utf-8",
            )
            csv_input = BackfillCsvInput(
                csv_path=csv_path,
                source_date_utc=datetime(2026, 4, 13, tzinfo=timezone.utc),
                source_date_label="13_04_2026",
                source_folder=Path(temp_dir),
            )

            prepared = _prepare_backfill_csv_worker(
                csv_input,
                min_market_cap_usd=1_000_000_000,
                max_market_cap_usd=None,
            )

            self.assertEqual(prepared.rows_loaded, 2)
            self.assertEqual(len(prepared.scan_data), 1)
            self.assertEqual(prepared.scan_data[0]["symbol"], "AAPL")
            self.assertGreaterEqual(prepared.load_seconds, 0.0)

    def test_targeted_days_in_same_iso_week_stay_grouped(self):
        inputs = [
            BackfillCsvInput(
                csv_path=Path("13.csv"),
                source_date_utc=datetime(2026, 4, 13, tzinfo=timezone.utc),
                source_date_label="13_04_2026",
                source_folder=Path("13_04_2026"),
            ),
            BackfillCsvInput(
                csv_path=Path("14.csv"),
                source_date_utc=datetime(2026, 4, 14, tzinfo=timezone.utc),
                source_date_label="14_04_2026",
                source_folder=Path("14_04_2026"),
            ),
            BackfillCsvInput(
                csv_path=Path("15.csv"),
                source_date_utc=datetime(2026, 4, 15, tzinfo=timezone.utc),
                source_date_label="15_04_2026",
                source_folder=Path("15_04_2026"),
            ),
        ]

        grouped = _group_csv_inputs_by_iso_week(inputs)
        expected_week_key = _iso_week_key(inputs[0].source_date_utc)

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0][0], expected_week_key)
        self.assertEqual(
            _iso_week_key(inputs[1].source_date_utc),
            expected_week_key,
        )
        self.assertEqual(
            _iso_week_key(inputs[2].source_date_utc),
            expected_week_key,
        )
        self.assertEqual(
            [item.source_date_label for item in grouped[0][1]],
            ["13_04_2026", "14_04_2026", "15_04_2026"],
        )


class TestDuckDBBackfillProfilingAndInterrupt(unittest.TestCase):
    def test_format_execution_seconds(self):
        self.assertEqual(_format_execution_seconds(12.3456), "12.346")
        self.assertEqual(_format_execution_seconds(None), "")

    def test_summary_manifest_row_contains_total_timing(self):
        summary_row = _build_summary_manifest_row(
            run_outcome="completed",
            total_execution_seconds=123.4,
            candidate_count=5,
            processed_count=3,
            skipped_count=1,
            interrupted_count=1,
            duckdb_output_root=Path("duckdb_runs"),
        )

        self.assertEqual(summary_row[0], BACKFILL_SUMMARY_LABEL)
        self.assertEqual(summary_row[4], "completed")
        self.assertEqual(summary_row[8], "5")
        self.assertEqual(summary_row[9], "3")
        self.assertEqual(summary_row[-1], "123.400")
        self.assertEqual(len(summary_row), len(BACKFILL_MANIFEST_HEADERS))

    def test_collect_unprocessed_interrupt_artifacts_marks_remaining_days(self):
        planned_inputs = [
            BackfillCsvInput(
                csv_path=Path("13.csv"),
                source_date_utc=datetime(2026, 4, 13, tzinfo=timezone.utc),
                source_date_label="13_04_2026",
                source_folder=Path("13_04_2026"),
            ),
            BackfillCsvInput(
                csv_path=Path("14.csv"),
                source_date_utc=datetime(2026, 4, 14, tzinfo=timezone.utc),
                source_date_label="14_04_2026",
                source_folder=Path("14_04_2026"),
            ),
        ]

        interrupted_records, manifest_rows = _collect_unprocessed_interrupt_artifacts(
            planned_inputs,
            completed_date_labels={"13_04_2026"},
            duckdb_output_root=Path("duckdb_runs"),
        )

        self.assertEqual(len(interrupted_records), 1)
        self.assertEqual(
            interrupted_records[0]["source_date_label"],
            "14_04_2026",
        )
        self.assertEqual(manifest_rows[0][4], "interrupted")
        self.assertEqual(manifest_rows[0][5], "execution_stopped_by_user")


if __name__ == "__main__":
    unittest.main()
