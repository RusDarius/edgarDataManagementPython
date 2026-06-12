-- DuckDB query session for TradingView move prediction POOL analysis
-- Connection: pooled_move_prediction_runs.duckdb
-- Run one @block at a time in DBCode/SQLTools — click the q_pool_* line only.
-- Block documentation: move_prediction_pool_analysis.block_reference.txt
-- ============================================================
-- 1. DATABASE INVENTORY & METADATA
-- ============================================================
-- @block q_pool_list_tables
SELECT table_name,
    table_type
FROM information_schema.tables
WHERE table_schema = 'main'
ORDER BY table_name;
-- @block q_pool_aggregation_summary
SELECT pool_aggregation_id,
    created_at_utc,
    source_database_count,
    source_run_count,
    chunk_count,
    notes
FROM pool_aggregation_runs
ORDER BY created_at_utc DESC
LIMIT 5;
-- @block q_pool_source_inventory
SELECT source_iso_year AS year,
    source_iso_week AS week,
    COUNT(*) AS databases,
    SUM(run_count) AS total_runs,
    MIN(ingested_at_utc) AS first_ingested,
    MAX(ingested_at_utc) AS last_ingested
FROM pool_source_databases
GROUP BY source_iso_year,
    source_iso_week
ORDER BY source_iso_year DESC,
    source_iso_week DESC;
-- ============================================================
-- 2. TOP PERFORMING PROFILES - BY SCORE METRICS
-- ============================================================
-- @block q_pool_profile_performance_weeks
SELECT profile_name,
    COUNT(DISTINCT run_id) AS runs_present,
    COUNT(DISTINCT symbol) AS total_symbols_scored,
    AVG(score) AS avg_score,
    AVG(risk_adjusted_score) AS avg_risk_adjusted_score,
    AVG(confidence) AS avg_confidence,
    AVG(coverage) AS avg_coverage,
    STDDEV(score) AS score_volatility,
    AVG(
        CASE
            WHEN direction IN ('Strong Up', 'Up') THEN 1.0
            ELSE 0.0
        END
    ) AS bullish_bias_ratio,
    AVG(
        CASE
            WHEN confidence >= 70 THEN 1.0
            ELSE 0.0
        END
    ) AS high_confidence_ratio,
    AVG(
        CASE
            WHEN score >= 1.10 THEN 1.0
            ELSE 0.0
        END
    ) AS strong_up_ratio,
    MAX(score) AS max_score_achieved,
    COUNT(DISTINCT sector) AS sectors_covered
FROM pool_profile_horizon_scores
WHERE horizon_name = 'weeks'
GROUP BY profile_name
ORDER BY avg_risk_adjusted_score DESC NULLS LAST;
-- @block q_pool_profile_performance_by_horizon
SELECT profile_name,
    horizon_name,
    COUNT(*) AS rows,
    AVG(score) AS avg_score,
    AVG(risk_adjusted_score) AS avg_risk_adj,
    AVG(confidence) AS avg_confidence,
    AVG(
        CASE
            WHEN direction IN ('Strong Up', 'Up') THEN 1.0
            ELSE 0.0
        END
    ) AS bullish_ratio
FROM pool_profile_horizon_scores
GROUP BY profile_name,
    horizon_name
ORDER BY profile_name,
    CASE
        horizon_name
        WHEN 'days' THEN 1
        WHEN 'weeks' THEN 2
        WHEN 'months' THEN 3
        WHEN 'years' THEN 4
        ELSE 5
    END;
