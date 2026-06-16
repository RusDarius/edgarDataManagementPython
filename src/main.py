from pathlib import Path
from datetime import datetime, timezone
import uuid

from constants.trading_view_constants import (
    PREFERRED_MARKETS,
    TRADING_VIEW_ALL_MARKETS,
    TRADING_VIEW_ALL_MARKETS_ARRAY,
    TRADING_VIEW_INDUSTRIES,
)
from data_analysis_scripts.sherwood_news_feed_gather import (
    fetch_and_log_full_articles,
    gather_sherwood_markets_feed,
    load_sherwood_feed_as_text,
)
from data_analysis_scripts.trading_view_activity_float_attention import (
    run_activity_float_attention_scan,
    run_activity_float_attention_scan_grouped_industries,
)
from data_analysis_scripts.trading_view_cross_scanner_aggregator import (
    run_cross_scanner_aggregate,
)
from data_analysis_scripts.trading_view_export_all_tdfields import (
    export_all_tradingview_fields,
    export_all_tradingview_fields_duckdb,
)
from data_analysis_scripts.trading_view_move_prediction_batch_pattern_analysis import (
    run_batch_prediction_pattern_analysis,
)
from data_analysis_scripts.trading_view_move_prediction_multi_run_pool_aggregator import (
    run_move_prediction_run_pool_aggregation,
)
from data_analysis_scripts.trading_view_move_prediction_pool_analyzer import (
    analyze_pool_database,
    export_analysis_reports,
    run_pool_analysis_from_path,
)
from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    analyze_all_fields_run_performance_patterns,
    analyze_cross_run_close_performance_patterns,
    benchmark_all_fields_pattern_analysis,
    compute_cross_run_close_returns,
    resolve_performance_fields,
    run_all_fields_multi_day_pattern_suite,
    run_all_fields_pattern_analysis_batch,
    run_scan_period_close_forward_predictor_tracking,
    market_cap_basic_universe_filter,
    preferred_markets_universe_filter,
)
from data_analysis_scripts.trading_view_priceperf_analysis import (
    analyze_global_price_performance,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite,
    run_full_analysis_suite_duckdb,
    run_full_analysis_suite_from_raw_csv_folders,
    run_full_analysis_suite_with_earnings_priority,
    run_full_analysis_suite_with_earnings_priority_duckdb,
    run_move_prediction_profile_suite,
    run_move_prediction_scan,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MOVE_PREDICTION_PROFILE_SUITE_BASELINE = (
    PROJECT_ROOT / "config" / "move_prediction_profiles" / "suites" / "baseline.json"
)
MOVE_PREDICTION_PROFILE_SUITE_SWING_REVERSAL_V1 = (
    PROJECT_ROOT
    / "config"
    / "move_prediction_profiles"
    / "suites"
    / "swing_reversal_v1.json"
)
MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V2 = (
    PROJECT_ROOT
    / "config"
    / "move_prediction_profiles"
    / "suites"
    / "active_manager_v2.json"
)
PREDICTION_MODULE2_SUITE_ACTIVE_MANAGER_V1 = (
    PROJECT_ROOT
    / "config"
    / "prediction_module2_profiles"
    / "suites"
    / "active_manager_module2_v1.json"
)
from data_analysis_scripts.analysis_prediction_module2 import run_module2_suite_duckdb
from data_analysis_scripts.trading_view_move_prediction_duckdb_backfill import (
    replay_historical_raw_csvs_into_duckdb_runs,
)
from data_analysis_scripts.trading_view_move_prediction_history_aggregator import (
    build_move_prediction_history_duckdb_inputs_from_week_folders,
    build_move_prediction_history_inputs_from_folder_names,
    run_move_prediction_history_aggregation_duckdb,
    run_move_prediction_history_aggregation,
)
from data_analysis_scripts.trading_view_safety_check_v1 import run_safety_core_scan
from data_analysis_scripts.trading_view_targets_analysis import run_targets_scan
from data_analysis_scripts.trading_view_valuation_analysis import (
    analyze_ev_ebitda_deviation,
)
from data_loaders.api_bvbdata import ApiBvbClient
from data_loaders.api_tradingview_client import ApiTradingViewClient
from database_scripts.insert_trading_view_company_data import (
    load_tradingview_company_data,
)
from database_scripts.split_large_csv_by_rows import split_csv_by_rows
from db.bvb_tickers_operations import (
    get_bvb_tickers_filtered,
    insert_bvb_ticker_entries,
)
from db.trading_view_company_data_map_operations import (
    get_trading_view_company_by_symbol,
)
from portofolio_integration_analysis.portfolio_tracker import PortfolioTracker
from portofolio_integration_analysis.portfolio_analysis_output import (
    export_portfolio_analysis,
)

USER_AGENT = "Barnnabass daniOO7XbX@gmail.com"
TRADINGVIEW_API_CLIENT = ApiTradingViewClient(user_agent=USER_AGENT)
BVB_API_CLIENT = ApiBvbClient(user_agent=USER_AGENT)


def _build_non_overriding_duckdb_run_label(prefix: str = "market_snapshot") -> str:
    """Build a human-readable run_label that stays unique across same-day runs.

    DuckDB writer replacement happens only when run_id matches exactly. Because
    run_label is used as run_id, include UTC minute and a short random suffix to
    avoid accidental same-day collisions while keeping labels easy to scan.
    """
    timestamp_utc = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M")
    return f"{prefix}_{timestamp_utc}_utc_{uuid.uuid4().hex[:6]}"


def run_portfolio_bootstrap_example() -> dict[str, object]:
    """Example flow for loading TradingView companies, opening dated positions,
    syncing market snapshots, and exporting portfolio metrics.

    The price and amount inputs in the sample entries are assumed to be in the
    same currency unit. When that unit differs from the portfolio currency, pass
    an FX rate into ``add_holding_by_amount``. A future FX API can replace the
    manual rate.
    """

    tracker = PortfolioTracker(
        portfolio_name="TV Date Aware Demo",
        description="Example portfolio using money-sized dated entries.",
        currency="USD",
        benchmark_symbol="SPY",
    )

    sample_entries = [
        {
            "symbol": "NVDA",
            "avg_price": 875.00,
            "date_open": datetime(2025, 12, 18, 14, 30),
            "amount_money": 8750.00,
            "unit": "USD",
        },
        {
            "symbol": "MSFT",
            "avg_price": 420.00,
            "date_open": datetime(2025, 11, 5, 14, 30),
            "amount_money": 8400.00,
            "unit": "USD",
        },
        {
            "symbol": "ASML",
            "avg_price": 820.00,
            "date_open": datetime(2025, 10, 2, 14, 30),
            "amount_money": 8200.00,
            "unit": "USD",
        },
    ]

    opened_positions: list[dict[str, object]] = []
    for entry in sample_entries:
        company_row = get_trading_view_company_by_symbol(entry["symbol"])
        if company_row is None:
            print(
                f"Skipping {entry['symbol']}: no internal_id found after company load."
            )
            continue
        position_row = tracker.add_holding_by_amount(
            internal_id=int(company_row["internal_id"]),
            avg_price=float(entry["avg_price"]),
            date_open=entry["date_open"],
            amount_money=float(entry["amount_money"]),
            unit=str(entry["unit"]),
            notes=f"Bootstrapped from main.py example for {entry['symbol']}",
        )
        opened_positions.append(position_row)

    scan_data = TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
        min_market_cap_usd=1_000_000_000,
        markets=PREFERRED_MARKETS,
    ).get("data", [])
    snapshot_result = tracker.sync_market_snapshots(
        scan_data=scan_data,
        symbol_key="ticker-view",
    )
    print(f"Snapshot sync result: {snapshot_result}")

    summary, metrics = tracker.get_summary()
    tracker.print_summary()
    export_paths = export_portfolio_analysis(tracker)
    print(f"Portfolio analysis exported to: {export_paths}")

    return {
        "opened_positions": opened_positions,
        "snapshot_result": snapshot_result,
        "summary": summary,
        "metrics": metrics,
        "export_paths": export_paths,
    }


