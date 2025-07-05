#!/usr/bin/env python3
"""
Focused test for ADSH chronological order-based fiscal metadata inference.
"""

import sys
import os

# Add the src directory to Python path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_loaders.sec_api_loaders.missing_filings_utils import (
    infer_fiscal_metadata_from_chronological_order,
)


def test_adsh_chronological_inference():
    """Test the core ADSH chronological inference logic with controlled data."""

    print("=" * 80)
    print("FOCUSED TEST: ADSH Chronological Order Fiscal Metadata Inference")
    print("=" * 80)

    # Create test data mimicking real ADSH sequence
    # Example: Known filing 0000950170-24-083577 is 10-K FY 2024
    # Next filing chronologically should be 10-Q Q1 2025

    test_known_metadata = [
        {
            "Adsh": "0000950170-24-083577",
            "FilingType": "10-K",
            "FiscalPeriod": "FY",
            "FiscalYear": 2024,
            "Ddate": "2024-07-15",
        },
        {
            "Adsh": "0000950170-23-048994",
            "FilingType": "10-Q",
            "FiscalPeriod": "Q1",
            "FiscalYear": 2024,
            "Ddate": "2023-09-21",
        },
    ]

    test_all_filings = [
        # Known filing 1 (earlier)
        {
            "Adsh": "0000950170-23-048994",
            "FilingType": "10-Q",
            "FiscalPeriod": "Q1",
            "FiscalYear": 2024,
            "Ddate": "2023-09-21",
        },
        # Missing filing 1 (should be Q2 2024)
        {
            "Adsh": "0000950170-23-MISSING1",
            "FilingType": "10-Q",  # Original from SEC API
            "FiscalPeriod": "",  # Empty - to be inferred
            "FiscalYear": 2023,  # Original from SEC API (may be wrong)
            "Ddate": "2023-12-20",
        },
        # Missing filing 2 (should be Q3 2024)
        {
            "Adsh": "0000950170-24-MISSING2",
            "FilingType": "10-Q",
            "FiscalPeriod": "",
            "FiscalYear": 2024,
            "Ddate": "2024-03-21",
        },
        # Known filing 2 (later)
        {
            "Adsh": "0000950170-24-083577",
            "FilingType": "10-K",
            "FiscalPeriod": "FY",
            "FiscalYear": 2024,
            "Ddate": "2024-07-15",
        },
        # Missing filing 3 (should be Q1 2025)
        {
            "Adsh": "0000950170-24-MISSING3",
            "FilingType": "10-Q",
            "FiscalPeriod": "",
            "FiscalYear": 2024,  # Wrong year from SEC API
            "Ddate": "2024-09-19",
        },
    ]

    print("INPUT DATA:")
    print("-" * 40)
    print("Known filings metadata:")
    for known in test_known_metadata:
        print(
            f"  {known['Adsh']}: {known['FilingType']} {known['FiscalPeriod']} {known['FiscalYear']} ({known['Ddate']})"
        )

    print("\nAll filings (missing + known):")
    for filing in sorted(test_all_filings, key=lambda x: x["Ddate"]):
        status = (
            "KNOWN"
            if filing["Adsh"] in [k["Adsh"] for k in test_known_metadata]
            else "MISSING"
        )
        print(
            f"  {filing['Ddate']}: {filing['Adsh']} - {filing['FilingType']} {filing['FiscalPeriod']} {filing['FiscalYear']} [{status}]"
        )

    # Run the inference
    print("\nRUNNING INFERENCE...")
    print("-" * 40)

    enriched_filings = infer_fiscal_metadata_from_chronological_order(
        test_all_filings, test_known_metadata
    )

    print("\nRESULTS:")
    print("-" * 40)
    print(
        f"{'Date':<12} {'ADSH':<25} {'Type':<6} {'Period':<8} {'Year':<6} {'Status':<8} {'Inference'}"
    )
    print("-" * 90)

    for filing in sorted(enriched_filings, key=lambda x: x["Ddate"]):
        date = filing["Ddate"][:10]
        adsh = filing["Adsh"][:25]
        filing_type = filing.get("FilingType", "")[:6]
        period = filing.get("FiscalPeriod", "")[:8]
        year = str(filing.get("FiscalYear", ""))[:6]
        status = "KNOWN" if filing.get("IsKnown", False) else "MISSING"
        inference = filing.get("InferredFrom", "Original")[:30]

        print(
            f"{date:<12} {adsh:<25} {filing_type:<6} {period:<8} {year:<6} {status:<8} {inference}"
        )

    # Validate expected results
    print("\nVALIDATION:")
    print("-" * 40)

    expected_results = {
        "0000950170-23-MISSING1": {
            "FilingType": "10-Q",
            "FiscalPeriod": "Q2",
            "FiscalYear": 2024,
        },
        "0000950170-24-MISSING2": {
            "FilingType": "10-Q",
            "FiscalPeriod": "Q3",
            "FiscalYear": 2024,
        },
        "0000950170-24-MISSING3": {
            "FilingType": "10-Q",
            "FiscalPeriod": "Q1",
            "FiscalYear": 2025,
        },
    }

    validation_passed = True

    for filing in enriched_filings:
        adsh = filing["Adsh"]
        if adsh in expected_results:
            expected = expected_results[adsh]
            actual_type = filing.get("FilingType")
            actual_period = filing.get("FiscalPeriod")
            actual_year = filing.get("FiscalYear")

            type_match = actual_type == expected["FilingType"]
            period_match = actual_period == expected["FiscalPeriod"]
            year_match = actual_year == expected["FiscalYear"]

            status = "✓" if (type_match and period_match and year_match) else "✗"
            print(f"{status} {adsh}:")
            print(
                f"    Expected: {expected['FilingType']} {expected['FiscalPeriod']} {expected['FiscalYear']}"
            )
            print(f"    Actual:   {actual_type} {actual_period} {actual_year}")

            if not (type_match and period_match and year_match):
                validation_passed = False

    print(f"\nOVERALL VALIDATION: {'✓ PASSED' if validation_passed else '✗ FAILED'}")

    return validation_passed


if __name__ == "__main__":
    success = test_adsh_chronological_inference()
    if success:
        print("\n🎉 Focused ADSH inference test PASSED!")
    else:
        print("\n❌ Focused ADSH inference test FAILED!")
        sys.exit(1)
