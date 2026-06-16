import csv
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    AllFieldsUniverseFilter,
    AllFieldsUniverseFilterClause,
    aggregate_all_fields_pattern_summaries,
    aggregate_all_fields_run_pool,
    analyze_cross_run_close_performance_patterns,
    analyze_period_pooled_performance_patterns,
    analyze_period_rolling_stability,
    analyze_all_fields_run_performance_patterns,
    analyze_all_fields_run_with_forward_returns,
    attach_history_performance_targets,
    benchmark_all_fields_pattern_analysis,
    build_close_forward_pattern_summary,
    classify_columns,
    compute_cross_run_close_returns,
    compute_period_boundary_returns,
    compute_period_close_progression,
    discover_all_fields_daily_databases,
    inventory_all_fields_runs,
    load_field_catalog,
    market_cap_basic_universe_filter,
    move_prediction_universe_filter,
    normalize_all_fields_universe_filter,
    preferred_markets_universe_filter,
    resolve_all_fields_universe_filter_sql,
    resolve_run_id,
    resolve_performance_fields,
    resolve_scan_period_all_field_predictors,
    run_all_fields_pattern_analysis_batch,
    run_scan_period_close_forward_predictor_tracking,
)
from constants.trading_view_constants import PREFERRED_MARKETS


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _write_catalog_csv(path: Path) -> None:
    rows = [
        {
            "Name": "enterprise_value_ebitda_ttm",
            "Display name": "Enterprise Value/EBITDA (TTM)",
            "Type": "number",
            "explanation": "EV to EBITDA ratio.",
            "model_use": "Lower can indicate cheaper valuation.",
        },
        {
            "Name": "price_revenue_ttm",
            "Display name": "Price to Revenue Ratio (TTM)",
            "Type": "number",
            "explanation": "Price to revenue ratio.",
            "model_use": "Lower can indicate better value.",
        },
        {
            "Name": "total_revenue_yoy_growth_ttm",
            "Display name": "Revenue (TTM YoY Growth)",
            "Type": "percent",
            "explanation": "Revenue growth.",
            "model_use": "Higher can indicate growth quality.",
        },
        {
            "Name": "Perf.1M",
            "Display name": "Monthly Performance",
            "Type": "number",
            "explanation": "Monthly trailing performance.",
            "model_use": "Trailing performance metric.",
        },
        {
            "Name": "Perf.3M",
            "Display name": "3-Month Performance",
            "Type": "number",
            "explanation": "Quarterly trailing performance.",
            "model_use": "Trailing performance metric.",
        },
        {
            "Name": "change|1M",
            "Display name": "Change % 1M",
            "Type": "percent",
            "explanation": "Monthly percent change.",
            "model_use": "Trailing change metric.",
        },
        {
            "Name": "close",
            "Display name": "Close",
            "Type": "price",
            "explanation": "Close price.",
            "model_use": "Price field.",
        },
        {
            "Name": "sector",
            "Display name": "Sector",
            "Type": "text",
            "explanation": "Sector label.",
            "model_use": "Grouping field.",
        },
        {
            "Name": "low_fill_metric",
            "Display name": "Low Fill Metric",
            "Type": "number",
            "explanation": "Sparse numeric field.",
            "model_use": "Should fail fill gate.",
        },
        {
            "Name": "mostly_text_metric",
            "Display name": "Mostly Text Metric",
            "Type": "number",
            "explanation": "Non-numeric metric",
            "model_use": "Should fail numeric parse gate.",
        },
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["Name", "Display name", "Type", "explanation", "model_use"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _create_all_fields_test_database(
    database_path: Path,
    *,
    run_id: str = "run_alpha",
    created_at_utc: datetime | None = None,
    row_count: int = 10,
) -> None:
    import duckdb

    created_at = created_at_utc or datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    conn = duckdb.connect(str(database_path))
    try:
        conn.execute(
            """
            CREATE TABLE run_metadata (
                run_id VARCHAR,
                created_at_utc TIMESTAMP,
                run_label VARCHAR,
                suite_name VARCHAR,
                scan_data_count BIGINT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE all_fields_rows (
                run_id VARCHAR,
                row_number BIGINT,
                symbol VARCHAR,
                enterprise_value_ebitda_ttm VARCHAR,
                price_revenue_ttm VARCHAR,
                total_revenue_yoy_growth_ttm VARCHAR,
                "Perf.1M" VARCHAR,
                "Perf.3M" VARCHAR,
                "change|1M" VARCHAR,
                close VARCHAR,
                low_fill_metric VARCHAR,
                mostly_text_metric VARCHAR,
                sector VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO run_metadata VALUES (?, ?, ?, ?, ?)
            """,
            [run_id, created_at, "test_run", "tradingview_all_fields_export_duckdb", row_count],
        )
        rows = []
        for index in range(1, row_count + 1):
            ev_ebitda = float(index) * 2.0
            price_revenue = float(index) * 1.5
            growth = float(index) * 4.0
            perf_1m = 20.0 - float(index)
            perf_3m = 30.0 - float(index) * 1.2
            change_1m = perf_1m * 0.8
            close = 100.0 + float(index)
            low_fill = str(index * 10.0) if index <= 2 else ""
            mostly_text = str(index) if index in (1, 2) else "not_number"
            sector = "Technology" if index <= row_count / 2 else "Healthcare"
            rows.append(
                [
                    run_id,
                    index,
                    f"NASDAQ:SYM{index:03d}",
                    f"{ev_ebitda}",
                    f"{price_revenue}",
                    f"{growth}",
                    f"{perf_1m}",
                    f"{perf_3m}",
                    f"{change_1m}",
                    f"{close}",
                    low_fill,
                    mostly_text,
                    sector,
                ]
            )
        conn.executemany(
            """
            INSERT INTO all_fields_rows VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows,
        )
    finally:
        conn.close()


def _create_history_test_database(path: Path) -> None:
    import duckdb

    conn = duckdb.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE vw_profile_horizon_progression_core (
                profile_name VARCHAR,
                symbol VARCHAR,
                horizon_name VARCHAR,
                close_return_pct_total DOUBLE,
                snapshot_date DATE
            )
            """
        )
        rows = []
        for index in range(1, 11):
            rows.append(
                [
                    "breakout_long",
                    f"SYM{index:03d}",
                    "weeks",
                    float(index) * 1.25,
                    "2026-06-13",
                ]
            )
        conn.executemany(
            """
            INSERT INTO vw_profile_horizon_progression_core VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
    finally:
        conn.close()


def _copy_database_with_day_label(
    source_path: Path,
    parent_dir: Path,
    day_label: str,
    file_name: str | None = None,
) -> Path:
    import shutil

    target_dir = parent_dir / day_label
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / (file_name or f"tradingview_all_fields_{day_label}.duckdb")
    shutil.copy2(source_path, target_file)
    return target_file


def _update_all_fields_column(database_path: Path, column_name: str, expression_sql: str) -> None:
    import duckdb

    conn = duckdb.connect(str(database_path))
    try:
        conn.execute(
            f"""
            UPDATE all_fields_rows
            SET "{column_name}" = CAST({expression_sql} AS VARCHAR)
            """
        )
    finally:
        conn.close()


class TestAllFieldsPatternAnalyzerHelpers(unittest.TestCase):
    def test_discover_daily_databases_filters_by_date_range(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "01_06_2026").mkdir()
            (root / "01_06_2026" / "tradingview_all_fields_01_06_2026.duckdb").write_bytes(b"")
            (root / "07_06_2026").mkdir()
            (root / "07_06_2026" / "tradingview_all_fields_07_06_2026.duckdb").write_bytes(b"")
            (root / "14_06_2026").mkdir()
            (root / "14_06_2026" / "tradingview_all_fields_14_06_2026.duckdb").write_bytes(b"")

            resolved = discover_all_fields_daily_databases(
                all_fields_root=root,
                start_day_label="02_06_2026",
                end_day_label="10_06_2026",
            )
            self.assertEqual(len(resolved), 1)
            self.assertTrue(resolved[0].name.endswith("07_06_2026.duckdb"))

    def test_classify_columns_excludes_perf_and_metadata(self):
        columns = [
            "run_id",
            "row_number",
            "symbol",
            "enterprise_value_ebitda_ttm",
            "price_revenue_ttm",
            "Perf.5D",
            "Perf.1M",
            "Perf.3M",
            "change|1M",
            "close",
            "description",
        ]
        predictors, performance = classify_columns(columns)
        self.assertIn("enterprise_value_ebitda_ttm", predictors)
        self.assertIn("price_revenue_ttm", predictors)
        self.assertNotIn("Perf.1M", predictors)
        self.assertNotIn("change|1M", predictors)
        self.assertIn("Perf.5D", performance)
        self.assertIn("Perf.1M", performance)
        self.assertIn("Perf.3M", performance)
        self.assertNotIn("change|1M", performance)

    def test_resolve_performance_fields_uses_explicit_override(self):
        columns = ["Perf.1M", "change|1M", "close_forward_return_pct"]
        resolved = resolve_performance_fields(
            columns,
            explicit_fields=["change|1M"],
        )
        self.assertEqual(resolved, ["change|1M"])

    def test_load_field_catalog_returns_display_mapping(self):
        with TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "catalog.csv"
            _write_catalog_csv(catalog_path)
            catalog = load_field_catalog(catalog_path)
            entry = catalog.get("enterprise_value_ebitda_ttm")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.display_name, "Enterprise Value/EBITDA (TTM)")

    def test_normalize_universe_filter_accepts_object_and_list_shapes(self):
        field_keyed = normalize_all_fields_universe_filter(
            {"market_cap_basic": {"min": 300_000_000}}
        )
        self.assertEqual(len(field_keyed.clauses), 1)
        self.assertEqual(field_keyed.clauses[0].field, "market_cap_basic")
        self.assertEqual(field_keyed.clauses[0].min_value, 300_000_000.0)

        clause_list = normalize_all_fields_universe_filter(
            [{"field": "price_earnings_ttm", "max": 40.0}]
        )
        self.assertEqual(clause_list.clauses[0].field, "price_earnings_ttm")
        self.assertEqual(clause_list.clauses[0].max_value, 40.0)

        convenience = normalize_all_fields_universe_filter(
            market_cap_basic_universe_filter(500_000_000)
        )
        self.assertEqual(convenience.clauses[0].min_value, 500_000_000.0)

        markets = normalize_all_fields_universe_filter(
            preferred_markets_universe_filter(["america", "uk"])
        )
        self.assertEqual(markets.clauses[0].field, "market")
        self.assertEqual(markets.clauses[0].allowed_values, ("america", "uk"))

        combined = normalize_all_fields_universe_filter(
            move_prediction_universe_filter(min_market_cap_usd=500_000_000)
        )
        self.assertEqual(len(combined.clauses), 2)
        self.assertEqual(
            {clause.field for clause in combined.clauses},
            {"market_cap_basic", "market"},
        )
        preferred_clause = next(
            clause for clause in combined.clauses if clause.field == "market"
        )
        self.assertEqual(
            preferred_clause.allowed_values,
            tuple(str(value).lower() for value in PREFERRED_MARKETS),
        )

    def test_resolve_universe_filter_builds_market_in_sql(self):
        resolution = resolve_all_fields_universe_filter_sql(
            {"market": {"in": ["America", "UK"]}},
            available_columns=["market", "symbol"],
        )
        self.assertIn("'america'", resolution.sql_fragment.lower())
        self.assertIn("'uk'", resolution.sql_fragment.lower())
        self.assertEqual(len(resolution.applied_clauses), 1)
        resolution = resolve_all_fields_universe_filter_sql(
            {"market_cap_basic": {"min": 1_000_000_000}},
            available_columns=["symbol", "close"],
        )
        self.assertEqual(resolution.sql_fragment, "")
        self.assertEqual(len(resolution.applied_clauses), 0)
        self.assertEqual(resolution.skipped_clauses[0][1], "missing_column")

    def test_normalize_universe_filter_ignores_invalid_entries(self):
        resolved = normalize_all_fields_universe_filter(
            {
                "": {"min": 1.0},
                "market_cap_basic": "not-a-mapping",
                "price_book": {},
            }
        )
        self.assertEqual(resolved.clauses, ())


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestAllFieldsPatternAnalyzerCore(unittest.TestCase):
    def test_inventory_and_resolve_run_id(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "all_fields.duckdb"
            _create_all_fields_test_database(db_path, run_id="run_alpha")

            inventory = inventory_all_fields_runs(db_path)
            self.assertEqual(len(inventory), 1)
            self.assertEqual(inventory[0].run_id, "run_alpha")
            self.assertEqual(resolve_run_id(db_path), "run_alpha")

    def test_universe_filter_restricts_rows_before_pattern_analysis(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "all_fields.duckdb"
            catalog_path = temp_path / "catalog.csv"
            import duckdb

            conn = duckdb.connect(str(db_path))
            try:
                conn.execute(
                    """
                    CREATE TABLE run_metadata (
                        run_id VARCHAR,
                        created_at_utc TIMESTAMP,
                        run_label VARCHAR,
                        suite_name VARCHAR,
                        scan_data_count BIGINT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE all_fields_rows (
                        run_id VARCHAR,
                        row_number BIGINT,
                        symbol VARCHAR,
                        market_cap_basic VARCHAR,
                        enterprise_value_ebitda_ttm VARCHAR,
                        "Perf.1M" VARCHAR
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO run_metadata VALUES (
                        'run_alpha',
                        TIMESTAMP '2026-06-13 12:00:00',
                        'test_run',
                        'tradingview_all_fields_export_duckdb',
                        6
                    )
                    """
                )
                rows = [
                    ("run_alpha", 1, "NASDAQ:BIG1", "2000000000", "8.0", "12.0"),
                    ("run_alpha", 2, "NASDAQ:BIG2", "1500000000", "10.0", "11.0"),
                    ("run_alpha", 3, "NASDAQ:BIG3", "1200000000", "12.0", "10.0"),
                    ("run_alpha", 4, "NASDAQ:PENNY1", "50000000", "30.0", "40.0"),
                    ("run_alpha", 5, "NASDAQ:PENNY2", "25000000", "35.0", "45.0"),
                    ("run_alpha", 6, "NASDAQ:PENNY3", "", "40.0", "50.0"),
                ]
                conn.executemany(
                    """
                    INSERT INTO all_fields_rows VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            finally:
                conn.close()

            _write_catalog_csv(catalog_path)
            with catalog_path.open("a", encoding="utf-8-sig", newline="") as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=["Name", "Display name", "Type", "explanation", "model_use"],
                )
                writer.writerow(
                    {
                        "Name": "market_cap_basic",
                        "Display name": "Market Cap",
                        "Type": "number",
                        "explanation": "Market capitalization.",
                        "model_use": "Universe filter field.",
                    }
                )

            unfiltered = analyze_all_fields_run_performance_patterns(
                database_path=db_path,
                performance_fields=["Perf.1M"],
                predictor_fields=["enterprise_value_ebitda_ttm"],
                min_fill_rate=0.0,
                min_numeric_parse_rate=0.0,
                min_pair_n=3,
                write_exports=False,
                field_catalog_csv=catalog_path,
            )
            filtered = analyze_all_fields_run_performance_patterns(
                database_path=db_path,
                performance_fields=["Perf.1M"],
                predictor_fields=["enterprise_value_ebitda_ttm"],
                universe_filter=market_cap_basic_universe_filter(1_000_000_000),
                min_fill_rate=0.0,
                min_numeric_parse_rate=0.0,
                min_pair_n=3,
                write_exports=False,
                field_catalog_csv=catalog_path,
            )
            self.assertEqual(unfiltered.scan_data_count, 6)
            self.assertEqual(filtered.scan_data_count, 3)
            self.assertGreater(len(unfiltered.rows), 0)
            self.assertGreater(len(filtered.rows), 0)
            unfiltered_pair_n = unfiltered.rows[0].pair_n
            filtered_pair_n = filtered.rows[0].pair_n
            self.assertEqual(unfiltered_pair_n, 6)
            self.assertEqual(filtered_pair_n, 3)

    def test_single_run_analysis_tolerates_extreme_predictor_values(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "all_fields.duckdb"
            catalog_path = temp_path / "catalog.csv"
            import duckdb

            conn = duckdb.connect(str(db_path))
            try:
                conn.execute(
                    """
                    CREATE TABLE run_metadata (
                        run_id VARCHAR,
                        created_at_utc TIMESTAMP,
                        run_label VARCHAR,
                        suite_name VARCHAR,
                        scan_data_count BIGINT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE all_fields_rows (
                        run_id VARCHAR,
                        row_number BIGINT,
                        symbol VARCHAR,
                        enterprise_value_ebitda_ttm VARCHAR,
                        extreme_metric VARCHAR,
                        "Perf.1M" VARCHAR
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO run_metadata VALUES (
                        'run_alpha',
                        TIMESTAMP '2026-06-13 12:00:00',
                        'test_run',
                        'tradingview_all_fields_export_duckdb',
                        10
                    )
                    """
                )
                for index in range(1, 11):
                    conn.execute(
                        """
                        INSERT INTO all_fields_rows VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        [
                            "run_alpha",
                            index,
                            f"NASDAQ:SYM{index:03d}",
                            str(float(index) * 2.0),
                            str(1e30 + float(index)),
                            str(20.0 - float(index)),
                        ],
                    )
            finally:
                conn.close()

            _write_catalog_csv(catalog_path)
            with catalog_path.open("a", encoding="utf-8-sig", newline="") as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=["Name", "Display name", "Type", "explanation", "model_use"],
                )
                writer.writerow(
                    {
                        "Name": "extreme_metric",
                        "Display name": "Extreme Metric",
                        "Type": "number",
                        "explanation": "Huge values",
                        "model_use": "Overflow test",
                    }
                )

            result = analyze_all_fields_run_performance_patterns(
                database_path=db_path,
                run_id="run_alpha",
                field_catalog_csv=catalog_path,
                performance_fields=["Perf.1M"],
                predictor_fields=["enterprise_value_ebitda_ttm", "extreme_metric"],
                min_fill_rate=0.10,
                min_numeric_parse_rate=0.80,
                min_pair_n=5,
                write_exports=False,
            )
            self.assertTrue(result.rows)

    def test_single_run_analysis_applies_filters_and_directional_adjustment(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "all_fields.duckdb"
            catalog_path = temp_path / "catalog.csv"
            _create_all_fields_test_database(db_path, run_id="run_alpha")
            _write_catalog_csv(catalog_path)

            result = analyze_all_fields_run_performance_patterns(
                database_path=db_path,
                run_id="run_alpha",
                field_catalog_csv=catalog_path,
                performance_fields=["Perf.1M", "Perf.3M"],
                min_fill_rate=0.30,
                min_numeric_parse_rate=0.80,
                min_pair_n=5,
                field_batch_size=2,
                output_dir=temp_path / "pattern_outputs",
                write_exports=True,
            )

            self.assertTrue(result.rows)
            self.assertNotIn("low_fill_metric", result.predictor_fields)
            self.assertNotIn("low_fill_metric", result.eligible_predictor_fields)
            self.assertIn("mostly_text_metric", result.skipped_predictors)
            self.assertNotIn("Perf.1M", result.eligible_predictor_fields)
            self.assertTrue(Path(result.exports["csv"]).exists())
            self.assertTrue(Path(result.exports["parquet"]).exists())
            self.assertTrue(Path(result.exports["overview_log"]).exists())

            ev_rows = [
                row
                for row in result.rows
                if row.predictor_field == "enterprise_value_ebitda_ttm"
                and row.performance_field == "Perf.1M"
            ]
            self.assertTrue(ev_rows)
            ev_row = ev_rows[0]
            self.assertEqual(ev_row.predictor_direction, "lower_is_better")
            self.assertGreater(ev_row.spearman_corr_adjusted or 0.0, 0.0)
            self.assertIn("trailing_performance_lookahead", ev_row.warnings)
            self.assertIsNotNone(ev_row.pattern_score)
            self.assertGreater(ev_row.pair_n, 0)

    def test_single_run_analysis_supports_parallel_chunks_and_memory_settings(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "all_fields.duckdb"
            catalog_path = temp_path / "catalog.csv"
            _create_all_fields_test_database(db_path, run_id="run_alpha", row_count=25)
            _write_catalog_csv(catalog_path)

            result = analyze_all_fields_run_performance_patterns(
                database_path=db_path,
                run_id="run_alpha",
                field_catalog_csv=catalog_path,
                performance_fields=["Perf.1M", "Perf.3M"],
                min_fill_rate=0.20,
                min_numeric_parse_rate=0.80,
                min_pair_n=5,
                field_batch_size=2,
                max_parallel_chunks=2,
                duckdb_memory_limit="1GB",
                output_dir=temp_path / "parallel_pattern_outputs",
                write_exports=True,
            )
            self.assertTrue(result.rows)
            context_payload = json.loads(
                Path(result.exports["context_json"]).read_text(encoding="utf-8")
            )
            self.assertEqual(2, context_payload["config"]["max_parallel_chunks"])
            self.assertEqual("1GB", context_payload["config"]["duckdb_memory_limit"])

    def test_aggregate_summaries_builds_stability_outputs(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            summary_dir = temp_path / "summaries"
            summary_dir.mkdir(parents=True, exist_ok=True)
            header = [
                "source_database_path",
                "source_day_label",
                "run_id",
                "run_created_at_utc",
                "run_label",
                "scan_data_count",
                "predictor_field",
                "predictor_display_name",
                "predictor_type",
                "predictor_direction",
                "predictor_fill_rate",
                "predictor_numeric_parse_rate",
                "performance_field",
                "performance_display_name",
                "performance_type",
                "pair_n",
                "pearson_corr",
                "spearman_corr",
                "pearson_corr_adjusted",
                "spearman_corr_adjusted",
                "top_quintile_avg_perf",
                "bottom_quintile_avg_perf",
                "quintile_spread",
                "quintile_spread_adjusted",
                "top_quintile_median_perf",
                "bottom_quintile_median_perf",
                "quintile_median_spread",
                "quintile_median_spread_adjusted",
                "performance_stddev",
                "performance_median",
                "predictor_median",
                "above_median_predictor_outperforms_rate",
                "below_median_predictor_outperforms_rate",
                "directional_outperforms_rate",
                "normalized_quintile_spread",
                "pattern_score",
                "warnings_json",
            ]
            for idx, value in enumerate((1.2, 1.4, 1.1), start=1):
                run_dir = summary_dir / f"run_{idx}"
                run_dir.mkdir(parents=True, exist_ok=True)
                csv_path = run_dir / "field_performance_patterns.csv"
                with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
                    writer = csv.DictWriter(csv_file, fieldnames=header)
                    writer.writeheader()
                    writer.writerow(
                        {
                            "source_database_path": f"db_{idx}.duckdb",
                            "source_day_label": f"0{idx}_06_2026",
                            "run_id": f"run_{idx}",
                            "run_created_at_utc": "2026-06-13T00:00:00+00:00",
                            "run_label": "test",
                            "scan_data_count": "10",
                            "predictor_field": "enterprise_value_ebitda_ttm",
                            "predictor_display_name": "Enterprise Value/EBITDA (TTM)",
                            "predictor_type": "number",
                            "predictor_direction": "lower_is_better",
                            "predictor_fill_rate": "1.0",
                            "predictor_numeric_parse_rate": "1.0",
                            "performance_field": "Perf.3M",
                            "performance_display_name": "3-Month Performance",
                            "performance_type": "number",
                            "pair_n": "10",
                            "pearson_corr": "-0.8",
                            "spearman_corr": "-0.8",
                            "pearson_corr_adjusted": "0.8",
                            "spearman_corr_adjusted": "0.8",
                            "top_quintile_avg_perf": "1.0",
                            "bottom_quintile_avg_perf": "-1.0",
                            "quintile_spread": "2.0",
                            "quintile_spread_adjusted": str(value),
                            "top_quintile_median_perf": "1.0",
                            "bottom_quintile_median_perf": "-1.0",
                            "quintile_median_spread": "2.0",
                            "quintile_median_spread_adjusted": str(value),
                            "performance_stddev": "1.0",
                            "performance_median": "0.0",
                            "predictor_median": "10.0",
                            "above_median_predictor_outperforms_rate": "0.2",
                            "below_median_predictor_outperforms_rate": "0.8",
                            "directional_outperforms_rate": "0.8",
                            "normalized_quintile_spread": "0.5",
                            "pattern_score": str(0.4 + idx * 0.1),
                            "warnings_json": "[]",
                        }
                    )

            catalog_path = temp_path / "catalog.csv"
            _write_catalog_csv(catalog_path)
            aggregate_result = aggregate_all_fields_pattern_summaries(
                per_run_summary_dirs=[summary_dir],
                output_dir=temp_path / "aggregate",
                min_runs_for_stability=3,
                require_sign_consistency=0.6,
                field_catalog_csv=catalog_path,
            )
            self.assertTrue(aggregate_result.rows)
            self.assertTrue(
                Path(
                    aggregate_result.exports["cross_run_field_stability_parquet"]
                ).exists()
            )
            self.assertTrue(Path(aggregate_result.exports["top_patterns_report_csv"]).exists())
            first_row = aggregate_result.rows[0]
            self.assertEqual(first_row.runs_seen, 3)
            self.assertGreater(first_row.sign_consistency_ratio or 0.0, 0.6)

    def test_aggregate_summaries_supports_close_forward_summary_injection(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            summary_dir = temp_path / "summaries"
            summary_dir.mkdir(parents=True, exist_ok=True)
            base_summary = summary_dir / "field_performance_patterns.csv"
            with base_summary.open("w", encoding="utf-8", newline="") as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=[
                        "predictor_field",
                        "performance_field",
                        "run_id",
                        "pearson_corr_adjusted",
                        "quintile_spread_adjusted",
                        "pattern_score",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "predictor_field": "enterprise_value_ebitda_ttm",
                        "performance_field": "Perf.1M",
                        "run_id": "run_alpha",
                        "pearson_corr_adjusted": "0.2",
                        "quintile_spread_adjusted": "0.3",
                        "pattern_score": "0.2",
                    }
                )
                writer.writerow(
                    {
                        "predictor_field": "enterprise_value_ebitda_ttm",
                        "performance_field": "Perf.1M",
                        "run_id": "run_beta",
                        "pearson_corr_adjusted": "0.4",
                        "quintile_spread_adjusted": "0.5",
                        "pattern_score": "0.4",
                    }
                )
                writer.writerow(
                    {
                        "predictor_field": "enterprise_value_ebitda_ttm",
                        "performance_field": "Perf.1M",
                        "run_id": "run_gamma",
                        "pearson_corr_adjusted": "0.5",
                        "quintile_spread_adjusted": "0.6",
                        "pattern_score": "0.5",
                    }
                )
            close_summary = summary_dir / "close_forward_patterns.csv"
            with close_summary.open("w", encoding="utf-8", newline="") as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=[
                        "predictor_field",
                        "performance_field",
                        "run_id",
                        "pearson_corr_adjusted",
                        "quintile_spread_adjusted",
                        "pattern_score",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "predictor_field": "price_revenue_ttm",
                        "performance_field": "close_forward_return_pct",
                        "run_id": "run_alpha",
                        "pearson_corr_adjusted": "0.5",
                        "quintile_spread_adjusted": "0.6",
                        "pattern_score": "0.5",
                    }
                )
                writer.writerow(
                    {
                        "predictor_field": "price_revenue_ttm",
                        "performance_field": "close_forward_return_pct",
                        "run_id": "run_beta",
                        "pearson_corr_adjusted": "0.6",
                        "quintile_spread_adjusted": "0.7",
                        "pattern_score": "0.6",
                    }
                )
                writer.writerow(
                    {
                        "predictor_field": "price_revenue_ttm",
                        "performance_field": "close_forward_return_pct",
                        "run_id": "run_gamma",
                        "pearson_corr_adjusted": "0.7",
                        "quintile_spread_adjusted": "0.8",
                        "pattern_score": "0.7",
                    }
                )
            catalog_path = temp_path / "catalog.csv"
            _write_catalog_csv(catalog_path)

            aggregate_result = aggregate_all_fields_pattern_summaries(
                per_run_summary_paths=[base_summary],
                output_dir=temp_path / "aggregate",
                min_runs_for_stability=3,
                require_sign_consistency=0.5,
                field_catalog_csv=catalog_path,
                include_close_forward_return=True,
                close_forward_return_paths=[close_summary],
            )
            self.assertTrue(aggregate_result.rows)
            perf_fields = {row.performance_field for row in aggregate_result.rows}
            self.assertIn("close_forward_return_pct", perf_fields)

    def test_raw_pool_creates_views_and_database(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db1 = temp_path / "day1.duckdb"
            db2 = temp_path / "day2.duckdb"
            _create_all_fields_test_database(
                db1,
                run_id="run_day1",
                created_at_utc=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            )
            _create_all_fields_test_database(
                db2,
                run_id="run_day2",
                created_at_utc=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
            )

            pool_result = aggregate_all_fields_run_pool(
                input_paths=[db1, db2],
                include_predictor_fields=["enterprise_value_ebitda_ttm", "price_revenue_ttm"],
                performance_fields=["Perf.1M"],
                output_dir=temp_path / "pool_output",
                group_by_sector=True,
            )
            pool_db = Path(pool_result["database_path"])
            self.assertTrue(pool_db.exists())

            from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb

            rows = query_move_prediction_duckdb(
                pool_db,
                "SELECT COUNT(*) AS row_count FROM vw_pool_field_quintile_performance",
            )
            self.assertGreater(rows[0]["row_count"], 0)
            sector_rows = query_move_prediction_duckdb(
                pool_db,
                "SELECT COUNT(*) AS row_count FROM vw_pool_sector_field_performance",
            )
            self.assertGreaterEqual(sector_rows[0]["row_count"], 0)

    def test_compute_close_forward_and_cross_run_close_analysis(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            base_db = temp_path / "source.duckdb"
            _create_all_fields_test_database(
                base_db,
                run_id="run_alpha",
                created_at_utc=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
                row_count=15,
            )
            day1_db = _copy_database_with_day_label(base_db, temp_path, "01_06_2026")
            day2_db = _copy_database_with_day_label(base_db, temp_path, "02_06_2026")
            day3_db = _copy_database_with_day_label(base_db, temp_path, "03_06_2026")
            catalog_path = temp_path / "catalog.csv"
            _write_catalog_csv(catalog_path)

            close_returns = compute_cross_run_close_returns(
                input_paths=[day1_db, day2_db, day3_db],
                output_dir=temp_path / "close_returns",
            )
            self.assertTrue(Path(close_returns["parquet_path"]).exists())
            self.assertGreater(close_returns["row_count"], 0)

            close_summary = build_close_forward_pattern_summary(
                close_returns_database_path=close_returns["database_path"],
                output_dir=temp_path / "close_summary",
                field_catalog_csv=catalog_path,
                include_predictor_fields=[
                    "enterprise_value_ebitda_ttm",
                    "price_revenue_ttm",
                ],
            )
            self.assertTrue(Path(close_summary["summary_csv"]).exists())
            self.assertGreater(close_summary["rows_emitted"], 0)

            close_analysis = analyze_cross_run_close_performance_patterns(
                input_paths=[day1_db, day2_db, day3_db],
                output_dir=temp_path / "close_analysis",
                close_forward_days=40,
                field_catalog_csv=catalog_path,
                include_predictor_fields=[
                    "enterprise_value_ebitda_ttm",
                    "price_revenue_ttm",
                ],
            )
            self.assertTrue(Path(close_analysis["summary_csv"]).exists())
            self.assertGreater(close_analysis["rows_emitted"], 0)
            overview_log = Path(close_analysis["output_dir"]) / (
                "_close_forward_pattern_overview.log"
            )
            self.assertTrue(overview_log.exists())
            self.assertIn("Top patterns", overview_log.read_text(encoding="utf-8"))

    def test_close_forward_end_scan_uses_lookahead_database(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            base_db = temp_path / "source.duckdb"
            _create_all_fields_test_database(
                base_db,
                run_id="run_alpha",
                created_at_utc=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
                row_count=12,
            )
            day1_db = _copy_database_with_day_label(base_db, temp_path, "01_06_2026")
            day2_db = _copy_database_with_day_label(base_db, temp_path, "02_06_2026")
            day3_db = _copy_database_with_day_label(base_db, temp_path, "03_06_2026")
            day4_db = _copy_database_with_day_label(base_db, temp_path, "04_06_2026")

            without_lookahead = compute_cross_run_close_returns(
                input_paths=[day1_db, day2_db, day3_db],
                all_fields_root=temp_path,
                end_day_label="03_06_2026",
                output_dir=temp_path / "close_returns_without",
                include_end_scan_forward_return=False,
            )
            with_lookahead = compute_cross_run_close_returns(
                input_paths=[day1_db, day2_db, day3_db],
                all_fields_root=temp_path,
                end_day_label="03_06_2026",
                output_dir=temp_path / "close_returns_with",
                include_end_scan_forward_return=True,
            )

            self.assertEqual(3, without_lookahead["interval_database_count"])
            self.assertEqual(3, without_lookahead["close_return_source_database_count"])
            self.assertEqual(3, with_lookahead["interval_database_count"])
            self.assertEqual(4, with_lookahead["close_return_source_database_count"])
            self.assertEqual(day4_db.as_posix(), with_lookahead["lookahead_database_path"])

            from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb

            end_day_rows = query_move_prediction_duckdb(
                with_lookahead["database_path"],
                """
                SELECT COUNT(*) AS row_count
                FROM cross_run_close_returns
                WHERE source_day_label = '03_06_2026'
                """,
            )
            self.assertGreater(int(end_day_rows[0]["row_count"]), 0)

            no_end_day_rows = query_move_prediction_duckdb(
                without_lookahead["database_path"],
                """
                SELECT COUNT(*) AS row_count
                FROM cross_run_close_returns
                WHERE source_day_label = '03_06_2026'
                """,
            )
            self.assertEqual(0, int(no_end_day_rows[0]["row_count"]))

    def test_attach_history_and_forward_wrapper(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            all_fields_db = temp_path / "all_fields.duckdb"
            history_db = temp_path / "history.duckdb"
            catalog_path = temp_path / "catalog.csv"
            _create_all_fields_test_database(all_fields_db, run_id="run_alpha")
            _create_history_test_database(history_db)
            _write_catalog_csv(catalog_path)

            attached = attach_history_performance_targets(
                database_path=all_fields_db,
                history_database_path=history_db,
                run_id="run_alpha",
                horizon_name="weeks",
                max_day_delta=3,
                output_database_path=temp_path / "joined_targets.duckdb",
            )
            self.assertTrue(Path(attached["database_path"]).exists())
            self.assertGreater(attached["row_count"], 0)

            analysis = analyze_all_fields_run_with_forward_returns(
                database_path=all_fields_db,
                history_database_path=history_db,
                run_id="run_alpha",
                predictor_fields=["enterprise_value_ebitda_ttm", "price_revenue_ttm"],
                output_dir=temp_path / "forward_analysis",
                write_exports=True,
                field_catalog_csv=catalog_path,
                min_fill_rate=0.2,
                min_pair_n=5,
                field_batch_size=2,
            )
            self.assertTrue(analysis.rows)
            self.assertIn("history_forward_return", analysis.performance_fields)
            for row in analysis.rows:
                self.assertNotIn("trailing_performance_lookahead", row.warnings)

    def test_resolve_scan_period_all_field_predictors_uses_classify_columns(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            day1_dir = temp_path / "01_06_2026"
            day1_dir.mkdir(parents=True, exist_ok=True)
            day1_db = day1_dir / "tradingview_all_fields_01_06_2026.duckdb"
            _create_all_fields_test_database(day1_db, run_id="run_a", row_count=20)

            day2_dir = temp_path / "02_06_2026"
            day2_dir.mkdir(parents=True, exist_ok=True)
            day2_db = day2_dir / "tradingview_all_fields_02_06_2026.duckdb"
            _create_all_fields_test_database(day2_db, run_id="run_b", row_count=20)

            catalog_path = temp_path / "catalog.csv"
            _write_catalog_csv(catalog_path)

            resolved = resolve_scan_period_all_field_predictors(
                start_day_label="01_06_2026",
                end_day_label="02_06_2026",
                all_fields_root=temp_path,
                field_catalog_csv=catalog_path,
            )
            self.assertEqual(2, resolved["database_count"])
            self.assertEqual("period_return_pct", resolved["performance_target"])
            self.assertIn("enterprise_value_ebitda_ttm", resolved["predictor_fields"])
            self.assertIn("price_revenue_ttm", resolved["predictor_fields"])
            self.assertIn("total_revenue_yoy_growth_ttm", resolved["predictor_fields"])
            self.assertNotIn("Perf.1M", resolved["predictor_fields"])
            self.assertNotIn("sector", resolved["predictor_fields"])
            self.assertNotIn("close", resolved["predictor_fields"])

    def test_scan_period_close_forward_predictor_tracking(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            day1_dir = temp_path / "01_06_2026"
            day1_dir.mkdir(parents=True, exist_ok=True)
            day1_db = day1_dir / "tradingview_all_fields_01_06_2026.duckdb"
            _create_all_fields_test_database(day1_db, run_id="run_a", row_count=20)

            day2_dir = temp_path / "02_06_2026"
            day2_dir.mkdir(parents=True, exist_ok=True)
            day2_db = day2_dir / "tradingview_all_fields_02_06_2026.duckdb"
            _create_all_fields_test_database(day2_db, run_id="run_b", row_count=20)

            catalog_path = temp_path / "catalog.csv"
            _write_catalog_csv(catalog_path)

            result = run_scan_period_close_forward_predictor_tracking(
                start_day_label="01_06_2026",
                end_day_label="02_06_2026",
                all_fields_root=temp_path,
                output_dir=temp_path / "tracking_root",
                run_lifecycle_id="abc12345",
                close_forward_days=7,
                predictor_fields=[
                    "enterprise_value_ebitda_ttm",
                    "price_revenue_ttm",
                    "total_revenue_yoy_growth_ttm",
                ],
                min_runs_for_stability=2,
                require_sign_consistency=0.5,
                duckdb_threads=4,
                field_batch_size=2,
                max_parallel_runs=1,
                field_catalog_csv=catalog_path,
                write_exports=False,
            )
            tracking_root = temp_path / "tracking_root"
            self.assertEqual(tracking_root.as_posix(), result["output_dir"])
            self.assertTrue(
                Path(result["tracking_overview_json"]).exists()
            )
            self.assertEqual(
                (tracking_root / "_scan_period_close_forward_tracking.json").as_posix(),
                result["tracking_overview_json"],
            )
            self.assertEqual("period_return_pct", result["performance_target"])
            period_total_dir = Path(result["period_total_result"]["output_dir"])
            self.assertEqual(
                (tracking_root / "period_total").resolve(), period_total_dir.resolve()
            )
            self.assertTrue((period_total_dir / "pool").exists())
            self.assertTrue((period_total_dir / "period_returns").exists())
            self.assertGreaterEqual(
                int(result["period_total_result"]["rows_emitted"]), 0
            )
            progression = result["progression_result"]
            self.assertTrue(Path(progression["symbol_progression_parquet"]).exists())
            self.assertTrue(Path(progression["universe_progression_csv"]).exists())
            self.assertTrue(Path(progression["field_quintile_progression_csv"]).exists())
            overview = json.loads(
                Path(result["tracking_overview_json"]).read_text(encoding="utf-8")
            )
            self.assertEqual(3, overview["scan_period_lens"]["predictor_field_count"])
            self.assertEqual("abc12345", overview["run_lifecycle_id"])
            if result.get("aggregate_result"):
                aggregate_db = result["aggregate_result"].get("aggregate_database")
                self.assertIsNotNone(aggregate_db)
                self.assertTrue(
                    str(aggregate_db).startswith(
                        (tracking_root / "aggregates").as_posix()
                    )
                )

    def test_period_boundary_returns_and_progression_outputs(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            base_db = temp_path / "source.duckdb"
            _create_all_fields_test_database(
                base_db,
                run_id="run_alpha",
                created_at_utc=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
                row_count=10,
            )
            _update_all_fields_column(base_db, "close", "100 + row_number * 2")
            day1_db = _copy_database_with_day_label(base_db, temp_path, "01_06_2026")
            day2_db = _copy_database_with_day_label(base_db, temp_path, "02_06_2026")
            day3_db = _copy_database_with_day_label(base_db, temp_path, "03_06_2026")
            _update_all_fields_column(day1_db, "close", "100 + row_number * 1")
            _update_all_fields_column(day2_db, "close", "100 + row_number * 1.5")
            _update_all_fields_column(day3_db, "close", "100 + row_number * 2")

            boundary = compute_period_boundary_returns(
                input_paths=[day1_db, day2_db, day3_db],
                output_dir=temp_path / "period_returns",
                run_lifecycle_id="period01",
            )
            self.assertTrue(Path(boundary["database_path"]).exists())
            self.assertGreater(boundary["row_count"], 0)
            self.assertEqual("01_06_2026", boundary["resolved_boundary_start_day"])
            self.assertEqual("03_06_2026", boundary["resolved_boundary_end_day"])

            progression = compute_period_close_progression(
                input_paths=[day1_db, day2_db, day3_db],
                period_returns_database_path=boundary["database_path"],
                output_dir=temp_path / "progression",
                run_lifecycle_id="period01",
            )
            self.assertTrue(Path(progression["symbol_progression_parquet"]).exists())
            self.assertTrue(Path(progression["universe_progression_csv"]).exists())
            self.assertGreaterEqual(progression["day_count"], 3)
            self.assertGreaterEqual(progression["symbol_count"], 5)

            from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb

            progression_rows = query_move_prediction_duckdb(
                progression["database_path"],
                """
                SELECT source_day_label, symbol, ROUND(cumulative_return_from_start_pct, 2) AS ret
                FROM period_symbol_progression
                WHERE symbol = 'NASDAQ:SYM001'
                ORDER BY run_created_at_utc
                """,
            )
            self.assertEqual(3, len(progression_rows))
            self.assertAlmostEqual(0.0, float(progression_rows[0]["ret"]), places=2)
            self.assertGreater(float(progression_rows[1]["ret"]), 0.0)
            self.assertGreater(float(progression_rows[2]["ret"]), float(progression_rows[1]["ret"]))

    def test_period_pooled_analysis_uses_period_return_target(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            base_db = temp_path / "source.duckdb"
            _create_all_fields_test_database(
                base_db,
                run_id="run_alpha",
                created_at_utc=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
                row_count=12,
            )
            day1_db = _copy_database_with_day_label(base_db, temp_path, "01_06_2026")
            day2_db = _copy_database_with_day_label(base_db, temp_path, "02_06_2026")
            day3_db = _copy_database_with_day_label(base_db, temp_path, "03_06_2026")
            _update_all_fields_column(day1_db, "close", "100 + row_number * 1")
            _update_all_fields_column(day2_db, "close", "100 + row_number * 1.5")
            _update_all_fields_column(day3_db, "close", "100 + row_number * 2")
            catalog_path = temp_path / "catalog.csv"
            _write_catalog_csv(catalog_path)

            period_result = analyze_period_pooled_performance_patterns(
                input_paths=[day1_db, day2_db, day3_db],
                output_dir=temp_path / "period_total",
                field_catalog_csv=catalog_path,
                include_predictor_fields=[
                    "enterprise_value_ebitda_ttm",
                    "price_revenue_ttm",
                    "total_revenue_yoy_growth_ttm",
                ],
                min_fill_rate=0.0,
                min_pair_n=5,
                min_numeric_parse_rate=0.0,
                write_exports=False,
                run_lifecycle_id="period02",
            )
            self.assertTrue(Path(period_result["summary_csv"]).exists())
            self.assertGreaterEqual(period_result["rows_emitted"], 1)
            rows = list(csv.DictReader(Path(period_result["summary_csv"]).open(encoding="utf-8")))
            self.assertTrue(rows)
            performance_fields = {row["performance_field"] for row in rows}
            self.assertEqual({"period_return_pct"}, performance_fields)
            run_ids = {row["run_id"] for row in rows}
            self.assertEqual({"period_pooled_period02"}, run_ids)
            predictor_rows = [
                row for row in rows if row["predictor_field"] == "total_revenue_yoy_growth_ttm"
            ]
            self.assertTrue(predictor_rows)
            self.assertGreater(float(predictor_rows[0]["spearman_corr_adjusted"] or 0.0), 0.0)

    def test_period_rolling_stability_outputs_aggregate(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            base_db = temp_path / "source.duckdb"
            _create_all_fields_test_database(
                base_db,
                run_id="run_alpha",
                created_at_utc=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
                row_count=10,
            )
            day1_db = _copy_database_with_day_label(base_db, temp_path, "01_06_2026")
            day2_db = _copy_database_with_day_label(base_db, temp_path, "02_06_2026")
            day3_db = _copy_database_with_day_label(base_db, temp_path, "03_06_2026")
            day4_db = _copy_database_with_day_label(base_db, temp_path, "04_06_2026")
            _update_all_fields_column(day1_db, "close", "100 + row_number * 1")
            _update_all_fields_column(day2_db, "close", "100 + row_number * 1.5")
            _update_all_fields_column(day3_db, "close", "100 + row_number * 2")
            _update_all_fields_column(day4_db, "close", "100 + row_number * 2.5")
            catalog_path = temp_path / "catalog.csv"
            _write_catalog_csv(catalog_path)

            rolling = analyze_period_rolling_stability(
                all_fields_root=temp_path,
                start_day_label="01_06_2026",
                end_day_label="04_06_2026",
                output_dir=temp_path / "tracking",
                field_catalog_csv=catalog_path,
                include_predictor_fields=[
                    "enterprise_value_ebitda_ttm",
                    "price_revenue_ttm",
                ],
                min_fill_rate=0.0,
                min_pair_n=5,
                min_numeric_parse_rate=0.0,
                rolling_window_days=2,
                rolling_window_step_days=1,
                min_runs_for_stability=2,
                require_sign_consistency=0.0,
                write_exports=False,
                run_lifecycle_id="period03",
            )
            self.assertGreaterEqual(rolling["window_count"], 2)
            self.assertTrue(rolling["summary_paths"])
            self.assertIsNotNone(rolling["aggregate_result"])

    def test_batch_analysis_applies_memory_budgeting(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            day1_dir = temp_path / "01_06_2026"
            day1_dir.mkdir(parents=True, exist_ok=True)
            day1_db = day1_dir / "tradingview_all_fields_01_06_2026.duckdb"
            _create_all_fields_test_database(day1_db, run_id="run_a", row_count=20)

            day2_dir = temp_path / "02_06_2026"
            day2_dir.mkdir(parents=True, exist_ok=True)
            day2_db = day2_dir / "tradingview_all_fields_02_06_2026.duckdb"
            _create_all_fields_test_database(day2_db, run_id="run_b", row_count=20)

            catalog_path = temp_path / "catalog.csv"
            _write_catalog_csv(catalog_path)

            result = run_all_fields_pattern_analysis_batch(
                start_day_label="01_06_2026",
                end_day_label="02_06_2026",
                all_fields_root=temp_path,
                max_parallel_runs=4,
                aggregate_after=False,
                min_scan_data_count=1,
                performance_fields=["Perf.1M"],
                predictor_fields=[
                    "enterprise_value_ebitda_ttm",
                    "price_revenue_ttm",
                    "total_revenue_yoy_growth_ttm",
                ],
                duckdb_threads=8,
                field_batch_size=2,
                max_parallel_chunks=1,
                max_system_memory_gb=8.0,
                memory_reserve_gb=2.0,
                estimated_run_memory_gb=4.0,
                field_catalog_csv=catalog_path,
                write_exports=False,
            )
            overview = json.loads(Path(result["overview_json"]).read_text(encoding="utf-8"))
            self.assertEqual(1, int(overview["effective_parallel_runs"]))
            self.assertGreaterEqual(len(result["successes"]), 1)

    def test_close_forward_analysis_applies_memory_budgeting(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            day1_dir = temp_path / "01_06_2026"
            day1_dir.mkdir(parents=True, exist_ok=True)
            day1_db = day1_dir / "tradingview_all_fields_01_06_2026.duckdb"
            _create_all_fields_test_database(day1_db, run_id="run_a", row_count=20)

            day2_dir = temp_path / "02_06_2026"
            day2_dir.mkdir(parents=True, exist_ok=True)
            day2_db = day2_dir / "tradingview_all_fields_02_06_2026.duckdb"
            _create_all_fields_test_database(day2_db, run_id="run_b", row_count=20)

            catalog_path = temp_path / "catalog.csv"
            _write_catalog_csv(catalog_path)

            result = analyze_cross_run_close_performance_patterns(
                input_paths=[day1_db, day2_db],
                output_dir=temp_path / "close_analysis",
                close_forward_days=7,
                field_catalog_csv=catalog_path,
                include_predictor_fields=[
                    "enterprise_value_ebitda_ttm",
                    "price_revenue_ttm",
                    "total_revenue_yoy_growth_ttm",
                ],
                max_parallel_runs=4,
                duckdb_threads=8,
                duckdb_memory_limit="9GB",
                max_system_memory_gb=8.0,
                memory_reserve_gb=2.0,
                estimated_run_memory_gb=4.0,
                write_exports=False,
            )
            overview = json.loads(Path(result["overview_json"]).read_text(encoding="utf-8"))
            self.assertEqual(1, int(overview["effective_parallel_runs"]))
            self.assertIn("per_worker_threads", overview)
            self.assertIn("per_worker_memory_gb", overview)

    def test_scan_period_tracking_deprecates_close_forward_days(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            day1_dir = temp_path / "01_06_2026"
            day1_dir.mkdir(parents=True, exist_ok=True)
            day1_db = day1_dir / "tradingview_all_fields_01_06_2026.duckdb"
            _create_all_fields_test_database(day1_db, run_id="run_a", row_count=12)

            day2_dir = temp_path / "02_06_2026"
            day2_dir.mkdir(parents=True, exist_ok=True)
            day2_db = day2_dir / "tradingview_all_fields_02_06_2026.duckdb"
            _create_all_fields_test_database(day2_db, run_id="run_b", row_count=12)

            catalog_path = temp_path / "catalog.csv"
            _write_catalog_csv(catalog_path)

            result = run_scan_period_close_forward_predictor_tracking(
                start_day_label="01_06_2026",
                end_day_label="02_06_2026",
                all_fields_root=temp_path,
                output_dir=temp_path / "tracking_root_deprecated",
                run_lifecycle_id="dep77777",
                close_forward_days=21,
                predictor_fields=["enterprise_value_ebitda_ttm"],
                min_runs_for_stability=1,
                require_sign_consistency=0.0,
                duckdb_threads=2,
                field_batch_size=1,
                max_parallel_runs=1,
                field_catalog_csv=catalog_path,
                write_exports=False,
            )
            self.assertEqual("period_return_pct", result["performance_target"])

    def test_benchmark_writer_outputs_metrics_json(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "all_fields.duckdb"
            catalog_path = temp_path / "catalog.csv"
            _create_all_fields_test_database(db_path, run_id="run_alpha", row_count=15)
            _write_catalog_csv(catalog_path)

            benchmark = benchmark_all_fields_pattern_analysis(
                database_path=db_path,
                run_id="run_alpha",
                output_dir=temp_path / "benchmark_out",
                field_catalog_csv=catalog_path,
                performance_fields=["Perf.1M"],
                predictor_fields=[
                    "enterprise_value_ebitda_ttm",
                    "price_revenue_ttm",
                    "total_revenue_yoy_growth_ttm",
                ],
                write_exports=True,
                field_batch_size=2,
            )
            self.assertTrue(Path(benchmark["benchmark_path"]).exists())
            payload = json.loads(
                Path(benchmark["benchmark_path"]).read_text(encoding="utf-8")
            )
            self.assertIn("elapsed_seconds", payload)


if __name__ == "__main__":
    unittest.main()

