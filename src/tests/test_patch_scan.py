from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

OLD = '''
def keep(a):
    return a + 1


def gone():
    return 0


class Box:
    def size(self):
        return 1
'''

NEW = '''
def keep(a):
    # a comment and a blank line do not count as a change

    return a + 1


def fresh(b):
    return b * 2


class Box:
    def size(self):
        return 2

    def weight(self):
        return 3
'''

CLI = '''
import argparse


def _cmd_peer_value(args):
    from generic_utils.peer_value import build_peer_value
    return build_peer_value()


def _cmd_anatomy(args):
    from generic_utils.winners_anatomy import build_anatomy
    return build_anatomy()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    pv = sub.add_parser("peer-value", help="Peer value: premium or discount to the industry. "
                                           "Second sentence.")
    pv.add_argument("--pack")
    pv.add_argument("--no-evidence", action="store_true")
    sub.add_parser("anatomy", help="Winners anatomy on the panel store.")
'''


class SymbolTests(unittest.TestCase):
    def test_added_removed_changed_ignore_comments(self):
        from generic_utils.patch_scan import py_symbols, symbol_changes

        ch = symbol_changes(OLD, NEW)
        self.assertEqual(ch["added"], ["fresh", "Box.weight"])
        self.assertEqual(ch["removed"], ["gone"])
        self.assertEqual(sorted(ch["changed"]), ["Box", "Box.size"])  # keep() only gained a comment
        self.assertEqual(symbol_changes(None, NEW)["added"], ["keep", "fresh", "Box", "Box.size", "Box.weight"])
        self.assertEqual(py_symbols("def broken(:"), {})  # a file that does not parse is reported elsewhere, not a crash
        self.assertEqual(py_symbols(NEW)["Box"]["kind"], "class")

    def test_cli_surface_and_aliases(self):
        from generic_utils.patch_scan import cli_aliases, cli_surface, first_sentence, mention_tokens

        surf = cli_surface(CLI)
        self.assertEqual(list(surf["subcommands"]), ["peer-value", "anatomy"])
        self.assertEqual(surf["flags"], ["--pack", "--no-evidence"])
        self.assertEqual(first_sentence(surf["subcommands"]["peer-value"]), "Peer value: premium or discount to the industry.")
        self.assertEqual(first_sentence("Names (e.g. one, two) and more. Then this."), "Names (e.g. one, two) and more.")
        aliases = cli_aliases(CLI)
        self.assertEqual(aliases["winners_anatomy"], ["anatomy"])  # the subcommand is not named like the module
        tokens = mention_tokens("src/generic_utils/winners_anatomy.py", aliases["winners_anatomy"])
        self.assertIn(".py anatomy", tokens)
        self.assertIn("winners-anatomy", tokens)
        self.assertNotIn("valuation", mention_tokens("src/financial_projection/valuation.py", []))  # a one-word stem is too common


class ConfigTests(unittest.TestCase):
    def test_json_changes_names_list_entries(self):
        from generic_utils.patch_scan import json_changes

        old = {"templates": ["brief"], "suites": [{"name": "movers", "after": []}, {"name": "outlook", "after": ["week_plays"]}], "max_workers": 4}
        new = {"templates": ["brief", "board"], "suites": [{"name": "movers", "after": []}, {"name": "outlook", "after": ["week_plays", "event_risk"]},
                                                           {"name": "event_risk", "after": ["movers"]}], "templates_all": ["brief"]}
        ch = json_changes(json.dumps(old), json.dumps(new))
        self.assertEqual((ch["added"], ch["removed"], sorted(ch["changed"])), (["templates_all"], ["max_workers"], ["suites", "templates"]))
        self.assertEqual(ch["lists"]["suites"], {"added": ["event_risk"], "removed": [], "changed": ["outlook"]})
        self.assertEqual(json_changes("{", "{}"), {"error": "not valid JSON"})

    def test_area_skill_and_numbering(self):
        from generic_utils.patch_scan import area_of, load_recipe, next_number, skill_meta

        areas = load_recipe()["areas"]
        self.assertEqual(area_of("src/generic_utils/tv_scan_cli.py", areas), "CLI (tv_scan_cli)")  # the specific prefix wins
        self.assertEqual(area_of("src/generic_utils/event_risk.py", areas), "generic_utils")
        self.assertEqual(area_of("config/generic_utils/pages/board.json", areas), "page templates")
        self.assertEqual(area_of("src/run_deep_dive.py", areas), "runners")
        self.assertEqual(area_of("package.json", areas), "other")
        meta = skill_meta("---\nname: patch-notes\ndescription: Review a change set. Then write it up.\n---\n# Body\n")
        self.assertEqual((meta["name"], meta["description"]), ("patch-notes", "Review a change set. Then write it up."))
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertEqual(next_number(d), 0)
            for name in ("0000_baseline.md", "0003_x.md", "README.md", "capability_index.md"):
                (d / name).write_text("x", encoding="utf-8")
            self.assertEqual(next_number(d), 4)


