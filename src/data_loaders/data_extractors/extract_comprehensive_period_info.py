from typing import Any, Dict, Optional
import re
from datetime import datetime
import sys


def extract_comprehensive_period_info(
    fact_element,
    context_ref: Optional[str],
    document_soup,
    expected_period_date: Optional[str] = None,
    VERBOSITY: int = 1,
    VERBOSE_OUTPUT=None,
) -> Dict[str, Any]:
    """
    Comprehensive method to extract period information from XBRL facts using multiple strategies.

    This method tries multiple approaches to extract period dates:
    1. Context ID pattern matching (e.g., C_0001048911_20210601_20210831)
    2. XBRL context element parsing from document
    3. Fact element attributes
    4. Document text pattern matching near context references

    Args:
        fact_element: BeautifulSoup element representing the XBRL fact
        context_ref: Context reference ID (e.g., "C_cee655a3-71ec-4690-8e89-8ed39c1da945")
        document_soup: BeautifulSoup object of the entire document
        expected_period_date: Optional expected period end date for validation

    Returns:
        Dictionary with extracted period information:
        - period_start: Start date of the period (YYYY-MM-DD format)
        - period_end: End date of the period (YYYY-MM-DD format)
        - period_type: "instant" or "duration"
        - extraction_method: Which method successfully extracted the date
        - context_found: Whether the context was found and parsed
        - period_match_score: How well the extracted period matches expected (0-100)
    """

    output = VERBOSE_OUTPUT if VERBOSE_OUTPUT is not None else sys.stdout

    def vprint(*args, **kwargs):
        if VERBOSITY > 0:
            print(*args, **kwargs, file=output)

    def vvprint(*args, **kwargs):
        if VERBOSITY > 1:
            print(*args, **kwargs, file=output)

    def eprint(*args, **kwargs):
        if VERBOSITY >= 0:
            print(*args, **kwargs, file=output)

    def safe_parse_date(date_str: str) -> Optional[str]:
        """Safely parse and validate date string."""
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

    def calculate_period_match_score(period_end: str, expected_date: str) -> int:
        """Calculate how well the extracted period matches the expected date (0-100)."""
        if not period_end or not expected_date:
            return 0

        try:
            extracted_date = datetime.strptime(period_end, "%Y-%m-%d")
            expected_date_obj = datetime.strptime(expected_date, "%Y-%m-%d")

            # Calculate days difference
            days_diff = abs((extracted_date - expected_date_obj).days)

            # Perfect match = 100, each day difference reduces score
            if days_diff == 0:
                return 100
            elif days_diff <= 7:  # Within a week
                return max(90 - days_diff, 80)
            elif days_diff <= 30:  # Within a month
                return max(70 - (days_diff // 7), 50)
            elif days_diff <= 90:  # Within a quarter
                return max(40 - (days_diff // 30), 20)
            else:
                return 10  # Very poor match but still some value

        except ValueError:
            return 0

    # Initialize result
    result: Dict[str, Any] = {
        "period_start": None,
        "period_end": None,
        "period_type": None,
        "extraction_method": None,
        "context_found": False,
        "period_match_score": 0,
    }

    vvprint(f"Extracting period info for context: {context_ref}")

    # Strategy 1: Extract dates from context ID patterns
    vvprint("Trying Strategy 1: Context ID pattern matching")
    if context_ref:
        # Pattern 1: CIK_startdate_enddate (e.g., C_0001048911_20210601_20210831)
        date_pattern = r"(\d{8})_(\d{8})"
        match = re.search(date_pattern, context_ref)
        if match:
            start_str, end_str = match.groups()
            try:
                start_date = f"{start_str[:4]}-{start_str[4:6]}-{start_str[6:8]}"
                end_date = f"{end_str[:4]}-{end_str[4:6]}-{end_str[6:8]}"

                # Validate dates
                datetime.strptime(start_date, "%Y-%m-%d")
                datetime.strptime(end_date, "%Y-%m-%d")

                result["period_start"] = start_date
                result["period_end"] = end_date
                result["period_type"] = "duration"
                result["extraction_method"] = "context_id_pattern_duration"
                vvprint(f"[OK] Strategy 1 success: {start_date} to {end_date}")

                if expected_period_date:
                    result["period_match_score"] = calculate_period_match_score(
                        end_date, expected_period_date
                    )

                return result

            except ValueError as e:
                eprint(f"Invalid date format in context pattern: {e}")

        # Pattern 2: Single date (instant) - various formats
        single_date_patterns = [
            r"(\d{8})",  # YYYYMMDD
            r"(\d{4}-\d{2}-\d{2})",  # YYYY-MM-DD
            r"(\d{4}/\d{2}/\d{2})",  # YYYY/MM/DD
        ]

        for pattern in single_date_patterns:
            matches = re.findall(pattern, context_ref)
            if matches:
                # Use the last date found (most recent)
                date_str = matches[-1]
                parsed_date = safe_parse_date(date_str)
                if parsed_date:
                    result["period_end"] = parsed_date
                    result["period_type"] = "instant"
                    result["extraction_method"] = "context_id_pattern_instant"
                    vvprint(f"[OK] Strategy 1 success (instant): {parsed_date}")

                    if expected_period_date:
                        result["period_match_score"] = calculate_period_match_score(
                            parsed_date, expected_period_date
                        )

                    return result

    # Strategy 2: Parse XBRL context elements from document
    vvprint("Trying Strategy 2: XBRL context element parsing")
    if document_soup and context_ref:
        # Look for context element with matching ID
        context_selectors = [
            f'context[id="{context_ref}"]',
            f'xbrli\\:context[id="{context_ref}"]',
            f'[id="{context_ref}"]',
        ]

        context_element = None
        for selector in context_selectors:
            try:
                context_element = document_soup.select_one(selector)
                if context_element:
                    vvprint(f"Found context element using selector: {selector}")
                    break
            except Exception as e:
                eprint(f"Selector {selector} failed: {e}")
                continue

        if context_element:
            result["context_found"] = True

            # Look for period information within the context
            period_elem = context_element.find("period") or context_element.find_all(
                re.compile(r".*period.*", re.I)
            )

            if period_elem:
                if isinstance(period_elem, list):
                    period_elem = period_elem[0]

                # Check for instant date
                instant_elem = period_elem.find("instant") or period_elem.find_all(
                    re.compile(r".*instant.*", re.I)
                )

                if instant_elem:
                    if isinstance(instant_elem, list):
                        instant_elem = instant_elem[0]

                    instant_text = (
                        instant_elem.get_text().strip()
                        if hasattr(instant_elem, "get_text")
                        else str(instant_elem).strip()
                    )
                    parsed_date = safe_parse_date(instant_text)

                    if parsed_date:
                        result["period_end"] = parsed_date
                        result["period_type"] = "instant"
                        result["extraction_method"] = "xbrl_context_instant"
                        vvprint(f"[OK] Strategy 2 success (instant): {parsed_date}")

                        if expected_period_date:
                            result["period_match_score"] = calculate_period_match_score(
                                parsed_date, expected_period_date
                            )

                        return result

                # Check for duration (start and end dates)
                start_elem = (
                    period_elem.find("startdate")
                    or period_elem.find("startDate")
                    or period_elem.find_all(re.compile(r".*start.*", re.I))
                )
                end_elem = (
                    period_elem.find("enddate")
                    or period_elem.find("endDate")
                    or period_elem.find_all(re.compile(r".*end.*", re.I))
                )

                if start_elem and end_elem:
                    if isinstance(start_elem, list):
                        start_elem = start_elem[0]
                    if isinstance(end_elem, list):
                        end_elem = end_elem[0]

                    start_text = (
                        start_elem.get_text().strip()
                        if hasattr(start_elem, "get_text")
                        else str(start_elem).strip()
                    )
                    end_text = (
                        end_elem.get_text().strip()
                        if hasattr(end_elem, "get_text")
                        else str(end_elem).strip()
                    )

                    start_date = safe_parse_date(start_text)
                    end_date = safe_parse_date(end_text)

                    if start_date and end_date:
                        result["period_start"] = start_date
                        result["period_end"] = end_date
                        result["period_type"] = "duration"
                        result["extraction_method"] = "xbrl_context_duration"
                        vvprint(
                            f"[OK] Strategy 2 success (duration): {start_date} to {end_date}"
                        )

                        if expected_period_date:
                            result["period_match_score"] = calculate_period_match_score(
                                end_date, expected_period_date
                            )

                        return result

    # Strategy 3: Extract from fact element attributes
    vvprint("Trying Strategy 3: Fact element attributes")
    if fact_element:
        # Look for period-related attributes on the fact element itself
        period_attrs = [
            "period",
            "contextref",
            "period_end",
            "period_start",
            "enddate",
            "startdate",
        ]

        for attr in period_attrs:
            attr_value = None
            if hasattr(fact_element, "attrs") and attr in fact_element.attrs:
                attr_value = fact_element.attrs[attr]
            elif hasattr(fact_element, "get"):
                attr_value = fact_element.get(attr)

            if attr_value:
                parsed_date = safe_parse_date(str(attr_value))
                if parsed_date:
                    result["period_end"] = parsed_date
                    result["period_type"] = "instant"
                    result["extraction_method"] = f"fact_attribute_{attr}"
                    vvprint(f"[OK] Strategy 3 success: {parsed_date} from {attr}")

                    if expected_period_date:
                        result["period_match_score"] = calculate_period_match_score(
                            parsed_date, expected_period_date
                        )

                    return result

    # Strategy 4: Document text pattern matching near context references
    vvprint("Trying Strategy 4: Document text pattern matching")
    if document_soup and context_ref:
        # Search for the context reference in the document text and look for nearby dates
        document_text = str(document_soup)

        # Find the position of the context reference
        context_pos = document_text.find(context_ref)
        if context_pos != -1:
            # Look in a window around the context reference (±500 characters)
            window_start = max(0, context_pos - 500)
            window_end = min(len(document_text), context_pos + 500)
            context_window = document_text[window_start:window_end]

            # Look for date patterns in the window
            date_patterns = [
                r"(\d{4}-\d{2}-\d{2})",  # YYYY-MM-DD
                r"(\d{2}/\d{2}/\d{4})",  # MM/DD/YYYY
                r"(\d{4}/\d{2}/\d{2})",  # YYYY/MM/DD
                r"(\d{8})",  # YYYYMMDD
            ]

            found_dates = []
            for pattern in date_patterns:
                matches = re.findall(pattern, context_window)
                for match in matches:
                    parsed_date = safe_parse_date(match)
                    if parsed_date:
                        found_dates.append(parsed_date)

            if found_dates:
                # Use the most recent date found
                found_dates.sort()
                recent_date = found_dates[-1]

                result["period_end"] = recent_date
                result["period_type"] = "instant"
                result["extraction_method"] = "document_text_pattern"
                vvprint(f"[OK] Strategy 4 success: {recent_date}")

                if expected_period_date:
                    result["period_match_score"] = calculate_period_match_score(
                        recent_date, expected_period_date
                    )

                return result

    eprint("[X] All strategies failed - no period information extracted")
    result["extraction_method"] = "failed"
    return result
