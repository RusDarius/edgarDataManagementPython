import unittest
from pathlib import Path

from data_analysis_scripts.trading_view_move_prediction_analysis import (
    DEFAULT_MOVE_PREDICTION_PROFILE_SUITE,
)
from data_analysis_scripts.trading_view_move_prediction_batch_pattern_analysis import (
    DEFAULT_BATCH_INCLUDE_PROFILES,
    _chunk_history_plans,
    _resolve_history_build_options,
    _resolve_batch_include_profiles,
)


class TestTradingViewMovePredictionBatchPatternAnalysis(unittest.TestCase):
    def test_default_batch_include_profiles_matches_builtin_suite(self):
        self.assertEqual(
            list(DEFAULT_MOVE_PREDICTION_PROFILE_SUITE),
            list(DEFAULT_BATCH_INCLUDE_PROFILES),
        )
        self.assertEqual(
            list(DEFAULT_MOVE_PREDICTION_PROFILE_SUITE),
            _resolve_batch_include_profiles(None),
        )

    def test_resolve_batch_include_profiles_deduplicates_and_preserves_order(self):
        self.assertEqual(
            ["breakout_long", "forward_edge_active", "fragility_short"],
            _resolve_batch_include_profiles(
                [
                    "breakout_long",
                    "forward_edge_active",
                    "breakout_long",
                    " fragility_short ",
                    "",
                ]
            ),
        )

    def test_resolve_history_build_options_supports_gates_only(self):
        build_options = _resolve_history_build_options("gates_only")
        self.assertFalse(build_options.build_profile_outputs)
        self.assertFalse(build_options.build_wide_progression_tables)
        self.assertFalse(build_options.build_cross_comparison)
        self.assertFalse(build_options.include_snapshot_delta_view)
        self.assertTrue(build_options.calibration_only)

    def test_chunk_history_plans_splits_weekly_databases(self):
        plans = _chunk_history_plans(
            [
                Path("D:/FinanceProjects/edgarDataManagementPython/week=21/move_prediction_2026_W21.duckdb"),
                Path("D:/FinanceProjects/edgarDataManagementPython/week=22/move_prediction_2026_W22.duckdb"),
                Path("D:/FinanceProjects/edgarDataManagementPython/week=23/move_prediction_2026_W23.duckdb"),
            ],
            start_week=21,
            chunk_weeks=2,
        )
        self.assertEqual(2, len(plans))
        self.assertEqual((21, 22), (plans[0].week_start, plans[0].week_end))
        self.assertEqual((23, 23), (plans[1].week_start, plans[1].week_end))
        self.assertEqual(2, plans[0].chunk_total)
        self.assertEqual(2, plans[1].chunk_total)


if __name__ == "__main__":
    unittest.main()
