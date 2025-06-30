#!/usr/bin/env python3
"""
Test script to analyze and improve fiscal period extraction from missing filings.

The previous test showed that FiscalPeriod is coming back empty. This script will:
1. Investigate why fiscal period is not being extracted
2. Try to derive fiscal period from period end dates and fiscal year end
3. Test the improved logic

Usage:
    python test_fiscal_period_extraction.py
"""

import sys
import os
import json
from datetime import datetime

# Add the src directory to Python path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_loaders.sec_api_loaders.missing_filings_utils import (
    get_missing_sec_filings,
    extract_financial_facts_from_missing_filing,
)
from data_loaders.fetch_known_adsh import fetch_known_adsh_for_ticker


def deduce_fiscal_period_from_period_end(period_end_str, fiscal_year_end="0531"):
    """
    Deduce fiscal period (Q1, Q2, Q3, Q4, FY) from period_end and fiscal_year_end.
    FDX fiscal year ends on May 31 (0531).
    """
    if not period_end_str or not fiscal_year_end or len(fiscal_year_end) != 4:
        return None

    try:
        period_dt = datetime.strptime(period_end_str, "%Y-%m-%d")
        fy_end_month = int(fiscal_year_end[:2])  # 05 = May
        fy_end_day = int(fiscal_year_end[2:])  # 31

        # For FDX, fiscal quarters end on:
        # Q1: August 31 (3 months after May 31)
        # Q2: November 30 (6 months after May 31)
        # Q3: February 28/29 (9 months after May 31)
        # Q4/FY: May 31 (fiscal year end)

        if period_dt.month == 8 and period_dt.day == 31:
            return "Q1"
        elif period_dt.month == 11 and period_dt.day == 30:
            return "Q2"
        elif period_dt.month == 2 and period_dt.day in [28, 29]:
            return "Q3"
        elif period_dt.month == fy_end_month and period_dt.day == fy_end_day:
            return "FY"
        else:
            return None
    except Exception:
        return None


def analyze_fiscal_period_extraction():
    """Analyze why fiscal period is not being extracted properly."""
    print("=" * 70)
    print("FISCAL PERIOD EXTRACTION ANALYSIS")
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

    # Analyze the first missing filing
    test_filing = missing_10q[0]
    print(f"Analyzing filing: {test_filing}")
    print()

    # Extract facts and analyze period information
    facts = extract_financial_facts_from_missing_filing(test_filing, verbose=False)

    if not facts:
        print("No facts extracted!")
        return None, [], {}

    # Group facts by period end to understand the periods in the filing
    periods = {}
    for fact in facts:
        period_end = fact.get("PeriodEnd")
        if period_end:
            if period_end not in periods:
                periods[period_end] = []
            periods[period_end].append(fact)

    print(f"Found {len(periods)} unique period end dates:")
    for period_end in sorted(periods.keys()):
        fact_count = len(periods[period_end])
        deduced_fp = deduce_fiscal_period_from_period_end(period_end)
        print(f"  {period_end}: {fact_count} facts -> Fiscal Period: {deduced_fp}")

    # Look for revenue facts to understand which period is the "main" reporting period
    print(f"\nRevenue facts analysis:")
    revenue_facts = [f for f in facts if "revenue" in f.get("Concept", "").lower()]
    for fact in revenue_facts[:5]:  # Show first 5 revenue facts
        concept = fact.get("Concept")
        value = fact.get("Value")
        period_end = fact.get("PeriodEnd")
        deduced_fp = deduce_fiscal_period_from_period_end(period_end)
        print(f"  {concept}: ${value:,.0f} | Period: {period_end} -> {deduced_fp}")

    # Determine what the "main" fiscal period should be for this filing
    filing_date = test_filing.get("Ddate")
    print(f"\nFiling date: {filing_date}")

    # For a Q1 filing (filed in September), the main period should be Q1 (ending August 31)
    expected_main_period = "2024-08-31"  # For FDX Q1 2025
    main_period_facts = periods.get(expected_main_period, [])
    print(
        f"Facts for expected main period ({expected_main_period}): {len(main_period_facts)}"
    )

    return test_filing, facts, periods


def test_improved_fiscal_period_logic():
    """Test improved fiscal period extraction and update the missing filing info."""
    print("\n\n" + "=" * 70)
    print("IMPROVED FISCAL PERIOD LOGIC TEST")
    print("=" * 70)

    result = analyze_fiscal_period_extraction()
    if result is None or result[0] is None:
        print("Could not analyze fiscal period - no data available")
        return None

    filing, facts, periods = result

    # Update the fiscal period in the filing based on the most common period end
    # that represents a quarter end
    quarter_periods = {}
    for period_end, period_facts in periods.items():
        deduced_fp = deduce_fiscal_period_from_period_end(period_end)
        if deduced_fp and deduced_fp.startswith("Q"):  # Only quarters, not FY
            quarter_periods[period_end] = {
                "fiscal_period": deduced_fp,
                "fact_count": len(period_facts),
            }

    if quarter_periods:
        # Find the quarter period with the most facts (likely the main reporting period)
        main_period = max(
            quarter_periods.keys(), key=lambda p: quarter_periods[p]["fact_count"]
        )
        main_fiscal_period = quarter_periods[main_period]["fiscal_period"]

        print(
            f"Determined main reporting period: {main_period} -> {main_fiscal_period}"
        )

        # Update the filing info
        updated_filing = filing.copy()
        updated_filing["FiscalPeriod"] = main_fiscal_period
        updated_filing["MainPeriodEnd"] = main_period

        print(f"Updated filing info:")
        print(json.dumps(updated_filing, indent=2))

        # Show how this affects the database facts
        sample_fact = facts[0].copy()
        sample_fact["FiscalPeriod"] = main_fiscal_period

        print(f"\nSample updated database fact:")
        print(json.dumps(sample_fact, indent=2, default=str))

        return updated_filing
    else:
        print("Could not determine main fiscal period")
        return filing


def main():
    """Run the fiscal period analysis and improvement test."""
    try:
        analyze_fiscal_period_extraction()
        updated_filing = test_improved_fiscal_period_logic()

        print("\n" + "=" * 70)
        print("FISCAL PERIOD ANALYSIS COMPLETED")
        print("=" * 70)
        print("RECOMMENDATION:")
        print("1. Extract period end dates from XBRL facts")
        print("2. Deduce fiscal period from period end + fiscal year end")
        print("3. Use the quarter period with most facts as main reporting period")
        print("4. Update FiscalPeriod field accordingly")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
