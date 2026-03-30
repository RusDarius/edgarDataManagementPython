from constants.trading_view_constants import (
    PREFERRED_MARKETS,
    TRADING_VIEW_ALL_MARKETS,
    TRADING_VIEW_INDUSTRIES,
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
    run_move_prediction_profile_suite,
    run_move_prediction_scan,
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

    # print(
    #     TRADINGVIEW_API_CLIENT.scan_global_market_safety_core(
    #         min_market_cap_usd=1_000_000_000,
    #     ).get("data", [])[0]
    # )

    # analyze_ev_ebitda_deviation(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_by_industry(
    #         min_market_cap_usd=1_000_000_000,
    #         industries=[TRADING_VIEW_INDUSTRIES.HOTELS_RESORTS_CRUISE_LINES],
    #     ).get("data", []),
    #     industries=[TRADING_VIEW_INDUSTRIES.HOTELS_RESORTS_CRUISE_LINES],
    # )

    # run_move_prediction_scan(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    #         min_market_cap_usd=1_000_000_000,
    #         markets=PREFERRED_MARKETS,
    #     ).get("data", []),
    #     min_market_cap_usd=1_000_000_000,
    # )

    run_move_prediction_profile_suite(
        scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
            min_market_cap_usd=1_000_000_000,
            markets=PREFERRED_MARKETS,
            industries=[TRADING_VIEW_INDUSTRIES.CHEMICALS_SPECIALTY],
        ).get("data", []),
        min_market_cap_usd=1_000_000_000,
        include_blind_spot_sections=True,
        industries=[TRADING_VIEW_INDUSTRIES.CHEMICALS_SPECIALTY],
    )

    # run_activity_float_attention_scan_grouped_industries(
    #     scan_data=TRADINGVIEW_API_CLIENT.scan_global_market_activity_float_attention(
    #         min_market_cap_usd=1_000_000_000,
    #     ).get("data", []),
    #     min_market_cap_usd=1_000_000_000,
    # )

    # exported_file = export_all_tradingview_fields()
    # print(f"TradingView all-fields export written to: {exported_file}")

    pass


if __name__ == "__main__":
    main()
