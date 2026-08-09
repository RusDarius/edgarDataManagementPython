"""
Financial projection entrypoint (sibling of main.py).

Flow (short):
  1) Ensure an all-fields DuckDB exists
     (main.py: export_all_tradingview_fields_duckdb() OR pass --all-fields-db).
  2) Optionally restrict to names (CLI --symbols, or names from a move-prediction
     DuckDB produced by run_full_analysis_suite_duckdb).
  3) Run bear/base/bull 5y EV/Revenue projections; peers still use the full
     eligible scan universe for relative scales.

Main-callable presets (market-cap filterable):
  - run_financial_projection_from_latest_all_fields
  - run_financial_projection_from_latest_prediction_analysis
  - run_financial_projection_from_all_fields_day  (e.g. day_label='01_07_2026')

Usage doc: documentation/financial_projection_usage.md
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence

from financial_projection import (
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SCENARIO_CONFIG_PATH,
    run_financial_projection_suite,
)
from financial_projection.config import (
    DEFAULT_MIN_MARKET_CAP_USD,
    PROJECT_ROOT,
)
from financial_projection.load_all_fields import (
    discover_latest_all_fields_db,
    discover_latest_prediction_db,
    resolve_all_fields_database,
    resolve_all_fields_day_database,
)
from financial_projection.sources import resolve_symbols_argument

# ---------------------------------------------------------------------------
# Main-callable presets — import these from main.py
# ---------------------------------------------------------------------------


def run_financial_projection_from_latest_all_fields(
    *,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    all_fields_db: str | Path | None = None,
    symbols: Sequence[str] | None = None,
    top_n: int = 25,
    scenario_config_path: str | Path | None = None,
    output_root: str | Path | None = None,
    export_parquet: bool = True,
    print_console: bool = True,
) -> dict[str, Any]:
    """
    Project from the latest all-fields scan (or an explicit day DB).

    Market-cap filter is applied on all-fields ``market_cap_basic`` during load.
    Peer scales use the full eligible universe after that filter.

    main.py:
        run_financial_projection_from_latest_all_fields(min_market_cap_usd=500_000_000)
    """
    database_path = (
        Path(all_fields_db)
        if all_fields_db is not None
        else discover_latest_all_fields_db()
    )
    if database_path is None:
        raise FileNotFoundError(
            "No tradingview_all_fields_*.duckdb found. "
            "Run export_all_tradingview_fields_duckdb() first."
        )
    database_path = resolve_all_fields_database(all_fields_db=database_path)

    result = run_financial_projection_suite(
        all_fields_db=database_path,
        scenario_config_path=scenario_config_path,
        output_root=output_root,
        min_market_cap_usd=min_market_cap_usd,
        symbols=symbols,
        top_n=top_n,
        export_parquet=export_parquet,
        print_console=print_console,
    )
    result["source_mode"] = "latest_all_fields"
    result["min_market_cap_usd"] = float(min_market_cap_usd)
    return result


def run_financial_projection_from_all_fields_day(
    day_label: str,
    *,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    symbols: Sequence[str] | None = None,
    top_n: int = 25,
    scenario_config_path: str | Path | None = None,
    output_root: str | Path | None = None,
    export_parquet: bool = True,
    print_console: bool = True,
) -> dict[str, Any]:
    """
    Project from a dated all-fields day folder (latest run inside that day's DuckDB).

    Example day folder:
      logs/tradingview_analysis/trading_view_all_fields_data/01_07_2026/
        tradingview_all_fields_01_07_2026.duckdb

    main.py:
        run_financial_projection_from_all_fields_day(
            "01_07_2026",
            min_market_cap_usd=500_000_000,
        )
    """
    database_path = resolve_all_fields_day_database(day_label)
    result = run_financial_projection_from_latest_all_fields(
        min_market_cap_usd=min_market_cap_usd,
        all_fields_db=database_path,
        symbols=symbols,
        top_n=top_n,
        scenario_config_path=scenario_config_path,
        output_root=output_root,
        export_parquet=export_parquet,
        print_console=print_console,
    )
    result["source_mode"] = "all_fields_day"
    result["day_label"] = str(day_label).strip()
    result["all_fields_database"] = database_path.as_posix()
    return result


def run_financial_projection_from_latest_prediction_analysis(
    *,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    prediction_database: str | Path | None = None,
    prediction_run_id: str | None = None,
    prediction_top_n: int | None = None,
    prediction_min_score: float | None = None,
    all_fields_db: str | Path | None = None,
    extra_symbols: Sequence[str] | None = None,
    top_n: int = 25,
    scenario_config_path: str | Path | None = None,
    output_root: str | Path | None = None,
    export_parquet: bool = True,
    print_console: bool = True,
) -> dict[str, Any]:
    """
    Project names from the latest move-prediction DuckDB run.

    - Symbol shortlist: latest ``move_prediction_*.duckdb`` (ISO week layout),
      or an explicit ``prediction_database`` (e.g. base_duckdb_result['_duckdb_database']).
    - ``prediction_top_n=None`` (default) = **all** distinct symbols in that run.
      Pass an int (e.g. 40) to keep only the top-N by score.
    - Fundamentals + short-term outlook: always from the **latest all-fields**
      scan (or ``all_fields_db``) — forecast/EPS/price-target fields are not
      taken from the prediction DB.
    - Market-cap filter: all-fields ``market_cap_basic``.

    main.py (all prediction symbols):
        run_financial_projection_from_latest_prediction_analysis(
            min_market_cap_usd=500_000_000,
        )

    main.py (top 40 only):
        run_financial_projection_from_latest_prediction_analysis(
            min_market_cap_usd=500_000_000,
            prediction_top_n=40,
        )
    """
    resolved_prediction = (
        Path(prediction_database)
        if prediction_database is not None
        else discover_latest_prediction_db()
    )
    if resolved_prediction is None or not Path(resolved_prediction).exists():
        raise FileNotFoundError(
            "No move_prediction_*.duckdb found under "
            "logs/tradingview_analysis/prediction_analysis/duckdb_runs. "
            "Run run_full_analysis_suite_duckdb() first, or pass prediction_database=."
        )

    symbols = resolve_symbols_argument(
        symbols=extra_symbols,
        prediction_database=resolved_prediction,
        prediction_run_id=prediction_run_id,
        prediction_top_n=prediction_top_n,
        prediction_min_score=prediction_min_score,
    )
    if not symbols:
        raise ValueError(
            "No symbols found in prediction database: "
            f"{Path(resolved_prediction).as_posix()}"
        )

    result = run_financial_projection_from_latest_all_fields(
        min_market_cap_usd=min_market_cap_usd,
        all_fields_db=all_fields_db,
        symbols=symbols,
        top_n=top_n,
        scenario_config_path=scenario_config_path,
        output_root=output_root,
        export_parquet=export_parquet,
        print_console=print_console,
    )
    result["source_mode"] = "latest_prediction_analysis"
    result["prediction_database"] = Path(resolved_prediction).as_posix()
    result["prediction_symbol_count"] = len(symbols)
    result["prediction_top_n"] = prediction_top_n
    result["prediction_min_score"] = prediction_min_score
    return result


# ---------------------------------------------------------------------------
# Example helpers — also usable from this entrypoint / CLI
# ---------------------------------------------------------------------------


def example_full_universe(
    top_n: int = 25,
    *,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
) -> dict[str, Any]:
    """Project the full eligible all-fields universe (latest day DB)."""
    return run_financial_projection_from_latest_all_fields(
        min_market_cap_usd=min_market_cap_usd,
        top_n=top_n,
    )


def example_named_symbols(
    symbols: Sequence[str] | None = None,
    *,
    all_fields_db: str | Path | None = None,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    top_n: int = 25,
) -> dict[str, Any]:
    """
    Project a short watchlist.

    Symbols must be EXCHANGE:TICKER labels (same as all-fields / move-prediction).
    Peer scales still come from the full eligible universe.
    """
    resolved = (
        list(symbols)
        if symbols is not None
        else [
            "NASDAQ:AAPL",
            "NASDAQ:MSFT",
            "NYSE:CRM",
        ]
    )
    return run_financial_projection_from_latest_all_fields(
        min_market_cap_usd=min_market_cap_usd,
        all_fields_db=all_fields_db,
        symbols=resolved,
        top_n=top_n,
    )


def example_from_all_fields_export_result(
    export_result: dict[str, Any],
    *,
    symbols: Sequence[str] | None = None,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    top_n: int = 25,
) -> dict[str, Any]:
    """
    Chain after main.py-style all-fields export.

    export_all_tradingview_fields_duckdb() returns keys like
    ``_duckdb_database`` — pass that path here (or use --all-fields-db).
    """
    database_path = export_result.get("_duckdb_database") or export_result.get(
        "database_path"
    )
    if not database_path:
        raise ValueError(
            "export_result missing '_duckdb_database' / 'database_path'. "
            "Run export_all_tradingview_fields_duckdb() first."
        )
    return run_financial_projection_from_latest_all_fields(
        min_market_cap_usd=min_market_cap_usd,
        all_fields_db=Path(database_path),
        symbols=symbols,
        top_n=top_n,
    )


def example_from_prediction_analysis(
    prediction_database: str | Path,
    *,
    all_fields_db: str | Path | None = None,
    prediction_run_id: str | None = None,
    prediction_top_n: int | None = None,
    prediction_min_score: float | None = None,
    extra_symbols: Sequence[str] | None = None,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    top_n: int = 25,
) -> dict[str, Any]:
    """
    Integrate with an explicit move-prediction DuckDB path.

    ``prediction_top_n=None`` (default) projects all distinct symbols in the run.

    Typical main.py flow after run_full_analysis_suite_duckdb(...):
      example_from_prediction_analysis(base_duckdb_result['_duckdb_database'])
    """
    return run_financial_projection_from_latest_prediction_analysis(
        min_market_cap_usd=min_market_cap_usd,
        prediction_database=prediction_database,
        prediction_run_id=prediction_run_id,
        prediction_top_n=prediction_top_n,
        prediction_min_score=prediction_min_score,
        all_fields_db=all_fields_db,
        extra_symbols=extra_symbols,
        top_n=top_n,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run 5y EV/Revenue financial projections from a TradingView "
            "all-fields DuckDB snapshot (bear/base/bull). "
            "See documentation/financial_projection_usage.md"
        )
    )
    parser.add_argument(
        "--all-fields-db",
        type=Path,
        default=None,
        help=(
            "Path to tradingview_all_fields_*.duckdb. "
            "Default: latest under logs/.../trading_view_all_fields_data/"
        ),
    )
    parser.add_argument(
        "--scenario-config",
        type=Path,
        default=DEFAULT_SCENARIO_CONFIG_PATH,
        help=f"Scenario JSON config (default: {DEFAULT_SCENARIO_CONFIG_PATH})",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Output root directory (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--min-market-cap-usd",
        type=float,
        default=DEFAULT_MIN_MARKET_CAP_USD,
        help=(
            "Min market_cap_basic filter in USD "
            f"(default: {DEFAULT_MIN_MARKET_CAP_USD:,.0f})"
        ),
    )
    parser.add_argument(
        "--day-label",
        type=str,
        default=None,
        help=(
            "All-fields day folder label (dd_mm_yyyy), e.g. 01_07_2026. "
            "Uses that day's DuckDB latest run."
        ),
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated EXCHANGE:TICKER list to project (peers still full universe)",
    )
    parser.add_argument(
        "--from-prediction-db",
        type=Path,
        default=None,
        help=(
            "Move-prediction DuckDB path (from run_full_analysis_suite_duckdb). "
            "Symbols are taken from profile_prediction_rows."
        ),
    )
    parser.add_argument(
        "--prediction-run-id",
        type=str,
        default=None,
        help="Optional run_id inside --from-prediction-db (default: latest)",
    )
    parser.add_argument(
        "--prediction-top-n",
        type=int,
        default=None,
        help=(
            "Optional cap on symbols pulled from prediction DB. "
            "Omit for all distinct symbols in the latest prediction run."
        ),
    )
    parser.add_argument(
        "--prediction-min-score",
        type=float,
        default=None,
        help="Optional min score filter when reading --from-prediction-db",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=25,
        help="Console top-N by base-scenario 5y upside (default: 25)",
    )
    parser.add_argument(
        "--no-parquet",
        action="store_true",
        help="Skip parquet export (CSV + DuckDB still written)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress console top-N report",
    )
    parser.add_argument(
        "--example",
        choices=("full", "named", "latest-all-fields", "latest-prediction", "none"),
        default="none",
        help=(
            "Built-in path: full/latest-all-fields=universe, named=demo watchlist, "
            "latest-prediction=names from latest move-prediction DuckDB"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.example in {"full", "latest-all-fields"}:
        result = run_financial_projection_from_latest_all_fields(
            min_market_cap_usd=args.min_market_cap_usd,
            all_fields_db=args.all_fields_db,
            top_n=args.top_n,
            scenario_config_path=args.scenario_config,
            output_root=args.output_root,
            export_parquet=not args.no_parquet,
            print_console=not args.quiet,
        )
    elif args.day_label:
        result = run_financial_projection_from_all_fields_day(
            args.day_label,
            min_market_cap_usd=args.min_market_cap_usd,
            symbols=resolve_symbols_argument(symbols_csv=args.symbols) or None,
            top_n=args.top_n,
            scenario_config_path=args.scenario_config,
            output_root=args.output_root,
            export_parquet=not args.no_parquet,
            print_console=not args.quiet,
        )
    elif args.example == "named":
        result = example_named_symbols(
            all_fields_db=args.all_fields_db,
            min_market_cap_usd=args.min_market_cap_usd,
            top_n=args.top_n,
        )
    elif args.example == "latest-prediction":
        result = run_financial_projection_from_latest_prediction_analysis(
            min_market_cap_usd=args.min_market_cap_usd,
            prediction_database=args.from_prediction_db,
            prediction_run_id=args.prediction_run_id,
            prediction_top_n=args.prediction_top_n,
            prediction_min_score=args.prediction_min_score,
            all_fields_db=args.all_fields_db,
            top_n=args.top_n,
            scenario_config_path=args.scenario_config,
            output_root=args.output_root,
            export_parquet=not args.no_parquet,
            print_console=not args.quiet,
        )
    elif args.from_prediction_db is not None and not args.symbols:
        result = run_financial_projection_from_latest_prediction_analysis(
            min_market_cap_usd=args.min_market_cap_usd,
            prediction_database=args.from_prediction_db,
            prediction_run_id=args.prediction_run_id,
            prediction_top_n=args.prediction_top_n,
            prediction_min_score=args.prediction_min_score,
            all_fields_db=args.all_fields_db,
            top_n=args.top_n,
            scenario_config_path=args.scenario_config,
            output_root=args.output_root,
            export_parquet=not args.no_parquet,
            print_console=not args.quiet,
        )
    else:
        symbols = resolve_symbols_argument(
            symbols_csv=args.symbols,
            prediction_database=args.from_prediction_db,
            prediction_run_id=args.prediction_run_id,
            prediction_top_n=args.prediction_top_n,
            prediction_min_score=args.prediction_min_score,
        )
        result = run_financial_projection_from_latest_all_fields(
            min_market_cap_usd=args.min_market_cap_usd,
            all_fields_db=args.all_fields_db,
            symbols=symbols or None,
            top_n=args.top_n,
            scenario_config_path=args.scenario_config,
            output_root=args.output_root,
            export_parquet=not args.no_parquet,
            print_console=not args.quiet,
        )

    print(f"Project root: {PROJECT_ROOT.as_posix()}")
    print(f"Source mode: {result.get('source_mode', 'custom')}")
    print(f"Run dir: {result['run_dir']}")
    print(f"DuckDB: {result['database_path']}")
    print(f"Summary CSV: {result['summary_csv']}")
    if result.get("requested_symbols"):
        print(f"Requested symbols: {len(result['requested_symbols'])}")
    if result.get("prediction_database"):
        print(f"Prediction DB: {result['prediction_database']}")
    if result.get("day_label"):
        print(f"Day label: {result['day_label']}")
    return result


if __name__ == "__main__":
    # Default: latest all-fields universe with market_cap_basic filter.
    # Examples:
    #   python run_financial_projection.py --example latest-all-fields
    #   python run_financial_projection.py --day-label 01_07_2026
    #   python run_financial_projection.py --example latest-prediction
    #   python run_financial_projection.py --example latest-prediction --prediction-top-n 40
    #   python run_financial_projection.py --example named
    #   python run_financial_projection.py --symbols NASDAQ:AAPL,NYSE:CRM
    #   python run_financial_projection.py --from-prediction-db <move_prediction.duckdb>

    run_financial_projection_from_latest_prediction_analysis(
        min_market_cap_usd=500_000_000,
    )

    # main()
    pass
