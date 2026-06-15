-- DuckDB query session for all-fields pattern analysis outputs
-- Produced by:
--   data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer
--
-- Workflow:
-- 1) Connect to a per-run summary export parquet or aggregate DuckDB output.
-- 2) Run one @block at a time in SQLTools / DBCode.
--
-- Latest single-run example (12 Jun 2026 scan, ~50k symbols, 184k pattern rows):
--   all_fields_pattern_20260613_205803_89287088
--   per_run/tradingview_all_fields_20260612_2008_utc_028e0f86/field_performance_patterns.parquet
-- @block
-- Inventory all per-run summary files from the default pattern analysis folder.
SELECT filename
FROM glob(
        'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/**/field_performance_patterns.parquet'
    )
ORDER BY filename DESC;
-- @block
-- [RUN SCOPE] Quick run inventory — row counts and field coverage for one analysis run.
-- Look for: scan size (pair_n vs scan_data_count), predictor/performance breadth.
WITH patterns AS (
    SELECT *
    FROM read_parquet(
            'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/all_fields_pattern_20260613_205803_89287088/per_run/tradingview_all_fields_20260612_2008_utc_028e0f86/field_performance_patterns.parquet'
        )
)
SELECT MAX(scan_data_count) AS symbols_in_scan,
    COUNT(*) AS pattern_rows,
    COUNT(DISTINCT predictor_field) AS predictors_tested,
    COUNT(DISTINCT performance_field) AS performance_targets,
    ROUND(AVG(pair_n), 0) AS avg_valid_pairs,
    ROUND(AVG(ABS(spearman_corr_adjusted)), 4) AS avg_abs_spearman,
    ROUND(MAX(ABS(pattern_score)), 4) AS max_abs_pattern_score
FROM patterns;
-- @block
-- [RUN SCOPE] Top patterns by pattern_score (default sort in exports).
-- pattern_score = sign × (0.45×|ρ| + 0.35×norm_quintile_spread + 0.20×hit_rate) × sqrt(pair_n/scan_n)
-- Positive score ⇒ higher predictor values align with higher performance (after direction adjustment).
-- CAUTION: Top rows are often tautological (Mom/gap/ROC vs change* on same bar). Prefer Perf.* targets below.
SELECT predictor_field,
    performance_field,
    predictor_direction,
    pair_n,
    ROUND(spearman_corr_adjusted, 4) AS spearman_adj,
    ROUND(quintile_spread_adjusted, 2) AS quintile_spread_adj,
    ROUND(pattern_score, 4) AS pattern_score
FROM read_parquet(
        'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/all_fields_pattern_20260613_205803_89287088/per_run/tradingview_all_fields_20260612_2008_utc_028e0f86/field_performance_patterns.parquet'
    )
ORDER BY pattern_score DESC NULLS LAST
LIMIT 50;
-- @block
-- [RUN SCOPE] Actionable momentum signals — predictors vs trailing Perf.* (excludes same-bar change fields).
-- Look for: RSI/CCI/MACD variants with 1W/1M lookback predicting Perf.3M, Perf.6M, Perf.YTD.
-- Strong positive spearman_adj + large quintile_spread_adj ⇒ top quintile materially outperforms bottom quintile.
SELECT predictor_field,
    performance_field,
    pair_n,
    ROUND(spearman_corr_adjusted, 4) AS spearman_adj,
    ROUND(quintile_spread_adjusted, 2) AS quintile_spread_adj,
    ROUND(top_quintile_avg_perf, 2) AS top_q_avg_perf,
    ROUND(bottom_quintile_avg_perf, 2) AS bottom_q_avg_perf,
    ROUND(pattern_score, 4) AS pattern_score
FROM read_parquet(
        'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/all_fields_pattern_20260613_205803_89287088/per_run/tradingview_all_fields_20260612_2008_utc_028e0f86/field_performance_patterns.parquet'
    )
WHERE performance_field LIKE 'Perf.%'
    AND predictor_field NOT LIKE 'change%'
    AND predictor_field NOT LIKE 'gap%'
    AND predictor_field NOT LIKE 'Mom%'
    AND predictor_field NOT LIKE 'ROC%'
    AND pair_n >= 3000
    AND ABS(spearman_corr_adjusted) >= 0.15
ORDER BY pattern_score DESC NULLS LAST
LIMIT 50;
-- @block
-- [RUN SCOPE] Fundamentals / valuation vs Perf.3M — slower-moving predictors.
-- Look for: sign and magnitude vs momentum block above (typically weaker on one day).
-- Example from 12_06_2026: market_cap negative (smaller names stronger 3M), EPS growth positive, high P/B negative.
SELECT predictor_field,
    pair_n,
    ROUND(spearman_corr_adjusted, 4) AS spearman_adj,
    ROUND(quintile_spread_adjusted, 2) AS quintile_spread_adj,
    ROUND(pattern_score, 4) AS pattern_score,
    predictor_fill_rate
FROM read_parquet(
        'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/all_fields_pattern_20260613_205803_89287088/per_run/tradingview_all_fields_20260612_2008_utc_028e0f86/field_performance_patterns.parquet'
    )
WHERE performance_field = 'Perf.3M'
    AND (
        predictor_field LIKE '%pe_ratio%'
        OR predictor_field LIKE '%ev_%'
        OR predictor_field LIKE '%margin%'
        OR predictor_field LIKE '%debt%'
        OR predictor_field LIKE '%yield%'
        OR predictor_field LIKE '%market_cap%'
        OR predictor_field LIKE '%earnings%'
        OR predictor_field LIKE '%revenue%'
        OR predictor_field LIKE '%book%'
    )
ORDER BY ABS(pattern_score) DESC NULLS LAST
LIMIT 50;
-- @block
-- [RUN SCOPE] Drill one predictor across all performance horizons.
-- Replace predictor_field to see which horizon it best explains (intraday change vs multi-month Perf).
SELECT performance_field,
    pair_n,
    ROUND(spearman_corr_adjusted, 4) AS spearman_adj,
    ROUND(quintile_spread_adjusted, 2) AS quintile_spread_adj,
    ROUND(pattern_score, 4) AS pattern_score
FROM read_parquet(
        'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/all_fields_pattern_20260613_205803_89287088/per_run/tradingview_all_fields_20260612_2008_utc_028e0f86/field_performance_patterns.parquet'
    )
WHERE predictor_field = 'RSI9|1W'
ORDER BY pattern_score DESC NULLS LAST;
-- @block
-- [RUN SCOPE] Quality filter — high sample + meaningful correlation + quintile separation.
-- Use this instead of raw top-N when hunting durable signals (still one-day snapshot).
SELECT predictor_field,
    performance_field,
    pair_n,
    ROUND(spearman_corr_adjusted, 4) AS spearman_adj,
    ROUND(quintile_spread_adjusted, 2) AS quintile_spread_adj,
    ROUND(directional_outperforms_rate, 3) AS hit_rate,
    ROUND(pattern_score, 4) AS pattern_score
FROM read_parquet(
        'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/all_fields_pattern_20260613_205803_89287088/per_run/tradingview_all_fields_20260612_2008_utc_028e0f86/field_performance_patterns.parquet'
    )
WHERE pair_n >= 5000
    AND ABS(spearman_corr_adjusted) >= 0.20
    AND ABS(quintile_spread_adjusted) >= 10
    AND performance_field LIKE 'Perf.%'
ORDER BY pattern_score DESC NULLS LAST
LIMIT 100;
-- @block
-- [MULTI-RUN] Stability report from an aggregate DuckDB output (after run_all_fields_pattern_analysis_batch).
-- rank_stability_score = median_quintile_spread × sign_consistency_ratio
-- Prefer pairs with runs_seen >= 4, sign_consistency_ratio >= 0.65, |median_quintile_spread| >= 1.
SELECT predictor_field,
    performance_field,
    runs_seen,
    sign_consistency_ratio,
    median_quintile_spread,
    rank_stability_score
FROM cross_run_field_stability
ORDER BY ABS(rank_stability_score) DESC NULLS LAST,
    runs_seen DESC
LIMIT 100;
-- @block
-- [MULTI-RUN] Identify strong stable patterns after quality constraints.
SELECT predictor_field,
    performance_field,
    runs_seen,
    runs_with_signal,
    sign_consistency_ratio,
    median_pearson,
    median_quintile_spread,
    rank_stability_score
FROM cross_run_field_stability
WHERE runs_seen >= 4
    AND sign_consistency_ratio >= 0.65
    AND ABS(median_quintile_spread) >= 1.0
ORDER BY ABS(rank_stability_score) DESC NULLS LAST;
-- @block
-- [MULTI-RUN] Drill into one predictor/performance pair across runs (requires aggregate DuckDB).
SELECT source_day_label,
    run_id,
    predictor_field,
    performance_field,
    pair_n,
    spearman_corr_adjusted,
    quintile_spread_adjusted,
    pattern_score
FROM per_run_patterns
WHERE predictor_field = 'RSI9|1W'
    AND performance_field = 'Perf.3M'
ORDER BY source_day_label,
    run_id;
-- @block
-- [MULTI-RUN] Compare trailing Perf.3M patterns vs actual close forward returns (if close pipeline run).
-- Connect to aggregate DB that merged include_close_forward_return=True summaries.
SELECT predictor_field,
    performance_field,
    runs_seen,
    sign_consistency_ratio,
    median_quintile_spread,
    rank_stability_score
FROM cross_run_field_stability
WHERE performance_field IN ('Perf.3M', 'close_forward_return_pct')
    AND predictor_field = 'RSI9|1W'
ORDER BY performance_field,
    ABS(rank_stability_score) DESC NULLS LAST;
-- =============================================================================
-- STOCK SCOUT — rank individual symbols against the day's strongest patterns
-- =============================================================================
-- Pattern parquet is pair-level (field × field). Per-symbol scoring requires the
-- source daily DuckDB (all_fields_rows). Disconnect other tools using that file first.
--
-- Scoring model (gradient 0–100):
--   1) Pick top non-tautological patterns (Perf.* targets, exclude change/gap/Mom/ROC).
--   2) For each pattern, rank every stock's predictor value → percentile [0,1].
--   3) Flip percentile when pattern_score < 0 (lower predictor = better performance).
--   4) pattern_fit_score = 100 × Σ(|pattern_score| × aligned_percentile) / Σ(|pattern_score|)
--
-- Tiers: A ≥ 85 | B ≥ 70 | C ≥ 55 | D < 55
-- Edit paths / run_id / LIMIT as needed.
--
-- Market filter: mirrors PREFERRED_MARKETS in src/constants/trading_view_constants.py
-- Uses all_fields_rows.market (TradingView scan market code, e.g. america, uk, germany).
-- country is the display label (e.g. United States) — shown in output, not used for filtering.
--
-- DBCode note: connect this session to the daily all-fields DuckDB (same file as below).
--   D:/FinanceProjects/.../12_06_2026/tradingview_all_fields_12_06_2026.duckdb
-- Each @block is self-contained (inline run_rows_preferred CTE). No ATTACH / temp view needed.
-- @block
-- [STOCK SCOUT 0] Optional smoke test — count rows for the run (connect to day DuckDB first).
SELECT COUNT(*) AS all_fields_rows_count
FROM all_fields_rows
WHERE run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86';
-- @block
-- [STOCK SCOUT 0b] Verify preferred-market filter counts (self-contained).
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            VALUES ('america'),
                ('canada'),
                ('mexico'),
                ('austria'),
                ('belgium'),
                ('cyprus'),
                ('czech'),
                ('denmark'),
                ('estonia'),
                ('finland'),
                ('france'),
                ('germany'),
                ('greece'),
                ('hungary'),
                ('iceland'),
                ('ireland'),
                ('italy'),
                ('latvia'),
                ('lithuania'),
                ('luxembourg'),
                ('netherlands'),
                ('norway'),
                ('poland'),
                ('portugal'),
                ('romania'),
                ('slovakia'),
                ('spain'),
                ('sweden'),
                ('switzerland'),
                ('uk')
        ) AS preferred_markets(market_code)
)
SELECT COUNT(*) AS rows_in_run,
    COUNT(*) FILTER (
        WHERE LOWER(TRIM(COALESCE(market, ''))) IN (
                SELECT market_code
                FROM preferred_market_codes
            )
    ) AS rows_preferred_markets,
    COUNT(*) FILTER (
        WHERE LOWER(TRIM(COALESCE(market, ''))) NOT IN (
                SELECT market_code
                FROM preferred_market_codes
            )
            OR COALESCE(TRIM(market), '') = ''
    ) AS rows_excluded
