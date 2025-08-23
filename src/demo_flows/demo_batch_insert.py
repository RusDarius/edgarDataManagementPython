#!/usr/bin/env python3
"""
Demo script showing how to use the batch download functionality.
"""
from data_loaders.arelle_data_loaders.arelle_missing_fillings_batch_data_insertion import (
    extract_facts_from_processed_data,
    perform_database_insertion,
)
from generic_utils.accept_utf8_encoding import accept_utf8_encoding

accept_utf8_encoding()


def demo_data_extraction_from_processed():
    all_facts = extract_facts_from_processed_data()


def demo_data_insert_facts():
    import time

    start_time = time.time()
    all_facts = extract_facts_from_processed_data()
    perform_database_insertion(all_facts)
    elapsed = time.time() - start_time
    print(f"⏱️ demo_data_insert_facts took {elapsed:.2f} seconds.")


if __name__ == "__main__":
    demo_data_insert_facts()
