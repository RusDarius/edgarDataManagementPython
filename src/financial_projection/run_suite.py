"""Orchestrate financial projection suite over an all-fields snapshot."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from constants.trading_view_constants import PREFERRED_MARKETS

from .config import (
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SCENARIO_CONFIG_PATH,
    FinancialProjectionConfig,
    load_projection_config,
)
from .load_all_fields import (
    load_latest_all_fields_universe,
    prepare_projection_universe,
    resolve_all_fields_database,
)
from .model import project_universe
from .peer_scales import build_peer_scale_context
from .store import persist_projection_run


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_pct(value: Any, digits: int = 1) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number:.{digits}f}%"


def _format_price(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number:,.2f}"


def print_top_n_console(
    summaries: Sequence[dict[str, Any]],
    *,
    top_n: int = 25,
    rank_eligible_only: bool = True,
) -> list[dict[str, Any]]:
    """Print top-N by primary (or terminal) upside and return those rows."""
    base_rows = [
        row
        for row in summaries
        if row.get("scenario") == "base" and bool(row.get("valid"))
    ]
    if rank_eligible_only:
        eligible = [row for row in base_rows if bool(row.get("rank_eligible", True))]
        if eligible:
            base_rows = eligible
    base_rows.sort(
        key=lambda row: (
            _safe_float(row.get("primary_upside_pct"))
            if _safe_float(row.get("primary_upside_pct")) is not None
            else _safe_float(row.get("terminal_upside_pct"))
            or float("-inf")
        ),
        reverse=True,
    )
    selected = base_rows[: max(0, int(top_n))]

    by_symbol_scenario: dict[tuple[str, str], dict[str, Any]] = {
        (str(row.get("symbol") or ""), str(row.get("scenario") or "")): row
        for row in summaries
    }

    print()
    print("=" * 128)
    print(
        f"Financial projection top {len(selected)} "
        "(rank-eligible primary upside + short-term outlook)"
    )
    print("=" * 128)
    header = (
        f"{'Rank':<5} {'Symbol':<18} {'Prim':>7} {'5yUp':>7} {'Y1Up':>7} "
        f"{'Street':>7} {'Lens':<10} {'Regime':<12} {'ST':<12} {'Name'}"
    )
    print(header)
    print("-" * len(header))
    for rank, row in enumerate(selected, start=1):
        symbol = str(row.get("symbol") or "")
        name = str(row.get("name") or "")[:20]
        print(
            f"{rank:<5} {symbol:<18} "
            f"{_format_pct(row.get('primary_upside_pct')):>7} "
            f"{_format_pct(row.get('terminal_upside_pct')):>7} "
            f"{_format_pct(row.get('st_model_y1_upside_pct') or row.get('model_y1_upside_pct')):>7} "
            f"{_format_pct(row.get('st_street_price_upside_pct')):>7} "
            f"{str(row.get('valuation_lens') or ''):<10} "
            f"{str(row.get('valuation_regime') or ''):<12} "
            f"{str(row.get('st_outlook') or ''):<12} "
            f"{name}"
        )
    print("=" * 128)
    print(
        "Columns: primary decision upside | EV/Rev 5y upside | model Y1 | street target | "
        "valuation lens | regime | short-term outlook"
    )
    if selected:
        top_symbol = str(selected[0].get("symbol") or "")
        bear = by_symbol_scenario.get((top_symbol, "bear"), {})
        bull = by_symbol_scenario.get((top_symbol, "bull"), {})
        best = selected[0]
        print(
            f"Top bear/bull 5y band: bear={_format_pct(bear.get('terminal_upside_pct'))} "
            f"bull={_format_pct(bull.get('terminal_upside_pct'))} "
            f"width={_format_pct(best.get('scenario_width_y5_pp'), digits=0)}"
        )
        print(
            f"Top name terminal price (base): {_format_price(best.get('terminal_price'))} "
            f"vs close {_format_price(best.get('close'))} | "
            f"street target {_format_price(best.get('price_target_median'))} | "
            f"next earn {_safe_str_date(best.get('st_earnings_release_next_calendar_date'))} | "
            f"flags={best.get('decision_flags') or 'none'}"
        )
    print()
    return selected


def _safe_str_date(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "n/a"


def run_financial_projection_suite(
    *,
    all_fields_db: str | Path | None = None,
    scenario_config_path: str | Path | None = None,
    output_root: str | Path | None = None,
    min_market_cap_usd: float | None = None,
    markets: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
    top_n: int = 25,
    export_parquet: bool = True,
    print_console: bool = True,
) -> dict[str, Any]:
    """
    End-to-end suite: load all-fields → peer scales → project → persist → console.

    ``symbols`` (optional EXCHANGE:TICKER list) restricts *projection* targets.
    Peer/industry scales are still built on the full eligible scan universe so
    relative multiples stay comparable to the broader market context.
    """
    config: FinancialProjectionConfig = load_projection_config(scenario_config_path)
    resolved_min_mcap = (
        float(min_market_cap_usd)
        if min_market_cap_usd is not None
        else float(config.min_market_cap_usd)
    )
    resolved_markets = list(markets) if markets is not None else list(PREFERRED_MARKETS)
    database_path = resolve_all_fields_database(all_fields_db=all_fields_db)

    raw_rows, load_metadata = load_latest_all_fields_universe(
        database_path=database_path,
        markets=resolved_markets,
        min_market_cap_usd=resolved_min_mcap,
    )
    universe = prepare_projection_universe(
        raw_rows,
        min_revenue_usd=config.min_revenue_usd,
        max_ev_to_revenue=config.max_ev_to_revenue,
    )
    peer_context = build_peer_scale_context(
        universe,
        min_peer_group_size=config.min_peer_group_size,
        peer_trim_fraction=config.peer_trim_fraction,
        relative_scale_clip_low=config.relative_scale_clip_low,
        relative_scale_clip_high=config.relative_scale_clip_high,
        peer_mcap_band_low=config.peer_mcap_band_low,
        peer_mcap_band_high=config.peer_mcap_band_high,
        peer_mcap_refine_min_industry_n=config.peer_mcap_refine_min_industry_n,
    )

    requested_symbols = [
        str(symbol).strip()
        for symbol in (symbols or [])
        if str(symbol).strip()
    ]
    project_rows = universe
    if requested_symbols:
        wanted = {symbol.upper() for symbol in requested_symbols}
        project_rows = [
            row
            for row in universe
            if str(row.get("symbol") or "").strip().upper() in wanted
        ]
        if not project_rows:
            raise ValueError(
                "None of the requested symbols were projection-eligible in the "
                f"all-fields universe ({len(requested_symbols)} requested, "
                f"{len(universe)} eligible). Check EXCHANGE:TICKER labels and "
                "min revenue / EV-Rev filters."
            )

    summaries, year_grids = project_universe(
        project_rows,
        config=config,
        peer_context_by_symbol=peer_context,
    )

    created_at = datetime.now(tz=timezone.utc)
    persist_result = persist_projection_run(
        output_root=Path(output_root) if output_root is not None else DEFAULT_OUTPUT_ROOT,
        summaries=summaries,
        year_grids=year_grids,
        run_metadata={
            "suite_name": "financial_projection_v1",
            "config_id": config.config_id,
            "config_path": str(config.source_path),
            "horizon_years": config.horizon_years,
            "min_market_cap_usd": resolved_min_mcap,
            "markets": [str(m).lower() for m in resolved_markets],
            "requested_symbols": requested_symbols,
            "requested_symbol_count": len(requested_symbols),
            "projected_symbol_count": len({str(r.get("symbol") or "") for r in project_rows}),
            "all_fields_database": database_path.as_posix(),
            "load_metadata": load_metadata,
            "universe_row_count": len(universe),
            "raw_row_count": len(raw_rows),
            "valid_base_count": sum(
                1
                for row in summaries
                if row.get("scenario") == "base" and bool(row.get("valid"))
            ),
        },
        export_parquet=export_parquet,
        created_at_utc=created_at,
    )

    top_rows: list[dict[str, Any]] = []
    if print_console:
        print(
            f"Loaded {len(raw_rows)} all-fields rows -> {len(universe)} projection-eligible "
            f"from {database_path.as_posix()}"
        )
        if requested_symbols:
            print(
                f"Symbol filter: {len(requested_symbols)} requested -> "
                f"{len(project_rows)} projected"
            )
        print(
            f"Projected {len(summaries)} summary rows "
            f"({len(year_grids)} year-grid rows) -> {persist_result['database_path']}"
        )
        top_rows = print_top_n_console(summaries, top_n=top_n)

    return {
        "config_path": str(
            Path(scenario_config_path)
            if scenario_config_path is not None
            else DEFAULT_SCENARIO_CONFIG_PATH
        ),
        "all_fields_database": database_path.as_posix(),
        "requested_symbols": requested_symbols,
        "projected_symbol_count": len(project_rows),
        "universe_row_count": len(universe),
        "raw_row_count": len(raw_rows),
        "summary_row_count": len(summaries),
        "year_grid_row_count": len(year_grids),
        "top_n_symbols": [str(row.get("symbol") or "") for row in top_rows],
        **persist_result,
    }
