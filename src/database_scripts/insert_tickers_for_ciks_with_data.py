import json
import os
from db.connection_provider import get_mysql_connection
from db.connection_credentials import BASE_DB_CONFIG

JSON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "savedData",
    "all_cik_ticker_pairs_with_financial_concepts_data_v1.json",
)

TABLE_NAME = "cik_ticker_checked"
SOURCE_TABLE = "sec_cik_tickers_mapping"


def insert_all_cik_ticker_checked_from_mapping(default_checked=False):
    """
    Insert all CIK/ticker pairs from sec_cik_tickers_mapping with Checked default.
    """
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            sql = f"""
            INSERT IGNORE INTO {TABLE_NAME} (Cik, Ticker, Checked)
            SELECT Cik, Ticker, %s
            FROM {SOURCE_TABLE}
            """
            cursor.execute(sql, (default_checked,))
        conn.commit()
        print(f"Inserted rows from {SOURCE_TABLE} into {TABLE_NAME}.")
    finally:
        conn.close()


def main():
    # Load the JSON file
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Prepare insert tuples (Cik, Ticker, Checked)
    # If Cik is not present in the JSON, this will need to be looked up or provided elsewhere
    # Here, we assume Cik is not available and only insert Ticker (will fail if Cik is required)
    # You may need to join with another table to get Cik for each Ticker
    insert_rows = []
    for entry in data:
        cik = entry.get("Cik")
        ticker = entry.get("Ticker")
        if cik is None or ticker is None:
            print(f"Skipping entry with missing Cik or Ticker: {entry}")
            continue
        insert_rows.append((cik, ticker, False))

    if not insert_rows:
        print("No valid entries to insert.")
        return

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            sql = f"""
            INSERT IGNORE INTO {TABLE_NAME} (Cik, Ticker, Checked)
            VALUES (%s, %s, %s)
            """
            cursor.executemany(sql, insert_rows)
        conn.commit()
        print(f"Inserted {len(insert_rows)} rows into {TABLE_NAME}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
