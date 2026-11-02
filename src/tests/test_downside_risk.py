from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


class DownsideMathTests(unittest.TestCase):
    def test_quantile_beta_drawdowns_and_episodes(self):
        from generic_utils.downside_risk import drawdowns, episodes_of, levels_from_steps, ols_beta, quantile, window_ends

        self.assertEqual(quantile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)
        self.assertAlmostEqual(ols_beta([2.0, -4.0, 6.0], [1.0, -2.0, 3.0]), 2.0)
        lvl = levels_from_steps([10.0, -10.0, 0.0, 10.0, 5.0])  # 100, 110, 99, 99, 108.9, 114.345
        self.assertAlmostEqual(drawdowns(lvl)[2], -10.0)
        eps = episodes_of(lvl, min_pct=1.0, top=3)
        self.assertEqual((eps[0]["peak_i"], eps[0]["trough_i"], round(eps[0]["depth_pct"], 4), eps[0]["recovered_i"]), (1, 2, -10.0, 5))
        files = [date(2026, 1, d) for d in (5, 6, 7, 9, 20)]
        self.assertEqual(window_ends(files, 2, 1), [(0, 2), (1, 3), (2, 3)])  # 9 -> 20 is past the tolerance


class DownsideBuildTests(unittest.TestCase):
    def test_book_replay_and_scenarios_on_a_small_store(self):
        import duckdb

        from generic_utils.downside_risk import build_downside_risk

        closes = {"NASDAQ:BIG": [100, 110, 99, 99, 108.9], "NYSE:SMALL": [100] * 5, "NASDAQ:BK": [100, 120, 96, 96, 115.2]}
        mcap = {"NASDAQ:BIG": 6e9, "NYSE:SMALL": 3e9, "NASDAQ:BK": 3e9}
        days = [date(2026, 1, d) for d in (5, 6, 7, 8, 9)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store.duckdb"
            con = duckdb.connect(str(store))
            con.execute("CREATE TABLE panel (as_of DATE, symbol VARCHAR, exchange VARCHAR, type VARCHAR, ind VARCHAR, mcap DOUBLE, close DOUBLE)")
            con.execute("CREATE TABLE series (symbol VARCHAR, as_of DATE, split_flag BOOLEAN, split_seq INTEGER)")
            for sym, px in closes.items():
                for d, c in zip(days, px):
                    con.execute("INSERT INTO panel VALUES (?, ?, ?, 'stock', 'Chips', ?, ?)", [d, sym, sym.split(":")[0], mcap[sym], c])
                    con.execute("INSERT INTO series VALUES (?, ?, false, 0)", [sym, d])
            con.close()
            pack = root / "pack"
            _write_csv(pack / "book_risk" / "book_risk.csv", [
                {"symbol": "NASDAQ:BK", "industry": "Chips", "value_usd": 1000, "close_quote": 115.2, "beta": 1.7},
                {"symbol": "OMXCOP:EU", "industry": "Pharma", "value_usd": 500, "close_quote": 250, "beta": 0.5}])
            _write_csv(pack / "deterioration" / "names.csv", [{"symbol": "NASDAQ:BK", "lo52": 80}, {"symbol": "OMXCOP:EU", "lo52": ""}])
            (pack / "briefing_pack.json").write_text(json.dumps({"capital": {"nav_usd": 2000, "cash_usd": 500}}), encoding="utf-8")
            recipe = {"floor_mcap": 2e9, "exchanges": ["NASDAQ", "NYSE"], "types": ["stock"], "horizons_days": [1], "window_tolerance_days": 1,
                      "clip_pct": [-90, 300], "quantiles": [0.5], "loss_marks_pct": [-10], "market_moves_pct": [-10], "beta_min_steps": 2,
                      "episodes_per_series": 2, "episode_min_pct": 1, "series": ["cap", "med", "cluster", "book"],
                      "cluster": {"ids": ["BK"]}, "level_fields": ["lo52"], "state_fields": []}
            payload = build_downside_risk(pack, recipe=recipe, store=store)
        steps = payload["steps"]
        self.assertEqual([s["cap_pct"] for s in steps], [10.0, -10.0, 0.0, 10.0])  # (6 x BIG + 3 x 0 + 3 x BK) / 12
        self.assertEqual([s["cluster_pct"] for s in steps], [20.0, -20.0, 0.0, 20.0])
        self.assertEqual([s["book_pct"] for s in steps], [15.0, -15.0, 0.0, 15.0])  # BK 2x cap, EU proxied at 0.5x cap
        self.assertEqual(steps[0]["proxy_usd"], 500)
        lines = {r["symbol"]: r for r in payload["lines"]}
        self.assertEqual((lines["NASDAQ:BK"]["beta_store"], lines["NASDAQ:BK"]["beta_src"], lines["OMXCOP:EU"]["beta_src"]), (2.0, "store", "tv"))
        self.assertTrue(lines["NASDAQ:BK"]["in_cluster"])
        self.assertEqual((lines["NASDAQ:BK"]["to_lo52_pct"], lines["NASDAQ:BK"]["to_lo52_usd"]), (-30.56, -306))
        cap_ep = next(e for e in payload["episodes"] if e["series"] == "cap")
        self.assertEqual((cap_ep["peak"], cap_ep["trough"], cap_ep["depth_pct"], cap_ep["recovered_on"]), (days[1], days[2], -10.0, None))
        self.assertEqual((cap_ep["book_usd"], cap_ep["book_nav_pct"]), (-225, -11.25))  # BK -20% x 1000, EU -5% x 500
        sc = payload["scenarios"][0]
        self.assertEqual((sc["book_usd_store"], sc["nav_pct_store"], sc["cluster_usd_store"]), (-225, -11.25, -200))
        cap1 = next(d for d in payload["dist"] if d["series"] == "cap" and d["horizon_d"] == 1)
        self.assertEqual((cap1["n_windows"], cap1["worst_pct"], cap1["worst_start"], cap1["p_le_10"]), (4, -10.0, days[1], 0.25))


if __name__ == "__main__":
    unittest.main()
