-- DuckDB query session for TradingView move prediction weekly store
-- Connection target: move_prediction_duckdb_week_2026_W22
-- Keep the same workflow as MySQL files: run one @block at a time in SQLTools.
-- @block
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'main'
ORDER BY table_name;
-- @block
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
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT profile_name,
    symbol,
    company,
    horizon_name,
    score,
    confidence,
    risk_adjusted_score,
    manager_action_signal
FROM profile_horizon_scores
WHERE run_id = (
        SELECT run_id
        FROM latest_run
    )
    AND profile_name = 'breakout_long'
    AND horizon_name = 'weeks'
ORDER BY risk_adjusted_score DESC NULLS LAST
LIMIT 100;
-- @block
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
SELECT table_name,
    row_count,
    parquet_path,
    exported_at_utc
FROM parquet_exports
ORDER BY exported_at_utc DESC,
    table_name;