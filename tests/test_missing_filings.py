"""
Test script to demonstrate and validate the missing filings handler functionality.
This script provides examples of how to use the missing filings workflow.
"""

import sys
import os

# Add the src directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from financial_execution_flows.missing_filings_handler import (
    MissingFilingsHandler,
    process_missing_filings_for_ticker,
)
import json


def test_missing_filings_identification():
    """Test the identification of missing filings without processing them."""
    print("Testing missing filings identification...")
    print("=" * 50)

    handler = MissingFilingsHandler()

    # Test with Micron Technology
    ticker = "MU"
    cik = "723125"

    print(f"Testing with {ticker} (CIK: {cik})")

    try:
        # Get database filings
        db_filings = handler.get_database_filings(ticker)
        print(f"Database filings found: {len(db_filings)}")

        if db_filings:
            print("Sample database filing:")
            print(json.dumps(db_filings[0], indent=2, default=str))

        # Identify missing filings
        missing_filings = handler.identify_missing_filings(
            ticker, cik, ["10-K", "10-Q"]
        )
        print(f"\nMissing filings identified: {len(missing_filings)}")

        if missing_filings:
            print("Sample missing filing:")
            print(json.dumps(missing_filings[0], indent=2, default=str))

            # Save report
            report_path = handler.save_missing_filings_report(missing_filings, ticker)
            print(f"Report saved to: {report_path}")

        return True

    except Exception as e:
        print(f"Error during identification test: {e}")
        return False


def test_concept_extraction():
    """Test concept extraction from a specific filing."""
    print("\nTesting concept extraction...")
    print("=" * 50)

    handler = MissingFilingsHandler()

    # Create a sample filing info
    filing_info = {
        "ticker": "MU",
        "cik": "723125",
        "form": "10-Q",
        "fiscal_period": "Q1",
        "fiscal_year": 2025,
        "accession_number": "0000950170-24-130978",  # Example accession
    }

    concept_tags = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"]

    try:
        concept_data = handler.extract_concepts_from_filing(filing_info, concept_tags)
        print("Extracted concept data:")
        print(json.dumps(concept_data, indent=2, default=str))
        return True

    except Exception as e:
        print(f"Error during concept extraction test: {e}")
        return False


def test_full_workflow():
    """Test the complete missing filings workflow."""
    print("\nTesting full workflow...")
    print("=" * 50)

    # Test with a company that likely has some missing data
    ticker = "AAPL"  # Apple Inc.
    cik = "320193"

    concept_tags = [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "Revenue",
    ]

    try:
        results = process_missing_filings_for_ticker(
            ticker=ticker,
            cik=cik,
            concept_tags=concept_tags,
            max_filings=2,  # Process only 2 to avoid long runtime
            form_types=["10-Q"],
        )

        print("Workflow results:")
        print(json.dumps(results, indent=2, default=str))
        return True

    except Exception as e:
        print(f"Error during full workflow test: {e}")
        return False


def test_date_fiscal_period_inference():
    """Test the fiscal period and year inference logic."""
    print("\nTesting fiscal period inference...")
    print("=" * 50)

    handler = MissingFilingsHandler()

    test_cases = [
        ("2024-05-31", "0531", "FY", 2024),  # Fiscal year end
        ("2024-08-31", "0531", "Q1", 2025),  # Q1 of FY2025
        ("2024-11-30", "0531", "Q2", 2025),  # Q2 of FY2025
        ("2024-02-28", "0531", "Q3", 2024),  # Q3 of FY2024
        ("2024-02-29", "1231", "Q1", 2024),  # Calendar Q1
        ("2024-12-31", "1231", "FY", 2024),  # Calendar year end
    ]

    for period_date, fiscal_year_end, expected_period, expected_year in test_cases:
        fiscal_period, fiscal_year = handler.infer_fiscal_period(
            period_date, fiscal_year_end
        )

        print(f"Date: {period_date}, FY End: {fiscal_year_end}")
        print(f"  Expected: {expected_period} {expected_year}")
        print(f"  Got: {fiscal_period} {fiscal_year}")
        print(
            f"  Match: {fiscal_period == expected_period and fiscal_year == expected_year}"
        )
        print()


def test_database_query():
    """Test database connectivity and query functionality."""
    print("\nTesting database connectivity...")
    print("=" * 50)

    handler = MissingFilingsHandler()

    try:
        # Test with a known ticker
        db_filings = handler.get_database_filings("AAPL")
        print(f"Database query successful. Found {len(db_filings)} filings for AAPL")

        if db_filings:
            print("Sample filing from database:")
            sample = db_filings[0]
            for key, value in sample.items():
                print(f"  {key}: {value}")

        return True

    except Exception as e:
        print(f"Database connectivity test failed: {e}")
        print("Make sure your MySQL database is running and configured correctly.")
        return False


def main():
    """Run all test functions."""
    print("Missing Filings Handler Test Suite")
    print("=" * 60)

    tests = [
        ("Database Connectivity", test_database_query),
        ("Fiscal Period Inference", test_date_fiscal_period_inference),
        ("Missing Filings Identification", test_missing_filings_identification),
        ("Concept Extraction", test_concept_extraction),
        ("Full Workflow", test_full_workflow),
    ]

    results = {}

    for test_name, test_func in tests:
        print(f"\n{'='*60}")
        print(f"Running: {test_name}")
        print("=" * 60)

        try:
            result = test_func()
            results[test_name] = "PASSED" if result else "FAILED"
        except Exception as e:
            print(f"Test failed with exception: {e}")
            results[test_name] = "ERROR"

    # Summary
    print(f"\n{'='*60}")
    print("TEST SUMMARY")
    print("=" * 60)

    for test_name, result in results.items():
        status_symbol = "✓" if result == "PASSED" else "✗"
        print(f"{status_symbol} {test_name}: {result}")

    passed = sum(1 for r in results.values() if r == "PASSED")
    total = len(results)
    print(f"\nOverall: {passed}/{total} tests passed")


if __name__ == "__main__":
    main()
