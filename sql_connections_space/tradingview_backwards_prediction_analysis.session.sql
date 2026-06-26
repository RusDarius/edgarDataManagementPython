-- DuckDB query session for TradingView backwards prediction analysis
-- Connection target: backwards_prediction_analysis.duckdb inside one analysis run folder:
--   logs/tradingview_analysis/prediction_analysis/duckdb_runs/backwards_prediction_analysis/runs/
--   backwards_prediction_analysis_YYYYMMDD_HHMM_utc_<8hex>/backwards_prediction_analysis.duckdb
-- Example run folder: backwards_prediction_analysis_20260623_1405_utc_2e43c07f
-- Produced by run_backwards_prediction_analysis() in src/main.py (see documentation/backwards_prediction_analysis_guide.md).
-- Run one @block at a time in DBCode/SQLTools.
-- Disconnect this DuckDB connection before rerunning the Python writer; DuckDB locks the file per process on Windows.
-- ============================================================
-- 1. DATABASE INVENTORY
-- ============================================================
-- @block q_backwards_list_tables
SELECT table_type,
    table_name
FROM information_schema.tables
WHERE table_schema = 'main'
ORDER BY CASE
        WHEN table_type = 'BASE TABLE' THEN 0
        ELSE 1
    END,
    table_name;
-- @block q_backwards_list_columns
SELECT c.table_name,
    t.table_type,
    c.ordinal_position AS column_position,
    c.column_name,
    c.data_type,
    c.is_nullable
FROM information_schema.columns c
    JOIN information_schema.tables t ON c.table_schema = t.table_schema
    AND c.table_name = t.table_name
WHERE c.table_schema = 'main'
ORDER BY c.table_name,
    c.ordinal_position;
-- @block q_backwards_row_counts
SELECT 'backwards_analysis_runs' AS table_name,
    COUNT(*) AS row_count
FROM backwards_analysis_runs
UNION ALL
SELECT 'backwards_analysis_anchors',
    COUNT(*)
FROM backwards_analysis_anchors
UNION ALL
SELECT 'backwards_profile_horizon_deltas',
    COUNT(*)
FROM backwards_profile_horizon_deltas
UNION ALL
SELECT 'backwards_consensus_horizon_deltas',
    COUNT(*)
FROM backwards_consensus_horizon_deltas
UNION ALL
SELECT 'backwards_profile_component_deltas',
    COUNT(*)
FROM backwards_profile_component_deltas
UNION ALL
SELECT 'backwards_anchor_snapshots',
    COUNT(*)
FROM backwards_anchor_snapshots
ORDER BY table_name;
-- ============================================================
-- 2. RUN METADATA & ANCHORS
-- ============================================================
-- @block q_backwards_analysis_metadata
SELECT backwards_analysis_id,
    created_at_utc,
    current_run_id,
    current_created_at_utc,
    current_snapshot_label,
    current_database_path,
    anchor_count,
    notes
FROM backwards_analysis_runs
ORDER BY created_at_utc DESC;
-- @block q_backwards_resolved_anchors
SELECT anchors.anchor_name,
    anchors.anchor_run_id,
    anchors.anchor_created_at_utc,
    anchors.anchor_snapshot_label,
    anchors.source_database_path,
    anchors.resolution_method,
    anchors.offset_days_from_current,
    anchors.runs_back_from_current
FROM backwards_analysis_anchors AS anchors
ORDER BY CASE anchors.anchor_name
        WHEN 'yesterday' THEN 1
        WHEN 'last_week' THEN 2
        WHEN 'last_month' THEN 3
        WHEN 'oldest' THEN 4
        ELSE 99
    END,
    anchors.anchor_name;
-- @block q_backwards_input_runs
SELECT backwards_analysis_id,
    current_run_id,
    current_created_at_utc,
    current_snapshot_label,
    anchor_name,
    anchor_run_id,
    anchor_created_at_utc,
    anchor_snapshot_label,
    resolution_method,
    offset_days_from_current,
    runs_back_from_current
FROM vw_backwards_input_runs
ORDER BY CASE anchor_name
        WHEN 'yesterday' THEN 1
        WHEN 'last_week' THEN 2
        WHEN 'last_month' THEN 3
        WHEN 'oldest' THEN 4
        ELSE 99
    END,
    anchor_name;
