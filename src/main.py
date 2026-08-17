from pathlib import Path
from datetime import datetime, timezone
import json
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
from data_analysis_scripts.trading_view_backwards_progression_symbol_plot import (
    plot_backwards_progression_for_symbols,
)
from data_analysis_scripts.trading_view_backwards_profile_cohort_attribution import (
    format_profile_cohort_markdown_table,
    run_backwards_profile_cohort_attribution,
)
from data_analysis_scripts.trading_view_cross_scanner_aggregator import (
    run_cross_scanner_aggregate,
)
from data_analysis_scripts.trading_view_export_all_tdfields import (
    export_all_tradingview_fields,
    export_all_tradingview_fields_duckdb,
)
from data_analysis_scripts.trading_view_market_flow_screening import (
    run_market_flow_screening_suite,
)
from data_analysis_scripts.trading_view_move_prediction_batch_pattern_analysis import (
    run_batch_prediction_pattern_analysis,
)
from data_analysis_scripts.trading_view_move_prediction_multi_run_pool_aggregator import (
    run_move_prediction_run_pool_aggregation,
)
from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
    run_backwards_prediction_dense_day_spacing_analysis,
    run_backwards_prediction_sparse_weekly_analysis,
)
from data_analysis_scripts.trading_view_backwards_prediction_scout_report import (
    run_backwards_prediction_scout_report,
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
    export_scan_period_data_set_conclusions,
    market_cap_basic_universe_filter,
    preferred_markets_universe_filter,
)
from data_analysis_scripts.trading_view_ticker_field_pattern_scan import (
    run_ticker_field_pattern_scan,
)
from data_analysis_scripts.trading_view_priceperf_analysis import (
    analyze_global_price_performance,
)
from data_analysis_scripts.trading_view_price_driven_score_analysis import (
    run_price_driven_score_analysis_duckdb,
)
from data_analysis_scripts.trading_view_holdings_scoring_analysis import (
    load_holdings_config,
    run_holdings_scoring_analysis,
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
from data_analysis_scripts.trading_view_scan_period_ticker_watchlist import (
    run_scan_period_ticker_watchlist_analysis,
)
from data_analysis_scripts.trading_view_move_prediction_industry_packs import (
    write_industry_packs_from_duckdb_run,
)
from data_analysis_scripts.trading_view_industry_price_mover_relative_scan import (
    run_industry_price_mover_relative_scan,
)
from data_analysis_scripts.trading_view_execution_backtest_suite import (
    run_existing_backwards_execution_examples,
    run_execution_backtest_suite,
)
from data_analysis_scripts.trading_view_all_fields_upside_edge_research import (
    run_mar_jun_best_trade_ladders,
    run_mar_jun_best_trade_ladder_realism_suite,
    run_mar_jun_best_trade_profile_tracking,
    run_mar_jun_trade_ladder_entry_field_diagnostics,
    run_mar_jun_trade_ladder_entry_profile_capture,
    run_mar_jun_upside_edge_research,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CURRENT_HOLDINGS_CONFIG = (
    PROJECT_ROOT / "config" / "holdings_scoring" / "current_holdings.json"
)
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
MOVE_PREDICTION_PROFILE_SUITE_UPSIDE_REVERSAL_V1 = (
    PROJECT_ROOT
    / "config"
    / "move_prediction_profiles"
    / "suites"
    / "upside_reversal_v1.json"
)
MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V2 = (
    PROJECT_ROOT
    / "config"
    / "move_prediction_profiles"
    / "suites"
    / "active_manager_v2.json"
)
MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3 = (
    PROJECT_ROOT
    / "config"
    / "move_prediction_profiles"
    / "suites"
    / "active_manager_v3.json"
)
MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V4 = (
    PROJECT_ROOT
    / "config"
    / "move_prediction_profiles"
    / "suites"
    / "active_manager_v4.json"
)
MOVE_PREDICTION_PROFILE_SUITE_BASELINE_V2 = (
    PROJECT_ROOT / "config" / "move_prediction_profiles" / "suites" / "baseline_v2.json"
)
CONVICTION_MODE_CONFIG = (
    PROJECT_ROOT / "config" / "move_prediction_conviction" / "active_manager_v1.json"
)
REGIME_CONTEXT_CONFIG = (
    PROJECT_ROOT / "config" / "regime_context" / "mar_jun_2026_risk_on_v1.json"
)
SCAN_PERIOD_TRACKING_RUN_ROOT = (
    PROJECT_ROOT
    / "logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs"
    / "scan_period_close_forward_tracking_29mar_15jun2026_b108820e"
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
from data_analysis_scripts.trading_view_etf_analysis import (
    run_etf_scan_and_analysis_suite,
)
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


def run_execution_backtest_suite_example() -> dict[str, object]:
    result = run_execution_backtest_suite()
    print(f"Execution backtest database: {result['database_path']}")
    print(f"Execution suite output: {result['output_dir']}")
    print(f"Execution manifest: {result['manifest_path']}")
    print(f"Execution summary CSV: {result['execution']['summary_csv']}")
    for rule_id, path in result["execution"].get("trade_csv_paths", {}).items():
        print(f"Positions CSV ({rule_id}): {path}")
    return result


def run_existing_backwards_execution_examples_example() -> dict[str, object]:
    result = run_existing_backwards_execution_examples()
    print(f"Existing backwards execution output: {result['output_dir']}")
    print(f"Existing backwards guidance report: {result['guidance_report']}")
    print(f"Existing backwards summary CSV: {result['execution']['summary_csv']}")
    print(
        "Existing backwards profile/rule summary CSV: "
        f"{result['execution']['profile_rule_summary_csv']}"
    )
    return result


def run_all_fields_upside_edge_research_example() -> dict[str, object]:
    result = run_mar_jun_upside_edge_research()
    print(f"Upside field candidate CSV: {result['candidate_csv']}")
    print(f"Upside field candidate report: {result['report_md']}")
    print(f"Upside field candidate manifest: {result['manifest_path']}")
    return result


def run_mar_jun_best_trade_profile_tracking_example() -> dict[str, object]:
    result = run_mar_jun_best_trade_profile_tracking()
    print(f"Best-trade tracking report: {result['report_md']}")
    print(f"Best-trade tracking CSV: {result['best_trades_csv']}")
    print(f"Indicator hindsight summary CSV: {result['indicator_summary_csv']}")
    print(f"Profile capture summary CSV: {result['profile_summary_csv']}")
    return result


def run_mar_jun_best_trade_ladders_example() -> dict[str, object]:
    result = run_mar_jun_best_trade_ladders()
    print(f"Best-trade ladder report: {result['report_md']}")
    print(f"Best-trade ladder ranking CSV: {result['ranking_csv']}")
    print(f"Best-trade ladder trades CSV: {result['trades_csv']}")
    return result


def run_mar_jun_best_trade_ladder_realism_suite_example() -> dict[str, object]:
    result = run_mar_jun_best_trade_ladder_realism_suite()
    print(f"Trade ladder realism report: {result['report_md']}")
    print(f"Trade ladder realism summary CSV: {result['summary_csv']}")
    return result


def run_mar_jun_trade_ladder_entry_profile_capture_example() -> dict[str, object]:
    result = run_mar_jun_trade_ladder_entry_profile_capture()
    print(f"Trade ladder profile-entry report: {result['report_md']}")
    print(f"Trade ladder profile-entry summary CSV: {result['summary_csv']}")
    print(f"Trade ladder profile-entry detail CSV: {result['detail_csv']}")
    return result


def run_mar_jun_trade_ladder_entry_field_diagnostics_example() -> dict[str, object]:
    result = run_mar_jun_trade_ladder_entry_field_diagnostics()
    print(f"Trade ladder entry-field report: {result['report_md']}")
    print(f"Trade ladder entry-field summary CSV: {result['summary_csv']}")
    print(f"Trade ladder entry-field detail CSV: {result['detail_csv']}")
    return result


def run_dense_backwards_every_2_days_example() -> dict[str, object]:
    result = run_backwards_prediction_dense_day_spacing_analysis(
        start_day_label="29_03_2026",
        spacing_days=2,
        min_scan_data_count=3000,
        anchor_min_scan_data_count=None,
        profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3,
        include_consensus=True,
        include_components=False,
        duckdb_threads=20,
        memory_gb=28,
    )
    print(f"Dense backwards database: {result['database_path']}")
    print(f"Dense backwards output: {result['output_dir']}")
    print(f"Dense backwards overview log: {result['overview_log']}")
    print(f"Dense backwards anchor summary: {result['dense_anchor_summary']}")
    return result


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

    # # Holdings scoring: merge current holdings with latest stock + ETF scans.
    # # Stocks (instrument_type omitted or "stock") join move-prediction DuckDB.
    # # Entries with "instrument_type": "etf" join the latest ETF analysis DuckDB.
    # # Output: logs/tradingview_analysis/holdings_scoring_analysis/runs/<run_id>/
    # #   holdings_scoring__shortlist.log  — human-readable portfolio shortlist
    # # Optional cash_position in config (value + currency) is passed through to manifest/logs.
    # run_holdings_scoring_analysis(
    #     holdings_config_path=PROJECT_ROOT
    #     / "config"
    #     / "holdings_scoring"
    #     / "current_holdings.json",
    # )

    # ── ETF analysis (world primary listings) ─────────────────────────────
    # Fetch + v1 composite + active-management book + persist. Output:
    #   logs/tradingview_analysis/etf_analysis/duckdb_runs/iso_year=YYYY/week=WW/
    #     etf_analysis_overview.log
    #     etf_regime_tape.log / etf_sleeve_heat.log / etf_divergences.log
    #     etf_catch_up_vs_extended.log / etf_vehicle_quality.log
    #     etf_holdings_overlay.log / etf_dod_changes.log / etf_book_ranked.csv
    # etf_result = run_etf_scan_and_analysis_suite(
    #     TRADINGVIEW_API_CLIENT,
    #     min_aum_usd=1_000_000_000,
    # )

    # ── Stock move-prediction ─────────────────────────────────────────────
    # # Model Analysis scan with duckdb storage solution
    # move_prediction_scan_response = (
    #     TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    #         min_market_cap_usd=500_000_000,
    #         markets=PREFERRED_MARKETS,
    #     )
    # )
    # # Default (omit profile_suite_path): built-in 10-profile baseline — see DEFAULT_MOVE_PREDICTION_PROFILE_SUITE.
    # # Extended lenses: profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3
    # # Legacy 17-profile suite: MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V2
    # # Realigned baseline (11 lenses): MOVE_PREDICTION_PROFILE_SUITE_BASELINE_V2
    # # Swing-reversal calibration only: profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_SWING_REVERSAL_V1
    # # Move-prediction suite outputs (.log / .csv / DuckDB / regime_context_focus.log)
    # # use EXCHANGE:TICKER labels via _get_symbol_name to avoid bare-ticker collisions.
    # base_duckdb_result = run_full_analysis_suite_duckdb(
    #     scan_data=move_prediction_scan_response,
    #     min_market_cap_usd=500_000_000,
    #     include_blind_spot_sections=True,
    #     profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3,
    #     conviction_mode_config_path=CONVICTION_MODE_CONFIG,
    #     defer_conviction_to_earnings=True,
    #     regime_context_config_path=REGIME_CONTEXT_CONFIG,
    #     write_industry_packs=True,
    # )
    # # Financial projection on prediction names (fundamentals from latest all-fields):
    # # All symbols from that prediction run (omit prediction_top_n / pass None):
    # # run_financial_projection_from_latest_prediction_analysis(
    # #     prediction_database=base_duckdb_result["_duckdb_database"],
    # #     min_market_cap_usd=500_000_000,
    # # )
    # # Or trim to top-N by score:
    # # run_financial_projection_from_latest_prediction_analysis(
    # #     prediction_database=base_duckdb_result["_duckdb_database"],
    # #     min_market_cap_usd=500_000_000,
    # #     prediction_top_n=40,
    # # )
    # # Or auto-discover latest prediction week DB (no base_duckdb_result needed):
    # # run_financial_projection_from_latest_prediction_analysis(
    # #     min_market_cap_usd=500_000_000,
    # # )

    # # FOR INDUSTRY RUN SPLIT Or post-process an existing run:
    # # write_industry_packs_from_duckdb_run(base_duckdb_result)

    # # Top-10 industries by price action + better-scored peer alternatives (bang-for-buck).
    # # Output: <run_output_dir>/industry_price_mover_relative_scan/
    # #   industry_price_mover_relative_scan__overview.log
    # #   industry_relative_opportunities.csv
    # #   <industry>/industry_relative_scan.log
    # # industry_mover_scan = run_industry_price_mover_relative_scan(
    # #     base_duckdb_result,
    # #     perf_field="Perf.1M",
    # #     top_industries=10,
    # #     price_movers_per_industry=5,
    # #     catch_up_per_industry=10,
    # #     alternatives_per_mover=3,
    # # )
    # # print(industry_mover_scan["overview_log"])
    # # print(industry_mover_scan["opportunities_csv"])

    # # # Reuses base_duckdb_result; earnings-priority and regime_context_focus logs
    # # # share the same EXCHANGE:TICKER labels.
    # run_full_analysis_suite_with_earnings_priority_duckdb(
    #     scan_data=move_prediction_scan_response,
    #     min_market_cap_usd=500_000_000,
    #     include_blind_spot_sections=True,
    #     base_result=base_duckdb_result,
    #     profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3,
    #     conviction_mode_config_path=CONVICTION_MODE_CONFIG,
    #     regime_context_config_path=REGIME_CONTEXT_CONFIG,
    # )

    # Price-driven decile analysis: bucket by change / Perf.5D / Perf.1M, score profiles,
    # surface upward-move opportunities in worst performers. Output:
    # logs/tradingview_analysis/prediction_analysis/price_driven_score_analysis/duckdb_runs/...
    # run_price_driven_score_analysis_duckdb(
    #     scan_data=move_prediction_scan_response,
    #     min_market_cap_usd=500_000_000,
    #     profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3,
    # )

    # ALL FIELDS DUCKDB EXPORT
    # Snapshot used as financial-projection input (market_cap_basic filterable).
    # export_all_tradingview_fields_duckdb()
    # export_all_tradingview_fields_duckdb(chunk_size=300, timeout=90)

    # Market flow screening (paired daily DuckDB snapshots)
    # Output: logs/tradingview_analysis/market_flow_screening/runs/flow_<base>_<compare>_<id>/
    # run_market_flow_screening_suite(
    #     base_day_label="29_06_2026",
    #     compare_day_label="01_07_2026",
    #     min_market_cap_usd=100_000_000,
    #     # optional refinement overrides:
    #     # max_abs_price_return_pct=150.0,
    #     # max_abs_flow_residual_pct=200.0,
    #     # buyback_yield_threshold=0.02,
    #     # share_buyback_ratio_threshold=0.02,
    # )

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
    # run_scan_period_close_forward_predictor_tracking(
    #     start_day_label="29_03_2026",
    #     end_day_label="15_06_2026",
    #     min_runs_for_stability=3,
    #     max_parallel_runs=4,
    #     duckdb_threads=6,
    #     field_batch_size=120,
    #     max_parallel_chunks=1,
    #     duckdb_memory_limit="10GB",
    #     max_system_memory_gb=30.0,
    #     memory_reserve_gb=4.0,
    #     universe_filter={
    #         **market_cap_basic_universe_filter(500_000_000),
    #         **preferred_markets_universe_filter(PREFERRED_MARKETS),
    #     },
    # )

    # # --- Scan-period post-processing (requires completed tracking run) ---
    #
    # The watchlist analyzer always needs a scan-period folder as its data source
    # (period returns, anchored predictors, daily progression). It does NOT run
    # without one. Provide the run in any of these ways:
    #   run_root=SCAN_PERIOD_TRACKING_RUN_ROOT
    #   tracking_id="scan_period_close_forward_tracking_29mar_15jun2026_b108820e"
    #   regime_context_config_path=REGIME_CONTEXT_CONFIG  # uses scan_period_run_root
    #
    # (A) Field-level conclusions for the same scan period:
    # export_scan_period_data_set_conclusions(run_root=SCAN_PERIOD_TRACKING_RUN_ROOT)
    # run_all_fields_upside_edge_research_example()
    # run_mar_jun_best_trade_profile_tracking_example()
    # run_mar_jun_best_trade_ladders_example()
    # run_mar_jun_best_trade_ladder_realism_suite_example()
    # run_mar_jun_trade_ladder_entry_profile_capture_example()
    # run_mar_jun_trade_ladder_entry_field_diagnostics_example()
    #
    # (A2) Field taxonomy splits (meaning / usage / relevance):
    # from data_analysis_scripts.trading_view_field_taxonomy_builder import build_trading_view_field_taxonomy
    # build_trading_view_field_taxonomy(scan_run_root=SCAN_PERIOD_TRACKING_RUN_ROOT)
    #
    # (B) PLAYBOOK A tickers vs Mar–Jun realized performance:
    # run_scan_period_ticker_watchlist_analysis(
    #     regime_context_config_path=REGIME_CONTEXT_CONFIG,
    #     from_regime_log=PROJECT_ROOT
    #     / "logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=25/runs"
    #     / "move_prediction_20260618_1442_utc_31b685b4/move_prediction__regime_context_focus.log",
    #     watchlist_id="playbook_a_mar_jun_check",
    # )
    # # Explicit scan-period path (equivalent):
    # run_scan_period_ticker_watchlist_analysis(
    #     run_root=SCAN_PERIOD_TRACKING_RUN_ROOT,
    #     tickers=[
    #         "SILEX",
    #         "QS",
    #         "DYVOX",
    #         "BFLY",
    #         "LEGN",
    #         "DAR",
    #         "GNS",
    #         "IDIA",
    #         "OCDO",
    #         "CPI",
    #         "GNFT",
    #         "AMS",
    #         "FAST",
    #         "SVMB",
    #         "FRAMERY",
    #         "MMGR_B",
    #         "SHA0",
    #         "ACN",
    #         "KAR",
    #         "CRW",
    #         "LNZ",
    #         "HACK",
    #         "ACAST",
    #         "CAP",
    #         "AMTD",
    #         "DRW8",
    #         "FGA",
    #         "FCH",
    #         "PE",
    #         "VPLAY_A",
    #         "BION",
    #         "SNDK",
    #         "MLI",
    #         "GPCR",
    #         "CALM",
    #         "MU",
    #         "TROW",
    #         "JHG",
    #         "SYF",
    #         "ABUS",
    #         "AMP",
    #         "ADMIE",
    #         "NVR",
    #         "AUPH",
    #         "LKFT",
    #         "ORKA",
    #         "ARGX",
    #         "IIIN",
    #         "PHIL",
    #         "CRDO",
    #         "UFPI",
    #         "CART",
    #         "RMS",
    #         "TFG",
    #         "WPK",
    #         "SNA",
    #         "VRTX",
    #         "INCY",
    #         "ALAB",
    #         "FDXF",
    #     ],
    #     watchlist_id="playbook_a_tactical_mar_jun2026",
    # )

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

    # --- Single ticker backward field-pattern scan ---
    # Tracks one symbol through daily all-fields snapshots, surfaces unusual field
    # shifts vs same-day universe, and links them to close forward returns.
    # run_ticker_field_pattern_scan(
    #     ticker="NASDAQ:NVDA",
    #     start_day_label="01_03_2026",
    #     end_day_label="22_06_2026",
    #     min_market_cap_usd=500_000_000,
    #     max_fields=250,
    #     forward_days=(1, 5, 10, 20),
    #     constrain_to_industry=True,
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

    # run_targets_scan(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    #         min_market_cap_usd=1_000_000_000,
    #         markets=PREFERRED_MARKETS,
    #     ).get("data", []),
    #     min_market_cap_usd=1_000_000_000,
    # )

    # Aggregate stored DuckDB runs across selected weekly databases.
    # run_move_prediction_history_aggregation_duckdb_example()

    # print(len(get_bvb_tickers_filtered()))

    # ── Portfolio management ────────────────────────────────────────
    # Step 1: Load / refresh the TradingView company universe from
    # scan_world_market_all_priceperf_metrics into trading_view_company_data_map.
    # load_result = load_tradingview_company_data(
    #     api_client=TRADINGVIEW_API_CLIENT,
    #     markets=PREFERRED_MARKETS,
    # )
    # print(load_result)

    # # Step 2: Open dated positions sized by money input.
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

    # Oldest indexed run -> today, 2 distinct-day anchors per ISO week (use runs_per_week=3
    # for three anchors/week with even_spread selection).
    # min_scan_data_count filters the *current* run only; anchors include all indexed
    # backfill weeks by default (anchor_min_scan_data_count=None).
    #
    # Week range: omit start_week to begin at oldest indexed week; set iso_year+start_week
    # to begin at a specific ISO week (still 2 anchors/week with runs_per_week=2).
    # backwards_result = run_backwards_prediction_sparse_weekly_analysis(
    #     profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3,
    #     runs_per_week=3,
    #     min_scan_data_count=3000,
    #     # iso_year=2026,
    #     start_week=13,  # from week=16; omit for oldest indexed week
    #     # end_week=24,    # optional upper cap
    # )
    # print(backwards_result["database_path"])
    # print(backwards_result["anchor_plan_summary"])
    # print(Path(backwards_result["overview_log"]).read_text(encoding="utf-8"))

    # Denser execution backtest suite: runs_per_week=5 backwards rebuild,
    # top-50/top-100 cohorts, fresh inclusion, and trade-level positions CSVs.
    # run_execution_backtest_suite_example()

    # Existing sparse backwards run: entry/exit rule examples, cohorts,
    # positions CSVs, and a small guidance report.
    # run_existing_backwards_execution_examples_example()

    # Dense backwards build: resolves unique anchors on a 2-day grid from the
    # Mar-29 history start through the latest eligible current run. Heavier than
    # the weekly sparse sampler; components are disabled to control size.
    # run_dense_backwards_every_2_days_example()

    # Weekly sparse-anchor progression (2-3 move-prediction runs per ISO week).
    # Oldest anchor comes from the move-prediction run index under duckdb_runs/
    # (currently week=13 backfill from 2026-03-29; day 01_04_2026 is included).
    # Earlier raw CSV days are not anchored until ingested into weekly DuckDB pools.
    # Profile families: profile_suite_path loads all suite names for the backwards
    # build; anchor snapshots match by profile_family so breakout_long_v1 anchors
    # align with older breakout_long rows. Plotting resolves latest _vN per family.

    # # Resume plotting from an existing backwards scan (skips rebuild; needs plotly installed)
    # Merges exchange-qualified + bare snapshot symbol keys per anchor so lines run
    # through the latest backwards anchor (not only the early qualified-key window).
    # _, current_holdings, _ = load_holdings_config(CURRENT_HOLDINGS_CONFIG)
    # progression_plot_symbols: list[str] = []
    # seen_progression_symbols: set[str] = set()
    # for ticker in [holding.ticker for holding in current_holdings] + ["MU"]:
    #     key = ticker.upper()
    #     if key in seen_progression_symbols:
    #         continue
    #     seen_progression_symbols.add(key)
    #     progression_plot_symbols.append(ticker)

    # symbol_plot_result = plot_backwards_progression_for_symbols(
    #     run_folder_pattern="backwards_prediction_analysis_20260625_1621_utc_b00e6c1d",
    #     symbols=[
    #         "NASDAQ:RMBS",
    #         "NASDAQ:ALAB",
    #         "NASDAQ:PLTR",
    #         "NASDAQ:MDB",
    #         "NYSE:HUBS",
    #         "NASDAQ:MU",
    #         "NASDAQ:FORM",
    #         "NASDAQ:ENPH",
    #         "AMEX:UEC",
    #         "NYSE:CVNA",
    #         "NYSE:SMR",
    #         "NASDAQ:CRDO",
    #         "NYSE:KVYO",
    #         "NYSE:PATH",
    #         "NASDAQ:PGY",
    #         "NASDAQ:DOCU",
    #         "NYSE:IOT",
    #         "NYSE:GWRE",
    #         "NYSE:FIG",
    #         "NASDAQ:WDAY",
    #     ],
    #     profile_suite_path=PROJECT_ROOT
    #     / "config"
    #     / "move_prediction_profiles"
    #     / "suites"
    #     / "active_manager_v3.json",
    #     horizon_name="weeks",
    #     watchlist_path=CURRENT_HOLDINGS_CONFIG,
    # )
    # print(symbol_plot_result["database_path"])
    # print(symbol_plot_result["html_path"])
    # print(symbol_plot_result.get("browser_file_uri"))
    # print(symbol_plot_result.get("index_file_uri"))
    # print(symbol_plot_result.get("plot_manifest_path"))
    # for plot in symbol_plot_result.get("profile_plots", []):
    #     print(
    #         plot["resolved_profile_name"],
    #         plot.get("browser_file_uri"),
    #         plot.get("browser_shortcut_path"),
    #         plot["point_count"],
    #     )

    # # Top-250 profile cohort attribution from the same backwards run (weeks horizon).
    # # period_boundary = memo-style first→last window return for ever-top-250 names.
    # # inclusion_entry = buy at first top-250 inclusion anchor, sell at last anchor.
    # cohort_result = run_backwards_profile_cohort_attribution(
    #     run_folder_pattern="backwards_prediction_analysis_20260625_1621_utc_b00e6c1d",
    #     profile_suite_path=PROJECT_ROOT
    #     / "config"
    #     / "move_prediction_profiles"
    #     / "suites"
    #     / "active_manager_v3.json",
    #     horizon_name="weeks",
    #     top_n=250,
    #     rank_scope="min1bil",
    #     # start_anchor_name="w2026_14_open",
    #     # end_anchor_name="w2026_21_open",
    # )
    # print(cohort_result["summary_csv"])
    # print(format_profile_cohort_markdown_table(cohort_result["summaries"], method="period_boundary"))
    # print(format_profile_cohort_markdown_table(cohort_result["summaries"], method="inclusion_entry"))

    # scout_result = run_backwards_prediction_scout_report(
    #     database_path=backwards_result["database_path"],
    #     profile_name=["breakout_long_v1", "value_recovery_v2"],
    #     horizon_name=["days", "weeks"],
    #     primary_anchor_name="last_week",
    #     top_n=50,
    # )
    # if scout_result["combination_count"] == 1:
    #     print(scout_result["highlights_log"])
    # else:
    #     for combo in scout_result["results"]:
    #         print(combo["highlights_log"])

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
