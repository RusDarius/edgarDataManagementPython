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

    def test_build_derived_metrics_includes_reversal_composites(self):
        row = {
            "close": 80.0,
            "RSI": 32.0,
            "RSI7": 28.0,
            "Stoch.RSI.K": 25.0,
            "W.R": 75.0,
            "BB.upper": 90.0,
            "BB.lower": 70.0,
            "price_52_week_high": 120.0,
            "price_52_week_low": 60.0,
            "Perf.5D": 4.0,
            "Perf.W": 5.0,
            "Perf.3M": -8.0,
            "relative_volume_10d_calc": 1.4,
            "ATRP": 3.5,
            "Recommend.MA|1M": -0.2,
            "altman_z_score_ttm": 2.5,
            "total_debt_to_ebitda_fq": 2.0,
            "interst_cover_ttm": 6.0,
            "market_cap_basic": 1_000_000_000.0,
            "net_debt": 100_000_000.0,
            "price_target_median": 95.0,
            "DonchCh20.Upper": 95.0,
            "DonchCh20.Lower": 65.0,
            "Pivot.M.Camarilla.R1": 88.0,
        }
        derived = _build_derived_metrics(row)

        self.assertIsNotNone(derived["williams_r_centered"])
        self.assertAlmostEqual(derived["williams_r_centered"], -25.0)
        self.assertIsNotNone(derived["regime_oversold_daily"])
        self.assertGreater(derived["regime_oversold_daily"], 0.0)
        self.assertIsNotNone(derived["repair_confirmation_score"])
        self.assertGreater(derived["repair_confirmation_score"], 0.0)
        self.assertIsNotNone(derived["pullback_context_score"])
        self.assertIsNotNone(derived["snapback_divergence_score"])
        self.assertGreater(derived["snapback_divergence_score"], 0.0)
        self.assertIsNotNone(derived["upside_room_score"])
        self.assertIsNotNone(derived["distress_floor"])

    def test_pullback_composites_favor_snapback_over_extended_momentum(self):
        pullback_row = {
            "close": 45.0,
            "RSI": 32.0,
            "RSI7": 28.0,
            "Stoch.RSI.K": 22.0,
            "BB.upper": 48.0,
            "BB.lower": 42.0,
            "price_52_week_high": 90.0,
            "price_52_week_low": 40.0,
            "Perf.5D": 4.5,
            "Perf.W": 6.0,
            "Perf.1M": -12.0,
            "Perf.3M": -18.0,
            "Perf.6M": -8.0,
            "Perf.Y": -5.0,
            "relative_volume_10d_calc": 1.8,
            "ATRP": 3.0,
            "SMA10": 44.0,
            "SMA20": 46.0,
            "SMA50": 52.0,
            "SMA200": 65.0,
            "EMA10": 44.5,
            "EMA20": 46.5,
            "EMA50": 53.0,
            "EMA200": 66.0,
        }
        breakout_row = {
            "close": 120.0,
            "RSI": 68.0,
            "RSI7": 70.0,
            "Stoch.RSI.K": 75.0,
            "BB.upper": 125.0,
            "BB.lower": 110.0,
            "price_52_week_high": 125.0,
            "price_52_week_low": 40.0,
            "Perf.5D": 8.0,
            "Perf.W": 12.0,
            "Perf.1M": 15.0,
            "Perf.3M": 35.0,
            "Perf.6M": 50.0,
            "Perf.Y": 80.0,
            "relative_volume_10d_calc": 2.2,
            "ATRP": 2.5,
            "SMA10": 115.0,
            "SMA20": 112.0,
            "SMA50": 100.0,
            "SMA200": 80.0,
            "EMA10": 116.0,
            "EMA20": 113.0,
            "EMA50": 105.0,
            "EMA200": 82.0,
            "Aroon.Up": 90.0,
            "Aroon.Down": 20.0,
            "ADX+DI": 35.0,
            "ADX-DI": 15.0,
        }
        pullback = _build_derived_metrics(pullback_row)
        breakout = _build_derived_metrics(breakout_row)

        self.assertGreater(
            pullback.get("snapback_divergence_score") or 0.0,
            breakout.get("snapback_divergence_score") or 0.0,
        )
        self.assertGreater(
            pullback.get("pullback_context_score") or 0.0,
            breakout.get("pullback_context_score") or 0.0,
        )
        self.assertGreater(
            breakout.get("regime_extended_tape") or 0.0,
            pullback.get("regime_extended_tape") or 0.0,
        )
        self.assertGreater(
            pullback.get("repair_confirmation_score") or 0.0,
            breakout.get("repair_confirmation_score") or 0.0,
        )

    def test_structural_bleed_score_flags_long_duration_collapse(self):
        bleeder = {
            "close": 10.0,
            "Perf.6M": -45.0,
            "Perf.Y": -60.0,
            "Perf.YTD": -55.0,
        }
        pullback = {
            "close": 45.0,
            "Perf.6M": -8.0,
            "Perf.Y": -5.0,
            "Perf.YTD": -10.0,
        }
        self.assertGreater(
            _build_derived_metrics(bleeder).get("structural_bleed_score") or 0.0,
            _build_derived_metrics(pullback).get("structural_bleed_score") or 0.0,
        )

    def test_reversal_composites_are_extended_by_default(self):
        for signal_name in (
            "williams_r_centered",
            "regime_oversold_daily",
            "regime_extended_tape",
            "repair_confirmation_score",
            "pullback_context_score",
            "snapback_divergence_score",
            "structural_bleed_score",
            "upside_room_score",
            "distress_floor",
        ):
            self.assertIn(signal_name, EXTENDED_SIGNALS)

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

    def test_upside_reversal_emits_accumulate_when_repair_confirmed(self):
        horizons = {
            "days": {"score": 0.45},
            "weeks": {"score": 0.60},
            "months": {"score": 0.35},
            "years": {"score": 0.10},
        }
        components = {
            "attention": 0.1,
            "momentum": 0.4,
            "trend": 0.3,
            "quality": 0.0,
            "valuation": 0.2,
            "safety": 0.1,
        }
        signal = _manager_action_signal(
            horizons,
            components,
            scoring_profile_name="upside_reversal_v1",
            derived_row={
                "regime_extended_tape": -0.2,
                "repair_confirmation_score": 0.35,
                "distress_floor": 0.5,
                "range_position_52w": 0.25,
            },
            profile_scores={
                "mean_reversion_exhaustion_v1": {"weeks": {"score": 0.10}},
            },
        )
        self.assertEqual(signal, "accumulate_reversal_long")

    def test_upside_reversal_avoids_extended_exhaustion_conflict(self):
        horizons = {
            "days": {"score": 0.40},
            "weeks": {"score": 0.55},
            "months": {"score": 0.30},
            "years": {"score": 0.10},
        }
        components = {
            "attention": 0.2,
            "momentum": 0.5,
            "trend": 0.4,
            "quality": 0.0,
            "valuation": 0.1,
            "safety": 0.0,
        }
        signal = _manager_action_signal(
            horizons,
            components,
            scoring_profile_name="upside_reversal_v1",
            derived_row={
                "regime_extended_tape": 0.8,
                "repair_confirmation_score": 0.4,
                "distress_floor": 0.3,
                "range_position_52w": 0.8,
            },
            profile_scores={
                "mean_reversion_exhaustion_v1": {"weeks": {"score": 0.70}},
            },
        )
        self.assertEqual(signal, "avoid_reversal_trap")

    def test_value_recovery_v3_avoids_extended_expensive_tape(self):
        horizons = {
            "days": {"score": 1.10},
            "weeks": {"score": 1.20},
            "months": {"score": 1.00},
            "years": {"score": 0.50},
        }
        components = {
            "attention": 1.0,
            "momentum": 2.0,
            "trend": 1.5,
            "quality": 2.5,
            "valuation": -1.8,
            "safety": 1.0,
        }
        signal = _manager_action_signal(
            horizons,
            components,
            scoring_profile_name="value_recovery_v3",
            derived_row={
                "regime_extended_tape": 0.9,
                "range_position_52w": 0.85,
                "repair_confirmation_score": 0.5,
            },
        )
        self.assertEqual(signal, "avoid_extended_recovery")

    def test_value_recovery_v3_emits_accumulate_when_recovery_aligned(self):
        horizons = {
            "days": {"score": 0.55},
            "weeks": {"score": 0.60},
            "months": {"score": 0.70},
            "years": {"score": 0.40},
        }
        components = {
            "attention": 0.1,
            "momentum": 0.3,
            "trend": 0.4,
            "quality": 0.8,
            "valuation": 0.6,
            "safety": 0.2,
        }
        signal = _manager_action_signal(
            horizons,
            components,
            scoring_profile_name="value_recovery_v3",
            derived_row={
                "regime_extended_tape": -0.2,
                "range_position_52w": 0.30,
                "repair_confirmation_score": 0.25,
            },
        )
        self.assertEqual(signal, "accumulate_value_recovery")


if __name__ == "__main__":
    unittest.main()
