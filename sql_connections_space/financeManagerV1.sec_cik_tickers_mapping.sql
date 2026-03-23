-- place for operations relating to sec_cik_tickers_mapping or SEC CIK Tickers 
-- -- @block
-- -- Table to store Cik, Ticker, and Checked status for financial concepts coverage
-- CREATE TABLE cik_ticker_checked (
--     Cik INT NOT NULL,
--     Ticker VARCHAR(16) NOT NULL,
--     Checked BOOLEAN DEFAULT FALSE,
--     PRIMARY KEY (Cik),
--     INDEX idx_cik_ticker_checked_ticker (Ticker)
-- );
-- Get all values from cik_ticker_checked table 
-- @block
-- Get count of Cik values in sec_cik_tickers_mapping
SELECT COUNT(Cik) AS cik_count
FROM sec_cik_tickers_mapping;
-- @block
SELECT *
FROM sec_cik_tickers_mapping
WHERE Title LIKE '%BERKSHIRE%';
-- @block
SELECT *
FROM sec_cik_tickers_mapping
WHERE Cik = 1046102;
-- @block
SELECT *
FROM sec_cik_tickers_mapping
WHERE Ticker LIKE 'FRO%';
-- @block
SELECT *
FROM sec_cik_tickers_mapping
WHERE JSON_CONTAINS(SecondaryTickers, '"BIDU"');
-- @block
-- Read all entries for a given IndustryTradingView (exact match)
SELECT *
FROM sec_cik_tickers_mapping
WHERE IndustryTradingView = 'Semiconductors'
ORDER BY Ticker;