def run_move_prediction_history_aggregation_example() -> dict[str, object]:
    """Example flow for aggregating profile progression across selected day folders.

    Each folder name below is resolved under the chosen history root and is
    expected to contain both ``marketOpen`` and ``marketClose`` subfolders.
    """

    history_root = Path(
        r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\prediction_analysis\AllInUniverse_min1bil"
    )
    included_snapshot_folders = [
        "30_03_2026",
        "31_03_2026",
        "01_04_2026",
        "02_04_2026",
        "06_04_2026",
        "07_04_2026",
        "08_04_2026",
        "09_04_2026",
        "10_04_2026",
        "13_04_2026",
        "14_04_2026",
        "15_04_2026",
        "16_04_2026",
        "17_04_2026",
        "20_04_2026",
        "21_04_2026",
        "22_04_2026",
        "23_04_2026",
        "24_04_2026",
        "27_04_2026",
        "28_04_2026",
        "29_04_2026",
        "30_04_2026",
        "01_05_2026",
        "04_05_2026",
        "05_05_2026",
        "06_05_2026",
        "07_05_2026",
        "08_05_2026",
        "11_05_2026",
        "12_05_2026",
        "13_05_2026",
        "14_05_2026",
        "15_05_2026",
        "18_05_2026",
        "19_05_2026",
        "20_05_2026",
        "21_05_2026",
        "22_05_2026",
        "26_05_2026",
        "27_05_2026",
        "28_05_2026",
        "29_05_2026",
    ]

    input_paths = build_move_prediction_history_inputs_from_folder_names(
        base_dir=history_root,
        folder_names=included_snapshot_folders,
    )
    output_dir = (
        history_root
        / "history_aggregations"
        / "all_in_universe_min1bil__30_03_2026__29_05_2026"
    )

    aggregation_result = run_move_prediction_history_aggregation(
        input_paths=input_paths,
        output_dir=output_dir,
        # include_profiles=["breakout_long", "quality_value_compounder"],
    )
    print(f"History aggregation written to: {aggregation_result['output_dir']}")
    print(f"Manifest CSV: {aggregation_result['manifest_csv']}")
    print(f"Combined summary CSV: {aggregation_result['summary_csv']}")

    breakout_summary = (
        aggregation_result["profiles"].get("breakout_long", {}).get("summary_csv")
    )
    if breakout_summary is not None:
        print(f"Breakout long summary CSV: {breakout_summary}")

    return aggregation_result


