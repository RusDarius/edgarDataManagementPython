from __future__ import annotations

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
            normalized.append(stripped.upper())
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


def _expand_us_aliases(countries: Sequence[str], us_only: bool) -> tuple[str, ...]:
    resolved = list(countries)
    if us_only:
        for alias in ("US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"):
            if alias not in resolved:
                resolved.append(alias)
    return tuple(resolved)


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


def _build_date_filter_sql(
    *,
    date_expr: str,
    start_date: str | None,
    end_date: str | None,
) -> str:
    filters: list[str] = []
    if start_date:
        filters.append(f"{date_expr} >= {_q(start_date)}::DATE")
    if end_date:
        filters.append(f"{date_expr} <= {_q(end_date)}::DATE")
    return " AND ".join(filters) if filters else "1 = 1"


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


def _discover_horizons(connection: Any) -> tuple[int, ...]:
    pattern = re.compile(r"^forward_return_(\d+)d_pct$")
    columns = [
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info('symbol_day_forward_labels')"
        ).fetchall()
    ]
    return tuple(
        sorted({int(m.group(1)) for col in columns if (m := pattern.match(col))})
    )


def _variant_conditions_fpsp(v: VolatilityLiquiditySetupVariant) -> str:
    """Conditions using fp/sp CTE qualifiers for use inside daily_flags CTE SELECT."""
    return (
        f"COALESCE(fp.adrp_directional_universe_percentile, 0) >= {v.min_adrp_pct}"
        f" AND COALESCE(fp.relative_volume_directional_universe_percentile, 0) >= {v.min_relative_volume_pct}"
        f" AND COALESCE(fp.value_traded_directional_universe_percentile, 0) >= {v.min_value_traded_pct}"
        f" AND COALESCE(sp.liquidity_core_directional_universe_percentile, 0) >= {v.min_liquidity_core_pct}"
        f" AND COALESCE(sp.momentum_context_directional_universe_percentile, 0) >= {v.min_momentum_context_pct}"
        f" AND COALESCE(sp.volatility_core_directional_universe_percentile, 0) >= {v.min_volatility_core_pct}"
    )


