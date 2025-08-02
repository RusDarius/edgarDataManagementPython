#!/usr/bin/env python3
"""
Example script showing how to use the new SEC EDGAR URL building functions
with the missing filings workflow.
"""

import sys
import os

# Add src to sys.path to allow imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from generic_utils.accept_utf8_encoding import accept_utf8_encoding
from data_loaders.sec_api_loaders.missing_filings_utils import (
    get_missing_sec_filings,
    build_sec_edgar_urls,
    get_all_filing_urls,
)
from data_loaders.fetch_known_adsh import fetch_known_adsh_for_ticker

accept_utf8_encoding()


def demo_missing_filings_with_url_building():
    """Demonstrate using the new URL building functions with missing filings detection."""

    print("🎯 Missing Filings + URL Building Demo")
    print("=" * 60)

    ticker = "FDX"
    cik = "1048911"

    # Step 1: Get known ADSH list
    print(f"\n1️⃣ Getting known filings for {ticker}...")
    known_adsh_list = fetch_known_adsh_for_ticker(ticker)
    print(f"Found {len(known_adsh_list)} known ADSH entries")

    # Step 2: Get missing filings
    print(f"\n2️⃣ Finding missing filings...")
    result = get_missing_sec_filings(
        cik=cik,
        known_adsh_list=known_adsh_list,
        min_year=2017,
        ticker=ticker,
        verbose=True,
    )

    missing_filings = result.get("all_missing", [])
    print(f"Found {len(missing_filings)} missing filings")

    # Step 3: Build URLs for each missing filing
    print(f"\n3️⃣ Building download URLs for missing filings...")
    print("-" * 60)

    for i, filing in enumerate(missing_filings[:3], 1):  # Show first 3
        print(f"\n📋 Missing Filing {i}:")
        print(f"  ADSH: {filing.get('Adsh')}")
        print(f"  Type: {filing.get('FilingType')} {filing.get('FiscalYear')}")
        print(f"  Date: {filing.get('Ddate')}")
        print(f"  BatchTag: {filing.get('BatchTag')}")

        try:
            # Build URLs using the new function
            urls_info = build_sec_edgar_urls(filing)

            print(f"\n  🌐 Download URLs:")
            print(f"    iXBRL: {urls_info['ixbrl_url']}")
            print(f"    XSD: {urls_info['schema_urls']['xsd']}")
            print(f"    DEF: {urls_info['schema_urls']['def_xml']}")
            print(f"    PRE: {urls_info['schema_urls']['pre_xml']}")
            print(f"    CAL: {urls_info['schema_urls']['cal_xml']}")
            print(f"    LAB: {urls_info['schema_urls']['lab_xml']}")

            # Get all URLs as a list (useful for batch downloading)
            all_urls = get_all_filing_urls(filing)
            print(f"    📦 Total files to download: {len(all_urls)}")

        except Exception as e:
            print(f"  ❌ Error building URLs: {e}")

    print(f"\n✅ Demo completed! The URL building functions are ready for integration.")
    print(f"💡 You can now use these URLs to download XBRL files and schema files")
    print(f"   for Arelle-based fact extraction.")


if __name__ == "__main__":
    demo_missing_filings_with_url_building()
