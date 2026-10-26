import unittest


class TestSafetyBand(unittest.TestCase):
    def _history(self):
        rows = []
        closes = [100, 110, 120, 30, 28, 26, 25]  # 4-for-1 split between day 3 and 4, then a slide
        for k, close in enumerate(closes):
            per_share = 1.0 if k < 3 else 0.25
            rows.append({"symbol": "NASDAQ:X", "as_of": f"2026-10-{k + 1:02d}", "close": close, "change": -1.0,
                         "price_target_average": 140 * per_share, "earnings_per_share_forecast_next_fy": (5 + k) * per_share})
        return rows

    def test_history_reads_are_split_adjusted_and_track_the_multiple(self):
        from generic_utils.safety_band import history_reads

        reads = history_reads(self._history())
        self.assertEqual(reads["splits"], 1)
        self.assertEqual(reads["window_high"], 30.0)  # 120 / 4
        self.assertEqual(reads["window_low"], 25.0)
        self.assertEqual(reads["pe_nfy_first"], 20.0)  # 100 / 5
        self.assertEqual(reads["pe_nfy_min"], round(25 / (11 * 0.25), 1))
        self.assertEqual(reads["pe_nfy_seat"], 0.0)  # today's multiple is the window's cheapest
        self.assertEqual(reads["eps_fc_pct"], 120.0)  # 11 vs 5, split-adjusted

    def test_levels_anchor_on_support_and_price_the_value_anchors(self):
        from generic_utils.safety_band import load_safety_recipe, safety_levels

        row = {"close": 100.0, "atr": 4.0, "vs50": -10.0, "street_px": 150.0, "pe_nfy": 10.0, "pe_nfy_ind_med": 20.0,
               "pe_nfy_min": 10.0, "pe_nfy_med": 15.0, "window_low": 95.0, "window_high": 140.0,
               "sma_role": "reclaim", "entry_low": 111.0, "entry_high": 113.0}
        out = safety_levels(row, load_safety_recipe())
        self.assertEqual((out["bounce_high"], out["bounce_low"], out["bounce_anchor"]), (98.0, 95.0, "window_low"))
        self.assertEqual(out["bounce_kill"], 92.0)  # 0.75 ATR under the support
        self.assertEqual((out["px_peer_pe"], out["mos_peer_pct"], out["mos_own_low_pct"]), (200.0, 100.0, 0.0))
        self.assertAlmostEqual(out["first_target"], 111.11, places=2)  # the 50-day
        self.assertEqual((out["add_trigger_low"], out["trim_zone"], out["trim_zone_state"]), (111.0, 140.0, "open"))
        self.assertEqual(out["rr_bounce"], round((111.11 - 98.0) / (98.0 - 92.0), 2))

    def test_build_joins_lens_and_history_by_symbol(self):
        from generic_utils.safety_band import build_safety_band

        rows = build_safety_band([{"symbol": "NASDAQ:X", "close": 25.0, "atr": 1.0, "vs50": -5.0}],
                                 lens=[{"symbol": "NASDAQ:X", "pe_nfy": "9.1", "pe_nfy_ind_med": "18"}], history=self._history())
        self.assertEqual(rows[0]["splits"], 1)
        self.assertEqual(rows[0]["pe_nfy"], "9.1")
        self.assertIsNotNone(rows[0]["bounce_kill"])


if __name__ == "__main__":
    unittest.main()
