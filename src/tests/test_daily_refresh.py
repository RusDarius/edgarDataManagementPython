import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class TestTaskDag(unittest.TestCase):
    def test_dependencies_order_failures_skip_and_optional(self):
        from generic_utils.task_dag import run_dag

        with TemporaryDirectory() as tmp:
            marker = Path(tmp) / "order.txt"
            write = "import sys; open(sys.argv[1], 'a').write(sys.argv[2] + '\\n')"
            steps = [
                {"name": "a", "after": [], "argv": ["python", "-c", write, str(marker), "a"]},
                {"name": "b", "after": ["a"], "argv": ["python", "-c", write, str(marker), "b"]},
                {"name": "bad", "after": [], "argv": ["python", "-c", "raise SystemExit(3)"]},
                {"name": "after_bad", "after": ["bad"], "argv": ["python", "-c", "pass"]},
                {"name": "soft", "after": [], "optional": True, "argv": ["python", "-c", "raise SystemExit(1)"]},
                {"name": "after_soft", "after": ["soft"], "argv": ["python", "-c", "pass"]},
            ]
            results = {r["name"]: r for r in run_dag(steps, log_dir=tmp, cwd=tmp, max_workers=3, python=sys.executable, echo=False)}
            self.assertEqual(marker.read_text().split(), ["a", "b"])
        self.assertEqual(results["bad"]["rc"], 3)
        self.assertEqual(results["after_bad"]["skipped_because"], ["bad"])
        self.assertEqual(results["after_soft"]["rc"], 0)

    def test_unknown_dependency_is_rejected(self):
        from generic_utils.task_dag import validate_steps

        with self.assertRaises(ValueError):
            validate_steps([{"name": "a", "after": ["missing"], "argv": []}])


class TestTemplate(unittest.TestCase):
    TEMPLATE = {
        "name": "unit-{DAY}", "page_key": "unit", "title": "Unit {DAY}", "tone_map": {"NEW": "good"},
        "tabs": [{"label": "Plan", "blocks": [
            {"type": "callout", "from": "update.banner"},
            {"type": "callout", "from": "pages.unit.blocks.bias"},
            {"type": "table", "title": "T", "source": {"csv": "{PACK}/t.csv"}, "extra_from": "courses", "extra_columns": ["Course"]},
        ]}],
    }

    def test_agent_and_manual_rendering(self):
        from generic_utils.canvas_page import render_template

        judgment = {"courses": {"NYSE:A": {"Course": "NEW"}},
                    "pages": {"unit": {"title": "Unit page", "blocks": {"bias": {"title": "Bias", "text": "x"}}}}}
        agent = render_template(self.TEMPLATE, pack="P", day="20261022", judgment=judgment)
        blocks = agent["tabs"][0]["blocks"]
        self.assertEqual(agent["name"], "unit-20261022")
        self.assertEqual(agent["title"], "Unit page")
        self.assertEqual(len(blocks), 2)  # no update banner in the judgment
        self.assertEqual(blocks[1]["source"]["csv"], "P/t.csv")
        self.assertEqual(blocks[1]["extra"], {"NYSE:A": {"Course": "NEW"}})
        manual = render_template(self.TEMPLATE, pack="P", day="20261022", judgment=None)
        self.assertEqual(manual["name"], "unit-20261022-manual")
        mblocks = manual["tabs"][0]["blocks"]
        self.assertEqual(len(mblocks), 1)
        self.assertNotIn("extra_columns", mblocks[0])


class TestTemplateSourcesAndMissingFiles(unittest.TestCase):
    def test_sources_come_from_the_pack_and_missing_tables_become_notes(self):
        import json as _json

        from generic_utils.canvas_page import build_page, render_template

        with TemporaryDirectory() as tmp:
            pack = Path(tmp) / "briefing_pack_x"
            pack.mkdir()
            (pack / "briefing_pack.json").write_text(_json.dumps({"sources": {"prediction_run_id": "pred_1", "mtp_dir": "/a/mtp_2"}}), encoding="utf-8")
            template = {"name": "u-{DAY}", "sources_extra": ["static"], "tabs": [{"label": "T", "blocks": [
                {"type": "table", "title": "Missing", "source": {"csv": "{PACK}/nope.csv"}}]}]}
            spec = render_template(template, pack=str(pack), day="20261022", judgment=None)
            self.assertEqual(spec["sources"], ["pack `briefing_pack_x`", "pred `pred_1`", "MTP `mtp_2`", "static"])
            page = build_page(spec, base_dir=Path(tmp))
        self.assertEqual(page["tabs"][0]["blocks"][0]["type"], "text")
        self.assertTrue(any("Missing" in w for w in page["warnings"]))


