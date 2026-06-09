-- Investment opportunity query library for prediction_analysis DuckDB stores
-- Weekly connection target: move_prediction_YYYY_WWW.duckdb (iso_year=YYYY/week=WW)
-- Historical connection target: historical_prediction_analysis.duckdb under historical_prediction_analysis/runs/<analysis_run_id>/
-- Run one @block at a time in SQLTools. Disconnect before rerunning the Python writer on Windows.
--
-- Pipeline order: Discovery -> Profile lens -> Raw validation -> Risk overlay -> Cross-profile -> Historical persistence -> Action bucket
-- Profile weighting reference: documentation/tradingview_move_prediction_profile_weighting_reference.md
--
-- Symbol join note: raw_scan_rows.symbol often includes an exchange prefix (e.g. NASDAQ:AAPL)
-- while profile_* tables store the ticker-view short symbol (e.g. AAPL). All raw joins use the
-- normalized match below so prefixed and short symbols resolve correctly.
--   (r.symbol = h.symbol OR r.symbol LIKE '%:' || h.symbol OR h.symbol LIKE '%:' || r.symbol)
-- =============================================================================
-- SECTION 1: breakout_long (Tactical Continuation)
-- What to look for: volume confirmation, ADX/Aroon directional strength, short-term momentum,
-- event/gap pressure, short MA trend alignment, safety floor (not high-risk).
-- =============================================================================
-- @block q_breakout_long_weekly_leaders
-- Primary tactical shortlist. Top 30 by risk-adjusted score on weeks horizon.
-- Look for: confidence >= 60, coverage >= 0.70, manager_action_signal = add_long_breakout.
WITH latest_run AS (
    SELECT run_id,
        created_at_utc
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.sector,
    h.industry,
    h.score,
    h.confidence,
    h.coverage,
    h.risk_adjusted_score,
    h.risk_tier,
    h.manager_action_signal,
    h.direction,
    h.setup
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
WHERE h.profile_name = 'breakout_long'
    AND h.horizon_name = 'weeks'
    AND h.score IS NOT NULL
    AND h.coverage >= 0.70
    AND h.confidence >= 60
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 30;
-- @block q_breakout_long_days_continuation
-- Same-day / next-day entries. Pure tactical days horizon with add_long_breakout signal.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    h.confidence,
    h.coverage,
    h.risk_adjusted_score,
    h.risk_tier,
    h.manager_action_signal,
    h.direction
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
WHERE h.profile_name = 'breakout_long'
    AND h.horizon_name = 'days'
    AND h.score >= 0.75
    AND h.manager_action_signal = 'add_long_breakout'
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_breakout_long_component_drivers
-- Why a name ranked: component scores behind the weeks horizon score.
-- Look for: momentum + attention + trend dominate; quality/valuation near zero on days.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.horizon_name,
    h.score,
    h.risk_adjusted_score,
    c.attention,
    c.event,
    c.momentum,
    c.trend,
    c.quality,
    c.valuation,
    c.safety,
    c.scale,
    h.manager_action_signal
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON h.run_id = c.run_id
    AND h.profile_name = c.profile_name
    AND h.symbol = c.symbol
WHERE h.profile_name = 'breakout_long'
    AND h.horizon_name IN ('days', 'weeks')
ORDER BY h.horizon_name,
    h.risk_adjusted_score DESC NULLS LAST
LIMIT 100;
-- @block q_breakout_long_raw_volume_confirm
-- Validate volume thesis with raw scan fields.
-- Look for: relative_volume_10d_calc > 1.3, volume_trend (10d/30d) > 1.05, positive attention component.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    h.risk_adjusted_score,
    r.relative_volume_10d_calc,
    r.average_volume_10d_calc / NULLIF(r.average_volume_30d_calc, 0) AS volume_trend_raw,
    r.volume / NULLIF(r.float_shares_outstanding, 0) AS float_turnover_raw,
    c.attention,
    c.momentum,
    c.trend
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'breakout_long'
    AND h.horizon_name = 'weeks'
    AND r.relative_volume_10d_calc > 1.3
    AND (
        r.average_volume_10d_calc / NULLIF(r.average_volume_30d_calc, 0)
    ) > 1.05
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_breakout_long_raw_directional_emerge
-- Validate ADX/Aroon directional emergence (highest-weight momentum signals for breakout_long).
-- Look for: ADX+DI > ADX-DI and Aroon Up > Down by meaningful margin.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    h.risk_adjusted_score,
    r."ADX+DI" - r."ADX-DI" AS adx_directional_spread_raw,
    r."Aroon.Up" - r."Aroon.Down" AS aroon_spread_raw,
    r."Perf.5D",
    r."Perf.W",
    c.momentum,
    c.trend
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'breakout_long'
    AND h.horizon_name = 'weeks'
    AND (r."ADX+DI" - r."ADX-DI") > 0
    AND (r."Aroon.Up" - r."Aroon.Down") > 20
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_breakout_long_raw_event_gap
-- Validate gap/event pressure. Look for: abnormal gap vs ATRP or strong premarket move; event component > 0.3.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    h.risk_adjusted_score,
    r.gap,
    r.ATRP,
    ABS(r.gap) / NULLIF(r.ATRP, 0) AS gap_severity_raw,
    r.premarket_change,
    r.premarket_volume / NULLIF(r.average_volume_10d_calc, 0) AS event_intensity_raw,
    c.event,
    c.attention
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'breakout_long'
    AND h.horizon_name = 'weeks'
    AND c.event > 0.3
    AND (
        ABS(r.gap) / NULLIF(r.ATRP, 0) > 0.5
        OR r.premarket_change > 1
    )
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_breakout_long_not_coiled
-- Exclude pre-breakout coiled names; breakout_long wants names already moving, not squeezing.
-- Look for: BB width above universe 25th percentile (not in tightest quartile).
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
), bb_widths AS (
    SELECT r.run_id,
        r.symbol,
        (r."BB.upper" - r."BB.lower") / NULLIF(r.close, 0) AS bb_width
    FROM raw_scan_rows r
        JOIN latest_run lr ON r.run_id = lr.run_id
    WHERE r.close IS NOT NULL
        AND r."BB.upper" IS NOT NULL
        AND r."BB.lower" IS NOT NULL
),
bb_threshold AS (
    SELECT quantile_cont(bb_width, 0.25) AS bb_width_p25
    FROM bb_widths
    WHERE bb_width IS NOT NULL
)
SELECT h.symbol,
    h.company,
    h.score,
    h.risk_adjusted_score,
    bw.bb_width,
    c.momentum,
    c.attention
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN bb_widths bw ON bw.run_id = h.run_id
    AND bw.symbol = h.symbol
    CROSS JOIN bb_threshold bt
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'breakout_long'
    AND h.horizon_name = 'weeks'
    AND bw.bb_width >= bt.bb_width_p25
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_breakout_long_coverage_gate
-- Data-quality filter: high coverage with all tactical components positive.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.horizon_name,
    h.score,
    h.coverage,
    h.confidence,
    h.risk_adjusted_score,
    c.attention,
    c.momentum,
    c.trend,
    c.event
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'breakout_long'
    AND h.horizon_name = 'weeks'
    AND h.coverage >= 0.75
    AND c.attention > 0
    AND c.momentum > 0
    AND c.trend > 0
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_breakout_long_false_breakout_screen
-- Risk reject: high days score but weak safety/quality/valuation on weeks — momentum without floor.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT days_h.symbol,
    days_h.company,
    days_h.score AS days_score,
    weeks_h.score AS weeks_score,
    c.safety,
    c.quality,
    c.valuation,
    days_h.risk_tier,
    days_h.manager_action_signal
