-- DuckDB query session for TradingView move prediction historical analysis DB
-- Connection target: historical_prediction_analysis.duckdb inside one analysis run folder under
-- logs/tradingview_analysis/prediction_analysis/duckdb_runs/historical_prediction_analysis/runs/<analysis_run_id>/
-- Use one @block at a time in SQLTools.
-- Disconnect this SQLTools DuckDB connection before rerunning the Python writer; DuckDB locks the file per process on Windows.
-- @block
-- Lists all base tables and views available in the connected historical analysis DuckDB.
SELECT table_type,
    table_name
FROM information_schema.tables
WHERE table_schema = 'main'
ORDER BY CASE
        WHEN table_type = 'BASE TABLE' THEN 0
        ELSE 1
    END,
    table_name;
-- @block
-- Lists every column in the connected historical analysis DuckDB.
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
-- @block
-- Shows available aggregation runs, output mode flags, and where the DuckDB/log outputs were written.
SELECT analysis_run_id,
    created_at_utc,
    input_database_count,
    input_run_count,
    output_dir,
    analysis_database_path,
    parquet_dir,
    tabular_output_mode,
    legacy_csv_outputs_enabled,
    parquet_exports_enabled,
    include_profiles_json
FROM historical_analysis_runs
ORDER BY created_at_utc DESC
LIMIT 20;
-- @block
-- Lists generated report entries for the latest historical analysis run.
-- In DuckDB-only mode, table outputs are registered as <database_path>::<table_name> locators.
WITH latest_analysis AS (
    SELECT analysis_run_id
    FROM historical_analysis_runs
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT report_key,
    report_type,
    file_path,
    created_at_utc
FROM generated_reports
WHERE run_id = (
        SELECT analysis_run_id
        FROM latest_analysis
    )
ORDER BY report_type,
    report_key;
-- @block
-- Lists Parquet exports for the latest historical analysis run, if export_parquet was enabled.
WITH latest_analysis AS (
    SELECT analysis_run_id
    FROM historical_analysis_runs
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT table_name,
    parquet_path,
    row_count,
    exported_at_utc
FROM parquet_exports
WHERE run_id = (
        SELECT analysis_run_id
        FROM latest_analysis
    )
ORDER BY table_name;
-- @block
-- Inventories the weekly DuckDB source runs that fed the latest historical analysis run.
WITH latest_analysis AS (
    SELECT analysis_run_id
    FROM historical_analysis_runs
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT run_id,
    created_at_utc,
    snapshot_date,
    snapshot_session,
    snapshot_label,
    suite_name,
    database_path,
    profile_count,
    profile_names_json
FROM vw_analysis_input_runs
WHERE analysis_run_id = (
        SELECT analysis_run_id
        FROM latest_analysis
    )
ORDER BY created_at_utc,
    run_id;
-- @block
-- Profile/horizon alignment summary for the latest analysis run.
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
    aligned_direction_count,
    aligned_positive_count,
    false_positive_count,
    ROUND(
        aligned_direction_count * 1.0 / NULLIF(symbol_count, 0),
        4
    ) AS aligned_ratio,
    ROUND(score_price_corr, 4) AS score_price_corr,
    ROUND(rank_price_corr, 4) AS rank_price_corr
FROM vw_profile_horizon_alignment_stats
WHERE analysis_run_id = (
        SELECT analysis_run_id
        FROM latest_analysis
    )
ORDER BY aligned_ratio DESC NULLS LAST,
    score_price_corr DESC NULLS LAST,
    profile_name,
    horizon_name;
-- @block
-- Score-trend leaders for the latest analysis run: symbols with improving scores and positive realized price performance.
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
    AND score_delta_total > 0
    AND close_return_pct_total > 0
ORDER BY close_return_pct_total DESC NULLS LAST,
    score_delta_total DESC NULLS LAST,
    profile_name,
    horizon_name,
    symbol
LIMIT 100;
-- @block
-- False positives for the latest analysis run: score improved but price still fell over the full history window.
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
    AND score_delta_total > 0
    AND close_return_pct_total < 0
ORDER BY close_return_pct_total ASC,
    score_delta_total DESC NULLS LAST,
    profile_name,
    horizon_name,
    symbol;
-- @block
-- Current leaders by profile and horizon for the latest analysis run.
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
-- @block
-- Snapshot-to-snapshot disagreements for the latest analysis run.
-- Use this to inspect cases where score moved one way but realized price moved the other way between adjacent snapshots.
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
ORDER BY ABS(close_return_pct_vs_previous_snapshot) DESC,
    ABS(score_delta_vs_previous) DESC,
    profile_name,
    horizon_name,
    symbol
LIMIT 100;
-- @block
-- Symbol drill-down for one ticker in the latest analysis run.
-- Edit the symbol in symbol_filter below before running.
WITH latest_analysis AS (
    SELECT analysis_run_id
    FROM historical_analysis_runs
    ORDER BY created_at_utc DESC
    LIMIT 1
), symbol_filter AS (
    SELECT 'NVDA' AS symbol_value
)
SELECT profile_name,
    horizon_name,
    symbol,
    snapshot_label,
    ROUND(score, 4) AS score,
    ROUND(score_delta_vs_previous, 4) AS score_delta_vs_previous,
    rank,
    rank_improvement_vs_previous,
    ROUND(close, 4) AS close,
    ROUND(close_return_pct_vs_previous_snapshot, 4) AS close_return_pct_vs_previous_snapshot,
    direction,
    confidence,
    setup
FROM vw_profile_horizon_snapshot_deltas
WHERE analysis_run_id = (
        SELECT analysis_run_id
        FROM latest_analysis
    )
    AND symbol = (
        SELECT symbol_value
        FROM symbol_filter
    )
ORDER BY profile_name,
    horizon_name,
    snapshot_date,
    snapshot_session;