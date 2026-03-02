# Query the database for a specific CIK and return its ticker.
def read_sec_cik_ticker_for_cik(conn, cik):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT Ticker FROM sec_cik_tickers_mapping WHERE Cik = %s", (cik,))
    row = cursor.fetchone()
    return row["Ticker"] if row else None


# Load a mapping of CIK to ticker from the database.
def read_all_sec_cik_ticker_map(conn):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT Cik, Ticker FROM sec_cik_tickers_mapping")
    return {row["Cik"]: row["Ticker"] for row in cursor.fetchall()}
