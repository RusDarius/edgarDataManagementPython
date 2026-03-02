-- @block
ALTER TABLE edgar_financial_data_concepts
MODIFY COLUMN BatchTag VARCHAR(128),
    ALGORITHM = INSTANT;
-- @block
-- Get the count of all distinct Cik values in the edgar_financial_data_concepts table
SELECT COUNT(DISTINCT Cik) AS distinct_cik_count
FROM edgar_financial_data_concepts;
-- @block
-- This query returns all facts (Concepts) present for a specific BatchTag,
-- along with the frequency (FactCount) of each Concept, ordered from most to least frequent.
SELECT Concept,
    COUNT(*) AS FactCount
FROM edgar_financial_data_concepts
WHERE BatchTag = '2018q1'
GROUP BY Concept
ORDER BY FactCount DESC;
-- @block
-- Get all distinct Concepts ranked by total occurrences (most to least frequent)
SELECT Concept,
    COUNT(*) AS FactCount
FROM edgar_financial_data_concepts
GROUP BY Concept
ORDER BY FactCount DESC;
-- @block
-- This query returns all revenue-related facts (Concepts) for a specific BatchTag,
-- using a case-insensitive match for 'revenue' in the Concept name.
SELECT Concept,
    COUNT(*) AS FactCount
FROM edgar_financial_data_concepts
WHERE BatchTag = '2018q1'
    AND LOWER(Concept) LIKE '%revenue%'
GROUP BY Concept
ORDER BY FactCount DESC;
-- @block
-- Check count of records to be deleted
SELECT COUNT(*) as records_to_delete
FROM edgar_financial_data_concepts
WHERE BatchTag = '2025q3';
-- @block
-- Delete records with BatchTag "fdx-20240831.htm" (limited to 1000 records per execution)
DELETE FROM edgar_financial_data_concepts
WHERE BatchTag = 'aee-20230930.htm_18654'
LIMIT 2000000;
-- get all distinct BatchTag values
-- @block
SELECT DISTINCT BatchTag
FROM edgar_financial_data_concepts;
-- get all entries for a cik/ticker for a concept sorted by Ddate and getting the first q or fy statement per fp
-- @block
WITH RankedData AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY FiscalYear,
            FiscalPeriod
            ORDER BY FiscalYear DESC,
                Ddate DESC,
                BatchTag DESC
        ) AS row_num
    FROM edgar_financial_data_concepts
    WHERE (
            Concept LIKE 'RevenueFromContractWithCustomerExcludingAssessedTax'
            OR Concept LIKE 'SalesRevenueNet'
            OR Concept LIKE 'Revenues'
            OR Concept LIKE 'Revenue'
        )
        AND (
            Ticker = 'MU' -- Cik = 1750
        )
        AND (
            Segment IS NULL
            OR Segment = ''
        )
        AND Qtrs != 2
        AND Qtrs != 3
        AND (
            FilingType = '10-Q'
            OR FilingType = '10-K'
        )
)
SELECT *
FROM RankedData
WHERE row_num = 1
ORDER BY Ddate DESC;
-- @block
SELECT *
FROM edgar_financial_data_concepts
WHERE (Concept LIKE 'NetIncomeLoss')
    AND (
        -- Ticker = 'FDX'
        Cik = 1750
    );
-- @block
-- Get all distinct FilingType values for ticker FDX and BatchTag 2015q1
SELECT DISTINCT FilingType
FROM edgar_financial_data_concepts
WHERE Ticker = 'FDX';
-- @block
SHOW INDEX
FROM edgar_financial_data_concepts;
-- @block
SELECT COUNT(*) AS row_count
FROM edgar_financial_data_concepts;
-- @block
SHOW TABLE STATUS LIKE 'edgar_financial_data_concepts';
-- @block
-- Get all DISTINCT Cik-Ticker pairs that have a non-null Ddate in edgar_financial_data_concepts
SELECT DISTINCT Cik,
    Ticker
FROM edgar_financial_data_concepts
WHERE Ddate IS NOT NULL;
-- @block
SELECT DISTINCT Adsh,
    FilingType,
    PeriodEnd,
    FiscalPeriod,
    BatchTag,
    FiscalYear
FROM edgar_financial_data_concepts
WHERE Cik = 1750
ORDER BY PeriodEnd DESC;
-- @block
SELECT *
FROM edgar_financial_data_concepts
WHERE Cik = 1002910
    AND Adsh = '0001002910-25-000129';
-- @block
WITH ranked AS (
    SELECT Adsh,
        FilingType,
        PeriodEnd,
        FiscalPeriod,
        BatchTag,
        FiscalYear,
        ROW_NUMBER() OVER (
            PARTITION BY Adsh
            ORDER BY COALESCE(PeriodEnd, '1900-01-01') DESC
        ) AS rn
    FROM edgar_financial_data_concepts
    WHERE Cik = 1046102
        AND Ticker = 'RBA'
        AND FilingType IN (
            '10-Q',
            '10-K',
            '20-F',
            '20-F/A',
            '40-F',
            '40-F/A'
        )
)
SELECT Adsh,
    FilingType,
    PeriodEnd,
    FiscalPeriod,
    BatchTag,
    FiscalYear
FROM ranked
WHERE rn = 1
ORDER BY PeriodEnd DESC;
-- @block
SELECT *
FROM edgar_financial_data_concepts
WHERE Adsh = '0000002488-25-000166'
    AND Concept = 'RevenueFromContractWithCustomerExcludingAssessedTax';