#!/usr/bin/env python3
"""
Test script for the new fetch_and_parse_all_financial_facts_from_submission function.
This script demonstrates how to extract ALL financial facts from a specific SEC filing
using its ADSH (Accession Number).

Usage:
    python test_all_financial_facts.py

Example ADSH values from FDX missing filings report:
- 0000950170-24-108107 (Q1 2025)
- 0000950170-23-048994 (Q1 2024)
- 0000950170-22-018769 (Q1 2023)
"""

import sys
import os

# Add the src directory to Python path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from generic_utils.accept_utf8_encoding import accept_utf8_encoding

from data_loaders.fetch_and_parse_all_financial_facts_from_submission_by_cik import (
    fetch_and_parse_all_financial_facts_from_submission_by_cik,
)

accept_utf8_encoding()


def test_all_financial_facts_from_filling(VERBOSITY: int = 1, VERBOSE_OUTPUT=None):
    """Test extracting ALL financial facts from a specified adsh and cik filing
    VERBOSITY: 0 = silent, 1 = normal, 2 = verbose (prints for each processed element)
    VERBOSE_OUTPUT: file-like object to write verbose output, defaults to sys.stdout
    """
    import sys

    output = VERBOSE_OUTPUT if VERBOSE_OUTPUT is not None else sys.stdout

    def vprint(*args, **kwargs):
        if VERBOSITY > 0:
            print(*args, **kwargs, file=output)

    def vvprint(*args, **kwargs):
        if VERBOSITY > 1:
            print(*args, **kwargs, file=output)

    vprint("Testing FDX Q1 2025 Filing - ALL FINANCIAL FACTS EXTRACTION")
    vprint("=" * 70)

    # Filing details from the missing filings report
    cik = "1048911"  # FDX CIK
    # adsh = "0000950123-17-006152"  # 2017 filing
    adsh = "0000950170-24-083577"  # 2017 filing
    # adsh = "0000950170-25-042672"  # Q1 2025 filing

    vprint(f"CIK: {cik}")
    vprint(f"ADSH: {adsh}")
    vprint("-" * 40)

    try:
        # Extract ALL financial facts from the filing
        result = fetch_and_parse_all_financial_facts_from_submission_by_cik(
            cik=cik,
            adsh=adsh,
            include_custom_facts=True,  # Include company-specific financial concepts
            VERBOSITY=VERBOSITY,
        )

        if result.get("error"):
            vprint(f"[X] Error: {result['error']}")
            return

        vprint(f"[OK] Document URL: {result['document_url']}")
        vprint(f"[OK] Total financial facts found: {result['total_facts_found']}")

        # Show breakdown by category
        vprint("\nFINANCIAL FACTS BY CATEGORY:")
        vprint("-" * 40)

        for category, facts in result["facts_by_category"].items():
            if facts:
                vprint(f"\n{category.upper()}: {len(facts)} facts")

                # Show first 5 facts from each category as examples
                for i, fact in enumerate(facts[:5]):
                    tag = fact.get("tag_name", "Unknown")
                    value = fact.get("actual_value") or fact.get("value", "N/A")
                    period_end = fact.get("period_end", "N/A")
                    unit = fact.get("unit_ref", "")
                    vvprint(f"  {i+1}. {tag}")
                    vvprint(f"     Value: {value} {unit}")
                    vvprint(f"     Period End: {period_end}")
                if len(facts) > 5:
                    vvprint(f"     ... and {len(facts) - 5} more facts")

        # Show unique tags
        unique_tags = set()
        for fact in result["all_financial_facts"]:
            tag_name = fact.get("tag_name", "")
            if tag_name:
                unique_tags.add(tag_name)
        vprint(f"\nALL FINANCIAL FACTS FOUND:")
        vprint("-" * 40)
        vprint(f"Total unique tags found: {len(unique_tags)}")
        for tag in sorted(unique_tags):
            vvprint(f"FACT NAME: {tag}")

        vprint("\nKEY FINANCIAL METRICS (if found):")
        vprint("-" * 40)

        key_metrics = [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenue",
            "Revenues",
            "GrossProfit",
            "OperatingIncomeLoss",
            "Assets",
            "TotalAssets",
            "Cash",
            "CashAndCashEquivalentsAtCarryingValue",
            "NetIncomeLoss",
            "EarningsPerShareBasic",
        ]

        found_metrics = {}
        for fact in result["all_financial_facts"]:
            tag_name = fact.get("tag_name", "")
            for metric in key_metrics:
                if metric.lower() in tag_name.lower():
                    if metric not in found_metrics:
                        found_metrics[metric] = []
                    found_metrics[metric].append(fact)

        for metric, facts in found_metrics.items():
            vprint(f"\n{metric}:")
            for fact in facts[:2]:  # Show first 2 instances
                value = fact.get("actual_value") or fact.get("value", "N/A")
                period_end = fact.get("period_end", "N/A")
                unit = fact.get("unit_ref", "")
                qs = fact.get("qtrs")
                vvprint(f"  Value: {value} {unit} {qs} (Period: {period_end})")

        vprint(
            f"\n[OK] Successfully extracted {result['total_facts_found']} financial facts from the filing"
        )

        # Show extraction methods used
        extraction_methods = {}
        for fact in result["all_financial_facts"]:
            method = fact.get("extraction_method", "unknown")
            extraction_methods[method] = extraction_methods.get(method, 0) + 1

        vprint(f"\nEXTRACTION METHODS USED:")
        for method, count in extraction_methods.items():
            vvprint(f"  - {method}: {count} facts")

    except Exception as e:
        vprint(f"[X] Exception during extraction: {e}")
        import traceback

        vprint(traceback.format_exc())


def main():
    """Main function to run all tests"""
    print("TESTING: Extract ALL Financial Facts from SEC Filings")
    print("=" * 70)
    print("This test demonstrates the new functionality to extract ALL financial")
    print("facts from SEC filings, not just specific concepts.")
    print()

    # Test 1: FDX (known working ADSH)
    test_all_financial_facts_from_filling(VERBOSITY=2)

    # Test 2: Different company (might fail - just for demonstration)
    # test_different_company()

    print("\n" + "=" * 70)
    print("SUMMARY:")
    print("The new fetch_and_parse_all_financial_facts_from_submission function")
    print("extracts ALL financial facts from SEC filings by:")
    print("  1. Identifying XBRL tags with financial patterns")
    print("  2. Categorizing facts by financial statement type")
    print("  3. Extracting period information for each fact")
    print("  4. Converting values to numeric format where possible")
    print("  5. Filtering out non-financial operational data")
    print()
    print("This provides a comprehensive view of all financial data in a filing!")


if __name__ == "__main__":
    main()
