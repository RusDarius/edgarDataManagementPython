import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_move_prediction_analysis import (
    DIRECTIONAL_MOVE_SCORE_THRESHOLD,
)
from data_analysis_scripts.trading_view_price_driven_score_analysis import (
    PRICE_DRIVEN_DUCKDB_ROOT,
    assign_performance_deciles,
    build_opportunity_candidates,
    build_performance_cohort_maps,
    is_upward_exemplar,
    run_price_driven_score_analysis_duckdb,
)
from db.trading_view_price_driven_score_duckdb import query_price_driven_score_duckdb


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _synthetic_scan_rows(count: int = 10) -> list[dict]:
    rows: list[dict] = []
    for index in range(count):
        perf_change = -5.0 + index * 1.5
        rows.append(
            {
                "symbol": f"NASDAQ:SYM{index:02d}",
                "name": f"Symbol {index:02d}",
                "sector": "Technology Services",
                "industry": "Software",
                "market": "america",
                "market_cap_basic": 2_000_000_000 + index * 100_000_000,
                "close": 40.0 + index,
                "relative_volume_10d_calc": 1.1 + index * 0.05,
                "Value.Traded": 12_000_000 + index * 100_000,
                "change": perf_change,
                "premarket_change": perf_change * 0.5,
                "postmarket_change": perf_change * 0.25,
                "gap": perf_change * 0.1,
                "premarket_gap": perf_change * 0.05,
                "Perf.5D": perf_change * 2.0,
                "Perf.1M": perf_change * 3.0,
                "Perf.W": perf_change,
                "Perf.YTD": perf_change * 4.0,
                "Perf.Y": perf_change * 5.0,
                "price_earnings_ttm": 20.0 + index,
                "total_revenue_yoy_growth_ttm": 10.0 + index,
                "debt_to_equity": 0.3,
            }
        )
    return rows


class TestPerformanceDecileAssignment(unittest.TestCase):
    def test_assigns_worst_and_best_deciles_for_ten_symbols(self):
        rows = _synthetic_scan_rows(10)
        cohort_map = assign_performance_deciles(rows, "change")

        self.assertEqual(len(cohort_map), 10)
        worst = cohort_map["NASDAQ:SYM00"]
        best = cohort_map["NASDAQ:SYM09"]
        self.assertEqual(worst.decile, 1)
        self.assertEqual(best.decile, 10)
        self.assertLess(worst.performance_value, best.performance_value)

    def test_excludes_missing_performance_from_lens_only(self):
        rows = _synthetic_scan_rows(5)
        rows[2]["change"] = None
        cohort_map = assign_performance_deciles(rows, "change")

        self.assertEqual(len(cohort_map), 4)
        self.assertNotIn("NASDAQ:SYM02", cohort_map)

    def test_builds_all_three_lenses(self):
        rows = _synthetic_scan_rows(10)
        cohort_maps = build_performance_cohort_maps(rows)
        self.assertEqual(set(cohort_maps.keys()), {"change", "Perf.5D", "Perf.1M"})
        for cohort_map in cohort_maps.values():
            self.assertEqual(len(cohort_map), 10)


class TestUpwardExemplarDetection(unittest.TestCase):
    def test_long_profile_detects_upward_exemplar(self):
        horizon = {"score": DIRECTIONAL_MOVE_SCORE_THRESHOLD, "direction": "Up"}
        self.assertTrue(
            is_upward_exemplar(
                "breakout_long",
                horizon,
                inverted_profiles=frozenset({"fragility_short"}),
            )
        )

    def test_inverted_profile_never_counts_as_upward_exemplar(self):
        horizon = {"score": 1.5, "direction": "Strong Down"}
        self.assertFalse(
            is_upward_exemplar(
                "fragility_short",
                horizon,
                inverted_profiles=frozenset({"fragility_short"}),
            )
        )


