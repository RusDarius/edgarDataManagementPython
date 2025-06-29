import requests
from bs4 import BeautifulSoup, Tag
import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime


def extract_comprehensive_period_info(
    fact_element,
    context_ref: Optional[str],
    document_soup,
    expected_period_date: Optional[str] = None,
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
    result = {
        "period_start": None,
        "period_end": None,
        "period_type": None,
        "extraction_method": None,
        "context_found": False,
        "period_match_score": 0,
    }

    print(f"Extracting period info for context: {context_ref}")

    # Strategy 1: Extract dates from context ID patterns
    print("Trying Strategy 1: Context ID pattern matching")
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
                print(f"[OK] Strategy 1 success: {start_date} to {end_date}")

                if expected_period_date:
                    result["period_match_score"] = calculate_period_match_score(
                        end_date, expected_period_date
                    )

                return result

            except ValueError as e:
                print(f"Invalid date format in context pattern: {e}")

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
                    print(f"[OK] Strategy 1 success (instant): {parsed_date}")

                    if expected_period_date:
                        result["period_match_score"] = calculate_period_match_score(
                            parsed_date, expected_period_date
                        )

                    return result

    # Strategy 2: Parse XBRL context elements from document
    print("Trying Strategy 2: XBRL context element parsing")
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
                    print(f"Found context element using selector: {selector}")
                    break
            except Exception as e:
                print(f"Selector {selector} failed: {e}")
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
                        print(f"[OK] Strategy 2 success (instant): {parsed_date}")

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
                        print(
                            f"[OK] Strategy 2 success (duration): {start_date} to {end_date}"
                        )

                        if expected_period_date:
                            result["period_match_score"] = calculate_period_match_score(
                                end_date, expected_period_date
                            )

                        return result

    # Strategy 3: Extract from fact element attributes
    print("Trying Strategy 3: Fact element attributes")
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
                    print(f"[OK] Strategy 3 success: {parsed_date} from {attr}")

                    if expected_period_date:
                        result["period_match_score"] = calculate_period_match_score(
                            parsed_date, expected_period_date
                        )

                    return result

    # Strategy 4: Document text pattern matching near context references
    print("Trying Strategy 4: Document text pattern matching")
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
                print(f"[OK] Strategy 4 success: {recent_date}")

                if expected_period_date:
                    result["period_match_score"] = calculate_period_match_score(
                        recent_date, expected_period_date
                    )

                return result

    print("[X] All strategies failed - no period information extracted")
    result["extraction_method"] = "failed"
    return result


def fetch_and_parse_submission(cik, form_type, fiscal_period, fiscal_year, fact_tag):
    """
    Fetches the SEC submission for a given CIK, form type (e.g., '10-Q'), fiscal period (e.g., 'Q2'), and fiscal year (e.g., 2023),
    then parses the primary document for the specified fact tag (e.g., 'RevenueFromContractWithCustomerExcludingAssessedTax').
    Uses ApiClient.get_submission_by_form_period for robust fetching.

    This function implements a fallback approach: if multiple values for the same fact tag are found,
    it selects the one with the latest/most recent context date to ensure the most current value is returned.
    This ensures that even if period matching fails, a value is still extracted.

    Returns a dictionary with:
    - document_text: The full document text
    - fact_value: The value of the requested XBRL fact tag if found
    - fact_unit: The unit of measurement for the fact (e.g., USD)
    - fact_context: The context ID for the fact (provides period information)
    - fact_decimals: The decimal precision of the fact value
    - error: Error message if any
    """
    from data_loaders.api_client import ApiClient
    import requests
    from bs4 import BeautifulSoup, Tag
    import re
    from typing import Dict, Any

    # Helper function to safely extract text from BeautifulSoup elements
    def safe_get_text(element) -> str:
        """Safely extract text from a BeautifulSoup element."""
        if element is None:
            return ""
        if hasattr(element, "get_text"):
            return element.get_text().strip()
        return str(element).strip()

    # Helper function to safely get attributes from BeautifulSoup elements
    def safe_get_attr(element, attr_name: str, default=None):
        """Safely get attribute from a BeautifulSoup element."""
        if element is None:
            return default

        # For Tag objects that have an attrs dictionary
        if isinstance(element, Tag) and hasattr(element, "attrs"):
            return element.attrs.get(attr_name, default)

        # Try getattr for objects that might have attributes as properties
        try:
            return getattr(element, attr_name, default)
        except (AttributeError, TypeError):
            pass

        # Last resort - try accessing as dictionary
        try:
            return element.get(attr_name, default)
        except (AttributeError, TypeError):
            return default

    # Use type annotations to fix Pylance errors
    result: Dict[str, Any] = {
        "document_text": None,
        "fact_value": None,
        "fact_unit": None,
        "fact_context": None,
        "fact_decimals": None,
        "error": None,
    }

    cik_str = str(cik).zfill(10)
    client = ApiClient(user_agent="Barnnabass daniOO7XbX@gmail.com")
    submission = client.get_submission_by_form_period(
        cik_str, form_type, fiscal_period, fiscal_year
    )
    if not submission:
        result["error"] = "No matching submission found."
        return result

    url = submission["url"]
    print(f"Fetching: {url}")
    resp = requests.get(url, headers={"User-Agent": "TestUser test@example.com"})

    if resp.status_code != 200:
        result["error"] = f"Failed to fetch {url}, status code: {resp.status_code}"
        return result

    document_text = resp.text
    result["document_text"] = document_text

    # Check if it's an XBRL document or inline XBRL (iXBRL)
    try:
        # First try to detect inline XBRL which is more common in recent submissions
        is_ixbrl = any(
            tag in document_text.lower()
            for tag in [
                "<ix:",
                "xmlns:ix=",
                "inline xbrl",
                "<html",
                "inline xbrl",
                "ixbrlmember",
                "ixbrl-member",
            ]
        )

        if is_ixbrl:
            print("Detected inline XBRL (iXBRL) document")

            # Parse with BeautifulSoup using HTML parser for inline XBRL
            soup = BeautifulSoup(document_text, "html.parser")

            # Collect all potential facts first
            all_potential_facts = []

            # Method 1: Look for span/div with name attribute
            print("Attempting method 1: searching by name attribute")
            facts = soup.find_all(attrs={"name": fact_tag})
            for fact in facts:
                print(f"Found fact by name attribute: {fact}")
                fact_value = safe_get_text(fact)

                if fact_value:
                    all_potential_facts.append(
                        {
                            "value": fact_value,
                            "context": safe_get_attr(fact, "contextref"),
                            "unit": safe_get_attr(fact, "unitref")
                            or safe_get_attr(fact, "format"),
                            "decimals": safe_get_attr(fact, "decimals")
                            or safe_get_attr(fact, "scale"),
                            "element": fact,
                        }
                    )

            # Method 2: Look for inline XBRL tags
            print("Attempting method 2: searching inline XBRL tags")
            for tag_name in ["ix:nonfraction", "ix:nonnumeric", "ix:fraction"]:
                facts = soup.find_all(tag_name.replace(":", ":"))
                for fact in facts:
                    fact_name = safe_get_attr(fact, "name", "")
                    if isinstance(fact_name, str) and (
                        fact_name == fact_tag or fact_name.endswith(":" + fact_tag)
                    ):
                        print(f"Found inline XBRL fact: {fact}")
                        fact_value = safe_get_text(fact)

                        if fact_value:
                            all_potential_facts.append(
                                {
                                    "value": fact_value,
                                    "context": safe_get_attr(fact, "contextref"),
                                    "unit": safe_get_attr(fact, "unitref")
                                    or safe_get_attr(fact, "format"),
                                    "decimals": safe_get_attr(fact, "decimals")
                                    or safe_get_attr(fact, "scale"),
                                    "element": fact,
                                }
                            )

            # If we found potential facts, select the best one using comprehensive extraction
            if all_potential_facts:
                print(
                    f"Found {len(all_potential_facts)} potential facts, selecting the best one using comprehensive extraction"
                )

                # Use the new comprehensive fact selection method
                best_fact = update_fact_selection_with_comprehensive_extraction(
                    all_potential_facts,
                    soup,
                    None,  # No expected_period_date for backward compatibility
                )

                if best_fact:
                    # Set the result from the best fact
                    result["fact_value"] = best_fact["value"]
                    result["fact_context"] = best_fact["context"]
                    result["fact_unit"] = best_fact["unit"]
                    result["fact_decimals"] = best_fact["decimals"]

                    # Try to convert to number if appropriate
                    try:
                        num_value = result["fact_value"].replace(",", "")
                        result["fact_value"] = float(num_value)
                    except ValueError:
                        # Keep as string if not convertible to number
                        pass

                    print(
                        f"Selected fact value: {result['fact_value']} with unit: {result['fact_unit']} from context: {result['fact_context']}"
                    )
                else:
                    result["error"] = (
                        "No suitable fact could be selected from potential facts"
                    )
                return result

            print("Attempting method 3: text-based search")
            # Method 3: Try searching for the tag in text and extracting nearby numbers
            # Find text containing the fact tag
            tag_elements = soup.find_all(text=re.compile(re.escape(fact_tag)))

            for text in tag_elements:
                parent = text.parent if hasattr(text, "parent") else None
                context = safe_get_text(parent) if parent else str(text)
                print(f"Found text containing {fact_tag}: {context[:100]}...")

                # Look for numbers in the context
                numbers = re.findall(
                    r"[\$€£]?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?(?:million|billion|thousand|M|B|K)?",
                    context,
                )
                if numbers:
                    # Use the closest number to the fact tag
                    result["fact_value"] = numbers[0].replace(",", "")
                    try:
                        result["fact_value"] = float(result["fact_value"])
                    except ValueError:
                        pass

                    # Try to determine the unit
                    if "$" in context:
                        result["fact_unit"] = "USD"
                    elif "€" in context:
                        result["fact_unit"] = "EUR"
                    elif "£" in context:
                        result["fact_unit"] = "GBP"

                    # Check for scale
                    if "million" in context.lower() or "M" in context:
                        if isinstance(result["fact_value"], (int, float)):
                            result["fact_value"] *= 1_000_000
                    elif "billion" in context.lower() or "B" in context:
                        if isinstance(result["fact_value"], (int, float)):
                            result["fact_value"] *= 1_000_000_000
                    elif "thousand" in context.lower() or "K" in context:
                        if isinstance(result["fact_value"], (int, float)):
                            result["fact_value"] *= 1_000

                    print(
                        f"Extracted approximate value: {result['fact_value']} {result['fact_unit']}"
                    )
                    return result

        # If not inline XBRL or failed to extract from iXBRL, try standard XBRL
        elif "<xbrl" in document_text.lower():
            print("Detected standard XBRL document")

            # Parse with BeautifulSoup
            soup = BeautifulSoup(
                document_text, "lxml-xml"
            )  # Find namespace prefixes by examining root element attributes
            ns_prefixes = []
            root_elem = soup.find()  # Get the root element

            # Safely get attributes from the root element
            if root_elem:
                if isinstance(root_elem, Tag) and hasattr(root_elem, "attrs"):
                    # For Tag objects that have attrs dictionary
                    for attr, value in root_elem.attrs.items():
                        if "xmlns:" in attr and "gaap" in attr.lower():
                            ns_prefix = attr.split(":")[1]
                            ns_prefixes.append(ns_prefix)
                else:
                    # For non-Tag objects, try to get attributes by name
                    all_attrs = dir(root_elem)
                    for attr in all_attrs:
                        if attr.startswith("xmlns:") and "gaap" in attr.lower():
                            ns_prefix = attr.split(":")[1]
                            ns_prefixes.append(ns_prefix)

            print(f"Found namespace prefixes: {ns_prefixes}")

            # Try with each prefix and without prefix
            all_xbrl_facts = []
            search_prefixes = ns_prefixes if ns_prefixes else ["us-gaap"]
            search_prefixes.append("")  # Also try without prefix

            for prefix in search_prefixes:
                # Format the tag pattern based on whether we have a prefix
                tag_pattern = f"{prefix}:{fact_tag}" if prefix else fact_tag
                fact_elements = soup.find_all(tag_pattern)

                for fact in fact_elements:
                    print(f"Found fact: {fact}")

                    # Get the fact value and context
                    value = fact.get_text().strip() if fact else ""
                    if value:
                        all_xbrl_facts.append(
                            {
                                "value": value,
                                "context": safe_get_attr(fact, "contextRef"),
                                "unit": safe_get_attr(fact, "unitRef"),
                                "decimals": safe_get_attr(fact, "decimals"),
                                "element": fact,
                            }
                        )

            # If we found XBRL facts, select the best one (latest context if multiple)
            if all_xbrl_facts:
                print(f"Found {len(all_xbrl_facts)} XBRL facts, selecting the best one")

                # Try to find the fact with the latest/most recent context
                best_fact = None
                latest_date = None

                for fact_data in all_xbrl_facts:
                    context_ref = fact_data["context"]
                    if context_ref:
                        # Try to extract date from context ID
                        import re

                        date_pattern = r"(\d{8})"
                        matches = re.findall(date_pattern, context_ref)
                        if matches:
                            # Use the last (most recent) date found in the context ID
                            date_str = matches[-1]
                            try:
                                from datetime import datetime

                                context_date = datetime.strptime(date_str, "%Y%m%d")
                                if latest_date is None or context_date > latest_date:
                                    latest_date = context_date
                                    best_fact = fact_data
                                    print(
                                        f"Updated best XBRL fact to context {context_ref} with date {date_str}"
                                    )
                            except ValueError:
                                pass

                # If no date-based selection worked, use the first fact found
                if not best_fact:
                    best_fact = all_xbrl_facts[0]
                    print(
                        "Using first available XBRL fact as no date-based selection possible"
                    )

                # Set the result from the best fact
                result["fact_value"] = best_fact["value"]
                result["fact_context"] = best_fact["context"]
                result["fact_unit"] = best_fact["unit"]
                result["fact_decimals"] = best_fact["decimals"]

                # Try to convert value to number if possible
                try:
                    num_value = result["fact_value"].replace(",", "")
                    result["fact_value"] = float(num_value)
                except ValueError:
                    # Keep as string if not convertible to number
                    pass

                print(
                    f"Selected XBRL fact value: {result['fact_value']} with unit: {result['fact_unit']} from context: {result['fact_context']}"
                )
                return result

        # If we still haven't found it, try text-based approaches
        if not result["fact_value"]:
            # Try direct regex search for XBRL tag
            pattern = f"<[^>]*:{fact_tag}[^>]*>(.*?)</[^>]*:{fact_tag}>"
            matches = re.findall(pattern, document_text)
            if matches:
                result["fact_value"] = matches[0].strip()
                # Try to extract context and unit from surrounding XML
                context_match = re.search(
                    f'<[^>]*:{fact_tag}[^>]*contextRef="([^"]*)"', document_text
                )
                unit_match = re.search(
                    f'<[^>]*:{fact_tag}[^>]*unitRef="([^"]*)"', document_text
                )
                decimals_match = re.search(
                    f'<[^>]*:{fact_tag}[^>]*decimals="([^"]*)"', document_text
                )

                if context_match:
                    result["fact_context"] = context_match.group(1)
                if unit_match:
                    result["fact_unit"] = unit_match.group(1)
                if decimals_match:
                    result["fact_decimals"] = decimals_match.group(1)

                # Try to convert value to number if possible
                try:
                    if result["fact_value"]:
                        num_value = result["fact_value"].replace(",", "")
                        result["fact_value"] = float(num_value)
                except ValueError:
                    # Keep as string if not convertible to number
                    pass

                print(f"Found fact value with regex: {result['fact_value']}")
                return result

            # If all XBRL parsing failed, try plain text search
            if fact_tag in document_text:
                print(f"Found {fact_tag} in document text, trying to extract value")

                # Pattern to find a dollar amount within 100 chars of the fact tag
                pattern = f"{fact_tag}[\\s\\S]{{0,100}}?\\$(\\d+(?:,\\d+)*(?:\\.\\d+)?)"
                matches = re.findall(pattern, document_text)
                if matches:
                    try:
                        result["fact_value"] = float(matches[0].replace(",", ""))
                        result["fact_unit"] = "USD"  # Assume USD if $ symbol was found
                        print(
                            f"Extracted approximate value: {result['fact_value']} {result['fact_unit']}"
                        )
                        return result
                    except ValueError:
                        result["error"] = "Found value but couldn't convert to number"
                else:
                    # Try with a more generic number pattern
                    pattern = (
                        f"{fact_tag}[\\s\\S]{{0,100}}?(\\d+(?:,\\d+)*(?:\\.\\d+)?)"
                    )
                    matches = re.findall(pattern, document_text)
                    if matches:
                        try:
                            result["fact_value"] = float(matches[0].replace(",", ""))
                            print(
                                f"Extracted approximate value: {result['fact_value']}"
                            )
                            return result
                        except ValueError:
                            result["error"] = (
                                "Found value but couldn't convert to number"
                            )
                    else:
                        result["error"] = (
                            f"Found {fact_tag} in text but couldn't extract a value"
                        )
            else:
                result["error"] = f"{fact_tag} not found in document"

    except Exception as e:
        result["error"] = f"Error parsing document: {str(e)}"
        print(f"Error: {result['error']}")
        import traceback

        print(traceback.format_exc())

    if result["error"]:
        print(result["error"])

    return result


def update_fact_selection_with_comprehensive_extraction(
    all_potential_facts: List[Dict],
    document_soup,
    expected_period_date: Optional[str] = None,
) -> Optional[Dict]:
    """
    Updated fact selection logic using the comprehensive period extraction method.

    Args:
        all_potential_facts: List of potential facts found
        document_soup: BeautifulSoup object of the entire document
        expected_period_date: Optional expected period end date for validation

    Returns:
        Best fact selected using comprehensive extraction, or None if no facts available
    """
    if not all_potential_facts:
        return None

    print(
        f"Selecting best fact from {len(all_potential_facts)} candidates using comprehensive extraction"
    )

    # Extract comprehensive period info for each fact
    enhanced_facts = []

    for fact_data in all_potential_facts:
        context_ref = fact_data.get("context")
        fact_element = fact_data.get("element")

        # Extract comprehensive period information
        period_info = extract_comprehensive_period_info(
            fact_element, context_ref, document_soup, expected_period_date
        )

        # Enhance the fact data with extracted period information
        enhanced_fact = fact_data.copy()
        enhanced_fact.update(
            {
                "period_start": period_info["period_start"],
                "period_end": period_info["period_end"],
                "period_type": period_info["period_type"],
                "extraction_method": period_info["extraction_method"],
                "context_found": period_info["context_found"],
                "period_match_score": period_info["period_match_score"],
            }
        )

        enhanced_facts.append(enhanced_fact)

        print(
            f"Enhanced fact: context={context_ref}, period_end={period_info['period_end']}, "
            f"method={period_info['extraction_method']}, score={period_info['period_match_score']}"
        )

    # Selection strategy: prioritize by period match score and recency
    best_fact = None

    # Strategy 1: If expected_period_date provided, select highest scoring match
    if expected_period_date:
        facts_with_scores = [
            f for f in enhanced_facts if f.get("period_match_score", 0) > 0
        ]
        if facts_with_scores:
            facts_with_scores.sort(key=lambda x: x["period_match_score"], reverse=True)
            best_fact = facts_with_scores[0]
            print(
                f"Selected fact with highest period match score: {best_fact['period_match_score']}"
            )

    # Strategy 2: Select most recent fact by period_end date
    if not best_fact:
        facts_with_dates = [f for f in enhanced_facts if f.get("period_end")]
        if facts_with_dates:
            try:
                # Sort by period_end date (most recent first)
                facts_with_dates.sort(
                    key=lambda x: datetime.strptime(x["period_end"], "%Y-%m-%d"),
                    reverse=True,
                )
                best_fact = facts_with_dates[0]
                print(
                    f"Selected most recent fact by period_end: {best_fact['period_end']}"
                )
            except ValueError as e:
                print(f"Error sorting by date: {e}")

    # Strategy 3: Fallback to first available fact
    if not best_fact:
        best_fact = enhanced_facts[0]
        print("Using first available fact as fallback")

    return best_fact


def fetch_and_parse_submission_enhanced(
    cik, form_type, fiscal_period, fiscal_year, fact_tag, expected_period_date=None
):
    """
    Enhanced version of fetch_and_parse_submission that ensures the extracted fact value
    corresponds to the correct reporting period by matching XBRL contexts.

    Args:
        cik: Company CIK
        form_type: Form type (e.g., '10-Q', '10-K')
        fiscal_period: Fiscal period (e.g., 'Q1', 'Q2', 'Q3', 'Q4', 'FY')
        fiscal_year: Fiscal year (e.g., 2023)
        fact_tag: XBRL fact tag to extract (e.g., 'RevenueFromContractWithCustomerExcludingAssessedTax')
        expected_period_date: Optional expected period date for validation (YYYY-MM-DD format)

    Returns:
        Dict with enhanced period validation including:
        - fact_value: The value of the fact for the correct period
        - fact_context: The context ID that matched the period
        - fact_period_start: Start date of the period context
        - fact_period_end: End date of the period context
        - period_validation: Whether the extracted period matches expected
        - all_contexts_found: List of all contexts found for debugging
    """
    from data_loaders.api_client import ApiClient
    import requests
    from bs4 import BeautifulSoup, Tag
    import re
    from typing import Dict, Any
    import datetime

    def safe_get_text(element) -> str:
        """Safely extract text from a BeautifulSoup element."""
        if element is None:
            return ""
        if hasattr(element, "get_text"):
            return element.get_text().strip()
        return str(element).strip()

    def safe_get_attr(element, attr_name: str, default=None):
        """Safely get attribute from a BeautifulSoup element."""
        if element is None:
            return default
        if isinstance(element, Tag) and hasattr(element, "attrs"):
            return element.attrs.get(attr_name, default)
        try:
            return getattr(element, attr_name, default)
        except (AttributeError, TypeError):
            pass
        try:
            return element.get(attr_name, default)
        except (AttributeError, TypeError):
            return default

    def parse_context_period(context_element):
        """
        Parse XBRL context to extract period information.
        Returns dict with startDate, endDate, instant, and period_type.
        """
        period_info: Dict[str, Any] = {
            "startDate": None,
            "endDate": None,
            "instant": None,
            "period_type": None,
        }

        if not context_element:
            return period_info

        # Look for period element within context
        period_elem = context_element.find("period")
        if not period_elem:
            return period_info

        # Check for instant (point-in-time) vs duration (period)
        instant_elem = period_elem.find("instant")
        if instant_elem:
            instant_text = safe_get_text(instant_elem)
            # Validate date format
            if (
                instant_text
                and len(instant_text) == 10
                and instant_text.count("-") == 2
            ):
                try:
                    # Try to parse the date to validate it
                    datetime.datetime.strptime(instant_text, "%Y-%m-%d")
                    period_info["instant"] = instant_text
                    period_info["period_type"] = "instant"
                    period_info["endDate"] = period_info["instant"]
                except ValueError:
                    print(f"Invalid instant date format: {instant_text}")
        else:
            # Look for duration (startDate and endDate)
            start_elem = period_elem.find("startdate") or period_elem.find("startDate")
            end_elem = period_elem.find("enddate") or period_elem.find("endDate")

            if start_elem and end_elem:
                start_text = safe_get_text(start_elem)
                end_text = safe_get_text(end_elem)

                # Validate date formats
                start_valid = False
                end_valid = False

                if start_text and len(start_text) == 10 and start_text.count("-") == 2:
                    try:
                        datetime.datetime.strptime(start_text, "%Y-%m-%d")
                        start_valid = True
                    except ValueError:
                        print(f"Invalid start date format: {start_text}")

                if end_text and len(end_text) == 10 and end_text.count("-") == 2:
                    try:
                        datetime.datetime.strptime(end_text, "%Y-%m-%d")
                        end_valid = True
                    except ValueError:
                        print(f"Invalid end date format: {end_text}")

                if start_valid and end_valid:
                    period_info["startDate"] = start_text
                    period_info["endDate"] = end_text
                    period_info["period_type"] = "duration"

        return period_info

    def extract_date_from_context_id(context_id):
        """
        Extract dates from context ID patterns commonly used in XBRL.
        Examples:
        - C_0001048911_20210601_20210831 -> start: 2021-06-01, end: 2021-08-31
        - C_cee655a3-71ec-4690-8e89-8ed39c1da945 -> None (need to parse XML)
        """
        if not context_id:
            return None, None

        # Pattern for CIK_startdate_enddate format
        date_pattern = r"(\d{8})_(\d{8})"
        match = re.search(date_pattern, context_id)
        if match:
            start_str, end_str = match.groups()
            try:
                start_date = f"{start_str[:4]}-{start_str[4:6]}-{start_str[6:8]}"
                end_date = f"{end_str[:4]}-{end_str[4:6]}-{end_str[6:8]}"
                return start_date, end_date
            except:
                pass

        # Pattern for single date (instant)
        single_date_pattern = r"(\d{8})"
        match = re.search(single_date_pattern, context_id)
        if match:
            date_str = match.group(1)
            try:
                date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                return None, date  # None for start, date for end/instant
            except:
                pass

        return None, None

    def is_period_match(period_info, expected_period_date, fiscal_period, fiscal_year):
        """
        Determine if the extracted period matches what we expect for the given fiscal period/year.
        """
        if not period_info.get("endDate"):
            return False

        try:
            end_date = datetime.datetime.strptime(period_info["endDate"], "%Y-%m-%d")
            expected_year = fiscal_year

            # For quarterly reports, we expect the end date to be around the quarter end
            # This is a simplified check - could be enhanced with company-specific fiscal calendars
            if fiscal_period == "Q1":
                # Q1 typically ends in Aug/Sep for May fiscal year end companies like FDX
                return end_date.month in [8, 9] and end_date.year == expected_year
            elif fiscal_period == "Q2":
                return end_date.month in [11, 12] and end_date.year == expected_year
            elif fiscal_period == "Q3":
                return end_date.month in [2, 3] and (
                    end_date.year == expected_year or end_date.year == expected_year + 1
                )
            elif fiscal_period == "Q4":
                return end_date.month in [5, 6] and (
                    end_date.year == expected_year or end_date.year == expected_year + 1
                )
            elif fiscal_period == "FY":
                return end_date.month in [5, 6] and (
                    end_date.year == expected_year or end_date.year == expected_year + 1
                )

            # If expected_period_date is provided, use exact match
            if expected_period_date:
                return period_info["endDate"] == expected_period_date

        except (ValueError, TypeError) as e:
            print(f"Error parsing dates for period matching: {e}")

        return False

    # Initialize result structure
    result: Dict[str, Any] = {
        "document_text": None,
        "fact_value": None,
        "fact_unit": None,
        "fact_context": None,
        "fact_decimals": None,
        "fact_period_start": None,
        "fact_period_end": None,
        "period_validation": False,
        "all_contexts_found": [],
        "error": None,
    }

    # Fetch submission using existing logic
    cik_str = str(cik).zfill(10)
    client = ApiClient(user_agent="Barnnabass daniOO7XbX@gmail.com")
    submission = client.get_submission_by_form_period(
        cik_str, form_type, fiscal_period, fiscal_year
    )
    if not submission:
        result["error"] = "No matching submission found."
        return result

    url = submission["url"]
    print(f"Fetching: {url}")
    resp = requests.get(url, headers={"User-Agent": "TestUser test@example.com"})

    if resp.status_code != 200:
        result["error"] = f"Failed to fetch {url}, status code: {resp.status_code}"
        return result

    document_text = resp.text
    result["document_text"] = document_text

    try:
        # Check if it's inline XBRL (most common in recent filings)
        is_ixbrl = any(
            tag in document_text.lower()
            for tag in [
                "<ix:",
                "xmlns:ix=",
                "inline xbrl",
                "<html",
                "ixbrlmember",
                "ixbrl-member",
            ]
        )

        if is_ixbrl:
            print("Detected inline XBRL (iXBRL) document")
            soup = BeautifulSoup(document_text, "html.parser")

            # Step 1: Find all contexts in the document
            print("Step 1: Extracting all XBRL contexts...")
            context_elements = soup.find_all("xbrli:context") or soup.find_all(
                "context"
            )
            contexts_info = {}

            for ctx in context_elements:
                ctx_id = safe_get_attr(ctx, "id")
                if ctx_id:
                    period_info = parse_context_period(ctx)
                    contexts_info[ctx_id] = period_info
                    print(f"Context {ctx_id}: {period_info}")

            result["all_contexts_found"] = list(contexts_info.keys())

            # Step 2: Find all facts with the target tag
            print(f"Step 2: Finding all facts for tag '{fact_tag}'...")
            potential_facts = []

            # Method 1: Look for inline XBRL tags with the fact
            for tag_name in ["ix:nonfraction", "ix:nonnumeric", "ix:fraction"]:
                facts = soup.find_all(tag_name.replace(":", ":"))
                for fact in facts:
                    fact_name = safe_get_attr(fact, "name", "")
                    if isinstance(fact_name, str) and (
                        fact_name == fact_tag or fact_name.endswith(":" + fact_tag)
                    ):
                        context_ref = safe_get_attr(fact, "contextref")
                        fact_value = safe_get_text(fact)

                        potential_facts.append(
                            {
                                "element": fact,
                                "value": fact_value,
                                "context_ref": context_ref,
                                "unit_ref": safe_get_attr(fact, "unitref"),
                                "decimals": safe_get_attr(fact, "decimals"),
                                "scale": safe_get_attr(fact, "scale"),
                            }
                        )
                        print(f"Found fact: value={fact_value}, context={context_ref}")

            # Method 2: Look for span/div with name attribute
            if not potential_facts:
                facts = soup.find_all(attrs={"name": fact_tag})
                for fact in facts:
                    context_ref = safe_get_attr(fact, "contextref")
                    fact_value = safe_get_text(fact)

                    potential_facts.append(
                        {
                            "element": fact,
                            "value": fact_value,
                            "context_ref": context_ref,
                            "unit_ref": safe_get_attr(fact, "unitref")
                            or safe_get_attr(fact, "format"),
                            "decimals": safe_get_attr(fact, "decimals")
                            or safe_get_attr(fact, "scale"),
                            "scale": safe_get_attr(fact, "scale"),
                        }
                    )

            print(f"Found {len(potential_facts)} potential facts")

            # Step 3: Find the fact with the correct period context
            best_fact = None
            best_period_info = None
            latest_fact = None
            latest_period_info = None
            latest_end_date = None

            for fact_data in potential_facts:
                context_ref = fact_data["context_ref"]
                print(f"Evaluating fact with context: {context_ref}")

                # First, try to get period info from parsed contexts
                period_info = contexts_info.get(context_ref)

                # If not found, try to extract from context ID pattern
                if not period_info or not period_info.get("endDate"):
                    start_date, end_date = extract_date_from_context_id(context_ref)
                    if end_date:
                        period_info = {
                            "startDate": start_date,
                            "endDate": end_date,
                            "period_type": "duration" if start_date else "instant",
                        }

                if period_info and period_info.get("endDate"):
                    print(f"Period info for {context_ref}: {period_info}")

                    # Track the latest available fact (fallback option)
                    try:
                        end_date = datetime.datetime.strptime(
                            period_info["endDate"], "%Y-%m-%d"
                        )
                        if latest_end_date is None or end_date > latest_end_date:
                            latest_end_date = end_date
                            latest_fact = fact_data
                            latest_period_info = period_info
                            print(
                                f"Updated latest fact to context {context_ref} with end date {period_info['endDate']}"
                            )
                    except ValueError:
                        print(f"Could not parse end date: {period_info['endDate']}")

                    # Check if this period matches our target
                    is_match = is_period_match(
                        period_info, expected_period_date, fiscal_period, fiscal_year
                    )
                    print(f"Period match for {context_ref}: {is_match}")

                    if is_match:
                        best_fact = fact_data
                        best_period_info = period_info
                        print(f"Selected fact with context {context_ref} as best match")
                        break
                    elif not best_fact:
                        # Keep as fallback if no better match found
                        best_fact = fact_data
                        best_period_info = period_info
                        print(f"Keeping {context_ref} as fallback option")

            # If no period-matched fact was found, use the latest available fact
            if not best_fact and latest_fact:
                print(
                    f"No period match found, using latest available fact from {latest_fact['context_ref']}"
                )
                best_fact = latest_fact
                best_period_info = latest_period_info
                result["period_validation"] = False  # Mark as not period-matched
            elif not best_fact and potential_facts:
                # If we have facts but no period info, just use the first one
                print("No period info available, using first available fact")
                best_fact = potential_facts[0]
                result["period_validation"] = False

            # Step 4: Process the best fact found
            if best_fact:
                result["fact_value"] = best_fact["value"]
                result["fact_context"] = best_fact["context_ref"]
                result["fact_unit"] = best_fact["unit_ref"]
                result["fact_decimals"] = best_fact["decimals"]

                if best_period_info:
                    result["fact_period_start"] = best_period_info.get("startDate")
                    result["fact_period_end"] = best_period_info.get("endDate")
                    result["period_validation"] = is_period_match(
                        best_period_info,
                        expected_period_date,
                        fiscal_period,
                        fiscal_year,
                    )

                # Convert to number if possible
                try:
                    if result["fact_value"]:
                        num_value = result["fact_value"].replace(",", "")
                        result["fact_value"] = float(num_value)

                        # Apply scale/decimals transformation
                        if result["fact_decimals"]:
                            try:
                                decimals = int(result["fact_decimals"])
                                if decimals < 0:  # Scale factor
                                    result["fact_value"] *= 10 ** abs(decimals)
                            except ValueError:
                                pass

                except ValueError:
                    pass  # Keep as string if not convertible

                print(
                    f"Final result: {result['fact_value']} {result['fact_unit']} (period: {result['fact_period_end']}, validated: {result['period_validation']})"
                )
                return result

            else:
                result["error"] = f"No facts found for {fact_tag} with valid context"

        # If not iXBRL or no facts found, fall back to the original method
        if not result["fact_value"]:
            print("Falling back to original extraction method...")
            original_result = fetch_and_parse_submission(
                cik, form_type, fiscal_period, fiscal_year, fact_tag
            )
            # Merge results but keep the enhanced structure
            for key in [
                "fact_value",
                "fact_unit",
                "fact_context",
                "fact_decimals",
                "error",
            ]:
                if key in original_result and original_result[key] is not None:
                    result[key] = original_result[key]

    except Exception as e:
        result["error"] = f"Error in enhanced parsing: {str(e)}"
        print(f"Error: {result['error']}")
        import traceback

        print(traceback.format_exc())

    return result


def fetch_and_parse_submission_by_adsh(cik, adsh, fact_tag, primary_document=None):
    """
    Fetches a specific SEC submission by ADSH (Accession Number) and extracts all instances
    of the specified fact tag with their associated periods/dates.

    Args:
        cik: Company CIK (string or int)
        adsh: Accession Number (e.g., "0000950170-24-108107")
        fact_tag: XBRL fact tag to extract (e.g., 'RevenueFromContractWithCustomerExcludingAssessedTax')
        primary_document: Optional primary document filename (if known)

    Returns:
        Dictionary with:
        - document_text: The full document text
        - all_facts: List of all fact instances found with their contexts and periods
        - best_fact: The most recent/latest fact instance
        - error: Error message if any
    """
    import requests
    from bs4 import BeautifulSoup, Tag
    import re
    from typing import Dict, Any, List
    import datetime

    def safe_get_text(element) -> str:
        """Safely extract text from a BeautifulSoup element."""
        if element is None:
            return ""
        if hasattr(element, "get_text"):
            return element.get_text().strip()
        return str(element).strip()

    def safe_get_attr(element, attr_name: str, default=None):
        """Safely get attribute from a BeautifulSoup element."""
        if element is None:
            return default
        if isinstance(element, Tag) and hasattr(element, "attrs"):
            return element.attrs.get(attr_name, default)
        try:
            return getattr(element, attr_name, default)
        except (AttributeError, TypeError):
            pass
        try:
            return element.get(attr_name, default)
        except (AttributeError, TypeError):
            return default

    def parse_context_period(context_element):
        """Parse XBRL context to extract period information."""
        period_info: Dict[str, Any] = {
            "startDate": None,
            "endDate": None,
            "instant": None,
            "period_type": None,
        }

        if not context_element:
            return period_info

        # Look for period element within context
        period_elem = context_element.find("period")
        if not period_elem:
            return period_info

        # Check for instant (point-in-time) vs duration (period)
        instant_elem = period_elem.find("instant")
        if instant_elem:
            instant_text = safe_get_text(instant_elem)
            if (
                instant_text
                and len(instant_text) == 10
                and instant_text.count("-") == 2
            ):
                try:
                    datetime.datetime.strptime(instant_text, "%Y-%m-%d")
                    period_info["instant"] = instant_text
                    period_info["period_type"] = "instant"
                    period_info["endDate"] = period_info["instant"]
                except ValueError:
                    print(f"Invalid instant date format: {instant_text}")
        else:
            # Look for duration (startDate and endDate)
            start_elem = period_elem.find("startdate") or period_elem.find("startDate")
            end_elem = period_elem.find("enddate") or period_elem.find("endDate")

            if start_elem and end_elem:
                start_text = safe_get_text(start_elem)
                end_text = safe_get_text(end_elem)

                # Validate date formats
                start_valid = False
                end_valid = False

                if start_text and len(start_text) == 10 and start_text.count("-") == 2:
                    try:
                        datetime.datetime.strptime(start_text, "%Y-%m-%d")
                        start_valid = True
                    except ValueError:
                        print(f"Invalid start date format: {start_text}")

                if end_text and len(end_text) == 10 and end_text.count("-") == 2:
                    try:
                        datetime.datetime.strptime(end_text, "%Y-%m-%d")
                        end_valid = True
                    except ValueError:
                        print(f"Invalid end date format: {end_text}")

                if start_valid and end_valid:
                    period_info["startDate"] = start_text
                    period_info["endDate"] = end_text
                    period_info["period_type"] = "duration"

        return period_info

    def extract_date_from_context_id(context_id):
        """Extract dates from context ID patterns."""
        if not context_id:
            return None, None

        # Pattern for CIK_startdate_enddate format
        date_pattern = r"(\d{8})_(\d{8})"
        match = re.search(date_pattern, context_id)
        if match:
            start_str, end_str = match.groups()
            try:
                start_date = f"{start_str[:4]}-{start_str[4:6]}-{start_str[6:8]}"
                end_date = f"{end_str[:4]}-{end_str[4:6]}-{end_str[6:8]}"
                return start_date, end_date
            except:
                pass

        # Pattern for single date (instant)
        single_date_pattern = r"(\d{8})"
        match = re.search(single_date_pattern, context_id)
        if match:
            date_str = match.group(1)
            try:
                date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                return None, date  # None for start, date for end/instant
            except:
                pass

        return None, None

    # Initialize result structure
    result: Dict[str, Any] = {
        "document_text": None,
        "all_facts": [],
        "best_fact": None,
        "document_url": None,
        "error": None,
    }

    try:
        # Construct URL from ADSH
        cik_str = str(cik).zfill(10)
        adsh_nodash = adsh.replace("-", "")

        # If primary document is not provided, try common patterns
        if not primary_document:
            # Try to find the primary document by making a request to the SEC API
            from data_loaders.api_client import ApiClient

            client = ApiClient(user_agent="Barnnabass daniOO7XbX@gmail.com")

            try:
                submissions_data = client.fetch_company_submissions(cik_str)
                filings = submissions_data["filings"]["recent"]

                # Find the filing with matching accession number
                for i, filing_adsh in enumerate(filings["accessionNumber"]):
                    if filing_adsh == adsh:
                        primary_document = filings["primaryDocument"][i]
                        break

                if not primary_document:
                    result["error"] = f"Could not find primary document for ADSH {adsh}"
                    return result

            except Exception as e:
                # Fallback to common patterns if API call fails
                print(f"Could not fetch submission data via API: {e}")
                # Try common document name patterns based on ADSH
                # Extract the date part from ADSH (last 8 digits after removing dashes)
                adsh_parts = adsh.split("-")
                if len(adsh_parts) >= 2:
                    date_part = adsh_parts[-1]
                    # Common patterns for document names
                    common_patterns = [
                        f"form10q_{date_part}.htm",
                        f"form10k_{date_part}.htm",
                        f"d{adsh_nodash}.htm",
                        "form10q.htm",
                        "form10k.htm",
                    ]
                    primary_document = common_patterns[
                        0
                    ]  # Use first pattern as fallback
                else:
                    primary_document = "form10q.htm"  # Ultimate fallback

        # Construct the document URL
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_str}/{adsh_nodash}/{primary_document}"
        result["document_url"] = url

        print(f"Fetching document from: {url}")
        resp = requests.get(url, headers={"User-Agent": "TestUser test@example.com"})

        if resp.status_code != 200:
            result["error"] = f"Failed to fetch {url}, status code: {resp.status_code}"
            return result

        document_text = resp.text
        result["document_text"] = document_text

        # Check if it's inline XBRL (most common in recent filings)
        is_ixbrl = any(
            tag in document_text.lower()
            for tag in [
                "<ix:",
                "xmlns:ix=",
                "inline xbrl",
                "<html",
                "ixbrlmember",
                "ixbrl-member",
            ]
        )

        if is_ixbrl:
            print("Detected inline XBRL (iXBRL) document")
            soup = BeautifulSoup(document_text, "html.parser")

            # Step 1: Find all contexts in the document
            print("Extracting all XBRL contexts...")
            context_elements = soup.find_all("xbrli:context") or soup.find_all(
                "context"
            )
            contexts_info = {}

            for ctx in context_elements:
                ctx_id = safe_get_attr(ctx, "id")
                if ctx_id:
                    period_info = parse_context_period(ctx)
                    contexts_info[ctx_id] = period_info

            print(f"Found {len(contexts_info)} contexts")

            # Step 2: Find all facts with the target tag
            print(f"Finding all facts for tag '{fact_tag}'...")
            all_facts = []

            # Method 1: Look for inline XBRL tags with the fact
            for tag_name in ["ix:nonfraction", "ix:nonnumeric", "ix:fraction"]:
                facts = soup.find_all(tag_name.replace(":", ":"))
                for fact in facts:
                    fact_name = safe_get_attr(fact, "name", "")
                    if isinstance(fact_name, str) and (
                        fact_name == fact_tag or fact_name.endswith(":" + fact_tag)
                    ):
                        context_ref = safe_get_attr(fact, "contextref")
                        fact_value = safe_get_text(fact)

                        # Get or extract period information using comprehensive extraction
                        period_info = extract_comprehensive_period_info(
                            fact, str(context_ref) if context_ref else None, soup, None
                        )

                        fact_data = {
                            "value": fact_value,
                            "context_ref": context_ref,
                            "unit_ref": safe_get_attr(fact, "unitref"),
                            "decimals": safe_get_attr(fact, "decimals"),
                            "scale": safe_get_attr(fact, "scale"),
                            "period_start": period_info.get("period_start"),
                            "period_end": period_info.get("period_end"),
                            "period_type": period_info.get("period_type"),
                            "extraction_method": period_info.get(
                                "extraction_method", "inline_xbrl_tag"
                            ),
                        }

                        all_facts.append(fact_data)
                        print(
                            f"Found fact: value={fact_value}, context={context_ref}, period_end={fact_data['period_end']}"
                        )

            # Method 2: Look for span/div with name attribute
            if not all_facts:
                facts = soup.find_all(attrs={"name": fact_tag})
                for fact in facts:
                    context_ref = safe_get_attr(fact, "contextref")
                    fact_value = safe_get_text(fact)

                    # Get or extract period information using comprehensive extraction
                    period_info = extract_comprehensive_period_info(
                        fact, str(context_ref) if context_ref else None, soup, None
                    )

                    fact_data = {
                        "value": fact_value,
                        "context_ref": context_ref,
                        "unit_ref": safe_get_attr(fact, "unitref")
                        or safe_get_attr(fact, "format"),
                        "decimals": safe_get_attr(fact, "decimals")
                        or safe_get_attr(fact, "scale"),
                        "scale": safe_get_attr(fact, "scale"),
                        "period_start": period_info.get("period_start"),
                        "period_end": period_info.get("period_end"),
                        "period_type": period_info.get("period_type"),
                        "extraction_method": period_info.get(
                            "extraction_method", "name_attribute"
                        ),
                    }

                    all_facts.append(fact_data)

        else:
            # Standard XBRL parsing
            print("Detected standard XBRL document")
            soup = BeautifulSoup(document_text, "lxml-xml")

            # Find namespace prefixes
            ns_prefixes = []
            root_elem = soup.find()
            if root_elem and isinstance(root_elem, Tag) and hasattr(root_elem, "attrs"):
                for attr, value in root_elem.attrs.items():
                    if "xmlns:" in attr and "gaap" in attr.lower():
                        ns_prefix = attr.split(":")[1]
                        ns_prefixes.append(ns_prefix)

            search_prefixes = ns_prefixes if ns_prefixes else ["us-gaap"]
            search_prefixes.append("")  # Also try without prefix

            all_facts = []
            for prefix in search_prefixes:
                tag_pattern = f"{prefix}:{fact_tag}" if prefix else fact_tag
                fact_elements = soup.find_all(tag_pattern)

                for fact in fact_elements:
                    value = fact.get_text().strip() if fact else ""
                    if value:
                        context_ref = safe_get_attr(fact, "contextRef")

                        # Try to extract period from context
                        period_start, period_end = extract_date_from_context_id(
                            context_ref
                        )

                        fact_data = {
                            "value": value,
                            "context_ref": context_ref,
                            "unit_ref": safe_get_attr(fact, "unitRef"),
                            "decimals": safe_get_attr(fact, "decimals"),
                            "scale": None,
                            "period_start": period_start,
                            "period_end": period_end,
                            "period_type": "duration" if period_start else "instant",
                            "extraction_method": "standard_xbrl",
                        }

                        all_facts.append(fact_data)

        # Convert string values to numbers where possible
        for fact in all_facts:
            try:
                if fact["value"]:
                    num_value = fact["value"].replace(",", "")
                    fact["numeric_value"] = float(num_value)

                    # Apply scale/decimals transformation
                    if fact["decimals"]:
                        try:
                            decimals = int(fact["decimals"])
                            if decimals < 0:  # Scale factor
                                fact["actual_value"] = fact["numeric_value"] * (
                                    10 ** abs(decimals)
                                )
                            else:
                                fact["actual_value"] = fact["numeric_value"]
                        except ValueError:
                            fact["actual_value"] = fact["numeric_value"]
                    else:
                        fact["actual_value"] = fact["numeric_value"]
            except (ValueError, TypeError):
                fact["numeric_value"] = None
                fact["actual_value"] = None

        result["all_facts"] = all_facts

        # Select the best fact (most recent by end date)
        if all_facts:
            best_fact = None
            latest_end_date = None

            for fact in all_facts:
                if fact.get("period_end"):
                    try:
                        end_date = datetime.datetime.strptime(
                            fact["period_end"], "%Y-%m-%d"
                        )
                        if latest_end_date is None or end_date > latest_end_date:
                            latest_end_date = end_date
                            best_fact = fact
                    except ValueError:
                        continue

            if not best_fact and all_facts:
                best_fact = all_facts[0]  # Fallback to first fact

            print(
                f"Found {len(all_facts)} total facts, selected best fact with period end: {best_fact.get('period_end') if best_fact else 'None'}"
            )
            result["best_fact"] = best_fact

        return result

    except Exception as e:
        print(f"Error in fetch_and_parse_submission_by_adsh: {e}")
        import traceback

        print(traceback.format_exc())
        result["error"] = str(e)
        return result
