import unittest
from pathlib import Path

from data_analysis_scripts.trading_view_move_prediction_overlap_report import (
    CRITICAL_PAIR_GATES,
    build_move_prediction_overlap_report,
    build_profile_rankings_for_horizon,
    compare_suite_overlap_reports,
    evaluate_overlap_gates,
    run_suite_overlap_report,
)
from data_analysis_scripts.trading_view_move_prediction_profile_config import (
    ProfileConfigRegistry,
    get_default_profile_registry,
    load_profile_suite,
)

CONFIG_ROOT = (
    Path(__file__).resolve().parents[2] / "config" / "move_prediction_profiles"
)


def _breakout_row() -> dict:
    return {
        "symbol": "NASDAQ:BREAK",
        "name": "Breakout Corp",
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
        "Perf.1M": 15.0,
        "Perf.3M": 35.0,
        "Perf.6M": 50.0,
        "Perf.Y": 80.0,
        "relative_volume_10d_calc": 2.2,
        "volume_trend": 1.5,
        "change": 2.5,
        "Aroon.Up": 90.0,
        "Aroon.Down": 20.0,
        "ADX+DI": 35.0,
        "ADX-DI": 15.0,
        "SMA10": 115.0,
        "SMA20": 112.0,
        "SMA50": 100.0,
        "SMA200": 80.0,
        "EMA10": 116.0,
        "EMA20": 113.0,
        "EMA50": 105.0,
        "EMA200": 82.0,
        "DonchCh20.Upper": 125.0,
        "DonchCh20.Lower": 95.0,
        "P.SAR": 110.0,
        "ChaikinMoneyFlow": 0.25,
        "return_on_invested_capital": 8.0,
        "piotroski_f_score_ttm": 5.0,
        "free_cash_flow_margin_ttm": 12.0,
    }


def _quality_continuation_row() -> dict:
    row = _breakout_row()
    row.update(
        {
            "symbol": "NASDAQ:QCONT",
            "name": "Quality Continuation",
            "return_on_invested_capital": 22.0,
            "piotroski_f_score_ttm": 8.0,
            "free_cash_flow_margin_ttm": 25.0,
            "revenue_per_employee": 500_000.0,
            "ebitda_per_employee": 120_000.0,
            "operating_margin": 28.0,
            "eps_forward_growth": 18.0,
            "Perf.W": 9.0,
            "relative_volume_10d_calc": 1.6,
        }
    )
    return row


def _turnaround_row() -> dict:
    return {
        "symbol": "NASDAQ:TURN",
        "name": "Turnaround Inc",
        "market_cap_basic": 2_000_000_000,
        "close": 18.0,
        "price_52_week_high": 45.0,
        "price_52_week_low": 15.0,
        "RSI": 38.0,
        "Stoch.RSI.K": 28.0,
        "Stoch.RSI.D": 22.0,
        "Perf.5D": 3.5,
        "Perf.W": 4.0,
        "Perf.1M": -5.0,
        "Perf.6M": -20.0,
        "Perf.Y": -35.0,
        "relative_volume_10d_calc": 1.4,
        "volume_trend": 1.2,
        "total_revenue_qoq_growth_fq": 8.0,
        "ebitda_qoq_growth_fq": 12.0,
        "net_income_qoq_growth_fq": 15.0,
        "free_cash_flow_qoq_growth_fq": 10.0,
        "eps_forward_growth": 20.0,
        "eps_surprise_percent_fq": 5.0,
        "price_book_fq": 0.8,
        "earnings_yield": 12.0,
        "enterprise_value_ebitda_ttm": 5.0,
        "piotroski_f_score_ttm": 6.0,
        "altman_z_score_ttm": 2.8,
        "total_debt_to_ebitda_fq": 2.5,
        "SMA10": 17.0,
        "SMA20": 17.5,
        "SMA50": 20.0,
        "SMA200": 25.0,
        "EMA10": 17.2,
        "EMA20": 17.8,
        "EMA50": 19.5,
        "EMA200": 24.0,
    }


