import sys
import os
import traceback
from datetime import datetime

# Add the src directory to Python path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from generic_utils.accept_utf8_encoding import accept_utf8_encoding

accept_utf8_encoding()

from .missing_filings_utils import (
    get_missing_sec_filings_with_inferred_metadata,
    process_missing_filings_for_database_insertion,
)
from db.connection_provider import get_mysql_connection

from typing import Optional


def process_multiple_missing_filings(
    ticker: str,
    cik: str,
    min_year: int = 2020,
    verbose: bool = True,
):
    """
    Complete workflow to process missing SEC filings:
    1. Get missing filings with inferred metadata
    2. Extract financial facts from missing filings
    3. Insert facts into edgar_financial_data_concepts table

    Args:
        ticker: Stock ticker symbol
        cik: Company CIK
        min_year: Minimum year to search for missing filings
        batch_tag: Optional batch tag for tracking (defaults to current timestamp)
        verbose: Enable verbose logging

    Returns:
        dict with results and statistics

    Raises:
        Exception: If database insertion fails
    """
    if verbose:
        print(f"=" * 80)
        print(f"PROCESSING MISSING SEC FILINGS FOR {ticker} (CIK: {cik})")
        print(f"=" * 80)

    try:
        # Step 1: Get missing filings with inferred metadata
        if verbose:
            print(
                f"Step 1: Getting missing filings with inferred metadata (min_year={min_year})"
            )
            print("-" * 60)

        result = get_missing_sec_filings_with_inferred_metadata(
            cik=cik, ticker=ticker, min_year=min_year, verbose=verbose
        )

        missing_filings = result.get("all_missing", [])
        missing_10q = result.get("missing_10q", [])
        missing_10k = result.get("missing_10k", [])
        missing_10qk = result.get("missing_10q", []) + result.get("missing_10k", [])

        if verbose:
            print(f"Found {len(missing_filings)} total missing filings")
            print(f"  - Missing 10-Qs: {len(missing_10q)}")
            print(f"  - Missing 10-Ks: {len(missing_10k)}")

        if not missing_10qk:
            if verbose:
                print("No missing filings found. Process completed.")
            return {
                "success": True,
                "total_missing_filings": 0,
                "total_facts_extracted": 0,
                "total_facts_inserted": 0,
            }

        # Step 2: Extract financial facts from missing filings
        if verbose:
            print(
                f"\nStep 2: Extracting financial facts from {len(missing_10qk)} missing filings"
            )
            print("-" * 60)

        all_facts = process_missing_filings_for_database_insertion(
            missing_10qk, verbose=verbose
        )

        if verbose:
            print(f"Extracted {len(all_facts)} total financial facts")

        if not all_facts:
            if verbose:
                print("No financial facts extracted. Process completed.")
            return {
                "success": True,
                "total_missing_filings": len(missing_filings),
                "total_facts_extracted": 0,
                "total_facts_inserted": 0,
            }

        # Step 3: Insert facts into database
        if verbose:
            print(f"\nStep 3: Inserting {len(all_facts)} facts into database")
            print("-" * 60)

        inserted_count = insert_facts_into_database(all_facts, verbose=verbose)

        if verbose:
            print(f"\n✓ Process completed successfully!")
            print(f"Summary:")
            print(f"  - Missing filings processed: {len(missing_10qk)}")
            print(f"  - Financial facts extracted: {len(all_facts)}")
            print(f"  - Facts inserted into database: {inserted_count}")

        return {
            "success": True,
            "total_missing_filings": len(missing_filings),
            "total_facts_extracted": len(all_facts),
            "total_facts_inserted": inserted_count,
            "missing_filings": missing_filings,
        }

    except Exception as e:
        error_msg = f"Error processing missing filings for {ticker}: {e}"
        if verbose:
            print(f"\n✗ {error_msg}")
            print(traceback.format_exc())
        raise Exception(error_msg) from e


def insert_facts_into_database(facts: list, verbose: bool = True) -> int:
    """
    Insert financial facts into edgar_financial_data_concepts table.

    Args:
        facts: List of financial facts formatted for database insertion
        batch_tag: Batch tag for tracking
        verbose: Enable verbose logging

    Returns:
        int: Number of facts successfully inserted

    Raises:
        Exception: If database insertion fails
    """
    if not facts:
        return 0

    connection = None
    cursor = None

    try:
        # Get database connection
        connection = get_mysql_connection()
        cursor = connection.cursor()

        if verbose:
            print(f"Connected to database, preparing to insert {len(facts)} facts...")

        # Add batch tag to all facts and ensure data fits in database columns
        for fact in facts:
            # Truncate fields that might be too long for database columns
            if fact.get("Unit") and len(str(fact["Unit"])) > 32:
                fact["Unit"] = str(fact["Unit"])[:32]

            if fact.get("Concept") and len(str(fact["Concept"])) > 256:
                fact["Concept"] = str(fact["Concept"])[:256]

            if fact.get("ValueString") and len(str(fact["ValueString"])) > 255:
                fact["ValueString"] = str(fact["ValueString"])[:255]

            if fact.get("DataType") and len(str(fact["DataType"])) > 32:
                fact["DataType"] = str(fact["DataType"])[:32]

            if fact.get("Segment") and len(str(fact["Segment"])) > 512:
                fact["Segment"] = str(fact["Segment"])[:512]

        # Prepare INSERT statement
        insert_query = """
        INSERT INTO edgar_financial_data_concepts (
            Cik, Ticker, FilingType, FiscalPeriod, FiscalYear, 
            Concept, Value, ValueString, Unit, DataType, 
            Adsh, PeriodEnd, Ddate, Segment, Qtrs, BatchTag
        ) VALUES (
            %(Cik)s, %(Ticker)s, %(FilingType)s, %(FiscalPeriod)s, %(FiscalYear)s,
            %(Concept)s, %(Value)s, %(ValueString)s, %(Unit)s, %(DataType)s,
            %(Adsh)s, %(PeriodEnd)s, %(Ddate)s, %(Segment)s, %(Qtrs)s, %(BatchTag)s
        )
        """

        # Insert facts in batches for better performance
        batch_size = 1000
        total_inserted = 0

        for i in range(0, len(facts), batch_size):
            batch = facts[i : i + batch_size]

            try:
                cursor.executemany(insert_query, batch)
                connection.commit()
                total_inserted += len(batch)

                if verbose:
                    print(
                        f"  Inserted batch {i//batch_size + 1}: {len(batch)} facts (total: {total_inserted})"
                    )

            except Exception as e:
                connection.rollback()
                raise Exception(
                    f"Database insertion failed at batch {i//batch_size + 1}: {e}"
                ) from e

        if verbose:
            print(
                f"✓ Successfully inserted {total_inserted} facts with batch tag: {fact.get('BatchTag')}"  # type: ignore
            )

        return total_inserted

    except Exception as e:
        if connection:
            connection.rollback()
        error_msg = f"Database insertion failed: {e}"
        if verbose:
            print(f"✗ {error_msg}")
        raise Exception(error_msg) from e

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def main():
    """Example usage"""
    ticker = "FDX"
    cik = "1048911"

    try:
        result = process_multiple_missing_filings(
            ticker=ticker,
            cik=cik,
            min_year=2023,  # Recent years for testing
            verbose=True,
        )
        print(f"\nProcess completed: {result}")

    except Exception as e:
        print(f"Process failed: {e}")


if __name__ == "__main__":
    main()
