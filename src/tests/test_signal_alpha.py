import unittest


def _epoch(text):
    from datetime import datetime, timezone

    return str(int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()))


_COLS = ["run_id", "symbol", "market", "type", "exchange", "industry", "close", "change", "market_cap_basic",
         "earnings_release_date", "earnings_release_time", "eps_surprise_percent_fq", "total_revenue_fq", "revenue_forecast_fq",
         "earnings_per_share_forecast_next_fy", "Perf.1M"]


def _day_file(path, run_id, created, rows):
    import duckdb

    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE run_metadata (run_id VARCHAR, created_at_utc TIMESTAMP)")
    con.execute("INSERT INTO run_metadata VALUES (?, ?)", [run_id, created])
    con.execute(f"CREATE TABLE all_fields_rows ({', '.join(f'{qi(c)} VARCHAR' for c in _COLS)})")
    for row in rows:
        con.execute(f"INSERT INTO all_fields_rows VALUES ({', '.join('?' for _ in _COLS)})", [run_id] + [row.get(c) for c in _COLS[1:]])
    con.close()


def qi(name):
    return '"' + name.replace('"', '""') + '"'


class TestPrintPointers(unittest.TestCase):
    def test_surprise_fields_and_mix(self):
        from generic_utils.print_pointers import rev_surprise_pct, surprise_mix

        self.assertEqual(rev_surprise_pct(207.385, 213.214286), -2.73)
        self.assertEqual(rev_surprise_pct(None, 213.0, 4.0), 4.0)  # forecast side missing: TradingView's own figure
        self.assertIsNone(rev_surprise_pct(None, None))
        self.assertEqual(surprise_mix(6.5, -2.7), "B/M")
        self.assertEqual(surprise_mix(0.0, None), "B/-")
        self.assertIsNone(surprise_mix(None, None))

    def test_snapshot_pointers(self):
        from datetime import date

        from generic_utils.print_pointers import snapshot_pointers

        row = {"last_surprise": "6.52", "rev_last_q": "207385000", "rev_est_last_q": "213214286", "eps_last_q": "0.84",
               "eps_next_q": "0.90", "rev_next_q": "230000000", "earn_last": _epoch("2026-10-26T20:05:00"),
               "earn_next": _epoch("2027-02-02T21:00:00"), "earn_next_time": "1"}
        out = snapshot_pointers(row, date(2026, 10, 27))
        self.assertEqual((out["last_surprise"], out["rev_surprise"], out["surprise_mix"]), (6.52, -2.73, "B/M"))
        self.assertEqual((out["earn_last"], out["dsp"], out["earn_slot"]), ("2026-10-26", 1, "AMC"))
        self.assertAlmostEqual(out["eps_q_step"], 7.14, places=2)

    def test_summarize_prints_respects_split_scale(self):
        from generic_utils.print_pointers import summarize_prints

        prints = [{"react": -21.3, "react_close": 80.0, "split_seq": 0}, {"react": 4.0, "react_close": 100.0, "split_seq": 0}]
        out = summarize_prints(prints, 110.0, 0)
        self.assertEqual((out["n_prints"], out["react_last"], out["react_up"], out["since_print"]), (2, 4.0, "1/2", 10.0))
        self.assertEqual(out["react_abs_mean"], 12.65)
        self.assertIsNone(summarize_prints(prints, 55.0, 1)["since_print"])  # a split since the reaction: no comparison


