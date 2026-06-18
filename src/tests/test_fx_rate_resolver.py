import unittest
from datetime import date
from unittest.mock import patch

from data_loaders.fx_rate_resolver import (
    FxRateQuote,
    convert_amount,
    get_fx_rate,
    normalize_currency_code,
)


class TestFxRateResolver(unittest.TestCase):
    def test_normalize_currency_code(self):
        self.assertEqual(normalize_currency_code(" usd "), "USD")
        self.assertEqual(normalize_currency_code(None), "USD")

    def test_identity_rate_when_currencies_match(self):
        quote = get_fx_rate("USD", "USD")
        self.assertEqual(quote.rate, 1.0)
        self.assertEqual(quote.provider, "identity")

    @patch("data_loaders.fx_rate_resolver.requests.get")
    def test_get_fx_rate_uses_frankfurter_latest(self, mock_get):
        mock_get.return_value.json.return_value = {
            "amount": 1.0,
            "base": "EUR",
            "date": "2026-06-16",
            "rates": {"USD": 1.08},
        }
        mock_get.return_value.raise_for_status.return_value = None

        quote = get_fx_rate("EUR", "USD")
        self.assertEqual(quote.rate, 1.08)
        self.assertEqual(quote.rate_date, "2026-06-16")
        self.assertEqual(quote.provider, "frankfurter")
        mock_get.assert_called_once()
        self.assertIn("/latest", mock_get.call_args.args[0])

    @patch("data_loaders.fx_rate_resolver.requests.get")
    def test_convert_amount_uses_cached_quote(self, mock_get):
        mock_get.return_value.json.return_value = {
            "amount": 1.0,
            "base": "EUR",
            "date": "2026-06-16",
            "rates": {"USD": 2.0},
        }
        mock_get.return_value.raise_for_status.return_value = None

        cache: dict[tuple[str, str, str | None], FxRateQuote] = {}
        converted, quote = convert_amount(100.0, "EUR", "USD", rate_cache=cache)
        self.assertEqual(converted, 200.0)
        self.assertEqual(quote.rate, 2.0)
        self.assertEqual(len(cache), 1)

    @patch("data_loaders.fx_rate_resolver.requests.get")
    def test_get_fx_rate_historical_endpoint(self, mock_get):
        mock_get.return_value.json.return_value = {
            "amount": 1.0,
            "base": "RON",
            "date": "2026-06-10",
            "rates": {"USD": 0.23},
        }
        mock_get.return_value.raise_for_status.return_value = None

        quote = get_fx_rate("RON", "USD", as_of_date=date(2026, 6, 10))
        self.assertEqual(quote.rate, 0.23)
        self.assertIn("2026-06-10", mock_get.call_args.args[0])
