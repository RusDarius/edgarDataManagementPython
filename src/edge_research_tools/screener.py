from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import build_edge_research_run_context, resolve_edge_research_paths
from .region_filters import (
    resolve_snapshot_market_filter,
    snapshot_market_select_sql,
)
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
        literals = ", ".join(_q(value.upper()) for value in countries)
        filters.append(f"UPPER(COALESCE({country_expr}, '')) IN ({literals})")
    if exchanges:
        literals = ", ".join(_q(value.upper()) for value in exchanges)
        filters.append(f"UPPER(COALESCE({exchange_expr}, '')) IN ({literals})")
    return " AND ".join(filters) if filters else "1 = 1"


def _build_market_filter_sql(*, market_expr: str, markets: Sequence[str]) -> str:
    if not markets:
        return "1 = 1"
    literals = ", ".join(_q(value.lower()) for value in markets)
    return f"LOWER(COALESCE({market_expr}, '')) IN ({literals})"


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


def _variant_conditions(v: VolatilityLiquiditySetupVariant, qual: str = "") -> str:
    """Six-threshold WHERE condition, optionally table-qualified."""
    q = f"{qual}." if qual else ""
    return (
        f"COALESCE({q}adrp_directional_universe_percentile, 0) >= {v.min_adrp_pct}"
        f" AND COALESCE({q}relative_volume_directional_universe_percentile, 0) >= {v.min_relative_volume_pct}"
        f" AND COALESCE({q}value_traded_directional_universe_percentile, 0) >= {v.min_value_traded_pct}"
        f" AND COALESCE({q}liquidity_core_directional_universe_percentile, 0) >= {v.min_liquidity_core_pct}"
        f" AND COALESCE({q}momentum_context_directional_universe_percentile, 0) >= {v.min_momentum_context_pct}"
        f" AND COALESCE({q}volatility_core_directional_universe_percentile, 0) >= {v.min_volatility_core_pct}"
    )


def _variant_conditions_fpsp(v: VolatilityLiquiditySetupVariant) -> str:
    """Conditions using fp/sp CTE qualifiers, for use inside today_combined CTE."""
    return (
        f"COALESCE(fp.adrp_directional_universe_percentile, 0) >= {v.min_adrp_pct}"
        f" AND COALESCE(fp.relative_volume_directional_universe_percentile, 0) >= {v.min_relative_volume_pct}"
        f" AND COALESCE(fp.value_traded_directional_universe_percentile, 0) >= {v.min_value_traded_pct}"
        f" AND COALESCE(sp.liquidity_core_directional_universe_percentile, 0) >= {v.min_liquidity_core_pct}"
        f" AND COALESCE(sp.momentum_context_directional_universe_percentile, 0) >= {v.min_momentum_context_pct}"
        f" AND COALESCE(sp.volatility_core_directional_universe_percentile, 0) >= {v.min_volatility_core_pct}"
    )


def _hist_perf_cols(horizons: Sequence[int], suffix: str = "") -> str:
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
    return ",\n                ".join(parts)


def _hist_col_refs(horizons: Sequence[int], suffix: str = "", alias: str = "h") -> str:
    names = []
    for h in horizons:
        names += [
            f"sample_count_{h}d{suffix}",
            f"median_fwd_{h}d{suffix}",
            f"avg_fwd_{h}d{suffix}",
            f"win_rate_{h}d{suffix}",
            f"median_mfe_{h}d{suffix}",
            f"median_mae_{h}d{suffix}",
            f"target_rate_{h}d{suffix}",
        ]
    return ", ".join(f"{alias}.{n}" for n in names)


