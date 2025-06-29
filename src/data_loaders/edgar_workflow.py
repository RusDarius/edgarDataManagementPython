import time
from data_loaders.api_client import ApiClient
from data_loaders.edgar_processing_utils import extract_guidance_from_text
from data_loaders.sec_cik_ticker_mapping import (
    fetch_sec_cik_tickers,
    insert_mappings_to_db,
)
from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection

USER_AGENT = "Barnnabass daniOO7XbX@gmail.com"
EDGAR_API_CLIENT = ApiClient(user_agent=USER_AGENT)


def run_edgar_workflow():
    """Example workflow: fetch and print Apple Inc. submissions from the SEC."""
    # Example: Fetch and print Apple Inc. submissions
    cik = "0000320193"  # Apple Inc.
    data = EDGAR_API_CLIENT.fetch_company_submissions(cik)
    print(data)


def run_sec_cik_ticker_mapping_workflow():
    """Fetch and load SEC CIK-ticker mappings into the database."""
    mappings = fetch_sec_cik_tickers(USER_AGENT)

    # Use the generic connection provider for the DB connection
    with get_mysql_connection(**BASE_DB_CONFIG) as conn:
        insert_mappings_to_db(mappings, conn)

    print("SEC CIK-ticker mappings loaded into database.")


def load_8k_filings_for_cik(cik, year):
    """Return a list of 8-K filings for a given CIK and year."""
    data = EDGAR_API_CLIENT.fetch_company_submissions(cik)
    filings = []
    for i, form in enumerate(data["filings"]["recent"]["form"]):
        if form == "8-K":
            filing_date = data["filings"]["recent"]["filingDate"][i]
            if str(filing_date).startswith(str(year)):
                accession = data["filings"]["recent"]["accessionNumber"][i]
                primary_doc = data["filings"]["recent"]["primaryDocument"][i]
                filings.append(
                    {
                        "accession": accession,
                        "filing_date": filing_date,
                        "primary_doc": primary_doc,
                    }
                )
    return filings


def run_guidance_extraction_workflow(cik, year):
    """Extract and print guidance lines from all 8-K filings for a CIK and year."""
    filings = load_8k_filings_for_cik(cik, year)
    for filing in filings:
        print(f"Processing 8-K: {filing['accession']} ({filing['filing_date']})")
        time.sleep(1)  # be polite
        text = EDGAR_API_CLIENT.fetch_8k_document(
            cik, filing["accession"], filing["primary_doc"]
        )
        guidance_lines = extract_guidance_from_text(text)
        if guidance_lines:
            print("Possible guidance found:")
            for line in guidance_lines:
                print(line)
        else:
            print("No guidance found in this 8-K.")
