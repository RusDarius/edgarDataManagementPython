#!/usr/bin/env python3
"""
Test script to demonstrate extracting financial facts from missing filings
and formatting them for database insertion.

This script:
1. Gets missing filings for FDX
2. Extracts financial facts from a few sample missing filings
3. Formats the facts for edgar_financial_data_concepts table
4. Analyzes the output

Usage:
    python test_missing_filings_extraction.py
"""

import sys
import os
import json

# Add the src directory to Python path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_loaders.sec_api_loaders.missing_filings_utils import (
    get_missing_sec_filings,
    extract_financial_facts_from_missing_filing,
    process_missing_filings_for_database_insertion,
)
from data_loaders.fetch_known_adsh import fetch_known_adsh_for_ticker


def test_extract_facts_from_single_missing_filing():
    """Test extracting financial facts from a single missing filing."""
    print("=" * 70)
    print("TEST 1: Extract financial facts from single missing filing")
    print("=" * 70)

    # Get missing filings for FDX
    ticker = "FDX"
    cik = "1048911"
    known_adsh = fetch_known_adsh_for_ticker(ticker)
    missing_result = get_missing_sec_filings(
        cik, known_adsh, min_year=2020, ticker=ticker, verbose=False
    )

    missing_10q = missing_result.get("missing_10q", [])
    if not missing_10q:
        print("No missing 10-Q filings found!")
        return

    # Test with the first missing 10-Q
    test_filing = missing_10q[0]
    print(f"Testing with filing: {test_filing}")
    print()

    # Extract financial facts
    print("Extracting financial facts...")
    facts = extract_financial_facts_from_missing_filing(test_filing, verbose=True)

    print(f"\nExtracted {len(facts)} financial facts")

    if facts:
        print("\nFirst 3 facts formatted for database:")
        for i, fact in enumerate(facts[:3], 1):
            print(f"\nFact {i}:")
            print(json.dumps(fact, indent=2, default=str))

        # Analyze the data completeness
        print(f"\nDATA COMPLETENESS ANALYSIS:")
        print(f"Total facts: {len(facts)}")

        # Check required fields
        facts_with_concept = sum(1 for f in facts if f.get("Concept"))
        facts_with_value = sum(
            1 for f in facts if f.get("Value") is not None or f.get("ValueString")
        )
        facts_with_unit = sum(1 for f in facts if f.get("Unit"))
        facts_with_period_end = sum(1 for f in facts if f.get("PeriodEnd"))

        print(
            f"Facts with Concept: {facts_with_concept}/{len(facts)} ({facts_with_concept/len(facts)*100:.1f}%)"
        )
        print(
            f"Facts with Value: {facts_with_value}/{len(facts)} ({facts_with_value/len(facts)*100:.1f}%)"
        )
        print(
            f"Facts with Unit: {facts_with_unit}/{len(facts)} ({facts_with_unit/len(facts)*100:.1f}%)"
        )
        print(
            f"Facts with PeriodEnd: {facts_with_period_end}/{len(facts)} ({facts_with_period_end/len(facts)*100:.1f}%)"
        )

        # Show concept variety
        concepts = set(f.get("Concept", "") for f in facts if f.get("Concept"))
        print(f"Unique concepts found: {len(concepts)}")
        print("Sample concepts:", list(concepts)[:5])


def test_process_multiple_missing_filings():
    """Test processing multiple missing filings (limited for testing)."""
    print("\n\n" + "=" * 70)
    print("TEST 2: Process multiple missing filings")
    print("=" * 70)

    # Get missing filings for FDX
    ticker = "FDX"
    cik = "1048911"

    known_adsh = fetch_known_adsh_for_ticker(ticker)
    missing_result = get_missing_sec_filings(
        cik, known_adsh, min_year=2017, ticker=ticker, verbose=False
    )

    missing_10q = missing_result.get("missing_10q", [])
    missing_10k = missing_result.get("missing_10k", [])

    print(f"Found {len(missing_10q)} missing 10-Q filings")
    print(f"Found {len(missing_10k)} missing 10-K filings")

    # Process a few filings (limit for testing)
    test_filings = missing_10q[:2] + missing_10k[:1]  # 2 10-Qs + 1 10-K
    print(f"\nProcessing {len(test_filings)} filings for testing...")

    all_facts = process_missing_filings_for_database_insertion(
        test_filings, verbose=True
    )

    print(f"\nTOTAL RESULTS:")
    print(f"Processed {len(test_filings)} filings")
    print(f"Extracted {len(all_facts)} total financial facts")

    if all_facts:
        # Analyze by filing type
        facts_by_type = {}
        for fact in all_facts:
            filing_type = fact.get("FilingType", "Unknown")
            facts_by_type[filing_type] = facts_by_type.get(filing_type, 0) + 1

        print(f"\nFacts by filing type:")
        for ftype, count in facts_by_type.items():
            print(f"  {ftype}: {count} facts")

        # Show database-ready format sample
        print(f"\nSample database-ready fact:")
        print(json.dumps(all_facts[0], indent=2, default=str))


def main():
    """Run all tests."""
    try:
        test_process_multiple_missing_filings()
        print("\n" + "=" * 70)
        print("ALL TESTS COMPLETED SUCCESSFULLY")
        print("=" * 70)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
