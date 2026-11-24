from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


def _pack(root: Path, stamp: str, names: dict[str, dict], book: list[dict]) -> Path:
    pack = root / f"briefing_pack_{stamp}_utc_test"
    pack.mkdir(parents=True)
    created = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:{stamp[11:13]}:00+00:00"
    (pack / "briefing_pack.json").write_text(json.dumps({"created_at_utc": created, "names": names, "book": book}), encoding="utf-8")
    return pack


def _store(path: Path, closes: dict[str, list[float]], first: date) -> None:
    import duckdb

    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE panel (as_of DATE, symbol VARCHAR, close DOUBLE)")
    con.execute("CREATE TABLE labels (symbol VARCHAR, as_of DATE, fwd_1d DOUBLE, fwd_7d DOUBLE, fwd_28d DOUBLE, fwd_56d DOUBLE, "
                "fwd_91d DOUBLE, fwd_182d DOUBLE)")
    for sym, series in closes.items():
        for i, c in enumerate(series):
            con.execute("INSERT INTO panel VALUES (?, ?, ?)", [first + timedelta(days=i), sym, c])
    # a flat market: the median store name moves 0 over every window
    for i in range(len(next(iter(closes.values())))):
        con.execute("INSERT INTO labels VALUES ('NYSE:MKT', ?, 0, 0, 0, 0, 0, 0)", [first + timedelta(days=i)])
    con.close()