-- ============================================================
-- 3. COHORT OVERVIEW (alignment, drift, movers)
-- ============================================================
-- @block q_backwards_profile_price_alignment
-- Score delta vs realized close return correlation by anchor/profile/horizon.
SELECT anchor_name,
    profile_name,
    horizon_name,
    symbol_count,
    ROUND(avg_score_delta, 4) AS avg_score_delta,
    ROUND(avg_close_delta_pct, 4) AS avg_close_delta_pct,
    ROUND(score_price_corr, 4) AS score_price_corr,
    aligned_direction_count,
    false_positive_count,
    ROUND(
        aligned_direction_count * 1.0 / NULLIF(symbol_count, 0),
        4
    ) AS aligned_ratio
FROM vw_backwards_price_score_alignment
ORDER BY CASE anchor_name
        WHEN 'yesterday' THEN 1
        WHEN 'last_week' THEN 2
        WHEN 'last_month' THEN 3
        WHEN 'oldest' THEN 4
        ELSE 99
    END,
  CASE horizon_name
        WHEN 'days' THEN 1
        WHEN 'weeks' THEN 2
        WHEN 'months' THEN 3
        WHEN 'years' THEN 4
        ELSE 99
    END,
    score_price_corr DESC NULLS LAST,
    profile_name;
-- @block q_backwards_universe_drift
-- Symbols added, dropped, or retained vs each anchor.
SELECT anchor_name,
    profile_name,
    horizon_name,
    symbols_added,
    symbols_dropped,
    symbols_retained,
    ROUND(
        symbols_retained * 1.0 / NULLIF(
            symbols_added + symbols_dropped + symbols_retained,
            0
        ),
        4
    ) AS retention_ratio
FROM vw_backwards_universe_drift
ORDER BY CASE anchor_name
        WHEN 'yesterday' THEN 1
        WHEN 'last_week' THEN 2
        WHEN 'last_month' THEN 3
        WHEN 'oldest' THEN 4
        ELSE 99
    END,
    profile_name,
    CASE horizon_name
        WHEN 'days' THEN 1
        WHEN 'weeks' THEN 2
        WHEN 'months' THEN 3
        WHEN 'years' THEN 4
        ELSE 99
    END;
-- @block q_backwards_top_score_movers
-- Largest absolute score_delta per anchor/profile/horizon (pre-ranked view).
-- Edit profile_name, horizon_name, anchor_name, and mover_rank ceiling as needed.
SELECT symbol,
    company,
    sector,
    industry,
    anchor_name,
    profile_name,
    horizon_name,
    mover_rank,
    ROUND(score_delta, 4) AS score_delta,
    ROUND(close_delta_pct, 4) AS close_delta_pct,
    ROUND(current_score, 4) AS current_score,
    ROUND(anchor_score, 4) AS anchor_score,
    current_direction,
    anchor_direction,
    direction_changed,
    rank_delta
FROM vw_backwards_top_score_movers
WHERE anchor_name = 'last_week'
    AND horizon_name = 'weeks'
    AND profile_name = 'breakout_long_v1'
    AND mover_rank <= 40
ORDER BY mover_rank;
-- ============================================================
-- 4. WATCHLIST / TICKER DRILL-DOWN
-- Edit tickers[], profile_name, and horizon_name before running.
-- ============================================================
-- @block q_backwards_watchlist_profile_deltas
WITH tickers AS (
    SELECT unnest(
            [
                'NVDA',
                'AAPL',
                'GOOG'
            ]
        ) AS ticker
), filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'weeks' AS horizon_name
)
SELECT d.anchor_name,
    d.symbol,
    d.company,
    d.sector,
    d.industry,
    d.profile_name,
    d.horizon_name,
    ROUND(d.current_score, 4) AS current_score,
    ROUND(d.anchor_score, 4) AS anchor_score,
    ROUND(d.score_delta, 4) AS score_delta,
    d.current_direction,
    d.anchor_direction,
    d.direction_changed,
    d.current_rank,
    d.anchor_rank,
    d.rank_delta,
    ROUND(d.current_close, 4) AS current_close,
    ROUND(d.anchor_close, 4) AS anchor_close,
    ROUND(d.close_delta_pct, 4) AS close_delta_pct,
    ROUND(d.current_perf_w, 4) AS current_perf_w,
    ROUND(d.anchor_perf_w, 4) AS anchor_perf_w,
    ROUND(d.perf_w_delta, 4) AS perf_w_delta,
    ROUND(d.current_perf_1m, 4) AS current_perf_1m,
    ROUND(d.anchor_perf_1m, 4) AS anchor_perf_1m,
    ROUND(d.perf_1m_delta, 4) AS perf_1m_delta
