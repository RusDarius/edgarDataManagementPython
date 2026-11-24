from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _plan(db: Path):
    from data_analysis_scripts.trading_view_all_fields_prune_runs import plan_day

    return plan_day(db, today="01_01_2000")


class ValidatorCatchesTests(unittest.TestCase):
    def setUp(self):
        from test_all_fields_prune_runs import RUNS, _make_day

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = _make_day(self.root, "18_11_2026", RUNS)
        self.other = _make_day(self.root, "17_11_2026", RUNS[:2])

    def tearDown(self):
        self._tmp.cleanup()

    def _snap_and_rewrite(self, keep_extra: str | None = None, backup: bool = False):
        from data_analysis_scripts import trading_view_all_fields_prune_validate as val
        from data_analysis_scripts.trading_view_all_fields_prune_runs import rewrite_day

        plan = _plan(self.db)
        before = val.snapshot_day(plan.database_path, plan.keep, plan.drop)
        if keep_extra:
            plan.keep = plan.keep + [keep_extra]
        rewrite_day(plan, keep_backup=backup)
        return plan, before

    def test_clean_rewrite_passes_every_check(self):
        from data_analysis_scripts import trading_view_all_fields_prune_validate as val
        from data_analysis_scripts.trading_view_all_fields_prune_runs import remove_run_folders

        plan, before = self._snap_and_rewrite()
        remove_run_folders(plan)
        findings = val.validate_day(before)
        self.assertEqual(val.failed(findings), [])
        self.assertGreater(len(findings), 15)

    def test_changed_cell_in_kept_run_is_caught(self):
        import duckdb
        from data_analysis_scripts import trading_view_all_fields_prune_validate as val

        plan, before = self._snap_and_rewrite()
        con = duckdb.connect(str(self.db))
        con.execute("UPDATE all_fields_rows SET close = close + 0.01 WHERE run_id = 'post_1' AND row_number = 7")
        con.close()
        bad = [f["check"] for f in val.failed(val.validate_day(before, check_folders=False))]
        self.assertIn("all_fields_rows: kept run post_1 identical", bad)

    def test_lost_row_in_kept_run_is_caught(self):
        import duckdb
        from data_analysis_scripts import trading_view_all_fields_prune_validate as val

        plan, before = self._snap_and_rewrite()
        con = duckdb.connect(str(self.db))
        con.execute("DELETE FROM all_fields_rows WHERE run_id = 'pre_2' AND row_number = 0")
        con.close()
        bad = [f["check"] for f in val.failed(val.validate_day(before, check_folders=False))]
        self.assertIn("all_fields_rows: kept run pre_2 identical", bad)
        self.assertIn("parquet: all_fields_rows matches the DuckDB rows", bad)

    def test_dropped_run_left_behind_is_caught(self):
        from data_analysis_scripts import trading_view_all_fields_prune_validate as val

        plan, before = self._snap_and_rewrite(keep_extra="cash_2")
        bad = [f["check"] for f in val.failed(val.validate_day(before, check_folders=False))]
        self.assertIn("run_metadata holds exactly the kept runs", bad)
        self.assertIn("all_fields_rows: no dropped run left", bad)

    def test_change_to_another_day_is_caught(self):
        import duckdb
        from data_analysis_scripts import trading_view_all_fields_prune_validate as val

        tree = val.snapshot_tree(self.root, ["18_11_2026"])
        con = duckdb.connect(str(self.other))
        con.execute("DELETE FROM all_fields_rows WHERE row_number = 0")
        con.execute("CHECKPOINT")
        con.close()
        bad = [f["check"] for f in val.failed(val.validate_tree(self.root, tree, ["18_11_2026"]))]
        self.assertTrue(any(b.startswith("other files: none changed") for b in bad))

    def test_failed_check_rolls_back_from_backup(self):
        import duckdb
        from data_analysis_scripts import trading_view_all_fields_prune_validate as val
        from data_analysis_scripts.trading_view_all_fields_prune_runs import apply_plan

        plan = _plan(self.db)
        with mock.patch.object(val, "validate_day", return_value=[{"check": "forced", "ok": False, "detail": ""}]):
            result = apply_plan(plan, keep_backup=True, data_dir=self.root)
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("restored", result["rollback"])
        con = duckdb.connect(str(self.db), read_only=True)
        self.assertEqual(con.execute("SELECT count(*) FROM run_metadata").fetchone()[0], 6)
        con.close()
        # run folders are only removed after the checks pass
        self.assertEqual(len(list((self.db.parent / "runs").iterdir())), 6)

    def test_manifest_revalidates_later(self):
        from data_analysis_scripts import trading_view_all_fields_prune_validate as val
        from data_analysis_scripts.trading_view_all_fields_prune_runs import prune_all_fields_day_runs

        out = prune_all_fields_day_runs(3, True, data_dir=self.root, verbose=False)
        self.assertEqual([r["status"] for r in out], ["pruned", "checked"])
        self.assertEqual(out[-1]["failed"], [])
        manifest = next((self.root / val.MANIFEST_DIR_NAME).iterdir())
        again = val.validate_manifest(manifest, verbose=False)
        self.assertEqual([r["failed"] for r in again], [[], []])


if __name__ == "__main__":
    unittest.main()
