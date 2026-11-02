import unittest


def _epoch(text):
    from datetime import datetime, timezone

    return str(int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()))


def _day_file(path, run_id, created, rows):
    import duckdb

    cols = ["run_id", "symbol", "description", "industry", "market", "type", "close", "change", "market_cap_basic",
            "price_target_1y", "price_target_average", "price_target_low", "price_target_high",
            "earnings_per_share_forecast_next_fy", "earnings_per_share_diluted_ttm", "price_earnings_ttm",
            "earnings_release_next_date", "earnings_release_next_time", "earnings_release_date", "earnings_release_time",
            "eps_surprise_percent_fq", "relative_volume_10d_calc"]
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE run_metadata (run_id VARCHAR, created_at_utc TIMESTAMP)")
    con.execute("INSERT INTO run_metadata VALUES (?, ?)", [run_id, created])
    con.execute(f"CREATE TABLE all_fields_rows ({', '.join(f'{c} VARCHAR' for c in cols)})")
    for row in rows:
        con.execute(f"INSERT INTO all_fields_rows VALUES ({', '.join('?' for _ in cols)})", [run_id] + [row.get(c) for c in cols[1:]])
    con.close()


class TestCalendarParts(unittest.TestCase):
    def test_epoch_slot_and_reaction_day(self):
        from datetime import date

        from generic_utils.earnings_week import epoch_dt, react_on, slot_label, week_window

        at = epoch_dt(_epoch("2026-10-30T20:05:00"))
        self.assertEqual(at.isoformat(), "2026-10-30T20:05:00+00:00")
        self.assertEqual(slot_label("1", at), "AMC")
        self.assertEqual(slot_label("-1", at), "BMO")
        self.assertEqual(slot_label("0", at), "AMC~")
        self.assertEqual(slot_label(None, epoch_dt(_epoch("2026-10-30T06:30:00"))), "BMO~")
        self.assertEqual(react_on(at, "AMC"), date(2026, 11, 2))  # Friday after the close -> Monday
        self.assertEqual(react_on(at, "BMO"), date(2026, 10, 30))
        self.assertEqual(week_window(date(2026, 10, 28)), (date(2026, 10, 26), date(2026, 11, 9)))

    def test_targets_in_quote_currency_and_forward_pe(self):
        from generic_utils.earnings_week import derive_row

        rec = derive_row({"close": 260.0, "pt_1y": 309.0, "pt_avg": 46.2, "pt_low": 30.0, "pt_high": 68.0,
                          "pe": 10.0, "eps_ttm": 6.0, "eps_nfy": 5.0})
        self.assertEqual(rec["pt"], 309.0)
        self.assertAlmostEqual(rec["pt_up"], 18.85, places=2)
        self.assertAlmostEqual(rec["pt_low"], 200.65, places=1)  # USD low scaled by 1y / average
        self.assertEqual(rec["pe_nfy"], 12.0)  # currency-free: P/E x EPS ttm / EPS next FY
        us = derive_row({"close": 100.0, "pt_1y": None, "pt_avg": 120.0, "pe": None, "pe_fwd_tv": 20.0})
        self.assertEqual((us["pt_up"], us["pe_nfy"]), (20.0, 20.0))

    def test_place_on_calendar_weeks_and_printed(self):
        from datetime import date, datetime, timezone

        from generic_utils.earnings_week import place_on_calendar

        scan = datetime(2026, 10, 26, 15, 51, tzinfo=timezone.utc)
        start, end = date(2026, 10, 26), date(2026, 11, 9)
        tonight = place_on_calendar({"earn_next": _epoch("2026-10-26T20:05:00"), "earn_next_time": "1"}, start=start, end=end, scan_at=scan)
        self.assertEqual((tonight["week"], tonight["slot"], tonight["react_on"], tonight["printed"], tonight["dte"]),
                         ("W0", "AMC", "2026-10-27", False, 0))
        morning = place_on_calendar({"earn_next": _epoch("2026-10-26T12:00:00"), "earn_next_time": "0"}, start=start, end=end, scan_at=scan)
        self.assertEqual((morning["slot"], morning["printed"]), ("BMO~", True))
        rolled = place_on_calendar({"earn_next": _epoch("2027-01-28T12:00:00"), "earn_last": _epoch("2026-10-26T11:00:00"),
                                    "earn_last_time": "-1"}, start=start, end=end, scan_at=scan)
        self.assertEqual((rolled["week"], rolled["printed"]), ("W0", True))
        self.assertEqual(place_on_calendar({"earn_next": _epoch("2026-11-04T21:00:00")}, start=start, end=end, scan_at=scan)["week"], "W1")
        self.assertIsNone(place_on_calendar({"earn_next": _epoch("2026-12-01T21:00:00")}, start=start, end=end, scan_at=scan))