FROM backwards_profile_horizon_deltas AS d
    CROSS JOIN filters AS f
    INNER JOIN tickers AS t ON upper(trim(d.symbol)) = upper(trim(t.ticker))
    OR upper(trim(d.symbol)) LIKE '%:' || upper(trim(t.ticker))
WHERE d.profile_name = f.profile_name
    AND d.horizon_name = f.horizon_name
    AND d.in_current
    AND d.in_anchor
ORDER BY d.symbol,
    CASE d.anchor_name
        WHEN 'yesterday' THEN 1
        WHEN 'last_week' THEN 2
        WHEN 'last_month' THEN 3
        WHEN 'oldest' THEN 4
        ELSE 99
    END;
-- @block q_backwards_watchlist_score_progression
-- Long-format score/price path: current snapshot plus each anchor.
WITH tickers AS (
    SELECT unnest(
            [
                'NVDA',
                'AAPL',
                'GOOG'
            ]
        ) AS ticker
), filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'weeks' AS horizon_name
)
SELECT s.symbol,
    s.company,
    CASE
        WHEN s.is_current THEN 'current'
        ELSE s.anchor_name
    END AS snapshot_label,
    s.is_current,
    s.snapshot_label AS source_snapshot_label,
  ROUND(s.score, 4) AS score,
    s.direction,
    ROUND(s.confidence, 4) AS confidence,
    ROUND(s.risk_adjusted_score, 4) AS risk_adjusted_score,
    s.risk_tier,
    s.profile_rank,
    ROUND(s.close, 4) AS close,
    ROUND(s.perf_5d, 4) AS perf_5d,
    ROUND(s.perf_w, 4) AS perf_w,
    ROUND(s.perf_1m, 4) AS perf_1m,
    ROUND(s.perf_ytd, 4) AS perf_ytd
FROM backwards_anchor_snapshots AS s
    CROSS JOIN filters AS f
    INNER JOIN tickers AS t ON upper(trim(s.symbol)) = upper(trim(t.ticker))
    OR upper(trim(s.symbol)) LIKE '%:' || upper(trim(t.ticker))
WHERE s.profile_name = f.profile_name
    AND s.horizon_name = f.horizon_name
ORDER BY s.symbol,
    s.is_current DESC,
    CASE s.anchor_name
        WHEN 'yesterday' THEN 1
        WHEN 'last_week' THEN 2
        WHEN 'last_month' THEN 3
        WHEN 'oldest' THEN 4
        ELSE 99
    END;
-- @block q_backwards_watchlist_all_profiles_current
-- All profile opinions for watchlist tickers at the current snapshot.
WITH tickers AS (
    SELECT unnest(
            [
                'NVDA',
                'AAPL',
                'GOOG'
            ]
        ) AS ticker
), filters AS (
    SELECT 'weeks' AS horizon_name
)
SELECT s.symbol,
    s.company,
    s.profile_name,
    s.profile_family,
    ROUND(s.score, 4) AS score,
    s.direction,
    ROUND(s.confidence, 4) AS confidence,
    ROUND(s.risk_adjusted_score, 4) AS risk_adjusted_score,
    s.risk_tier,
    s.profile_rank,
    ROUND(s.close, 4) AS close
FROM backwards_anchor_snapshots AS s
    CROSS JOIN filters AS f
    INNER JOIN tickers AS t ON upper(trim(s.symbol)) = upper(trim(t.ticker))
    OR upper(trim(s.symbol)) LIKE '%:' || upper(trim(t.ticker))
WHERE s.is_current
    AND s.horizon_name = f.horizon_name
ORDER BY s.symbol,
    s.risk_adjusted_score DESC NULLS LAST,
    s.profile_name;
-- @block q_backwards_watchlist_component_deltas
-- Component pillar shifts for watchlist tickers vs one anchor.
WITH tickers AS (
    SELECT unnest(
            [
                'NVDA',
                'AAPL',
                'GOOG'
            ]
        ) AS ticker
), filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'last_week' AS anchor_name
)
SELECT c.symbol,
    c.company,
    c.anchor_name,
    ROUND(c.close_delta_pct, 4) AS close_delta_pct,
    ROUND(c.momentum_delta, 4) AS momentum_delta,
    ROUND(c.trend_delta, 4) AS trend_delta,
    ROUND(c.attention_delta, 4) AS attention_delta,
    ROUND(c.event_delta, 4) AS event_delta,
    ROUND(c.quality_delta, 4) AS quality_delta,
    ROUND(c.valuation_delta, 4) AS valuation_delta,
    ROUND(c.safety_delta, 4) AS safety_delta,
    ROUND(c.scale_delta, 4) AS scale_delta,
    ROUND(c.current_momentum, 4) AS current_momentum,
    ROUND(c.anchor_momentum, 4) AS anchor_momentum
