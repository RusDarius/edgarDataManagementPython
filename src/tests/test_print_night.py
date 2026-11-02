from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


class PrintNightRuleTests(unittest.TestCase):
    def test_welch_t_and_short_samples(self):
        from generic_utils.print_night import welch_t

        self.assertIsNone(welch_t([1.0, 2.0], [0.0, 1.0, 2.0]))
        self.assertGreater(welch_t([3.0, 4.0, 5.0, 4.0], [0.0, 1.0, -1.0, 0.0]), 4)

    def test_direction_needs_t_n_halves_and_hit(self):
        from generic_utils.print_night import direction

        cell = {"lift": 1.5, "lift_t": 3.0, "n": 100, "half1_lift": 1.0, "half2_lift": 2.0, "hit_lift": 0.05}
        rules = {"t_min": 2.0, "min_n": 40, "require_halves": True}
        self.assertEqual(direction(cell, **rules), "up")
        self.assertEqual(direction({**cell, "lift": -1.5, "lift_t": -3.0, "half1_lift": -1.0, "half2_lift": -2.0, "hit_lift": -0.05}, **rules), "down")
        self.assertEqual(direction({**cell, "lift_t": 1.9}, **rules), "-")
        self.assertEqual(direction({**cell, "n": 39}, **rules), "-")
        self.assertEqual(direction({**cell, "half2_lift": -0.1}, **rules), "-")
        self.assertEqual(direction({**cell, "half2_lift": -0.1}, **{**rules, "require_halves": False}), "up")
        # the mean rose but fewer prints were positive: a few large moves carried it
        self.assertEqual(direction({**cell, "hit_lift": -0.02}, **rules), "-")
        self.assertEqual(direction({**cell, "hit_lift": -0.02}, **rules, require_hit=False), "up")
        self.assertIsNone(direction({"lift": None, "n": 10}, **rules))

    def test_category_and_session(self):
        from generic_utils.print_night import category, pick_session

        self.assertEqual(category("slot", "BMO~"), "BMO")
        self.assertEqual(category("surprise_mix", "B/M"), "B/M")
        self.assertIsNone(category("slot", ""))
        cal = [{"react_on": "2026-10-29"}, {"react_on": "2026-10-30"}, {"react_on": "2026-11-02"}]
        self.assertEqual(pick_session(cal, "2026-10-29", None), "2026-10-30")
        self.assertEqual(pick_session(cal, "2026-10-29", "today"), "2026-10-29")
        self.assertEqual(pick_session(cal, "2026-10-29", "2026-11-02"), "2026-11-02")

    def test_peer_reaction_window_excludes_the_name(self):
        from generic_utils.print_night import peer_reaction

        events = [{"symbol": f"NYSE:P{i}", "ind": "Chips", "react_on": "2026-10-20", "react_fx": float(i)} for i in range(4)]
        events += [{"symbol": "NYSE:OLD", "ind": "Chips", "react_on": "2026-08-01", "react_fx": 50.0},
                   {"symbol": "NYSE:P0", "ind": "Chips", "react_on": "2026-10-30", "react_fx": 99.0}]
        self.assertEqual(peer_reaction(events, "Chips", "2026-10-30", days=35, min_n=3), (1.5, 4))
        self.assertEqual(peer_reaction(events, "Chips", "2026-10-30", days=35, min_n=3, exclude="NYSE:P3"), (1.0, 3))
        self.assertEqual(peer_reaction(events, "Chips", "2026-10-30", days=35, min_n=5), (None, 4))

    def test_pointer_cells_find_the_quintile_that_paid(self):
        from generic_utils.print_night import pointer_cells

        # runup Q1 (most sold into) holds +3 better in both halves, with most prints positive; the rest is noise around 0
        events = []
        for i in range(500):
            runup = float(i % 100) - 50.0
            noise = ((i * 37) % 11 - 5) / 2.0
            events.append({"print_date": (date(2026, 4, 1) + timedelta(days=i // 3)).isoformat(), "ptr_runup": runup,
                           "hold_fx_28d": (3.0 if runup < -30 else 0.0) + noise})
        recipe = {"quantiles": 5, "t_min": 2.0, "min_n": 40, "require_halves": True}
        cells, edges = pointer_cells(events, [{"key": "runup", "family": "Tape", "event": "runup"}],
                                     [("hold_fx_28d", "h28", "hold 28d")], recipe)
        self.assertEqual(len(edges["runup"]), 4)
        q1 = next(c for c in cells if c["bucket"].startswith("Q1"))
        self.assertEqual(q1["dir"], "up")
        self.assertEqual(q1["n"], 100)
        self.assertAlmostEqual(q1["lift"], 3.0 + q1["mean"] - 3.0 - (sum(e["hold_fx_28d"] for e in events if e["ptr_runup"] >= -30) / 400), places=2)
        self.assertTrue(all(c["dir"] in {"-", "down"} for c in cells if not c["bucket"].startswith("Q1")))


class PrintNightBuildTests(unittest.TestCase):
    def test_build_on_a_small_pack_and_store(self):
        import duckdb

        from generic_utils.print_night import build_print_night, load_recipe

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            # 300 past prints: sold into the print (runup < -5) held +4 better, with more prints positive
            events = []
            for i in range(300):
                runup = float(i % 30) - 15.0
                noise = ((i * 13) % 9 - 4) / 2.0
                events.append({"symbol": f"NYSE:E{i}", "ind": "Chips", "print_date": (date(2026, 4, 1) + timedelta(days=i // 2)).isoformat(),
                               "slot": "AMC" if i % 2 else "BMO", "pre_file": (date(2026, 4, 1) + timedelta(days=i // 2)).isoformat(),
                               "react_on": (date(2026, 4, 2) + timedelta(days=i // 2)).isoformat(), "runup": runup, "prev_react": float((i * 7) % 5 - 2),
                               "eps_surprise": noise, "rev_surprise": noise, "surprise_mix": "B/B" if noise > 0 else "M/M",
                               "react_fx": noise, "hold_fx_7d": noise, "hold_fx_28d": (4.0 if runup < -5 else 0.0) + noise,
                               "drift_fx_28d": noise, "ah_read": "", "ah_to_close": ""})
            _write_csv(pack / "signal_lab" / "print_events.csv", events)
            cal = [{"symbol": "NASDAQ:SOLD", "company": "Sold Inc", "ind": "Chips", "mcap": 5e9, "earn_date": "2026-10-29", "slot": "AMC",
                    "react_on": "2026-10-30", "printed": "False", "runup": -12.0, "react_last": 2.0, "react_abs_mean": 6.0},
                   {"symbol": "NASDAQ:RAN", "company": "Ran Inc", "ind": "Chips", "mcap": 3e9, "earn_date": "2026-10-30", "slot": "BMO",
                    "react_on": "2026-10-30", "printed": "False", "runup": 10.0, "react_last": -1.0, "react_abs_mean": 3.0},
                   {"symbol": "NYSE:LATER", "company": "Later Inc", "ind": "Banks", "mcap": 9e9, "earn_date": "2026-11-03", "slot": "BMO",
                    "react_on": "2026-11-03", "printed": "False", "runup": -12.0, "react_last": 1.0, "react_abs_mean": 2.0}]
            _write_csv(pack / "earnings_week" / "calendar.csv", cal)
            (pack / "briefing_pack.json").write_text('{"created_at_utc": "2026-10-29T14:00:00+00:00"}', encoding="utf-8")  # scan day, not the clock
            store = root / "store.duckdb"
            con = duckdb.connect(str(store))
            con.execute("""CREATE TABLE prints (symbol VARCHAR, print_date DATE, slot VARCHAR, react_on DATE, runup DOUBLE, prev_react DOUBLE,
                           eps_surprise DOUBLE, rev_surprise DOUBLE, react DOUBLE, drift DOUBLE)""")
            con.execute("INSERT INTO prints VALUES ('NASDAQ:SOLD', DATE '2026-07-29', 'AMC', DATE '2026-07-30', -3, 1, 5, 1, 2.0, 1.0)")
            con.execute("CREATE TABLE features (symbol VARCHAR, as_of DATE)")
            con.close()
            recipe = load_recipe()
            recipe["pointers"] = [p for p in recipe["pointers"] if p["key"] in {"runup", "prev_react", "slot"}]
            payload = build_print_night(pack, recipe=recipe, store=store)
            named = build_print_night(pack, recipe=recipe, store=store, ids=["LATER"])
        # a named id joins the session's names; it never narrows them
        self.assertEqual({r["symbol"] for r in named["names"]}, {"NASDAQ:SOLD", "NASDAQ:RAN", "NYSE:LATER"})
        spec = payload["spec"]
        self.assertEqual(spec["sessions"], ["2026-10-30"])
        names = {r["symbol"]: r for r in payload["names"]}
        self.assertEqual(set(names), {"NASDAQ:SOLD", "NASDAQ:RAN"})
        self.assertEqual(names["NASDAQ:SOLD"]["h28_up"], 1)
        self.assertEqual(names["NASDAQ:SOLD"]["h28_up_list"], "runup")
        self.assertEqual(names["NASDAQ:RAN"]["h28_up"], 0)
        self.assertFalse(names["NASDAQ:SOLD"]["in_store"])
        self.assertEqual(names["NASDAQ:SOLD"]["own_move_starter_usd"], 300)
        matrix = {(r["symbol"], r["measure"]): r for r in payload["matrix"]}
        self.assertTrue(matrix[("NASDAQ:SOLD", "hold_fx_28d")]["x_runup"].startswith("▲+"))
        self.assertNotIn("▲", matrix[("NASDAQ:RAN", "hold_fx_28d")]["x_runup"] or "")
        self.assertIsNone(matrix[("NASDAQ:SOLD", "hold_fx_28d")].get("x_eps_rev_3m"))  # not in this recipe's pointers
        self.assertEqual([h["symbol"] for h in payload["history"]], ["NASDAQ:SOLD"])
        base = {(b["phase"], b["measure"]): b for b in payload["base"]}
        self.assertEqual(base[("pre", "hold_fx_28d")]["n"], 300)


class RendererArrowAndScreenTests(unittest.TestCase):
    def test_arrow_cells_screens_and_guide(self):
        from generic_utils.canvas_page import _cell_html, build_page, render_html

        self.assertIn('class="num up"', _cell_html("▲+1.2", False))
        self.assertIn('class="num dn"', _cell_html("▼-0.8", False))
        self.assertNotIn("up", _cell_html("+0.4", False))
        spec = {"name": "t-20261029", "guide": {"intro": "How to use.", "sections": [{"title": "Steps", "rows": [["1", "Screen"]]}]},
                "tabs": [{"label": "Tonight", "blocks": [{"type": "table", "title": "M", "headers": ["symbol", "rx_up", "rx_dn"],
                                                          "rows": [["NYSE:A", "2", "0"]],
                                                          "screens": [{"label": "Leans up", "filters": {"rx_up": ">=2", "nope": "1"}}]}]}]}
        page = build_page(spec, base_dir=Path("."))
        self.assertTrue(any("nope" in w for w in page["warnings"]))
        html = render_html(page)
        self.assertIn('data-tab="guide"', html)
        self.assertLess(html.index('data-tab="guide"'), html.index('data-tab="ledger"'))
        self.assertIn("Leans up", html)
        with self.assertRaises(ValueError):
            build_page({"name": "x", "tabs": [{"label": "Guide", "blocks": []}]}, base_dir=Path("."))

    def test_tab_requires_skips_missing_scan(self):
        from generic_utils.canvas_page import render_template

        template = {"name": "o-{DAY}", "page_key": "o", "tabs": [
            {"label": "Always", "blocks": [{"type": "text", "text": "x"}]},
            {"label": "On demand", "requires": "{PACK}/print_night/print_night.duckdb", "blocks": [{"type": "text", "text": "y"}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            spec = render_template(template, pack=tmp, day="20261029", judgment=None)
            self.assertEqual([t["label"] for t in spec["tabs"]], ["Always"])
            (Path(tmp) / "print_night").mkdir()
            (Path(tmp) / "print_night" / "print_night.duckdb").write_bytes(b"")
            spec = render_template(template, pack=tmp, day="20261029", judgment=None)
            self.assertEqual([t["label"] for t in spec["tabs"]], ["Always", "On demand"])


if __name__ == "__main__":
    unittest.main()
