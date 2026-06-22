import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite_duckdb,
)
from data_analysis_scripts.trading_view_move_prediction_industry_packs import (
    write_industry_packs_from_duckdb_run,
)


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _sample_scan_row(symbol: str, **overrides) -> dict:
    row = {
        "symbol": symbol,
        "name": f"{symbol} Corp",
        "sector": "Electronic Technology",
        "industry": "Semiconductors",
        "market": "america",
        "market_cap_basic": 2_500_000_000,
        "total_revenue": 1_000_000_000,
        "close": 42.5,
        "relative_volume_10d_calc": 1.8,
        "average_volume_10d_calc": 1_200_000,
        "average_volume_30d_calc": 1_000_000,
        "volume": 2_000_000,
        "float_shares_outstanding": 50_000_000,
        "Value.Traded": 15_000_000,
        "Perf.W": 4.2,
        "Perf.1M": 8.5,
        "Perf.3M": 12.0,
        "Perf.YTD": 18.0,
        "Perf.Y": 35.0,
        "Perf.5Y": 120.0,
        "change": 1.8,
        "gap": 1.2,
        "ATRP": 2.0,
        "ADX+DI": 28.0,
        "ADX-DI": 18.0,
        "Aroon.Up": 85.0,
        "Aroon.Down": 40.0,
        "BB.upper": 45.0,
        "BB.lower": 40.0,
        "SMA10": 41.0,
        "SMA20": 40.0,
        "EMA10": 41.5,
        "EMA20": 40.5,
        "price_earnings_ttm": 24.0,
        "total_revenue_yoy_growth_ttm": 14.0,
        "debt_to_equity": 0.25,
    }
    row.update(overrides)
    return row


@unittest.skipUnless(_duckdb_available(), "duckdb is not installed")
class TestTradingViewMovePredictionIndustryPacks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output_root = Path(self.temp_dir.name)

    def _build_scan_data(self) -> list[dict]:
        semis = [
            _sample_scan_row(
                f"NASDAQ:SEM{i}",
                name=f"Micron Semi {i} Corp",
                **{"Perf.1M": 5.0 + i},
            )
            for i in range(1, 7)
        ]
        software = [
            _sample_scan_row(
                f"NASDAQ:SW{i}",
                industry="Packaged Software",
                sector="Technology Services",
                **{"Perf.1M": 2.0 + i},
            )
            for i in range(1, 4)
        ]
        return semis + software

    def test_write_industry_packs_from_existing_run(self):
        scan_data = self._build_scan_data()
        suite_result = run_full_analysis_suite_duckdb(
            scan_data=scan_data,
            profile_names=["breakout_long", "quality_value_compounder", "asymmetric_value"],
            min_market_cap_usd=1_000_000_000,
            include_blind_spot_sections=False,
            output_dir=self.output_root,
            export_parquet=False,
            reference_time=datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc),
        )

        pack_result = write_industry_packs_from_duckdb_run(suite_result)

        packs_dir = pack_result["industry_packs_dir"]
        overview_log = pack_result["overview_log"]
        perf_overview_log = pack_result["perf_overview_log"]
        analysis_overview_log = pack_result["analysis_overview_log"]
        self.assertTrue(packs_dir.exists())
        self.assertTrue(overview_log.exists())
        self.assertTrue(perf_overview_log.exists())
        self.assertTrue(analysis_overview_log.exists())

        overview_text = overview_log.read_text(encoding="utf-8")
        self.assertIn("MedScore", overview_text)
        self.assertIn("PkAvg1M", overview_text)
        self.assertIn("PkMed1M", overview_text)
        self.assertIn("TopP1M", overview_text)

        perf_overview_text = perf_overview_log.read_text(encoding="utf-8")
        self.assertIn("perf overview", perf_overview_text.lower())
        self.assertIn("A1M", perf_overview_text)
        self.assertIn("M1Y", perf_overview_text)

        analysis_text = analysis_overview_log.read_text(encoding="utf-8")
        self.assertIn("CatchUp", analysis_text)
        self.assertIn("GateF%", analysis_text)

        semis_meta = pack_result["industries"]["Semiconductors"]
        self.assertEqual(semis_meta["row_count"], 6)
        self.assertTrue(semis_meta["summary_log"].exists())
        self.assertTrue(semis_meta["ranked_by_score_log"].exists())
        self.assertTrue(semis_meta["ranked_by_perf_1m_log"].exists())
        self.assertTrue(semis_meta["peer_rescored"])

        summary_text = semis_meta["summary_log"].read_text(encoding="utf-8")
        self.assertIn("Semiconductors", summary_text)
        self.assertIn("Quick reference", summary_text)

        score_text = semis_meta["ranked_by_score_log"].read_text(encoding="utf-8")
        self.assertIn("BY CONSENSUS SCORE", score_text)
        self.assertIn("peer-local consensus", score_text)
        self.assertIn("Name", score_text)
        self.assertIn("FQSym", score_text)
        self.assertIn("StSafe", score_text)
        self.assertIn("Micron Semi", score_text)

        perf_text = semis_meta["ranked_by_perf_1m_log"].read_text(encoding="utf-8")
        self.assertIn("BY Perf.1M", perf_text)
        self.assertIn("SEM6", perf_text)

    def test_run_full_analysis_suite_duckdb_can_emit_industry_packs_inline(self):
        scan_data = self._build_scan_data()
        result = run_full_analysis_suite_duckdb(
            scan_data=scan_data,
            profile_names=["breakout_long", "quality_value_compounder", "asymmetric_value"],
            min_market_cap_usd=1_000_000_000,
            include_blind_spot_sections=False,
            output_dir=self.output_root / "inline",
            export_parquet=False,
            reference_time=datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc),
            write_industry_packs=True,
        )

        self.assertIn("_industry_packs", result)
        self.assertIn("_consensus_rows", result)
        overview = result["_industry_packs"]["overview_log"]
        self.assertTrue(overview.exists())
        self.assertIn("Semiconductors", overview.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
