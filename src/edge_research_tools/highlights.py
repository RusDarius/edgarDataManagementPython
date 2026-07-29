from __future__ import annotations

import csv
import json
import math
import random
import re
import statistics
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import build_edge_research_run_context, resolve_edge_research_paths
from .region_filters import resolve_snapshot_market_filter
from .setup_engine import (
    DEFAULT_VOLATILITY_LIQUIDITY_SETUP_VARIANTS,
    VolatilityLiquiditySetupVariant,
)
from .upside_prediction import (
    compute_upside_prediction_fields,
    project_upside_prediction_row,
    sort_rows_by_upside_prediction,
)


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _table_exists(connection: Any, table_name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
            [table_name],
        ).fetchone()[0]
    )


def _normalize_string_list(values: Iterable[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    normalized: list[str] = []
    for value in values:
        stripped = str(value).strip()
        if stripped:
            normalized.append(stripped)
    return tuple(normalized)


def _normalize_market_list(values: Iterable[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    normalized: list[str] = []
    for value in values:
        stripped = str(value).strip().lower()
        if stripped:
            normalized.append(stripped)
    return tuple(normalized)


def _discover_horizons(connection: Any) -> tuple[int, ...]:
    pattern = re.compile(r"^forward_return_(\d+)d_pct$")
    columns = [
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info('symbol_day_forward_labels')"
        ).fetchall()
    ]
    return tuple(
        sorted(
            {int(match.group(1)) for col in columns if (match := pattern.match(col))}
        )
    )


def _variant_condition_sql(
    variant: VolatilityLiquiditySetupVariant,
    *,
    qualifier: str = "",
) -> str:
    q = f"{qualifier}." if qualifier else ""
    return (
        f"COALESCE({q}adrp_directional_universe_percentile, 0) >= {variant.min_adrp_pct}"
        f" AND COALESCE({q}relative_volume_directional_universe_percentile, 0) >= {variant.min_relative_volume_pct}"
        f" AND COALESCE({q}value_traded_directional_universe_percentile, 0) >= {variant.min_value_traded_pct}"
        f" AND COALESCE({q}liquidity_core_directional_universe_percentile, 0) >= {variant.min_liquidity_core_pct}"
        f" AND COALESCE({q}momentum_context_directional_universe_percentile, 0) >= {variant.min_momentum_context_pct}"
        f" AND COALESCE({q}volatility_core_directional_universe_percentile, 0) >= {variant.min_volatility_core_pct}"
    )


def _build_region_filter_sql(
    *,
    country_expr: str,
    exchange_expr: str,
    countries: Sequence[str],
    exchanges: Sequence[str],
) -> str:
    filters: list[str] = []
    if countries:
        country_literals = ", ".join(_q(value.upper()) for value in countries)
        filters.append(f"UPPER(COALESCE({country_expr}, '')) IN ({country_literals})")
    if exchanges:
        exchange_literals = ", ".join(_q(value.upper()) for value in exchanges)
        filters.append(f"UPPER(COALESCE({exchange_expr}, '')) IN ({exchange_literals})")
    return " AND ".join(filters) if filters else "1 = 1"


def _build_market_filter_sql(*, market_expr: str, markets: Sequence[str]) -> str:
    if not markets:
        return "1 = 1"
    literals = ", ".join(_q(value.lower()) for value in markets)
    return f"LOWER(COALESCE({market_expr}, '')) IN ({literals})"


def _hist_metric_columns(horizons: Sequence[int], suffix: str = "") -> str:
    parts: list[str] = []
    for horizon in horizons:
        col = f"forward_return_{horizon}d_pct"
        mfe = f"max_favorable_excursion_{horizon}d_pct"
        mae = f"max_adverse_excursion_{horizon}d_pct"
        flag = f"target_before_stop_{horizon}d_flag"
        parts.extend(
            [
                f"COUNT({col}) AS sample_count_{horizon}d{suffix}",
                f"MEDIAN({col}) AS median_fwd_{horizon}d{suffix}",
                f"AVG({col}) AS avg_fwd_{horizon}d{suffix}",
                (
                    f"AVG(CASE WHEN {col} IS NULL THEN NULL "
                    f"WHEN {col} > 0 THEN 1.0 ELSE 0.0 END) AS win_rate_{horizon}d{suffix}"
                ),
                f"MEDIAN({mfe}) AS median_mfe_{horizon}d{suffix}",
                f"MEDIAN({mae}) AS median_mae_{horizon}d{suffix}",
                (
                    f"AVG(CASE WHEN {flag} IS NULL THEN NULL "
                    f"WHEN {flag} THEN 1.0 ELSE 0.0 END) AS target_rate_{horizon}d{suffix}"
                ),
            ]
        )
    return ",\n                ".join(parts)


def _lane_metric_columns(horizons: Sequence[int]) -> str:
    parts: list[str] = []
    for horizon in horizons:
        col = f"forward_return_{horizon}d_pct"
        mfe = f"max_favorable_excursion_{horizon}d_pct"
        mae = f"max_adverse_excursion_{horizon}d_pct"
        flag = f"target_before_stop_{horizon}d_flag"
        parts.extend(
            [
                f"COUNT({col}) AS sample_count_{horizon}d",
                f"MEDIAN({col}) AS median_fwd_{horizon}d",
                f"AVG({col}) AS avg_fwd_{horizon}d",
                (
                    f"AVG(CASE WHEN {col} IS NULL THEN NULL "
                    f"WHEN {col} > 0 THEN 1.0 ELSE 0.0 END) AS win_rate_{horizon}d"
                ),
                f"MEDIAN({mfe}) AS median_mfe_{horizon}d",
                f"MEDIAN({mae}) AS median_mae_{horizon}d",
                (
                    f"AVG(CASE WHEN {flag} IS NULL THEN NULL "
                    f"WHEN {flag} THEN 1.0 ELSE 0.0 END) AS target_rate_{horizon}d"
                ),
            ]
        )
    return ",\n                ".join(parts)


def _fetch_dict_rows(connection: Any, query: str) -> list[dict[str, Any]]:
    cursor = connection.execute(query)
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    bounded_q = max(0.0, min(1.0, float(q)))
    pos = bounded_q * (len(sorted_values) - 1)
    lower_idx = int(math.floor(pos))
    upper_idx = int(math.ceil(pos))
    if lower_idx == upper_idx:
        return float(sorted_values[lower_idx])
    lower_val = float(sorted_values[lower_idx])
    upper_val = float(sorted_values[upper_idx])
    return lower_val + (upper_val - lower_val) * (pos - lower_idx)


def _bootstrap_median_ci(
    values: Sequence[float],
    *,
    iterations: int,
    confidence_level: float,
    seed: int,
) -> tuple[float, float] | None:
    cleaned = [float(v) for v in values if v is not None]
    if len(cleaned) < 2:
        return None
    iter_count = max(50, int(iterations))
    conf = max(0.5, min(0.999, float(confidence_level)))
    rng = random.Random(int(seed))
    n = len(cleaned)
    medians: list[float] = []
    for _ in range(iter_count):
        sample = [cleaned[rng.randrange(n)] for _ in range(n)]
        medians.append(float(statistics.median(sample)))
    medians.sort()
    alpha = (1.0 - conf) / 2.0
    ci_low = _quantile(medians, alpha)
    ci_high = _quantile(medians, 1.0 - alpha)
    return (ci_low, ci_high)


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _normalize_symbol_field(row: dict[str, Any]) -> dict[str, Any]:
    """Backfill a missing symbol from exchange:bare_ticker when available.

    Some upstream snapshot rows carry a blank ``symbol`` but still have
    ``exchange`` and ``bare_ticker``. Reconstruct the fully-qualified
    ``EXCHANGE:TICKER`` form so the shortlist and downstream lenses keep
    a usable join key.
    """
    symbol = str(row.get("symbol") or "").strip()
    if symbol:
        return row
    exchange = str(row.get("exchange") or "").strip()
    bare_ticker = str(row.get("bare_ticker") or "").strip()
    if ":" in bare_ticker and not exchange:
        row["symbol"] = bare_ticker
    elif exchange and bare_ticker:
        row["symbol"] = f"{exchange}:{bare_ticker}"
    elif bare_ticker:
        row["symbol"] = bare_ticker
    else:
        row["symbol"] = ""
    return row


def _resolve_latest_snapshot_database(output_root: str | Path | None) -> Path:
    paths = resolve_edge_research_paths(output_root=output_root)
    candidates = sorted(
        paths.foundation_root.glob(
            "edge_feature_snapshot_*/symbol_day_feature_snapshot.duckdb"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        candidates = sorted(
            paths.output_root.glob(
                "edge_feature_snapshot_*/symbol_day_feature_snapshot.duckdb"
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    if not candidates:
        raise FileNotFoundError(
            "No edge_feature_snapshot_* database was found under the edge research foundations or legacy runs directories."
        )
    return candidates[0]


def _format_scope_args(
    *,
    markets: Sequence[str],
    countries: Sequence[str],
    exchanges: Sequence[str],
    us_only: bool,
) -> str:
    args: list[str] = []
    if markets:
        args.append(f"--markets {','.join(markets)}")
    if countries:
        args.append(f"--countries {','.join(countries)}")
    if exchanges:
        args.append(f"--exchanges {','.join(exchanges)}")
    if us_only:
        args.append("--us-only")
    return f" {' '.join(args)}" if args else ""


def _write_highlights_report(
    path: Path,
    *,
    snapshot_database_path: Path,
    resolved_target_date: str,
    ranking_horizon: int,
    lane_setup_name: str,
    group_by: str,
    markets: Sequence[str],
    countries: Sequence[str],
    exchanges: Sequence[str],
    us_only: bool,
    screen_min_composite: float,
    lane_min_sample_count: int,
    lane_min_win_rate: float,
    lane_min_median_fwd: float,
    min_any_setup_rate: float,
    min_hist_occurrences: int,
    min_hist_win_rate: float,
    min_hist_median_fwd: float,
    stability_short_lookback: int,
    stability_long_lookback: int,
    bootstrap_iterations: int,
    bootstrap_confidence_level: float,
    lane_leaders_path: Path,
    universe_path: Path,
    shortlist_path: Path,
    top30_path: Path,
    top10_path: Path,
    upside_ranked_path: Path,
    upside_top_path: Path,
    lane_row_count: int,
    universe_row_count: int,
    shortlist_row_count: int,
    strict_shortlist_row_count: int,
    expanded_shortlist_row_count: int,
    expanded_added_row_count: int,
    min_shortlist_count: int,
    top30_row_count: int,
    top10_row_count: int,
    upside_ranked_row_count: int,
    upside_top_row_count: int,
    upside_top_count: int,
    filter_args: str,
) -> None:
    lines = [
        "# Daily Edge Highlights",
        "",
        "## What This Run Does",
        "",
        "This is the daily active-management extraction pass for the edge research module.",
        "It takes the latest snapshot database, selects the strongest edge lanes, ranks current big-mover candidates,",
        "computes rolling stability, and produces a focused top-30 plus top-10 confidence list.",
        "",
        "## Daily Use",
        "",
        "Run this once after your daily edge suite or latest snapshot refresh.",
        "The intended workflow is: latest all-fields export -> snapshot + labels -> daily highlights.",
        "",
        "## Interpretation",
        "",
        "- `edge_lane_leaders.csv`: the strongest historical edge lanes in the selected scope.",
        "- `edge_name_universe.csv`: full current-date universe in the selected scope with no shortlist cutoff, used by downstream integrated lenses so every eligible ticker can be scored.",
        "- `edge_name_shortlist.csv`: ranked universe for this run (strict lane+durability pass names first, then expanded momentum candidates when needed for breadth).",
        "- `edge_name_top30.csv`: daily big-mover focus list ranked primarily by current setup state.",
        "- `edge_name_top10_confidence.csv`: quick screen ranked by confirmation, durability, and stability.",
        "- `edge_upside_prediction_ranked.csv`: full shortlist ranked for upward move prediction using current state + historical forward behavior.",
        f"- `edge_upside_prediction_top{upside_top_count}.csv`: focused upside-prediction review list (safety columns appended when run through the integrated suite).",
        "- `edge_lane_leaders.csv` now includes bootstrap confidence intervals for lane median forward returns.",
        "- `strict_filter_pass` / `strict_filter_failed_tags` identify whether a row passed full durability thresholds or entered via expanded-momentum coverage.",
        "",
        "## Score Definitions",
        "",
        "- `big_mover_score`: current-state score emphasizing composite setup quality, ADRP, relative volume, traded value, and sleeve support.",
        "- `confidence_score`: confirmation score emphasizing rolling stability, recurrence, historical win rate, and lane quality.",
        "- `stability_score`: rolling-window consistency score based on recent setup hit-rate and recent composite-score dispersion.",
        "- `upside_prediction_score`: upward-move prediction rank combining current expansion, blended historical win/median/target/MFE (name vs lane), and lane context; penalizes severe historical drawdowns.",
        "- `upside_hist_signal_blend`: name/lane blended historical upside signal used inside `upside_prediction_score`.",
        "- `upside_target_before_stop_rate`: historical share of past setup days where +10% was hit before -7% within the ranking horizon.",
        "",
        "## Core Fields Used By This Daily Method",
        "",
        "Current-state rank inputs:",
        "- ADRP (raw + directional percentile)",
        "- ATRP (raw only, informational)",
        "- Volatility.M (raw only, informational)",
        "- relative_volume_10d_calc (raw + directional percentile)",
        "- Value.Traded directional percentile",
        "- volume raw",
        "- volatility_core sleeve directional percentile",
        "- liquidity_core sleeve directional percentile",
        "- momentum_context sleeve directional percentile",
        "",
        "Historical forward-label inputs:",
        "- forward_return_3d_pct / 5d / 10d / 20d",
        "- max_favorable_excursion_<N>d_pct",
        "- max_adverse_excursion_<N>d_pct",
        "- target_before_stop_<N>d_flag",
        "",
        "Important scope note:",
        "- This method does not currently use EV, FCF yield, net debt, Altman Z, ROIC, or defensive-fundamental profile fields.",
        "- Those belong in a separate safety / quality companion scan rather than being forced into the volatility-liquidity composite.",
        "",
        "## Suggested Safety Companion Design",
        "",
        "Use two independent fundamental components rather than one noisy quality composite:",
        "- `balance_sheet_safety_score`: net cash to market cap, short-term cash coverage, cash ratio/current ratio, debt to equity, net debt, Altman Z, Zmijewski.",
        "- `cash_generation_value_score`: free cash flow margin, earnings yield, shareholder yield, cash generation relative to EV or market cap, with peer-relative percentiles by industry.",
        "",
        "This keeps the current module focused on actionable state detection while the safety layer handles durability and downside quality.",
        "",
        "## Replay Commands",
        "",
        "PowerShell:",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        f'python -m run_edge_research_tools highlights --snapshot-db "{snapshot_database_path.as_posix()}" --ranking-horizon {ranking_horizon} --lane-setup-name {lane_setup_name} --group-by {group_by}{filter_args}',
        "```",
        "",
        "Git bash:",
        "```bash",
        f"PYTHONPATH='src:.' python -m run_edge_research_tools highlights --snapshot-db \"{snapshot_database_path.as_posix()}\" --ranking-horizon {ranking_horizon} --lane-setup-name {lane_setup_name} --group-by {group_by}{filter_args}",
        "```",
        "",
        "US-only example:",
        "```bash",
        f"PYTHONPATH='src:.' python -m run_edge_research_tools highlights --snapshot-db \"{snapshot_database_path.as_posix()}\" --us-only",
        "```",
        "",
        "## Filters Used In This Run",
        "",
        f"- snapshot_database_path: `{snapshot_database_path.as_posix()}`",
        f"- target_date: {resolved_target_date}",
        f"- ranking_horizon: {ranking_horizon}d",
        f"- lane_setup_name: {lane_setup_name}",
        f"- group_by: {group_by}",
        f"- markets: {', '.join(markets) if markets else 'all'}",
        f"- countries: {', '.join(countries) if countries else 'all'}",
        f"- exchanges: {', '.join(exchanges) if exchanges else 'all'}",
        f"- us_only: {us_only}",
        f"- screen_min_composite: {screen_min_composite}",
        f"- lane_min_sample_count: {lane_min_sample_count}",
        f"- lane_min_win_rate: {lane_min_win_rate}",
        f"- lane_min_median_fwd: {lane_min_median_fwd}",
        f"- min_any_setup_rate: {min_any_setup_rate}",
        f"- min_hist_occurrences: {min_hist_occurrences}",
        f"- min_hist_win_rate: {min_hist_win_rate}",
        f"- min_hist_median_fwd: {min_hist_median_fwd}",
        f"- min_shortlist_count: {min_shortlist_count}",
        f"- stability_short_lookback: {stability_short_lookback}",
        f"- stability_long_lookback: {stability_long_lookback}",
        f"- bootstrap_iterations: {bootstrap_iterations}",
        f"- bootstrap_confidence_level: {bootstrap_confidence_level}",
        "",
        "## Output Files",
        "",
        f"- lane leaders: `{lane_leaders_path.as_posix()}` ({lane_row_count} rows)",
        f"- full lens universe: `{universe_path.as_posix()}` ({universe_row_count} rows)",
        f"- shortlist: `{shortlist_path.as_posix()}` ({shortlist_row_count} rows)",
        f"- shortlist strict-pass rows: {strict_shortlist_row_count}",
        f"- shortlist expanded-candidate rows considered: {expanded_shortlist_row_count}",
        f"- shortlist expanded rows added: {expanded_added_row_count}",
        f"- top30 big movers: `{top30_path.as_posix()}` ({top30_row_count} rows)",
        f"- top10 confidence: `{top10_path.as_posix()}` ({top10_row_count} rows)",
        f"- upside prediction ranked: `{upside_ranked_path.as_posix()}` ({upside_ranked_row_count} rows)",
        f"- upside prediction top focus: `{upside_top_path.as_posix()}` ({upside_top_row_count} rows, requested count = {upside_top_count})",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT = 2000


def run_edge_highlights(
    *,
    snapshot_database_path: str | Path | None = None,
    target_date: str | None = None,
    ranking_horizon: int = 5,
    lane_setup_name: str = "adrp_relvol_core",
    group_by: str = "industry",
    markets: Iterable[str] | None = None,
    countries: Iterable[str] | None = None,
    exchanges: Iterable[str] | None = None,
    us_only: bool = False,
    screen_min_composite: float = 0.45,
    lane_min_sample_count: int = 20,
    lane_min_win_rate: float = 0.55,
    lane_min_median_fwd: float = 1.0,
    min_any_setup_rate: float = 0.03,
    min_hist_occurrences: int = 2,
    min_hist_win_rate: float = 0.45,
    min_hist_median_fwd: float = 0.0,
    min_shortlist_count: int = DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
    shortlist_top_n: int = 30,
    top10_count: int = 10,
    upside_top_count: int = 20,
    stability_short_lookback: int = 10,
    stability_long_lookback: int = 20,
    bootstrap_iterations: int = 400,
    bootstrap_confidence_level: float = 0.9,
    bootstrap_seed: int = 31,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
    variants: Sequence[
        VolatilityLiquiditySetupVariant
    ] = DEFAULT_VOLATILITY_LIQUIDITY_SETUP_VARIANTS,
) -> dict[str, Any]:
    allowed_groupings = {"industry", "sector", "exchange", "country"}
    normalized_group_by = str(group_by).strip().lower() or "industry"
    if normalized_group_by not in allowed_groupings:
        raise ValueError(
            f"Unsupported group_by '{group_by}'. Expected one of: {', '.join(sorted(allowed_groupings))}."
        )

    variant_map = {variant.name: variant for variant in variants}
    if lane_setup_name not in variant_map:
        raise ValueError(
            f"Unsupported lane_setup_name '{lane_setup_name}'. Expected one of: {', '.join(sorted(variant_map))}."
        )

    resolved_countries = list(
        value.upper() for value in _normalize_string_list(countries)
    )
    if us_only:
        for alias in ("UNITED STATES", "US", "USA"):
            if alias not in resolved_countries:
                resolved_countries.append(alias)
    resolved_exchanges = list(
        value.upper() for value in _normalize_string_list(exchanges)
    )
    resolved_markets = list(_normalize_market_list(markets))
    filter_args = _format_scope_args(
        markets=resolved_markets,
        countries=resolved_countries,
        exchanges=resolved_exchanges,
        us_only=bool(us_only),
    )

    resolved_snapshot_database_path = (
        Path(snapshot_database_path)
        if snapshot_database_path is not None
        else _resolve_latest_snapshot_database(output_root)
    )
    if not resolved_snapshot_database_path.exists():
        raise FileNotFoundError(
            f"Snapshot database was not found: {resolved_snapshot_database_path}"
        )

    output_paths = resolve_edge_research_paths(output_root=output_root)
    context = build_edge_research_run_context(
        prefix="edge_highlights",
        output_root=output_paths.output_root,
    )
    lane_leaders_path = context.output_dir / "edge_lane_leaders.csv"
    universe_path = context.output_dir / "edge_name_universe.csv"
    shortlist_path = context.output_dir / "edge_name_shortlist.csv"
    top30_path = context.output_dir / "edge_name_top30.csv"
    top10_path = (
        context.output_dir / f"edge_name_top{int(max(1, top10_count))}_confidence.csv"
    )
    resolved_upside_top_count = int(max(1, upside_top_count))
    upside_ranked_path = context.output_dir / "edge_upside_prediction_ranked.csv"
    upside_top_path = (
        context.output_dir
        / f"edge_upside_prediction_top{resolved_upside_top_count}.csv"
    )
    report_md = context.output_dir / "edge_highlights_report.md"
    manifest_path = context.output_dir / "edge_highlights_manifest.json"

    duckdb_mod = _import_duckdb()
    conn = duckdb_mod.connect(
        resolved_snapshot_database_path.as_posix(), read_only=False
    )
    conn.execute(f"SET threads TO {int(duckdb_threads)}")
    try:
        for table_name in (
            "symbol_day_feature_snapshot",
            "symbol_day_feature_values",
            "symbol_day_sleeve_scores",
            "symbol_day_forward_labels",
        ):
            if not _table_exists(conn, table_name):
                raise ValueError(
                    f"Required table '{table_name}' not found. Run the full suite first."
                )

        horizons = _discover_horizons(conn)
        resolved_ranking_horizon = (
            ranking_horizon if ranking_horizon in horizons else max(horizons)
        )
        region_filter_sql = _build_region_filter_sql(
            country_expr="snap.country",
            exchange_expr="snap.exchange",
            countries=resolved_countries,
            exchanges=resolved_exchanges,
        )
        market_filter_sql, _market_filter_applied = resolve_snapshot_market_filter(
            conn,
            alias="snap",
            markets=resolved_markets,
        )
        resolved_target_date = (
            target_date
            if target_date is not None
            else str(
                conn.execute(
                    f"SELECT MAX(source_date)::VARCHAR FROM symbol_day_feature_snapshot AS snap WHERE {region_filter_sql} AND {market_filter_sql}"
                ).fetchone()[0]
            )
        )
        if not resolved_target_date or resolved_target_date == "None":
            raise ValueError(
                "No source_date matched the selected region filters in symbol_day_feature_snapshot."
            )

        variant_flag_columns = ",\n                    ".join(
            f"CASE WHEN {_variant_condition_sql(variant)} THEN 1 ELSE 0 END AS in_{variant.name}"
            for variant in variants
        )
        any_setup_sql = " OR ".join(f"in_{variant.name} = 1" for variant in variants)
        chosen_setup_flag = f"in_{lane_setup_name}"

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE daily_state_enriched AS
            WITH field_pivot AS (
                SELECT
                    source_day_label,
                    source_date,
                    symbol,
                    MAX(CASE WHEN field_name = 'ADRP' THEN raw_value END) AS adrp_raw,
                    MAX(CASE WHEN field_name = 'ATRP' THEN raw_value END) AS atrp_raw,
                    MAX(CASE WHEN field_name = 'Volatility.M' THEN raw_value END) AS volatility_month_raw,
                    MAX(CASE WHEN field_name = 'relative_volume_10d_calc' THEN raw_value END) AS relvol_raw,
                    MAX(CASE WHEN field_name = 'volume' THEN raw_value END) AS volume_raw,
                    MAX(CASE WHEN field_name = 'ADRP' THEN directional_universe_percentile END) AS adrp_directional_universe_percentile,
                    MAX(CASE WHEN field_name = 'relative_volume_10d_calc' THEN directional_universe_percentile END) AS relative_volume_directional_universe_percentile,
                    MAX(CASE WHEN field_name = 'Value.Traded' THEN directional_universe_percentile END) AS value_traded_directional_universe_percentile
                FROM symbol_day_feature_values
                GROUP BY source_day_label, source_date, symbol
            ),
            sleeve_pivot AS (
                SELECT
                    source_day_label,
                    source_date,
                    symbol,
                    MAX(CASE WHEN sleeve_name = 'volatility_core' THEN avg_directional_universe_percentile END) AS volatility_core_directional_universe_percentile,
                    MAX(CASE WHEN sleeve_name = 'liquidity_core' THEN avg_directional_universe_percentile END) AS liquidity_core_directional_universe_percentile,
                    MAX(CASE WHEN sleeve_name = 'momentum_context' THEN avg_directional_universe_percentile END) AS momentum_context_directional_universe_percentile
                FROM symbol_day_sleeve_scores
                GROUP BY source_day_label, source_date, symbol
            ),
            base_state AS (
                SELECT
                    snap.source_day_label,
                    snap.source_date,
                    snap.symbol,
                    snap.bare_ticker,
                    snap.company_name,
                    snap.exchange,
                    snap.country,
                    snap.sector,
                    snap.industry,
                    snap.close_price,
                    snap.market_cap_basic,
                    fp.adrp_raw,
                    fp.atrp_raw,
                    fp.volatility_month_raw,
                    fp.relvol_raw,
                    fp.volume_raw,
                    fp.adrp_directional_universe_percentile,
                    fp.relative_volume_directional_universe_percentile,
                    fp.value_traded_directional_universe_percentile,
                    sp.volatility_core_directional_universe_percentile,
                    sp.liquidity_core_directional_universe_percentile,
                    sp.momentum_context_directional_universe_percentile,
                    ROUND((
                        COALESCE(fp.adrp_directional_universe_percentile, 0) +
                        COALESCE(fp.relative_volume_directional_universe_percentile, 0) +
                        COALESCE(fp.value_traded_directional_universe_percentile, 0) +
                        COALESCE(sp.volatility_core_directional_universe_percentile, 0) +
                        COALESCE(sp.liquidity_core_directional_universe_percentile, 0) +
                        COALESCE(sp.momentum_context_directional_universe_percentile, 0)
                    ) / 6.0, 4) AS composite_score_day,
                    {variant_flag_columns}
                FROM symbol_day_feature_snapshot AS snap
                LEFT JOIN field_pivot AS fp
                    ON fp.source_day_label = snap.source_day_label
                    AND fp.source_date = snap.source_date
                    AND fp.symbol = snap.symbol
                LEFT JOIN sleeve_pivot AS sp
                    ON sp.source_day_label = snap.source_day_label
                    AND sp.source_date = snap.source_date
                    AND sp.symbol = snap.symbol
                WHERE {region_filter_sql}
                    AND {market_filter_sql}
            )
            SELECT
                base_state.*,
                CASE WHEN {any_setup_sql} THEN 1 ELSE 0 END AS in_any_setup
            FROM base_state
            """)

        hist_metric_columns_sql = _hist_metric_columns(horizons, suffix="_in_setup")
        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE hist_by_symbol AS
            SELECT
                state.symbol,
                COUNT(*) AS hist_occurrence_count_in_setup,
                {hist_metric_columns_sql}
            FROM daily_state_enriched AS state
            JOIN symbol_day_forward_labels AS lbl
                ON lbl.source_day_label = state.source_day_label
                AND lbl.symbol = state.symbol
            WHERE state.in_any_setup = 1
            GROUP BY state.symbol
            """)

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE stability_by_symbol AS
            WITH ranked AS (
                SELECT
                    state.symbol,
                    state.composite_score_day,
                    state.in_any_setup,
                    ROW_NUMBER() OVER (
                        PARTITION BY state.symbol
                        ORDER BY state.source_date DESC
                    ) AS recency_rank
                FROM daily_state_enriched AS state
            ),
            aggregated AS (
                SELECT
                    symbol,
                    AVG(CASE WHEN recency_rank <= {int(max(1, stability_short_lookback))} THEN CAST(in_any_setup AS DOUBLE) END) AS short_setup_rate,
                    AVG(CASE WHEN recency_rank <= {int(max(1, stability_long_lookback))} THEN CAST(in_any_setup AS DOUBLE) END) AS long_setup_rate,
                    AVG(CASE WHEN recency_rank <= {int(max(1, stability_short_lookback))} THEN composite_score_day END) AS short_composite_avg,
                    STDDEV_SAMP(CASE WHEN recency_rank <= {int(max(1, stability_short_lookback))} THEN composite_score_day END) AS short_composite_stddev,
                    AVG(CASE WHEN recency_rank <= {int(max(1, stability_long_lookback))} THEN composite_score_day END) AS long_composite_avg,
                    STDDEV_SAMP(CASE WHEN recency_rank <= {int(max(1, stability_long_lookback))} THEN composite_score_day END) AS long_composite_stddev
                FROM ranked
                GROUP BY symbol
            ),
            scored AS (
                SELECT
                    aggregated.*,
                    GREATEST(0.0, LEAST(1.0, 1.0 - COALESCE(short_composite_stddev, 0.25) / 0.20)) AS short_consistency_score,
                    GREATEST(0.0, LEAST(1.0, 1.0 - COALESCE(long_composite_stddev, 0.25) / 0.20)) AS long_consistency_score
                FROM aggregated
            )
            SELECT
                scored.*,
                ROUND(
                    (0.35 * COALESCE(short_setup_rate, 0.0)) +
                    (0.35 * COALESCE(long_setup_rate, 0.0)) +
                    (0.15 * short_consistency_score) +
                    (0.15 * long_consistency_score),
                    4
                ) AS stability_score
            FROM scored
            """)

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE persistence_by_symbol AS
            SELECT
                state.symbol,
                COUNT(*) AS total_dataset_days,
                SUM(CASE WHEN state.in_any_setup = 1 THEN 1 ELSE 0 END) AS any_setup_days,
                ROUND(
                    SUM(CASE WHEN state.in_any_setup = 1 THEN 1 ELSE 0 END) * 1.0
                    / NULLIF(COUNT(*), 0),
                    4
                ) AS any_setup_rate,
                SUM(CASE WHEN state.{chosen_setup_flag} = 1 THEN 1 ELSE 0 END) AS lane_setup_days,
                ROUND(
                    SUM(CASE WHEN state.{chosen_setup_flag} = 1 THEN 1 ELSE 0 END) * 1.0
                    / NULLIF(COUNT(*), 0),
                    4
                ) AS lane_setup_rate,
                ROUND(AVG(state.adrp_directional_universe_percentile), 4) AS avg_adrp_pct,
                ROUND(AVG(state.relative_volume_directional_universe_percentile), 4) AS avg_relvol_pct,
                ROUND(AVG(state.volatility_core_directional_universe_percentile), 4) AS avg_vol_core_pct,
                ROUND(AVG(state.liquidity_core_directional_universe_percentile), 4) AS avg_liq_core_pct,
                ROUND(AVG(state.momentum_context_directional_universe_percentile), 4) AS avg_mom_core_pct
            FROM daily_state_enriched AS state
            GROUP BY state.symbol
            """)

        lane_metric_columns_sql = _lane_metric_columns(horizons)
        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE lane_summary_raw AS
            SELECT
                {_q(lane_setup_name)} AS setup_name,
                {_q(normalized_group_by)} AS group_by,
                COALESCE(state.{normalized_group_by}, 'Unknown') AS group_value,
                COUNT(*) AS occurrence_count,
                COUNT(DISTINCT state.symbol) AS distinct_symbol_count,
                MIN(state.source_date) AS first_occurrence_date,
                MAX(state.source_date) AS last_occurrence_date,
                {lane_metric_columns_sql}
            FROM daily_state_enriched AS state
            LEFT JOIN symbol_day_forward_labels AS lbl
                ON lbl.source_day_label = state.source_day_label
                AND lbl.symbol = state.symbol
            WHERE state.{chosen_setup_flag} = 1
            GROUP BY COALESCE(state.{normalized_group_by}, 'Unknown')
            HAVING COUNT(*) >= {int(max(1, lane_min_sample_count))}
            """)

        lane_horizon_sample_col = f"sample_count_{resolved_ranking_horizon}d"
        lane_horizon_median_col = f"median_fwd_{resolved_ranking_horizon}d"
        lane_horizon_win_col = f"win_rate_{resolved_ranking_horizon}d"
        raw_lane_rows = _fetch_dict_rows(
            conn,
            (
                "SELECT * FROM lane_summary_raw "
                f"ORDER BY {lane_horizon_median_col} DESC NULLS LAST, "
                f"{lane_horizon_win_col} DESC NULLS LAST, occurrence_count DESC"
            ),
        )

        lane_rows = [
            row
            for row in raw_lane_rows
            if _safe_int(row.get(lane_horizon_sample_col))
            >= int(max(1, lane_min_sample_count))
            and _safe_float(row.get(lane_horizon_win_col)) >= float(lane_min_win_rate)
            and _safe_float(row.get(lane_horizon_median_col))
            >= float(lane_min_median_fwd)
        ]
        if not lane_rows:
            lane_rows = raw_lane_rows[: max(10, shortlist_top_n)]

        lane_bootstrap_samples = conn.execute(f"""
            SELECT
                COALESCE(state.{normalized_group_by}, 'Unknown') AS group_value,
                lbl.forward_return_{resolved_ranking_horizon}d_pct AS forward_return
            FROM daily_state_enriched AS state
            LEFT JOIN symbol_day_forward_labels AS lbl
                ON lbl.source_day_label = state.source_day_label
                AND lbl.symbol = state.symbol
            WHERE state.{chosen_setup_flag} = 1
              AND lbl.forward_return_{resolved_ranking_horizon}d_pct IS NOT NULL
            """).fetchall()
        bootstrap_by_group: dict[str, list[float]] = {}
        for group_value, forward_return in lane_bootstrap_samples:
            key = str(group_value)
            bootstrap_by_group.setdefault(key, []).append(float(forward_return))

        for idx, lane_row in enumerate(lane_rows):
            group_value = str(lane_row.get("group_value") or "Unknown")
            values = bootstrap_by_group.get(group_value, [])
            ci = _bootstrap_median_ci(
                values,
                iterations=bootstrap_iterations,
                confidence_level=bootstrap_confidence_level,
                seed=int(bootstrap_seed) + idx,
            )
            if ci is None:
                lane_row[f"median_fwd_{resolved_ranking_horizon}d_ci_low"] = None
                lane_row[f"median_fwd_{resolved_ranking_horizon}d_ci_high"] = None
                lane_row[f"median_fwd_{resolved_ranking_horizon}d_ci_width"] = None
            else:
                ci_low, ci_high = ci
                lane_row[f"median_fwd_{resolved_ranking_horizon}d_ci_low"] = round(
                    ci_low, 4
                )
                lane_row[f"median_fwd_{resolved_ranking_horizon}d_ci_high"] = round(
                    ci_high, 4
                )
                lane_row[f"median_fwd_{resolved_ranking_horizon}d_ci_width"] = round(
                    ci_high - ci_low,
                    4,
                )
            lane_row[
                f"median_fwd_{resolved_ranking_horizon}d_bootstrap_sample_count"
            ] = len(values)

        lane_by_group = {str(row["group_value"]): row for row in lane_rows}

        resolved_min_shortlist_count = int(max(1, min_shortlist_count))

        all_target_rows = _fetch_dict_rows(
            conn,
            (
                "SELECT state.*, hist.*, persistence.* EXCLUDE (symbol), stability.* EXCLUDE (symbol) "
                "FROM daily_state_enriched AS state "
                "LEFT JOIN hist_by_symbol AS hist ON hist.symbol = state.symbol "
                "LEFT JOIN persistence_by_symbol AS persistence ON persistence.symbol = state.symbol "
                "LEFT JOIN stability_by_symbol AS stability ON stability.symbol = state.symbol "
                f"WHERE state.source_date = {_q(resolved_target_date)}::DATE"
            ),
        )

        all_candidate_rows: list[dict[str, Any]] = []
        all_strict_rows: list[dict[str, Any]] = []
        all_expanded_candidate_rows: list[dict[str, Any]] = []
        screen_scope_strict_rows: list[dict[str, Any]] = []
        screen_scope_expanded_candidate_rows: list[dict[str, Any]] = []
        for row in all_target_rows:
            group_value = str(row.get(normalized_group_by) or "Unknown")
            lane_row = lane_by_group.get(group_value)
            if lane_row is None:
                lane_row = {
                    lane_horizon_sample_col: 0,
                    lane_horizon_median_col: 0.0,
                    lane_horizon_win_col: 0.0,
                }

            hist_occurrences = _safe_int(row.get("hist_occurrence_count_in_setup"))
            hist_win_rate = _safe_float(
                row.get(f"win_rate_{resolved_ranking_horizon}d_in_setup")
            )
            hist_median = _safe_float(
                row.get(f"median_fwd_{resolved_ranking_horizon}d_in_setup")
            )
            any_setup_rate = _safe_float(row.get("any_setup_rate"))
            pass_hist_occurrences = hist_occurrences >= int(
                max(0, min_hist_occurrences)
            )
            pass_hist_win_rate = hist_win_rate >= float(min_hist_win_rate)
            pass_hist_median = hist_median >= float(min_hist_median_fwd)
            pass_any_setup_rate = any_setup_rate >= float(min_any_setup_rate)
            strict_filter_pass_count = sum(
                (
                    int(pass_hist_occurrences),
                    int(pass_hist_win_rate),
                    int(pass_hist_median),
                    int(pass_any_setup_rate),
                )
            )
            strict_filter_fail_count = int(4 - strict_filter_pass_count)
            strict_filter_pass = strict_filter_fail_count == 0
            failed_threshold_tags: list[str] = []
            if not pass_hist_occurrences:
                failed_threshold_tags.append("hist_occurrences")
            if not pass_hist_win_rate:
                failed_threshold_tags.append("hist_win_rate")
            if not pass_hist_median:
                failed_threshold_tags.append("hist_median_fwd")
            if not pass_any_setup_rate:
                failed_threshold_tags.append("any_setup_rate")

            lane_median_norm = _clamp(
                _safe_float(lane_row.get(lane_horizon_median_col)) / 6.0
            )
            lane_win_norm = _clamp(_safe_float(lane_row.get(lane_horizon_win_col)))
            lane_sample_norm = _clamp(
                _safe_int(lane_row.get(lane_horizon_sample_col)) / 200.0
            )
            lane_context_score = round(
                (0.45 * lane_median_norm)
                + (0.35 * lane_win_norm)
                + (0.20 * lane_sample_norm),
                4,
            )

            composite_score = _safe_float(row.get("composite_score_day"))
            adrp_pct = _safe_float(row.get("adrp_directional_universe_percentile"))
            relvol_pct = _safe_float(
                row.get("relative_volume_directional_universe_percentile")
            )
            value_traded_pct = _safe_float(
                row.get("value_traded_directional_universe_percentile")
            )
            vol_core_pct = _safe_float(
                row.get("volatility_core_directional_universe_percentile")
            )
            liq_core_pct = _safe_float(
                row.get("liquidity_core_directional_universe_percentile")
            )
            mom_core_pct = _safe_float(
                row.get("momentum_context_directional_universe_percentile")
            )
            stability_score = _safe_float(row.get("stability_score"))
            persistence_norm = _clamp(any_setup_rate / 0.20)
            hist_median_norm = _clamp(hist_median / 8.0)
            hist_sample_depth_norm = _clamp(hist_occurrences / 10.0)
            current_lane_flag = _safe_int(row.get(chosen_setup_flag))
            screen_scope_pass = (
                composite_score >= float(screen_min_composite)
                or _safe_int(row.get("in_any_setup")) == 1
            )

            big_mover_score = _clamp(
                (0.35 * composite_score)
                + (0.15 * adrp_pct)
                + (0.15 * relvol_pct)
                + (0.10 * value_traded_pct)
                + (0.10 * vol_core_pct)
                + (0.05 * liq_core_pct)
                + (0.05 * mom_core_pct)
                + (0.05 * lane_context_score)
                + (0.03 if current_lane_flag else 0.0)
            )
            confidence_score = _clamp(
                (0.22 * composite_score)
                + (0.23 * stability_score)
                + (0.18 * persistence_norm)
                + (0.15 * hist_win_rate)
                + (0.10 * hist_median_norm)
                + (0.07 * hist_sample_depth_norm)
                + (0.05 * lane_context_score)
            )
            expanded_capture_score = _clamp(
                (0.50 * big_mover_score)
                + (0.20 * confidence_score)
                + (0.10 * lane_context_score)
                + (0.10 * _clamp(relvol_pct))
                + (0.10 * _clamp(value_traded_pct))
                - (0.06 * float(strict_filter_fail_count))
            )

            candidate_row = {
                "symbol": row.get("symbol"),
                "bare_ticker": row.get("bare_ticker"),
                "company_name": row.get("company_name"),
                "exchange": row.get("exchange"),
                "country": row.get("country"),
                "sector": row.get("sector"),
                "industry": row.get("industry"),
                "source_date": resolved_target_date,
                "lane_setup_name": lane_setup_name,
                "lane_group_by": normalized_group_by,
                "lane_group_value": group_value,
                "current_lane_flag": current_lane_flag,
                "screen_scope_pass": int(screen_scope_pass),
                "strict_filter_pass": int(strict_filter_pass),
                "strict_filter_pass_count": int(strict_filter_pass_count),
                "strict_filter_fail_count": int(strict_filter_fail_count),
                "strict_filter_failed_tags": "|".join(failed_threshold_tags),
                "shortlist_source_tier": "strict" if strict_filter_pass else "expanded",
                "big_mover_score": round(big_mover_score, 4),
                "confidence_score": round(confidence_score, 4),
                "expanded_capture_score": round(expanded_capture_score, 4),
                "stability_score": round(stability_score, 4),
                "short_setup_rate": round(_safe_float(row.get("short_setup_rate")), 4),
                "long_setup_rate": round(_safe_float(row.get("long_setup_rate")), 4),
                "short_composite_avg": round(
                    _safe_float(row.get("short_composite_avg")), 4
                ),
                "long_composite_avg": round(
                    _safe_float(row.get("long_composite_avg")), 4
                ),
                "short_composite_stddev": round(
                    _safe_float(row.get("short_composite_stddev")), 4
                ),
                "long_composite_stddev": round(
                    _safe_float(row.get("long_composite_stddev")), 4
                ),
                "composite_score": round(composite_score, 4),
                "adrp_raw": round(_safe_float(row.get("adrp_raw")), 6),
                "atrp_raw": round(_safe_float(row.get("atrp_raw")), 6),
                "volatility_month_raw": round(
                    _safe_float(row.get("volatility_month_raw")), 6
                ),
                "relvol_raw": round(_safe_float(row.get("relvol_raw")), 6),
                "volume_raw": round(_safe_float(row.get("volume_raw")), 6),
                "adrp_pct_today": round(adrp_pct, 4),
                "relvol_pct_today": round(relvol_pct, 4),
                "value_traded_pct_today": round(value_traded_pct, 4),
                "vol_core_pct_today": round(vol_core_pct, 4),
                "liq_core_pct_today": round(liq_core_pct, 4),
                "mom_core_pct_today": round(mom_core_pct, 4),
                "any_setup_days": _safe_int(row.get("any_setup_days")),
                "any_setup_rate": round(any_setup_rate, 4),
                "hist_occurrence_count_in_setup": hist_occurrences,
                f"median_fwd_{resolved_ranking_horizon}d_in_setup": round(
                    hist_median, 4
                ),
                f"win_rate_{resolved_ranking_horizon}d_in_setup": round(
                    hist_win_rate, 4
                ),
                f"target_rate_{resolved_ranking_horizon}d_in_setup": round(
                    _safe_float(
                        row.get(f"target_rate_{resolved_ranking_horizon}d_in_setup")
                    ),
                    4,
                ),
                f"median_mfe_{resolved_ranking_horizon}d_in_setup": round(
                    _safe_float(
                        row.get(f"median_mfe_{resolved_ranking_horizon}d_in_setup")
                    ),
                    4,
                ),
                f"median_mae_{resolved_ranking_horizon}d_in_setup": round(
                    _safe_float(
                        row.get(f"median_mae_{resolved_ranking_horizon}d_in_setup")
                    ),
                    4,
                ),
                f"lane_sample_count_{resolved_ranking_horizon}d": _safe_int(
                    lane_row.get(lane_horizon_sample_col)
                ),
                f"lane_median_fwd_{resolved_ranking_horizon}d": round(
                    _safe_float(lane_row.get(lane_horizon_median_col)), 4
                ),
                f"lane_win_rate_{resolved_ranking_horizon}d": round(
                    _safe_float(lane_row.get(lane_horizon_win_col)), 4
                ),
                "lane_context_score": lane_context_score,
            }
            candidate_row = _normalize_symbol_field(candidate_row)
            all_candidate_rows.append(candidate_row)

            if strict_filter_pass:
                all_strict_rows.append(candidate_row)
                if screen_scope_pass:
                    screen_scope_strict_rows.append(candidate_row)
            else:
                all_expanded_candidate_rows.append(candidate_row)
                if screen_scope_pass:
                    screen_scope_expanded_candidate_rows.append(candidate_row)

        screen_scope_row_count = len(screen_scope_strict_rows) + len(
            screen_scope_expanded_candidate_rows
        )
        if screen_scope_row_count >= resolved_min_shortlist_count:
            strict_rows = list(screen_scope_strict_rows)
            expanded_candidate_rows = list(screen_scope_expanded_candidate_rows)
        else:
            strict_rows = list(all_strict_rows)
            expanded_candidate_rows = list(all_expanded_candidate_rows)

        expanded_added_rows: list[dict[str, Any]] = []
        if len(strict_rows) >= resolved_min_shortlist_count:
            shortlist_rows = list(strict_rows)
        else:
            expanded_sorted = sorted(
                expanded_candidate_rows,
                key=lambda candidate: (
                    _safe_float(candidate.get("expanded_capture_score")),
                    _safe_float(candidate.get("big_mover_score")),
                    _safe_float(candidate.get("confidence_score")),
                    _safe_float(candidate.get("composite_score")),
                ),
                reverse=True,
            )
            needed = max(0, resolved_min_shortlist_count - len(strict_rows))
            expanded_added_rows = expanded_sorted[:needed]
            shortlist_rows = list(strict_rows) + expanded_added_rows

        if not shortlist_rows:
            fallback_rows = sorted(
                expanded_candidate_rows,
                key=lambda candidate: (
                    _safe_float(candidate.get("expanded_capture_score")),
                    _safe_float(candidate.get("big_mover_score")),
                    _safe_float(candidate.get("confidence_score")),
                ),
                reverse=True,
            )[:resolved_min_shortlist_count]
            shortlist_rows = fallback_rows
            expanded_added_rows = fallback_rows

        big_sorted = sorted(
            shortlist_rows,
            key=lambda row: (
                _safe_int(row.get("strict_filter_pass")),
                _safe_float(row.get("big_mover_score")),
                _safe_float(row.get("expanded_capture_score")),
                _safe_float(row.get("confidence_score")),
                _safe_float(row.get("composite_score")),
            ),
            reverse=True,
        )
        for index, row in enumerate(big_sorted, start=1):
            row["big_mover_rank"] = index

        confidence_sorted = sorted(
            shortlist_rows,
            key=lambda row: (
                _safe_int(row.get("strict_filter_pass")),
                _safe_float(row.get("confidence_score")),
                _safe_float(row.get("stability_score")),
                _safe_float(row.get("expanded_capture_score")),
                _safe_float(row.get("composite_score")),
            ),
            reverse=True,
        )
        confidence_rank_by_symbol = {
            str(row.get("symbol")): index
            for index, row in enumerate(confidence_sorted, start=1)
        }
        for row in big_sorted:
            row["confidence_rank"] = confidence_rank_by_symbol.get(
                str(row.get("symbol")),
                0,
            )

        top30_rows = big_sorted[: int(max(1, shortlist_top_n))]
        top10_rows = confidence_sorted[: int(max(1, top10_count))]

        universe_rows = sorted(
            all_candidate_rows,
            key=lambda row: (
                _safe_int(row.get("strict_filter_pass")),
                _safe_float(row.get("big_mover_score")),
                _safe_float(row.get("expanded_capture_score")),
                _safe_float(row.get("confidence_score")),
                _safe_float(row.get("composite_score")),
            ),
            reverse=True,
        )

        for row in shortlist_rows:
            row.update(
                compute_upside_prediction_fields(
                    row,
                    ranking_horizon=resolved_ranking_horizon,
                )
            )
        upside_sorted = sort_rows_by_upside_prediction(shortlist_rows)
        upside_top_rows = [
            project_upside_prediction_row(
                row,
                ranking_horizon=resolved_ranking_horizon,
                include_safety=False,
            )
            for row in upside_sorted[:resolved_upside_top_count]
        ]
        upside_ranked_rows = [
            project_upside_prediction_row(
                row,
                ranking_horizon=resolved_ranking_horizon,
                include_safety=False,
            )
            for row in upside_sorted
        ]

        _write_csv(lane_leaders_path, lane_rows)
        _write_csv(universe_path, universe_rows)
        _write_csv(shortlist_path, big_sorted)
        _write_csv(top30_path, top30_rows)
        _write_csv(top10_path, top10_rows)
        _write_csv(upside_ranked_path, upside_ranked_rows)
        _write_csv(upside_top_path, upside_top_rows)

        _write_highlights_report(
            report_md,
            snapshot_database_path=resolved_snapshot_database_path,
            resolved_target_date=resolved_target_date,
            ranking_horizon=resolved_ranking_horizon,
            lane_setup_name=lane_setup_name,
            group_by=normalized_group_by,
            markets=resolved_markets,
            countries=resolved_countries,
            exchanges=resolved_exchanges,
            us_only=bool(us_only),
            screen_min_composite=screen_min_composite,
            lane_min_sample_count=lane_min_sample_count,
            lane_min_win_rate=lane_min_win_rate,
            lane_min_median_fwd=lane_min_median_fwd,
            min_any_setup_rate=min_any_setup_rate,
            min_hist_occurrences=min_hist_occurrences,
            min_hist_win_rate=min_hist_win_rate,
            min_hist_median_fwd=min_hist_median_fwd,
            stability_short_lookback=stability_short_lookback,
            stability_long_lookback=stability_long_lookback,
            bootstrap_iterations=int(max(50, bootstrap_iterations)),
            bootstrap_confidence_level=float(bootstrap_confidence_level),
            lane_leaders_path=lane_leaders_path,
            universe_path=universe_path,
            shortlist_path=shortlist_path,
            top30_path=top30_path,
            top10_path=top10_path,
            upside_ranked_path=upside_ranked_path,
            upside_top_path=upside_top_path,
            lane_row_count=len(lane_rows),
            universe_row_count=len(universe_rows),
            shortlist_row_count=len(big_sorted),
            strict_shortlist_row_count=len(strict_rows),
            expanded_shortlist_row_count=len(expanded_candidate_rows),
            expanded_added_row_count=len(
                [
                    row
                    for row in big_sorted
                    if _safe_int(row.get("strict_filter_pass")) == 0
                ]
            ),
            min_shortlist_count=resolved_min_shortlist_count,
            top30_row_count=len(top30_rows),
            top10_row_count=len(top10_rows),
            upside_ranked_row_count=len(upside_ranked_rows),
            upside_top_row_count=len(upside_top_rows),
            upside_top_count=resolved_upside_top_count,
            filter_args=filter_args,
        )

        manifest = {
            "run_id": context.run_id,
            "run_created_at_utc": context.created_at_utc.isoformat(),
            "command": "highlights",
            "snapshot_database_path": resolved_snapshot_database_path.as_posix(),
            "target_date": resolved_target_date,
            "ranking_horizon": resolved_ranking_horizon,
            "lane_setup_name": lane_setup_name,
            "group_by": normalized_group_by,
            "markets": resolved_markets,
            "countries": resolved_countries,
            "exchanges": resolved_exchanges,
            "us_only": us_only,
            "screen_min_composite": screen_min_composite,
            "lane_min_sample_count": lane_min_sample_count,
            "lane_min_win_rate": lane_min_win_rate,
            "lane_min_median_fwd": lane_min_median_fwd,
            "min_any_setup_rate": min_any_setup_rate,
            "min_hist_occurrences": min_hist_occurrences,
            "min_hist_win_rate": min_hist_win_rate,
            "min_hist_median_fwd": min_hist_median_fwd,
            "min_shortlist_count": resolved_min_shortlist_count,
            "shortlist_top_n": shortlist_top_n,
            "top10_count": top10_count,
            "upside_top_count": resolved_upside_top_count,
            "stability_short_lookback": stability_short_lookback,
            "stability_long_lookback": stability_long_lookback,
            "bootstrap_iterations": int(max(50, bootstrap_iterations)),
            "bootstrap_confidence_level": float(bootstrap_confidence_level),
            "bootstrap_seed": int(bootstrap_seed),
            "lane_row_count": len(lane_rows),
            "universe_row_count": len(universe_rows),
            "shortlist_row_count": len(big_sorted),
            "strict_shortlist_row_count": len(strict_rows),
            "expanded_shortlist_row_count": len(expanded_candidate_rows),
            "expanded_added_row_count": len(expanded_added_rows),
            "top30_row_count": len(top30_rows),
            "top10_row_count": len(top10_rows),
            "upside_ranked_row_count": len(upside_ranked_rows),
            "upside_top_row_count": len(upside_top_rows),
            "output_dir": context.output_dir.as_posix(),
            "lane_leaders_csv": lane_leaders_path.as_posix(),
            "universe_csv": universe_path.as_posix(),
            "shortlist_csv": shortlist_path.as_posix(),
            "top30_csv": top30_path.as_posix(),
            "top10_csv": top10_path.as_posix(),
            "upside_ranked_csv": upside_ranked_path.as_posix(),
            "upside_top_csv": upside_top_path.as_posix(),
            "report_md": report_md.as_posix(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        return {
            "output_dir": context.output_dir,
            "lane_leaders_csv": lane_leaders_path,
            "universe_csv": universe_path,
            "shortlist_csv": shortlist_path,
            "top30_csv": top30_path,
            "top10_csv": top10_path,
            "upside_ranked_csv": upside_ranked_path,
            "upside_top_csv": upside_top_path,
            "report_md": report_md,
            "manifest_path": manifest_path,
            "target_date": resolved_target_date,
            "ranking_horizon": resolved_ranking_horizon,
            "lane_row_count": len(lane_rows),
            "universe_row_count": len(universe_rows),
            "shortlist_row_count": len(big_sorted),
            "strict_shortlist_row_count": len(strict_rows),
            "expanded_shortlist_row_count": len(expanded_candidate_rows),
            "expanded_added_row_count": len(expanded_added_rows),
            "min_shortlist_count": resolved_min_shortlist_count,
            "top30_row_count": len(top30_rows),
            "top10_row_count": len(top10_rows),
            "upside_ranked_row_count": len(upside_ranked_rows),
            "upside_top_row_count": len(upside_top_rows),
            "upside_top_count": resolved_upside_top_count,
            "snapshot_database_path": resolved_snapshot_database_path,
        }
    finally:
        conn.close()