-- @block q_pool_profile_weekly_score_price_layer
-- Run: use "Run block" codelens on this line, or put cursor on WITH below. Ctrl+Enter on @block alone is empty.
WITH scored_with_price AS (
    SELECT s.profile_name,
        s.source_iso_year,
        s.source_iso_week,
        s.horizon_name,
        s.run_id,
        s.symbol,
        s.score,
        s.risk_adjusted_score,
        s.direction,
        CASE
            WHEN s.direction IN ('Strong Up', 'Up') THEN 1
            WHEN s.direction IN ('Strong Down', 'Down') THEN -1
            ELSE 0
        END AS direction_sign,
        TRY_CAST(r.change AS DOUBLE) AS change_pct,
        TRY_CAST(r."Perf.W" AS DOUBLE) AS perf_w_pct,
        TRY_CAST(r."Perf.1M" AS DOUBLE) AS perf_1m_pct,
        TRY_CAST(r."Perf.5D" AS DOUBLE) AS perf_5d_pct
    FROM pool_profile_horizon_scores AS s
        INNER JOIN pool_raw_scan_rows AS r ON s.pool_aggregation_id = r.pool_aggregation_id
        AND s.source_database_path = r.source_database_path
        AND s.run_id = r.run_id
        AND (
            s.symbol = r.symbol
            OR r.symbol LIKE '%:' || s.symbol
            OR s.symbol LIKE '%:' || r.symbol
        )
    WHERE s.horizon_name = 'weeks'
),
cohorted AS (
    SELECT *,
        NTILE(10) OVER (
            PARTITION BY profile_name,
            source_iso_year,
            source_iso_week,
            horizon_name
            ORDER BY score DESC NULLS LAST
        ) AS score_decile
    FROM scored_with_price
    WHERE score IS NOT NULL
)
SELECT profile_name,
    source_iso_year AS year,
    source_iso_week AS week,
    horizon_name,
    COUNT(DISTINCT symbol) AS symbols_scored,
    COUNT(DISTINCT run_id) AS runs_in_week,
    AVG(score) AS avg_score,
    AVG(risk_adjusted_score) AS avg_risk_adj,
    AVG(change_pct) AS avg_same_day_change_pct,
    AVG(perf_w_pct) AS avg_perf_w_pct,
    AVG(perf_1m_pct) AS avg_perf_1m_pct,
    AVG(perf_5d_pct) AS avg_perf_5d_pct,
    CORR(score, change_pct) AS week_score_change_corr,
    CORR(score, perf_w_pct) AS week_score_perf_w_corr,
    CORR(risk_adjusted_score, perf_w_pct) AS week_ras_perf_w_corr,
    CORR(risk_adjusted_score, perf_1m_pct) AS week_ras_perf_1m_corr,
    AVG(
        CASE
            WHEN score_decile = 1 THEN perf_w_pct
        END
    ) AS top_decile_avg_perf_w,
    AVG(
        CASE
            WHEN score_decile = 10 THEN perf_w_pct
        END
    ) AS bottom_decile_avg_perf_w,
    AVG(
        CASE
            WHEN direction_sign > 0
            AND perf_w_pct > 0 THEN 1.0
            WHEN direction_sign > 0
            AND perf_w_pct <= 0 THEN 0.0
        END
    ) AS bullish_perf_w_hit_rate,
    AVG(
        CASE
            WHEN direction_sign < 0
            AND perf_w_pct < 0 THEN 1.0
            WHEN direction_sign < 0
            AND perf_w_pct >= 0 THEN 0.0
        END
    ) AS bearish_perf_w_hit_rate
FROM cohorted
GROUP BY profile_name,
    source_iso_year,
    source_iso_week,
    horizon_name
HAVING COUNT(*) >= 20
ORDER BY profile_name,
    source_iso_year DESC,
    source_iso_week DESC;
