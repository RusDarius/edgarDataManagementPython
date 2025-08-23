# Standard library imports
import os
import json
import shutil
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# Third-party imports
import requests

# Project-specific imports
from data_loaders.sec_api_loaders.missing_filings_utils import (
    build_sec_edgar_urls,
    validate_filing_data_for_url_building,
    download_ixbrl_with_fallback,
)

# User agent for SEC requests
USER_AGENT = "EdgarDataExtractor contact@yourcompany.com"


def download_file_safely(url: str, local_path: str, description: str = "file") -> bool:
    """Download a file from URL to local path with error handling."""
    try:
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        # Ensure directory exists
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        with open(local_path, "wb") as f:
            f.write(response.content)
        return True
    except Exception as e:
        print(f"    ❌ Failed to download {description}: {e}")
        return False


def download_single_filing_files(
    filing_data: Dict[str, Any], base_download_dir: str, verbosity: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Download all required files for a single filing to organized directory structure.

    Args:
        filing_data: Filing metadata dict
        base_download_dir: Base directory for downloads (e.g., savedData/downloads)
        verbosity: Whether to print progress messages

    Returns:
        Dict with filing info and downloaded file paths, or None if failed
    """
    try:
        if not validate_filing_data_for_url_building(filing_data):
            if verbosity:
                print(f"❌ Filing data validation failed for {filing_data.get('Adsh')}")
            return None

        # Create organized directory structure
        cik = str(filing_data.get("Cik", "unknown"))
        adsh = filing_data.get("Adsh", "unknown")
        filing_dir = os.path.join(base_download_dir, f"cik_{cik}", adsh)
        os.makedirs(filing_dir, exist_ok=True)

        # Build URLs
        urls_info = build_sec_edgar_urls(filing_data)
        if verbosity:
            print(f"🌐 Built URLs for filing: {adsh}")

        downloaded_files = {}

        # Download main iXBRL document
        if verbosity:
            print(f"📥 Downloading iXBRL document for {adsh}...")

        download_result = download_ixbrl_with_fallback(
            filing_data, filing_dir, verbose=verbosity
        )

        if download_result["success"]:
            downloaded_files["ixbrl"] = download_result["local_path"]
            if verbosity:
                print(
                    f"    ✅ Downloaded iXBRL: {os.path.basename(download_result['local_path'])}"
                )
        else:
            if verbosity:
                print(f"❌ Failed to download main iXBRL: {download_result['error']}")
            return None

        # Download schema files
        schema_downloaded = 0
        schema_total = len(urls_info["schema_urls"])

        for schema_type, schema_url in urls_info["schema_urls"].items():
            schema_filename = os.path.basename(schema_url)
            schema_path = os.path.join(filing_dir, schema_filename)

            if download_file_safely(schema_url, schema_path, f"{schema_type} schema"):
                downloaded_files[schema_type] = schema_path
                schema_downloaded += 1

        if verbosity:
            print(
                f"📊 Schema summary: {schema_downloaded}/{schema_total} files downloaded"
            )

        # Save metadata
        metadata = {
            "filing_data": filing_data,
            "downloaded_files": downloaded_files,
            "download_timestamp": time.time(),
            "download_success": True,
            "schemas_downloaded": schema_downloaded,
            "schemas_total": schema_total,
        }

        metadata_path = os.path.join(filing_dir, "metadata.json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2, default=str)

        return {
            "filing_data": filing_data,
            "filing_dir": filing_dir,
            "downloaded_files": downloaded_files,
            "metadata_path": metadata_path,
            "success": True,
        }

    except Exception as e:
        if verbosity:
            print(f"💥 Error downloading files for {filing_data.get('Adsh')}: {e}")
        return None


def batch_download_filing_files(
    filing_data_list: List[Dict[str, Any]],
    max_workers: int = 5,
    verbosity: bool = True,
    save_manifest: bool = False,
) -> Dict[str, Any]:
    """
    Download filing files for an array of filing_data entries to savedData/downloads folder.

    Args:
        filing_data_list: List of filing metadata dicts
        max_workers: Number of concurrent download threads
        verbosity: Whether to print progress messages

    Returns:
        Dict with download results and manifest information
    """
    if verbosity:
        print(f"🚀 Starting batch download of {len(filing_data_list)} filings")
        print("=" * 60)

    # Setup base download directory
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    )
    base_download_dir = os.path.join(project_root, "savedData", "downloads")
    os.makedirs(base_download_dir, exist_ok=True)

    if verbosity:
        print(f"📁 Download directory: {base_download_dir}")

    # Track results
    successful_downloads = []
    failed_downloads = []
    start_time = time.time()

    # Download files concurrently
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all download tasks
        future_to_filing = {
            executor.submit(
                download_single_filing_files, filing_data, base_download_dir, False
            ): filing_data
            for filing_data in filing_data_list
        }

        # Process completed downloads
        for i, future in enumerate(as_completed(future_to_filing), 1):
            filing_data = future_to_filing[future]
            adsh = filing_data.get("Adsh", "unknown")

            try:
                result = future.result()
                if result and result.get("success"):
                    successful_downloads.append(result)
                    if verbosity:
                        print(f"✅ [{i}/{len(filing_data_list)}] Downloaded: {adsh}")
                else:
                    failed_downloads.append(
                        {"filing_data": filing_data, "error": "Download failed"}
                    )
                    if verbosity:
                        print(f"❌ [{i}/{len(filing_data_list)}] Failed: {adsh}")

            except Exception as e:
                failed_downloads.append({"filing_data": filing_data, "error": str(e)})
                if verbosity:
                    print(f"💥 [{i}/{len(filing_data_list)}] Error: {adsh} - {e}")

    # Create batch manifest
    elapsed_time = time.time() - start_time
    manifest = {
        "batch_info": {
            "total_filings": len(filing_data_list),
            "successful_downloads": len(successful_downloads),
            "failed_downloads": len(failed_downloads),
            "download_time_seconds": elapsed_time,
            "base_download_dir": base_download_dir,
            "timestamp": time.time(),
        },
        "successful_downloads": successful_downloads,
        "failed_downloads": failed_downloads,
    }

    # Save manifest
    if save_manifest:
        manifest_path = os.path.join(
            base_download_dir, f"batch_manifest_{int(time.time())}.json"
        )
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, default=str)

    if verbosity:
        print(f"\n📊 Batch Download Summary:")
        print(f"   ✅ Successful: {len(successful_downloads)}")
        print(f"   ❌ Failed: {len(failed_downloads)}")
        print(f"   ⏱️ Total time: {elapsed_time:.2f} seconds")
        if save_manifest:
            print(f"   📄 Manifest: {manifest_path}")

    return manifest


def get_downloaded_filings_manifest(manifest_path: str) -> Optional[Dict[str, Any]]:
    """
    Load a previously created download manifest.

    Args:
        manifest_path: Path to the manifest JSON file

    Returns:
        Manifest dict or None if file doesn't exist
    """
    try:
        if not os.path.exists(manifest_path):
            return None

        with open(manifest_path, "r") as f:
            return json.load(f)

    except Exception as e:
        print(f"❌ Error loading manifest: {e}")
        return None


def cleanup_download_directory(
    base_download_dir: Optional[str] = None, older_than_days: int = 7
) -> None:
    """
    Clean up old download files to manage disk space.

    Args:
        base_download_dir: Base download directory (defaults to savedData/downloads)
        older_than_days: Remove files older than this many days
    """
    if base_download_dir is None:
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        )
        base_download_dir = os.path.join(project_root, "savedData", "downloads")

    if not os.path.exists(base_download_dir):
        return

    cutoff_time = time.time() - (older_than_days * 24 * 60 * 60)
    removed_count = 0

    try:
        for root, dirs, files in os.walk(base_download_dir):
            for file in files:
                file_path = os.path.join(root, file)
                if os.path.getmtime(file_path) < cutoff_time:
                    os.remove(file_path)
                    removed_count += 1

        # Remove empty directories
        for root, dirs, files in os.walk(base_download_dir, topdown=False):
            for dir in dirs:
                dir_path = os.path.join(root, dir)
                try:
                    if not os.listdir(dir_path):  # Directory is empty
                        os.rmdir(dir_path)
                except OSError:
                    pass  # Directory not empty or other error

        print(f"🧹 Cleanup complete: Removed {removed_count} old files")

    except Exception as e:
        print(f"⚠️ Cleanup error: {e}")