def run_move_prediction_history_aggregation_duckdb_example() -> dict[str, object]:
    """Example flow for aggregating stored DuckDB move-prediction runs.

    Each folder name below is resolved under the chosen ``iso_year`` root and is
    expected to contain exactly one weekly ``move_prediction_YYYY_WWW.duckdb``
    file. Individual run folders under ``week=WW/runs/`` (live runs such as
    ``move_prediction_...`` or backfill runs such as
    ``raw_csv_duckdb_backfill_backfill_...``) are not scanned directly;
    aggregation reads ``run_metadata`` and ``profile_prediction_rows`` from the
    weekly database, so mixed run-id naming does not affect the analysis.
    The output is a DuckDB-native historical analysis dataset under
    ``duckdb_runs/historical_prediction_analysis`` plus a human-readable
    overview log. Legacy CSV duplication is disabled by default.
    """

    duckdb_history_root = Path(
        r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\prediction_analysis\duckdb_runs\iso_year=2026"
    )
    included_week_folders = [
        "week=22",
        "week=23",
    ]

    input_paths = build_move_prediction_history_duckdb_inputs_from_week_folders(
        base_dir=duckdb_history_root,
        folder_names=included_week_folders,
    )
    output_root = duckdb_history_root.parent / "historical_prediction_analysis"

    aggregation_result = run_move_prediction_history_aggregation_duckdb(
        input_paths=input_paths,
        output_dir=output_root,
        write_legacy_csv_outputs=False,
        export_parquet=False,
        # include_profiles=["breakout_long", "quality_value_compounder"],
    )
    print(f"DuckDB history aggregation written to: {aggregation_result['output_dir']}")
    print(f"Analysis run id: {aggregation_result['analysis_run_id']}")
    print(f"Analysis DuckDB: {aggregation_result['analysis_database']}")
    print(f"Manifest table: {aggregation_result['manifest_table']}")
    print(f"Combined history table: {aggregation_result['history_table']}")
    print(f"Combined summary table: {aggregation_result['summary_table']}")
    print(f"Overview log: {aggregation_result['overview_log']}")
    print("Starter views: " + ", ".join(aggregation_result.get("analysis_views") or []))

    breakout_summary_table = (
        aggregation_result["profiles"].get("breakout_long", {}).get("summary_table")
    )
    if breakout_summary_table is not None:
        print(f"Breakout long summary table: {breakout_summary_table}")

    return aggregation_result