def _static_value_row() -> dict:
    return {
        "symbol": "NASDAQ:CHEAP",
        "name": "Cheap Static",
        "market_cap_basic": 1_500_000_000,
        "close": 22.0,
        "price_52_week_high": 35.0,
        "price_52_week_low": 20.0,
        "Perf.Y": -8.0,
        "Perf.6M": -5.0,
        "price_book_fq": 0.6,
        "earnings_yield": 14.0,
        "enterprise_value_to_free_cash_flow_ttm": 6.0,
        "graham_value_gap": 25.0,
        "book_value_discount": 30.0,
        "return_on_invested_capital": 14.0,
        "piotroski_f_score_ttm": 7.0,
        "free_cash_flow_margin_ttm": 18.0,
        "low_relative_volume": 1.0,
        "altman_z_score_ttm": 3.2,
        "debt_to_equity": 0.4,
    }


def _fortress_row() -> dict:
    return {
        "symbol": "NASDAQ:FORT",
        "name": "Fortress Utilities",
        "market_cap_basic": 8_000_000_000,
        "close": 55.0,
        "beta_1_year": 0.45,
        "altman_z_score_ttm": 4.5,
        "net_cash_to_market_cap": 0.15,
        "cash_dividend_coverage_ratio_ttm": 2.5,
        "dps_common_stock_prim_issue_yoy_growth_fy": 6.0,
        "dividend_yield_recent": 3.2,
        "free_cash_flow_margin_ttm": 15.0,
        "return_on_invested_capital": 12.0,
        "piotroski_f_score_ttm": 7.0,
        "volume_trend": 1.1,
        "relative_volume_10d_calc": 1.05,
        "Perf.W": 1.5,
        "Perf.1M": 2.0,
        "SMA50": 53.0,
        "SMA200": 50.0,
        "EMA50": 53.5,
        "EMA200": 50.5,
        "debt_to_equity": 0.3,
        "current_ratio": 2.0,
    }


def _pullback_reversal_row() -> dict:
    return {
        "symbol": "NASDAQ:REV",
        "name": "Pullback Reversal",
        "market_cap_basic": 2_000_000_000,
        "close": 45.0,
        "price_52_week_high": 90.0,
        "price_52_week_low": 40.0,
        "RSI": 32.0,
        "RSI7": 28.0,
        "Stoch.RSI.K": 22.0,
        "Stoch.RSI.D": 18.0,
        "Perf.5D": 4.5,
        "Perf.W": 6.0,
        "Perf.1M": -12.0,
        "Perf.3M": -18.0,
        "Perf.6M": -8.0,
        "Perf.Y": -5.0,
        "relative_volume_10d_calc": 1.8,
        "volume_trend": 1.2,
        "change": 1.8,
        "Aroon.Up": 35.0,
        "Aroon.Down": 75.0,
        "ADX+DI": 18.0,
        "ADX-DI": 28.0,
        "SMA10": 44.0,
        "SMA20": 46.0,
        "SMA50": 52.0,
        "SMA200": 65.0,
        "EMA10": 44.5,
        "EMA20": 46.5,
        "EMA50": 53.0,
        "EMA200": 66.0,
        "DonchCh20.Upper": 48.0,
        "DonchCh20.Lower": 41.0,
        "P.SAR": 47.0,
        "ChaikinMoneyFlow": 0.08,
        "BB.upper": 48.0,
        "BB.lower": 42.0,
        "altman_z_score_ttm": 2.8,
        "total_debt_to_ebitda_fq": 2.5,
        "return_on_invested_capital": 6.0,
        "piotroski_f_score_ttm": 4.0,
        "free_cash_flow_margin_ttm": 8.0,
    }