-- @block q_pool_profile_strongest_score_price_corr
-- Run: use "Run block" codelens on this line, or put cursor on WITH below. Ctrl+Enter on @block alone is empty.
-- Threshold ladder: ALL baseline, then score >= 0.35 / 1.10 / 1.50 / 2.00, plus TOP_DECILE per ISO week.
WITH scored_with_price AS (
    SELECT s.profile_name,
        s.horizon_name,
        s.source_iso_year,
        s.source_iso_week,
        s.run_id,
        s.symbol,
        s.score,
        s.risk_adjusted_score,
        CASE
            WHEN s.direction IN ('Strong Up', 'Up') THEN 1
            WHEN s.direction IN ('Strong Down', 'Down') THEN -1
            ELSE 0
        END AS direction_sign,
        TRY_CAST(r.change AS DOUBLE) AS change_pct,
        TRY_CAST(r."Perf.W" AS DOUBLE) AS perf_w_pct,
        TRY_CAST(r."Perf.1M" AS DOUBLE) AS perf_1m_pct,
        TRY_CAST(r."Perf.5D" AS DOUBLE) AS perf_5d_pct
    FROM pool_profile_horizon_scores AS s
        INNER JOIN pool_raw_scan_rows AS r ON s.pool_aggregation_id = r.pool_aggregation_id
        AND s.source_database_path = r.source_database_path
        AND s.run_id = r.run_id
        AND (
            s.symbol = r.symbol
            OR r.symbol LIKE '%:' || s.symbol
            OR s.symbol LIKE '%:' || r.symbol
        )
    WHERE s.horizon_name = 'weeks'
        AND s.score IS NOT NULL
),
deciled AS (
    SELECT *,
        NTILE(10) OVER (
            PARTITION BY profile_name,
            source_iso_year,
            source_iso_week
            ORDER BY score DESC NULLS LAST
        ) AS score_decile
    FROM scored_with_price
),
score_thresholds AS (
    SELECT *
    FROM (
            VALUES ('ALL', NULL::DOUBLE, 0),
                ('BULLISH_UP', 0.35, 1),
                ('STRONG_UP', 1.10, 2),
                ('VERY_HIGH', 1.50, 3),
                ('ELITE', 2.00, 4),
                ('TOP_DECILE', NULL::DOUBLE, 5)
        ) AS t(score_slice, min_score, slice_order)
),
sliced AS (
    SELECT s.profile_name,
        s.horizon_name,
        s.source_iso_year,
        s.source_iso_week,
        s.run_id,
        s.symbol,
        s.score,
        s.risk_adjusted_score,
        s.direction_sign,
        s.change_pct,
        s.perf_w_pct,
        s.perf_1m_pct,
        s.perf_5d_pct,
        t.score_slice,
        t.min_score,
        t.slice_order
    FROM deciled AS s
        CROSS JOIN score_thresholds AS t
    WHERE (t.score_slice = 'ALL')
        OR (
            t.score_slice = 'TOP_DECILE'
            AND s.score_decile = 1
        )
        OR (
            t.min_score IS NOT NULL
            AND s.score >= t.min_score
        )
),
slice_corrs AS (
    SELECT profile_name,
        horizon_name,
        score_slice,
        min_score,
        slice_order,
        COUNT(*) AS pair_count,
        COUNT(
            DISTINCT source_iso_year || '-' || source_iso_week
        ) AS snapshot_weeks,
        COUNT(perf_w_pct) AS perf_w_obs_count,
        COUNT(perf_1m_pct) AS perf_1m_obs_count,
        ROUND(STDDEV_SAMP(direction_sign), 4) AS direction_sign_stddev,
        ROUND(AVG(score), 3) AS avg_score_in_slice,
        ROUND(AVG(risk_adjusted_score), 3) AS avg_ras_in_slice,
        ROUND(AVG(perf_w_pct), 3) AS avg_perf_w_pct,
        ROUND(AVG(perf_1m_pct), 3) AS avg_perf_1m_pct,
        ROUND(
            AVG(
                CASE
                    WHEN direction_sign > 0
                    AND perf_w_pct > 0 THEN 1.0
                    WHEN direction_sign > 0
                    AND perf_w_pct <= 0 THEN 0.0
                END
            ),
            3
        ) AS bullish_perf_w_hit_rate,
        CORR(score, change_pct) AS score_vs_change_corr,
        CORR(score, perf_w_pct) AS score_vs_perf_w_corr,
        CORR(score, perf_1m_pct) AS score_vs_perf_1m_corr,
        CORR(score, perf_5d_pct) AS score_vs_perf_5d_corr,
        CORR(risk_adjusted_score, perf_w_pct) AS ras_vs_perf_w_corr,
        CORR(risk_adjusted_score, perf_1m_pct) AS ras_vs_perf_1m_corr,
        CASE
            WHEN STDDEV_SAMP(direction_sign) > 0 THEN CORR(direction_sign, perf_w_pct)
        END AS direction_vs_perf_w_corr
    FROM sliced
    GROUP BY profile_name,
        horizon_name,
        score_slice,
        min_score,
        slice_order
    HAVING COUNT(*) >= CASE
            score_slice
            WHEN 'ALL' THEN 50
            WHEN 'ELITE' THEN 10
            WHEN 'TOP_DECILE' THEN 15
            ELSE 20
        END
),
weekly_slice_corrs AS (
    SELECT profile_name,
        horizon_name,
        score_slice,
        source_iso_year,
        source_iso_week,
        COUNT(*) AS week_pair_count,
        COUNT(perf_w_pct) AS week_perf_w_obs_count,
        CORR(score, perf_w_pct) AS week_score_perf_w_corr,
        CORR(risk_adjusted_score, perf_w_pct) AS week_ras_perf_w_corr,
        AVG(perf_w_pct) AS week_avg_perf_w_pct
    FROM sliced
    GROUP BY profile_name,
        horizon_name,
        score_slice,
        source_iso_year,
        source_iso_week
    HAVING COUNT(*) >= CASE score_slice
            WHEN 'ALL' THEN 8
            WHEN 'ELITE' THEN 3
            WHEN 'VERY_HIGH' THEN 4
            ELSE 5
        END
        AND COUNT(perf_w_pct) >= CASE score_slice
            WHEN 'ELITE' THEN 3
            ELSE 5
        END
),
snapshot_stats AS (
    SELECT profile_name,
        horizon_name,
        score_slice,
        COUNT(*) AS weekly_snapshots_with_corr,
        ROUND(AVG(week_score_perf_w_corr), 4) AS mean_weekly_score_perf_w_corr,
        ROUND(MEDIAN(week_score_perf_w_corr), 4) AS median_weekly_score_perf_w_corr,
        ROUND(
            AVG(
                CASE
                    WHEN week_score_perf_w_corr IS NOT NULL
                    AND week_score_perf_w_corr >= 0.30 THEN 1.0
                    WHEN week_score_perf_w_corr IS NOT NULL THEN 0.0
                END
            ),
            3
        ) AS pct_weeks_strong_aligned,
        ROUND(
            AVG(
                CASE
                    WHEN week_score_perf_w_corr IS NOT NULL
                    AND week_score_perf_w_corr >= 0.10 THEN 1.0
                    WHEN week_score_perf_w_corr IS NOT NULL THEN 0.0
                END
            ),
            3
        ) AS pct_weeks_moderate_aligned
    FROM weekly_slice_corrs
    WHERE week_score_perf_w_corr IS NOT NULL
        AND week_score_perf_w_corr = week_score_perf_w_corr
    GROUP BY profile_name,
        horizon_name,
        score_slice
),
baseline AS (
    SELECT profile_name,
        horizon_name,
        score_vs_perf_w_corr AS baseline_score_perf_w_corr,
        ras_vs_perf_w_corr AS baseline_ras_perf_w_corr,
        direction_vs_perf_w_corr AS baseline_direction_perf_w_corr,
        bullish_perf_w_hit_rate AS baseline_bullish_hit_rate
    FROM slice_corrs
    WHERE score_slice = 'ALL'
),
corr_long AS (
    SELECT profile_name,
        horizon_name,
        score_slice,
        slice_order,
        pair_count,
        metric_name,
        corr_value
    FROM slice_corrs UNPIVOT (
            corr_value FOR metric_name IN (
                score_vs_change_corr,
                score_vs_perf_w_corr,
                score_vs_perf_1m_corr,
                score_vs_perf_5d_corr,
                ras_vs_perf_w_corr,
                ras_vs_perf_1m_corr,
                direction_vs_perf_w_corr
            )
        )
),
valid_corr_long AS (
    SELECT *
    FROM corr_long
    WHERE corr_value IS NOT NULL
        AND corr_value = corr_value
        AND isfinite(corr_value)
),
ranked_metrics AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY profile_name,
            horizon_name,
            score_slice
            ORDER BY ABS(corr_value) DESC NULLS LAST,
                corr_value DESC NULLS LAST
        ) AS metric_rank
    FROM valid_corr_long
)
SELECT c.profile_name,
    c.horizon_name,
    c.score_slice,
    c.min_score,
    c.pair_count,
    c.snapshot_weeks,
    c.perf_w_obs_count,
    c.direction_sign_stddev,
    r.metric_name AS strongest_metric,
    ROUND(r.corr_value, 4) AS strongest_corr,
    ROUND(ABS(r.corr_value), 4) AS strongest_corr_abs,
    CASE
        WHEN r.corr_value IS NULL THEN 'NO_VALID_CORR'
        WHEN r.corr_value >= 0.30 THEN 'STRONG_ALIGNED'
        WHEN r.corr_value >= 0.10 THEN 'MODERATE_ALIGNED'
        WHEN r.corr_value <= -0.10 THEN 'CONTRARIAN'
        ELSE 'WEAK'
    END AS alignment_label,
    c.avg_score_in_slice,
    c.avg_ras_in_slice,
    c.avg_perf_w_pct,
    c.avg_perf_1m_pct,
    c.bullish_perf_w_hit_rate,
    ROUND(c.score_vs_perf_w_corr, 4) AS score_vs_perf_w_corr,
    ROUND(c.ras_vs_perf_w_corr, 4) AS ras_vs_perf_w_corr,
    ROUND(b.baseline_score_perf_w_corr, 4) AS baseline_score_perf_w_corr,
    ROUND(
        c.score_vs_perf_w_corr - b.baseline_score_perf_w_corr,
        4
    ) AS score_perf_w_lift_vs_all,
    ROUND(
        c.ras_vs_perf_w_corr - b.baseline_ras_perf_w_corr,
        4
    ) AS ras_perf_w_lift_vs_all,
    ROUND(
        c.bullish_perf_w_hit_rate - b.baseline_bullish_hit_rate,
        3
    ) AS bullish_hit_lift_vs_all,
    ss.weekly_snapshots_with_corr,
    ss.mean_weekly_score_perf_w_corr,
    ss.median_weekly_score_perf_w_corr,
    ss.pct_weeks_strong_aligned,
    ss.pct_weeks_moderate_aligned,
    CASE
        WHEN c.direction_sign_stddev = 0
        OR c.direction_sign_stddev IS NULL THEN 'direction_constant_in_slice'
        WHEN c.perf_w_obs_count < 20 THEN 'sparse_price_data'
        WHEN ss.weekly_snapshots_with_corr IS NULL
        OR ss.weekly_snapshots_with_corr < 2 THEN 'insufficient_weekly_snapshots'
        ELSE NULL
    END AS null_diag_note
