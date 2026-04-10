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
from data_analysis_scripts.trading_view_export_all_tdfields import (
    export_all_tradingview_fields,
)
from data_analysis_scripts.trading_view_priceperf_analysis import (
    analyze_global_price_performance,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite,
    run_move_prediction_profile_suite,
    run_move_prediction_scan,
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
    load_result = load_tradingview_company_data(
        api_client=TRADINGVIEW_API_CLIENT,
        markets=PREFERRED_MARKETS,
    )
    print(f"TradingView companies loaded: {load_result}")

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
        "load_result": load_result,
        "opened_positions": opened_positions,
        "snapshot_result": snapshot_result,
        "summary": summary,
        "metrics": metrics,
        "export_paths": export_paths,
    }


# Main entry point for running workflows and data loaders.
def main():
    base_data_dir = r"D:\FinanceProjects\edgarFinancialStatements"

    load_tradingview_company_data(
        api_client=TRADINGVIEW_API_CLIENT,
        markets=TRADING_VIEW_ALL_MARKETS_ARRAY,
    )

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

    # run_full_analysis_suite(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    #         min_market_cap_usd=1_000_000_000,
    #         markets=PREFERRED_MARKETS,
    #     ).get("data", []),
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

    # run_activity_float_attention_scan_grouped_industries(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_activity_float_attention(
    #         min_market_cap_usd=1_000_000_000,
    #     ).get("data", []),
    #     min_market_cap_usd=1_000_000_000,
    # )

    # daily use to get all market data for a day
    # exported_file = export_all_tradingview_fields()
    # print(f"TradingView all-fields export written to: {exported_file}")
    # split_csv_by_rows(
    #     input_csv=Path(
    #         "d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/09_04_2026/tradingview_global_all_tdfields_09_04_2026.csv"
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
    # tracker = PortfolioTracker("TV Date Aware Demo", currency="USD")
    # tracker.add_holding_by_amount(
    #     internal_id=1,
    #     avg_price=120.00,
    #     date_open=datetime(2025, 12, 1, 14, 30),
    #     amount_money=6_000.00,
    #     unit="USD",
    # )

    # Step 3: Refresh market snapshots from the move-prediction scan and measure
    # holding-period metrics such as days held, total return, and annualized return.
    # snapshot_scan = TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    #     min_market_cap_usd=1_000_000_000,
    #     markets=PREFERRED_MARKETS,
    # ).get("data", [])
    # tracker.sync_market_snapshots(snapshot_scan, symbol_key="ticker-view")
    # tracker.print_summary()
    # export_portfolio_analysis(tracker)

    # End-to-end example flow:
    # run_portfolio_bootstrap_example()

    pass


if __name__ == "__main__":
    main()
