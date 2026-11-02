from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


class PrintBriefWordingTests(unittest.TestCase):
    def test_numbers_read_as_words(self):
        from generic_utils.print_brief import money, pct, pp, rel, sentence

        self.assertEqual(pct(-17.47), "-17.5%")
        self.assertEqual(money(-403), "-$403")
        self.assertEqual(money(874_910_000), "$874.91M")
        self.assertEqual(pp(None), "-")
        self.assertEqual(rel(-26.2, "its own median"), "26% under its own median")
        self.assertEqual(rel(42, "its industry"), "42% over its industry")
        self.assertIsNone(pct(None))
        self.assertEqual(sentence("A", None, "B"), "A, B.")
        self.assertIsNone(sentence(None))

    def test_headline_before_and_after_the_print(self):
        from generic_utils.print_brief import headline

        pre = {"phase": "pre", "runup": -17.5, "react_last": -20.9, "pe_nfy": 24.0, "pe_nfy_vs_ind": 42.0, "pe_nfy_vs_own": -26.0,
               "h28_up": 5, "h28_dn": 2, "h7_up": 4, "h7_dn": 1, "react_abs_mean": 17.0}
        text = headline(pre)
        self.assertTrue(text.startswith("Sold into the print (-17.5% in 14 days) after a down print (-20.9%)"))
        self.assertIn("42% over its industry, 26% under its own median", text)
        self.assertIn("28-day hold 5 up / 2 down, 7-day 4 / 1", text)
        post = {"phase": "post", "surprise_mix": "M/M", "last_surprise": -29.7, "rev_surprise": -1.0, "react_fx": -13.6}
        self.assertEqual(headline(post), "Printed M/M, EPS -29.7%, revenue -1.0%, reaction -13.6% vs the market.")

    def test_peers_rank_and_season(self):
        from generic_utils.print_brief import month_rank, peer_summary, season_prints

        peers = [{"symbol": f"NYSE:P{i}", "ind": "Chips", "mcap_b": float(i), "pe_nfy": 10.0 + i, "m1": float(i), "m3": 0.0} for i in range(5)]
        peers.append({"symbol": "NYSE:ME", "ind": "Chips", "mcap_b": 1.0, "pe_nfy": -5.0, "m1": 2.5, "m3": 0.0})
        s = peer_summary("NYSE:ME", "Chips", peers, listed=2)
        self.assertEqual((s["peer_count"], s["peer_pe_med"], s["peer_m1_med"]), (5, 12.0, 2.0))
        self.assertEqual([p["symbol"] for p in s["largest"]], ["NYSE:P4", "NYSE:P3"])
        self.assertEqual(month_rank(2.5, [0.0, 1.0, 2.0, 3.0, 4.0]), 3)
        events = [{"symbol": "NYSE:P1", "ind": "Chips", "react_on": "2026-10-20", "react_fx": 4.0},
                  {"symbol": "NYSE:P2", "ind": "Chips", "react_on": "2026-10-28", "react_fx": -2.0},
                  {"symbol": "NYSE:P3", "ind": "Chips", "react_on": "2026-08-01", "react_fx": 9.0},
                  {"symbol": "NYSE:ME", "ind": "Chips", "react_on": "2026-10-27", "react_fx": 1.0}]
        self.assertEqual([e["symbol"] for e in season_prints(events, "Chips", "2026-10-30", days=35, exclude="NYSE:ME")], ["NYSE:P2", "NYSE:P1"])