FROM slice_corrs AS c
    LEFT JOIN ranked_metrics AS r ON c.profile_name = r.profile_name
    AND c.horizon_name = r.horizon_name
    AND c.score_slice = r.score_slice
    AND r.metric_rank = 1
    LEFT JOIN baseline AS b ON c.profile_name = b.profile_name
    AND c.horizon_name = b.horizon_name
    LEFT JOIN snapshot_stats AS ss ON c.profile_name = ss.profile_name
    AND c.horizon_name = ss.horizon_name
    AND c.score_slice = ss.score_slice
ORDER BY c.profile_name,
    c.slice_order,
    strongest_corr_abs DESC NULLS LAST;
-- ============================================================
-- 3. STOCK COMPOSITE RANKINGS - MULTI-PROFILE CONSENSUS
-- ============================================================
-- @block q_pool_composite_stock_rankings
WITH stock_aggregates AS (
    SELECT symbol,
        MAX(company) AS company,
        MAX(sector) AS sector,
        MAX(industry) AS industry,
        COUNT(DISTINCT profile_name) AS profile_appearances,
        COUNT(DISTINCT run_id) AS run_appearances,
        COUNT(
            DISTINCT CASE
                WHEN direction IN ('Strong Up', 'Up') THEN profile_name
            END
        ) AS bullish_profiles,
        COUNT(
            DISTINCT CASE
                WHEN direction IN ('Strong Down', 'Down') THEN profile_name
            END
        ) AS bearish_profiles,
        AVG(score) AS avg_score,
        MAX(score) AS max_score,
        AVG(risk_adjusted_score) AS avg_risk_adj,
        MAX(risk_adjusted_score) AS max_risk_adj,
        AVG(confidence) AS avg_confidence,
        SUM(
            CASE
                WHEN direction IN ('Strong Up', 'Up') THEN 1
                ELSE 0
            END
        ) AS long_votes,
        SUM(
            CASE
                WHEN direction IN ('Strong Down', 'Down') THEN 1
                ELSE 0
            END
        ) AS short_votes,
        MODE(profile_name) AS most_common_profile
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND score IS NOT NULL
    GROUP BY symbol
    HAVING COUNT(DISTINCT profile_name) >= 3
)
SELECT symbol,
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
    (
        avg_risk_adj * 0.5 + avg_score * 0.3 + (avg_confidence / 100.0) * 0.2
    ) * (1 + LEAST(profile_appearances / 10.0, 0.5)) AS composite_rank,
    CASE
        WHEN bullish_profiles > bearish_profiles * 2
        AND long_votes > short_votes * 2 THEN 'STRONG_LONG'
        WHEN bullish_profiles > bearish_profiles
        AND long_votes > short_votes THEN 'LONG'
        WHEN bearish_profiles > bullish_profiles
        AND short_votes > long_votes THEN 'SHORT'
        WHEN bearish_profiles > bullish_profiles * 2
        AND short_votes > long_votes * 2 THEN 'STRONG_SHORT'
        ELSE 'MIXED'
    END AS consensus_signal,
    most_common_profile AS strongest_predictor_profile