class TestUpdateDiff(unittest.TestCase):
    def test_band_kill_and_label_flags(self):
        from generic_utils.page_update import diff_symbol

        old = {"close": 10.0, "entry_low": 9.5, "entry_high": 10.0, "kill_px": 9.2, "mark_vs_band": "inside",
               "rsi": 50, "up_class": "IS_SUPPORTED", "left": 40}
        fields = ["close", "rsi", "up_class", "left"]
        thresholds = {"close_pct": 2.0, "rsi": 5.0, "left": 5.0}
        killed = diff_symbol("NYSE:A", old, dict(old, close=9.1, up_class="CHASE"), fields=fields, thresholds=thresholds)
        self.assertIn("THROUGH_KILL", killed["flags"])
        self.assertIn("up_class IS_SUPPORTED->CHASE", killed["flags"])
        above = diff_symbol("NYSE:A", old, dict(old, close=10.5), fields=fields, thresholds=thresholds)
        self.assertIn("band inside->above_band", above["flags"])
        quiet = diff_symbol("NYSE:A", old, dict(old, close=9.9, rsi=52), fields=fields, thresholds=thresholds)
        self.assertFalse(quiet["review"])
        reclaim = {"close": 84.0, "entry_low": 93.5, "entry_high": 95.2, "kill_px": 87.7, "mark_vs_band": "below_band"}
        still = diff_symbol("NYSE:R", reclaim, dict(reclaim, close=84.9), fields=fields, thresholds=thresholds)
        self.assertNotIn("THROUGH_KILL", still["flags"])  # the kill already sat above the price
        gone = diff_symbol("NYSE:A", old, None, fields=fields, thresholds=thresholds)
        self.assertEqual(gone["flags"], ["dropped_from_tables"])


if __name__ == "__main__":
    unittest.main()


class TestBufferedLogs(unittest.TestCase):
    def test_buffered_output_is_byte_identical(self):
        from generic_utils.log_to_files_util import buffer_log_writes, buffered_logs, log_to_file

        def write(path_a, path_b):
            for i in range(50):
                log_to_file(path_a, f"line {i}")
                log_to_file(path_b, f"other {i}", add_newline=(i % 2 == 0))

        with TemporaryDirectory() as tmp:
            plain_a, plain_b = Path(tmp) / "p" / "a.log", Path(tmp) / "p" / "b.log"
            buf_a, buf_b = Path(tmp) / "b" / "a.log", Path(tmp) / "b" / "b.log"
            write(plain_a, plain_b)
            with buffered_logs():
                write(buf_a, str(buf_b))
                self.assertFalse(buf_a.exists())  # held until the block exits
            buffer_log_writes(write)(buf_a, buf_b)
            write(plain_a, plain_b)
            self.assertEqual(plain_a.read_bytes(), buf_a.read_bytes())
            self.assertEqual(plain_b.read_bytes(), buf_b.read_bytes())


class TestReviewFixes(unittest.TestCase):
    def test_step_that_cannot_start_fails_alone(self):
        from generic_utils.task_dag import run_dag

        with TemporaryDirectory() as tmp:
            steps = [{"name": "ghost", "after": [], "argv": [str(Path(tmp) / "missing.exe")]},
                     {"name": "fine", "after": [], "argv": ["python", "-c", "pass"]},
                     {"name": "after_ghost", "after": ["ghost"], "argv": ["python", "-c", "pass"]}]
            results = {r["name"]: r for r in run_dag(steps, log_dir=tmp, cwd=tmp, python=sys.executable, echo=False)}
        self.assertEqual(results["ghost"]["rc"], -1)
        self.assertEqual(results["fine"]["rc"], 0)
        self.assertEqual(results["after_ghost"]["skipped_because"], ["ghost"])

    def test_sell_of_a_name_not_in_the_book_is_flagged(self):
        from generic_utils.capital_plan import plan_rows

        out = plan_rows([{"symbol": "ZZZ", "course": "EXIT"}], [{"ticker": "MU", "value_usd": 100, "ind": "x"}], cash=0)
        self.assertEqual(out["plan"][0]["size_note"], "not_in_book")
        self.assertEqual(out["summary"]["cash_from_sells_usd"], 0)

    def test_newest_pack_is_current_compares_locked_inputs(self):
        import json as _json
        from unittest import mock

        import run_daily_refresh as rdr

        with TemporaryDirectory() as tmp:
            runs = Path(tmp)
            pack = runs / "briefing_pack_20261022_1830_utc_x"
            pack.mkdir()
            sources = {k: f"v_{k}" for k in rdr.SOURCE_KEYS}
            (pack / "briefing_pack.json").write_text(_json.dumps({"sources": sources}), encoding="utf-8")
            with mock.patch.object(rdr, "RUNS", runs), \
                 mock.patch("operator_briefing.inspect_sources.inspect_locked_sources", return_value={"sources": dict(sources)}):
                self.assertTrue(rdr.newest_pack_is_current())
            newer = dict(sources, prediction_run_id="v_new")
            with mock.patch.object(rdr, "RUNS", runs), \
                 mock.patch("operator_briefing.inspect_sources.inspect_locked_sources", return_value={"sources": newer}):
                self.assertFalse(rdr.newest_pack_is_current())
