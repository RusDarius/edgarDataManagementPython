#!/usr/bin/env python3
"""
Test script for the SEC EDGAR URL building utility functions.
"""

import sys
import os
import json

# Add src to sys.path to allow imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_loaders.sec_api_loaders.missing_filings_utils import (
    build_sec_edgar_urls,
    get_all_filing_urls,
    validate_filing_data_for_url_building,
)


def test_url_building():
    """Test the URL building functions with sample FDX data."""

    # Sample filing data from user's request
    sample_filings = [
        {
            "Cik": "1048911",
            "Ticker": "FDX",
            "FilingType": "10-Q",
            "FiscalPeriod": "",
            "FiscalYear": 2024,
            "Adsh": "0000950170-24-108107",
            "Ddate": "2024-09-19",
            "BatchTag": "fdx-20240831.htm",
        },
        {
            "Cik": "1048911",
            "Ticker": "FDX",
            "FilingType": "10-Q",
            "FiscalPeriod": "",
            "FiscalYear": 2023,
            "Adsh": "0000950170-23-048994",
            "Ddate": "2023-09-20",
            "BatchTag": "fdx-20230831.htm",
        },
        {
            "Cik": "1048911",
            "Ticker": "FDX",
            "FilingType": "10-Q",
            "FiscalPeriod": "",
            "FiscalYear": 2022,
            "Adsh": "0000950170-22-018769",
            "Ddate": "2022-09-22",
            "BatchTag": "fdx-20220831.htm",
        },
        {
            "Cik": "1048911",
            "Ticker": "FDX",
            "FilingType": "10-K",
            "FiscalPeriod": "",
            "FiscalYear": 2025,
            "Adsh": "0001048911-25-000011",
            "Ddate": "2025-07-21",
            "BatchTag": "fdx-20250531.htm",
        },
        {
            "Cik": "1048911",
            "Ticker": "FDX",
            "FilingType": "10-K",
            "FiscalPeriod": "",
            "FiscalYear": 2017,
            "Adsh": "0000950123-17-006152",
            "Ddate": "2017-07-17",
            "BatchTag": "fdx-10k_20170531.htm",
        },
    ]

    print("🧪 Testing SEC EDGAR URL Building Functions")
    print("=" * 60)

    for i, filing in enumerate(sample_filings, 1):
        print(
            f"\n📋 Test {i}: {filing['FilingType']} {filing['FiscalYear']} (ADSH: {filing['Adsh']})"
        )
        print("-" * 50)

        # Validate filing data
        is_valid = validate_filing_data_for_url_building(filing)
        print(f"✅ Validation: {'PASS' if is_valid else 'FAIL'}")

        if not is_valid:
            print("❌ Skipping due to validation failure")
            continue

        try:
            # Build URLs
            urls_info = build_sec_edgar_urls(filing)

            print(f"🌐 Base URL: {urls_info['base_url']}")
            print(f"📄 iXBRL URL: {urls_info['ixbrl_url']}")
            print(f"📁 Base filename: {urls_info['base_filename']}")

            print(f"\n📚 Schema/Linkbase URLs:")
            for file_type, url in urls_info["schema_urls"].items():
                print(f"  {file_type.upper()}: {url}")

            # Get all URLs list
            all_urls = get_all_filing_urls(filing)
            print(f"\n📋 All URLs ({len(all_urls)} total):")
            for j, url in enumerate(all_urls, 1):
                filename = url.split("/")[-1]
                print(f"  {j}. {filename}")

        except Exception as e:
            print(f"❌ Error building URLs: {e}")

    print(f"\n🎯 URL Pattern Verification")
    print("-" * 50)

    # Test specific case that should match the expected format
    test_filing = {
        "Cik": "1048911",
        "Ticker": "FDX",
        "FilingType": "10-Q",
        "FiscalPeriod": "",
        "FiscalYear": 2023,
        "Adsh": "0000950170-23-048994",
        "Ddate": "2023-09-20",
        "BatchTag": "fdx-20230831.htm",
    }

    expected_base = (
        "https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/"
    )
    expected_ixbrl = "https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/fdx-20230831.htm"
    expected_xsd = "https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/fdx-20230831.xsd"

    urls_info = build_sec_edgar_urls(test_filing)

    print(f"Expected base URL: {expected_base}")
    print(f"Generated base URL: {urls_info['base_url']}")
    print(
        f"✅ Base URL match: {'YES' if urls_info['base_url'] == expected_base else 'NO'}"
    )

    print(f"\nExpected iXBRL URL: {expected_ixbrl}")
    print(f"Generated iXBRL URL: {urls_info['ixbrl_url']}")
    print(
        f"✅ iXBRL URL match: {'YES' if urls_info['ixbrl_url'] == expected_ixbrl else 'NO'}"
    )

    print(f"\nExpected XSD URL: {expected_xsd}")
    print(f"Generated XSD URL: {urls_info['schema_urls']['xsd']}")
    print(
        f"✅ XSD URL match: {'YES' if urls_info['schema_urls']['xsd'] == expected_xsd else 'NO'}"
    )

    print(f"\n🏁 Test completed!")


if __name__ == "__main__":
    test_url_building()
