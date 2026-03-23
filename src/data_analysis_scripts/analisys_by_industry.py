from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection
from generic_utils.log_to_files_util import log_to_file

# -- Annual revenue trend from 2020 to last completed fiscal year (one row per year, annual filings only)
# -- This version targets USD-equivalent values by:
# -- 1) preferring Unit='USD',
# -- 2) normalizing common scaled-USD units (thousands/millions/billions),
# -- 3) falling back to the original value/unit when deterministic USD conversion is not available.
ANNUAL_REVENUE_TREND_QUERY = """
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
    WHERE Cik = %s -- or %(cik)s
        AND Ticker = %s -- or %(ticker)s
        AND FiscalYear >= %s -- or %(start_year)s
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
    PeriodEnd,
    Unit AS SourceUnit,
    CASE
        WHEN ValueUSD IS NOT NULL THEN 1
        ELSE 0
    END AS IsUSDEquivalent
FROM ranked
WHERE rn = 1
ORDER BY FiscalYear;
"""

TICKER_CIK_LOOKUP_QUERY = """
SELECT Cik, Ticker
FROM sec_cik_tickers_mapping
WHERE UPPER(Ticker) = UPPER(%s)
LIMIT 1
"""

TICKER_CIK_LOOKUP_SECONDARY_QUERY = """
SELECT m.Cik, m.Ticker
FROM sec_cik_tickers_mapping m
JOIN JSON_TABLE(
    m.SecondaryTickers,
    '$[*]' COLUMNS (secondary_ticker VARCHAR(16) PATH '$')
) jt
    ON UPPER(jt.secondary_ticker) = UPPER(%s)
LIMIT 1
"""

FULL_YEAR_FILING_EXISTS_QUERY = """
SELECT 1
FROM edgar_financial_data_concepts
WHERE Cik = %s
    AND Ticker = %s
    AND FiscalYear >= %s
    AND FiscalYear < YEAR(CURDATE())
    AND Qtrs = 4
    AND FilingType IN ('10-K', '20-F', '40-F')
    AND (Segment IS NULL OR Segment = '')
LIMIT 1
"""

CONCEPT_EXISTS_QUERY = """
SELECT 1
FROM edgar_financial_data_concepts
WHERE Cik = %s
    AND Ticker = %s
    AND FiscalYear >= %s
    AND FiscalYear < YEAR(CURDATE())
    AND Qtrs = 4
    AND FilingType IN ('10-K', '20-F', '40-F')
    AND Concept IN (
        'RevenueFromContractWithCustomerExcludingAssessedTax',
        'Revenues',
        'Revenue',
        'SalesRevenueNet',
        'RevenueFromContractWithCustomerIncludingAssessedTax',
        'RevenueFromRenderingOfServices'
    )
    AND (Segment IS NULL OR Segment = '')
LIMIT 1
"""


def _normalize_ticker(ticker: str) -> str:
    ticker_str = str(ticker).strip().upper()
    # Convert formats like 'BRK.A' to 'BRK-A'
    if "." in ticker_str:
        parts = ticker_str.split(".")
        if len(parts) == 2 and parts[1]:
            ticker_str = f"{parts[0]}-{parts[1]}"
    return ticker_str


def _classify_empty_reason(cursor, cik, ticker, start_year: int) -> str:
    cursor.execute(FULL_YEAR_FILING_EXISTS_QUERY, (cik, ticker, start_year))
    has_full_year_filing = cursor.fetchone() is not None
    if not has_full_year_filing:
        return "Other: no full-year filing rows found for selected period"

    cursor.execute(CONCEPT_EXISTS_QUERY, (cik, ticker, start_year))
    has_target_concept = cursor.fetchone() is not None
    if not has_target_concept:
        return "Concept not found"

    return "Other: no annual revenue rows returned"


def analyze_revenues_by_industry(tickers: list[str], start_year: int = 2020) -> dict:
    """
    Run the annual revenue trend query for each ticker and return a result object per ticker.

    Returns a dictionary keyed by ticker with either:
    - rows populated for success, or
    - empty rows and a reason (Ticker not found / Concept not found / Other).
    """
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    results_by_ticker = {}

    try:
        with conn.cursor(dictionary=True) as cursor:
            for raw_ticker in tickers:
                ticker = _normalize_ticker(raw_ticker)
                result_obj = {
                    "ticker": ticker,
                    "rows": [],
                    "reason": None,
                    "cik": None,
                }

                cursor.execute(TICKER_CIK_LOOKUP_QUERY, (ticker,))
                ticker_row = cursor.fetchone()

                if not ticker_row:
                    cursor.execute(TICKER_CIK_LOOKUP_SECONDARY_QUERY, (ticker,))
                    ticker_row = cursor.fetchone()

                if not ticker_row:
                    result_obj["reason"] = "Ticker not found"
                    results_by_ticker[ticker] = result_obj
                    continue

                cik = ticker_row["Cik"]
                result_obj["cik"] = str(cik)

                cursor.execute(ANNUAL_REVENUE_TREND_QUERY, (cik, ticker, start_year))
                annual_rows = cursor.fetchall()

                if annual_rows:
                    result_obj["rows"] = annual_rows
                else:
                    result_obj["reason"] = _classify_empty_reason(
                        cursor, cik, ticker, start_year
                    )

                results_by_ticker[ticker] = result_obj
    finally:
        conn.close()

    return results_by_ticker


def process_revenues_for_tickers(tickers: list[str], start_year: int = 2020):
    """
    Run the revenue analysis and log results for each ticker to logs/revenues_for_tickers_v1.log.
    """
    log_file = (
        r"D:\FinanceProjects\edgarDataManagementPython\logs\revenues_for_tickers_v1.log"
    )
    analysis_results = analyze_revenues_by_industry(tickers, start_year)
    yearly_revenue_by_ticker: dict[int, dict[str, float]] = {}

    for ticker, result in analysis_results.items():
        log_revenues_for_tickers(ticker, result, log_file)

        if result["reason"]:
            continue

        for row in result["rows"]:
            year = row["FiscalYear"]
            annual_revenue = row["AnnualRevenueUSD"]
            if annual_revenue is None:
                continue

            year_bucket = yearly_revenue_by_ticker.setdefault(year, {})
            year_bucket[ticker] = float(annual_revenue)

    print("\nRevenue share by year (participants with available annual revenue only):")
    for year in sorted(yearly_revenue_by_ticker.keys()):
        ticker_revenues = yearly_revenue_by_ticker[year]
        total_revenue = sum(ticker_revenues.values())

        print(f"Year {year} | Total Revenue: {total_revenue:,.2f}")
        if total_revenue == 0:
            for ticker in sorted(ticker_revenues.keys()):
                print(f"  {ticker}: 0.00% (Revenue: {ticker_revenues[ticker]:,.2f})")
            continue

        for ticker, revenue in sorted(
            ticker_revenues.items(), key=lambda item: item[1], reverse=True
        ):
            share_pct = (revenue / total_revenue) * 100
            print(f"  {ticker}: {share_pct:.2f}% (Revenue: {revenue:,.2f})")


def log_revenues_for_tickers(ticker: str, result: dict, log_file: str):
    log_to_file(log_file, f"Ticker: {ticker}")
    if result["reason"]:
        log_to_file(log_file, f"  Reason: {result['reason']}")
    else:
        for row in result["rows"]:
            log_to_file(
                log_file,
                f"  Year: {row['FiscalYear']}, Concept: {row['Concept']}, Revenue: {row['AnnualRevenueUSD']}, PeriodEnd: {row['PeriodEnd']}",
            )
