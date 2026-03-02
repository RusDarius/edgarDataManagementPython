from db.connection_provider import get_mysql_connection
from db.connection_credentials import BASE_DB_CONFIG


def get_all_cik_ticker_checked(limit: int = None, checked: bool = None):
    """
    Fetch rows from the `cik_ticker_checked` table.

    Args:
        limit: Optional number of rows to return. If None, returns all matching rows.
        checked: If True/False, filter rows by the `Checked` column. If None, no filter applied.

    Returns:
        List of tuples `(Cik, Ticker, Checked)`.
    """
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            sql = "SELECT Cik, Ticker, Checked FROM cik_ticker_checked"
            params = []
            if checked is not None:
                sql += " WHERE Checked = %s"
                params.append(1 if checked else 0)
            if limit is not None and isinstance(limit, int) and limit > 0:
                sql += " LIMIT %s"
                params.append(limit)

            cursor.execute(sql, tuple(params) if params else None)
            results = cursor.fetchall()
    finally:
        conn.close()
    return results


def mark_cik_tickers_checked(cik, checked=True):
    """
    Mark a CIK/ticker row as checked (or unchecked) in the cik_ticker_checked table.

    Returns:
        Number of rows updated.
    """
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE cik_ticker_checked
                SET Checked = %s
                WHERE Cik = %s
                """,
                (checked, cik),
            )
            updated_rows = cursor.rowcount
        conn.commit()
        return updated_rows
    finally:
        conn.close()
