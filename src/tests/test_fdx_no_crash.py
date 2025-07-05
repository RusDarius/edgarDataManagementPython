#!/usr/bin/env python3
"""
Simple test to verify the date parsing fix works with the FDX extraction.
"""

import sys
import os

# Add the src directory to Python path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_loaders.fetch_and_parse_all_financial_facts_from_submission_by_cik import (
    fetch_and_parse_all_financial_facts_from_submission_by_cik,
)


def test_fdx_extraction_no_crash():
    """Test that FDX extraction doesn't crash with date parsing errors."""

    print("=" * 60)
    print("TESTING FDX EXTRACTION - SHOULD NOT CRASH")
    print("=" * 60)

    # Test with the known problematic FDX filing
    cik = "1048911"
    adsh = "0000950123-17-006152"  # The 2017 10-K that was failing

    print(f"Testing CIK: {cik}")
    print(f"Testing ADSH: {adsh}")
    print("This should complete without date parsing errors...")

    try:
        # Extract with minimal verbosity to avoid too much output
        result = fetch_and_parse_all_financial_facts_from_submission_by_cik(
            cik=cik,
            adsh=adsh,
            include_custom_facts=True,
            VERBOSITY=1,  # Reduced verbosity
        )

        if result.get("error"):
            print(f"EXTRACTION FAILED: {result['error']}")
        else:
            print(f"SUCCESS: Extraction completed!")
            print(f"Total facts found: {result.get('total_facts_found', 0)}")
            print(f"Document URL: {result.get('document_url')}")

            # Show a few sample facts
            facts = result.get("all_financial_facts", [])
            if facts:
                print(f"\nSample facts (first 3):")
                for i, fact in enumerate(facts[:3], 1):
                    tag = fact.get("tag_name", "Unknown")
                    value = fact.get("actual_value") or fact.get("value", "N/A")
                    period = fact.get("period_end", "N/A")
                    print(f"  {i}. {tag}: {value} (period: {period})")

    except Exception as e:
        print(f"UNEXPECTED ERROR: {e}")
        import traceback

        print(traceback.format_exc())

    print("\n" + "=" * 60)
    print("TEST COMPLETED")
    print("=" * 60)


if __name__ == "__main__":
    test_fdx_extraction_no_crash()