FROM backwards_profile_component_deltas AS c
    CROSS JOIN filters AS f
    INNER JOIN tickers AS t ON upper(trim(c.symbol)) = upper(trim(t.ticker))
    OR upper(trim(c.symbol)) LIKE '%:' || upper(trim(t.ticker))
WHERE c.profile_name = f.profile_name
    AND c.anchor_name = f.anchor_name
    AND c.in_current
    AND c.in_anchor
ORDER BY c.symbol;
-- ============================================================
-- 5. PROGRESSION & LEADERBOARDS (single profile / horizon)
-- Edit profile_name and horizon_name in filters CTE.
-- ============================================================
-- @block q_backwards_top_progressors
WITH filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'weeks' AS horizon_name,
        'last_week' AS anchor_name
), deduped AS (
    SELECT * EXCLUDE (row_num)
    FROM (
            SELECT d.*,
                ROW_NUMBER() OVER (
                    PARTITION BY d.anchor_name,
                    d.symbol
                    ORDER BY ABS(d.score_delta) DESC NULLS LAST,
                        d.symbol ASC
                ) AS row_num
            FROM backwards_profile_horizon_deltas AS d
                CROSS JOIN filters AS f
            WHERE d.profile_name = f.profile_name
                AND d.horizon_name = f.horizon_name
                AND d.anchor_name = f.anchor_name
        )
    WHERE row_num = 1
)
SELECT symbol,
    company,
    sector,
    industry,
    ROUND(score_delta, 4) AS score_delta,
    rank_delta,
    ROUND(close_delta_pct, 4) AS close_delta_pct,
    ROUND(current_score, 4) AS current_score,
    ROUND(anchor_score, 4) AS anchor_score,
    current_direction,
    anchor_direction,
    ROUND(current_close, 4) AS current_close,
    ROUND(anchor_close, 4) AS anchor_close
FROM deduped
WHERE in_current
    AND in_anchor
    AND score_delta IS NOT NULL
    AND score_delta > 0
ORDER BY score_delta DESC,
    rank_delta DESC NULLS LAST,
    symbol ASC
LIMIT 50;
-- @block q_backwards_significant_movers
WITH filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'weeks' AS horizon_name,
        'last_week' AS anchor_name,
        0.35 AS min_abs_score_delta
), deduped AS (
    SELECT * EXCLUDE (row_num)
    FROM (
            SELECT d.*,
                ROW_NUMBER() OVER (
                    PARTITION BY d.anchor_name,
                    d.symbol
                    ORDER BY ABS(d.score_delta) DESC NULLS LAST,
                        d.symbol ASC
                ) AS row_num
            FROM backwards_profile_horizon_deltas AS d
                CROSS JOIN filters AS f
            WHERE d.profile_name = f.profile_name
                AND d.horizon_name = f.horizon_name
                AND d.anchor_name = f.anchor_name
        )
    WHERE row_num = 1
)
SELECT symbol,
    company,
    ROUND(score_delta, 4) AS score_delta,
    rank_delta,
    ROUND(close_delta_pct, 4) AS close_delta_pct,
    ROUND(current_score, 4) AS current_score,
    ROUND(anchor_score, 4) AS anchor_score,
    CASE
        WHEN score_delta > 0 THEN 'up'
        WHEN score_delta < 0 THEN 'down'
        ELSE 'flat'
    END AS move_direction
FROM deduped
WHERE in_current
    AND in_anchor
    AND score_delta IS NOT NULL
    AND ABS(score_delta) >= (
        SELECT min_abs_score_delta
        FROM filters
    )
ORDER BY ABS(score_delta) DESC,
    symbol ASC
