#!/usr/bin/env python3
"""
Enhanced XBRL Data Extraction using build_sec_edgar_urls

This version improves upon the original test_python_package_for_xbrl_data.py by:
1. Using the proven URL building logic from missing_filings_utils.py
2. Supporting dynamic CIK and ADSH specification via declared parameters
3. Better error handling and file management
4. More comprehensive fact extraction

Usage:
    python test_python_package_for_xbrl_data_v2.py

To test different filings, modify the FILING_PARAMETERS in get_filing_parameters() function.

Examples of filings you can test:
- FedEx: CIK=1048911, ADSH=0000950170-23-048994, BatchTag=fdx-20230831.htm
- Microsoft: CIK=789019, ADSH=0000891020-23-000024, BatchTag=msft-20230930.htm
- Apple: CIK=320193, ADSH=0000320193-23-000077, BatchTag=aapl-20230930.htm
"""

import json
import os
import sys
import requests
import shutil
import tempfile
import warnings
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

from data_loaders.edgar_financial_loader import insert_financial_data
from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection


# Add src to sys.path to allow imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_loaders.data_extractors.arelle_extractors.arelle_uitls_data_extractors import (
    extract_quarters_covered,
    extract_segment_string,
    extract_unit_string,
    extract_datatype_string,
)
from data_loaders.fetch_known_adsh import (
    fetch_known_adsh_for_ticker,
    fetch_known_adsh_with_metadata_for_ticker,
)
from generic_utils.accept_utf8_encoding import accept_utf8_encoding
from data_loaders.sec_api_loaders.missing_filings_utils import (
    build_sec_edgar_urls,
    get_missing_sec_filings,
    get_missing_sec_filings_with_inferred_metadata,
    validate_filing_data_for_url_building,
    download_ixbrl_with_fallback,
)
from generic_utils.print_all_properties import print_all_properties

# Import Arelle
try:
    from arelle import Cntlr
    from arelle.ModelXbrl import ModelXbrl

    ARELLE_AVAILABLE = True
except ImportError:
    print("⚠️ Warning: Arelle not available. Please install: pip install arelle")
    ARELLE_AVAILABLE = False

accept_utf8_encoding()

# Target revenue tags for extraction
TARGET_REVENUE_TAGS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "TotalRevenues",
}

USER_AGENT = "EdgarDataExtractor contact@yourcompany.com"


# === UTILITY FUNCTIONS ===
def clean_value_for_display(value, max_length=50):
    """Clean and format a value for safe display, avoiding raw HTML/XML content."""
    if not value:
        return "N/A"

    # Convert to string and strip whitespace
    str_value = str(value).strip()

    # If it contains HTML tags or is very long, summarize it
    if "<" in str_value and ">" in str_value:
        return f"<HTML_CONTENT:{len(str_value)}_chars>"

    # If it's very long, truncate it
    if len(str_value) > max_length:
        return f"{str_value[:max_length]}... ({len(str_value)} chars)"

    return str_value


def suppress_arelle_warnings():
    """
    Suppress non-critical Arelle warnings that don't affect fact extraction.
    """
    warnings.filterwarnings("ignore", category=UserWarning, module="arelle")

    # Set Arelle logger to only show errors
    arelle_logger = logging.getLogger("arelle")
    arelle_logger.setLevel(logging.ERROR)

    return arelle_logger


def download_file_safely(url: str, local_path: str, description: str = "file") -> bool:
    """Download a file from URL to local path with error handling."""
    try:
        print(f"📥 Downloading {description}: {os.path.basename(local_path)}")
        headers = {"User-Agent": USER_AGENT}

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        with open(local_path, "wb") as f:
            f.write(response.content)

        print(f"    ✅ Downloaded {description} ({len(response.content)} bytes)")
        return True

    except requests.exceptions.RequestException as e:
        print(f"    ❌ Failed to download {description}: {e}")
        return False
    except Exception as e:
        print(f"    💥 Unexpected error downloading {description}: {e}")
        return False