FROM stock_aggregates
ORDER BY composite_rank DESC NULLS LAST
LIMIT 200;
-- @block q_pool_best_stocks_by_profile
SELECT symbol,
    MAX(company) AS company,
    MAX(sector) AS sector,
    AVG(score) AS avg_score,
    AVG(risk_adjusted_score) AS avg_risk_adj,
    AVG(confidence) AS avg_confidence,
    COUNT(DISTINCT run_id) AS appearances
FROM pool_profile_horizon_scores
WHERE profile_name = 'breakout_long'
    AND horizon_name = 'weeks'
GROUP BY symbol
HAVING COUNT(DISTINCT run_id) >= 2
ORDER BY avg_risk_adj DESC NULLS LAST
LIMIT 100;
-- @block q_pool_sector_leaders
WITH sector_scores AS (
    SELECT sector,
        symbol,
        MAX(company) AS company,
        AVG(risk_adjusted_score) AS avg_risk_adj,
        AVG(score) AS avg_score,
        COUNT(DISTINCT profile_name) AS profile_count
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND sector IS NOT NULL
    GROUP BY sector,
        symbol
    HAVING COUNT(DISTINCT profile_name) >= 2
)
SELECT *
FROM (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY sector
                ORDER BY avg_risk_adj DESC
            ) AS rank_in_sector
        FROM sector_scores
    )
WHERE rank_in_sector <= 10
ORDER BY sector,
    rank_in_sector;
-- ============================================================
-- 4. PATTERN PREDICTOR DISCOVERY - RAW FIELD CORRELATIONS
-- ============================================================
-- @block q_pool_raw_momentum_correlations
WITH scored_with_raw AS (
    SELECT s.profile_name,
        s.symbol,
        s.score,
        s.risk_adjusted_score,
        s.direction,
        TRY_CAST(s."RSI" AS DOUBLE) AS rsi,
        TRY_CAST(s."Perf.W" AS DOUBLE) AS perf_w,
        TRY_CAST(s."Perf.1M" AS DOUBLE) AS perf_1m,
        c.momentum AS momentum_component,
        c.trend AS trend_component
    FROM vw_pool_profile_scores_with_raw AS s
        LEFT JOIN pool_profile_components AS c ON c.pool_aggregation_id = s.pool_aggregation_id
        AND c.source_database_path = s.source_database_path
        AND c.run_id = s.run_id
        AND c.profile_name = s.profile_name
        AND c.symbol = s.symbol
    WHERE s.horizon_name = 'weeks'
)
SELECT profile_name,
    CORR(score, rsi) AS score_rsi_corr,
    CORR(score, perf_w) AS score_perf_w_corr,
    CORR(score, perf_1m) AS score_perf_1m_corr,
    CORR(score, momentum_component) AS score_momentum_corr,
    CORR(score, trend_component) AS score_trend_corr,
    CORR(risk_adjusted_score, momentum_component) AS riskadj_momentum_corr,
    CORR(
        CASE
            WHEN direction IN ('Strong Up', 'Up') THEN 1.0
            WHEN direction IN ('Strong Down', 'Down') THEN -1.0
            ELSE 0.0
        END,
        rsi
    ) AS direction_rsi_corr,
    ABS(CORR(score, rsi)) + ABS(CORR(score, momentum_component)) + ABS(
        CORR(risk_adjusted_score, momentum_component)
    ) AS combined_momentum_power
FROM scored_with_raw
GROUP BY profile_name
HAVING CORR(score, rsi) IS NOT NULL
ORDER BY combined_momentum_power DESC NULLS LAST;
-- @block q_pool_raw_numeric_columns
SELECT column_name,
    data_type
