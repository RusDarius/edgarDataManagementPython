from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


def _make_day(root: Path, day: str, runs: list[tuple[str, datetime, int]], rows_per_run: int = 50) -> Path:
    import duckdb

    folder = root / day
    (folder / "parquet").mkdir(parents=True)
    db = folder / f"tradingview_all_fields_{day}.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE run_metadata (run_id VARCHAR, created_at_utc TIMESTAMP, "
                "api_request_columns_json VARCHAR, scan_data_count BIGINT)")
    con.execute("CREATE TABLE all_fields_rows (run_id VARCHAR, row_number BIGINT, symbol VARCHAR, close DOUBLE)")
    con.execute("CREATE TABLE generated_reports (run_id VARCHAR, report_key VARCHAR)")
    for run_id, at, n_cols in runs:
        con.execute("INSERT INTO run_metadata VALUES (?, ?, ?, ?)",
                    [run_id, at, json.dumps([f"f{i}" for i in range(n_cols)]), rows_per_run])
        con.execute("INSERT INTO all_fields_rows SELECT ?, i, 'NASDAQ:T' || i, i * 1.5 FROM range(?) t(i)",
                    [run_id, rows_per_run])
        con.execute("INSERT INTO generated_reports VALUES (?, 'overview')", [run_id])
        (folder / "runs" / run_id).mkdir(parents=True)
    con.close()
    return db


# 18 Nov 2026 (EST, UTC-5): 13:22Z premarket 08:22, 14:45Z cash 09:45, 22:10Z after-hours 17:10
RUNS = [
    ("pre_1", datetime(2026, 11, 18, 12, 0), 250),
    ("pre_2", datetime(2026, 11, 18, 13, 22), 250),
    ("full_cash", datetime(2026, 11, 18, 14, 45), 3500),
    ("cash_2", datetime(2026, 11, 18, 16, 30), 250),
    ("cash_3", datetime(2026, 11, 18, 19, 0), 250),
    ("post_1", datetime(2026, 11, 18, 22, 10), 250),
]


class ChooseTests(unittest.TestCase):
    def _runs(self):
        from data_analysis_scripts.trading_view_all_fields_prune_runs import RunInfo

        return [RunInfo(r, at, n, 50) for r, at, n in RUNS]

    def test_spread_keeps_newest_full_and_premarket(self):
        from data_analysis_scripts.trading_view_all_fields_prune_runs import choose_runs

        chosen = choose_runs(self._runs(), keep=3)
        self.assertEqual(chosen, {"post_1": "newest", "full_cash": "newest_full", "pre_2": "last_pre"})

    def test_newest_strategy(self):
        from data_analysis_scripts.trading_view_all_fields_prune_runs import choose_runs

        chosen = choose_runs(self._runs(), keep=3, strategy="newest")
        self.assertEqual(set(chosen), {"post_1", "full_cash", "cash_3"})

    def test_two_full_plus_one_focused(self):
        from data_analysis_scripts.trading_view_all_fields_prune_runs import RunInfo, choose_runs

        runs = [RunInfo(r, at, 3500 if r in ("pre_1", "full_cash", "cash_2") else 250, 50) for r, at, _ in RUNS]
        chosen = choose_runs(runs, keep=3)
        # three full scans: the newest two stay, the newest (focused) run takes the third slot
        self.assertEqual(chosen, {"full_cash": "full_2", "cash_2": "newest_full", "post_1": "newest"})

    def test_newest_run_full_keeps_two_full_and_one_focused(self):
        from data_analysis_scripts.trading_view_all_fields_prune_runs import RunInfo, choose_runs

        runs = [RunInfo(r, at, 3500 if r in ("full_cash", "post_1") else 250, 50) for r, at, _ in RUNS]
        chosen = choose_runs(runs, keep=3)
        self.assertEqual(chosen, {"post_1": "newest_full", "full_cash": "full_2", "pre_2": "last_pre"})

    def test_three_focused_when_no_full(self):
        from data_analysis_scripts.trading_view_all_fields_prune_runs import RunInfo, choose_runs

        runs = [RunInfo(r, at, 250, 50) for r, at, _ in RUNS]
        self.assertEqual(choose_runs(runs, keep=3), {"post_1": "newest", "cash_3": "last_cash", "pre_2": "last_pre"})

    def test_keep_one_still_keeps_newest_full(self):
        from data_analysis_scripts.trading_view_all_fields_prune_runs import choose_runs

        self.assertEqual(set(choose_runs(self._runs(), keep=1)), {"post_1", "full_cash"})


