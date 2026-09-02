import importlib.util
import sys
import unittest
from pathlib import Path

UTILS = (
    Path(__file__).resolve().parents[2]
    / "logs"
    / "AI_ANALYSIS_UTILS"
    / "briefing_reconciliation.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("briefing_reconciliation", UTILS)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["briefing_reconciliation"] = module
    spec.loader.exec_module(module)
    return module


class TestContinuityMapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_self_check_passes(self):
        self.assertEqual(self.mod.self_check(), 0)

    def test_naive_trap_is_exit_but_continuity_holds_leftover_book(self):
        row = self.mod.apply_continuity(
            {
                "prior_operator_action": "ADD",
                "prior_invalidation": 18.5,
                "prior_mix_signal": "avoid_value_trap",
                "current_mix_signal": "avoid_value_trap",
                "close": 21.66,
                "above_sma50": True,
                "weeks_ras": 0.42,
                "months_ras": 0.20,
                "vs_cost_pct": -1.6,
                "rsi": 56.0,
                "range_52w": 0.30,
            }
        )
        self.assertEqual(row["naive_action"], "EXIT")
        self.assertEqual(row["continuity_action"], "HOLD_NO_ADD")
        self.assertTrue(row["polar_if_naive"])
        self.assertTrue(row["publish_blocked"])

    def test_invalidation_hit_allows_exit(self):
        row = self.mod.apply_continuity(
            {
                "prior_operator_action": "ADD",
                "prior_invalidation": 22.0,
                "prior_mix_signal": "avoid_value_trap",
                "current_mix_signal": "avoid_value_trap",
                "close": 21.0,
                "above_sma50": False,
                "weeks_ras": 0.10,
                "months_ras": 0.05,
                "vs_cost_pct": -8.0,
            }
        )
        self.assertEqual(row["continuity_action"], "EXIT")
        self.assertTrue(row["thesis_kill"])
        self.assertFalse(row["publish_blocked"])

    def test_classify_polar(self):
        self.assertEqual(self.mod.classify_transition("ADD", "EXIT"), "polar_flip")
        self.assertEqual(self.mod.classify_transition("ADD", "HOLD_NO_ADD"), "conservative_downgrade")
        self.assertEqual(self.mod.classify_transition("EXIT", "EXIT"), "stable")
