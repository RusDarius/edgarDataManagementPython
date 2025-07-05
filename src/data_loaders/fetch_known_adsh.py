from typing import List, Dict, Any
from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection


def fetch_known_adsh_for_ticker(ticker: str) -> List[str]:
    """
    Fetch the list of known ADSH (accession numbers) for a given ticker from the database.
    Returns the latest entry per period (Q1, Q2, Q3, Q4, FY) and filing type (10-Q, 10-K, etc).
    """
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    cursor = conn.cursor(
        dictionary=True
    )  # Use dictionary cursor to get results as dictionaries
    query = """
        WITH LatestEntries AS (
            SELECT FiscalYear,
                   FiscalPeriod,
                   FilingType,
                   Ddate,
                   PeriodEnd,
                   BatchTag,
                   Adsh,
                   ROW_NUMBER() OVER (
                       PARTITION BY FiscalYear, FiscalPeriod, FilingType
                       ORDER BY Ddate DESC, BatchTag DESC
                   ) as rn
            FROM edgar_financial_data_concepts
            WHERE Ticker = %s
        )
        SELECT Adsh AS LatestAccessionNumber
        FROM LatestEntries
        WHERE rn = 1
        ORDER BY FiscalYear DESC,
            CASE
                WHEN FiscalPeriod = 'FY' THEN 5
                WHEN FiscalPeriod = 'Q4' THEN 4
                WHEN FiscalPeriod = 'Q3' THEN 3
                WHEN FiscalPeriod = 'Q2' THEN 2
                WHEN FiscalPeriod = 'Q1' THEN 1
                ELSE 0
            END DESC;
    """
    cursor.execute(query, (ticker,))
    # Access the column by name
    results = [
        str(row["LatestAccessionNumber"])
        for row in cursor.fetchall()
        if isinstance(row, dict)
    ]
    cursor.close()
    conn.close()
    return results


def fetch_known_adsh_with_metadata_for_ticker(ticker: str) -> List[Dict[str, Any]]:
    """
    Fetch the list of known ADSH with detailed metadata for a given ticker from the database.
    Returns the latest entry per period (Q1, Q2, Q3, Q4, FY) and filing type (10-Q, 10-K, etc)
    with complete metadata for inferring missing filing details.
    """
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    query = """
        WITH LatestEntries AS (
            SELECT FiscalYear,
                   FiscalPeriod,
                   FilingType,
                   Ddate,
                   PeriodEnd,
                   BatchTag,
                   Adsh,
                   ROW_NUMBER() OVER (
                       PARTITION BY FiscalYear, FiscalPeriod, FilingType
                       ORDER BY Ddate DESC, BatchTag DESC
                   ) as rn
            FROM edgar_financial_data_concepts
            WHERE Ticker = %s
        )
        SELECT FiscalYear,
               FiscalPeriod, 
               FilingType,
               Ddate,
               PeriodEnd,
               BatchTag,
               Adsh
        FROM LatestEntries
        WHERE rn = 1
        ORDER BY Ddate DESC;  -- Order by actual date for chronological sorting
    """

    cursor.execute(query, (ticker,))
    results = [
        {k: v for k, v in row.items()} if isinstance(row, dict) else {}
        for row in cursor.fetchall()
    ]
    cursor.close()
    conn.close()
    return results
