import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_analysis_scripts.trading_view_field_semantic_classifier import (
    classify_field_semantics,
    classify_semantic_bucket,
    mode_bucket,
    parse_name,
)


class TradingViewFieldSemanticClassifierTests(unittest.TestCase):
    def test_parse_name_with_timeframe_and_lag(self):
        base, timeframe, lag = parse_name("RSI21[1]|1M")
        self.assertEqual(base, "RSI21")
        self.assertEqual(timeframe, "1M")
        self.assertEqual(lag, "1")

    def test_rsi_maps_to_technical_oscillator(self):
        semantics = classify_field_semantics("RSI", "Relative Strength Index (14)", "number")
        self.assertEqual(semantics.semantic_bucket, "technical_oscillator")
        self.assertEqual(semantics.indicator_family, "rsi")

    def test_market_cap_basic_maps_to_volume_liquidity(self):
        bucket, tags = classify_semantic_bucket(
            "market_cap_basic",
            "Market Capitalization",
            "fundamental_price",
        )
        self.assertEqual(bucket, "volume_liquidity")
        self.assertIn("size", tags)

    def test_adrp_maps_to_volatility_risk(self):
        semantics = classify_field_semantics("ADRP|1W", "Average Day Range %", "percent")
        self.assertEqual(semantics.semantic_bucket, "volatility_risk")
        self.assertEqual(semantics.base_name, "ADRP")
        self.assertEqual(semantics.timeframe_class, "weekly")

    def test_earnings_release_next_date_maps_to_event_catalyst(self):
        semantics = classify_field_semantics(
            "earnings_release_next_date",
            "Next Earnings Date",
            "time",
        )
        self.assertEqual(semantics.semantic_bucket, "event_catalyst")

    def test_mode_bucket_reports_conflict(self):
        bucket, conflict = mode_bucket(
            ["volatility_risk", "volatility_risk", "momentum_performance"]
        )
        self.assertEqual(bucket, "volatility_risk")
        self.assertFalse(conflict)

        tied_bucket, tied_conflict = mode_bucket(["volatility_risk", "momentum_performance"])
        self.assertTrue(tied_conflict)


if __name__ == "__main__":
    unittest.main()