FROM profile_horizon_scores days_h
    JOIN latest_run lr ON days_h.run_id = lr.run_id
    JOIN profile_horizon_scores weeks_h ON weeks_h.run_id = days_h.run_id
    AND weeks_h.profile_name = days_h.profile_name
    AND weeks_h.symbol = days_h.symbol
    AND weeks_h.horizon_name = 'weeks'
    JOIN profile_components c ON c.run_id = days_h.run_id
    AND c.profile_name = days_h.profile_name
    AND c.symbol = days_h.symbol
WHERE days_h.profile_name = 'breakout_long'
    AND days_h.horizon_name = 'days'
    AND days_h.score >= 0.75
    AND (
        c.safety < -0.35
        OR c.quality < -0.20
        OR weeks_h.score < -0.20
    )
ORDER BY days_h.score DESC NULLS LAST;
-- @block q_breakout_long_consensus_agree
-- High conviction: breakout_long top 50 intersected with consensus weeks top 100 and agreement >= 55%.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
), breakout_top AS (
    SELECT run_id,
        symbol,
        risk_adjusted_score,
        ROW_NUMBER() OVER (
            ORDER BY risk_adjusted_score DESC NULLS LAST
        ) AS breakout_rank
    FROM profile_horizon_scores
        JOIN latest_run USING (run_id)
    WHERE profile_name = 'breakout_long'
        AND horizon_name = 'weeks'
),
consensus_top AS (
    SELECT run_id,
        symbol,
        score,
        agreement_ratio,
        opinions,
        risk_adjusted_score,
        ROW_NUMBER() OVER (
            ORDER BY risk_adjusted_score DESC NULLS LAST
        ) AS consensus_rank
    FROM consensus_horizon_scores
        JOIN latest_run USING (run_id)
    WHERE horizon_name = 'weeks'
)
SELECT b.symbol,
    phs.company,
    phs.sector,
    phs.industry,
    b.breakout_rank,
    c.consensus_rank,
    phs.score AS breakout_score,
    c.score AS consensus_score,
    c.agreement_ratio,
    c.opinions,
    phs.risk_adjusted_score,
    phs.manager_action_signal
FROM breakout_top b
    JOIN consensus_top c ON b.run_id = c.run_id
    AND b.symbol = c.symbol
    JOIN profile_horizon_scores phs ON phs.run_id = b.run_id
    AND phs.profile_name = 'breakout_long'
    AND phs.horizon_name = 'weeks'
    AND phs.symbol = b.symbol
WHERE b.breakout_rank <= 50
    AND c.consensus_rank <= 100
    AND c.agreement_ratio >= 0.55
ORDER BY b.breakout_rank,
    c.consensus_rank;
-- @block q_breakout_long_fragility_disagree
-- Hedge check: breakout_long bullish but fragility_short also flags structural weakness — reduce size.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT bl.symbol,
    bl.company,
    bl.score AS breakout_weeks_score,
    bl.risk_adjusted_score AS breakout_risk_adj,
    fs.score AS fragility_weeks_score,
    fs.risk_adjusted_score AS fragility_risk_adj,
    bl.manager_action_signal
FROM profile_horizon_scores bl
    JOIN latest_run lr ON bl.run_id = lr.run_id
    JOIN profile_horizon_scores fs ON fs.run_id = bl.run_id
    AND fs.symbol = bl.symbol
    AND fs.profile_name = 'fragility_short'
    AND fs.horizon_name = 'weeks'
WHERE bl.profile_name = 'breakout_long'
    AND bl.horizon_name = 'weeks'
    AND bl.score >= 0.50
    AND fs.score >= 0.50
ORDER BY bl.risk_adjusted_score DESC NULLS LAST,
    fs.score DESC NULLS LAST