class ModeTests(unittest.TestCase):
    def test_range_forms_and_first_commit(self):
        from generic_utils.patch_scan import EMPTY_TREE, _cell, git, resolve_mode

        self.assertEqual(resolve_mode(rng="abc..def")["diff"], ["abc", "def"])
        self.assertEqual(resolve_mode(rng="abc...def")["diff"], ["abc", "def"])  # three dots must not leave '.def'
        self.assertEqual(resolve_mode(rng="abc..")["new"], "HEAD")
        with self.assertRaises(ValueError):
            resolve_mode(rng="..def")
        self.assertEqual(resolve_mode()["diff"], ["--cached"])  # no flag = the staged index
        self.assertEqual(resolve_mode(worktree=True)["new"], "WORKTREE")
        root = git("rev-list", "--max-parents=0", "HEAD").split()[0]
        self.assertEqual(resolve_mode(commit=root)["old"], EMPTY_TREE)  # the first commit has no parent to diff against
        self.assertEqual(resolve_mode(commit="HEAD")["old"], "HEAD^")
        self.assertEqual(_cell("named --csv|--db"), "named --csv\\|--db")  # a pipe would split a table row


class WiringTests(unittest.TestCase):
    def test_refresh_argv_on_several_lines_counts_as_a_mention(self):
        from generic_utils.patch_scan import load_recipe, wiring_rows

        # daily_refresh.json writes each argv item on its own line; the suite name differs from the module stem
        rows = {r["module"]: r for r in wiring_rows(["src/generic_utils/panel_store.py", "src/generic_utils/deterioration.py"], load_recipe())}
        self.assertEqual(rows["src/generic_utils/panel_store.py"]["refresh"], "yes")
        self.assertIn("panel-store", rows["src/generic_utils/panel_store.py"]["cli"])
        self.assertEqual(rows["src/generic_utils/deterioration.py"]["refresh"], "yes")
        self.assertEqual(rows["src/generic_utils/deterioration.py"]["tests"], "test_deterioration.py")


class LifecycleTests(unittest.TestCase):
    TEXTS = {
        "src/a.py": "from generic_utils.old_tool import build_old, helper\nrows = build_old()\n",
        "src/b.py": "def build_old():\n    return 1  # same name, another module: not a reference\n",
        "config/refresh.json": '{"suites": [{"name": "x", "after": ["old_suite"],\n "argv": ["python", "src/generic_utils/tv_scan_cli.py",\n   "old-cmd", "--pack"]}]}',
        "CLAUDE.md": "Run `tv_scan_cli.py old-cmd --pack PACK`; not `tv_scan_cli.py old-cmd-two`.\n",
    }

    def test_what_still_points_at_a_removed_thing(self):
        from generic_utils.patch_scan import references, removal_patterns

        mod = references(texts=self.TEXTS, **removal_patterns("module", "old_tool", "src/generic_utils/old_tool.py"))
        self.assertEqual(mod, [("src/a.py", 2)])  # the import line matches as an import and as the bare stem
        fn = references(texts=self.TEXTS, **removal_patterns("function", "build_old", "src/generic_utils/old_tool.py"))
        self.assertEqual([p for p, _ in fn], ["src/a.py"])  # b.py defines its own build_old and never names old_tool
        cmd = dict(references(texts=self.TEXTS, **removal_patterns("subcommand", "old-cmd", "src/generic_utils/tv_scan_cli.py")))
        self.assertEqual(cmd, {"CLAUDE.md": 1, "config/refresh.json": 1})  # prose once (old-cmd-two is another command), argv over two lines
        entry = references(texts=self.TEXTS, **removal_patterns("list entry", "old_suite", "config/refresh.json"))
        self.assertEqual(entry, [("config/refresh.json", 1)])  # a removed suite still named in another suite's `after`
        self.assertEqual(references(["("], self.TEXTS), [])  # a pattern that does not compile is skipped, not a crash

    def test_added_lines_wording_and_deprecated_use(self):
        from generic_utils.patch_scan import deprecated_uses, lifecycle_signals

        recipe = {"lifecycle": {"signal_words": ["deprecat", "legacy"], "signal_limit": 1, "signal_skip": ["documentation/patch_notes/"],
                                "guard_statuses": ["deprecated", "removed"]}}
        added = {"src/new.py": [(10, "x = old_field  # the Legacy reader"), (11, "y = 1")],
                 "src/fallback.py": [(3, "return row.get('old_field')")],
                 "documentation/patch_notes/0002_x.md": [(1, "old_field is deprecated")],
                 "README.md": [(5, "The scorer is deprecated in favour of the board.")]}
        sig = lifecycle_signals(added, recipe)
        self.assertEqual((sig["total"], len(sig["rows"])), (2, 1))  # the patch notes are skipped; the limit only cuts the display
        self.assertEqual((sig["rows"][0]["path"], sig["rows"][0]["word"]), ("README.md", "deprecat"))
        entries = [{"names": ["old_field"], "status": "deprecated", "patterns": [r"\bold_field\b"], "allow": ["src/fallback.py"], "replaced_by": "new_field"},
                   {"names": ["page_x"], "status": "on_demand", "patterns": ["page_x"]},  # not a guarded status
                   {"names": ["marker"], "status": "removed", "patterns": ["y = 1"], "guard": False}]
        uses = deprecated_uses(added, entries, recipe)
        self.assertEqual([(u["kind"], u["path"]) for u in uses], [("deprecated item used", "src/new.py")])
        self.assertIn("use new_field", uses[0]["detail"])

    def test_command_front_module_and_patch_numbers(self):
        from generic_utils.patch_scan import cli_primary, patch_index, patch_of

        source = CLI + '''

def _cmd_named(args):
    from generic_utils.series import parse_csv_list
    from generic_utils.scan_sources import rows_from_csv


def _cmd_span(args):
    from generic_utils.series import span_rows
'''
        front = cli_primary(source)
        self.assertEqual((front["peer-value"], front["anatomy"]), ("peer_value", "winners_anatomy"))
        self.assertEqual(front["named"], "scan_sources")  # series is shared by two commands, scan_sources by one
        commits = [{"n": 1, "sha": "aaaaaaa"}, {"n": 2, "sha": "bbbbbbb"}, {"n": 3, "sha": "ccccccc"}]
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "0000_baseline.md").write_text("# Patch 0000: baseline (through commit `bbbbbbb`)\n", encoding="utf-8")
            (d / "0001_x.md").write_text("# Patch 0001: x\n\n| | |\n|---|---|\n| Commit | pending; base `bbbbbbb` |\n", encoding="utf-8")
            patches = patch_index(d, commits)
            self.assertEqual([(p["number"], p["commit_n"], p["pending"]) for p in patches], [("0000", 2, False), ("0001", None, True)])
            self.assertEqual((patch_of(1, patches), patch_of(2, patches)), ("0000", "0000"))
            self.assertEqual((patch_of(3, patches), patch_of(None, patches)), ("0001*", "0001*"))  # after the baseline, and uncommitted work
            (d / "0001_x.md").write_text("# Patch 0001: x\n\n| | |\n|---|---|\n| Commit | `ccccccc`; base `bbbbbbb` |\n", encoding="utf-8")
            patches = patch_index(d, commits)
            self.assertEqual((patch_of(3, patches), patch_of(None, patches)), ("0001", "-"))  # committed; nothing pending


