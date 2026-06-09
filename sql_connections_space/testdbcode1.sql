
-- @block
-- Lists all tables available in the connected DuckDB file.
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'main'
ORDER BY table_name;