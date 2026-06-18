import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
    AnchorSpec,
    resolve_anchor_specs,
    run_backwards_prediction_analysis,
)
from db.trading_view_backwards_prediction_duckdb import (
    query_backwards_prediction_duckdb,
)
from db.trading_view_move_prediction_duckdb import (
    MovePredictionDuckDBStore,
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
    aaa_score: float,
    bbb_score: float,
    aaa_close: float,
    bbb_close: float,
    include_consensus: bool = True,
    include_components: bool = True,
    extra_symbol: str | None = None,
    profile_name: str = "breakout_long",
) -> Path:
    iso_year_root = temp_root / "duckdb_runs" / "iso_year=2026"
    week_dir = iso_year_root / week_name
    week_dir.mkdir(parents=True, exist_ok=True)
    database_path = week_dir / database_name

    raw_rows = [
        ["NASDAQ:AAA", "Alpha", 1.4, aaa_close, 2.5, 3.0, 4.0, 5.0],
        ["NASDAQ:BBB", "Beta", 0.8, bbb_close, -1.0, -0.5, -2.0, -3.0],
    ]
    if extra_symbol == "CCC":
        raw_rows.append(["NASDAQ:CCC", "Gamma", 1.0, 25.0, 0.5, 0.5, 0.5, 0.5])

    horizon_rows = [
        {
            "run_id": run_id,
            "profile_name": profile_name,
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
            "profile_name": profile_name,
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
    ]
    if extra_symbol == "CCC":
        horizon_rows.append(
            {
                "run_id": run_id,
                "profile_name": profile_name,
                "row_number": 3,
                "symbol": "NASDAQ:CCC",
                "company": "Gamma",
                "sector": "Technology",
                "industry": "Software",
                "market_cap_basic": 500_000_000,
                "close": 25.0,
                "horizon_name": "weeks",
                "score": 0.5,
                "direction": "Neutral",
                "confidence": 60.0,
                "coverage": 0.7,
                "setup": "range bound",
                "risk_adjusted_score": 0.4,
                "risk_tier": "medium",
                "manager_action_signal": "watch",
            }
        )

    with MovePredictionDuckDBStore(database_path=database_path) as store:
        store.register_run(
            run_id=run_id,
            created_at_utc=created_at_utc,
            suite_name="tradingview_move_prediction_full_analysis_duckdb",
            scan_data_count=3000,
            profile_names=[profile_name],
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
            raw_rows,
            context={"run_id": run_id},
        )
        store.append_profile_horizon_scores(horizon_rows)

        if include_components:
            store.append_profile_components(
                [
                    {
                        "run_id": run_id,
                        "profile_name": profile_name,
                        "row_number": 1,
                        "symbol": "NASDAQ:AAA",
                        "company": "Alpha",
                        "sector": "Technology",
                        "industry": "Software",
                        "market_cap_basic": 1_500_000_000,
                        "close": aaa_close,
                        "manager_action_signal": "accumulate",
                        "attention": 1.0,
                        "event": 0.5,
                        "momentum": 0.8,
                        "trend": 0.6,
                        "quality": 0.4,
                        "valuation": 0.2,
                        "safety": 0.3,
                        "scale": 0.1,
                    },
                    {
                        "run_id": run_id,
                        "profile_name": profile_name,
                        "row_number": 2,
                        "symbol": "NASDAQ:BBB",
                        "company": "Beta",
                        "sector": "Technology",
                        "industry": "Software",
                        "market_cap_basic": 900_000_000,
                        "close": bbb_close,
                        "manager_action_signal": "avoid",
                        "attention": -0.2,
                        "event": -0.1,
                        "momentum": -0.3,
                        "trend": -0.4,
                        "quality": 0.1,
                        "valuation": 0.0,
                        "safety": -0.2,
                        "scale": 0.0,
                    },
                ]
            )

        if include_consensus:
            store.append_records(
                "consensus_horizon_scores",
                [
                    {
                        "run_id": run_id,
                        "row_number": 1,
                        "symbol": "NASDAQ:AAA",
                        "company": "Alpha",
                        "sector": "Technology",
                        "industry": "Software",
                        "market_cap": 1_500_000_000,
                        "horizon_name": "weeks",
                        "score": aaa_score,
                        "direction": "Up",
                        "confidence": 88.0,
                        "agreement_ratio": 0.8,
                        "opinions": 3,
                        "risk_adjusted_score": aaa_score - 0.05,
                        "risk_tier": "medium",
                        "manager_action_signal": "accumulate",
                    },
                    {
                        "run_id": run_id,
                        "row_number": 2,
                        "symbol": "NASDAQ:BBB",
                        "company": "Beta",
                        "sector": "Technology",
                        "industry": "Software",
                        "market_cap": 900_000_000,
                        "horizon_name": "weeks",
                        "score": bbb_score,
                        "direction": "Down",
                        "confidence": 65.0,
                        "agreement_ratio": 0.6,
                        "opinions": 3,
                        "risk_adjusted_score": bbb_score - 0.05,
                        "risk_tier": "high",
                        "manager_action_signal": "avoid",
                    },
                ],
                [
                    ("run_id", "VARCHAR"),
                    ("row_number", "BIGINT"),
                    ("symbol", "VARCHAR"),
                    ("company", "VARCHAR"),
                    ("sector", "VARCHAR"),
                    ("industry", "VARCHAR"),
                    ("market_cap", "DOUBLE"),
                    ("horizon_name", "VARCHAR"),
                    ("score", "DOUBLE"),
                    ("direction", "VARCHAR"),
                    ("confidence", "DOUBLE"),
                    ("agreement_ratio", "DOUBLE"),
                    ("opinions", "BIGINT"),
                    ("risk_adjusted_score", "DOUBLE"),
                    ("risk_tier", "VARCHAR"),
                    ("manager_action_signal", "VARCHAR"),
                ],
            )

    return database_path


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestBackwardsPredictionAnalysis(unittest.TestCase):
    def test_anchor_resolution_and_score_deltas(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            current_time = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
            yesterday_time = current_time - timedelta(days=1)
            week_ago_time = current_time - timedelta(days=7)
            oldest_time = datetime(2026, 5, 20, 14, 0, tzinfo=timezone.utc)

            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=20",
                database_name="move_prediction_2026_W20.duckdb",
                run_id="move_prediction_20260520_1400_utc_oldest01",
                created_at_utc=oldest_time,
                aaa_score=0.5,
                bbb_score=-0.2,
                aaa_close=80.0,
                bbb_close=40.0,
            )
            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=23",
                database_name="move_prediction_2026_W23.duckdb",
                run_id="move_prediction_20260608_1400_utc_weekago1",
                created_at_utc=week_ago_time,
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
                created_at_utc=yesterday_time,
                aaa_score=1.0,
                bbb_score=0.15,
                aaa_close=98.0,
                bbb_close=49.0,
            )
            current_db = _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=25",
                database_name="move_prediction_2026_W25.duckdb",
                run_id="move_prediction_20260615_1400_utc_current1",
                created_at_utc=current_time,
                aaa_score=1.2,
                bbb_score=0.2,
                aaa_close=100.0,
                bbb_close=50.0,
                extra_symbol="CCC",
            )

            result = run_backwards_prediction_analysis(
                current_run_id="move_prediction_20260615_1400_utc_current1",
                duckdb_runs_root=temp_root / "duckdb_runs",
                anchors=[
                    AnchorSpec.preset("yesterday"),
                    AnchorSpec.preset("last_week"),
                    AnchorSpec.preset("oldest"),
                ],
                output_dir=temp_root / "backwards_output",
                include_consensus=True,
                include_components=True,
            )

            self.assertEqual(result["current_run_id"], "move_prediction_20260615_1400_utc_current1")
            self.assertEqual(len(result["resolved_anchors"]), 3)

            yesterday_delta = query_backwards_prediction_duckdb(
                result["database_path"],
                """
                SELECT symbol, score_delta, close_delta_pct, in_current, in_anchor
                FROM backwards_profile_horizon_deltas
                WHERE anchor_name = 'yesterday'
                  AND symbol = 'NASDAQ:AAA'
                  AND horizon_name = 'weeks'
                """,
            )[0]
            self.assertAlmostEqual(yesterday_delta["score_delta"], 0.2)
            self.assertAlmostEqual(yesterday_delta["close_delta_pct"], 100.0 * (2.0 / 98.0))
            self.assertTrue(yesterday_delta["in_current"])
            self.assertTrue(yesterday_delta["in_anchor"])

            week_ago_delta = query_backwards_prediction_duckdb(
                result["database_path"],
                """
                SELECT score_delta
                FROM backwards_profile_horizon_deltas
                WHERE anchor_name = 'last_week'
                  AND symbol = 'NASDAQ:AAA'
                  AND horizon_name = 'weeks'
                """,
            )[0]
            self.assertAlmostEqual(week_ago_delta["score_delta"], 0.3)

            drift = query_backwards_prediction_duckdb(
                result["database_path"],
                """
                SELECT symbols_added, symbols_dropped, symbols_retained
                FROM vw_backwards_universe_drift
                WHERE anchor_name = 'yesterday'
                  AND profile_name = 'breakout_long'
                  AND horizon_name = 'weeks'
                """,
            )[0]
            self.assertEqual(drift["symbols_added"], 1)
            self.assertEqual(drift["symbols_dropped"], 0)
            self.assertEqual(drift["symbols_retained"], 2)

            component_delta = query_backwards_prediction_duckdb(
                result["database_path"],
                """
                SELECT momentum_delta
                FROM backwards_profile_component_deltas
                WHERE anchor_name = 'yesterday'
                  AND symbol = 'NASDAQ:AAA'
                """,
            )[0]
            self.assertIsNotNone(component_delta["momentum_delta"])

            movers = query_backwards_prediction_duckdb(
                result["database_path"],
                """
                SELECT COUNT(*) AS row_count
                FROM vw_backwards_top_score_movers
                WHERE anchor_name = 'yesterday'
                """,
            )[0]
            self.assertGreater(int(movers["row_count"]), 0)

            self.assertTrue(Path(result["overview_log"]).exists())
            self.assertEqual(current_db.name, "move_prediction_2026_W25.duckdb")

    def test_resolve_anchor_specs_with_runs_back_and_explicit_run_id(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            current_time = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
            runs = [
                ("move_prediction_20260601_1200_utc_run00001", current_time - timedelta(days=9)),
                ("move_prediction_20260605_1200_utc_run00002", current_time - timedelta(days=5)),
                ("move_prediction_20260609_1200_utc_run00003", current_time - timedelta(days=1)),
                ("move_prediction_20260610_1200_utc_run00004", current_time),
            ]
            for index, (run_id, created_at) in enumerate(runs):
                _build_sample_weekly_database(
                    temp_root=temp_root,
                    week_name=f"week={20 + index}",
                    database_name=f"move_prediction_2026_W{20 + index}.duckdb",
                    run_id=run_id,
                    created_at_utc=created_at,
                    aaa_score=1.0,
                    bbb_score=0.5,
                    aaa_close=100.0,
                    bbb_close=50.0,
                    include_consensus=False,
                    include_components=False,
                )

            from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
                _build_run_index,
                _resolve_current_run,
            )

            run_index = _build_run_index(duckdb_runs_root=temp_root / "duckdb_runs")
            current = _resolve_current_run(
                run_index=run_index,
                current_run_id="move_prediction_20260610_1200_utc_run00004",
                current_database_path=None,
            )
            resolved = resolve_anchor_specs(
                run_index=run_index,
                current=current,
                anchors=[
                    AnchorSpec(name="previous", runs_back=1),
                    AnchorSpec(
                        name="explicit",
                        run_id="move_prediction_20260601_1200_utc_run00001",
                    ),
                ],
            )
            self.assertEqual(resolved[0].entry.run_id, "move_prediction_20260609_1200_utc_run00003")
            self.assertEqual(resolved[1].entry.run_id, "move_prediction_20260601_1200_utc_run00001")

    def test_profile_family_matches_versioned_current_to_unversioned_anchor(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            current_time = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)

            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=24",
                database_name="move_prediction_2026_W24.duckdb",
                run_id="move_prediction_20260614_1400_utc_weekago1",
                created_at_utc=current_time - timedelta(days=7),
                aaa_score=0.9,
                bbb_score=0.1,
                aaa_close=95.0,
                bbb_close=48.0,
                profile_name="breakout_long",
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
                profile_name="breakout_long_v1",
            )

            result = run_backwards_prediction_analysis(
                current_run_id="move_prediction_20260615_1400_utc_current1",
                duckdb_runs_root=temp_root / "duckdb_runs",
                anchors=[AnchorSpec.preset("last_week")],
                output_dir=temp_root / "backwards_output",
                include_profiles=["breakout_long_v1"],
                include_consensus=False,
                include_components=False,
            )

            week_delta = query_backwards_prediction_duckdb(
                result["database_path"],
                """
                SELECT score_delta, anchor_profile_name, profile_version_exact_match, in_anchor
                FROM backwards_profile_horizon_deltas
                WHERE anchor_name = 'last_week'
                  AND symbol = 'NASDAQ:AAA'
                  AND profile_name = 'breakout_long_v1'
                  AND horizon_name = 'weeks'
                """,
            )[0]
            self.assertAlmostEqual(week_delta["score_delta"], 0.3)
            self.assertEqual(week_delta["anchor_profile_name"], "breakout_long")
            self.assertFalse(week_delta["profile_version_exact_match"])
            self.assertTrue(week_delta["in_anchor"])

            overview_text = Path(result["overview_log"]).read_text(encoding="utf-8")
            self.assertIn("Profile family matching", overview_text)
            self.assertIn("family_fallback", overview_text)


if __name__ == "__main__":
    unittest.main()
