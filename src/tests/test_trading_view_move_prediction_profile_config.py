import unittest
from pathlib import Path

from data_analysis_scripts.trading_view_move_prediction_analysis import (
    CONSENSUS_PROFILE_WEIGHTS,
    DEFAULT_MOVE_PREDICTION_PROFILE_SUITE,
    INVERTED_CONSENSUS_PROFILES,
    PRESET_SCORING_PROFILES,
    resolve_move_prediction_scoring_profile,
)
from data_analysis_scripts.trading_view_move_prediction_profile_config import (
    load_profile_config_file,
    load_profile_suite,
    resolve_profile_suite,
)

CONFIG_ROOT = (
    Path(__file__).resolve().parents[2] / "config" / "move_prediction_profiles"
)


class TestProfileConfigLoader(unittest.TestCase):
    def test_baseline_suite_matches_builtin_defaults(self):
        suite = load_profile_suite(CONFIG_ROOT / "suites" / "baseline.json")
        self.assertEqual(suite["profile_names"], DEFAULT_MOVE_PREDICTION_PROFILE_SUITE)
        self.assertAlmostEqual(
            sum(suite["consensus_profile_weights"].values()), 1.0, places=6
        )
        self.assertEqual(suite["inverted_consensus_profiles"], INVERTED_CONSENSUS_PROFILES)

    def test_active_manager_v1_loads_versioned_profiles(self):
        suite = load_profile_suite(
            CONFIG_ROOT / "suites" / "active_manager_v1.json"
        )
        self.assertIn("breakout_long_v1", suite["profile_names"])
        self.assertIn("income_compounder_v1", suite["profile_names"])
        profile = suite["registry"].get_profile("breakout_long_v1")
        self.assertIsNotNone(profile)
        trend_weights = profile.component_signal_weights.get("trend", {})
        self.assertGreater(trend_weights.get("donchian_position", 0.0), 0.0)

    def test_resolve_profile_suite_default_is_builtin_baseline(self):
        suite = resolve_profile_suite()
        self.assertEqual(suite["suite_id"], "builtin_baseline")
        self.assertEqual(suite["profile_names"], DEFAULT_MOVE_PREDICTION_PROFILE_SUITE)
        self.assertEqual(
            suite["consensus_profile_weights"], dict(CONSENSUS_PROFILE_WEIGHTS)
        )

    def test_breakout_long_builtin_unchanged_while_v1_is_json(self):
        builtin = resolve_move_prediction_scoring_profile("breakout_long")
        self.assertIn("breakout_long", PRESET_SCORING_PROFILES)
        v1_meta = load_profile_config_file(
            CONFIG_ROOT / "profiles" / "breakout_long_v1.json"
        )
        v1 = resolve_move_prediction_scoring_profile("breakout_long_v1")
        self.assertEqual(v1_meta["base_profile_id"], "breakout_long")
        self.assertNotEqual(
            builtin.component_signal_weights,
            v1.component_signal_weights,
        )

    def test_swing_reversal_v1_profile_loads(self):
        meta = load_profile_config_file(
            CONFIG_ROOT / "profiles" / "swing_reversal_v1.json"
        )
        profile = resolve_move_prediction_scoring_profile("swing_reversal_v1")
        self.assertEqual(meta["profile_id"], "swing_reversal_v1")
        self.assertEqual(meta["base_profile_id"], "swing_reversal")
        self.assertEqual(profile.name, "swing_reversal_v1")
        momentum_weights = profile.component_signal_weights.get("momentum", {})
        trend_weights = profile.component_signal_weights.get("trend", {})
        self.assertGreaterEqual(momentum_weights.get("stoch_rsi_crossover", 0.0), 1.6)
        self.assertGreaterEqual(trend_weights.get("close_vs_camarilla_s1", 0.0), 1.4)

    def test_swing_reversal_v1_standalone_suite_weights_sum_to_one(self):
        suite = load_profile_suite(
            CONFIG_ROOT / "suites" / "swing_reversal_v1.json"
        )
        self.assertEqual(suite["suite_id"], "swing_reversal_v1")
        self.assertEqual(suite["profile_names"], ["swing_reversal_v1"])
        self.assertAlmostEqual(
            sum(suite["consensus_profile_weights"].values()), 1.0, places=6
        )

    def test_active_manager_v2_includes_swing_reversal_v1(self):
        suite = load_profile_suite(
            CONFIG_ROOT / "suites" / "active_manager_v2.json"
        )
        self.assertEqual(suite["suite_id"], "active_manager_v2")
        self.assertIn("swing_reversal_v1", suite["profile_names"])
        self.assertIn("swing_reversal_v1", suite["long_consensus_profiles"])
        self.assertNotIn(
            "swing_reversal_v1", suite["inverted_consensus_profiles"]
        )
        self.assertAlmostEqual(
            sum(suite["consensus_profile_weights"].values()), 1.0, places=6
        )
        self.assertAlmostEqual(
            suite["consensus_profile_weights"]["swing_reversal_v1"], 0.06, places=6
        )

    def test_active_manager_v3_loads_consolidated_profiles(self):
        suite = load_profile_suite(
            CONFIG_ROOT / "suites" / "active_manager_v3.json"
        )
        self.assertEqual(suite["suite_id"], "active_manager_v3")
        self.assertEqual(len(suite["profile_names"]), 11)
        quality_continuation = suite["registry"].get_profile("quality_continuation_v1")
        self.assertIsNotNone(quality_continuation)
        quality_weights = quality_continuation.component_signal_weights.get(
            "quality", {}
        )
        self.assertGreater(quality_weights.get("return_on_invested_capital", 0.0), 1.4)
        forward_edge = suite["registry"].get_profile("forward_edge_active_v2")
        self.assertIsNotNone(forward_edge)
        event_weights = forward_edge.component_signal_weights.get("event", {})
        self.assertGreaterEqual(
            event_weights.get("eps_surprise_percent_fq", 0.0), 1.85
        )

    def test_baseline_v2_weights_sum_to_one(self):
        suite = load_profile_suite(CONFIG_ROOT / "suites" / "baseline_v2.json")
        self.assertEqual(suite["suite_id"], "baseline_v2")
        self.assertAlmostEqual(
            sum(suite["consensus_profile_weights"].values()), 1.0, places=6
        )


if __name__ == "__main__":
    unittest.main()
