import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

TEMPLATE = {
    "name": "unit-{DAY}", "page_key": "unit", "title": "Unit {DAY}",
    "tabs": [{"label": "Plan", "blocks": [
        {"type": "callout", "from": "update.banner"},
        {"type": "table", "title": "T", "source": {"csv": "{PACK}/t.csv"}, "extra_from": "courses", "extra_columns": ["Course"]},
    ]}],
}


class TestRunFolders(unittest.TestCase):
    def test_run_dir_name_collision_and_record(self):
        from generic_utils import canvas_runs as cr

        now = datetime(2026, 10, 23, 9, 5, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TV_CANVAS_ROOT": tmp}):
            first = cr.new_run_dir("20261023", "full", now=now)
            second = cr.new_run_dir("20261023", "full", now=now)
            self.assertEqual(first, Path(tmp) / "20261023" / "0905_full")
            self.assertEqual(second.name, "0905_full_2")
            self.assertTrue(cr.is_run_dir(second))
            self.assertFalse(cr.is_run_dir(Path(tmp) / "20261023"))
            cr.write_run_record(first, pack="P1", n_review=3)
            cr.write_run_record(first, keep_existing=True, pack="P2", n_review=0, refresh_json="r.json")
            record = json.loads((first / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((record["mode"], record["pack"], record["n_review"], record["refresh_json"]), ("full", "P1", 3, "r.json"))

    def test_template_render_goes_to_the_run_with_a_judgment_snapshot_and_index(self):
        from generic_utils import canvas_runs as cr
        from generic_utils.canvas_page import write_template_page

        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TV_CANVAS_ROOT": tmp}):
            os.environ.pop(cr.RUN_ENV, None)
            tpl = Path(tmp) / "unit.json"
            tpl.write_text(json.dumps(TEMPLATE), encoding="utf-8")
            jpath = cr.judgment_path("20261023")
            jpath.parent.mkdir(parents=True)
            jpath.write_text(json.dumps({"pack": "P", "courses": {"NYSE:A": {"Course": "NEW"}}}), encoding="utf-8")
            run = cr.new_run_dir("20261023", "full")
            with mock.patch.dict(os.environ, {cr.RUN_ENV: str(run)}):
                payload = write_template_page(tpl, pack=tmp, day="20261023", judgment_path=jpath)
            loose = write_template_page(tpl, pack=tmp, day="20261023")  # no run open: the day folder
            index = Path(payload["index"]).read_text(encoding="utf-8")
            self.assertEqual(Path(payload["html"]).parent, run)
            self.assertEqual(json.loads((run / "judgment.json").read_text(encoding="utf-8"))["pack"], "P")
            self.assertEqual(Path(loose["html"]).parent, Path(tmp) / "20261023")
            self.assertEqual(Path(payload["index"]), Path(tmp) / "index.html")
        self.assertIn(f"{run.name}/unit-20261023.html", index.replace("\\", "/"))
        self.assertIn("unit-20261023-manual", index)
        self.assertIn("23 Oct 2026", index)


class TestUpdateWritesIntoTheRun(unittest.TestCase):
    def _day(self, tmp: str, *, pack_name: str) -> tuple[Path, Path, Path]:
        from generic_utils import canvas_runs as cr

        packs = Path(tmp) / "packs"
        for name in ("briefing_pack_old", "briefing_pack_new"):
            (packs / name).mkdir(parents=True, exist_ok=True)
            (packs / name / "briefing_pack.json").write_text("{}", encoding="utf-8")
        tpl = Path(tmp) / "unit.json"
        tpl.write_text(json.dumps(TEMPLATE), encoding="utf-8")
        jpath = cr.judgment_path("20990101")
        jpath.parent.mkdir(parents=True, exist_ok=True)
        jpath.write_text(json.dumps({"pack": (packs / pack_name).as_posix(), "courses": {"NYSE:A": {"Course": "WAIT"}}}), encoding="utf-8")
        return packs, tpl, jpath

    def test_new_pack_writes_report_and_prev_into_the_run(self):
        from generic_utils import canvas_runs as cr
        from generic_utils.page_update import run_page_update

        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TV_CANVAS_ROOT": tmp}):
            packs, tpl, jpath = self._day(tmp, pack_name="briefing_pack_old")
            run = cr.new_run_dir("20990101", "update")
            out = run_page_update("20990101", new_pack=(packs / "briefing_pack_new").as_posix(), templates=[str(tpl)],
                                  run_dir=run, log_dir=Path(tmp) / "replay")
            live = json.loads(jpath.read_text(encoding="utf-8"))
            prev = json.loads((run / "judgment.prev.json").read_text(encoding="utf-8"))
            self.assertTrue((run / "update.md").exists() and (run / "update.json").exists())
            self.assertTrue((run / "unit-20990101.html").exists())
            self.assertFalse(list(Path(tmp, "20990101").glob("*.update.md")))
        self.assertEqual(out["run_dir"], run.as_posix())
        self.assertEqual(Path(prev["pack"]).name, "briefing_pack_old")
        self.assertEqual(live["pack"], "briefing_pack_new")
        self.assertIn(f"{run.name}/update.md", live["update"]["banner"]["text"])

    def test_same_pack_still_replays_capital_plan_but_keeps_the_judgment(self):
        from generic_utils import canvas_runs as cr
        from generic_utils import page_update

        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TV_CANVAS_ROOT": tmp}):
            packs, tpl, jpath = self._day(tmp, pack_name="briefing_pack_new")
            before = jpath.read_text(encoding="utf-8")
            run = cr.new_run_dir("20990101", "full")
            with mock.patch.object(page_update, "replay_inputs", return_value=[{"name": "capital_plan", "rc": 0}]) as replay:
                out = page_update.run_page_update("20990101", new_pack=(packs / "briefing_pack_new").as_posix(),
                                                  templates=[str(tpl)], run_dir=run, log_dir=Path(tmp) / "replay")
            self.assertEqual(jpath.read_text(encoding="utf-8"), before)
            self.assertFalse((run / "update.md").exists())
        replay.assert_called_once()
        self.assertEqual(out["replayed"], [{"name": "capital_plan", "rc": 0}])


class TestOperatorDayRunFolder(unittest.TestCase):
    def test_mode_auto_follows_the_day_judgment(self):
        import run_operator_day as rod
        from generic_utils import canvas_runs as cr

        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TV_CANVAS_ROOT": tmp}):
            self.assertEqual(rod.pick_mode("20261022", "auto"), "full")
            cr.judgment_path("20261022").parent.mkdir(parents=True)
            cr.judgment_path("20261022").write_text("{}", encoding="utf-8")
            self.assertEqual(rod.pick_mode("20261022", "auto"), "update")
            self.assertEqual(rod.pick_mode("20261022", "full"), "full")

    def test_prompts_name_the_run_folder_and_live_judgment(self):
        import run_operator_day as rod

        run = rod.REPO_ROOT / "logs" / "canvases" / "20261022" / "0905_update"
        for mode in ("full", "update"):
            text = rod.build_agent_prompt("20261022", mode, pack="P", refresh_json=None, run_dir=run)
            self.assertIn("logs/canvases/20261022/0905_update", text)
            self.assertNotIn("{RUN_DIR}", text)
            self.assertNotIn("{JUDGMENT}", text)
            self.assertIn("--out-dir logs/canvases/20261022/0905_update", text)


if __name__ == "__main__":
    unittest.main()