LIMIT 80;
-- @block q_backwards_price_aligned_improvers
WITH filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'weeks' AS horizon_name,
        'last_week' AS anchor_name
), deduped AS (
    SELECT * EXCLUDE (row_num)
    FROM (
            SELECT d.*,
                ROW_NUMBER() OVER (
                    PARTITION BY d.anchor_name,
                    d.symbol
                    ORDER BY ABS(d.score_delta) DESC NULLS LAST,
                        d.symbol ASC
                ) AS row_num
            FROM backwards_profile_horizon_deltas AS d
                CROSS JOIN filters AS f
            WHERE d.profile_name = f.profile_name
                AND d.horizon_name = f.horizon_name
                AND d.anchor_name = f.anchor_name
        )
    WHERE row_num = 1
)
SELECT symbol,
    company,
    ROUND(score_delta, 4) AS score_delta,
    ROUND(close_delta_pct, 4) AS close_delta_pct,
    ROUND(current_score, 4) AS current_score,
    ROUND(anchor_score, 4) AS anchor_score,
    ROUND(current_close, 4) AS current_close,
    ROUND(anchor_close, 4) AS anchor_close
FROM deduped
WHERE in_current
    AND in_anchor
    AND score_delta > 0
    AND close_delta_pct > 0
ORDER BY score_delta DESC,
    close_delta_pct DESC,
    symbol ASC
LIMIT 50;
-- @block q_backwards_price_divergent_improvers
-- Score improved but price fell between anchor and current.
WITH filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'weeks' AS horizon_name,
        'last_week' AS anchor_name
), deduped AS (
    SELECT * EXCLUDE (row_num)
    FROM (
            SELECT d.*,
                ROW_NUMBER() OVER (
                    PARTITION BY d.anchor_name,
                    d.symbol
                    ORDER BY ABS(d.score_delta) DESC NULLS LAST,
                        d.symbol ASC
                ) AS row_num
            FROM backwards_profile_horizon_deltas AS d
                CROSS JOIN filters AS f
            WHERE d.profile_name = f.profile_name
                AND d.horizon_name = f.horizon_name
                AND d.anchor_name = f.anchor_name
        )
    WHERE row_num = 1
)
SELECT symbol,
    company,
    ROUND(score_delta, 4) AS score_delta,
    ROUND(close_delta_pct, 4) AS close_delta_pct,
    ROUND(current_score, 4) AS current_score,
    ROUND(anchor_score, 4) AS anchor_score,
    current_direction,
    anchor_direction
FROM deduped
WHERE in_current
    AND in_anchor
    AND score_delta > 0
    AND close_delta_pct < 0
ORDER BY score_delta DESC,
    close_delta_pct ASC,
    symbol ASC
LIMIT 50;
-- @block q_backwards_persistent_risers
-- Symbols with positive score_delta on 2+ anchors for one profile/horizon.
WITH filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'weeks' AS horizon_name,
        2 AS min_positive_anchors
), deduped AS (
    SELECT * EXCLUDE (row_num)
    FROM (
            SELECT d.*,
                ROW_NUMBER() OVER (
                    PARTITION BY d.anchor_name,
                    d.symbol
                    ORDER BY ABS(d.score_delta) DESC NULLS LAST,
                        d.symbol ASC
                ) AS row_num
            FROM backwards_profile_horizon_deltas AS d
                CROSS JOIN filters AS f
            WHERE d.profile_name = f.profile_name
                AND d.horizon_name = f.horizon_name
        )
    WHERE row_num = 1
), hits AS (
    SELECT symbol,
        company,
        anchor_name,
        score_delta,
        close_delta_pct
    FROM deduped
    WHERE in_current
        AND in_anchor
        AND score_delta > 0
)
SELECT symbol,
    MAX(company) AS company,
    COUNT(*) AS positive_anchor_hits,
    ROUND(MIN(score_delta), 4) AS min_positive_delta,
    ROUND(AVG(score_delta), 4) AS avg_score_delta,
    ROUND(AVG(close_delta_pct), 4) AS avg_price_delta_pct,
    STRING_AGG(anchor_name, '+' ORDER BY anchor_name) AS anchor_hits
FROM hits
GROUP BY symbol
HAVING COUNT(*) >= (
        SELECT min_positive_anchors
        FROM filters
    )
ORDER BY avg_score_delta DESC,
    positive_anchor_hits DESC,
    symbol ASC
LIMIT 60;
-- @block q_backwards_direction_changes
-- Symbols whose model direction flipped between anchor and current.
WITH filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'weeks' AS horizon_name,
        'last_week' AS anchor_name
)
SELECT symbol,
    company,
    sector,
    industry,
    anchor_direction,
    current_direction,
    ROUND(score_delta, 4) AS score_delta,
    ROUND(close_delta_pct, 4) AS close_delta_pct,
    ROUND(current_score, 4) AS current_score,
    ROUND(anchor_score, 4) AS anchor_score,
    rank_delta