class TestPanelStore(unittest.TestCase):
    def _build(self, tmp):
        from pathlib import Path

        days = [("20_10_2026", "2026-10-20"), ("21_10_2026", "2026-10-21"), ("22_10_2026", "2026-10-22"), ("23_10_2026", "2026-10-23")]
        closes = {"NASDAQ:AAA": [100, 110, 121, 121], "NYSE:SPL": [100, 101, 50.5, 51], "NYSE:BIG": [10, 10, 10, 10]}
        changes = {"NASDAQ:AAA": [0, 10, 10, 0], "NYSE:SPL": [0, 1, 0, 1], "NYSE:BIG": [0, 0, 0, 0]}
        for k, (label, iso) in enumerate(days):
            folder = Path(tmp) / label
            folder.mkdir()
            rows = []
            for symbol in closes:
                printed = symbol == "NASDAQ:AAA" and k >= 1  # AAA prints 2026-10-21 before the open
                rows.append({"symbol": symbol, "market": "america", "type": "stock", "exchange": symbol.split(":")[0], "industry": "Semis",
                             "close": str(closes[symbol][k]), "change": str(changes[symbol][k]), "market_cap_basic": "5e9",
                             "earnings_release_date": _epoch("2026-10-21T12:00:00") if printed else _epoch("2026-07-21T12:00:00"),
                             "earnings_release_time": "-1", "eps_surprise_percent_fq": "12" if printed else "3",
                             "total_revenue_fq": "105" if printed else "100", "revenue_forecast_fq": "100",
                             "earnings_per_share_forecast_next_fy": "5", "Perf.1M": "2"})
            rows.append({"symbol": "OTC:XYZ", "market": "america", "type": "stock", "exchange": "OTC", "close": "1", "market_cap_basic": "5e9"})
            _day_file(folder / f"tradingview_all_fields_{label}.duckdb", f"r{k}", f"{iso} 15:00:00", rows)

    def _recipe(self, tmp):
        from pathlib import Path

        from generic_utils.panel_store import load_store_recipe

        recipe = load_store_recipe()
        recipe["path"] = str(Path(tmp) / "store.duckdb")
        recipe["all_fields_root"] = str(tmp)
        recipe["prediction"]["root"] = str(Path(tmp) / "no_predictions")
        return recipe

    def test_ingest_labels_splits_prints_and_features(self):
        from datetime import date
        from tempfile import TemporaryDirectory

        import duckdb

        from generic_utils.panel_store import update_store

        with TemporaryDirectory() as tmp:
            self._build(tmp)
            recipe = self._recipe(tmp)
            first = update_store(recipe)
            again = update_store(recipe)
            con = duckdb.connect(recipe["path"], read_only=True)
            try:
                n = con.execute("SELECT count(*) FROM panel").fetchone()[0]
                labels = dict(con.execute("SELECT symbol || '@' || CAST(as_of AS VARCHAR), fwd_1d FROM labels").fetchall())
                split = con.execute("SELECT as_of FROM series WHERE split_flag = 1").fetchall()
                prints = con.execute("SELECT symbol, print_date, slot, react_file, react, eps_surprise, rev_surprise FROM prints WHERE react IS NOT NULL").fetchall()
                feats = dict(con.execute("SELECT symbol, last_surprise FROM features WHERE as_of = DATE '2026-10-22'").fetchall())
                react_last = dict(con.execute("SELECT symbol, react_last FROM features WHERE as_of = DATE '2026-10-23'").fetchall())
            finally:
                con.close()
        self.assertEqual(len(first["ingested"]), 4)
        self.assertEqual((again["ingested"], again["kept"]), ([], 4))  # incremental: nothing new
        self.assertEqual(n, 12)  # OTC row is under the exchange floor
        self.assertAlmostEqual(labels["NASDAQ:AAA@2026-10-20"], 10.0)
        self.assertIsNone(labels["NYSE:SPL@2026-10-21"])  # a split between the files leaves the label empty
        self.assertEqual(split, [(date(2026, 10, 22),)])
        self.assertEqual(prints, [("NASDAQ:AAA", date(2026, 10, 21), "BMO", date(2026, 10, 21), 10.0, 12.0, 5.0)])
        self.assertEqual(feats["NASDAQ:AAA"], 12.0)
        self.assertEqual(react_last["NASDAQ:AAA"], 10.0)
        self.assertIsNone(react_last["NYSE:BIG"])