class PrintBriefBuildTests(unittest.TestCase):
    def test_cards_from_a_small_pack(self):
        import duckdb

        from generic_utils.print_brief import build_print_brief, load_recipe

        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp)
            (pack / "print_night").mkdir()
            con = duckdb.connect(str(pack / "print_night" / "print_night.duckdb"))
            con.execute("""CREATE TABLE names AS SELECT * FROM (VALUES
                ('NYSE:ME', 'Me Inc', 'Chips', 'AMC', '2026-10-29', '2026-10-30', 'pre', 3.0, -8.0, 2.0, 5.0, 3, 1, 2, 0, 'runup, slot', 'eps_rev_3m',
                 'sold into & previous print up', -9.4, -470.0, 250.0, NULL),
                ('NYSE:YOU', 'You Inc', 'Banks', 'BMO', '2026-10-30', '2026-10-30', 'pre', 9.0, 4.0, -1.0, 2.0, 0, 4, 0, 2, NULL, 'rvol_20',
                 'ran into & previous print up', -9.2, -460.0, 100.0, 1000.0))
                t(symbol, company, ind, slot, earn_date, react_on, phase, mcap_b, runup, react_last, react_abs_mean, h28_up, h28_dn, h7_up, h7_dn,
                  h28_up_list, h28_dn_list, setup_cell, setup_rx_q10, tail_starter_usd, own_move_starter_usd, book_usd)""")
            con.execute("""CREATE TABLE cells AS SELECT * FROM (VALUES
                ('surprise_mix', 'B/B', 'react_fx', 3000, 3.4, 13.7, 'up'), ('surprise_mix', 'M/M', 'react_fx', 478, -5.1, -11.2, 'down'),
                ('surprise_mix', 'B/B', 'drift_fx_28d', 2500, 1.3, 3.3, 'up'), ('ah_read', 'Q1 [min, -3.5)', 'ah_to_close', 50, 1.7, 2.0, 'up'),
                ('ah_read', 'Q1 [min, -3.5)', 'react_fx', 49, -10.0, -6.9, 'down'), ('eps_rev_3m', 'Q5 [+6.6, max)', 'hold_fx_28d', 379, -1.8, -2.3, 'down'))
                t(pointer, bucket, measure, n, lift, lift_t, dir)""")
            con.execute("CREATE TABLE pointers AS SELECT 'NYSE:ME' AS symbol, 'eps_rev_3m' AS pointer, 'Q5 [+6.6, max)' AS bucket, 'hold_fx_28d' AS measure")
            con.close()
            _write_csv(pack / "earnings_week" / "calendar.csv", [
                {"symbol": "NYSE:ME", "eps_next_q": 1.33, "eps_last_q": 1.25, "eps_q_step": 6.3, "rev_next_q": 874910000, "rev_last_q": 804900000,
                 "rev_q_step": 8.7, "eps_rev_3m": 8.8, "eps_rev_1m": 0.3, "pe_nfy": 24.0, "pe_nfy_vs_ind": 42.0, "pe_nfy_ind_med": 17.0, "m1": -10.0,
                 "react_hist": "07-30 AMC -20.9% (s +32)"},
                {"symbol": "NYSE:YOU", "eps_next_q": "", "eps_last_q": "", "eps_q_step": "", "rev_next_q": "", "rev_last_q": "", "rev_q_step": "",
                 "eps_rev_3m": "", "eps_rev_1m": "", "pe_nfy": "", "pe_nfy_vs_ind": "", "pe_nfy_ind_med": "", "m1": "", "react_hist": ""}])
            (pack / "outlook").mkdir()
            o = duckdb.connect(str(pack / "outlook" / "outlook.duckdb"))
            o.execute("CREATE TABLE names AS SELECT * FROM (VALUES ('NYSE:P1', 'Chips', 50.0, 20.0, 3.0, 1.0), ('NYSE:ME', 'Chips', 3.0, 24.0, -10.0, 0.0)) "
                      "t(symbol, ind, mcap_b, pe_nfy, m1, m3)")
            o.close()
            payload = build_print_brief(pack, recipe=load_recipe())
        cards = {c["symbol"]: c for c in payload["cards"]}
        me = cards["NYSE:ME"]
        self.assertIn("Consensus EPS 1.33 vs 1.25 last quarter (+6.3%), revenue $874.91M vs $804.90M (+8.7%)", me["quarter_text"])
        self.assertIn("Q5 [+6.6, max) of past prints, which held 28 days -1.8 pp vs the rest (a measured lean)", me["quarter_text"])
        self.assertIn("Chips: 1 US $2B+ peers", me["peers_text"])
        self.assertIn("NYSE:P1 (P/E 20.0, 1M +3.0%)", me["peers_text"])
        items = me["watch_items"].split(" | ")
        self.assertTrue(any(i.startswith("The bar: EPS 1.33") for i in items))
        self.assertTrue(any("After hours below -3.5%" in i for i in items))
        self.assertTrue(any(i.startswith("Set-up: sold into after an up print") for i in items))
        self.assertTrue(any("-$470" in i for i in items))
        you = cards["NYSE:YOU"]
        self.assertFalse(any("After hours below" in i for i in you["watch_items"].split(" | ")))  # BMO
        self.assertIn("No US $2B+ peer set", you["peers_text"])
        self.assertIsNone(you["quarter_text"])
        self.assertEqual({r["ind"] for r in payload["industries"]}, {"Chips", "Banks"})


class CardsBlockTests(unittest.TestCase):
    def test_cards_render_with_ledger_checks_notes_and_links(self):
        import duckdb

        from generic_utils.canvas_page import _inline, build_page, render_html

        self.assertIn('<a href="https://x.org/q/AAPL" target="_blank"', _inline("[preview](https://x.org/q/AAPL)"))
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "c.duckdb"
            con = duckdb.connect(str(db))
            con.execute("CREATE TABLE cards AS SELECT 'NYSE:ME' AS symbol, 'Me Inc' AS company, 'Sold into the print.' AS headline, "
                        "-8.0 AS runup, 1.0 AS mystery_metric, 'Into the print: ...' AS setup_text, 'a | b' AS watch_items")
            con.close()
            spec = {"name": "pb-20261029", "tabs": [{"label": "Focus", "blocks": [{
                "type": "cards", "title": "Focus", "source": {"duckdb": str(db), "sql": "SELECT * FROM cards"},
                "subtitle_fields": ["company"], "headline_field": "headline", "metrics": ["runup", "mystery_metric"],
                "sections": [["Going in", "setup_text"]], "list_sections": [["Read", "watch_items"]],
                "extra": {"NYSE:ME": {"Course": "WATCH", "Reason": "why"}}, "notes": {"NYSE:ME": ["OUTSIDE: note"]}}]}]}
            page = build_page(spec, base_dir=Path(tmp))
        self.assertTrue(any("mystery_metric" in w for w in page["warnings"]))
        self.assertFalse(any("'runup'" in w for w in page["warnings"]))
        card = page["tabs"][0]["blocks"][0]["cards"][0]
        self.assertEqual(card["lists"], [["Read", ["a", "b"]]])
        html = render_html(page)
        self.assertIn('id="card-NYSE-ME"', html)
        self.assertIn("OUTSIDE: note", html)
        self.assertIn("WATCH", html)


if __name__ == "__main__":
    unittest.main()
