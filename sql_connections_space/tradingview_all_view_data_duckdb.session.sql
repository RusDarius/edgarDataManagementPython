-- DuckDB query session for TradingView all-fields daily export store
-- Produced by: export_all_tradingview_fields_duckdb()
-- Connection target example:
--   D:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/29_03_2026/tradingview_all_fields_29_03_2026.duckdb
--
-- Storage layout per day (dd_mm_yyyy):
--   trading_view_all_fields_data/<dd_mm_yyyy>/tradingview_all_fields_<dd_mm_yyyy>.duckdb
--   trading_view_all_fields_data/<dd_mm_yyyy>/parquet/
--   trading_view_all_fields_data/<dd_mm_yyyy>/runs/<run_id>/_duckdb_run_overview.log
--
-- Workflow: run one @block at a time in SQLTools.
-- Disconnect this DuckDB connection before rerunning the Python export; DuckDB locks the file per process on Windows.
-- @block
-- Lists all tables available in the connected daily DuckDB file.
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'main'
    AND table_type = 'BASE TABLE'
ORDER BY table_name;
-- @block
-- Lists every table and every column in the connected DuckDB main schema.
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
-- Shows recent all-fields export runs for the connected day database.
SELECT run_id,
    created_at_utc,
    run_label,
    suite_name,
    scan_data_count,
    run_id_generated,
    notes
FROM run_metadata
WHERE suite_name = 'tradingview_all_fields_export_duckdb'
ORDER BY created_at_utc DESC;
-- @block
-- Verifies each run's metadata row count matches rows stored in all_fields_rows.
SELECT m.run_id,
    m.created_at_utc,
    m.run_label,
    m.scan_data_count AS expected_rows,
    COUNT(r.symbol) AS actual_rows,
    COUNT(DISTINCT r.symbol) AS distinct_symbols,
    CASE
        WHEN m.scan_data_count = COUNT(r.symbol) THEN 'ok'
        ELSE 'mismatch'
    END AS row_count_status
FROM run_metadata m
    LEFT JOIN all_fields_rows r ON r.run_id = m.run_id
WHERE m.suite_name = 'tradingview_all_fields_export_duckdb'
GROUP BY m.run_id,
    m.created_at_utc,
    m.run_label,
    m.scan_data_count
