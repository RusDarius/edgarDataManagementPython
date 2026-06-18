import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
    AnchorSpec,
    run_backwards_prediction_analysis,
)
from data_analysis_scripts.trading_view_backwards_prediction_scout_report import (
    resolve_backwards_analysis_database,
    run_backwards_prediction_scout_report,
)
from db.trading_view_move_prediction_duckdb import MovePredictionDuckDBStore


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
    aaa_score: float,
    bbb_score: float,
    aaa_close: float,
    bbb_close: float,
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
            scan_data_count=3000,
            profile_names=["breakout_long"],
            industries=["Technology"],
            min_market_cap_usd=1_000_000_000,
            max_market_cap_usd=None,
            include_blind_spot_sections=False,
        )
        store.append_tabular_output(
            "raw_scan_rows",
            [
                "symbol",
                "Company",
                "relative_volume_10d_calc",
                "close",
                "Perf.5D",
                "Perf.W",
                "Perf.1M",
                "Perf.YTD",
            ],
            [
                ["NASDAQ:AAA", "Alpha", 1.4, aaa_close, 2.5, 3.0, 4.0, 5.0],
                ["NASDAQ:BBB", "Beta", 0.8, bbb_close, -1.0, -0.5, -2.0, -3.0],
                ["NASDAQ:CCC", "Gamma", 1.0, 25.0, 0.5, 0.5, 0.5, 0.5],
            ],
            context={"run_id": run_id},
        )
        store.append_profile_horizon_scores(
            [
                {
                    "run_id": run_id,
                    "profile_name": "breakout_long",
                    "row_number": 1,
                    "symbol": "NASDAQ:AAA",
                    "company": "Alpha",
                    "sector": "Technology",
                    "industry": "Software",
                    "market_cap_basic": 1_500_000_000,
                    "close": aaa_close,
                    "horizon_name": "weeks",
                    "score": aaa_score,
                    "direction": "Up",
                    "confidence": 90.0,
                    "coverage": 1.0,
                    "setup": "opening strength",
                    "risk_adjusted_score": aaa_score - 0.1,
                    "risk_tier": "medium",
                    "manager_action_signal": "accumulate",
                },
                {
                    "run_id": run_id,
                    "profile_name": "breakout_long",
                    "row_number": 2,
                    "symbol": "NASDAQ:BBB",
                    "company": "Beta",
                    "sector": "Technology",
                    "industry": "Software",
                    "market_cap_basic": 900_000_000,
                    "close": bbb_close,
                    "horizon_name": "weeks",
                    "score": bbb_score,
                    "direction": "Down",
                    "confidence": 70.0,
                    "coverage": 0.8,
                    "setup": "weak tape",
                    "risk_adjusted_score": bbb_score - 0.1,
                    "risk_tier": "high",
                    "manager_action_signal": "avoid",
                },
                {
                    "run_id": run_id,
                    "profile_name": "breakout_long",
                    "row_number": 3,
                    "symbol": "NASDAQ:CCC",
                    "company": "Gamma",
                    "sector": "Technology",
                    "industry": "Software",
                    "market_cap_basic": 500_000_000,
                    "close": 25.0,
                    "horizon_name": "weeks",
                    "score": 0.4,
                    "direction": "Neutral",
                    "confidence": 60.0,
                    "coverage": 0.7,
                    "setup": "range bound",
                    "risk_adjusted_score": 0.3,
                    "risk_tier": "medium",
                    "manager_action_signal": "watch",
                },
            ]
        )

    return database_path


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestBackwardsPredictionScoutReport(unittest.TestCase):
    def test_scout_report_resolves_run_and_writes_highlights(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            current_time = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)

            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=23",
                database_name="move_prediction_2026_W23.duckdb",
                run_id="move_prediction_20260608_1400_utc_weekago1",
                created_at_utc=current_time - timedelta(days=7),
                aaa_score=0.9,
                bbb_score=0.1,
                aaa_close=95.0,
                bbb_close=48.0,
            )
            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=24",
                database_name="move_prediction_2026_W24.duckdb",
                run_id="move_prediction_20260614_1400_utc_yester01",
                created_at_utc=current_time - timedelta(days=1),
                aaa_score=1.0,
                bbb_score=0.15,
                aaa_close=98.0,
                bbb_close=49.0,
            )
            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=25",
                database_name="move_prediction_2026_W25.duckdb",
                run_id="move_prediction_20260615_1400_utc_current1",
                created_at_utc=current_time,
                aaa_score=1.2,
                bbb_score=0.2,
                aaa_close=100.0,
                bbb_close=50.0,
            )

            backwards_result = run_backwards_prediction_analysis(
                current_run_id="move_prediction_20260615_1400_utc_current1",
                duckdb_runs_root=temp_root / "duckdb_runs",
                anchors=[
                    AnchorSpec.preset("yesterday"),
                    AnchorSpec.preset("last_week"),
                ],
                output_dir=temp_root / "backwards_output",
                include_consensus=False,
                include_components=False,
            )

            resolved = resolve_backwards_analysis_database(
                run_folder_pattern=backwards_result["backwards_analysis_id"],
                backwards_runs_root=temp_root / "backwards_output" / "runs",
            )
            self.assertTrue(resolved.exists())

            scout_result = run_backwards_prediction_scout_report(
                database_path=resolved,
                profile_name="breakout_long",
                horizon_name="weeks",
                primary_anchor_name="last_week",
                top_n=10,
                min_abs_score_delta=0.1,
                min_persistence_anchors=2,
            )

            log_path = Path(scout_result["highlights_log"])
            self.assertTrue(log_path.exists())
            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn("BACKWARDS SCOUT REPORT", log_text)
            self.assertIn("ANCHOR COVERAGE FOR PROFILE", log_text)
            self.assertIn("SCORE + PRICE PROGRESSION ACROSS ANCHORS", log_text)
            self.assertIn("COMPONENT DELTA PROGRESSION", log_text)
            self.assertIn("PERSISTENT RISERS", log_text)
            self.assertIn("ANCHOR: last_week", log_text)
            self.assertNotIn("PROFILE CORRELATION OVERVIEW", log_text)

            last_week_counts = scout_result["per_anchor_counts"]["last_week"]
            self.assertGreater(last_week_counts["top_progressors"], 0)

            progressors_csv = Path(
                scout_result["exported_files"]["top_progressors__last_week"]
            )
            self.assertTrue(progressors_csv.exists())

            # AAA improves from 0.9 -> 1.2 vs last_week (delta 0.3), should rank first.
            progressors_text = progressors_csv.read_text(encoding="utf-8")
            self.assertIn("NASDAQ:AAA", progressors_text.splitlines()[1])

            self.assertGreaterEqual(scout_result["persistent_risers_count"], 1)

    def test_scout_report_supports_multiple_profile_horizon_combinations(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            current_time = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)

            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=24",
                database_name="move_prediction_2026_W24.duckdb",
                run_id="move_prediction_20260614_1400_utc_yester01",
                created_at_utc=current_time - timedelta(days=1),
                aaa_score=1.0,
                bbb_score=0.15,
                aaa_close=98.0,
                bbb_close=49.0,
            )
            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=25",
                database_name="move_prediction_2026_W25.duckdb",
                run_id="move_prediction_20260615_1400_utc_current1",
                created_at_utc=current_time,
                aaa_score=1.2,
                bbb_score=0.2,
                aaa_close=100.0,
                bbb_close=50.0,
            )

            backwards_result = run_backwards_prediction_analysis(
                current_run_id="move_prediction_20260615_1400_utc_current1",
                duckdb_runs_root=temp_root / "duckdb_runs",
                anchors=[AnchorSpec.preset("yesterday")],
                output_dir=temp_root / "backwards_output",
                include_consensus=False,
                include_components=False,
            )

            scout_result = run_backwards_prediction_scout_report(
                database_path=backwards_result["database_path"],
                profile_name="breakout_long",
                horizon_name=["weeks", "days"],
                primary_anchor_name="yesterday",
                top_n=5,
                min_abs_score_delta=0.1,
            )

            self.assertEqual(scout_result["combination_count"], 2)
            self.assertEqual(len(scout_result["results"]), 2)
            highlight_logs = {Path(item["highlights_log"]) for item in scout_result["results"]}
            self.assertEqual(len(highlight_logs), 2)
            for log_path in highlight_logs:
                self.assertTrue(log_path.exists())


if __name__ == "__main__":
    unittest.main()
