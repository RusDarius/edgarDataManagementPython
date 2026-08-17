import csv
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from data_analysis_scripts.trading_view_holdings_scoring_analysis import (
    HoldingConfig,
    MarkToMarketContext,
    RawScanSymbolEntry,
    SCAN_KEY_ETF_BOOK,
    SCAN_KEY_MOVE_PREDICTION,
    _build_mark_to_market_fields,
    _resolve_close_quote_currency,
    default_enabled_scans,
    enrich_holdings_positions,
    holding_config_label,
    holding_matches_db_symbol,
    holding_matches_db_symbol_for_holding,
    holding_matches_db_symbol_strict,
    holding_ticker_keys,
    load_holdings_config,
    match_holdings_to_db_symbols,
    normalize_holding_ticker,
    normalize_instrument_type,
    resolve_latest_etf_scan_source,
    resolve_latest_move_prediction_source,
    run_holdings_scoring_analysis,
)
from data_analysis_scripts.trading_view_etf_analysis import (
    run_etf_analysis_suite_duckdb,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite_duckdb,
)


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _sample_scan_row(symbol: str, **overrides) -> dict:
    row = {
        "symbol": symbol,
        "name": f"{symbol} Corp",
        "sector": "Technology Services",
        "industry": "Software",
        "market": "america",
        "market_cap_basic": 2_500_000_000,
        "close": 42.5,
        "relative_volume_10d_calc": 1.8,
        "average_volume_10d_calc": 1_200_000,
        "average_volume_30d_calc": 1_000_000,
        "volume": 2_000_000,
        "float_shares_outstanding": 50_000_000,
        "Value.Traded": 15_000_000,
        "Perf.5D": 4.2,
        "Perf.W": 4.2,
        "Perf.1M": 8.5,
        "Perf.YTD": 18.0,
        "Perf.Y": 35.0,
        "Perf.5Y": 120.0,
        "change": 1.8,
        "gap": 1.2,
        "ATRP": 2.0,
        "premarket_change": 0.5,
        "ADX+DI": 28.0,
        "ADX-DI": 18.0,
        "Aroon.Up": 85.0,
        "Aroon.Down": 40.0,
        "BB.upper": 45.0,
        "BB.lower": 40.0,
        "SMA10": 41.0,
        "SMA20": 40.0,
        "EMA10": 41.5,
        "EMA20": 40.5,
        "price_earnings_ttm": 24.0,
        "total_revenue_yoy_growth_ttm": 14.0,
        "debt_to_equity": 0.25,
        "earnings_release_next_calendar_date": "2099-12-31",
    }
    row.update(overrides)
    return row


def _etf_scan_row(symbol: str, name: str, **overrides) -> dict:
    payload = {
        "symbol": symbol,
        "name": name,
        "description": f"{name} Fund",
        "category": "Equity",
        "focus": "Large Cap",
        "asset_class": "Equity",
        "brand": "Test",
        "sector": "Miscellaneous",
        "industry": "Investment Trusts/Mutual Funds",
        "market": "america",
        "aum": 10_000_000_000,
        "nav": 100.0,
        "expense_ratio": 0.03,
        "nav_discount_premium": 0.1,
        "close": 100.0,
        "SMA20": 99.0,
        "SMA50": 98.0,
        "SMA200": 95.0,
        "RSI": 55.0,
        "ADX": 22.0,
        "Recommend.All": 0.2,
        "Recommend.All|1W": 0.15,
        "relative_volume_10d_calc": 1.1,
        "fund_flows.1M": 100_000_000,
        "fund_flows.3M": 250_000_000,
        "ChaikinMoneyFlow": 0.1,
        "Perf.5D": 1.0,
        "Perf.W": 1.2,
        "Perf.1M": 3.0,
        "Perf.3M": 6.0,
        "Perf.6M": 8.0,
        "Perf.YTD": 10.0,
        "Perf.Y": 12.0,
        "Perf.3Y": 30.0,
        "Perf.5Y": 50.0,
        "nav_total_return.1M": 2.8,
        "nav_total_return.3M": 5.5,
        "aum_perf.1M": 3.5,
        "aum_perf.3M": 7.0,
        "Value.Traded": 50_000_000,
        "price_52_week_high": 110.0,
        "High.1M": 102.0,
        "MACD.macd": 0.4,
        "MACD.signal": 0.2,
    }
    payload.update(overrides)
    return payload