FROM backwards_profile_horizon_deltas AS d
    CROSS JOIN filters AS f
WHERE d.profile_name = f.profile_name
    AND d.horizon_name = f.horizon_name
    AND d.anchor_name = f.anchor_name
    AND d.direction_changed
    AND d.in_current
    AND d.in_anchor
ORDER BY ABS(score_delta) DESC NULLS LAST,
    symbol ASC
LIMIT 80;
-- @block q_backwards_ticker_rank_progression
-- Rank/score progression for ONE ticker across all anchors (one profile + horizon).
-- Edit ticker, profile_name, and horizon_name before running.
-- Negative rank_delta = moved up in the universe ranking.
WITH filters AS (
    SELECT 'VSH' AS ticker,
        'breakout_long_v1' AS profile_name,
        'weeks' AS horizon_name
)
SELECT d.anchor_name,
    d.symbol,
    d.company,
    d.profile_name,
    d.horizon_name,
    d.anchor_rank,
    d.current_rank,
    d.rank_delta,
    ROUND(d.anchor_score, 4) AS anchor_score,
    ROUND(d.current_score, 4) AS current_score,
    ROUND(d.score_delta, 4) AS score_delta,
    d.anchor_direction,
    d.current_direction,
    d.direction_changed,
    ROUND(d.anchor_close, 4) AS anchor_close,
    ROUND(d.current_close, 4) AS current_close,
    ROUND(d.close_delta_pct, 4) AS close_delta_pct,
    ROUND(d.anchor_perf_w, 4) AS anchor_perf_w,
    ROUND(d.current_perf_w, 4) AS current_perf_w,
    ROUND(d.perf_w_delta, 4) AS perf_w_delta
FROM backwards_profile_horizon_deltas AS d
    CROSS JOIN filters AS f
WHERE d.profile_name = f.profile_name
    AND d.horizon_name = f.horizon_name
    AND d.in_current
    AND d.in_anchor
    AND (
        upper(trim(d.symbol)) = upper(trim(f.ticker))
        OR upper(trim(d.symbol)) LIKE '%:' || upper(trim(f.ticker))
    )
ORDER BY CASE d.anchor_name
        WHEN 'yesterday' THEN 1
        WHEN 'last_week' THEN 2
        WHEN 'last_month' THEN 3
        WHEN 'oldest' THEN 4
        ELSE 99
    END;
-- @block q_backwards_rank_movers
-- Largest rank improvements (negative rank_delta = moved up in ranking).
WITH filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'weeks' AS horizon_name,
        'last_week' AS anchor_name
)
SELECT symbol,
    company,
    rank_delta,
    anchor_rank,
    current_rank,
    ROUND(score_delta, 4) AS score_delta,
    ROUND(close_delta_pct, 4) AS close_delta_pct,
    current_direction
FROM backwards_profile_horizon_deltas AS d
    CROSS JOIN filters AS f
WHERE d.profile_name = f.profile_name
    AND d.horizon_name = f.horizon_name
    AND d.anchor_name = f.anchor_name
    AND d.in_current
    AND d.in_anchor
    AND d.rank_delta IS NOT NULL
ORDER BY rank_delta ASC,
    score_delta DESC NULLS LAST
LIMIT 60;
-- ============================================================
-- 6. CONSENSUS DELTAS
-- ============================================================
-- @block q_backwards_consensus_alignment
SELECT anchor_name,
    horizon_name,
    COUNT(*) AS symbol_count,
    ROUND(AVG(score_delta), 4) AS avg_score_delta,
    ROUND(AVG(close_delta_pct), 4) AS avg_close_delta_pct,
    ROUND(CORR(score_delta, close_delta_pct), 4) AS score_price_corr,
    SUM(
        CASE
            WHEN score_delta > 0
            AND close_delta_pct > 0 THEN 1
            WHEN score_delta < 0
            AND close_delta_pct < 0 THEN 1
            ELSE 0
        END
    ) AS aligned_direction_count,
    SUM(
        CASE
            WHEN score_delta > 0
            AND close_delta_pct < 0 THEN 1
            ELSE 0
        END
    ) AS false_positive_count
FROM backwards_consensus_horizon_deltas
WHERE in_current
    AND in_anchor
