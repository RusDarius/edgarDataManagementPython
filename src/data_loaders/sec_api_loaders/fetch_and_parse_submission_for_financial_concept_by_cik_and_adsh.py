from data_loaders.data_extractors.extract_comprehensive_period_info import (
    extract_comprehensive_period_info,
)


def fetch_and_parse_submission_for_financial_concept_by_cik_and_adsh(
    cik, adsh, fact_tag, primary_document=None
):
    """
    Fetches a specific SEC submission by ADSH (Accession Number) and extracts all instances
    of the specified fact tag with their associated periods/dates.

    Args:
        cik: Company CIK (string or int)
        adsh: Accession Number (e.g., "0000950170-24-108107")
        fact_tag: XBRL fact tag to search for (e.g., 'Revenue').
                 Performs case-insensitive substring matching - will find all tags
                 containing this string (e.g., 'Revenue' matches 'RevenueFromContractWithCustomerExcludingAssessedTax')
        primary_document: Optional primary document filename (if known)

    Returns:
        Dictionary with:
        - document_text: The full document text
        - all_facts: List of all fact instances found with their contexts and periods
        - best_fact: The most recent/latest fact instance
        - error: Error message if any

    Note:
        Fact matching is now case-insensitive and substring-based. The function will find
        all XBRL facts whose tag names contain the provided fact_tag string.
        Each fact includes a 'tag_name' field with the actual XBRL tag name found.
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
                    print(f"Context {ctx_id}: {period_info}")

            result["all_contexts_found"] = list(contexts_info.keys())

            # Step 2: Find all facts with the target tag
            print(
                f"Finding all facts containing tag '{fact_tag}' (case-insensitive)..."
            )
            all_facts = []

            # Method 1: Look for inline XBRL tags with the fact
            for tag_name in ["ix:nonfraction", "ix:nonnumeric", "ix:fraction"]:
                facts = soup.find_all(tag_name.replace(":", ":"))
                for fact in facts:
                    fact_name = safe_get_attr(fact, "name", "")
                    if (
                        isinstance(fact_name, str)
                        and fact_tag.lower() in fact_name.lower()
                    ):
                        context_ref = safe_get_attr(fact, "contextref")
                        fact_value = safe_get_text(fact)

                        # Get period information using comprehensive extraction
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
                            "tag_name": fact_name,
                        }

                        all_facts.append(fact_data)
                        print(f"Found fact: value={fact_value}, context={context_ref}")

            # Method 2: Look for span/div with name attribute (case-insensitive substring matching)
            additional_facts = soup.find_all(attrs={"name": True})
            for fact in additional_facts:
                fact_name = safe_get_attr(fact, "name", "")
                if isinstance(fact_name, str) and fact_tag.lower() in fact_name.lower():
                    context_ref = safe_get_attr(fact, "contextref")
                    fact_value = safe_get_text(fact)

                    # Get period information using comprehensive extraction
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
                        "tag_name": fact_name,  # Store the actual tag name found
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
                # Find all XBRL fact elements and filter by substring match (case-insensitive)
                all_elements = soup.find_all()

                for element in all_elements:
                    element_name = getattr(element, "name", None) or ""
                    # Check if this element name contains our fact_tag (case-insensitive)
                    if element_name and fact_tag.lower() in element_name.lower():
                        # Also check if it matches the prefixed pattern
                        if prefix:
                            tag_pattern = f"{prefix}:{fact_tag}"
                            if tag_pattern.lower() in element_name.lower():
                                value = element.get_text().strip() if element else ""
                                if value:
                                    context_ref = safe_get_attr(element, "contextRef")

                                    # Try to extract period from context
                                    period_start, period_end = (
                                        extract_date_from_context_id(context_ref)
                                    )

                                    fact_data = {
                                        "value": value,
                                        "context_ref": context_ref,
                                        "unit_ref": safe_get_attr(element, "unitRef"),
                                        "decimals": safe_get_attr(element, "decimals"),
                                        "scale": None,
                                        "period_start": period_start,
                                        "period_end": period_end,
                                        "period_type": (
                                            "duration" if period_start else "instant"
                                        ),
                                        "extraction_method": "standard_xbrl",
                                        "tag_name": element_name,  # Store the actual tag name found
                                    }

                                    all_facts.append(fact_data)
                        else:
                            # No prefix - direct substring match
                            value = element.get_text().strip() if element else ""
                            if value:
                                context_ref = safe_get_attr(element, "contextRef")

                                # Try to extract period from context
                                period_start, period_end = extract_date_from_context_id(
                                    context_ref
                                )

                                fact_data = {
                                    "value": value,
                                    "context_ref": context_ref,
                                    "unit_ref": safe_get_attr(element, "unitRef"),
                                    "decimals": safe_get_attr(element, "decimals"),
                                    "scale": None,
                                    "period_start": period_start,
                                    "period_end": period_end,
                                    "period_type": (
                                        "duration" if period_start else "instant"
                                    ),
                                    "extraction_method": "standard_xbrl",
                                    "tag_name": element_name,  # Store the actual tag name found
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