FROM all_fields_rows
WHERE run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86';
-- @block
-- [STOCK SCOUT 0c] Market / country breakdown for preferred-universe rows (self-contained).
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            VALUES ('america'),
                ('canada'),
                ('mexico'),
                ('austria'),
                ('belgium'),
                ('cyprus'),
                ('czech'),
                ('denmark'),
                ('estonia'),
                ('finland'),
                ('france'),
                ('germany'),
                ('greece'),
                ('hungary'),
                ('iceland'),
                ('ireland'),
                ('italy'),
                ('latvia'),
                ('lithuania'),
                ('luxembourg'),
                ('netherlands'),
                ('norway'),
                ('poland'),
                ('portugal'),
                ('romania'),
                ('slovakia'),
                ('spain'),
                ('sweden'),
                ('switzerland'),
                ('uk')
        ) AS preferred_markets(market_code)
),
run_rows_preferred AS (
    SELECT r.*
    FROM all_fields_rows r
        INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
)
SELECT market,
    country,
    COUNT(*) AS symbol_count
FROM run_rows_preferred
GROUP BY market,
    country
ORDER BY symbol_count DESC,
    market,
    country;
-- @block
-- [STOCK SCOUT 1] Which predictors drive today's scout? (pick from here before editing scout 2/3)
-- One row per predictor = its strongest Perf.* pair for the day.
SELECT predictor_field,
    performance_field,
    ROUND(pattern_score, 4) AS pattern_score,
    ROUND(spearman_corr_adjusted, 4) AS spearman_adj,
    ROUND(quintile_spread_adjusted, 2) AS quintile_spread_adj,
    pair_n
FROM read_parquet(
        'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/all_fields_pattern_20260613_205803_89287088/per_run/tradingview_all_fields_20260612_2008_utc_028e0f86/field_performance_patterns.parquet'
    )
WHERE performance_field LIKE 'Perf.%'
    AND predictor_field NOT LIKE 'change%'
    AND predictor_field NOT LIKE 'gap%'
    AND predictor_field NOT LIKE 'Mom%'
    AND predictor_field NOT LIKE 'ROC%'
    AND pair_n >= 3000
    AND ABS(spearman_corr_adjusted) >= 0.15 QUALIFY ROW_NUMBER() OVER (
        PARTITION BY predictor_field
        ORDER BY ABS(pattern_score) DESC
    ) = 1