GROUP BY anchor_name,
    horizon_name
ORDER BY CASE anchor_name
        WHEN 'yesterday' THEN 1
        WHEN 'last_week' THEN 2
        WHEN 'last_month' THEN 3
        WHEN 'oldest' THEN 4
        ELSE 99
    END,
    CASE horizon_name
        WHEN 'days' THEN 1
        WHEN 'weeks' THEN 2
        WHEN 'months' THEN 3
        WHEN 'years' THEN 4
        ELSE 99
    END;
-- @block q_backwards_consensus_top_progressors
WITH filters AS (
    SELECT 'weeks' AS horizon_name,
        'last_week' AS anchor_name
)
SELECT symbol,
    company,
    sector,
    industry,
    ROUND(current_score, 4) AS current_score,
    ROUND(anchor_score, 4) AS anchor_score,
    ROUND(score_delta, 4) AS score_delta,
    current_direction,
    anchor_direction,
    ROUND(current_risk_adjusted_score, 4) AS current_risk_adjusted_score,
    ROUND(anchor_risk_adjusted_score, 4) AS anchor_risk_adjusted_score,
    rank_delta,
    ROUND(close_delta_pct, 4) AS close_delta_pct
FROM backwards_consensus_horizon_deltas AS c
    CROSS JOIN filters AS f
WHERE c.horizon_name = f.horizon_name
    AND c.anchor_name = f.anchor_name
    AND c.in_current
    AND c.in_anchor
    AND c.score_delta IS NOT NULL
    AND c.score_delta > 0
ORDER BY c.score_delta DESC,
    c.rank_delta DESC NULLS LAST,
    c.symbol ASC
LIMIT 60;
-- @block q_backwards_watchlist_consensus_deltas
WITH tickers AS (
    SELECT unnest(
            [
                'NVDA',
                'AAPL',
                'GOOG'
            ]
        ) AS ticker
), filters AS (
    SELECT 'weeks' AS horizon_name
)
SELECT c.anchor_name,
    c.symbol,
    c.company,
    ROUND(c.current_score, 4) AS current_score,
    ROUND(c.anchor_score, 4) AS anchor_score,
    ROUND(c.score_delta, 4) AS score_delta,
    c.current_direction,
    c.anchor_direction,
    ROUND(c.close_delta_pct, 4) AS close_delta_pct,
    c.rank_delta
FROM backwards_consensus_horizon_deltas AS c
    CROSS JOIN filters AS f
    INNER JOIN tickers AS t ON upper(trim(c.symbol)) = upper(trim(t.ticker))
    OR upper(trim(c.symbol)) LIKE '%:' || upper(trim(t.ticker))
WHERE c.horizon_name = f.horizon_name
    AND c.in_current
    AND c.in_anchor
ORDER BY c.symbol,
    CASE c.anchor_name
        WHEN 'yesterday' THEN 1
        WHEN 'last_week' THEN 2
        WHEN 'last_month' THEN 3
        WHEN 'oldest' THEN 4
        ELSE 99
    END;
-- ============================================================
-- 7. CROSS-PROFILE / INDUSTRY ROLLUPS
-- ============================================================
-- @block q_backwards_industry_score_delta_rollup
WITH filters AS (
    SELECT 'weeks' AS horizon_name,
        'last_week' AS anchor_name
)
SELECT d.industry,
    d.sector,
    d.profile_name,
    COUNT(DISTINCT d.symbol) AS symbols,
    ROUND(MEDIAN(d.score_delta), 4) AS median_score_delta,
    ROUND(AVG(d.score_delta), 4) AS avg_score_delta,
    ROUND(MEDIAN(d.close_delta_pct), 4) AS median_close_delta_pct,
    ROUND(AVG(d.close_delta_pct), 4) AS avg_close_delta_pct,
    SUM(
        CASE
            WHEN d.score_delta > 0 THEN 1
            ELSE 0
        END
    ) AS improvers,
    SUM(
        CASE
            WHEN d.score_delta < 0 THEN 1
            ELSE 0
        END
    ) AS decliners
FROM backwards_profile_horizon_deltas AS d
    CROSS JOIN filters AS f
WHERE d.horizon_name = f.horizon_name
    AND d.anchor_name = f.anchor_name
    AND d.in_current
    AND d.in_anchor
    AND COALESCE(TRIM(d.industry), '') <> ''
GROUP BY d.industry,
    d.sector,
    d.profile_name
