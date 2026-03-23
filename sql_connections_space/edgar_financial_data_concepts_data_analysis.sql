-- pass
-- @block
-- All entries for a given Adsh + Cik + Ticker combination
SELECT *
FROM edgar_financial_data_concepts
WHERE Adsh = '0000913290-24-000002' -- replace with target Adsh
    AND Cik = 913290 -- replace with target CIK
    AND Ticker = 'FRO' -- replace with target ticker
ORDER BY PeriodEnd DESC,
    Ddate DESC,
    Concept;
-- @block
SELECT DISTINCT Segment
FROM edgar_financial_data_concepts
WHERE Cik = 1048911
    AND Ticker = 'FDX'
    AND FilingType IN ('10-K', '20-F', '40-F')
    AND Adsh = '0001048911-25-000011'
ORDER BY Segment;
-- @block
-- All annual filing rows (10-K / 20-F / 40-F) for a given CIK + ticker
SELECT *
FROM edgar_financial_data_concepts
WHERE Cik = 1048911 -- replace with target CIK
    AND Ticker = 'FDX' -- replace with target ticker
    AND FilingType IN ('10-K', '20-F', '40-F')
    AND Adsh = '0001048911-25-000011'
ORDER BY FiscalYear DESC,
    PeriodEnd DESC,
    Ddate DESC,
    BatchTag DESC;
-- @block
-- Simplified: 2024 revenue for a CIK + ticker (latest row per period/concept)
WITH ranked AS (
    SELECT FiscalPeriod,
        Concept,
        Value,
        PeriodEnd,
        ROW_NUMBER() OVER (
            PARTITION BY FiscalPeriod,
            Concept
            ORDER BY Ddate DESC,
                BatchTag DESC
        ) AS rn
    FROM edgar_financial_data_concepts
    WHERE Cik = 1048911
        AND Ticker = 'FDX'
        AND FiscalYear = 2025
        AND Concept IN (
            'RevenueFromContractWithCustomerExcludingAssessedTax',
            'SalesRevenueNet',
            'Revenues',
            'Revenue'
        )
        AND (
            Segment IS NULL
            OR Segment = ''
        )
        AND Qtrs IN (1, 4)
        AND FilingType IN ('10-Q', '10-K')
)
SELECT FiscalPeriod,
    Concept,
    Value,
    PeriodEnd
FROM ranked
WHERE rn = 1
ORDER BY PeriodEnd;
-- @block
-- QID = 1
-- Annual revenue trend from 2020 to last completed fiscal year (one row per year, full-year only)
-- This version targets USD-equivalent values by:
-- 1) preferring Unit='USD',
-- 2) normalizing common scaled-USD units (thousands/millions/billions),
-- 3) falling back to the original value/unit when deterministic USD conversion is not available.
WITH normalized AS (
    SELECT FiscalYear,
        FiscalPeriod,
        Concept,
        Value,
        Unit,
        PeriodEnd,
        Ddate,
        BatchTag,
        CASE
            WHEN UPPER(Unit) = 'USD' THEN Value
            WHEN UPPER(REPLACE(Unit, ' ', '')) IN ('USDK', 'USDTH', 'USDTHOUSANDS') THEN Value * 1000
            WHEN UPPER(REPLACE(Unit, ' ', '')) IN ('USDM', 'USDMN', 'USDMILLIONS') THEN Value * 1000000
            WHEN UPPER(REPLACE(Unit, ' ', '')) IN ('USDB', 'USDBN', 'USDBILLIONS') THEN Value * 1000000000
            ELSE NULL
        END AS ValueUSD,
        CASE
            WHEN UPPER(Unit) = 'USD' THEN 1
            WHEN UPPER(REPLACE(Unit, ' ', '')) IN ('USDK', 'USDTH', 'USDTHOUSANDS') THEN 2
            WHEN UPPER(REPLACE(Unit, ' ', '')) IN ('USDM', 'USDMN', 'USDMILLIONS') THEN 2
            WHEN UPPER(REPLACE(Unit, ' ', '')) IN ('USDB', 'USDBN', 'USDBILLIONS') THEN 2
            ELSE 9
        END AS UnitPriority
    FROM edgar_financial_data_concepts
    WHERE Cik = 913290 -- or %(cik)s
        AND Ticker = 'FRO' -- or %(ticker)s
        AND FiscalYear >= 2015 -- or %(start_year)s
        AND FiscalYear < YEAR(CURDATE())
        AND Qtrs = 4
        AND FilingType IN ('10-K', '20-F', '40-F')
        AND Concept IN (
            'RevenueFromContractWithCustomerExcludingAssessedTax',
            'SalesRevenueNet',
            'Revenues',
            'Revenue',
            'RevenueFromContractWithCustomerIncludingAssessedTax',
            'RevenueFromRenderingOfServices'
        )
        AND (
            Segment IS NULL
            OR Segment = ''
        )
),
ranked AS (
    SELECT FiscalYear,
        FiscalPeriod,
        Concept,
        Value AS AnnualRevenueRaw,
        Unit,
        ValueUSD,
        PeriodEnd,
        ROW_NUMBER() OVER (
            PARTITION BY FiscalYear
            ORDER BY CASE
                    WHEN FiscalPeriod = 'FY' THEN 1
                    WHEN FiscalPeriod = 'Q4' THEN 2
                    WHEN FiscalPeriod = 'Q3' THEN 3
                    WHEN FiscalPeriod = 'Q2' THEN 4
                    WHEN FiscalPeriod = 'Q1' THEN 5
                    ELSE 6
                END,
                CASE
                    WHEN Concept = 'RevenueFromContractWithCustomerExcludingAssessedTax' THEN 1
                    WHEN Concept = 'SalesRevenueNet' THEN 2
                    WHEN Concept = 'Revenues' THEN 3
                    WHEN Concept = 'Revenue' THEN 4
                    WHEN Concept = 'RevenueFromContractWithCustomerIncludingAssessedTax' THEN 5
                    WHEN Concept = 'RevenueFromRenderingOfServices' THEN 6
                    ELSE 7
                END,
                UnitPriority,
                Ddate DESC,
                BatchTag DESC
        ) rn
    FROM normalized
)
SELECT FiscalYear,
    FiscalPeriod,
    Concept,
    ValueUSD AS AnnualRevenueUSD,
    AnnualRevenueRaw,
    Unit AS SourceUnit,
    CASE
        WHEN ValueUSD IS NOT NULL THEN 1
        ELSE 0
    END AS IsUSDEquivalent,
    PeriodEnd