LIMIT 50;
-- @block q_breakout_long_earnings_window
-- Catalyst timing: flag breakout candidates with earnings within 7 days (higher event risk / confidence penalty).
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    h.confidence,
    h.risk_adjusted_score,
    r.earnings_release_next_date,
    r.earnings_release_next_calendar_date,
    DATE_DIFF(
        'day',
        CURRENT_DATE,
        TRY_CAST(r.earnings_release_next_calendar_date AS DATE)
    ) AS days_to_earnings,
    h.manager_action_signal
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
WHERE h.profile_name = 'breakout_long'
    AND h.horizon_name = 'weeks'
    AND h.score >= 0.50
    AND TRY_CAST(r.earnings_release_next_calendar_date AS DATE) IS NOT NULL
    AND DATE_DIFF(
        'day',
        CURRENT_DATE,
        TRY_CAST(r.earnings_release_next_calendar_date AS DATE)
    ) BETWEEN 0 AND 7
ORDER BY days_to_earnings,
    h.risk_adjusted_score DESC NULLS LAST;
-- =============================================================================
-- SECTION 2: early_momentum_inflection (Pre-Breakout / Coiled Energy)
-- What to look for: tight BB/range, volatility contraction, Aroon flip, short MA emergence,
-- muted trailing Perf.Y (not already extended).
-- =============================================================================
-- @block q_early_inflection_coiled_energy
-- Squeeze candidates with coiled-energy raw signals.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    h.risk_adjusted_score,
    (r."BB.upper" - r."BB.lower") / NULLIF(r.close, 0) AS bb_squeeze_raw,
    (r."High.3M" - r."Low.3M") / NULLIF(r.close, 0) AS range_compression_raw,
    r."Volatility.D" / NULLIF(r."Volatility.M", 0) AS volatility_contraction_raw,
    c.attention,
    c.momentum
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'early_momentum_inflection'
    AND h.horizon_name = 'weeks'
    AND c.attention > 0.20
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_early_inflection_aroon_flip
-- New trend forming: Aroon Up above Down, not yet extended on Perf.Y.
-- Perf.Y filter is universe-relative (<= 55th percentile), not a fixed 30% cutoff.
-- Requires Aroon columns in raw_scan_rows; if still empty, run q_early_inflection_aroon_flip_diagnostic.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
),
universe_perf AS (
    SELECT quantile_cont("Perf.Y", 0.55) AS perf_y_not_extended_threshold
    FROM raw_scan_rows r
        JOIN latest_run lr ON r.run_id = lr.run_id
    WHERE r."Perf.Y" IS NOT NULL
)
SELECT h.symbol,
    h.company,
    h.score,
    h.risk_adjusted_score,
    r."Aroon.Up",
    r."Aroon.Down",
    r."Aroon.Up" - r."Aroon.Down" AS aroon_spread_raw,
    r."Perf.Y",
    r."Perf.YTD",
    up.perf_y_not_extended_threshold,
    c.momentum,
    c.trend
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    CROSS JOIN universe_perf up
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'early_momentum_inflection'
    AND h.horizon_name = 'weeks'
    AND r."Aroon.Up" IS NOT NULL
    AND r."Aroon.Down" IS NOT NULL
    AND r."Aroon.Up" > r."Aroon.Down"
    AND (
        r."Perf.Y" IS NULL
        OR r."Perf.Y" <= up.perf_y_not_extended_threshold
    )
ORDER BY h.risk_adjusted_score DESC NULLS LAST,
    aroon_spread_raw DESC
LIMIT 50;
-- @block q_early_inflection_aroon_flip_diagnostic
-- Troubleshoot empty aroon-flip results: counts at each filter stage for the latest run.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
),
base AS (
    SELECT r.symbol AS raw_symbol,
        h.symbol AS profile_symbol,
        r."Aroon.Up",
        r."Aroon.Down",
        r."Perf.Y",
        h.score,
        h.risk_adjusted_score,
        (
            r.symbol = h.symbol
            OR r.symbol LIKE '%:' || h.symbol
            OR h.symbol LIKE '%:' || r.symbol
        ) AS symbol_join_ok
    FROM raw_scan_rows r
        JOIN latest_run lr ON r.run_id = lr.run_id
        LEFT JOIN profile_horizon_scores h ON h.run_id = r.run_id
        AND h.profile_name = 'early_momentum_inflection'
        AND h.horizon_name = 'weeks'
        AND (
            r.symbol = h.symbol
            OR r.symbol LIKE '%:' || h.symbol
            OR h.symbol LIKE '%:' || r.symbol
        )
)
SELECT COUNT(*) AS raw_rows,
    COUNT(*) FILTER (
        WHERE "Aroon.Up" IS NOT NULL
            AND "Aroon.Down" IS NOT NULL
    ) AS rows_with_aroon,
    COUNT(*) FILTER (
        WHERE "Aroon.Up" > "Aroon.Down"
    ) AS rows_aroon_bullish,
    COUNT(*) FILTER (
        WHERE symbol_join_ok
    ) AS rows_profile_joined,
    COUNT(*) FILTER (
        WHERE symbol_join_ok
            AND "Aroon.Up" > "Aroon.Down"
    ) AS rows_joined_aroon_bullish,
    quantile_cont("Perf.Y", 0.55) AS perf_y_p55_threshold
