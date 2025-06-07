from edgar.api_client import ApiClient
from edgar.sec_cik_ticker_mapping import fetch_sec_cik_tickers, insert_mappings_to_db
from db.connection_provider import get_mysql_connection


def run_edgar_workflow():
    # Example: Fetch and print Apple Inc. submissions
    user_agent = "Barnnabass daniOO7XbX@gmail.com"
    cik = "0000320193"  # Apple Inc.
    client = ApiClient(user_agent=user_agent)
    data = client.fetch_company_submissions(cik)
    print(data)


def run_sec_cik_ticker_mapping_workflow():
    user_agent = "Barnnabass daniOO7XbX@gmail.com"
    mappings = fetch_sec_cik_tickers(user_agent)

    # Use the generic connection provider for the DB connection
    with get_mysql_connection(
        host="localhost",
        port=3306,
        user="root",
        password="!@#4QWEr",
        database="finance_manager_iteration_1",
    ) as conn:
        insert_mappings_to_db(mappings, conn)

    print("SEC CIK-ticker mappings loaded into database.")
