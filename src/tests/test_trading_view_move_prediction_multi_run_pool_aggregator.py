import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_move_prediction_multi_run_pool_aggregator import (
    aggregate_move_prediction_run_pool,
    aggregate_move_prediction_run_pool_in_chunks,
)
from db.trading_view_move_prediction_duckdb import (
    MovePredictionDuckDBStore,
    open_move_prediction_duckdb_connection,
)


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _build_sample_weekly_database(
    *,
    temp_root: Path,
    week_name: str,
    database_name: str,
    run_id: str,
    created_at_utc: datetime,
) -> Path:
    iso_year_root = temp_root / "duckdb_runs" / "iso_year=2026"
    week_dir = iso_year_root / week_name
    week_dir.mkdir(parents=True, exist_ok=True)
    database_path = week_dir / database_name

    with MovePredictionDuckDBStore(database_path=database_path) as store:
        store.register_run(
            run_id=run_id,
            created_at_utc=created_at_utc,
            suite_name="tradingview_move_prediction_full_analysis_duckdb",
            scan_data_count=2,
            profile_names=["breakout_long"],
            industries=["Technology"],
            min_market_cap_usd=1_000_000_000,
            max_market_cap_usd=None,
            include_blind_spot_sections=False,
        )
        store.append_tabular_output(
            "raw_scan_rows",
            ["symbol", "Company", "relative_volume_10d_calc", "close"],
            [
                ["NASDAQ:AAA", "Alpha", 1.4, 100.0],
                ["NASDAQ:BBB", "Beta", 0.8, 50.0],
            ],
            context={"run_id": run_id},
        )
        store.append_profile_horizon_scores(
            [
                {
                    "run_id": run_id,
                    "profile_name": "breakout_long",
                    "row_number": 1,
                    "symbol": "AAA",
                    "company": "Alpha",
                    "sector": "Technology",
                    "industry": "Software",
                    "market_cap_basic": 1_500_000_000,
                    "close": 100.0,
                    "horizon_name": "weeks",
                    "score": 1.1,
                    "direction": "Up",
                    "confidence": 90.0,
                    "coverage": 1.0,
                    "setup": "opening strength",
                    "risk_adjusted_score": 1.0,
                    "risk_tier": "medium",
                    "manager_action_signal": "accumulate",
                },
                {
                    "run_id": run_id,
                    "profile_name": "breakout_long",
                    "row_number": 2,
                    "symbol": "BBB",
                    "company": "Beta",
                    "sector": "Technology",
                    "industry": "Software",
                    "market_cap_basic": 900_000_000,
                    "close": 50.0,
                    "horizon_name": "weeks",
                    "score": 0.2,
                    "direction": "Down",
                    "confidence": 70.0,
                    "coverage": 0.8,
                    "setup": "weak tape",
                    "risk_adjusted_score": 0.1,
                    "risk_tier": "high",
                    "manager_action_signal": "avoid",
                },
            ]
        )

    return database_path


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestMovePredictionMultiRunPoolAggregator(unittest.TestCase):
    def test_pools_scores_and_raw_fields_across_weekly_databases(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            week_22_db = _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=22",
                database_name="move_prediction_2026_W22.duckdb",
                run_id="move_prediction_20260601_1419_utc_aaa11111",
                created_at_utc=datetime(2026, 6, 1, 14, 19, tzinfo=timezone.utc),
            )
            week_23_db = _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=23",
                database_name="move_prediction_2026_W23.duckdb",
                run_id="move_prediction_20260608_1419_utc_bbb22222",
                created_at_utc=datetime(2026, 6, 8, 14, 19, tzinfo=timezone.utc),
            )

            result = aggregate_move_prediction_run_pool(
                [week_22_db, week_23_db],
                output_dir=temp_root / "multi_run_pool",
                show_progress=False,
            )

            with open_move_prediction_duckdb_connection(
                result["database_path"],
                read_only=True,
            ) as conn:
                raw_count = conn.execute("SELECT COUNT(*) FROM pool_raw_scan_rows").fetchone()[0]
                score_count = conn.execute(
                    "SELECT COUNT(*) FROM pool_profile_horizon_scores"
                ).fetchone()[0]
                joined_count = conn.execute(
                    "SELECT COUNT(*) FROM vw_pool_profile_scores_with_raw"
                ).fetchone()[0]
                source_count = conn.execute(
                    "SELECT COUNT(*) FROM pool_source_databases"
                ).fetchone()[0]

            self.assertEqual(result["input_database_count"], 2)
            self.assertEqual(result["source_run_count"], 2)
            self.assertEqual(raw_count, 4)
            self.assertEqual(score_count, 4)
            self.assertEqual(joined_count, 4)
            self.assertEqual(source_count, 2)

    def test_chunk_merge_produces_consolidated_pool(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            week_22_db = _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=22",
                database_name="move_prediction_2026_W22.duckdb",
                run_id="move_prediction_20260601_1419_utc_aaa11111",
                created_at_utc=datetime(2026, 6, 1, 14, 19, tzinfo=timezone.utc),
            )
            week_23_db = _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=23",
                database_name="move_prediction_2026_W23.duckdb",
                run_id="move_prediction_20260608_1419_utc_bbb22222",
                created_at_utc=datetime(2026, 6, 8, 14, 19, tzinfo=timezone.utc),
            )

            chunk_result = aggregate_move_prediction_run_pool_in_chunks(
                [week_22_db, week_23_db],
                chunk_size=1,
                output_dir=temp_root / "multi_run_pool",
                merge_chunks=True,
                attach_batch_size=1,
                show_progress=False,
            )
            merged = chunk_result["merged_result"]
            self.assertIsNotNone(merged)

            with open_move_prediction_duckdb_connection(
                merged["database_path"],
                read_only=True,
            ) as conn:
                raw_count = conn.execute("SELECT COUNT(*) FROM pool_raw_scan_rows").fetchone()[0]
                score_count = conn.execute(
                    "SELECT COUNT(*) FROM pool_profile_horizon_scores"
                ).fetchone()[0]

            self.assertEqual(raw_count, 4)
            self.assertEqual(score_count, 4)


if __name__ == "__main__":
    unittest.main()
