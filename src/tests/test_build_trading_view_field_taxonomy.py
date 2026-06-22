import csv
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_analysis_scripts.trading_view_field_taxonomy_builder import (
    build_trading_view_field_taxonomy,
)


def _write_catalog(path: Path) -> None:
    rows = [
        {
            "Name": "ADRP|1W",
            "Display name": "Average Day Range %",
            "Type": "percent",
            "explanation": "Weekly ADRP",
            "model_use": "Volatility feature",
        },
        {
            "Name": "RSI",
            "Display name": "Relative Strength Index (14)",
            "Type": "number",
            "explanation": "RSI",
            "model_use": "Momentum oscillator",
        },
        {
            "Name": "ex_dividend_date_upcoming",
            "Display name": "Upcoming Ex-Dividend Date",
            "Type": "time",
            "explanation": "Calendar field",
            "model_use": "Structural",
        },
        {
            "Name": "Perf.W",
            "Display name": "Performance Week",
            "Type": "percent",
            "explanation": "Weekly performance",
            "model_use": "Target",
        },
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(rows[0].keys()), quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)


def _write_scan_fixture(scan_root: Path) -> None:
    import duckdb

    period_dir = scan_root / "period_total"
    rolling_dir = scan_root / "rolling_windows" / "window_a"
    period_dir.mkdir(parents=True, exist_ok=True)
    rolling_dir.mkdir(parents=True, exist_ok=True)

    period_parquet = period_dir / "period_field_performance_patterns.parquet"
    rolling_parquet = rolling_dir / "period_field_performance_patterns.parquet"
    stability_dir = scan_root / "aggregates"
    stability_dir.mkdir(parents=True, exist_ok=True)
    stability_parquet = stability_dir / "cross_run_field_stability.parquet"

    conn = duckdb.connect()
    try:
        conn.execute(
            f"""
            COPY (
                SELECT * FROM (VALUES
                    ('ADRP|1W', 'Average Day Range %', 'percent', 'period_return_pct',
                     0.82, 0.31, 11.2, 14.8, 3.6, 1200, 0.95),
                    ('RSI', 'RSI', 'number', 'period_return_pct',
                     -0.15, -0.08, -1.2, 4.0, 5.2, 1100, 0.99)
                ) AS t(
                    predictor_field,
                    predictor_display_name,
                    predictor_type,
                    performance_field,
                    pattern_score,
                    pearson_corr_adjusted,
                    quintile_spread_adjusted,
                    top_quintile_avg_perf,
                    bottom_quintile_avg_perf,
                    pair_n,
                    predictor_fill_rate
                )
            ) TO '{period_parquet.as_posix()}' (FORMAT PARQUET)
            """
        )
        conn.execute(
            f"""
            COPY (
                SELECT * FROM (VALUES
                    ('ADRP|1W', 'period_return_pct', 0.7, 9.0, 0.25),
                    ('RSI', 'period_return_pct', -0.1, -0.8, -0.05)
                ) AS t(
                    predictor_field,
                    performance_field,
                    pattern_score,
                    quintile_spread_adjusted,
                    pearson_corr_adjusted
                )
            ) TO '{rolling_parquet.as_posix()}' (FORMAT PARQUET)
            """
        )
        conn.execute(
            f"""
            COPY (
                SELECT * FROM (VALUES
                    ('ADRP|1W', 'period_return_pct', 1, 0.67, 0.92, 1.4, 1.6, 0.25, 0.24),
                    ('RSI', 'period_return_pct', 1, 0.55, -0.12, -0.8, -0.7, -0.05, -0.04)
                ) AS t(
                    predictor_field,
                    performance_field,
                    runs_seen,
                    sign_consistency_ratio,
                    rank_stability_score,
                    median_quintile_spread,
                    mean_quintile_spread,
                    median_pearson,
                    mean_pearson
                )
            ) TO '{stability_parquet.as_posix()}' (FORMAT PARQUET)
            """
        )
    finally:
        conn.close()

    tracking_payload = {
        "tracking_id": "scan_period_close_forward_tracking_test_abcd1234",
        "run_lifecycle_id": "abcd1234",
        "start_day_label": "29_03_2026",
        "end_day_label": "15_06_2026",
        "performance_target": "period_return_pct",
        "min_runs_for_stability": 1,
        "require_sign_consistency": 0.5,
        "universe_filter": {
            "clauses": [
                {
                    "field": "market_cap_basic",
                    "min_value": 500000000.0,
                    "max_value": None,
                    "allowed_values": [],
                    "excluded_values": [],
                    "exclude_null": True,
                }
            ]
        },
    }
    (scan_root / "_scan_period_close_forward_tracking.json").write_text(
        json.dumps(tracking_payload),
        encoding="utf-8",
    )


class BuildTradingViewFieldTaxonomyTests(unittest.TestCase):
    def test_build_taxonomy_outputs_structure_and_tiers(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            catalog_path = temp_path / "catalog.csv"
            scan_root = temp_path / "scan_run"
            output_dir = temp_path / "taxonomy"
            profile_dir = temp_path / "profiles"
            profile_dir.mkdir()
            (profile_dir / "demo_profile_v1.json").write_text(
                json.dumps(
                    {
                        "profile_id": "demo_profile_v1",
                        "profile": {
                            "component_signal_weights": {
                                "momentum": {"RSI": 1.0},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            _write_catalog(catalog_path)
            _write_scan_fixture(scan_root)

            result = build_trading_view_field_taxonomy(
                field_catalog=catalog_path,
                scan_run_root=scan_root,
                output_dir=output_dir,
                profile_dir=profile_dir,
                stability_min_runs=1,
                sign_consistency_gate=0.5,
            )

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["variant_count"], 4)
            self.assertEqual(manifest["family_count"], 4)
            self.assertTrue((output_dir / "variants" / "master_registry.csv").exists())
            self.assertTrue((output_dir / "families" / "master_registry.csv").exists())
            self.assertTrue(
                (output_dir / "variants" / "by_meaning" / "volatility_risk.csv").exists()
            )
            self.assertTrue(
                (output_dir / "variants" / "by_usage" / "performance_targets.csv").exists()
            )
            self.assertTrue(
                (output_dir / "scan_evidence" / "abcd1234_snapshot_meta.json").exists()
            )
            self.assertEqual(result["tracking_id"], "scan_period_close_forward_tracking_test_abcd1234")

            variant_text = (
                output_dir / "variants" / "master_registry.csv"
            ).read_text(encoding="utf-8")
            self.assertIn("ADRP|1W", variant_text)
            self.assertIn("ex_dividend_date_upcoming", variant_text)

            promote_text = (
                output_dir / "variants" / "by_relevance" / "tier_promote.csv"
            ).read_text(encoding="utf-8")
            self.assertIn("ADRP|1W", promote_text)

            ignore_text = (
                output_dir / "variants" / "by_usage" / "structural_non_tradable.csv"
            ).read_text(encoding="utf-8")
            self.assertIn("ex_dividend_date_upcoming", ignore_text)

            family_text = (output_dir / "families" / "master_registry.csv").read_text(
                encoding="utf-8"
            )
            self.assertIn("ADRP", family_text)
            self.assertIn("recommended_default_variant", family_text)


if __name__ == "__main__":
    unittest.main()
