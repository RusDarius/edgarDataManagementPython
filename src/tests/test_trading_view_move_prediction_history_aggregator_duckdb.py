import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_move_prediction_history_aggregator import (
    HISTORICAL_ANALYSIS_VIEW_NAMES,
    build_move_prediction_history_duckdb_inputs_from_week_folders,
    run_move_prediction_history_aggregation_duckdb,
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


CSV_HEADERS = [
    "symbol",
    "Company",
    "name",
    "exchange",
    "country",
    "sector",
    "industry",
    "market",
    "scoring_profile",
    "manager_action_signal",
    "market_cap_basic",
    "close",
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.YTD",
    "Perf.Y",
    "Perf.5Y",
    "days_score",
    "days_direction",
    "days_confidence",
    "days_coverage",
    "days_setup",
    "days_risk_adjusted_score",
    "days_risk_tier",
    "weeks_score",
    "weeks_direction",
    "weeks_confidence",
    "weeks_coverage",
    "weeks_setup",
    "weeks_risk_adjusted_score",
    "weeks_risk_tier",
    "months_score",
    "months_direction",
    "months_confidence",
    "months_coverage",
    "months_setup",
    "months_risk_adjusted_score",
    "months_risk_tier",
    "years_score",
    "years_direction",
    "years_confidence",
    "years_coverage",
    "years_setup",
    "years_risk_adjusted_score",
    "years_risk_tier",
]


def _rows_to_csv_values(rows: list[list[object]]) -> list[list[str]]:
    return [[str(value) for value in row] for row in rows]


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestTradingViewMovePredictionHistoryAggregatorDuckDB(unittest.TestCase):
    def test_aggregates_duckdb_runs_into_duckdb_only_history_dataset(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            iso_year_root = temp_root / "duckdb_runs" / "iso_year=2026"
            week_dir = iso_year_root / "week=22"
            week_dir.mkdir(parents=True, exist_ok=True)
            database_path = week_dir / "move_prediction_2026_W22.duckdb"
            output_root = temp_root / "duckdb_runs" / "historical_prediction_analysis"

            run_1 = "move_prediction_20260601_1419_utc_aaa11111"
            run_2 = "move_prediction_20260601_2058_utc_bbb22222"
            run_3 = "move_prediction_20260602_1504_utc_ccc33333"

            with MovePredictionDuckDBStore(database_path=database_path) as store:
                for run_id, created_at_utc, rows in [
                    (
                        run_1,
                        datetime(2026, 6, 1, 14, 19, tzinfo=timezone.utc),
                        [
                            [
                                "NASDAQ:AAA",
                                "Alpha Holdings",
                                "AAA",
                                "NASDAQ",
                                "United States",
                                "Technology",
                                "Software",
                                "america",
                                "breakout_long",
                                "accumulate",
                                1_500_000_000,
                                100,
                                3,
                                4,
                                5,
                                10,
                                12,
                                30,
                                1.20,
                                "Up",
                                90,
                                1.0,
                                "opening strength",
                                1.10,
                                "medium",
                                1.00,
                                "Up",
                                88,
                                1.0,
                                "opening strength",
                                0.95,
                                "medium",
                                0.70,
                                "Up",
                                85,
                                1.0,
                                "opening strength",
                                0.68,
                                "medium",
                                0.40,
                                "Up",
                                80,
                                1.0,
                                "opening strength",
                                0.38,
                                "medium",
                            ],
                            [
                                "NASDAQ:BBB",
                                "Beta Industries",
                                "BBB",
                                "NASDAQ",
                                "United States",
                                "Technology",
                                "Hardware",
                                "america",
                                "breakout_long",
                                "watch",
                                1_200_000_000,
                                20,
                                1,
                                2,
                                3,
                                5,
                                7,
                                10,
                                0.80,
                                "Neutral",
                                80,
                                1.0,
                                "coiling",
                                0.75,
                                "medium",
                                0.90,
                                "Up",
                                81,
                                1.0,
                                "coiling",
                                0.83,
                                "medium",
                                0.60,
                                "Up",
                                79,
                                1.0,
                                "coiling",
                                0.58,
                                "medium",
                                0.30,
                                "Neutral",
                                75,
                                1.0,
                                "coiling",
                                0.28,
                                "medium",
                            ],
                        ],
                    ),
                    (
                        run_2,
                        datetime(2026, 6, 1, 20, 58, tzinfo=timezone.utc),
                        [
                            [
                                "NASDAQ:AAA",
                                "Alpha Holdings",
                                "AAA",
                                "NASDAQ",
                                "United States",
                                "Technology",
                                "Software",
                                "america",
                                "breakout_long",
                                "accumulate",
                                1_550_000_000,
                                102,
                                4,
                                5,
                                7,
                                12,
                                14,
                                32,
                                1.00,
                                "Up",
                                88,
                                1.0,
                                "holding gains",
                                0.94,
                                "medium",
                                1.05,
                                "Up",
                                87,
                                1.0,
                                "holding gains",
                                0.98,
                                "medium",
                                0.75,
                                "Up",
                                84,
                                1.0,
                                "holding gains",
                                0.72,
                                "medium",
                                0.45,
                                "Up",
                                79,
                                1.0,
                                "holding gains",
                                0.42,
                                "medium",
                            ],
                            [
                                "NASDAQ:BBB",
                                "Beta Industries",
                                "BBB",
                                "NASDAQ",
                                "United States",
                                "Technology",
                                "Hardware",
                                "america",
                                "breakout_long",
                                "accumulate",
                                1_250_000_000,
                                22,
                                3,
                                6,
                                9,
                                11,
                                13,
                                16,
                                1.40,
                                "Strong Up",
                                93,
                                1.0,
                                "closing breakout",
                                1.32,
                                "high",
                                1.20,
                                "Strong Up",
                                90,
                                1.0,
                                "closing breakout",
                                1.12,
                                "high",
                                0.85,
                                "Up",
                                86,
                                1.0,
                                "closing breakout",
                                0.81,
                                "high",
                                0.50,
                                "Up",
                                82,
                                1.0,
                                "closing breakout",
                                0.47,
                                "high",
                            ],
                        ],
                    ),
                    (
                        run_3,
                        datetime(2026, 6, 2, 15, 4, tzinfo=timezone.utc),
                        [
                            [
                                "NASDAQ:AAA",
                                "Alpha Holdings",
                                "AAA",
                                "NASDAQ",
                                "United States",
                                "Technology",
                                "Software",
                                "america",
                                "breakout_long",
                                "accumulate",
                                1_580_000_000,
                                108,
                                6,
                                8,
                                12,
                                18,
                                20,
                                36,
                                1.60,
                                "Strong Up",
                                95,
                                1.0,
                                "follow-through",
                                1.50,
                                "high",
                                1.30,
                                "Strong Up",
                                91,
                                1.0,
                                "follow-through",
                                1.21,
                                "high",
                                0.95,
                                "Up",
                                89,
                                1.0,
                                "follow-through",
                                0.91,
                                "high",
                                0.60,
                                "Up",
                                84,
                                1.0,
                                "follow-through",
                                0.56,
                                "high",
                            ],
                            [
                                "NASDAQ:BBB",
                                "Beta Industries",
                                "BBB",
                                "NASDAQ",
                                "United States",
                                "Technology",
                                "Hardware",
                                "america",
                                "breakout_long",
                                "accumulate",
                                1_210_000_000,
                                24,
                                2,
                                4,
                                8,
                                10,
                                11,
                                15,
                                1.10,
                                "Up",
                                86,
                                1.0,
                                "cooling but positive",
                                1.02,
                                "medium",
                                1.00,
                                "Up",
                                85,
                                1.0,
                                "cooling but positive",
                                0.94,
                                "medium",
                                0.80,
                                "Up",
                                83,
                                1.0,
                                "cooling but positive",
                                0.76,
                                "medium",
                                0.55,
                                "Up",
                                80,
                                1.0,
                                "cooling but positive",
                                0.51,
                                "medium",
                            ],
                        ],
                    ),
                ]:
                    store.register_run(
                        run_id=run_id,
                        created_at_utc=created_at_utc,
                        suite_name="tradingview_move_prediction_full_analysis_duckdb",
                        scan_data_count=len(rows),
                        profile_names=["breakout_long"],
                        industries=None,
                        min_market_cap_usd=1_000_000_000,
                        max_market_cap_usd=None,
                        include_blind_spot_sections=False,
                    )
                    store.append_tabular_output(
                        "profile_prediction_rows",
                        CSV_HEADERS,
                        _rows_to_csv_values(rows),
                        context={"run_id": run_id, "profile_name": "breakout_long"},
                    )

            input_paths = build_move_prediction_history_duckdb_inputs_from_week_folders(
                base_dir=iso_year_root,
                folder_names=["22"],
            )

            result = run_move_prediction_history_aggregation_duckdb(
                input_paths=input_paths,
                output_dir=output_root,
                include_profiles=["breakout_long"],
                write_legacy_csv_outputs=False,
                export_parquet=False,
            )

            profile_result = result["profiles"]["breakout_long"]
            history_rows = query_move_prediction_duckdb(
                result["analysis_database"],
                f"""
                SELECT symbol,
                    snapshot_label,
                    days_rank,
                    days_rank_improvement_vs_previous,
                    days_score_delta_vs_previous,
                    close_return_pct_vs_previous_snapshot
                FROM {profile_result['history_table']}
                WHERE analysis_run_id = ?
                ORDER BY row_number
                """,
                [result["analysis_run_id"]],
            )
            summary_rows = query_move_prediction_duckdb(
                result["analysis_database"],
                f"""
                SELECT symbol,
                    snapshots_seen,
                    days_best_rank,
                    days_last_score,
                    "Perf.W_last" AS perf_w_last,
                    close_return_pct_total
                FROM {profile_result['summary_table']}
                WHERE analysis_run_id = ?
                ORDER BY row_number
                """,
                [result["analysis_run_id"]],
            )
            manifest_rows = query_move_prediction_duckdb(
                result["analysis_database"],
                f"""
                SELECT snapshot_label
                FROM {result['manifest_table']}
                WHERE analysis_run_id = ?
                ORDER BY row_number
                """,
                [result["analysis_run_id"]],
            )

            self.assertTrue(result["analysis_database"].exists())
            self.assertTrue(str(result["output_dir"]).startswith(str(output_root)))
            self.assertEqual("duckdb", result["output_mode"])
            self.assertNotIn("manifest_csv", result)
            self.assertFalse(any(result["output_dir"].glob("*.csv")))
            self.assertEqual(6, len(history_rows))
            self.assertEqual(2, len(summary_rows))
            self.assertEqual(3, len(manifest_rows))
            self.assertEqual(
                [
                    "2026-06-01 run_1419_utc_aaa11111",
                    "2026-06-01 run_2058_utc_bbb22222",
                    "2026-06-02 run_1504_utc_ccc33333",
                ],
                [str(row["snapshot_label"]) for row in manifest_rows],
            )

            bbb_history = [row for row in history_rows if row["symbol"] == "NASDAQ:BBB"]
            self.assertEqual(
                [
                    "2026-06-01 run_1419_utc_aaa11111",
                    "2026-06-01 run_2058_utc_bbb22222",
                    "2026-06-02 run_1504_utc_ccc33333",
                ],
                [row["snapshot_label"] for row in bbb_history],
            )
            self.assertEqual(2, bbb_history[0]["days_rank"])
            self.assertEqual(1, bbb_history[1]["days_rank"])
            self.assertEqual(1, bbb_history[1]["days_rank_improvement_vs_previous"])
            self.assertAlmostEqual(
                0.6,
                float(bbb_history[1]["days_score_delta_vs_previous"]),
                places=6,
            )
            self.assertAlmostEqual(
                (24 - 22) / 22 * 100,
                float(bbb_history[2]["close_return_pct_vs_previous_snapshot"]),
                places=6,
            )

            aaa_summary = next(
                row for row in summary_rows if row["symbol"] == "NASDAQ:AAA"
            )
            self.assertEqual(3, aaa_summary["snapshots_seen"])
            self.assertEqual(1, aaa_summary["days_best_rank"])
            self.assertAlmostEqual(1.6, float(aaa_summary["days_last_score"]), places=6)
            self.assertAlmostEqual(8.0, float(aaa_summary["perf_w_last"]), places=6)
            self.assertAlmostEqual(
                8.0, float(aaa_summary["close_return_pct_total"]), places=6
            )

            input_run_count = query_move_prediction_duckdb(
                result["analysis_database"],
                "SELECT COUNT(*) AS row_count FROM historical_analysis_input_runs WHERE analysis_run_id = ?",
                [result["analysis_run_id"]],
            )[0]["row_count"]
            self.assertEqual(3, input_run_count)

            all_history_count = query_move_prediction_duckdb(
                result["analysis_database"],
                "SELECT COUNT(*) AS row_count FROM all_profiles_history WHERE analysis_run_id = ?",
                [result["analysis_run_id"]],
            )[0]["row_count"]
            self.assertEqual(6, all_history_count)

            all_summary_count = query_move_prediction_duckdb(
                result["analysis_database"],
                "SELECT COUNT(*) AS row_count FROM profile_breakout_long__summary WHERE analysis_run_id = ?",
                [result["analysis_run_id"]],
            )[0]["row_count"]
            self.assertEqual(2, all_summary_count)

            exported_tables = set(result["analysis_tables"]["exported_tables"])
            self.assertIn("all_profiles_history", exported_tables)
            self.assertIn("profile_breakout_long__summary", exported_tables)
            self.assertIn("all_profiles_score_progression__days", exported_tables)

            self.assertEqual(
                set(HISTORICAL_ANALYSIS_VIEW_NAMES),
                set(result["analysis_views"]),
            )

            analysis_views = {
                row["table_name"]
                for row in query_move_prediction_duckdb(
                    result["analysis_database"],
                    """
                    SELECT table_name
                    FROM information_schema.views
                    WHERE table_schema = 'main'
                        AND table_name LIKE 'vw_%'
                    ORDER BY table_name
                    """,
                )
            }
            self.assertEqual(set(HISTORICAL_ANALYSIS_VIEW_NAMES), analysis_views)

            alignment_rows = query_move_prediction_duckdb(
                result["analysis_database"],
                """
                SELECT symbol_count,
                    aligned_positive_count,
                    false_positive_count
                FROM vw_profile_horizon_alignment_stats
                WHERE analysis_run_id = ?
                    AND profile_name = 'breakout_long'
                    AND horizon_name = 'days'
                """,
                [result["analysis_run_id"]],
            )
            self.assertEqual(1, len(alignment_rows))
            self.assertEqual(2, alignment_rows[0]["symbol_count"])
            self.assertEqual(2, alignment_rows[0]["aligned_positive_count"])
            self.assertEqual(0, alignment_rows[0]["false_positive_count"])

            top_day_leader = query_move_prediction_duckdb(
                result["analysis_database"],
                """
                SELECT symbol
                FROM vw_profile_horizon_current_leaders
                WHERE analysis_run_id = ?
                    AND profile_name = 'breakout_long'
                    AND horizon_name = 'days'
                    AND leader_rank = 1
                """,
                [result["analysis_run_id"]],
            )
            self.assertEqual("NASDAQ:AAA", top_day_leader[0]["symbol"])

            snapshot_delta_count = query_move_prediction_duckdb(
                result["analysis_database"],
                """
                SELECT COUNT(*) AS row_count
                FROM vw_profile_horizon_snapshot_deltas
                WHERE analysis_run_id = ?
                """,
                [result["analysis_run_id"]],
            )[0]["row_count"]
            self.assertEqual(24, snapshot_delta_count)


if __name__ == "__main__":
    unittest.main()