class TestOpportunityRanking(unittest.TestCase):
    def test_worst_decile_with_more_bullish_profiles_ranks_higher(self):
        scan_rows = _synthetic_scan_rows(2)
        cohort_maps = build_performance_cohort_maps(scan_rows)

        profiles_data = {
            "breakout_long": [
                {
                    "row": scan_rows[0],
                    "horizons": {
                        "weeks": {
                            "score": 1.2,
                            "direction": "Strong Up",
                            "risk_adjusted_score": 1.0,
                        }
                    },
                    "components": {},
                },
                {
                    "row": scan_rows[1],
                    "horizons": {
                        "weeks": {
                            "score": 0.1,
                            "direction": "Neutral",
                            "risk_adjusted_score": 0.1,
                        }
                    },
                    "components": {},
                },
            ],
            "value_recovery": [
                {
                    "row": scan_rows[0],
                    "horizons": {
                        "weeks": {
                            "score": 0.9,
                            "direction": "Up",
                            "risk_adjusted_score": 0.8,
                        }
                    },
                    "components": {},
                },
                {
                    "row": scan_rows[1],
                    "horizons": {
                        "weeks": {
                            "score": 1.5,
                            "direction": "Strong Up",
                            "risk_adjusted_score": 1.4,
                        }
                    },
                    "components": {},
                },
            ],
        }
        consensus_rows = [
            {
                "ticker": "NASDAQ:SYM00",
                "company": "Symbol 00",
                "market_cap": scan_rows[0]["market_cap_basic"],
                "row": scan_rows[0],
                "horizons": {
                    "weeks": {
                        "score": 1.0,
                        "direction": "Up",
                        "risk_adjusted_score": 0.9,
                    }
                },
                "components": {},
            },
            {
                "ticker": "NASDAQ:SYM01",
                "company": "Symbol 01",
                "market_cap": scan_rows[1]["market_cap_basic"],
                "row": scan_rows[1],
                "horizons": {
                    "weeks": {
                        "score": 1.2,
                        "direction": "Strong Up",
                        "risk_adjusted_score": 1.1,
                    }
                },
                "components": {},
            },
        ]

        records = build_opportunity_candidates(
            "test_run",
            scan_rows,
            profiles_data,
            consensus_rows,
            cohort_maps,
            ["breakout_long", "value_recovery"],
            inverted_profiles=frozenset({"fragility_short"}),
            long_profiles=frozenset({"breakout_long", "value_recovery"}),
            primary_horizon="weeks",
        )
        change_records = {
            record["symbol"]: record
            for record in records
            if record["performance_lens"] == "change"
        }
        self.assertGreater(
            change_records["NASDAQ:SYM00"]["opportunity_score"],
            change_records["NASDAQ:SYM01"]["opportunity_score"],
        )


@unittest.skipUnless(_duckdb_available(), "duckdb package is not installed")
class TestPriceDrivenScoreDuckDBPersistence(unittest.TestCase):
    def test_run_writes_tables_views_and_storage_layout(self):
        reference_time = datetime(2026, 5, 31, 14, 30, tzinfo=timezone.utc)
        scan_data = _synthetic_scan_rows(10)

        with TemporaryDirectory() as temp_dir:
            result = run_price_driven_score_analysis_duckdb(
                scan_data=scan_data,
                profile_names=["breakout_long"],
                min_market_cap_usd=1_000_000_000,
                output_dir=temp_dir,
                reference_time=reference_time,
                export_parquet=True,
                create_indexes=False,
            )

            period_dir = Path(result["_duckdb_period_dir"])
            self.assertIn("iso_year=2026", str(period_dir))
            self.assertIn("week=22", str(period_dir))
            self.assertTrue(
                Path(result["_duckdb_database"]).name.startswith("price_driven_score_")
            )
            self.assertTrue(Path(result["_duckdb_database"]).exists())
            self.assertTrue(Path(result["_duckdb_overview_log"]).exists())

            tables = query_price_driven_score_duckdb(
                result["_duckdb_database"],
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                ORDER BY table_name
                """,
            )
            table_names = {row["table_name"] for row in tables}
            for expected_table in (
                "performance_cohort_rows",
                "profile_horizon_scores",
                "opportunity_candidates",
                "raw_scan_rows",
                "run_metadata",
            ):
                self.assertIn(expected_table, table_names)

            opportunity_rows = query_price_driven_score_duckdb(
                result["_duckdb_database"],
                """
                SELECT symbol, performance_lens, decile, opportunity_score
                FROM v_opportunity_ranked
                WHERE run_id = ?
                ORDER BY opportunity_rank
                LIMIT 5
                """,
                [result["_duckdb_run_id"]],
            )
            self.assertGreater(len(opportunity_rows), 0)

            parquet_exports = query_price_driven_score_duckdb(
                result["_duckdb_database"],
                """
                SELECT table_name, parquet_path
                FROM parquet_exports
                WHERE run_id = ?
                """,
                [result["_duckdb_run_id"]],
            )
            self.assertGreater(len(parquet_exports), 0)
            self.assertTrue(Path(parquet_exports[0]["parquet_path"]).exists())

    def test_default_storage_root_constant(self):
        self.assertIn("price_driven_score_analysis", str(PRICE_DRIVEN_DUCKDB_ROOT))


if __name__ == "__main__":
    unittest.main()
