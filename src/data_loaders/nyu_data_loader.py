import csv
from db.connection_provider import get_mysql_connection
from db.connection_credentials import BASE_DB_CONFIG


# Load NYU industry grouping data from a CSV file into the database.
def load_nyu_industry_grouping(csv_path):
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    cursor = conn.cursor()
    insert_sql = """
        INSERT INTO nyu_tickers_industry_grouping
        (CompanyName, ExchangeTicker, IndustryGroup, PrimarySector, SicCode, Country, BroadGroup, SubGroup)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        data = []
        for row in reader:
            # Clean and convert SIC code to int if possible, else None
            sic_code = row["SIC Code"].strip()
            sic_code = int(sic_code) if sic_code.isdigit() else None
            data.append(
                (
                    row["Company Name"].strip(),
                    row["Exchange:Ticker"].strip(),
                    row["Industry Group"].strip(),
                    row["Primary Sector"].strip(),
                    sic_code,
                    row["Country"].strip(),
                    row["Broad Group"].strip(),
                    row["Sub Group"].strip(),
                )
            )
    cursor.executemany(insert_sql, data)
    conn.commit()
    cursor.close()
    conn.close()
    print(f"Loaded {len(data)} rows into nyu_tickers_industry_grouping.")
