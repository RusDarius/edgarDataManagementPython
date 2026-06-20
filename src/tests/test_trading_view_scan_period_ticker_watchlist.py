import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_scan_period_ticker_watchlist import (
    _resolve_watchlist_scan_period_run_root,
    parse_regime_context_log_section,
    run_scan_period_ticker_watchlist_analysis,
)


class TestTradingViewScanPeriodTickerWatchlist(unittest.TestCase):
    def test_parse_regime_context_log_playbook_a(self):
        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "move_prediction__regime_context_focus.log"
            log_path.write_text(
                "\n".join(
                    [
                        "PLAYBOOK A TACTICAL (3 names)",
                        "-" * 80,
                        "  SILEX        RegFit=96.3 ATRP|1W=18.6 relvol=1.94",
                        "  SVMB         RegFit=67.8 ATRP|1W=162.5 relvol=3.11",
                        "  QS           RegFit=94.4 ATRP|1W=20.2 relvol=1.80",
                        "",
                        "WARNING OVERLAY ON CONSENSUS LEADERS",
                    ]
                ),
                encoding="utf-8",
            )

            rows = parse_regime_context_log_section(log_path, section="playbook_a")
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["symbol"], "SILEX")
            self.assertAlmostEqual(rows[0]["regime_fit_score"], 96.3)
            self.assertAlmostEqual(rows[1]["current_atrp_1w"], 162.5)

    def test_resolve_watchlist_scan_period_run_root_from_regime_config(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tracking_root = temp_path / "scan_period_run"
            tracking_root.mkdir()
            (tracking_root / "_scan_period_close_forward_tracking.json").write_text(
                json.dumps({"tracking_id": "scan_period_test"}),
                encoding="utf-8",
            )
            config_path = temp_path / "regime.json"
            config_path.write_text(
                json.dumps({"scan_period_run_root": tracking_root.as_posix()}),
                encoding="utf-8",
            )
            resolved = _resolve_watchlist_scan_period_run_root(
                regime_context_config_path=config_path,
            )
            self.assertEqual(resolved, tracking_root)

    def test_run_scan_period_ticker_watchlist_analysis_flags_extreme_atrp(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tracking_root = temp_path / "tracking_root"
            period_total = tracking_root / "period_total"
            returns_dir = period_total / "period_returns"
            progression_dir = tracking_root / "progression"
            period_total.mkdir(parents=True)
            returns_dir.mkdir(parents=True)
            progression_dir.mkdir(parents=True)

            import duckdb

            input_db = period_total / "period_analysis_input.duckdb"
            returns_db = returns_dir / "period_boundary_returns.duckdb"
            progression_db = progression_dir / "period_progression.duckdb"

            conn = duckdb.connect(str(input_db))
            conn.execute(
                """
                CREATE TABLE all_fields_rows (
                    symbol VARCHAR,
                    "ATRP|1W" DOUBLE,
                    "ADRP|1W" DOUBLE,
                    relative_volume DOUBLE,
                    ebitda_ttm DOUBLE,
                    "Recommend.MA|1M" DOUBLE,
                    oper_income_ttm DOUBLE,
                    "RSI21[1]|1M" DOUBLE,
                    "Stoch.K_14_1_3|1M" DOUBLE,
                    "W.R|1M" DOUBLE,
                    period_return_pct DOUBLE
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO all_fields_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("ALPHA", 12.0, 8.0, 2.0, 100.0, 1.0, 50.0, 55.0, 45.0, -20.0, 10.0),
                    ("EXTREME", 80.0, 30.0, 3.5, 500.0, 2.0, 200.0, 20.0, 10.0, -80.0, -5.0),
                    ("OTHER", 5.0, 4.0, 1.0, 10.0, 0.5, 5.0, 60.0, 50.0, -10.0, 8.0),
                ],
            )
            conn.close()

            conn = duckdb.connect(str(returns_db))
            conn.execute(
                """
                CREATE TABLE period_boundary_returns (
                    symbol VARCHAR,
                    start_close_price DOUBLE,
                    end_close_price DOUBLE,
                    period_return_pct DOUBLE
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO period_boundary_returns VALUES (?, ?, ?, ?)
                """,
                [
                    ("ALPHA", 100.0, 110.0, 10.0),
                    ("EXTREME", 50.0, 47.5, -5.0),
                    ("OTHER", 20.0, 21.6, 8.0),
                ],
            )
            conn.close()

            conn = duckdb.connect(str(progression_db))
            conn.execute(
                """
                CREATE TABLE period_symbol_progression (
                    source_day_label VARCHAR,
                    run_id VARCHAR,
                    run_created_at_utc TIMESTAMP,
                    symbol VARCHAR,
                    close_price DOUBLE,
                    cumulative_return_from_start_pct DOUBLE
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO period_symbol_progression VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("01_06_2026", "run1", "2026-06-01", "EXTREME", 50.0, 0.0),
                    ("02_06_2026", "run1", "2026-06-02", "EXTREME", 45.0, -10.0),
                    ("03_06_2026", "run1", "2026-06-03", "EXTREME", 47.5, -5.0),
                ],
            )
            conn.close()

            tracking_payload = {
                "tracking_id": "scan_period_close_forward_tracking_test",
                "start_day_label": "01_06_2026",
                "end_day_label": "03_06_2026",
                "performance_target": "period_return_pct",
                "period_total_result": {
                    "period_input_result": {"database_path": input_db.as_posix()},
                    "period_returns_result": {"database_path": returns_db.as_posix()},
                },
            }
            (tracking_root / "_scan_period_close_forward_tracking.json").write_text(
                json.dumps(tracking_payload),
                encoding="utf-8",
            )

            result = run_scan_period_ticker_watchlist_analysis(
                run_root=tracking_root,
                tickers=["ALPHA", "EXTREME", "MISSING"],
                watchlist_id="test_watchlist",
            )
            self.assertEqual(result["symbols_matched"], 2)
            self.assertEqual(result["symbols_unmatched"], ["MISSING"])

            extreme_row = next(
                row for row in result["summary_rows"] if row["symbol"] == "EXTREME"
            )
            self.assertIn("extreme_atrp_at_start", extreme_row["risk_flags"])
            self.assertIn("negative_period_return", extreme_row["risk_flags"])
            self.assertTrue(Path(result["summary_log"]).exists())
            self.assertTrue(Path(result["manifest_path"]).exists())


if __name__ == "__main__":
    unittest.main()
