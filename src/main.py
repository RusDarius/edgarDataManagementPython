from pathlib import Path
from datetime import datetime

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
)
from data_analysis_scripts.trading_view_priceperf_analysis import (
    analyze_global_price_performance,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite,
    run_full_analysis_suite_duckdb,
    run_full_analysis_suite_from_raw_csv_folders,
    run_full_analysis_suite_with_earnings_priority,
    run_move_prediction_profile_suite,
    run_move_prediction_scan,
)
from data_analysis_scripts.trading_view_move_prediction_history_aggregator import (
    build_move_prediction_history_inputs_from_folder_names,
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


# Main entry point for running workflows and data loaders.
def main():
    base_data_dir = r"D:\FinanceProjects\edgarFinancialStatements"

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

    # run_activity_float_attention_scan(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_activity_float_attention(
    #         min_market_cap_usd=1_000_000_000,
    #         markets=PREFERRED_MARKETS,
    #     ).get("data", []),
    #     min_market_cap_usd=1_000_000_000,
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

    # Replay the current model against prior all-fields CSV exports. The runner
    # expects each dated folder to contain tradingview_global_all_tdfields_*.csv
    # and writes profile/horizon/date snapshots under prediction_analysis/raw_csv_backscan.
    # run_full_analysis_suite_from_raw_csv_folders(
    #     raw_data_folders=[
    #         Path(
    #             r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\trading_view_all_fields_data\01_04_2026"
    #         ),
    #     ],
    #     min_market_cap_usd=1_000_000_000,
    #     include_blind_spot_sections=False,
    # )

    run_full_analysis_suite_duckdb(
        scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
            min_market_cap_usd=1_000_000_000,
            markets=PREFERRED_MARKETS,
        ).get("data", []),
        min_market_cap_usd=1_000_000_000,
        include_blind_spot_sections=True,
    )

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

    # Aggregate progression across selected dated result folders.
    # Pass only the dated folder names under AllInUniverse_min1bil.
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

    pass


if __name__ == "__main__":
    main()
