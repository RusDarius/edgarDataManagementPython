-- DuckDB query session for TradingView move prediction weekly store
-- Connection target: move_prediction_duckdb_week_2026_W22
-- Keep the same workflow as MySQL files: run one @block at a time in SQLTools.
-- Disconnect this SQLTools DuckDB connection before rerunning the Python writer; DuckDB locks the file per process on Windows.
-- @block
-- Lists every table and every column (field) in the connected DuckDB main schema.
SELECT c.table_name,
    c.ordinal_position AS column_position,
    c.column_name,
    c.data_type,
    c.is_nullable
FROM information_schema.columns c
    JOIN information_schema.tables t ON c.table_schema = t.table_schema
    AND c.table_name = t.table_name
WHERE c.table_schema = 'main'
    AND t.table_schema = 'main'
    AND t.table_type = 'BASE TABLE'
ORDER BY c.table_name,
    c.ordinal_position;
-- @block
-- Lists all tables available in the connected DuckDB file.
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'main'
ORDER BY table_name;
-- @block
-- Shows recent suite runs and their high-level run settings.
SELECT run_id,
    created_at_utc,
    scan_data_count,
    suite_name,
    min_market_cap_usd,
    include_blind_spot_sections
FROM run_metadata
ORDER BY created_at_utc DESC
LIMIT 20;
-- @block
-- Inventories every distinct run_id found in metadata/output tables, including generated UTC date and row coverage.
-- Run with the cursor on the WITH line below, or select the full statement; selecting only this comment/@block is empty.
WITH all_runs AS (
    SELECT run_id
    FROM run_metadata
    UNION
    SELECT run_id
    FROM raw_scan_rows
    UNION
    SELECT run_id
    FROM profile_horizon_scores
    UNION
    SELECT run_id
    FROM consensus_horizon_scores
),
raw_counts AS (
    SELECT run_id,
        COUNT(*) AS raw_rows
    FROM raw_scan_rows
    GROUP BY run_id
),
profile_counts AS (
    SELECT run_id,
        COUNT(DISTINCT profile_name) AS profiles_scored,
        COUNT(DISTINCT horizon_name) AS profile_horizons,
        COUNT(*) AS profile_horizon_rows
    FROM profile_horizon_scores
    GROUP BY run_id
),
consensus_counts AS (
    SELECT run_id,
        COUNT(DISTINCT symbol) AS consensus_symbols,
        COUNT(DISTINCT horizon_name) AS consensus_horizons,
        COUNT(*) AS consensus_horizon_rows
    FROM consensus_horizon_scores
    GROUP BY run_id
),
report_counts AS (
    SELECT run_id,
        COUNT(*) AS report_count
    FROM generated_reports
    GROUP BY run_id
)
SELECT r.run_id,
    CAST(m.created_at_utc AS DATE) AS generated_date_utc,
    DATE_TRUNC('minute', m.created_at_utc) AS generated_minute_utc,
    COUNT(*) OVER (PARTITION BY CAST(m.created_at_utc AS DATE)) AS runs_on_generated_date,
    m.created_at_utc,
    m.scan_data_count,
    COALESCE(raw_counts.raw_rows, 0) AS raw_rows,
    COALESCE(profile_counts.profiles_scored, 0) AS profiles_scored,
    COALESCE(profile_counts.profile_horizons, 0) AS profile_horizons,
    COALESCE(profile_counts.profile_horizon_rows, 0) AS profile_horizon_rows,
    COALESCE(consensus_counts.consensus_symbols, 0) AS consensus_symbols,
    COALESCE(consensus_counts.consensus_horizons, 0) AS consensus_horizons,
    COALESCE(consensus_counts.consensus_horizon_rows, 0) AS consensus_horizon_rows,
    COALESCE(report_counts.report_count, 0) AS report_count,
    m.min_market_cap_usd,
    m.max_market_cap_usd,
    m.industries_json,
    m.profile_names_json
FROM all_runs r
    LEFT JOIN run_metadata m ON r.run_id = m.run_id
    LEFT JOIN raw_counts ON r.run_id = raw_counts.run_id
    LEFT JOIN profile_counts ON r.run_id = profile_counts.run_id
    LEFT JOIN consensus_counts ON r.run_id = consensus_counts.run_id
    LEFT JOIN report_counts ON r.run_id = report_counts.run_id