class TestPrintHistory(unittest.TestCase):
    def _points(self):
        days = ["2026-07-08", "2026-07-10", "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17", "2026-07-20", "2026-07-21",
                "2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30"]
        closes = [100, 100, 99, 98, 97, 96, 95, 94, 93, 92, 90, 99, 100, 101, 102, 103]
        print_at = _epoch("2026-07-22T20:05:00")
        moved = _epoch("2026-07-21T20:05:00")  # the same print, dated a day earlier at first
        points = []
        for k, (day, close) in enumerate(zip(days, closes)):
            points.append({"as_of": day, "close": close, "change": 10.0 if day == "2026-07-23" else -1.0,
                           "earnings_release_date": moved if k < 5 else print_at, "earnings_release_time": "1",
                           "eps_surprise_percent_fq": 12.0 if day >= "2026-07-23" else 3.0,
                           "earnings_per_share_forecast_next_fy": 4.0 + k * 0.01})
        return points

    def test_reaction_runup_drift_and_summary(self):
        from datetime import date

        from generic_utils.earnings_week import history_summary, load_recipe, past_prints, print_reactions

        recipe = load_recipe()
        points = self._points()
        prints = past_prints(points, date(2026, 10, 26))
        self.assertEqual([p[0].date().isoformat() for p in prints], ["2026-07-22"])  # 07-21 merged into the later date
        rows = print_reactions(points, prints, recipe)
        self.assertEqual(rows[0]["react_file"], "2026-07-23")  # AMC -> next file
        self.assertEqual(rows[0]["react"], 10.0)
        self.assertAlmostEqual(rows[0]["react_px"], -2.17, places=2)
        self.assertAlmostEqual(rows[0]["runup"], -8.0, places=2)  # 92 vs 100 fourteen days earlier
        self.assertAlmostEqual(rows[0]["drift"], 13.33, places=2)  # 4 files after: 102 vs 90
        self.assertEqual(rows[0]["eps_surprise"], 12.0)
        summary = history_summary(points, rows, recipe)
        self.assertEqual((summary["n_prints"], summary["react_up"]), (1, "1/1"))
        self.assertEqual(summary["react_hist"], "07-22 AMC +10.0% (s +12)")
        self.assertEqual(summary["since_print"], round((103 / 90 - 1) * 100, 2))
        self.assertTrue(0 <= summary["pe_nfy_seat"] <= 100)

    def test_revenue_not_posted_yet_is_not_a_miss(self):
        from datetime import date

        from generic_utils.earnings_week import attach_revenue_posted, load_recipe, past_prints, print_reactions

        points = self._points()
        for p in points:  # forecast rolls with the print; the new actual only lands on 07-24
            p["total_revenue_fq"] = 120.0 if p["as_of"] >= "2026-07-24" else 100.0
            p["revenue_forecast_fq"] = 110.0 if p["as_of"] >= "2026-07-23" else 98.0
        rows = print_reactions(points, past_prints(points, date(2026, 10, 26)), load_recipe())
        self.assertAlmostEqual(rows[0]["rev_surprise"], 9.09, places=2)  # 120 vs 110 from 07-24, not 100 vs 110 from 07-23
        night = {"earn_last": _epoch("2026-07-22T20:05:00"), "rev_last_q": 100.0, "rev_est_last_q": 110.0, "rev_surprise": -9.09,
                 "rev_surprise_tv": None, "last_surprise": 12.0, "surprise_mix": "B/M", "rev_q_step": 30.0}
        attach_revenue_posted(night, [p for p in points if p["as_of"] <= "2026-07-22"])
        self.assertEqual((night["rev_posted"], night["rev_surprise"], night["surprise_mix"], night["rev_q_step"]), (False, None, "B/-", None))
        posted = {**night, "rev_last_q": 120.0, "rev_surprise": 9.09, "surprise_mix": "B/B", "rev_q_step": 5.0}
        attach_revenue_posted(posted, points)
        self.assertEqual((posted["rev_posted"], posted["surprise_mix"]), (True, "B/B"))


