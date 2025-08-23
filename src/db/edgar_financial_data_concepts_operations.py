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
