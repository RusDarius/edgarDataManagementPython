import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    ALL_FIELDS_SUITE_NAME,
)
from data_analysis_scripts.trading_view_market_flow_screening import (
    GroupFlowRecord,
    SymbolFlowRecord,
    aggregate_group_flows,
    align_snapshot_pairs,
    build_sector_rotation_pairs,
    build_symbol_flow_records,
    compute_symbol_flow_record,
    discover_prior_day_label,
    resolve_day_database,
    run_capital_flow_screening_suite,
    run_market_flow_screening_suite,
    summarize_universe_flows,
)
from db.trading_view_all_fields_duckdb import TradingViewAllFieldsDuckDBStore


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _base_row(**overrides):
    payload = {
        "symbol": "NASDAQ:AAA",
        "name": "Alpha Corp",
        "country": "United States",
        "sector": "Technology",
        "industry": "Semiconductors",
        "market": "america",
        "market_cap_basic": 1_000_000_000,
        "close": 100.0,
        "relative_volume_10d_calc": 1.0,
        "AvgValue.Traded_10d": 10_000_000,
        "Perf.5D": 2.0,
        "Perf.1M": 5.0,
        "SMA50": 95.0,
        "SMA200": 90.0,
        "Volatility.W": 3.0,
    }
    payload.update(overrides)
    return payload


def _write_day_database(
    *,
    root: Path,
    day_label: str,
    run_id: str,
    rows: list[dict],
    created_at_utc: datetime,
) -> Path:
    day_dir = root / day_label
    day_dir.mkdir(parents=True, exist_ok=True)
    database_path = day_dir / f"tradingview_all_fields_{day_label}.duckdb"
    field_names = sorted(
        {
            field_name
            for row in rows
            for field_name in row
            if field_name != "symbol"
        }
    )
    with TradingViewAllFieldsDuckDBStore(database_path=database_path) as store:
        store.begin_transaction()
        try:
            store.register_run(
                run_id=run_id,
                created_at_utc=created_at_utc,
                suite_name=ALL_FIELDS_SUITE_NAME,
                scan_data_count=len(rows),
                profile_names=[],
                industries=None,
                min_market_cap_usd=None,
                max_market_cap_usd=None,
                include_blind_spot_sections=False,
                run_label=day_label,
            )
            store.append_all_fields_rows(
                run_id=run_id,
                field_names=field_names,
                rows=[
                    {
                        "run_id": run_id,
                        "row_number": index + 1,
                        "symbol": row["symbol"],
                        **{field: row.get(field) for field in field_names},
                    }
                    for index, row in enumerate(rows)
                ],
            )
            store.commit()
        except Exception:
            store.rollback()
            raise
    return database_path


class TestSymbolFlowMetrics(unittest.TestCase):
    def test_price_only_move_has_zero_residual(self):
        base = _base_row(close=100.0, market_cap_basic=1_000_000_000)
        compare = _base_row(close=110.0, market_cap_basic=1_100_000_000)
        record = compute_symbol_flow_record(base, compare)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertAlmostEqual(record.price_return_pct or 0.0, 10.0, places=4)
        self.assertAlmostEqual(record.flow_residual_pct or 0.0, 0.0, places=4)
        self.assertAlmostEqual(record.flow_residual_usd or 0.0, 0.0, places=0)

    def test_duckdb_string_values_parse_correctly(self):
        base = _base_row(close="100.0", market_cap_basic="1000000000")
        compare = _base_row(close="110.0", market_cap_basic="1100000000")
        record = compute_symbol_flow_record(base, compare)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertAlmostEqual(record.price_return_pct or 0.0, 10.0, places=4)
        self.assertAlmostEqual(record.flow_residual_pct or 0.0, 0.0, places=4)

    def test_cap_grows_faster_than_price_signals_inflow(self):
        base = _base_row(close=100.0, market_cap_basic=1_000_000_000)
        compare = _base_row(
            close=105.0,
            market_cap_basic=1_200_000_000,
            relative_volume_10d_calc=1.4,
        )
        record = compute_symbol_flow_record(base, compare)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertGreater(record.flow_residual_pct or 0.0, 0.0)
        self.assertEqual(record.signal, "inflow")

    def test_align_snapshot_pairs_matches_bare_symbol(self):
        base_rows = [_base_row(symbol="NASDAQ:AAA")]
        compare_rows = [_base_row(symbol="AAA", close=105.0, market_cap_basic=1_050_000_000)]
        pairs = align_snapshot_pairs(base_rows, compare_rows)
        self.assertEqual(len(pairs), 1)

    def test_share_decomposition_buyback_like(self):
        base = _base_row(
            close=100.0,
            market_cap_basic=1_000_000_000,
            float_shares_outstanding=10_000_000,
        )
        compare = _base_row(
            close=105.0,
            market_cap_basic=1_150_000_000,
            float_shares_outstanding=9_500_000,
            buyback_yield=0.05,
            relative_volume_10d_calc=1.2,
        )
        record = compute_symbol_flow_record(base, compare)
        assert record is not None
        self.assertEqual(record.flow_driver, "buyback_like")
        self.assertEqual(record.signal, "inflow")
        self.assertEqual(record.signal_context, "inflow_buyback")

    def test_share_decomposition_demand_like(self):
        base = _base_row(
            close=100.0,
            market_cap_basic=1_000_000_000,
            float_shares_outstanding=10_000_000,
            relative_volume_10d_calc=1.0,
        )
        compare = _base_row(
            close=105.0,
            market_cap_basic=1_150_000_000,
            float_shares_outstanding=10_000_000,
            relative_volume_10d_calc=1.3,
            **{"ChaikinMoneyFlow|1M": 0.25},
        )
        record = compute_symbol_flow_record(base, compare)
        assert record is not None
        self.assertEqual(record.flow_driver, "demand_like")
        self.assertEqual(record.signal_context, "inflow_confirmed")

    def test_outlier_excluded_from_adjusted_net(self):
        base = _base_row(close=100.0, market_cap_basic=1_000_000_000)
        compare = _base_row(close=400.0, market_cap_basic=4_000_000_000)
        record = compute_symbol_flow_record(base, compare)
        assert record is not None
        self.assertEqual(record.data_quality, "outlier_price")
        self.assertEqual(record.signal_context, "excluded_outlier")
        self.assertEqual(record.flow_residual_usd_adjusted, 0.0)

    def test_raw_signal_unchanged_by_refinement_context(self):
        base = _base_row(close=100.0, market_cap_basic=1_000_000_000)
        compare = _base_row(
            close=105.0,
            market_cap_basic=1_200_000_000,
            relative_volume_10d_calc=1.4,
        )
        record = compute_symbol_flow_record(base, compare)
        assert record is not None
        self.assertEqual(record.signal, "inflow")
        self.assertIn(
            record.signal_context,
            ("inflow_unconfirmed", "inflow_confirmed", "inflow_buyback"),
        )


