-- DuckDB query session for TradingView move prediction POOL analysis
-- Connection target: move_prediction_run_pool database (pooled_move_prediction_runs.duckdb)
-- Purpose: Analyze aggregated multi-week/run data for profile performance and pattern discovery

-- ============================================================
-- 1. DATABASE INVENTORY & METADATA
-- ============================================================
-- @block
-- Show all tables in the pooled database
SELECT table_name,
    table_type
FROM information_schema.tables
WHERE table_schema = 'main'
ORDER BY table_name;

-- @block
-- Pool aggregation summary
SELECT
    pool_aggregation_id,
    created_at_utc,
    source_database_count,
    source_run_count,
    chunk_count,
    notes
FROM pool_aggregation_runs
ORDER BY created_at_utc DESC
LIMIT 5;

-- @block
-- Source databases inventory (weeks included)
SELECT
    source_iso_year as year,
    source_iso_week as week,
    COUNT(*) as databases,
    SUM(run_count) as total_runs,
    MIN(ingested_at_utc) as first_ingested,
    MAX(ingested_at_utc) as last_ingested
FROM pool_source_databases
GROUP BY source_iso_year, source_iso_week
ORDER BY source_iso_year DESC, source_iso_week DESC;

-- ============================================================
-- 2. TOP PERFORMING PROFILES - BY SCORE METRICS
-- ============================================================
-- @block
-- Profile performance ranking (weeks horizon primary)
SELECT
    profile_name,
    COUNT(DISTINCT run_id) as runs_present,
    COUNT(DISTINCT symbol) as total_symbols_scored,
    AVG(score) as avg_score,
    AVG(risk_adjusted_score) as avg_risk_adjusted_score,
    AVG(confidence) as avg_confidence,
    AVG(coverage) as avg_coverage,
    STDDEV(score) as score_volatility,
    AVG(CASE WHEN direction > 0 THEN 1.0 ELSE 0.0 END) as bullish_bias_ratio,
    AVG(CASE WHEN confidence >= 0.7 THEN 1.0 ELSE 0.0 END) as high_confidence_ratio,
    AVG(CASE WHEN score >= 80 THEN 1.0 ELSE 0.0 END) as high_score_ratio,
    MAX(score) as max_score_achieved,
    COUNT(DISTINCT sector) as sectors_covered
FROM pool_profile_horizon_scores
WHERE horizon_name = 'weeks'
GROUP BY profile_name
ORDER BY avg_risk_adjusted_score DESC NULLS LAST;

-- @block
-- Profile performance by horizon
SELECT
    profile_name,
    horizon_name,
    COUNT(*) as rows,
    AVG(score) as avg_score,
    AVG(risk_adjusted_score) as avg_risk_adj,
    AVG(confidence) as avg_confidence,
    AVG(CASE WHEN direction > 0 THEN 1.0 ELSE 0.0 END) as bullish_ratio
FROM pool_profile_horizon_scores
GROUP BY profile_name, horizon_name
ORDER BY profile_name,
    CASE horizon_name
        WHEN 'days' THEN 1
        WHEN 'weeks' THEN 2
        WHEN 'months' THEN 3
        WHEN 'years' THEN 4
        ELSE 5
    END;