ORDER BY m.created_at_utc DESC;
-- @block
-- Quick sanity check for the latest run: symbol count, duplicates, and a few core field fill rates.
WITH latest_run AS (
    SELECT run_id,
        created_at_utc,
        run_label,
        scan_data_count
    FROM run_metadata
    WHERE suite_name = 'tradingview_all_fields_export_duckdb'
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT lr.run_id,
    lr.created_at_utc,
    lr.run_label,
    lr.scan_data_count,
    COUNT(*) AS row_count,
    COUNT(DISTINCT r.symbol) AS distinct_symbols,
    COUNT(*) - COUNT(DISTINCT r.symbol) AS duplicate_symbol_rows,
    SUM(
        CASE
            WHEN COALESCE(TRIM(r.symbol), '') = '' THEN 1
            ELSE 0
        END
    ) AS blank_symbol_rows,
    SUM(
        CASE
            WHEN COALESCE(TRIM(r.close), '') <> '' THEN 1
            ELSE 0
        END
    ) AS rows_with_close,
    SUM(
        CASE
            WHEN COALESCE(TRIM(r.name), '') <> '' THEN 1
            ELSE 0
        END
    ) AS rows_with_name,
    SUM(
        CASE
            WHEN COALESCE(TRIM(r.market_cap_basic), '') <> '' THEN 1
            ELSE 0
        END
    ) AS rows_with_market_cap_basic,
    SUM(
        CASE
            WHEN COALESCE(TRIM(r.sector), '') <> '' THEN 1
            ELSE 0
        END
    ) AS rows_with_sector,
    SUM(
        CASE
            WHEN COALESCE(TRIM(r.industry), '') <> '' THEN 1
            ELSE 0
        END
    ) AS rows_with_industry
FROM latest_run lr
    JOIN all_fields_rows r ON r.run_id = lr.run_id
GROUP BY lr.run_id,
    lr.created_at_utc,
    lr.run_label,
    lr.scan_data_count;
-- @block
-- Sample 25 symbols from the latest run to eyeball core fields.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    WHERE suite_name = 'tradingview_all_fields_export_duckdb'
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT r.symbol,
    r.name,
    r.close,
    r.volume,
    r.market_cap_basic,
    r.sector,
    r.industry,
    r.country,
    r.exchange,
    r.type,
    r.row_number
FROM all_fields_rows r
WHERE r.run_id = (
        SELECT run_id
        FROM latest_run
    )
ORDER BY r.symbol
LIMIT 25;
-- @block
-- Top 25 names by market cap in the latest run (market_cap_basic is stored as text).
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    WHERE suite_name = 'tradingview_all_fields_export_duckdb'
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT r.symbol,
    r.name,
    TRY_CAST(r.market_cap_basic AS DOUBLE) AS market_cap_basic,
    r.close,
    r.sector,
    r.industry
FROM all_fields_rows r
WHERE r.run_id = (
        SELECT run_id
        FROM latest_run
    )
    AND COALESCE(TRIM(r.market_cap_basic), '') <> ''
ORDER BY market_cap_basic DESC NULLS LAST
LIMIT 25;
-- @block
-- Inspect one known symbol in the latest run. Edit the symbol filter as needed.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    WHERE suite_name = 'tradingview_all_fields_export_duckdb'
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT r.run_id,
    r.symbol,
    r.name,
    r.close,
    r.volume,
    r.market_cap_basic,
    r.sector,
    r.industry,
    r.description,
    r.recommendation_mark,
    r.price_earnings_ttm,
    r.dividends_yield_current,
    r.earnings_release_next_date
FROM all_fields_rows r
WHERE r.run_id = (
        SELECT run_id
        FROM latest_run
    )
    AND (
        UPPER(r.symbol) LIKE '%AAPL%'
        OR UPPER(r.name) LIKE '%APPLE%'
    )
ORDER BY r.symbol;
-- @block
-- Finds duplicate symbols inside a single run (should normally return zero rows).
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    WHERE suite_name = 'tradingview_all_fields_export_duckdb'
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT r.run_id,
    r.symbol,
    COUNT(*) AS duplicate_count
FROM all_fields_rows r
WHERE r.run_id = (
        SELECT run_id
        FROM latest_run
    )
GROUP BY r.run_id,
    r.symbol
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC,
    r.symbol;
-- @block
-- Compare two runs from the same day database, if multiple intraday exports exist.
WITH ranked_runs AS (
    SELECT run_id,
        created_at_utc,
        run_label,
        ROW_NUMBER() OVER (
            ORDER BY created_at_utc DESC
        ) AS run_rank
    FROM run_metadata
    WHERE suite_name = 'tradingview_all_fields_export_duckdb'
),
latest_two AS (
    SELECT run_id,
        created_at_utc,
        run_label,
        run_rank
    FROM ranked_runs
    WHERE run_rank <= 2
)
SELECT lt.run_rank,
    lt.run_id,
    lt.created_at_utc,
    lt.run_label,
    COUNT(r.symbol) AS row_count,
    COUNT(DISTINCT r.symbol) AS distinct_symbols
FROM latest_two lt
    LEFT JOIN all_fields_rows r ON r.run_id = lt.run_id
GROUP BY lt.run_rank,
    lt.run_id,
    lt.created_at_utc,
    lt.run_label
ORDER BY lt.run_rank;
-- @block
-- Lists overview log and other generated report files for the latest run.
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    WHERE suite_name = 'tradingview_all_fields_export_duckdb'
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
ORDER BY created_at_utc DESC,
    report_key;
-- @block
-- Lists Parquet export files and row counts for the connected day database.
SELECT run_id,
    table_name,
    row_count,
    parquet_path,
    exported_at_utc
FROM parquet_exports
ORDER BY exported_at_utc DESC,
    table_name,
    run_id;
-- @block
-- Query a specific run_id directly. Replace the placeholder below after reading it from run_metadata or _duckdb_run_overview.log.
SELECT symbol,
    name,
    close,
    volume,
    market_cap_basic,
    sector,
    industry
FROM all_fields_rows
WHERE run_id = 'REPLACE_WITH_RUN_ID'
ORDER BY symbol
LIMIT 50;