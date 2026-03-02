import os
import time
import concurrent.futures
from data_loaders.arelle_data_loaders.arelle_missing_fillings_processor import (
    arelle_missing_fillings_processing_ticker,
)
from data_loaders.edgar_financial_loader import (
    run_financial_data_loader,
    run_load_tag_data_into_db,
)
from db.cik_ticker_checked_operations import (
    get_all_cik_ticker_checked,
    mark_cik_tickers_checked,
)
from generic_utils.log_to_files_util import log_to_file
import json


# Batch process and load SEC financial data for multiple batch tags.
def batchLoadSecData():
    batch_tags = [
        "2012q1",
        "2012q2",
        "2012q3",
        "2012q4",
        "2011q1",
        "2011q2",
        "2011q3",
        "2011q4",
        "2010q1",
        "2010q2",
        "2010q3",
        "2010q4",
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


def batchLoadSecDataParallel(batch_tags=None):
    # Accepts an iterable of batch tag strings. If not provided, fall back
    # to the historical default list for backward compatibility.
    if batch_tags is None:
        batch_tags = [
            "2012q1",
            "2012q2",
            "2012q3",
            "2012q4",
            "2011q1",
            "2011q2",
            "2011q3",
            "2011q4",
            "2010q1",
            "2010q2",
            "2010q3",
            "2010q4",
            # Add more as needed
        ]
    base_data_dir = r"D:\FinanceProjects\edgarFinancialStatements"
    print("Starting parallel batch processing...")
    print("os.cpu_count(): ", os.cpu_count())
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
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


def batchLoadSecTagDataParallel(batch_tags=None):
    # Accepts an iterable of batch tag strings. If not provided, fall back
    # to the historical default list for backward compatibility.
    if batch_tags is None:
        batch_tags = [
            "2012q1",
            "2012q2",
            "2012q3",
            "2012q4",
            "2011q1",
            "2011q2",
            "2011q3",
            "2011q4",
            "2010q1",
            "2010q2",
            "2010q3",
            "2010q4",
            # Add more as needed
        ]
    base_data_dir = r"D:\FinanceProjects\edgarFinancialStatements"
    print("Starting parallel batch tag data processing...")
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        futures = []
        for batch_tag in batch_tags:
            bulk_data_dir = os.path.join(base_data_dir, batch_tag)
            print(
                f"Processing tag data for batch: {batch_tag} in {bulk_data_dir}",
                flush=True,
            )
            futures.append(executor.submit(run_load_tag_data_into_db, bulk_data_dir))
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                print(f"Batch finished: {result}")
            except Exception as e:
                print(f"Batch failed: {e}")
    elapsed = time.time() - start_time
    print(f"Finished all batches in {elapsed:.2f} seconds", flush=True)


def batchLoadMissingSecDataUsingArelle(batch_size=100, min_year=2013):
    """
    Used to process missing fillings with fetching the needed xml and related files and parsing them using
    arelle pacakge - batches are set as to not overload and chunk too many potential failures
    """
    start_ts = time.time()
    print(
        f"Starting batchLoadMissingSecDataUsingArelle at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_ts))}",
        flush=True,
    )

    all_cik_ticker_pairs = get_all_cik_ticker_checked(limit=batch_size, checked=False)

    for entry in all_cik_ticker_pairs:
        cik, ticker, checked = entry  # Clean tuple unpacking
        if checked:
            print(f"Skipping already processed: {cik}-{ticker}")
            continue
        print(f"Processing missing data for: {cik}-{ticker}")

        result = arelle_missing_fillings_processing_ticker(
            cik=cik,
            ticker=ticker,
            verbosity=False,
            insert_to_db=True,
            min_year=min_year,
        )
        mark_cik_tickers_checked(cik, checked=True)

        if result:
            print(f"❌ Errors processing missing data for: {cik}-{ticker}")
            log_failed_extractions(result)
            continue

    end_ts = time.time()
    print(
        f"Total elapsed: {end_ts - start_ts:.2f} seconds",
        flush=True,
    )


def log_failed_extractions(failed_extractions):
    log_file = r"D:\FinanceProjects\edgarDataManagementPython\logs\arelle_failed_extractions.log"
    for extraction in failed_extractions:
        try:
            entry = {
                "cik": extraction.get("cik"),
                "adsh": extraction.get("adsh"),
                "text": extraction.get("text"),
            }
            line = json.dumps(entry, ensure_ascii=False)
        except Exception:
            line = str(extraction)
        log_to_file(log_file, line)