-- ============================================================
-- 3. STOCK COMPOSITE RANKINGS - MULTI-PROFILE CONSENSUS
-- ============================================================
-- @block
-- Top stocks by composite ranking (appears in multiple profiles)
WITH stock_aggregates AS (
    SELECT
        symbol,
        MAX(company) as company,
        MAX(sector) as sector,
        MAX(industry) as industry,
        COUNT(DISTINCT profile_name) as profile_appearances,
        COUNT(DISTINCT run_id) as run_appearances,
        COUNT(DISTINCT CASE WHEN direction > 0 THEN profile_name END) as bullish_profiles,
        COUNT(DISTINCT CASE WHEN direction < 0 THEN profile_name END) as bearish_profiles,
        AVG(score) as avg_score,
        MAX(score) as max_score,
        AVG(risk_adjusted_score) as avg_risk_adj,
        MAX(risk_adjusted_score) as max_risk_adj,
        AVG(confidence) as avg_confidence,
        AVG(direction) as avg_direction_bias,
        SUM(CASE WHEN direction > 0 THEN 1 ELSE 0 END) as long_votes,
        SUM(CASE WHEN direction < 0 THEN 1 ELSE 0 END) as short_votes,
        MODE(profile_name) as most_common_profile
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND score IS NOT NULL
    GROUP BY symbol
    HAVING COUNT(DISTINCT profile_name) >= 3  -- Require multi-profile coverage
)
SELECT
    symbol,
    company,
    sector,
    profile_appearances,
    run_appearances,
    bullish_profiles,
    bearish_profiles,
    avg_score,
    avg_risk_adj,
    avg_confidence,
    long_votes,
    short_votes,
    -- Composite ranking formula
    (avg_risk_adj * 0.5 + avg_score * 0.3 + avg_confidence * 10 * 0.2)
        * (1 + LEAST(profile_appearances / 10, 0.5)) as composite_rank,
    -- Consensus signal strength
    CASE
        WHEN bullish_profiles > bearish_profiles * 2 AND long_votes > short_votes * 2 THEN 'STRONG_LONG'
        WHEN bullish_profiles > bearish_profiles AND long_votes > short_votes THEN 'LONG'
        WHEN bearish_profiles > bullish_profiles AND short_votes > long_votes THEN 'SHORT'
        WHEN bearish_profiles > bullish_profiles * 2 AND short_votes > long_votes * 2 THEN 'STRONG_SHORT'
        ELSE 'MIXED'
    END as consensus_signal,
    most_common_profile as strongest_predictor_profile
FROM stock_aggregates
ORDER BY composite_rank DESC NULLS LAST
LIMIT 200;

-- @block
-- Best stocks by specific profile (change profile_name as needed)
SELECT
    symbol,
    MAX(company) as company,
    MAX(sector) as sector,
    AVG(score) as avg_score,
    AVG(risk_adjusted_score) as avg_risk_adj,
    AVG(confidence) as avg_confidence,
    COUNT(DISTINCT run_id) as appearances
FROM pool_profile_horizon_scores
WHERE profile_name = 'breakout_long'  -- Change to desired profile
    AND horizon_name = 'weeks'
GROUP BY symbol
HAVING COUNT(DISTINCT run_id) >= 2  -- Appeared in at least 2 runs
ORDER BY avg_risk_adj DESC NULLS LAST
LIMIT 100;

-- @block
-- Sector leaders by composite score
WITH sector_scores AS (
    SELECT
        sector,
        symbol,
        MAX(company) as company,
        AVG(risk_adjusted_score) as avg_risk_adj,
        AVG(score) as avg_score,
        COUNT(DISTINCT profile_name) as profile_count
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND sector IS NOT NULL
    GROUP BY sector, symbol
    HAVING COUNT(DISTINCT profile_name) >= 2
)
SELECT *
FROM (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY sector ORDER BY avg_risk_adj DESC) as rank_in_sector
    FROM sector_scores
)
WHERE rank_in_sector <= 10
ORDER BY sector, rank_in_sector;

-- ============================================================
-- 4. PATTERN PREDICTOR DISCOVERY - RAW FIELD CORRELATIONS
-- ============================================================
-- @block
-- Correlation between raw momentum fields and profile scores
WITH scored_with_raw AS (
    SELECT
        s.profile_name,
        s.symbol,
        s.score,
        s.risk_adjusted_score,
        s.direction,
        TRY_CAST(r.RSI AS DOUBLE) as rsi,
        TRY_CAST(r.Perf.W AS DOUBLE) as perf_w,
        TRY_CAST(r.Perf.1M AS DOUBLE) as perf_1m,
        TRY_CAST(r.Perf.3M AS DOUBLE) as perf_3m,
        TRY_CAST(r.momentum AS DOUBLE) as momentum_component,
        TRY_CAST(r.trend AS DOUBLE) as trend_component
    FROM pool_profile_horizon_scores s
    INNER JOIN pool_raw_scan_rows r
        ON s.run_id = r.run_id
        AND (s.symbol = r.symbol
            OR r.symbol LIKE '%:' || s.symbol
            OR s.symbol LIKE '%:' || r.symbol)
    WHERE s.horizon_name = 'weeks'
)
SELECT
    profile_name,
    CORR(score, rsi) as score_rsi_corr,
    CORR(score, perf_w) as score_perf_w_corr,
    CORR(score, perf_1m) as score_perf_1m_corr,
    CORR(score, perf_3m) as score_perf_3m_corr,
    CORR(score, momentum_component) as score_momentum_corr,
    CORR(score, trend_component) as score_trend_corr,
    CORR(risk_adjusted_score, momentum_component) as riskadj_momentum_corr,
    CORR(direction, rsi) as direction_rsi_corr,
    -- Combined predictive power (sum of absolute correlations)
    ABS(CORR(score, rsi)) +
    ABS(CORR(score, momentum_component)) +
    ABS(CORR(risk_adjusted_score, momentum_component)) as combined_momentum_power
