#!/usr/bin/env python3
"""
Test script for the new fetch_and_parse_submission_by_adsh function.
This script demonstrates how to extract XBRL concepts directly from a specific SEC filing
using its ADSH (Accession Number).

Usage:
    python test_adsh_extraction.py

Example ADSH values from FDX missing filings report:
- 0000950170-24-108107 (Q1 2025)
- 0000950170-23-048994 (Q1 2024)
- 0000950170-22-018769 (Q1 2023)
- 0001564590-21-048468 (Q1 2022)
"""


from data_loaders.sec_api_loaders.fetch_and_parse_submission_for_financial_concept_by_cik_and_adsh import (
    fetch_and_parse_submission_for_financial_concept_by_cik_and_adsh,
)


def test_fdx_filing():
    """Test with FDX Q1 2025 filing"""
    print("Testing FDX Q1 2025 Filing (ADSH: 0000950170-24-108107)")
    print("=" * 70)

    # Filing details from the missing filings report
    cik = "1048911"  # FDX CIK
    adsh = "0000950170-24-108107"  # Q1 2025 filing
    primary_document = "fdx-20240831.htm"

    # Test multiple revenue-related concepts
    concepts_to_test = [
        "Revenues",
        "TotalRevenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenue",
        "SalesRevenueNet",
        "OperatingRevenues",
    ]

    print(f"CIK: {cik}")
    print(f"ADSH: {adsh}")
    print(f"Primary Document: {primary_document}")
    print(f"Testing {len(concepts_to_test)} concepts...")
    print()

    successful_extractions = 0

    for i, concept in enumerate(concepts_to_test, 1):
        print(f"{i}. Testing concept: {concept}")
        print("-" * 40)

        try:
            result = fetch_and_parse_submission_for_financial_concept_by_cik_and_adsh(
                cik=cik, adsh=adsh, fact_tag=concept, primary_document=primary_document
            )

            if result.get("error"):
                print(f"   ✗ Error: {result['error']}")
                if "404" in str(result.get("error", "")):
                    print(f"   💡 Tip: The document might have a different filename")
                    print(f"   📄 Attempted URL: {result.get('document_url', 'N/A')}")

            elif result.get("all_facts"):
                successful_extractions += 1
                facts = result["all_facts"]
                best_fact = result.get("best_fact")

                print(f"   ✓ Found {len(facts)} fact(s) for '{concept}'")

                # Show all facts with their periods
                for j, fact in enumerate(facts):
                    value = (
                        fact.get("actual_value")
                        or fact.get("numeric_value")
                        or fact.get("value")
                    )
                    period_info = ""
                    if fact.get("period_start") and fact.get("period_end"):
                        period_info = (
                            f" ({fact['period_start']} to {fact['period_end']})"
                        )
                    elif fact.get("period_end"):
                        period_info = f" (ending {fact['period_end']})"

                    print(
                        f"     Fact {j+1}: {value} {fact.get('unit_ref', '')}{period_info}"
                    )

                # Highlight the best fact
                if best_fact:
                    best_value = (
                        best_fact.get("actual_value")
                        or best_fact.get("numeric_value")
                        or best_fact.get("value")
                    )
                    best_period = best_fact.get("period_end", "N/A")
                    print(
                        f"   🌟 Best (Most Recent): {best_value} {best_fact.get('unit_ref', '')} (Period: {best_period})"
                    )

            else:
                print(f"   ✗ No facts found for '{concept}'")

        except Exception as e:
            print(f"   ✗ Exception: {e}")

        print()  # Empty line between concepts

    print("=" * 70)
    print(
        f"SUMMARY: Successfully extracted {successful_extractions}/{len(concepts_to_test)} concepts"
    )

    if successful_extractions == 0:
        print("\n💡 TROUBLESHOOTING TIPS:")
        print("1. Check if the ADSH and primary document name are correct")
        print("2. The SEC server might be temporarily unavailable")
        print(
            "3. Try with a different concept tag (some companies use different XBRL tags)"
        )
        print(
            "4. The document might not be in iXBRL format (try an older or newer filing)"
        )


def test_multiple_filings():
    """Test with multiple FDX filings from different years"""
    print("\n\nTesting Multiple FDX Filings")
    print("=" * 70)

    # Multiple filings from the missing filings report
    test_cases = [
        {
            "year": "Q1 2025",
            "adsh": "0000950170-24-108107",
            "primary_doc": "fdx-20240831.htm",
        },
        {
            "year": "Q1 2024",
            "adsh": "0000950170-23-048994",
            "primary_doc": "fdx-20230831.htm",
        },
        {
            "year": "Q1 2023",
            "adsh": "0000950170-22-018769",
            "primary_doc": "fdx-20220831.htm",
        },
        {
            "year": "Q1 2022",
            "adsh": "0001564590-21-048468",
            "primary_doc": "fdx-10q_20210831.htm",
        },
    ]

    cik = "1048911"
    concept = "Revenues"  # Use the most common concept

    print(f"Extracting '{concept}' from {len(test_cases)} different FDX filings...")
    print()

    for case in test_cases:
        print(f"📅 {case['year']} - ADSH: {case['adsh']}")

        try:
            result = fetch_and_parse_submission_for_financial_concept_by_cik_and_adsh(
                cik=cik,
                adsh=case["adsh"],
                fact_tag=concept,
                primary_document=case["primary_doc"],
            )

            if result.get("error"):
                print(f"   ✗ Error: {result['error']}")
            elif result.get("best_fact"):
                best_fact = result["best_fact"]
                value = (
                    best_fact.get("actual_value")
                    or best_fact.get("numeric_value")
                    or best_fact.get("value")
                )
                period = best_fact.get("period_end", "N/A")
                unit = best_fact.get("unit_ref", "")
                print(f"   ✓ Revenue: {value:,.0f} {unit} (Period: {period})")
            else:
                print(f"   ✗ No facts found")

        except Exception as e:
            print(f"   ✗ Exception: {e}")

        print()


if __name__ == "__main__":
    print("SEC EDGAR ADSH-Based Concept Extraction Test")
    print("=" * 70)
    print("This script tests the new fetch_and_parse_submission_by_adsh function")
    print("which allows direct extraction of XBRL concepts from specific SEC filings.")
    print()

    # Test with a single filing
    test_fdx_filing()

    # Test with multiple filings
    test_multiple_filings()

    print("\n" + "=" * 70)
    print("Test completed! 🎉")
    print("\nTo use this function in your own code:")
    print("```python")
    print("from data_loaders.sec_api_loader import fetch_and_parse_submission_by_adsh")
    print("")
    print("result = fetch_and_parse_submission_by_adsh(")
    print("    cik='1048911',")
    print("    adsh='0000950170-24-108107',")
    print("    fact_tag='Revenues',")
    print("    primary_document='fdx-20240831.htm'")
    print(")")
    print("")
    print("# Access all facts found")
    print("all_facts = result['all_facts']")
    print("# Access the most recent fact")
    print("best_fact = result['best_fact']")
    print("```")