FROM base;
-- @block q_early_inflection_short_ma_emergence
-- Entry trigger: price above multiple short-term moving averages.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
), short_ma_flags AS (
    SELECT h.symbol,
        h.company,
        h.score,
        h.risk_adjusted_score,
        r.close,
        r."SMA10",
        r."SMA20",
        r."EMA10",
        r."EMA20",
        CASE
            WHEN r.close >= r."SMA10" THEN 1
            ELSE 0
        END + CASE
            WHEN r.close >= r."SMA20" THEN 1
            ELSE 0
        END + CASE
            WHEN r.close >= r."EMA10" THEN 1
            ELSE 0
        END + CASE
            WHEN r.close >= r."EMA20" THEN 1
            ELSE 0
        END AS short_ma_bull_count,
        c.trend,
        c.momentum
    FROM profile_horizon_scores h
        JOIN latest_run lr ON h.run_id = lr.run_id
        JOIN raw_scan_rows r ON r.run_id = h.run_id
        AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
        JOIN profile_components c ON c.run_id = h.run_id
        AND c.profile_name = h.profile_name
        AND c.symbol = h.symbol
    WHERE h.profile_name = 'early_momentum_inflection'
        AND h.horizon_name = 'weeks'
        AND r.close IS NOT NULL
)
SELECT symbol,
    company,
    score,
    close,
    "SMA10",
    "SMA20",
    "EMA10",
    "EMA20",
    short_ma_bull_count,
    trend,
    momentum
FROM short_ma_flags
WHERE short_ma_bull_count >= 3
ORDER BY risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_early_inflection_not_extended
-- Avoid late entries: low long-duration performance despite high weeks score.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    r."Perf.Y",
    r."Perf.YTD",
    r."Perf.6M",
    c.momentum,
    h.risk_adjusted_score
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'early_momentum_inflection'
    AND h.horizon_name = 'weeks'
    AND h.score >= 0.50
    AND COALESCE(r."Perf.Y", 999) < 40
    AND COALESCE(r."Perf.6M", 999) < 50
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_early_inflection_vs_breakout_handoff
-- Pipeline bridge: inflection leader now that is not yet a breakout leader — watch for handoff.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
), inflection AS (
    SELECT symbol,
        score,
        risk_adjusted_score,
        ROW_NUMBER() OVER (
            ORDER BY risk_adjusted_score DESC NULLS LAST
        ) AS inflection_rank
    FROM profile_horizon_scores
        JOIN latest_run USING (run_id)
    WHERE profile_name = 'early_momentum_inflection'
        AND horizon_name = 'weeks'
),
breakout AS (
    SELECT symbol,
        score,
        risk_adjusted_score,
        ROW_NUMBER() OVER (
            ORDER BY risk_adjusted_score DESC NULLS LAST
        ) AS breakout_rank
    FROM profile_horizon_scores
        JOIN latest_run USING (run_id)
    WHERE profile_name = 'breakout_long'
        AND horizon_name = 'weeks'
)
SELECT i.symbol,
    i.inflection_rank,
    i.score AS inflection_score,
    b.breakout_rank,
    b.score AS breakout_score
FROM inflection i
    LEFT JOIN breakout b ON i.symbol = b.symbol
WHERE i.inflection_rank <= 30
    AND (
        b.breakout_rank IS NULL
        OR b.breakout_rank > 30
    )
ORDER BY i.inflection_rank;
-- =============================================================================
-- SECTION 3: Real Quality Profiles
-- =============================================================================
-- @block q_qvc_years_core_holdings
-- quality_value_compounder long-book anchors on years horizon.
-- Look for: ROIC, FCF margin, Piotroski strong; years score >= 0.45.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score AS years_score,
    h.risk_adjusted_score,
    c.quality,
    c.valuation,
    c.safety,
    c.attention,
    r.return_on_invested_capital,
    r.free_cash_flow_margin_ttm,
    r.piotroski_f_score_ttm,
    r.sustainable_growth_rate_ttm,
    h.manager_action_signal
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
WHERE h.profile_name = 'quality_value_compounder'
    AND h.horizon_name = 'years'
    AND h.score >= 0.45
    AND c.quality >= 0.50
    AND COALESCE(r.piotroski_f_score_ttm, 0) >= 7
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_qvc_anti_crowd
-- Low-attention quality compounders (profile inverts attention 0.70x/1.50x).
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.horizon_name,
    h.score,
    c.attention,
    c.quality,
    c.valuation,
    c.safety,
    r.relative_volume_10d_calc,
    r."Value.Traded"
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
WHERE h.profile_name = 'quality_value_compounder'
    AND h.horizon_name IN ('months', 'years')
    AND c.attention <= 0.10
    AND c.quality >= 0.70
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_qvc_value_trap_reject
-- Hard reject value traps from quality_value_compounder universe.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.horizon_name,
    h.score,
    c.valuation,
    c.quality,
    c.safety,
    h.manager_action_signal,
    r.debt_to_equity,
    r.altman_z_score_ttm,
    r.total_debt_to_ebitda_fq
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
WHERE h.profile_name = 'quality_value_compounder'
    AND h.manager_action_signal = 'avoid_value_trap'
ORDER BY c.valuation DESC NULLS LAST;
-- @block q_qvc_missing_data_penalty
-- Sparse fundamentals likely penalized on years horizon (coverage < 0.60).
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.horizon_name,
    h.score,
    h.coverage,
    h.confidence,
    c.quality,
    c.valuation,
    c.safety
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'quality_value_compounder'
    AND h.horizon_name = 'years'
    AND h.coverage < 0.60
