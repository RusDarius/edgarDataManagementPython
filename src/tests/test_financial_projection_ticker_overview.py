"""Tests for financial projection ticker overview field whitelist."""

from __future__ import annotations

import unittest

from financial_projection.overview_fields import (
    OVERVIEW_FIELD_BUCKETS,
    OVERVIEW_FIELD_WHITELIST,
    overview_field_count,
)


class OverviewFieldsTests(unittest.TestCase):
    def test_whitelist_near_top_100(self) -> None:
        count = overview_field_count()
        self.assertGreaterEqual(count, 90)
        self.assertLessEqual(count, 130)
        self.assertEqual(count, len(OVERVIEW_FIELD_WHITELIST))
        self.assertEqual(len(OVERVIEW_FIELD_WHITELIST), len(set(OVERVIEW_FIELD_WHITELIST)))

    def test_buckets_cover_whitelist(self) -> None:
        flattened = [field for fields in OVERVIEW_FIELD_BUCKETS.values() for field in fields]
        self.assertTrue(set(OVERVIEW_FIELD_WHITELIST).issubset(set(flattened)))
        self.assertIn("identity", OVERVIEW_FIELD_BUCKETS)
        self.assertIn("valuation", OVERVIEW_FIELD_BUCKETS)
        self.assertIn("forward_street", OVERVIEW_FIELD_BUCKETS)

    def test_qualified_ticker_fetch_does_not_cross_exchange(self) -> None:
        import duckdb

        from financial_projection.ticker_overview import _fetch_ticker_row

        conn = duckdb.connect()
        try:
            conn.execute(
                """
                CREATE TABLE all_fields_rows (
                    symbol VARCHAR,
                    close DOUBLE,
                    market_cap_basic DOUBLE
                )
                """
            )
            conn.execute(
                """
                INSERT INTO all_fields_rows VALUES
                    ('ASX:MMS', 18.30, 885486035),
                    ('NYSE:MMS', 56.99, 2983868903)
                """
            )
            row = _fetch_ticker_row(
                conn,
                run_id="",
                ticker="NYSE:MMS",
                fields=("close", "market_cap_basic"),
                available={"symbol", "close", "market_cap_basic"},
            )
            self.assertIsNotNone(row)
            self.assertEqual(row["symbol"], "NYSE:MMS")
            self.assertEqual(row["close"], 56.99)

            conn.execute("DELETE FROM all_fields_rows WHERE symbol = 'NYSE:MMS'")
            self.assertIsNone(
                _fetch_ticker_row(
                    conn,
                    run_id="",
                    ticker="NYSE:MMS",
                    fields=("close", "market_cap_basic"),
                    available={"symbol", "close", "market_cap_basic"},
                )
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