FROM scored_with_raw
GROUP BY profile_name
HAVING CORR(score, rsi) IS NOT NULL
ORDER BY combined_momentum_power DESC NULLS LAST;

-- @block
-- Which raw fields best predict high scores for each profile
-- (Requires inspecting available raw fields first)
SELECT
    column_name,
    data_type,
    COUNT(*) as row_count
FROM information_schema.columns
WHERE table_name = 'pool_raw_scan_rows'
    AND column_name NOT IN ('pool_aggregation_id', 'source_database_path',
        'source_iso_year', 'source_iso_week', 'run_id', 'row_number', 'symbol')
    AND data_type IN ('FLOAT', 'DOUBLE', 'INTEGER', 'BIGINT', 'DECIMAL')
ORDER BY column_name;

-- @block
-- Component analysis for a specific profile
-- Shows how profile component scores relate to final score
SELECT
    c.profile_name,
    c.symbol,
    MAX(c.company) as company,
    AVG(c.momentum) as avg_momentum,
    AVG(c.trend) as avg_trend,
    AVG(c.quality) as avg_quality,
    AVG(c.valuation) as avg_valuation,
    AVG(c.safety) as avg_safety,
    AVG(c.scale) as avg_scale,
    AVG(c.attention) as avg_attention,
    AVG(c.event) as avg_event,
    AVG(s.score) as avg_final_score,
    AVG(s.risk_adjusted_score) as avg_risk_adj,
    CORR(c.momentum, s.score) as momentum_score_corr,
    CORR(c.trend, s.score) as trend_score_corr,
    CORR(c.quality, s.score) as quality_score_corr,
    CORR(c.valuation, s.score) as valuation_score_corr
FROM pool_profile_components c
INNER JOIN pool_profile_horizon_scores s
    ON c.run_id = s.run_id
    AND c.profile_name = s.profile_name
    AND c.symbol = s.symbol
WHERE c.profile_name = 'breakout_long'  -- Change profile as needed
GROUP BY c.profile_name, c.symbol
ORDER BY avg_risk_adj DESC NULLS LAST
LIMIT 50;

-- ============================================================
-- 5. SCORE vs PRICE PERFORMANCE INTERSECTION ANALYSIS
-- ============================================================
-- @block
-- High score + positive momentum intersection (winners)
WITH high_scorers AS (
    SELECT DISTINCT
        symbol,
        MAX(risk_adjusted_score) as max_score,
        AVG(score) as avg_score,
        COUNT(DISTINCT profile_name) as profile_count
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND risk_adjusted_score >= 75
    GROUP BY symbol
    HAVING COUNT(DISTINCT profile_name) >= 2
),
price_performers AS (
    SELECT DISTINCT
        r.symbol,
        TRY_CAST(r.Perf.W AS DOUBLE) as perf_w,
        TRY_CAST(r.Perf.1M AS DOUBLE) as perf_1m,
        TRY_CAST(r.RSI AS DOUBLE) as rsi,
        TRY_CAST(r.momentum AS DOUBLE) as momentum
    FROM pool_raw_scan_rows r
    WHERE (TRY_CAST(r.Perf.W AS DOUBLE) > 0 OR TRY_CAST(r.Perf.1M AS DOUBLE) > 0)
        AND TRY_CAST(r.RSI AS DOUBLE) BETWEEN 40 AND 80
)
SELECT
    h.symbol,
    h.max_score,
    h.avg_score,
    h.profile_count,
    p.perf_w,
    p.perf_1m,
    p.rsi,
    p.momentum,
    'HIGH_SCORE_POSITIVE_MOMENTUM' as classification
