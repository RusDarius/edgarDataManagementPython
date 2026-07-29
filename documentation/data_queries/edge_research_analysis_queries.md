-- db edge_unified_highlights.duckdb
SELECT
    forward_upside_rank,
    symbol,
    industry,
    forward_valuation_upside_pct,
    forward_valuation_bear_upside_pct,
    forward_valuation_bull_upside_pct,
    forward_company_style,
    forward_selected_lenses,
    forward_near_term_base_upside_pct,
    forward_medium_term_base_upside_pct,
    forward_long_term_base_upside_pct,
    hist_occurrence_count_in_setup,
    median_fwd_5d_in_setup,
    win_rate_5d_in_setup,
    target_rate_5d_in_setup,
    lane_median_fwd_5d,
    lane_win_rate_5d,
    historical_validation_pass,
    historical_validation_bucket,
    historical_validation_win_rate,
    historical_validation_median_fwd_pct,
    big_mover_rank,
    upside_prediction_rank,
    tradeable_safety_rank,
    safety_bucket
FROM symbol_unified_highlights
WHERE forward_upside_mode = 'valuation'
ORDER BY upside_prediction_rank ASC;


-- db edge_unified_highlights.duckdb
SELECT
    forward_upside_rank,
    symbol,
    industry,
    forward_valuation_upside_pct,
    forward_valuation_bear_upside_pct,
    forward_valuation_bull_upside_pct,
    forward_company_style,
    forward_selected_lenses,
    forward_near_term_base_upside_pct,
    forward_medium_term_base_upside_pct,
    forward_long_term_base_upside_pct,
    hist_occurrence_count_in_setup,
    median_fwd_5d_in_setup,
    win_rate_5d_in_setup,
    target_rate_5d_in_setup,
    lane_median_fwd_5d,
    lane_win_rate_5d,
    historical_validation_pass,
    historical_validation_bucket,
    historical_validation_win_rate,
    historical_validation_median_fwd_pct,
    big_mover_rank,
    upside_prediction_rank,
    tradeable_safety_rank,
    safety_bucket
FROM symbol_unified_highlights
WHERE symbol IN ['OMXCOP:ZEAL', 'NASDAQ:MU', 'NYSE:FDXF', 'LSE:AEP', 'NASDAQ:NKLR', 'NASDAQ:EVER', 'TSX:WDO', 'OMXCOP:GUBRA', 'ATHEX:PRODEA', 'NASDAQ:DAVE', 'NASDAQ:MAAS', 'TSX:DPM', 'OMXSTO:TRUE_B', 'LSE:MPE', 'NASDAQ:SNDK', 'LSE:ATYM', 'NASDAQ:ABUS', 'XETR:1INN', 'NASDAQ:INTU', 'BMV:PE_OLES', 'EURONEXT:CSG', 'NASDAQ:SEZL', 'NASDAQ:NVDA', 'NASDAQ:COCO', 'NASDAQ:FUTU', 'NYSE:TNK', 'NASDAQ:KLRA', 'NASDAQ:BLLN', 'TSX:ARG', 'NASDAQ:PHOE']
ORDER BY upside_prediction_rank ASC;


-- db upside_move_potential_scan.duckdb
-- General move-potential scan (join-ready with opportunity fields already carried)
SELECT
  move_upside_rank,
  symbol,
  sector,
  industry,
  move_tier,
  ROUND(TRY_CAST(move_upside_score AS DOUBLE), 4)              AS move_upside_score,
  ROUND(TRY_CAST(move_magnitude_score AS DOUBLE), 4)           AS magnitude,
  ROUND(TRY_CAST(move_magnitude_universe_pct AS DOUBLE), 4)    AS magnitude_pct,
  ROUND(TRY_CAST(move_upside_tilt_score AS DOUBLE), 4)         AS upside_tilt,
  ROUND(TRY_CAST(move_upside_tilt_universe_pct AS DOUBLE), 4)  AS upside_tilt_pct,
  ROUND(TRY_CAST(move_catalyst_score AS DOUBLE), 4)            AS catalyst,
  earnings_days_until,
  ROUND(TRY_CAST(expected_move_proxy_pct AS DOUBLE), 2)        AS expected_move_proxy_pct,
  expected_move_horizon_days,
  ROUND(TRY_CAST(atrp AS DOUBLE), 3)                           AS atrp,
  ROUND(TRY_CAST(volatility_w AS DOUBLE), 3)                   AS vol_w,
  ROUND(TRY_CAST(recommend_all_raw AS DOUBLE), 3)              AS recommend_all,
  ROUND(TRY_CAST(perf_5d AS DOUBLE), 2)                        AS perf_5d,
  ROUND(TRY_CAST(perf_1m AS DOUBLE), 2)                        AS perf_1m,
  ROUND(TRY_CAST(rsi AS DOUBLE), 1)                            AS rsi,
  -- opportunity carry (for aggregate / safety filter; not used in move score)
  upside_opportunity_rank,
  ROUND(TRY_CAST(upside_opportunity_score AS DOUBLE), 4)       AS upside_opportunity_score,
  opportunity_tier,
  directional_lean,
  ROUND(TRY_CAST(opp_binary_risk_score AS DOUBLE), 4)          AS opp_binary_risk,
  ROUND(TRY_CAST(opp_downside_risk_score AS DOUBLE), 4)        AS opp_downside_risk
FROM upside_move_potential_candidates
WHERE TRY_CAST(move_upside_score AS DOUBLE) IS NOT NULL
  -- practical co-filter: keep names opportunity does not reject
  AND COALESCE(directional_lean, 'NEUTRAL') NOT IN (
        'AVOID_DOWNSIDE_RISK', 'CAUTION_DOWNSIDE'
      )
ORDER BY move_upside_rank
LIMIT 100;


-- db market_timing_policy.duckdb
SELECT
   policy_rank, symbol, action, primary_setup, timing_score,
   normalized_risk_units, stop_atr_units, max_hold_days,
   candidate_source, upside_prediction_rank, screen_rank,
   gap_pct, industry_breadth_up_3pct, drawdown_from_high_pct,
   prior_day_pct, existing_conviction_state
FROM action_policy_recommendations
ORDER BY policy_rank;


-- db market_timing_policy.duckdb
SELECT
   event_policy_rank,
   symbol,
   event_action,
   event_book,
   event_policy_score,
   normalized_risk_units,
   earnings_days_until,
   entry_state,
   conviction_rank,
   conviction_score,
   upside_prediction_rank,
   expected_move_proxy_pct,
   drawdown_from_high_pct,
   perf_1m
FROM catalyst_event_action_recommendations
WHERE event_action IN (
    'BOOK_A_HOLD_THROUGH',
    'BOOK_A_DERISK_INTO_PRINT',
    'BOOK_B_SPEC_EARN',
    'WATCH_CATALYST'
)
ORDER BY event_policy_rank;