def _write_screen_report(
    path: Path,
    *,
    snapshot_database_path: Path,
    target_date: str,
    top_n: int,
    ranking_horizon: int,
    horizons: tuple[int, ...],
    candidate_count: int,
    ranked_csv: Path,
    variants: Sequence[VolatilityLiquiditySetupVariant],
    markets: Sequence[str],
    countries: Sequence[str],
    exchanges: Sequence[str],
    us_only: bool,
    filter_args: str,
) -> None:
    db = snapshot_database_path.as_posix()
    lines = [
        f"# Edge Screen — {target_date}",
        "",
        "## Why Run This",
        "",
        "The point-in-time screener shows which symbols are in high-percentile volatility and liquidity conditions on a specific date.",
        "The composite score averages six directional percentile signals. Historical in-setup performance columns show how the same",
        "symbol behaved on prior dataset dates when it was in a similar setup — giving actionable forward-return context.",
        "",
        "## How To Rerun",
        "",
        "Latest date in dataset (git bash):",
        "```bash",
        f"PYTHONPATH='src:.' python -m run_edge_research_tools screen \\",
        f'  --snapshot-db "{db}" --top-n {top_n} --ranking-horizon {ranking_horizon}{filter_args}',
        "```",
        "",
        "Specific date (git bash):",
        "```bash",
        f"PYTHONPATH='src:.' python -m run_edge_research_tools screen \\",
        f'  --snapshot-db "{db}" --date {target_date} --top-n 100 --ranking-horizon {ranking_horizon}{filter_args}',
        "```",
        "",
        "PowerShell:",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        f'python -m run_edge_research_tools screen --snapshot-db "{db}" --date {target_date} --top-n {top_n} --ranking-horizon {ranking_horizon}{filter_args}',
        "```",
        "",
        "Widen the filter (composite >= 0.45, top 200):",
        "```bash",
        f"PYTHONPATH='src:.' python -m run_edge_research_tools screen --snapshot-db \"{db}\" --min-composite 0.45 --top-n 200{filter_args}",
        "```",
        "",
        "## Output Column Guide",
        "",
        "| Column | Meaning |",
        "|--------|---------|",
        "| screen_rank | Rank by composite_score descending |",
        "| composite_score | Mean of the 6 directional percentiles. 1.0 = universe top on all six. |",
        "| adrp_raw | Average Daily Range % (raw value) |",
        "| relvol_raw | Relative volume vs 10-day average (raw multiplier) |",
        "| adrp_directional_universe_percentile | ADRP ranked bullishly within today's universe |",
        "| relative_volume_directional_universe_percentile | Relative volume ranked within today's universe |",
        "| value_traded_directional_universe_percentile | Dollar value traded ranked within today's universe |",
        "| volatility_core_directional_universe_percentile | Sleeve-level mean: volatility fields |",
        "| liquidity_core_directional_universe_percentile | Sleeve-level mean: liquidity fields |",
        "| momentum_context_directional_universe_percentile | Sleeve-level mean: momentum fields |",
        f"| in_<variant> | 1 if all six thresholds pass for that variant on {target_date} |",
        "| hist_occurrence_count_in_setup | Past days this symbol was in any setup within this dataset |",
        "| median_fwd_Nd_in_setup | Median forward return (%) from historical in-setup dates |",
        "| win_rate_Nd_in_setup | Fraction of in-setup dates with positive forward return |",
        "| target_rate_Nd_in_setup | Fraction where 10% target was hit before 7% stop |",
        "| median_mfe_Nd_in_setup | Median max favorable excursion from historical in-setup dates |",
        "| median_mae_Nd_in_setup | Median max adverse excursion from historical in-setup dates |",
        "",
        "## Output Files",
        "",
        f"- Ranked candidates CSV: `{ranked_csv.as_posix()}`",
        f"- This report: `{path.as_posix()}`",
        "",
        "## Scope",
        "",
        f"- snapshot_database_path: `{db}`",
        f"- target_date: {target_date}",
        f"- markets: {', '.join(markets) if markets else 'all'}",
        f"- countries: {', '.join(countries) if countries else 'all'}",
        f"- exchanges: {', '.join(exchanges) if exchanges else 'all'}",
        f"- us_only: {us_only}",
        f"- candidate_count: {candidate_count} (top {top_n} by composite_score)",
        f"- ranking_horizon: {ranking_horizon}d",
        f"- horizons: {', '.join(str(h) for h in horizons)}",
        "",
        "## Setup Variant Thresholds Applied",
        "",
    ]
    for v in variants:
        lines.append(
            f"- **{v.name}**: ADRP≥{v.min_adrp_pct:.0%} | RelVol≥{v.min_relative_volume_pct:.0%}"
            f" | ValTraded≥{v.min_value_traded_pct:.0%} | VolSleeve≥{v.min_volatility_core_pct:.0%}"
            f" | LiqSleeve≥{v.min_liquidity_core_pct:.0%} | MomSleeve≥{v.min_momentum_context_pct:.0%}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_edge_screen(
    *,
    snapshot_database_path: str | Path,
    target_date: str | None = None,
    top_n: int = 50,
    min_composite: float = 0.55,
    ranking_horizon: int | None = None,
    markets: Iterable[str] | None = None,
    countries: Iterable[str] | None = None,
    exchanges: Iterable[str] | None = None,
    us_only: bool = False,
    output_root: str | Path | None = None,
    duckdb_threads: int = 16,
    variants: Sequence[
        VolatilityLiquiditySetupVariant
    ] = DEFAULT_VOLATILITY_LIQUIDITY_SETUP_VARIANTS,
) -> dict[str, Any]:
    """Point-in-time screener: ranks symbols by composite setup quality on a single date.

    Defaults to the latest date available in the snapshot database.
    Historical in-setup performance columns show forward-return context from prior
    dataset dates where the same symbol was in a similar setup condition.
    """
    resolved_db = Path(snapshot_database_path)
    if not resolved_db.exists():
        raise FileNotFoundError(f"Snapshot database not found: {resolved_db}")

    output_paths = resolve_edge_research_paths(output_root=output_root)
    context = build_edge_research_run_context(
        prefix="edge_screen",
        output_root=output_paths.output_root,
    )
    ranked_csv = context.output_dir / "edge_screen_ranked.csv"
    report_md = context.output_dir / "edge_screen_report.md"
    manifest_path = context.output_dir / "edge_screen_manifest.json"

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
        region_filter_snap = _build_region_filter_sql(
            country_expr="snap.country",
            exchange_expr="snap.exchange",
            countries=resolved_countries,
            exchanges=resolved_exchanges,
        )
        market_filter_snap, _market_filter_applied = resolve_snapshot_market_filter(
            conn,
            alias="snap",
            markets=resolved_markets,
        )
        market_select_sql = snapshot_market_select_sql("snap", conn)
        resolved_date = (
            str(
                conn.execute(
                    "SELECT MAX(source_date)::VARCHAR "
                    "FROM symbol_day_feature_snapshot AS snap "
                    f"WHERE {region_filter_snap} AND {market_filter_snap}"
                ).fetchone()[0]
            )
            if target_date is None
            else target_date
        )
        if not resolved_date or resolved_date == "None":
            raise ValueError(
                "No source_date matched the selected region filters in symbol_day_feature_snapshot."
            )
        matching_row_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM symbol_day_feature_snapshot AS snap "
                f"WHERE snap.source_date = {_q(resolved_date)}::DATE "
                f"AND {region_filter_snap} AND {market_filter_snap}"
            ).fetchone()[0]
        )
        if matching_row_count <= 0:
            raise ValueError(
                f"No rows matched source_date={resolved_date} with the selected region filters."
            )

        # Any-in-setup condition for the combined_daily CTE alias 'cd'
        any_in_setup_cd = "\n                OR ".join(
            f"({_variant_conditions(v, qual='cd')})" for v in variants
        )
        # Variant flag CASE expressions using fp/sp qualifiers inside today_combined CTE
        variant_flag_cols = ",\n                    ".join(
            f"CASE WHEN {_variant_conditions_fpsp(v)} THEN 1 ELSE 0 END AS in_{v.name}"
            for v in variants
        )
        # Final WHERE filter uses tc. alias (today_combined)
        any_variant_tc = " OR ".join(f"tc.in_{v.name} = 1" for v in variants)

        hist_cols = _hist_perf_cols(horizons, suffix="_in_setup")
        hist_refs = _hist_col_refs(horizons, suffix="_in_setup", alias="h")

        # Step 1: historical in-setup performance aggregated across all dates
        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE screen_hist_in_setup AS
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
            ),
            snap_region AS (
                SELECT source_day_label, symbol, country, exchange
                FROM symbol_day_feature_snapshot AS snap
                WHERE {region_filter_snap}
            ),
            combined_daily AS (
                SELECT
                    fp.symbol, fp.source_day_label,
                    fp.adrp_directional_universe_percentile,
                    fp.relative_volume_directional_universe_percentile,
                    fp.value_traded_directional_universe_percentile,
                    sp.volatility_core_directional_universe_percentile,
                    sp.liquidity_core_directional_universe_percentile,
                    sp.momentum_context_directional_universe_percentile
                FROM fp
                LEFT JOIN sp
                    ON sp.source_day_label = fp.source_day_label
                    AND sp.symbol = fp.symbol
                JOIN snap_region AS sr
                    ON sr.source_day_label = fp.source_day_label
                    AND sr.symbol = fp.symbol
            ),
            in_setup_lbl AS (
                SELECT cd.symbol, lbl.*
                FROM combined_daily AS cd
                JOIN symbol_day_forward_labels AS lbl
                    ON lbl.source_day_label = cd.source_day_label
                    AND lbl.symbol = cd.symbol
                WHERE {any_in_setup_cd}
            )
            SELECT
                symbol,
                COUNT(*) AS hist_occurrence_count_in_setup,
                {hist_cols}
            FROM in_setup_lbl
            GROUP BY symbol
            """)

        # Step 2: today's point-in-time screen
        conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE today_screen AS
            WITH fp AS (
                SELECT symbol,
                    MAX(CASE WHEN field_name = 'ADRP' THEN raw_value END) AS adrp_raw,
                    MAX(CASE WHEN field_name = 'ATRP' THEN raw_value END) AS atrp_raw,
                    MAX(CASE WHEN field_name = 'Volatility.M' THEN raw_value END) AS volatility_month_raw,
                    MAX(CASE WHEN field_name = 'relative_volume_10d_calc' THEN raw_value END) AS relvol_raw,
                    MAX(CASE WHEN field_name = 'volume' THEN raw_value END) AS volume_raw,
                    MAX(CASE WHEN field_name = 'ADRP' THEN directional_universe_percentile END)
                        AS adrp_directional_universe_percentile,
                    MAX(CASE WHEN field_name = 'relative_volume_10d_calc' THEN directional_universe_percentile END)
                        AS relative_volume_directional_universe_percentile,
                    MAX(CASE WHEN field_name = 'Value.Traded' THEN directional_universe_percentile END)
                        AS value_traded_directional_universe_percentile
                FROM symbol_day_feature_values
                WHERE source_date = {_q(resolved_date)}::DATE
                GROUP BY symbol
            ),
            sp AS (
                SELECT symbol,
                    MAX(CASE WHEN sleeve_name = 'volatility_core' THEN avg_directional_universe_percentile END)
                        AS volatility_core_directional_universe_percentile,
                    MAX(CASE WHEN sleeve_name = 'liquidity_core' THEN avg_directional_universe_percentile END)
                        AS liquidity_core_directional_universe_percentile,
                    MAX(CASE WHEN sleeve_name = 'momentum_context' THEN avg_directional_universe_percentile END)
                        AS momentum_context_directional_universe_percentile
                FROM symbol_day_sleeve_scores
                WHERE source_date = {_q(resolved_date)}::DATE
                GROUP BY symbol
            ),
            today_combined AS (
                SELECT
                    snap.symbol, snap.bare_ticker, snap.company_name,
                    {market_select_sql}, snap.exchange, snap.country, snap.sector, snap.industry,
                    snap.close_price, snap.market_cap_basic,
                    snap.source_day_label, snap.source_date,
                    fp.adrp_raw, fp.atrp_raw, fp.volatility_month_raw,
                    fp.relvol_raw, fp.volume_raw,
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
                    ) / 6.0, 4) AS composite_score,
                    {variant_flag_cols}
                FROM symbol_day_feature_snapshot AS snap
                LEFT JOIN fp ON fp.symbol = snap.symbol
                LEFT JOIN sp ON sp.symbol = snap.symbol
                WHERE snap.source_date = {_q(resolved_date)}::DATE
                                    AND {region_filter_snap}
                                    AND {market_filter_snap}
            )
            SELECT
                ROW_NUMBER() OVER (
                    ORDER BY tc.composite_score DESC,
                        tc.adrp_directional_universe_percentile DESC NULLS LAST
                ) AS screen_rank,
                tc.*,
                h.hist_occurrence_count_in_setup,
                {hist_refs}
            FROM today_combined AS tc
            LEFT JOIN screen_hist_in_setup AS h ON h.symbol = tc.symbol
            WHERE tc.composite_score >= {float(min_composite)}
               OR ({any_variant_tc})
            ORDER BY tc.composite_score DESC
            LIMIT {int(top_n)}
            """)

        candidate_count = int(
            conn.execute("SELECT COUNT(*) FROM today_screen").fetchone()[0]
        )
        conn.execute(
            f"COPY (SELECT * FROM today_screen) TO {_q(ranked_csv.as_posix())} (HEADER, DELIMITER ',')"
        )

        _write_screen_report(
            report_md,
            snapshot_database_path=resolved_db,
            target_date=resolved_date,
            top_n=top_n,
            ranking_horizon=rh,
            horizons=horizons,
            candidate_count=candidate_count,
            ranked_csv=ranked_csv,
            variants=list(variants),
            markets=resolved_markets,
            countries=resolved_countries,
            exchanges=resolved_exchanges,
            us_only=bool(us_only),
            filter_args=filter_args,
        )

        manifest = {
            "run_id": context.run_id,
            "run_created_at_utc": context.created_at_utc.isoformat(),
            "command": "screen",
            "snapshot_database_path": resolved_db.as_posix(),
            "target_date": resolved_date,
            "markets": list(resolved_markets),
            "countries": list(resolved_countries),
            "exchanges": list(resolved_exchanges),
            "us_only": bool(us_only),
            "top_n": top_n,
            "min_composite": min_composite,
            "ranking_horizon": rh,
            "horizons": list(horizons),
            "candidate_count": candidate_count,
            "output_dir": context.output_dir.as_posix(),
            "ranked_csv": ranked_csv.as_posix(),
            "report_md": report_md.as_posix(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        return {
            "output_dir": context.output_dir,
            "ranked_csv": ranked_csv,
            "report_md": report_md,
            "manifest_path": manifest_path,
            "target_date": resolved_date,
            "candidate_count": candidate_count,
        }
    finally:
        conn.close()