FROM high_scorers h
INNER JOIN price_performers p ON h.symbol = p.symbol
ORDER BY h.max_score DESC, p.perf_w DESC NULLS LAST
LIMIT 100;

-- @block
-- High score but poor momentum (potential false positives / early signals)
WITH high_scorers AS (
    SELECT DISTINCT
        symbol,
        MAX(risk_adjusted_score) as max_score,
        AVG(score) as avg_score,
        COUNT(DISTINCT profile_name) as profile_count
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND risk_adjusted_score >= 75
    GROUP BY symbol
    HAVING COUNT(DISTINCT profile_name) >= 2
),
poor_performers AS (
    SELECT DISTINCT symbol
    FROM pool_raw_scan_rows
    WHERE (TRY_CAST(Perf.W AS DOUBLE) < 0 OR TRY_CAST(Perf.1M AS DOUBLE) < 0)
        OR TRY_CAST(RSI AS DOUBLE) < 40
        OR TRY_CAST(RSI AS DOUBLE) > 70
)
SELECT
    h.symbol,
    h.max_score,
    h.avg_score,
    h.profile_count,
    'HIGH_SCORE_CHECK_PRICE' as status,
    'May be early signal or false positive' as note
FROM high_scorers h
LEFT JOIN (
    SELECT DISTINCT symbol FROM pool_raw_scan_rows
    WHERE TRY_CAST(Perf.W AS DOUBLE) > 0
) good ON h.symbol = good.symbol
WHERE good.symbol IS NULL
ORDER BY h.max_score DESC
LIMIT 50;

-- @block
-- Score/price divergence analysis by profile
WITH profile_high_scores AS (
    SELECT
        profile_name,
        symbol,
        score,
        risk_adjusted_score,
        direction
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND risk_adjusted_score >= 70
)
SELECT
    phs.profile_name,
    COUNT(*) as high_score_count,
    AVG(TRY_CAST(r.Perf.W AS DOUBLE)) as avg_perf_w,
    AVG(TRY_CAST(r.Perf.1M AS DOUBLE)) as avg_perf_1m,
    CORR(phs.risk_adjusted_score, TRY_CAST(r.Perf.W AS DOUBLE)) as score_price_corr_w,
    CORR(phs.risk_adjusted_score, TRY_CAST(r.Perf.1M AS DOUBLE)) as score_price_corr_1m,
    CASE
        WHEN CORR(phs.risk_adjusted_score, TRY_CAST(r.Perf.W AS DOUBLE)) > 0.3 THEN 'ALIGNED'
        WHEN CORR(phs.risk_adjusted_score, TRY_CAST(r.Perf.W AS DOUBLE)) < -0.1 THEN 'CONTRARIAN'
        ELSE 'NEUTRAL'
    END as alignment_status
FROM profile_high_scores phs
LEFT JOIN pool_raw_scan_rows r
    ON phs.symbol = r.symbol
    OR r.symbol LIKE '%:' || phs.symbol
    OR phs.symbol LIKE '%:' || r.symbol
GROUP BY phs.profile_name
ORDER BY score_price_corr_w DESC NULLS LAST;

-- ============================================================
-- 6. TEMPORAL ANALYSIS - PERFORMANCE ACROSS WEEKS
-- ============================================================
-- @block
-- Profile performance consistency across weeks (weeks horizon)
SELECT
    profile_name,
    source_iso_week as week,
    COUNT(DISTINCT symbol) as symbols_scored,
    AVG(score) as avg_score,
    AVG(risk_adjusted_score) as avg_risk_adj,
    AVG(confidence) as avg_confidence,
    AVG(CASE WHEN direction > 0 THEN 1.0 ELSE 0.0 END) as bullish_pct,
    STDDEV(score) as score_stddev,
    MAX(score) as max_score
FROM pool_profile_horizon_scores
    JOIN pool_run_metadata USING (run_id, pool_aggregation_id, source_database_path)