class TestJoinsAndBuild(unittest.TestCase):
    def test_csv_join_casts_numbers_and_concat_groups(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        import duckdb

        from generic_utils.earnings_week import load_join

        with TemporaryDirectory() as tmp:
            csv = Path(tmp) / "edge.csv"
            csv.write_text("symbol,score,bucket\nNASDAQ:MU,0.54,balanced\n", encoding="utf-8")
            index, _ = load_join({"csv": str(csv), "fields": {"score": "edge", "bucket": "safety"}})
            db = Path(tmp) / "m.duckdb"
            con = duckdb.connect(str(db))
            con.execute("CREATE TABLE t AS SELECT * FROM (VALUES ('NASDAQ:MU', 'w', 'leaders', 3), ('NASDAQ:MU', 'day', 'laggards', 9)) AS v(symbol, horizon, tail, rank)")
            con.close()
            tails, _ = load_join({"duckdb": str(db), "table": "t", "concat": {"dest": "tails", "format": "{horizon} {tail} #{rank}"}})
        self.assertEqual(index["NASDAQ:MU"], {"edge": 0.54, "safety": "balanced"})
        self.assertEqual(tails["NASDAQ:MU"]["tails"], "w leaders #3; day laggards #9")

    def test_build_on_two_day_files(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from generic_utils.earnings_week import build_earnings_week, load_recipe, write_earnings_week

        recipe = dict(load_recipe(), joins=[], min_market_cap_usd=1e9)
        base = {"description": "Co", "industry": "Semis", "market": "america", "type": "stock", "market_cap_basic": "5e9",
                "price_target_1y": "150", "price_target_average": "150", "earnings_per_share_forecast_next_fy": "5",
                "earnings_per_share_diluted_ttm": "4", "price_earnings_ttm": "25"}
        with TemporaryDirectory() as tmp:
            for day, close, run in (("23_10_2026", "110", "r1"), ("26_10_2026", "100", "r2")):
                folder = Path(tmp) / day
                folder.mkdir()
                _day_file(folder / f"tradingview_all_fields_{day}.duckdb", run, f"{day[6:]}-{day[3:5]}-{day[:2]} 15:00:00", [
                    dict(base, symbol="NASDAQ:AAA", close=close, change="-1", earnings_release_next_date=_epoch("2026-10-26T20:05:00"),
                         earnings_release_next_time="1"),
                    dict(base, symbol="NYSE:BBB", close=close, change="1", earnings_release_next_date=_epoch("2026-12-01T20:05:00")),
                    dict(base, symbol="NYSE:SML", close=close, market_cap_basic="5e8", earnings_release_next_date=_epoch("2026-10-27T12:00:00")),
                ])
            payload = build_earnings_week(pack=None, ids=["BBB"], recipe=recipe, history_root=tmp)
            exported = write_earnings_week(payload, Path(tmp) / "out")
            self.assertTrue(Path(exported["duckdb"]).exists())
        by_symbol = {r["symbol"]: r for r in payload["calendar"]}
        self.assertEqual(sorted(by_symbol), ["NASDAQ:AAA", "NYSE:BBB"])  # SML under the floor; BBB kept as a named id
        self.assertEqual(by_symbol["NASDAQ:AAA"]["week"], "W0")
        self.assertEqual(by_symbol["NYSE:BBB"]["week"], "OUT")
        self.assertTrue(by_symbol["NYSE:BBB"]["named"])
        self.assertEqual(by_symbol["NASDAQ:AAA"]["pt_up"], 50.0)
        self.assertEqual(by_symbol["NASDAQ:AAA"]["hist_n"], 2)
        self.assertEqual(payload["by_day"][0]["n"], 1)
        self.assertEqual(payload["spec"]["missing_ids"], [])


class TestIdsFromKeepsEveryRow(unittest.TestCase):
    def test_several_rows_per_id_in_judgment_order(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from generic_utils.canvas_page import table_rows

        with TemporaryDirectory() as tmp:
            csv = Path(tmp) / "p.csv"
            csv.write_text("symbol,print_date\nNYSE:A,07-01\nNYSE:B,07-02\nNYSE:A,04-01\n", encoding="utf-8")
            rows, _clauses, _n = table_rows({"source": {"csv": str(csv)}, "ids": ["NYSE:B", "NYSE:A"]}, Path(tmp))
        self.assertEqual([(r["symbol"], r["print_date"]) for r in rows], [("NYSE:B", "07-02"), ("NYSE:A", "07-01"), ("NYSE:A", "04-01")])


if __name__ == "__main__":
    unittest.main()
