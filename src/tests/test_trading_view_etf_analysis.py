import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from data_analysis_scripts.trading_view_etf_active_book import (
    assign_peer_groups,
    classify_product_class,
    derive_etf_metrics,
    run_etf_active_book,
)
from data_analysis_scripts.trading_view_etf_analysis import (
    run_etf_analysis_suite_duckdb,
    run_etf_scan_and_analysis_suite,
)
from db.trading_view_etf_scan_duckdb import query_etf_scan_duckdb


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _etf_row(symbol: str, name: str, **overrides) -> dict:
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


class TestEtfProductClassAndDerived(unittest.TestCase):
    def test_classifies_1x_levered_and_inverse(self):
        self.assertEqual(
            classify_product_class(
                {"niche.tr": "Large Cap"}, name="VOO", description="S&P 500"
            ),
            "1x",
        )
        self.assertEqual(
            classify_product_class(
                {"niche.tr": "Leveraged"},
                name="TQQQ",
                description="ProShares UltraPro QQQ",
            ),
            "levered",
        )
        self.assertEqual(
            classify_product_class(
                {"category.tr": "Inverse Equity"},
                name="SH",
                description="ProShares Short S&P500",
            ),
            "inverse",
        )
        self.assertEqual(
            classify_product_class(
                {"niche.tr": "Inverse"},
                name="SQQQ",
                description="ProShares UltraPro Short QQQ",
            ),
            "inverse_levered",
        )
        self.assertEqual(
            classify_product_class(
                {"niche.tr": "Short-Term Bond", "category.tr": "Bond"},
                name="BSV",
                description="Short-Term Bond ETF",
            ),
            "1x",
        )

    def test_organic_demand_and_tracking_gap(self):
        item = {
            "symbol": "AMEX:VOO",
            "name": "VOO",
            "description": "S&P 500",
            "row": _etf_row(
                "AMEX:VOO",
                "VOO",
                **{
                    "aum": 10_000_000_000,
                    "fund_flows.1M": 200_000_000,
                    "aum_perf.1M": 5.0,
                    "nav_total_return.1M": 2.0,
                    "Perf.1M": 2.4,
                },
            ),
        }
        derived = derive_etf_metrics(item)
        self.assertEqual(derived["ticker"], "VOO")
        self.assertEqual(derived["product_class"], "1x")
        self.assertAlmostEqual(derived["flow_to_aum_1m"], 0.02)
        self.assertAlmostEqual(derived["organic_demand_1m"], 3.0)
        self.assertAlmostEqual(derived["tracking_gap_1m"], 0.4)

    def test_peer_groups_roll_up_when_niche_is_thin(self):
        items = []
        for index in range(9):
            items.append(
                {
                    "product_class": "1x",
                    "asset_class": "Equity",
                    "category": "Equity",
                    "focus": "Large Cap",
                    "niche": f"Niche{index}",
                    "symbol": f"AMEX:E{index}",
                }
            )
        assigned = assign_peer_groups(items, min_peer_group_size=8)
        self.assertTrue(all(item["peer_group"] == assigned[0]["peer_group"] for item in assigned))
        self.assertNotIn("niche", assigned[0]["peer_level"])
        self.assertIn("focus", assigned[0]["peer_level"])


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestEtfAnalysisSuiteDuckDB(unittest.TestCase):
    def test_scores_and_persists_ranked_etfs(self):
        scan_rows = [
            _etf_row(
                "AMEX:AAA",
                "AAA",
                **{
                    "aum": 50_000_000_000,
                    "Perf.1M": 12.0,
                    "Recommend.All": 0.8,
                    "category": "26",
                    "category.tr": "Equity",
                    "market": "america",
                    "country": "United States",
                },
            ),
            _etf_row(
                "AMEX:BBB",
                "BBB",
                **{
                    "aum": 20_000_000_000,
                    "Perf.1M": 4.0,
                    "Recommend.All": 0.1,
                    "category": "26",
                    "category.tr": "Equity",
                },
            ),
            _etf_row(
                "AMEX:CCC",
                "CCC",
                **{
                    "aum": 8_000_000_000,
                    "Perf.1M": -3.0,
                    "Recommend.All": -0.4,
                    "category": "26",
                    "category.tr": "Equity",
                },
            ),
            _etf_row(
                "AMEX:DDD",
                "DDD",
                **{
                    "aum": 5_000_000_000,
                    "Perf.1M": 1.0,
                    "category": "7",
                    "category.tr": "Bond",
                    "asset_class": "Fixed Income",
                    "focus": "Treasury",
                },
            ),
            _etf_row(
                "AMEX:EEE",
                "EEE",
                **{
                    "aum": 3_000_000_000,
                    "Perf.1M": 0.5,
                    "category": "7",
                    "category.tr": "Bond",
                    "asset_class": "Fixed Income",
                    "focus": "Treasury",
                },
            ),
            _etf_row("AMEX:FFF", "FFF", **{"aum": 400_000_000, "Perf.1M": 20.0}),
        ]
        scan_payload = {
            "data": scan_rows,
            "request_metadata": {
                "url": "https://scanner.tradingview.com/global/scan?label-product=screener-etf",
                "columns": ["name", "aum", "Perf.1M"],
            },
        }

        with TemporaryDirectory() as temp_dir:
            result = run_etf_analysis_suite_duckdb(
                scan_data=scan_payload,
                min_aum_usd=1_000_000_000,
                output_dir=Path(temp_dir),
                run_label="etf_test",
                reference_time=datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc),
                export_parquet=False,
            )

            self.assertEqual(result["scan_data_count"], 6)
            self.assertEqual(result["scored_count"], 5)
            self.assertEqual(result["excluded_count"], 1)
            self.assertTrue(result["overview_log"].exists())
            self.assertTrue(result["ranked_csv"].exists())
            overview_text = result["overview_log"].read_text(encoding="utf-8")
            self.assertIn("screener-etf", overview_text)
            self.assertIn("AMEX:AAA", overview_text)
            self.assertIn("Active book", overview_text)

            ranked = query_etf_scan_duckdb(
                str(result["_duckdb_database"]),
                "SELECT symbol, composite_score, rank FROM etf_ranked_scores WHERE run_id = ? ORDER BY rank",
                [result["run_id"]],
            )
            self.assertEqual(len(ranked), 5)
            self.assertEqual(ranked[0]["symbol"], "AMEX:AAA")
            self.assertGreater(
                ranked[0]["composite_score"], ranked[-1]["composite_score"]
            )
            self.assertNotIn("AMEX:FFF", [row["symbol"] for row in ranked])

            categories = query_etf_scan_duckdb(
                str(result["_duckdb_database"]),
                "SELECT DISTINCT category FROM etf_category_leaders WHERE run_id = ? ORDER BY category",
                [result["run_id"]],
            )
            self.assertEqual(
                [row["category"] for row in categories], ["Bond", "Equity"]
            )

            self.assertTrue(result["regime_tape_log"].exists())
            self.assertTrue(result["sleeve_heat_log"].exists())
            self.assertTrue(result["book_ranked_csv"].exists())
            self.assertTrue(result["holdings_overlay_log"].exists())
            book_rows = query_etf_scan_duckdb(
                str(result["_duckdb_database"]),
                "SELECT symbol, product_class, book_consensus FROM etf_book_rows WHERE run_id = ?",
                [result["run_id"]],
            )
            self.assertEqual(len(book_rows), 5)
            self.assertTrue(all(row["product_class"] == "1x" for row in book_rows))

    def test_active_book_isolates_levered_and_writes_catch_up(self):
        scan_rows = [
            _etf_row("AMEX:VOO", "VOO", **{"aum": 1_200_000_000_000, "Perf.1M": 2.0, "RSI": 52}),
            _etf_row("AMEX:QQQ", "QQQ", **{"aum": 250_000_000_000, "Perf.1M": 4.0, "RSI": 60, "focus": "Large Cap"}),
            _etf_row(
                "AMEX:XLK",
                "XLK",
                **{
                    "aum": 70_000_000_000,
                    "Perf.1M": 8.0,
                    "RSI": 72,
                    "focus": "Technology",
                    "price_52_week_high": 101.0,
                },
            ),
            _etf_row(
                "AMEX:IGV",
                "IGV",
                **{
                    "aum": 10_000_000_000,
                    "Perf.1M": 1.0,
                    "RSI": 48,
                    "focus": "Technology",
                    "fund_flows.1M": 400_000_000,
                },
            ),
            _etf_row(
                "AMEX:SMH",
                "SMH",
                **{"aum": 25_000_000_000, "Perf.1M": 9.0, "RSI": 74, "focus": "Technology"},
            ),
            _etf_row(
                "AMEX:SOXL",
                "SOXL",
                **{
                    "aum": 8_000_000_000,
                    "Perf.1M": 20.0,
                    "niche": "Leveraged",
                    "niche.tr": "Leveraged",
                    "description": "Direxion Daily Semiconductor Bull 3X",
                    "focus": "Technology",
                },
            ),
            _etf_row(
                "AMEX:TLT",
                "TLT",
                **{
                    "aum": 50_000_000_000,
                    "Perf.1M": -1.0,
                    "asset_class": "Fixed Income",
                    "category": "Bond",
                    "category.tr": "Bond",
                    "focus": "Treasury",
                },
            ),
            _etf_row(
                "AMEX:IEF",
                "IEF",
                **{
                    "aum": 30_000_000_000,
                    "Perf.1M": -0.4,
                    "asset_class": "Fixed Income",
                    "category": "Bond",
                    "category.tr": "Bond",
                    "focus": "Treasury",
                },
            ),
            _etf_row(
                "AMEX:GLD",
                "GLD",
                **{
                    "aum": 90_000_000_000,
                    "Perf.1M": 3.0,
                    "asset_class": "Commodities",
                    "category": "Commodities",
                    "category.tr": "Commodities",
                    "focus": "Gold",
                },
            ),
        ]

        with TemporaryDirectory() as temp_dir:
            result = run_etf_analysis_suite_duckdb(
                scan_data=scan_rows,
                min_aum_usd=1_000_000_000,
                output_dir=Path(temp_dir),
                run_label="etf_book",
                reference_time=datetime(2026, 8, 16, 13, 0, tzinfo=timezone.utc),
                export_parquet=False,
            )
            book = query_etf_scan_duckdb(
                str(result["_duckdb_database"]),
                "SELECT symbol, product_class, vehicle_quality FROM etf_book_rows WHERE run_id = ?",
                [result["run_id"]],
            )
            by_symbol = {row["symbol"]: row for row in book}
            self.assertEqual(by_symbol["AMEX:SOXL"]["product_class"], "levered")
            self.assertIsNone(by_symbol["AMEX:SOXL"]["vehicle_quality"])
            self.assertIsNotNone(by_symbol["AMEX:VOO"]["vehicle_quality"])

            regime_text = result["regime_tape_log"].read_text(encoding="utf-8")
            self.assertIn("AMEX:VOO", regime_text)
            self.assertIn("AMEX:TLT", regime_text)
            self.assertIn("AMEX:GLD", regime_text)

            overlay_text = result["holdings_overlay_log"].read_text(encoding="utf-8")
            self.assertIn("software_platform", overlay_text)
            self.assertIn("AMEX:XLK", overlay_text)

            catch_up_text = result["catch_up_log"].read_text(encoding="utf-8")
            self.assertTrue(
                "catch_up" in catch_up_text or "extended" in catch_up_text
            )

            vehicles = query_etf_scan_duckdb(
                str(result["_duckdb_database"]),
                "SELECT symbol FROM etf_vehicle_quality WHERE run_id = ?",
                [result["run_id"]],
            )
            self.assertNotIn("AMEX:SOXL", [row["symbol"] for row in vehicles])

    def test_day_over_day_uses_prior_run_in_same_week_db(self):
        first_rows = [
            _etf_row("AMEX:AAA", "AAA", **{"aum": 20_000_000_000, "fund_flows.1M": 50_000_000, "Perf.1M": 1.0}),
            _etf_row("AMEX:BBB", "BBB", **{"aum": 15_000_000_000, "fund_flows.1M": 40_000_000, "Perf.1M": 1.2}),
            _etf_row("AMEX:CCC", "CCC", **{"aum": 12_000_000_000, "fund_flows.1M": 30_000_000, "Perf.1M": 0.8}),
            _etf_row("AMEX:DDD", "DDD", **{"aum": 11_000_000_000, "fund_flows.1M": 20_000_000, "Perf.1M": 0.4}),
            _etf_row("AMEX:EEE", "EEE", **{"aum": 10_000_000_000, "fund_flows.1M": 10_000_000, "Perf.1M": 0.2}),
        ]
        second_rows = [
            _etf_row("AMEX:AAA", "AAA", **{"aum": 20_000_000_000, "fund_flows.1M": 400_000_000, "Perf.1M": 4.0}),
            _etf_row("AMEX:BBB", "BBB", **{"aum": 15_000_000_000, "fund_flows.1M": 40_000_000, "Perf.1M": 1.2}),
            _etf_row("AMEX:CCC", "CCC", **{"aum": 12_000_000_000, "fund_flows.1M": 30_000_000, "Perf.1M": 0.8}),
            _etf_row("AMEX:DDD", "DDD", **{"aum": 11_000_000_000, "fund_flows.1M": 20_000_000, "Perf.1M": 0.4}),
            _etf_row("AMEX:EEE", "EEE", **{"aum": 10_000_000_000, "fund_flows.1M": 10_000_000, "Perf.1M": 0.2}),
        ]

        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "etf_week.duckdb"
            first = run_etf_analysis_suite_duckdb(
                scan_data=first_rows,
                min_aum_usd=1_000_000_000,
                output_dir=Path(temp_dir),
                database_path=database_path,
                run_label="etf_day1",
                reference_time=datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc),
                export_parquet=False,
            )
            self.assertIn("first session", first["dod_log"].read_text(encoding="utf-8"))

            second = run_etf_analysis_suite_duckdb(
                scan_data=second_rows,
                min_aum_usd=1_000_000_000,
                output_dir=Path(temp_dir),
                database_path=database_path,
                run_label="etf_day2",
                reference_time=datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
                export_parquet=False,
            )
            dod_text = second["dod_log"].read_text(encoding="utf-8")
            self.assertIn("AMEX:AAA", dod_text)
            dod_rows = query_etf_scan_duckdb(
                str(database_path),
                "SELECT symbol FROM etf_dod_changes WHERE run_id = ?",
                [second["run_id"]],
            )
            self.assertIn("AMEX:AAA", [row["symbol"] for row in dod_rows])

    def test_parent_suite_fetches_then_analyzes(self):
        scan_rows = [
            _etf_row("AMEX:AAA", "AAA", **{"aum": 50_000_000_000, "Perf.1M": 12.0}),
            _etf_row("AMEX:BBB", "BBB", **{"aum": 20_000_000_000, "Perf.1M": 4.0}),
            _etf_row("AMEX:CCC", "CCC", **{"aum": 8_000_000_000, "Perf.1M": -3.0}),
            _etf_row("AMEX:DDD", "DDD", **{"aum": 5_000_000_000, "Perf.1M": 1.0}),
            _etf_row("AMEX:EEE", "EEE", **{"aum": 3_000_000_000, "Perf.1M": 0.5}),
        ]
        api_client = Mock()
        api_client.scan_global_etf_move_prediction.return_value = {
            "data": scan_rows,
            "request_metadata": {
                "url": "https://scanner.tradingview.com/global/scan?label-product=screener-etf",
                "markets": ["america", "uk", "japan"],
            },
        }

        with TemporaryDirectory() as temp_dir:
            result = run_etf_scan_and_analysis_suite(
                api_client,
                min_aum_usd=1_000_000_000,
                output_dir=Path(temp_dir),
                run_label="etf_parent",
                reference_time=datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc),
                export_parquet=False,
            )

            api_client.scan_global_etf_move_prediction.assert_called_once()
            call_kwargs = api_client.scan_global_etf_move_prediction.call_args.kwargs
            self.assertEqual(call_kwargs["min_aum_usd"], 1_000_000_000)
            self.assertEqual(result["scored_count"], 5)
            self.assertEqual(
                result["scan_url"],
                "https://scanner.tradingview.com/global/scan?label-product=screener-etf",
            )
            self.assertEqual(result["scan_markets"], ["america", "uk", "japan"])
            self.assertTrue(result["overview_log"].exists())
            self.assertTrue(result["regime_tape_log"].exists())
            self.assertEqual(result["active_book_suite_id"], "etf_active_manager_v1")