def _filler_rows(count: int = 12) -> list[dict]:
    rows: list[dict] = []
    for index in range(count):
        rows.append(
            {
                "symbol": f"NASDAQ:FILL{index:02d}",
                "name": f"Filler {index}",
                "market_cap_basic": 1_200_000_000 + index * 100_000_000,
                "close": 30.0 + index,
                "Perf.W": 0.5,
                "Perf.1M": 1.0,
                "relative_volume_10d_calc": 0.9,
                "return_on_invested_capital": 5.0,
            }
        )
    return rows


class TestActiveManagerV4Suite(unittest.TestCase):
    def test_active_manager_v4_has_eight_profiles(self):
        suite = load_profile_suite(CONFIG_ROOT / "suites" / "active_manager_v4.json")
        self.assertEqual(suite["suite_id"], "active_manager_v4")
        self.assertEqual(len(suite["profile_names"]), 8)
        self.assertAlmostEqual(
            sum(suite["consensus_profile_weights"].values()), 1.0, places=6
        )
        self.assertIn("upside_reversal_v1", suite["profile_names"])
        self.assertIn("upside_reversal_v1", suite["long_consensus_profiles"])


class TestActiveManagerV3Suite(unittest.TestCase):
    def test_active_manager_v3_has_eleven_profiles(self):
        suite = load_profile_suite(CONFIG_ROOT / "suites" / "active_manager_v3.json")
        self.assertEqual(suite["suite_id"], "active_manager_v3")
        self.assertEqual(len(suite["profile_names"]), 11)
        self.assertAlmostEqual(
            sum(suite["consensus_profile_weights"].values()), 1.0, places=6
        )
        self.assertIn("quality_continuation_v1", suite["profile_names"])
        self.assertIn("forward_edge_active_v2", suite["profile_names"])
        self.assertIn("value_recovery_v2", suite["profile_names"])
        self.assertIn("defensive_fortress_v2", suite["profile_names"])
        self.assertNotIn("pre_earnings_drift_v1", suite["profile_names"])
        self.assertNotIn("swing_reversal_v1", suite["profile_names"])

    def test_baseline_v2_matches_active_manager_v3_profiles(self):
        v3 = load_profile_suite(CONFIG_ROOT / "suites" / "active_manager_v3.json")
        baseline = load_profile_suite(CONFIG_ROOT / "suites" / "baseline_v2.json")
        self.assertEqual(baseline["profile_names"], v3["profile_names"])
        self.assertEqual(
            baseline["consensus_profile_weights"], v3["consensus_profile_weights"]
        )


