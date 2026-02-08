# Standard library imports
import os
import json
import shutil
import tempfile
import warnings
import logging
from typing import Dict, List, Any, Optional

# Third-party imports
import requests

# Project-specific imports
from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection
from db.edgar_financial_data_concepts_operations import batch_tag_exists
from data_loaders.sec_api_loaders.missing_filings_utils import (
    build_sec_edgar_urls,
    get_missing_sec_filings_with_inferred_metadata,
    validate_filing_data_for_url_building,
    download_ixbrl_with_fallback,
)
from data_loaders.edgar_financial_loader import insert_financial_data
from data_loaders.data_extractors.arelle_extractors.arelle_uitls_data_extractors import (
    extract_datatype_value_from_fact,
    extract_numeric_value_from_fact,
    extract_quarters_covered,
    extract_segment_string,
    extract_unit_string,
    get_fact_string_value,
)
from data_loaders.arelle_data_loaders.arelle_missing_fillings_processor_utils import (
    analyze_revenue_facts,
    display_fact_summary,
)

# Import Arelle
try:
    from arelle import Cntlr

    ARELLE_AVAILABLE = True
except ImportError:
    print("⚠️ Warning: Arelle not available. Please install: pip install arelle")
    ARELLE_AVAILABLE = False

# Target revenue tags for extraction
USER_AGENT = "EdgarDataExtractor contact@yourcompany.com"


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
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(response.content)
        return True
    except Exception as e:
        print(f"    ❌ Failed to download {description}: {e}")
        return False


def download_filing_files(
    filing_data: Dict[str, Any], download_dir: str, verbosity: bool = True
) -> Optional[Dict[str, str]]:
    """
    Download all required files for a filing using the proven URL building logic.

    Returns:
        Dict with local file paths or None if download failed
    """
    try:
        if not validate_filing_data_for_url_building(filing_data):
            if verbosity:
                print(f"❌ Filing data validation failed")
            return None
        urls_info = build_sec_edgar_urls(filing_data)
        if verbosity:
            print(f"🌐 Built URLs for filing: {filing_data.get('Adsh')}")
            print(f"  📄 iXBRL: {urls_info}")
        downloaded_files = {}
        if verbosity:
            print(f"📥 Downloading iXBRL document with fallback...")
        download_result = download_ixbrl_with_fallback(
            filing_data, download_dir, verbose=verbosity
        )
        if download_result["success"]:
            downloaded_files["ixbrl"] = download_result["local_path"]
            if verbosity:
                print(
                    f"    ✅ Downloaded iXBRL document using: {download_result['ixbrl_url']}"
                )
        else:
            if verbosity:
                print(
                    f"❌ Failed to download main iXBRL document: {download_result['error']}"
                )
            return None
        schema_downloaded = 0
        schema_total = len(urls_info["schema_urls"])
        for schema_type, schema_url in urls_info["schema_urls"].items():
            schema_filename = os.path.basename(schema_url)
            schema_path = os.path.join(download_dir, schema_filename)
            if download_file_safely(schema_url, schema_path, f"{schema_type} schema"):
                downloaded_files[schema_type] = schema_path
                schema_downloaded += 1
        if verbosity:
            print(
                f"📊 Schema download summary: {schema_downloaded}/{schema_total} files downloaded"
            )
        return downloaded_files
    except Exception as e:
        print(f"💥 Error downloading files: {e}")
        return None


def extract_all_facts_with_arelle(
    ixbrl_path: str, filing_data: Dict[str, Any], verbosity: bool = True
) -> List[Any]:
    """
    Extract all financial facts from iXBRL file using Arelle.

    Returns:
        List of extracted facts with metadata
    """
    if not ARELLE_AVAILABLE:
        if verbosity:
            print("❌ Arelle not available for fact extraction")
        return []

    facts = []
    ctrl = None

    try:
        ctrl = Cntlr.Cntlr()
        if verbosity:
            print(f"🔍 Loading XBRL model: {os.path.basename(ixbrl_path)}")
        model_xbrl = ctrl.modelManager.load(
            ixbrl_path,
            isInlineXBRL=True,
            validateInferences=False,
            validateInlineXBRL=False,
            validateDuplicateFacts=False,
            validateDisclosureSystem=False,
        )
        if not model_xbrl or not hasattr(model_xbrl, "facts"):
            if verbosity:
                print("❌ Failed to load XBRL model or no facts available")
            return []
        if verbosity:
            print(
                f"📊 XBRL model loaded successfully: {len(model_xbrl.facts)} total facts"
            )
        adsh = filing_data.get("Adsh")
        cik = filing_data.get("Cik")
        batch_tag = f"{filing_data.get('BatchTag')}_{cik}"
        ticker = filing_data.get("ticker")
        filing_type = filing_data.get("filing_type")
        fiscal_period = filing_data.get("fiscal_period")
        fiscal_year = filing_data.get("fiscal_year")
        ddate = filing_data.get("ddate")
        if verbosity:
            print(f"🔄 Processing facts for {adsh}...")
        processed_facts = 0
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
                value_string = get_fact_string_value(fact)
                value = extract_numeric_value_from_fact(fact)
                datatype = extract_datatype_value_from_fact(fact)
                uom = extract_unit_string(getattr(fact, "unit", None))
                if hasattr(fact, "context") and fact.context is not None:
                    try:
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
                        segment = extract_segment_string(fact.context)
                        qtrs = extract_quarters_covered(fact.context)
                    except Exception:
                        pass
                final_period_date = (
                    period_end if period_end is not None else instant_date
                )
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
        if verbosity:
            print(f"✅ Fact extraction complete: {processed_facts} facts processed")
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