def _build_daily_flags_cte(
    variants: Sequence[VolatilityLiquiditySetupVariant],
    *,
    market_filter_sql: str,
    countries: Sequence[str] = (),
    exchanges: Sequence[str] = (),
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Build the full daily_flags temp table SQL (fp + sp + snapshot join with variant flag columns)."""
    region_filter_sql = _build_region_filter_sql(
        country_expr="snap.country",
        exchange_expr="snap.exchange",
        countries=countries,
        exchanges=exchanges,
    )
    date_filter_sql = _build_date_filter_sql(
        date_expr="snap.source_date",
        start_date=start_date,
        end_date=end_date,
    )
    variant_flag_selects = ",\n                ".join(
        f"CASE WHEN {_variant_conditions_fpsp(v)} THEN 1 ELSE 0 END AS in_{v.name}"
        for v in variants
    )
    return f"""
        CREATE OR REPLACE TEMP TABLE daily_flags AS
        WITH fp AS (
            SELECT source_day_label, symbol,
                MAX(CASE WHEN field_name = 'ADRP' THEN directional_universe_percentile END)
                    AS adrp_directional_universe_percentile,
                MAX(CASE WHEN field_name = 'relative_volume_10d_calc' THEN directional_universe_percentile END)
                    AS relative_volume_directional_universe_percentile,
                MAX(CASE WHEN field_name = 'Value.Traded' THEN directional_universe_percentile END)
                    AS value_traded_directional_universe_percentile
            FROM symbol_day_feature_values
            GROUP BY source_day_label, symbol
        ),
        sp AS (
            SELECT source_day_label, symbol,
                MAX(CASE WHEN sleeve_name = 'volatility_core' THEN avg_directional_universe_percentile END)
                    AS volatility_core_directional_universe_percentile,
                MAX(CASE WHEN sleeve_name = 'liquidity_core' THEN avg_directional_universe_percentile END)
                    AS liquidity_core_directional_universe_percentile,
                MAX(CASE WHEN sleeve_name = 'momentum_context' THEN avg_directional_universe_percentile END)
                    AS momentum_context_directional_universe_percentile
            FROM symbol_day_sleeve_scores
            GROUP BY source_day_label, symbol
        )
        SELECT
            snap.symbol, snap.bare_ticker, snap.company_name,
            snap.exchange, snap.country, snap.sector, snap.industry,
            snap.source_day_label, snap.source_date,
            fp.adrp_directional_universe_percentile,
            fp.relative_volume_directional_universe_percentile,
            sp.volatility_core_directional_universe_percentile,
            sp.liquidity_core_directional_universe_percentile,
            {variant_flag_selects}
        FROM symbol_day_feature_snapshot AS snap
        LEFT JOIN fp
            ON fp.source_day_label = snap.source_day_label
            AND fp.symbol = snap.symbol
        LEFT JOIN sp
            ON sp.source_day_label = snap.source_day_label
            AND sp.symbol = snap.symbol
                WHERE {region_filter_sql}
                    AND {market_filter_sql}
                    AND {date_filter_sql}
        """


def _in_setup_any(
    variants: Sequence[VolatilityLiquiditySetupVariant], qual: str = "df"
) -> str:
    q = f"{qual}." if qual else ""
    return " OR ".join(f"{q}in_{v.name} = 1" for v in variants)


def _perf_agg_cols(horizons: Sequence[int], suffix: str = "") -> str:
    parts = []
    for h in horizons:
        col = f"forward_return_{h}d_pct"
        mfe = f"max_favorable_excursion_{h}d_pct"
        mae = f"max_adverse_excursion_{h}d_pct"
        flag = f"target_before_stop_{h}d_flag"
        parts += [
            f"COUNT({col}) AS sample_count_{h}d{suffix}",
            f"MEDIAN({col}) AS median_fwd_{h}d{suffix}",
            f"AVG({col}) AS avg_fwd_{h}d{suffix}",
            f"AVG(CASE WHEN {col} IS NULL THEN NULL WHEN {col} > 0 THEN 1.0 ELSE 0.0 END) AS win_rate_{h}d{suffix}",
            f"MEDIAN({mfe}) AS median_mfe_{h}d{suffix}",
            f"MEDIAN({mae}) AS median_mae_{h}d{suffix}",
            f"AVG(CASE WHEN {flag} IS NULL THEN NULL WHEN {flag} THEN 1.0 ELSE 0.0 END) AS target_rate_{h}d{suffix}",
        ]
    return ",\n            ".join(parts)


# ---------------------------------------------------------------------------
# Persistence scan
# ---------------------------------------------------------------------------


def _write_persistence_report(
    path: Path,
    *,
    snapshot_database_path: Path,
    total_days: int,
    min_setup_days: int,
    top_n: int,
    ranking_horizon: int,
    horizons: tuple[int, ...],
    row_count: int,
    persistence_csv: Path,
    variants: Sequence[VolatilityLiquiditySetupVariant],
    markets: Sequence[str],
    countries: Sequence[str],
    exchanges: Sequence[str],
    us_only: bool,
    start_date: str | None,
    end_date: str | None,
    filter_args: str,
) -> None:
    db = snapshot_database_path.as_posix()
    lines = [
        "# Edge Persistence Scan",
        "",
        "## Why Run This",
        "",
        f"Measures how consistently each symbol appeared in setup conditions across the {total_days}-day analysis window.",
        "Symbols with high `any_setup_rate` have structural rather than one-off setup presence.",
        "Combining persistence rate with in-setup forward-return quality identifies names with durable edge.",
        "",
        "## How To Rerun",
        "",
        "Git bash:",
        "```bash",
        f"PYTHONPATH='src:.' python -m run_edge_research_tools scan-persistence \\",
        f'  --snapshot-db "{db}" \\',
        f"  --min-setup-days {min_setup_days} --top-n {top_n} --ranking-horizon {ranking_horizon}{filter_args}",
        "```",
        "",
        "Relax to show any symbol with 3+ setup days:",
        "```bash",
        f"PYTHONPATH='src:.' python -m run_edge_research_tools scan-persistence --snapshot-db \"{db}\" --min-setup-days 3 --top-n 200{filter_args}",
        "```",
        "",
        "PowerShell:",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        f'python -m run_edge_research_tools scan-persistence --snapshot-db "{db}" --min-setup-days {min_setup_days} --top-n {top_n}{filter_args}',
        "```",
        "",
        "## Output Column Guide",
        "",
        "| Column | Meaning |",
        "|--------|---------|",
        "| persistence_rank | Rank by any_setup_rate descending |",
        "| any_setup_days | Days where symbol was in any of the three setup variants |",
        f"| any_setup_rate | any_setup_days / {total_days} total days in dataset |",
        "| adrp_relvol_core_days / _rate | Days in the strictest variant |",
        "| adrp_relvol_momentum_days / _rate | Days in the momentum-confirmation variant |",
        "| volatility_liquidity_balanced_days / _rate | Days in the balanced sleeve variant |",
        "| avg_adrp_pct | Mean ADRP directional percentile across ALL days (not just setup days) |",
        "| avg_relvol_pct | Mean relative volume percentile across all days |",
        "| avg_vol_core_pct | Mean volatility core sleeve percentile across all days |",
        "| avg_liq_core_pct | Mean liquidity core sleeve percentile across all days |",
        "| hist_occurrence_count_in_setup | # past days symbol was in any setup with a valid label |",
        "| median_fwd_Nd_in_setup | Median forward return (%) across in-setup days |",
        "| win_rate_Nd_in_setup | Win rate (>0 return) across in-setup days |",
        "| target_rate_Nd_in_setup | Target-before-stop rate across in-setup days |",
        "",
        "## Output Files",
        "",
        f"- Persistence scan CSV: `{persistence_csv.as_posix()}`",
        f"- This report: `{path.as_posix()}`",
        "",
        "## Scope",
        "",
        f"- snapshot_database_path: `{db}`",
        f"- total_days_in_dataset: {total_days}",
        f"- start_date: {start_date or 'none'}",
        f"- end_date: {end_date or 'none'}",
        f"- markets: {', '.join(markets) if markets else 'all'}",
        f"- countries: {', '.join(countries) if countries else 'all'}",
        f"- exchanges: {', '.join(exchanges) if exchanges else 'all'}",
        f"- us_only: {us_only}",
        f"- min_setup_days_filter: {min_setup_days}",
        f"- symbols_shown: {row_count} (top {top_n} by any_setup_rate)",
        f"- ranking_horizon: {ranking_horizon}d",
        f"- horizons: {', '.join(str(h) for h in horizons)}",
        "",
        "## Setup Variant Thresholds",
        "",
    ]
    for v in variants:
        lines.append(
            f"- **{v.name}**: ADRP≥{v.min_adrp_pct:.0%} | RelVol≥{v.min_relative_volume_pct:.0%}"
            f" | ValTraded≥{v.min_value_traded_pct:.0%} | VolSleeve≥{v.min_volatility_core_pct:.0%}"
            f" | LiqSleeve≥{v.min_liquidity_core_pct:.0%} | MomSleeve≥{v.min_momentum_context_pct:.0%}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_persistence_scan(
    *,
    snapshot_database_path: str | Path,
    min_setup_days: int = 5,
    top_n: int = 100,
    ranking_horizon: int | None = None,
    markets: Iterable[str] | None = None,
    countries: Iterable[str] | None = None,
    exchanges: Iterable[str] | None = None,
    us_only: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
    variants: Sequence[
        VolatilityLiquiditySetupVariant
    ] = DEFAULT_VOLATILITY_LIQUIDITY_SETUP_VARIANTS,
) -> dict[str, Any]:
    """Cross-day persistence scan: which symbols appeared in setup conditions the most days?

    Aggregates all 63 (or N) days in the snapshot dataset, flags each day per variant,
    counts how often each symbol triggered, and joins historical in-setup forward returns.
    """
    resolved_db = Path(snapshot_database_path)
    if not resolved_db.exists():
        raise FileNotFoundError(f"Snapshot database not found: {resolved_db}")

    output_paths = resolve_edge_research_paths(output_root=output_root)
    context = build_edge_research_run_context(
        prefix="edge_persistence",
        output_root=output_paths.output_root,
    )
    persistence_csv = context.output_dir / "edge_persistence_ranked.csv"
    report_md = context.output_dir / "edge_persistence_report.md"
    manifest_path = context.output_dir / "edge_persistence_manifest.json"

    duckdb_mod = _import_duckdb()
    conn = duckdb_mod.connect(resolved_db.as_posix(), read_only=False)
    conn.execute(f"SET threads TO {int(duckdb_threads)}")
    try:
        for t in (
            "symbol_day_feature_values",
            "symbol_day_sleeve_scores",
            "symbol_day_forward_labels",
        ):
            if not _table_exists(conn, t):
                raise ValueError(
                    f"Required table '{t}' not found. Run the full suite first."
                )

        horizons = _discover_horizons(conn)
        rh = ranking_horizon if ranking_horizon is not None else max(horizons)
        resolved_markets = _normalize_market_list(markets)
        resolved_countries = _expand_us_aliases(
            _normalize_string_list(countries),
            bool(us_only),
        )
        resolved_exchanges = _normalize_string_list(exchanges)
        filter_args = _format_scope_args(
            markets=resolved_markets,
            countries=resolved_countries,
            exchanges=resolved_exchanges,
            us_only=bool(us_only),
        )
        market_filter_sql, _market_filter_applied = resolve_snapshot_market_filter(
            conn,
            alias="snap",
            markets=resolved_markets,
        )

        # Step 1: Build daily_flags temp table (one row per symbol-day with variant flags)
        conn.execute(
            _build_daily_flags_cte(
                variants,
                market_filter_sql=market_filter_sql,
                countries=resolved_countries,
                exchanges=resolved_exchanges,
                start_date=start_date,
                end_date=end_date,
            )
        )
        total_days = int(
            conn.execute(
                "SELECT COUNT(DISTINCT source_date) FROM daily_flags"
            ).fetchone()[0]
        )
        if total_days <= 0:
            raise ValueError(
                "No rows matched the selected region/date filters in daily_flags."
            )

        # Step 2: In-setup performance (forward labels for days when in any setup)
        any_setup_df = _in_setup_any(variants, qual="df")
        perf_cols = _perf_agg_cols(horizons, suffix="_in_setup")
        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE in_setup_perf AS
            SELECT df.symbol, lbl.*
            FROM daily_flags AS df
            JOIN symbol_day_forward_labels AS lbl
                ON lbl.source_day_label = df.source_day_label
                AND lbl.symbol = df.symbol
            WHERE {any_setup_df}
            """)

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE hist_by_symbol AS
            SELECT
                symbol,
                COUNT(*) AS hist_occurrence_count_in_setup,
                {perf_cols}
            FROM in_setup_perf
            GROUP BY symbol
            """)

        # Step 3: Symbol-level aggregation of setup day counts and average percentiles
        variant_day_counts = ",\n                ".join(
            f"SUM(df.in_{v.name}) AS {v.name}_days,\n                "
            f"ROUND(SUM(df.in_{v.name}) * 1.0 / {int(total_days)}, 4) AS {v.name}_rate"
            for v in variants
        )
        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE symbol_persistence AS
            SELECT
                df.symbol, df.bare_ticker, df.company_name,
                df.exchange, df.country, df.sector, df.industry,
                {int(total_days)} AS total_dataset_days,
                SUM(CASE WHEN {any_setup_df} THEN 1 ELSE 0 END) AS any_setup_days,
                ROUND(SUM(CASE WHEN {any_setup_df} THEN 1 ELSE 0 END) * 1.0 / {int(total_days)}, 4) AS any_setup_rate,
                {variant_day_counts},
                ROUND(AVG(df.adrp_directional_universe_percentile), 4) AS avg_adrp_pct,
                ROUND(AVG(df.relative_volume_directional_universe_percentile), 4) AS avg_relvol_pct,
                ROUND(AVG(df.volatility_core_directional_universe_percentile), 4) AS avg_vol_core_pct,
                ROUND(AVG(df.liquidity_core_directional_universe_percentile), 4) AS avg_liq_core_pct
            FROM daily_flags AS df
            GROUP BY df.symbol, df.bare_ticker, df.company_name, df.exchange, df.country, df.sector, df.industry
            """)

        # Step 4: Final result with performance join
        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE persistence_result AS
            SELECT
                ROW_NUMBER() OVER (
                    ORDER BY sa.any_setup_rate DESC,
                        sa.avg_adrp_pct DESC NULLS LAST
                ) AS persistence_rank,
                sa.*,
                h.* EXCLUDE (symbol)
            FROM symbol_persistence AS sa
            LEFT JOIN hist_by_symbol AS h ON h.symbol = sa.symbol
            WHERE sa.any_setup_days >= {int(max(1, min_setup_days))}
            ORDER BY sa.any_setup_rate DESC
            LIMIT {int(top_n)}
            """)

        row_count = int(
            conn.execute("SELECT COUNT(*) FROM persistence_result").fetchone()[0]
        )
        conn.execute(
            f"COPY (SELECT * FROM persistence_result) TO {_q(persistence_csv.as_posix())} (HEADER, DELIMITER ',')"
        )

        _write_persistence_report(
            report_md,
            snapshot_database_path=resolved_db,
            total_days=total_days,
            min_setup_days=min_setup_days,
            top_n=top_n,
            ranking_horizon=rh,
            horizons=horizons,
            row_count=row_count,
            persistence_csv=persistence_csv,
            variants=list(variants),
            markets=resolved_markets,
            countries=resolved_countries,
            exchanges=resolved_exchanges,
            us_only=bool(us_only),
            start_date=start_date,
            end_date=end_date,
            filter_args=filter_args,
        )

        manifest = {
            "run_id": context.run_id,
            "run_created_at_utc": context.created_at_utc.isoformat(),
            "command": "scan-persistence",
            "snapshot_database_path": resolved_db.as_posix(),
            "total_days": total_days,
            "start_date": start_date,
            "end_date": end_date,
            "markets": list(resolved_markets),
            "countries": list(resolved_countries),
            "exchanges": list(resolved_exchanges),
            "us_only": bool(us_only),
            "min_setup_days": min_setup_days,
            "top_n": top_n,
            "ranking_horizon": rh,
            "horizons": list(horizons),
            "row_count": row_count,
            "output_dir": context.output_dir.as_posix(),
            "persistence_csv": persistence_csv.as_posix(),
            "report_md": report_md.as_posix(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        return {
            "output_dir": context.output_dir,
            "persistence_csv": persistence_csv,
            "report_md": report_md,
            "manifest_path": manifest_path,
            "total_days": total_days,
            "row_count": row_count,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Edge summary (forward-return edge grouped by sector or industry)
# ---------------------------------------------------------------------------


def _edge_summary_metric_cols(horizons: Sequence[int]) -> str:
    parts = []
    for h in horizons:
        col = f"forward_return_{h}d_pct"
        mfe = f"max_favorable_excursion_{h}d_pct"
        mae = f"max_adverse_excursion_{h}d_pct"
        flag = f"target_before_stop_{h}d_flag"
        parts += [
            f"COUNT({col}) AS sample_count_{h}d",
            f"MEDIAN({col}) AS median_fwd_{h}d",
            f"AVG({col}) AS avg_fwd_{h}d",
            f"AVG(CASE WHEN {col} IS NULL THEN NULL WHEN {col} > 0 THEN 1.0 ELSE 0.0 END) AS win_rate_{h}d",
            f"MEDIAN({mfe}) AS median_mfe_{h}d",
            f"MEDIAN({mae}) AS median_mae_{h}d",
            f"AVG(CASE WHEN {flag} IS NULL THEN NULL WHEN {flag} THEN 1.0 ELSE 0.0 END) AS target_rate_{h}d",
        ]
    return ",\n            ".join(parts)


def _write_edge_summary_report(
    path: Path,
    *,
    snapshot_database_path: Path,
    group_by: str,
    ranking_horizon: int,
    horizons: tuple[int, ...],
    row_count: int,
    summary_csv: Path,
    variants: Sequence[VolatilityLiquiditySetupVariant],
    markets: Sequence[str],
    countries: Sequence[str],
    exchanges: Sequence[str],
    us_only: bool,
    start_date: str | None,
    end_date: str | None,
    bootstrap_iterations: int,
    bootstrap_confidence_level: float,
    filter_args: str,
) -> None:
    db = snapshot_database_path.as_posix()
    lines = [
        f"# Edge Summary — By {group_by.title()}",
        "",
        "## Why Run This",
        "",
        f"Shows where setup edge is concentrated when grouped by {group_by}.",
        "Groups with high win rates and positive median forward returns identify the best research lanes.",
        "Cross-reference with the persistence scan to find groups where multiple strong names converge.",
        "",
        "## How To Rerun",
        "",
        "Git bash:",
        "```bash",
        f"PYTHONPATH='src:.' python -m run_edge_research_tools scan-edge \\",
        f'  --snapshot-db "{db}" \\',
        f"  --group-by {group_by} --ranking-horizon {ranking_horizon}{filter_args}",
        "```",
        "",
        "By sector:",
        "```bash",
        f"PYTHONPATH='src:.' python -m run_edge_research_tools scan-edge --snapshot-db \"{db}\" --group-by sector --ranking-horizon {ranking_horizon}{filter_args}",
        "```",
        "",
        "PowerShell:",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        f'python -m run_edge_research_tools scan-edge --snapshot-db "{db}" --group-by {group_by} --ranking-horizon {ranking_horizon}{filter_args}',
        "```",
        "",
        "## Output Column Guide",
        "",
        "| Column | Meaning |",
        "|--------|---------|",
        f"| {group_by} | Group name |",
        "| setup_name | Which setup variant (adrp_relvol_core, adrp_relvol_momentum, volatility_liquidity_balanced) |",
        "| occurrence_count | Setup symbol-days in this group |",
        "| distinct_symbol_count | Distinct symbols that triggered setup in this group |",
        "| median_fwd_Nd | Median forward return (%) for this group + variant across all occurrence dates |",
        "| win_rate_Nd | Win rate (>0 return) for this group + variant |",
        "| target_rate_Nd | Fraction where 10% target was hit before 7% stop |",
        "| median_mfe_Nd | Median max favorable excursion |",
        "| median_mae_Nd | Median max adverse excursion (negative = drawdown) |",
        "| median_fwd_Nd_ci_low / ci_high | Bootstrap confidence interval bounds for lane median forward return at ranking horizon |",
        "| median_fwd_Nd_ci_width | Width of the lane median confidence interval at ranking horizon |",
        "| median_fwd_Nd_bootstrap_sample_count | Number of labeled observations used for the bootstrap lane CI |",
        "",
        "## Output Files",
        "",
        f"- Edge summary CSV: `{summary_csv.as_posix()}`",
        f"- This report: `{path.as_posix()}`",
        "",
        "## Scope",
        "",
        f"- snapshot_database_path: `{db}`",
        f"- start_date: {start_date or 'none'}",
        f"- end_date: {end_date or 'none'}",
        f"- markets: {', '.join(markets) if markets else 'all'}",
        f"- countries: {', '.join(countries) if countries else 'all'}",
        f"- exchanges: {', '.join(exchanges) if exchanges else 'all'}",
        f"- us_only: {us_only}",
        f"- group_by: {group_by}",
        f"- group_rows_returned: {row_count}",
        f"- ranking_horizon: {ranking_horizon}d",
        f"- horizons: {', '.join(str(h) for h in horizons)}",
        f"- bootstrap_iterations: {bootstrap_iterations}",
        f"- bootstrap_confidence_level: {bootstrap_confidence_level}",
        "",
        "## Setup Variant Thresholds",
        "",
    ]
    for v in variants:
        lines.append(
            f"- **{v.name}**: ADRP≥{v.min_adrp_pct:.0%} | RelVol≥{v.min_relative_volume_pct:.0%}"
            f" | ValTraded≥{v.min_value_traded_pct:.0%} | VolSleeve≥{v.min_volatility_core_pct:.0%}"
            f" | LiqSleeve≥{v.min_liquidity_core_pct:.0%} | MomSleeve≥{v.min_momentum_context_pct:.0%}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_edge_summary(
    *,
    snapshot_database_path: str | Path,
    group_by: str = "industry",
    ranking_horizon: int | None = None,
    min_occurrence_count: int = 5,
    markets: Iterable[str] | None = None,
    countries: Iterable[str] | None = None,
    exchanges: Iterable[str] | None = None,
    us_only: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    bootstrap_iterations: int = 400,
    bootstrap_confidence_level: float = 0.9,
    bootstrap_seed: int = 17,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
    variants: Sequence[
        VolatilityLiquiditySetupVariant
    ] = DEFAULT_VOLATILITY_LIQUIDITY_SETUP_VARIANTS,
) -> dict[str, Any]:
    """Cross-day edge summary: forward-return performance grouped by sector or industry.

    Shows where setup edge is concentrated.  Complements the persistence scan by
    giving the group-level view rather than the individual-symbol view.
    """
    allowed_groupings = {"sector", "industry", "exchange", "country"}
    normalized_group_by = str(group_by).strip().lower()
    if normalized_group_by not in allowed_groupings:
        raise ValueError(
            f"Unsupported group_by '{group_by}'. Expected one of: {', '.join(sorted(allowed_groupings))}."
        )

    resolved_db = Path(snapshot_database_path)
    if not resolved_db.exists():
        raise FileNotFoundError(f"Snapshot database not found: {resolved_db}")

    output_paths = resolve_edge_research_paths(output_root=output_root)
    context = build_edge_research_run_context(
        prefix=f"edge_summary_{normalized_group_by}",
        output_root=output_paths.output_root,
    )
    summary_csv = context.output_dir / f"edge_summary_{normalized_group_by}.csv"
    report_md = context.output_dir / f"edge_summary_{normalized_group_by}_report.md"
    manifest_path = (
        context.output_dir / f"edge_summary_{normalized_group_by}_manifest.json"
    )

    duckdb_mod = _import_duckdb()
    conn = duckdb_mod.connect(resolved_db.as_posix(), read_only=False)
    conn.execute(f"SET threads TO {int(duckdb_threads)}")
    try:
        for t in (
            "symbol_day_feature_values",
            "symbol_day_sleeve_scores",
            "symbol_day_forward_labels",
        ):
            if not _table_exists(conn, t):
                raise ValueError(
                    f"Required table '{t}' not found. Run the full suite first."
                )

        horizons = _discover_horizons(conn)
        rh = ranking_horizon if ranking_horizon is not None else max(horizons)
        resolved_markets = _normalize_market_list(markets)
        resolved_countries = _expand_us_aliases(
            _normalize_string_list(countries),
            bool(us_only),
        )
        resolved_exchanges = _normalize_string_list(exchanges)
        filter_args = _format_scope_args(
            markets=resolved_markets,
            countries=resolved_countries,
            exchanges=resolved_exchanges,
            us_only=bool(us_only),
        )
        market_filter_sql, _market_filter_applied = resolve_snapshot_market_filter(
            conn,
            alias="snap",
            markets=resolved_markets,
        )

        # Build daily_flags (same as persistence scan)
        conn.execute(
            _build_daily_flags_cte(
                variants,
                market_filter_sql=market_filter_sql,
                countries=resolved_countries,
                exchanges=resolved_exchanges,
                start_date=start_date,
                end_date=end_date,
            )
        )
        filtered_rows = int(
            conn.execute("SELECT COUNT(*) FROM daily_flags").fetchone()[0]
        )
        if filtered_rows <= 0:
            raise ValueError(
                "No rows matched the selected region/date filters in daily_flags."
            )

        # Build labeled occurrence rows: one row per symbol-day-variant combination
        label_exclude_cols = (
            "source_database_path, source_day_label, source_date, run_id, run_created_at_utc, "
            "symbol, bare_ticker, company_name, exchange, country, "
            "close_price, market_cap_basic, sector, industry, type, is_primary"
        )
        variant_union_sql = "\n            UNION ALL\n            ".join(f"""SELECT
                df.symbol, df.bare_ticker, df.company_name,
                df.exchange, df.country, df.sector, df.industry,
                df.source_day_label, df.source_date,
                {_q(v.name)} AS setup_name,
                lbl.* EXCLUDE ({label_exclude_cols})
            FROM daily_flags AS df
            LEFT JOIN symbol_day_forward_labels AS lbl
                ON lbl.source_day_label = df.source_day_label
                AND lbl.symbol = df.symbol
            WHERE df.in_{v.name} = 1""" for v in variants)

        group_col_expr = f"COALESCE(occ.{normalized_group_by}, 'Unknown')"
        metric_cols = _edge_summary_metric_cols(horizons)

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE edge_summary_occurrences AS
            {variant_union_sql}
            """)

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE edge_summary_result AS
            SELECT
                occ.setup_name,
                {group_col_expr} AS {normalized_group_by},
                COUNT(*) AS occurrence_count,
                COUNT(DISTINCT occ.symbol) AS distinct_symbol_count,
                MIN(occ.source_date) AS first_occurrence_date,
                MAX(occ.source_date) AS last_occurrence_date,
                {metric_cols}
            FROM edge_summary_occurrences AS occ
            GROUP BY occ.setup_name, {group_col_expr}
            HAVING COUNT(*) >= {int(max(1, min_occurrence_count))}
            ORDER BY
                occ.setup_name,
                median_fwd_{rh}d DESC NULLS LAST,
                occurrence_count DESC
            """)

        # Bootstrap confidence interval for lane medians at ranking horizon.
        bootstrap_rows = conn.execute(f"""
            SELECT
                setup_name,
                COALESCE({normalized_group_by}, 'Unknown') AS group_value,
                forward_return_{rh}d_pct AS forward_return
            FROM edge_summary_occurrences
            WHERE forward_return_{rh}d_pct IS NOT NULL
            """).fetchall()
        bucketed: dict[tuple[str, str], list[float]] = {}
        for setup_name, group_value, forward_return in bootstrap_rows:
            key = (str(setup_name), str(group_value))
            bucketed.setdefault(key, []).append(float(forward_return))

        ci_insert_rows: list[
            tuple[str, str, float | None, float | None, float | None, int]
        ] = []
        for idx, (key, values) in enumerate(bucketed.items()):
            ci = _bootstrap_median_ci(
                values,
                iterations=bootstrap_iterations,
                confidence_level=bootstrap_confidence_level,
                seed=int(bootstrap_seed) + idx,
            )
            if ci is None:
                ci_low = None
                ci_high = None
                ci_width = None
            else:
                ci_low, ci_high = ci
                ci_width = ci_high - ci_low
            ci_insert_rows.append(
                (
                    key[0],
                    key[1],
                    ci_low,
                    ci_high,
                    ci_width,
                    len(values),
                )
            )

        conn.execute("""
            CREATE OR REPLACE TEMP TABLE edge_summary_ci (
                setup_name VARCHAR,
                group_value VARCHAR,
                median_fwd_ci_low DOUBLE,
                median_fwd_ci_high DOUBLE,
                median_fwd_ci_width DOUBLE,
                median_fwd_bootstrap_sample_count BIGINT
            )
            """)
        if ci_insert_rows:
            conn.executemany(
                """
                INSERT INTO edge_summary_ci VALUES (?, ?, ?, ?, ?, ?)
                """,
                ci_insert_rows,
            )

        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE edge_summary_result_final AS
            SELECT
                base.*,
                ci.median_fwd_ci_low AS median_fwd_{rh}d_ci_low,
                ci.median_fwd_ci_high AS median_fwd_{rh}d_ci_high,
                ci.median_fwd_ci_width AS median_fwd_{rh}d_ci_width,
                ci.median_fwd_bootstrap_sample_count AS median_fwd_{rh}d_bootstrap_sample_count
            FROM edge_summary_result AS base
            LEFT JOIN edge_summary_ci AS ci
                ON ci.setup_name = base.setup_name
               AND ci.group_value = base.{normalized_group_by}
            ORDER BY
                base.setup_name,
                base.median_fwd_{rh}d DESC NULLS LAST,
                base.occurrence_count DESC
            """)

        row_count = int(
            conn.execute("SELECT COUNT(*) FROM edge_summary_result_final").fetchone()[0]
        )
        conn.execute(
            f"COPY (SELECT * FROM edge_summary_result_final) TO {_q(summary_csv.as_posix())} (HEADER, DELIMITER ',')"
        )

        _write_edge_summary_report(
            report_md,
            snapshot_database_path=resolved_db,
            group_by=normalized_group_by,
            ranking_horizon=rh,
            horizons=horizons,
            row_count=row_count,
            summary_csv=summary_csv,
            variants=list(variants),
            markets=resolved_markets,
            countries=resolved_countries,
            exchanges=resolved_exchanges,
            us_only=bool(us_only),
            start_date=start_date,
            end_date=end_date,
            bootstrap_iterations=int(max(50, bootstrap_iterations)),
            bootstrap_confidence_level=float(bootstrap_confidence_level),
            filter_args=filter_args,
        )

        manifest = {
            "run_id": context.run_id,
            "run_created_at_utc": context.created_at_utc.isoformat(),
            "command": "scan-edge",
            "snapshot_database_path": resolved_db.as_posix(),
            "group_by": normalized_group_by,
            "start_date": start_date,
            "end_date": end_date,
            "markets": list(resolved_markets),
            "countries": list(resolved_countries),
            "exchanges": list(resolved_exchanges),
            "us_only": bool(us_only),
            "min_occurrence_count": min_occurrence_count,
            "ranking_horizon": rh,
            "bootstrap_iterations": int(max(50, bootstrap_iterations)),
            "bootstrap_confidence_level": float(bootstrap_confidence_level),
            "bootstrap_seed": int(bootstrap_seed),
            "horizons": list(horizons),
            "row_count": row_count,
            "output_dir": context.output_dir.as_posix(),
            "summary_csv": summary_csv.as_posix(),
            "report_md": report_md.as_posix(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        return {
            "output_dir": context.output_dir,
            "summary_csv": summary_csv,
            "report_md": report_md,
            "manifest_path": manifest_path,
            "group_by": normalized_group_by,
            "row_count": row_count,
        }
    finally:
        conn.close()
