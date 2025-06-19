from data_loaders.api_client import ApiClient
from data_loaders.edgar_financial_loader import (
    run_financial_data_loader,
    run_load_edgar_submissions,
    run_load_tag_data_into_db,
)
from data_loaders.edgar_workflow import (
    run_edgar_workflow,
    run_guidance_extraction_workflow,
    run_sec_cik_ticker_mapping_workflow,
)
from data_loaders.nyu_data_loader import load_nyu_industry_grouping
import os
import time


# Batch process and load SEC financial data for multiple batch tags.
def batchLoadSecData():
    batch_tags = [
        "2019q4",
        "2019q3",
        "2019q2",
        "2019q1",
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


# Main entry point for running workflows and data loaders.
def main():
    # Example: list of batch tags and their corresponding directories
    pass


if __name__ == "__main__":
    main()
