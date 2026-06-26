import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
    AnchorSpec,
    run_backwards_prediction_analysis,
)
from data_analysis_scripts.trading_view_backwards_profile_cohort_attribution import (
    AnchorObservation,
    SymbolWindowAttribution,
    _merge_observations_by_bare_ticker,
    _pct_return,
    format_profile_cohort_markdown_table,
    run_backwards_profile_cohort_attribution,
    summarize_profile_cohorts,
)
from db.trading_view_move_prediction_duckdb import MovePredictionDuckDBStore


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
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
    rows: list[dict[str, object]],
) -> Path:
    week_dir = temp_root / "duckdb_runs" / "iso_year=2026" / week_name
    week_dir.mkdir(parents=True, exist_ok=True)
    week_suffix = week_name.split("=", maxsplit=1)[-1]
    database_path = week_dir / f"move_prediction_2026_W{week_suffix}.duckdb"

    raw_rows = []
    score_rows = []
    for index, row in enumerate(rows, start=1):
        symbol = str(row["symbol"])
        company = str(row["company"])
        score = float(row["score"])
        close = float(row["close"])
        raw_rows.append([symbol, company, 1.2, close, 1.0, 2.0, 3.0, 4.0])
        score_rows.append(
            {
                "run_id": run_id,
                "profile_name": profile_name,
                "row_number": index,
                "symbol": symbol,
                "company": company,
                "sector": "Materials",
                "industry": "Chemicals",
                "market_cap_basic": 2_000_000_000,
                "close": close,
                "horizon_name": "weeks",
                "score": score,
                "direction": "Up",
                "confidence": 80.0,
                "coverage": 1.0,
                "setup": "test",
                "risk_adjusted_score": score,
                "risk_tier": "medium",
                "manager_action_signal": "hold",
            }
        )

    with MovePredictionDuckDBStore(database_path=database_path) as store:
        store.register_run(
            run_id=run_id,
            created_at_utc=created_at_utc,
            suite_name="tradingview_move_prediction_full_analysis_duckdb",
            scan_data_count=3200,
            profile_names=[profile_name],
            industries=["Materials"],
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
        store.append_profile_horizon_scores(score_rows)
    return database_path


def _build_backwards_db(temp_root: Path) -> Path:
    current_time = datetime(2026, 6, 24, 14, 0, tzinfo=timezone.utc)
    rows = [
        {"symbol": "NYSE:AAA", "company": "AAA Corp", "score": 1.0, "close": 100.0},
        {"symbol": "NASDAQ:BBB", "company": "BBB Corp", "score": 0.1, "close": 100.0},
    ]
    _build_weekly_db(
        temp_root=temp_root,
        week_name="week=25",
        run_id="move_prediction_20260618_1400_utc_anchor02",
        created_at_utc=current_time - timedelta(days=6),
        profile_name="breakout_long_v1",
        rows=rows,
    )
    _build_weekly_db(
        temp_root=temp_root,
        week_name="week=26",
        run_id="move_prediction_20260624_1400_utc_current1",
        created_at_utc=current_time,
        profile_name="breakout_long_v1",
        rows=[
            {"symbol": "NYSE:AAA", "company": "AAA Corp", "score": 2.0, "close": 200.0},
            {"symbol": "NASDAQ:BBB", "company": "BBB Corp", "score": 1.0, "close": 110.0},
        ],
    )
    backwards_result = run_backwards_prediction_analysis(
        current_run_id="move_prediction_20260624_1400_utc_current1",
        duckdb_runs_root=temp_root / "duckdb_runs",
        anchors=[AnchorSpec(name="a1", run_id="move_prediction_20260618_1400_utc_anchor02")],
        output_dir=temp_root / "backwards_output",
        include_consensus=False,
        include_components=False,
    )
    return Path(backwards_result["database_path"])


class TestBackwardsProfileCohortAttribution(unittest.TestCase):
    def test_pct_return(self):
        self.assertAlmostEqual(_pct_return(150.0, 100.0), 50.0)

    def test_merge_observations_by_bare_ticker(self):
        when = datetime(2026, 6, 1, tzinfo=timezone.utc)
        merged = _merge_observations_by_bare_ticker(
            [
                AnchorObservation(
                    "w1",
                    False,
                    when,
                    "breakout_long_v1",
                    "NYSE:VEEV",
                    "VEEV",
                    "Veeva",
                    100.0,
                    1.0,
                    10,
                    2e9,
                ),
                AnchorObservation(
                    "w1",
                    False,
                    when,
                    "breakout_long_v1",
                    "VEEV",
                    "VEEV",
                    "Veeva",
                    101.0,
                    0.5,
                    500,
                    2e9,
                ),
            ]
        )
        timeline = merged[("breakout_long_v1", "VEEV")]
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0].symbol, "NYSE:VEEV")
        self.assertEqual(timeline[0].profile_rank, 10)

    @unittest.skipUnless(_duckdb_available(), "duckdb not installed")
    def test_run_backwards_profile_cohort_attribution(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            backwards_db = _build_backwards_db(temp_root)
            result = run_backwards_profile_cohort_attribution(
                database_path=backwards_db,
                profile_names=["breakout_long_v1"],
                top_n=1,
                rank_scope="global",
                include_current=True,
                output_dir=temp_root / "cohort_out",
            )
            self.assertTrue(Path(result["summary_csv"]).exists())
            summaries = result["summaries"]
            period = next(
                row for row in summaries if row.method == "period_boundary"
            )
            inclusion = next(
                row for row in summaries if row.method == "inclusion_entry"
            )
            self.assertEqual(period.cohort_size, 1)
            self.assertAlmostEqual(period.avg_return_pct, 100.0)
            self.assertAlmostEqual(inclusion.avg_return_pct, 100.0)
            markdown = format_profile_cohort_markdown_table(summaries)
            self.assertIn("period_boundary", markdown)

    @unittest.skipUnless(_duckdb_available(), "duckdb not installed")
    def test_inclusion_entry_differs_when_late_top_rank(self):
        symbol_rows = [
            SymbolWindowAttribution(
                profile_name="breakout_long_v1",
                bare_ticker="AAA",
                symbol="NYSE:AAA",
                company="AAA Corp",
                best_rank=1,
                first_anchor_name="a1",
                last_anchor_name="current",
                entry_anchor_name="current",
                first_close=100.0,
                last_close=150.0,
                entry_close=136.36,
                period_return_pct=50.0,
                inclusion_return_pct=10.0,
                in_top_n=True,
            )
        ]
        summaries = summarize_profile_cohorts(symbol_rows, top_n=250, rank_scope="global")
        period = next(row for row in summaries if row.method == "period_boundary")
        inclusion = next(row for row in summaries if row.method == "inclusion_entry")
        self.assertAlmostEqual(period.avg_return_pct, 50.0)
        self.assertAlmostEqual(inclusion.avg_return_pct, 10.0)

    def test_hold_until_rank_exit_uses_first_disqualification_anchor(self):
        symbol_rows = [
            SymbolWindowAttribution(
                profile_name="breakout_long_v1",
                bare_ticker="AAA",
                symbol="NYSE:AAA",
                company="AAA Corp",
                best_rank=50,
                first_anchor_name="a1",
                last_anchor_name="a4",
                entry_anchor_name="a2",
                first_close=100.0,
                last_close=200.0,
                entry_close=110.0,
                period_return_pct=100.0,
                inclusion_return_pct=81.82,
                in_top_n=True,
                exit_anchor_name="a3",
                exit_close=150.0,
                hold_until_rank_exit_return_pct=36.36,
                exited_by_rank=True,
            )
        ]
        summaries = summarize_profile_cohorts(
            symbol_rows,
            top_n=100,
            rank_scope="global",
            exit_rank_threshold=1000,
        )
        hold = next(row for row in summaries if row.method == "hold_until_rank_exit")
        self.assertAlmostEqual(hold.avg_return_pct, 36.36)


if __name__ == "__main__":
    unittest.main()
