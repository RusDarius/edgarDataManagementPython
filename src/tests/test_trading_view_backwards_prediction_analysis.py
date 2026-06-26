import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
    AnchorSpec,
    BackwardsAnalysisBuildOptions,
    build_profile_family_variant_groups,
    build_weekly_sample_anchors,
    resolve_anchor_specs,
    run_backwards_prediction_analysis,
    run_backwards_prediction_sparse_weekly_analysis,
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
    scan_data_count: int = 3000,
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
            scan_data_count=scan_data_count,
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
    def test_build_profile_family_variant_groups_include_unversioned_aliases(self):
        groups = build_profile_family_variant_groups()
        breakout_variants = groups.get("breakout_long", frozenset())
        self.assertIn("breakout_long", breakout_variants)
        self.assertIn("breakout_long_v1", breakout_variants)

        value_recovery_variants = groups.get("value_recovery", frozenset())
        self.assertIn("value_recovery", value_recovery_variants)
        self.assertIn("value_recovery_v1", value_recovery_variants)
        self.assertIn("value_recovery_v2", value_recovery_variants)
        self.assertIn("value_recovery_v3", value_recovery_variants)

        forward_edge_variants = groups.get("forward_edge_active", frozenset())
        self.assertIn("forward_edge_active", forward_edge_variants)
        self.assertIn("forward_edge_active_v1", forward_edge_variants)
        self.assertIn("forward_edge_active_v2", forward_edge_variants)

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
            self.assertTrue(
                "unversioned_fallback" in overview_text or "family_fallback" in overview_text
            )

    def test_build_weekly_sample_anchors_supports_sparse_schedule(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            base_time = datetime(2026, 6, 20, 14, 0, tzinfo=timezone.utc)
            runs = [
                ("week=24", "move_prediction_20260610_1200_utc_a0010001", base_time - timedelta(days=10)),
                ("week=24", "move_prediction_20260611_1600_utc_a0010002", base_time - timedelta(days=9)),
                ("week=25", "move_prediction_20260616_1200_utc_a0010003", base_time - timedelta(days=4)),
                ("week=25", "move_prediction_20260617_1600_utc_a0010004", base_time - timedelta(days=3)),
                ("week=25", "move_prediction_20260618_1700_utc_a0010005", base_time - timedelta(days=2)),
                ("week=25", "move_prediction_20260620_1400_utc_a0010006", base_time),
            ]
            for week_name, run_id, created_at in runs:
                week_suffix = week_name.split("=", maxsplit=1)[-1]
                _build_sample_weekly_database(
                    temp_root=temp_root,
                    week_name=week_name,
                    database_name=f"move_prediction_2026_W{week_suffix}.duckdb",
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
                current_run_id="move_prediction_20260620_1400_utc_a0010006",
                current_database_path=None,
            )
            weekly_two = build_weekly_sample_anchors(
                run_index,
                current,
                runs_per_week=2,
                selection="first_last",
            )
            self.assertEqual(len(weekly_two), 4)
            self.assertEqual(weekly_two[0].run_id, "move_prediction_20260610_1200_utc_a0010001")
            self.assertEqual(weekly_two[1].run_id, "move_prediction_20260611_1600_utc_a0010002")
            self.assertEqual(weekly_two[2].run_id, "move_prediction_20260616_1200_utc_a0010003")
            self.assertEqual(weekly_two[3].run_id, "move_prediction_20260618_1700_utc_a0010005")

            weekly_one = build_weekly_sample_anchors(
                run_index,
                current,
                runs_per_week=1,
                selection="last_only",
            )
            self.assertEqual(len(weekly_one), 2)
            self.assertEqual(weekly_one[0].run_id, "move_prediction_20260611_1600_utc_a0010002")
            self.assertEqual(weekly_one[1].run_id, "move_prediction_20260618_1700_utc_a0010005")

            weekly_three = build_weekly_sample_anchors(
                run_index,
                current,
                runs_per_week=3,
                selection="even_spread",
            )
            self.assertEqual(len(weekly_three), 5)
            self.assertEqual(
                [anchor.name for anchor in weekly_three],
                [
                    "w2026_24_open",
                    "w2026_24_close",
                    "w2026_25_open",
                    "w2026_25_mid",
                    "w2026_25_close",
                ],
            )
            self.assertEqual(weekly_three[-1].run_id, "move_prediction_20260618_1700_utc_a0010005")

    def test_run_backwards_prediction_sparse_weekly_analysis(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            base_time = datetime(2026, 6, 20, 14, 0, tzinfo=timezone.utc)
            runs = [
                ("week=24", "move_prediction_20260610_1200_utc_a0010001", base_time - timedelta(days=10)),
                ("week=24", "move_prediction_20260611_1600_utc_a0010002", base_time - timedelta(days=9)),
                ("week=25", "move_prediction_20260616_1200_utc_a0010003", base_time - timedelta(days=4)),
                ("week=25", "move_prediction_20260618_1700_utc_a0010005", base_time - timedelta(days=2)),
                ("week=25", "move_prediction_20260620_1400_utc_a0010006", base_time),
            ]
            for week_name, run_id, created_at in runs:
                week_suffix = week_name.split("=", maxsplit=1)[-1]
                _build_sample_weekly_database(
                    temp_root=temp_root,
                    week_name=week_name,
                    database_name=f"move_prediction_2026_W{week_suffix}.duckdb",
                    run_id=run_id,
                    created_at_utc=created_at,
                    aaa_score=1.0,
                    bbb_score=0.5,
                    aaa_close=100.0,
                    bbb_close=50.0,
                    include_consensus=False,
                    include_components=False,
                )

            result = run_backwards_prediction_sparse_weekly_analysis(
                current_run_id="move_prediction_20260620_1400_utc_a0010006",
                duckdb_runs_root=temp_root / "duckdb_runs",
                runs_per_week=2,
                output_dir=temp_root / "backwards_output",
                include_profiles=["breakout_long_v1"],
                include_consensus=False,
                include_components=False,
            )

            self.assertEqual(result["runs_per_week"], 2)
            self.assertEqual(result["selection"], "first_last")
            self.assertEqual(result["anchor_plan_summary"]["anchor_count"], 4)
            self.assertEqual(
                result["anchor_plan_summary"]["oldest_run_id"],
                "move_prediction_20260610_1200_utc_a0010001",
            )
            self.assertEqual(len(result["resolved_anchors"]), 4)

    def test_run_folder_anchor_resolution_and_deltas_toggle(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            current_time = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)

            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=24",
                database_name="move_prediction_2026_W24.duckdb",
                run_id="move_prediction_20260614_1400_utc_anchorab",
                created_at_utc=current_time - timedelta(days=1),
                aaa_score=1.0,
                bbb_score=0.15,
                aaa_close=98.0,
                bbb_close=49.0,
                include_consensus=False,
                include_components=False,
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
                current_run_id="move_prediction_20260615_1400_utc_current1",
                current_database_path=None,
            )
            resolved = resolve_anchor_specs(
                run_index=run_index,
                current=current,
                anchors=[AnchorSpec(name="folder_anchor", run_folder="anchorab")],
            )
            self.assertEqual(len(resolved), 1)
            self.assertEqual(resolved[0].entry.run_id, "move_prediction_20260614_1400_utc_anchorab")

            result = run_backwards_prediction_analysis(
                current_run_id="move_prediction_20260615_1400_utc_current1",
                duckdb_runs_root=temp_root / "duckdb_runs",
                anchors=[AnchorSpec(name="folder_anchor", run_folder="anchorab")],
                output_dir=temp_root / "backwards_output",
                include_consensus=False,
                include_components=False,
                build_options=BackwardsAnalysisBuildOptions(
                    include_deltas=False,
                    include_consensus=False,
                    include_components=False,
                    include_snapshots=True,
                ),
            )
            deltas_count = query_backwards_prediction_duckdb(
                result["database_path"],
                """
                SELECT COUNT(*) AS row_count
                FROM backwards_profile_horizon_deltas
                """,
            )[0]
            snapshots_count = query_backwards_prediction_duckdb(
                result["database_path"],
                """
                SELECT COUNT(*) AS row_count
                FROM backwards_anchor_snapshots
                """,
            )[0]
            self.assertEqual(int(deltas_count["row_count"]), 0)
            self.assertGreater(int(snapshots_count["row_count"]), 0)

    def test_weekly_anchors_include_low_scan_backfill_week(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            april_time = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
            current_time = datetime(2026, 6, 20, 14, 0, tzinfo=timezone.utc)

            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=16",
                database_name="move_prediction_2026_W16.duckdb",
                run_id="move_prediction_20260416_1200_utc_backfill1",
                created_at_utc=april_time,
                aaa_score=0.6,
                bbb_score=0.2,
                aaa_close=85.0,
                bbb_close=45.0,
                scan_data_count=1200,
                include_consensus=False,
                include_components=False,
            )
            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=25",
                database_name="move_prediction_2026_W25.duckdb",
                run_id="move_prediction_20260620_1400_utc_current1",
                created_at_utc=current_time,
                aaa_score=1.2,
                bbb_score=0.2,
                aaa_close=100.0,
                bbb_close=50.0,
                scan_data_count=5000,
                include_consensus=False,
                include_components=False,
            )

            result = run_backwards_prediction_sparse_weekly_analysis(
                current_run_id="move_prediction_20260620_1400_utc_current1",
                duckdb_runs_root=temp_root / "duckdb_runs",
                runs_per_week=1,
                min_scan_data_count=3000,
                anchor_min_scan_data_count=None,
                output_dir=temp_root / "backwards_output",
                include_profiles=["breakout_long_v1"],
                include_consensus=False,
                include_components=False,
            )

            self.assertEqual(
                result["anchor_plan_summary"]["oldest_run_id"],
                "move_prediction_20260416_1200_utc_backfill1",
            )
            anchor_names = {
                anchor["anchor_name"]
                for anchor in result["resolved_anchors"]
            }
            self.assertIn("w2026_16_only", anchor_names)

    def test_unversioned_anchor_profiles_emit_snapshots_for_versioned_current(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            current_time = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)

            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=24",
                database_name="move_prediction_2026_W24.duckdb",
                run_id="move_prediction_20260608_1400_utc_anchor01",
                created_at_utc=current_time - timedelta(days=7),
                aaa_score=0.7,
                bbb_score=0.1,
                aaa_close=92.0,
                bbb_close=47.0,
                profile_name="durable_value_compounder",
                include_consensus=False,
                include_components=False,
            )
            _build_sample_weekly_database(
                temp_root=temp_root,
                week_name="week=25",
                database_name="move_prediction_2026_W25.duckdb",
                run_id="move_prediction_20260615_1400_utc_current1",
                created_at_utc=current_time,
                aaa_score=1.0,
                bbb_score=0.2,
                aaa_close=100.0,
                bbb_close=50.0,
                profile_name="durable_value_compounder_v1",
                include_consensus=False,
                include_components=False,
            )

            result = run_backwards_prediction_analysis(
                current_run_id="move_prediction_20260615_1400_utc_current1",
                duckdb_runs_root=temp_root / "duckdb_runs",
                anchors=[AnchorSpec.preset("last_week")],
                output_dir=temp_root / "backwards_output",
                include_profiles=["durable_value_compounder_v1"],
                include_consensus=False,
                include_components=False,
            )

            snapshot = query_backwards_prediction_duckdb(
                result["database_path"],
                """
                SELECT score, anchor_profile_name, profile_name
                FROM backwards_anchor_snapshots
                WHERE anchor_name = 'last_week'
                  AND symbol = 'NASDAQ:AAA'
                  AND profile_name = 'durable_value_compounder_v1'
                  AND horizon_name = 'weeks'
                  AND is_current = FALSE
                """,
            )[0]
            self.assertAlmostEqual(snapshot["score"], 0.7)
            self.assertEqual(snapshot["anchor_profile_name"], "durable_value_compounder")
            self.assertEqual(snapshot["profile_name"], "durable_value_compounder_v1")

    def test_sparse_weekly_start_week_limits_anchor_range(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            week_runs = [
                ("week=13", "move_prediction_20260329_1200_utc_w13", datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)),
                ("week=16", "move_prediction_20260416_1200_utc_w16a", datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)),
                ("week=16", "move_prediction_20260418_1200_utc_w16b", datetime(2026, 4, 18, 10, 0, tzinfo=timezone.utc)),
                ("week=20", "move_prediction_20260515_1200_utc_w20a", datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)),
                ("week=20", "move_prediction_20260517_1200_utc_w20b", datetime(2026, 5, 17, 10, 0, tzinfo=timezone.utc)),
                ("week=25", "move_prediction_20260620_1400_utc_current1", datetime(2026, 6, 20, 14, 0, tzinfo=timezone.utc)),
            ]
            for week_name, run_id, created_at in week_runs:
                week_suffix = week_name.split("=", maxsplit=1)[-1]
                _build_sample_weekly_database(
                    temp_root=temp_root,
                    week_name=week_name,
                    database_name=f"move_prediction_2026_W{week_suffix}.duckdb",
                    run_id=run_id,
                    created_at_utc=created_at,
                    aaa_score=1.0,
                    bbb_score=0.5,
                    aaa_close=100.0,
                    bbb_close=50.0,
                    include_consensus=False,
                    include_components=False,
                )

            oldest_result = run_backwards_prediction_sparse_weekly_analysis(
                current_run_id="move_prediction_20260620_1400_utc_current1",
                duckdb_runs_root=temp_root / "duckdb_runs",
                runs_per_week=2,
                output_dir=temp_root / "backwards_output_oldest",
                include_profiles=["breakout_long_v1"],
                include_consensus=False,
                include_components=False,
            )
            self.assertEqual(
                oldest_result["anchor_plan_summary"]["oldest_run_id"],
                "move_prediction_20260329_1200_utc_w13",
            )

            from_week16 = run_backwards_prediction_sparse_weekly_analysis(
                current_run_id="move_prediction_20260620_1400_utc_current1",
                duckdb_runs_root=temp_root / "duckdb_runs",
                runs_per_week=2,
                iso_year=2026,
                start_week=16,
                output_dir=temp_root / "backwards_output_w16",
                include_profiles=["breakout_long_v1"],
                include_consensus=False,
                include_components=False,
            )
            self.assertEqual(from_week16["start_iso_week"], (2026, 16))
            self.assertEqual(
                from_week16["anchor_plan_summary"]["oldest_run_id"],
                "move_prediction_20260416_1200_utc_w16a",
            )
            anchor_names = {
                anchor["anchor_name"] for anchor in from_week16["resolved_anchors"]
            }
            self.assertNotIn("w2026_13_only", anchor_names)
            self.assertIn("w2026_16_open", anchor_names)
            self.assertIn("w2026_16_close", anchor_names)
            self.assertEqual(from_week16["runs_per_week"], 2)


if __name__ == "__main__":
    unittest.main()