HAVING COUNT(DISTINCT d.symbol) >= 5
ORDER BY median_score_delta DESC NULLS LAST,
    d.industry,
    d.profile_name
LIMIT 120;
-- @block q_backwards_profile_avg_move_by_anchor
-- Which profiles moved scores the most on average vs each anchor?
SELECT anchor_name,
    profile_name,
    horizon_name,
    COUNT(*) AS comparable_symbols,
    ROUND(AVG(score_delta), 4) AS avg_score_delta,
    ROUND(AVG(close_delta_pct), 4) AS avg_close_delta_pct,
    ROUND(CORR(score_delta, close_delta_pct), 4) AS score_price_corr,
    ROUND(AVG(ABS(score_delta)), 4) AS avg_abs_score_delta
FROM backwards_profile_horizon_deltas
WHERE in_current
    AND in_anchor
    AND score_delta IS NOT NULL
GROUP BY anchor_name,
    profile_name,
    horizon_name
ORDER BY CASE anchor_name
        WHEN 'yesterday' THEN 1
        WHEN 'last_week' THEN 2
        WHEN 'last_month' THEN 3
        WHEN 'oldest' THEN 4
        ELSE 99
    END,
    avg_abs_score_delta DESC NULLS LAST,
    profile_name,
    CASE horizon_name
        WHEN 'days' THEN 1
        WHEN 'weeks' THEN 2
        WHEN 'months' THEN 3
        WHEN 'years' THEN 4
        ELSE 99
    END;
-- @block q_backwards_component_shift_leaders
-- Largest average component pillar shifts for one profile vs an anchor.
WITH filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'last_week' AS anchor_name
)
SELECT COUNT(*) AS symbols,
    ROUND(AVG(momentum_delta), 4) AS avg_momentum_delta,
    ROUND(AVG(trend_delta), 4) AS avg_trend_delta,
    ROUND(AVG(attention_delta), 4) AS avg_attention_delta,
    ROUND(AVG(event_delta), 4) AS avg_event_delta,
    ROUND(AVG(quality_delta), 4) AS avg_quality_delta,
    ROUND(AVG(valuation_delta), 4) AS avg_valuation_delta,
    ROUND(AVG(safety_delta), 4) AS avg_safety_delta,
    ROUND(AVG(scale_delta), 4) AS avg_scale_delta,
    ROUND(AVG(close_delta_pct), 4) AS avg_close_delta_pct
FROM backwards_profile_component_deltas AS c
    CROSS JOIN filters AS f
WHERE c.profile_name = f.profile_name
    AND c.anchor_name = f.anchor_name
    AND c.in_current
    AND c.in_anchor;
-- @block q_backwards_new_universe_entries
-- Symbols present in current run but absent at anchor (new to scored universe).
WITH filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'weeks' AS horizon_name,
        'last_week' AS anchor_name
)
SELECT symbol,
    company,
    sector,
    industry,
    ROUND(current_score, 4) AS current_score,
    current_direction,
    current_rank,
    ROUND(current_close, 4) AS current_close,
    ROUND(current_perf_w, 4) AS current_perf_w
FROM backwards_profile_horizon_deltas AS d
    CROSS JOIN filters AS f
WHERE d.profile_name = f.profile_name
    AND d.horizon_name = f.horizon_name
    AND d.anchor_name = f.anchor_name
    AND d.in_current
    AND NOT d.in_anchor
ORDER BY current_score DESC NULLS LAST,
    symbol ASC
LIMIT 80;
-- @block q_backwards_dropped_universe_entries
-- Symbols scored at anchor but no longer in current universe.
WITH filters AS (
    SELECT 'breakout_long_v1' AS profile_name,
        'weeks' AS horizon_name,
        'last_week' AS anchor_name
)
SELECT symbol,
    company,
    sector,
    industry,
    ROUND(anchor_score, 4) AS anchor_score,
    anchor_direction,
    anchor_rank,
    ROUND(anchor_close, 4) AS anchor_close,
    ROUND(anchor_perf_w, 4) AS anchor_perf_w
FROM backwards_profile_horizon_deltas AS d
    CROSS JOIN filters AS f
WHERE d.profile_name = f.profile_name
    AND d.horizon_name = f.horizon_name
    AND d.anchor_name = f.anchor_name
    AND d.in_anchor
    AND NOT d.in_current
ORDER BY anchor_score DESC NULLS LAST,
    symbol ASC
LIMIT 80;
