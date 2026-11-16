from __future__ import annotations

import math
import unittest

RECIPE = {"min_peers": 3, "min_fit_n": 4, "winsor_pct": 0.0, "ridge": 0.01,
          "lenses": [{"name": "pe", "yield": "ey_fwd", "label": "pe", "drivers": ["rev_nfy_g"]}],
          "primary_order": ["pe"], "percentiles": {"growth": "rev_nfy_g"}, "spreads": ["rev_nfy_g"]}


def _rows(growths, pes, close=100.0):
    return [{"symbol": f"X:{i}", "ind": "Widgets", "sector": "Tech", "mcap": 1e10 * (i + 1), "close": close,
             "ey_fwd": 100.0 / pe, "rev_nfy_g": g, "eps_nfy_g": 10.0} for i, (g, pe) in enumerate(zip(growths, pes))]


class TestPeerValue(unittest.TestCase):
    def test_fit_recovers_what_peers_pay_for_growth(self):
        from generic_utils.peer_value import score_day

        # multiple = 10 * exp(0.05 * growth) on four names; a fifth name has 20% growth but trades at the 0% multiple
        growths = [0.0, 10.0, 20.0, 30.0, 20.0]
        pes = [10 * math.exp(0.05 * g) for g in growths[:4]] + [10.0]
        rows = _rows(growths, pes)
        groups = score_day(rows, RECIPE)
        self.assertEqual(groups[0]["group"], "ind: Widgets")
        cheap = rows[4]
        self.assertGreater(cheap["gap_pct"], 50)  # peers with 20% growth trade near 27x, it trades at 10x
        self.assertLess(cheap["prem_pct"], 0)
        self.assertGreater(cheap["just_pct"], cheap["prem_pct"])
        self.assertAlmostEqual(cheap["px_fit"], 100.0 * (1 + cheap["gap_pct"] / 100), delta=0.5)
        self.assertEqual(cheap["disc_pct"], -cheap["prem_pct"])

    def test_thin_industry_falls_back_to_sector_and_negative_yields_skip_the_lens(self):
        from generic_utils.peer_value import score_day

        rows = _rows([5, 6, 7], [10, 11, 12])
        rows[0]["ind"], rows[1]["ind"], rows[2]["ind"] = "A", "B", "C"
        rows.append({"symbol": "X:9", "ind": "A", "sector": "Tech", "mcap": 5e9, "close": 50, "ey_fwd": -2.0, "rev_nfy_g": 3})
        score_day(rows, RECIPE)
        self.assertTrue(all(r["group"] == "sector: Tech" for r in rows))
        self.assertIsNone(rows[3].get("pe_mult"))
        self.assertIsNotNone(rows[0].get("pe_prem_pct"))

    def test_percentiles_and_spearman(self):
        from generic_utils.peer_value import percentile_ranks, spearman

        self.assertEqual(percentile_ranks([3.0, None, 1.0, 2.0]), [100.0, None, 0.0, 50.0])
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)

    def test_nearest_peers_by_market_cap(self):
        from generic_utils.peer_value import nearest_peers, score_day

        rows = _rows([0, 10, 20, 30, 20], [10, 15, 20, 30, 10])
        score_day(rows, RECIPE)
        peers = nearest_peers(rows, ["X:2"], 2)
        self.assertEqual([p["peer"] for p in peers], ["X:3", "X:1"])  # 4e10 is closer to 3e10 than 2e10 in log terms


if __name__ == "__main__":
    unittest.main()
