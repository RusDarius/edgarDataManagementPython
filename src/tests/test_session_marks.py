from __future__ import annotations

import unittest
from datetime import datetime, timezone


class PhaseTests(unittest.TestCase):
    def test_new_york_clock_with_daylight_saving(self):
        from generic_utils.session_marks import us_phase

        utc = lambda *a: datetime(*a, tzinfo=timezone.utc)  # noqa: E731
        self.assertEqual(us_phase(utc(2026, 10, 6, 13, 0)), "pre")  # 09:00 EDT
        self.assertEqual(us_phase(utc(2026, 10, 6, 13, 40)), "cash")  # 09:40 EDT
        self.assertEqual(us_phase(utc(2026, 11, 18, 14, 0)), "pre")  # 09:00 EST: the open moved to 14:30 UTC
        self.assertEqual(us_phase(utc(2026, 11, 18, 21, 30)), "post")  # 16:30 EST
        self.assertEqual(us_phase(utc(2026, 10, 7, 7, 6)), "closed")  # 03:06 EDT: before the premarket
        self.assertEqual(us_phase(utc(2026, 11, 21, 15, 0)), "closed")  # Saturday
        self.assertEqual(us_phase(datetime(2026, 11, 18, 15, 0)), "cash")  # naive = UTC (run_metadata.created_at_utc)
        self.assertIsNone(us_phase(None))


class MarkTests(unittest.TestCase):
    ROW = {"close": 100.0, "change": -4.0, "pre_chg": -3.0, "post_chg": 2.0, "pre_vol": 150_000, "post_vol": 40_000,
           "avgvol10": 2_000_000, "vwap": 102.0}

    def test_base_close_follows_the_session(self):
        from generic_utils.session_marks import session_marks

        pre = session_marks(self.ROW, datetime(2026, 11, 18, 13, 0, tzinfo=timezone.utc))
        self.assertEqual((pre["us_phase"], pre["pre_px"]), ("pre", 97.0))  # premarket: base = the row close
        self.assertEqual(pre["post_px"], 102.0)
        cash = session_marks(self.ROW, datetime(2026, 11, 18, 16, 0, tzinfo=timezone.utc))
        self.assertAlmostEqual(cash["pre_px"], 100 / 0.96 * 0.97, places=3)  # after the open: base = the prior close
        self.assertAlmostEqual(cash["post_px"], 100 / 0.96 * 1.02, places=3)
        post = session_marks(self.ROW, datetime(2026, 11, 18, 22, 0, tzinfo=timezone.utc))
        self.assertEqual(post["post_px"], 102.0)  # after the close: base = today's close

    def test_explicit_prices_volume_and_vwap(self):
        from generic_utils.session_marks import marks_from_raw, session_marks

        row = {**self.ROW, "pre_close": 95.5, "post_close": 101.25}
        out = session_marks(row, None)  # explicit prices need no clock
        self.assertEqual((out["pre_px"], out["post_px"]), (95.5, 101.25))
        self.assertEqual((out["pre_vol_x"], out["post_vol_x"]), (0.075, 0.02))
        self.assertAlmostEqual(out["vs_vwap"], -1.96, places=2)
        self.assertIsNone(session_marks(self.ROW, None)["pre_px"])  # derived price without a scan time: empty
        raw = marks_from_raw({"close": 50, "change": 0, "premarket_change": 10, "premarket_volume": 5, "average_volume_10d_calc": 100},
                             datetime(2026, 11, 18, 13, 0, tzinfo=timezone.utc))
        self.assertEqual((raw["pre_px"], raw["pre_vol_x"]), (55.0, 0.05))


if __name__ == "__main__":
    unittest.main()
