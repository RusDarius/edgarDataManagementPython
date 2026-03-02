from db.connection_provider import get_mysql_connection
from db.connection_credentials import BASE_DB_CONFIG


def get_distinct_cik_ticker_pairs_with_secondary() -> list[dict[str, object]]:
    """
    Returns a list of dicts: {"cik": str, "ticker": str, "secondary_tickers": list[str]}
    For each CIK, finds all tickers. The first ticker is 'ticker', others are in 'secondary_tickers'.
    """
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT Cik, Ticker FROM edgar_financial_data_concepts WHERE Ticker IS NOT NULL"
            )
            results = cursor.fetchall()
    finally:
        conn.close()
    cik_map = {}
    for row in results:
        cik, ticker = map(str, row)
        if cik not in cik_map:
            cik_map[cik] = {"cik": cik, "ticker": ticker, "secondary_tickers": []}
        else:
            if (
                ticker != cik_map[cik]["ticker"]
                and ticker not in cik_map[cik]["secondary_tickers"]
            ):
                cik_map[cik]["secondary_tickers"].append(ticker)
    return list(cik_map.values())


def batch_tag_exists(batch_tag):
    try:
        conn = get_mysql_connection(**BASE_DB_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM edgar_financial_data_concepts WHERE BatchTag = %s LIMIT 1",
                (batch_tag,),
            )
            exists = cursor.fetchone() is not None
    finally:
        conn.close()
    return exists


def get_edgar_financial_data_concepts_adshs_sorted_periodend(cik, ticker):
    try:
        conn = get_mysql_connection(**BASE_DB_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute(
                """
                WITH ranked AS (
                    SELECT
                        Adsh,
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
                    WHERE Cik = %s AND Ticker = %s
                    AND FilingType IN ('10-Q', '10-K', '20-F', '20-F/A', '40-F', '40-F/A')
                )
                SELECT Adsh, FilingType, PeriodEnd, FiscalPeriod, BatchTag, FiscalYear
                FROM ranked
                WHERE rn = 1
                ORDER BY PeriodEnd DESC;
                """,
                (cik, ticker),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return rows


def update_fiscal_period_for_entries(cik, ticker, adsh, new_fiscal_period):
    """
    Update FiscalPeriod for all entries matching cik, ticker, and adsh.
    Args:
        cik (int or str): CIK value
        ticker (str): Ticker value
        adsh (str): Adsh value
        new_fiscal_period (str): New FiscalPeriod to set
    Returns:
        int: Number of rows updated
    """
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE edgar_financial_data_concepts
                SET FiscalPeriod = %s
                WHERE Cik = %s AND Ticker = %s AND Adsh = %s
                """,
                (new_fiscal_period, cik, ticker, adsh),
            )
            conn.commit()
            return cursor.rowcount
    finally:
        conn.close()
