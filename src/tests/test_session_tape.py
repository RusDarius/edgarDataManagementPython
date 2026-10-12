import unittest
from datetime import date, datetime, timezone


class TestSessionTape(unittest.TestCase):
    def test_join_keeps_the_print_day_and_the_afternoon_gap(self):
        from generic_utils.session_tape import join_post_earn, when_label

        earn = datetime(2026, 10, 6, 21, 0, tzinfo=timezone.utc)
        epoch = str(int(earn.timestamp()))
        other = str(int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()))
        prior = [
            {
                "symbol": "NASDAQ:PENG",
                "name": "Penguin",
                "exchange": "NASDAQ",
                "industry": "Semiconductors",
                "close": 57.56,
                "change": -5.2,
                "premarket_change": 1.47,
                "earnings_release_date": epoch,
                "earnings_release_time": 1,
            },
            {
                "symbol": "NYSE:KEEP",
                "name": "Later",
                "exchange": "NYSE",
                "close": 10,
                "change": 1,
                "earnings_release_date": other,
                "earnings_release_time": -1,
            },
        ]
        current = [
            {
                "symbol": "NASDAQ:PENG",
                "name": "Penguin",
                "exchange": "NASDAQ",
                "industry": "Semiconductors",
                "close": 64.21,
                "change": 5.77,
                "premarket_change": 1.47,
                "premarket_close": 61.6,
                "postmarket_change": 5.39,
                "postmarket_close": 67.67,
                "earnings_release_date": epoch,
                "earnings_release_time": "1",
                "price_target_average": 80,
                "RSI": 61.3,
            }
        ]
        rows = join_post_earn(prior, current, date(2026, 10, 6), exchanges=["NASDAQ"])
        self.assertEqual([row["symbol"] for row in rows], ["NASDAQ:PENG"])
        row = rows[0]
        self.assertEqual(row["when_label"], "AMC")
        self.assertEqual(when_label(-1), "BMO")
        self.assertEqual(row["afternoon_close"], 57.56)
        self.assertEqual(row["session_close"], 64.21)
        self.assertAlmostEqual(row["vs_afternoon"], 11.55, places=2)
        self.assertTrue(row["pre_repeat"])
        self.assertEqual(row["post_close"], 67.67)

    def test_empty_exchange_list_is_not_the_default_us_filter(self):
        from generic_utils.session_tape import join_post_earn

        earn = datetime(2026, 10, 6, tzinfo=timezone.utc)
        epoch = str(int(earn.timestamp()))
        rows = [
            {
                "symbol": "LSE:ABC",
                "exchange": "LSE",
                "close": 1,
                "change": 2,
                "earnings_release_date": epoch,
            }
        ]
        self.assertEqual(len(join_post_earn(rows, rows, date(2026, 10, 6), exchanges=None)), 1)
        self.assertEqual(len(join_post_earn(rows, rows, date(2026, 10, 6), exchanges=["NASDAQ"])), 0)