ORDER BY h.score DESC NULLS LAST
LIMIT 50;
-- @block q_dvc_cash_at_discount
-- durable_value_compounder: cheap cash generators with quality floor.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    c.quality,
    c.valuation,
    c.safety,
    r.enterprise_value_to_free_cash_flow_ttm,
    r.price_free_cash_flow_ttm,
    r.book_value_per_share_fq,
    (r.book_value_per_share_fq - r.close) / NULLIF(r.close, 0) AS book_value_discount_raw,
    r.piotroski_f_score_ttm,
    h.risk_adjusted_score
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
WHERE h.profile_name = 'durable_value_compounder'
    AND h.horizon_name IN ('months', 'years')
    AND c.quality >= 0.35
    AND c.valuation >= 0.35
    AND c.safety >= 0
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_dvc_months_entry
-- Months positioning entry: quality AND valuation both strong.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    h.confidence,
    h.coverage,
    c.quality,
    c.valuation,
    c.safety,
    h.risk_adjusted_score,
    h.manager_action_signal
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'durable_value_compounder'
    AND h.horizon_name = 'months'
    AND c.quality >= 0.35
    AND c.valuation >= 0.35
    AND c.safety >= 0
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_sector_accumulation
-- sector_relative_outperformer: sustained volume accumulation (10d/30d rising).
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    r.average_volume_10d_calc / NULLIF(r.average_volume_30d_calc, 0) AS volume_trend_raw,
    r.relative_volume_10d_calc,
    c.attention,
    c.quality,
    c.trend
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'sector_relative_outperformer'
    AND h.horizon_name = 'months'
    AND (
        r.average_volume_10d_calc / NULLIF(r.average_volume_30d_calc, 0)
    ) > 1.05
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_sector_efficiency_leader
-- Operational excellence: revenue per employee and ROIC.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.industry,
    h.score,
    r.total_revenue / NULLIF(r.number_of_employees, 0) AS revenue_per_employee_raw,
    r.return_on_invested_capital,
    r.operating_margin,
    c.quality,
    h.risk_adjusted_score
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'sector_relative_outperformer'
    AND h.horizon_name IN ('months', 'years')
    AND r.return_on_invested_capital IS NOT NULL
    AND r.number_of_employees > 0
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_sector_long_momentum
-- Long-duration momentum with crushed short-term noise.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    r."Perf.Y",
    r."Perf.6M",
    r."Perf.YTD",
    r.change,
    c.momentum,
    c.trend,
    h.risk_adjusted_score
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'sector_relative_outperformer'
    AND h.horizon_name = 'years'
    AND COALESCE(r."Perf.Y", 0) > 15
    AND COALESCE(r."Perf.6M", 0) > 10
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_forward_eps_acceleration
-- forward_edge_active: forward EPS growth is the highest-weight signal (1.75).
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    r.earnings_per_share_fq,
    r.earnings_per_share_forecast_next_fq,
    (
        r.earnings_per_share_forecast_next_fq - r.earnings_per_share_fq
    ) / NULLIF(ABS(r.earnings_per_share_fq), 0) AS eps_forward_growth_raw,
    c.quality,
    c.trend,
    h.risk_adjusted_score
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'forward_edge_active'
    AND h.horizon_name IN ('weeks', 'months')
    AND r.earnings_per_share_forecast_next_fq > r.earnings_per_share_fq
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_forward_qoq_improvement
-- Sequential QoQ improvement dominates YoY in forward_edge_active.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    r.total_revenue_qoq_growth_fq,
    r.total_revenue_yoy_growth_ttm,
    r.free_cash_flow_qoq_growth_fq,
    r.net_income_qoq_growth_fq,
    c.quality,
    h.risk_adjusted_score
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'forward_edge_active'
    AND h.horizon_name = 'months'
    AND COALESCE(r.total_revenue_qoq_growth_fq, 0) > COALESCE(r.total_revenue_yoy_growth_ttm, 0)
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_forward_earnings_surprise
-- Positive earnings surprise history (eps_surprise_percent_fq weight 1.60).
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    r.eps_surprise_percent_fq,
    c.event,
    c.quality,
    h.risk_adjusted_score
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'forward_edge_active'
    AND h.horizon_name = 'weeks'
    AND COALESCE(r.eps_surprise_percent_fq, 0) > 0
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_forward_anti_crowd
-- Undiscovered forward-edge names: low attention, strong quality/trend.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    c.attention,
    c.quality,
    c.trend,
    r.relative_volume_10d_calc,
    h.risk_adjusted_score
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
WHERE h.profile_name = 'forward_edge_active'
    AND h.horizon_name = 'weeks'
    AND c.attention <= 0
    AND c.quality >= 0.30
    AND c.trend >= 0.25
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- =============================================================================
-- SECTION 4: Overlooked Fundamentals & Hedging
-- =============================================================================
-- @block q_asym_deep_discount
-- asymmetric_value: cheap on multiple valuation axes.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    c.valuation,
    c.quality,
    c.safety,
    r.price_earnings_ttm,
    r.price_book_fq,
    r.enterprise_value_to_free_cash_flow_ttm,
    (r.close - r.price_52_week_low) / NULLIF(
        r.price_52_week_high - r.price_52_week_low,
        0
    ) AS range_position_52w_raw,
    h.risk_adjusted_score
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
WHERE h.profile_name = 'asymmetric_value'
    AND h.horizon_name IN ('months', 'years')
    AND c.valuation >= 0.45
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_asym_operating_floor
-- Operating-company quality floor (not passive-vehicle cheapness).
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    c.quality,
    c.valuation,
    c.safety,
    r.return_on_invested_capital,
    r.piotroski_f_score_ttm,
    r.free_cash_flow_margin_ttm,
    r.earnings_per_share_forecast_next_fq,
    h.manager_action_signal
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
WHERE h.profile_name = 'asymmetric_value'
    AND c.quality >= 0.20
    AND COALESCE(r.piotroski_f_score_ttm, 0) >= 6
    AND h.manager_action_signal != 'avoid_value_trap'
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_asym_peer_mispricing
-- Peer revenue share exceeds market-cap share (best when run is industry-scoped).
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.industry,
    h.score,
    p.peer_revenue_value_gap,
    p._peer_revenue_share,
    p._peer_market_cap_share,
    c.valuation,
    c.quality,
    h.risk_adjusted_score
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_prediction_rows p ON p.run_id = h.run_id
    AND p.profile_name = h.profile_name
    AND p.symbol = h.symbol
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'asymmetric_value'
    AND h.horizon_name = 'months'
    AND COALESCE(p.peer_revenue_value_gap, 0) > 0
