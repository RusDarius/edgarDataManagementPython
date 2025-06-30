#!/usr/bin/env python3
"""
Production workflow test: Complete pipeline from missing filings to database-ready facts.

This script demonstrates the complete workflow:
1. Get missing filings for a ticker
2. Extract financial facts from each missing filing
3. Format facts for database insertion
4. Show statistics and sample data

Usage:
    python test_production_workflow.py
"""

import sys
import os
import json

# Add the src directory to Python path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_loaders.sec_api_loaders.missing_filings_utils import (
    get_missing_sec_filings,
    process_missing_filings_for_database_insertion,
)
from data_loaders.fetch_known_adsh import fetch_known_adsh_for_ticker


def production_workflow_test(ticker="FDX", max_filings=3):
    """
    Test the complete production workflow for extracting missing filing data.

    Args:
        ticker: Stock ticker to process
        max_filings: Maximum number of filings to process (for testing)
    """
    print("=" * 80)
    print(f"PRODUCTION WORKFLOW TEST - {ticker}")
    print("=" * 80)

    # Step 1: Get company info
    cik_mapping = {"FDX": "1048911"}  # In production, get from database
    cik = cik_mapping.get(ticker)
    if not cik:
        print(f"CIK not found for ticker {ticker}")
        return

    print(f"Company: {ticker} (CIK: {cik})")

    # Step 2: Get known filings from database
    print(f"\nStep 1: Fetching known filings from database...")
    known_adsh = fetch_known_adsh_for_ticker(ticker)
    print(f"Found {len(known_adsh)} known filings in database")

    # Step 3: Get missing filings from SEC API
    print(f"\nStep 2: Comparing with SEC filings since 2020...")
    missing_result = get_missing_sec_filings(
        cik, known_adsh, min_year=2020, ticker=ticker, verbose=True
    )

    missing_10q = missing_result.get("missing_10q", [])
    missing_10k = missing_result.get("missing_10k", [])

    print(f"Missing 10-Q filings: {len(missing_10q)}")
    print(f"Missing 10-K filings: {len(missing_10k)}")

    if not missing_10q and not missing_10k:
        print("No missing filings found!")
        return

    # Step 4: Process missing filings (limited for testing)
    all_missing = missing_10q + missing_10k
    filings_to_process = all_missing[:max_filings]

    print(
        f"\nStep 3: Processing {len(filings_to_process)} missing filings for database insertion..."
    )
    print("(Limited for testing purposes)")

    # Extract all financial facts
    all_facts = process_missing_filings_for_database_insertion(
        filings_to_process, verbose=True
    )

    # Step 5: Analyze results
    print(f"\nStep 4: Analysis of extracted data")
    print("=" * 50)

    if not all_facts:
        print("No facts extracted!")
        return

    # Overall statistics
    print(f"SUMMARY:")
    print(f"  Processed filings: {len(filings_to_process)}")
    print(f"  Total facts extracted: {len(all_facts)}")
    print(f"  Average facts per filing: {len(all_facts) / len(filings_to_process):.0f}")

    # Facts by filing type and fiscal period
    stats = {}
    for fact in all_facts:
        filing_type = fact.get("FilingType", "Unknown")
        fiscal_period = fact.get("FiscalPeriod", "Unknown")
        key = f"{filing_type}-{fiscal_period}"
        stats[key] = stats.get(key, 0) + 1

    print(f"\nFACTS BY FILING TYPE & FISCAL PERIOD:")
    for key, count in sorted(stats.items()):
        print(f"  {key}: {count} facts")

    # Concept analysis
    concepts = {}
    for fact in all_facts:
        concept = fact.get("Concept", "Unknown")
        concepts[concept] = concepts.get(concept, 0) + 1

    top_concepts = sorted(concepts.items(), key=lambda x: x[1], reverse=True)[:10]
    print(f"\nTOP 10 MOST COMMON CONCEPTS:")
    for concept, count in top_concepts:
        print(f"  {concept}: {count} facts")

    # Data quality check
    print(f"\nDATA QUALITY CHECK:")
    total = len(all_facts)

    checks = [
        ("Cik", lambda f: f.get("Cik") is not None),
        ("Ticker", lambda f: f.get("Ticker")),
        ("FilingType", lambda f: f.get("FilingType")),
        ("FiscalPeriod", lambda f: f.get("FiscalPeriod")),
        ("FiscalYear", lambda f: f.get("FiscalYear")),
        ("Concept", lambda f: f.get("Concept")),
        (
            "Value or ValueString",
            lambda f: f.get("Value") is not None or f.get("ValueString"),
        ),
        ("Adsh", lambda f: f.get("Adsh")),
        ("PeriodEnd", lambda f: f.get("PeriodEnd")),
        ("Ddate", lambda f: f.get("Ddate")),
    ]

    for field_name, check_func in checks:
        valid_count = sum(1 for f in all_facts if check_func(f))
        percentage = (valid_count / total) * 100
        print(f"  {field_name}: {valid_count}/{total} ({percentage:.1f}%)")

    # Sample records for verification
    print(f"\nSAMPLE DATABASE-READY RECORDS:")
    print("-" * 50)

    # Show one fact from each filing
    seen_adsh = set()
    sample_facts = []
    for fact in all_facts:
        adsh = fact.get("Adsh")
        if adsh not in seen_adsh:
            sample_facts.append(fact)
            seen_adsh.add(adsh)

    for i, fact in enumerate(sample_facts, 1):
        print(
            f"\nSample {i} - {fact.get('FilingType')} {fact.get('FiscalPeriod')} {fact.get('FiscalYear')}:"
        )
        # Show key fields
        key_fields = [
            "Cik",
            "Ticker",
            "FilingType",
            "FiscalPeriod",
            "FiscalYear",
            "Concept",
            "Value",
            "Unit",
            "Adsh",
            "PeriodEnd",
        ]
        for field in key_fields:
            value = fact.get(field)
            if value is not None:
                if (
                    field == "Value"
                    and isinstance(value, (int, float))
                    and value > 1000
                ):
                    print(f"  {field}: ${value:,.0f}")
                else:
                    print(f"  {field}: {value}")

    print(f"\n" + "=" * 80)
    print(f"WORKFLOW COMPLETED SUCCESSFULLY")
    print(
        f"Ready to insert {len(all_facts)} facts into edgar_financial_data_concepts table"
    )
    print("=" * 80)

    return all_facts


def main():
    """Run the production workflow test."""
    try:
        # Test with FDX, processing up to 3 filings
        facts = production_workflow_test("FDX", max_filings=3)

        if facts:
            print(
                f"\n🎉 SUCCESS: Extracted {len(facts)} financial facts ready for database insertion!"
            )
            print(f"💡 Next step: Use these facts with your database insertion logic")
        else:
            print("❌ No facts extracted")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
