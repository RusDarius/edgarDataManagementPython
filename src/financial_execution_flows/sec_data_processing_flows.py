import os
import time

import concurrent.futures
from data_loaders.arelle_data_loaders.arelle_missing_fillings_processor import (
    arelle_missing_fillings_processing_ticker,
)
from data_loaders.edgar_financial_loader import run_financial_data_loader
from db.cik_ticker_checked_operations import get_all_cik_ticker_checked


# Batch process and load SEC financial data for multiple batch tags.
def batchLoadSecData():
    batch_tags = [
        "2015q1",
        "2015q2",
        "2015q3",
        "2015q4",
        "2016q1",
        "2016q2",
        "2016q3",
        "2016q4",
        "2017q1",
        "2017q2",
        "2017q3",
        "2017q4",
        "2018q1",
        "2018q2",
        "2018q3",
        "2018q4",
        # Add more as needed
    ]
    base_data_dir = r"D:\Projects\StocksDataEDGAR"

    for batch_tag in batch_tags:
        bulk_data_dir = os.path.join(base_data_dir, batch_tag)
        print(f"Processing batch: {batch_tag} in {bulk_data_dir}", flush=True)
        start_time = time.time()
        run_financial_data_loader(bulk_data_dir, batch_tag)
        elapsed = time.time() - start_time
        print(f"Finished batch: {batch_tag} in {elapsed:.2f} seconds", flush=True)


def batchLoadSecDataParallel():
    batch_tags = [
        "2025q1",
        "2025q2",
        # Add more as needed
    ]
    base_data_dir = r"D:\Projects\StocksDataEDGAR"
    print("Starting parallel batch processing...")
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        for batch_tag in batch_tags:
            bulk_data_dir = os.path.join(base_data_dir, batch_tag)
            print(f"Processing batch: {batch_tag} in {bulk_data_dir}", flush=True)
            futures.append(
                executor.submit(run_financial_data_loader, bulk_data_dir, batch_tag)
            )
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                print(f"Batch finished: {result}")
            except Exception as e:
                print(f"Batch failed: {e}")
    elapsed = time.time() - start_time
    print(f"Finished all batches in {elapsed:.2f} seconds", flush=True)


def batchLoadMissingSecDataUsingArelle(batch_size=100):
    """
    Used to process missing fillings with fetching the needed xml and related files and parsing them using
    arelle pacakge - batches are set as to not overload and chunk too many potential failures
    """
    all_cik_ticker_pairs = get_all_cik_ticker_checked()

    for entry in all_cik_ticker_pairs[:batch_size]:
        cik, ticker, checked = entry  # Clean tuple unpacking
        if checked:
            print(f"Skipping already processed: {cik}-{ticker}")
            continue
        print(f"Processing missing data for: {cik}-{ticker}")
        arelle_missing_fillings_processing_ticker(cik=cik, ticker=ticker)
