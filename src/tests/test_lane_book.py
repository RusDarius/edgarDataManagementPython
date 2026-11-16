from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


def _recipe() -> dict:
    return {
        "floor": {"mcap_min": 1e9, "exchanges": ["NASDAQ"]},
        "horizons_days": [7, 28],
        "big_move_pct": 10,
        "clip_pct": [-90, 300],
        "min_rows_best": 1,
        "live_top": 5,
        "cluster": {},
        "pct_fields": ["ey_fwd_ri"],
        "defs": {"hot": "r_1d >= 5", "hotter": "{hot} AND r_1d >= 6"},
        "lanes": [
            {"key": "base", "side": "base", "family": "base", "label": "Every name", "where": "TRUE", "order": "mcap DESC"},
            {"key": "hot", "side": "long", "family": "moves", "label": "Hot", "where": "{hotter}", "order": "r_1d DESC"},
            {"key": "cold", "side": "avoid", "family": "moves", "label": "Cold", "where": "r_1d < 0", "order": "r_1d ASC"},
        ],
        "when": {"value_fields": ["ey_fwd_ri"], "quantiles": 2, "splits": [["all", "TRUE"]],
                 "timing": [["soon", "dte BETWEEN 0 AND 10"], ["later", "TRUE"]]},
        "rotation": {"min_names": 5, "week_share": 0.238},
        "timeline": {"horizon_days": 30},
        "capital": {"cash_floor_pct": 20, "starter_usd": 5000, "add_usd": 2500},
    }


def _build_fixture(root: Path) -> tuple[Path, Path, Path, date]:
    import duckdb

    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(30)]
    store = root / "store.duckdb"
    con = duckdb.connect(str(store))
    con.execute("""CREATE TABLE features (as_of DATE, symbol VARCHAR, ind VARCHAR, sector VARCHAR, exchange VARCHAR, mcap DOUBLE,
                   r_1d DOUBLE, r_1w DOUBLE, r_1m DOUBLE, r_3m DOUBLE, vs50 DOUBLE, vs200 DOUBLE, hi_prox DOUBLE, rsi DOUBLE,
                   relvol DOUBLE, ey_fwd DOUBLE, ey_fwd_ri DOUBLE, pt_up DOUBLE, eps_rev_3m DOUBLE, fcf_margin DOUBLE,
                   nd_ebitda DOUBLE, dte INTEGER, dsp INTEGER)""")
    con.execute("CREATE TABLE labels (symbol VARCHAR, as_of DATE, fwd_7d DOUBLE, fwd_28d DOUBLE)")
    syms = [(f"NASDAQ:A{i}", "X") for i in range(6)] + [(f"NASDAQ:B{i}", "Y") for i in range(6)]
    for d in days:
        for sym, ind in syms:
            hot = sym == "NASDAQ:A0"
            con.execute("INSERT INTO features VALUES (?, ?, ?, 'S', 'NASDAQ', 5e9, ?, ?, ?, 1, 1, 1, -10, 50, 1, 5, ?, 10, 1, 15, 0.5, 20, 30)",
                        [d, sym, ind, 6.0 if hot else 0.0, 1.0 if ind == "X" else -1.0, 5.0 if ind == "X" else -5.0, 3.0 if hot else 0.0])
            if d <= days[-6]:
                con.execute("INSERT INTO labels VALUES (?, ?, ?, ?)", [sym, d, 5.0 if hot else 0.0, 20.0 if hot else 0.0])
    con.close()

    pack = root / "pack"
    (pack / "board").mkdir(parents=True)
    (pack / "outlook").mkdir(parents=True)
    b = duckdb.connect(str(pack / "board" / "board.duckdb"))
    b.execute("""CREATE TABLE names (symbol VARCHAR, company VARCHAR, close DOUBLE, bounce_low DOUBLE, bounce_high DOUBLE, bounce_kill DOUBLE,
                 b28d_q80 DOUBLE, street_px DOUBLE, in_book BOOLEAN, book_usd DOUBLE)""")
    b.execute("INSERT INTO names VALUES ('NASDAQ:A0', 'A zero', 100, 90, 95, 85, 120, 130, FALSE, NULL)")
    b.execute("INSERT INTO names VALUES ('NASDAQ:B1', 'B one', 50, 45, 48, 44, 55, 60, TRUE, 10000)")
    b.close()
    today = days[-1]
    o = duckdb.connect(str(pack / "outlook" / "outlook.duckdb"))
    o.execute("""CREATE TABLE events (date DATE, kind VARCHAR, symbol VARCHAR, slot VARCHAR, detail VARCHAR, status VARCHAR, source VARCHAR,
                 "read" VARCHAR, mcap_b DOUBLE, in_book BOOLEAN)""")
    o.execute("INSERT INTO events VALUES (?, 'earnings', 'NASDAQ:A0', 'AMC', NULL, NULL, 'day file', NULL, 5.0, FALSE)", [today + timedelta(days=3)])
    o.close()
    (pack / "briefing_pack.json").write_text(json.dumps({
        "capital": {"nav_usd": 100000, "cash_usd": 40000, "cash_pct": 40, "equity_usd": 60000, "unrealized_usd": 0},
        "book": [{"symbol": "NASDAQ:B1", "value_usd": 10000, "ind": "Y"}, {"symbol": "XETR:ZZZ", "value_usd": 5000, "ind": "Funds"}],
        "industry_exposure": [{"ind": "Y", "nav_pct": 10.0}],
    }), encoding="utf-8")
    cal = root / "calendar.json"
    cal.write_text(json.dumps({"events": [{"date": (today + timedelta(days=5)).isoformat(), "kind": "macro", "event": "Test CPI",
                                           "status": "confirmed", "source": "test", "read": "rates"}]}), encoding="utf-8")
    return store, pack, cal, today


