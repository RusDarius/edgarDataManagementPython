from data_loaders.sec_api_loaders.fetch_and_parse_submission_by_cik import (
    fetch_and_parse_submission_by_cik,
)


def fetch_and_parse_submission_enhanced_by_cik_and_adsh(
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
            original_result = fetch_and_parse_submission_by_cik(
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
