from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path


def _edge(root: Path, name: str, scan_day: str, mtime: float) -> Path:
    run = root / name
    (run / "aggregate").mkdir(parents=True)
    (run / "parent_run_manifest.json").write_text(json.dumps({"scan_day": scan_day}), encoding="utf-8")
    (run / "aggregate" / "x.csv").write_text("a,b\n1,2\n" * 50, encoding="utf-8")
    for p in (run / "aggregate" / "x.csv", run / "parent_run_manifest.json", run / "aggregate", run):
        os.utime(p, (mtime, mtime))
    return run


def _mtp(root: Path, name: str, scan_day: str, created: str) -> Path:
    run = root / name
    run.mkdir(parents=True)
    (run / "market_timing_policy_manifest.json").write_text(
        json.dumps({"scan_day": scan_day, "created_at_utc": created}), encoding="utf-8")
    (run / "action_policy_recommendations.csv").write_text("s\n" * 20, encoding="utf-8")
    return run


def _fin(root: Path, day: str, name: str) -> Path:
    run = root / day / name
    run.mkdir(parents=True)
    (run / f"{name}.duckdb").write_bytes(b"x" * 100)
    meta = {"run_id": name, "day_label": day, "created_at_utc": datetime.strptime(name.split("_")[1], "%Y%m%dT%H%M%SZ").isoformat()}
    if not name.startswith("finvalue_"):
        (run / "run_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return run


class FlowPruneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self._tmp.name)
        self.roots = {"edge": self.logs / "edge", "mtp": self.logs / "mtp", "finproj": self.logs / "fp"}
        base = datetime(2026, 11, 19, 12).timestamp()
        for i, hhmm in enumerate(["1300", "1400", "1500", "1600", "1700"]):
            _edge(self.roots["edge"], f"edge_latest_500m_full_parent_20261119_{hhmm}_utc_{i:08x}", "2026-11-19", base + i * 600)
            _mtp(self.roots["mtp"], f"market_timing_policy_20261119_{hhmm}_utc_{i:08x}", "2026-11-19",
                 f"2026-11-19T{hhmm[:2]}:{hhmm[2:]}:00+00:00")
        # an older-named run touched last: the backtest and latest-mtime readers pick it
        _edge(self.roots["edge"], "edge_latest_500m_full_parent_20261119_1200_utc_000000ff", "2026-11-19", base + 9000)
        _edge(self.roots["edge"], "edge_latest_500m_full_parent_20261118_1200_utc_000000aa", "2026-11-18", base - 90000)
        for name in ["fingrowth_20261102T155616Z", "fingrowth_20261102T174029Z", "finvalue_20261102T174036Z",
                     "fingrowth_20261102T174051Z", "finvalue_20261102T174058Z"]:
            _fin(self.roots["finproj"], "02_11_2026", name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, apply: bool, **kw):
        from data_analysis_scripts.flow_runs_prune import prune_flow_runs

        pack = self.logs / "pack.json"
        pack.write_text(json.dumps({"sources": {"mtp_dir": str(self.roots["mtp"] / "market_timing_policy_20261119_1300_utc_00000000")}}),
                        encoding="utf-8")
        return prune_flow_runs(3, apply, roots=self.roots, logs_dir=self.logs, pack_json=pack,
                               today=date(2026, 11, 23), verbose=False, recent_seconds=0, **kw)

    def test_plan_keeps_reader_picks_and_kinds(self):
        rep = self._run(False)
        edge = rep["flows"]["edge"]["per_day"][0]
        self.assertEqual(edge["keep"]["edge_latest_500m_full_parent_20261119_1200_utc_000000ff"], "latest_mtime")
        self.assertEqual(len(edge["keep"]), 3)
        mtp = rep["flows"]["mtp"]["per_day"][0]
        self.assertEqual(mtp["keep"]["market_timing_policy_20261119_1300_utc_00000000"], "briefing_pack")
        self.assertIn("market_timing_policy_20261119_1700_utc_00000004", mtp["keep"])
        fin = rep["flows"]["finproj"]["per_day"][0]
        kinds = {k.split("_")[0] for k in fin["keep"]}
        self.assertEqual(kinds, {"fingrowth", "finvalue"})
        self.assertEqual(sorted(fin["drop"]), ["fingrowth_20261102T155616Z", "fingrowth_20261102T174029Z"])
        # dry run removed nothing
        self.assertEqual(len(list(self.roots["edge"].iterdir())), 7)

    def test_apply_validates_and_logs(self):
        from data_analysis_scripts.flow_runs_prune import LOG_NAME

        dry = self._run(False)
        rep = self._run(True)
        for flow in ("edge", "mtp", "finproj"):
            self.assertEqual(rep["flows"][flow]["failed"], [], flow)
            self.assertEqual(rep["flows"][flow]["bytes_dropped"], dry["flows"][flow]["bytes_dropped"])
        self.assertEqual(len(list(self.roots["edge"].iterdir())), 4)  # 3 for 19 Nov + 18 Nov
        self.assertEqual(len(list((self.roots["finproj"] / "02_11_2026").iterdir())), 3)
        self.assertEqual(len((self.logs / LOG_NAME).read_text(encoding="utf-8").splitlines()), 3)

    def test_keep_backup_then_drop(self):
        from data_analysis_scripts.flow_runs_prune import BACKUP_DIR_NAME, drop_backups

        rep = self._run(True, keep_backup=True)
        self.assertEqual(rep["flows"]["mtp"]["failed"], [])
        self.assertTrue((self.roots["mtp"] / BACKUP_DIR_NAME).is_dir())
        self.assertGreater(drop_backups(self.roots, printer=lambda s: None), 0)
        self.assertFalse((self.roots["mtp"] / BACKUP_DIR_NAME).exists())

    def test_validator_catches_changed_kept_file(self):
        from data_analysis_scripts import flow_runs_prune as fp

        root = self.roots["mtp"]
        groups, _, _ = fp.plan_flow("mtp", root, 3, today=date(2026, 11, 23), pack_sources=[], recent_seconds=0)
        drops = [r for g in groups for r in g.drop]
        runs = [r for g in groups for r in g.runs]
        before = {"flow": "mtp", "dropped": [r.run_id for r in drops],
                  "kept": [r.run_id for r in runs if r not in drops],
                  "picks": fp.reader_picks("mtp", root, runs),
                  "files": fp.snapshot_files(root, {r.path for r in drops})}
        import shutil

        for r in drops[:-1]:
            shutil.rmtree(r.path)
        kept = root / before["kept"][0] / "action_policy_recommendations.csv"
        kept.write_text("changed\n", encoding="utf-8")
        bad = {f["check"] for f in fp.failed(fp.validate_flow(before, root))}
        self.assertIn("dropped runs are gone", bad)
        self.assertIn("kept files unchanged (size, mtime)", bad)


if __name__ == "__main__":
    unittest.main()
