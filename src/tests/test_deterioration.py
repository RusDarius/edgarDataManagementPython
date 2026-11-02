from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


def _recipe(**over):
    from generic_utils.deterioration import load_recipe

    recipe = load_recipe()
    recipe.update(over)
    return recipe


class DeteriorationStateTests(unittest.TestCase):
    def test_state_columns_label_drawdown_letters_and_count(self):
        import duckdb

        from generic_utils.deterioration import state_columns

        con = duckdb.connect()
        con.execute("""CREATE TABLE t AS SELECT * FROM (VALUES
            ('A', -5.0, 2.0, 1.0, 10.0, 3.0, 1.0, 5.0, 20.0, 0.5, 3.0),
            ('B', -40.0, -5.0, -2.0, -1.0, -4.0, -8.0, -10.0, 150.0, -0.2, -5.0),
            ('C', -60.0, NULL, 1.0, 5.0, 2.0, 1.0, 1.0, -20.0, 0.1, 0.0)
        ) v(symbol, hi_prox, eps_rev_3m, rev_rev_3m, fcf_margin, last_surprise, vs200, r_1m, r_12m, mkt_regime, pt_rev_1m)""")
        cols = state_columns(_recipe())
        rows = con.execute(f"SELECT symbol, {', '.join(f'{sql} AS {a}' for a, sql in cols)} FROM t ORDER BY symbol").fetchall()
        names = ["symbol"] + [a for a, _ in cols]
        a, b, c = (dict(zip(names, r)) for r in rows)
        self.assertEqual((a["dd"], a["det"], a["n_det"], a["det_E"]), ("dd 0-10", "-", "0", "E off"))
        self.assertEqual((b["dd"], b["det"], b["n_det"], b["det_C"]), ("dd 35-50", "ERCS", "3+", "C on"))
        self.assertEqual((b["run_12m"], b["mkt"], b["trend"], b["street"], b["dir_1m"]), ("12M 100+", "mkt down", "under 200d", "targets cut", "1M down"))
        # count_needs_all: an unknown letter leaves det / n_det empty, single letters still read
        self.assertEqual((c["dd"], c["det"], c["n_det"], c["det_E"], c["det_R"]), ("dd 50+", None, None, None, "R off"))

    def test_seen_letters_marks_unknown_fields(self):
        from generic_utils.deterioration import seen_letters

        letters = _recipe()["letters"]
        self.assertEqual(seen_letters({"eps_rev_3m": -22, "rev_rev_3m": 0.1, "fcf_margin": None, "last_surprise": -3}, letters), "ES?")
        self.assertEqual(seen_letters({"eps_rev_3m": 1, "rev_rev_3m": 1, "fcf_margin": 4, "last_surprise": 2}, letters), "-")

    def test_derive_live_levels_and_quarter_changes(self):
        from generic_utils.deterioration import derive_live

        rec = derive_live({"close": 100.0, "hi52": 200.0, "lo52": 80.0, "sma50": 110.0, "sma200": 150.0,
                           "opm": 20.0, "opm_q_ago": 25.0, "rev_yoy": 10.0, "rev_yoy_q_ago": None, "fcf_margin": 5.0, "fcf_margin_q_ago": 4.0})
        self.assertEqual((rec["half_px"], rec["to_half_px_pct"], rec["to_sma50_pct"], rec["to_lo52_pct"]), (150.0, 50.0, 10.0, -20.0))
        self.assertEqual((rec["opm_chg_q"], rec["rev_yoy_chg_q"], rec["fcf_margin_chg_q"]), (-5.0, None, 1.0))


