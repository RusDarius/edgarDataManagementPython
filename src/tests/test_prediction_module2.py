import unittest
from pathlib import Path

from data_analysis_scripts.analysis_prediction_module2 import run_module2_profile_suite
from data_analysis_scripts.prediction_module2_derived import build_module2_derived_metrics
from data_analysis_scripts.prediction_module2_profile_config import (
    load_module2_profile_config_file,
    load_module2_profile_suite,
)
from data_analysis_scripts.prediction_module2_scoring import (
    evaluate_hard_gates,
    jaccard_overlap,
    prepare_scoring_context,
    rank_profile_results,
    score_profile_row,
    score_universe_for_profile,
)

CONFIG_ROOT = (
    Path(__file__).resolve().parents[2] / "config" / "prediction_module2_profiles"
)


def _extended_momentum_row() -> dict:
    return {
        "symbol": "NASDAQ:SNDK",
        "name": "Sandisk",
        "market_cap_basic": 5_000_000_000,
        "close": 120.0,
        "price_52_week_high": 125.0,
        "price_52_week_low": 40.0,
        "RSI": 68.0,
        "RSI7": 70.0,
        "Stoch.RSI.K": 75.0,
        "Stoch.RSI.D": 70.0,
        "Perf.5D": 8.0,
        "Perf.W": 12.0,
        "Perf.3M": 35.0,
        "Perf.6M": 50.0,
        "relative_volume_10d_calc": 1.8,
        "ATRP": 4.5,
        "average_volume_10d_calc": 2_000_000,
        "average_volume_30d_calc": 1_200_000,
        "Aroon.Up": 90.0,
        "Aroon.Down": 20.0,
        "ADX+DI": 35.0,
        "ADX-DI": 15.0,
        "SMA50": 100.0,
        "SMA200": 80.0,
        "EMA50": 105.0,
        "EMA200": 82.0,
        "SMA10": 115.0,
        "SMA20": 112.0,
        "SMA30": 110.0,
        "EMA10": 116.0,
        "EMA20": 113.0,
        "EMA30": 111.0,
        "BB.upper": 125.0,
        "BB.lower": 100.0,
        "Recommend.MA|1M": 0.8,
        "altman_z_score_ttm": 3.0,
        "total_debt_to_ebitda_fq": 1.5,
    }


def _oversold_repair_row() -> dict:
    return {
        "symbol": "NASDAQ:REPAIR",
        "name": "Repair Co",
        "market_cap_basic": 2_000_000_000,
        "close": 45.0,
        "price_52_week_high": 90.0,
        "price_52_week_low": 40.0,
        "RSI": 32.0,
        "RSI7": 30.0,
        "Stoch.RSI.K": 25.0,
        "Stoch.RSI.D": 20.0,
        "Perf.5D": 4.0,
        "Perf.W": 5.5,
        "Perf.3M": -12.0,
        "Perf.6M": -18.0,
        "relative_volume_10d_calc": 1.6,
        "ATRP": 3.5,
        "average_volume_10d_calc": 900_000,
        "average_volume_30d_calc": 650_000,
        "Aroon.Up": 35.0,
        "Aroon.Down": 65.0,
        "ADX+DI": 18.0,
        "ADX-DI": 24.0,
        "SMA50": 50.0,
        "SMA200": 55.0,
        "EMA50": 49.0,
        "EMA200": 54.0,
        "SMA10": 44.0,
        "SMA20": 46.0,
        "SMA30": 47.0,
        "EMA10": 43.5,
        "EMA20": 45.5,
        "EMA30": 46.5,
        "BB.upper": 52.0,
        "BB.lower": 42.0,
        "Recommend.MA|1M": -0.2,
        "price_target_median": 58.0,
        "Pivot.M.Camarilla.R1": 48.0,
        "DonchCh20.Upper": 55.0,
        "DonchCh20.Lower": 41.0,
        "altman_z_score_ttm": 2.5,
        "total_debt_to_ebitda_fq": 2.0,
        "total_revenue_qoq_growth_fq": 3.0,
    }


class TestModule2ProfileConfig(unittest.TestCase):
    def test_active_manager_suite_loads_and_weights_sum_to_one(self):
        suite = load_module2_profile_suite(
            CONFIG_ROOT / "suites" / "active_manager_module2_v1.json"
        )
        self.assertEqual(suite.suite_id, "active_manager_module2_v1")
        self.assertIn("upside_swing_v1", suite.profile_names)
        self.assertAlmostEqual(
            sum(suite.consensus_profile_weights.values()), 1.0, places=6
        )
        self.assertEqual(suite.consensus_mode, "family_orthogonal")

    def test_upside_swing_profile_has_hard_gates(self):
        profile = load_module2_profile_config_file(
            CONFIG_ROOT / "profiles" / "upside_swing_v1.json"
        )
        self.assertEqual(profile.outlook_family, "mean_reversion_repair")
        self.assertGreaterEqual(len(profile.regime_gates_hard), 2)
        self.assertIn("repair_trigger", profile.pillars)


