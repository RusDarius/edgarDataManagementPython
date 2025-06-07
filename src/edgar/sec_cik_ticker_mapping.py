import requests


def fetch_sec_cik_tickers(user_agent):
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {"User-Agent": user_agent}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


def insert_mappings_to_db(mappings, conn):
    cursor = conn.cursor()
    insert_sql = """
        INSERT INTO sec_cik_tickers_mapping (Cik, Ticker, Title)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            ticker=VALUES(Ticker),
            title=VALUES(Title)
    """
    data = []
    for entry in mappings.values():
        cik = int(entry["cik_str"])
        ticker = entry["ticker"]
        title = entry["title"]
        data.append((cik, ticker, title))
    cursor.executemany(insert_sql, data)
    conn.commit()
    cursor.close()
