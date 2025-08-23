#!/usr/bin/env python3
"""
Demo script showing how to use the batch download functionality.
"""
import os
from data_loaders.arelle_data_loaders.arelle_missing_fillings_batch_data_processing import (
    batch_process_manifest_filings,
    get_filing_paths_from_manifest,
    get_manifest_from_downloads,
    print_manifest_summary,
)
from generic_utils.accept_utf8_encoding import accept_utf8_encoding

accept_utf8_encoding()


def demo_manifest_access():
    """
    Demo function showing how to access manifest data.
    """
    print("🚀 Demo: Accessing Batch Manifest Data")
    print("=" * 50)

    # Load latest manifest
    manifest = get_manifest_from_downloads()

    if not manifest:
        print("❌ Could not load manifest - ensure downloads have been completed")
        return

    # Print summary
    print_manifest_summary(manifest)

    # Get filing paths
    filing_paths = get_filing_paths_from_manifest(manifest)

    print(f"\n📋 Filing Paths:")
    for i, filing in enumerate(filing_paths, 1):
        print(f"   {i}. {filing['adsh']} ({filing['ticker']})")
        print(f"      iXBRL: {filing['ixbrl_path']}")
        print(f"      Directory: {filing['filing_dir']}")

        # Check if files exist
        if filing["ixbrl_path"] and os.path.exists(filing["ixbrl_path"]):
            print(f"      ✅ iXBRL file exists")
        else:
            print(f"      ❌ iXBRL file missing")
        print()


def demo_batch_processing():
    """
    Demo function showing complete batch processing workflow.
    """
    print("🚀 Demo: Complete Batch Processing Workflow")
    print("=" * 60)

    # Step 1: Load manifest
    print("1️⃣ Loading manifest...")
    manifest = get_manifest_from_downloads()

    if not manifest:
        print("❌ Could not load manifest - ensure downloads have been completed")
        return

    # Step 2: Process filings
    print("\n2️⃣ Processing filings with Arelle...")
    result = batch_process_manifest_filings(
        manifest_data=manifest, max_workers=5, verbosity=True  # Limit for demo
    )

    if result and not result.get("error"):
        print("\n✅ Demo completed successfully!")
    else:
        print(f"\n❌ Demo failed: {result.get('error', 'Unknown error')}")


if __name__ == "__main__":
    demo_batch_processing()
