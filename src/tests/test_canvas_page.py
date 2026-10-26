import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class TestCanvasPage(unittest.TestCase):
    def _spec(self, **overrides):
        spec = {
            "name": "unit-20261022",
            "tabs": [
                {
                    "label": "Plan",
                    "blocks": [
                        {
                            "type": "table",
                            "title": "Actions",
                            "headers": ["Symbol", "Action", "rr", "zz_unknown"],
                            "rows": [["MU", "TRIM", 1.23456, "x"], ["SNDK", "ODD_CODE", 2, "y"]],
                        }
                    ],
                }
            ],
            "ledger": {"auto": True},
        }
        spec.update(overrides)
        return spec

    def test_auto_ledger_pulls_standing_meanings_and_warns_on_gaps(self):
        from generic_utils.canvas_page import build_page

        page = build_page(self._spec(), base_dir=Path("."))
        keys = {row[0] for group in page["ledger"] for row in group["rows"]}
        self.assertIn("rr", keys)
        self.assertIn("TRIM", keys)
        joined = " ".join(page["warnings"])
        self.assertIn("zz_unknown", joined)
        self.assertIn("ODD_CODE", joined)
        # Bare tickers in a free-text column are not coded values.
        self.assertNotIn("'MU'", joined)
        self.assertEqual(page["tabs"][0]["blocks"][0]["rows"][0][2], "1.23")

    def test_page_ledger_entry_overrides_standard(self):
        from generic_utils.canvas_page import build_page

        spec = self._spec(ledger={"auto": True, "entries": [{"group": "Page", "rows": [["rr", "Page meaning."]]}]})
        page = build_page(spec, base_dir=Path("."))
        rr_rows = [row for group in page["ledger"] for row in group["rows"] if row[0] == "rr"]
        self.assertEqual(rr_rows, [["rr", "Page meaning."]])

    def test_mesh_cells_are_explained_by_their_letters(self):
        from generic_utils.canvas_page import build_page

        spec = self._spec(tabs=[{"label": "Base", "blocks": [
            {"type": "table", "headers": ["Symbol", "Mesh"], "rows": [["MU", "FCPD"], ["SNDK", "-"]]}]}])
        page = build_page(spec, base_dir=Path("."))
        self.assertEqual([w for w in page["warnings"] if "FCPD" in w or "letter" in w], [])
        keys = {row[0] for group in page["ledger"] for row in group["rows"]}
        self.assertTrue({"F", "C", "P", "D"} <= keys)

    def test_a_second_ledger_tab_is_rejected(self):
        from generic_utils.canvas_page import build_page

        spec = self._spec(tabs=[{"label": "Ledger", "blocks": []}])
        with self.assertRaises(ValueError):
            build_page(spec, base_dir=Path("."))

    def test_csv_source_where_sort_limit_extra_and_tone(self):
        from generic_utils.canvas_page import build_page

        with TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "radar.csv"
            csv_path.write_text(
                "symbol,left,rr\nNASDAQ:MU,40,2.5\nNASDAQ:SNDK,10,9.0\nNYSE:FSLR,30,3.5\nNYSE:JD,50,\n",
                encoding="utf-8",
            )
            spec = self._spec(
                tabs=[
                    {
                        "label": "Radar",
                        "blocks": [
                            {
                                "type": "table",
                                "source": {"csv": "radar.csv"},
                                "where": "left>=25",
                                "sort": "-rr",
                                "limit": 2,
                                "columns": ["symbol", "left", "rr"],
                                "extra": {"NYSE:FSLR": {"Course": "NEW"}},
                                "extra_columns": ["Course"],
                                "tone_field": "Course",
                                "tone_map": {"NEW": "good"},
                            }
                        ],
                    }
                ]
            )
            page = build_page(spec, base_dir=Path(tmp))
        table = page["tabs"][0]["blocks"][0]
        self.assertEqual([r[0] for r in table["rows"]], ["NYSE:FSLR", "NASDAQ:MU"])
        self.assertEqual(table["headers"], ["symbol", "left", "rr", "Course"])
        self.assertEqual(table["rows"][0][3], "NEW")
        self.assertEqual(table["tones"], ["good", None])
        self.assertEqual(table["spec"]["n_loaded"], 4)
        self.assertEqual(table["spec"]["where"][0]["field"], "left")

    def test_write_page_writes_html_with_one_ledger_and_index(self):
        from generic_utils.canvas_page import write_page

        with TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "unit-20261022.json"
            spec_path.write_text(json.dumps(self._spec()), encoding="utf-8")
            payload = write_page(spec_path)
            html_text = Path(payload["html"]).read_text(encoding="utf-8")
            self.assertTrue(Path(payload["index"]).exists())
        self.assertEqual(payload["tabs"], ["Plan", "Ledger"])
        self.assertEqual(html_text.count('id="ledger"'), 1)
        self.assertIn("<title>unit-20261022</title>", html_text)


if __name__ == "__main__":
    unittest.main()
