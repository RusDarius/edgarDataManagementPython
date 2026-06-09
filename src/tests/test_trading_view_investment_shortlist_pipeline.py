import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_investment_shortlist_pipeline import (
    DEFAULT_QUERY_SESSION_PATH,
    load_named_query_sql,
    run_investment_shortlist_pipeline,
    run_named_query,
    run_profile_raw_validation_report,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite_duckdb,
)


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _sample_scan_row(symbol: str, **overrides) -> dict:
    row = {
        "symbol": symbol,
        "name": f"{symbol} Corp",
        "sector": "Technology Services",
        "industry": "Software",
        "market": "america",
        "market_cap_basic": 2_500_000_000,
        "close": 42.5,
        "relative_volume_10d_calc": 1.8,
        "average_volume_10d_calc": 1_200_000,
        "average_volume_30d_calc": 1_000_000,
        "volume": 2_000_000,
        "float_shares_outstanding": 50_000_000,
        "Value.Traded": 15_000_000,
        "Perf.5D": 4.2,
        "Perf.W": 4.2,
        "Perf.1M": 8.5,
        "Perf.YTD": 18.0,
        "Perf.Y": 35.0,
        "Perf.5Y": 120.0,
        "change": 1.8,
        "gap": 1.2,
        "ATRP": 2.0,
        "premarket_change": 0.5,
        "ADX+DI": 28.0,
        "ADX-DI": 18.0,
        "Aroon.Up": 85.0,
        "Aroon.Down": 40.0,
        "BB.upper": 45.0,
        "BB.lower": 40.0,
        "SMA10": 41.0,
        "SMA20": 40.0,
        "EMA10": 41.5,
        "EMA20": 40.5,
        "price_earnings_ttm": 24.0,
        "total_revenue_yoy_growth_ttm": 14.0,
        "debt_to_equity": 0.25,
        "earnings_release_next_calendar_date": "2099-12-31",
    }
    row.update(overrides)
    return row


@unittest.skipUnless(_duckdb_available(), "duckdb is not installed")
class TestTradingViewInvestmentShortlistPipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output_root = Path(self.temp_dir.name)

    def _build_database(self, profile_names: list[str] | None = None):
        scan_data = [
            _sample_scan_row("NASDAQ:AAA"),
            _sample_scan_row(
                "NASDAQ:BBB",
                relative_volume_10d_calc=0.8,
                average_volume_10d_calc=900_000,
                average_volume_30d_calc=1_100_000,
                **{
                    "ADX+DI": 12.0,
                    "ADX-DI": 20.0,
                    "change": -0.5,
                },
            ),
        ]
        result = run_full_analysis_suite_duckdb(
            scan_data=scan_data,
            profile_names=profile_names or ["breakout_long", "quality_value_compounder"],
            min_market_cap_usd=1_000_000_000,
            include_blind_spot_sections=False,
            output_dir=self.output_root,
            export_parquet=False,
            reference_time=datetime(2026, 5, 31, 14, 30, tzinfo=timezone.utc),
        )
        return result

    def test_load_named_query_sql_returns_breakout_block(self):
        sql = load_named_query_sql("q_breakout_long_weekly_leaders")
        self.assertIn("profile_name = 'breakout_long'", sql)
        self.assertIn("risk_adjusted_score DESC", sql)
        self.assertTrue(DEFAULT_QUERY_SESSION_PATH.exists())

    def test_run_named_query_executes_against_weekly_database(self):
        result = self._build_database()
        rows = run_named_query(
            result["_duckdb_database"],
            "q_breakout_long_weekly_leaders",
        )
        self.assertIsInstance(rows, list)

    def test_run_investment_shortlist_pipeline_exports_files(self):
        result = self._build_database(
            profile_names=[
                "breakout_long",
                "quality_value_compounder",
                "asymmetric_value",
                "fragility_short",
            ]
        )
        pipeline_result = run_investment_shortlist_pipeline(
            database_path=result["_duckdb_database"],
            run_id=result["_duckdb_run_id"],
        )

        output_dir = Path(pipeline_result["output_dir"])
        self.assertTrue((output_dir / "investment_shortlist__discovery.csv").exists())
        self.assertTrue(
            (output_dir / "investment_shortlist__breakout_long_validated.csv").exists()
        )
        self.assertTrue((output_dir / "investment_shortlist__overview.log").exists())
        self.assertIn("discovery", pipeline_result["counts"])
        self.assertIn("exported_files", pipeline_result)

    def test_aroon_flip_query_handles_prefixed_raw_symbols(self):
        scan_data = [
            _sample_scan_row(
                "NASDAQ:FLIP",
                name="Flip Corp",
                **{
                    "ticker-view": {"name": "FLIP", "description": "Flip Corp"},
                    "Aroon.Up": 88,
                    "Aroon.Down": 35,
                    "Perf.Y": 45,
                    "Perf.YTD": 40,
                },
            )
        ]
        result = run_full_analysis_suite_duckdb(
            scan_data=scan_data,
            profile_names=["early_momentum_inflection"],
            min_market_cap_usd=1_000_000_000,
            include_blind_spot_sections=False,
            output_dir=self.output_root,
            export_parquet=False,
            reference_time=datetime(2026, 5, 31, 14, 30, tzinfo=timezone.utc),
        )
        rows = run_named_query(
            result["_duckdb_database"],
            "q_early_inflection_aroon_flip",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "FLIP")
        self.assertGreater(rows[0]["aroon_spread_raw"], 0)

    def test_run_profile_raw_validation_report_returns_leader_rows(self):
        result = self._build_database()
        report = run_profile_raw_validation_report(
            database_path=result["_duckdb_database"],
            profile_name="breakout_long",
            horizon_name="weeks",
            top_n=5,
        )
        self.assertGreaterEqual(report["row_count"], 0)
        if report["rows"]:
            self.assertIn("relative_volume_10d_calc", report["rows"][0])


if __name__ == "__main__":
    unittest.main()
