-- -- @block
-- CREATE TABLE sec_cik_tickers_mapping (
--     Cik INT PRIMARY KEY,
--     Ticker VARCHAR(16) NOT NULL,
--     Title VARCHAR(255) NOT NULL
-- );
-- -- @block
-- ALTER TABLE sec_cik_tickers_mapping
-- ADD INDEX idx_ticker (Ticker);
-- -- @block
-- SELECT *
-- FROM sec_cik_tickers_mapping
-- WHERE Cik = 1046179;
-- -- @block
-- DROP TABLE sec_cik_tickers_mapping
-- @block
CREATE TABLE edgar_financial_data_concepts (
    Id INT AUTO_INCREMENT PRIMARY KEY,
    Cik INT NOT NULL,
    Ticker VARCHAR(16) NOT NULL,
    FilingType VARCHAR(8) NOT NULL,
    -- '10-K', '10-Q', etc.
    FiscalPeriod VARCHAR(16) NOT NULL,
    -- e.g., 'Q1', 'FY'
    FiscalYear INT NOT NULL,
    -- e.g., 2025
    Concept VARCHAR(128) NOT NULL,
    -- e.g., 'Revenues'
    Value DECIMAL(24, 4),
    -- Numeric value (NULL if not monetary)
    ValueString VARCHAR(255),
    -- String value (NULL if not string)
    Unit VARCHAR(32),
    -- e.g., 'USD', 'shares'
    DataType VARCHAR(32),
    -- 'monetary', 'shares', 'string', etc.
    Adsh VARCHAR(32) NOT NULL,
    -- Filing accession number
    PeriodEnd DATE,
    -- Period end date
    UNIQUE KEY unique_concept (
        Cik,
        FilingType,
        FiscalPeriod,
        FiscalYear,
        Concept,
        Adsh
    )
);
-- @block
SELECT *
FROM edgar_financial_data_concepts
WHERE Ticker = 'MU'
    AND FilingType = '10-Q'
    AND FiscalPeriod = 'Q1'
    AND FiscalYear = 2025
    AND (
        Concept LIKE '%Revenue%'
        OR Concept LIKE '%Sales%'
    );