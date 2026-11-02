from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path


def _epoch(text: str, hour: int = 12) -> str:
    d = date.fromisoformat(text)
    return str(int(datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc).timestamp()))


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class CatalystEventTests(unittest.TestCase):
    def test_week_label(self):
        from generic_utils.catalyst_outlook import week_label

        scan = date(2026, 10, 28)  # a Wednesday
        self.assertEqual(week_label(date(2026, 10, 26), scan), "W0")
        self.assertEqual(week_label(date(2026, 11, 1), scan), "W0")
        self.assertEqual(week_label(date(2026, 11, 2), scan), "W1")
        self.assertEqual(week_label(date(2026, 12, 9), scan), "W6")

    def test_name_events_inside_the_horizon_only(self):
        from generic_utils.catalyst_outlook import name_events

        row = {"earn_next": _epoch("2026-11-04", 21), "earn_next_time": "1", "exdiv": _epoch("2026-11-10"),
               "div_amt": "0.5", "div_yield": "2.4", "ipo_date": _epoch("2026-05-14")}
        events = name_events(row, scan_day=date(2026, 10, 28), end=date(2026, 12, 9), lockup_days=180)
        kinds = {e["kind"]: e for e in events}
        self.assertEqual(kinds["earnings"]["slot"], "AMC")
        self.assertEqual(kinds["ex_dividend"]["detail"], "0.5/sh yield 2.40%")
        self.assertEqual(kinds["ipo_lockup"]["date"], date(2026, 11, 10))
        self.assertEqual(name_events(row, scan_day=date(2026, 10, 28), end=date(2026, 11, 1), lockup_days=180), [])

    def test_calendar_events_one_row_per_symbol(self):
        from generic_utils.catalyst_outlook import calendar_events

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cal.json"
            path.write_text(json.dumps({"events": [
                {"date": "2026-11-10", "kind": "macro", "event": "CPI", "status": "confirmed", "source": "x"},
                {"date": "2026-11-14", "kind": "regulatory", "event": "PDUFA", "symbols": ["NASDAQ:AAA", "NASDAQ:BBB"]},
                {"date": "2027-01-10", "kind": "macro", "event": "outside the horizon"}]}), encoding="utf-8")
            rows = calendar_events(path, scan_day=date(2026, 10, 28), end=date(2026, 12, 9))
        self.assertEqual(len(rows), 3)
        self.assertIsNone(rows[0]["symbol"])
        self.assertEqual({r["symbol"] for r in rows[1:]}, {"NASDAQ:AAA", "NASDAQ:BBB"})


