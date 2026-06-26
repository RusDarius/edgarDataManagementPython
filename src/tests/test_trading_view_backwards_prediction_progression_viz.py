import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
    AnchorSpec,
    run_backwards_prediction_analysis,
)
from data_analysis_scripts.trading_view_backwards_prediction_progression_viz import (
    HighlightMarker,
    ResolvedBackwardsProfileTarget,
    _latest_profile_target_per_family,
    extract_progression_series,
    render_score_progression_plotly_html,
    resolve_backwards_profile_targets,
    run_backwards_progression_plot,
    validate_progression_integrity,
)
from db.trading_view_move_prediction_duckdb import MovePredictionDuckDBStore


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _plotly_available() -> bool:
    try:
        import plotly  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _build_weekly_db(
    *,
    temp_root: Path,
    week_name: str,
    run_id: str,
    created_at_utc: datetime,
    profile_name: str,
    aaa_score: float,
    aaa_close: float,
) -> Path:
    week_dir = temp_root / "duckdb_runs" / "iso_year=2026" / week_name
    week_dir.mkdir(parents=True, exist_ok=True)
    week_suffix = week_name.split("=", maxsplit=1)[-1]
    database_path = week_dir / f"move_prediction_2026_W{week_suffix}.duckdb"
    with MovePredictionDuckDBStore(database_path=database_path) as store:
        store.register_run(
            run_id=run_id,
            created_at_utc=created_at_utc,
            suite_name="tradingview_move_prediction_full_analysis_duckdb",
            scan_data_count=3200,
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
            [
                ["NASDAQ:AAA", "Alpha", 1.4, aaa_close, 2.5, 3.0, 4.0, 5.0],
                ["NASDAQ:BBB", "Beta", 0.8, 50.0, -1.0, -0.5, -2.0, -3.0],
            ],
            context={"run_id": run_id},
        )
        store.append_profile_horizon_scores(
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
class TestBackwardsProgressionViz(unittest.TestCase):
    def test_extract_progression_series_and_quality(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            current_time = datetime(2026, 6, 24, 14, 0, tzinfo=timezone.utc)
            _build_weekly_db(
                temp_root=temp_root,
                week_name="week=24",
                run_id="move_prediction_20260612_1400_utc_anchor01",
                created_at_utc=current_time - timedelta(days=12),
                profile_name="breakout_long",
                aaa_score=0.9,
                aaa_close=90.0,
            )
            _build_weekly_db(
                temp_root=temp_root,
                week_name="week=25",
                run_id="move_prediction_20260618_1400_utc_anchor02",
                created_at_utc=current_time - timedelta(days=6),
                profile_name="breakout_long_v1",
                aaa_score=1.0,
                aaa_close=95.0,
            )
            _build_weekly_db(
                temp_root=temp_root,
                week_name="week=26",
                run_id="move_prediction_20260624_1400_utc_current1",
                created_at_utc=current_time,
                profile_name="breakout_long_v1",
                aaa_score=1.3,
                aaa_close=100.0,
            )

            backwards_result = run_backwards_prediction_analysis(
                current_run_id="move_prediction_20260624_1400_utc_current1",
                duckdb_runs_root=temp_root / "duckdb_runs",
                anchors=[
                    AnchorSpec(name="a1", run_id="move_prediction_20260612_1400_utc_anchor01"),
                    AnchorSpec(name="a2", run_id="move_prediction_20260618_1400_utc_anchor02"),
                ],
                output_dir=temp_root / "backwards_output",
                include_consensus=False,
                include_components=False,
            )
            points = extract_progression_series(
                backwards_result["database_path"],
                profile_name="breakout_long_v1",
                horizon_name="weeks",
                symbols=["AAA"],
            )
            self.assertEqual(len(points), 3)
            self.assertTrue(points[0].point_time <= points[1].point_time <= points[2].point_time)

            quality = validate_progression_integrity(points, expected_anchor_count=4)
            self.assertEqual(quality["point_count"], 3)
            self.assertTrue(any("Expected 4 anchors" in warning for warning in quality["warnings"]))

    def test_resolve_latest_profile_version_per_family(self):
        catalog = [
            ResolvedBackwardsProfileTarget("breakout_long", "breakout_long", 10),
            ResolvedBackwardsProfileTarget("breakout_long", "breakout_long_v1", 20),
            ResolvedBackwardsProfileTarget("value_recovery", "value_recovery_v2", 5),
            ResolvedBackwardsProfileTarget("value_recovery", "value_recovery_v3", 8),
        ]
        latest = _latest_profile_target_per_family(catalog)
        self.assertEqual(latest["breakout_long"].profile_name, "breakout_long_v1")
        self.assertEqual(latest["value_recovery"].profile_name, "value_recovery_v3")

    def test_resolve_backwards_profile_targets_from_existing_db(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            current_time = datetime(2026, 6, 24, 14, 0, tzinfo=timezone.utc)
            _build_weekly_db(
                temp_root=temp_root,
                week_name="week=24",
                run_id="move_prediction_20260612_1400_utc_anchor01",
                created_at_utc=current_time - timedelta(days=12),
                profile_name="breakout_long",
                aaa_score=0.9,
                aaa_close=90.0,
            )
            _build_weekly_db(
                temp_root=temp_root,
                week_name="week=26",
                run_id="move_prediction_20260624_1400_utc_current1",
                created_at_utc=current_time,
                profile_name="breakout_long_v1",
                aaa_score=1.3,
                aaa_close=100.0,
            )
            backwards_result = run_backwards_prediction_analysis(
                current_run_id="move_prediction_20260624_1400_utc_current1",
                duckdb_runs_root=temp_root / "duckdb_runs",
                anchors=[AnchorSpec(name="a1", run_id="move_prediction_20260612_1400_utc_anchor01")],
                output_dir=temp_root / "backwards_output",
                include_consensus=False,
                include_components=False,
            )
            targets, _ = resolve_backwards_profile_targets(
                backwards_result["database_path"],
                profile_family="breakout_long",
                horizon_name="weeks",
            )
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0].profile_name, "breakout_long_v1")

    def test_build_progression_plot_title_and_spec_compact_for_many_symbols(self):
        from data_analysis_scripts.trading_view_backwards_prediction_progression_viz import (
            ProgressionSymbolSpecLine,
            build_progression_plot_title_and_spec,
        )

        lines = [
            ProgressionSymbolSpecLine(
                requested=f"SYM{i}",
                plotted_label=f"SYM{i}",
                db_symbol=f"NASDAQ:SYM{i}",
            )
            for i in range(12)
        ]
        title, spec_text, spec_dict = build_progression_plot_title_and_spec(
            profile_name="breakout_long_v1",
            horizon_name="weeks",
            symbol_lines=lines,
        )
        self.assertIn("12 symbols", title)
        self.assertNotIn("SYM0, SYM1", title)
        self.assertEqual(spec_text, "")
        self.assertEqual(spec_dict["symbol_count"], 12)
        _, spec_text_on, _ = build_progression_plot_title_and_spec(
            profile_name="breakout_long_v1",
            horizon_name="weeks",
            symbol_lines=lines,
            include_chart_subtitle=True,
        )
        self.assertIn("Plotted symbols (12):", spec_text_on)

    @unittest.skipUnless(_plotly_available(), "plotly not installed")
    def test_render_plotly_html_smoke(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            point_time = datetime(2026, 6, 20, 14, 0, tzinfo=timezone.utc)

            from data_analysis_scripts.trading_view_backwards_prediction_progression_viz import (
                ProgressionPoint,
            )

            series = [
                ProgressionPoint(
                    backwards_analysis_id="demo",
                    point_time=point_time,
                    anchor_name="anchor_a",
                    is_current=False,
                    run_id="run_a",
                    snapshot_label="snapshot_a",
                    profile_name="breakout_long_v1",
                    profile_family="breakout_long",
                    anchor_profile_name="breakout_long",
                    horizon_name="weeks",
                    symbol="NASDAQ:AAA",
                    company="Alpha",
                    sector="Technology",
                    industry="Software",
                    score=1.0,
                    close=95.0,
                    profile_rank=10,
                    direction="Up",
                    confidence=90.0,
                ),
                ProgressionPoint(
                    backwards_analysis_id="demo",
                    point_time=point_time + timedelta(days=7),
                    anchor_name="current",
                    is_current=True,
                    run_id="run_current",
                    snapshot_label="snapshot_current",
                    profile_name="breakout_long_v1",
                    profile_family="breakout_long",
                    anchor_profile_name=None,
                    horizon_name="weeks",
                    symbol="NASDAQ:AAA",
                    company="Alpha",
                    sector="Technology",
                    industry="Software",
                    score=1.2,
                    close=100.0,
                    profile_rank=5,
                    direction="Up",
                    confidence=92.0,
                ),
            ]
            highlights = [
                HighlightMarker(
                    symbol="NASDAQ:AAA",
                    profile_name="breakout_long_v1",
                    highlight_type="watchlist",
                    anchor_name="current",
                    point_time=series[-1].point_time,
                    score=series[-1].score,
                    score_delta=None,
                    label="watchlist",
                )
            ]

            html_path = temp_root / "score_progression.html"
            render_score_progression_plotly_html(
                series,
                highlights,
                title="Smoke test",
                output_path=html_path,
            )
            self.assertTrue(html_path.exists())
            self.assertIn("plotly", html_path.read_text(encoding="utf-8").lower())

    @unittest.skipUnless(_plotly_available(), "plotly not installed")
    def test_render_plotly_html_adds_hidden_close_traces_per_symbol(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            point_time = datetime(2026, 6, 20, 14, 0, tzinfo=timezone.utc)

            from data_analysis_scripts.trading_view_backwards_prediction_progression_viz import (
                ProgressionPoint,
            )

            def _point(symbol: str, score: float, close: float) -> ProgressionPoint:
                return ProgressionPoint(
                    backwards_analysis_id="demo",
                    point_time=point_time,
                    anchor_name="anchor_a",
                    is_current=False,
                    run_id="run_a",
                    snapshot_label="snapshot_a",
                    profile_name="breakout_long_v1",
                    profile_family="breakout_long",
                    anchor_profile_name="breakout_long",
                    horizon_name="weeks",
                    symbol=symbol,
                    company=symbol,
                    sector="Technology",
                    industry="Software",
                    score=score,
                    close=close,
                    profile_rank=10,
                    direction="Up",
                    confidence=90.0,
                )

            series = [
                _point("NASDAQ:AAA", 1.0, 95.0),
                _point("NASDAQ:BBB", 0.8, 42.0),
            ]
            html_path = temp_root / "multi_symbol_progression.html"
            render_score_progression_plotly_html(
                series,
                [],
                title="Multi-symbol close traces",
                output_path=html_path,
                close_price_visible_by_default=False,
            )
            html_text = html_path.read_text(encoding="utf-8")
            self.assertIn("NASDAQ:AAA close", html_text)
            self.assertIn("NASDAQ:BBB close", html_text)
            self.assertIn('"visible":"legendonly"', html_text)
            self.assertIn('"yaxis":"y2"', html_text)

    @unittest.skipUnless(_plotly_available(), "plotly not installed")
    def test_run_backwards_progression_plot_end_to_end(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            current_time = datetime(2026, 6, 24, 14, 0, tzinfo=timezone.utc)
            _build_weekly_db(
                temp_root=temp_root,
                week_name="week=24",
                run_id="move_prediction_20260610_1400_utc_anchor01",
                created_at_utc=current_time - timedelta(days=14),
                profile_name="breakout_long",
                aaa_score=0.9,
                aaa_close=90.0,
            )
            _build_weekly_db(
                temp_root=temp_root,
                week_name="week=25",
                run_id="move_prediction_20260617_1400_utc_anchor02",
                created_at_utc=current_time - timedelta(days=7),
                profile_name="breakout_long_v1",
                aaa_score=1.1,
                aaa_close=96.0,
            )
            _build_weekly_db(
                temp_root=temp_root,
                week_name="week=26",
                run_id="move_prediction_20260624_1400_utc_current1",
                created_at_utc=current_time,
                profile_name="breakout_long_v1",
                aaa_score=1.3,
                aaa_close=101.0,
            )

            result = run_backwards_progression_plot(
                duckdb_runs_root=temp_root / "duckdb_runs",
                current_run_id="move_prediction_20260624_1400_utc_current1",
                runs_per_week=1,
                profile_family="breakout_long",
                horizon_name="weeks",
                symbols=["AAA"],
                watchlist_path=temp_root / "missing_watchlist.json",
                include_scout_highlights=False,
                include_deltas_for_highlights=False,
                output_dir=temp_root / "viz_output",
            )

            self.assertGreaterEqual(result["point_count"], 2)
            self.assertTrue(Path(result["html_path"]).exists())
            self.assertTrue(Path(result["quality_report_path"]).exists())


if __name__ == "__main__":
    unittest.main()