class TestGroupAggregation(unittest.TestCase):
    def test_rotation_ratio_high_when_groups_offset(self):
        records = [
            SymbolFlowRecord(
                symbol="NASDAQ:AAA",
                company="Alpha",
                country="United States",
                sector="Technology",
                industry="Semiconductors",
                market="america",
                base_market_cap=1_000_000_000,
                compare_market_cap=1_200_000_000,
                price_return_pct=5.0,
                mcap_delta_pct=20.0,
                flow_residual_pct=15.0,
                flow_residual_usd=150_000_000,
                relvol_delta=0.2,
                participation_delta=0.01,
                perf_5d=3.0,
                volatility_w_delta=0.1,
                signal="inflow",
            ),
            SymbolFlowRecord(
                symbol="NYSE:BBB",
                company="Beta",
                country="United States",
                sector="Healthcare",
                industry="Biotechnology",
                market="america",
                base_market_cap=900_000_000,
                compare_market_cap=750_000_000,
                price_return_pct=-5.0,
                mcap_delta_pct=-16.67,
                flow_residual_pct=-11.67,
                flow_residual_usd=-150_000_000,
                relvol_delta=-0.1,
                participation_delta=-0.01,
                perf_5d=-2.0,
                volatility_w_delta=0.2,
                signal="outflow",
            ),
        ]
        industry_groups = aggregate_group_flows(records, "industry")
        summary = summarize_universe_flows(records, industry_groups)
        self.assertAlmostEqual(summary.universe_net_flow_usd, 0.0, places=0)
        self.assertGreater(summary.rotation_ratio, 3.0)
        self.assertEqual(summary.verdict, "rotation_dominant")

    def test_sector_rotation_pairs_prefers_balanced_donor_receiver(self):
        sector_groups = [
            GroupFlowRecord(
                group_name="Technology",
                group_field="sector",
                universe_count=10,
                inflow_count=6,
                outflow_count=2,
                neutral_count=2,
                net_flow_usd=200_000_000,
                cap_weighted_flow_pct=2.0,
                median_flow_residual_pct=1.5,
                participation_intensity=0.2,
            ),
            GroupFlowRecord(
                group_name="Healthcare",
                group_field="sector",
                universe_count=8,
                inflow_count=2,
                outflow_count=5,
                neutral_count=1,
                net_flow_usd=-180_000_000,
                cap_weighted_flow_pct=-1.8,
                median_flow_residual_pct=-1.2,
                participation_intensity=-0.1,
            ),
        ]
        pairs = build_sector_rotation_pairs(sector_groups, limit=3)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].donor_sector, "Healthcare")
        self.assertEqual(pairs[0].receiver_sector, "Technology")
        self.assertAlmostEqual(pairs[0].implied_rotation_usd, 180_000_000, places=0)

    def test_group_adjusted_net_excludes_outliers(self):
        records = [
            SymbolFlowRecord(
                symbol="NASDAQ:OUT",
                company="Outlier",
                country="United States",
                sector="Technology",
                industry="Semiconductors",
                market="america",
                base_market_cap=1_000_000_000,
                compare_market_cap=2_000_000_000,
                price_return_pct=200.0,
                mcap_delta_pct=100.0,
                flow_residual_pct=-100.0,
                flow_residual_usd=-1_000_000_000,
                relvol_delta=0.0,
                participation_delta=0.0,
                perf_5d=0.0,
                volatility_w_delta=0.0,
                signal="outflow",
                data_quality="outlier_price",
                flow_residual_usd_adjusted=0.0,
            ),
            SymbolFlowRecord(
                symbol="NASDAQ:OK",
                company="Normal",
                country="United States",
                sector="Technology",
                industry="Semiconductors",
                market="america",
                base_market_cap=1_000_000_000,
                compare_market_cap=1_100_000_000,
                price_return_pct=5.0,
                mcap_delta_pct=10.0,
                flow_residual_pct=5.0,
                flow_residual_usd=50_000_000,
                relvol_delta=0.1,
                participation_delta=0.0,
                perf_5d=1.0,
                volatility_w_delta=0.0,
                signal="inflow",
                data_quality="ok",
                flow_residual_usd_adjusted=50_000_000,
            ),
        ]
        groups = aggregate_group_flows(records, "industry")
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertAlmostEqual(group.net_flow_usd, -950_000_000, places=0)
        self.assertAlmostEqual(group.net_flow_usd_adjusted, 50_000_000, places=0)
        self.assertEqual(group.outlier_count, 1)


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestMarketFlowScreeningIntegration(unittest.TestCase):
    def test_run_suite_writes_overview_and_exports(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "all_fields"
            output_root = Path(temp_dir) / "output"
            base_rows = [
                _base_row(
                    symbol="NASDAQ:AAA",
                    industry="Semiconductors",
                    sector="Technology",
                    close=100.0,
                    market_cap_basic=2_000_000_000,
                    relative_volume_10d_calc=1.1,
                ),
                _base_row(
                    symbol="NYSE:BBB",
                    name="Beta Corp",
                    industry="Biotechnology",
                    sector="Healthcare",
                    close=50.0,
                    market_cap_basic=1_500_000_000,
                    relative_volume_10d_calc=0.9,
                ),
            ]
            compare_rows = [
                _base_row(
                    symbol="NASDAQ:AAA",
                    industry="Semiconductors",
                    sector="Technology",
                    close=110.0,
                    market_cap_basic=2_400_000_000,
                    relative_volume_10d_calc=1.5,
                    **{"Perf.5D": 8.0, "Perf.1M": 12.0},
                ),
                _base_row(
                    symbol="NYSE:BBB",
                    name="Beta Corp",
                    industry="Biotechnology",
                    sector="Healthcare",
                    close=48.0,
                    market_cap_basic=1_200_000_000,
                    relative_volume_10d_calc=0.7,
                    **{"Perf.5D": -4.0, "Perf.1M": -6.0, "Volatility.W": 4.5},
                ),
            ]
            _write_day_database(
                root=root,
                day_label="29_05_2026",
                run_id="run_base",
                rows=base_rows,
                created_at_utc=datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc),
            )
            _write_day_database(
                root=root,
                day_label="01_06_2026",
                run_id="run_compare",
                rows=compare_rows,
                created_at_utc=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(
                discover_prior_day_label("01_06_2026", all_fields_root=root),
                "29_05_2026",
            )
            self.assertEqual(
                resolve_day_database("01_06_2026", root).name,
                "tradingview_all_fields_01_06_2026.duckdb",
            )

            result = run_market_flow_screening_suite(
                base_day_label="29_05_2026",
                compare_day_label="01_06_2026",
                all_fields_root=root,
                output_root=output_root,
                min_market_cap_usd=500_000_000,
            )
            self.assertTrue(result.overview_log.exists())
            self.assertTrue(result.manifest_path.exists())
            self.assertEqual(len(result.symbol_records), 2)
            symbol_csv = result.output_root / "exports" / "symbol_flow_detail.csv"
            industry_csv = result.output_root / "exports" / "industry_flow_summary.csv"
            self.assertTrue(symbol_csv.exists())
            self.assertTrue(industry_csv.exists())
            self.assertTrue((result.output_root / "macro" / "breadth_snapshot.log").exists())
            self.assertTrue(
                (result.output_root / "macro" / "sector_rotation.log").exists()
            )
            self.assertTrue(
                (result.output_root / "macro" / "flow_quality_summary.log").exists()
            )

            flow_only = run_capital_flow_screening_suite(
                base_day_label="29_05_2026",
                compare_day_label="01_06_2026",
                all_fields_root=root,
                output_root=output_root / "flow_only",
                min_market_cap_usd=500_000_000,
            )
            self.assertGreater(len(flow_only.industry_groups), 0)
            records = build_symbol_flow_records(base_rows, compare_rows)
            self.assertEqual(len(records), 2)


if __name__ == "__main__":
    unittest.main()
