-- @block
-- Get all values from cik_ticker_checked table
SELECT *
FROM cik_ticker_checked;
-- -- @block
-- -- Table to store Cik, Ticker, and Checked status for financial concepts coverage
-- CREATE TABLE cik_ticker_checked (
--     Cik INT NOT NULL,
--     Ticker VARCHAR(16) NOT NULL,
--     Checked BOOLEAN DEFAULT FALSE,
--     PRIMARY KEY (Cik, Ticker)
-- );
-- -- @block
-- -- Add index for Cik for fast lookup
-- CREATE INDEX idx_cik ON cik_ticker_checked (Cik);