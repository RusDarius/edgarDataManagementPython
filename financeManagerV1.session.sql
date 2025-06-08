-- -- @block
-- CREATE TABLE sec_cik_tickers_mapping (
--     Cik INT PRIMARY KEY,
--     Ticker VARCHAR(16) NOT NULL,
--     Title VARCHAR(255) NOT NULL
-- );
-- -- @block
-- ALTER TABLE sec_cik_tickers_mapping
-- ADD INDEX idx_ticker (Ticker);
-- @block
SELECT *
FROM sec_cik_tickers_mapping
WHERE Ticker = "MU";
-- @block
DROP TABLE edgar_financial_data_concepts;
-- @block
CREATE TABLE edgar_financial_data_concepts (
    Id INT AUTO_INCREMENT PRIMARY KEY,
    Cik INT NOT NULL,
    Ticker VARCHAR(16) NOT NULL,
    FilingType VARCHAR(8) NOT NULL,
    FiscalPeriod VARCHAR(16) NOT NULL,
    FiscalYear INT NOT NULL,
    Concept VARCHAR(256) NOT NULL,
    Value DECIMAL(24, 4),
    ValueString VARCHAR(255),
    Unit VARCHAR(32),
    DataType VARCHAR(32),
    Adsh VARCHAR(32) NOT NULL,
    PeriodEnd DATE,
    Ddate DATE,
    -- <--- Add this: actual date for the value (from num.txt)
    Segment VARCHAR(128),
    -- <--- Add this: segment/context info if available
    INDEX idx_cik (Cik),
    INDEX idx_ticker (Ticker),
    INDEX idx_concept (Concept) -- No unique constraint on concept/adsh/period
);
-- @block
-- @block
SELECT *
FROM edgar_financial_data_concepts
WHERE Concept = 'RevenueFromContractWithCustomerExcludingAssessedTax'
    AND (
        Ticker = 'MU'
        OR Cik = 723125
    );