#!/usr/bin/env python3
"""
Test script to debug and fix the date parsing issue in extract_comprehensive_period_info.
"""

import sys
import os
import re
from datetime import datetime

# Add the src directory to Python path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def safe_parse_date_improved(date_str: str):
    """Improved date parsing that validates date components."""
    if not date_str:
        return None

    # Handle various date formats
    date_formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%m/%d/%Y", "%d/%m/%Y"]

    for fmt in date_formats:
        try:
            parsed_date = datetime.strptime(date_str.strip(), fmt)
            return parsed_date.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Try to extract YYYY-MM-DD pattern from longer strings
    match = re.search(r"(\d{4}-\d{2}-\d{2})", date_str)
    if match:
        try:
            parsed_date = datetime.strptime(match.group(1), "%Y-%m-%d")
            return parsed_date.strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def validate_date_components(year_str, month_str, day_str):
    """Validate that year, month, day components make sense before parsing."""
    try:
        year = int(year_str)
        month = int(month_str)
        day = int(day_str)

        # Basic range checks
        if year < 1900 or year > 2030:  # Reasonable range for SEC filings
            return False
        if month < 1 or month > 12:
            return False
        if day < 1 or day > 31:
            return False

        # Try to create the actual date to validate
        datetime(year, month, day)
        return True

    except (ValueError, TypeError):
        return False


def extract_dates_from_context_improved(context_ref):
    """Improved date extraction that validates dates before parsing."""
    if not context_ref:
        return None, None

    print(f"Processing context: {context_ref}")

    # Pattern 1: CIK_startdate_enddate (e.g., C_0001048911_20210601_20210831)
    date_pattern = r"(\d{8})_(\d{8})"
    match = re.search(date_pattern, context_ref)
    if match:
        start_str, end_str = match.groups()
        print(f"Found potential date pattern: {start_str} -> {end_str}")

        # Extract date components and validate them
        start_year = start_str[:4]
        start_month = start_str[4:6]
        start_day = start_str[6:8]

        end_year = end_str[:4]
        end_month = end_str[4:6]
        end_day = end_str[6:8]

        print(f"Start components: {start_year}-{start_month}-{start_day}")
        print(f"End components: {end_year}-{end_month}-{end_day}")

        # Validate start date components
        start_valid = validate_date_components(start_year, start_month, start_day)
        end_valid = validate_date_components(end_year, end_month, end_day)

        print(f"Start date valid: {start_valid}")
        print(f"End date valid: {end_valid}")

        if start_valid and end_valid:
            try:
                start_date = f"{start_year}-{start_month}-{start_day}"
                end_date = f"{end_year}-{end_month}-{end_day}"

                # Double-check with datetime parsing
                datetime.strptime(start_date, "%Y-%m-%d")
                datetime.strptime(end_date, "%Y-%m-%d")

                print(f"SUCCESS: {start_date} to {end_date}")
                return start_date, end_date

            except ValueError as e:
                print(f"Date validation failed: {e}")
                return None, None
        else:
            print("Date components validation failed")
            return None, None

    # Pattern 2: Single date (instant) - various formats with validation
    single_date_patterns = [
        r"(\d{8})",  # YYYYMMDD
        r"(\d{4}-\d{2}-\d{2})",  # YYYY-MM-DD
        r"(\d{4}/\d{2}/\d{2})",  # YYYY/MM/DD
    ]

    for pattern in single_date_patterns:
        matches = re.findall(pattern, context_ref)
        if matches:
            print(f"Found potential single dates with pattern {pattern}: {matches}")
            # Use the last date found (most recent)
            for date_str in reversed(matches):  # Try from most recent
                if pattern == r"(\d{8})":  # YYYYMMDD format
                    if len(date_str) == 8:
                        year = date_str[:4]
                        month = date_str[4:6]
                        day = date_str[6:8]
                        if validate_date_components(year, month, day):
                            formatted_date = f"{year}-{month}-{day}"
                            try:
                                datetime.strptime(formatted_date, "%Y-%m-%d")
                                print(f"SUCCESS: Single date {formatted_date}")
                                return None, formatted_date
                            except ValueError:
                                continue
                else:
                    # For already formatted dates, just validate
                    parsed_date = safe_parse_date_improved(date_str)
                    if parsed_date:
                        print(f"SUCCESS: Single date {parsed_date}")
                        return None, parsed_date

    print("No valid dates found")
    return None, None


def test_problematic_contexts():
    """Test with contexts that are known to cause issues."""

    # Simulate some problematic context IDs that might cause the error
    test_contexts = [
        "C_0001048911_20210601_20210831",  # Valid case
        "C_0001048911_01048911_20210831",  # Invalid start date (CIK confusion)
        "FDX_0104891120170531",  # Possible case where CIK gets mixed with date
        "context_01048911_20170531",  # Another variation
        "C_20170531",  # Simple date
        "something_01040601_invalid",  # Clearly invalid
    ]

    print("=" * 60)
    print("TESTING DATE EXTRACTION WITH VALIDATION")
    print("=" * 60)

    for context in test_contexts:
        print(f"\n--- Testing: {context} ---")
        start_date, end_date = extract_dates_from_context_improved(context)
        if start_date or end_date:
            print(f"RESULT: start={start_date}, end={end_date}")
        else:
            print("RESULT: No valid dates extracted")


def test_current_logic_failures():
    """Test the specific case that's failing."""

    # The error message shows '0104-89-11' which suggests
    # the pattern (\d{8})_(\d{8}) matched something like "01048911_????????"
    # where 01048911 is likely the CIK (1048911 with leading zero)

    test_cases = [
        "C_01048911_20170531",  # Likely scenario - CIK confused as start date
        "01048911_20170531",  # Direct case
        "context_01048911_something",
    ]

    print("\n" + "=" * 60)
    print("TESTING CURRENT LOGIC FAILURE CASES")
    print("=" * 60)

    for context in test_cases:
        print(f"\n--- Testing problematic case: {context} ---")

        # Show what current logic would extract
        date_pattern = r"(\d{8})_(\d{8})"
        match = re.search(date_pattern, context)
        if match:
            start_str, end_str = match.groups()
            start_formatted = f"{start_str[:4]}-{start_str[4:6]}-{start_str[6:8]}"
            end_formatted = f"{end_str[:4]}-{end_str[4:6]}-{end_str[6:8]}"
            print(f"Current logic would extract: {start_formatted} to {end_formatted}")

            # Try to parse with datetime to see the error
            try:
                datetime.strptime(start_formatted, "%Y-%m-%d")
                print("Start date parsing: OK")
            except ValueError as e:
                print(f"Start date parsing: ERROR - {e}")

            try:
                datetime.strptime(end_formatted, "%Y-%m-%d")
                print("End date parsing: OK")
            except ValueError as e:
                print(f"End date parsing: ERROR - {e}")

        # Test improved logic
        start_date, end_date = extract_dates_from_context_improved(context)


if __name__ == "__main__":
    test_problematic_contexts()
    test_current_logic_failures()
