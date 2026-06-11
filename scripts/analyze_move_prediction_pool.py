#!/usr/bin/env python3
"""
Command-line script to analyze move prediction pool databases.

This script provides a simple interface to run pool analysis from bash/terminal
without needing to import modules in Python code.

Usage:
    # Analyze by pool ID pattern (searches in default location)
    python scripts/analyze_move_prediction_pool.py --pool-id "20260610_2000"

    # Analyze by explicit database path
    python scripts/analyze_move_prediction_pool.py --database-path "path/to/pool.duckdb"

    # With custom thresholds
    python scripts/analyze_move_prediction_pool.py \\
        --pool-id "20260610_2000" \\
        --min-score 70 \\
        --top-n-profiles 15 \\
        --top-n-stocks 300

    # Export only CSV and SQL (skip JSON)
    python scripts/analyze_move_prediction_pool.py \\
        --pool-id "20260610_2000" \\
        --no-json

Environment Variables:
    POOL_ANALYSIS_MIN_SCORE: Default minimum score threshold (default: 60.0)
    POOL_ANALYSIS_TOP_PROFILES: Default number of top profiles (default: 15)
    POOL_ANALYSIS_TOP_STOCKS: Default number of top stocks (default: 200)
"""

import os
import sys
from pathlib import Path

# Add src to Python path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from data_analysis_scripts.trading_view_move_prediction_pool_analyzer import (
    analyze_pool_database,
    export_analysis_reports,
    main as analyzer_main,
    run_pool_analysis_from_path,
)


def print_usage():
    """Print usage information."""
    print(__doc__)
    print("\nArguments:")
    print("  --database-path PATH     Path to pooled_move_prediction_runs.duckdb file")
    print("  --pool-id ID             Pool aggregation ID pattern to search for")
    print("  --output-dir DIR         Output directory for analysis reports")
    print("  --min-score VALUE        Minimum score threshold (default: 60.0)")
    print("  --top-n-profiles N       Number of top profiles to analyze (default: 15)")
    print("  --top-n-stocks N         Number of top stocks to rank (default: 200)")
    print("  --no-json                Skip JSON export")
    print("  --no-csv                 Skip CSV export")
    print("  --no-sql                 Skip SQL queries export")
    print("  --quiet                  Suppress progress output")
    print("  -h, --help               Show this help message")


def main():
    """Main entry point for bash/terminal usage."""
    # Get default values from environment or use defaults
    default_min_score = float(os.environ.get("POOL_ANALYSIS_MIN_SCORE", "60.0"))
    default_top_profiles = int(os.environ.get("POOL_ANALYSIS_TOP_PROFILES", "15"))
    default_top_stocks = int(os.environ.get("POOL_ANALYSIS_TOP_STOCKS", "200"))

    # Check for help
    if len(sys.argv) < 2 or "-h" in sys.argv or "--help" in sys.argv:
        print_usage()
        return 0

    # Build argument list for the analyzer's main function
    # Convert our simple args to the analyzer's expected format
    args = []

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]

        if arg in ("--database-path", "--pool-id", "--output-dir"):
            if i + 1 >= len(sys.argv):
                print(f"Error: {arg} requires a value", file=sys.stderr)
                return 1
            args.extend([arg, sys.argv[i + 1]])
            i += 2
        elif arg in ("--min-score", "--top-n-profiles", "--top-n-stocks"):
            if i + 1 >= len(sys.argv):
                print(f"Error: {arg} requires a value", file=sys.stderr)
                return 1
            args.extend([arg, sys.argv[i + 1]])
            i += 2
        elif arg in ("--no-json", "--no-csv", "--no-sql", "--quiet"):
            args.append(arg)
            i += 1
        else:
            print(f"Warning: Unknown argument {arg}", file=sys.stderr)
            i += 1

    # If no specific database or pool specified, try to find the most recent pool
    if "--database-path" not in args and "--pool-id" not in args:
        print("No --database-path or --pool-id specified.", file=sys.stderr)
        print("Searching for most recent pool database...", file=sys.stderr)

        # Search for recent pool databases
        pool_root = (
            PROJECT_ROOT
            / "logs"
            / "tradingview_analysis"
            / "prediction_analysis"
            / "multi_run_pool"
            / "runs"
        )

        if pool_root.exists():
            # Find all duckdb files sorted by modification time
            db_files = sorted(
                pool_root.rglob("pooled_move_prediction_runs.duckdb"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

            if db_files:
                most_recent = db_files[0]
                print(f"Found: {most_recent}", file=sys.stderr)
                args.extend(["--database-path", str(most_recent)])
            else:
                print("No pool databases found.", file=sys.stderr)
                print(f"Searched in: {pool_root}", file=sys.stderr)
                return 1
        else:
            print(f"Pool directory not found: {pool_root}", file=sys.stderr)
            return 1

    # Set defaults for optional arguments if not provided
    if "--min-score" not in " ".join(args):
        args.extend(["--min-score", str(default_min_score)])
    if "--top-n-profiles" not in " ".join(args):
        args.extend(["--top-n-profiles", str(default_top_profiles)])
    if "--top-n-stocks" not in " ".join(args):
        args.extend(["--top-n-stocks", str(default_top_stocks)])

    # Replace sys.argv and call the analyzer's main
    sys.argv = [sys.argv[0]] + args
    return analyzer_main()


if __name__ == "__main__":
    sys.exit(main())
