import sys
import os
import json

# Add the src directory to Python path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_loaders.sec_api_loaders.missing_filings_utils import (
    get_missing_sec_filings,
    get_missing_sec_filings_with_inferred_metadata,
)


def test_missing_filings_and_format():
    # Fetch known filings (ADSH) from the database for ticker FDX
    from data_loaders.fetch_known_adsh import fetch_known_adsh_for_ticker

    # ticker = "FDX"
    # cik = "1048911"
    ticker = "AIR"
    cik = "1750"
    known_adsh = fetch_known_adsh_for_ticker(ticker)
    # Print each known ADSH, one per line
    print("KNOWN ADSH ENTRIES:")
    for entry in known_adsh:
        print(entry)
    print("END OF KNOWN ADSH\n")
    # Get missing filings since 2017
    missing_result = get_missing_sec_filings(
        cik, known_adsh, min_year=2015, ticker=ticker, verbose=False
    )
    missing_10q = missing_result.get("missing_10q", [])
    missing_10k = missing_result.get("missing_10k", [])
    # Print each missing 10-Q as a JSON object, one per line
    for entry in missing_10q:
        print(json.dumps(entry, ensure_ascii=False))
    # Print each missing 10-K as a JSON object, one per line
    for entry in missing_10k:
        print(json.dumps(entry, ensure_ascii=False))
    print("============================")
    print("result_with_inffered_data")
    result_with_inffered_data = get_missing_sec_filings_with_inferred_metadata(
        cik=cik, ticker=ticker, min_year=2015, verbose=False
    )
    for entry in result_with_inffered_data.get("missing_10q", []):
        print(json.dumps(entry, ensure_ascii=False))
    # Print each missing 10-K as a JSON object, one per line
    for entry in result_with_inffered_data.get("missing_10k", []):
        print(json.dumps(entry, ensure_ascii=False))


if __name__ == "__main__":
    test_missing_filings_and_format()
