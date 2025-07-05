#!/usr/bin/env python3
"""
Test script to demonstrate extracting financial facts from missing filings
and formatting them for database insertion.

This script:
1. Gets missing filings for FDX
2. Extracts financial facts from a few sample missing filings
3. Formats the facts for edgar_financial_data_concepts table
4. Analyzes the output

Usage:
    python test_missing_filings_extraction.py
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from generic_utils.accept_utf8_encoding import accept_utf8_encoding

# Add the src directory to Python path to import modules

from data_loaders.sec_api_loaders.missing_filings_utils import (
    get_missing_sec_filings,
    get_missing_sec_filings_with_inferred_metadata,
    extract_financial_facts_from_missing_filing,
    process_missing_filings_for_database_insertion,
)
from data_loaders.fetch_known_adsh import fetch_known_adsh_for_ticker

accept_utf8_encoding()


def test_fiscal_metadata_inference():
    """Test the new fiscal metadata inference logic."""
    print("=" * 70)
    print("TEST: Fiscal Metadata Inference from Chronological Order")
    print("=" * 70)

    ticker = "FDX"
    cik = "1048911"

    print(f"Testing fiscal metadata inference for {ticker} (CIK: {cik})")
    print()

    try:
        # Get missing filings with inferred metadata
        result = get_missing_sec_filings_with_inferred_metadata(
            cik=cik, ticker=ticker, min_year=2017, verbose=True
        )

        missing_filings = result.get("all_missing", [])
        all_filings = result.get("all_filings_with_metadata", [])

        print(f"\nRESULTS:")
        print(f"Total filings analyzed: {len(all_filings)}")
        print(f"Missing filings found: {len(missing_filings)}")

        # Show detailed inference results
        print(
            f"\nDETAILED INFERENCE RESULTS (First {len(all_filings)} filings chronologically):"
        )
        print("-" * 90)
        print(
            f"{'ADSH':<25} {'Type':<6} {'Period':<8} {'Year':<6} {'Known':<6} {'Inference Source'}"
        )
        print("-" * 90)

        # Sort by date for better visualization
        sorted_filings = sorted(all_filings, key=lambda x: x.get("Ddate", ""))

        for filing in sorted_filings:
            adsh = filing.get("Adsh", "")[:25]
            filing_type = filing.get("FilingType", "")[:6]
            period = filing.get("FiscalPeriod", "")[:8]
            year = str(filing.get("FiscalYear", ""))[:6]
            is_known = "Yes" if filing.get("IsKnown", False) else "No"
            inference = filing.get("InferredFrom", "Original data")[:30]

            print(
                f"{adsh:<25} {filing_type:<6} {period:<8} {year:<6} {is_known:<6} {inference}"
            )

        # Test specific inference logic
        print(f"\nINFERENCE LOGIC VALIDATION:")
        print("-" * 50)

        # Find a sequence where we can validate the logic
        missing_with_inference = [f for f in missing_filings if f.get("InferredFrom")]

        if missing_with_inference:
            test_filing = missing_with_inference[0]
            print(f"Example missing filing: {test_filing.get('Adsh')}")
            print(
                f"  Original data: {test_filing.get('FilingType', 'N/A')} {test_filing.get('FiscalPeriod', 'N/A')} {test_filing.get('FiscalYear', 'N/A')}"
            )
            print(f"  Inferred from: {test_filing.get('InferredFrom', 'N/A')}")
            print(f"  Filing date: {test_filing.get('Ddate', 'N/A')}")

        # Show quarter progression validation
        print(f"\nQUARTER PROGRESSION VALIDATION:")
        print("-" * 40)

        # Group by year and show progression
        by_year = {}
        for filing in sorted_filings:
            year = filing.get("FiscalYear")
            if year and year >= 2022:  # Focus on recent years
                if year not in by_year:
                    by_year[year] = []
                by_year[year].append(filing)

        for year in sorted(by_year.keys(), reverse=True)[:3]:  # Show last 3 years
            year_filings = sorted(by_year[year], key=lambda x: x.get("Ddate", ""))
            print(f"\nFiscal Year {year}:")
            for filing in year_filings:
                status = "KNOWN" if filing.get("IsKnown") else "MISSING"
                print(
                    f"  {filing.get('Ddate')}: {filing.get('FilingType')} {filing.get('FiscalPeriod')} [{status}]"
                )

        print(f"\n✓ Fiscal metadata inference test completed successfully!")
        return True

    except Exception as e:
        print(f"✗ Fiscal metadata inference test failed: {e}")
        import traceback

        print(traceback.format_exc())
        return False


def test_extract_facts_from_single_missing_filing():
    """Test extracting financial facts from a single missing filing."""
    print("=" * 70)
    print("TEST 1: Extract financial facts from single missing filing")
    print("=" * 70)

    # Get missing filings for FDX
    ticker = "FDX"
    cik = "1048911"
    known_adsh = fetch_known_adsh_for_ticker(ticker)
    missing_result = get_missing_sec_filings(
        cik, known_adsh, min_year=2020, ticker=ticker, verbose=False
    )

    missing_10q = missing_result.get("missing_10q", [])
    if not missing_10q:
        print("No missing 10-Q filings found!")
        return

    # Test with the first missing 10-Q
    test_filing = missing_10q[0]
    print(f"Testing with filing: {test_filing}")
    print()

    # Extract financial facts
    print("Extracting financial facts...")
    facts = extract_financial_facts_from_missing_filing(test_filing, verbose=True)

    print(f"\nExtracted {len(facts)} financial facts")

    if facts:
        print("\nFirst 3 facts formatted for database:")
        for i, fact in enumerate(facts[:3], 1):
            print(f"\nFact {i}:")
            print(json.dumps(fact, indent=2, default=str))

        # Analyze the data completeness
        print(f"\nDATA COMPLETENESS ANALYSIS:")
        print(f"Total facts: {len(facts)}")

        # Check required fields
        facts_with_concept = sum(1 for f in facts if f.get("Concept"))
        facts_with_value = sum(
            1 for f in facts if f.get("Value") is not None or f.get("ValueString")
        )
        facts_with_unit = sum(1 for f in facts if f.get("Unit"))
        facts_with_period_end = sum(1 for f in facts if f.get("PeriodEnd"))

        print(
            f"Facts with Concept: {facts_with_concept}/{len(facts)} ({facts_with_concept/len(facts)*100:.1f}%)"
        )
        print(
            f"Facts with Value: {facts_with_value}/{len(facts)} ({facts_with_value/len(facts)*100:.1f}%)"
        )
        print(
            f"Facts with Unit: {facts_with_unit}/{len(facts)} ({facts_with_unit/len(facts)*100:.1f}%)"
        )
        print(
            f"Facts with PeriodEnd: {facts_with_period_end}/{len(facts)} ({facts_with_period_end/len(facts)*100:.1f}%)"
        )

        # Show concept variety
        concepts = set(f.get("Concept", "") for f in facts if f.get("Concept"))
        print(f"Unique concepts found: {len(concepts)}")
        print("Sample concepts:", list(concepts)[:5])


def test_process_multiple_missing_filings():
    """Test processing multiple missing filings (limited for testing)."""
    print("\n\n" + "=" * 70)
    print("TEST 2: Process multiple missing filings")
    print("=" * 70)

    # Get missing filings for FDX
    ticker = "FDX"
    cik = "1048911"

    known_adsh = fetch_known_adsh_for_ticker(ticker)
    missing_result = get_missing_sec_filings(
        cik, known_adsh, min_year=2017, ticker=ticker, verbose=True
    )

    missing_10q = missing_result.get("missing_10q", [])
    missing_10k = missing_result.get("missing_10k", [])

    print(f"Found {len(missing_10q)} missing 10-Q filings")
    print(f"Found {len(missing_10k)} missing 10-K filings")

    # Process a few filings (limit for testing)
    test_filings = missing_10q[:2] + missing_10k[:1]  # 2 10-Qs + 1 10-K
    print(f"\nProcessing {len(test_filings)} filings for testing...")

    all_facts = process_missing_filings_for_database_insertion(
        test_filings, verbose=True
    )

    print(f"\nTOTAL RESULTS:")
    print(f"Processed {len(test_filings)} filings")
    print(f"Extracted {len(all_facts)} total financial facts")

    if all_facts:
        # Analyze by filing type
        facts_by_type = {}
        for fact in all_facts:
            filing_type = fact.get("FilingType", "Unknown")
            facts_by_type[filing_type] = facts_by_type.get(filing_type, 0) + 1

        print(f"\nFacts by filing type:")
        for ftype, count in facts_by_type.items():
            print(f"  {ftype}: {count} facts")

        # Show database-ready format sample
        print(f"\nSample database-ready fact:")
        print(json.dumps(all_facts[0], indent=2, default=str))


def test_process_multiple_missing_filings_with_adsh_inference():
    """
    Test the complete flow:
    1. Get missing filings (only 10-Q and 10-K)
    2. Create chronological order of all filings (missing + existing)
    3. Infer fiscal metadata based on order
    4. Process for database insertion with corrected metadata

    Focus on the scenario: 10-K FY 2024 followed by missing 10-Q Q1 2025
    """
    print("=" * 70)
    print("TEST: Process Multiple Missing Filings with ADSH Inference")
    print("=" * 70)

    ticker = "FDX"
    cik = "1048911"

    print(f"Testing complete missing filings workflow for {ticker} (CIK: {cik})")
    print("Step 1: Get missing filings with inferred metadata (ONLY 10-Q and 10-K)")
    print("-" * 60)

    try:
        # Get missing filings with inferred metadata
        result = get_missing_sec_filings_with_inferred_metadata(
            cik=cik, ticker=ticker, min_year=2023, verbose=True
        )

        all_filings = result.get("all_filings_with_metadata", [])
        missing_filings = result.get("all_missing", [])
        missing_10q = result.get("missing_10q", [])
        missing_10k = result.get("missing_10k", [])

        print(f"\nStep 2: Analyze chronological order and inference results")
        print("-" * 60)
        print(f"Total filings (10-Q/10-K only): {len(all_filings)}")
        print(f"Missing filings: {len(missing_filings)}")
        print(f"Missing 10-Qs: {len(missing_10q)}")
        print(f"Missing 10-Ks: {len(missing_10k)}")

        # Sort by date to show chronological progression
        sorted_filings = sorted(all_filings, key=lambda x: x.get("Ddate", ""))

        print(f"\nStep 3: Validate ADSH chronological inference logic")
        print("-" * 60)
        print(
            f"{'Date':<12} {'ADSH':<25} {'Type':<6} {'Period':<8} {'Year':<6} {'Status':<8} {'Inference Source'}"
        )
        print("-" * 100)

        # Show recent filings to validate the inference
        for filing in sorted_filings[-15:]:  # Last 15 filings chronologically
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

        # Test specific scenario: Find 10-K FY followed by 10-Q Q1 next year
        print(
            f"\nStep 4: Test specific scenario - 10-K FY followed by 10-Q Q1 next year"
        )
        print("-" * 60)

        found_scenario = False
        for i, filing in enumerate(sorted_filings[:-1]):
            if (
                filing.get("FilingType") == "10-K"
                and filing.get("FiscalPeriod") == "FY"
            ):

                next_filing = sorted_filings[i + 1]
                if (
                    next_filing.get("FilingType") == "10-Q"
                    and next_filing.get("FiscalPeriod") == "Q1"
                    and next_filing.get("FiscalYear") == filing.get("FiscalYear") + 1
                ):

                    print(f"✓ Found expected scenario:")
                    print(
                        f"  Known 10-K:   {filing.get('Adsh')} - {filing.get('FilingType')} {filing.get('FiscalPeriod')} {filing.get('FiscalYear')} ({filing.get('Ddate')})"
                    )
                    print(
                        f"  Next 10-Q:    {next_filing.get('Adsh')} - {next_filing.get('FilingType')} {next_filing.get('FiscalPeriod')} {next_filing.get('FiscalYear')} ({next_filing.get('Ddate')})"
                    )
                    print(f"  Inference:    {next_filing.get('InferredFrom', 'N/A')}")
                    found_scenario = True
                    break

        if not found_scenario:
            print(
                "⚠ Expected scenario (10-K FY followed by 10-Q Q1 next year) not found in recent data"
            )  # Test processing for database insertion
        print(f"\nStep 5: Process missing filings for database insertion")
        print("-" * 60)

        # Test with a small sample using the process_missing_filings_for_database_insertion function
        test_missing_filings = (
            missing_filings[:3] if len(missing_filings) >= 3 else missing_filings
        )

        if test_missing_filings:
            print(
                f"Processing {len(test_missing_filings)} missing filings for database insertion..."
            )

            # Use the batch processing function for complete workflow
            all_facts_for_db = process_missing_filings_for_database_insertion(
                test_missing_filings, verbose=True
            )

            print(f"\nBATCH PROCESSING RESULTS:")
            print(f"Total facts extracted for database: {len(all_facts_for_db)}")

            if all_facts_for_db:
                # Verify that database records use inferred metadata
                print(f"\nVERIFYING INFERRED METADATA IN DATABASE RECORDS:")
                print("-" * 60)

                for i, filing in enumerate(test_missing_filings):
                    filing_adsh = filing.get("Adsh")
                    inferred_filing_type = filing.get("FilingType")
                    inferred_fiscal_period = filing.get("FiscalPeriod")
                    inferred_fiscal_year = filing.get("FiscalYear")

                    # Find facts from this filing
                    filing_facts = [
                        f for f in all_facts_for_db if f.get("Adsh") == filing_adsh
                    ]

                    if filing_facts:
                        sample_fact = filing_facts[0]
                        db_filing_type = sample_fact.get("FilingType")
                        db_fiscal_period = sample_fact.get("FiscalPeriod")
                        db_fiscal_year = sample_fact.get("FiscalYear")

                        print(f"\n[{i+1}] Filing: {filing_adsh}")
                        print(
                            f"  INFERRED metadata: {inferred_filing_type} {inferred_fiscal_period} {inferred_fiscal_year}"
                        )
                        print(
                            f"  DATABASE record:   {db_filing_type} {db_fiscal_period} {db_fiscal_year}"
                        )
                        print(
                            f"  Inference source:  {filing.get('InferredFrom', 'N/A')}"
                        )

                        # Verify they match
                        matches = (
                            db_filing_type == inferred_filing_type
                            and db_fiscal_period == inferred_fiscal_period
                            and db_fiscal_year == inferred_fiscal_year
                        )

                        status = "✓ MATCHES" if matches else "✗ MISMATCH"
                        print(f"  Status: {status}")
                        print(f"  Facts extracted: {len(filing_facts)}")

                # Show sample database-ready record
                print(f"\nSAMPLE DATABASE-READY RECORD (ALL FIELDS):")
                print("-" * 50)
                sample_record = all_facts_for_db[0]
                for key, value in sample_record.items():
                    print(f"  {key}: {value}")

                # Show all records
                print(f"\nFIRST 3 DATABASE-READY RECORDS:")
                print("-" * 50)
                print(len(all_facts_for_db))
                for i, record in enumerate(all_facts_for_db[:3], 1):
                    print(f"\nRecord {i}:")
                    for key, value in record.items():
                        print(f"  {key}: {value}")

            # Show individual filing details for comparison
            print(f"\nINDIVIDUAL FILING ANALYSIS:")
            print("-" * 60)
            for i, filing in enumerate(test_missing_filings, 1):
                print(
                    f"\n[{i}/{len(test_missing_filings)}] Filing Details: {filing.get('Adsh')}"
                )
                print(
                    f"  INFERRED metadata: {filing.get('FilingType')} {filing.get('FiscalPeriod')} {filing.get('FiscalYear')}"
                )
                print(f"  Inferred from: {filing.get('InferredFrom', 'N/A')}")
                print(f"  Filing date: {filing.get('Ddate')}")

                # Count facts for this filing in the batch results
                filing_facts_count = len(
                    [f for f in all_facts_for_db if f.get("Adsh") == filing.get("Adsh")]
                )
                print(f"  Facts in database batch: {filing_facts_count}")
        else:
            print("No missing filings to process.")

        print(f"\nStep 6: Validation Summary")
        print("-" * 60)

        # Count inference methods used
        inference_methods = {}
        for filing in missing_filings:
            inference = filing.get("InferredFrom", "Unknown")
            inference_type = inference.split(":")[0] if ":" in inference else inference
            inference_methods[inference_type] = (
                inference_methods.get(inference_type, 0) + 1
            )

        print(f"Inference methods used:")
        for method, count in inference_methods.items():
            print(f"  {method}: {count} filings")

        # Validate fiscal year progression
        yearly_counts = {}
        for filing in missing_filings:
            year = filing.get("FiscalYear")
            if year:
                yearly_counts[year] = yearly_counts.get(year, 0) + 1

        print(f"\nMissing filings by fiscal year:")
        for year in sorted(yearly_counts.keys()):
            print(f"  {year}: {yearly_counts[year]} filings")

        print(f"\n✓ Complete missing filings workflow test completed successfully!")
        return True

    except Exception as e:
        print(f"✗ Complete missing filings workflow test failed: {e}")
        import traceback

        print(traceback.format_exc())
        return False


def main():
    """Run all tests."""
    try:
        # New test for fiscal metadata inference
        test_fiscal_metadata_inference()
        # test_process_multiple_missing_filings()  # Old test without inference
        test_process_multiple_missing_filings_with_adsh_inference()  # New test with ADSH inference
        print("\n" + "=" * 70)
        print("ALL TESTS COMPLETED SUCCESSFULLY")
        print("=" * 70)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
