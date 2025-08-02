import re
from typing import List, Dict, Any, Optional
from data_loaders.api_client import ApiClient
from data_loaders.fetch_and_parse_all_financial_facts_from_submission_by_cik import (
    fetch_and_parse_all_financial_facts_from_submission_by_cik,
)
from data_loaders.fetch_known_adsh import fetch_known_adsh_with_metadata_for_ticker


def infer_fiscal_metadata_from_chronological_order(
    all_filings: List[Dict[str, Any]],
    known_adsh_metadata: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Infer FilingType, FiscalPeriod, and FiscalYear for missing filings based on
    chronological order of ADSH codes and known filing metadata.

    Args:
        all_filings: List of all filings (missing + known) with basic metadata
        known_adsh_metadata: List of known filings with complete fiscal metadata

    Returns:
        List of all filings with inferred fiscal metadata
    """
    # Create a mapping of known ADSH to their fiscal metadata
    known_metadata_map = {item["Adsh"]: item for item in known_adsh_metadata}

    # Sort all filings by date chronologically (oldest first for easier inference)
    all_filings_sorted = sorted(all_filings, key=lambda x: x.get("Ddate", ""))

    # Standard fiscal quarter progression for most companies
    QUARTER_PROGRESSION = {
        "FY": "Q1",  # After FY comes Q1 of next fiscal year
        "Q1": "Q2",
        "Q2": "Q3",
        "Q3": "FY",
    }

    # Infer metadata for each filing
    enriched_filings = []

    for i, filing in enumerate(all_filings_sorted):
        adsh = filing.get("Adsh")

        # If this is a known filing, use its metadata directly
        if adsh in known_metadata_map:
            known_data = known_metadata_map[adsh]
            enriched_filing = filing.copy()
            enriched_filing.update(
                {
                    "FilingType": known_data.get(
                        "FilingType", filing.get("FilingType", "")
                    ),
                    "FiscalPeriod": known_data.get(
                        "FiscalPeriod", filing.get("FiscalPeriod", "")
                    ),
                    "FiscalYear": known_data.get(
                        "FiscalYear", filing.get("FiscalYear", 0)
                    ),
                    "IsKnown": True,
                }
            )
            enriched_filings.append(enriched_filing)
        else:
            # This is a missing filing - infer metadata
            enriched_filing = filing.copy()
            enriched_filing["IsKnown"] = False

            # Find the closest known filing before this one
            prev_known_filing = None
            for j in range(i - 1, -1, -1):  # Look backwards
                prev_adsh = all_filings_sorted[j].get("Adsh")
                if prev_adsh in known_metadata_map:
                    prev_known_filing = known_metadata_map[prev_adsh]
                    break

            # Also find the immediate previous filing (could be inferred)
            prev_filing = None
            if i > 0 and len(enriched_filings) > 0:
                # Use the most recently processed filing
                prev_filing = enriched_filings[-1]
            elif prev_known_filing:
                # If no inferred filing, use the known filing
                prev_filing = prev_known_filing

            # Find the closest known filing after this one
            next_known_filing = None
            for j in range(i + 1, len(all_filings_sorted)):  # Look forwards
                next_adsh = all_filings_sorted[j].get("Adsh")
                if next_adsh in known_metadata_map:
                    next_known_filing = known_metadata_map[next_adsh]
                    break

            # Infer based on immediate previous filing (known or inferred)
            if prev_filing:
                prev_period = prev_filing.get("FiscalPeriod", "")
                prev_year = prev_filing.get("FiscalYear", 0)
                prev_filing_type = prev_filing.get("FilingType", "")

                # Determine next expected period and year
                if prev_period in QUARTER_PROGRESSION:
                    next_period = QUARTER_PROGRESSION[prev_period]

                    # If transitioning from FY to Q1, increment year
                    if prev_period == "FY" and next_period == "Q1":
                        next_year = prev_year + 1
                    else:
                        next_year = prev_year

                    # Determine filing type based on period
                    if next_period == "FY":
                        next_filing_type = "10-K"
                    else:
                        next_filing_type = "10-Q"

                    enriched_filing.update(
                        {
                            "FilingType": next_filing_type,
                            "FiscalPeriod": next_period,
                            "FiscalYear": next_year,
                            "InferredFrom": f"Previous: {prev_filing.get('Adsh')} ({prev_period} {prev_year})",
                        }
                    )

                else:
                    # Fallback: use original data if available
                    enriched_filing.update(
                        {
                            "FilingType": filing.get("FilingType", "10-Q"),
                            "FiscalPeriod": filing.get("FiscalPeriod", "Q1"),
                            "FiscalYear": filing.get("FiscalYear", prev_year),
                            "InferredFrom": "Fallback from original data",
                        }
                    )

            elif next_known_filing:
                # If no previous known filing, try to infer from next one
                next_period = next_known_filing.get("FiscalPeriod", "")
                next_year = next_known_filing.get("FiscalYear", 0)

                # Work backwards from next known filing
                reverse_progression = {v: k for k, v in QUARTER_PROGRESSION.items()}

                if next_period in reverse_progression:
                    prev_period = reverse_progression[next_period]

                    # If transitioning from Q1 to FY, decrement year
                    if next_period == "Q1" and prev_period == "FY":
                        prev_year = next_year - 1
                    else:
                        prev_year = next_year

                    # Determine filing type
                    if prev_period == "FY":
                        prev_filing_type = "10-K"
                    else:
                        prev_filing_type = "10-Q"

                    enriched_filing.update(
                        {
                            "FilingType": prev_filing_type,
                            "FiscalPeriod": prev_period,
                            "FiscalYear": prev_year,
                            "InferredFrom": f"Next: {next_known_filing.get('Adsh')} ({next_period} {next_year})",
                        }
                    )
                else:
                    # Final fallback
                    enriched_filing.update(
                        {
                            "FilingType": filing.get("FilingType", "10-Q"),
                            "FiscalPeriod": filing.get("FiscalPeriod", "Q1"),
                            "FiscalYear": filing.get("FiscalYear", next_year),
                            "InferredFrom": "Fallback from next filing year",
                        }
                    )
            else:
                # No known filings found - use original data
                enriched_filing.update(
                    {
                        "FilingType": filing.get("FilingType", "10-Q"),
                        "FiscalPeriod": filing.get("FiscalPeriod", "Q1"),
                        "FiscalYear": filing.get("FiscalYear", 2024),
                        "InferredFrom": "No reference filings found",
                    }
                )

            enriched_filings.append(enriched_filing)

    # Sort back to original order (latest first)
    enriched_filings.sort(key=lambda x: x.get("Ddate", ""), reverse=True)
    return enriched_filings


def get_missing_sec_filings_with_inferred_metadata(
    cik: str,
    ticker: str,
    min_year: int = 2017,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Enhanced version of get_missing_sec_filings that infers fiscal metadata
    based on chronological order and known filings.
    """
    # Get known ADSH metadata from database
    known_adsh_metadata = fetch_known_adsh_with_metadata_for_ticker(ticker)
    known_adsh_list = [item["Adsh"] for item in known_adsh_metadata]

    if verbose:
        print(
            f"Found {len(known_adsh_metadata)} known filings with metadata for {ticker}"
        )

    # Get all filings from SEC API
    client = ApiClient(user_agent="Barnnabass daniOO7XbX@gmail.com")
    cik_str = str(cik).zfill(10)
    submissions_data = client.fetch_company_submissions(cik_str)
    filings = submissions_data["filings"]["recent"]

    # Build list of all filings (missing + known) - ONLY 10-Q and 10-K filings
    all_filings = []
    missing_filings = []

    for i in range(len(filings["accessionNumber"])):
        adsh = filings["accessionNumber"][i]
        filing_type = filings["form"][i]
        filing_date = filings["filingDate"][i]
        fiscal_year = filings.get(
            "fiscalYear", [None] * len(filings["accessionNumber"])
        )[i]
        fiscal_period = filings.get(
            "periodOfReport", [None] * len(filings["accessionNumber"])
        )[i]
        batch_tag = filings.get(
            "primaryDocument", [None] * len(filings["accessionNumber"])
        )[i]

        # ONLY consider 10-Q and 10-K filings
        if filing_type.upper() not in ["10-Q", "10-K"]:
            continue

        # Only consider filings since min_year
        year = None
        try:
            year = int(filing_date[:4])
        except Exception:
            pass
        if year is not None and year < min_year:
            continue

        filing_info = {
            "Cik": cik,
            "Ticker": ticker,
            "FilingType": filing_type,
            "FiscalPeriod": fiscal_period or "",
            "FiscalYear": fiscal_year or year,
            "Adsh": adsh,
            "Ddate": filing_date,
            "BatchTag": batch_tag,
        }

        all_filings.append(filing_info)

        # Track missing filings
        if adsh not in known_adsh_list:
            missing_filings.append(filing_info)

    # Infer fiscal metadata for all filings
    enriched_filings = infer_fiscal_metadata_from_chronological_order(
        all_filings, known_adsh_metadata
    )

    # Separate missing filings with enriched metadata
    enriched_missing = [f for f in enriched_filings if not f.get("IsKnown", True)]
    missing_10q = [
        f for f in enriched_missing if f.get("FilingType", "").upper() == "10-Q"
    ]
    missing_10k = [
        f for f in enriched_missing if f.get("FilingType", "").upper() == "10-K"
    ]

    if verbose:
        print(
            f"Found {len(enriched_missing)} missing filings since {min_year} for CIK {cik}"
        )
        print(f"Missing 10-Qs: {len(missing_10q)} | Missing 10-Ks: {len(missing_10k)}")

        # Show inference details
        for filing in enriched_missing[:3]:  # Show first 3 as example
            adsh = filing.get("Adsh", "")
            inferred_from = filing.get("InferredFrom", "Unknown")
            print(
                f"  {adsh}: {filing.get('FilingType')} {filing.get('FiscalPeriod')} {filing.get('FiscalYear')} (from: {inferred_from})"
            )

    return {
        "all_missing": enriched_missing,
        "missing_10q": missing_10q,
        "missing_10k": missing_10k,
        "all_filings_with_metadata": enriched_filings,  # Includes both missing and known
    }


def get_missing_sec_filings(
    cik: str,
    known_adsh_list: List[str],
    min_year: int = 2017,
    ticker: Optional[str] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Fetch all SEC filings (ADSH) for a CIK since min_year, compare to known_adsh_list, and return missing filings.
    Returns a dict with all missing filings and arrays for missing 10-Qs and 10-Ks.
    """
    client = ApiClient(user_agent="Barnnabass daniOO7XbX@gmail.com")
    cik_str = str(cik).zfill(10)
    submissions_data = client.fetch_company_submissions(cik_str)
    filings = submissions_data["filings"]["recent"]
    missing = []
    missing_10q = []
    missing_10k = []
    for i in range(len(filings["accessionNumber"])):
        adsh = filings["accessionNumber"][i]
        filing_type = filings["form"][i]
        filing_date = filings["filingDate"][i]
        fiscal_year = filings.get(
            "fiscalYear", [None] * len(filings["accessionNumber"])
        )[i]
        fiscal_period = filings.get(
            "periodOfReport", [None] * len(filings["accessionNumber"])
        )[i]
        batch_tag = filings.get(
            "primaryDocument", [None] * len(filings["accessionNumber"])
        )[i]

        # ONLY consider 10-Q and 10-K filings
        if filing_type.upper() not in ["10-Q", "10-K"]:
            continue

        # Only consider filings since min_year
        year = None
        try:
            year = int(filing_date[:4])
        except Exception:
            pass
        if year is not None and year < min_year:
            continue
        if adsh not in known_adsh_list:
            filing_info = {
                "Cik": cik,
                "Ticker": ticker or "",
                "FilingType": filing_type,
                "FiscalPeriod": fiscal_period or "",
                "FiscalYear": fiscal_year or year,
                "Adsh": adsh,
                "Ddate": filing_date,
                "BatchTag": batch_tag,
            }
            missing.append(filing_info)
            if filing_type.upper() == "10-Q":
                missing_10q.append(filing_info)
            elif filing_type.upper() == "10-K":
                missing_10k.append(filing_info)
    # Sort from latest to oldest
    missing.sort(
        key=lambda x: (
            x["FiscalYear"] or 0,
            x["FilingType"],
            x["FiscalPeriod"] or "",
            x["Ddate"],
        ),
        reverse=True,
    )
    missing_10q.sort(
        key=lambda x: (x["FiscalYear"] or 0, x["FiscalPeriod"] or "", x["Ddate"]),
        reverse=True,
    )
    missing_10k.sort(
        key=lambda x: (x["FiscalYear"] or 0, x["FiscalPeriod"] or "", x["Ddate"]),
        reverse=True,
    )
    if verbose:
        print(f"Found {len(missing)} missing filings since {min_year} for CIK {cik}")
        print(f"Missing 10-Qs: {len(missing_10q)} | Missing 10-Ks: {len(missing_10k)}")
    return {
        "all_missing": missing,
        "missing_10q": missing_10q,
        "missing_10k": missing_10k,
    }


def format_concept_for_insert(
    fact: dict, cik: str, ticker: str, batch_tag: Optional[str] = None
) -> dict:
    """
    Map a fact dict from fetch_and_parse_all_financial_facts_from_submission_by_cik to the edgar_financial_data_concepts table format.
    """

    tag_name = fact.get("tag_name", "")
    if ":" in tag_name:
        tag_name = tag_name.split(":", 1)[1]

    return {
        "Cik": int(cik),
        "Ticker": ticker,
        "FilingType": fact.get("filing_type", ""),
        "FiscalPeriod": fact.get("fiscal_period", ""),
        "FiscalYear": fact.get("fiscal_year", 0),
        "Concept": tag_name,
        "Value": fact.get("actual_value"),
        "ValueString": str(fact.get("value", "")),
        "Unit": fact.get("unit_ref", ""),
        "DataType": fact.get("data_type", ""),
        "Adsh": fact.get("adsh", ""),
        "PeriodEnd": fact.get("period_end", None),
        "Ddate": fact.get("ddate", None),
        "Segment": fact.get("segment", None),
        "Qtrs": fact.get(
            "qtrs", None
        ),  # Now mapped from the qtrs field calculated from period dates
        "BatchTag": f"missingInsertTag-{cik}",
    }


def deduce_fiscal_period_from_period_end(period_end_str, fiscal_year_end="0531"):
    """
    Deduce fiscal period (Q1, Q2, Q3, Q4, FY) from period_end and fiscal_year_end.
    Default fiscal_year_end is "0531" (May 31) for FDX.
    """
    if not period_end_str or not fiscal_year_end or len(fiscal_year_end) != 4:
        return None

    try:
        from datetime import datetime

        period_dt = datetime.strptime(period_end_str, "%Y-%m-%d")
        fy_end_month = int(fiscal_year_end[:2])
        fy_end_day = int(fiscal_year_end[2:])

        # For companies with May 31 fiscal year end (like FDX):
        # Q1: August 31, Q2: November 30, Q3: February 28/29, Q4/FY: May 31

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


def determine_main_fiscal_period(facts, fiscal_year_end="0531"):
    """
    Determine the main fiscal period for a filing based on extracted facts.
    Returns the fiscal period with the most facts that represents a quarter/FY.
    """
    # Group facts by period end
    periods = {}
    for fact in facts:
        period_end = fact.get("period_end")
        if period_end:
            if period_end not in periods:
                periods[period_end] = []
            periods[period_end].append(fact)

    # Find quarter/FY periods and their fact counts
    quarter_periods = {}
    for period_end, period_facts in periods.items():
        deduced_fp = deduce_fiscal_period_from_period_end(period_end, fiscal_year_end)
        if deduced_fp:  # Q1, Q2, Q3, Q4, or FY
            quarter_periods[period_end] = {
                "fiscal_period": deduced_fp,
                "fact_count": len(period_facts),
            }

    if quarter_periods:
        # Return the period with the most facts (main reporting period)
        main_period = max(
            quarter_periods.keys(), key=lambda p: quarter_periods[p]["fact_count"]
        )
        return quarter_periods[main_period]["fiscal_period"], main_period

    return None, None


def extract_financial_facts_from_missing_filing(
    missing_filing: Dict[str, Any], verbose: bool = False
) -> List[Dict[str, Any]]:
    """
    Extract all financial facts from a missing filing entry and format them for database insertion.

    Args:
        missing_filing: A filing dict from get_missing_sec_filings
        verbose: Whether to print progress information

    Returns:
        List of financial facts formatted for edgar_financial_data_concepts table
    """
    cik = missing_filing.get("Cik")
    adsh = missing_filing.get("Adsh")
    ticker = missing_filing.get("Ticker")
    filing_type = missing_filing.get("FilingType")
    fiscal_year = missing_filing.get("FiscalYear")
    fiscal_period = missing_filing.get("FiscalPeriod")
    ddate = missing_filing.get("Ddate")
    batch_tag = missing_filing.get("BatchTag")

    if verbose:
        print(f"Extracting facts from {filing_type} filing: {adsh}")

    try:
        # Extract all financial facts from the filing
        result = fetch_and_parse_all_financial_facts_from_submission_by_cik(
            cik=cik,
            adsh=adsh,
            include_custom_facts=False,  # Only standard GAAP facts
            VERBOSITY=0,  # Silent extraction
        )

        if result.get("error"):
            if verbose:
                print(f"Error extracting facts from {adsh}: {result['error']}")
            return []

        all_facts = result.get("all_financial_facts", [])
        if verbose:
            print(f"Found {len(all_facts)} financial facts in {adsh}")

        # Determine the main fiscal period from the extracted facts
        main_fiscal_period, main_period_end = determine_main_fiscal_period(all_facts)
        if main_fiscal_period and verbose:
            print(
                f"Determined main fiscal period: {main_fiscal_period} (period end: {main_period_end})"
            )

        # PRIORITY: Use the inferred fiscal metadata from ADSH chronological order logic
        # Only fallback to document-determined metadata if inferred data is missing
        effective_filing_type = (
            filing_type if filing_type else "10-Q"
        )  # From inferred metadata
        effective_fiscal_year = (
            fiscal_year if fiscal_year else 0
        )  # From inferred metadata
        effective_fiscal_period = (
            fiscal_period if fiscal_period else main_fiscal_period
        )  # Prioritize inferred

        if verbose and fiscal_period:
            print(
                f"Using INFERRED fiscal metadata: {effective_filing_type} {effective_fiscal_period} {effective_fiscal_year}"
            )
        elif verbose and main_fiscal_period:
            print(
                f"Using DOCUMENT-DETERMINED fiscal metadata: {effective_filing_type} {main_fiscal_period} {effective_fiscal_year}"
            )

        # Format each fact for database insertion
        formatted_facts = []
        for fact in all_facts:
            # Enhance fact with filing metadata - PRIORITIZE INFERRED METADATA
            enhanced_fact = fact.copy()
            enhanced_fact.update(
                {
                    "cik": cik,
                    "ticker": ticker,
                    "filing_type": effective_filing_type,  # Use inferred filing type
                    "fiscal_year": effective_fiscal_year,  # Use inferred fiscal year
                    "fiscal_period": effective_fiscal_period  # Use inferred fiscal period
                    or fact.get("fiscal_period", ""),
                    "adsh": adsh,
                    "ddate": ddate,
                    "batch_tag": batch_tag,
                }
            )

            # Format for database
            db_fact = format_concept_for_insert(
                enhanced_fact,
                str(cik) if cik else "",
                str(ticker) if ticker else "",
                batch_tag,
            )
            formatted_facts.append(db_fact)

        return formatted_facts

    except Exception as e:
        if verbose:
            print(f"Exception extracting facts from {adsh}: {e}")
        return []


def process_missing_filings_for_database_insertion(
    missing_filings: List[Dict[str, Any]],
    max_filings: Optional[int] = None,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    """
    Process a list of missing filings and extract all financial facts for database insertion.

    Args:
        missing_filings: List of missing filing dicts from get_missing_sec_filings
        max_filings: Optional limit on number of filings to process (for testing)
        verbose: Whether to print progress information

    Returns:
        List of all financial facts formatted for edgar_financial_data_concepts table
    """
    all_facts_for_db = []
    filings_to_process = (
        missing_filings[:max_filings] if max_filings else missing_filings
    )

    if verbose:
        print(
            f"Processing {len(filings_to_process)} missing filings for database insertion..."
        )

    for i, filing in enumerate(filings_to_process, 1):
        if verbose:
            print(
                f"\n[{i}/{len(filings_to_process)}] Processing {filing.get('FilingType')} {filing.get('Adsh')}"
            )

        facts = extract_financial_facts_from_missing_filing(filing, verbose=verbose)
        all_facts_for_db.extend(facts)

        if verbose:
            print(f"Added {len(facts)} facts to database queue")

    if verbose:
        print(f"\nTotal facts ready for database insertion: {len(all_facts_for_db)}")

    return all_facts_for_db


def build_sec_edgar_urls(filing_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build SEC EDGAR URLs for downloading iXBRL files and related schema/linkbase files.

    Handles multiple batch tag formats with smart fallback URL generation:

    1. Standard format: "fdx-20230831.htm"
       - Primary: fdx-20230831.xml
       - Fallback: fdx-20230831_htm.xml

    2. Filing type format: "fdx-10q_20210831.htm"
       - Primary: fdx-20210831.xml (extracted date)
       - Fallback: fdx-10q_20210831_htm.xml (original with _htm suffix)

    Args:
        filing_data: Dictionary containing filing information with keys:
            - Cik: Company CIK
            - Adsh: Accession number (e.g., "0000950170-23-048994")
            - BatchTag: Primary document filename (e.g., "fdx-20230831.htm" or "fdx-10q_20210831.htm")

    Returns:
        Dictionary with URLs for the main filing and related files:
        {
            "base_url": "https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/",
            "ixbrl_url": "https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/fdx-20230831.xml",
            "ixbrl_url_fallback": "https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/fdx-20230831_htm.xml",
            "schema_urls": {
                "xsd": "https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/fdx-20230831.xsd",
                "def_xml": "https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/fdx-20230831_def.xml",
                "pre_xml": "https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/fdx-20230831_pre.xml",
                "cal_xml": "https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/fdx-20230831_cal.xml",
                "lab_xml": "https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/fdx-20230831_lab.xml"
            }
        }
    """
    cik = str(filing_data.get("Cik", "")).strip()
    adsh = str(filing_data.get("Adsh", "")).strip()
    batch_tag = str(filing_data.get("BatchTag", "")).strip()

    if not cik or not adsh or not batch_tag:
        raise ValueError(
            f"Missing required fields: Cik={cik}, Adsh={adsh}, BatchTag={batch_tag}"
        )

    # Parse ADSH to get accession number without dashes
    # ADSH format: "0000950170-23-048994" -> accession: "000095017023048994"
    try:
        adsh_parts = adsh.split("-")
        if len(adsh_parts) != 3:
            raise ValueError(f"Invalid ADSH format: {adsh}")

        # Remove leading zeros from CIK for directory structure
        cik_clean = str(int(cik))

        # Construct accession number without dashes
        accession_no = "".join(adsh_parts)

    except Exception as e:
        raise ValueError(f"Error parsing ADSH {adsh}: {e}")

    # Build base URL
    base_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{accession_no}/"

    # Extract base filename from batch_tag for schema files
    # Handle different batch tag patterns:
    # 1. Standard: "fdx-20230831.htm" -> "fdx-20230831"
    # 2. With filing type: "fdx-10q_20210831.htm" -> "fdx-20210831" (primary), "fdx-10q_20210831_htm" (fallback)

    batch_tag_base = batch_tag
    if batch_tag_base.endswith(".htm"):
        batch_tag_base = batch_tag_base[:-4]  # Remove .htm extension

    # Check if this is the "10q/10k" format pattern
    is_filing_type_format = re.search(
        r"-(10[qk])_(\d{8})", batch_tag_base, re.IGNORECASE
    )

    if is_filing_type_format:
        # Pattern: "fdx-10q_20210831" -> extract date for primary URL
        filing_type = is_filing_type_format.group(1).lower()  # "10q" or "10k"
        date_part = is_filing_type_format.group(2)  # "20210831"
        company_prefix = batch_tag_base.split("-")[0]  # "fdx"

        # Primary URL: fdx-20210831.xml (simple date format)
        batch_tag_v2_primary = f"{company_prefix}-{date_part}"

        # Fallback URL: fdx-10q_20210831_htm.xml (original format with _htm)
        batch_tag_v2_fallback = f"{batch_tag_base}_htm"

    else:
        # Standard pattern: "fdx-20230831"
        # Clean by removing any "-(10q|10k)_" pattern for legacy compatibility
        batch_tag_v2_primary = re.sub(
            r"-(10q|10k)_", "-", batch_tag_base, flags=re.IGNORECASE
        )
        batch_tag_v2_fallback = f"{batch_tag_v2_primary}_htm"

    # Build iXBRL URLs with enhanced fallback logic
    ixbrl_url_simple = f"{base_url}{batch_tag_v2_primary}.xml"
    ixbrl_url_htm = f"{base_url}{batch_tag_v2_fallback}.xml"

    # Build schema/linkbase URLs using primary format
    schema_urls = {
        "xsd": f"{base_url}{batch_tag_v2_primary}.xsd",
        "def_xml": f"{base_url}{batch_tag_v2_primary}_def.xml",
        "pre_xml": f"{base_url}{batch_tag_v2_primary}_pre.xml",
        "cal_xml": f"{base_url}{batch_tag_v2_primary}_cal.xml",
        "lab_xml": f"{base_url}{batch_tag_v2_primary}_lab.xml",
    }

    return {
        "base_url": base_url,
        "ixbrl_url": ixbrl_url_simple,  # Primary URL (older format)
        "ixbrl_url_fallback": ixbrl_url_htm,  # Fallback URL (newer format)
        "schema_urls": schema_urls,
    }


def download_ixbrl_with_fallback(
    filing_data: Dict[str, Any], download_dir: str, verbose: bool = False
) -> Dict[str, Any]:
    """
    Download iXBRL file with automatic fallback for different naming formats.

    For newer filings (2019+), tries both:
    1. Simple format: fdx-20240831.xml
    2. HTM format: fdx-20240831_htm.xml

    Args:
        filing_data: Dictionary containing filing information
        download_dir: Directory to save downloaded files
        verbose: Whether to print progress information

    Returns:
        Dictionary with success status and actual URL used:
        {
            "success": True/False,
            "ixbrl_url": "actual_url_that_worked",
            "local_path": "path_to_downloaded_file",
            "error": "error_message_if_failed"
        }
    """
    import requests
    import os

    try:
        urls_info = build_sec_edgar_urls(filing_data)

        # Try primary URL first (simple format)
        primary_url = urls_info["ixbrl_url"]
        fallback_url = urls_info["ixbrl_url_fallback"]

        headers = {"User-Agent": "YourName Contact@Email.com"}

        # Extract filename for local storage
        adsh = filing_data.get("Adsh", "")
        batch_tag = filing_data.get("BatchTag", "")

        urls_to_try = [(primary_url, "simple format"), (fallback_url, "htm format")]

        for url, format_desc in urls_to_try:
            try:
                if verbose:
                    print(f"🔄 Trying {format_desc}: {os.path.basename(url)}")

                response = requests.get(url, headers=headers, timeout=30)

                if response.status_code == 200:
                    # Success! Save the file
                    filename = os.path.basename(url)
                    local_path = os.path.join(download_dir, filename)

                    with open(local_path, "wb") as f:
                        f.write(response.content)

                    if verbose:
                        print(f"✅ Downloaded iXBRL file ({format_desc}): {filename}")

                    return {
                        "success": True,
                        "ixbrl_url": url,
                        "local_path": local_path,
                        "format_used": format_desc,
                        "error": None,
                    }

                elif verbose:
                    print(f"❌ {format_desc} failed: HTTP {response.status_code}")

            except Exception as e:
                if verbose:
                    print(f"❌ {format_desc} failed: {e}")
                continue

        # If we get here, both formats failed
        error_msg = f"Both iXBRL formats failed for ADSH {adsh}"
        if verbose:
            print(f"❌ {error_msg}")

        return {
            "success": False,
            "ixbrl_url": None,
            "local_path": None,
            "format_used": None,
            "error": error_msg,
        }

    except Exception as e:
        error_msg = f"Error in download_ixbrl_with_fallback: {e}"
        if verbose:
            print(f"❌ {error_msg}")

        return {
            "success": False,
            "ixbrl_url": None,
            "local_path": None,
            "format_used": None,
            "error": error_msg,
        }


def get_all_filing_urls(filing_data: Dict[str, Any]) -> List[str]:
    """
    Get a list of all URLs (iXBRL + schema files) for a filing.
    Includes both iXBRL format options for compatibility.

    Args:
        filing_data: Dictionary containing filing information

    Returns:
        List of all URLs to download for complete filing extraction
    """
    urls_info = build_sec_edgar_urls(filing_data)

    all_urls = [
        urls_info["ixbrl_url"],  # Primary iXBRL format
        urls_info["ixbrl_url_fallback"],  # Fallback iXBRL format
    ]
    all_urls.extend(urls_info["schema_urls"].values())

    return all_urls


def get_primary_ixbrl_urls(filing_data: Dict[str, Any]) -> Dict[str, str]:
    """
    Get both primary and fallback iXBRL URLs for a filing.

    Args:
        filing_data: Dictionary containing filing information

    Returns:
        Dictionary with primary and fallback URLs:
        {
            "primary": "fdx-20240831.xml",
            "fallback": "fdx-20240831_htm.xml"
        }
    """
    urls_info = build_sec_edgar_urls(filing_data)

    return {
        "primary": urls_info["ixbrl_url"],
        "fallback": urls_info["ixbrl_url_fallback"],
    }


def validate_filing_data_for_url_building(filing_data: Dict[str, Any]) -> bool:
    """
    Validate that filing data contains the required fields for URL building.

    Args:
        filing_data: Dictionary containing filing information

    Returns:
        True if valid, False otherwise
    """
    required_fields = ["Cik", "Adsh", "BatchTag"]

    for field in required_fields:
        if not filing_data.get(field):
            return False

    # Validate ADSH format
    adsh = str(filing_data.get("Adsh", ""))
    if len(adsh.split("-")) != 3:
        return False

    return True