# Main entry point for running workflows and data loaders.
def main():
    # analyze_global_price_performance(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_world_market_all_priceperf_metrics().get(
    #         "data", []
    #     ),
    #     min_market_cap_usd=1_000_000_000,
    #     min_performance_pct={
    #         "Perf.All": 10.0,
    #         "Perf.10Y": 10.0,
    #         "Perf.5Y": 10.0,
    #         "Perf.3Y": 10.0,
    #         "Perf.1Y": 10.0,
    #         "Perf.6M": 10.0,
    #         "Perf.YTD": 10.0,
    #         "Perf.1M": 10.0,
    #         "Perf.W": 10.0,
    #         "Perf.5D": 10.0,
    #     },
    # )

    # run_safety_core_scan(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_safety_core(
    #         min_market_cap_usd=1_000_000_000,
    #     ).get("data", []),
    #     min_market_cap_usd=1_000_000_000,
    # )

    # analyze_ev_ebitda_deviation(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_by_industry(
    #         min_market_cap_usd=1_000_000_000,
    #         industries=[TRADING_VIEW_INDUSTRIES.HOTELS_RESORTS_CRUISE_LINES],
    #     ).get("data", []),
    #     industries=[TRADING_VIEW_INDUSTRIES.HOTELS_RESORTS_CRUISE_LINES],
    # )

    # Replay the current model against prior all-fields CSV exports (legacy CSV
    # output path). The runner expects each dated folder to contain
    # tradingview_global_all_tdfields_*.csv and writes profile/horizon/date
    # snapshots under prediction_analysis/raw_csv_backscan.
    #
    # run_full_analysis_suite_from_raw_csv_folders(
    #     raw_data_folders=[
    #         Path(
    #             r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\trading_view_all_fields_data\01_04_2026"
    #         ),
    #     ],
    #     min_market_cap_usd=1_000_000_000,
    #     include_blind_spot_sections=False,
    # )

    # # # # Model Analysis scan with duckdb storage solution
    # move_prediction_scan_response = (
    #     TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    #         min_market_cap_usd=1_000_000_000,
    #         markets=PREFERRED_MARKETS,
    #     )
    # )
    # # Default (omit profile_suite_path): built-in 10-profile baseline — see DEFAULT_MOVE_PREDICTION_PROFILE_SUITE.
    # # Extended lenses: profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V2
    # # Swing-reversal calibration only: profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_SWING_REVERSAL_V1
    # base_duckdb_result = run_full_analysis_suite_duckdb(
    #     scan_data=move_prediction_scan_response,
    #     min_market_cap_usd=1_000_000_000,
    #     include_blind_spot_sections=True,
    #     profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V2,
    # )
    # run_full_analysis_suite_with_earnings_priority_duckdb(
    #     scan_data=move_prediction_scan_response,
    #     min_market_cap_usd=1_000_000_000,
    #     include_blind_spot_sections=True,
    #     base_result=base_duckdb_result,
    #     profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V2,
    # )

    # Module2 orthogonal outlook scoring (separate from v1 move-prediction profiles).
    # Output guide: documentation/prediction_module2_run_output_guide.md
    # Run output: logs/tradingview_analysis/prediction_module2/duckdb_runs/iso_year=.../week=.../runs/<run_id>/
    # move_prediction_scan_response = (
    #     TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    #         min_market_cap_usd=1_000_000_000,
    #         markets=PREFERRED_MARKETS,
    #     )
    # )
    # run_module2_suite_duckdb(
    #     scan_data=move_prediction_scan_response,
    #     min_market_cap_usd=1_000_000_000,
    #     profile_suite_path=PREDICTION_MODULE2_SUITE_ACTIVE_MANAGER_V1,
    # )

    # ALL FIELDS DUCKDB EXPORT
    # export_all_tradingview_fields_duckdb()

    # all-view trading view data analysis
    #
    # --- One-week pilot (5+ daily DuckDB files required under all_fields_root) ---
    # Trailing Perf.* (Perf.5D … Perf.YTD) + cross-run stability + actual close forward returns.
    # duckdb_memory_limit is adjusted depending on max_parallel_runs - max_parallel_runs x duckdb_memory_limit
    # run_all_fields_multi_day_pattern_suite(
    #     start_day_label="08_06_2026",
    #     end_day_label="12_06_2026",
    #     close_forward_days=7,
    #     min_runs_for_stability=3,
    #     max_parallel_runs=3,
    #     duckdb_threads=20,
    #     field_batch_size=100,
    #     max_parallel_chunks=1,
    #     duckdb_memory_limit="9GB",
    #     max_system_memory_gb=30.0,
    #     memory_reserve_gb=4.0,
    #     performance_fields=["Perf.1M", "Perf.3M"],
    # )

    # --- Scan-period predictor tracking (whole-period close performance) ---
    # Pools all eligible fields across the period; performance target is period_return_pct.
    # Predictors are anchored at period start and correlated against start->end close return.
    # Progression exports:
    #   progression/period_symbol_progression.parquet
    #   progression/period_universe_progression.csv
    #   progression/period_field_quintile_progression.csv
    # Fast pilot option for faster runtime:
    # - narrow date window
    # - explicit predictor_fields subset (20-50 fields)
    # - stricter gates (min_fill_rate=0.20, min_pair_n=50)
    run_scan_period_close_forward_predictor_tracking(
        start_day_label="29_03_2026",
        end_day_label="15_06_2026",
        min_runs_for_stability=3,
        max_parallel_runs=4,
        duckdb_threads=6,
        field_batch_size=120,
        max_parallel_chunks=1,
        duckdb_memory_limit="10GB",
        max_system_memory_gb=30.0,
        memory_reserve_gb=4.0,
        universe_filter={
            **market_cap_basic_universe_filter(500_000_000),
            **preferred_markets_universe_filter(PREFERRED_MARKETS),
        },
    )

    # # --- Single day (smoke / one snapshot) ---
    # analyze_all_fields_run_performance_patterns(
    #     database_path=Path(
    #         r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\trading_view_all_fields_data\12_06_2026\tradingview_all_fields_12_06_2026.duckdb"
    #     ),
    #     duckdb_threads=6,
    #     field_batch_size=100,
    #     max_parallel_chunks=4,
    #     duckdb_memory_limit="9GB",
    #     universe_filter={
    #         **market_cap_basic_universe_filter(500_000_000),
    #         **preferred_markets_universe_filter(PREFERRED_MARKETS),
    #     },
    # )

    # run_all_fields_pattern_analysis_batch(
    #     start_day_label="01_03_2026",
    #     end_day_label="13_06_2026",
    #     duckdb_threads=20,
    #     field_batch_size=80,
    #     max_parallel_runs=2,
    #     max_parallel_chunks=1,
    #     duckdb_memory_limit="28GB",
    #     max_system_memory_gb=30.0,
    #     memory_reserve_gb=4.0,
    # )
    # benchmark_all_fields_pattern_analysis(
    #     database_path=Path(
    #         r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\trading_view_all_fields_data\12_06_2026\tradingview_all_fields_12_06_2026.duckdb"
    #     ),
    #     duckdb_threads=20,
    #     field_batch_size=80,
    #     max_parallel_chunks=1,
    #     duckdb_memory_limit="28GB",
    # )
    # close_forward = compute_cross_run_close_returns(
    #     start_day_label="01_03_2026",
    #     end_day_label="13_06_2026",
    #     max_forward_days=40,
    # )
    # analyze_cross_run_close_performance_patterns(
    #     start_day_label="01_03_2026",
    #     end_day_label="13_06_2026",
    #     close_forward_days=40,
    # )

    # run_full_analysis_suite(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    #         industries=[TRADING_VIEW_INDUSTRIES.INFORMATION_TECHNOLOGY_SERVICES],
    #         min_market_cap_usd=1_000_000_000,
    #         markets=PREFERRED_MARKETS,
    #     ).get("data", []),
    #     min_market_cap_usd=1_000_000_000,
    #     include_blind_spot_sections=True,
    #     industries=[TRADING_VIEW_INDUSTRIES.INFORMATION_TECHNOLOGY_SERVICES],
    # )

    # # RUN SUITE DAILY BELOW - old CSV format - will try to move to the DuckDB version
    # run_full_analysis_suite(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    #         min_market_cap_usd=1_000_000_000,
    #         markets=PREFERRED_MARKETS,
    #     ).get("data", []),
    #     min_market_cap_usd=1_000_000_000,
    #     include_blind_spot_sections=True,
    # )

    # # Earnings-priority variant: same per-profile + consensus analysis as the
    # # standard full analysis suite, plus an extra report ordering names
    # # chronologically by upcoming earnings (catalyst-time view). Uses the
    # # earnings-enriched scan so the next-earnings columns are populated.
    # run_full_analysis_suite_with_earnings_priority(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction_with_earnings(
    #         min_market_cap_usd=1_000_000_000,
    #         markets=PREFERRED_MARKETS,
    #     ).get(
    #         "data", []
    #     ),
    #     min_market_cap_usd=1_000_000_000,
    #     include_blind_spot_sections=True,
    # )

    # run_targets_scan(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    #         min_market_cap_usd=1_000_000_000,
    #         markets=PREFERRED_MARKETS,
    #     ).get("data", []),
    #     min_market_cap_usd=1_000_000_000,
    # )

    # run_cross_scanner_aggregate(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    #         min_market_cap_usd=1_000_000_000,
    #         markets=PREFERRED_MARKETS,
    #     ).get("data", []),
    #     min_market_cap_usd=1_000_000_000,
    # )

    # Aggregate stored DuckDB runs across selected weekly databases.
    # run_move_prediction_history_aggregation_duckdb_example()

    # Legacy CSV-folder aggregation path kept for backward compatibility.
    # run_move_prediction_history_aggregation_example()

    # daily use to get all market data for a day
    # exported_file = export_all_tradingview_fields()
    # print(f"TradingView all-fields export written to: {exported_file}")
    # split_csv_by_rows(
    #     input_csv=Path(
    #         "d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/22_05_2026/tradingview_global_all_tdfields_22_05_2026.csv"
    #     )
    # )

    # # 1. Pull new articles from sitemap into the feed
    # gather_result = gather_sherwood_markets_feed()
    # print(
    #     f"New: {gather_result['new_articles']}, Total: {gather_result['total_articles']}"
    # )

    # # 2. Fetch full body text for articles missing it & write .log dump
    # scan_result = fetch_and_log_full_articles(max_articles=20)
    # print(
    #     f"Fetched: {scan_result['articles_fetched']}, Log: {scan_result['session_log']}"
    # )

    # print(len(get_bvb_tickers_filtered()))

    # ── Portfolio management ────────────────────────────────────────
    # Step 1: Load / refresh the TradingView company universe from
    # scan_world_market_all_priceperf_metrics into trading_view_company_data_map.
    # load_result = load_tradingview_company_data(
    #     api_client=TRADINGVIEW_API_CLIENT,
    #     markets=PREFERRED_MARKETS,
    # )
    # print(load_result)

    # Step 2: Open dated positions sized by money input.
    # tracker = PortfolioTracker("Demo1", currency="USD")
    # tracker.add_holding_by_amount(
    #     internal_id=1,
    #     avg_price=120.00,
    #     date_open=datetime(2025, 12, 1, 14, 30),
    #     amount_money=6_000.00,
    #     unit="USD",
    # )

    # Step 3: Refresh market snapshots from the move-prediction scan and measure
    # holding-period metrics such as days held, total return, and annualized return.
    # snapshot_scan = TRADINGVIEW_API_CLIENT.scan_world_market_all_priceperf_metrics(
    # ).get("data", [])
    # tracker.sync_market_snapshots(snapshot_scan, symbol_key="ticker-view")
    # tracker.print_summary()
    # export_portfolio_analysis(tracker)

    # End-to-end example flow:
    # run_portfolio_bootstrap_example()

    # !!!!!!!!!!!!!!!!!!!!!!!!!!!!! might need to deprecate it since it does not give meaningful results
    # run batch prediction pattern analysis
    # result = run_batch_prediction_pattern_analysis(
    #     iso_year=2026,
    #     start_week=21,
    #     end_week=24,  # omit to use current ISO week
    #     primary_profile_name="breakout_long",  # starting lens
    #     horizon_name="weeks",
    #     min_scan_data_count=3000,  # use 8000+ to prefer backfill universe only
    #     show_progress=True,  # live stderr progress in Git Bash (week bar, ETA, timings)
    # )
    # print(result["overview_log"])

    # # Discover weeks 13–24 of 2026 and pool them
    # result = run_move_prediction_run_pool_aggregation(
    #     iso_year=2026,
    #     start_week=20,
    #     end_week=24,
    #     memory_gb=28,
    #     duckdb_threads=20,
    #     attach_batch_size=8,  # more parallel attach batches
    #     prefer_parquet_inputs=True,
    # )
    # print(result["database_path"])

    # # Analyze the pooled database for top profiles and stock rankings
    # # Option 1: By pool ID pattern
    # analysis_result = run_pool_analysis_from_path(
    #     pool_id_pattern="move_prediction_run_pool_20260610_2000",
    #     min_score_threshold=65.0,
    #     top_n_profiles=10,
    #     top_n_stocks=200,
    # )
    # # Option 2: By explicit database path
    # # analysis_result = analyze_pool_database(
    # #     database_path=result["database_path"],
    # #     min_score_threshold=65.0,
    # #     top_n_profiles=10,
    # #     top_n_stocks=200,
    # # )
    # # Export analysis reports
    # exported = export_analysis_reports(
    #     analysis_result,
    #     output_dir=Path(result["output_dir"]) / "analysis",
    #     export_json=True,
    #     export_csv=True,
    #     export_sql=True,
    # )
    # print(f"Analysis reports exported: {list(exported.keys())}")

    pass


if __name__ == "__main__":
    main()
