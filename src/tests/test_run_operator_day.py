import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


class TestOperatorDay(unittest.TestCase):
    def test_prompts_fill_day_pack_and_keep_the_scan_placeholder(self):
        import run_operator_day as rod

        for mode in ("full", "update"):
            text = rod.build_agent_prompt("20261022", mode, pack="logs/x/briefing_pack_1", refresh_json="r.json")
            self.assertIn("20261022", text)
            self.assertIn("logs/x/briefing_pack_1", text)
            self.assertNotIn("{DAY}", text)
            self.assertNotIn("{REFRESH_JSON}", text)
        full = rod.build_agent_prompt("20261022", "full", pack="P", refresh_json=None)
        self.assertIn("argv with {PACK}", full)  # literal placeholder survives for judgment scans

    def test_cli_from_env_wins(self):
        import run_operator_day as rod

        with TemporaryDirectory() as tmp:
            fake = Path(tmp) / "claude.exe"
            fake.write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, {"CLAUDE_CLI": str(fake)}):
                self.assertEqual(rod.find_claude_cli(), str(fake))


if __name__ == "__main__":
    unittest.main()