FROM information_schema.columns
WHERE table_name = 'pool_raw_scan_rows'
    AND column_name NOT IN (
        'pool_aggregation_id',
        'source_database_path',
        'source_iso_year',
        'source_iso_week',
        'run_id',
        'row_number',
        'symbol'
    )
    AND data_type IN (
        'FLOAT',
        'DOUBLE',
        'INTEGER',
        'BIGINT',
        'DECIMAL'
    )
ORDER BY column_name;
-- @block q_pool_component_analysis
SELECT c.profile_name,
    c.symbol,
    MAX(c.company) AS company,
    AVG(c.momentum) AS avg_momentum,
    AVG(c.trend) AS avg_trend,
    AVG(c.quality) AS avg_quality,
    AVG(c.valuation) AS avg_valuation,
    AVG(c.safety) AS avg_safety,
    AVG(c.scale) AS avg_scale,
    AVG(c.attention) AS avg_attention,
    AVG(c.event) AS avg_event,
    AVG(s.score) AS avg_final_score,
    AVG(s.risk_adjusted_score) AS avg_risk_adj,
    CORR(c.momentum, s.score) AS momentum_score_corr,
    CORR(c.trend, s.score) AS trend_score_corr,
    CORR(c.quality, s.score) AS quality_score_corr,
    CORR(c.valuation, s.score) AS valuation_score_corr
FROM pool_profile_components AS c
    INNER JOIN pool_profile_horizon_scores AS s ON c.pool_aggregation_id = s.pool_aggregation_id
    AND c.source_database_path = s.source_database_path
    AND c.run_id = s.run_id
    AND c.profile_name = s.profile_name
    AND c.symbol = s.symbol
WHERE c.profile_name = 'breakout_long'
    AND s.horizon_name = 'weeks'
GROUP BY c.profile_name,
    c.symbol
ORDER BY avg_risk_adj DESC NULLS LAST
LIMIT 50;
-- ============================================================
-- 5. SCORE vs PRICE PERFORMANCE INTERSECTION ANALYSIS
-- ============================================================
-- @block q_pool_high_score_positive_momentum
WITH high_scorers AS (
    SELECT symbol,
        MAX(risk_adjusted_score) AS max_score,
        AVG(score) AS avg_score,
        COUNT(DISTINCT profile_name) AS profile_count
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND risk_adjusted_score >= 0.75
    GROUP BY symbol
    HAVING COUNT(DISTINCT profile_name) >= 2
),
price_performers AS (
    SELECT DISTINCT r.symbol,
        TRY_CAST(r."Perf.W" AS DOUBLE) AS perf_w,
        TRY_CAST(r."Perf.1M" AS DOUBLE) AS perf_1m,
        TRY_CAST(r."RSI" AS DOUBLE) AS rsi
    FROM pool_raw_scan_rows AS r
    WHERE (
            TRY_CAST(r."Perf.W" AS DOUBLE) > 0
            OR TRY_CAST(r."Perf.1M" AS DOUBLE) > 0
        )
        AND TRY_CAST(r."RSI" AS DOUBLE) BETWEEN 40 AND 80
)
SELECT h.symbol,
    h.max_score,
    h.avg_score,
    h.profile_count,
    p.perf_w,
    p.perf_1m,
    p.rsi,
    'HIGH_SCORE_POSITIVE_MOMENTUM' AS classification
FROM high_scorers AS h
    INNER JOIN price_performers AS p ON h.symbol = p.symbol
ORDER BY h.max_score DESC,
    p.perf_w DESC NULLS LAST
LIMIT 100;
-- @block q_pool_high_score_poor_momentum
WITH high_scorers AS (
    SELECT symbol,
        MAX(risk_adjusted_score) AS max_score,
        AVG(score) AS avg_score,
        COUNT(DISTINCT profile_name) AS profile_count
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND risk_adjusted_score >= 0.75
    GROUP BY symbol
    HAVING COUNT(DISTINCT profile_name) >= 2
)
SELECT h.symbol,
    h.max_score,
    h.avg_score,
    h.profile_count,
    'HIGH_SCORE_CHECK_PRICE' AS status,
    'May be early signal or false positive' AS note
FROM high_scorers AS h
    LEFT JOIN (
        SELECT DISTINCT symbol
        FROM pool_raw_scan_rows
        WHERE TRY_CAST("Perf.W" AS DOUBLE) > 0
    ) AS good ON h.symbol = good.symbol
