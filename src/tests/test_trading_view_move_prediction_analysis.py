import unittest

from data_analysis_scripts.trading_view_move_prediction_analysis import (
    CONSENSUS_PROFILE_WEIGHTS,
    DEFAULT_MOVE_PREDICTION_PROFILE_SUITE,
    EXTENDED_SIGNALS,
    INVERTED_CONSENSUS_PROFILES,
    PRESET_SCORING_PROFILES,
    _build_derived_metrics,
    _count_bullish_long_profiles,
    _manager_action_signal,
    resolve_move_prediction_scoring_profile,
)


class TestTierOneDerivedSignals(unittest.TestCase):
    def test_build_derived_metrics_includes_tier_one_signals(self):
        row = {
            "close": 100.0,
            "DonchCh20.Upper": 110.0,
            "DonchCh20.Lower": 90.0,
            "P.SAR": 95.0,
            "HullMA9": 98.0,
            "ChaikinMoneyFlow": 0.12,
            "BBPower": 1.5,
            "Recommend.All": 0.2,
            "Recommend.All|1W": 0.35,
            "ebitda": 50_000_000.0,
            "number_of_employees": 1000.0,
            "price_52_week_high": 105.0,
            "price_52_week_low": 70.0,
        }
        derived = _build_derived_metrics(row)

        self.assertAlmostEqual(derived["donchian_position"], 0.5)
        self.assertEqual(derived["close_vs_psar"], 1.0)
        self.assertEqual(derived["close_vs_hullma9"], 1.0)
        self.assertEqual(derived["chaikin_money_flow_signal"], 0.12)
        self.assertEqual(derived["bbpower_divergence"], 1.5)
        self.assertAlmostEqual(derived["recommend_tf_spread"], 0.15)
        self.assertEqual(derived["ebitda_per_employee"], 50_000.0)
        self.assertAlmostEqual(derived["near_52w_high_score"], 5.0 / 105.0)

    def test_new_signals_are_extended_by_default(self):
        for signal_name in (
            "donchian_position",
            "close_vs_psar",
            "close_vs_hullma9",
            "chaikin_money_flow_signal",
            "bbpower_divergence",
            "recommend_tf_spread",
            "ebitda_per_employee",
        ):
            self.assertIn(signal_name, EXTENDED_SIGNALS)


class TestBaselineProfiles(unittest.TestCase):
    def test_default_suite_is_ten_builtin_profiles(self):
        self.assertEqual(len(DEFAULT_MOVE_PREDICTION_PROFILE_SUITE), 10)
        for profile_name in DEFAULT_MOVE_PREDICTION_PROFILE_SUITE:
            self.assertIn(profile_name, PRESET_SCORING_PROFILES)
            self.assertIn(profile_name, CONSENSUS_PROFILE_WEIGHTS)

    def test_consensus_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(CONSENSUS_PROFILE_WEIGHTS.values()), 1.0, places=6)

    def test_only_fragility_short_is_inverted_in_baseline(self):
        self.assertEqual(INVERTED_CONSENSUS_PROFILES, frozenset({"fragility_short"}))

    def test_breakout_long_v1_activates_tier_one_signals(self):
        profile = resolve_move_prediction_scoring_profile("breakout_long_v1")
        trend_weights = profile.component_signal_weights.get("trend", {})
        attention_weights = profile.component_signal_weights.get("attention", {})
        self.assertGreater(trend_weights.get("donchian_position", 0.0), 0.0)
        self.assertGreater(trend_weights.get("close_vs_psar", 0.0), 0.0)
        self.assertGreater(
            attention_weights.get("chaikin_money_flow_signal", 0.0), 0.0
        )


class TestManagerActionSignals(unittest.TestCase):
    def test_early_inflection_handoff_emits_promote_to_breakout(self):
        horizons = {
            "days": {"score": 0.40},
            "weeks": {"score": 0.50},
            "months": {"score": 0.20},
            "years": {"score": 0.10},
        }
        components = {
            "attention": 0.2,
            "momentum": 0.3,
            "trend": 0.2,
            "quality": 0.0,
            "valuation": 0.0,
            "safety": 0.0,
        }
        signal = _manager_action_signal(
            horizons,
            components,
            scoring_profile_name="early_momentum_inflection",
            derived_row={"volume_trend": 1.10, "adx_directional_spread": 2.0},
            profile_scores={
                "breakout_long": {"weeks": {"score": 0.80}},
            },
        )
        self.assertEqual(signal, "promote_to_breakout")

    def test_fragility_requires_two_long_disagreements_for_hedge(self):
        horizons = {
            "days": {"score": -0.60},
            "weeks": {"score": -0.70},
            "months": {"score": -0.50},
            "years": {"score": -0.40},
        }
        components = {
            "attention": -0.1,
            "momentum": -0.5,
            "trend": -0.4,
            "quality": -0.2,
            "valuation": 0.0,
            "safety": -0.4,
        }
        signal = _manager_action_signal(
            horizons,
            components,
            scoring_profile_name="fragility_short",
            profile_scores={
                "fragility_short": {"weeks": {"score": 0.80}},
                "breakout_long": {"weeks": {"score": 0.50}},
            },
        )
        self.assertEqual(signal, "neutral_watch")

    def test_count_bullish_long_profiles(self):
        profile_scores = {
            "breakout_long": {"weeks": {"score": 0.80}},
            "quality_value_compounder": {"weeks": {"score": 0.10}},
            "fragility_short": {"weeks": {"score": 0.90}},
        }
        self.assertEqual(_count_bullish_long_profiles(profile_scores), 1)


if __name__ == "__main__":
    unittest.main()
