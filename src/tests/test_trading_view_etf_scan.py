import json
import unittest
from unittest.mock import Mock, patch

from data_loaders.api_tradingview_client import (
    AMERICA_ETF_EMPTY_STOCK_COLUMNS,
    AMERICA_ETF_MOVE_PREDICTION_BASE_PAYLOAD,
    AMERICA_ETF_MOVE_PREDICTION_COLUMNS,
    ApiTradingViewClient,
    GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD,
    TRADINGVIEW_GLOBAL_ETF_SCAN_URL,
)
from constants.trading_view_constants import TRADING_VIEW_ALL_MARKETS_ARRAY


class TestGlobalEtfMovePredictionPayload(unittest.TestCase):
    def test_uses_global_etf_screener_url(self):
        self.assertEqual(
            TRADINGVIEW_GLOBAL_ETF_SCAN_URL,
            "https://scanner.tradingview.com/global/scan?label-product=screener-etf",
        )

    def test_payload_shell_matches_stock_scan(self):
        stock_payload = GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD
        etf_payload = AMERICA_ETF_MOVE_PREDICTION_BASE_PAYLOAD
        self.assertEqual(etf_payload["ignore_unknown_fields"], True)
        self.assertEqual(etf_payload["options"], stock_payload["options"])
        self.assertEqual(etf_payload["price_conversion"], stock_payload["price_conversion"])
        self.assertEqual(etf_payload["symbols"], {})
        self.assertEqual(etf_payload["markets"], list(TRADING_VIEW_ALL_MARKETS_ARRAY))
        self.assertEqual(
            etf_payload["filter"],
            [{"left": "is_primary", "operation": "equal", "right": True}],
        )

    def test_filter2_selects_etf_funds_only(self):
        filter2 = AMERICA_ETF_MOVE_PREDICTION_BASE_PAYLOAD["filter2"]
        encoded = json.dumps(filter2)
        self.assertIn('"right": "fund"', encoded)
        self.assertIn('"right": ["etf"]', encoded)
        self.assertNotIn('"right": "stock"', encoded)
        self.assertNotIn("has_none_of", encoded)

    def test_payload_omits_stock_fields_that_returned_no_etf_values(self):
        columns = set(AMERICA_ETF_MOVE_PREDICTION_COLUMNS)
        for field_name in AMERICA_ETF_EMPTY_STOCK_COLUMNS:
            self.assertNotIn(field_name, columns)

    def test_payload_keeps_etf_structure_and_stock_technical_fields(self):
        columns = AMERICA_ETF_MOVE_PREDICTION_COLUMNS
        for field_name in (
            "name",
            "ticker-view",
            "aum",
            "nav",
            "expense_ratio",
            "fund_flows.1M",
            "nav_discount_premium",
            "category",
            "category.tr",
            "focus",
            "asset_class.tr",
            "Perf.5D",
            "relative_volume_10d_calc",
            "Recommend.All",
        ):
            self.assertIn(field_name, columns)

    def test_sorts_by_aum_instead_of_market_cap(self):
        self.assertEqual(
            AMERICA_ETF_MOVE_PREDICTION_BASE_PAYLOAD["sort"],
            {"sortBy": "aum", "sortOrder": "desc"},
        )


class TestScanGlobalEtfMovePrediction(unittest.TestCase):
    def _response_payload(self) -> dict:
        columns = AMERICA_ETF_MOVE_PREDICTION_COLUMNS
        values = [None] * len(columns)
        values[columns.index("name")] = "VOO"
        values[columns.index("aum")] = 1_000_000_000_000
        values[columns.index("close")] = 500.0
        return {
            "totalCount": 1,
            "data": [{"s": "AMEX:VOO", "d": values}],
        }

    @patch("data_loaders.api_tradingview_client.requests.post")
    def test_posts_aum_filter_and_etf_url(self, post_mock: Mock):
        response = Mock()
        response.json.return_value = self._response_payload()
        response.raise_for_status.return_value = None
        post_mock.return_value = response

        client = ApiTradingViewClient(user_agent="test-agent")
        payload = client.scan_global_etf_move_prediction(
            min_aum_usd=500_000_000,
            categories=["Equity"],
        )

        post_mock.assert_called_once()
        call_kwargs = post_mock.call_args
        self.assertEqual(call_kwargs.args[0], TRADINGVIEW_GLOBAL_ETF_SCAN_URL)
        request_payload = json.loads(call_kwargs.kwargs["data"])
        self.assertEqual(request_payload["markets"], list(TRADING_VIEW_ALL_MARKETS_ARRAY))
        self.assertIn(
            {"left": "is_primary", "operation": "equal", "right": True},
            request_payload["filter"],
        )
        self.assertIn(
            {"left": "aum", "operation": "egreater", "right": 500_000_000},
            request_payload["filter"],
        )
        self.assertIn(
            {"left": "category", "operation": "in_range", "right": ["Equity"]},
            request_payload["filter"],
        )
        self.assertNotIn("market_cap_basic", json.dumps(request_payload["filter"]))
        self.assertEqual(payload["data"][0]["symbol"], "AMEX:VOO")
        self.assertEqual(payload["data"][0]["name"], "VOO")
        self.assertEqual(payload["data"][0]["aum"], 1_000_000_000_000)
        self.assertEqual(
            payload["request_metadata"]["url"], TRADINGVIEW_GLOBAL_ETF_SCAN_URL
        )

    @patch("data_loaders.api_tradingview_client.requests.post")
    def test_market_cap_alias_filters_aum(self, post_mock: Mock):
        response = Mock()
        response.json.return_value = self._response_payload()
        response.raise_for_status.return_value = None
        post_mock.return_value = response

        client = ApiTradingViewClient(user_agent="test-agent")
        client.scan_global_etf_move_prediction(min_market_cap_usd=1_000_000_000)

        request_payload = json.loads(post_mock.call_args.kwargs["data"])
        self.assertIn(
            {"left": "aum", "operation": "egreater", "right": 1_000_000_000},
            request_payload["filter"],
        )

    @patch("data_loaders.api_tradingview_client.requests.post")
    def test_america_alias_uses_global_primary_universe(self, post_mock: Mock):
        response = Mock()
        response.json.return_value = self._response_payload()
        response.raise_for_status.return_value = None
        post_mock.return_value = response

        client = ApiTradingViewClient(user_agent="test-agent")
        client.scan_america_etf_move_prediction(min_aum_usd=500_000_000)

        self.assertEqual(post_mock.call_args.args[0], TRADINGVIEW_GLOBAL_ETF_SCAN_URL)
        request_payload = json.loads(post_mock.call_args.kwargs["data"])
        self.assertEqual(request_payload["markets"], list(TRADING_VIEW_ALL_MARKETS_ARRAY))
        self.assertIn(
            {"left": "is_primary", "operation": "equal", "right": True},
            request_payload["filter"],
        )


if __name__ == "__main__":
    unittest.main()
