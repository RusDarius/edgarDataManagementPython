from data_loaders.data_extractors.extract_comprehensive_period_info import (
    extract_comprehensive_period_info,
)
import datetime


def fetch_and_parse_all_financial_facts_from_submission_by_cik(
    cik,
    adsh,
    primary_document=None,
    include_custom_facts=False,
    VERBOSITY=1,
    VERBOSE_OUTPUT=None,
):
    """
    Fetches a specific SEC submission by ADSH and extracts ALL financial facts found,
    categorized by financial statement type and filtered for relevance.

    Args:
        cik: Company CIK (string or int)
        adsh: Accession Number (e.g., "0000950170-24-108107")
        primary_document: Optional primary document filename (if known)
        include_custom_facts: Whether to include company-specific custom facts (default: False)

    Returns:
        Dictionary with:
        - document_text: The full document text
        - total_facts_found: Total number of financial facts extracted
        - facts_by_category: Facts organized by financial statement category
        - all_financial_facts: Complete list of all financial facts
        - document_url: URL of the accessed document
        - error: Error message if any

    Financial facts are identified by:
        - US-GAAP namespace tags (us-gaap:*)
        - Common financial concept patterns
        - Exclusion of non-financial operational data
    """
    from bs4 import BeautifulSoup, Tag
    from typing import Dict, Any
    import sys

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

    # Define financial fact identification patterns
    FINANCIAL_FACT_PATTERNS = {
        "income_statement": [
            "revenue",
            "sales",
            "income",
            "loss",
            "expense",
            "cost",
            "earning",
            "profit",
            "margin",
            "ebitda",
            "dividend",
            "tax",
            "interest",
            "depreciation",
            "amortization",
            "impairment",
            "goodwill",
        ],
        "balance_sheet": [
            "asset",
            "liability",
            "equity",
            "cash",
            "inventory",
            "receivable",
            "payable",
            "debt",
            "capital",
            "stock",
            "retained",
            "accumulated",
            "property",
            "plant",
            "equipment",
            "investment",
            "security",
        ],
        "cash_flow": [
            "cashflow",
            "cash",
            "financing",
            "investing",
            "operating",
            "proceeds",
            "payments",
            "acquisition",
            "disposal",
            "issuance",
        ],
        "per_share": ["pershare", "earnings", "dividend", "bookvalue"],
    }

    # Exclude non-financial operational tags
    EXCLUDE_PATTERNS = [
        "textblock",
        "table",
        "axis",
        "member",
        "domain",
        "line",
        "abstract",
        "policy",
        "disclosure",
        "segment",
        "geographic",
        "concentration",
        "subsequent",
        "commitment",
        "contingent",
        "legal",
        "litigation",
    ]

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

    def is_financial_fact(tag_name: str) -> tuple[bool, str]:
        """
        Determine if a tag represents a financial fact and categorize it.
        Returns (is_financial, category)
        """
        if not tag_name:
            return False, ""

        tag_lower = tag_name.lower()

        # Skip if it contains exclude patterns
        for exclude_pattern in EXCLUDE_PATTERNS:
            if exclude_pattern in tag_lower:
                return False, ""

        # Check financial patterns
        for category, patterns in FINANCIAL_FACT_PATTERNS.items():
            for pattern in patterns:
                if pattern in tag_lower:
                    return True, category

        # Additional checks for US-GAAP namespace
        if "us-gaap:" in tag_lower or "usgaap" in tag_lower:
            # It's a US-GAAP tag, likely financial unless excluded
            return True, "other_financial"

        # Check for company-specific financial concepts
        if include_custom_facts and ":" in tag_name:
            # Company-specific tags often follow pattern like "abc:ConceptName"
            return True, "custom_financial"

        return False, ""

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
                    eprint(f"Invalid instant date format: {instant_text}")
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
                        eprint(f"Invalid start date format: {start_text}")

                if end_text and len(end_text) == 10 and end_text.count("-") == 2:
                    try:
                        datetime.datetime.strptime(end_text, "%Y-%m-%d")
                        end_valid = True
                    except ValueError:
                        eprint(f"Invalid end date format: {end_text}")

                if start_valid and end_valid:
                    period_info["startDate"] = start_text
                    period_info["endDate"] = end_text
                    period_info["period_type"] = "duration"

        return period_info

    # Initialize result structure
    result: Dict[str, Any] = {
        "document_text": None,
        "total_facts_found": 0,
        "facts_by_category": {
            "income_statement": [],
            "balance_sheet": [],
            "cash_flow": [],
            "per_share": [],
            "other_financial": [],
            "custom_financial": [],
        },
        "all_financial_facts": [],
        "document_url": None,
        "error": None,
    }

    try:
        # Construct URL from ADSH
        cik_str = str(cik).zfill(10)
        adsh_nodash = adsh.replace("-", "")

        # If primary document is not provided, try to find it
        if not primary_document:
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
                    eprint(f"[X] Could not find primary document for ADSH {adsh}")
                    result["error"] = f"Could not find primary document for ADSH {adsh}"
                    return result

            except Exception as e:
                eprint(f"[X] Could not fetch submission data via API: {e}")
                primary_document = "form10q.htm"  # Fallback

        # Construct the document URL
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_str}/{adsh_nodash}/{primary_document}"
        print("url")
        print(url)
        result["document_url"] = url

        vprint(f"Fetching document from: {url}")
        import requests

        resp = requests.get(url, headers={"User-Agent": "TestUser test@example.com"})

        if resp.status_code != 200:
            eprint(f"[X] Failed to fetch {url}, status code: {resp.status_code}")
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
            vprint("Detected inline XBRL (iXBRL) document")
            soup = BeautifulSoup(document_text, "html.parser")

            # Step 1: Find all contexts in the document
            vprint("Extracting all XBRL contexts...")
            context_elements = soup.find_all("xbrli:context") or soup.find_all(
                "context"
            )
            contexts_info = {}

            for ctx in context_elements:
                ctx_id = safe_get_attr(ctx, "id")
                if ctx_id:
                    period_info = parse_context_period(ctx)
                    contexts_info[ctx_id] = period_info
                    # Move context extraction print to vvprint
                    vvprint(f"Context {ctx_id}: {period_info}")

            result["all_contexts_found"] = list(contexts_info.keys())
            vprint(f"Total contexts found: {len(contexts_info)}")

            # Step 2: Find ALL financial facts in the document
            vprint("Extracting ALL financial facts...")
            all_financial_facts = []

            # Method 1: Look for inline XBRL tags
            for tag_name in ["ix:nonfraction", "ix:nonnumeric", "ix:fraction"]:
                facts = soup.find_all(tag_name.replace(":", ":"))
                for fact in facts:
                    fact_name = safe_get_attr(fact, "name", "")

                    # Check if this is a financial fact
                    if not isinstance(fact_name, str):
                        fact_name = str(fact_name) if fact_name else ""
                    is_financial, category = is_financial_fact(fact_name)

                    if is_financial:
                        context_ref = safe_get_attr(fact, "contextref")
                        fact_value = safe_get_text(fact)

                        # Get period information using comprehensive extraction
                        period_info = extract_comprehensive_period_info(
                            fact,
                            str(context_ref) if context_ref else None,
                            soup,
                            None,
                            VERBOSITY=VERBOSITY,
                            VERBOSE_OUTPUT=VERBOSE_OUTPUT,
                        )
                        vvprint(
                            f"Fact: {fact_name}, Value: {fact_value}, Context: {context_ref}, Period: {period_info}"
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
                            "category": category,
                        }

                        all_financial_facts.append(fact_data)
                        # Move this print to vvprint
                        vvprint(
                            f"Found fact: value={fact_value}, context={context_ref}, period_end={period_info.get('period_end')}"
                        )

            # Method 2: Look for standard XBRL elements (fallback)
            if not all_financial_facts:
                vprint("No inline XBRL facts found, trying standard XBRL...")
                # Look for all elements with namespace prefixes
                for element in soup.find_all():
                    element_name = getattr(element, "name", None)
                    if element_name and ":" in str(element_name):
                        # Check if this is a financial fact
                        is_financial, category = is_financial_fact(str(element_name))

                        if is_financial:
                            value = safe_get_text(element)
                            context_ref = safe_get_attr(element, "contextRef")

                            if value and context_ref:
                                # Convert context_ref to string
                                context_ref_str = (
                                    str(context_ref) if context_ref else None
                                )

                                # Get period information
                                period_info = extract_comprehensive_period_info(
                                    element,
                                    context_ref_str,
                                    soup,
                                    None,
                                    VERBOSITY=VERBOSITY,
                                )

                                fact_data = {
                                    "value": value,
                                    "context_ref": context_ref,
                                    "unit_ref": safe_get_attr(element, "unitRef"),
                                    "decimals": safe_get_attr(element, "decimals"),
                                    "scale": None,
                                    "period_start": period_info.get("period_start"),
                                    "period_end": period_info.get("period_end"),
                                    "period_type": period_info.get("period_type"),
                                    "extraction_method": "standard_xbrl",
                                    "tag_name": str(element_name),
                                    "category": category,
                                }

                                all_financial_facts.append(fact_data)

        else:
            eprint(
                "[X] Document is not inline XBRL format - cannot extract financial facts"
            )
            result["error"] = (
                "Document is not inline XBRL format - cannot extract financial facts"
            )
            return result

        # Convert string values to numbers where possible
        for fact in all_financial_facts:
            try:
                if fact["value"]:
                    # Clean up the value
                    num_value = (
                        fact["value"]
                        .replace(",", "")
                        .replace("$", "")
                        .replace("(", "-")
                        .replace(")", "")
                    )
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

        # Organize facts by category
        for fact in all_financial_facts:
            category = fact.get("category", "other_financial")
            if category in result["facts_by_category"]:
                result["facts_by_category"][category].append(fact)

        result["all_financial_facts"] = all_financial_facts
        result["total_facts_found"] = len(all_financial_facts)

        vprint(f"Total financial facts extracted: {result['total_facts_found']}")
        for category, facts in result["facts_by_category"].items():
            if facts:
                vprint(f"  - {category}: {len(facts)} facts")

        return result

    except Exception as e:
        eprint(f"[X] Error in fetch_and_parse_all_financial_facts_from_submission: {e}")
        import traceback

        eprint(traceback.format_exc())
        result["error"] = str(e)
        return result
