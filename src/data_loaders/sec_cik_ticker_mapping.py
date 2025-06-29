import requests
import json


def fetch_sec_cik_tickers(user_agent):
    """Fetch the SEC CIK-ticker mapping JSON from the SEC website."""
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {"User-Agent": user_agent}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


def insert_mappings_to_db(mappings, conn):
    """Insert or update CIK-ticker mappings in the sec_cik_tickers_mapping table, supporting secondary tickers."""
    cursor = conn.cursor()

    # Ensure the SecondaryTickers field exists (TEXT or JSON)
    cursor.execute("SHOW COLUMNS FROM sec_cik_tickers_mapping LIKE 'SecondaryTickers'")
    if not cursor.fetchone():
        cursor.execute(
            "ALTER TABLE sec_cik_tickers_mapping ADD COLUMN SecondaryTickers TEXT"
        )

    # Build a CIK -> set of tickers mapping
    cik_to_tickers = {}
    cik_to_title = {}

    for entry in mappings.values():
        cik = int(entry["cik_str"])
        ticker = entry["ticker"].upper()
        title = entry["title"]
        if cik not in cik_to_tickers:
            cik_to_tickers[cik] = set()
        cik_to_tickers[cik].add(ticker)
        cik_to_title[cik] = title

    # Prepare data for insert
    data = []
    for cik, tickers in cik_to_tickers.items():
        tickers_list = sorted(tickers)
        main_ticker = tickers_list[0]
        secondary_tickers = json.dumps(tickers_list)  # store as JSON array
        title = cik_to_title[cik]
        data.append((cik, main_ticker, title, secondary_tickers))

    insert_sql = """
        INSERT INTO sec_cik_tickers_mapping (Cik, Ticker, Title, SecondaryTickers)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            Ticker=VALUES(Ticker),
            Title=VALUES(Title),
            SecondaryTickers=VALUES(SecondaryTickers)
    """

    cursor.executemany(insert_sql, data)
    conn.commit()
    cursor.close()
