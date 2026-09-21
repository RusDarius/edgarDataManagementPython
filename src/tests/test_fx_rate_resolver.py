import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import requests

from data_loaders.fx_rate_resolver import (
    FRANKFURTER_API_BASE,
    FxRateQuote,
    clear_fx_lookup_failures,
    convert_amount,
    get_fx_rate,
    normalize_currency_code,
)


def _ok_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class TestFxRateResolver(unittest.TestCase):
    def setUp(self):
        clear_fx_lookup_failures()

    def tearDown(self):
        clear_fx_lookup_failures()

    def test_normalize_currency_code(self):
        self.assertEqual(normalize_currency_code(" usd "), "USD")
        self.assertEqual(normalize_currency_code(None), "USD")

    def test_identity_rate_when_currencies_match(self):
        quote = get_fx_rate("USD", "USD")
        self.assertEqual(quote.rate, 1.0)
        self.assertEqual(quote.provider, "identity")

    @patch("data_loaders.fx_rate_resolver.requests.get")
    def test_get_fx_rate_uses_frankfurter_latest(self, mock_get):
        mock_get.return_value = _ok_response(
            {
                "amount": 1.0,
                "base": "EUR",
                "date": "2026-06-16",
                "rates": {"USD": 1.08},
            }
        )

        quote = get_fx_rate("EUR", "USD")
        self.assertEqual(quote.rate, 1.08)
        self.assertEqual(quote.rate_date, "2026-06-16")
        self.assertEqual(quote.provider, "frankfurter")
        mock_get.assert_called_once()
        self.assertIn("/v1/latest", mock_get.call_args.args[0])
        self.assertTrue(mock_get.call_args.args[0].startswith(FRANKFURTER_API_BASE))

    @patch("data_loaders.fx_rate_resolver.requests.get")
    def test_convert_amount_uses_cached_quote(self, mock_get):
        mock_get.return_value = _ok_response(
            {
                "amount": 1.0,
                "base": "EUR",
                "date": "2026-06-16",
                "rates": {"USD": 2.0},
            }
        )

        cache: dict[tuple[str, str, str | None], FxRateQuote] = {}
        converted, quote = convert_amount(100.0, "EUR", "USD", rate_cache=cache)
        self.assertEqual(converted, 200.0)
        self.assertEqual(quote.rate, 2.0)
        self.assertEqual(len(cache), 1)
        convert_amount(50.0, "EUR", "USD", rate_cache=cache)
        mock_get.assert_called_once()

    @patch("data_loaders.fx_rate_resolver.requests.get")
    def test_get_fx_rate_historical_endpoint(self, mock_get):
        mock_get.return_value = _ok_response(
            {
                "amount": 1.0,
                "base": "RON",
                "date": "2026-06-10",
                "rates": {"USD": 0.23},
            }
        )

        quote = get_fx_rate("RON", "USD", as_of_date=date(2026, 6, 10))
        self.assertEqual(quote.rate, 0.23)
        self.assertIn("2026-06-10", mock_get.call_args.args[0])

    @patch("data_loaders.fx_rate_resolver.time.sleep")
    @patch("data_loaders.fx_rate_resolver.requests.get")
    def test_retries_timeout_then_succeeds(self, mock_get, mock_sleep):
        mock_get.side_effect = [
            requests.exceptions.ReadTimeout("timed out"),
            _ok_response(
                {
                    "amount": 1.0,
                    "base": "EUR",
                    "date": "2026-09-14",
                    "rates": {"USD": 1.16},
                }
            ),
        ]

        quote = get_fx_rate("EUR", "USD")
        self.assertEqual(quote.rate, 1.16)
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once()

    @patch("data_loaders.fx_rate_resolver.time.sleep")
    @patch("data_loaders.fx_rate_resolver.requests.get")
    def test_dated_timeout_falls_back_to_latest(self, mock_get, mock_sleep):
        timeout = requests.exceptions.ReadTimeout("timed out")
        mock_get.side_effect = [
            timeout,
            timeout,
            _ok_response(
                {
                    "amount": 1.0,
                    "base": "DKK",
                    "date": "2026-09-11",
                    "rates": {"USD": 0.155},
                }
            ),
        ]

        quote = get_fx_rate("DKK", "USD", as_of_date=date(2026, 9, 14))
        self.assertEqual(quote.rate, 0.155)
        self.assertIn("/2026-09-14", mock_get.call_args_list[0].args[0])
        self.assertIn("/latest", mock_get.call_args_list[-1].args[0])

    @patch("data_loaders.fx_rate_resolver.time.sleep")
    @patch("data_loaders.fx_rate_resolver.requests.get")
    def test_failed_lookup_is_not_retried(self, mock_get, mock_sleep):
        mock_get.side_effect = requests.exceptions.ReadTimeout("timed out")

        with self.assertRaises(requests.exceptions.ReadTimeout):
            get_fx_rate("EUR", "USD")
        first_calls = mock_get.call_count
        self.assertGreater(first_calls, 0)

        with self.assertRaises(requests.exceptions.ReadTimeout):
            get_fx_rate("EUR", "USD")
        self.assertEqual(mock_get.call_count, first_calls)
