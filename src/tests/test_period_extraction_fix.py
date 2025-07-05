#!/usr/bin/env python3
"""
Test the fixed extract_comprehensive_period_info function.
"""

import sys
import os

# Add the src directory to Python path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_loaders.data_extractors.extract_comprehensive_period_info import (
    extract_comprehensive_period_info,
)


def test_fixed_period_extraction():
    """Test the fixed period extraction with problematic context IDs."""

    # Test cases that would have caused the original error
    test_cases = [
        "C_01048911_20170531",  # Original problematic case
        "01048911_20170531",  # Direct case
        "C_0001048911_20210601_20210831",  # Valid case (but with CIK confusion)
        "C_20170531_20170831",  # Valid duration
        "C_20170531",  # Valid instant
        "context_random_text",  # No dates
    ]

    print("=" * 70)
    print("TESTING FIXED EXTRACT_COMPREHENSIVE_PERIOD_INFO")
    print("=" * 70)

    for context_id in test_cases:
        print(f"\n--- Testing context: {context_id} ---")
        try:
            result = extract_comprehensive_period_info(
                fact_element=None,
                context_ref=context_id,
                document_soup=None,
                expected_period_date=None,
                VERBOSITY=2,
            )

            print(f"RESULT: {result}")

            if result.get("extraction_method") != "failed":
                print(f"SUCCESS: Extracted period info without error")
                print(f"  - Method: {result.get('extraction_method')}")
                print(f"  - Period start: {result.get('period_start')}")
                print(f"  - Period end: {result.get('period_end')}")
                print(f"  - Period type: {result.get('period_type')}")
            else:
                print(f"No period info extracted (expected for some cases)")

        except Exception as e:
            print(f"ERROR: {e}")
            import traceback

            print(traceback.format_exc())


if __name__ == "__main__":
    test_fixed_period_extraction()