class TestHoldingTickerMatching(unittest.TestCase):
    def test_normalize_holding_ticker_uppercases(self):
        self.assertEqual(normalize_holding_ticker(" nasdaq:mu "), "NASDAQ:MU")

    def test_holding_ticker_keys_includes_short_symbol(self):
        self.assertEqual(holding_ticker_keys("NASDAQ:MU"), {"NASDAQ:MU", "MU"})

    def test_holding_matches_db_symbol_across_exchange_prefix(self):
        self.assertTrue(holding_matches_db_symbol("NASDAQ:MU", "MU"))
        self.assertTrue(holding_matches_db_symbol("MU", "NASDAQ:MU"))
        self.assertFalse(holding_matches_db_symbol("NASDAQ:MU", "AMD"))

    def test_holding_matches_db_symbol_strict_rejects_cross_exchange(self):
        self.assertTrue(holding_matches_db_symbol_strict("NYSE:CF", "NYSE:CF"))
        self.assertFalse(holding_matches_db_symbol_strict("NYSE:CF", "NASDAQ:CF"))
        self.assertFalse(holding_matches_db_symbol_strict("NYSE:CF", "CF"))

    def test_holding_matches_db_symbol_for_holding_uses_symbol_when_set(self):
        holding = HoldingConfig(ticker="CF", symbol="NYSE:CF")
        self.assertTrue(holding_matches_db_symbol_for_holding(holding, "NYSE:CF"))
        self.assertFalse(holding_matches_db_symbol_for_holding(holding, "NASDAQ:CF"))
        self.assertFalse(holding_matches_db_symbol_for_holding(holding, "CF"))

    def test_holding_config_label_includes_symbol_when_distinct(self):
        self.assertEqual(
            holding_config_label(HoldingConfig(ticker="CF", symbol="NYSE:CF")),
            "CF [NYSE:CF]",
        )

    def test_match_holdings_to_db_symbols_prefers_one_to_one(self):
        holdings = [
            HoldingConfig(ticker="NASDAQ:AAA"),
            HoldingConfig(ticker="BBB"),
        ]
        matched, unmatched = match_holdings_to_db_symbols(
            holdings,
            ["AAA", "NASDAQ:BBB"],
        )
        self.assertEqual(unmatched, [])
        self.assertEqual({match.db_symbol for match in matched}, {"AAA", "NASDAQ:BBB"})

    def test_match_holdings_to_db_symbols_respects_symbol_exchange(self):
        catalog = [
            RawScanSymbolEntry(
                symbol="NASDAQ:CF",
                company="Other CF",
                bare_ticker="CF",
            ),
            RawScanSymbolEntry(
                symbol="NYSE:CF",
                company="CF Industries Holdings, Inc.",
                bare_ticker="CF",
            ),
        ]
        holdings = [HoldingConfig(ticker="CF", symbol="NYSE:CF")]
        matched, unmatched = match_holdings_to_db_symbols(
            holdings,
            ["CF"],
            raw_catalog=catalog,
        )
        self.assertEqual(unmatched, [])
        self.assertEqual(matched[0].db_symbol, "CF")
        self.assertEqual(matched[0].scan_symbol, "NYSE:CF")
        self.assertEqual(matched[0].company, "CF Industries Holdings, Inc.")
        self.assertEqual(matched[0].lookup_key, "NYSE:CF")

    def test_build_mark_to_market_fields_uses_implied_shares_and_close(self):
        holding = HoldingConfig(
            ticker="MU",
            invested_sum=5000.0,
            average_price=100.0,
            currency="USD",
            implied_shares=50.0,
            invested_sum_usd=5000.0,
            fx_rate_to_usd=1.0,
        )
        fields = _build_mark_to_market_fields(holding, 110.0)
        self.assertEqual(fields["close_quote"], 110.0)
        self.assertEqual(fields["close"], 110.0)
        self.assertEqual(fields["close_usd"], 110.0)
        self.assertEqual(fields["current_value"], 5500.0)
        self.assertEqual(fields["current_value_usd"], 5500.0)
        self.assertEqual(fields["unrealized_pnl"], 500.0)
        self.assertAlmostEqual(fields["unrealized_return_pct"], 10.0)

    def test_resolve_close_quote_currency_from_omxcop_exchange(self):
        self.assertEqual(
            _resolve_close_quote_currency(exchange="OMXCOP", country="Denmark"),
            "DKK",
        )

    def test_build_mark_to_market_fields_keeps_dkk_close_for_dkk_holding(self):
        holding = HoldingConfig(
            ticker="NOVO_B",
            invested_sum=79329.0,
            average_price=353.28,
            invested_currency="DKK",
            price_currency="DKK",
            invested_sum_in_price_currency=79329.0,
            invested_sum_usd=12300.0,
            fx_rate_to_usd=0.15507,
            fx_rate_price_to_usd=0.15507,
        )
        fields = _build_mark_to_market_fields(
            holding,
            287.15,
            close_quote_currency="DKK",
        )
        self.assertEqual(fields["close_quote"], 287.15)
        self.assertEqual(fields["close_quote_currency"], "DKK")
        self.assertEqual(fields["close"], 287.15)
        self.assertAlmostEqual(fields["close_usd"], 287.15 * 0.15507, places=2)
        self.assertAlmostEqual(
            fields["current_value"], 79329.0 * (287.15 / 353.28), places=1
        )
        self.assertAlmostEqual(
            fields["current_value_usd"],
            fields["current_value"] * 0.15507,
            places=1,
        )
        self.assertAlmostEqual(fields["unrealized_return_pct"], -18.7, places=0)

    def test_resolve_close_quote_currency_from_euronext_exchange(self):
        self.assertEqual(
            _resolve_close_quote_currency(exchange="EURONEXT", country="Switzerland"),
            "EUR",
        )

    @patch("data_analysis_scripts.trading_view_holdings_scoring_analysis.convert_amount")
    def test_build_mark_to_market_fields_converts_dkk_quote_to_usd_holding(
        self, mock_convert
    ):
        mock_convert.return_value = (
            44.53,
            type("Quote", (), {"rate": 0.15507, "rate_date": "2026-06-17"})(),
        )
        holding = HoldingConfig(
            ticker="NOVO_B",
            invested_sum=11497.0,
            average_price=51.2,
            invested_currency="USD",
            price_currency="USD",
            invested_sum_in_price_currency=11497.0,
            invested_sum_usd=11497.0,
            fx_rate_to_usd=1.0,
            fx_rate_price_to_usd=1.0,
        )
        context = MarkToMarketContext(portfolio_currency="USD", resolve_fx_rates=True)
        fields = _build_mark_to_market_fields(
            holding,
            287.15,
            close_quote_currency="DKK",
            mtm_context=context,
        )
        self.assertEqual(fields["close_quote"], 287.15)
        self.assertAlmostEqual(fields["close"], 44.53, places=2)
        self.assertAlmostEqual(fields["close_usd"], 44.53, places=2)
        self.assertAlmostEqual(fields["unrealized_return_pct"], (44.53 / 51.2 - 1) * 100, places=1)


