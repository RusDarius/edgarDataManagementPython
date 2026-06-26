import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
    AnchorSpec,
    run_backwards_prediction_analysis,
)
from data_analysis_scripts.trading_view_backwards_prediction_progression_viz import (
    ProgressionPoint,
)
from data_analysis_scripts.trading_view_backwards_progression_symbol_plot import (
    BackwardsSymbolCatalogEntry,
    ResolvedSymbolMapping,
    _merge_progression_points_by_anchor,
    fetch_backwards_symbol_catalog,
    load_holdings_exchange_hints,
    plot_backwards_progression_for_symbols,
    resolve_symbols_for_backwards_plot,
)
from db.trading_view_move_prediction_duckdb import MovePredictionDuckDBStore


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _plotly_available() -> bool:
    try:
        import plotly  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _build_weekly_db(
    *,
    temp_root: Path,
    week_name: str,
    run_id: str,
    created_at_utc: datetime,
    profile_name: str,
    rows: list[dict[str, object]],
) -> Path:
    week_dir = temp_root / "duckdb_runs" / "iso_year=2026" / week_name
    week_dir.mkdir(parents=True, exist_ok=True)
    week_suffix = week_name.split("=", maxsplit=1)[-1]
    database_path = week_dir / f"move_prediction_2026_W{week_suffix}.duckdb"

    raw_rows = []
    score_rows = []
    for index, row in enumerate(rows, start=1):
        symbol = str(row["symbol"])
        company = str(row["company"])
        score = float(row["score"])
        close = float(row["close"])
        raw_rows.append([symbol, company, 1.2, close, 1.0, 2.0, 3.0, 4.0])
        score_rows.append(
            {
                "run_id": run_id,
                "profile_name": profile_name,
                "row_number": index,
                "symbol": symbol,
                "company": company,
                "sector": "Materials",
                "industry": "Chemicals",
                "market_cap_basic": 2_000_000_000,
                "close": close,
                "horizon_name": "weeks",
                "score": score,
                "direction": "Up",
                "confidence": 80.0,
                "coverage": 1.0,
                "setup": "test",
                "risk_adjusted_score": score,
                "risk_tier": "medium",
                "manager_action_signal": "hold",
            }
        )

    with MovePredictionDuckDBStore(database_path=database_path) as store:
        store.register_run(
            run_id=run_id,
            created_at_utc=created_at_utc,
            suite_name="tradingview_move_prediction_full_analysis_duckdb",
            scan_data_count=3200,
            profile_names=[profile_name],
            industries=["Materials"],
            min_market_cap_usd=1_000_000_000,
            max_market_cap_usd=None,
            include_blind_spot_sections=False,
        )
        store.append_tabular_output(
            "raw_scan_rows",
            [
                "symbol",
                "Company",
                "relative_volume_10d_calc",
                "close",
                "Perf.5D",
                "Perf.W",
                "Perf.1M",
                "Perf.YTD",
            ],
            raw_rows,
            context={"run_id": run_id},
        )
        store.append_profile_horizon_scores(score_rows)
    return database_path