class CatalystBuildTests(unittest.TestCase):
    def test_build_on_a_small_pack(self):
        import duckdb

        from generic_utils.catalyst_outlook import build_outlook, load_recipe, write_outlook

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "tradingview_all_fields_28_10_2026.duckdb"
            con = duckdb.connect(str(db))
            con.execute("CREATE TABLE run_metadata (run_id VARCHAR, created_at_utc TIMESTAMP)")
            con.execute("INSERT INTO run_metadata VALUES ('full', '2026-10-28 15:54:00'), ('intraday', '2026-10-28 17:10:00')")
            con.execute("CREATE TABLE all_fields_rows (run_id VARCHAR, symbol VARCHAR, description VARCHAR, industry VARCHAR, market VARCHAR, "
                        "type VARCHAR, close VARCHAR, market_cap_basic VARCHAR, earnings_release_next_date VARCHAR, "
                        "earnings_release_next_time VARCHAR, ex_dividend_date_upcoming VARCHAR, dividend_amount_upcoming VARCHAR, "
                        "dividends_yield_current VARCHAR, ipo_offer_date VARCHAR, recommendation_total VARCHAR)")
            rows = [
                ("intraday", "NASDAQ:AAA", "Aaa", "Software", "america", "stock", "100", "5e9", _epoch("2026-11-04", 21), "1", None, None, None, None, None),
                ("intraday", "NYSE:BBB", "Bbb", "Banks", "america", "stock", "50", "3e9", None, None, None, None, "3.1", None, None),
                ("intraday", "NYSE:TINY", "Tiny", "Banks", "america", "stock", "5", "1e8", None, None, None, None, None, None, None),
                ("full", "NASDAQ:AAA", "Aaa", "Software", "america", "stock", "99", "5e9", None, None, None, None, None, _epoch("2026-05-14"), "6"),
                ("full", "NYSE:BBB", "Bbb", "Banks", "america", "stock", "49", "3e9", None, None, _epoch("2026-11-12"), "0.4", "3.1", None, "12"),
            ]
            con.executemany("INSERT INTO all_fields_rows VALUES (" + ", ".join("?" for _ in range(15)) + ")", rows)
            con.close()
            pack = root / "briefing_pack_test"
            pack.mkdir()
            (pack / "briefing_pack.json").write_text(json.dumps({"created_at_utc": "2026-10-28T17:12:00+00:00"}), encoding="utf-8")
            _write_csv(pack / "alpha_model" / "live.csv", [{"symbol": "NASDAQ:AAA", "dec_28d": "10", "pred_28d": "2.5"}])
            recipe = load_recipe()
            recipe["calendar_path"] = None
            payload = build_outlook(pack, recipe=recipe, database=db, history_root=root / "none")
            names = {r["symbol"]: r for r in payload["names"]}
            self.assertEqual(set(names), {"NASDAQ:AAA", "NYSE:BBB"})
            self.assertEqual(list(payload["names"][0])[:4], ["symbol", "company", "ind", "mcap_b"])
            self.assertEqual(names["NASDAQ:AAA"]["earn_week"], "W1")
            self.assertEqual(names["NASDAQ:AAA"]["earn_slot"], "AMC")
            self.assertEqual(names["NASDAQ:AAA"]["analysts"], 6.0)
            self.assertEqual(names["NASDAQ:AAA"]["dec_28d"], "10")
            self.assertIn("ipo_lockup 11-10", names["NASDAQ:AAA"]["catalysts"])
            self.assertEqual(names["NYSE:BBB"]["next_kind"], "ex_dividend")
            self.assertIsNone(names["NYSE:BBB"]["pack_lists"])
            self.assertEqual(payload["spec"]["catalyst_run"], "full")
            days = {d["date"]: d for d in payload["days"]}
            self.assertEqual(days["2026-11-04"]["largest_earnings"], "NASDAQ:AAA AMC")
            exported = write_outlook(payload, pack / "outlook")
            self.assertEqual(exported["tables"]["names"], 2)


class ColumnGroupRenderTests(unittest.TestCase):
    def test_group_labels_and_header_row(self):
        from generic_utils.canvas_page import _render_table, column_group_labels

        labels = column_group_labels(["symbol", "close", "rsi", "pe_fwd", "note"], ["symbol", "close", "rsi", "PE fwd", "note"],
                                     [["Tech", ["close", "rsi"]], {"label": "Value", "columns": ["pe_fwd"]}])
        self.assertEqual(labels, ["", "Tech", "Tech", "Value", ""])
        self.assertEqual(column_group_labels(["a"], ["a"], [["X", ["b"]]]), [])
        html = _render_table({"headers": ["symbol", "close", "rsi", "PE fwd", "note"], "groups": labels,
                              "rows": [["NYSE:A", "1.00", "50", "12", "x" * 60]], "tones": []}, "t0")
        self.assertIn('<tr class="grp"><th colspan="1"></th><th colspan="2" class="g0">Tech</th>', html)
        self.assertIn('<td class="txt gs"><div class="clamp"', html)
        self.assertIn('<th data-col="1" data-group="Tech" class="gs">close</th>', html)
        self.assertIn('<table id="t0" class="grid"><thead>', html)
        self.assertIn('<tr class="hdr">', html)
        self.assertIn("pe_<wbr>fwd", _render_table({"headers": ["pe_fwd"], "rows": [], "tones": []}, "t1"))

    def test_header_tooltips_come_from_the_ledger(self):
        from pathlib import Path

        from generic_utils.canvas_page import build_page, render_html

        spec = {"name": "tips-20261028", "tabs": [{"label": "T", "blocks": [
            {"type": "table", "headers": ["symbol", "rr", "Why"], "rows": [["NYSE:A", "1.2", "x"]]}]}]}
        page = build_page(spec, base_dir=Path("."))
        self.assertIn("rr", page["tips"])
        doc = render_html(page)
        self.assertIn('<body data-page="tips">', doc)
        self.assertIn('data-col="1" title="', doc)
        self.assertIn("function moveCol", doc)


if __name__ == "__main__":
    unittest.main()
