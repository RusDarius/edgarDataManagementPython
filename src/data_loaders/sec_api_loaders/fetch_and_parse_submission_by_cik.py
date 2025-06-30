from data_loaders.data_extractors.update_fact_selection_with_comprehensive_extraction import (
    update_fact_selection_with_comprehensive_extraction,
)


def fetch_and_parse_submission_by_cik(
    cik, form_type, fiscal_period, fiscal_year, fact_tag
):
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