class TestLabStatistics(unittest.TestCase):
    def test_newey_west_and_verdict(self):
        from generic_utils.signal_lab import newey_west_t, plain_t, verdict

        xs = [0.05, 0.02, 0.04, -0.01, 0.03, 0.06, 0.01, 0.02, 0.05, 0.03]
        self.assertAlmostEqual(newey_west_t(xs, 0), plain_t(xs) * (10 / 9) ** 0.5, places=6)  # lag 0: population variance
        spec = {"strong_t": 3.0, "t": 2.0, "regime_ic": 0.02}
        self.assertEqual(verdict(3.5, (0.03, 0.04), (0.03, 0.02), 0.035, spec), "STRONG")
        self.assertEqual(verdict(2.4, (0.03, 0.04), (0.03, 0.02), 0.035, spec), "CONSISTENT")
        self.assertEqual(verdict(2.4, (0.06, -0.01), (0.03, 0.02), 0.035, spec), "REGIME")
        self.assertEqual(verdict(1.0, (0.03, -0.04), (0.05, -0.04), 0.01, spec), "REGIME")  # flips with the market
        self.assertEqual(verdict(1.0, (0.03, 0.01), (0.02, 0.01), 0.02, spec), "WEAK")

    def test_after_close_is_new_york_time(self):
        from datetime import datetime

        from generic_utils.signal_lab import after_close

        self.assertTrue(after_close(datetime(2026, 10, 28, 21, 24), "2026-10-28", 30))  # 17:24 EDT
        self.assertFalse(after_close(datetime(2026, 10, 28, 20, 10), "2026-10-28", 30))  # 16:10 EDT
        self.assertFalse(after_close("2026-10-28T00:00:00", "2026-10-28", 30))  # midnight placeholder = the evening before
        self.assertTrue(after_close("2026-01-14T21:40:00", "2026-01-14", 30))  # 16:40 EST
        self.assertFalse(after_close(None, "2026-10-28", 30))

    def test_reaction_night_cells_and_tails(self):
        from generic_utils.signal_lab import cell_stats, load_lab_recipe, print_cell_studies

        events = [{"runup": -2.0, "prev_react": -3.0, "react_fx": fx, "react_gap": -6.0, "react_oc": oc, "ah_read": -8.0,
                   "drift_fx_28d": 1.0} for fx, oc in ((-9.0, 1.0), (-1.0, -2.0), (7.0, 0.5))]
        keys = {name: key for name, key, _ in print_cell_studies(events, load_lab_recipe())}
        labels = [keys["set-up x reaction"](e) for e in events]
        self.assertEqual(labels, ["sold into & previous print down | sold 5%+", "sold into & previous print down | inside the band",
                                  "sold into & previous print down | bought 5%+"])
        self.assertEqual(keys["gap x session"](events[0]), "gapped down 5%+, bought after the open")
        self.assertEqual(keys["gap x session"](events[1]), "gapped down 5%+, sold after the open")
        self.assertIsNone(keys["set-up x reaction"]({"runup": -2.0, "react_fx": 1.0}))
        rows = cell_stats([{"x": float(i)} for i in range(11)], "s", lambda e: "all", ["x"])
        self.assertEqual((rows[0]["x_q10"], rows[0]["x_q90"]), (1.0, 9.0))
        self.assertIsNone(cell_stats([{"x": 1.0}], "s", lambda e: "all", ["x"])[0]["x_q10"])

    def test_average_ranks_and_daily_ic(self):
        import numpy as np

        from generic_utils.signal_lab import avg_rank, factor_days

        self.assertEqual(avg_rank(np.array([3.0, 1.0, 3.0, 2.0])).tolist(), [3.5, 1.0, 3.5, 2.0])
        rng = np.random.default_rng(7)
        n = 400
        x = rng.normal(size=2 * n)
        noise = rng.normal(size=2 * n)
        arrays = {"days": ["2026-10-01", "2026-10-02"], "bounds": [(0, n), (n, 2 * n)], "ind": np.zeros(2 * n, dtype=int),
                  "X": np.column_stack([x, noise]), "Y": (x * 2 + 0.1 * rng.normal(size=2 * n))[:, None],
                  "features": ["signal", "noise"], "horizons": ["7d"]}
        rows = factor_days(arrays, {"study": {"min_names": 100, "quantiles": 5, "industry_min_n": 5}})
        ic = {r["feature"]: r["ic"] for r in rows if r["as_of"] == "2026-10-01"}
        self.assertGreater(ic["signal"], 0.95)
        self.assertLess(abs(ic["noise"]), 0.15)