def download_filing_files(
    filing_data: Dict[str, Any], download_dir: str
) -> Optional[Dict[str, str]]:
    """
    Download all required files for a filing using the proven URL building logic.

    Returns:
        Dict with local file paths or None if download failed
    """
    try:
        # Validate filing data
        if not validate_filing_data_for_url_building(filing_data):
            print(f"❌ Filing data validation failed")
            return None

        # Build URLs using proven logic
        urls_info = build_sec_edgar_urls(filing_data)

        print(f"🌐 Built URLs for filing: {filing_data.get('Adsh')}")
        print(f"  📄 iXBRL: {urls_info}")

        downloaded_files = {}

        # Download iXBRL file (main document) with fallback mechanism
        print(f"📥 Downloading iXBRL document with fallback...")

        # Use the enhanced download function with fallback
        download_result = download_ixbrl_with_fallback(
            filing_data, download_dir, verbose=True
        )

        if download_result["success"]:
            downloaded_files["ixbrl"] = download_result["local_path"]
            print(
                f"    ✅ Downloaded iXBRL document using: {download_result['ixbrl_url']}"
            )
        else:
            print(
                f"❌ Failed to download main iXBRL document: {download_result['error']}"
            )
            return None

        # Download schema and linkbase files (best effort)
        schema_downloaded = 0
        schema_total = len(urls_info["schema_urls"])

        for schema_type, schema_url in urls_info["schema_urls"].items():
            schema_filename = os.path.basename(schema_url)
            schema_path = os.path.join(download_dir, schema_filename)

            if download_file_safely(schema_url, schema_path, f"{schema_type} schema"):
                downloaded_files[schema_type] = schema_path
                schema_downloaded += 1

        print(
            f"📊 Schema download summary: {schema_downloaded}/{schema_total} files downloaded"
        )

        return downloaded_files

    except Exception as e:
        print(f"💥 Error downloading files: {e}")
        return None


