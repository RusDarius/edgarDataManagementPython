import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_plot_browser_view import (
    CHROME_FILE_URL_SAFE_LENGTH,
    BackwardsPlotRef,
    extract_backwards_run_slug,
    is_safe_for_chrome_file_url,
    register_backwards_plot,
    resolve_backwards_plot_html_path,
)


class TestPlotBrowserView(unittest.TestCase):
    def test_extract_backwards_run_slug(self):
        slug = extract_backwards_run_slug(
            "backwards_prediction_analysis_20260624_1835_utc_d9d4a78b"
        )
        self.assertEqual(slug, "20260624_1835_utc_d9d4a78b")

    def test_register_backwards_plot_builds_run_and_master_indexes(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            plots_root_dir = temp_root / "plots"
            run_slug = "20260624_1835_utc_d9d4a78b"
            html_path = resolve_backwards_plot_html_path(
                run_slug,
                "stmpa_vsh_goog",
                "breakout_long_v1",
                plots_root_dir=plots_root_dir / "backwards",
            )
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text("<html><body>plot</body></html>", encoding="utf-8")

            entry = register_backwards_plot(
                html_path,
                plot_ref=BackwardsPlotRef(
                    run_slug=run_slug,
                    symbol_slug="stmpa_vsh_goog",
                    profile_slug="breakout_long_v1",
                    horizon_name="weeks",
                    database_path=str(
                        temp_root
                        / "logs"
                        / "backwards_prediction_analysis_20260624_1835_utc_d9d4a78b"
                        / "backwards_prediction_analysis.duckdb"
                    ),
                    run_data_dir=str(temp_root / "logs" / "progression_plots"),
                    title="Breakout plot",
                ),
                plots_root_dir=plots_root_dir,
            )

            self.assertLess(len(entry["browser_file_uri"]), CHROME_FILE_URL_SAFE_LENGTH)
            self.assertTrue(is_safe_for_chrome_file_url(Path(entry["html_path"])))
            self.assertTrue((plots_root_dir / "index.html").is_file())
            self.assertTrue((plots_root_dir / "latest" / "index.html").is_file())
            self.assertTrue(
                (
                    plots_root_dir
                    / "backwards"
                    / run_slug
                    / "symbols__stmpa_vsh_goog"
                    / "index.html"
                ).is_file()
            )
            self.assertIn("plots_index_file_uri", entry)
            self.assertIn("latest_index_file_uri", entry)


if __name__ == "__main__":
    unittest.main()
