import unittest


class TestNamedRowsFromCsv(unittest.TestCase):
    def test_bare_ids_match_prefixed_rows_in_caller_order_and_missing_are_listed(self):
        from generic_utils.series import select_named_rows

        rows = [{"symbol": "NYSE:LMT", "close": "595.9", "left": "10.6"},
                {"symbol": "NYSE:CRC", "close": "54.6", "left": "37.9"},
                {"symbol": "ASX:LMT", "close": "1.0", "left": "0"}]
        out = select_named_rows(rows, columns=["symbol", "left", "nope"], ids=["CRC", "NYSE:LMT", "ZZZ"])
        self.assertEqual([r["symbol"] for r in out["rows"]], ["NYSE:CRC", "NYSE:LMT"])
        self.assertEqual(out["spec"]["missing_ids"], ["ZZZ"])
        self.assertEqual(out["spec"]["missing_columns"], ["nope"])
        self.assertEqual(set(out["rows"][0]), {"symbol", "left"})


class TestTapeCarriesBandInputs(unittest.TestCase):
    def test_tape_rows_get_atr_and_price_levels_for_entry_book(self):
        from operator_briefing.compile import _tape_for_movers

        af = [{"symbol": "NYSE:CRC", "close": 50.0, "street_left": 20.0, "pt": 60.0, "atr": 1.5, "atrp": 3.0}]
        tape = _tape_for_movers(af, {"NYSE:CRC": {"left": 40.0}})
        self.assertEqual((tape[0]["atr"], tape[0]["atrp"]), (1.5, 3.0))
        self.assertEqual(tape[0]["street_px"], 60.0)
        self.assertAlmostEqual(tape[0]["target_px"], 70.0)


class TestBriefLines(unittest.TestCase):
    def test_one_line_per_name_with_conflicts_and_missing(self):
        from operator_briefing.example_entry import _brief_lines

        text = _brief_lines({"pack_id": "P", "missing": ["GXO"], "rows": [
            {"symbol": "NASDAQ:MU", "in_book": True, "stance": "HOLD", "support": 0.52,
             "raw": {"close": 884.71, "mcap": 1e12, "rsi": 39.3}, "bo_path": {"d_bo": 0.45}, "conflicts": ["x"]}]})
        lines = text.splitlines()
        self.assertEqual(lines[1], "NASDAQ:MU BOOK HOLD support=0.52 close=884.71 rsi=39.3 d_bo=0.45 conflicts=x")
        self.assertIn("GXO", lines[2])


class TestQualifiedIds(unittest.TestCase):
    def test_qualify_uses_catalogs_in_order_and_never_guesses(self):
        from generic_utils.symbol_identity import SymbolCatalog, display_id, qualify, qualify_rows

        day = SymbolCatalog.build(["NYSE:MMS", "ASX:MMS", "NASDAQ:MU"])
        book = SymbolCatalog.build(["NYSE:MMS"])
        self.assertEqual(qualify("mu", day), "NASDAQ:MU")
        self.assertEqual(qualify("MMS", day), "MMS")  # two listings: stays bare
        self.assertEqual(qualify("MMS", day, book), "NYSE:MMS")
        self.assertEqual(qualify("nyse:mms"), "NYSE:MMS")
        self.assertEqual(display_id({"ticker": "VUAA", "matched_symbol": "XETR:VUAA"}), "XETR:VUAA")
        rows = qualify_rows([{"ticker": "MU"}], day)
        self.assertEqual((rows[0]["symbol"], rows[0]["ticker"]), ("NASDAQ:MU", "MU"))

    def test_brief_history_rows_are_exchange_ticker_for_bare_and_qualified_priors(self):
        from generic_utils.brief_history import build_brief_history, format_digest_md

        sessions = [
            {"day": "2026-10-20", "pack": "", "source_file": "a.json",
             "actions": [{"symbol": "", "ticker": "MU", "action": "ADD"}, {"symbol": "NYSE:MMS", "ticker": "MMS", "action": "NEW"}]},
            {"day": "2026-10-21", "pack": "", "source_file": "b.json",
             "actions": [{"symbol": "NASDAQ:MU", "ticker": "MU", "action": "HOLD"}]},
        ]
        marks = {"2026-10-20": {"NASDAQ:MU": 100.0, "NYSE:MMS": 50.0, "ASX:MMS": 1.0},
                 "2026-10-21": {"NASDAQ:MU": 101.0, "NYSE:MMS": 51.0},
                 "2026-10-23": {"NASDAQ:MU": 110.0, "NYSE:MMS": 55.0}}
        payload = build_brief_history(sessions, marks, latest_day="2026-10-23")
        names = {row["symbol"]: row for row in payload["names"]}
        self.assertEqual(set(names), {"NASDAQ:MU", "NYSE:MMS"})  # bare MU and NASDAQ:MU are one name
        self.assertEqual(names["NASDAQ:MU"]["n_sessions"], 2)
        self.assertEqual(names["NASDAQ:MU"]["ticker"], "MU")
        self.assertTrue(all(":" in row["symbol"] for row in payload["events"]))
        self.assertIn("| NASDAQ:MU |", format_digest_md(payload))