WHERE good.symbol IS NULL
ORDER BY h.max_score DESC
LIMIT 50;
-- @block q_pool_score_price_divergence
WITH profile_high_scores AS (
    SELECT profile_name,
        symbol,
        score,
        risk_adjusted_score,
        direction
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND risk_adjusted_score >= 0.70
)
SELECT phs.profile_name,
    COUNT(*) AS high_score_count,
    AVG(TRY_CAST(r."Perf.W" AS DOUBLE)) AS avg_perf_w,
    AVG(TRY_CAST(r."Perf.1M" AS DOUBLE)) AS avg_perf_1m,
    CORR(
        phs.risk_adjusted_score,
        TRY_CAST(r."Perf.W" AS DOUBLE)
    ) AS score_price_corr_w,
    CORR(
        phs.risk_adjusted_score,
        TRY_CAST(r."Perf.1M" AS DOUBLE)
    ) AS score_price_corr_1m,
    CASE
        WHEN CORR(
            phs.risk_adjusted_score,
            TRY_CAST(r."Perf.W" AS DOUBLE)
        ) > 0.3 THEN 'ALIGNED'
        WHEN CORR(
            phs.risk_adjusted_score,
            TRY_CAST(r."Perf.W" AS DOUBLE)
        ) < -0.1 THEN 'CONTRARIAN'
        ELSE 'NEUTRAL'
    END AS alignment_status
FROM profile_high_scores AS phs
    LEFT JOIN pool_raw_scan_rows AS r ON phs.symbol = r.symbol
    OR r.symbol LIKE '%:' || phs.symbol
    OR phs.symbol LIKE '%:' || r.symbol
GROUP BY phs.profile_name
ORDER BY score_price_corr_w DESC NULLS LAST;
-- ============================================================
-- 6. TEMPORAL ANALYSIS - PERFORMANCE ACROSS WEEKS
-- ============================================================
-- @block q_pool_profile_weekly_consistency
SELECT profile_name,
    source_iso_week AS week,
    COUNT(DISTINCT symbol) AS symbols_scored,
    AVG(score) AS avg_score,
    AVG(risk_adjusted_score) AS avg_risk_adj,
    AVG(confidence) AS avg_confidence,
    AVG(
        CASE
            WHEN direction IN ('Strong Up', 'Up') THEN 1.0
            ELSE 0.0
        END
    ) AS bullish_pct,
    STDDEV(score) AS score_stddev,
    MAX(score) AS max_score
FROM pool_profile_horizon_scores
WHERE horizon_name = 'weeks'
GROUP BY profile_name,
    source_iso_week
ORDER BY profile_name,
    source_iso_week;
-- @block q_pool_weekly_consensus_leaders
WITH weekly_scores AS (
    SELECT source_iso_week AS week,
        symbol,
        MAX(company) AS company,
        MAX(sector) AS sector,
        COUNT(DISTINCT profile_name) AS profile_count,
        AVG(risk_adjusted_score) AS avg_risk_adj,
        SUM(
            CASE
                WHEN direction IN ('Strong Up', 'Up') THEN 1
                ELSE 0
            END
        ) AS long_votes,
        COUNT(*) AS total_votes
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
    GROUP BY source_iso_week,
        symbol
    HAVING COUNT(DISTINCT profile_name) >= 4
)
SELECT *
FROM (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY week
                ORDER BY avg_risk_adj DESC
            ) AS weekly_rank,
            CAST(long_votes AS DOUBLE) / NULLIF(total_votes, 0) AS consensus_ratio
        FROM weekly_scores
    )
WHERE weekly_rank <= 20
ORDER BY week DESC,
    weekly_rank;
-- @block q_pool_profile_persistence
WITH weekly_profile_scores AS (
    SELECT profile_name,
        source_iso_week AS week,
        AVG(risk_adjusted_score) AS weekly_avg_score
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
    GROUP BY profile_name,
        source_iso_week
)
SELECT profile_name,
    COUNT(DISTINCT week) AS weeks_present,
    AVG(weekly_avg_score) AS overall_avg_score,
    STDDEV(weekly_avg_score) AS week_to_week_volatility,
    MIN(weekly_avg_score) AS worst_week_score,
    MAX(weekly_avg_score) AS best_week_score,
    (AVG(weekly_avg_score) - STDDEV(weekly_avg_score)) AS persistence_score,
    CASE
        WHEN STDDEV(weekly_avg_score) < 0.20 THEN 'VERY_STABLE'
        WHEN STDDEV(weekly_avg_score) < 0.50 THEN 'STABLE'
        WHEN STDDEV(weekly_avg_score) < 1.00 THEN 'MODERATE'
        ELSE 'VOLATILE'
    END AS stability_rating
FROM weekly_profile_scores
GROUP BY profile_name
HAVING COUNT(DISTINCT week) >= 2
ORDER BY persistence_score DESC NULLS LAST;
-- ============================================================
-- 7. SECTOR & INDUSTRY ANALYSIS
-- ============================================================
-- @block q_pool_sector_by_profile
WITH sector_profile_stats AS (
    SELECT profile_name,
        sector,
        COUNT(DISTINCT symbol) AS symbol_count,
        COUNT(DISTINCT industry) AS industries,
        AVG(score) AS avg_score,
        AVG(risk_adjusted_score) AS avg_risk_adj,
        AVG(confidence) AS avg_confidence,
        AVG(
            CASE
                WHEN direction IN ('Strong Up', 'Up') THEN 1.0
                ELSE 0.0
            END
        ) AS bullish_pct,
        STDDEV(score) AS score_dispersion
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND sector IS NOT NULL
    GROUP BY profile_name,
        sector
    HAVING COUNT(DISTINCT symbol) >= 5
)
SELECT *,
    ROW_NUMBER() OVER (
        PARTITION BY profile_name
        ORDER BY avg_risk_adj DESC
    ) AS rank_in_profile