class JournalTests(unittest.TestCase):
    def test_course_class_and_text(self):
        from generic_utils.journal import clean_text, course_class, load_recipe

        r = load_recipe()
        self.assertEqual(course_class("NEW (event, 23 Nov AMC)", r), ("buy", 1))
        self.assertEqual(course_class("HOLD_NO_ADD (trim cancelled)", r), ("hold", 1))
        self.assertEqual(course_class("SHORT_WAIT", r), ("short_watch", 0))
        self.assertEqual(course_class("TRIM (half, into a bounce)", r), ("trim", -1))
        self.assertEqual(course_class("SOLD (operator, after hours)", r), ("exit", -1))
        self.assertEqual(course_class("OUT", r), ("exit", -1))
        self.assertEqual(course_class("PASS (through kill)", r), ("pass", -1))
        self.assertEqual(course_class("QUEUE", r), ("watch", 0))
        self.assertEqual(clean_text("19 Nov: hold.  Earlier: 18 Nov: add.", 100, [" Earlier: "]), "19 Nov: hold.")

    def test_record_is_change_only_and_idempotent(self):
        from generic_utils import journal as jr

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = jr.load_recipe()
            con = jr.connect(root / "j.duckdb", recipe=r)
            names = {"NASDAQ:AAA": {"close": 100.0, "rsi": 50}, "NYSE:CCC": {"close": 20.0}}
            book = [{"symbol": "NYSE:BBB", "close": 50.0}]
            j1 = {"courses": {"NASDAQ:AAA": {"Course": "NEW", "Grade": "Med", "Reason": "cheap. Earlier: older text"},
                              "NYSE:BBB": {"Course": "HOLD", "Grade": "Low"}},
                  "capital_plan": {"capital_plan": [{"symbol": "NASDAQ:AAA", "course": "NEW", "px": "99.5", "usd": "5000"}]}}
            p1 = _pack(root, "20261102_2000", names, book)
            out = jr.record_session(con, p1, judgment=j1, recipe=r)
            self.assertEqual((out["snaps"], out["calls"]), (3, 2))
            self.assertEqual(jr.record_session(con, p1, judgment=j1, recipe=r)["calls"], 2)  # same session again: same rows
            p2 = _pack(root, "20261103_2000", names, book)
            self.assertEqual(jr.record_session(con, p2, judgment=j1, recipe=r)["calls"], 0)  # nothing changed
            j3 = json.loads(json.dumps(j1))
            j3["courses"]["NYSE:BBB"]["Course"] = "EXIT"
            p3 = _pack(root, "20261104_2000", names, book)
            self.assertEqual(jr.record_session(con, p3, judgment=j3, recipe=r)["calls"], 1)
            row = con.execute("SELECT c.px, c.plan_px, t.text FROM calls c JOIN texts t USING (text_id) "
                              "WHERE c.symbol = 'NASDAQ:AAA'").fetchone()
            self.assertAlmostEqual(row[0], 100.0)
            self.assertAlmostEqual(row[1], 99.5, places=3)
            self.assertEqual(row[2], "cheap.")
            self.assertEqual(con.execute("SELECT count(*) FROM snaps").fetchone()[0], 9)  # one row per (day, name)
            self.assertEqual(con.execute("SELECT in_book FROM snaps WHERE symbol = 'NYSE:BBB' LIMIT 1").fetchone()[0], True)
            con.close()

    def test_notes_are_deduplicated(self):
        from generic_utils import journal as jr

        with tempfile.TemporaryDirectory() as tmp:
            r = jr.load_recipe()
            con = jr.connect(Path(tmp) / "j.duckdb", recipe=r)
            note = [{"symbol": "NASDAQ:AAA", "kind": "outside", "text": "Guidance cut (OUTSIDE)", "source": "x"}]
            from datetime import datetime

            now = datetime(2026, 11, 19, 21, 0)
            self.assertEqual(jr.add_notes(con, note, day=now.date(), at=now, session_id=None, recipe=r), 1)
            self.assertEqual(jr.add_notes(con, note, day=now.date(), at=now, session_id=None, recipe=r), 0)
            con.close()

    def test_score_and_review_on_a_small_store(self):
        from generic_utils import journal as jr

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store.duckdb"
            first = date(2026, 11, 2)
            up = [100.0 * (1.01 ** i) for i in range(40)]      # +1% a day
            down = [50.0 * (0.99 ** i) for i in range(40)]     # -1% a day
            _store(store, {"NASDAQ:AAA": up, "NYSE:BBB": down}, first)
            r = jr.load_recipe()
            con = jr.connect(root / "j.duckdb", recipe=r)
            names = {"NASDAQ:AAA": {"close": 100.0}}
            book = [{"symbol": "NYSE:BBB", "close": 50.0}]
            j = {"courses": {"NASDAQ:AAA": {"Course": "NEW", "Grade": "Med"}, "NYSE:BBB": {"Course": "EXIT", "Grade": "Med"}}}
            jr.record_session(con, _pack(root, "20261102_2000", names, book), judgment=j, recipe=r)
            sc = jr.score(con, r, store=store)
            self.assertEqual(sc["scored"], 2)
            a = con.execute("SELECT ret_7d, fx_7d, sfx_7d, ret_28d, n FROM (SELECT *, 1 AS n FROM outcomes) WHERE symbol = 'NASDAQ:AAA'").fetchone()
            self.assertAlmostEqual(a[0], (1.01 ** 7 - 1) * 100, places=1)
            self.assertAlmostEqual(a[1], a[0], places=3)  # flat market median
            self.assertAlmostEqual(a[2], a[0], places=3)  # bought: sign +1
            b = con.execute("SELECT fx_7d, sfx_7d FROM outcomes WHERE symbol = 'NYSE:BBB'").fetchone()
            self.assertLess(b[0], 0)
            self.assertGreater(b[1], 0)  # sold a falling name: the exit was right
            payload = jr.review(con, r, store=store)
            cls = {row["cls"]: row for row in payload["scorecard"]}
            self.assertIn("buy", cls)
            self.assertIn("exit", cls)
            self.assertEqual(cls["all calls"]["n"], 2)
            self.assertTrue(any(s["study"] == "list" for s in payload["studies"]))
            self.assertEqual(len(payload["similar_now"]), 2)
            out = jr.write_review(payload, root / "review")
            self.assertTrue(Path(out["duckdb"]).exists())
            con.close()
            done = jr.compact(root / "j.duckdb", recipe=r)
            self.assertGreater(done["bytes_after"], 0)


if __name__ == "__main__":
    unittest.main()