ORDER BY p.peer_revenue_value_gap DESC NULLS LAST,
    h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_recovery_turnaround_trigger
-- value_recovery: Stoch RSI crossover + short trend emergence as entry triggers.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    r."Stoch.RSI.K",
    r."Stoch.RSI.D",
    r."Stoch.RSI.K" - r."Stoch.RSI.D" AS stoch_rsi_crossover_raw,
    c.momentum,
    c.trend,
    c.valuation,
    h.risk_adjusted_score
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'value_recovery'
    AND h.horizon_name = 'months'
    AND (r."Stoch.RSI.K" - r."Stoch.RSI.D") > 0
    AND c.trend >= 0
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_recovery_qoq_inflection
-- Sequential QoQ improvement dominates YoY for turnaround detection.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    r.free_cash_flow_qoq_growth_fq,
    r.net_income_qoq_growth_fq,
    r.ebitda_qoq_growth_fq,
    r.total_revenue_qoq_growth_fq,
    c.quality,
    c.valuation
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'value_recovery'
    AND h.horizon_name = 'months'
    AND (
        COALESCE(r.free_cash_flow_qoq_growth_fq, 0) > 0
        OR COALESCE(r.net_income_qoq_growth_fq, 0) > 0
    )
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_recovery_cheap_rerating
-- Cheap rerating candidates with weak near-term momentum forgiven.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    c.valuation,
    c.quality,
    c.momentum,
    r.price_earnings_growth_ttm,
    r.price_book_fq,
    r.price_target_average,
    (r.price_target_average - r.close) / NULLIF(r.close, 0) AS target_upside_raw,
    h.manager_action_signal
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
WHERE h.profile_name = 'value_recovery'
    AND h.horizon_name = 'months'
    AND c.valuation >= 0.45
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_dvm_value_plus_catalyst
-- deep_value_momentum: cheap AND reversal starting.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    c.valuation,
    c.quality,
    c.momentum,
    c.trend,
    r."Stoch.RSI.K" - r."Stoch.RSI.D" AS stoch_rsi_crossover_raw,
    r."Aroon.Up" - r."Aroon.Down" AS aroon_spread_raw,
    r.price_book_fq,
    r.earnings_yield,
    h.risk_adjusted_score
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name = 'deep_value_momentum'
    AND h.horizon_name = 'weeks'
    AND c.valuation >= 0.35
    AND (
        (r."Stoch.RSI.K" - r."Stoch.RSI.D") > 0
        OR (r."Aroon.Up" - r."Aroon.Down") > 0
    )
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_dvm_not_value_trap
-- Value plus catalyst with safety floor.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    c.safety,
    c.valuation,
    c.quality,
    r.total_debt_to_ebitda_fq,
    r.altman_z_score_ttm,
    r.beta_1_year,
    r.ATRP,
    h.manager_action_signal
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
WHERE h.profile_name = 'deep_value_momentum'
    AND c.safety >= -0.30
    AND h.manager_action_signal != 'avoid_value_trap'
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_fragility_hedge_candidates
-- fragility_short: structural weakness candidates for hedge/short book.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.score,
    h.risk_adjusted_score,
    c.safety,
    c.momentum,
    c.trend,
    r.debt_to_equity,
    r.beta_1_year,
    r.altman_z_score_ttm,
    r."Perf.5D",
    r."Perf.W",
    h.manager_action_signal
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
    JOIN raw_scan_rows r ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
WHERE h.profile_name = 'fragility_short'
    AND h.horizon_name IN ('weeks', 'months')
    AND h.score >= 0.50
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_fragility_vs_long_conflict
-- Long profile bullish + fragility_short bearish = hedge pair candidates.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT long_h.symbol,
    long_h.company,
    long_h.profile_name AS long_profile,
    long_h.score AS long_weeks_score,
    fs.score AS fragility_weeks_score,
    long_h.manager_action_signal AS long_action,
    fs.manager_action_signal AS fragility_action
FROM profile_horizon_scores long_h
    JOIN latest_run lr ON long_h.run_id = lr.run_id
    JOIN profile_horizon_scores fs ON fs.run_id = long_h.run_id
    AND fs.symbol = long_h.symbol
    AND fs.profile_name = 'fragility_short'
    AND fs.horizon_name = 'weeks'
WHERE long_h.horizon_name = 'weeks'
    AND long_h.profile_name IN (
        'breakout_long',
        'forward_edge_active',
        'deep_value_momentum'
    )
    AND long_h.score >= 0.50
    AND fs.score >= 0.50
ORDER BY long_h.risk_adjusted_score DESC NULLS LAST,
    fs.score DESC NULLS LAST
LIMIT 50;
-- =============================================================================
-- SECTION 5: Cross-Profile & Consensus Queries
-- Connect to weekly move_prediction DB.
-- =============================================================================
-- @block q_consensus_weekly_high_conviction
-- Best overall ideas: top risk-adjusted consensus with strong agreement.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT c.symbol,
    c.company,
    c.sector,
    c.industry,
    c.score,
    c.risk_adjusted_score,
    c.agreement_ratio,
    c.opinions,
    c.confidence,
    c.risk_tier,
    c.manager_action_signal
FROM consensus_horizon_scores c
    JOIN latest_run lr ON c.run_id = lr.run_id
WHERE c.horizon_name = 'weeks'
    AND c.agreement_ratio >= 0.60
    AND c.opinions >= 5
ORDER BY c.risk_adjusted_score DESC NULLS LAST
LIMIT 30;
-- @block q_consensus_manager_action_add_long
-- Actionable tactical longs from consensus manager_action_signal.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT c.symbol,
    c.company,
    c.score,
    c.risk_adjusted_score,
    c.agreement_ratio,
    c.opinions,
    c.risk_tier,
    c.manager_action_signal
