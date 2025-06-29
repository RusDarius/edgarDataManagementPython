"""
Module for identifying and fetching missing SEC filings from the database.
Handles comparison between database records and SEC API submissions to fill gaps.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Set, Any
from data_loaders.api_client import ApiClient
from data_loaders.sec_api_loader import (
    fetch_and_parse_submission,
    fetch_and_parse_submission_enhanced,
)
from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection


class MissingFilingsHandler:
    """Handles identification and fetching of missing SEC filings."""

    def __init__(
        self, user_agent: str = "Corporate Finance Research project@email.com"
    ):
        """Initialize the handler with API client and database connection."""
        self.api_client = ApiClient(user_agent)
        self.save_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "savedData"
        )
        os.makedirs(self.save_dir, exist_ok=True)

    def get_database_filings(self, ticker: str) -> List[Any]:
        """
        Get all filings for a ticker from the database with their fiscal periods and latest entries.
        Returns a list of dictionaries with filing information.
        """
        query = """
        WITH LatestEntries AS (
            SELECT FiscalYear,
                FiscalPeriod,
                FilingType,
                Ddate,
                PeriodEnd,
                BatchTag,
                Adsh,
                Cik,
                ROW_NUMBER() OVER (
                    PARTITION BY FiscalYear,
                    FiscalPeriod,
                    FilingType
                    ORDER BY Ddate DESC,
                        BatchTag DESC
                ) as rn
            FROM edgar_financial_data_concepts
            WHERE Ticker = %s
        )
        SELECT FiscalYear,
            FiscalPeriod,
            FilingType,
            Ddate AS LatestDdate,
            PeriodEnd,
            BatchTag AS LatestBatchTag,
            Adsh AS LatestAccessionNumber,
            Cik
        FROM LatestEntries
        WHERE rn = 1
        ORDER BY FiscalYear DESC,
            CASE
                WHEN FiscalPeriod = 'FY' THEN 5
                WHEN FiscalPeriod = 'Q4' THEN 4
                WHEN FiscalPeriod = 'Q3' THEN 3
                WHEN FiscalPeriod = 'Q2' THEN 2
                WHEN FiscalPeriod = 'Q1' THEN 1
                ELSE 0
            END DESC
        """

        conn = get_mysql_connection(**BASE_DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(query, (ticker,))
            results = cursor.fetchall()
            return results
        finally:
            cursor.close()
            conn.close()

    def get_sec_submissions(self, cik: str) -> Dict:
        """
        Fetch all SEC submissions for a CIK from the SEC API.
        Returns the full submissions data structure.
        """
        print(f"Fetching SEC submissions for CIK: {cik}")
        return self.api_client.fetch_company_submissions(cik)

    def parse_sec_submissions(self, submissions_data: Dict) -> List[Dict]:
        """
        Parse SEC submissions data into a standardized format.
        Returns a list of submission dictionaries with relevant fields.
        """
        filings = submissions_data["filings"]["recent"]
        fiscal_year_end = submissions_data.get("fiscalYearEnd", "1231")

        parsed_submissions = []

        for i in range(len(filings["form"])):
            submission = {
                "form": filings["form"][i],
                "accession_number": filings["accessionNumber"][i],
                "filing_date": filings["filingDate"][i],
                "report_date": filings.get("reportDate", [None] * len(filings["form"]))[
                    i
                ],
                "period_of_report": filings.get(
                    "periodOfReport", [None] * len(filings["form"])
                )[i],
                "primary_document": filings["primaryDocument"][i],
                "fiscal_year_end": fiscal_year_end,
            }
            parsed_submissions.append(submission)

        return parsed_submissions

    def infer_fiscal_period(
        self, period_date: str, fiscal_year_end: str
    ) -> Tuple[Optional[str], Optional[int]]:
        """
        Infer fiscal period and year from a period date and fiscal year end.
        Returns (fiscal_period, fiscal_year) tuple.
        """
        if not period_date or len(period_date) < 10:
            return None, None

        try:
            dt = datetime.strptime(period_date, "%Y-%m-%d")
            fy_end_month = int(fiscal_year_end[:2])

            # Determine fiscal year
            if dt.month <= fy_end_month:
                fiscal_year = dt.year
            else:
                fiscal_year = dt.year + 1

            # Determine fiscal period based on month relative to fiscal year end
            month_diff = (dt.month - fy_end_month) % 12

            if month_diff == 0:  # Fiscal year end month
                fiscal_period = "FY"
            elif 1 <= month_diff <= 3:
                fiscal_period = "Q1"
            elif 4 <= month_diff <= 6:
                fiscal_period = "Q2"
            elif 7 <= month_diff <= 9:
                fiscal_period = "Q3"
            elif 10 <= month_diff <= 12:
                fiscal_period = "Q4"
            else:
                fiscal_period = None

            return fiscal_period, fiscal_year

        except Exception as e:
            print(f"Error parsing date {period_date}: {e}")
            return None, None

    def identify_missing_filings(
        self, ticker: str, cik: str, form_types: Optional[List[str]] = None
    ) -> List[Dict]:
        """
        Identify missing filings by comparing database records with SEC API submissions.
        Returns a list of missing filing dictionaries with URLs for fetching.
        """
        if form_types is None:
            form_types = ["10-K", "10-Q", "8-K"]

        print(f"Identifying missing filings for {ticker} (CIK: {cik})")

        # Get database filings
        db_filings = self.get_database_filings(ticker)
        print(f"Found {len(db_filings)} filings in database")

        # Create set of existing accession numbers
        existing_accessions = {filing["LatestAccessionNumber"] for filing in db_filings}
        print(f"Existing accession numbers: {len(existing_accessions)}")

        # Get SEC submissions
        sec_data = self.get_sec_submissions(cik)
        sec_submissions = self.parse_sec_submissions(sec_data)

        # Filter by form types
        relevant_submissions = [
            sub for sub in sec_submissions if sub["form"] in form_types
        ]
        print(f"Found {len(relevant_submissions)} relevant SEC submissions")

        # Identify missing submissions
        missing_filings = []

        for submission in relevant_submissions:
            accession = submission["accession_number"]

            # Skip if we already have this filing
            if accession in existing_accessions:
                continue

            # Infer fiscal period and year
            period_date = submission["period_of_report"] or submission["report_date"]
            fiscal_period, fiscal_year = self.infer_fiscal_period(
                period_date, submission["fiscal_year_end"]
            )

            # Create URL for fetching
            accession_nodash = accession.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{submission['primary_document']}"

            missing_filing = {
                "ticker": ticker,
                "cik": cik,
                "form": submission["form"],
                "accession_number": accession,
                "filing_date": submission["filing_date"],
                "report_date": submission["report_date"],
                "period_of_report": period_date,
                "fiscal_period": fiscal_period,
                "fiscal_year": fiscal_year,
                "primary_document": submission["primary_document"],
                "url": url,
                "fiscal_year_end": submission["fiscal_year_end"],
            }

            missing_filings.append(missing_filing)

        print(f"Identified {len(missing_filings)} missing filings")
        return missing_filings

    def save_missing_filings_report(
        self, missing_filings: List[Dict], ticker: str
    ) -> str:
        """
        Save a report of missing filings to disk for review.
        Returns the path to the saved report.
        """
        report_path = os.path.join(
            self.save_dir, f"{ticker}_missing_filings_report.json"
        )

        report = {
            "ticker": ticker,
            "generated_at": datetime.now().isoformat(),
            "total_missing": len(missing_filings),
            "missing_filings": missing_filings,
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

        print(f"Saved missing filings report to: {report_path}")
        return report_path

    def extract_concepts_from_filing(
        self, filing_info: Dict, concept_tags: List[str]
    ) -> Dict:
        """
        Extract specific XBRL concept values from a filing using the SEC API.
        Returns a dictionary mapping concept tags to their values and metadata.
        """
        results = {}

        for concept_tag in concept_tags:
            print(
                f"Extracting {concept_tag} from {filing_info['form']} {filing_info['fiscal_period']} {filing_info['fiscal_year']}"
            )

            # Use enhanced extraction with period validation
            result = fetch_and_parse_submission_enhanced(
                filing_info["cik"],
                filing_info["form"],
                filing_info["fiscal_period"] or "Q1",  # Default fallback
                filing_info["fiscal_year"] or 2024,  # Default fallback
                concept_tag,
                filing_info.get(
                    "period_of_report"
                ),  # Expected period date for validation
            )

            if result.get("fact_value"):
                # Calculate actual value considering decimals
                try:
                    value = float(result["fact_value"])
                    decimals = int(result.get("fact_decimals", 0))
                    actual_value = (
                        value * (10 ** abs(decimals)) if decimals < 0 else value
                    )

                    results[concept_tag] = {
                        "raw_value": result["fact_value"],
                        "actual_value": actual_value,
                        "unit": result.get("fact_unit"),
                        "context": result.get("fact_context"),
                        "decimals": result.get("fact_decimals"),
                        "period_start": result.get("fact_period_start"),
                        "period_end": result.get("fact_period_end"),
                        "period_validated": result.get("period_validation", False),
                        "all_contexts": result.get("all_contexts_found", []),
                        "error": None,
                    }

                    # Print period validation info
                    period_status = (
                        "VALIDATED"
                        if result.get("period_validation")
                        else "NOT VALIDATED"
                    )
                    print(
                        f"Period validation: {period_status} (period: {result.get('fact_period_end')})"
                    )

                except Exception as e:
                    results[concept_tag] = {
                        "raw_value": result["fact_value"],
                        "actual_value": None,
                        "unit": result.get("fact_unit"),
                        "context": result.get("fact_context"),
                        "decimals": result.get("fact_decimals"),
                        "period_start": result.get("fact_period_start"),
                        "period_end": result.get("fact_period_end"),
                        "period_validated": result.get("period_validation", False),
                        "all_contexts": result.get("all_contexts_found", []),
                        "error": f"Value calculation error: {e}",
                    }
            else:
                # If enhanced extraction failed, try ADSH-based extraction as fallback
                print(
                    f"Enhanced extraction failed for {concept_tag}, trying ADSH-based extraction..."
                )

                try:
                    from data_loaders.sec_api_loader import (
                        fetch_and_parse_submission_by_adsh,
                    )

                    adsh_result = fetch_and_parse_submission_by_adsh(
                        cik=filing_info["cik"],
                        adsh=filing_info["accession_number"],
                        fact_tag=concept_tag,
                        primary_document=filing_info.get("primary_document"),
                    )

                    if adsh_result.get("best_fact"):
                        best_fact = adsh_result["best_fact"]
                        actual_value = best_fact.get("actual_value") or best_fact.get(
                            "numeric_value"
                        )

                        if actual_value is not None:
                            results[concept_tag] = {
                                "raw_value": best_fact.get("value"),
                                "actual_value": actual_value,
                                "unit": best_fact.get("unit_ref"),
                                "context": best_fact.get("context_ref"),
                                "decimals": best_fact.get("decimals"),
                                "period_start": best_fact.get("period_start"),
                                "period_end": best_fact.get("period_end"),
                                "period_validated": False,  # ADSH method doesn't validate specific periods
                                "all_contexts": (
                                    [best_fact.get("context_ref")]
                                    if best_fact.get("context_ref")
                                    else []
                                ),
                                "error": None,
                                "extraction_method": "adsh_fallback",
                            }
                            print(
                                f"✓ ADSH fallback successful: {actual_value} {best_fact.get('unit_ref', '')} (period: {best_fact.get('period_end', 'N/A')})"
                            )
                            continue  # Move to next concept

                except Exception as e:
                    print(f"ADSH fallback also failed: {e}")

                # If both methods failed, record the failure
                results[concept_tag] = {
                    "raw_value": None,
                    "actual_value": None,
                    "unit": None,
                    "context": None,
                    "decimals": None,
                    "period_start": None,
                    "period_end": None,
                    "period_validated": False,
                    "all_contexts": result.get("all_contexts_found", []),
                    "error": result.get("error", "Concept not found"),
                }

        return results

    def insert_missing_filing_data(
        self, filing_info: Dict, concept_data: Dict, batch_tag: Optional[str] = None
    ):
        """
        Insert extracted concept data from a missing filing into the database.
        Handles the integration with the edgar_financial_data_concepts table.
        """
        if not batch_tag:
            batch_tag = f"api_fetch_{datetime.now().strftime('%Y%m%d')}"

        conn = get_mysql_connection(**BASE_DB_CONFIG)
        cursor = conn.cursor()

        insert_query = """
        INSERT INTO edgar_financial_data_concepts (
            Cik, Ticker, FilingType, FiscalPeriod, FiscalYear, Concept,
            Value, ValueString, Unit, DataType, Adsh, PeriodEnd, Ddate,
            Segment, Qtrs, BatchTag
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """

        try:
            # Parse dates
            period_end = None
            ddate = None

            if filing_info.get("period_of_report"):
                try:
                    period_end = datetime.strptime(
                        filing_info["period_of_report"], "%Y-%m-%d"
                    ).date()
                    ddate = period_end  # Use period end as ddate
                except:
                    pass

            if not ddate and filing_info.get("filing_date"):
                try:
                    ddate = datetime.strptime(
                        filing_info["filing_date"], "%Y-%m-%d"
                    ).date()
                except:
                    pass

            # Determine qtrs value
            qtrs = 1 if filing_info.get("fiscal_period", "").startswith("Q") else 4

            # Insert each concept
            for concept_tag, concept_info in concept_data.items():
                if concept_info.get("actual_value") is not None:
                    values = (
                        int(filing_info["cik"]),
                        filing_info["ticker"],
                        filing_info["form"],
                        filing_info.get("fiscal_period", ""),
                        filing_info.get("fiscal_year", 0),
                        concept_tag,
                        concept_info["actual_value"],
                        str(concept_info["raw_value"]),
                        concept_info.get("unit", ""),
                        "num",  # Default data type
                        filing_info["accession_number"],
                        period_end,
                        ddate,
                        concept_info.get("context", ""),
                        qtrs,
                        batch_tag,
                    )

                    cursor.execute(insert_query, values)
                    print(
                        f"Inserted {concept_tag} = {concept_info['actual_value']} for {filing_info['ticker']}"
                    )

            conn.commit()
            print(
                f"Successfully inserted data for filing {filing_info['accession_number']}"
            )

        except Exception as e:
            conn.rollback()
            print(f"Error inserting filing data: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def process_missing_filings(
        self,
        ticker: str,
        cik: str,
        concept_tags: List[str],
        max_filings: int = 10,
        form_types: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> Dict:
        """
        Complete workflow to identify, fetch, and integrate missing filings.

        Args:
            ticker: Company ticker symbol
            cik: Company CIK
            concept_tags: List of XBRL concept tags to extract
            max_filings: Maximum number of missing filings to process
            form_types: List of form types to process (default: 10-K, 10-Q, 8-K)
            dry_run: If True, extract concepts but don't insert into database

        Returns:
            Dictionary with processing results and statistics
        """
        results = {
            "ticker": ticker,
            "cik": cik,
            "processed_at": datetime.now().isoformat(),
            "missing_filings_found": 0,
            "filings_processed": 0,
            "concepts_extracted": 0,
            "errors": [],
            "dry_run": dry_run,
            "extracted_data": [],  # Store extracted data for dry run
        }

        try:
            # Identify missing filings
            missing_filings = self.identify_missing_filings(ticker, cik, form_types)
            results["missing_filings_found"] = len(missing_filings)

            if not missing_filings:
                print("No missing filings found.")
                return results

            # Save report
            report_path = self.save_missing_filings_report(missing_filings, ticker)
            results["report_path"] = report_path

            # Process up to max_filings
            filings_to_process = missing_filings[:max_filings]
            batch_tag = f"api_missing_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M')}"

            for i, filing_info in enumerate(filings_to_process):
                print(
                    f"\nProcessing filing {i+1}/{len(filings_to_process)}: {filing_info['accession_number']}"
                )

                try:
                    # Skip filings without proper fiscal period/year
                    if not filing_info.get("fiscal_period") or not filing_info.get(
                        "fiscal_year"
                    ):
                        print(
                            f"Skipping filing - missing fiscal period/year information"
                        )
                        results["errors"].append(
                            f"Skipped {filing_info['accession_number']} - missing fiscal info"
                        )
                        continue

                    # Extract concepts with enhanced error handling
                    try:
                        concept_data = self.extract_concepts_from_filing(
                            filing_info, concept_tags
                        )
                    except UnicodeEncodeError as e:
                        error_msg = f"Unicode encoding error processing filing {filing_info['accession_number']}: {str(e).replace(chr(0x26a0), 'WARNING').replace(chr(0x2713), 'OK')}"
                        print(error_msg)
                        results["errors"].append(error_msg)
                        continue
                    except Exception as e:
                        error_msg = f"Unexpected error extracting concepts from {filing_info['accession_number']}: {e}"
                        print(error_msg)
                        results["errors"].append(error_msg)
                        continue

                    # Count successful extractions
                    successful_concepts = sum(
                        1
                        for data in concept_data.values()
                        if data.get("actual_value") is not None
                    )
                    results["concepts_extracted"] += successful_concepts

                    if successful_concepts > 0:
                        results["filings_processed"] += 1

                        # Store extracted data for review
                        extracted_info = {
                            "filing_info": filing_info,
                            "concept_data": concept_data,
                            "successful_concepts": successful_concepts,
                        }
                        results["extracted_data"].append(extracted_info)

                        if dry_run:
                            print(
                                f"DRY RUN MODE: Would insert {successful_concepts} concepts for filing {filing_info['accession_number']}"
                            )
                            print("Extracted concept data:")
                            for concept_tag, concept_info in concept_data.items():
                                if concept_info.get("actual_value") is not None:
                                    print(
                                        f"  - {concept_tag}: {concept_info['actual_value']} {concept_info.get('unit', 'N/A')}"
                                    )
                        else:
                            # Insert into database only if not dry run
                            self.insert_missing_filing_data(
                                filing_info, concept_data, batch_tag
                            )
                    else:
                        print(
                            f"No concepts successfully extracted from filing {filing_info['accession_number']}"
                        )
                        results["errors"].append(
                            f"No concepts extracted from {filing_info['accession_number']}"
                        )

                except Exception as e:
                    error_msg = f"Error processing filing {filing_info['accession_number']}: {e}"
                    print(error_msg)
                    results["errors"].append(error_msg)

            print(f"\nCompleted processing missing filings for {ticker}")
            print(
                f"Processed: {results['filings_processed']}/{results['missing_filings_found']} filings"
            )
            print(f"Extracted: {results['concepts_extracted']} concept values")

        except Exception as e:
            error_msg = f"Error in missing filings workflow: {e}"
            print(error_msg)
            results["errors"].append(error_msg)

        return results


def process_missing_filings_for_ticker(
    ticker: str,
    cik: str,
    concept_tags: Optional[List[str]] = None,
    max_filings: int = 5,
    form_types: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict:
    """
    Convenience function to process missing filings for a single ticker.

    Args:
        ticker: Company ticker symbol
        cik: Company CIK
        concept_tags: List of XBRL concept tags to extract (default: common revenue tags)
        max_filings: Maximum number of missing filings to process
        form_types: List of form types to process (default: 10-K, 10-Q)
        dry_run: If True, extract concepts but don't insert into database

    Returns:
        Dictionary with processing results
    """
    if concept_tags is None:
        concept_tags = [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "Revenue",
            "SalesRevenueNet",
            "TotalRevenues",
        ]

    if form_types is None:
        form_types = ["10-K", "10-Q"]

    handler = MissingFilingsHandler()
    return handler.process_missing_filings(
        ticker, cik, concept_tags, max_filings, form_types, dry_run
    )
