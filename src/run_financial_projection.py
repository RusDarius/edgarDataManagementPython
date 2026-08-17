"""
Financial projection entrypoint (sibling of main.py).

Flow (short):
  1) Ensure an all-fields DuckDB exists
     (main.py: export_all_tradingview_fields_duckdb() OR pass --all-fields-db).
  2) Optionally restrict to names (CLI --symbols, or names from a move-prediction
     DuckDB produced by run_full_analysis_suite_duckdb).
  3) Run growth lanes (default), full EV/Rev price projections, or a single-ticker
     overview scan (--mode overview).

Growth-first callables (this iteration):
  - run_growth_projection_from_latest_all_fields
  - run_growth_projection_from_latest_prediction_analysis
  - run_growth_projection_from_all_fields_day
  - run_growth_projection_for_custom_peer_group

Price-projection callables (EV/Rev suite):
  - run_financial_projection_from_latest_all_fields
  - run_financial_projection_from_latest_prediction_analysis
  - run_financial_projection_from_all_fields_day
  - run_financial_projection_for_custom_peer_group

Ticker overview:
  - run_ticker_projection_overview / example_ticker_overview

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
    run_financial_projection_suite_custom_peer_group,
    run_growth_projection_suite,
    run_growth_projection_suite_custom_peer_group,
    run_ticker_projection_overview,
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
# Growth suite presets — primary first-iteration output
# ---------------------------------------------------------------------------


def run_growth_projection_from_latest_all_fields(
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
    Growth lanes from the latest all-fields scan (or an explicit day DB).

    Emits universe / own / peer paths for revenue, EBIT, EBITDA, and margins.
    Peer scales still use the full eligible universe after the market-cap filter.

    main.py / REPL:
        run_growth_projection_from_latest_all_fields(min_market_cap_usd=500_000_000)
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

    result = run_growth_projection_suite(
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


def run_growth_projection_from_all_fields_day(
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
    """Growth lanes from a dated all-fields day folder (``dd_mm_yyyy``)."""
    database_path = resolve_all_fields_day_database(day_label)
    result = run_growth_projection_from_latest_all_fields(
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


def run_growth_projection_from_latest_prediction_analysis(
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
    Growth lanes for names from the latest move-prediction DuckDB run.

    Prediction DB = symbol shortlist only. Fundamentals always come from
    the all-fields snapshot.
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

    result = run_growth_projection_from_latest_all_fields(
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


def run_growth_projection_for_custom_peer_group(
    peer_group_symbols: Sequence[str],
    *,
    group_label: str = "custom_peer_group",
    project_symbols: Sequence[str] | None = None,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    all_fields_db: str | Path | None = None,
    top_n: int = 25,
    scenario_config_path: str | Path | None = None,
    output_root: str | Path | None = None,
    export_parquet: bool = True,
    print_console: bool = True,
) -> dict[str, Any]:
    """Growth lanes with hand-picked comps (separate method, not main-suite params)."""
    result = run_growth_projection_suite_custom_peer_group(
        peer_group_symbols=peer_group_symbols,
        all_fields_db=all_fields_db,
        scenario_config_path=scenario_config_path,
        output_root=output_root,
        min_market_cap_usd=min_market_cap_usd,
        project_symbols=project_symbols,
        group_label=group_label,
        top_n=top_n,
        export_parquet=export_parquet,
        print_console=print_console,
    )
    result["source_mode"] = "custom_peer_group"
    result["min_market_cap_usd"] = float(min_market_cap_usd)
    return result


# ---------------------------------------------------------------------------
# Price-projection suite presets (EV/Rev → terminal price)
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
    """Project EV/Rev price paths from the latest all-fields scan."""
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
    """EV/Rev projections from a dated all-fields day folder."""
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
    """EV/Rev projections for names from the latest move-prediction DuckDB."""
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


def run_financial_projection_for_custom_peer_group(
    peer_group_symbols: Sequence[str],
    *,
    group_label: str = "custom_peer_group",
    project_symbols: Sequence[str] | None = None,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    all_fields_db: str | Path | None = None,
    top_n: int = 25,
    scenario_config_path: str | Path | None = None,
    output_root: str | Path | None = None,
    export_parquet: bool = True,
    print_console: bool = True,
) -> dict[str, Any]:
    """EV/Rev projections with hand-picked comps."""
    result = run_financial_projection_suite_custom_peer_group(
        peer_group_symbols=peer_group_symbols,
        all_fields_db=all_fields_db,
        scenario_config_path=scenario_config_path,
        output_root=output_root,
        min_market_cap_usd=min_market_cap_usd,
        project_symbols=project_symbols,
        group_label=group_label,
        top_n=top_n,
        export_parquet=export_parquet,
        print_console=print_console,
    )
    result["source_mode"] = "custom_peer_group"
    result["min_market_cap_usd"] = float(min_market_cap_usd)
    return result


# ---------------------------------------------------------------------------
# Example helpers
# ---------------------------------------------------------------------------


def example_growth_full_universe(
    top_n: int = 25,
    *,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
) -> dict[str, Any]:
    """Growth lanes on the full eligible all-fields universe."""
    return run_growth_projection_from_latest_all_fields(
        min_market_cap_usd=min_market_cap_usd,
        top_n=top_n,
    )


def example_growth_named_symbols(
    symbols: Sequence[str] | None = None,
    *,
    all_fields_db: str | Path | None = None,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    top_n: int = 25,
) -> dict[str, Any]:
    """Growth lanes for a short watchlist (EXCHANGE:TICKER)."""
    resolved = (
        list(symbols)
        if symbols is not None
        else [
            "NASDAQ:AAPL",
            "NASDAQ:MSFT",
            "NYSE:CRM",
        ]
    )
    return run_growth_projection_from_latest_all_fields(
        min_market_cap_usd=min_market_cap_usd,
        all_fields_db=all_fields_db,
        symbols=resolved,
        top_n=top_n,
    )


def example_full_universe(
    top_n: int = 25,
    *,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
) -> dict[str, Any]:
    """EV/Rev suite on the full eligible all-fields universe."""
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
    """EV/Rev suite for a short watchlist."""
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


def example_from_prediction_analysis(
    prediction_database: str | Path,
    *,
    mode: str = "growth",
    all_fields_db: str | Path | None = None,
    prediction_run_id: str | None = None,
    prediction_top_n: int | None = None,
    prediction_min_score: float | None = None,
    extra_symbols: Sequence[str] | None = None,
    min_market_cap_usd: float = DEFAULT_MIN_MARKET_CAP_USD,
    top_n: int = 25,
) -> dict[str, Any]:
    """
    Chain after ``run_full_analysis_suite_duckdb`` using its DuckDB path.

    ``mode='growth'`` (default) or ``mode='price'``.
    """
    kwargs = dict(
        min_market_cap_usd=min_market_cap_usd,
        prediction_database=prediction_database,
        prediction_run_id=prediction_run_id,
        prediction_top_n=prediction_top_n,
        prediction_min_score=prediction_min_score,
        all_fields_db=all_fields_db,
        extra_symbols=extra_symbols,
        top_n=top_n,
    )
    if str(mode).lower() == "price":
        return run_financial_projection_from_latest_prediction_analysis(**kwargs)
    return run_growth_projection_from_latest_prediction_analysis(**kwargs)


# ---------------------------------------------------------------------------
# Single-ticker overview scan (~100 curated fields)
# ---------------------------------------------------------------------------


def example_ticker_overview(
    ticker: str = "NASDAQ:PENG", **kwargs: Any
) -> dict[str, Any]:
    """Pull bucketed overview metrics for one ticker into the projection log tree."""
    return run_ticker_projection_overview(ticker=ticker, **kwargs)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run growth lanes (default), EV/Rev financial projections, or a "
            "single-ticker overview scan from a TradingView all-fields DuckDB. "
            "See documentation/financial_projection_usage.md"
        )
    )
    parser.add_argument(
        "--mode",
        choices=("growth", "price", "overview"),
        default="growth",
        help=(
            "growth=revenue/EBIT/EBITDA/margin lanes (default); "
            "price=EV/Rev suite; "
            "overview=single-ticker ~100-field projection scan"
        ),
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="EXCHANGE:TICKER for --mode overview (e.g. NASDAQ:PENG)",
    )
    parser.add_argument(
        "--no-peer-percentiles",
        action="store_true",
        help="Overview mode: skip industry peer percentiles",
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
        help="Console top-N (growth: own-lane rev CAGR; price: primary upside)",
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
    mode = str(args.mode).lower()

    if mode == "overview":
        ticker = (
            args.ticker
            or (resolve_symbols_argument(symbols_csv=args.symbols) or [None])[0]
        )
        if not ticker:
            raise SystemExit(
                "--mode overview requires --ticker NASDAQ:PENG "
                "(or --symbols with one EXCHANGE:TICKER)."
            )
        result = run_ticker_projection_overview(
            ticker=str(ticker),
            all_fields_db=args.all_fields_db,
            day_label=args.day_label,
            output_root=args.output_root,
            include_peer_percentiles=not args.no_peer_percentiles,
            print_console=not args.quiet,
        )
        print(f"Project root: {PROJECT_ROOT.as_posix()}")
        print(f"Mode: {mode}")
        print(f"Matched: {result.get('matched_symbol')}")
        print(f"Run dir: {result['run_dir']}")
        print(f"Overview log: {result['overview_log']}")
        return result

    growth = mode == "growth"

    common = dict(
        min_market_cap_usd=args.min_market_cap_usd,
        top_n=args.top_n,
        scenario_config_path=args.scenario_config,
        output_root=args.output_root,
        export_parquet=not args.no_parquet,
        print_console=not args.quiet,
    )

    if args.example in {"full", "latest-all-fields"}:
        runner = (
            run_growth_projection_from_latest_all_fields
            if growth
            else run_financial_projection_from_latest_all_fields
        )
        result = runner(all_fields_db=args.all_fields_db, **common)
    elif args.day_label:
        runner = (
            run_growth_projection_from_all_fields_day
            if growth
            else run_financial_projection_from_all_fields_day
        )
        result = runner(
            args.day_label,
            symbols=resolve_symbols_argument(symbols_csv=args.symbols) or None,
            **common,
        )
    elif args.example == "named":
        result = (
            example_growth_named_symbols(
                all_fields_db=args.all_fields_db,
                min_market_cap_usd=args.min_market_cap_usd,
                top_n=args.top_n,
            )
            if growth
            else example_named_symbols(
                all_fields_db=args.all_fields_db,
                min_market_cap_usd=args.min_market_cap_usd,
                top_n=args.top_n,
            )
        )
    elif args.example == "latest-prediction" or (
        args.from_prediction_db is not None and not args.symbols
    ):
        runner = (
            run_growth_projection_from_latest_prediction_analysis
            if growth
            else run_financial_projection_from_latest_prediction_analysis
        )
        result = runner(
            prediction_database=args.from_prediction_db,
            prediction_run_id=args.prediction_run_id,
            prediction_top_n=args.prediction_top_n,
            prediction_min_score=args.prediction_min_score,
            all_fields_db=args.all_fields_db,
            **common,
        )
    else:
        symbols = resolve_symbols_argument(
            symbols_csv=args.symbols,
            prediction_database=args.from_prediction_db,
            prediction_run_id=args.prediction_run_id,
            prediction_top_n=args.prediction_top_n,
            prediction_min_score=args.prediction_min_score,
        )
        runner = (
            run_growth_projection_from_latest_all_fields
            if growth
            else run_financial_projection_from_latest_all_fields
        )
        result = runner(
            all_fields_db=args.all_fields_db,
            symbols=symbols or None,
            **common,
        )

    print(f"Project root: {PROJECT_ROOT.as_posix()}")
    print(f"Mode: {mode}")
    print(f"Source mode: {result.get('source_mode', 'custom')}")
    print(f"Run dir: {result['run_dir']}")
    print(f"DuckDB: {result['database_path']}")
    print(f"Summary CSV: {result['summary_csv']}")
    if result.get("requested_symbols"):
        print(f"Requested symbols: {len(result['requested_symbols'])}")
    if result.get("prediction_database"):
        print(f"Prediction DB: {result['prediction_database']}")
    if result.get("source_mode") == "all_fields_day" and result.get("day_label"):
        print(f"Day label: {result['day_label']}")
    return result


if __name__ == "__main__":
    # Default CLI: growth lanes on latest all-fields universe.
    # Examples:
    #   python run_financial_projection.py --top-n 25
    #   python run_financial_projection.py --mode price --example latest-all-fields
    #   python run_financial_projection.py --example latest-prediction
    #   python run_financial_projection.py --day-label 01_07_2026
    #   python run_financial_projection.py --symbols NASDAQ:AAPL,NYSE:CRM
    #   python run_financial_projection.py --from-prediction-db <move_prediction.duckdb>
    #   python run_financial_projection.py --mode overview --ticker NASDAQ:PENG
    #   python run_financial_projection.py --mode overview --ticker NASDAQ:TTD --day-label 11_08_2026
    #
    # Or call a dedicated method from this file / REPL:
    run_growth_projection_from_latest_prediction_analysis(
        min_market_cap_usd=500_000_000
    )
    #   run_growth_projection_from_latest_all_fields(symbols=["NASDAQ:ADBE", "NYSE:PATH"])
    #   run_growth_projection_for_custom_peer_group([...], group_label="saas_mature")
    #   example_ticker_overview("NASDAQ:PENG")
    # main()
    # DONT REMOVE PASS - ITS FOR CODE INSPECTION
    pass