FROM consensus_horizon_scores c
    JOIN latest_run lr ON c.run_id = lr.run_id
WHERE c.horizon_name = 'weeks'
    AND c.manager_action_signal = 'add_long_breakout'
ORDER BY c.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_consensus_manager_action_accumulate
-- Value plus catalyst longs from consensus.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT c.symbol,
    c.company,
    c.score,
    c.risk_adjusted_score,
    c.agreement_ratio,
    c.opinions,
    c.risk_tier,
    c.manager_action_signal
FROM consensus_horizon_scores c
    JOIN latest_run lr ON c.run_id = lr.run_id
WHERE c.horizon_name = 'months'
    AND c.manager_action_signal = 'accumulate_value_catalyst'
ORDER BY c.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_consensus_profile_disagreement
-- Tactical vs fundamental tension: breakout_long days top 20 but QVC years weak.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
), breakout_days AS (
    SELECT symbol,
        score,
        ROW_NUMBER() OVER (
            ORDER BY risk_adjusted_score DESC NULLS LAST
        ) AS breakout_days_rank
    FROM profile_horizon_scores
        JOIN latest_run USING (run_id)
    WHERE profile_name = 'breakout_long'
        AND horizon_name = 'days'
),
qvc_years AS (
    SELECT symbol,
        score,
        PERCENT_RANK() OVER (
            ORDER BY score ASC
        ) AS qvc_years_percentile
    FROM profile_horizon_scores
        JOIN latest_run USING (run_id)
    WHERE profile_name = 'quality_value_compounder'
        AND horizon_name = 'years'
)
SELECT b.symbol,
    b.breakout_days_rank,
    b.score AS breakout_days_score,
    q.score AS qvc_years_score,
    q.qvc_years_percentile
FROM breakout_days b
    JOIN qvc_years q ON b.symbol = q.symbol
WHERE b.breakout_days_rank <= 20
    AND q.qvc_years_percentile <= 0.25
ORDER BY b.breakout_days_rank;
-- @block q_consensus_multi_lens_agree
-- Count profiles agreeing bullish (score >= 0.35) per symbol on weeks horizon.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
), profile_opinions AS (
    SELECT symbol,
        company,
        COUNT(*) FILTER (
            WHERE score >= 0.35
        ) AS bullish_profile_count,
        COUNT(*) AS total_profiles,
        AVG(score) AS avg_profile_score
    FROM consensus_profile_horizon_scores
        JOIN latest_run USING (run_id)
    WHERE horizon_name = 'weeks'
        AND profile_name != 'fragility_short'
    GROUP BY symbol,
        company
)
SELECT p.symbol,
    p.company,
    p.bullish_profile_count,
    p.total_profiles,
    p.avg_profile_score,
    c.score AS consensus_score,
    c.agreement_ratio,
    c.manager_action_signal
FROM profile_opinions p
    JOIN consensus_horizon_scores c ON c.run_id = (
        SELECT run_id
        FROM latest_run
    )
    AND c.symbol = p.symbol
    AND c.horizon_name = 'weeks'
WHERE p.bullish_profile_count >= 3
ORDER BY p.bullish_profile_count DESC,
    c.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- @block q_consensus_avoid_traps
-- Hard reject list: avoid_value_trap or weak quality/safety with high valuation.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.profile_name,
    h.horizon_name,
    h.score,
    c.valuation,
    c.quality,
    c.safety,
    h.manager_action_signal
FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    JOIN profile_components c ON c.run_id = h.run_id
    AND c.profile_name = h.profile_name
    AND c.symbol = h.symbol
WHERE h.profile_name IN (
        'asymmetric_value',
        'value_recovery',
        'quality_value_compounder'
    )
    AND (
        h.manager_action_signal = 'avoid_value_trap'
        OR (
            c.valuation >= 0.55
            AND (
                c.quality <= -0.35
                OR c.safety <= -0.55
            )
        )
    )
ORDER BY c.valuation DESC NULLS LAST;
-- @block q_consensus_earnings_catalyst
-- Near-term earnings within 30 days with positive days consensus.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT c.symbol,
    c.company,
    c.score AS consensus_days_score,
    c.risk_adjusted_score,
    c.agreement_ratio,
    r.earnings_release_next_calendar_date,
    DATE_DIFF(
        'day',
        CURRENT_DATE,
        TRY_CAST(r.earnings_release_next_calendar_date AS DATE)
    ) AS days_to_earnings,
    c.manager_action_signal
FROM consensus_horizon_scores c
    JOIN latest_run lr ON c.run_id = lr.run_id
    JOIN raw_scan_rows r ON r.run_id = c.run_id
    AND (
        r.symbol = c.symbol
        OR r.symbol LIKE '%:' || c.symbol
        OR c.symbol LIKE '%:' || r.symbol
    )
WHERE c.horizon_name = 'days'
    AND c.score >= 0.35
    AND TRY_CAST(r.earnings_release_next_calendar_date AS DATE) IS NOT NULL
    AND DATE_DIFF(
        'day',
        CURRENT_DATE,
        TRY_CAST(r.earnings_release_next_calendar_date AS DATE)
    ) BETWEEN 0 AND 30
ORDER BY days_to_earnings,
    c.risk_adjusted_score DESC NULLS LAST
