#!/usr/bin/env python3
"""
Test script to extract and verify "RevenueFromContractWithCustomerExcludingAssessedTax" facts
for FDX Q1 2024 and FY 2024 filings, specifically to validate the quarters assignment.

This test demonstrates the difference in quarters assignment between quarterly (Q1) and
annual (FY) facts for the same financial concept.

Usage:
    python test_fdx_revenue_quarters.py
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


def test_fdx_revenue_quarters():
    """Test RevenueFromContractWithCustomerExcludingAssessedTax facts for FDX Q1 2024 and FY 2024"""

    print("Testing FDX Revenue Quarters Assignment")
    print("=" * 70)

    # FDX CIK
    cik = "1048911"

    # Test cases: Q1 2024 and FY 2024 filings
    test_cases = [
        {
            "name": "FDX Q1 2024",
            "adsh": "0000950170-23-048994",  # Q1 2024 filing
            "expected_qtrs": 1,  # Quarterly filing should show 1 quarter
        },
        # {
        #     "name": "FDX FY 2024",
        #     "adsh": "0000950170-24-083577",  # FY 2024 filing (annual)
        #     "expected_qtrs": 4,  # Annual filing should show 4 quarters
        # },
    ]

    target_concept = "RevenueFromContractWithCustomerExcludingAssessedTax"

    for test_case in test_cases:
        print(f"\n{test_case['name']} ({test_case['adsh']})")
        print("-" * 50)

        try:
            # Extract ALL financial facts from the filing
            result = fetch_and_parse_all_financial_facts_from_submission_by_cik(
                cik=cik,
                adsh=test_case["adsh"],
                include_custom_facts=True,
                VERBOSITY=1,
            )

            if result.get("error"):
                print(f"[X] Error: {result['error']}")
                continue

            print(f"[OK] Total facts found: {result['total_facts_found']}")

            # Find all Revenue facts
            revenue_facts = []
            for fact in result["all_financial_facts"]:
                tag_name = fact.get("tag_name", "")
                if target_concept.lower() in tag_name.lower():
                    revenue_facts.append(fact)

            print(f"[OK] Found {len(revenue_facts)} '{target_concept}' facts")

            if not revenue_facts:
                print(f"[!] No '{target_concept}' facts found in this filing")

                # Show all revenue-related facts as fallback
                print("\nAll revenue-related facts found:")
                all_revenue_facts = []
                for fact in result["all_financial_facts"]:
                    tag_name = fact.get("tag_name", "")
                    if "revenue" in tag_name.lower():
                        all_revenue_facts.append(fact)

                for i, fact in enumerate(all_revenue_facts[:5]):  # Show first 5
                    print(f"  {i+1}. {fact.get('tag_name', 'Unknown')}")
                    print(
                        f"     Value: {fact.get('actual_value', 'N/A')} {fact.get('unit_ref', '')}"
                    )
                    print(
                        f"     Period: {fact.get('period_start', 'N/A')} to {fact.get('period_end', 'N/A')}"
                    )
                    print(f"     Qtrs: {fact.get('qtrs', 'N/A')}")
                    print()
                continue

            # Show all properties for each revenue fact
            print(f"\n{target_concept} FACTS - ALL PROPERTIES:")
            print("=" * 60)

            for i, fact in enumerate(revenue_facts):
                print(f"\nFact #{i+1}:")
                print("-" * 20)

                # Print all properties of the fact
                for key, value in sorted(fact.items()):
                    print(f"  {key}: {value}")

                # Verify quarters assignment
                qtrs = fact.get("qtrs")
                print(f"\n  >> QUARTERS VALIDATION:")
                print(f"     Assigned Qtrs: {qtrs}")
                print(f"     Expected Qtrs: {test_case['expected_qtrs']}")

                if qtrs == test_case["expected_qtrs"]:
                    print(f"     ✓ CORRECT: Quarters assignment matches expectation")
                elif qtrs is None:
                    print(f"     ? UNCERTAIN: Quarters is None (irregular period?)")
                else:
                    print(
                        f"     ✗ MISMATCH: Expected {test_case['expected_qtrs']}, got {qtrs}"
                    )

                print()

        except Exception as e:
            print(f"[X] Exception during extraction: {e}")
            import traceback

            print(traceback.format_exc())

    print("\n" + "=" * 70)
    print("SUMMARY:")
    print(f"This test extracts '{target_concept}' facts from FDX filings")
    print("to verify that the quarters field is correctly assigned:")
    print("  - Q1 filings should have Qtrs = 1 (quarterly)")
    print("  - FY filings should have Qtrs = 4 (annual)")
    print("  - Irregular periods should have Qtrs = None")


def main():
    """Main function to run the revenue quarters test"""
    test_fdx_revenue_quarters()


if __name__ == "__main__":
    main()
