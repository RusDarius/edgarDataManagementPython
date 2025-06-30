import os
import time
from data_loaders.edgar_financial_loader import run_financial_data_loader


# Batch process and load SEC financial data for multiple batch tags.
def batchLoadSecData():
    batch_tags = [
        "2017q4",
        "2017q3",
        "2017q2",
        "2017q1",
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
