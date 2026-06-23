import csv
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_ticker_field_pattern_scan import (
    _dedupe_events_by_family,
    _resolve_effective_forward_days,
    run_ticker_field_pattern_scan,
)
from db.trading_view_all_fields_duckdb import TradingViewAllFieldsDuckDBStore


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _write_field_catalog(path: Path) -> None:
    rows = [
        {"Name": "close", "Display name": "Close", "Type": "number"},
        {"Name": "Perf.5D", "Display name": "Perf 5D", "Type": "percent"},
        {"Name": "Perf.1M", "Display name": "Perf 1M", "Type": "percent"},
        {"Name": "Perf.3M", "Display name": "Perf 3M", "Type": "percent"},
        {"Name": "volume", "Display name": "Volume", "Type": "number"},
        {
            "Name": "relative_volume_10d_calc",
            "Display name": "Relative Volume",
            "Type": "number",
        },
        {
            "Name": "market_cap_basic",
            "Display name": "Market Cap",
            "Type": "number",
        },
        {"Name": "sector", "Display name": "Sector", "Type": "text"},
        {"Name": "industry", "Display name": "Industry", "Type": "text"},
        {"Name": "type", "Display name": "Type", "Type": "text"},
        {"Name": "is_primary", "Display name": "Is Primary", "Type": "bool"},
        {"Name": "RSI", "Display name": "RSI", "Type": "number"},
        {
            "Name": "price_earnings_ttm",
            "Display name": "P/E TTM",
            "Type": "number",
        },
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Name", "Display name", "Type"])
        writer.writeheader()
        writer.writerows(rows)


def _write_taxonomy_predictors(path: Path) -> None:
    rows = [
        {
            "field_name": "RSI",
            "relevance_tier": "promote",
            "composite_relevance_score": "1.2",
            "predictor_fill_rate": "0.98",
        },
        {
            "field_name": "price_earnings_ttm",
            "relevance_tier": "watch",
            "composite_relevance_score": "0.7",
            "predictor_fill_rate": "0.95",
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "field_name",
                "relevance_tier",
                "composite_relevance_score",
                "predictor_fill_rate",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _create_daily_all_fields_database(
    *,
    all_fields_root: Path,
    day_label: str,
    created_at_utc: datetime,
    rows: list[dict[str, object]],
) -> Path:
    day_dir = all_fields_root / day_label
    day_dir.mkdir(parents=True, exist_ok=True)
    database_path = day_dir / f"tradingview_all_fields_{day_label}.duckdb"
    run_id = f"test_{day_label}_{created_at_utc.strftime('%H%M')}_utc_{day_label[-4:]}"
    field_names = sorted({key for row in rows for key in row if key != "symbol"})

    write_rows: list[dict[str, object]] = []
    for idx, row in enumerate(rows, start=1):
        write_rows.append(
            {
                "run_id": run_id,
                "row_number": idx,
                "symbol": row["symbol"],
                **{key: row.get(key) for key in field_names},
            }
        )

    with TradingViewAllFieldsDuckDBStore(database_path=database_path) as store:
        store.register_run(
            run_id=run_id,
            created_at_utc=created_at_utc,
            suite_name="tradingview_all_fields_export_duckdb",
            scan_data_count=len(rows),
            profile_names=[],
            industries=[],
            min_market_cap_usd=500_000_000,
            max_market_cap_usd=None,
            include_blind_spot_sections=False,
            run_label=day_label,
            run_id_generated=False,
        )
        store.append_all_fields_rows(
            run_id=run_id,
            field_names=field_names,
            rows=write_rows,
        )
    return database_path


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestTickerFieldPatternScan(unittest.TestCase):
    def test_scan_writes_expected_artifacts(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            all_fields_root = temp_root / "all_fields"
            output_root = temp_root / "outputs"
            field_catalog_csv = temp_root / "field_catalog.csv"
            taxonomy_csv = temp_root / "predictor_eligible.csv"
            _write_field_catalog(field_catalog_csv)
            _write_taxonomy_predictors(taxonomy_csv)

            _create_daily_all_fields_database(
                all_fields_root=all_fields_root,
                day_label="01_06_2026",
                created_at_utc=datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc),
                rows=[
                    {
                        "symbol": "NASDAQ:AAA",
                        "close": 100,
                        "Perf.5D": 1.2,
                        "Perf.1M": 4.0,
                        "Perf.3M": 7.5,
                        "volume": 1000,
                        "relative_volume_10d_calc": 1.1,
                        "market_cap_basic": 1_000_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 40,
                        "price_earnings_ttm": 20,
                    },
                    {
                        "symbol": "NASDAQ:BBB",
                        "close": 60,
                        "Perf.5D": 0.5,
                        "Perf.1M": 2.0,
                        "Perf.3M": 4.0,
                        "volume": 700,
                        "relative_volume_10d_calc": 0.9,
                        "market_cap_basic": 900_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 55,
                        "price_earnings_ttm": 18,
                    },
                    {
                        "symbol": "NASDAQ:CCC",
                        "close": 45,
                        "Perf.5D": -1.0,
                        "Perf.1M": -2.0,
                        "Perf.3M": 1.0,
                        "volume": 500,
                        "relative_volume_10d_calc": 0.8,
                        "market_cap_basic": 800_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 48,
                        "price_earnings_ttm": 15,
                    },
                ],
            )
            _create_daily_all_fields_database(
                all_fields_root=all_fields_root,
                day_label="02_06_2026",
                created_at_utc=datetime(2026, 6, 2, 13, 0, tzinfo=timezone.utc),
                rows=[
                    {
                        "symbol": "NASDAQ:AAA",
                        "close": 106,
                        "Perf.5D": 2.3,
                        "Perf.1M": 5.0,
                        "Perf.3M": 8.2,
                        "volume": 2100,
                        "relative_volume_10d_calc": 2.1,
                        "market_cap_basic": 1_050_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 74,
                        "price_earnings_ttm": 22,
                    },
                    {
                        "symbol": "NASDAQ:BBB",
                        "close": 59,
                        "Perf.5D": 0.3,
                        "Perf.1M": 1.5,
                        "Perf.3M": 3.9,
                        "volume": 750,
                        "relative_volume_10d_calc": 1.0,
                        "market_cap_basic": 890_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 57,
                        "price_earnings_ttm": 18.5,
                    },
                    {
                        "symbol": "NASDAQ:CCC",
                        "close": 44,
                        "Perf.5D": -1.5,
                        "Perf.1M": -2.2,
                        "Perf.3M": 0.8,
                        "volume": 520,
                        "relative_volume_10d_calc": 0.85,
                        "market_cap_basic": 790_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 49,
                        "price_earnings_ttm": 14.8,
                    },
                ],
            )
            _create_daily_all_fields_database(
                all_fields_root=all_fields_root,
                day_label="03_06_2026",
                created_at_utc=datetime(2026, 6, 3, 13, 0, tzinfo=timezone.utc),
                rows=[
                    {
                        "symbol": "NASDAQ:AAA",
                        "close": 95,
                        "Perf.5D": -3.0,
                        "Perf.1M": 1.2,
                        "Perf.3M": 6.0,
                        "volume": 2400,
                        "relative_volume_10d_calc": 2.6,
                        "market_cap_basic": 980_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 28,
                        "price_earnings_ttm": 17,
                    },
                    {
                        "symbol": "NASDAQ:BBB",
                        "close": 58,
                        "Perf.5D": -0.2,
                        "Perf.1M": 1.2,
                        "Perf.3M": 3.8,
                        "volume": 730,
                        "relative_volume_10d_calc": 0.95,
                        "market_cap_basic": 880_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 56,
                        "price_earnings_ttm": 18.1,
                    },
                    {
                        "symbol": "NASDAQ:CCC",
                        "close": 43,
                        "Perf.5D": -2.0,
                        "Perf.1M": -2.5,
                        "Perf.3M": 0.5,
                        "volume": 510,
                        "relative_volume_10d_calc": 0.82,
                        "market_cap_basic": 780_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 47,
                        "price_earnings_ttm": 14.5,
                    },
                ],
            )
            _create_daily_all_fields_database(
                all_fields_root=all_fields_root,
                day_label="04_06_2026",
                created_at_utc=datetime(2026, 6, 4, 13, 0, tzinfo=timezone.utc),
                rows=[
                    {
                        "symbol": "NASDAQ:AAA",
                        "close": 112,
                        "Perf.5D": 5.8,
                        "Perf.1M": 7.8,
                        "Perf.3M": 10.9,
                        "volume": 3000,
                        "relative_volume_10d_calc": 3.2,
                        "market_cap_basic": 1_100_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 79,
                        "price_earnings_ttm": 23.5,
                    },
                    {
                        "symbol": "NASDAQ:BBB",
                        "close": 60,
                        "Perf.5D": 0.4,
                        "Perf.1M": 1.7,
                        "Perf.3M": 3.9,
                        "volume": 760,
                        "relative_volume_10d_calc": 1.0,
                        "market_cap_basic": 900_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 58,
                        "price_earnings_ttm": 18.6,
                    },
                    {
                        "symbol": "NASDAQ:CCC",
                        "close": 42,
                        "Perf.5D": -2.4,
                        "Perf.1M": -2.8,
                        "Perf.3M": 0.3,
                        "volume": 500,
                        "relative_volume_10d_calc": 0.8,
                        "market_cap_basic": 770_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 46,
                        "price_earnings_ttm": 14.1,
                    },
                ],
            )

            result = run_ticker_field_pattern_scan(
                ticker="AAA",
                start_day_label="01_06_2026",
                end_day_label="04_06_2026",
                all_fields_root=all_fields_root,
                output_root=output_root,
                field_catalog_csv=field_catalog_csv,
                taxonomy_predictor_csv=taxonomy_csv,
                min_market_cap_usd=500_000_000,
                max_fields=8,
                forward_days=(1, 2),
                min_samples_for_association=2,
            )

            self.assertEqual(result["matched_snapshot_count"], 4)
            self.assertTrue(Path(result["snapshot_progression_csv"]).is_file())
            self.assertTrue(Path(result["field_change_events_csv"]).is_file())
            self.assertTrue(Path(result["field_forward_return_summary_csv"]).is_file())
            self.assertTrue(Path(result["overview_log"]).is_file())
            self.assertTrue(Path(result["context_json"]).is_file())
            self.assertIn("NASDAQ:AAA", result["matched_symbols"])

            with Path(result["snapshot_progression_csv"]).open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                snapshot_rows = list(csv.DictReader(handle))
            self.assertEqual(len(snapshot_rows), 4)
            self.assertNotEqual(snapshot_rows[0]["close_forward_return_1td"], "")

            with Path(result["field_forward_return_summary_csv"]).open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                forward_rows = list(csv.DictReader(handle))
            self.assertTrue(
                any(
                    row.get("field_name") == "RSI" and row.get("horizon_days") == "1"
                    for row in forward_rows
                )
            )

            with Path(result["field_change_events_csv"]).open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                event_rows = list(csv.DictReader(handle))
            self.assertGreater(len(event_rows), 0)

    def test_scan_accepts_prefixed_ticker(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            all_fields_root = temp_root / "all_fields"
            output_root = temp_root / "outputs"
            field_catalog_csv = temp_root / "field_catalog.csv"
            _write_field_catalog(field_catalog_csv)

            _create_daily_all_fields_database(
                all_fields_root=all_fields_root,
                day_label="01_06_2026",
                created_at_utc=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
                rows=[
                    {
                        "symbol": "NASDAQ:AAA",
                        "close": 100,
                        "Perf.5D": 1.0,
                        "Perf.1M": 2.0,
                        "Perf.3M": 3.0,
                        "volume": 1000,
                        "relative_volume_10d_calc": 1.1,
                        "market_cap_basic": 900_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 50,
                        "price_earnings_ttm": 18,
                    },
                    {
                        "symbol": "NASDAQ:BBB",
                        "close": 60,
                        "Perf.5D": 0.5,
                        "Perf.1M": 1.0,
                        "Perf.3M": 1.8,
                        "volume": 700,
                        "relative_volume_10d_calc": 0.9,
                        "market_cap_basic": 800_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 52,
                        "price_earnings_ttm": 16,
                    },
                ],
            )
            _create_daily_all_fields_database(
                all_fields_root=all_fields_root,
                day_label="02_06_2026",
                created_at_utc=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
                rows=[
                    {
                        "symbol": "NASDAQ:AAA",
                        "close": 104,
                        "Perf.5D": 2.2,
                        "Perf.1M": 2.8,
                        "Perf.3M": 4.5,
                        "volume": 1200,
                        "relative_volume_10d_calc": 1.3,
                        "market_cap_basic": 930_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 60,
                        "price_earnings_ttm": 19,
                    },
                    {
                        "symbol": "NASDAQ:BBB",
                        "close": 59,
                        "Perf.5D": 0.2,
                        "Perf.1M": 0.8,
                        "Perf.3M": 1.6,
                        "volume": 690,
                        "relative_volume_10d_calc": 0.95,
                        "market_cap_basic": 790_000_000,
                        "sector": "Technology",
                        "industry": "Software",
                        "type": "stock",
                        "is_primary": "true",
                        "RSI": 53,
                        "price_earnings_ttm": 16.5,
                    },
                ],
            )

            result = run_ticker_field_pattern_scan(
                ticker="NASDAQ:AAA",
                start_day_label="01_06_2026",
                end_day_label="02_06_2026",
                all_fields_root=all_fields_root,
                output_root=output_root,
                field_catalog_csv=field_catalog_csv,
                taxonomy_predictor_csv=None,
                max_fields=5,
                forward_days=(1,),
                min_samples_for_association=1,
            )
            self.assertEqual(result["matched_snapshot_count"], 2)
            self.assertIn("NASDAQ:AAA", result["matched_symbols"])

    def test_trading_day_forward_return_skips_weekend_gap(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            all_fields_root = temp_root / "all_fields"
            output_root = temp_root / "outputs"
            field_catalog_csv = temp_root / "field_catalog.csv"
            _write_field_catalog(field_catalog_csv)

            # Fri 05 Jun 2026 -> Mon 08 Jun 2026 is one trading day forward.
            for day_label, close in (("05_06_2026", 100.0), ("08_06_2026", 110.0)):
                _create_daily_all_fields_database(
                    all_fields_root=all_fields_root,
                    day_label=day_label,
                    created_at_utc=datetime.strptime(day_label, "%d_%m_%Y").replace(
                        tzinfo=timezone.utc
                    ),
                    rows=[
                        {
                            "symbol": "NASDAQ:AAA",
                            "close": close,
                            "Perf.5D": 1.0,
                            "Perf.1M": 2.0,
                            "Perf.3M": 3.0,
                            "volume": 1000,
                            "relative_volume_10d_calc": 1.1,
                            "market_cap_basic": 900_000_000,
                            "sector": "Technology",
                            "industry": "Software",
                            "type": "stock",
                            "is_primary": "true",
                            "RSI": 50,
                            "price_earnings_ttm": 18,
                        },
                        {
                            "symbol": "NASDAQ:BBB",
                            "close": 60,
                            "Perf.5D": 0.5,
                            "Perf.1M": 1.0,
                            "Perf.3M": 1.8,
                            "volume": 700,
                            "relative_volume_10d_calc": 0.9,
                            "market_cap_basic": 800_000_000,
                            "sector": "Technology",
                            "industry": "Software",
                            "type": "stock",
                            "is_primary": "true",
                            "RSI": 52,
                            "price_earnings_ttm": 16,
                        },
                    ],
                )

            result = run_ticker_field_pattern_scan(
                ticker="AAA",
                start_day_label="05_06_2026",
                end_day_label="08_06_2026",
                all_fields_root=all_fields_root,
                output_root=output_root,
                field_catalog_csv=field_catalog_csv,
                taxonomy_predictor_csv=None,
                max_fields=5,
                forward_days=(1,),
                min_samples_for_association=1,
            )

            with Path(result["snapshot_progression_csv"]).open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                snapshot_rows = list(csv.DictReader(handle))
            self.assertAlmostEqual(
                float(snapshot_rows[0]["close_forward_return_1td"]),
                10.0,
            )
            self.assertEqual(snapshot_rows[0]["forward_trading_day_gap_1td"], "1")

    def test_forward_horizon_cap_intersects_requested_values(self):
        effective = _resolve_effective_forward_days(
            (1, 5, 20),
            snapshot_count=6,
            trading_day_span=8,
        )
        self.assertEqual(effective, (1, 5))

    def test_overview_demotes_low_sample_associations(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            all_fields_root = temp_root / "all_fields"
            output_root = temp_root / "outputs"
            field_catalog_csv = temp_root / "field_catalog.csv"
            taxonomy_csv = temp_root / "predictor_eligible.csv"
            _write_field_catalog(field_catalog_csv)
            _write_taxonomy_predictors(taxonomy_csv)

            for day_label, close, rsi in (
                ("01_06_2026", 100.0, 40.0),
                ("02_06_2026", 101.0, 41.0),
                ("03_06_2026", 102.0, 42.0),
            ):
                _create_daily_all_fields_database(
                    all_fields_root=all_fields_root,
                    day_label=day_label,
                    created_at_utc=datetime.strptime(day_label, "%d_%m_%Y").replace(
                        tzinfo=timezone.utc
                    ),
                    rows=[
                        {
                            "symbol": "NASDAQ:AAA",
                            "close": close,
                            "Perf.5D": 1.0,
                            "Perf.1M": 2.0,
                            "Perf.3M": 3.0,
                            "volume": 1000,
                            "relative_volume_10d_calc": 1.1,
                            "market_cap_basic": 900_000_000,
                            "sector": "Technology",
                            "industry": "Software",
                            "type": "stock",
                            "is_primary": "true",
                            "RSI": rsi,
                            "price_earnings_ttm": 18,
                        },
                        {
                            "symbol": "NASDAQ:BBB",
                            "close": 60,
                            "Perf.5D": 0.5,
                            "Perf.1M": 1.0,
                            "Perf.3M": 1.8,
                            "volume": 700,
                            "relative_volume_10d_calc": 0.9,
                            "market_cap_basic": 800_000_000,
                            "sector": "Technology",
                            "industry": "Software",
                            "type": "stock",
                            "is_primary": "true",
                            "RSI": 52,
                            "price_earnings_ttm": 16,
                        },
                    ],
                )

            result = run_ticker_field_pattern_scan(
                ticker="AAA",
                start_day_label="01_06_2026",
                end_day_label="03_06_2026",
                all_fields_root=all_fields_root,
                output_root=output_root,
                field_catalog_csv=field_catalog_csv,
                taxonomy_predictor_csv=taxonomy_csv,
                max_fields=8,
                forward_days=(1,),
                min_samples_for_association=8,
            )

            overview = Path(result["overview_log"]).read_text(encoding="utf-8")
            self.assertIn("Demoted (low sample)", overview)
            self.assertNotIn("Top Forward Associations\n  - RSI", overview)

            with Path(result["field_forward_return_summary_csv"]).open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                forward_rows = list(csv.DictReader(handle))
            self.assertTrue(any(row.get("meets_min_sample") == "False" for row in forward_rows))

    def test_event_dedupe_keeps_highest_score_per_family(self):
        from data_analysis_scripts.trading_view_ticker_field_pattern_scan import (
            FieldChangeEvent,
        )

        events = [
            FieldChangeEvent(
                snapshot_index=0,
                source_day_label="01_06_2026",
                run_id="r1",
                run_created_at_utc="t1",
                matched_symbol="AAA",
                field_name="RSI|15",
                field_value=80.0,
                prior_field_value=50.0,
                field_delta=30.0,
                field_pct_delta=60.0,
                history_zscore=2.5,
                delta_zscore=2.0,
                percentile_rank=0.95,
                ticker_universe_zscore=2.1,
                event_score=4.0,
                event_flags_json='["delta_spike"]',
            ),
            FieldChangeEvent(
                snapshot_index=0,
                source_day_label="01_06_2026",
                run_id="r1",
                run_created_at_utc="t1",
                matched_symbol="AAA",
                field_name="RSI",
                field_value=75.0,
                prior_field_value=50.0,
                field_delta=25.0,
                field_pct_delta=50.0,
                history_zscore=2.0,
                delta_zscore=1.5,
                percentile_rank=0.9,
                ticker_universe_zscore=1.8,
                event_score=2.5,
                event_flags_json='["delta_spike"]',
            ),
        ]
        deduped, removed = _dedupe_events_by_family(
            events,
            {"RSI|15": "RSI", "RSI": "RSI"},
        )
        self.assertEqual(removed, 1)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].field_name, "RSI|15")

    def test_scale_fields_are_excluded_from_event_scoring(self):
        rows = [
            {
                "snapshot_index": 0,
                "source_day_label": "01_06_2026",
                "run_id": "r1",
                "run_created_at_utc": "t1",
                "matched_symbol": "AAA",
                "field_name": "Value.Traded",
                "field_value": 1_000_000.0,
                "percentile_rank": 0.99,
                "ticker_universe_zscore": 4.0,
            },
            {
                "snapshot_index": 1,
                "source_day_label": "02_06_2026",
                "run_id": "r2",
                "run_created_at_utc": "t2",
                "matched_symbol": "AAA",
                "field_name": "Value.Traded",
                "field_value": 2_000_000.0,
                "percentile_rank": 0.99,
                "ticker_universe_zscore": 4.5,
            },
        ]
        from data_analysis_scripts.trading_view_ticker_field_pattern_scan import (
            _annotate_field_metrics,
        )

        events = _annotate_field_metrics(
            field_metric_rows=rows,
            min_event_score=1.0,
        )
        self.assertEqual(events, [])
        self.assertEqual(rows[1]["event_score"], 0.0)
        self.assertIn("scale_field_excluded", rows[1]["event_flags_json"])


if __name__ == "__main__":
    unittest.main()