FROM ranked
WHERE rn = 1
ORDER BY FiscalYear;
-- @block
-- Quarterly revenue trend since 2020, reconstructing Q4 if not reported (SEC logic)
WITH base AS (
    SELECT FiscalYear,
        FiscalPeriod,
        Concept,
        Value,
        PeriodEnd,
        Ddate,
        ROW_NUMBER() OVER (
            PARTITION BY FiscalYear,
            FiscalPeriod
            ORDER BY CASE
                    WHEN Concept = 'RevenueFromContractWithCustomerExcludingAssessedTax' THEN 1
                    WHEN Concept = 'SalesRevenueNet' THEN 2
                    WHEN Concept = 'Revenues' THEN 3
                    WHEN Concept = 'Revenue' THEN 4
                    WHEN Concept = 'RevenueFromContractWithCustomerIncludingAssessedTax' THEN 5
                    WHEN Concept = 'RevenueFromRenderingOfServices' THEN 6
                    ELSE 7
                END,
                Ddate DESC,
                BatchTag DESC
        ) rn
    FROM edgar_financial_data_concepts
    WHERE Cik = 913290 -- or %(cik)s
        AND Ticker = 'FRO' -- or %(ticker)s
        AND FiscalYear >= 2020
        AND FiscalPeriod IN ('Q1', 'Q2', 'Q3', 'Q4', 'FY')
        AND Qtrs IN (1, 4)
        AND FilingType IN ('10-Q', '10-K')
        AND Concept IN (
            'RevenueFromContractWithCustomerExcludingAssessedTax',
            'SalesRevenueNet',
            'Revenues',
            'Revenue',
            'RevenueFromContractWithCustomerIncludingAssessedTax',
            'RevenueFromRenderingOfServices'
        )
        AND (
            Segment IS NULL
            OR Segment = ''
        )
),
quarters AS (
    SELECT *
    FROM base
    WHERE rn = 1
),
fy AS (
    SELECT *
    FROM quarters
    WHERE FiscalPeriod = 'FY'
),
q AS (
    SELECT *
    FROM quarters
    WHERE FiscalPeriod IN ('Q1', 'Q2', 'Q3')
),
q4_reported AS (
    SELECT *
    FROM quarters
    WHERE FiscalPeriod = 'Q4'
),
q4_computed AS (
    SELECT fy.FiscalYear,
        'Q4' FiscalPeriod,
        fy.Concept,
        fy.Value - IFNULL(q1.Value, 0) - IFNULL(q2.Value, 0) - IFNULL(q3.Value, 0) Value,
        fy.PeriodEnd,
        fy.Ddate,
        'computed' Q4Source
    FROM fy
        JOIN q q1 ON q1.FiscalYear = fy.FiscalYear
        AND q1.FiscalPeriod = 'Q1'
        JOIN q q2 ON q2.FiscalYear = fy.FiscalYear
        AND q2.FiscalPeriod = 'Q2'
        JOIN q q3 ON q3.FiscalYear = fy.FiscalYear
        AND q3.FiscalPeriod = 'Q3'
    WHERE NOT EXISTS (
            SELECT 1
            FROM q4_reported qr
            WHERE qr.FiscalYear = fy.FiscalYear
        )
),
all_quarters AS (
    SELECT FiscalYear,
        FiscalPeriod,
        Concept,
        Value,
        PeriodEnd,
        Ddate,
        'reported' Q4Source
    FROM q
    UNION ALL
    SELECT FiscalYear,
        FiscalPeriod,
        Concept,
        Value,
        PeriodEnd,
        Ddate,
        'reported' Q4Source
    FROM q4_reported
    UNION ALL
    SELECT FiscalYear,
        FiscalPeriod,
        Concept,
        Value,
        PeriodEnd,
        Ddate,
        Q4Source
    FROM q4_computed
)
SELECT FiscalYear,
    FiscalPeriod,
    Concept,
    Value AS QuarterlyRevenue,
    PeriodEnd,
    Q4Source
FROM all_quarters
WHERE FiscalPeriod IN ('Q1', 'Q2', 'Q3', 'Q4')
ORDER BY FiscalYear,
    FIELD(FiscalPeriod, 'Q1', 'Q2', 'Q3', 'Q4');