class TestIdGuards(unittest.TestCase):
    def test_bare_tickers_in_and_qualify_text(self):
        from generic_utils.symbol_identity import bare_tickers_in, qualify_text, ticker_map

        self.assertEqual(bare_tickers_in("NYSE:RHI / NASDAQ:FCNCA"), [])
        self.assertEqual(bare_tickers_in("chips (TTMI, FLEX) and NYSE:BRK.B"), ["TTMI", "FLEX"])
        by_ticker = ticker_map(["NASDAQ:MU", "NYSE:CRC", "NYSE:MMS", "ASX:MMS"])
        text, swapped = qualify_text("Buy CRC, hold MU and MMS before FOMC; NASDAQ:MU stays.", by_ticker)
        self.assertEqual(text, "Buy NYSE:CRC, hold NASDAQ:MU and MMS before FOMC; NASDAQ:MU stays.")
        self.assertEqual(swapped, ["CRC", "MU"])

    def test_page_warns_on_bare_ids_in_tables_and_judged_text(self):
        from generic_utils.canvas_page import build_page, render_template

        template = {"name": "u-{DAY}", "tabs": [{"label": "Plan", "blocks": [
            {"type": "callout", "from": "pages.u.blocks.bias"},
            {"type": "table", "from": "pages.u.blocks.calls", "headers": ["Symbol", "Course"]}]}], "page_key": "u"}
        judgment = {"courses": {"NYSE:CRC": {"Course": "NEW"}, "MU": {"Course": "HOLD"}},
                    "pages": {"u": {"blocks": {"bias": {"title": "Bias", "text": "Buy CRC."},
                                               "calls": {"rows": [["NYSE:CRC", "NEW"], ["TTMI / FLEX", "WAIT"]]}}}}}
        spec = render_template(template, pack="P", day="20261023", judgment=judgment)
        spec["tabs"][0]["blocks"][1]["extra"] = {"MU": {"Course": "HOLD"}}
        from pathlib import Path
        warnings = build_page(spec, base_dir=Path("."))["warnings"]
        self.assertTrue(any("'CRC' in page text" in w for w in warnings))
        self.assertTrue(any("TTMI" in w and "symbol column" in w for w in warnings))
        self.assertTrue(any("judgment key 'MU'" in w for w in warnings))

    def test_priors_are_written_exchange_ticker_and_merge_legacy_keys(self):
        import json
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest import mock

        from generic_utils import priors

        with TemporaryDirectory() as tmp:
            pack = Path(tmp) / "briefing_pack_x"
            pack.mkdir()
            (pack / "briefing_pack.json").write_text(json.dumps({"book": [{"symbol": "NASDAQ:MU"}], "sources": {}}), encoding="utf-8")
            out_dir = Path(tmp) / "priors"
            out_dir.mkdir()
            (out_dir / "20261023_operator_actions.json").write_text(json.dumps({"actions": {"MU": {"action": "ADD"}}}), encoding="utf-8")
            from generic_utils.symbol_identity import SymbolCatalog
            cats = (SymbolCatalog.build(["NASDAQ:MU"]), SymbolCatalog.build(["NASDAQ:MU", "NYSE:CRC"]))
            judgment = {"courses": {"NYSE:CRC": {"Course": "NEW", "Grade": "Med", "Reason": "Starter beside MU."},
                                    "NYSE:RHI": {"Course": "PASS", "Reason": "paid"}}}
            with mock.patch.object(priors, "pack_catalogs", return_value=cats):
                result = priors.write_priors("20261023", judgment, pack_dir=pack, priors_dir=out_dir, notes="Buy CRC.")
            data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertEqual(set(data["actions"]), {"NASDAQ:MU", "NYSE:CRC"})  # PASS not held: skipped; legacy MU qualified
        self.assertEqual(data["actions"]["NYSE:CRC"]["note"], "Starter beside NASDAQ:MU.")
        self.assertEqual(data["notes"], "Buy NYSE:CRC.")
        self.assertEqual((data["id_format"], result["bare_ids"]), ("EXCHANGE:TICKER", []))


if __name__ == "__main__":
    unittest.main()