FROM sector_profile_stats
ORDER BY profile_name,
    avg_risk_adj DESC NULLS LAST;
-- @block q_pool_top_industries
SELECT industry,
    sector,
    COUNT(DISTINCT symbol) AS symbols,
    COUNT(DISTINCT profile_name) AS profiles_covering,
    AVG(score) AS avg_score,
    AVG(risk_adjusted_score) AS avg_risk_adj,
    AVG(confidence) AS avg_confidence,
    AVG(
        CASE
            WHEN direction IN ('Strong Up', 'Up') THEN 1.0
            ELSE 0.0
        END
    ) AS bullish_consensus
FROM pool_profile_horizon_scores
WHERE horizon_name = 'weeks'
    AND industry IS NOT NULL
GROUP BY industry,
    sector
HAVING COUNT(DISTINCT profile_name) >= 3
    AND COUNT(DISTINCT symbol) >= 3
ORDER BY avg_risk_adj DESC NULLS LAST
LIMIT 50;
-- ============================================================
-- 8. CONFIDENCE & COVERAGE ANALYSIS
-- ============================================================
-- @block q_pool_confidence_stats
SELECT profile_name,
    horizon_name,
    AVG(confidence) AS mean_confidence,
    MEDIAN(confidence) AS median_confidence,
    MIN(confidence) AS min_confidence,
    MAX(confidence) AS max_confidence,
    quantile_cont(confidence, 0.10) AS p10_confidence,
    quantile_cont(confidence, 0.90) AS p90_confidence,
    AVG(
        CASE
            WHEN confidence >= 70 THEN 1.0
            ELSE 0.0
        END
    ) AS high_confidence_rate,
    AVG(
        CASE
            WHEN confidence >= 50 THEN 1.0
            ELSE 0.0
        END
    ) AS medium_plus_confidence_rate,
    COUNT(*) FILTER (
        WHERE confidence >= 70
            AND score >= 1.10
    ) AS high_conf_strong_up_count
FROM pool_profile_horizon_scores
GROUP BY profile_name,
    horizon_name
ORDER BY profile_name,
    horizon_name;
-- @block q_pool_coverage_analysis
WITH coverage_stats AS (
    SELECT symbol,
        MAX(company) AS company,
        MAX(sector) AS sector,
        COUNT(DISTINCT profile_name) AS profiles_covering,
        COUNT(DISTINCT run_id) AS runs_present,
        AVG(coverage) AS avg_coverage
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
    GROUP BY symbol
)
SELECT symbol,
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
    END AS coverage_tier
FROM coverage_stats
ORDER BY profiles_covering DESC,
    runs_present DESC
LIMIT 100;
-- ============================================================
-- 9. MANAGER ACTION SIGNAL ANALYSIS
-- ============================================================
-- @block q_pool_manager_action_distribution
SELECT profile_name,
    manager_action_signal,
    COUNT(*) AS count,
    AVG(score) AS avg_score,
    AVG(risk_adjusted_score) AS avg_risk_adj,
    AVG(confidence) AS avg_confidence,
    COUNT(DISTINCT symbol) AS unique_symbols
FROM pool_profile_horizon_scores
WHERE horizon_name = 'weeks'
    AND manager_action_signal IS NOT NULL
GROUP BY profile_name,
    manager_action_signal
ORDER BY profile_name,
    CASE
        manager_action_signal
        WHEN 'add_long_breakout' THEN 1
        WHEN 'accumulate_value_catalyst' THEN 2
        WHEN 'promote_to_breakout' THEN 3
        WHEN 'hold_quality_long' THEN 4
        WHEN 'watch_value_reversal' THEN 5
        WHEN 'mean_reversion_watch' THEN 6
        WHEN 'neutral_watch' THEN 7
        WHEN 'trim_extended_long' THEN 8
        WHEN 'avoid_value_trap' THEN 9
        WHEN 'hedge_or_short' THEN 10
        ELSE 11
    END;
-- @block q_pool_strong_long_action_consensus
WITH strong_signals AS (
    SELECT symbol,
        MAX(company) AS company,
        MAX(sector) AS sector,
        COUNT(*) AS long_action_signals,
        COUNT(DISTINCT profile_name) AS unique_profiles,
        AVG(score) AS avg_score,
        AVG(risk_adjusted_score) AS avg_risk_adj
    FROM pool_profile_horizon_scores
    WHERE horizon_name = 'weeks'
        AND manager_action_signal IN (
            'add_long_breakout',
            'accumulate_value_catalyst',
            'promote_to_breakout',
            'hold_quality_long'
        )
    GROUP BY symbol
    HAVING COUNT(*) >= 2
)
SELECT symbol,
    company,
    sector,
    long_action_signals,
    unique_profiles,
    avg_score,
    avg_risk_adj,
    DENSE_RANK() OVER (
        ORDER BY avg_risk_adj DESC
    ) AS rank
FROM strong_signals
ORDER BY avg_risk_adj DESC NULLS LAST
LIMIT 50;