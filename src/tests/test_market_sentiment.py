from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path


def _rows(n: int, *, signal: bool) -> list[dict]:
    """A synthetic daily series: x leads next-day returns when ``signal`` (alternating highs and lows)."""
    rows = []
    for i in range(n):
        x = float((i * 37) % 11)
        rows.append({"as_of": date(2026, 1, 1) + timedelta(days=i), "x": x})
    for i, r in enumerate(rows):
        nxt = rows[i + 1]["x"] if i + 1 < n else None
        r["fwd_1"] = (r["x"] - 5.0) * 0.2 if signal and i + 1 < n else ((-1) ** i) * 0.3 if i + 1 < n else None
        del nxt
    return rows


class MarketSentimentTests(unittest.TestCase):
    def test_session_frac_and_dst(self):
        from generic_utils.market_sentiment import session_frac, us_dst

        cfg = {"dst": [13.5, 20.0], "std": [14.5, 21.0], "min_frac": 0.15}
        self.assertTrue(us_dst(date(2026, 10, 30)))
        self.assertFalse(us_dst(date(2026, 11, 3)))  # DST ended Sunday 1 November 2026
        self.assertAlmostEqual(session_frac(datetime(2026, 11, 3, 16, 12), cfg), (16.2 - 14.5) / 6.5, places=3)
        self.assertEqual(session_frac(datetime(2026, 10, 29, 21, 28), cfg), 1.0)
        self.assertEqual(session_frac(datetime(2026, 11, 3, 9, 0), cfg), 0.15)
        self.assertIsNone(session_frac(None, cfg))

    def test_spearman_and_buckets(self):
        from generic_utils.market_sentiment import bucket, quantile_edges, spearman

        self.assertAlmostEqual(spearman([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]), -1.0)
        self.assertIsNone(spearman([1, 2], [1, 2]))
        edges = quantile_edges(list(range(1, 101)), 5)
        self.assertEqual(len(edges), 4)
        self.assertEqual(bucket(1, edges), 1)
        self.assertEqual(bucket(100, edges), 5)

    def test_arrow_needs_a_measured_relation_and_a_lean(self):
        from generic_utils.market_sentiment import family_counts, measure

        recipe = {"horizons": [1], "indicators": {"x": {"family": "tape", "label": "x"}},
                  "measure": {"quantiles": 5, "t_min": 2.0, "min_indep": 5, "min_cell_n": 5, "hi_pctl": 60, "lo_pctl": 40}}
        rows = _rows(120, signal=True)
        rows[-1]["x"] = 10.0  # today in the top of the history
        readings, cells = measure(rows, recipe)
        r = readings[0]
        self.assertGreater(r["ic_1"], 0.9)
        self.assertEqual(r["dir_1"], "up")
        self.assertTrue(r["read_1"].startswith("▲"))
        self.assertTrue(any(c["today"] for c in cells))
        fam = family_counts(readings, [1])
        self.assertEqual((fam[0]["up_1"], fam[0]["dn_1"]), (1, 0))
        rows[-1]["x"] = 5.0  # a middling reading: no lean, no arrow
        self.assertEqual(measure(rows, recipe)[0][0]["dir_1"], "")
        noise = _rows(120, signal=False)
        noise[-1]["x"] = 10.0
        self.assertEqual(measure(noise, recipe)[0][0]["dir_1"], "")

    def test_derive_counts_and_patterns(self):
        from generic_utils.market_sentiment import derive, pattern_stats

        rows = []
        for i in range(40):
            rows.append({"as_of": date(2026, 3, 1) + timedelta(days=i), "ret_cap": -0.5 if i % 2 else 0.5, "ret_med": 0.0,
                         "nh_pct": 3.0, "nl_pct": 1.0, "vol_ratio": 1.0 + i * 0.01, "scan_frac": 1.0, "adv_pct": 50.0})
        steps = [{"as_of": r["as_of"], "cap": r["ret_cap"]} for r in rows]
        recipe = {"horizons": [1, 5], "move_pct": 0.2, "count_window": 25, "smooth_window": 10,
                  "patterns": [{"name": "dist", "family": "volume", "where": "dist_25 >= 10"}, {"name": "bad", "where": "nope > 1"}]}
        derive(rows, steps, recipe)
        self.assertEqual(rows[-1]["dist_25"], 13)  # files 15..39: 13 odd files fall 0.5% on rising volume pace
        self.assertEqual(rows[-1]["acc_25"], 12)
        self.assertAlmostEqual(rows[1]["fwd_1"], -0.5, places=6)
        self.assertIsNone(rows[-1]["fwd_1"])
        self.assertEqual(rows[-1]["nh_nl"], 2.0)
        pats = pattern_stats(rows, recipe)
        self.assertTrue(pats[0]["live"])
        self.assertGreaterEqual(pats[0]["episodes"], 1)
        self.assertIn("error", pats[1])

    def test_add_outside_replaces_same_kind_and_day(self):
        from generic_utils.market_sentiment import add_outside, outside_rows

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            add_outside(path, kind="vix_close", value=18.0, day="2026-11-02", source="Cboe")
            add_outside(path, kind="vix_close", value=19.5, day="2026-11-02", source="Cboe")
            add_outside(path, kind="vix_close", value=17.0, day="2026-11-03", source="Cboe")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["rows"]), 2)
            rows = outside_rows(path)
            self.assertEqual(rows[-1]["chg_prev"], -2.5)
            self.assertEqual(rows[-1]["tag"], "OUTSIDE")


class ScreenGroupRenderTests(unittest.TestCase):
    def test_screen_group_reaches_page_and_guide(self):
        from generic_utils.canvas_page import build_page, render_html

        spec = {"name": "t-20261103", "guide": {"intro": "x"},
                "tabs": [{"label": "E", "blocks": [{"type": "table", "title": "M", "headers": ["symbol", "earn_days", "bar_rev"],
                                                    "rows": [["NYSE:A", "1", "T1"]] * 7,
                                                    "screens": [{"group": "When", "label": "Tonight", "filters": {"earn_days": "0..1"}},
                                                                {"label": "Low bar", "filters": {"bar_rev": "=t1"}}]}]}]}
        page = build_page(spec, base_dir=Path("."))
        html = render_html(page)
        self.assertIn("&quot;group&quot;: &quot;When&quot;", html)
        self.assertIn("When: Tonight", html)  # the Guide's screen table names the group
        self.assertIn("function toggleScreen", html)
        self.assertNotIn("&quot;group&quot;: null", html)


if __name__ == "__main__":
    unittest.main()
