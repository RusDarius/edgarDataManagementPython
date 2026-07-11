import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_backwards_execution_backtest import (
    ExecutionRule,
    load_execution_observations,
    run_backwards_execution_backtest,
    simulate_execution_trades,
)
from src.tests.test_trading_view_backwards_profile_cohort_attribution import (
    _build_fresh_inclusion_backwards_db,
    _duckdb_available,
)


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestBackwardsExecutionBacktest(unittest.TestCase):
    def test_fresh_rank_and_relative_volume_entry(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            backwards_db = _build_fresh_inclusion_backwards_db(temp_root)
            observations = load_execution_observations(
                backwards_db,
                profile_names=["breakout_long_v1"],
            )
            rule = ExecutionRule(
                rule_id="fresh_top1_relvol",
                entry_rank_threshold=1,
                exit_rank_threshold=1000,
                require_fresh_inclusion=True,
                fresh_rank_threshold=1,
                min_entry_relative_volume=1.1,
            )

            trades = simulate_execution_trades(
                observations,
                rule=rule,
                rank_scope="global",
            )

            self.assertEqual(len(trades), 1)
            trade = trades[0]
            self.assertEqual(trade.bare_ticker, "AAA")
            self.assertEqual(trade.entry_anchor_name, "a2")
            self.assertEqual(trade.entry_prior_anchor_name, "a1")
            self.assertEqual(trade.entry_prior_rank, 2)
            self.assertTrue(trade.fresh_entry)
            self.assertAlmostEqual(trade.entry_relative_volume_10d_calc or 0.0, 1.2)
            self.assertEqual(trade.exit_reason, "window_end")
            self.assertAlmostEqual(trade.return_pct, 25.0)

    def test_run_backwards_execution_backtest_writes_outputs(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            backwards_db = _build_fresh_inclusion_backwards_db(temp_root)
            result = run_backwards_execution_backtest(
                database_path=backwards_db,
                output_dir=temp_root / "execution_out",
                profile_names=["breakout_long_v1"],
                rank_scope="global",
                rules=[
                    ExecutionRule(
                        rule_id="fresh_top1_relvol",
                        entry_rank_threshold=1,
                        exit_rank_threshold=1000,
                        require_fresh_inclusion=True,
                        fresh_rank_threshold=1,
                        min_entry_relative_volume=1.1,
                    )
                ],
            )

            self.assertTrue(Path(result["summary_csv"]).exists())
            self.assertTrue(Path(result["manifest_path"]).exists())
            self.assertTrue(
                Path(result["trade_csv_paths"]["fresh_top1_relvol"]).exists()
            )
            self.assertEqual(result["summaries"][0].trade_count, 1)


if __name__ == "__main__":
    unittest.main()
