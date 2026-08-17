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


if __name__ == "__main__":
    unittest.main()
