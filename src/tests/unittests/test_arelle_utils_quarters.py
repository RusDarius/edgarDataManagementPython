import unittest
from datetime import datetime
from types import SimpleNamespace

from data_loaders.data_extractors.arelle_extractors.arelle_uitls_data_extractors import (
    extract_quarters_covered,
)


class TestExtractQuartersCovered(unittest.TestCase):
    def test_instant(self):
        ctx = SimpleNamespace(
            startDatetime=None,
            endDate=datetime(2024, 6, 30),
            instantDate=datetime(2024, 6, 30),
        )
        self.assertEqual(extract_quarters_covered(ctx), 1)

    def test_one_quarter(self):
        ctx = SimpleNamespace(
            startDatetime=datetime(2024, 4, 1),
            endDate=datetime(2024, 6, 30),
            instantDate=None,
        )
        self.assertEqual(extract_quarters_covered(ctx), 1)

    def test_two_quarters(self):
        ctx = SimpleNamespace(
            startDatetime=datetime(2024, 1, 1),
            endDate=datetime(2024, 6, 30),
            instantDate=None,
        )
        self.assertEqual(extract_quarters_covered(ctx), 2)

    def test_three_quarters(self):
        ctx = SimpleNamespace(
            startDatetime=datetime(2023, 10, 1),
            endDate=datetime(2024, 6, 30),
            instantDate=None,
        )
        self.assertEqual(extract_quarters_covered(ctx), 3)

    def test_four_quarters(self):
        ctx = SimpleNamespace(
            startDatetime=datetime(2023, 7, 1),
            endDate=datetime(2024, 6, 30),
            instantDate=None,
        )
        self.assertEqual(extract_quarters_covered(ctx), 4)

    def test_none(self):
        ctx = SimpleNamespace(startDatetime=None, endDate=None, instantDate=None)
        self.assertIsNone(extract_quarters_covered(ctx))


if __name__ == "__main__":
    unittest.main()
