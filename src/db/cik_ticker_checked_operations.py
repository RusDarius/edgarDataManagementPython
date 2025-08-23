from db.connection_provider import get_mysql_connection
from db.connection_credentials import BASE_DB_CONFIG


def get_all_cik_ticker_checked():
    """
    Fetch all rows from the cik_ticker_checked table.
    Returns a list of tuples, each representing a row.
    """
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM cik_ticker_checked")
            results = cursor.fetchall()
    finally:
        conn.close()
    return results