class RepoTests(unittest.TestCase):
    def test_feature_register_and_scan_lifecycle_sections(self):
        from generic_utils.patch_scan import build_scan, feature_map, features_md, load_recipe, resolve_mode, scan_md, skeleton_md

        recipe = load_recipe()
        fmap = feature_map(recipe)
        self.assertEqual(fmap["features"]["deterioration"]["cli"], ["deterioration"])
        self.assertIn("deterioration", fmap["features"]["deterioration"]["steps"])
        self.assertEqual(fmap["by_path"]["config/generic_utils/deterioration.json"], "deterioration")
        self.assertNotIn("__init__", fmap["features"])
        payload = build_scan(resolve_mode(commit="HEAD"), recipe)
        self.assertTrue({"features", "removed", "signals"} <= set(payload))
        text = scan_md(payload)
        for heading in ("## Features touched", "## Removed in this change set", "## Lifecycle wording in added lines"):
            self.assertIn(heading, text)
        self.assertIn("## Feature changes", skeleton_md(payload, 7, "inventory.md"))
        register = features_md(recipe)
        for heading in ("## Declared lifecycle", "## Features in `src/generic_utils`", "## Page templates", "## Skills", "## Files removed over the history"):
            self.assertIn(heading, register)
        self.assertIn("| `panel_store` |", register)
        self.assertNotIn("__pycache__", register)

    def test_scan_of_head_commit_and_generated_pages(self):
        from generic_utils.patch_scan import build_scan, capabilities_md, load_recipe, resolve_mode, scan_md

        recipe = load_recipe()
        payload = build_scan(resolve_mode(commit="HEAD"), recipe)
        self.assertEqual(payload["spec"]["files"], sum(a["A"] + a["M"] + a["D"] for a in payload["areas"]))
        text = scan_md(payload)
        for heading in ("## Areas", "## Findings", "## New modules", "## CLI surface", "## Wiring", "## Recipes and config", "## Tests"):
            self.assertIn(heading, text)
        index = capabilities_md(recipe)
        self.assertIn("`panel-store`", index)  # a CLI subcommand
        self.assertIn("`daily-operator-scan`", index)  # a skill
        self.assertIn("| `event_risk` |", index)  # a refresh suite


if __name__ == "__main__":
    unittest.main()