LIMIT 50;
-- =============================================================================
-- SECTION 6: Historical Validation Pipeline
-- Connect to historical_prediction_analysis.duckdb (not the weekly store).
-- Run after 3+ weekly snapshots via trading_view_move_prediction_history_aggregator.py.
-- =============================================================================
-- @block q_hist_profile_calibration
-- Which profile×horizon combinations predicted price returns in your universe.
WITH latest_analysis AS (
    SELECT analysis_run_id
    FROM historical_analysis_runs
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT profile_name,
    horizon_name,
    symbol_count,
    ROUND(avg_presence_ratio, 4) AS avg_presence_ratio,
    ROUND(avg_score_delta_total, 4) AS avg_score_delta_total,
    ROUND(avg_close_return_pct_total, 4) AS avg_close_return_pct_total,
    aligned_positive_count,
    false_positive_count,
    ROUND(
        aligned_positive_count * 1.0 / NULLIF(symbol_count, 0),
        4
    ) AS aligned_positive_ratio,
    ROUND(score_price_corr, 4) AS score_price_corr,
    ROUND(rank_price_corr, 4) AS rank_price_corr
FROM vw_profile_horizon_alignment_stats
WHERE analysis_run_id = (
        SELECT analysis_run_id
        FROM latest_analysis
    )
ORDER BY score_price_corr DESC NULLS LAST,
    aligned_positive_ratio DESC NULLS LAST,
    profile_name,
    horizon_name;
-- @block q_hist_persistent_winners
-- Names with improving scores and positive realized returns over the history window.
WITH latest_analysis AS (
    SELECT analysis_run_id
    FROM historical_analysis_runs
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT profile_name,
    horizon_name,
    symbol,
    company_name,
    snapshots_seen,
    ROUND(presence_ratio, 4) AS presence_ratio,
    last_rank,
    ROUND(last_score, 4) AS last_score,
    ROUND(score_delta_total, 4) AS score_delta_total,
    ROUND(close_return_pct_total, 4) AS close_return_pct_total,
    ROUND(max_drawdown_pct, 4) AS max_drawdown_pct
FROM vw_profile_horizon_progression_core
WHERE analysis_run_id = (
        SELECT analysis_run_id
        FROM latest_analysis
    )
    AND snapshots_seen >= 2
    AND presence_ratio >= 0.5
    AND score_delta_total > 0
    AND close_return_pct_total > 0
ORDER BY close_return_pct_total DESC NULLS LAST,
    score_delta_total DESC NULLS LAST,
    profile_name,
    horizon_name,
    symbol
LIMIT 100;
-- @block q_hist_false_positives
-- Score improved but price fell — candidates for breakout_long reject rules.
WITH latest_analysis AS (
    SELECT analysis_run_id
    FROM historical_analysis_runs
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT profile_name,
    horizon_name,
    symbol,
    company_name,
    snapshots_seen,
    ROUND(presence_ratio, 4) AS presence_ratio,
    last_rank,
    ROUND(last_score, 4) AS last_score,
    ROUND(score_delta_total, 4) AS score_delta_total,
    ROUND(close_return_pct_total, 4) AS close_return_pct_total
FROM vw_profile_horizon_progression_core
WHERE analysis_run_id = (
        SELECT analysis_run_id
        FROM latest_analysis
    )
    AND snapshots_seen >= 2
    AND score_delta_total > 0
    AND close_return_pct_total < 0
ORDER BY close_return_pct_total ASC,
    score_delta_total DESC NULLS LAST,
    profile_name,
    horizon_name,
    symbol
LIMIT 100;
-- @block q_hist_current_leaders_streak
-- Current top-10 leaders by profile and horizon from latest snapshot.
WITH latest_analysis AS (
    SELECT analysis_run_id
    FROM historical_analysis_runs
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT profile_name,
    horizon_name,
    leader_rank,
    symbol,
    company_name,
    last_rank,
    ROUND(last_score, 4) AS last_score,
    ROUND(score_delta_total, 4) AS score_delta_total,
    ROUND(close_return_pct_total, 4) AS close_return_pct_total
FROM vw_profile_horizon_current_leaders
WHERE analysis_run_id = (
        SELECT analysis_run_id
        FROM latest_analysis
    )
    AND leader_rank <= 10
ORDER BY profile_name,
    horizon_name,
    leader_rank;
-- @block q_hist_snapshot_mismatch
-- Score moved up between snapshots but price moved down (alignment flag -1).
WITH latest_analysis AS (
    SELECT analysis_run_id
    FROM historical_analysis_runs
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT profile_name,
    horizon_name,
    symbol,
    snapshot_label,
    ROUND(score_delta_vs_previous, 4) AS score_delta_vs_previous,
    ROUND(close_return_pct_vs_previous_snapshot, 4) AS close_return_pct_vs_previous_snapshot,
    rank,
    rank_improvement_vs_previous,
    direction,
    confidence,
    setup
FROM vw_profile_horizon_snapshot_deltas
WHERE analysis_run_id = (
        SELECT analysis_run_id
        FROM latest_analysis
    )
    AND score_delta_vs_previous IS NOT NULL
    AND close_return_pct_vs_previous_snapshot IS NOT NULL
    AND score_price_alignment_flag = -1
ORDER BY profile_name,
    horizon_name,
    symbol,
    snapshot_label
LIMIT 100;
-- @block q_breakout_long_historical_sticky
-- breakout_long persistence: repeated top ranks with improving scores (historical DB).
WITH latest_analysis AS (
    SELECT analysis_run_id
    FROM historical_analysis_runs
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT profile_name,
    horizon_name,
    symbol,
    company_name,
    snapshots_seen,
    ROUND(presence_ratio, 4) AS presence_ratio,
    last_rank,
    ROUND(last_score, 4) AS last_score,
    ROUND(score_delta_total, 4) AS score_delta_total,
    ROUND(close_return_pct_total, 4) AS close_return_pct_total
FROM vw_profile_horizon_progression_core
WHERE analysis_run_id = (
        SELECT analysis_run_id
        FROM latest_analysis
    )
    AND profile_name = 'breakout_long'
    AND horizon_name = 'weeks'
    AND snapshots_seen >= 3
    AND presence_ratio >= 0.5
    AND last_rank <= 30
    AND score_delta_total > 0
ORDER BY last_rank,
    score_delta_total DESC NULLS LAST
LIMIT 50;