def _build_backwards_db(temp_root: Path) -> Path:
    current_time = datetime(2026, 6, 24, 14, 0, tzinfo=timezone.utc)
    rows = [
        {"symbol": "NYSE:CF", "company": "CF Industries", "score": 1.0, "close": 80.0},
        {"symbol": "NASDAQ:CF", "company": "Cantor Equity", "score": 0.2, "close": 12.0},
        {"symbol": "NASDAQ:NVDA", "company": "NVIDIA", "score": 1.5, "close": 120.0},
    ]
    _build_weekly_db(
        temp_root=temp_root,
        week_name="week=25",
        run_id="move_prediction_20260618_1400_utc_anchor02",
        created_at_utc=current_time - timedelta(days=6),
        profile_name="breakout_long_v1",
        rows=rows,
    )
    _build_weekly_db(
        temp_root=temp_root,
        week_name="week=26",
        run_id="move_prediction_20260624_1400_utc_current1",
        created_at_utc=current_time,
        profile_name="breakout_long_v1",
        rows=[
            {"symbol": "NYSE:CF", "company": "CF Industries", "score": 1.2, "close": 85.0},
            {"symbol": "NASDAQ:CF", "company": "Cantor Equity", "score": 0.3, "close": 13.0},
            {"symbol": "NASDAQ:NVDA", "company": "NVIDIA", "score": 1.7, "close": 130.0},
        ],
    )
    backwards_result = run_backwards_prediction_analysis(
        current_run_id="move_prediction_20260624_1400_utc_current1",
        duckdb_runs_root=temp_root / "duckdb_runs",
        anchors=[AnchorSpec(name="a1", run_id="move_prediction_20260618_1400_utc_anchor02")],
        output_dir=temp_root / "backwards_output",
        include_consensus=False,
        include_components=False,
    )
    return Path(backwards_result["database_path"])


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestBackwardsProgressionSymbolPlot(unittest.TestCase):
    def test_resolve_cf_with_holdings_hint(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            backwards_db = _build_backwards_db(temp_root)
            catalog = fetch_backwards_symbol_catalog(
                backwards_db,
                profile_name="breakout_long_v1",
                horizon_name="weeks",
            )
            hints = {"CF": "NYSE:CF"}
            mappings, messages = resolve_symbols_for_backwards_plot(
                ["CF", "NVDA"],
                catalog,
                holdings_hints=hints,
                use_tradingview_catalog=False,
            )
            self.assertEqual(messages, [])
            resolved = {mapping.requested: mapping.db_symbol for mapping in mappings}
            self.assertEqual(resolved["CF"], "NYSE:CF")
            self.assertEqual(resolved["NVDA"], "NASDAQ:NVDA")

    def test_resolve_cf_ambiguous_without_hint(self):
        catalog = [
            BackwardsSymbolCatalogEntry("NYSE:CF", "CF", "NYSE", "CF Industries"),
            BackwardsSymbolCatalogEntry("NASDAQ:CF", "CF", "NASDAQ", "Cantor Equity"),
        ]
        with self.assertRaisesRegex(ValueError, "ambiguous bare ticker"):
            resolve_symbols_for_backwards_plot(
                ["CF"],
                catalog,
                holdings_hints={},
                use_tradingview_catalog=False,
            )

    def test_resolve_prefers_snapshot_coverage_over_bare_ticker(self):
        catalog = [
            BackwardsSymbolCatalogEntry("STMPA", "STMPA", None, "STMicro", anchor_count=9),
            BackwardsSymbolCatalogEntry(
                "EURONEXT:STMPA", "STMPA", "EURONEXT", "STMicro", anchor_count=19
            ),
        ]
        mappings, messages = resolve_symbols_for_backwards_plot(
            ["STMPA"],
            catalog,
            use_tradingview_catalog=False,
        )
        self.assertEqual(messages, [])
        self.assertEqual(mappings[0].db_symbol, "EURONEXT:STMPA")
        self.assertEqual(mappings[0].resolution, "snapshot_coverage")

    def test_merge_progression_points_by_anchor_combines_symbol_aliases(self):
        mapping = ResolvedSymbolMapping(
            requested="VEEV",
            db_symbol="NYSE:VEEV",
            bare_ticker="VEEV",
            exchange="NYSE",
            company="Veeva Systems Inc.",
            resolution="snapshot_coverage",
        )
        early = datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc)
        late = datetime(2026, 6, 25, 16, 32, tzinfo=timezone.utc)

        def point(symbol: str, anchor_name: str, point_time: datetime) -> ProgressionPoint:
            return ProgressionPoint(
                backwards_analysis_id="test",
                point_time=point_time,
                anchor_name=anchor_name,
                is_current=False,
                run_id="run",
                snapshot_label="snap",
                profile_name="breakout_long_v1",
                profile_family="breakout_long",
                anchor_profile_name=None,
                horizon_name="weeks",
                symbol=symbol,
                company="Veeva Systems Inc.",
                sector=None,
                industry=None,
                score=1.0,
                close=100.0,
                profile_rank=10,
                direction="up",
                confidence=0.5,
            )

        merged = _merge_progression_points_by_anchor(
            [
                point("NYSE:VEEV", "w2026_23_open", early),
                point("VEEV", "w2026_26_close", late),
            ],
            [mapping],
        )
        self.assertEqual(len(merged), 2)
        by_anchor = {row.anchor_name: row.symbol for row in merged}
        self.assertEqual(by_anchor["w2026_23_open"], "NYSE:VEEV")
        self.assertEqual(by_anchor["w2026_26_close"], "VEEV")

    def test_resolve_exchange_qualified_request(self):
        catalog = [
            BackwardsSymbolCatalogEntry("NYSE:CF", "CF", "NYSE", "CF Industries"),
            BackwardsSymbolCatalogEntry("NASDAQ:CF", "CF", "NASDAQ", "Cantor Equity"),
        ]
        mappings, messages = resolve_symbols_for_backwards_plot(
            ["NYSE:CF"],
            catalog,
            use_tradingview_catalog=False,
        )
        self.assertEqual(messages, [])
        self.assertEqual(mappings[0].db_symbol, "NYSE:CF")
        self.assertEqual(mappings[0].resolution, "exact_match")

    def test_load_holdings_exchange_hints(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            holdings_path = temp_root / "holdings.json"
            holdings_path.write_text(
                """
                {
                  "schema_version": "holdings_scoring_v1",
                  "holdings": [
                    {"ticker": "CF", "symbol": "NYSE:CF", "notes": "CF Industries"}
                  ]
                }
                """,
                encoding="utf-8",
            )
            hints = load_holdings_exchange_hints(holdings_path)
            self.assertEqual(hints["CF"], "NYSE:CF")

    @unittest.skipUnless(_plotly_available(), "plotly not installed")
    def test_plot_backwards_progression_for_symbols_end_to_end(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            backwards_db = _build_backwards_db(temp_root)
            holdings_path = temp_root / "holdings.json"
            holdings_path.write_text(
                """
                {
                  "schema_version": "holdings_scoring_v1",
                  "holdings": [
                    {"ticker": "CF", "symbol": "NYSE:CF"}
                  ]
                }
                """,
                encoding="utf-8",
            )

            result = plot_backwards_progression_for_symbols(
                database_path=backwards_db,
                symbols=["CF", "NVDA"],
                profile_name="breakout_long_v1",
                horizon_name="weeks",
                watchlist_path=holdings_path,
                use_tradingview_catalog=False,
                output_dir=temp_root / "symbol_plots",
                plots_root_dir=temp_root / "plots",
            )

            self.assertEqual(result["resolved_symbols"], ["NYSE:CF", "NASDAQ:NVDA"])
            self.assertTrue(Path(result["html_path"]).exists())
            self.assertTrue(Path(result["symbol_resolution_path"]).exists())
            self.assertGreaterEqual(result["point_count"], 4)

    @unittest.skipUnless(_plotly_available(), "plotly not installed")
    def test_plot_skips_profiles_without_matching_symbols(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            backwards_db = _build_backwards_db(temp_root)

            result = plot_backwards_progression_for_symbols(
                database_path=backwards_db,
                symbols=["CF", "NVDA"],
                profile_names=["breakout_long_v1", "defensive_fortress_v2"],
                horizon_name="weeks",
                use_tradingview_catalog=False,
                output_dir=temp_root / "symbol_plots",
                plots_root_dir=temp_root / "plots",
            )

            self.assertEqual(len(result["profile_plots"]), 1)
            self.assertEqual(
                result["profile_plots"][0]["resolved_profile_name"],
                "breakout_long_v1",
            )
            defensive_messages = [
                message
                for message in result["profile_resolution_messages"]
                if "defensive_fortress" in message
            ]
            self.assertGreaterEqual(len(defensive_messages), 1)


if __name__ == "__main__":
    unittest.main()