class ApplyTests(unittest.TestCase):
    def test_dry_run_then_apply(self):
        import duckdb
        from data_analysis_scripts.trading_view_all_fields_prune_runs import PRUNE_LOG_NAME, prune_all_fields_day_runs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = _make_day(root, "18_11_2026", RUNS)
            small = _make_day(root, "17_11_2026", RUNS[:3])
            small_bytes = small.stat().st_size

            dry = prune_all_fields_day_runs(3, False, data_dir=root, verbose=False)
            self.assertEqual([r["day"] for r in dry], ["18_11_2026"])
            con = duckdb.connect(str(db), read_only=True)
            self.assertEqual(con.execute("SELECT count(*) FROM run_metadata").fetchone()[0], 6)
            con.close()

            done = prune_all_fields_day_runs(3, True, data_dir=root, verbose=False)
            self.assertEqual(done[0]["status"], "pruned")
            con = duckdb.connect(str(db), read_only=True)
            runs = {r[0] for r in con.execute("SELECT run_id FROM run_metadata").fetchall()}
            per_run = dict(con.execute("SELECT run_id, count(*) FROM all_fields_rows GROUP BY 1").fetchall())
            reports = con.execute("SELECT count(*) FROM generated_reports").fetchone()[0]
            con.close()
            self.assertEqual(runs, {"post_1", "full_cash", "pre_2"})
            self.assertEqual(per_run, {"post_1": 50, "full_cash": 50, "pre_2": 50})
            self.assertEqual(reports, 3)
            self.assertEqual(sorted(p.name for p in (db.parent / "runs").iterdir()), ["full_cash", "post_1", "pre_2"])
            self.assertTrue((db.parent / "parquet" / "all_fields_rows.parquet").exists())
            self.assertFalse(db.with_name(db.name + ".prune.tmp").exists())
            self.assertEqual(small.stat().st_size, small_bytes)
            log = (root / PRUNE_LOG_NAME).read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(log), 1)
            self.assertEqual(sorted(json.loads(log[0])["dropped"]), ["cash_2", "cash_3", "pre_1"])

    def test_estimate_and_measure_write_nothing(self):
        from data_analysis_scripts.trading_view_all_fields_prune_runs import estimate_all_fields_prune

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as scratch:
            root = Path(tmp)
            db = _make_day(root, "18_11_2026", RUNS, rows_per_run=2000)
            _make_day(root, "17_11_2026", [(r + "_x", at.replace(day=17), 250) for r, at, _ in RUNS[:4]], 2000)
            before = sorted((p.relative_to(root).as_posix(), p.stat().st_size) for p in root.rglob("*") if p.is_file())
            est = estimate_all_fields_prune(3, data_dir=root, verbose=False)
            meas = estimate_all_fields_prune(3, data_dir=root, measure=True, scratch_dir=Path(scratch), verbose=False)
            after = sorted((p.relative_to(root).as_posix(), p.stat().st_size) for p in root.rglob("*") if p.is_file())
            self.assertEqual(before, after)
            self.assertEqual(list(Path(scratch).iterdir()), [])
            self.assertEqual((est["days"], est["runs_dropped"]), (2, 4))
            day18 = next(r for r in est["per_day"] if r["day"] == "18_11_2026")
            self.assertEqual((day18["mix_before"], day18["mix_after"]), ("1F+5f", "1F+2f"))
            self.assertGreater(est["focused_bytes_per_row"]["db"], 0)
            self.assertEqual(meas["method"], "measured")
            m18 = next(r for r in meas["per_day"] if r["day"] == "18_11_2026")
            self.assertLessEqual(m18["db_after"], db.stat().st_size)

    def test_three_full_runs_within_keep_drop_one(self):
        from data_analysis_scripts.trading_view_all_fields_prune_runs import plan_day

        with tempfile.TemporaryDirectory() as tmp:
            three_full = [(r, at, 3500) for r, at, _ in RUNS[:3]]
            db = _make_day(Path(tmp), "18_11_2026", three_full)
            plan = plan_day(db, today="19_11_2026")
            self.assertEqual(plan.drop, ["pre_1"])
            self.assertEqual(sorted(plan.keep), ["full_cash", "pre_2"])

    def test_skips_today_and_locked(self):
        from data_analysis_scripts.trading_view_all_fields_prune_runs import plan_day

        with tempfile.TemporaryDirectory() as tmp:
            db = _make_day(Path(tmp), "18_11_2026", RUNS)
            self.assertIn("today", plan_day(db, today="18_11_2026").skipped)
            db.with_name(db.name + ".write.lock").write_text("{}", encoding="utf-8")
            self.assertEqual(plan_day(db, today="19_11_2026").skipped, "writer lock present")


if __name__ == "__main__":
    unittest.main()
