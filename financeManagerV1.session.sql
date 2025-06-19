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
-- CREATE TABLE edgar_financial_data_concepts (
--     Id INT AUTO_INCREMENT PRIMARY KEY,
--     Cik INT NOT NULL,
--     Ticker VARCHAR(16) NOT NULL,
--     FilingType VARCHAR(8) NOT NULL,
--     FiscalPeriod VARCHAR(16) NOT NULL,
--     FiscalYear INT NOT NULL,
--     Concept VARCHAR(256) NOT NULL,
--     Value DECIMAL(24, 4),
--     ValueString VARCHAR(255),
--     Unit VARCHAR(32),
--     DataType VARCHAR(32),
--     Adsh VARCHAR(32) NOT NULL,
--     PeriodEnd DATE,
--     Ddate DATE,
--     -- <--- Add this: actual date for the value (from num.txt)
--     Segment VARCHAR(512),
--     -- <--- Add this: segment/context info if available
--     Qtrs INT,
--     -- num of quarters on this concept coverage
--     BatchTag VARCHAR(32),
--     -- tag to know from what entry batch
--     INDEX idx_cik (Cik),
--     INDEX idx_batch_tag (BatchTag),
--     INDEX idx_ticker (Ticker),
--     INDEX idx_concept (Concept) -- No unique constraint on concept/adsh/period
-- );
-- -- @block
-- CREATE TABLE edgar_tag_info (
--     Id INT AUTO_INCREMENT PRIMARY KEY,
--     Tag VARCHAR(256) NOT NULL UNIQUE,
--     Version VARCHAR(64),
--     Custom TINYINT(1),
--     Abstract TINYINT(1),
--     Datatype VARCHAR(64),
--     Iord CHAR(1),
--     Crdr CHAR(1),
--     Tlabel VARCHAR(512),
--     Doc TEXT
-- );
-- -- @block
-- CREATE TABLE nyu_tickers_industry_grouping (
--     Id SERIAL PRIMARY KEY,
--     CompanyName VARCHAR(128),
--     ExchangeTicker VARCHAR(64),
--     IndustryGroup VARCHAR(128),
--     PrimarySector VARCHAR(128),
--     SicCode INTEGER,
--     Country VARCHAR(64),
--     BroadGroup VARCHAR(128),
--     SubGroup VARCHAR(128)
-- );
-- -- @block
-- CREATE TABLE sec_company_info (
--     Cik INT PRIMARY KEY,
--     Name VARCHAR(256),
--     Sic INT,
--     CountryBa VARCHAR(8),
--     StprBa VARCHAR(8),
--     CityBa VARCHAR(64),
--     ZipBa VARCHAR(16),
--     Bas1 VARCHAR(128),
--     Bas2 VARCHAR(128),
--     Baph VARCHAR(32),
--     CountryMa VARCHAR(8),
--     StprMa VARCHAR(8),
--     CityMa VARCHAR(64),
--     ZipMa VARCHAR(16),
--     Mas1 VARCHAR(128),
--     Mas2 VARCHAR(128),
--     CountryInc VARCHAR(8),
--     StprInc VARCHAR(8),
--     Ein VARCHAR(32),
--     Former VARCHAR(128),
--     Changed VARCHAR(16),
--     Afs VARCHAR(8),
--     Wksi VARCHAR(8)
-- );
-- @block
SELECT *
FROM sec_company_info
WHERE Cik = 1800;
-- @block
SELECT Cik
FROM sec_company_info
WHERE Sic IN (
        SELECT DISTINCT SicCode
        FROM nyu_tickers_industry_grouping
        WHERE IndustryGroup = 'Advertising'
    );
-- @block
SELECT *
FROM nyu_tickers_industry_grouping;
-- @block
SELECT DISTINCT SicCode
FROM nyu_tickers_industry_grouping
WHERE IndustryGroup = 'Advertising';
-- @block
SELECT DISTINCT Cik
FROM sec_company_info;
-- @block
SELECT DISTINCT IndustryGroup
FROM nyu_tickers_industry_grouping;
-- @block
SELECT *
FROM sec_cik_tickers_mapping
WHERE Title LIKE '%baidu%';
-- @block
SELECT *
FROM edgar_financial_data_concepts
WHERE Cik = '27673';
-- @block
SELECT Id,
    Cik,
    Concept
FROM edgar_financial_data_concepts
WHERE BatchTag = '2024q1'
LIMIT 100;
-- @block
SELECT DISTINCT BatchTag
FROM edgar_financial_data_concepts;
-- @block
DELETE FROM edgar_financial_data_concepts
WHERE BatchTag = '2024q1'
LIMIT 300000;
-- @block
SELECT *
FROM edgar_financial_data_concepts
WHERE Concept = 'RevenueFromContractWithCustomerExcludingAssessedTax'
    AND (
        Ticker = 'AAPL' -- OR Cik = 723125
    )
    AND YEAR(Ddate) = 2025
    AND Qtrs = 1
    AND (
        Segment IS NULL
        OR Segment = ''
    );
-- @block
SELECT *
FROM sec_company_info
WHERE Cik = 320193;
-- @block
SELECT COUNT(DISTINCT Cik) AS company_count
FROM edgar_financial_data_concepts;
-- @block
-- @block
SELECT *
FROM edgar_financial_data_concepts
WHERE Cik = 1329099
    AND BatchTag = '2024q4';
-- @block
SELECT *
FROM sec_company_info
WHERE Cik NOT IN (
        SELECT DISTINCT Cik
        FROM edgar_financial_data_concepts
    );
-- @block
SELECT *
FROM edgar_financial_data_concepts
WHERE Concept = 'RevenueFromContractWithCustomerExcludingAssessedTax'
    AND (
        Ticker = ''
        OR Cik = 1329099
    )
    AND (
        Segment IS NULL
        OR Segment = ''
    )
ORDER BY FiscalYear DESC,
    -- Give FY highest priority, then others in descending lex order
    CASE
        WHEN FiscalPeriod = 'FY' THEN 1
        ELSE 0
    END DESC,
    FiscalPeriod DESC,
    Ddate DESC;