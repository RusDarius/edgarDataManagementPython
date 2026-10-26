import unittest


def _day_file(path, run_ids, rows):
    import duckdb

    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE run_metadata (run_id VARCHAR, created_at_utc TIMESTAMP)")
    for k, run in enumerate(run_ids):
        con.execute("INSERT INTO run_metadata VALUES (?, ?)", [run, f"2026-10-0{k + 1}"])
    con.execute("CREATE TABLE all_fields_rows (run_id VARCHAR, symbol VARCHAR, close VARCHAR, change VARCHAR, "
                "price_target_average VARCHAR, earnings_per_share_forecast_next_fy VARCHAR, market_cap_basic VARCHAR)")
    for row in rows:
        con.execute("INSERT INTO all_fields_rows VALUES (?, ?, ?, ?, ?, ?, ?)", row)
    con.close()


class TestPanelSkipWithoutRuns(unittest.TestCase):
    def test_file_without_run_metadata_is_skipped_only_when_asked(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from generic_utils.panel import load_panel

        with TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.duckdb", Path(tmp) / "b.duckdb"
            _day_file(a, ["r1"], [("r1", "NASDAQ:MU", "10", "1", "12", "2", "1e9")])
            _day_file(b, [], [("x", "NASDAQ:MU", "11", "1", "12", "2", "1e9")])
            sources = [{"path": a, "as_of": "2026-10-01"}, {"path": b, "as_of": "2026-10-02"}]
            every = load_panel(sources, columns=["symbol", "close"], numeric=["close"])
            skip = load_panel(sources, columns=["symbol", "close"], numeric=["close"], skip_without_runs=True)
        self.assertEqual([r["close"] for r in every["rows"]], [10.0, 11.0])
        self.assertEqual([r["close"] for r in skip["rows"]], [10.0])
        self.assertEqual(skip["snapshots"][1]["note"], "no run_metadata match")


class TestThemesAndHistory(unittest.TestCase):
    def test_theme_ids_dedupe_in_order_and_unknown_raises(self):
        from generic_utils.name_levels import theme_ids

        themes = {"a": {"ids": ["MU", "PENG"]}, "b": {"ids": ["PENG", "CRWD"]}}
        self.assertEqual(theme_ids(["a", "b"], themes), ["MU", "PENG", "CRWD"])
        with self.assertRaises(KeyError):
            theme_ids(["zzz"], themes)

    def test_shipped_themes_load(self):
        from generic_utils.name_levels import load_themes

        themes = load_themes()
        self.assertIn("ai_core", themes)
        self.assertTrue(themes["ai_buildout"]["industries"])

    def test_history_rows_keep_exact_ids_and_skip_runless_files(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from generic_utils.name_levels import history_rows

        with TemporaryDirectory() as tmp:
            for day, runs in (("01_10_2026", ["r1"]), ("02_10_2026", [])):
                folder = Path(tmp) / day
                folder.mkdir()
                _day_file(folder / f"tradingview_all_fields_{day}.duckdb", runs,
                          [("r1" if runs else "x", "NASDAQ:MU", "10", "1", "12", "2", "1e9"),
                           ("r1" if runs else "x", "TSXV:MU", "1", "1", "1", "1", "1e6")])
            out = history_rows(["NASDAQ:MU"], root=tmp)
        self.assertEqual([r["symbol"] for r in out["rows"]], ["NASDAQ:MU"])
        self.assertEqual((out["n_files"], out["skipped_files"]), (2, 1))


class TestJudgmentTools(unittest.TestCase):
    def test_shapes_carry_and_unfilled(self):
        from generic_utils.judgment_tools import build_skeleton, carry_courses, template_shapes, unfilled

        template = {"page_key": "p", "tabs": [{"label": "T", "blocks": [
            {"type": "stats", "from": "pages.p.blocks.s0"},
            {"type": "table", "from": "pages.p.blocks.t0", "headers": ["Symbol", "Course"]},
            {"type": "table", "source": {"csv": "x"}, "ids_from": "pages.p.best"}]}]}
        shapes = template_shapes(template)["p"]
        self.assertTrue(shapes["s0"].startswith("stats: list of {value, label, tone}"))
        self.assertIn("[[Symbol, Course]]", shapes["t0"])
        self.assertTrue(shapes["best"].startswith("ids:"))
        prev = {"day": "20261023", "courses": {"NASDAQ:MU": {"Course": "HOLD", "Reason": "why"},
                                                "NYSE:X": {"Course": "PASS", "Reason": "22 Oct (carried): old"}},
                "scans": [{"name": "s", "argv": ["python"]}]}
        carried = carry_courses(prev, prev_day="20261023")
        self.assertEqual(carried["NASDAQ:MU"]["Reason"], "23 Oct (carried): why")
        self.assertEqual(carried["NYSE:X"]["Reason"], "22 Oct (carried): old")  # first date kept
        skeleton = build_skeleton("20261026", pack="logs/x/briefing_pack_1", templates=[template], prev=prev, prev_day="20261023")
        self.assertEqual(skeleton["pack"], "briefing_pack_1")
        self.assertEqual(skeleton["scans"], prev["scans"])
        self.assertEqual(unfilled(skeleton), {"p": ["s0", "t0", "best"]})
        skeleton["pages"]["p"]["blocks"]["s0"] = [{"value": "1", "label": "x"}]
        skeleton["pages"]["p"]["best"] = ["NASDAQ:MU"]
        self.assertEqual(unfilled(skeleton), {"p": ["t0"]})


class TestDeepDive(unittest.TestCase):
    def test_groups_qualify_and_template_has_a_tab_per_group(self):
        from generic_utils.deep_dive import build_template, qualify_groups

        groups = qualify_groups({"AI": ["MU", "NASDAQ:PENG", "ZZZ"], "Cyber": ["CRWD"], "Empty": ["QQQQ"]},
                                ["NASDAQ:MU", "NASDAQ:PENG", "NASDAQ:CRWD"])
        self.assertEqual(groups, {"AI": ["NASDAQ:MU", "NASDAQ:PENG"], "Cyber": ["NASDAQ:CRWD"], "Empty": []})
        template = build_template("RUN/name_levels", groups)
        labels = [t["label"] for t in template["tabs"]]
        self.assertEqual(labels, ["Plan", "AI", "Cyber", "Lenses", "Sources"])
        sql = template["tabs"][1]["blocks"][1]["source"]["sql"]
        self.assertIn("'NASDAQ:MU', 'NASDAQ:PENG'", sql)
        self.assertEqual(template["tabs"][2]["blocks"][0]["from"], "pages.deepdive.blocks.group_2")


class TestRendererHooks(unittest.TestCase):
    def test_optional_table_is_a_note_without_warning_and_requires_list(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from generic_utils.canvas_page import build_page, write_template_page

        spec = {"name": "x", "tabs": [{"label": "T", "blocks": [
            {"type": "table", "title": "opt", "optional": True, "source": {"csv": "missing.csv"}},
            {"type": "table", "title": "req", "source": {"csv": "missing.csv"}}]}]}
        with TemporaryDirectory() as tmp:
            warnings = build_page(spec, base_dir=Path(tmp))["warnings"]
            template = Path(tmp) / "t.json"
            template.write_text('{"name": "t-{DAY}", "requires": ["{PACK}", "{PACK}/nope.duckdb"], "tabs": []}', encoding="utf-8")
            skipped = write_template_page(template, pack=tmp, day="20261026", out_dir=tmp)
        self.assertEqual([w for w in warnings if "opt" in w], [])
        self.assertTrue(any("'req'" in w for w in warnings))
        self.assertIsNone(skipped["html"])
        self.assertTrue(skipped["skipped"].startswith("t-20261026:"))


class TestCompactOutput(unittest.TestCase):
    def test_row_lines_and_one_line(self):
        from generic_utils.tv_scan_cli import _one_line, _row_lines

        text = _row_lines([{"symbol": "NASDAQ:MU", "close": "910.6712", "left": "", "rsi": 43.33333}], ["symbol", "close", "left", "rsi"])
        self.assertEqual(text, "NASDAQ:MU close=910.7 rsi=43.33")
        self.assertEqual(_one_line({"spec": {"n": 2, "missing_ids": ["ZZZ"]}, "out": "a.csv"}), "n=2 missing_ids=ZZZ out=a.csv")

    def test_movers_class_counts_line(self):
        from operator_briefing.compile import _class_counts

        block = {"leaders": [{"symbol": "NYSE:A", "up_class": "IS_SUPPORTED"}, {"symbol": "NYSE:B", "up_class": "CHASE"},
                             {"symbol": "NYSE:C", "up_class": "CHASE"}],
                 "laggards": [{"down_class": "BOUNCE"}]}
        self.assertEqual(_class_counts(block), "up CHASE 2 / IS_SUPPORTED 1; punished BOUNCE 1; IS_SUPPORTED: NYSE:A")


class TestCapitalPlanFromJudgment(unittest.TestCase):
    def test_rows_come_from_the_judgment_key(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from generic_utils.tv_scan_cli import main

        with TemporaryDirectory() as tmp:
            judgment = Path(tmp) / "judgment.json"
            judgment.write_text(json.dumps({"capital_plan": {"capital_plan": [
                {"symbol": "NASDAQ:MU", "course": "TRIM", "frac": "0.5", "px": "100"}]}}), encoding="utf-8")
            pack = Path(tmp) / "briefing_pack.json"
            pack.write_text(json.dumps({"book": [{"symbol": "NASDAQ:MU", "value_usd": 1000, "ind": "Semis"}],
                                        "capital": {"cash_usd": 500}}), encoding="utf-8")
            payload = main(["capital-plan", "--judgment", str(judgment), "--pack", str(pack), "--quiet"])
            self.assertTrue((Path(tmp) / "capital_plan" / "capital_plan.duckdb").exists())
        self.assertEqual(payload["summary"]["cash_end_usd"], 1000.0)


if __name__ == "__main__":
    unittest.main()


class TestRunnerSkeleton(unittest.TestCase):
    def test_full_mode_skeleton_carries_the_prior_day_and_never_writes_the_live_judgment(self):
        import json
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest import mock

        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TV_CANVAS_ROOT": tmp}):
            from generic_utils.canvas_runs import judgment_path, previous_judgment
            from run_operator_day import write_skeleton

            for day in ("20261022", "20261023"):
                (Path(tmp) / day).mkdir()
                (Path(tmp) / day / "judgment.json").write_text(json.dumps({"day": day, "courses": {"NASDAQ:MU": {"Course": "HOLD", "Reason": day}}}), encoding="utf-8")
            self.assertEqual(previous_judgment("20261026")[0], "20261023")
            run = Path(tmp) / "20261026" / "1606_full"
            run.mkdir(parents=True)
            path = write_skeleton("20261026", run, pack="logs/x/briefing_pack_y")
            skeleton = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(judgment_path("20261026").exists())
        self.assertEqual(skeleton["courses"]["NASDAQ:MU"]["Reason"], "23 Oct (carried): 20261023")
        self.assertIn("plan_0", skeleton["pages"]["daily"]["_shapes"])
        self.assertIn("best_25", skeleton["pages"]["playbook"]["_shapes"])