class TestAlphaModel(unittest.TestCase):
    def _arrays(self, n_days=60, n=300, horizon_days=7, seed=3):
        import numpy as np

        rng = np.random.default_rng(seed)
        start = np.datetime64("2026-06-01")
        days = np.array([start + np.timedelta64(k, "D") for k in range(n_days)])
        bounds = [(k * n, (k + 1) * n) for k in range(n_days)]
        good = rng.normal(size=n_days * n)
        noise = rng.normal(size=n_days * n)
        y = 0.5 * good + rng.normal(size=n_days * n)
        y[-n:] = np.nan  # the live day has no label yet
        lab_max = [{"7d": days[k] + np.timedelta64(horizon_days, "D")} if k < n_days - 1 else {"7d": None} for k in range(n_days)]
        context = {"close": np.full(n_days * n, 100.0), "mcap": np.full(n_days * n, 5e9), "rvol_20": np.full(n_days * n, 2.0),
                   "atrp": np.full(n_days * n, 2.0)}
        return {"days": days, "bounds": bounds, "symbols": np.array([f"NYSE:S{i % n}" for i in range(n_days * n)]),
                "ind": np.array(["Semis"] * (n_days * n)), "X": np.column_stack([good, noise]), "features": ["good", "noise"],
                "Y": {"7d": y}, "lab_max": lab_max, "horizons": ["7d"], "files": {"7d": 5}, "context": context}

    def _recipe(self):
        return {"universe": {"min_names": 100}, "baselines": ["good"],
                "fit": {"mode": "ic", "min_coverage": 0.5, "select_t": 2.0, "lookback_days": 150, "half_life_days": 60,
                        "min_train_days": 10, "embargo_days": 0, "shrink_c": 4.0, "calib_days": 30},
                "bands": {"quantiles": [0.2, 0.5, 0.8], "deciles": 10, "vol_field": "rvol_20", "vol_fallback": "atrp", "min_obs": 50},
                "state": {"recent_days": 10, "ok_t": 2.0}, "drivers": 2}

    def test_walk_forward_uses_only_known_labels(self):
        import numpy as np

        from generic_utils.alpha_model import day_fits, walk_forward

        arrays = self._arrays()
        recipe = self._recipe()
        wf = walk_forward(arrays, day_fits(arrays, recipe), recipe)["7d"]
        first = next(k for k, p in enumerate(wf["preds"]) if p is not None)
        # day k may use day s only when s + 7 days <= day k (s <= k - 7): ten known days first happen on day 16
        self.assertEqual(first, 16)
        for rec in wf["oos"]:
            k = int(np.flatnonzero(arrays["days"] == np.datetime64(rec["as_of"]))[0])
            self.assertEqual(rec["n_train"], k - 6)
        last = wf["coef"][len(arrays["days"]) - 1]
        self.assertGreater(last[0], 0.2)  # the planted feature keeps its weight
        self.assertEqual(last[1], 0.0)  # noise stays under select_t

    def test_oos_state_calibration_and_bands(self):
        from generic_utils.alpha_model import calibrate, day_fits, live_rows, oos_summary, walk_forward

        arrays = self._arrays()
        recipe = self._recipe()
        fits = day_fits(arrays, recipe)
        wf = walk_forward(arrays, fits, recipe)
        summary, states = oos_summary(wf, arrays, recipe)
        model = next(s for s in summary if s["method"] == "model")
        self.assertGreater(model["ic_mean"], 0.3)
        self.assertEqual(states[0]["state"], "OK")
        calib = calibrate(wf, arrays, recipe)
        live = live_rows(arrays, fits, wf, calib, recipe, {})
        top = max(live, key=lambda r: r["pred_7d"])
        self.assertEqual(top["dec_7d"], 10)
        self.assertLess(top["b7d_q20"], top["b7d_q50"])
        self.assertLess(top["b7d_q50"], top["b7d_q80"])
        self.assertTrue(top["why_7d"].startswith("good"))


class TestCanvasSources(unittest.TestCase):
    def test_drop_missing_columns_keeps_older_tables_rendering(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        import duckdb

        from generic_utils.canvas_page import _source_rows

        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "p.duckdb"
            con = duckdb.connect(str(db))
            con.execute("CREATE TABLE t AS SELECT 'NYSE:A' AS symbol, 1 AS dte, 2 AS \"left\"")
            con.close()
            sql = 'SELECT symbol, dte, last_surprise, surprise_mix, "left" FROM t'
            rows = _source_rows({"duckdb": str(db), "sql": sql, "drop_missing_columns": True}, Path(tmp))
            with self.assertRaises(Exception):
                _source_rows({"duckdb": str(db), "sql": sql}, Path(tmp))
        self.assertEqual(rows, [{"symbol": "NYSE:A", "dte": 1, "left": 2}])


if __name__ == "__main__":
    unittest.main()
