# Standard library imports
import os
import json
import warnings
import logging
import sys
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# Project-specific imports
from data_loaders.data_extractors.arelle_extractors.arelle_uitls_data_extractors import (
    extract_datatype_value_from_fact,
    extract_numeric_value_from_fact,
    extract_quarters_covered,
    extract_segment_string,
    extract_unit_string,
    get_fact_string_value,
)

# Import Arelle
try:
    from arelle import Cntlr

    ARELLE_AVAILABLE = True
except ImportError:
    print("⚠️ Warning: Arelle not available. Please install: pip install arelle")
    ARELLE_AVAILABLE = False


def load_batch_manifest(manifest_path: str) -> Optional[Dict[str, Any]]:
    """
    Load and parse a batch manifest JSON file.

    Args:
        manifest_path: Path to the batch manifest JSON file

    Returns:
        Dict containing manifest data or None if failed
    """
    try:
        if not os.path.exists(manifest_path):
            print(f"❌ Manifest file not found: {manifest_path}")
            return None

        with open(manifest_path, "r") as f:
            manifest = json.load(f)

        return manifest

    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in manifest file: {e}")
        return None
    except Exception as e:
        print(f"❌ Error loading manifest: {e}")
        return None


def get_manifest_from_downloads(
    manifest_filename: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Load a batch manifest from the savedData/downloads directory.

    Args:
        manifest_filename: Specific manifest filename, or None for latest

    Returns:
        Dict containing manifest data or None if failed
    """
    # Get project root and downloads directory
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    )
    downloads_dir = os.path.join(project_root, "savedData", "downloads")

    if not os.path.exists(downloads_dir):
        print(f"❌ Downloads directory not found: {downloads_dir}")
        return None

    # If specific filename provided, use it
    if manifest_filename:
        manifest_path = os.path.join(downloads_dir, manifest_filename)
        return load_batch_manifest(manifest_path)

    # Otherwise, find the latest manifest
    manifest_files = [
        f
        for f in os.listdir(downloads_dir)
        if f.startswith("batch_manifest_") and f.endswith(".json")
    ]

    if not manifest_files:
        print("❌ No batch manifest files found in downloads directory")
        return None

    # Sort by timestamp (extract from filename) and get latest
    manifest_files.sort(key=lambda x: int(x.split("_")[-1].split(".")[0]), reverse=True)
    latest_manifest = manifest_files[0]

    manifest_path = os.path.join(downloads_dir, latest_manifest)
    print(f"📄 Loading latest manifest: {latest_manifest}")

    return load_batch_manifest(manifest_path)


def get_successful_downloads_from_manifest(
    manifest_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Extract successful downloads list from manifest data.

    Args:
        manifest_data: Loaded manifest dictionary

    Returns:
        List of successful download dictionaries
    """
    if not manifest_data:
        return []

    return manifest_data.get("successful_downloads", [])


def get_filing_paths_from_manifest(
    manifest_data: Dict[str, Any],
) -> List[Dict[str, str]]:
    """
    Extract filing paths and metadata from manifest data.

    Args:
        manifest_data: Loaded manifest dictionary

    Returns:
        List of dicts with filing paths and basic info
    """
    successful_downloads = get_successful_downloads_from_manifest(manifest_data)
    filing_paths = []

    for download in successful_downloads:
        filing_data = download.get("filing_data", {})
        downloaded_files = download.get("downloaded_files", {})

        filing_info = {
            "adsh": filing_data.get("Adsh", "unknown"),
            "cik": filing_data.get("Cik", "unknown"),
            "ticker": filing_data.get("Ticker", filing_data.get("ticker", "unknown")),
            "filing_type": filing_data.get(
                "FilingType", filing_data.get("filing_type", "unknown")
            ),
            "ixbrl_path": downloaded_files.get("ixbrl"),
            "filing_dir": download.get("filing_dir"),
            "metadata_path": download.get("metadata_path"),
            "ddate": filing_data.get("Ddate"),
            "batch_tag": filing_data.get("BatchTag"),
            "fiscal_period": filing_data.get("FiscalPeriod"),
            "fiscal_year": filing_data.get("FiscalYear"),
        }

        filing_paths.append(filing_info)

    return filing_paths


def print_manifest_summary(manifest_data: Dict[str, Any]) -> None:
    """
    Print a summary of the manifest data.

    Args:
        manifest_data: Loaded manifest dictionary
    """
    if not manifest_data:
        print("❌ No manifest data to summarize")
        return

    batch_info = manifest_data.get("batch_info", {})
    successful_downloads = manifest_data.get("successful_downloads", [])
    failed_downloads = manifest_data.get("failed_downloads", [])

    print(f"\n📊 Manifest Summary:")
    print(f"   Total filings: {batch_info.get('total_filings', 0)}")
    print(f"   ✅ Successful downloads: {len(successful_downloads)}")
    print(f"   ❌ Failed downloads: {len(failed_downloads)}")
    print(
        f"   ⏱️ Download time: {batch_info.get('download_time_seconds', 0):.2f} seconds"
    )
    print(f"   📁 Base directory: {batch_info.get('base_download_dir', 'unknown')}")

    if successful_downloads:
        print(f"\n   📄 Successful filings:")
        for download in successful_downloads:
            filing_data = download.get("filing_data", {})
            adsh = filing_data.get("Adsh", "unknown")
            ticker = filing_data.get("Ticker", filing_data.get("ticker", "unknown"))
            filing_type = filing_data.get(
                "FilingType", filing_data.get("filing_type", "unknown")
            )
            print(f"      - {adsh} ({ticker} - {filing_type})")


def suppress_arelle_warnings():
    """Suppress non-critical Arelle warnings that don't affect fact extraction."""
    warnings.filterwarnings("ignore", category=UserWarning, module="arelle")
    arelle_logger = logging.getLogger("arelle")
    arelle_logger.setLevel(logging.ERROR)
    return arelle_logger


def extract_facts_from_single_filing(
    filing_info: Dict[str, Any], verbosity: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Extract facts from a single filing using Arelle.

    Args:
        filing_info: Dict containing filing path and metadata
        verbosity: Whether to print progress messages

    Returns:
        Dict with extracted facts and metadata, or None if failed
    """
    if not ARELLE_AVAILABLE:
        if verbosity:
            print("❌ Arelle not available for fact extraction")
        return None

    ixbrl_path = filing_info.get("ixbrl_path")
    if not ixbrl_path or not os.path.exists(ixbrl_path):
        if verbosity:
            print(f"❌ iXBRL file not found: {ixbrl_path}")
        return None

    facts = []
    ctrl = None

    try:
        # Suppress Arelle warnings
        suppress_arelle_warnings()

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
            return None

        if verbosity:
            print(f"📊 XBRL model loaded: {len(model_xbrl.facts)} total facts")

        # Extract filing metadata
        adsh = filing_info.get("adsh")
        cik = filing_info.get("cik")
        batch_tag = f"{filing_info.get("batch_tag")}_{cik}"
        ticker = filing_info.get("ticker")
        filing_type = filing_info.get("filing_type")
        fiscal_period = filing_info.get("fiscal_period")
        fiscal_year = filing_info.get("fiscal_year")
        ddate = filing_info.get("ddate")

        if verbosity:
            print(f"🔄 Processing facts for {adsh}...")

        processed_facts = 0
        for fact in model_xbrl.facts:
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

                # Extract fact values
                value_string = get_fact_string_value(fact)
                value = extract_numeric_value_from_fact(fact)
                datatype = extract_datatype_value_from_fact(fact)
                uom = extract_unit_string(getattr(fact, "unit", None))

                # Extract context information
                segment = None
                qtrs = None
                period_end = None
                instant_date = None

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

                # Create fact tuple (same format as original extract_all_facts_with_arelle)
                facts.append(
                    (
                        cik,  # Cik
                        ticker,  # Ticker
                        filing_type,  # filing_type
                        "Q1",  # fiscal_period (default)
                        2024,  # fiscal_year (default)
                        concept_name,  # concept_name
                        value,  # value
                        value_string,  # value_string
                        uom,  # uom
                        datatype,  # datatype
                        adsh,  # adsh
                        final_period_date,  # final_period_date
                        ddate,  # ddate (not available from manifest)
                        segment,  # segment
                        qtrs,  # qtrs
                        batch_tag,  # batch_tag
                    )
                )
                processed_facts += 1

            except Exception as e:
                if verbosity:
                    print(f"⚠️ Error processing fact: {e}")
                continue

        if verbosity:
            print(f"✅ Fact extraction complete: {processed_facts} facts processed")

        return {
            "filing_info": filing_info,
            "facts": facts,
            "facts_count": len(facts),
            "processing_success": True,
            "adsh": adsh,
            "cik": cik,
            "ticker": ticker,
        }

    except Exception as e:
        if verbosity:
            print(f"💥 Error during Arelle fact extraction: {e}")
        return None

    finally:
        # Clean up Arelle resources
        if ctrl:
            try:
                ctrl.close()
            except:
                pass


def batch_process_manifest_filings(
    manifest_data: Optional[Dict[str, Any]] = None,
    manifest_filename: Optional[str] = None,
    max_workers: int = 3,
    verbosity: bool = True,
) -> Dict[str, Any]:
    """
    Process all filings from a manifest using Arelle and save results to savedData/processed.

    Args:
        manifest_data: Pre-loaded manifest data (optional)
        manifest_filename: Specific manifest filename (optional)
        max_workers: Number of concurrent processing threads
        verbosity: Whether to print progress messages

    Returns:
        Dict with processing results and summary
    """
    if verbosity:
        print(f"🚀 Starting batch processing from manifest")
        print("=" * 60)

    # Load manifest if not provided
    if not manifest_data:
        manifest_data = get_manifest_from_downloads(manifest_filename)

    if not manifest_data:
        return {"error": "Failed to load manifest"}

    # Get filing paths and data for usage in concept extraction
    filing_paths = get_filing_paths_from_manifest(manifest_data)

    if not filing_paths:
        print("❌ No filings found in manifest")
        return {"error": "No filings to process"}

    if verbosity:
        print(f"📋 Found {len(filing_paths)} filings to process")

    # Setup output directory
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    )
    processed_dir = os.path.join(project_root, "savedData", "processed")
    os.makedirs(processed_dir, exist_ok=True)

    if verbosity:
        print(f"📁 Output directory: {processed_dir}")

    # Track results
    successful_processing = []
    failed_processing = []
    start_time = time.time()

    # Process filings concurrently
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all processing tasks
        future_to_filing = {
            executor.submit(
                extract_facts_from_single_filing, filing_info, False
            ): filing_info
            for filing_info in filing_paths
        }

        # Process completed extractions
        for i, future in enumerate(as_completed(future_to_filing), 1):
            filing_info = future_to_filing[future]
            adsh = filing_info.get("adsh", "unknown")

            try:
                result = future.result()
                if result and result.get("processing_success"):
                    # Save facts to JSON file
                    cik = result["cik"]
                    output_filename = f"{cik}_{adsh}_facts.json"
                    output_path = os.path.join(processed_dir, output_filename)

                    with open(output_path, "w") as f:
                        json.dump(
                            {
                                "filing_info": result["filing_info"],
                                "facts": result["facts"],
                                "facts_count": result["facts_count"],
                                "processing_timestamp": time.time(),
                            },
                            f,
                            indent=2,
                            default=str,
                        )

                    successful_processing.append(
                        {
                            "filing_info": filing_info,
                            "facts_count": result["facts_count"],
                            "output_file": output_path,
                        }
                    )

                    if verbosity:
                        print(
                            f"✅ [{i}/{len(filing_paths)}] Processed: {adsh} ({result['facts_count']} facts)"
                        )
                else:
                    failed_processing.append(
                        {"filing_info": filing_info, "error": "Processing failed"}
                    )
                    if verbosity:
                        print(f"❌ [{i}/{len(filing_paths)}] Failed: {adsh}")

            except Exception as e:
                failed_processing.append({"filing_info": filing_info, "error": str(e)})
                if verbosity:
                    print(f"💥 [{i}/{len(filing_paths)}] Error: {adsh} - {e}")

    # Create processing summary
    elapsed_time = time.time() - start_time
    processing_summary = {
        "processing_info": {
            "total_filings": len(filing_paths),
            "successful_processing": len(successful_processing),
            "failed_processing": len(failed_processing),
            "processing_time_seconds": elapsed_time,
            "output_directory": processed_dir,
            "timestamp": time.time(),
        },
        "successful_processing": successful_processing,
        "failed_processing": failed_processing,
    }

    # Save processing summary
    summary_filename = f"processing_summary_{int(time.time())}.json"
    summary_path = os.path.join(processed_dir, summary_filename)
    with open(summary_path, "w") as f:
        json.dump(processing_summary, f, indent=2, default=str)

    if verbosity:
        print(f"\n📊 Batch Processing Summary:")
        print(f"   ✅ Successful: {len(successful_processing)}")
        print(f"   ❌ Failed: {len(failed_processing)}")
        print(f"   ⏱️ Total time: {elapsed_time:.2f} seconds")
        print(f"   📄 Summary: {summary_path}")

        total_facts = sum(p["facts_count"] for p in successful_processing)
        print(f"   📊 Total facts extracted: {total_facts}")

    return processing_summary
