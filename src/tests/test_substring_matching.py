"""
Test script to verify case-insensitive substring matching in fetch_and_parse_submission_by_adsh.
This test demonstrates that the function now finds all tags containing the search term.

# AI-GENERATED: Code created by AI assistant for testing substring matching functionality
"""

import sys
import os

from data_loaders.sec_api_loaders.fetch_and_parse_submission_for_financial_concept_by_cik_and_adsh import (
    fetch_and_parse_submission_for_financial_concept_by_cik_and_adsh,
)

# Add the src directory to the path so we can import modules
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))


def test_substring_matching():
    """Test case-insensitive substring matching with different revenue patterns"""
    print("Testing Case-Insensitive Substring Matching")
    print("=" * 60)

    # Use the same FDX filing as the existing tests
    cik = "1048911"  # FDX CIK
    adsh = "0000950170-24-108107"  # Q1 2025 filing
    primary_document = "fdx-20240831.htm"

    # Test different search terms to demonstrate substring matching
    test_cases = [
        {
            "search_term": "revenue",
            "description": "Search for 'revenue' (lowercase) - should find all revenue-related tags",
        },
        {
            "search_term": "Revenue",
            "description": "Search for 'Revenue' (capitalized) - should find same tags as lowercase",
        },
        {
            "search_term": "REVENUE",
            "description": "Search for 'REVENUE' (uppercase) - should find same tags as other cases",
        },
        {
            "search_term": "operating",
            "description": "Search for 'operating' - should find operating-related tags",
        },
    ]

    for i, test_case in enumerate(test_cases, 1):
        search_term = test_case["search_term"]
        description = test_case["description"]

        print(f"\nTest {i}: {description}")
        print(f"Search term: '{search_term}'")
        print("-" * 50)

        try:
            result = fetch_and_parse_submission_for_financial_concept_by_cik_and_adsh(
                cik=cik,
                adsh=adsh,
                fact_tag=search_term,
                primary_document=primary_document,
            )

            if result.get("error"):
                print(f"[X] Error: {result['error']}")
                continue

            all_facts = result.get("all_facts", [])

            if all_facts:
                print(f"[OK] Found {len(all_facts)} facts matching '{search_term}':")

                # Show unique tag names found
                unique_tags = set()
                for fact in all_facts:
                    tag_name = fact.get("tag_name", "Unknown")
                    unique_tags.add(tag_name)

                for tag in sorted(unique_tags):
                    print(f"   - {tag}")

                # Show best fact details
                best_fact = result.get("best_fact")
                if best_fact:
                    print(f"\nBest fact:")
                    print(f"   Tag: {best_fact.get('tag_name', 'Unknown')}")
                    print(f"   Value: {best_fact.get('value', 'N/A')}")
                    print(f"   Period End: {best_fact.get('period_end', 'N/A')}")
                    print(
                        f"   Extraction Method: {best_fact.get('extraction_method', 'N/A')}"
                    )
            else:
                print(f"[X] No facts found for search term '{search_term}'")

        except Exception as e:
            print(f"[X] Exception during test: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    test_substring_matching()