class DeteriorationCellTests(unittest.TestCase):
    def _frame(self):
        import duckdb

        con = duckdb.connect()
        rows = []
        start = date(2026, 6, 1)
        for d in range(12):
            day = start + timedelta(days=d)
            for i in range(10):
                clean = i < 5
                rows.append((day, f"X:{i}", "dd 35-50", "0" if clean else "3+", 4.0 if clean else -6.0, 4.0 if clean else -12.0, 30.0))
        con.execute("CREATE TABLE det_state (as_of DATE, symbol VARCHAR, dd VARCHAR, n_det VARCHAR, fx_28d DOUBLE, fwd_28d DOUBLE, half_need DOUBLE)")
        con.executemany("INSERT INTO det_state VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        days = [r[0] for r in con.execute("SELECT DISTINCT as_of FROM det_state ORDER BY 1").fetchall()]
        return con, {d: i for i, d in enumerate(days)}

    def test_study_cells_measures_and_floor(self):
        from generic_utils.deterioration import study_cells

        con, index = self._frame()
        cells = study_cells(con, {"study": "drawdown x deterioration count", "keys": ["dd", "n_det"]}, ["28d"], {"28d": 4},
                            {"dn_pct": 10, "up_pct": 10}, index, min_rows=10)
        by = {c["cell"]: c for c in cells}
        self.assertEqual(sorted(by), ["dd 35-50 | 0", "dd 35-50 | 3+"])
        clean, bad = by["dd 35-50 | 0"], by["dd 35-50 | 3+"]
        self.assertEqual((clean["n"], clean["days"], clean["fx_28d_mean"], clean["fx_28d_hit"]), (60, 12, 4.0, 1.0))
        self.assertEqual((bad["p_dn_28d"], bad["p_up_28d"], clean["p_half_28d"]), (1.0, 0.0, 0.0))
        self.assertEqual(clean["fx_28d_n_indep"], 3)  # days 0, 4, 8 of 12 with a stride of 4 files
        self.assertEqual(study_cells(con, {"study": "s", "keys": ["dd"]}, ["28d"], {"28d": 4}, {}, index, min_rows=500), [])

    def test_live_cells_and_dollars(self):
        from generic_utils.deterioration import attach_dollars, attach_live_cells

        recipe = _recipe(live_cells=[{"col": "cnt", "study": "drawdown x deterioration count"}], live_measures=["fx_28d_mean", "fx_28d_q10"],
                         dollar_cell="cnt", dollar_horizons=["28d"])
        cells = [{"study": "drawdown x deterioration count", "cell": "dd 50+ | 0", "fx_28d_mean": 6.5, "fx_28d_q10": -13.2}]
        rows = [{"symbol": "NYSE:A", "dd": "dd 50+", "n_det": "0", "book_usd": 10000.0}, {"symbol": "NYSE:B", "dd": "dd 50+", "n_det": None}]
        attach_live_cells(rows, cells, recipe)
        attach_dollars(rows, recipe)
        self.assertEqual((rows[0]["cnt_cell"], rows[0]["cnt_fx_28d_mean"], rows[0]["carry_28d_usd"], rows[0]["tail_28d_usd"]), ("dd 50+ | 0", 6.5, 650.0, -1320.0))
        self.assertEqual((rows[1]["cnt_cell"], rows[1]["carry_28d_usd"]), (None, None))


class DeteriorationBuildTests(unittest.TestCase):
    def test_build_on_a_small_store(self):
        import duckdb

        from generic_utils.deterioration import build_deterioration

        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store.duckdb"
            con = duckdb.connect(str(store))
            con.execute("""CREATE TABLE features (symbol VARCHAR, as_of DATE, ind VARCHAR, mcap DOUBLE, r_1d DOUBLE, r_1m DOUBLE, r_12m DOUBLE,
                           hi_prox DOUBLE, vs200 DOUBLE, eps_rev_3m DOUBLE, rev_rev_3m DOUBLE, pt_rev_1m DOUBLE, fcf_margin DOUBLE, last_surprise DOUBLE)""")
            con.execute("""CREATE TABLE labels (symbol VARCHAR, as_of DATE, fwd_7d DOUBLE, lab_7d DATE, fwd_28d DOUBLE, lab_28d DATE, fwd_56d DOUBLE, lab_56d DATE)""")
            con.execute("""CREATE TABLE panel (symbol VARCHAR, as_of DATE, close DOUBLE, hi52 DOUBLE, lo52 DOUBLE, sma50 DOUBLE, sma200 DOUBLE, change DOUBLE,
                           opm DOUBLE, rev_yoy DOUBLE, fcf_margin DOUBLE)""")
            start = date(2026, 3, 1)
            for d in range(120):
                day = start + timedelta(days=d)
                for i in range(8):
                    sym = f"NYSE:S{i}"
                    clean = i % 2 == 0
                    con.execute("INSERT INTO features VALUES (?, ?, 'Ind', 5e9, 0.1, 1.0, 20.0, -40.0, -5.0, ?, 1.0, 0.0, ?, 2.0)",
                                [sym, day, 3.0 if clean else -5.0, 10.0 if clean else -2.0])
                    fwd = 5.0 if clean else -5.0
                    con.execute("INSERT INTO labels VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [sym, day, fwd, day, fwd, day, fwd, day] if d < 100 else [sym, day, None, None, None, None, None, None])
                    con.execute("INSERT INTO panel VALUES (?, ?, 60.0, 100.0, 50.0, 62.0, 70.0, 0.5, ?, 10.0, 5.0)", [sym, day, 20.0 + d / 10])
            con.close()
            recipe = _recipe(min_cell_rows=10, live_fields=["ind", "mcap", "hi_prox", "r_1m", "eps_rev_3m", "fcf_margin"], joins=[])
            recipe["study"] = {"min_market_cap_usd": 1e9, "winsor_pct": 0.0}
            payload = build_deterioration(None, recipe=recipe, ids=["S0", "NYSE:ZZ"], store=store)
        cells = {(c["study"], c["cell"]): c for c in payload["cells"]}
        clean, bad = cells[("drawdown x deterioration count", "dd 35-50 | 0")], cells[("drawdown x deterioration count", "dd 35-50 | 2")]
        self.assertGreater(clean["fx_28d_mean"], 0)
        self.assertLess(bad["fx_28d_mean"], 0)
        names = {r["symbol"]: r for r in payload["names"]}
        self.assertEqual((names["NYSE:S0"]["named"], names["NYSE:S0"]["cnt_cell"], names["NYSE:S0"]["det_seen"]), (True, "dd 35-50 | 0", "-"))
        self.assertEqual(names["NYSE:S1"]["det"], "EC")
        self.assertFalse(names["NYSE:ZZ"]["in_store"])
        self.assertEqual(payload["spec"]["missing_ids"], ["NYSE:ZZ"])
        self.assertEqual(names["NYSE:S0"]["opm_chg_q"], 9.1)  # 20 + 119/10 now vs 20 + 28/10 on the day 91 days earlier


if __name__ == "__main__":
    unittest.main()
