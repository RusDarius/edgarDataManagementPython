#!/usr/bin/env python3
"""
Test script for the complete process_multiple_missing_filings workflow.
This tests the full process but limits the scope to avoid inserting too much data.
"""

import sys
import os

# Add the src directory to Python path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from generic_utils.accept_utf8_encoding import accept_utf8_encoding
from data_loaders.sec_api_loaders.process_multiple_missing_filings import (
    process_multiple_missing_filings,
)
from db.connection_provider import get_mysql_connection

accept_utf8_encoding()


def test_process_multiple_missing_filings():
    """Test the complete workflow with limited scope."""
    print("=" * 80)
    print("TESTING COMPLETE MISSING FILINGS WORKFLOW")
    print("=" * 80)

    ticker = "FDX"
    cik = "1048911"

    try:
        # Test with limited scope (recent year only)
        print(f"Testing complete workflow for {ticker} (CIK: {cik})")
        print("Limiting to 2024 filings to avoid large data insertion...")
        print()

        result = process_multiple_missing_filings(
            ticker=ticker,
            cik=cik,
            min_year=2024,  # Recent year only for testing
            verbose=True,
        )

        print(f"\n✓ Workflow completed successfully!")
        print(f"Results: {result}")

        # Verify some data was inserted if there were missing filings
        if result["total_facts_inserted"] > 0:
            print(f"\nVerifying inserted data...")
            connection = get_mysql_connection()
            cursor = connection.cursor()

            batch_tag = result["batch_tag"]
            verify_query = """
            SELECT COUNT(*) as count,
                   MIN(Concept) as sample_concept,
                   MIN(FilingType) as filing_type,
                   MIN(FiscalPeriod) as fiscal_period,
                   MIN(FiscalYear) as fiscal_year
            FROM edgar_financial_data_concepts 
            WHERE BatchTag = %s
            """

            cursor.execute(verify_query, (batch_tag,))
            verification = cursor.fetchone()

            if verification:
                count, sample_concept, filing_type, fiscal_period, fiscal_year = (
                    verification
                )
                print(f"  ✓ Found {count} records in database")
                print(f"  Sample concept: {sample_concept}")
                print(f"  Filing: {filing_type} {fiscal_period} {fiscal_year}")

            cursor.close()
            connection.close()

            print(f"\n⚠ NOTE: Real data has been inserted with batch tag: {batch_tag}")
            print(
                f"   You may want to review or clean up this data if it was just for testing."
            )

        return True

    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Run the complete workflow test."""
    try:
        success = test_process_multiple_missing_filings()
        if success:
            print("\n🎉 Complete workflow test passed!")
        else:
            print("\n❌ Complete workflow test failed!")

    except Exception as e:
        print(f"\n💥 Test execution failed: {e}")


if __name__ == "__main__":
    main()
