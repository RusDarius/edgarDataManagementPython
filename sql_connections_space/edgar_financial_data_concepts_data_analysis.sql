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
-- Annual revenue trend from 2020 to last completed fiscal year (one row per year, full-year only)
WITH ranked AS (
    SELECT FiscalYear,
        Concept,
        Value,
        PeriodEnd,
        ROW_NUMBER() OVER (
            PARTITION BY FiscalYear
            ORDER BY CASE
                    WHEN Concept = 'RevenueFromContractWithCustomerExcludingAssessedTax' THEN 1
                    WHEN Concept = 'SalesRevenueNet' THEN 2
                    WHEN Concept = 'Revenues' THEN 3
                    WHEN Concept = 'Revenue' THEN 4
                    ELSE 5
                END,
                Ddate DESC,
                BatchTag DESC
        ) rn
    FROM edgar_financial_data_concepts
    WHERE Cik = 1860160 -- or %(cik)s
        AND Ticker = 'FLY' -- or %(ticker)s
        AND FiscalYear >= 2020 -- or %(start_year)s
        AND FiscalYear < YEAR(CURDATE())
        AND FiscalPeriod = 'FY'
        AND Qtrs = 4
        AND FilingType IN ('10-K', '20-F', '40-F')
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
)
SELECT FiscalYear,
    Concept,
    Value AS AnnualRevenue,
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
                    ELSE 5
                END,
                Ddate DESC,
                BatchTag DESC
        ) rn
    FROM edgar_financial_data_concepts
    WHERE Cik = 1860160 -- or %(cik)s
        AND Ticker = 'FLY' -- or %(ticker)s
        AND FiscalYear >= 2020
        AND FiscalPeriod IN ('Q1', 'Q2', 'Q3', 'Q4', 'FY')
        AND Qtrs IN (1, 4)
        AND FilingType IN ('10-Q', '10-K')
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