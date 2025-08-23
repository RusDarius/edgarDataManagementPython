import json
import os

#!/usr/bin/env python3
"""
Demo script showing how to use the batch download functionality.
"""
from generic_utils.accept_utf8_encoding import accept_utf8_encoding

accept_utf8_encoding()

from data_loaders.arelle_data_loaders.arelle_missing_fillings_batch_data_download import (
    batch_download_filing_files,
)
from data_loaders.sec_api_loaders.missing_filings_utils import (
    get_missing_sec_filings_with_inferred_metadata,
)


def demo_batch_download_for_ticker(ticker: str, cik: str):
    """
    Demo: Download missing filings for a specific ticker using batch functionality.

    Args:
        ticker: Stock ticker symbol
        cik: CIK number
        max_filings: Maximum number of filings to download (for demo purposes)
    """
    print(f"🚀 Demo: Batch Download for {ticker} (CIK: {cik})")
    print("=" * 60)

    try:
        # Step 1: Get missing filings
        print(f"1️⃣ Getting missing filings for {ticker}...")
        missing_fillings = get_missing_sec_filings_with_inferred_metadata(
            cik=cik, ticker=ticker, min_year=2015, verbose=False
        )

        # Combine 10-Q and 10-K filings
        all_missing = []
        missing_10q = missing_fillings.get("missing_10q", [])
        missing_10k = missing_fillings.get("missing_10k", [])

        print(f"   📄 Found {len(missing_10q)} missing 10-Q filings")
        print(f"   📄 Found {len(missing_10k)} missing 10-K filings")

        # Add filing type to each entry
        for entry in missing_10q:
            entry["filing_type"] = "10-Q"
            all_missing.append(entry)

        for entry in missing_10k:
            entry["filing_type"] = "10-K"
            all_missing.append(entry)

        if not all_missing:
            print("   ℹ️ No missing filings found")
            return

        print(f"   🎯 Selected {len(all_missing)} filings for batch download")

        # Step 2: Batch download
        print(f"\n2️⃣ Starting batch download...")
        manifest = batch_download_filing_files(
            filing_data_list=all_missing,
            max_workers=3,  # Limit concurrent downloads
            verbosity=True,
        )

        # Step 3: Show results
        print(f"\n3️⃣ Download Results:")
        batch_info = manifest["batch_info"]
        print(f"   📊 Total filings: {batch_info['total_filings']}")
        print(f"   ✅ Successful downloads: {batch_info['successful_downloads']}")
        print(f"   ❌ Failed downloads: {batch_info['failed_downloads']}")
        print(f"   ⏱️ Download time: {batch_info['download_time_seconds']:.2f} seconds")
        print(f"   📁 Download directory: {batch_info['base_download_dir']}")

        # Show successful downloads
        if manifest["successful_downloads"]:
            print(f"\n   ✅ Successfully downloaded filings:")
            for download in manifest["successful_downloads"]:
                filing_data = download["filing_data"]
                adsh = filing_data.get("Adsh", "unknown")
                filing_type = filing_data.get("filing_type", "unknown")
                print(f"      - {adsh} ({filing_type})")

        # Show failed downloads
        if manifest["failed_downloads"]:
            print(f"\n   ❌ Failed downloads:")
            for failure in manifest["failed_downloads"]:
                filing_data = failure["filing_data"]
                adsh = filing_data.get("Adsh", "unknown")
                error = failure.get("error", "unknown error")
                print(f"      - {adsh}: {error}")

        return manifest

    except Exception as e:
        print(f"💥 Demo failed: {e}")
        return None


