from constants.trading_view_constants import TRADING_VIEW_INDUSTRIES
from data_analysis_scripts.trading_view_activity_float_attention import (
    run_activity_float_attention_scan,
)
from data_analysis_scripts.trading_view_priceperf_analysis import (
    analyze_global_price_performance,
)
from data_analysis_scripts.trading_view_safety_check_v1 import run_safety_core_scan
from data_analysis_scripts.trading_view_valuation_analysis import (
    analyze_ev_ebitda_deviation,
)
from data_loaders.api_tradingview_client import ApiTradingViewClient


USER_AGENT = "Barnnabass daniOO7XbX@gmail.com"
TRADINGVIEW_API_CLIENT = ApiTradingViewClient(user_agent=USER_AGENT)


# Main entry point for running workflows and data loaders.
def main():
    base_data_dir = r"D:\FinanceProjects\edgarFinancialStatements"

    # tickers = []

    # for el in extract_companies_from_tradingview_screener_html():
    #     ticker = el.get("ticker")
    #     if ticker:
    #         tickers.append(ticker)

    # process_revenues_for_tickers(tickers, start_year=2015)

    # analyze_global_price_performance(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_world_market_all_priceperf_metrics().get(
    #         "data", []
    #     ),
    #     min_market_cap_usd=1_000_000_000,
    #     min_performance_pct=10.0,
    # )

    run_activity_float_attention_scan(
        scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_activity_float_attention(
            min_market_cap_usd=1_000_000_000,
        ).get("data", []),
        min_market_cap_usd=1_000_000_000,
    )

    # run_safety_core_scan(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_safety_core(
    #         min_market_cap_usd=1_000_000_000,
    #     ).get("data", []),
    #     min_market_cap_usd=1_000_000_000,
    # )

    # print(
    #     TRADINGVIEW_API_CLIENT.scan_global_market_safety_core(
    #         min_market_cap_usd=1_000_000_000,
    #     ).get("data", [])[0]
    # )

    # analyze_ev_ebitda_deviation(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_by_industry(
    #         industries=[TRADING_VIEW_INDUSTRIES.TOBACCO],
    #         min_market_cap_usd=1_000_000_000,
    #     ).get("data", [])
    # )

    pass


if __name__ == "__main__":
    main()
