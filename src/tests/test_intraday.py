import unittest


class TestIntradayDrift(unittest.TestCase):
    def test_session_path_labels(self):
        from generic_utils.intraday import session_path

        self.assertEqual(session_path(2.7, 4.9), "EXTENDING")
        self.assertEqual(session_path(-5.8, 0.85), "FADING")
        self.assertEqual(session_path(-1.8, 3.1), "REVERSING_UP")
        self.assertEqual(session_path(1.0, -2.0), "REVERSING_DOWN")
        self.assertEqual(session_path(1.0, 0.1), "FLAT")

    def test_run_drift_joins_two_runs_and_rolls_up_by_industry(self):
        import duckdb
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from generic_utils.intraday import drift_by_group, list_runs, run_drift

        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "day.duckdb"
            con = duckdb.connect(str(db))
            con.execute("CREATE TABLE run_metadata (run_id VARCHAR, created_at_utc TIMESTAMP)")
            con.execute("INSERT INTO run_metadata VALUES ('a', '2026-10-26 14:06'), ('b', '2026-10-26 15:51')")
            con.execute("CREATE TABLE all_fields_rows (run_id VARCHAR, symbol VARCHAR, description VARCHAR, industry VARCHAR, sector VARCHAR, "
                        "close VARCHAR, change VARCHAR, relative_volume_10d_calc VARCHAR, market_cap_basic VARCHAR, volume VARCHAR)")
            con.execute("INSERT INTO all_fields_rows VALUES "
                        "('a','NASDAQ:VICR','Vicor','Electronic Components','Tech','180','2.7','0.1','8e9','1'),"
                        "('b','NASDAQ:VICR','Vicor','Electronic Components','Tech','189','7.7','0.2','8e9','2'),"
                        "('a','NYSE:RDDT','Reddit','Internet','Tech','135','-5.8','0.5','25e9','1'),"
                        "('b','NYSE:RDDT','Reddit','Internet','Tech','136','-5.0','0.9','25e9','2'),"
                        "('a','NYSE:TINY','Tiny','Internet','Tech','5','1','1','1e8','1')")
            con.close()
            self.assertEqual([r["run_id"] for r in list_runs(db)], ["a", "b"])
            rows = run_drift(db, run_a="a", run_b="b", where_sql="market_cap_basic >= ?", params=[1e9])
        by = {r["symbol"]: r for r in rows}
        self.assertEqual(set(by), {"NASDAQ:VICR", "NYSE:RDDT"})  # TINY under the floor; only names in both runs
        self.assertEqual(by["NASDAQ:VICR"]["drift_pct"], 5.0)
        self.assertEqual(by["NASDAQ:VICR"]["session_path"], "EXTENDING")
        self.assertEqual(by["NYSE:RDDT"]["session_path"], "FADING")
        groups = drift_by_group(rows)
        self.assertEqual(groups[0]["ind"], "Electronic Components")
        self.assertEqual(groups[0]["drift_up_pct"], 100.0)


class TestTableIdsFrom(unittest.TestCase):
    def test_judgment_list_picks_and_orders_rows(self):
        import csv
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from generic_utils.canvas_page import build_page, render_template

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mesh.csv"
            with path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["symbol", "rr"])
                w.writerows([["NYSE:A", "3"], ["NYSE:B", "2"], ["NYSE:C", "1"]])
            template = {"name": "t-{DAY}", "page_key": "p", "tabs": [{"label": "T", "blocks": [
                {"type": "table", "title": "Best", "source": {"csv": str(path)}, "sort": "-rr", "limit": 2, "ids_from": "pages.p.best"}]}]}
            picked = render_template(template, pack=tmp, day="20261026", judgment={"courses": {}, "pages": {"p": {"best": ["NYSE:C", "NYSE:A"]}}})
            fallback = render_template(template, pack=tmp, day="20261026", judgment={"courses": {}, "pages": {"p": {}}})
            rows_picked = build_page(picked, base_dir=Path(tmp))["tabs"][0]["blocks"][0]["rows"]
            rows_fallback = build_page(fallback, base_dir=Path(tmp))["tabs"][0]["blocks"][0]["rows"]
        self.assertEqual([r[0] for r in rows_picked], ["NYSE:C", "NYSE:A"])
        self.assertEqual([r[0] for r in rows_fallback], ["NYSE:A", "NYSE:B"])  # no list: sort + limit stand


if __name__ == "__main__":
    unittest.main()