class TestLaneBook(unittest.TestCase):
    def test_expand_macros(self):
        from generic_utils.lane_book import expand

        defs = {"a": "x > 1", "b": "{a} AND y < 2"}
        self.assertEqual(expand("{b} OR z", defs), "((x > 1) AND y < 2) OR z")
        with self.assertRaises(ValueError):
            expand("{missing} AND x", defs)

    def test_build_lane_book_on_a_synthetic_store(self):
        from generic_utils.lane_book import build_lane_book, write_lane_book

        with tempfile.TemporaryDirectory() as tmp:
            store, pack, cal, today = _build_fixture(Path(tmp))
            payload = build_lane_book(pack, recipe=_recipe(), store=store, calendar=cal)

            lanes = {(r["lane"], r["split"]): r for r in payload["lanes"]}
            hot = lanes[("hot", "all")]
            self.assertEqual(hot["n_28"], 25)
            self.assertAlmostEqual(hot["xs_28"], 20.0)
            self.assertAlmostEqual(hot["p_up_28"], 100.0)
            self.assertAlmostEqual(hot["hit_28"], 100.0)
            self.assertEqual(hot["n_live"], 1)
            self.assertEqual(hot["live_top"], "NASDAQ:A0")
            self.assertAlmostEqual(lanes[("base", "all")]["xs_28"], round(20.0 / 12, 2))
            self.assertNotIn(("hot", "ai"), lanes)  # no cluster: nothing in the ai split

            names = {r["symbol"]: r for r in payload["names"]}
            a0 = names["NASDAQ:A0"]
            self.assertEqual(a0["lanes"], "hot")
            self.assertEqual(a0["best_lane"], "hot")
            self.assertEqual(a0["entry_px"], 95.0)
            self.assertAlmostEqual(a0["month_up_pct"], 26.3)
            self.assertAlmostEqual(a0["risk_pct"], 10.5)
            self.assertAlmostEqual(a0["payoff"], 2.5)
            self.assertEqual(names["NASDAQ:B1"]["in_book"], 1)
            self.assertAlmostEqual(names["NASDAQ:B1"]["nav_pct"], 10.0)
            self.assertEqual(names["XETR:ZZZ"]["best_lane"], "off the store floor")

            tl = payload["timeline"]
            cpi = [r for r in tl if r["detail"] == "Test CPI"]
            self.assertEqual(len(cpi), 1)
            self.assertEqual(cpi[0]["outside"], 1)
            earn = [r for r in tl if r["symbol"] == "NASDAQ:A0"]
            self.assertEqual(earn[0]["lanes"], "hot")
            self.assertEqual(earn[0]["days"], 3)

            cap = payload["capital"][0]
            self.assertEqual(cap["above_floor_usd"], 20000)
            self.assertEqual(cap["starters_at_hint"], 4)

            spread = [r for r in payload["when_spread"] if r["field"] == "ey_fwd_ri" and r["timing"] == "any"]
            self.assertTrue(spread and spread[0]["xs_28_spread"] > 0)
            self.assertTrue({r["dim"] for r in payload["rotation"]} >= {"quadrant", "all industries"})

            out = write_lane_book(payload, Path(tmp) / "out")
            self.assertTrue(Path(out["duckdb"]).exists())
            self.assertIn("lane book", Path(out["highlights"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