class TestMovePredictionOverlapReport(unittest.TestCase):
    def setUp(self) -> None:
        self.scan_data = [
            _breakout_row(),
            _quality_continuation_row(),
            _turnaround_row(),
            _static_value_row(),
            _fortress_row(),
            *_filler_rows(),
        ]
        self.v3_suite = load_profile_suite(
            CONFIG_ROOT / "suites" / "active_manager_v3.json"
        )
        self.v2_suite = load_profile_suite(
            CONFIG_ROOT / "suites" / "active_manager_v2.json"
        )

    def test_overlap_report_structure(self):
        report = build_move_prediction_overlap_report(
            self.scan_data,
            self.v3_suite["profile_names"],
            top_n=3,
            profile_registry=self.v3_suite["registry"],
            horizon_names=["weeks"],
        )
        self.assertIn("horizons", report)
        self.assertIn("gate_failures", report)
        weeks = report["horizons"]["weeks"]
        self.assertGreater(len(weeks["pair_overlaps"]), 0)

    def test_v3_critical_pairs_pass_gates_on_fixture_universe(self):
        report = build_move_prediction_overlap_report(
            self.scan_data,
            self.v3_suite["profile_names"],
            top_n=5,
            profile_registry=self.v3_suite["registry"],
            horizon_names=["weeks", "months"],
        )
        for failure in report["gate_failures"]:
            if failure["gate"] == "critical_pair":
                pair = (failure["left_profile"], failure["right_profile"])
                self.assertIn(
                    pair,
                    CRITICAL_PAIR_GATES,
                    msg=f"Unexpected critical gate failure: {failure}",
                )

    def test_v3_has_fewer_profiles_than_v2(self):
        comparison = compare_suite_overlap_reports(
            self.scan_data,
            left_suite_path=CONFIG_ROOT / "suites" / "active_manager_v2.json",
            right_suite_path=CONFIG_ROOT / "suites" / "active_manager_v3.json",
            top_n=5,
        )
        self.assertEqual(comparison["left_suite_id"], "active_manager_v2")
        self.assertEqual(comparison["right_suite_id"], "active_manager_v3")
        left_pairs = len(
            comparison["left_horizons"]["weeks"]["pair_overlaps"]
        )
        right_pairs = len(
            comparison["right_horizons"]["weeks"]["pair_overlaps"]
        )
        self.assertGreater(left_pairs, right_pairs)

    def _score_symbol(
        self,
        scan_rows: list[dict],
        profile_name: str,
        horizon_name: str,
        symbol: str,
        *,
        profile_registry: ProfileConfigRegistry | None = None,
    ) -> float:
        registry = profile_registry or self.v3_suite["registry"]
        rankings = build_profile_rankings_for_horizon(
            scan_rows,
            [profile_name],
            horizon_name=horizon_name,
            top_n=len(scan_rows),
            profile_registry=registry,
        )
        for row in rankings[profile_name]:
            if row["symbol"] == symbol:
                return float(row["score"])
        return float("-inf")

    def test_breakout_ranks_breakout_row_highest(self):
        scan_rows = [_breakout_row(), *_filler_rows()]
        rankings = build_profile_rankings_for_horizon(
            scan_rows,
            ["breakout_long_v1"],
            horizon_name="weeks",
            top_n=3,
            profile_registry=self.v3_suite["registry"],
        )
        self.assertEqual(rankings["breakout_long_v1"][0]["symbol"], "NASDAQ:BREAK")

    def test_upside_reversal_prefers_pullback_row_over_breakout_row(self):
        v4_suite = load_profile_suite(
            CONFIG_ROOT / "suites" / "active_manager_v4.json"
        )
        scan_rows = [_breakout_row(), _pullback_reversal_row(), *_filler_rows()]
        rankings = build_profile_rankings_for_horizon(
            scan_rows,
            ["upside_reversal_v1"],
            horizon_name="weeks",
            top_n=3,
            profile_registry=v4_suite["registry"],
        )
        reversal_score = next(
            row["score"] for row in rankings["upside_reversal_v1"] if row["symbol"] == "NASDAQ:REV"
        )
        breakout_score = next(
            row["score"] for row in rankings["upside_reversal_v1"] if row["symbol"] == "NASDAQ:BREAK"
        )
        self.assertGreater(reversal_score, breakout_score)
        self.assertEqual(rankings["upside_reversal_v1"][0]["symbol"], "NASDAQ:REV")

    def test_quality_continuation_prefers_quality_row(self):
        scan_rows = [_breakout_row(), _quality_continuation_row(), *_filler_rows()]
        quality_score = self._score_symbol(
            scan_rows, "quality_continuation_v1", "months", "NASDAQ:QCONT"
        )
        breakout_score = self._score_symbol(
            scan_rows, "quality_continuation_v1", "months", "NASDAQ:BREAK"
        )
        self.assertGreater(quality_score, breakout_score)

    def test_value_recovery_beats_asymmetric_on_turnaround_row(self):
        scan_rows = [_turnaround_row(), _static_value_row(), *_filler_rows(6)]
        recovery_turn = self._score_symbol(
            scan_rows, "value_recovery_v2", "months", "NASDAQ:TURN"
        )
        asymmetric_turn = self._score_symbol(
            scan_rows, "asymmetric_value", "months", "NASDAQ:TURN"
        )
        self.assertGreater(recovery_turn, asymmetric_turn)

    def test_value_recovery_v3_prefers_turnaround_over_breakout(self):
        scan_rows = [_turnaround_row(), _breakout_row(), *_filler_rows(6)]
        registry = get_default_profile_registry()
        turnaround_score = self._score_symbol(
            scan_rows,
            "value_recovery_v3",
            "months",
            "NASDAQ:TURN",
            profile_registry=registry,
        )
        breakout_score = self._score_symbol(
            scan_rows,
            "value_recovery_v3",
            "months",
            "NASDAQ:BREAK",
            profile_registry=registry,
        )
        self.assertGreater(turnaround_score, breakout_score)

    def test_value_recovery_v3_prefers_turnaround_over_quality_continuation(self):
        scan_rows = [_turnaround_row(), _quality_continuation_row(), *_filler_rows(6)]
        registry = get_default_profile_registry()
        turnaround_score = self._score_symbol(
            scan_rows,
            "value_recovery_v3",
            "months",
            "NASDAQ:TURN",
            profile_registry=registry,
        )
        quality_score = self._score_symbol(
            scan_rows,
            "value_recovery_v3",
            "months",
            "NASDAQ:QCONT",
            profile_registry=registry,
        )
        self.assertGreater(turnaround_score, quality_score)

    def test_value_recovery_v3_still_beats_asymmetric_on_turnaround_row(self):
        scan_rows = [_turnaround_row(), _static_value_row(), *_filler_rows(6)]
        registry = get_default_profile_registry()
        recovery_turn = self._score_symbol(
            scan_rows,
            "value_recovery_v3",
            "months",
            "NASDAQ:TURN",
            profile_registry=registry,
        )
        asymmetric_turn = self._score_symbol(
            scan_rows, "asymmetric_value", "months", "NASDAQ:TURN"
        )
        self.assertGreater(recovery_turn, asymmetric_turn)

    def test_value_recovery_v3_breakout_overlap_gate_passes_fixture(self):
        scan_rows = [
            _breakout_row(),
            _turnaround_row(),
            _pullback_reversal_row(),
            _static_value_row(),
            *_filler_rows(12),
        ]
        report = build_move_prediction_overlap_report(
            scan_rows,
            ["breakout_long_v1", "value_recovery_v3", "upside_reversal_v1"],
            top_n=3,
            profile_registry=get_default_profile_registry(),
            horizon_names=["weeks", "months"],
        )
        for horizon_name in ("weeks",):
            for pair in report["horizons"][horizon_name]["pair_overlaps"]:
                profiles = {pair["left_profile"], pair["right_profile"]}
                if profiles == {"breakout_long_v1", "value_recovery_v3"}:
                    self.assertLessEqual(
                        pair["jaccard_top_n"],
                        CRITICAL_PAIR_GATES[("breakout_long_v1", "value_recovery_v3")],
                        msg=f"{horizon_name} breakout/value_recovery_v3 overlap too high: {pair}",
                    )

    def test_evaluate_overlap_gates_detects_violation(self):
        horizon_reports = {
            "weeks": {
                "pair_overlaps": [
                    {
                        "left_profile": "breakout_long_v1",
                        "right_profile": "quality_continuation_v1",
                        "jaccard_top_n": 0.9,
                    }
                ]
            }
        }
        failures = evaluate_overlap_gates(horizon_reports)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["gate"], "critical_pair")

    def test_run_suite_overlap_report_writes_json(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "overlap.json"
            report = run_suite_overlap_report(
                self.scan_data,
                profile_suite_path=CONFIG_ROOT / "suites" / "active_manager_v3.json",
                top_n=5,
                output_path=output_path,
            )
            self.assertTrue(output_path.exists())
            self.assertEqual(report["suite_id"], "active_manager_v3")


if __name__ == "__main__":
    unittest.main()