def execute_extraction_for_filling(filing_data, insert_to_db=False, verbosity=True):
    """Main execution function with verbosity control."""
    if verbosity:
        print("🚀 Enhanced XBRL Data Extraction (v2)")
        print("=" * 60)
    temp_dir = tempfile.mkdtemp(prefix="xbrl_extraction_")
    if verbosity:
        print(f"📁 Working directory: {temp_dir}")
    try:
        if verbosity:
            print(f"\n1️⃣ Downloading Filing Files")
            print("-" * 40)
        downloaded_files = download_filing_files(
            filing_data, temp_dir, verbosity=verbosity
        )
        if not downloaded_files:
            if verbosity:
                print("❌ Failed to download required files")
            return
        if verbosity:
            print(f"\n2️⃣ Extracting Facts with Arelle")
            print("-" * 40)
        ixbrl_path = downloaded_files.get("ixbrl")
        if not ixbrl_path:
            if verbosity:
                print("❌ iXBRL file not available")
            return
        facts = extract_all_facts_with_arelle(
            ixbrl_path, filing_data, verbosity=verbosity
        )
        if not facts:
            if verbosity:
                print("❌ No facts extracted")
            return
        if insert_to_db:
            conn = get_mysql_connection(**BASE_DB_CONFIG)
            batch_size = 1000
            for i in range(0, len(facts), batch_size):
                insert_financial_data(conn, facts[i : i + batch_size])
        if verbosity:
            print(f"\n3️⃣ Analysis Results")
            print("-" * 40)
            display_fact_summary(facts)
            analyze_revenue_facts(facts)
            print(f"\n4️⃣ Saving Results")
            print("-" * 40)
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            saved_data_dir = os.path.join(project_root, "savedData")
            os.makedirs(saved_data_dir, exist_ok=True)
            print(f"\n✅ Extraction Complete!")
            print(f"   📊 Total facts: {len(facts)}")
    except Exception as e:
        print(f"💥 Extraction failed: {e}")
    finally:
        try:
            shutil.rmtree(temp_dir)
            if verbosity:
                print(f"\n🧹 Cleaned up temporary directory")
        except Exception as e:
            print(f"⚠️ Could not clean up temporary directory: {e}")


def arelle_missing_fillings_processing_ticker(ticker, cik, verbosity=False):
    missing_fillings_with_inffered_data = (
        get_missing_sec_filings_with_inferred_metadata(
            cik=cik, ticker=ticker, min_year=2018, verbose=False
        ) # min_year can be changed as needed to cover more historical data
    )
    missing_10q = missing_fillings_with_inffered_data.get("missing_10q", [])
    missing_10k = missing_fillings_with_inffered_data.get("missing_10k", [])

    print(f"10-Q missing entries: {len(missing_10q)}")
    for entry in missing_10q:
        # if batch_tag_exists(f"{entry.get('BatchTag')}_{cik}"):
        #     print(f"{entry.get('BatchTag')} exists")
        #     continue
        print(f"BATCHTAG USED: {entry.get('BatchTag')}")
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
            },
            False,
            verbosity=verbosity,
        )

    print(f"10-K missing entries: {len(missing_10k)}")
    for entry in missing_10k:
        # if batch_tag_exists(f"{entry.get('BatchTag')}_{cik}"):
        #     print(f"{entry.get('BatchTag')} exists")
        #     continue
        print(f"BATCHTAG USED: {entry.get('BatchTag')}")
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
            },
            False,
            verbosity=verbosity,
        )