WHERE horizon_name = 'weeks'
GROUP BY profile_name, source_iso_week
ORDER BY profile_name, source_iso_week;

-- @block
-- Week-by-week consensus leaders
WITH weekly_scores AS (
    SELECT
        source_iso_week as week,
        symbol,
        MAX(company) as company,
        MAX(sector) as sector,
        COUNT(DISTINCT profile_name) as profile_count,
        AVG(risk_adjusted_score) as avg_risk_adj,
        SUM(CASE WHEN direction > 0 THEN 1 ELSE 0 END) as long_votes,
        COUNT(*) as total_votes
    FROM pool_profile_horizon_scores
        JOIN pool_run_metadata USING (run_id, pool_aggregation_id, source_database_path)
    WHERE horizon_name = 'weeks'
    GROUP BY source_iso_week, symbol
    HAVING COUNT(DISTINCT profile_name) >= 4
)
SELECT *
FROM (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY week ORDER BY avg_risk_adj DESC) as weekly_rank,
        CAST(long_votes AS DOUBLE) / NULLIF(total_votes, 0) as consensus_ratio
    FROM weekly_scores
)
WHERE weekly_rank <= 20
ORDER BY week DESC, weekly_rank;

-- @block
-- Profile persistence score (consistency across weeks)
WITH weekly_profile_scores AS (
    SELECT
        profile_name,
        source_iso_week as week,
        AVG(risk_adjusted_score) as weekly_avg_score
    FROM pool_profile_horizon_scores
        JOIN pool_run_metadata USING (run_id, pool_aggregation_id, source_database_path)
    WHERE horizon_name = 'weeks'
    GROUP BY profile_name, source_iso_week
)
SELECT
    profile_name,
    COUNT(DISTINCT week) as weeks_present,
    AVG(weekly_avg_score) as overall_avg_score,
    STDDEV(weekly_avg_score) as week_to_week_volatility,
    MIN(weekly_avg_score) as worst_week_score,
    MAX(weekly_avg_score) as best_week_score,
    (AVG(weekly_avg_score) - STDDEV(weekly_avg_score)) as persistence_score,
    CASE
        WHEN STDDEV(weekly_avg_score) < 2 THEN 'VERY_STABLE'
        WHEN STDDEV(weekly_avg_score) < 5 THEN 'STABLE'
        WHEN STDDEV(weekly_avg_score) < 10 THEN 'MODERATE'
        ELSE 'VOLATILE'
    END as stability_rating
FROM weekly_profile_scores
GROUP BY profile_name
HAVING COUNT(DISTINCT week) >= 2
ORDER BY persistence_score DESC NULLS LAST;

-- ============================================================
-- 7. SECTOR & INDUSTRY ANALYSIS
-- ============================================================
-- @block
-- Sector performance by profile
SELECT
    profile_name,
    sector,
    COUNT(DISTINCT symbol) as symbol_count,
    COUNT(DISTINCT industry) as industries,
    AVG(score) as avg_score,
    AVG(risk_adjusted_score) as avg_risk_adj,
    AVG(confidence) as avg_confidence,
    AVG(CASE WHEN direction > 0 THEN 1.0 ELSE 0.0 END) as bullish_pct,
    STDDEV(score) as score_dispersion,
    -- Rank sectors within each profile
    ROW_NUMBER() OVER (
        PARTITION BY profile_name
        ORDER BY AVG(risk_adjusted_score) DESC
    ) as rank_in_profile
FROM pool_profile_horizon_scores
WHERE horizon_name = 'weeks'
    AND sector IS NOT NULL
GROUP BY profile_name, sector
HAVING COUNT(DISTINCT symbol) >= 5
ORDER BY profile_name, avg_risk_adj DESC NULLS LAST;

-- @block
-- Best performing industries (multi-profile consensus)
SELECT
    industry,
    sector,
    COUNT(DISTINCT symbol) as symbols,
    COUNT(DISTINCT profile_name) as profiles_covering,
    AVG(score) as avg_score,
    AVG(risk_adjusted_score) as avg_risk_adj,
    AVG(confidence) as avg_confidence,
    AVG(CASE WHEN direction > 0 THEN 1.0 ELSE 0.0 END) as bullish_consensus
