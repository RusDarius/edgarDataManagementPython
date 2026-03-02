-- place for operations relating to sec_cik_tickers_mapping or SEC CIK Tickers 
-- @block
-- Get all values from cik_ticker_checked table 
SELECT *
FROM cik_ticker_checked
WHERE Checked = TRUE;
-- -- @block
-- -- Table to store Cik, Ticker, and Checked status for financial concepts coverage
-- CREATE TABLE cik_ticker_checked (
--     Cik INT NOT NULL,
--     Ticker VARCHAR(16) NOT NULL,
--     Checked BOOLEAN DEFAULT FALSE,
--     PRIMARY KEY (Cik),
--     INDEX idx_cik_ticker_checked_ticker (Ticker)
-- );
-- @block
SELECT *
FROM sec_cik_tickers_mapping
WHERE Cik = 1046102;
-- @block
SELECT *
FROM sec_cik_tickers_mapping
WHERE Ticker = 'RBA';
-- @block
SELECT *
FROM sec_cik_tickers_mapping
WHERE JSON_CONTAINS(SecondaryTickers, '"BIDU"');