class TestHoldingsConfigLoader(unittest.TestCase):
    def test_load_holdings_config_parses_metadata(self):
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "holdings.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "holdings_scoring_v1",
                        "holdings_id": "test",
                        "holdings": [
                            {
                                "ticker": "NASDAQ:MU",
                                "sleeve": "tactical",
                                "invested_sum": 5000.0,
                                "average_price": 95.0,
                                "currency": "USD",
                                "notes": "memory",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            payload, holdings, cash_position = load_holdings_config(config_path)
            self.assertEqual(payload["holdings_id"], "test")
            self.assertEqual(len(holdings), 1)
            self.assertIsNone(cash_position)
            self.assertEqual(holdings[0].ticker, "NASDAQ:MU")
            self.assertEqual(holdings[0].sleeve, "tactical")
            self.assertEqual(holdings[0].invested_sum, 5000.0)
            self.assertEqual(holdings[0].average_price, 95.0)
            self.assertEqual(holdings[0].invested_currency, "USD")
            self.assertEqual(holdings[0].price_currency, "USD")
            self.assertEqual(holdings[0].currency, "USD")
            self.assertEqual(holdings[0].notes, "memory")
            self.assertEqual(holdings[0].instrument_type, "stock")

    def test_load_holdings_config_parses_instrument_type(self):
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "holdings.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "holdings_scoring_v1",
                        "holdings": [
                            {
                                "ticker": "MU",
                                "invested_sum": 100.0,
                                "average_price": 10.0,
                                "currency": "USD",
                            },
                            {
                                "ticker": "VOO",
                                "symbol": "AMEX:VOO",
                                "instrument_type": "ETF",
                                "invested_sum": 1000.0,
                                "average_price": 500.0,
                                "currency": "USD",
                            },
                            {
                                "ticker": "SPY",
                                "instrument_type": "fund",
                                "invested_sum": 500.0,
                                "average_price": 400.0,
                                "currency": "USD",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            _, holdings, _ = load_holdings_config(config_path)
            self.assertEqual(holdings[0].instrument_type, "stock")
            self.assertEqual(holdings[1].instrument_type, "etf")
            self.assertEqual(holdings[2].instrument_type, "etf")
            self.assertEqual(
                default_enabled_scans(holdings),
                (SCAN_KEY_MOVE_PREDICTION, SCAN_KEY_ETF_BOOK),
            )

    def test_load_holdings_config_resolves_split_currency_fields(self):
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "holdings.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "holdings_scoring_v1",
                        "holdings": [
                            {
                                "ticker": "STMPA",
                                "invested_sum": 2758.02,
                                "invested_currency": "EUR",
                                "average_price": 33.98,
                                "price_currency": "EUR",
                            },
                            {
                                "ticker": "NOVO_B",
                                "invested_sum": 79329,
                                "invested_currency": "DKK",
                                "average_price": 353.28,
                                "price_currency": "DKK",
                            },
                            {
                                "ticker": "MU",
                                "invested_sum": 100.0,
                                "average_price": 10.0,
                                "currency": "USD",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            _, holdings, _ = load_holdings_config(config_path)
            self.assertEqual(holdings[0].invested_currency, "EUR")
            self.assertEqual(holdings[0].price_currency, "EUR")
            self.assertEqual(holdings[1].invested_currency, "DKK")
            self.assertEqual(holdings[1].price_currency, "DKK")
            self.assertEqual(holdings[2].invested_currency, "USD")
            self.assertEqual(holdings[2].price_currency, "USD")

    def test_load_holdings_config_parses_cash_position(self):
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "holdings.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "holdings_scoring_v1",
                        "portfolio_currency": "USD",
                        "cash_position": {"value": 12500.5, "currency": "EUR"},
                        "holdings": [
                            {
                                "ticker": "MU",
                                "invested_sum": 100.0,
                                "average_price": 10.0,
                                "currency": "USD",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            payload, holdings, cash_position = load_holdings_config(config_path)
            self.assertEqual(len(holdings), 1)
            self.assertIsNotNone(cash_position)
            assert cash_position is not None
            self.assertEqual(cash_position.value, 12500.5)
            self.assertEqual(cash_position.currency, "EUR")
            self.assertEqual(
                payload["_resolved_cash_position"],
                {
                    "value": 12500.5,
                    "currency": "EUR",
                    "value_in_portfolio_currency": None,
                    "fx_rate_to_portfolio": None,
                    "fx_rate_date": None,
                },
            )

    def test_load_holdings_config_ignores_null_cash_value(self):
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "holdings.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "holdings_scoring_v1",
                        "cash_position": {"value": None, "currency": "USD"},
                        "holdings": [
                            {
                                "ticker": "MU",
                                "invested_sum": 100.0,
                                "average_price": 10.0,
                                "currency": "USD",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            _, _, cash_position = load_holdings_config(config_path)
            self.assertIsNone(cash_position)

    def test_load_holdings_config_parses_optional_symbol(self):
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "holdings.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "holdings_scoring_v1",
                        "holdings": [
                            {
                                "ticker": "CF",
                                "symbol": "NYSE:CF",
                                "invested_sum": 100.0,
                                "average_price": 10.0,
                                "currency": "USD",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            _, holdings, _ = load_holdings_config(config_path)
            self.assertEqual(holdings[0].ticker, "CF")
            self.assertEqual(holdings[0].symbol, "NYSE:CF")

    @patch("data_analysis_scripts.trading_view_holdings_scoring_analysis.convert_amount")
    def test_enrich_holdings_positions_computes_usd_and_weights(self, mock_convert):
        mock_convert.return_value = (1080.0, type("Quote", (), {"rate": 1.08, "rate_date": "2026-06-16"})())
        holdings = [
            HoldingConfig(
                ticker="AZM",
                invested_sum=1000.0,
                average_price=50.0,
                invested_currency="EUR",
                price_currency="EUR",
            ),
            HoldingConfig(
                ticker="MU",
                invested_sum=2000.0,
                average_price=100.0,
                invested_currency="USD",
                price_currency="USD",
            ),
        ]
        enriched, warnings = enrich_holdings_positions(holdings, portfolio_currency="USD")
        self.assertEqual(warnings, [])
        self.assertEqual(enriched[0].invested_sum_usd, 1080.0)
        self.assertEqual(enriched[0].implied_shares, 20.0)
        self.assertEqual(enriched[1].invested_sum_usd, 2000.0)
        self.assertAlmostEqual(enriched[0].portfolio_weight_pct, 1080.0 / 3080.0 * 100.0)
        self.assertAlmostEqual(enriched[1].portfolio_weight_pct, 2000.0 / 3080.0 * 100.0)


class TestInstrumentTypeNormalization(unittest.TestCase):
    def test_normalize_instrument_type_aliases(self):
        self.assertEqual(normalize_instrument_type(None), "stock")
        self.assertEqual(normalize_instrument_type(""), "stock")
        self.assertEqual(normalize_instrument_type("Equity"), "stock")
        self.assertEqual(normalize_instrument_type("ETF"), "etf")
        self.assertEqual(normalize_instrument_type("exchange-traded-fund"), "etf")
        self.assertEqual(normalize_instrument_type("etp"), "etf")
        self.assertEqual(normalize_instrument_type("bond"), "bond")

    def test_default_enabled_scans_follow_book_composition(self):
        stocks = [HoldingConfig(ticker="MU")]
        etfs = [HoldingConfig(ticker="VOO", instrument_type="etf")]
        self.assertEqual(default_enabled_scans(stocks), (SCAN_KEY_MOVE_PREDICTION,))
        self.assertEqual(default_enabled_scans(etfs), (SCAN_KEY_ETF_BOOK,))
        self.assertEqual(
            default_enabled_scans(stocks + etfs),
            (SCAN_KEY_MOVE_PREDICTION, SCAN_KEY_ETF_BOOK),
        )


@unittest.skipUnless(_duckdb_available(), "duckdb is not installed")
class TestHoldingsScoringAnalysisIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output_root = Path(self.temp_dir.name)

    def _build_database(self):
        scan_data = [
            _sample_scan_row(
                "NASDAQ:MU",
                name="Micron Technology, Inc.",
                **{"ticker-view": {"name": "MU", "description": "Micron"}},
            ),
            _sample_scan_row(
                "NYSE:WPM",
                name="Wheaton Precious Metals Corp",
                **{"ticker-view": {"name": "WPM"}},
            ),
        ]
        return run_full_analysis_suite_duckdb(
            scan_data=scan_data,
            profile_names=["breakout_long", "quality_value_compounder"],
            min_market_cap_usd=1_000_000_000,
            include_blind_spot_sections=False,
            output_dir=self.output_root,
            export_parquet=False,
            reference_time=datetime(2026, 6, 16, 15, 30, tzinfo=timezone.utc),
        )

    def _write_holdings_config(self, holdings: list[dict]) -> Path:
        config_path = self.output_root / "holdings.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": "holdings_scoring_v1",
                    "holdings_id": "test",
                    "holdings": holdings,
                }
            ),
            encoding="utf-8",
        )
        return config_path

    def test_resolve_latest_move_prediction_source_from_database(self):
        result = self._build_database()
        source = resolve_latest_move_prediction_source(
            database_path=result["_duckdb_database"],
            run_id=result["_duckdb_run_id"],
        )
        self.assertEqual(source.run_id, result["_duckdb_run_id"])
        self.assertEqual(len(source.profile_names), 2)

    def test_run_holdings_scoring_analysis_exports_expected_files(self):
        result = self._build_database()
        config_path = self._write_holdings_config(
            [
                {
                    "ticker": "NASDAQ:MU",
                    "sleeve": "tactical",
                    "invested_sum": 5000.0,
                    "average_price": 95.0,
                    "currency": "USD",
                },
                {
                    "ticker": "WPM",
                    "sleeve": "core",
                    "invested_sum": 3500.0,
                    "average_price": 78.0,
                    "currency": "USD",
                },
            ]
        )
        run_result = run_holdings_scoring_analysis(
            holdings_config_path=config_path,
            output_dir=self.output_root / "holdings_run",
            database_path=result["_duckdb_database"],
            run_id=result["_duckdb_run_id"],
            reference_time=datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc),
            resolve_fx_rates=False,
        )

        output_dir = Path(run_result["output_dir"])
        scan_dir = output_dir / "scans" / "move_prediction_v1"
        self.assertTrue((output_dir / "holdings_scoring__manifest.json").exists())
        self.assertTrue((output_dir / "holdings_scoring__overview.log").exists())
        self.assertTrue((output_dir / "holdings_scoring__shortlist.log").exists())
        self.assertTrue((scan_dir / "holdings__summary.csv").exists())
        self.assertTrue((scan_dir / "holdings__profile_horizons.csv").exists())
        self.assertTrue((scan_dir / "holdings__raw_perf.csv").exists())
        self.assertTrue((scan_dir / "holdings__unmatched.csv").exists())
        self.assertEqual(run_result["matched_count"], 2)
        self.assertEqual(run_result["unmatched_tickers"], [])

        with (scan_dir / "holdings__summary.csv").open(encoding="utf-8", newline="") as handle:
            summary_rows = list(csv.DictReader(handle))
        self.assertEqual(len(summary_rows), 2)
        self.assertIn("consensus_weeks_score", summary_rows[0])
        self.assertIn("Perf.1M", summary_rows[0])
        self.assertIn("invested_sum", summary_rows[0])
        self.assertIn("portfolio_weight_pct", summary_rows[0])
        self.assertIn("current_value", summary_rows[0])
        self.assertIn("current_value_usd", summary_rows[0])
        self.assertIn("close_usd", summary_rows[0])
        self.assertIn("close_quote", summary_rows[0])
        self.assertEqual(summary_rows[0]["instrument_type"], "stock")
        self.assertIsNotNone(summary_rows[0]["close"])
        self.assertGreater(float(summary_rows[0]["current_value_usd"]), 0.0)

        overview_text = (output_dir / "holdings_scoring__overview.log").read_text(
            encoding="utf-8"
        )
        self.assertIn("Mark-to-market (run close price)", overview_text)

        shortlist_text = (output_dir / "holdings_scoring__shortlist.log").read_text(
            encoding="utf-8"
        )
        self.assertIn("HOLDINGS SHORTLIST", shortlist_text)
        self.assertIn("PORTFOLIO AT A GLANCE", shortlist_text)
        self.assertIn("POSITIONS (largest current value first)", shortlist_text)
        self.assertIn("MU", shortlist_text)
        self.assertEqual(run_result["shortlist_log"], str(output_dir / "holdings_scoring__shortlist.log"))

        with (scan_dir / "holdings__profile_horizons.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            profile_rows = list(csv.DictReader(handle))
        self.assertGreater(len(profile_rows), 0)
        horizons = {row["horizon_name"] for row in profile_rows}
        self.assertTrue({"days", "weeks", "months", "years"} & horizons)

    def test_unmatched_ticker_lands_in_unmatched_csv(self):
        result = self._build_database()
        config_path = self._write_holdings_config(
            [
                {"ticker": "MU"},
                {"ticker": "MISSING"},
            ]
        )
        run_result = run_holdings_scoring_analysis(
            holdings_config_path=config_path,
            output_dir=self.output_root / "holdings_run_unmatched",
            database_path=result["_duckdb_database"],
            run_id=result["_duckdb_run_id"],
            reference_time=datetime(2026, 6, 17, 10, 5, tzinfo=timezone.utc),
            resolve_fx_rates=False,
        )

        unmatched_path = (
            Path(run_result["output_dir"])
            / "scans"
            / "move_prediction_v1"
            / "holdings__unmatched.csv"
        )
        with unmatched_path.open(encoding="utf-8", newline="") as handle:
            unmatched_rows = list(csv.DictReader(handle))
        self.assertEqual(len(unmatched_rows), 1)
        self.assertEqual(unmatched_rows[0]["config_ticker"], "MISSING")
        self.assertEqual(run_result["unmatched_tickers"], ["MISSING"])

    def _build_etf_database(self):
        scan_payload = {
            "data": [
                _etf_scan_row("AMEX:VOO", "VOO", **{"aum": 1_200_000_000_000, "close": 500.0}),
                _etf_scan_row("AMEX:QQQ", "QQQ", **{"aum": 250_000_000_000, "Perf.1M": 4.0}),
                _etf_scan_row("AMEX:XLK", "XLK", **{"aum": 70_000_000_000, "Perf.1M": 8.0}),
                _etf_scan_row(
                    "AMEX:TLT",
                    "TLT",
                    **{
                        "aum": 50_000_000_000,
                        "category": "Bond",
                        "focus": "Treasury",
                        "asset_class": "Fixed Income",
                        "Perf.1M": -1.0,
                    },
                ),
                _etf_scan_row("AMEX:IWM", "IWM", **{"aum": 60_000_000_000, "focus": "Small Cap"}),
            ],
            "request_metadata": {
                "url": "https://scanner.tradingview.com/global/scan?label-product=screener-etf",
                "columns": ["name", "aum", "close"],
            },
        }
        return run_etf_analysis_suite_duckdb(
            scan_data=scan_payload,
            min_aum_usd=1_000_000_000,
            output_dir=self.output_root / "etf_suite",
            run_label="etf_holdings_test",
            reference_time=datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc),
            export_parquet=False,
        )

    def test_etf_holding_joins_etf_scan_without_stock_database(self):
        etf_result = self._build_etf_database()
        config_path = self._write_holdings_config(
            [
                {
                    "ticker": "VOO",
                    "symbol": "AMEX:VOO",
                    "instrument_type": "etf",
                    "sleeve": "core",
                    "invested_sum": 10000.0,
                    "average_price": 480.0,
                    "currency": "USD",
                }
            ]
        )
        run_result = run_holdings_scoring_analysis(
            holdings_config_path=config_path,
            output_dir=self.output_root / "holdings_etf_only",
            etf_database_path=etf_result["_duckdb_database"],
            etf_run_id=etf_result["run_id"],
            reference_time=datetime(2026, 6, 17, 11, 0, tzinfo=timezone.utc),
            resolve_fx_rates=False,
        )

        output_dir = Path(run_result["output_dir"])
        scan_dir = output_dir / "scans" / SCAN_KEY_ETF_BOOK
        self.assertTrue((scan_dir / "holdings__summary.csv").exists())
        self.assertFalse((output_dir / "scans" / SCAN_KEY_MOVE_PREDICTION).exists())
        self.assertEqual(run_result["matched_count"], 1)
        self.assertEqual(run_result["unmatched_tickers"], [])
        self.assertIsNone(run_result["source"])
        self.assertEqual(run_result["etf_source"]["run_id"], etf_result["run_id"])

        with (scan_dir / "holdings__summary.csv").open(encoding="utf-8", newline="") as handle:
            summary_rows = list(csv.DictReader(handle))
        self.assertEqual(len(summary_rows), 1)
        self.assertEqual(summary_rows[0]["instrument_type"], "etf")
        self.assertEqual(summary_rows[0]["matched_symbol"], "AMEX:VOO")
        self.assertNotEqual(summary_rows[0].get("book_consensus"), "")
        self.assertGreater(float(summary_rows[0]["current_value_usd"]), 0.0)

        manifest = json.loads(
            (output_dir / "holdings_scoring__manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["enabled_scans"], [SCAN_KEY_ETF_BOOK])
        self.assertEqual(manifest["etf_holdings_count"], 1)
        self.assertEqual(manifest["stock_holdings_count"], 0)

        overview_text = (output_dir / "holdings_scoring__overview.log").read_text(
            encoding="utf-8"
        )
        self.assertIn("ETF analysis source", overview_text)
        shortlist_text = (output_dir / "holdings_scoring__shortlist.log").read_text(
            encoding="utf-8"
        )
        self.assertIn("VOO", shortlist_text)
        self.assertIn("ETF book tracking:", shortlist_text)

    def test_mixed_stock_and_etf_holdings_export_both_scans(self):
        stock_result = self._build_database()
        etf_result = self._build_etf_database()
        config_path = self._write_holdings_config(
            [
                {
                    "ticker": "NASDAQ:MU",
                    "instrument_type": "stock",
                    "sleeve": "tactical",
                    "invested_sum": 5000.0,
                    "average_price": 95.0,
                    "currency": "USD",
                },
                {
                    "ticker": "VOO",
                    "symbol": "AMEX:VOO",
                    "instrument_type": "etf",
                    "sleeve": "core",
                    "invested_sum": 8000.0,
                    "average_price": 480.0,
                    "currency": "USD",
                },
            ]
        )
        run_result = run_holdings_scoring_analysis(
            holdings_config_path=config_path,
            output_dir=self.output_root / "holdings_mixed",
            database_path=stock_result["_duckdb_database"],
            run_id=stock_result["_duckdb_run_id"],
            etf_database_path=etf_result["_duckdb_database"],
            etf_run_id=etf_result["run_id"],
            reference_time=datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
            resolve_fx_rates=False,
        )

        output_dir = Path(run_result["output_dir"])
        self.assertEqual(run_result["matched_count"], 2)
        self.assertEqual(run_result["unmatched_tickers"], [])
        self.assertTrue(
            (output_dir / "scans" / SCAN_KEY_MOVE_PREDICTION / "holdings__summary.csv").exists()
        )
        self.assertTrue(
            (output_dir / "scans" / SCAN_KEY_ETF_BOOK / "holdings__summary.csv").exists()
        )
        shortlist_text = (output_dir / "holdings_scoring__shortlist.log").read_text(
            encoding="utf-8"
        )
        self.assertIn("MU", shortlist_text)
        self.assertIn("VOO", shortlist_text)
        self.assertIn("ETF book tracking:", shortlist_text)

        source = resolve_latest_etf_scan_source(
            database_path=etf_result["_duckdb_database"],
            run_id=etf_result["run_id"],
        )
        self.assertEqual(source.run_id, etf_result["run_id"])