FROM pool_profile_horizon_scores
WHERE horizon_name = 'weeks'
    AND industry IS NOT NULL
GROUP BY industry, sector
HAVING COUNT(DISTINCT profile_name) >= 3
    AND COUNT(DISTINCT symbol) >= 3
ORDER BY avg_risk_adj DESC NULLS LAST
LIMIT 50;

-- ============================================================
-- 8. CONFIDENCE & COVERAGE ANALYSIS
-- ============================================================
-- @block
-- Profile confidence statistics
SELECT
    profile_name,
    horizon_name,
    AVG(confidence) as mean_confidence,
    MEDIAN(confidence) as median_confidence,
    MIN(confidence) as min_confidence,
    MAX(confidence) as max_confidence,
    PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY confidence) as p10_confidence,
    PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY confidence) as p90_confidence,
    AVG(CASE WHEN confidence >= 0.7 THEN 1.0 ELSE 0.0 END) as high_confidence_rate,
    AVG(CASE WHEN confidence >= 0.5 THEN 1.0 ELSE 0.0 END) as medium_plus_confidence_rate,
    COUNT(*) FILTER (WHERE confidence >= 0.7 AND score >= 75) as high_conf_high_score_count
FROM pool_profile_horizon_scores
GROUP BY profile_name, horizon_name
ORDER BY profile_name, horizon_name;

-- @block
-- Coverage analysis - which profiles cover which stocks most consistently
WITH coverage_stats AS (
    SELECT
        symbol,
        MAX(company) as company,
        MAX(sector) as sector,
        COUNT(DISTINCT profile_name) as profiles_covering,
        COUNT(DISTINCT run_id) as runs_present,
        AVG(coverage) as avg_coverage
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
    GROUP BY symbol
)
SELECT
    symbol,
    company,
    sector,
    profiles_covering,
    runs_present,
    avg_coverage,
    CASE
        WHEN profiles_covering >= 8 THEN 'FULL_COVERAGE'
        WHEN profiles_covering >= 5 THEN 'HIGH_COVERAGE'
        WHEN profiles_covering >= 3 THEN 'MODERATE_COVERAGE'
        ELSE 'LOW_COVERAGE'
    END as coverage_tier
FROM coverage_stats
ORDER BY profiles_covering DESC, runs_present DESC
LIMIT 100;

-- ============================================================
-- 9. MANAGER ACTION SIGNAL ANALYSIS
-- ============================================================
-- @block
-- Manager action signal distribution by profile
SELECT
    profile_name,
    manager_action_signal,
    COUNT(*) as count,
    AVG(score) as avg_score,
    AVG(risk_adjusted_score) as avg_risk_adj,
    AVG(confidence) as avg_confidence,
    COUNT(DISTINCT symbol) as unique_symbols
FROM pool_profile_horizon_scores
WHERE horizon_name = 'weeks'
    AND manager_action_signal IS NOT NULL
GROUP BY profile_name, manager_action_signal
ORDER BY profile_name,
    CASE manager_action_signal
        WHEN 'STRONG_BUY' THEN 1
        WHEN 'BUY' THEN 2
        WHEN 'HOLD' THEN 3
        WHEN 'REDUCE' THEN 4
        WHEN 'SELL' THEN 5
        ELSE 6
    END;

-- @block
-- Strong signals consensus (symbols with multiple STRONG_BUY signals)
WITH strong_signals AS (
    SELECT
        symbol,
        MAX(company) as company,
        MAX(sector) as sector,
        COUNT(*) as strong_buy_signals,
        COUNT(DISTINCT profile_name) as unique_profiles,
        AVG(score) as avg_score,
        AVG(risk_adjusted_score) as avg_risk_adj
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND manager_action_signal IN ('STRONG_BUY', 'BUY')
    GROUP BY symbol
    HAVING COUNT(*) >= 2
)
SELECT
    symbol,
    company,
    sector,
    strong_buy_signals,
    unique_profiles,
    avg_score,
    avg_risk_adj,
    DENSE_RANK() OVER (ORDER BY avg_risk_adj DESC) as rank
FROM strong_signals
ORDER BY avg_risk_adj DESC NULLS LAST
LIMIT 50;
