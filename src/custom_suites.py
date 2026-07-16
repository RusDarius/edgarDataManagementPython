from constants.trading_view_constants import PREFERRED_MARKETS
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    run_full_analysis_suite_duckdb,
    run_full_analysis_suite_with_earnings_priority_duckdb,
)
from main import (
    CONVICTION_MODE_CONFIG,
    MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3,
    REGIME_CONTEXT_CONFIG,
    TRADINGVIEW_API_CLIENT,
)


def run_custom_prediction_analysis_min50mil():
    # Model Analysis scan with duckdb storage solution
    move_prediction_scan_response = (
        TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
            min_market_cap_usd=50_000_000,
            markets=PREFERRED_MARKETS,
        )
    )
    # Default (omit profile_suite_path): built-in 10-profile baseline — see DEFAULT_MOVE_PREDICTION_PROFILE_SUITE.
    # Extended lenses: profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3
    # Legacy 17-profile suite: MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V2
    # Realigned baseline (11 lenses): MOVE_PREDICTION_PROFILE_SUITE_BASELINE_V2
    # Swing-reversal calibration only: profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_SWING_REVERSAL_V1
    # Move-prediction suite outputs (.log / .csv / DuckDB / regime_context_focus.log)
    # use EXCHANGE:TICKER labels via _get_symbol_name to avoid bare-ticker collisions.
    base_duckdb_result = run_full_analysis_suite_duckdb(
        scan_data=move_prediction_scan_response,
        min_market_cap_usd=50_000_000,
        include_blind_spot_sections=True,
        profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3,
        conviction_mode_config_path=CONVICTION_MODE_CONFIG,
        defer_conviction_to_earnings=True,
        regime_context_config_path=REGIME_CONTEXT_CONFIG,
        write_industry_packs=True,
    )

    # FOR INDUSTRY RUN SPLIT Or post-process an existing run:
    #   write_industry_packs_from_duckdb_run(base_duckdb_result)

    # Top-10 industries by price action + better-scored peer alternatives (bang-for-buck).
    # Output: <run_output_dir>/industry_price_mover_relative_scan/
    #   industry_price_mover_relative_scan__overview.log
    #   industry_relative_opportunities.csv
    #   <industry>/industry_relative_scan.log
    # industry_mover_scan = run_industry_price_mover_relative_scan(
    #     base_duckdb_result,
    #     perf_field="Perf.1M",
    #     top_industries=10,
    #     price_movers_per_industry=5,
    #     catch_up_per_industry=10,
    #     alternatives_per_mover=3,
    # )
    # print(industry_mover_scan["overview_log"])
    # print(industry_mover_scan["opportunities_csv"])

    # # Reuses base_duckdb_result; earnings-priority and regime_context_focus logs
    # # share the same EXCHANGE:TICKER labels.
    run_full_analysis_suite_with_earnings_priority_duckdb(
        scan_data=move_prediction_scan_response,
        min_market_cap_usd=50_000_000,
        include_blind_spot_sections=True,
        base_result=base_duckdb_result,
        profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3,
        conviction_mode_config_path=CONVICTION_MODE_CONFIG,
        regime_context_config_path=REGIME_CONTEXT_CONFIG,
    )
