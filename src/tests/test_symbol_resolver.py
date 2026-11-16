from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


def _all_fields_db(folder: Path) -> Path:
    import duckdb

    day = folder / "04_11_2026"
    day.mkdir(parents=True)
    path = day / "tradingview_all_fields_04_11_2026.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE run_metadata (run_id VARCHAR, created_at_utc TIMESTAMP)")
    con.execute("INSERT INTO run_metadata VALUES ('old', TIMESTAMP '2026-11-04 10:00:00'), ('new', TIMESTAMP '2026-11-04 23:00:00')")
    con.execute("CREATE TABLE all_fields_rows (run_id VARCHAR, symbol VARCHAR, name VARCHAR, description VARCHAR, exchange VARCHAR, "
                "country VARCHAR, close VARCHAR, market_cap_basic VARCHAR, is_primary VARCHAR, type VARCHAR)")
    rows = [
        ("new", "NASDAQ:GOOG", "GOOG", "Alphabet Inc.", "NASDAQ", "United States", "287.3", "3.5e12", "False", "stock"),
        ("new", "NASDAQ:GOOGL", "GOOGL", "Alphabet Inc.", "NASDAQ", "United States", "288.93", "3.5e12", "True", "stock"),
        ("new", "NYSE:XYZ", "XYZ", "Block, Inc.", "NYSE", "United States", "87.63", "5e10", "True", "stock"),
        ("new", "SET:XYZ", "XYZ", "RSXYZ Public Company Limited", "SET", "Thailand", "0.25", "1e7", "True", "stock"),
        ("new", "OMXCOP:NOVO_B", "NOVO_B", "Novo Nordisk A/S", "OMXCOP", "Denmark", "285.2", "1.9e11", "True", "stock"),
        ("new", "NASDAQ:META", "META", "Meta Platforms, Inc.", "NASDAQ", "United States", "630", "1.6e12", "True", "stock"),
        ("new", "NYSE:META", "META", "Some other META", "NYSE", "United States", "1", "1e8", "True", "stock"),
        ("old", "NASDAQ:OLDONLY", "OLDONLY", "Old", "NASDAQ", "United States", "5", "1e9", "True", "stock"),
    ]
    con.executemany("INSERT INTO all_fields_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.close()
    return path


def _store(folder: Path) -> Path:
    import duckdb

    path = folder / "panel_store.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE panel (as_of DATE, symbol VARCHAR, exchange VARCHAR, close DOUBLE, mcap DOUBLE)")
    con.executemany("INSERT INTO panel VALUES (?, ?, ?, ?, ?)", [
        (date(2026, 10, 20), "NASDAQ:GONE", "NASDAQ", 12.0, 3e9),
        (date(2026, 10, 27), "NASDAQ:GONE", "NASDAQ", 11.0, 3e9),
        (date(2026, 11, 4), "NASDAQ:GOOG", "NASDAQ", 287.0, 3.5e12),
    ])
    con.close()
    return path


class AliasTests(unittest.TestCase):
    def test_chain_and_loop(self):
        from generic_utils.symbol_resolver import canonical

        table = {"NYSE:SQ": "NYSE:XYZ", "NASDAQ:A": "NASDAQ:B", "NASDAQ:B": "NASDAQ:A"}
        self.assertEqual(canonical("nyse:sq", table), "NYSE:XYZ")
        self.assertEqual(canonical("NYSE:XYZ", table), "NYSE:XYZ")
        self.assertEqual(canonical("NASDAQ:A", table), "NASDAQ:B")  # a loop stops, never spins

    def test_repo_alias_file_never_maps_a_share_class(self):
        from generic_utils.symbol_resolver import load_aliases

        aliases = load_aliases()
        self.assertEqual(aliases.get("NYSE:SQ"), "NYSE:XYZ")
        self.assertNotIn("NASDAQ:GOOG", aliases)
        self.assertNotIn("NASDAQ:GOOGL", aliases)