class TestModule2GateLogic(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = load_module2_profile_suite(
            CONFIG_ROOT / "suites" / "active_manager_module2_v1.json"
        )
        self.scan_rows = [_extended_momentum_row(), _oversold_repair_row()]
        self.derived, self.metric_profiles, self.catalog = prepare_scoring_context(
            self.scan_rows, self.suite
        )
        self.swing_profile = self.suite.registry.get_profile("upside_swing_v1")
        self.breakout_profile = self.suite.registry.get_profile(
            "breakout_continuation_v1"
        )
        assert self.swing_profile is not None
        assert self.breakout_profile is not None
        self.swing_profile_obj = self.swing_profile
        self.breakout_profile_obj = self.breakout_profile

    def test_extended_momentum_fails_swing_hard_gates(self):
        row = self.scan_rows[0]
        derived = self.derived[0]
        passed, _ = evaluate_hard_gates(
            row,
            derived,
            self.swing_profile_obj,
            self.metric_profiles,
            catalog=self.catalog,
        )
        self.assertFalse(passed)

    def test_oversold_repair_passes_swing_hard_gates(self):
        row = self.scan_rows[1]
        derived = self.derived[1]
        passed, _ = evaluate_hard_gates(
            row,
            derived,
            self.swing_profile_obj,
            self.metric_profiles,
            catalog=self.catalog,
        )
        self.assertTrue(passed)

    def test_extended_momentum_passes_breakout_hard_gates(self):
        row = self.scan_rows[0]
        derived = self.derived[0]
        passed, _ = evaluate_hard_gates(
            row,
            derived,
            self.breakout_profile_obj,
            self.metric_profiles,
            catalog=self.catalog,
        )
        self.assertTrue(passed)


class TestModule2LeaderDisjointness(unittest.TestCase):
    def test_upside_swing_and_breakout_top_leaders_mostly_disjoint(self):
        suite = load_module2_profile_suite(
            CONFIG_ROOT / "suites" / "active_manager_module2_v1.json"
        )
        scan_rows = [_extended_momentum_row(), _oversold_repair_row()]
        derived, metric_profiles, catalog = prepare_scoring_context(scan_rows, suite)

        swing_profile = suite.registry.get_profile("upside_swing_v1")
        breakout_profile = suite.registry.get_profile("breakout_continuation_v1")
        assert swing_profile is not None
        assert breakout_profile is not None

        swing_scored = score_universe_for_profile(
            scan_rows, derived, swing_profile, metric_profiles, catalog=catalog
        )
        breakout_scored = score_universe_for_profile(
            scan_rows,
            derived,
            breakout_profile,
            metric_profiles,
            catalog=catalog,
        )
        swing_ranked = rank_profile_results(swing_scored, top_n=2)
        breakout_ranked = rank_profile_results(breakout_scored, top_n=2)

        swing_symbols = [row.get("symbol") for row in swing_ranked]
        breakout_symbols = [row.get("symbol") for row in breakout_ranked]
        overlap = jaccard_overlap(swing_symbols, breakout_symbols)

        self.assertIn("NASDAQ:REPAIR", swing_symbols)
        self.assertIn("NASDAQ:SNDK", breakout_symbols)
        self.assertLess(overlap, 0.25)

    def test_run_module2_profile_suite_writes_overlap_report(self):
        suite_path = CONFIG_ROOT / "suites" / "active_manager_module2_v1.json"
        result = run_module2_profile_suite(
            [_extended_momentum_row(), _oversold_repair_row()],
            profile_suite_path=suite_path,
            output_dir=Path(__file__).resolve().parent / "_tmp_module2_run",
        )
        self.assertIn("overlap_report", result)
        self.assertIn("profiles", result)
        swing_overlap = next(
            pair
            for pair in result["overlap_report"]["pair_overlaps"]
            if pair["left_profile"] == "breakout_continuation_v1"
            and pair["right_profile"] == "upside_swing_v1"
        ) if any(
            pair["left_profile"] == "breakout_continuation_v1"
            and pair["right_profile"] == "upside_swing_v1"
            for pair in result["overlap_report"]["pair_overlaps"]
        ) else next(
            pair
            for pair in result["overlap_report"]["pair_overlaps"]
            if pair["left_profile"] == "upside_swing_v1"
            and pair["right_profile"] == "breakout_continuation_v1"
        )
        self.assertLess(swing_overlap["jaccard_top_n"], 0.25)


class TestModule2DerivedMetrics(unittest.TestCase):
    def test_regime_extended_tape_higher_for_momentum_row(self):
        extended = build_module2_derived_metrics(_extended_momentum_row())
        repair = build_module2_derived_metrics(_oversold_repair_row())
        self.assertGreater(
            extended.get("regime_extended_tape") or 0.0,
            repair.get("regime_extended_tape") or 0.0,
        )
        self.assertGreater(
            repair.get("regime_oversold_daily") or 0.0,
            extended.get("regime_oversold_daily") or 0.0,
        )


if __name__ == "__main__":
    unittest.main()