def extract_all_facts_with_arelle(
    ixbrl_path: str, filing_data: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Extract all financial facts from iXBRL file using Arelle.

    Returns:
        List of extracted facts with metadata
    """
    if not ARELLE_AVAILABLE:
        print("❌ Arelle not available for fact extraction")
        return []

    facts = []
    ctrl = None

    try:
        # suppress_arelle_warnings()

        # Initialize Arelle controller
        ctrl = Cntlr.Cntlr()

        print(f"🔍 Loading XBRL model: {os.path.basename(ixbrl_path)}")

        # Load XBRL with comprehensive options
        model_xbrl = ctrl.modelManager.load(
            ixbrl_path,
            isInlineXBRL=True,
            validateInferences=False,
            validateInlineXBRL=False,
            validateDuplicateFacts=False,
            validateDisclosureSystem=False,
        )

        if not model_xbrl or not hasattr(model_xbrl, "facts"):
            print("❌ Failed to load XBRL model or no facts available")
            return []

        print(f"📊 XBRL model loaded successfully: {len(model_xbrl.facts)} total facts")

        # Extract filing metadata
        adsh = filing_data.get("Adsh")
        cik = filing_data.get("Cik")
        batch_tag = filing_data.get("BatchTag")
        ticker = filing_data.get("ticker")
        filing_type = filing_data.get("filing_type")
        fiscal_period = filing_data.get("fiscal_period")
        fiscal_year = filing_data.get("fiscal_year")
        ddate = filing_data.get("ddate")

        print(f"🔄 Processing facts for {adsh}...")

        # Process each fact
        processed_facts = 0
        revenue_facts = 0

        for fact in model_xbrl.facts:
            uom = None
            datatype = None
            segment = None
            qtrs = None
            value_string = None
            value = None
            period_start = None
            period_end = None
            instant_date = None
            try:
                # Extract concept name
                concept_name = "Unknown"
                if hasattr(fact, "concept") and fact.concept is not None:
                    try:
                        if (
                            hasattr(fact.concept, "qname")
                            and fact.concept.qname is not None
                        ):
                            concept_name = str(fact.concept.qname.localName)
                        elif hasattr(fact.concept, "name") and fact.concept.name:
                            concept_name = str(fact.concept.name)
                    except Exception:
                        concept_name = f"ConceptError_{type(fact.concept).__name__}"

                # Get fact value_string
                value_string = getattr(fact, "value", None)

                # Get fact numeric value
                value = getattr(fact, "xValue", None)

                # Get fact datatype value - DOES NOT WORK ATM
                # datatype = extract_datatype_string(fact)

                # Get unit of measure used
                uom = extract_unit_string(getattr(fact, "unit", None))

                if hasattr(fact, "context") and fact.context is not None:
                    try:
                        # Get period information
                        if (
                            hasattr(fact.context, "isInstantPeriod")
                            and fact.context.isInstantPeriod == True
                        ):
                            instant_date = fact.context.instantDate
                        if (
                            hasattr(fact.context, "isStartEndPeriod")
                            and fact.context.isStartEndPeriod == True
                        ):
                            period_end = fact.context.endDate

                        # Get segment info
                        segment = extract_segment_string(fact.context)
                        qtrs = extract_quarters_covered(fact.context)
                    except Exception:
                        pass

                final_period_date = None
                if period_end != None:
                    final_period_date = period_end
                else:
                    final_period_date = instant_date

                facts.append(
                    (
                        cik,
                        ticker,
                        filing_type,
                        fiscal_period,
                        fiscal_year,
                        concept_name,
                        value,
                        value_string,
                        uom,
                        datatype,
                        adsh,
                        final_period_date,
                        ddate,
                        segment,
                        qtrs,
                        batch_tag,
                    )
                )
                processed_facts += 1

            except Exception as e:
                print(e)
                break

        print(f"✅ Fact extraction complete: {processed_facts} facts processed")
        print(f"🎯 Revenue facts found: {revenue_facts}")
        return facts

    except Exception as e:
        print(f"💥 Error during Arelle fact extraction: {e}")
        return []

    finally:
        # Clean up Arelle resources
        if ctrl:
            try:
                ctrl.close()
            except:
                pass


def analyze_revenue_facts(facts: List[Any]) -> None:
    """Analyze and display revenue-specific facts."""
    revenue_facts = [
        f
        for f in facts
        if any(target.lower() in str(f[5]).lower() for target in TARGET_REVENUE_TAGS)
    ]

    print(f"\n📈 Revenue Analysis")
    print("=" * 50)
    print(f"Total revenue facts found: {len(revenue_facts)}")
    fact_labels = [
        "CIK",
        "Ticker",
        "FilingType",
        "FiscalPeriod",
        "FiscalYear",
        "Concept",
        "Value",
        "ValueString",
        "Unit",
        "DataType",
        "Adsh",
        "PeriodEnd",
        "Ddate",
        "Segment",
        "Qtrs",
        "BatchTag",
    ]

    if revenue_facts:
        print(f"\n🎯 Revenue Facts Detail:")
        for idx, fact in enumerate(revenue_facts[:3], 1):
            print(f"\n--- Revenue Fact #{idx} ---")
            for label, value in zip(fact_labels, fact):
                print(f"{label}: {value}")
    else:
        print("⚠️ No revenue facts found")


def display_fact_summary(facts: List[Any]) -> None:
    """Display a summary of all extracted facts."""
    print(f"\n📊 Fact Extraction Summary")
    print("=" * 50)
    print(f"Total facts extracted: {len(facts)}")

    # Group by concept type
    concept_counts = {}
    for fact in facts:
        concept = fact[5]
        concept_counts[concept] = concept_counts.get(concept, 0) + 1

    # Show top 10 most common concepts
    sorted_concepts = sorted(concept_counts.items(), key=lambda x: x[1], reverse=True)
    print(f"\n🔝 Top 10 Most Common Concepts:")
    for i, (concept, count) in enumerate(sorted_concepts[:10], 1):
        print(f"  {i:2}. {concept}: {count} facts")

    # Show units summary
    units = {}
    for fact in facts:
        unit = fact[8]
        units[unit] = units.get(unit, 0) + 1

    print(f"\n💱 Units Summary:")
    for unit, count in sorted(units.items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f"  {unit}: {count} facts")


def save_results_to_file(
    facts: List[Dict[str, Any]], filing_data: Dict[str, Any], output_dir: str
) -> str:
    """Save extraction results to JSON file."""
    import json

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    adsh_clean = filing_data.get("Adsh", "unknown").replace("-", "_")
    output_file = os.path.join(
        output_dir, f"xbrl_extraction_{adsh_clean}_{timestamp}.json"
    )

    results = {
        "extraction_metadata": {
            "timestamp": datetime.now().isoformat(),
            "filing_data": filing_data,
            "total_facts": len(facts),
        },
        "facts": facts,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"💾 Results saved to: {output_file}")
    return output_file


def get_filing_parameters() -> Dict[str, Any]:
    """Get filing parameters - using declared parameters instead of command line args."""

    # ===============================================
    # MODIFY THESE PARAMETERS TO TEST DIFFERENT FILINGS
    # ===============================================
    FILING_PARAMETERS = {
        "Cik": "1048911",  # FedEx CIK
        "Adsh": "0000950170-23-048994",  # FedEx Q1 2023 filing
        "BatchTag": "fdx-20230831.htm",  # FedEx main document
    }

    # Alternative filings you can uncomment to test:
    #
    # # Microsoft 10-Q example:
    # FILING_PARAMETERS = {
    #     "Cik": "789019",
    #     "Adsh": "0000891020-23-000024",
    #     "BatchTag": "msft-20230930.htm"
    # }
    #
    # # Apple 10-K example:
    # FILING_PARAMETERS = {
    #     "Cik": "320193",
    #     "Adsh": "0000320193-23-000077",
    #     "BatchTag": "aapl-20230930.htm"
    # }
    #
    # # Amazon 10-Q example:
    # FILING_PARAMETERS = {
    #     "Cik": "1018724",
    #     "Adsh": "0000018724-23-000037",
    #     "BatchTag": "amzn-20230930.htm"
    # }

    print(f"📋 Using declared filing parameters:")
    print(f"   CIK: {FILING_PARAMETERS['Cik']}")
    print(f"   ADSH: {FILING_PARAMETERS['Adsh']}")
    print(f"   BatchTag: {FILING_PARAMETERS['BatchTag']}")
    return FILING_PARAMETERS


def execute_extraction_for_filling(filing_data, insert_to_db=False):
    """Main execution function."""
    print("🚀 Enhanced XBRL Data Extraction (v2)")
    print("=" * 60)

    # Create temporary directory for downloads
    temp_dir = tempfile.mkdtemp(prefix="xbrl_extraction_")
    print(f"📁 Working directory: {temp_dir}")

    try:
        # Download filing files
        print(f"\n1️⃣ Downloading Filing Files")
        print("-" * 40)

        downloaded_files = download_filing_files(filing_data, temp_dir)
        if not downloaded_files:
            print("❌ Failed to download required files")
            return

        # Extract facts with Arelle
        print(f"\n2️⃣ Extracting Facts with Arelle")
        print("-" * 40)

        ixbrl_path = downloaded_files.get("ixbrl")
        if not ixbrl_path:
            print("❌ iXBRL file not available")
            return

        facts = extract_all_facts_with_arelle(ixbrl_path, filing_data)

        if not facts:
            print("❌ No facts extracted")
            return

        if insert_to_db:
            # if we have fact do insertion in batches
            conn = get_mysql_connection(**BASE_DB_CONFIG)
            batch_size = 1000
            for i in range(0, len(facts), batch_size):
                insert_financial_data(conn, facts[i : i + batch_size])

        # Analyze results
        print(f"\n3️⃣ Analysis Results")
        print("-" * 40)

        display_fact_summary(facts)
        analyze_revenue_facts(facts)

        # Save results
        print(f"\n4️⃣ Saving Results")
        print("-" * 40)

        # Save to savedData directory
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        saved_data_dir = os.path.join(project_root, "savedData")
        os.makedirs(saved_data_dir, exist_ok=True)

        # output_file = save_results_to_file(facts, filing_data, saved_data_dir)

        print(f"\n✅ Extraction Complete!")
        print(f"   📊 Total facts: {len(facts)}")
        # print(f"   📁 Results: {output_file}")

    except Exception as e:
        print(f"💥 Extraction failed: {e}")

    finally:
        # Clean up temporary directory
        try:
            shutil.rmtree(temp_dir)
            print(f"\n🧹 Cleaned up temporary directory")
        except Exception as e:
            print(f"⚠️ Could not clean up temporary directory: {e}")


def main():

    ticker = "FDX"
    cik = "1048911"
    missing_fillings_with_inffered_data = (
        get_missing_sec_filings_with_inferred_metadata(
            cik=cik, ticker=ticker, min_year=2017, verbose=False
        )
    )

    missing_10q = missing_fillings_with_inffered_data.get("missing_10q", [])
    missing_10k = missing_fillings_with_inffered_data.get("missing_10k", [])
    # Print each missing 10-Q as a JSON object, one per line - limited to 1 !!!
    print(f"10-Q missing entries: {len(missing_10q)}")
    for entry in missing_10q:
        print(f"BATCHTAG USED: {entry.get("BatchTag")}")
        print(json.dumps(entry, ensure_ascii=False))
        execute_extraction_for_filling(
            {
                "Cik": entry.get("Cik"),
                "BatchTag": entry.get("BatchTag"),
                "ticker": ticker,
                "filing_type": "10-Q",
                "fiscal_period": entry.get("FiscalPeriod"),
                "fiscal_year": entry.get("FiscalYear"),
                "ddate": entry.get("Ddate"),
                "Adsh": entry.get("Adsh"),
            }
        )
    # Print each missing 10-K as a JSON object, one per line - limited to 1 !!!
    print(f"10-K missing entries: {len(missing_10k)}")
    for entry in missing_10k:
        print(f"BATCHTAG USED: {entry.get("BatchTag")}")
        print(json.dumps(entry, ensure_ascii=False))
        execute_extraction_for_filling(
            {
                "Cik": entry.get("Cik"),
                "BatchTag": entry.get("BatchTag"),
                "ticker": ticker,
                "filing_type": "10-K",
                "fiscal_period": entry.get("FiscalPeriod"),
                "fiscal_year": entry.get("FiscalYear"),
                "ddate": entry.get("Ddate"),
                "Adsh": entry.get("Adsh"),
            }
        )


if __name__ == "__main__":
    main()