ORDER BY m.created_at_utc DESC NULLS LAST,
    r.run_id;
-- @block
-- Profile scores across ALL profiles and ALL horizons for a ticker watchlist.
-- One distinct row per symbol + profile_name + horizon_name.
-- Edit the tickers list in tickers[] below.
--
-- Three variants are included below:
--   A) latest run          — ACTIVE (runnable as-is)
--   B) specific run_id     — commented out; uncomment block B and comment block A to use
--   C) all runs            — commented out; uncomment block C and comment block A to use
WITH tickers AS (
    SELECT unnest(
            [
                'STMPA',
                'DUOL',
                'VEEV',
                'SEZL',
                'GOOG',
                'ZS',
                'UNH',
                'NOVO_B',
                'GTLB',
                'PATH',

            ]
        ) AS ticker
),
latest_run AS (
    SELECT run_id,
        created_at_utc,
        run_date_utc,
        iso_year,
        iso_week
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.industry,
    h.market_cap_basic,
    h.close AS close_price,
    h.profile_name,
    h.horizon_name,
    h.score,
    h.direction,
    h.confidence,
    h.coverage,
    h.setup,
    h.risk_adjusted_score,
    h.risk_tier,
    h.manager_action_signal
FROM profile_horizon_scores h
    INNER JOIN latest_run lr ON h.run_id = lr.run_id
    INNER JOIN tickers t ON upper(trim(h.symbol)) = upper(trim(t.ticker))
    OR upper(trim(h.symbol)) LIKE '%:' || upper(trim(t.ticker))
ORDER BY h.symbol,
    h.profile_name,
    CASE
        h.horizon_name
        WHEN 'days' THEN 1
        WHEN 'weeks' THEN 2
        WHEN 'months' THEN 3
        WHEN 'years' THEN 4
        ELSE 99
    END;
-- @block
-- Variant B: profile scores for a SPECIFIC run_id and ticker watchlist.
-- Uncomment the full statement below and comment out Variant A block above to use.
--
-- WITH tickers AS (
--     SELECT unnest(
--             [
--                 'NVDA',
--                 'AAPL',
--                 'MSFT',
--                 'GOOGL',
--                 'AMZN'
--             ]
--         ) AS ticker
-- ),
-- target_run AS (
--     SELECT run_id,
--         created_at_utc,
--         run_date_utc,
--         iso_year,
--         iso_week
--     FROM run_metadata
--     WHERE run_id = 'move_prediction_20260610_1437_utc_698d1f59'
-- )
-- SELECT tr.run_date_utc,
--     tr.created_at_utc AS run_created_at_utc,
--     tr.iso_year,
--     tr.iso_week,
--     h.run_id,
--     h.symbol,
--     h.company,
--     h.sector,
--     h.industry,
--     h.market_cap_basic,
--     h.close AS close_price,
--     h.profile_name,
--     h.horizon_name,
--     h.score,
--     h.direction,
--     h.confidence,
--     h.coverage,
--     h.setup,
--     h.risk_adjusted_score,
--     h.risk_tier,
--     h.manager_action_signal
-- FROM profile_horizon_scores h
--     INNER JOIN target_run tr ON h.run_id = tr.run_id
--     INNER JOIN tickers t ON upper(trim(h.symbol)) = upper(trim(t.ticker))
--     OR upper(trim(h.symbol)) LIKE '%:' || upper(trim(t.ticker))
-- ORDER BY h.symbol,
--     h.profile_name,
--     CASE h.horizon_name
--         WHEN 'days' THEN 1
--         WHEN 'weeks' THEN 2
--         WHEN 'months' THEN 3
--         WHEN 'years' THEN 4
--         ELSE 99
--     END;
-- @block
-- Variant C: profile scores across ALL runs for a ticker watchlist.
-- Uncomment the full statement below and comment out Variant A block above to use.
--
-- WITH tickers AS (
--     SELECT unnest(
--             [
--                 'NVDA',
--                 'AAPL',
--                 'MSFT',
--                 'GOOGL',
--                 'AMZN'
--             ]
--         ) AS ticker
-- )
-- SELECT m.run_date_utc,
--     m.created_at_utc AS run_created_at_utc,
--     m.iso_year,
--     m.iso_week,
--     h.run_id,
--     h.symbol,
--     h.company,
--     h.sector,
--     h.industry,
--     h.market_cap_basic,
--     h.close AS close_price,
--     h.profile_name,
--     h.horizon_name,
--     h.score,
--     h.direction,
--     h.confidence,
--     h.coverage,
--     h.setup,
--     h.risk_adjusted_score,
--     h.risk_tier,
--     h.manager_action_signal
-- FROM profile_horizon_scores h
--     INNER JOIN run_metadata m ON h.run_id = m.run_id
--     INNER JOIN tickers t ON upper(trim(h.symbol)) = upper(trim(t.ticker))
--     OR upper(trim(h.symbol)) LIKE '%:' || upper(trim(t.ticker))
-- ORDER BY h.symbol,
--     m.created_at_utc DESC,
--     h.profile_name,
--     CASE h.horizon_name
--         WHEN 'days' THEN 1
--         WHEN 'weeks' THEN 2
--         WHEN 'months' THEN 3
--         WHEN 'years' THEN 4
--         ELSE 99
--     END;
-- @block
-- Ranks the latest run's top weekly consensus long candidates by risk-adjusted score.
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
    c.risk_tier,
    c.manager_action_signal
FROM consensus_horizon_scores c
    JOIN latest_run r ON c.run_id = r.run_id
WHERE c.horizon_name = 'weeks'
    AND c.score IS NOT NULL
ORDER BY c.risk_adjusted_score DESC NULLS LAST
LIMIT 2000;
-- @block
-- Shows breakout_long weekly profile rankings for the latest run.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT *
FROM profile_horizon_scores
WHERE run_id = (
        SELECT run_id
        FROM latest_run
    )
    AND profile_name = 'breakout_long'
    AND horizon_name = 'weeks'
ORDER BY risk_adjusted_score DESC NULLS LAST;
-- @block
-- Shows breakout_long component-level inputs for the latest run; this is the all-components table view.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT c.run_id,
    c.profile_name,
    c.symbol,
    c.company,
    c.sector,
    c.industry,
    c.market_cap_basic,
    c.close AS close_price,
    c.momentum,
    c.trend,
    c.quality,
    c.valuation,
    c.safety,
    c.scale,
    c.attention,
    c.event,
    c.manager_action_signal,
    c.row_number
FROM profile_components c
WHERE c.run_id = (
        SELECT run_id
        FROM latest_run
    )
    AND c.profile_name = 'breakout_long'
ORDER BY c.symbol;
-- @block
-- Shows score plus all component inputs that fed it for breakout_long weekly horizon in the latest run.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.run_id,
    h.profile_name,
    h.horizon_name,
    h.symbol,
    h.company,
    h.sector,
    h.industry,
    h.market_cap_basic,
    h.close AS close_price,
    h.score,
    h.confidence,
    h.coverage,
    h.risk_adjusted_score,
    h.risk_tier,
    h.manager_action_signal,
    c.attention,
    c.event,
    c.momentum,
    c.trend,
    c.quality,
    c.valuation,
    c.safety,
    c.scale
FROM profile_horizon_scores h
    JOIN profile_components c ON h.run_id = c.run_id
    AND h.profile_name = c.profile_name
    AND h.symbol = c.symbol
WHERE h.run_id = (
        SELECT run_id
        FROM latest_run
    )
    AND h.profile_name = 'breakout_long'
    AND h.horizon_name = 'weeks'
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 100;
-- @block
-- Shows all profile/horizon opinions for NVDA in the latest run to inspect disagreement.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT symbol,
    profile_name,
    horizon_name,
    score,
    direction,
    confidence,
    coverage
FROM consensus_profile_horizon_scores
WHERE run_id = (
        SELECT run_id
        FROM latest_run
    )
    AND symbol = 'NVDA'
ORDER BY horizon_name,
    profile_name;
-- @block
-- Lists report log files generated by the latest run.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT report_key,
    report_type,
    profile_name,
    file_path,
    created_at_utc
FROM generated_reports
WHERE run_id = (
        SELECT run_id
        FROM latest_run
    )
ORDER BY created_at_utc DESC;
-- @block
-- Lists Parquet export files and their row counts, newest exports first.
SELECT table_name,
    row_count,
    parquet_path,
    exported_at_utc
FROM parquet_exports
ORDER BY exported_at_utc DESC,
    table_name;
-- @block
-- Latest run: today's best/worst movers with model scores for score-vs-price review.
-- Uses raw_scan_rows.change as same-day % move (TradingView daily change field).
-- Edit top_n (default 40) or horizon filter as needed; days horizon is the primary short-term lens.
WITH latest_run AS (
    SELECT run_id,
        created_at_utc,
        run_date_utc
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
), today_tape AS (
    SELECT lr.run_id,
        lr.created_at_utc AS run_created_at_utc,
        lr.run_date_utc,
        r.symbol,
        r.company,
        r.sector,
        r.industry,
        TRY_CAST(r.close AS DOUBLE) AS close_price,
        TRY_CAST(r.change AS DOUBLE) AS change_pct,
        TRY_CAST(r.change_from_open AS DOUBLE) AS change_from_open_pct,
        TRY_CAST(r.premarket_change AS DOUBLE) AS premarket_change_pct,
        TRY_CAST(r.postmarket_change AS DOUBLE) AS postmarket_change_pct,
        TRY_CAST(r."Perf.5D" AS DOUBLE) AS perf_5d_pct,
        TRY_CAST(r."Perf.W" AS DOUBLE) AS perf_w_pct
    FROM latest_run lr
        INNER JOIN raw_scan_rows r ON r.run_id = lr.run_id
    WHERE TRY_CAST(r.change AS DOUBLE) IS NOT NULL
),
ranked_movers AS (
    SELECT *,
        ROW_NUMBER() OVER (
            ORDER BY change_pct DESC NULLS LAST,
                symbol
        ) AS gainer_rank,
        ROW_NUMBER() OVER (
            ORDER BY change_pct ASC NULLS LAST,
                symbol
        ) AS loser_rank
    FROM today_tape
),
mover_cohort AS (
    SELECT *
    FROM ranked_movers
    WHERE gainer_rank <= 40
        OR loser_rank <= 40
),
consensus_scores AS (
    SELECT c.symbol,
        c.horizon_name,
        c.score AS consensus_score,
        c.risk_adjusted_score AS consensus_risk_adjusted_score,
        c.direction AS consensus_direction,
        c.confidence AS consensus_confidence,
        c.agreement_ratio,
        c.manager_action_signal AS consensus_manager_action_signal
    FROM consensus_horizon_scores c
        INNER JOIN latest_run lr ON c.run_id = lr.run_id
    WHERE c.horizon_name IN ('days', 'weeks')
)
SELECT m.run_created_at_utc,
    m.run_date_utc,
    CASE
        WHEN m.gainer_rank <= 40 THEN 'TOP_GAINER'
        ELSE 'TOP_LOSER'
    END AS mover_bucket,
    m.gainer_rank,
    m.loser_rank,
    m.symbol,
    m.company,
    m.industry,
    m.close_price,
    m.change_pct,
    m.change_from_open_pct,
    m.premarket_change_pct,
    m.postmarket_change_pct,
    m.perf_5d_pct,
    m.perf_w_pct,
    cs_days.consensus_score AS consensus_days_score,
    cs_days.consensus_risk_adjusted_score AS consensus_days_risk_adjusted_score,
    cs_days.consensus_direction AS consensus_days_direction,
    cs_weeks.consensus_score AS consensus_weeks_score,
    cs_weeks.consensus_risk_adjusted_score AS consensus_weeks_risk_adjusted_score,
    cs_weeks.consensus_direction AS consensus_weeks_direction,
    cs_weeks.agreement_ratio AS consensus_weeks_agreement_ratio,
    h.profile_name,
    h.horizon_name,
    h.score,
    h.risk_adjusted_score,
    h.confidence,
    h.risk_tier,
    h.manager_action_signal
FROM mover_cohort m
    LEFT JOIN consensus_scores cs_days ON (
        cs_days.symbol = m.symbol
        OR cs_days.symbol LIKE '%:' || m.symbol
        OR m.symbol LIKE '%:' || cs_days.symbol
    )
    AND cs_days.horizon_name = 'days'
    LEFT JOIN consensus_scores cs_weeks ON (
        cs_weeks.symbol = m.symbol
        OR cs_weeks.symbol LIKE '%:' || m.symbol
        OR m.symbol LIKE '%:' || cs_weeks.symbol
    )
    AND cs_weeks.horizon_name = 'weeks'
    LEFT JOIN profile_horizon_scores h ON h.run_id = m.run_id
    AND (
        h.symbol = m.symbol
        OR h.symbol LIKE '%:' || m.symbol
        OR m.symbol LIKE '%:' || h.symbol
    )
    AND h.horizon_name IN ('days', 'weeks')
ORDER BY m.change_pct DESC NULLS LAST,
    m.symbol,
    h.profile_name,
    CASE
        h.horizon_name
        WHEN 'days' THEN 1
        WHEN 'weeks' THEN 2
        ELSE 99
    END;
-- @block
-- Latest run: compact top-gainer scoreboard (one row per symbol) for quick score/price eyeballing.
-- Shows best consensus + best profile days score beside today's tape.
WITH latest_run AS (
    SELECT run_id,
        created_at_utc,
        run_date_utc
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
), today_tape AS (
    SELECT lr.run_id,
        lr.created_at_utc AS run_created_at_utc,
        lr.run_date_utc,
        r.symbol,
        MAX(r.company) AS company,
        MAX(r.sector) AS sector,
        MAX(r.industry) AS industry,
        MAX(TRY_CAST(r.close AS DOUBLE)) AS close_price,
        MAX(TRY_CAST(r.change AS DOUBLE)) AS change_pct,
        MAX(TRY_CAST(r.change_from_open AS DOUBLE)) AS change_from_open_pct,
        MAX(TRY_CAST(r."Perf.5D" AS DOUBLE)) AS perf_5d_pct
    FROM latest_run lr
        INNER JOIN raw_scan_rows r ON r.run_id = lr.run_id
    WHERE TRY_CAST(r.change AS DOUBLE) IS NOT NULL
    GROUP BY lr.run_id,
        lr.created_at_utc,
        lr.run_date_utc,
        r.symbol
),
profile_days AS (
    SELECT h.symbol,
        MAX(
            CASE
                WHEN h.horizon_name = 'days' THEN h.score
            END
        ) AS best_days_score,
        max_by(
            h.profile_name,
            CASE
                WHEN h.horizon_name = 'days' THEN h.score
            END
        ) AS best_days_profile,
        MAX(
            CASE
                WHEN h.horizon_name = 'days' THEN h.risk_adjusted_score
            END
        ) AS best_days_risk_adjusted_score,
        MAX(
            CASE
                WHEN h.horizon_name = 'weeks' THEN h.score
            END
        ) AS best_weeks_score,
        max_by(
            h.profile_name,
            CASE
                WHEN h.horizon_name = 'weeks' THEN h.score
            END
        ) AS best_weeks_profile,
        COUNT(DISTINCT h.profile_name) FILTER (
            WHERE h.horizon_name = 'days'
                AND h.direction IN ('Strong Up', 'Up')
        ) AS bullish_days_profiles,
        COUNT(DISTINCT h.profile_name) FILTER (
            WHERE h.horizon_name = 'days'
        ) AS days_profiles_scored
    FROM profile_horizon_scores h
        INNER JOIN latest_run lr ON h.run_id = lr.run_id
    GROUP BY h.symbol
),
consensus_days AS (
    SELECT c.symbol,
        c.score AS consensus_days_score,
        c.risk_adjusted_score AS consensus_days_risk_adjusted_score,
        c.direction AS consensus_days_direction,
        c.agreement_ratio AS consensus_days_agreement_ratio,
        c.manager_action_signal AS consensus_days_manager_action_signal
    FROM consensus_horizon_scores c
        INNER JOIN latest_run lr ON c.run_id = lr.run_id
    WHERE c.horizon_name = 'days'
)
SELECT t.run_created_at_utc,
    t.run_date_utc,
    t.symbol,
    t.company,
    t.sector,
    t.industry,
    t.close_price,
    t.change_pct,
    t.change_from_open_pct,
    t.perf_5d_pct,
    cd.consensus_days_score,
    cd.consensus_days_risk_adjusted_score,
    cd.consensus_days_direction,
    cd.consensus_days_agreement_ratio,
    cd.consensus_days_manager_action_signal,
    pd.best_days_score,
    pd.best_days_profile,
    pd.best_days_risk_adjusted_score,
    pd.best_weeks_score,
    pd.best_weeks_profile,
    pd.bullish_days_profiles,
    pd.days_profiles_scored
FROM today_tape t
    LEFT JOIN consensus_days cd ON (
        cd.symbol = t.symbol
        OR cd.symbol LIKE '%:' || t.symbol
        OR t.symbol LIKE '%:' || cd.symbol
    )
    LEFT JOIN profile_days pd ON (
        pd.symbol = t.symbol
        OR pd.symbol LIKE '%:' || t.symbol
        OR t.symbol LIKE '%:' || pd.symbol
    )
ORDER BY t.change_pct DESC NULLS LAST,
    t.symbol
LIMIT 80;
-- @block
-- Latest run: which profile/horizon is most aligned with TODAY's price move (change field).
-- Ranks profiles by correlation and top-vs-bottom decile spread on same-day % change.
-- Prefer days horizon for intraday/daily tuning; weeks included for context.
WITH latest_run AS (
    SELECT run_id,
        created_at_utc,
        run_date_utc
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
), scored_with_today AS (
    SELECT h.profile_name,
        h.horizon_name,
        h.symbol,
        h.score,
        h.risk_adjusted_score,
        h.direction,
        h.confidence,
        CASE
            WHEN h.direction IN ('Strong Up', 'Up') THEN 1
            WHEN h.direction IN ('Strong Down', 'Down') THEN -1
            ELSE 0
        END AS direction_sign,
        TRY_CAST(r.change AS DOUBLE) AS change_pct,
        TRY_CAST(r.change_from_open AS DOUBLE) AS change_from_open_pct
    FROM profile_horizon_scores h
        INNER JOIN latest_run lr ON h.run_id = lr.run_id
        INNER JOIN raw_scan_rows r ON r.run_id = h.run_id
        AND (
            r.symbol = h.symbol
            OR r.symbol LIKE '%:' || h.symbol
            OR h.symbol LIKE '%:' || r.symbol
        )
    WHERE TRY_CAST(r.change AS DOUBLE) IS NOT NULL
        AND h.score IS NOT NULL
),
cohorted AS (
    SELECT *,
        NTILE(10) OVER (
            PARTITION BY profile_name,
            horizon_name
            ORDER BY score DESC NULLS LAST
        ) AS score_decile
    FROM scored_with_today
),
profile_alignment AS (
    SELECT profile_name,
        horizon_name,
        COUNT(*) AS scored_symbols,
        CORR(score, change_pct) AS score_change_corr,
        CORR(risk_adjusted_score, change_pct) AS ras_change_corr,
        CORR(direction_sign, change_pct) AS direction_change_corr,
        CORR(confidence, ABS(change_pct)) AS confidence_abs_move_corr,
        AVG(
            CASE
                WHEN score_decile = 1 THEN change_pct
            END
        ) AS top_decile_avg_change_pct,
        AVG(
            CASE
                WHEN score_decile = 10 THEN change_pct
            END
        ) AS bottom_decile_avg_change_pct,
        AVG(
            CASE
                WHEN direction_sign > 0 THEN change_pct
            END
        ) AS bullish_cohort_avg_change_pct,
        AVG(
            CASE
                WHEN direction_sign < 0 THEN change_pct
            END
        ) AS bearish_cohort_avg_change_pct,
        AVG(
            CASE
                WHEN direction_sign > 0
                AND change_pct > 0 THEN 1.0
                WHEN direction_sign > 0
                AND change_pct <= 0 THEN 0.0
            END
        ) AS bullish_hit_rate,
        AVG(
            CASE
                WHEN direction_sign < 0
                AND change_pct < 0 THEN 1.0
                WHEN direction_sign < 0
                AND change_pct >= 0 THEN 0.0
            END
        ) AS bearish_hit_rate
    FROM cohorted
    GROUP BY profile_name,
        horizon_name
),
consensus_alignment AS (
    SELECT 'consensus' AS profile_name,
        c.horizon_name,
        COUNT(*) AS scored_symbols,
        CORR(c.score, TRY_CAST(r.change AS DOUBLE)) AS score_change_corr,
        CORR(
            c.risk_adjusted_score,
            TRY_CAST(r.change AS DOUBLE)
        ) AS ras_change_corr,
        CORR(
            CASE
                WHEN c.direction IN ('Strong Up', 'Up') THEN 1
                WHEN c.direction IN ('Strong Down', 'Down') THEN -1
                ELSE 0
            END,
            TRY_CAST(r.change AS DOUBLE)
        ) AS direction_change_corr,
        CORR(c.confidence, ABS(TRY_CAST(r.change AS DOUBLE))) AS confidence_abs_move_corr,
        NULL::DOUBLE AS top_decile_avg_change_pct,
        NULL::DOUBLE AS bottom_decile_avg_change_pct,
        AVG(
            CASE
                WHEN c.direction IN ('Strong Up', 'Up') THEN TRY_CAST(r.change AS DOUBLE)
            END
        ) AS bullish_cohort_avg_change_pct,
        AVG(
            CASE
                WHEN c.direction IN ('Strong Down', 'Down') THEN TRY_CAST(r.change AS DOUBLE)
            END
        ) AS bearish_cohort_avg_change_pct,
        AVG(
            CASE
                WHEN c.direction IN ('Strong Up', 'Up')
                AND TRY_CAST(r.change AS DOUBLE) > 0 THEN 1.0
                WHEN c.direction IN ('Strong Up', 'Up')
                AND TRY_CAST(r.change AS DOUBLE) <= 0 THEN 0.0
            END
        ) AS bullish_hit_rate,
        AVG(
            CASE
                WHEN c.direction IN ('Strong Down', 'Down')
                AND TRY_CAST(r.change AS DOUBLE) < 0 THEN 1.0
                WHEN c.direction IN ('Strong Down', 'Down')
                AND TRY_CAST(r.change AS DOUBLE) >= 0 THEN 0.0
            END
        ) AS bearish_hit_rate
    FROM consensus_horizon_scores c
        INNER JOIN latest_run lr ON c.run_id = lr.run_id
        INNER JOIN raw_scan_rows r ON r.run_id = c.run_id
        AND (
            r.symbol = c.symbol
            OR r.symbol LIKE '%:' || c.symbol
            OR c.symbol LIKE '%:' || r.symbol
        )
    WHERE TRY_CAST(r.change AS DOUBLE) IS NOT NULL
        AND c.score IS NOT NULL
    GROUP BY c.horizon_name
)
SELECT *,
    top_decile_avg_change_pct - bottom_decile_avg_change_pct AS top_minus_bottom_decile_spread,
    CASE
        WHEN ras_change_corr >= 0.25 THEN 'ALIGNED'
        WHEN ras_change_corr <= -0.10 THEN 'CONTRARIAN'
        ELSE 'NEUTRAL'
    END AS alignment_label
FROM (
        SELECT *
        FROM profile_alignment
        UNION ALL
        SELECT *
        FROM consensus_alignment
    ) aligned
ORDER BY CASE
        horizon_name
        WHEN 'days' THEN 1
        WHEN 'weeks' THEN 2
        WHEN 'months' THEN 3
        WHEN 'years' THEN 4
        ELSE 99
    END,
    ras_change_corr DESC NULLS LAST,
    top_minus_bottom_decile_spread DESC NULLS LAST,
    profile_name;
-- @block
-- Latest run (or given run): industry rollup by industry + sector + profile + horizon.
-- Comment out entire MEAN or AVG sections below to keep only one aggregate style.
-- Variant A: latest run — ACTIVE. Variant B: swap latest_run for commented target_run.
-- Optional filters in industry_detail WHERE: sector and/or profile_name.
WITH latest_run AS (
    SELECT run_id,
        created_at_utc,
        run_date_utc,
        iso_year,
        iso_week
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
), -- target_run AS (
--     SELECT run_id,
--         created_at_utc,
--         run_date_utc,
--         iso_year,
--         iso_week
--     FROM run_metadata
--     WHERE run_id = 'move_prediction_20260616_1515_utc_8b6d74b8'
-- ),
symbol_tape AS (
    SELECT lr.run_id,
        lr.created_at_utc AS run_created_at_utc,
        lr.run_date_utc,
        lr.iso_year,
        lr.iso_week,
        r.symbol,
        MAX(r.sector) AS sector,
        MAX(r.industry) AS industry,
        MAX(TRY_CAST(r.change AS DOUBLE)) AS change_pct_today,
        MAX(TRY_CAST(r."Perf.W" AS DOUBLE)) AS perf_w_pct,
        MAX(TRY_CAST(r."Perf.1M" AS DOUBLE)) AS perf_1m_pct,
        MAX(TRY_CAST(r."Perf.3M" AS DOUBLE)) AS perf_3m_pct,
        MAX(TRY_CAST(r."Perf.YTD" AS DOUBLE)) AS perf_ytd_pct,
        MAX(TRY_CAST(r."Perf.Y" AS DOUBLE)) AS perf_y_pct,
        MAX(TRY_CAST(r."Perf.5Y" AS DOUBLE)) AS perf_5y_pct
    FROM latest_run lr
        INNER JOIN raw_scan_rows r ON r.run_id = lr.run_id
    GROUP BY lr.run_id,
        lr.created_at_utc,
        lr.run_date_utc,
        lr.iso_year,
        lr.iso_week,
        r.symbol
),
industry_detail AS (
    SELECT t.run_created_at_utc,
        t.run_date_utc,
        t.iso_year,
        t.iso_week,
        h.run_id,
        t.industry,
        t.sector,
        h.symbol,
        h.profile_name,
        h.horizon_name,
        h.score,
        h.risk_adjusted_score,
        h.confidence,
        h.coverage,
        t.change_pct_today,
        t.perf_w_pct,
        t.perf_1m_pct,
        t.perf_3m_pct,
        t.perf_ytd_pct,
        t.perf_y_pct,
        t.perf_5y_pct
    FROM profile_horizon_scores h
        INNER JOIN latest_run lr ON h.run_id = lr.run_id
        INNER JOIN symbol_tape t ON t.run_id = h.run_id
        AND (
            t.symbol = h.symbol
            OR t.symbol LIKE '%:' || h.symbol
            OR h.symbol LIKE '%:' || t.symbol
        )
    WHERE COALESCE(TRIM(t.industry), '') <> '' -- AND t.sector = 'Technology'
        -- AND h.profile_name = 'breakout_long'
)
SELECT run_created_at_utc,
    run_date_utc,
    run_id,
    industry,
    sector,
    profile_name,
    horizon_name,
    COUNT(DISTINCT symbol) AS symbols_in_industry,
    -- --- MEDIAN: model scores ---
    MEDIAN(score) AS median_score,
    MEDIAN(risk_adjusted_score) AS median_risk_adjusted_score,
    MEDIAN(confidence) AS median_confidence,
    MEDIAN(coverage) AS median_coverage,
    -- --- MEDIAN: tape returns ---
    MEDIAN(change_pct_today) AS median_return_today,
    MEDIAN(perf_w_pct) AS median_return_w,
    MEDIAN(perf_1m_pct) AS median_return_1m,
    MEDIAN(perf_3m_pct) AS median_return_3m,
    MEDIAN(perf_ytd_pct) AS median_return_ytd,
    MEDIAN(perf_y_pct) AS median_return_y,
    MEDIAN(perf_5y_pct) AS median_return_5y -- ,
    -- -- --- AVG: model scores ---
    -- AVG(score) AS avg_score,
    -- AVG(risk_adjusted_score) AS avg_risk_adjusted_score,
    -- AVG(confidence) AS avg_confidence,
    -- AVG(coverage) AS avg_coverage,
    -- -- --- AVG: tape returns ---
    -- AVG(change_pct_today) AS avg_return_today,
    -- AVG(perf_w_pct) AS avg_return_w,
    -- AVG(perf_1m_pct) AS avg_return_1m,
    -- AVG(perf_3m_pct) AS avg_return_3m,
    -- AVG(perf_ytd_pct) AS avg_return_ytd,
    -- AVG(perf_y_pct) AS avg_return_y,
    -- AVG(perf_5y_pct) AS avg_return_5y
FROM industry_detail
GROUP BY run_created_at_utc,
    run_date_utc,
    run_id,
    industry,
    sector,
    profile_name,
    horizon_name
ORDER BY median_return_today DESC NULLS LAST,
    industry,
    profile_name,
    CASE
        horizon_name
        WHEN 'days' THEN 1
        WHEN 'weeks' THEN 2
        WHEN 'months' THEN 3
        WHEN 'years' THEN 4
        ELSE 99
    END;