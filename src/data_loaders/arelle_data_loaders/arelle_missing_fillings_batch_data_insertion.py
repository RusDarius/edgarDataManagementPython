"""
Batch Data Insertion Module for Arelle-processed Financial Facts
"""

import json
import os
from typing import List, Tuple, Optional
import concurrent.futures

from data_loaders.edgar_financial_loader import insert_financial_data
from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection
from db.edgar_financial_data_concepts_operations import batch_tag_exists


def extract_facts_from_processed_data(
    processed_dir: Optional[str] = None,
) -> List[Tuple]:
    """
    Extract all facts from processed JSON files in the specified directory.

    Args:
        processed_dir: Path to processed data directory (default: savedData/processed)

    Returns:
        List of fact tuples ready for database insertion
    """
    # Set default processed directory if not provided
    if processed_dir is None:
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        )
        processed_dir = os.path.join(project_root, "savedData", "processed")

    all_facts = []
    if not os.path.exists(processed_dir):
        return all_facts

    def process_file(filename):
        if not filename.endswith("_facts.json"):
            return []
        file_path = os.path.join(processed_dir, filename)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            batch_tag = f"{data.get("filing_info", {}).get("batch_tag", "")}_{data.get("filing_info", {}).get("cik", "")}"
            if batch_tag_exists(batch_tag):
                print(f"BatchTag exists in table already: {batch_tag}")
                return []
            print(f"Processing data for: {batch_tag}")
            facts = data.get("facts", [])
            return [tuple(fact) if isinstance(fact, list) else fact for fact in facts]
        except Exception as e:
            print(f"Error loading {filename}: {e}")
            return []

    filenames = [fn for fn in os.listdir(processed_dir) if fn.endswith("_facts.json")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(process_file, filenames))
    for facts_list in results:
        all_facts.extend(facts_list)
    return all_facts


def perform_database_insertion(facts):
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    print(len(facts))
    batch_size = 10000
    for i in range(0, len(facts), batch_size):
        insert_financial_data(conn, facts[i : i + batch_size])