ORDER BY ABS(pattern_score) DESC
LIMIT 25;
-- @block
-- [STOCK SCOUT 2] Top 100 stocks — composite pattern_fit_score (gradient ranking).
-- Uses 12 strongest momentum/technical predictors from the 12_06_2026 run.
-- Universe = PREFERRED_MARKETS only; percentiles are within that set (self-contained block).
-- Add WHERE market_cap_basic >= ... for large-cap only; raise min_signals for stricter fit.
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            VALUES ('america'),
                ('canada'),
                ('mexico'),
                ('austria'),
                ('belgium'),
                ('cyprus'),
                ('czech'),
                ('denmark'),
                ('estonia'),
                ('finland'),
                ('france'),
                ('germany'),
                ('greece'),
                ('hungary'),
                ('iceland'),
                ('ireland'),
                ('italy'),
                ('latvia'),
                ('lithuania'),
                ('luxembourg'),
                ('netherlands'),
                ('norway'),
                ('poland'),
                ('portugal'),
                ('romania'),
                ('slovakia'),
                ('spain'),
                ('sweden'),
                ('switzerland'),
                ('uk')
        ) AS preferred_markets(market_code)
),
run_rows_preferred AS (
    SELECT r.*
    FROM all_fields_rows r
        INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
),
patterns_pq AS (
    SELECT *
    FROM read_parquet(
            'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/all_fields_pattern_20260613_205803_89287088/per_run/tradingview_all_fields_20260612_2008_utc_028e0f86/field_performance_patterns.parquet'
        )
),
pattern_weights AS (
    SELECT predictor_field,
        pattern_score,
        ABS(pattern_score) AS weight
    FROM patterns_pq
    WHERE performance_field LIKE 'Perf.%'
        AND predictor_field NOT LIKE 'change%'
        AND predictor_field NOT LIKE 'gap%'
        AND predictor_field NOT LIKE 'Mom%'
        AND predictor_field NOT LIKE 'ROC%'
        AND pair_n >= 3000
        AND ABS(spearman_corr_adjusted) >= 0.15
        AND predictor_field IN (
            'CCI20|1W',
            'RSI9|1W',
            'RSI10|1W',
            'RSI20|1W',
            'CCI20|1M',
            'RSI21|1W',
            'RSI7|1W',
            'RSI5|1M',
            'RSI4|1M',
            'RSI3|1M',
            'RSI30',
            'RSI|1W'
        ) QUALIFY ROW_NUMBER() OVER (
            PARTITION BY predictor_field
            ORDER BY ABS(pattern_score) DESC
        ) = 1
),
long_vals AS (
    SELECT symbol,
        'CCI20|1W' AS predictor_field,
        TRY_CAST("CCI20|1W" AS DOUBLE) AS pred_value
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI9|1W',
        TRY_CAST("RSI9|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI10|1W',
        TRY_CAST("RSI10|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI20|1W',
        TRY_CAST("RSI20|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'CCI20|1M',
        TRY_CAST("CCI20|1M" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI21|1W',
        TRY_CAST("RSI21|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI7|1W',
        TRY_CAST("RSI7|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI5|1M',
        TRY_CAST("RSI5|1M" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI4|1M',
        TRY_CAST("RSI4|1M" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI3|1M',
        TRY_CAST("RSI3|1M" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI30',
        TRY_CAST(RSI30 AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI|1W',
        TRY_CAST("RSI|1W" AS DOUBLE)
    FROM run_rows_preferred
),
ranked AS (
    SELECT lv.symbol,
        lv.predictor_field,
        pw.pattern_score,
        pw.weight,
        PERCENT_RANK() OVER (
            PARTITION BY lv.predictor_field
            ORDER BY lv.pred_value
        ) AS pred_pct_rank
    FROM long_vals lv
        INNER JOIN pattern_weights pw USING (predictor_field)
    WHERE lv.pred_value IS NOT NULL
        AND isfinite(lv.pred_value)
),
aligned AS (
    SELECT symbol,
        predictor_field,
        pattern_score,
        weight,
        CASE
            WHEN pattern_score >= 0 THEN pred_pct_rank
            ELSE 1.0 - pred_pct_rank
        END AS aligned_rank
    FROM ranked
),
stock_scores AS (
    SELECT a.symbol,
        COUNT(*) AS signals_matched,
        SUM(weight) AS total_weight,
        SUM(weight * aligned_rank) / NULLIF(SUM(weight), 0) AS raw_score
    FROM aligned a
    GROUP BY a.symbol
),
meta AS (
    SELECT symbol,
        name,
        market,
        country,
        sector,
        industry,
        TRY_CAST(close AS DOUBLE) AS close,
        TRY_CAST(market_cap_basic AS DOUBLE) AS market_cap_basic,
        TRY_CAST("Perf.3M" AS DOUBLE) AS perf_3m,
        TRY_CAST("Perf.6M" AS DOUBLE) AS perf_6m,
        TRY_CAST("Perf.YTD" AS DOUBLE) AS perf_ytd
    FROM run_rows_preferred
)
SELECT m.symbol,
    m.name,
    m.market,
    m.country,
    m.sector,
    m.industry,
    ROUND(m.close, 4) AS close,
    ROUND(m.market_cap_basic / 1e9, 2) AS mcap_b_usd,
    ROUND(m.perf_3m, 2) AS perf_3m,
    ROUND(m.perf_6m, 2) AS perf_6m,
    s.signals_matched,
    ROUND(100.0 * s.raw_score, 2) AS pattern_fit_score,
    CASE
        WHEN 100.0 * s.raw_score >= 85 THEN 'A'
        WHEN 100.0 * s.raw_score >= 70 THEN 'B'
        WHEN 100.0 * s.raw_score >= 55 THEN 'C'
        ELSE 'D'
    END AS fit_tier,
    RANK() OVER (
        ORDER BY s.raw_score DESC
    ) AS scout_rank
FROM stock_scores s
    INNER JOIN meta m USING (symbol)
WHERE s.signals_matched >= 8
    AND m.market_cap_basic >= 1e9
ORDER BY s.raw_score DESC
LIMIT 100;
-- @block
-- [STOCK SCOUT 3] Short list — top 25 with one-line "why" (best aligned indicators).
-- Good for a quick morning scan; drill symbols in scout 4.
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            VALUES ('america'),
                ('canada'),
                ('mexico'),
                ('austria'),
                ('belgium'),
                ('cyprus'),
                ('czech'),
                ('denmark'),
                ('estonia'),
                ('finland'),
                ('france'),
                ('germany'),
                ('greece'),
                ('hungary'),
                ('iceland'),
                ('ireland'),
                ('italy'),
                ('latvia'),
                ('lithuania'),
                ('luxembourg'),
                ('netherlands'),
                ('norway'),
                ('poland'),
                ('portugal'),
                ('romania'),
                ('slovakia'),
                ('spain'),
                ('sweden'),
                ('switzerland'),
                ('uk')
        ) AS preferred_markets(market_code)
),
run_rows_preferred AS (
    SELECT r.*
    FROM all_fields_rows r
        INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
),
patterns_pq AS (
    SELECT *
    FROM read_parquet(
            'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/all_fields_pattern_20260613_205803_89287088/per_run/tradingview_all_fields_20260612_2008_utc_028e0f86/field_performance_patterns.parquet'
        )
),
pattern_weights AS (
    SELECT predictor_field,
        pattern_score,
        ABS(pattern_score) AS weight
    FROM patterns_pq
    WHERE performance_field LIKE 'Perf.%'
        AND predictor_field NOT LIKE 'change%'
        AND predictor_field NOT LIKE 'gap%'
        AND predictor_field NOT LIKE 'Mom%'
        AND predictor_field NOT LIKE 'ROC%'
        AND pair_n >= 3000
        AND predictor_field IN (
            'CCI20|1W',
            'RSI9|1W',
            'RSI10|1W',
            'RSI20|1W',
            'CCI20|1M'
        ) QUALIFY ROW_NUMBER() OVER (
            PARTITION BY predictor_field
            ORDER BY ABS(pattern_score) DESC
        ) = 1
),
long_vals AS (
    SELECT symbol,
        'CCI20|1W' AS predictor_field,
        TRY_CAST("CCI20|1W" AS DOUBLE) AS pred_value
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI9|1W',
        TRY_CAST("RSI9|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI10|1W',
        TRY_CAST("RSI10|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'RSI20|1W',
        TRY_CAST("RSI20|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'CCI20|1M',
        TRY_CAST("CCI20|1M" AS DOUBLE)
    FROM run_rows_preferred
),
ranked AS (
    SELECT lv.symbol,
        lv.predictor_field,
        pw.weight,
        CASE
            WHEN pw.pattern_score >= 0 THEN PERCENT_RANK() OVER (
                PARTITION BY lv.predictor_field
                ORDER BY lv.pred_value
            )
            ELSE 1.0 - PERCENT_RANK() OVER (
                PARTITION BY lv.predictor_field
                ORDER BY lv.pred_value
            )
        END AS aligned_rank
    FROM long_vals lv
        INNER JOIN pattern_weights pw USING (predictor_field)
    WHERE lv.pred_value IS NOT NULL
        AND isfinite(lv.pred_value)
),
stock_scores AS (
    SELECT symbol,
        SUM(weight * aligned_rank) / NULLIF(SUM(weight), 0) AS raw_score
    FROM ranked
    GROUP BY symbol
),
top_drivers AS (
    SELECT symbol,
        string_agg(
            predictor_field || '=' || CAST(ROUND(100 * aligned_rank, 0) AS VARCHAR),
            ', '
            ORDER BY aligned_rank DESC
        ) AS top_drivers
    FROM (
            SELECT symbol,
                predictor_field,
                aligned_rank,
                ROW_NUMBER() OVER (
                    PARTITION BY symbol
                    ORDER BY aligned_rank DESC
                ) AS rn
            FROM ranked
        ) x
    WHERE rn <= 3
    GROUP BY symbol
)
SELECT m.symbol,
    m.name,
    m.market,
    m.country,
    ROUND(100.0 * s.raw_score, 1) AS pattern_fit_score,
    td.top_drivers,
    ROUND(TRY_CAST(m."Perf.3M" AS DOUBLE), 1) AS perf_3m
FROM stock_scores s
    INNER JOIN run_rows_preferred m ON m.symbol = s.symbol
    LEFT JOIN top_drivers td USING (symbol)
ORDER BY s.raw_score DESC
LIMIT 25;
-- @block
-- [STOCK SCOUT 4] Single-symbol gradient — how one ticker aligns across all scout indicators.
-- Replace symbol literal.
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            VALUES ('america'),
                ('canada'),
                ('mexico'),
                ('austria'),
                ('belgium'),
                ('cyprus'),
                ('czech'),
                ('denmark'),
                ('estonia'),
                ('finland'),
                ('france'),
                ('germany'),
                ('greece'),
                ('hungary'),
                ('iceland'),
                ('ireland'),
                ('italy'),
                ('latvia'),
                ('lithuania'),
                ('luxembourg'),
                ('netherlands'),
                ('norway'),
                ('poland'),
                ('portugal'),
                ('romania'),
                ('slovakia'),
                ('spain'),
                ('sweden'),
                ('switzerland'),
                ('uk')
        ) AS preferred_markets(market_code)
),
run_rows_preferred AS (
    SELECT r.*
    FROM all_fields_rows r
        INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
),
patterns_pq AS (
    SELECT *
    FROM read_parquet(
            'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/all_fields_pattern_20260613_205803_89287088/per_run/tradingview_all_fields_20260612_2008_utc_028e0f86/field_performance_patterns.parquet'
        )
),
pattern_weights AS (
    SELECT predictor_field,
        performance_field,
        pattern_score,
        ABS(pattern_score) AS weight
    FROM patterns_pq
    WHERE performance_field LIKE 'Perf.%'
        AND predictor_field NOT LIKE 'change%'
        AND predictor_field NOT LIKE 'gap%'
        AND predictor_field NOT LIKE 'Mom%'
        AND predictor_field NOT LIKE 'ROC%'
        AND pair_n >= 3000
        AND predictor_field IN (
            'CCI20|1W',
            'RSI9|1W',
            'RSI10|1W',
            'RSI20|1W',
            'CCI20|1M'
        ) QUALIFY ROW_NUMBER() OVER (
            PARTITION BY predictor_field
            ORDER BY ABS(pattern_score) DESC
        ) = 1
),
universe AS (
    SELECT 'CCI20|1W' AS predictor_field,
        TRY_CAST("CCI20|1W" AS DOUBLE) AS pred_value
    FROM run_rows_preferred
    UNION ALL
    SELECT 'RSI9|1W',
        TRY_CAST("RSI9|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT 'RSI10|1W',
        TRY_CAST("RSI10|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT 'RSI20|1W',
        TRY_CAST("RSI20|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT 'CCI20|1M',
        TRY_CAST("CCI20|1M" AS DOUBLE)
    FROM run_rows_preferred
),
target AS (
    SELECT 'CCI20|1W' AS predictor_field,
        TRY_CAST("CCI20|1W" AS DOUBLE) AS pred_value
    FROM run_rows_preferred
    WHERE symbol = 'NASDAQ:AAPL'
    UNION ALL
    SELECT 'RSI9|1W',
        TRY_CAST("RSI9|1W" AS DOUBLE)
    FROM run_rows_preferred
    WHERE symbol = 'NASDAQ:AAPL'
    UNION ALL
    SELECT 'RSI10|1W',
        TRY_CAST("RSI10|1W" AS DOUBLE)
    FROM run_rows_preferred
    WHERE symbol = 'NASDAQ:AAPL'
    UNION ALL
    SELECT 'RSI20|1W',
        TRY_CAST("RSI20|1W" AS DOUBLE)
    FROM run_rows_preferred
    WHERE symbol = 'NASDAQ:AAPL'
    UNION ALL
    SELECT 'CCI20|1M',
        TRY_CAST("CCI20|1M" AS DOUBLE)
    FROM run_rows_preferred
    WHERE symbol = 'NASDAQ:AAPL'
),
pct AS (
    SELECT u.predictor_field,
        COUNT(*) AS universe_n,
        SUM(
            CASE
                WHEN u.pred_value <= t.pred_value THEN 1
                ELSE 0
            END
        )::DOUBLE / NULLIF(COUNT(*), 0) AS pct_rank
    FROM universe u
        INNER JOIN target t USING (predictor_field)
    WHERE u.pred_value IS NOT NULL
        AND t.pred_value IS NOT NULL
        AND isfinite(u.pred_value)
        AND isfinite(t.pred_value)
    GROUP BY u.predictor_field
)
SELECT pw.predictor_field,
    pw.performance_field,
    ROUND(pw.pattern_score, 4) AS pattern_score,
    ROUND(t.pred_value, 4) AS symbol_value,
    ROUND(pct.pct_rank, 3) AS universe_pct_rank,
    ROUND(
        100 * CASE
            WHEN pw.pattern_score >= 0 THEN pct.pct_rank
            ELSE 1.0 - pct.pct_rank
        END,
        1
    ) AS aligned_pct,
    ROUND(
        100 * pw.weight * CASE
            WHEN pw.pattern_score >= 0 THEN pct.pct_rank
            ELSE 1.0 - pct.pct_rank
        END / SUM(pw.weight) OVER (),
        2
    ) AS contribution_to_score
FROM pattern_weights pw
    LEFT JOIN target t USING (predictor_field)
    LEFT JOIN pct USING (predictor_field)
ORDER BY contribution_to_score DESC NULLS LAST;
-- @block
-- [STOCK SCOUT 5] Single-indicator quick rank — top quintile for one strong pattern.
-- Edit the TRY_CAST column to match predictor_field from STOCK SCOUT 1 (default: CCI20|1W → Perf.3M).
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            VALUES ('america'),
                ('canada'),
                ('mexico'),
                ('austria'),
                ('belgium'),
                ('cyprus'),
                ('czech'),
                ('denmark'),
                ('estonia'),
                ('finland'),
                ('france'),
                ('germany'),
                ('greece'),
                ('hungary'),
                ('iceland'),
                ('ireland'),
                ('italy'),
                ('latvia'),
                ('lithuania'),
                ('luxembourg'),
                ('netherlands'),
                ('norway'),
                ('poland'),
                ('portugal'),
                ('romania'),
                ('slovakia'),
                ('spain'),
                ('sweden'),
                ('switzerland'),
                ('uk')
        ) AS preferred_markets(market_code)
),
run_rows_preferred AS (
    SELECT r.*
    FROM all_fields_rows r
        INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
),
top1 AS (
    SELECT predictor_field,
        performance_field,
        pattern_score
    FROM read_parquet(
            'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/all_fields_pattern_20260613_205803_89287088/per_run/tradingview_all_fields_20260612_2008_utc_028e0f86/field_performance_patterns.parquet'
        )
    WHERE performance_field LIKE 'Perf.%'
        AND predictor_field NOT LIKE 'change%'
        AND predictor_field NOT LIKE 'gap%'
        AND predictor_field NOT LIKE 'Mom%'
        AND predictor_field NOT LIKE 'ROC%'
    ORDER BY pattern_score DESC
    LIMIT 1
), scored AS (
    SELECT r.symbol,
        r.name,
        r.market,
        r.country,
        TRY_CAST(r."Perf.3M" AS DOUBLE) AS perf_3m,
        TRY_CAST(r."CCI20|1W" AS DOUBLE) AS indicator_value,
        NTILE(5) OVER (
            ORDER BY TRY_CAST(r."CCI20|1W" AS DOUBLE)
        ) AS indicator_quintile,
        PERCENT_RANK() OVER (
            ORDER BY TRY_CAST(r."CCI20|1W" AS DOUBLE)
        ) AS indicator_pct
    FROM run_rows_preferred r
    WHERE TRY_CAST(r."CCI20|1W" AS DOUBLE) IS NOT NULL
)
SELECT s.symbol,
    s.name,
    s.market,
    s.country,
    ROUND(s.indicator_value, 2) AS indicator_value,
    s.indicator_quintile,
    ROUND(100 * s.indicator_pct, 1) AS pct_in_universe,
    ROUND(s.perf_3m, 2) AS perf_3m,
    t.predictor_field AS pattern_predictor,
    t.performance_field AS pattern_target,
    ROUND(t.pattern_score, 4) AS pattern_score
FROM scored s
    CROSS JOIN top1 t
WHERE s.indicator_quintile = 5
ORDER BY s.indicator_pct DESC
LIMIT 100;
-- =============================================================================
-- CLOSE-FORWARD AGGREGATE — conclusions + stock scout (preferred markets)
-- =============================================================================
-- Connect aggregate DB for pattern blocks below:
--   .../aggregates/all_fields_pattern_aggregate_20260614_182658_daaa8008/all_fields_pattern_aggregate.duckdb
--
-- Connect latest daily all-fields DuckDB for stock scout blocks (same as STOCK SCOUT 0–5):
--   .../12_06_2026/tradingview_all_fields_12_06_2026.duckdb
--
-- Conclusions from merged aggregate (5 runs, 4 with close_forward_return_pct):
--   • Raw top stability is dominated by gap_* fields — tautological vs next close; exclude for scouting.
--   • Durable non-tautological close_forward predictors (runs_seen=4, sign_consistency=1.0):
--       - Oversold oscillators (negative rank_stability_score): W.R|1M, Stoch.K_14_1_3|1M, W.R|1W,
--         Stoch.K_14_1_3|1W — lower values align with higher next-scan close return.
--       - Volatility (positive RSS): ATRP|1M — higher ATR% aligns with higher forward close return.
--       - Size (negative RSS): market_cap_basic — smaller names outperform to next scan.
--   • Trailing Perf.* scouts (STOCK SCOUT 2) favor RSI/CCI momentum; close-forward lens favors
--     oversold mean-reversion — use the blocks below when the strategy target is next-scan close.
-- @block
-- [AGG CF 1] Stable close_forward predictors — actionable only (no gap/change/Mom/ROC).
-- Connect: all_fields_pattern_aggregate.duckdb
SELECT predictor_field,
    runs_seen,
    ROUND(sign_consistency_ratio, 3) AS sign_consistency,
    ROUND(median_quintile_spread, 2) AS median_q_spread_pct,
    ROUND(rank_stability_score, 3) AS rank_stability_score,
    CASE
        WHEN rank_stability_score >= 0 THEN 'higher predictor → higher fwd close'
        ELSE 'lower predictor → higher fwd close'
    END AS directional_read
FROM cross_run_field_stability
WHERE performance_field = 'close_forward_return_pct'
    AND runs_seen >= 4
    AND sign_consistency_ratio >= 0.65
    AND predictor_field NOT LIKE 'change%'
    AND predictor_field NOT LIKE 'gap%'
    AND predictor_field NOT LIKE 'Mom%'
    AND predictor_field NOT LIKE 'ROC%'
    AND ABS(median_quintile_spread) >= 5
ORDER BY ABS(rank_stability_score) DESC NULLS LAST
LIMIT 40;
-- @block
-- [AGG CF 2] Close_forward vs trailing Perf.3M — same predictor, different target stability.
-- Helps decide whether a signal is a forward-close edge or a multi-month momentum read.
SELECT predictor_field,
    MAX(
        CASE
            WHEN performance_field = 'close_forward_return_pct' THEN rank_stability_score
        END
    ) AS close_fwd_rss,
    MAX(
        CASE
            WHEN performance_field = 'Perf.3M' THEN rank_stability_score
        END
    ) AS perf_3m_rss,
    MAX(
        CASE
            WHEN performance_field = 'close_forward_return_pct' THEN runs_seen
        END
    ) AS close_fwd_runs,
    MAX(
        CASE
            WHEN performance_field = 'Perf.3M' THEN runs_seen
        END
    ) AS perf_3m_runs
FROM cross_run_field_stability
WHERE performance_field IN ('close_forward_return_pct', 'Perf.3M')
    AND predictor_field NOT LIKE 'change%'
    AND predictor_field NOT LIKE 'gap%'
    AND predictor_field NOT LIKE 'Mom%'
    AND predictor_field NOT LIKE 'ROC%'
GROUP BY predictor_field
HAVING close_fwd_rss IS NOT NULL
    AND perf_3m_rss IS NOT NULL
ORDER BY ABS(close_fwd_rss) DESC NULLS LAST
LIMIT 30;
-- @block
-- [AGG CF 3] Per-run close_forward top patterns (latest aggregate pool).
SELECT source_day_label,
    run_id,
    predictor_field,
    pair_n,
    ROUND(TRY_CAST(spearman_corr_adjusted AS DOUBLE), 4) AS spearman_adj,
    ROUND(TRY_CAST(quintile_spread_adjusted AS DOUBLE), 2) AS quintile_spread_adj,
    ROUND(TRY_CAST(pattern_score AS DOUBLE), 4) AS pattern_score
FROM per_run_patterns
WHERE performance_field = 'close_forward_return_pct'
    AND predictor_field NOT LIKE 'change%'
    AND predictor_field NOT LIKE 'gap%'
    AND predictor_field NOT LIKE 'Mom%'
    AND predictor_field NOT LIKE 'ROC%'
    AND pair_n >= 3000
ORDER BY source_day_label DESC,
    ABS(TRY_CAST(pattern_score AS DOUBLE)) DESC NULLS LAST
LIMIT 50;
-- @block
-- [STOCK SCOUT CF 1] Top stable close_forward predictors to drive scouting (one row each).
-- Weights = rank_stability_score from aggregate (signed); flip percentile when RSS < 0.
SELECT predictor_field,
    ROUND(rank_stability_score, 3) AS rank_stability_score,
    ROUND(median_quintile_spread, 2) AS median_q_spread_pct,
    runs_seen,
    CASE
        WHEN rank_stability_score >= 0 THEN 'rank high in universe'
        ELSE 'rank low in universe (oversold / smaller cap)'
    END AS scout_direction
FROM cross_run_field_stability
WHERE performance_field = 'close_forward_return_pct'
    AND runs_seen >= 4
    AND sign_consistency_ratio >= 0.65
    AND predictor_field NOT LIKE 'change%'
    AND predictor_field NOT LIKE 'gap%'
    AND predictor_field NOT LIKE 'Mom%'
    AND predictor_field NOT LIKE 'ROC%'
    AND predictor_field IN (
        'W.R|1M',
        'Stoch.K_14_1_3|1M',
        'W.R|1W',
        'Stoch.K_14_1_3|1W',
        'Stoch.K_14_1_3',
        'HullMA200|1W',
        'ATRP|1M',
        'market_cap_basic'
    )
ORDER BY ABS(rank_stability_score) DESC;
-- @block
-- [STOCK SCOUT CF 2] Top 100 stocks — close_forward_fit_score from aggregate-stable predictors.
-- Universe = PREFERRED_MARKETS; weights from cross-run close_forward stability (not single-day Perf.*).
-- Strategy lens: oversold W.R/Stoch + elevated ATRP + smaller cap within preferred markets.
-- Connect: daily all-fields DuckDB (12_06_2026). Edit run_id / mcap / min_signals as needed.
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            VALUES ('america'),
                ('canada'),
                ('mexico'),
                ('austria'),
                ('belgium'),
                ('cyprus'),
                ('czech'),
                ('denmark'),
                ('estonia'),
                ('finland'),
                ('france'),
                ('germany'),
                ('greece'),
                ('hungary'),
                ('iceland'),
                ('ireland'),
                ('italy'),
                ('latvia'),
                ('lithuania'),
                ('luxembourg'),
                ('netherlands'),
                ('norway'),
                ('poland'),
                ('portugal'),
                ('romania'),
                ('slovakia'),
                ('spain'),
                ('sweden'),
                ('switzerland'),
                ('uk')
        ) AS preferred_markets(market_code)
),
run_rows_preferred AS (
    SELECT r.*
    FROM all_fields_rows r
        INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
),
pattern_weights AS (
    SELECT predictor_field,
        rank_stability_score AS rss,
        ABS(rank_stability_score) AS weight
    FROM (
            VALUES ('W.R|1M', -16.182),
                ('Stoch.K_14_1_3|1M', -16.136),
                ('W.R|1W', -15.860),
                ('Stoch.K_14_1_3|1W', -15.738),
                ('Stoch.K_14_1_3', -15.753),
                ('HullMA200|1W', -15.909),
                ('ATRP|1M', 15.450),
                ('market_cap_basic', -15.507)
        ) AS t(predictor_field, rank_stability_score)
),
long_vals AS (
    SELECT symbol,
        'W.R|1M' AS predictor_field,
        TRY_CAST("W.R|1M" AS DOUBLE) AS pred_value
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'Stoch.K_14_1_3|1M',
        TRY_CAST("Stoch.K_14_1_3|1M" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'W.R|1W',
        TRY_CAST("W.R|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'Stoch.K_14_1_3|1W',
        TRY_CAST("Stoch.K_14_1_3|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'Stoch.K_14_1_3',
        TRY_CAST("Stoch.K_14_1_3" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'HullMA200|1W',
        TRY_CAST("HullMA200|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'ATRP|1M',
        TRY_CAST("ATRP|1M" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'market_cap_basic',
        TRY_CAST(market_cap_basic AS DOUBLE)
    FROM run_rows_preferred
),
ranked AS (
    SELECT lv.symbol,
        lv.predictor_field,
        pw.rss,
        pw.weight,
        PERCENT_RANK() OVER (
            PARTITION BY lv.predictor_field
            ORDER BY lv.pred_value
        ) AS pred_pct_rank
    FROM long_vals lv
        INNER JOIN pattern_weights pw USING (predictor_field)
    WHERE lv.pred_value IS NOT NULL
        AND isfinite(lv.pred_value)
),
aligned AS (
    SELECT symbol,
        predictor_field,
        weight,
        CASE
            WHEN rss >= 0 THEN pred_pct_rank
            ELSE 1.0 - pred_pct_rank
        END AS aligned_rank
    FROM ranked
),
stock_scores AS (
    SELECT a.symbol,
        COUNT(*) AS signals_matched,
        SUM(weight) AS total_weight,
        SUM(weight * aligned_rank) / NULLIF(SUM(weight), 0) AS raw_score
    FROM aligned a
    GROUP BY a.symbol
),
meta AS (
    SELECT symbol,
        name,
        market,
        country,
        sector,
        industry,
        TRY_CAST(close AS DOUBLE) AS close,
        TRY_CAST(market_cap_basic AS DOUBLE) AS market_cap_basic,
        TRY_CAST("W.R|1M" AS DOUBLE) AS wr_1m,
        TRY_CAST("Stoch.K_14_1_3|1M" AS DOUBLE) AS stoch_1m,
        TRY_CAST("ATRP|1M" AS DOUBLE) AS atrp_1m
    FROM run_rows_preferred
)
SELECT m.symbol,
    m.name,
    m.market,
    m.country,
    m.sector,
    m.industry,
    ROUND(m.close, 4) AS close,
    ROUND(m.market_cap_basic / 1e9, 2) AS mcap_b_usd,
    ROUND(m.wr_1m, 1) AS wr_1m,
    ROUND(m.stoch_1m, 1) AS stoch_k_1m,
    ROUND(m.atrp_1m, 2) AS atrp_1m,
    s.signals_matched,
    ROUND(100.0 * s.raw_score, 2) AS close_fwd_fit_score,
    CASE
        WHEN 100.0 * s.raw_score >= 85 THEN 'A'
        WHEN 100.0 * s.raw_score >= 70 THEN 'B'
        WHEN 100.0 * s.raw_score >= 55 THEN 'C'
        ELSE 'D'
    END AS fit_tier,
    RANK() OVER (
        ORDER BY s.raw_score DESC
    ) AS scout_rank
FROM stock_scores s
    INNER JOIN meta m USING (symbol)
WHERE s.signals_matched >= 6
    AND m.market_cap_basic >= 1e9
ORDER BY s.raw_score DESC
LIMIT 100;
-- @block
-- [STOCK SCOUT CF 3] Short list — top 25 close-forward fits with driver snapshot.
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            VALUES ('america'),
                ('canada'),
                ('mexico'),
                ('austria'),
                ('belgium'),
                ('cyprus'),
                ('czech'),
                ('denmark'),
                ('estonia'),
                ('finland'),
                ('france'),
                ('germany'),
                ('greece'),
                ('hungary'),
                ('iceland'),
                ('ireland'),
                ('italy'),
                ('latvia'),
                ('lithuania'),
                ('luxembourg'),
                ('netherlands'),
                ('norway'),
                ('poland'),
                ('portugal'),
                ('romania'),
                ('slovakia'),
                ('spain'),
                ('sweden'),
                ('switzerland'),
                ('uk')
        ) AS preferred_markets(market_code)
),
run_rows_preferred AS (
    SELECT r.*
    FROM all_fields_rows r
        INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
),
pattern_weights AS (
    SELECT predictor_field,
        rank_stability_score AS rss,
        ABS(rank_stability_score) AS weight
    FROM (
            VALUES ('W.R|1M', -16.182),
                ('Stoch.K_14_1_3|1M', -16.136),
                ('W.R|1W', -15.860),
                ('Stoch.K_14_1_3|1W', -15.738),
                ('ATRP|1M', 15.450)
        ) AS t(predictor_field, rank_stability_score)
),
long_vals AS (
    SELECT symbol,
        'W.R|1M' AS predictor_field,
        TRY_CAST("W.R|1M" AS DOUBLE) AS pred_value
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'Stoch.K_14_1_3|1M',
        TRY_CAST("Stoch.K_14_1_3|1M" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'W.R|1W',
        TRY_CAST("W.R|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'Stoch.K_14_1_3|1W',
        TRY_CAST("Stoch.K_14_1_3|1W" AS DOUBLE)
    FROM run_rows_preferred
    UNION ALL
    SELECT symbol,
        'ATRP|1M',
        TRY_CAST("ATRP|1M" AS DOUBLE)
    FROM run_rows_preferred
),
ranked AS (
    SELECT lv.symbol,
        lv.predictor_field,
        pw.weight,
        CASE
            WHEN pw.rss >= 0 THEN PERCENT_RANK() OVER (
                PARTITION BY lv.predictor_field
                ORDER BY lv.pred_value
            )
            ELSE 1.0 - PERCENT_RANK() OVER (
                PARTITION BY lv.predictor_field
                ORDER BY lv.pred_value
            )
        END AS aligned_rank
    FROM long_vals lv
        INNER JOIN pattern_weights pw USING (predictor_field)
    WHERE lv.pred_value IS NOT NULL
        AND isfinite(lv.pred_value)
),
stock_scores AS (
    SELECT symbol,
        SUM(weight * aligned_rank) / NULLIF(SUM(weight), 0) AS raw_score
    FROM ranked
    GROUP BY symbol
),
top_drivers AS (
    SELECT symbol,
        string_agg(
            predictor_field || '=' || CAST(ROUND(100 * aligned_rank, 0) AS VARCHAR),
            ', '
            ORDER BY aligned_rank DESC
        ) AS top_drivers
    FROM (
            SELECT symbol,
                predictor_field,
                aligned_rank,
                ROW_NUMBER() OVER (
                    PARTITION BY symbol
                    ORDER BY aligned_rank DESC
                ) AS rn
            FROM ranked
        ) x
    WHERE rn <= 3
    GROUP BY symbol
)
SELECT m.symbol,
    m.name,
    m.market,
    m.country,
    ROUND(100.0 * s.raw_score, 1) AS close_fwd_fit_score,
    td.top_drivers,
    ROUND(TRY_CAST(m."W.R|1M" AS DOUBLE), 1) AS wr_1m,
    ROUND(TRY_CAST(m."Stoch.K_14_1_3|1M" AS DOUBLE), 1) AS stoch_k_1m,
    ROUND(TRY_CAST(m."ATRP|1M" AS DOUBLE), 2) AS atrp_1m
FROM stock_scores s
    INNER JOIN run_rows_preferred m ON m.symbol = s.symbol
    LEFT JOIN top_drivers td USING (symbol)
ORDER BY s.raw_score DESC
LIMIT 25;
-- @block
-- [STOCK SCOUT CF 4] Oversold single-pattern rank — bottom quintile W.R|1M (strongest stable close_fwd signal).
-- Lower Williams %R ⇒ higher aligned rank for next-scan close return.
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            VALUES ('america'),
                ('canada'),
                ('mexico'),
                ('austria'),
                ('belgium'),
                ('cyprus'),
                ('czech'),
                ('denmark'),
                ('estonia'),
                ('finland'),
                ('france'),
                ('germany'),
                ('greece'),
                ('hungary'),
                ('iceland'),
                ('ireland'),
                ('italy'),
                ('latvia'),
                ('lithuania'),
                ('luxembourg'),
                ('netherlands'),
                ('norway'),
                ('poland'),
                ('portugal'),
                ('romania'),
                ('slovakia'),
                ('spain'),
                ('sweden'),
                ('switzerland'),
                ('uk')
        ) AS preferred_markets(market_code)
),
run_rows_preferred AS (
    SELECT r.*
    FROM all_fields_rows r
        INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
),
scored AS (
    SELECT r.symbol,
        r.name,
        r.market,
        r.country,
        r.sector,
        TRY_CAST(r."W.R|1M" AS DOUBLE) AS wr_1m,
        TRY_CAST(r."Stoch.K_14_1_3|1M" AS DOUBLE) AS stoch_k_1m,
        TRY_CAST(r."ATRP|1M" AS DOUBLE) AS atrp_1m,
        TRY_CAST(r.market_cap_basic AS DOUBLE) AS market_cap_basic,
        TRY_CAST(r.close AS DOUBLE) AS close,
        NTILE(5) OVER (
            ORDER BY TRY_CAST(r."W.R|1M" AS DOUBLE)
        ) AS wr_quintile,
        PERCENT_RANK() OVER (
            ORDER BY TRY_CAST(r."W.R|1M" AS DOUBLE)
        ) AS wr_pct
    FROM run_rows_preferred r
    WHERE TRY_CAST(r."W.R|1M" AS DOUBLE) IS NOT NULL
)
SELECT s.symbol,
    s.name,
    s.market,
    s.country,
    s.sector,
    ROUND(s.close, 2) AS close,
    ROUND(s.market_cap_basic / 1e9, 2) AS mcap_b_usd,
    ROUND(s.wr_1m, 1) AS wr_1m,
    s.wr_quintile,
    ROUND(100 * s.wr_pct, 1) AS wr_pct_in_universe,
    ROUND(s.stoch_k_1m, 1) AS stoch_k_1m,
    ROUND(s.atrp_1m, 2) AS atrp_1m
FROM scored s
WHERE s.wr_quintile = 1
    AND s.market_cap_basic >= 1e9
ORDER BY s.wr_pct ASC,
    s.atrp_1m DESC NULLS LAST
LIMIT 100;
-- @block
-- [STOCK SCOUT CF 5] Backtest lens — realized close_forward_return_pct by W.R|1M quintile (preferred markets).
-- Connect: close_forward_analysis_input.duckdb
--   .../close_forward_pattern_20260614_171638_32d7b60b/close_forward_analysis_input.duckdb
-- ATTACH latest day DB once per session for market filter (edit path / run_id if needed).
ATTACH 'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/12_06_2026/tradingview_all_fields_12_06_2026.duckdb' AS latest_day (READ_ONLY);
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            VALUES ('america'),
                ('canada'),
                ('mexico'),
                ('austria'),
                ('belgium'),
                ('cyprus'),
                ('czech'),
                ('denmark'),
                ('estonia'),
                ('finland'),
                ('france'),
                ('germany'),
                ('greece'),
                ('hungary'),
                ('iceland'),
                ('ireland'),
                ('italy'),
                ('latvia'),
                ('lithuania'),
                ('luxembourg'),
                ('netherlands'),
                ('norway'),
                ('poland'),
                ('portugal'),
                ('romania'),
                ('slovakia'),
                ('spain'),
                ('sweden'),
                ('switzerland'),
                ('uk')
        ) AS preferred_markets(market_code)
),
preferred_symbols AS (
    SELECT DISTINCT r.symbol
    FROM latest_day.all_fields_rows r
        INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
),
oversold_hist AS (
    SELECT symbol,
        run_id,
        TRY_CAST("W.R|1M" AS DOUBLE) AS wr_1m,
        TRY_CAST(close_forward_return_pct AS DOUBLE) AS fwd_ret,
        NTILE(5) OVER (
            PARTITION BY run_id
            ORDER BY TRY_CAST("W.R|1M" AS DOUBLE)
        ) AS wr_quintile
    FROM all_fields_rows
    WHERE TRY_CAST("W.R|1M" AS DOUBLE) IS NOT NULL
        AND TRY_CAST(close_forward_return_pct AS DOUBLE) IS NOT NULL
        AND symbol IN (
            SELECT symbol
            FROM preferred_symbols
        )
)
SELECT run_id,
    wr_quintile,
    COUNT(*) AS symbol_count,
    ROUND(AVG(fwd_ret), 3) AS avg_fwd_close_return_pct,
    ROUND(MEDIAN(fwd_ret), 3) AS median_fwd_close_return_pct
FROM oversold_hist
GROUP BY run_id,
    wr_quintile
ORDER BY run_id,
    wr_quintile;
-- @block
-- [STOCK SCOUT CF 5b] Pooled backtest summary — Q1 (oversold) vs Q5 W.R|1M forward close spread.
-- Same connection as CF 5 (close_forward_analysis_input + ATTACH latest_day).
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            VALUES ('america'),
                ('canada'),
                ('mexico'),
                ('austria'),
                ('belgium'),
                ('cyprus'),
                ('czech'),
                ('denmark'),
                ('estonia'),
                ('finland'),
                ('france'),
                ('germany'),
                ('greece'),
                ('hungary'),
                ('iceland'),
                ('ireland'),
                ('italy'),
                ('latvia'),
                ('lithuania'),
                ('luxembourg'),
                ('netherlands'),
                ('norway'),
                ('poland'),
                ('portugal'),
                ('romania'),
                ('slovakia'),
                ('spain'),
                ('sweden'),
                ('switzerland'),
                ('uk')
        ) AS preferred_markets(market_code)
),
preferred_symbols AS (
    SELECT DISTINCT r.symbol
    FROM latest_day.all_fields_rows r
        INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
),
quintile_stats AS (
    SELECT wr_quintile,
        COUNT(*) AS observations,
        ROUND(AVG(fwd_ret), 3) AS avg_fwd_close_return_pct,
        ROUND(MEDIAN(fwd_ret), 3) AS median_fwd_close_return_pct
    FROM (
            SELECT TRY_CAST(close_forward_return_pct AS DOUBLE) AS fwd_ret,
                NTILE(5) OVER (
                    PARTITION BY run_id
                    ORDER BY TRY_CAST("W.R|1M" AS DOUBLE)
                ) AS wr_quintile
            FROM all_fields_rows
            WHERE TRY_CAST("W.R|1M" AS DOUBLE) IS NOT NULL
                AND TRY_CAST(close_forward_return_pct AS DOUBLE) IS NOT NULL
                AND symbol IN (
                    SELECT symbol
                    FROM preferred_symbols
                )
        ) x
    GROUP BY wr_quintile
)
SELECT qs.*,
    ROUND(
        (
            SELECT avg_fwd_close_return_pct
            FROM quintile_stats
            WHERE wr_quintile = 1
        ) - (
            SELECT avg_fwd_close_return_pct
            FROM quintile_stats
            WHERE wr_quintile = 5
        ),
        3
    ) AS q1_minus_q5_avg_spread
FROM quintile_stats qs
ORDER BY qs.wr_quintile;
-- =============================================================================
-- [CF DD898100] Close-forward stock rankings — scan_period_close_forward_tracking_25may_12jun2026
-- =============================================================================
-- Source advice: pattern_analysis/advised_conclusions/close_forward_25may_12jun2026_dd898100_advised_conclusions.md
-- CSV exports: .../runs/scan_period_close_forward_tracking_25may_12jun2026_dd898100/predictor_stock_rankings/
--   indicator_perf/00_*.csv — TOP predictors + quintile correlation/perf (pivot in Excel for charts)
-- Re-generate CSVs: python scripts/run_close_forward_predictor_stock_rankings.py --top-n 100
--
-- WHICH DUCKDB TO TARGET
-- ----------------------
-- | Use case | Database | Notes |
-- | Predictor stability / which fields matter | .../aggregates/all_fields_pattern_aggregate.duckdb | cross_run_field_stability |
-- | Historical 7d forward returns (26 May–11 Jun) | .../close_forward/close_returns/cross_run_close_returns.duckdb | 13 scored source days |
-- | Enriched rows for joins (predictors + labels) | .../close_forward/close_forward_analysis_input.duckdb | all_fields_rows; no name/market cols |
-- | Latest positioning (12 Jun, no forward label) | .../12_06_2026/tradingview_all_fields_12_06_2026.duckdb | run_id below |
-- | Do NOT use close_forward_analysis_input alone for live screens | ends at scored rows through 11 Jun |
--
-- Multi-DB blocks: run the ATTACH block once per session, then run ranking blocks.
-- @block
-- [CF DD898100 SETUP] Attach run databases (run once per session).
ATTACH 'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/scan_period_close_forward_tracking_25may_12jun2026_dd898100/aggregates/all_fields_pattern_aggregate.duckdb' AS agg (READ_ONLY);
ATTACH 'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/scan_period_close_forward_tracking_25may_12jun2026_dd898100/close_forward/close_forward_analysis_input.duckdb' AS enr (READ_ONLY);
ATTACH 'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs/scan_period_close_forward_tracking_25may_12jun2026_dd898100/close_forward/close_returns/cross_run_close_returns.duckdb' AS cr (READ_ONLY);
ATTACH 'D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/12_06_2026/tradingview_all_fields_12_06_2026.duckdb' AS day12 (READ_ONLY);
-- daily run_id: tradingview_all_fields_20260612_2008_utc_028e0f86
-- @block
-- [CF DD898100 R1] Composite bullish fit score — 12 Jun (no LIMIT).
WITH preferred_market_codes AS (
    SELECT market_code FROM (
        VALUES ('america'),('canada'),('mexico'),('austria'),('belgium'),('cyprus'),('czech'),
        ('denmark'),('estonia'),('finland'),('france'),('germany'),('greece'),('hungary'),
        ('iceland'),('ireland'),('italy'),('latvia'),('lithuania'),('luxembourg'),('netherlands'),
        ('norway'),('poland'),('portugal'),('romania'),('slovakia'),('spain'),('sweden'),
        ('switzerland'),('uk')
    ) AS preferred_markets(market_code)
),
run_rows_preferred AS (
    SELECT r.* FROM day12.all_fields_rows r
    INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
      AND TRY_CAST(r.market_cap_basic AS DOUBLE) >= 500000000
      AND TRY_CAST(r.close AS DOUBLE) >= 10
),
pattern_weights AS (
    SELECT predictor_field, rank_stability_score AS rss, ABS(rank_stability_score) AS weight
    FROM agg.cross_run_field_stability
    WHERE performance_field = 'close_forward_return_pct'
      AND predictor_field IN (
          'ATRP|1W','ATRP','ADRP|15','ADRP|1W','ADX-DI|1M','ADX-DI_50|1M','relative_volume',
          'RSI21[1]|1M','Stoch.K_14_1_3|1M','W.R|1M','Recommend.MA|1M','oper_income_ttm','ebitda_ttm'
      )
),
long_vals AS (
    SELECT symbol, 'ATRP|1W' AS predictor_field, TRY_CAST("ATRP|1W" AS DOUBLE) AS pred_value FROM run_rows_preferred
    UNION ALL SELECT symbol, 'ATRP', TRY_CAST(ATRP AS DOUBLE) FROM run_rows_preferred
    UNION ALL SELECT symbol, 'ADRP|15', TRY_CAST("ADRP|15" AS DOUBLE) FROM run_rows_preferred
    UNION ALL SELECT symbol, 'ADRP|1W', TRY_CAST("ADRP|1W" AS DOUBLE) FROM run_rows_preferred
    UNION ALL SELECT symbol, 'ADX-DI|1M', TRY_CAST("ADX-DI|1M" AS DOUBLE) FROM run_rows_preferred
    UNION ALL SELECT symbol, 'ADX-DI_50|1M', TRY_CAST("ADX-DI_50|1M" AS DOUBLE) FROM run_rows_preferred
    UNION ALL SELECT symbol, 'relative_volume', TRY_CAST(relative_volume AS DOUBLE) FROM run_rows_preferred
    UNION ALL SELECT symbol, 'RSI21[1]|1M', TRY_CAST("RSI21[1]|1M" AS DOUBLE) FROM run_rows_preferred
    UNION ALL SELECT symbol, 'Stoch.K_14_1_3|1M', TRY_CAST("Stoch.K_14_1_3|1M" AS DOUBLE) FROM run_rows_preferred
    UNION ALL SELECT symbol, 'W.R|1M', TRY_CAST("W.R|1M" AS DOUBLE) FROM run_rows_preferred
    UNION ALL SELECT symbol, 'Recommend.MA|1M', TRY_CAST("Recommend.MA|1M" AS DOUBLE) FROM run_rows_preferred
    UNION ALL SELECT symbol, 'oper_income_ttm', TRY_CAST(oper_income_ttm AS DOUBLE) FROM run_rows_preferred
    UNION ALL SELECT symbol, 'ebitda_ttm', TRY_CAST(ebitda_ttm AS DOUBLE) FROM run_rows_preferred
),
ranked AS (
    SELECT lv.symbol, lv.predictor_field, pw.rss, pw.weight,
        PERCENT_RANK() OVER (PARTITION BY lv.predictor_field ORDER BY lv.pred_value) AS pred_pct_rank
    FROM long_vals lv INNER JOIN pattern_weights pw USING (predictor_field)
    WHERE lv.pred_value IS NOT NULL AND isfinite(lv.pred_value)
),
aligned AS (
    SELECT symbol, predictor_field, weight,
        CASE WHEN rss >= 0 THEN pred_pct_rank ELSE 1.0 - pred_pct_rank END AS aligned_rank
    FROM ranked
),
stock_scores AS (
    SELECT symbol, COUNT(*) AS signals_matched,
        SUM(weight * aligned_rank) / NULLIF(SUM(weight), 0) AS raw_score
    FROM aligned GROUP BY symbol HAVING COUNT(*) >= 7
)
SELECT m.symbol, m.name, m.market, m.sector,
    ROUND(TRY_CAST(m.close AS DOUBLE), 2) AS close,
    ROUND(TRY_CAST(m.market_cap_basic AS DOUBLE) / 1e9, 2) AS mcap_b_usd,
    ROUND(TRY_CAST(m."ATRP|1W" AS DOUBLE), 2) AS atrp_1w,
    ROUND(TRY_CAST(m.relative_volume AS DOUBLE), 2) AS relative_volume,
    ROUND(100.0 * s.raw_score, 2) AS close_fwd_fit_score,
    RANK() OVER (ORDER BY s.raw_score DESC) AS rank
FROM stock_scores s INNER JOIN run_rows_preferred m USING (symbol)
ORDER BY s.raw_score DESC;
-- @block
-- [CF DD898100 R2] Playbook A — volatility continuation on 12 Jun (no LIMIT).
WITH preferred_market_codes AS (
    SELECT market_code FROM (
        VALUES ('america'),('canada'),('mexico'),('austria'),('belgium'),('cyprus'),('czech'),
        ('denmark'),('estonia'),('finland'),('france'),('germany'),('greece'),('hungary'),
        ('iceland'),('ireland'),('italy'),('latvia'),('lithuania'),('luxembourg'),('netherlands'),
        ('norway'),('poland'),('portugal'),('romania'),('slovakia'),('spain'),('sweden'),
        ('switzerland'),('uk')
    ) AS preferred_markets(market_code)
),
base AS (
    SELECT r.symbol, r.name, r.market, r.sector,
        TRY_CAST(r.close AS DOUBLE) AS close,
        TRY_CAST(r.market_cap_basic AS DOUBLE) AS market_cap_basic,
        TRY_CAST(r."ATRP|1W" AS DOUBLE) AS atrp_1w,
        TRY_CAST(r.relative_volume AS DOUBLE) AS relvol,
        TRY_CAST(r."ADX-DI|5" AS DOUBLE) AS adx_di5,
        TRY_CAST(r."Stoch.K_14_1_3|1M" AS DOUBLE) AS stoch_k_1m
    FROM day12.all_fields_rows r
    INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
      AND TRY_CAST(r.market_cap_basic AS DOUBLE) >= 500000000
      AND TRY_CAST(r.close AS DOUBLE) >= 10
),
med AS (SELECT MEDIAN(atrp_1w) AS atrp_median FROM base),
stoch_med AS (SELECT MEDIAN(stoch_k_1m) AS stoch_median FROM base)
SELECT b.symbol, b.name, b.market, b.sector,
    ROUND(b.close, 2) AS close,
    ROUND(b.market_cap_basic / 1e9, 2) AS mcap_b_usd,
    ROUND(b.atrp_1w, 2) AS atrp_1w,
    ROUND(b.relvol, 2) AS relative_volume,
    ROUND(b.adx_di5, 1) AS adx_di5,
    ROUND(b.stoch_k_1m, 1) AS stoch_k_1m,
    RANK() OVER (ORDER BY b.atrp_1w DESC, b.relvol DESC, b.adx_di5 DESC NULLS LAST) AS rank
FROM base b CROSS JOIN med CROSS JOIN stoch_med sm
WHERE b.atrp_1w > med.atrp_median AND b.relvol > 1.5
  AND (b.stoch_k_1m IS NULL OR b.stoch_k_1m > sm.stoch_median)
ORDER BY b.atrp_1w DESC, b.relvol DESC, b.adx_di5 DESC NULLS LAST;
-- @block
-- [CF DD898100 R3] Playbook B — quality drift (no LIMIT).
WITH joined AS (
    SELECT cr.symbol, cr.close_forward_return_pct AS fwd
    FROM cr.cross_run_close_returns cr
    INNER JOIN enr.all_fields_rows a USING (run_id, symbol)
    WHERE TRY_CAST(a.market_cap_basic AS DOUBLE) >= 500000000
      AND cr.close_forward_return_pct BETWEEN -25 AND 25
      AND cr.close_price >= 10
)
SELECT symbol, COUNT(*) AS n_days,
    ROUND(AVG(fwd), 2) AS avg_fwd_pct,
    ROUND(STDDEV(fwd), 2) AS stdev_pct,
    ROUND(AVG(CASE WHEN fwd > 0 THEN 1.0 ELSE 0 END), 2) AS win_rate,
    RANK() OVER (ORDER BY AVG(fwd) DESC) AS rank
FROM joined
GROUP BY symbol
HAVING COUNT(*) >= 10 AND AVG(fwd) > 1.5
   AND AVG(CASE WHEN fwd > 0 THEN 1.0 ELSE 0 END) >= 0.60
ORDER BY avg_fwd_pct DESC;
-- @block
-- [CF DD898100 R4] Trim list — negative drift (no LIMIT).
WITH joined AS (
    SELECT cr.symbol, cr.close_forward_return_pct AS fwd
    FROM cr.cross_run_close_returns cr
    INNER JOIN enr.all_fields_rows a USING (run_id, symbol)
    WHERE TRY_CAST(a.market_cap_basic AS DOUBLE) >= 500000000
      AND cr.close_forward_return_pct BETWEEN -25 AND 25
      AND cr.close_price >= 10
)
SELECT symbol, COUNT(*) AS n_days,
    ROUND(AVG(fwd), 2) AS avg_fwd_pct,
    ROUND(STDDEV(fwd), 2) AS stdev_pct,
    ROUND(AVG(CASE WHEN fwd > 0 THEN 1.0 ELSE 0 END), 2) AS win_rate,
    RANK() OVER (ORDER BY AVG(fwd) ASC) AS rank
FROM joined
GROUP BY symbol
HAVING COUNT(*) >= 8 AND AVG(fwd) < -2
ORDER BY avg_fwd_pct ASC;
-- @block
-- [CF DD898100 R5] Warning overlay — 2+ avoid flags on 12 Jun (no LIMIT).
WITH preferred_market_codes AS (
    SELECT market_code FROM (
        VALUES ('america'),('canada'),('mexico'),('austria'),('belgium'),('cyprus'),('czech'),
        ('denmark'),('estonia'),('finland'),('france'),('germany'),('greece'),('hungary'),
        ('iceland'),('ireland'),('italy'),('latvia'),('lithuania'),('luxembourg'),('netherlands'),
        ('norway'),('poland'),('portugal'),('romania'),('slovakia'),('spain'),('sweden'),
        ('switzerland'),('uk')
    ) AS preferred_markets(market_code)
),
base AS (
    SELECT r.symbol, r.name, r.market,
        TRY_CAST(r.close AS DOUBLE) AS close,
        TRY_CAST(r."Stoch.K_14_1_3|1M" AS DOUBLE) AS stoch_k_1m,
        TRY_CAST(r."W.R|1M" AS DOUBLE) AS wr_1m,
        TRY_CAST(r."Recommend.MA|1M" AS DOUBLE) AS rec_ma_1m,
        TRY_CAST(r.oper_income_ttm AS DOUBLE) AS oper_income_ttm,
        TRY_CAST(r.ebitda_ttm AS DOUBLE) AS ebitda_ttm
    FROM day12.all_fields_rows r
    INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
      AND TRY_CAST(r.market_cap_basic AS DOUBLE) >= 500000000
      AND TRY_CAST(r.close AS DOUBLE) >= 10
),
hist AS (
    SELECT symbol, ROUND(AVG(fwd), 2) AS avg_fwd_pct
    FROM (
        SELECT cr.symbol, cr.close_forward_return_pct AS fwd
        FROM cr.cross_run_close_returns cr
        INNER JOIN enr.all_fields_rows a USING (run_id, symbol)
        WHERE TRY_CAST(a.market_cap_basic AS DOUBLE) >= 500000000
          AND cr.close_forward_return_pct BETWEEN -25 AND 25
          AND cr.close_price >= 10
    ) x
    GROUP BY symbol HAVING COUNT(*) >= 5
),
quintiles AS (
    SELECT symbol, name, market, close, stoch_k_1m, wr_1m, rec_ma_1m,
        NTILE(5) OVER (ORDER BY stoch_k_1m) AS stoch_q,
        NTILE(5) OVER (ORDER BY wr_1m) AS wr_q,
        NTILE(5) OVER (ORDER BY rec_ma_1m) AS rec_ma_q,
        NTILE(5) OVER (ORDER BY oper_income_ttm) AS oper_q,
        NTILE(5) OVER (ORDER BY ebitda_ttm) AS ebitda_q,
        oper_income_ttm,
        ebitda_ttm
    FROM base
),
scored AS (
    SELECT q.symbol, q.name, q.market, q.close, h.avg_fwd_pct,
        (CASE WHEN q.stoch_q = 1 THEN 1 ELSE 0 END
         + CASE WHEN q.wr_q = 1 THEN 1 ELSE 0 END
         + CASE WHEN q.rec_ma_q = 5 AND COALESCE(h.avg_fwd_pct, 0) < 0 THEN 1 ELSE 0 END
         + CASE WHEN q.oper_q = 5 THEN 1 ELSE 0 END
         + CASE WHEN q.ebitda_q = 5 THEN 1 ELSE 0 END) AS warning_flag_count
    FROM quintiles q LEFT JOIN hist h USING (symbol)
)
SELECT symbol, name, market, ROUND(close, 2) AS close, avg_fwd_pct, warning_flag_count,
    RANK() OVER (ORDER BY warning_flag_count DESC, COALESCE(avg_fwd_pct, 999) ASC) AS rank
FROM scored
WHERE warning_flag_count >= 2
ORDER BY warning_flag_count DESC, COALESCE(avg_fwd_pct, 999) ASC;
-- @block
-- [CF DD898100 R6] Playbook A realized on 11 Jun (no LIMIT).
WITH base AS (
    SELECT cr.symbol, cr.close_price, cr.close_forward_return_pct AS fwd,
        TRY_CAST(a."ATRP|1W" AS DOUBLE) AS atrp_1w,
        TRY_CAST(a.relative_volume AS DOUBLE) AS relvol,
        TRY_CAST(a."ADX-DI|5" AS DOUBLE) AS adx_di5
    FROM cr.cross_run_close_returns cr
    INNER JOIN enr.all_fields_rows a USING (run_id, symbol)
    WHERE cr.source_day_label = '11_06_2026'
      AND TRY_CAST(a.market_cap_basic AS DOUBLE) >= 500000000
      AND cr.close_price >= 10
),
med AS (SELECT MEDIAN(atrp_1w) AS m FROM base)
SELECT b.symbol,
    ROUND(b.close_price, 2) AS close_px,
    ROUND(b.fwd, 2) AS realized_fwd_pct,
    ROUND(b.atrp_1w, 2) AS atrp_1w,
    ROUND(b.relvol, 2) AS relative_volume,
    ROUND(b.adx_di5, 1) AS adx_di5,
    RANK() OVER (ORDER BY b.fwd DESC) AS rank
FROM base b, med
WHERE b.atrp_1w > med.m AND b.relvol > 1.5
ORDER BY b.fwd DESC;
-- @block
-- [CF DD898100 R7] Single bullish predictor — ATRP|1W (no LIMIT). Swap field for other by_predictor CSVs.
WITH preferred_market_codes AS (
    SELECT market_code FROM (
        VALUES ('america'),('canada'),('mexico'),('austria'),('belgium'),('cyprus'),('czech'),
        ('denmark'),('estonia'),('finland'),('france'),('germany'),('greece'),('hungary'),
        ('iceland'),('ireland'),('italy'),('latvia'),('lithuania'),('luxembourg'),('netherlands'),
        ('norway'),('poland'),('portugal'),('romania'),('slovakia'),('spain'),('sweden'),
        ('switzerland'),('uk')
    ) AS preferred_markets(market_code)
),
base AS (
    SELECT r.symbol, r.name, r.market, r.sector,
        TRY_CAST(r.close AS DOUBLE) AS close,
        TRY_CAST(r.market_cap_basic AS DOUBLE) AS market_cap_basic,
        TRY_CAST(r."ATRP|1W" AS DOUBLE) AS predictor_value
    FROM day12.all_fields_rows r
    INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
      AND TRY_CAST(r.market_cap_basic AS DOUBLE) >= 500000000
      AND TRY_CAST(r.close AS DOUBLE) >= 10
      AND TRY_CAST(r."ATRP|1W" AS DOUBLE) IS NOT NULL
)
SELECT symbol, name, market, sector,
    ROUND(close, 2) AS close,
    ROUND(market_cap_basic / 1e9, 2) AS mcap_b_usd,
    ROUND(predictor_value, 4) AS atrp_1w,
    RANK() OVER (ORDER BY predictor_value DESC) AS rank
FROM base
ORDER BY predictor_value DESC;
-- @block
-- [CF DD898100 R8] Single warning predictor — W.R|1M oversold (no LIMIT). Use ASC for avoid list.
WITH preferred_market_codes AS (
    SELECT market_code FROM (
        VALUES ('america'),('canada'),('mexico'),('austria'),('belgium'),('cyprus'),('czech'),
        ('denmark'),('estonia'),('finland'),('france'),('germany'),('greece'),('hungary'),
        ('iceland'),('ireland'),('italy'),('latvia'),('lithuania'),('luxembourg'),('netherlands'),
        ('norway'),('poland'),('portugal'),('romania'),('slovakia'),('spain'),('sweden'),
        ('switzerland'),('uk')
    ) AS preferred_markets(market_code)
),
base AS (
    SELECT r.symbol, r.name, r.market,
        TRY_CAST(r.close AS DOUBLE) AS close,
        TRY_CAST(r."W.R|1M" AS DOUBLE) AS predictor_value
    FROM day12.all_fields_rows r
    INNER JOIN preferred_market_codes pm ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = 'tradingview_all_fields_20260612_2008_utc_028e0f86'
      AND TRY_CAST(r.market_cap_basic AS DOUBLE) >= 500000000
      AND TRY_CAST(r.close AS DOUBLE) >= 10
      AND TRY_CAST(r."W.R|1M" AS DOUBLE) IS NOT NULL
)
SELECT symbol, name, market,
    ROUND(close, 2) AS close,
    ROUND(predictor_value, 1) AS wr_1m,
    RANK() OVER (ORDER BY predictor_value ASC) AS rank
FROM base
ORDER BY predictor_value ASC;
-- @block
-- [CF DD898100 R9] TOP stable close_forward predictors — actionable only (export to CSV for charts).
SELECT predictor_field,
    runs_seen,
    ROUND(sign_consistency_ratio, 3) AS sign_consistency,
    ROUND(median_pearson, 4) AS median_pearson,
    ROUND(median_quintile_spread, 2) AS median_q_spread_pp,
    ROUND(rank_stability_score, 3) AS rank_stability_score,
    CASE
        WHEN rank_stability_score >= 0 THEN 'rank_high_in_universe'
        ELSE 'rank_low_in_universe'
    END AS scout_direction,
    CASE
        WHEN median_quintile_spread >= 0.4
            AND sign_consistency_ratio >= 0.65 THEN 'bullish_tilt'
        WHEN median_quintile_spread <= -0.3
            AND sign_consistency_ratio >= 0.65 THEN 'avoid_warning'
        WHEN ABS(median_quintile_spread) >= 0.3 THEN 'moderate_mixed'
        ELSE 'weak_sparse'
    END AS advice_bucket
FROM agg.cross_run_field_stability
WHERE performance_field = 'close_forward_return_pct'
    AND runs_seen >= 10
    AND sign_consistency_ratio >= 0.65
    AND predictor_field NOT ILIKE '%gap%'
    AND predictor_field NOT ILIKE '%change%'
    AND predictor_field NOT ILIKE '%Mom%'
    AND predictor_field NOT ILIKE '%ROC%'
    AND ABS(median_quintile_spread) >= 0.3
ORDER BY ABS(rank_stability_score) DESC NULLS LAST;
-- @block
-- [CF DD898100 R10] Advised active-manager predictors — stability + playbook tags.
WITH advised_meta AS (
    SELECT predictor_field, advice_category, active_mgmt_note
    FROM (
            VALUES ('ATRP|1W', 'volatility_bullish', 'Playbook A primary — rank high'),
                ('ATRP', 'volatility_bullish', 'Playbook A — rank high'),
                ('ADRP|15', 'volatility_bullish', 'Volatility tilt — rank high'),
                ('ADRP|1W', 'volatility_bullish', 'Volatility tilt — rank high'),
                ('ADX-DI|1M', 'adx_pressure_bullish', 'Playbook A optional — rank high'),
                ('ADX-DI_50|1M', 'adx_pressure_bullish', 'Confirmation filter — rank high'),
                ('relative_volume', 'volatility_bullish', 'Playbook A optional — rank high'),
                ('RSI21[1]|1M', 'oversold_warning', 'Playbook C — rank low (avoid oversold long)'),
                ('Stoch.K_14_1_3|1M', 'oversold_warning', 'Playbook C — bottom quintile avoid'),
                ('W.R|1M', 'oversold_warning', 'Playbook C — bottom quintile avoid'),
                ('Recommend.MA|1M', 'crowded_consensus_warning', 'Playbook C — rank low (bullish MA = headwind)'),
                ('oper_income_ttm', 'mega_cap_warning', 'Playbook C — rank low (size headwind)'),
                ('ebitda_ttm', 'mega_cap_warning', 'Playbook C — rank low (size headwind)')
        ) AS t(predictor_field, advice_category, active_mgmt_note)
),
stability AS (
    SELECT predictor_field,
        runs_seen,
        ROUND(sign_consistency_ratio, 3) AS sign_consistency,
        ROUND(median_pearson, 4) AS median_pearson,
        ROUND(median_quintile_spread, 2) AS median_q_spread_pp,
        ROUND(rank_stability_score, 3) AS rank_stability_score
    FROM agg.cross_run_field_stability
    WHERE performance_field = 'close_forward_return_pct'
)
SELECT m.predictor_field,
    m.advice_category,
    m.active_mgmt_note,
    s.runs_seen,
    s.sign_consistency,
    s.median_pearson,
    s.median_q_spread_pp,
    s.rank_stability_score,
    CASE
        WHEN s.rank_stability_score >= 0 THEN 'rank_high_in_universe'
        ELSE 'rank_low_in_universe'
    END AS scout_direction
FROM advised_meta m
    LEFT JOIN stability s USING (predictor_field)
ORDER BY ABS(COALESCE(s.rank_stability_score, 0)) DESC NULLS LAST,
    m.predictor_field;
-- @block
-- [CF DD898100 R11] Pooled quintile forward-return perf — advised predictors (pivot pred_quintile in Excel).
-- Long format: one row per (predictor_field, quintile). Q5 = highest predictor value each scan day.
WITH filtered AS (
    SELECT a.run_id,
        a.symbol,
        TRY_CAST(a.close_forward_return_pct AS DOUBLE) AS fwd_ret,
        TRY_CAST(a."ATRP|1W" AS DOUBLE) AS "ATRP|1W",
        TRY_CAST(a.ATRP AS DOUBLE) AS ATRP,
        TRY_CAST(a."ADRP|15" AS DOUBLE) AS "ADRP|15",
        TRY_CAST(a."ADRP|1W" AS DOUBLE) AS "ADRP|1W",
        TRY_CAST(a."ADX-DI|1M" AS DOUBLE) AS "ADX-DI|1M",
        TRY_CAST(a."ADX-DI_50|1M" AS DOUBLE) AS "ADX-DI_50|1M",
        TRY_CAST(a.relative_volume AS DOUBLE) AS relative_volume,
        TRY_CAST(a."RSI21[1]|1M" AS DOUBLE) AS "RSI21[1]|1M",
        TRY_CAST(a."Stoch.K_14_1_3|1M" AS DOUBLE) AS "Stoch.K_14_1_3|1M",
        TRY_CAST(a."W.R|1M" AS DOUBLE) AS "W.R|1M",
        TRY_CAST(a."Recommend.MA|1M" AS DOUBLE) AS "Recommend.MA|1M",
        TRY_CAST(a.oper_income_ttm AS DOUBLE) AS oper_income_ttm,
        TRY_CAST(a.ebitda_ttm AS DOUBLE) AS ebitda_ttm
    FROM enr.all_fields_rows a
        INNER JOIN cr.cross_run_close_returns cr USING (run_id, symbol)
    WHERE TRY_CAST(a.close_forward_return_pct AS DOUBLE) BETWEEN -25 AND 25
        AND cr.close_price >= 10
),
long_vals AS (
    SELECT run_id, symbol, 'ATRP|1W' AS predictor_field, "ATRP|1W" AS pred_value, fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'ATRP', ATRP, fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'ADRP|15', "ADRP|15", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'ADRP|1W', "ADRP|1W", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'ADX-DI|1M', "ADX-DI|1M", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'ADX-DI_50|1M', "ADX-DI_50|1M", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'relative_volume', relative_volume, fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'RSI21[1]|1M', "RSI21[1]|1M", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'Stoch.K_14_1_3|1M', "Stoch.K_14_1_3|1M", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'W.R|1M', "W.R|1M", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'Recommend.MA|1M', "Recommend.MA|1M", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'oper_income_ttm', oper_income_ttm, fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'ebitda_ttm', ebitda_ttm, fwd_ret FROM filtered
),
quintiled AS (
    SELECT run_id,
        symbol,
        predictor_field,
        pred_value,
        fwd_ret,
        NTILE(5) OVER (
            PARTITION BY run_id, predictor_field
            ORDER BY pred_value
        ) AS pred_quintile
    FROM long_vals
    WHERE pred_value IS NOT NULL
        AND isfinite(pred_value)
        AND fwd_ret IS NOT NULL
)
SELECT predictor_field,
    pred_quintile,
    COUNT(*) AS n_symbol_days,
    COUNT(DISTINCT run_id) AS n_runs,
    ROUND(AVG(fwd_ret), 3) AS avg_fwd_pct,
    ROUND(MEDIAN(fwd_ret), 3) AS median_fwd_pct,
    ROUND(STDDEV(fwd_ret), 3) AS stdev_fwd_pct,
    ROUND(AVG(CASE WHEN fwd_ret > 0 THEN 1.0 ELSE 0 END), 3) AS win_rate
FROM quintiled
GROUP BY predictor_field, pred_quintile
ORDER BY predictor_field, pred_quintile;
-- @block
-- [CF DD898100 R12] Quintile spread summary — advised predictors vs aggregate stability (self-check CSV).
WITH filtered AS (
    SELECT a.run_id,
        a.symbol,
        TRY_CAST(a.close_forward_return_pct AS DOUBLE) AS fwd_ret,
        TRY_CAST(a."ATRP|1W" AS DOUBLE) AS "ATRP|1W",
        TRY_CAST(a.ATRP AS DOUBLE) AS ATRP,
        TRY_CAST(a."ADRP|15" AS DOUBLE) AS "ADRP|15",
        TRY_CAST(a."ADRP|1W" AS DOUBLE) AS "ADRP|1W",
        TRY_CAST(a."ADX-DI|1M" AS DOUBLE) AS "ADX-DI|1M",
        TRY_CAST(a."ADX-DI_50|1M" AS DOUBLE) AS "ADX-DI_50|1M",
        TRY_CAST(a.relative_volume AS DOUBLE) AS relative_volume,
        TRY_CAST(a."RSI21[1]|1M" AS DOUBLE) AS "RSI21[1]|1M",
        TRY_CAST(a."Stoch.K_14_1_3|1M" AS DOUBLE) AS "Stoch.K_14_1_3|1M",
        TRY_CAST(a."W.R|1M" AS DOUBLE) AS "W.R|1M",
        TRY_CAST(a."Recommend.MA|1M" AS DOUBLE) AS "Recommend.MA|1M",
        TRY_CAST(a.oper_income_ttm AS DOUBLE) AS oper_income_ttm,
        TRY_CAST(a.ebitda_ttm AS DOUBLE) AS ebitda_ttm
    FROM enr.all_fields_rows a
        INNER JOIN cr.cross_run_close_returns cr USING (run_id, symbol)
    WHERE TRY_CAST(a.close_forward_return_pct AS DOUBLE) BETWEEN -25 AND 25
        AND cr.close_price >= 10
),
long_vals AS (
    SELECT run_id, symbol, 'ATRP|1W' AS predictor_field, "ATRP|1W" AS pred_value, fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'ATRP', ATRP, fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'ADRP|15', "ADRP|15", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'ADRP|1W', "ADRP|1W", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'ADX-DI|1M', "ADX-DI|1M", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'ADX-DI_50|1M', "ADX-DI_50|1M", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'relative_volume', relative_volume, fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'RSI21[1]|1M', "RSI21[1]|1M", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'Stoch.K_14_1_3|1M', "Stoch.K_14_1_3|1M", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'W.R|1M', "W.R|1M", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'Recommend.MA|1M', "Recommend.MA|1M", fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'oper_income_ttm', oper_income_ttm, fwd_ret FROM filtered
    UNION ALL SELECT run_id, symbol, 'ebitda_ttm', ebitda_ttm, fwd_ret FROM filtered
),
quintiled AS (
    SELECT run_id,
        predictor_field,
        fwd_ret,
        NTILE(5) OVER (
            PARTITION BY run_id, predictor_field
            ORDER BY pred_value
        ) AS pred_quintile
    FROM long_vals
    WHERE pred_value IS NOT NULL
        AND isfinite(pred_value)
        AND fwd_ret IS NOT NULL
),
pooled AS (
    SELECT predictor_field,
        pred_quintile,
        AVG(fwd_ret) AS avg_fwd_pct
    FROM quintiled
    GROUP BY predictor_field, pred_quintile
),
spreads AS (
    SELECT predictor_field,
        MAX(CASE WHEN pred_quintile = 5 THEN avg_fwd_pct END) AS q5_avg_fwd_pct,
        MAX(CASE WHEN pred_quintile = 1 THEN avg_fwd_pct END) AS q1_avg_fwd_pct,
        MAX(CASE WHEN pred_quintile = 5 THEN avg_fwd_pct END)
            - MAX(CASE WHEN pred_quintile = 1 THEN avg_fwd_pct END) AS q5_minus_q1_spread_pp
    FROM pooled
    GROUP BY predictor_field
)
SELECT s.predictor_field,
    ROUND(s.q1_avg_fwd_pct, 3) AS q1_avg_fwd_pct,
    ROUND(s.q5_avg_fwd_pct, 3) AS q5_avg_fwd_pct,
    ROUND(s.q5_minus_q1_spread_pp, 3) AS q5_minus_q1_spread_pp,
    st.runs_seen,
    ROUND(st.sign_consistency_ratio, 3) AS sign_consistency,
    ROUND(st.median_quintile_spread, 2) AS aggregate_median_q_spread_pp,
    ROUND(st.median_pearson, 4) AS aggregate_median_pearson,
    CASE
        WHEN st.rank_stability_score >= 0 THEN 'rank_high_in_universe'
        ELSE 'rank_low_in_universe'
    END AS scout_direction
FROM spreads s
    LEFT JOIN agg.cross_run_field_stability st
        ON st.predictor_field = s.predictor_field
        AND st.performance_field = 'close_forward_return_pct'
ORDER BY ABS(COALESCE(st.rank_stability_score, s.q5_minus_q1_spread_pp)) DESC NULLS LAST,
    s.predictor_field;
