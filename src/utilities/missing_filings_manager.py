"""
Utility script for managing and monitoring missing SEC filings.
Provides tools for batch processing, monitoring progress, and generating reports.
"""

import sys
import os
import json
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple

# Add the src directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from db.connection_credentials import BASE_DB_CONFIG
from financial_execution_flows.missing_filings_handler import (
    MissingFilingsHandler,
    process_missing_filings_for_ticker,
)
from db.connection_provider import get_mysql_connection


class MissingFilingsManager:
    """Manager for batch processing and monitoring missing filings operations."""

    def __init__(self):
        self.handler = MissingFilingsHandler()
        self.save_dir = self.handler.save_dir

    def get_tickers_with_data(self) -> List[Any]:
        """Get all tickers that have data in the database with their CIKs."""
        query = """
        SELECT DISTINCT 
            e.Ticker,
            e.Cik,
            COUNT(DISTINCT e.Adsh) as UniqueFilings,
            MAX(e.FiscalYear) as LatestYear,
            MIN(e.FiscalYear) as EarliestYear,
            COUNT(DISTINCT e.Concept) as UniqueConcepts
        FROM edgar_financial_data_concepts e
        WHERE e.Ticker IS NOT NULL AND e.Ticker != ''
        GROUP BY e.Ticker, e.Cik
        HAVING UniqueFilings > 0
        ORDER BY UniqueFilings DESC, LatestYear DESC
        """

        conn = get_mysql_connection(**BASE_DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(query)
            results = cursor.fetchall()
            return results
        finally:
            cursor.close()
            conn.close()

    def get_ticker_cik_mapping(self) -> Dict[str, str]:
        """Get a mapping of tickers to CIKs from the database."""
        query = """
        SELECT DISTINCT Ticker, Cik
        FROM edgar_financial_data_concepts
        WHERE Ticker IS NOT NULL AND Ticker != ''
        """

        conn = get_mysql_connection(**BASE_DB_CONFIG)
        cursor = conn.cursor()

        try:
            cursor.execute(query)
            results = cursor.fetchall()
            # results is a list of tuples: [(ticker, cik), ...]
            ticker_cik_map: Dict[str, str] = {}
            for row in results:
                # Explicitly type cast to handle tuple access
                row_tuple = tuple(row) if not isinstance(row, tuple) else row
                ticker = str(row_tuple[0])
                cik = str(row_tuple[1])
                ticker_cik_map[ticker] = cik
            return ticker_cik_map
        finally:
            cursor.close()
            conn.close()

    def generate_missing_filings_report(self, ticker: Optional[str] = None) -> str:
        """Generate a comprehensive report of missing filings for one or all tickers."""
        if ticker:
            tickers_data = [
                row for row in self.get_tickers_with_data() if row["Ticker"] == ticker
            ]
        else:
            tickers_data = self.get_tickers_with_data()

        report = {
            "generated_at": datetime.now().isoformat(),
            "total_tickers": len(tickers_data),
            "summary": {},
            "tickers": [],
        }

        total_missing = 0

        for ticker_info in tickers_data:
            ticker_symbol = ticker_info["Ticker"]
            cik = str(ticker_info["Cik"])

            print(f"Analyzing {ticker_symbol} (CIK: {cik})...")

            try:
                missing_filings = self.handler.identify_missing_filings(
                    ticker_symbol, cik, ["10-K", "10-Q"]
                )

                ticker_report = {
                    "ticker": ticker_symbol,
                    "cik": cik,
                    "database_stats": ticker_info,
                    "missing_filings_count": len(missing_filings),
                    "missing_filings": missing_filings[
                        :10
                    ],  # Limit to first 10 for brevity
                    "analysis_timestamp": datetime.now().isoformat(),
                }

                report["tickers"].append(ticker_report)
                total_missing += len(missing_filings)

            except Exception as e:
                print(f"Error analyzing {ticker_symbol}: {e}")
                ticker_report = {
                    "ticker": ticker_symbol,
                    "cik": cik,
                    "error": str(e),
                    "analysis_timestamp": datetime.now().isoformat(),
                }
                report["tickers"].append(ticker_report)

        report["summary"]["total_missing_filings"] = total_missing
        report["summary"]["tickers_with_missing"] = sum(
            1 for t in report["tickers"] if t.get("missing_filings_count", 0) > 0
        )

        # Save report
        filename = f"missing_filings_analysis_{ticker if ticker else 'all'}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        report_path = os.path.join(self.save_dir, filename)

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

        print(f"Missing filings analysis saved to: {report_path}")
        return report_path

    def batch_process_missing_filings(
        self,
        tickers: Optional[List[str]] = None,
        max_filings_per_ticker: int = 3,
        concept_tags: Optional[List[str]] = None,
    ) -> Dict:
        """Process missing filings for multiple tickers in batch."""
        if concept_tags is None:
            concept_tags = [
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "Revenue",
                "SalesRevenueNet",
                "TotalRevenues",
            ]

        if tickers is None:
            # Get top 10 tickers by filing count
            tickers_data = self.get_tickers_with_data()[:10]
            tickers = [t["Ticker"] for t in tickers_data]

        ticker_cik_map = self.get_ticker_cik_mapping()

        batch_results = {
            "batch_started": datetime.now().isoformat(),
            "tickers_processed": 0,
            "total_filings_processed": 0,
            "total_concepts_extracted": 0,
            "results": [],
            "errors": [],
        }

        for i, ticker in enumerate(tickers):
            if ticker not in ticker_cik_map:
                error_msg = f"CIK not found for ticker {ticker}"
                print(error_msg)
                batch_results["errors"].append(error_msg)
                continue

            cik = ticker_cik_map[ticker]
            print(f"\nProcessing {i+1}/{len(tickers)}: {ticker} (CIK: {cik})")
            print("-" * 50)

            try:
                results = process_missing_filings_for_ticker(
                    ticker=ticker,
                    cik=cik,
                    concept_tags=concept_tags,
                    max_filings=max_filings_per_ticker,
                    form_types=["10-K", "10-Q"],
                )

                batch_results["results"].append(results)
                batch_results["tickers_processed"] += 1
                batch_results["total_filings_processed"] += results.get(
                    "filings_processed", 0
                )
                batch_results["total_concepts_extracted"] += results.get(
                    "concepts_extracted", 0
                )

                print(
                    f"Completed {ticker}: {results.get('filings_processed', 0)} filings, {results.get('concepts_extracted', 0)} concepts"
                )

            except Exception as e:
                error_msg = f"Error processing {ticker}: {e}"
                print(error_msg)
                batch_results["errors"].append(error_msg)

        batch_results["batch_completed"] = datetime.now().isoformat()

        # Save batch results
        filename = (
            f"batch_missing_filings_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        )
        results_path = os.path.join(self.save_dir, filename)

        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(batch_results, f, indent=2, default=str)

        print(f"\nBatch results saved to: {results_path}")
        return batch_results

    def monitor_recent_activity(self, days: int = 7) -> Dict:
        """Monitor recent missing filings processing activity."""
        cutoff_date = datetime.now() - timedelta(days=days)

        query = """
        SELECT 
            BatchTag,
            COUNT(*) as RecordsCount,
            COUNT(DISTINCT Ticker) as UniqueTickersCount,
            COUNT(DISTINCT Concept) as UniqueConceptsCount,
            MIN(Ddate) as EarliestDate,
            MAX(Ddate) as LatestDate,
            GROUP_CONCAT(DISTINCT Ticker ORDER BY Ticker) as Tickers
        FROM edgar_financial_data_concepts
        WHERE BatchTag LIKE 'api_%'
        GROUP BY BatchTag
        ORDER BY MAX(Ddate) DESC
        """

        conn = get_mysql_connection(**BASE_DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(query)
            results = cursor.fetchall()

            # Filter recent results
            recent_results = []
            for result in results:
                try:
                    # result is a dictionary when using cursor(dictionary=True)
                    batch_tag = (
                        result.get("BatchTag", "") if isinstance(result, dict) else ""
                    )
                    batch_tag_str = str(batch_tag) if batch_tag else ""
                    if batch_tag_str and "api_" in batch_tag_str:
                        recent_results.append(result)
                except Exception:
                    pass

            return {
                "monitoring_period_days": days,
                "monitoring_timestamp": datetime.now().isoformat(),
                "recent_api_batches": recent_results[:10],  # Show last 10 batches
                "total_recent_batches": len(recent_results),
            }

        finally:
            cursor.close()
            conn.close()


def main():
    """Main CLI interface for the missing filings manager."""
    parser = argparse.ArgumentParser(
        description="Manage missing SEC filings processing"
    )
    parser.add_argument(
        "command",
        choices=["analyze", "process", "batch", "monitor", "test"],
        help="Command to execute",
    )

    parser.add_argument("--ticker", help="Specific ticker to process")
    parser.add_argument(
        "--max-filings",
        type=int,
        default=3,
        help="Maximum filings to process per ticker",
    )
    parser.add_argument(
        "--tickers", nargs="+", help="List of tickers for batch processing"
    )
    parser.add_argument(
        "--days", type=int, default=7, help="Days to look back for monitoring"
    )

    args = parser.parse_args()

    manager = MissingFilingsManager()

    if args.command == "analyze":
        print("Generating missing filings analysis...")
        report_path = manager.generate_missing_filings_report(args.ticker)
        print(f"Analysis complete. Report saved to: {report_path}")

    elif args.command == "process":
        if not args.ticker:
            print("Error: --ticker is required for process command")
            return

        # Get CIK for ticker
        ticker_map = manager.get_ticker_cik_mapping()
        if args.ticker not in ticker_map:
            print(f"Error: CIK not found for ticker {args.ticker}")
            return

        cik = ticker_map[args.ticker]
        print(f"Processing missing filings for {args.ticker} (CIK: {cik})")

        results = process_missing_filings_for_ticker(
            ticker=args.ticker, cik=cik, max_filings=args.max_filings
        )

        print("\nProcessing Results:")
        print(json.dumps(results, indent=2, default=str))

    elif args.command == "batch":
        print("Starting batch processing of missing filings...")
        results = manager.batch_process_missing_filings(
            tickers=args.tickers, max_filings_per_ticker=args.max_filings
        )

        print(f"\nBatch Processing Summary:")
        print(f"Tickers processed: {results['tickers_processed']}")
        print(f"Total filings processed: {results['total_filings_processed']}")
        print(f"Total concepts extracted: {results['total_concepts_extracted']}")
        print(f"Errors: {len(results['errors'])}")

    elif args.command == "monitor":
        print(f"Monitoring recent activity (last {args.days} days)...")
        activity = manager.monitor_recent_activity(args.days)
        print(json.dumps(activity, indent=2, default=str))

    elif args.command == "test":
        print("Running basic functionality test...")
        tickers_data = manager.get_tickers_with_data()
        print(f"Found {len(tickers_data)} tickers with data in database")

        if tickers_data:
            sample_ticker = tickers_data[0]
            print(f"Sample ticker: {sample_ticker}")

            # Test missing filings identification
            missing = manager.handler.identify_missing_filings(
                sample_ticker["Ticker"], str(sample_ticker["Cik"]), ["10-Q"]
            )
            print(f"Missing filings for {sample_ticker['Ticker']}: {len(missing)}")


if __name__ == "__main__":
    main()
