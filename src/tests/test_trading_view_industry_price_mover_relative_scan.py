import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_industry_price_mover_relative_scan import (
    run_industry_price_mover_relative_scan,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite_duckdb,
)
from tests.test_trading_view_move_prediction_industry_packs import (
    _duckdb_available,
    _sample_scan_row,
)


@unittest.skipUnless(_duckdb_available(), "duckdb is not installed")
class TestTradingViewIndustryPriceMoverRelativeScan(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output_root = Path(self.temp_dir.name)

    def _build_semiconductor_scan(self) -> list[dict]:
        rows = [
            _sample_scan_row(
                "NASDAQ:MOVER",
                name="Hot Semi Mover Corp",
                **{"Perf.W": 12.0, "Perf.1M": 25.0},
                close=120.0,
            ),
            _sample_scan_row(
                "NASDAQ:LAGGER",
                name="Better Score Lagger Corp",
                **{"Perf.W": 1.0, "Perf.1M": 3.0},
                close=40.0,
            ),
            _sample_scan_row(
                "NASDAQ:MID",
                name="Mid Semi Corp",
                **{"Perf.W": 4.0, "Perf.1M": 8.0},
                close=55.0,
            ),
            _sample_scan_row(
                "NASDAQ:LOW",
                name="Weak Semi Corp",
                **{"Perf.W": -2.0, "Perf.1M": -1.0},
                close=30.0,
            ),
            _sample_scan_row(
                "NASDAQ:ALT",
                name="Alt Better Corp",
                **{"Perf.W": 0.5, "Perf.1M": 2.0},
                close=38.0,
            ),
        ]
        software = [
            _sample_scan_row(
                f"NASDAQ:SW{i}",
                industry="Packaged Software",
                sector="Technology Services",
                **{"Perf.W": 1.0 + i, "Perf.1M": 2.0 + i},
            )
            for i in range(1, 4)
        ]
        return rows + software

    def test_scan_finds_top_industry_and_relative_opportunities(self):
        scan_data = self._build_semiconductor_scan()
        suite_result = run_full_analysis_suite_duckdb(
            scan_data=scan_data,
            profile_names=[
                "breakout_long",
                "quality_value_compounder",
                "asymmetric_value",
            ],
            min_market_cap_usd=1_000_000_000,
            include_blind_spot_sections=False,
            output_dir=self.output_root,
            export_parquet=False,
        )

        result = run_industry_price_mover_relative_scan(
            suite_result,
            top_industries=2,
            price_movers_per_industry=2,
            catch_up_per_industry=5,
            alternatives_per_mover=2,
            min_group_size_for_peer_rescore=3,
        )

        self.assertTrue(result["overview_log"].is_file())
        self.assertTrue(result["opportunities_csv"].is_file())
        self.assertIn("Semiconductors", result["manifest"]["selected_industries"])

        semis_output = result["industries"].get("Semiconductors")
        self.assertIsNotNone(semis_output)
        detail_log = semis_output["detail_log"]
        self.assertTrue(detail_log.is_file())
        detail_text = detail_log.read_text(encoding="utf-8")
        self.assertIn("SECTION 1 — TOP PRICE MOVERS", detail_text)
        self.assertIn("SECTION 2 — BETTER-SCORED ALTERNATIVES", detail_text)
        self.assertIn("SECTION 3 — CATCH-UP / BANG-FOR-BUCK", detail_text)
        self.assertIn("NASDAQ:MOVER", detail_text)

        csv_text = result["opportunities_csv"].read_text(encoding="utf-8")
        self.assertIn("bang_for_buck", csv_text)
        self.assertGreaterEqual(result["manifest"]["opportunity_row_count"], 0)

    def test_perf_week_ranking_changes_industry_order(self):
        scan_data = [
            _sample_scan_row(
                "NASDAQ:FAST",
                industry="Semiconductors",
                **{"Perf.W": 20.0, "Perf.1M": 5.0},
            ),
            _sample_scan_row(
                "NASDAQ:SLOW",
                industry="Semiconductors",
                **{"Perf.W": 1.0, "Perf.1M": 4.0},
            ),
            _sample_scan_row(
                "NASDAQ:MED",
                industry="Semiconductors",
                **{"Perf.W": 2.0, "Perf.1M": 3.0},
            ),
            _sample_scan_row(
                "NASDAQ:PS1",
                industry="Packaged Software",
                **{"Perf.W": 1.0, "Perf.1M": 30.0},
                sector="Technology Services",
            ),
            _sample_scan_row(
                "NASDAQ:PS2",
                industry="Packaged Software",
                **{"Perf.W": 1.5, "Perf.1M": 28.0},
                sector="Technology Services",
            ),
            _sample_scan_row(
                "NASDAQ:PS3",
                industry="Packaged Software",
                **{"Perf.W": 2.0, "Perf.1M": 26.0},
                sector="Technology Services",
            ),
        ]
        suite_result = run_full_analysis_suite_duckdb(
            scan_data=scan_data,
            profile_names=["breakout_long", "quality_value_compounder"],
            min_market_cap_usd=1_000_000_000,
            include_blind_spot_sections=False,
            output_dir=self.output_root / "week",
            export_parquet=False,
        )
        week_result = run_industry_price_mover_relative_scan(
            suite_result,
            perf_field="Perf.W",
            top_industries=1,
            min_group_size_for_peer_rescore=3,
        )
        self.assertEqual(
            week_result["manifest"]["selected_industries"][0],
            "Semiconductors",
        )

        suite_result_1m = run_full_analysis_suite_duckdb(
            scan_data=scan_data,
            profile_names=["breakout_long", "quality_value_compounder"],
            min_market_cap_usd=1_000_000_000,
            include_blind_spot_sections=False,
            output_dir=self.output_root / "month",
            export_parquet=False,
        )
        month_result = run_industry_price_mover_relative_scan(
            suite_result_1m,
            perf_field="Perf.1M",
            top_industries=1,
            min_group_size_for_peer_rescore=3,
        )
        self.assertEqual(
            month_result["manifest"]["selected_industries"][0],
            "Packaged Software",
        )


if __name__ == "__main__":
    unittest.main()