# Batch download for multiple tickers, storing all results in one batch_manifest
def batch_download_multiple_tickers_to_manifest(
    ticker_cik_list, manifest_path=None, min_year=2015, max_workers=3
):
    """
    Batch download filings for multiple tickers, storing all results in one manifest file.
    Args:
        ticker_cik_list: List of (ticker, cik) tuples
        manifest_path: Path to output manifest JSON file
        min_year: Minimum year for missing filings
        max_workers: Max concurrent downloads per ticker
    """
    all_successful = []
    all_failed = []
    total_filings = 0
    start_time = None
    base_download_dir = None
    for ticker, cik in ticker_cik_list:
        print(f"\n=== Batch Download for {ticker} (CIK: {cik}) ===")
        missing_fillings = get_missing_sec_filings_with_inferred_metadata(
            cik=cik, ticker=ticker, min_year=min_year, verbose=False
        )
        all_missing = []
        missing_10q = missing_fillings.get("missing_10q", [])
        missing_10k = missing_fillings.get("missing_10k", [])
        for entry in missing_10q:
            entry["filing_type"] = "10-Q"
            all_missing.append(entry)
        for entry in missing_10k:
            entry["filing_type"] = "10-K"
            all_missing.append(entry)
        if not all_missing:
            print(f"No missing filings for {ticker}")
            continue
        print(f"   🎯 Selected {len(all_missing)} filings for batch download")
        if start_time is None:
            import time

            start_time = time.time()
        manifest = batch_download_filing_files(
            filing_data_list=all_missing,
            max_workers=max_workers,
            verbosity=True,
        )
        # Collect results
        batch_info = manifest["batch_info"]
        total_filings += batch_info["total_filings"]
        base_download_dir = batch_info["base_download_dir"]
        all_successful.extend(manifest.get("successful_downloads", []))
        all_failed.extend(manifest.get("failed_downloads", []))
    elapsed = 0
    if start_time:
        import time

        elapsed = time.time() - start_time
    # Build combined manifest
    combined_manifest = {
        "batch_info": {
            "total_filings": total_filings,
            "successful_downloads": len(all_successful),
            "failed_downloads": len(all_failed),
            "download_time_seconds": elapsed,
            "base_download_dir": base_download_dir,
        },
        "successful_downloads": all_successful,
        "failed_downloads": all_failed,
    }
    # Write manifest
    if manifest_path is None:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        manifest_path = os.path.join(
            project_root,
            "savedData",
            "downloads",
            f"batch_manifest_multi_{int(time.time())}.json",
        )
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(combined_manifest, f, indent=2)
    print(f"\n✅ Combined batch manifest written to: {manifest_path}")
    # Example usage: batch download for multiple tickers
    # ticker_cik_list = [("FDX", "1048911"), ("ACM", "868857")]
    # batch_download_multiple_tickers_to_manifest(ticker_cik_list)


def batch_download_from_cik_ticker_json(json_path=None, batch_size=1000):
    import time

    start_time = time.time()
    if json_path is None:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        json_path = os.path.join(
            project_root,
            "savedData",
            "all_cik_ticker_pairs_with_financial_concepts_data_v2.json",
        )
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    selected = []
    count = 0
    for entry in data:
        if entry.get("processed", 0) == 0 and count < batch_size:
            selected.append(entry)
            entry["processed"] = 1
            count += 1
    # Write back the updated file in one go for efficiency
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Selected and marked {len(selected)} entries as processed.")
    processing_list = []
    # Feed to demo_batch_download_for_ticker
    for entry in selected:
        cik = str(entry["Cik"])
        ticker = entry["Ticker"]
        processing_list.append((ticker, cik))
    batch_download_multiple_tickers_to_manifest(processing_list, max_workers=5)
    elapsed = time.time() - start_time
    print(f"⏱️ batch_download_from_cik_ticker_json took {elapsed:.2f} seconds.")


if __name__ == "__main__":
    print("🌟 Batch Download Demo")
    print("=====================")

    # Uncomment to run batch download for 1000 unprocessed entries
    batch_download_from_cik_ticker_json(batch_size=500)

    print(f"\n🎉 Demo complete!")
    print(f"   Check savedData/downloads/ folder for downloaded files")
