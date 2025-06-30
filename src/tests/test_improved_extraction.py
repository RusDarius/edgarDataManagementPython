#!/usr/bin/env python3
"""
Test script for the improved date extraction functionality
"""


from data_loaders.sec_api_loaders.fetch_and_parse_submission_for_financial_concept_by_cik_and_adsh import (
    fetch_and_parse_submission_for_financial_concept_by_cik_and_adsh,
)


def test_improved_date_extraction():
    """Test the improved date extraction with a known filing"""
    print("Testing Improved Date Extraction")
    print("=" * 50)

    # Test with FDX Q1 2025 filing
    cik = "1048911"  # FDX
    adsh = "0000950170-24-108107"  # Q1 2025 filing
    concept = "RevenueFromContractWithCustomerExcludingAssessedTax"

    print(f"Testing extraction from:")
    print(f"  CIK: {cik}")
    print(f"  ADSH: {adsh}")
    print(f"  Concept: {concept}")
    print()

    try:
        result = fetch_and_parse_submission_for_financial_concept_by_cik_and_adsh(
            cik=cik, adsh=adsh, fact_tag=concept
        )

        if result.get("error"):
            print(f"[X] Error: {result['error']}")
            return False

        all_facts = result.get("all_facts", [])
        best_fact = result.get("best_fact")

        print(f"Results:")
        print(f"  Total facts found: {len(all_facts)}")
        print(f"  Best fact selected: {'Yes' if best_fact else 'No'}")

        if best_fact:
            print(
                f"  Best fact value: {best_fact.get('actual_value', best_fact.get('value', 'N/A'))}"
            )
            print(f"  Best fact unit: {best_fact.get('unit_ref', 'N/A')}")
            print(f"  Best fact period_end: {best_fact.get('period_end', 'N/A')}")
            print(f"  Best fact context: {best_fact.get('context_ref', 'N/A')}")
            print(f"  Extraction method: {best_fact.get('extraction_method', 'N/A')}")

        # Show period extraction details for first few facts
        print("\nDetailed period extraction for first 3 facts:")
        for i, fact in enumerate(all_facts[:3], 1):
            print(f"  Fact {i}:")
            print(f"    Value: {fact.get('actual_value', fact.get('value', 'N/A'))}")
            print(f"    Context: {fact.get('context_ref', 'N/A')}")
            print(f"    Period End: {fact.get('period_end', 'N/A')}")
            print(f"    Period Start: {fact.get('period_start', 'N/A')}")
            print(f"    Period Type: {fact.get('period_type', 'N/A')}")
            print(f"    Extraction Method: {fact.get('extraction_method', 'N/A')}")
            print()

        # Count how many facts have period_end extracted
        facts_with_periods = [f for f in all_facts if f.get("period_end")]
        print(
            f"Facts with period_end extracted: {len(facts_with_periods)}/{len(all_facts)} ({len(facts_with_periods)/len(all_facts)*100:.1f}%)"
        )

        return len(facts_with_periods) > 0

    except Exception as e:
        print(f"[X] Exception during test: {e}")
        import traceback

        print(traceback.format_exc())
        return False


if __name__ == "__main__":
    success = test_improved_date_extraction()
    if success:
        print("\n[OK] Test completed - Some period information was extracted!")
    else:
        print("\n[X] Test failed - No period information extracted")