class ResolveTests(unittest.TestCase):
    def test_order_all_fields_then_store_then_unresolved(self):
        from generic_utils.symbol_resolver import newest_all_fields, resolve_marks

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = _all_fields_db(root / "af")
            store = _store(root)
            found_db, run = newest_all_fields(root / "af")
            self.assertEqual((found_db, run), (db, "new"))
            out = resolve_marks(["NASDAQ:GOOG", "NYSE:SQ", "NASDAQ:GONE", "NASDAQ:OLDONLY", "NOVO_B", "META", "NOPE"],
                                aliases={"NYSE:SQ": "NYSE:XYZ"}, all_fields_db=db, all_fields_run=run, store=store,
                                today=date(2026, 11, 5))
            self.assertEqual((out["NASDAQ:GOOG"]["close"], out["NASDAQ:GOOG"]["mark_source"]), (287.3, "all_fields"))  # never GOOGL's 288.93
            self.assertEqual((out["NYSE:SQ"]["symbol"], out["NYSE:SQ"]["alias_from"], out["NYSE:SQ"]["close"]), ("NYSE:XYZ", "NYSE:SQ", 87.63))
            gone = out["NASDAQ:GONE"]
            self.assertEqual((gone["mark_source"], gone["close"], gone["as_of"], gone["age_days"]), ("panel_store", 11.0, "2026-10-27", 9))
            self.assertIsNone(out["NASDAQ:OLDONLY"]["close"])  # only in an older run of the day: not used
            self.assertEqual((out["NOVO_B"]["symbol"], out["NOVO_B"]["qualified"]), ("OMXCOP:NOVO_B", "unique"))
            self.assertEqual(out["META"]["reason"], "bare_id")  # two US listings: never guessed
            self.assertIn("ambiguous", out["META"]["qualified"])
            self.assertEqual(out["NOPE"]["qualified"], "not_found")

    def test_bare_prefers_the_only_us_listing(self):
        from generic_utils.symbol_resolver import qualify_bare

        with tempfile.TemporaryDirectory() as tmp:
            db = _all_fields_db(Path(tmp))
            got = qualify_bare(["XYZ", "GOOG"], db, "new")
            self.assertEqual(got["XYZ"], ("NYSE:XYZ", "unique_us"))
            self.assertEqual(got["GOOG"], ("NASDAQ:GOOG", "unique"))


class HoldingsFallbackTests(unittest.TestCase):
    def test_held_line_missing_from_the_run_keeps_its_mark(self):
        from data_analysis_scripts.trading_view_holdings_scoring_analysis import (
            HoldingConfig,
            MovePredictionSource,
            _build_unmatched_rows,
            _resolver_fallback_rows,
            holding_config_label,
        )

        goog = HoldingConfig(ticker="GOOG", symbol="NASDAQ:GOOG", invested_sum=7000.0, average_price=309.16,
                             invested_sum_usd=7000.0, implied_shares=7000.0 / 309.16)
        lost = HoldingConfig(ticker="LOST", symbol="NASDAQ:LOST", invested_sum=1000.0, average_price=10.0)
        source = MovePredictionSource(Path("x.duckdb"), "run", None, "suite", [])
        marks = {"NASDAQ:GOOG": {"symbol": "NASDAQ:GOOG", "close": 287.3, "exchange": "NASDAQ", "country": "United States",
                                 "description": "Alphabet Inc.", "mcap": 3.5e12, "mark_source": "all_fields", "age_days": 0},
                 "NASDAQ:LOST": {"symbol": "NASDAQ:LOST", "close": None, "reason": "not_in_all_fields_or_store"}}
        labels = [holding_config_label(goog), holding_config_label(lost)]
        with mock.patch("generic_utils.symbol_resolver.resolve_marks", return_value=marks):
            rows, reasons = _resolver_fallback_rows(source, labels, [goog, lost])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["matched_symbol"], row["close_source"], row["match_note"]),
                         ("NASDAQ:GOOG", "symbol_resolver.all_fields", "not_in_source_run"))
        self.assertAlmostEqual(row["current_value_usd"], 7000.0 / 309.16 * 287.3, places=2)
        unmatched = _build_unmatched_rows(labels, [goog, lost], reasons)
        self.assertIn("marked from all_fields", unmatched[0]["reason"])
        self.assertIn("not_in_all_fields_or_store", unmatched[1]["reason"])


class BookMissingTests(unittest.TestCase):
    def test_config_line_not_on_the_book_is_listed(self):
        from operator_briefing.compile import _book_missing

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "h.json"
            cfg.write_text(json.dumps({"holdings": [{"ticker": "MU"}, {"ticker": "GOOG", "symbol": "NASDAQ:GOOG"},
                                                    {"ticker": "VUAA", "symbol": "XETR:VUAA", "instrument_type": "etf"}]}),
                           encoding="utf-8")
            self.assertEqual(_book_missing(cfg, {"NASDAQ:MU", "XETR:VUAA"}), ["NASDAQ:GOOG"])
            self.assertEqual(_book_missing(cfg, {"NASDAQ:MU", "NASDAQ:GOOG", "XETR:VUAA"}), [])


class PredictionScanUniverseTests(unittest.TestCase):
    def test_move_prediction_request_admits_us_secondary_listings(self):
        from data_loaders.api_tradingview_client import ApiTradingViewClient, GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD

        sent = {}

        def fake_post(url, headers=None, data=None, timeout=None):
            sent["payload"] = json.loads(data)
            resp = mock.Mock()
            resp.json.return_value = {"data": [], "totalCount": 0}
            resp.raise_for_status.return_value = None
            return resp

        with mock.patch("data_loaders.api_tradingview_client.requests.post", side_effect=fake_post):
            ApiTradingViewClient("test").scan_global_market_move_prediction(include_mapped_rows=False)
        payload = sent["payload"]
        self.assertFalse(any(f.get("left") == "is_primary" for f in payload["filter"]))
        gate = payload["filter2"]["operands"][-1]["operation"]
        self.assertEqual(gate["operands"][0]["expression"]["left"], "is_primary")
        self.assertTrue(any(f.get("left") == "is_primary" for f in GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD["filter"]))


if __name__ == "__main__":
    unittest.main()
