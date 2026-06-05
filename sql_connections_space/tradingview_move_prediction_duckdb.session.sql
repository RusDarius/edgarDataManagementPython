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
LIMIT 50;
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