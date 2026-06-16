import csv
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite_duckdb,
    run_full_analysis_suite_with_earnings_priority_duckdb,
)
from data_analysis_scripts.trading_view_move_prediction_conviction_config import (
    DEFAULT_CONVICTION_CONFIG_PATH,
)
from data_analysis_scripts.trading_view_move_prediction_conviction_mode import (
    _coerce_bool,
    _exclusion_reason,
    run_conviction_mode_duckdb,
)
from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb


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
class TestTradingViewMovePredictionConvictionMode(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output_root = Path(self.temp_dir.name)

    def _run_suite(self, **kwargs):
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
            _sample_scan_row(
                "NASDAQ:CCC",
                price_earnings_ttm=8.0,
                **{"Perf.1M": 2.0},
            ),
        ]
        return run_full_analysis_suite_duckdb(
            scan_data=scan_data,
            profile_names=["breakout_long", "quality_value_compounder", "asymmetric_value"],
            min_market_cap_usd=1_000_000_000,
            include_blind_spot_sections=False,
            output_dir=self.output_root,
            export_parquet=False,
            reference_time=datetime(2026, 5, 31, 14, 30, tzinfo=timezone.utc),
            **kwargs,
        )

    def test_conviction_mode_exports_files_and_caps_display(self):
        result = self._run_suite(
            conviction_mode_config_path=DEFAULT_CONVICTION_CONFIG_PATH,
        )
        run_dir = Path(result["_duckdb_run_output_dir"])
        csv_path = run_dir / "move_prediction__conviction_rankings.csv"
        log_path = run_dir / "move_prediction__conviction_daily_focus.log"

        self.assertTrue(csv_path.exists())
        self.assertTrue(log_path.exists())
        self.assertIn("_conviction_rankings", result)

        with csv_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

        self.assertGreater(len(rows), 0)
        display_rows = [row for row in rows if row.get("display_flag") == "True"]
        self.assertLessEqual(len(display_rows), 10)
        self.assertGreaterEqual(len(rows), len(display_rows))

        db_rows = query_move_prediction_duckdb(
            result["_duckdb_database"],
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN display_flag THEN 1 ELSE 0 END) AS displayed
            FROM conviction_rankings
            WHERE run_id = ?
            """,
            parameters=[result["_duckdb_run_id"]],
        )
        self.assertEqual(db_rows[0]["total"], len(rows))
        self.assertEqual(db_rows[0]["displayed"], len(display_rows))

    def test_defer_conviction_to_earnings_runs_once_with_boost(self):
        base_result = self._run_suite(
            conviction_mode_config_path=DEFAULT_CONVICTION_CONFIG_PATH,
            defer_conviction_to_earnings=True,
        )
        self.assertNotIn("_conviction_rankings", base_result)

        scan_data = [
            _sample_scan_row("NASDAQ:AAA"),
            _sample_scan_row("NASDAQ:BBB"),
            _sample_scan_row("NASDAQ:CCC"),
        ]
        final_result = run_full_analysis_suite_with_earnings_priority_duckdb(
            scan_data=scan_data,
            base_result=base_result,
            conviction_mode_config_path=DEFAULT_CONVICTION_CONFIG_PATH,
        )
        run_dir = Path(final_result["_duckdb_run_output_dir"])
        self.assertTrue(
            (run_dir / "move_prediction__conviction_daily_focus.log").exists()
        )
        self.assertIn("_conviction_rankings", final_result)

    def test_standalone_conviction_runner(self):
        result = self._run_suite()
        conviction_result = run_conviction_mode_duckdb(
            database_path=result["_duckdb_database"],
            run_id=result["_duckdb_run_id"],
            run_output_dir=result["_duckdb_run_output_dir"],
            config_path=DEFAULT_CONVICTION_CONFIG_PATH,
            export_parquet=False,
        )
        self.assertGreater(conviction_result["candidate_count"], 0)
        self.assertLessEqual(conviction_result["display_count"], 10)

    def test_coerce_bool_handles_duckdb_varchar_flags(self):
        self.assertFalse(_coerce_bool("False"))
        self.assertTrue(_coerce_bool("True"))
        self.assertFalse(_coerce_bool(False))
        self.assertTrue(_coerce_bool(True))

    def test_exclusion_reason_does_not_treat_string_false_as_risk(self):
        row = {
            "manager_action_signal": "add_long_breakout",
            "weeks_opinions": 17,
            "weeks_agreement_ratio": 0.75,
        }
        breakout_row = {
            "risk_false_breakout": "False",
            "risk_fragility_conflict": "False",
            "thesis_1": "Tape confirms breakout",
        }
        exclusions = {
            "manager_actions": ["avoid_value_trap"],
            "min_consensus_opinions": 3,
            "min_agreement_ratio": 0.45,
            "breakout_risk_flags": ["false_breakout_risk", "fragility_conflict"],
        }
        self.assertIsNone(_exclusion_reason(row, breakout_row, exclusions))

        breakout_row["risk_false_breakout"] = "True"
        self.assertEqual(
            _exclusion_reason(row, breakout_row, exclusions),
            "breakout_risk:false_breakout_risk",
        )

    def test_duplicate_ticker_symbols_do_not_cartesian_join(self):
        """Two listings sharing a short ticker must not explode conviction rows."""
        scan_data = [
            _sample_scan_row(
                "LSE:PLUS",
                name="Plus500 Ltd.",
                **{"ticker-view": {"name": "PLUS", "description": "Plus500 Ltd."}},
            ),
            _sample_scan_row(
                "NASDAQ:PLUS",
                name="ePlus inc.",
                **{"ticker-view": {"name": "PLUS", "description": "ePlus inc."}},
            ),
            _sample_scan_row("NASDAQ:ZZZ"),
        ]
        result = run_full_analysis_suite_duckdb(
            scan_data=scan_data,
            profile_names=[
                "breakout_long",
                "quality_value_compounder",
                "asymmetric_value",
            ],
            min_market_cap_usd=1_000_000_000,
            include_blind_spot_sections=False,
            output_dir=self.output_root,
            export_parquet=False,
            reference_time=datetime(2026, 5, 31, 14, 30, tzinfo=timezone.utc),
            conviction_mode_config_path=DEFAULT_CONVICTION_CONFIG_PATH,
        )
        csv_path = Path(result["_duckdb_run_output_dir"]) / "move_prediction__conviction_rankings.csv"
        with csv_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

        plus_rows = [row for row in rows if row.get("symbol") == "PLUS"]
        self.assertEqual(len(plus_rows), 2)
        companies = {row.get("company") for row in plus_rows}
        self.assertIn("Plus500 Ltd.", companies)
        self.assertIn("ePlus inc.", companies)


if __name__ == "__main__":
    unittest.main()
