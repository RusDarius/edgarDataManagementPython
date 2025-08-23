-- @block
-- Get all DISTINCT Cik-Ticker pairs in the edgar_financial_data_concepts table
SELECT DISTINCT Cik,
    Ticker
FROM edgar_financial_data_concepts;
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
-- Delete all records from edgar_financial_data_concepts where BatchTag starts with "fdx-"
DELETE FROM edgar_financial_data_concepts
WHERE BatchTag LIKE 'fdx-%';
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
WHERE BatchTag = 'acm-20250630.htm_868857';
-- @block
-- Delete records with BatchTag "fdx-20240831.htm" (limited to 1000 records per execution)
DELETE FROM edgar_financial_data_concepts
WHERE BatchTag = 'rusha20250630_10q.htm_1012019'
LIMIT 500000;
-- get all distinct BatchTag values
-- @block
SELECT DISTINCT BatchTag
FROM edgar_financial_data_concepts;
-- get all entries for a cik/ticker for a concept sorted by Ddate and getting the first q or fy statement per fp
-- @block
WITH RankedData AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY BatchTag
            ORDER BY FiscalYear DESC,
                CASE
                    WHEN FiscalPeriod = 'FY' THEN 1
                    ELSE 0
                END DESC,
                FiscalPeriod DESC,
                Ddate DESC
        ) AS row_num
    FROM edgar_financial_data_concepts
    WHERE (
            Concept LIKE 'RevenueFromContractWithCustomerExcludingAssessedTax'
            OR Concept LIKE 'SalesRevenueNet'
            OR Concept LIKE 'Revenues'
            OR Concept LIKE 'Revenue'
        )
        AND (
            Ticker = 'FDX' -- Cik = 1750
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
-- Delete all records with BatchTag "fdx-20230831.htm_1048911"
DELETE FROM edgar_financial_data_concepts
WHERE BatchTag = 'fdx-20250531.htm_1048911';
-- @block
SELECT *
FROM edgar_financial_data_concepts
WHERE Adsh = '0001193125-15-098477';
-- @block
-- Get all distinct FilingType values for ticker FDX and BatchTag 2015q1
SELECT DISTINCT FilingType
FROM edgar_financial_data_concepts
WHERE Ticker = 'FDX';
-- @block
TRUNCATE TABLE edgar_financial_data_concepts;
-- @block
SHOW INDEX
FROM edgar_financial_data_concepts;
-- @block
-- Get all DISTINCT Cik-Ticker pairs that have a non-null Ddate in edgar_financial_data_concepts
SELECT DISTINCT Cik,
    Ticker
FROM edgar_financial_data_concepts
WHERE Ddate IS NOT NULL;