class TestEtfActiveBookOffline(unittest.TestCase):
    def test_run_etf_active_book_writes_reports_without_scan(self):
        scored_rows = []
        for symbol, name, perf, focus in (
            ("AMEX:AAA", "AAA", 8.0, "Technology"),
            ("AMEX:BBB", "BBB", 1.0, "Technology"),
            ("AMEX:CCC", "CCC", 3.0, "Technology"),
            ("AMEX:DDD", "DDD", 0.5, "Large Cap"),
            ("AMEX:EEE", "EEE", 2.0, "Large Cap"),
        ):
            row = _etf_row(symbol, name, **{"Perf.1M": perf, "focus": focus})
            scored_rows.append(
                {
                    "row": row,
                    "symbol": symbol,
                    "name": name,
                    "description": row["description"],
                    "category": "Equity",
                    "focus": focus,
                    "asset_class": "Equity",
                    "brand": "Test",
                    "aum": row["aum"],
                    "expense_ratio": row["expense_ratio"],
                    "nav_discount_premium": row["nav_discount_premium"],
                    "composite_score": 50.0 + perf,
                }
            )
        with TemporaryDirectory() as temp_dir:
            result = run_etf_active_book(
                scored_rows,
                run_id="test_run",
                run_output_dir=Path(temp_dir),
            )
            self.assertEqual(result["one_x_count"], 5)
            self.assertTrue(result["reports"]["regime_tape_log"].exists())
            self.assertTrue(result["reports"]["book_ranked_csv"].exists())
            roles = {record["role"] for record in result["catch_up_records"]}
            self.assertTrue(roles & {"catch_up", "extended"})


if __name__ == "__main__":
    unittest.main()
