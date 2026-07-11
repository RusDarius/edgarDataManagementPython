from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import build_edge_research_run_context, resolve_edge_research_paths


@dataclass(frozen=True)
class VolatilityLiquiditySetupVariant:
    name: str
    description: str
    min_adrp_pct: float
    min_relative_volume_pct: float
    min_value_traded_pct: float
    min_liquidity_core_pct: float
    min_momentum_context_pct: float
    min_volatility_core_pct: float


DEFAULT_VOLATILITY_LIQUIDITY_SETUP_VARIANTS: tuple[
    VolatilityLiquiditySetupVariant, ...
] = (
    VolatilityLiquiditySetupVariant(
        name="adrp_relvol_core",
        description="High ADRP, high relative volume, and strong traded value with adequate liquidity sleeve support.",
        min_adrp_pct=0.80,
        min_relative_volume_pct=0.70,
        min_value_traded_pct=0.55,
        min_liquidity_core_pct=0.60,
        min_momentum_context_pct=0.45,
        min_volatility_core_pct=0.65,
    ),
    VolatilityLiquiditySetupVariant(
        name="adrp_relvol_momentum",
        description="High ADRP with strong relative volume and slightly stronger momentum confirmation.",
        min_adrp_pct=0.75,
        min_relative_volume_pct=0.70,
        min_value_traded_pct=0.50,
        min_liquidity_core_pct=0.58,
        min_momentum_context_pct=0.55,
        min_volatility_core_pct=0.60,
    ),
    VolatilityLiquiditySetupVariant(
        name="volatility_liquidity_balanced",
        description="Balanced sleeve-level expansion where volatility and liquidity are both above-average and momentum is non-weak.",
        min_adrp_pct=0.70,
        min_relative_volume_pct=0.60,
        min_value_traded_pct=0.50,
        min_liquidity_core_pct=0.68,
        min_momentum_context_pct=0.50,
        min_volatility_core_pct=0.75,
    ),
)

_LABEL_HORIZON_PATTERN = re.compile(r"^forward_return_(\d+)d_pct$")


def _import_duckdb():
    import duckdb

    return duckdb


def _quote_sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _table_exists(connection: Any, table_name: str) -> bool:
    return bool(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_name = ?
            """,
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


def _discover_label_horizons(connection: Any) -> tuple[int, ...]:
    columns = [
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info('symbol_day_forward_labels')"
        ).fetchall()
    ]
    horizons = sorted(
        {
            int(match.group(1))
            for column_name in columns
            if (match := _LABEL_HORIZON_PATTERN.match(column_name))
        }
    )
    if not horizons:
        raise ValueError(
            "No forward_return_<N>d_pct columns were found in symbol_day_forward_labels."
        )
    return tuple(horizons)


def _build_variant_occurrence_union(
    variants: Sequence[VolatilityLiquiditySetupVariant],
) -> str:
    selects: list[str] = []
    for variant in variants:
        selects.append(f"""
            SELECT
                scoped.*,
                {_quote_sql_literal(variant.name)} AS setup_name,
                {_quote_sql_literal(variant.description)} AS setup_description
            FROM vol_liq_inputs_scoped AS scoped
            WHERE COALESCE(scoped.adrp_directional_universe_percentile, 0.0) >= {variant.min_adrp_pct}
                AND COALESCE(scoped.relative_volume_directional_universe_percentile, 0.0) >= {variant.min_relative_volume_pct}
                AND COALESCE(scoped.value_traded_directional_universe_percentile, 0.0) >= {variant.min_value_traded_pct}
                AND COALESCE(scoped.liquidity_core_directional_universe_percentile, 0.0) >= {variant.min_liquidity_core_pct}
                AND COALESCE(scoped.momentum_context_directional_universe_percentile, 0.0) >= {variant.min_momentum_context_pct}
                AND COALESCE(scoped.volatility_core_directional_universe_percentile, 0.0) >= {variant.min_volatility_core_pct}
            """)
    return "\nUNION ALL\n".join(selects)


def _build_summary_metric_columns(horizons: Sequence[int]) -> str:
    columns: list[str] = []
    for horizon in horizons:
        columns.extend(
            [
                f"COUNT(forward_return_{horizon}d_pct) AS sample_count_{horizon}d",
                f"MEDIAN(forward_return_{horizon}d_pct) AS median_forward_return_{horizon}d_pct",
                f"AVG(forward_return_{horizon}d_pct) AS avg_forward_return_{horizon}d_pct",
                (
                    f"AVG(CASE WHEN forward_return_{horizon}d_pct IS NULL THEN NULL "
                    f"WHEN forward_return_{horizon}d_pct > 0 THEN 1.0 ELSE 0.0 END) AS win_rate_{horizon}d"
                ),
                f"MEDIAN(max_favorable_excursion_{horizon}d_pct) AS median_mfe_{horizon}d_pct",
                f"MEDIAN(max_adverse_excursion_{horizon}d_pct) AS median_mae_{horizon}d_pct",
                (
                    f"AVG(CASE WHEN target_before_stop_{horizon}d_flag IS NULL THEN NULL "
                    f"WHEN target_before_stop_{horizon}d_flag THEN 1.0 ELSE 0.0 END) AS target_before_stop_rate_{horizon}d"
                ),
                (
                    f"AVG(CASE WHEN stop_hit_{horizon}d_flag IS NULL THEN NULL "
                    f"WHEN stop_hit_{horizon}d_flag THEN 1.0 ELSE 0.0 END) AS stop_hit_rate_{horizon}d"
                ),
                (
                    f"AVG(CASE WHEN max_adverse_excursion_{horizon}d_pct IS NULL OR ABS(max_adverse_excursion_{horizon}d_pct) < 0.000001 THEN NULL "
                    f"ELSE forward_return_{horizon}d_pct / ABS(max_adverse_excursion_{horizon}d_pct) END) AS return_to_mae_ratio_{horizon}d"
                ),
            ]
        )
    return ",\n        ".join(columns)


def _write_setup_report(
    path: Path,
    *,
    snapshot_database_path: Path,
    output_dir: Path,
    group_by: str,
    group_values: Sequence[str],
    symbols: Sequence[str],
    custom_group_csv: str | None,
    ranking_horizon: int,
    horizons: Sequence[int],
    occurrence_row_count: int,
    summary_row_count: int,
    best_name_row_count: int,
    variants: Sequence[VolatilityLiquiditySetupVariant],
    summary_csv_path: Path,
    best_names_csv_path: Path,
    occurrences_csv_path: Path,
) -> None:
    lines = [
        "# Volatility And Liquidity Setup Pass",
        "",
        "## Why Run This",
        "",
        "This pass takes the labeled symbol-day dataset and looks for interpretable high-volatility, high-liquidity states that may surface higher-upside names with explicit drawdown awareness.",
        "",
        "## How To Run",
        "",
        "Universe-wide:",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        f'python -m run_edge_research_tools setup-vol-liq --snapshot-db "{snapshot_database_path.as_posix()}" --group-by universe --ranking-horizon {ranking_horizon}',
        "```",
        "",
        "Industry grouped:",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        f'python -m run_edge_research_tools setup-vol-liq --snapshot-db "{snapshot_database_path.as_posix()}" --group-by industry --ranking-horizon {ranking_horizon}',
        "```",
        "",
        "Peers or selected names:",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        f'python -m run_edge_research_tools setup-vol-liq --snapshot-db "{snapshot_database_path.as_posix()}" --group-by universe --symbols NVDA,AMD,AVGO --ranking-horizon {ranking_horizon}',
        "```",
        "",
        "Custom grouped lanes:",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        f'python -m run_edge_research_tools setup-vol-liq --snapshot-db "{snapshot_database_path.as_posix()}" --group-by custom_group --custom-group-csv "D:/path/to/custom_groups.csv" --ranking-horizon {ranking_horizon}',
        "```",
        "",
        "Individual name history:",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        f'python -m run_edge_research_tools setup-vol-liq --snapshot-db "{snapshot_database_path.as_posix()}" --group-by symbol --symbols NVDA --ranking-horizon {ranking_horizon}',
        "```",
        "",
        "## Output Meaning",
        "",
        "- `volatility_liquidity_setup_occurrences.csv`: every symbol-day that matched one of the setup variants, with attached forward labels.",
        "- `volatility_liquidity_setup_summary.csv`: grouped setup quality table with sample size, return, win rate, MFE, MAE, and target-before-stop rates across requested horizons.",
        "- `volatility_liquidity_best_names.csv`: symbol-level ranking inside the selected scope so you can see which names repeatedly performed best under each setup.",
        "",
        "## Usage",
        "",
        "- Start with `group-by universe` to see whether the setup family has any broad edge at all.",
        "- Move to `group-by industry` or `group-by sector` to see whether the edge is concentrated rather than universal.",
        "- Use `--symbols` for peers or a single name.",
        "- Use `--custom-group-csv` when you want your own lanes or matched baskets. The CSV should contain `symbol` and `group_name` columns.",
        "",
        "## Scope",
        "",
        f"- snapshot_database_path: `{snapshot_database_path.as_posix()}`",
        f"- output_dir: `{output_dir.as_posix()}`",
        f"- group_by: {group_by}",
        f"- group_values: {', '.join(group_values) if group_values else 'all'}",
        f"- symbols: {', '.join(symbols) if symbols else 'all'}",
        f"- custom_group_csv: {custom_group_csv or 'none'}",
        f"- horizons: {', '.join(str(h) for h in horizons)}",
        f"- ranking_horizon: {ranking_horizon}",
        f"- occurrence_row_count: {occurrence_row_count}",
        f"- summary_row_count: {summary_row_count}",
        f"- best_name_row_count: {best_name_row_count}",
        f"- summary_csv_path: `{summary_csv_path.as_posix()}`",
        f"- best_names_csv_path: `{best_names_csv_path.as_posix()}`",
        f"- occurrences_csv_path: `{occurrences_csv_path.as_posix()}`",
        "",
        "## Setup Variants",
        "",
    ]
    for variant in variants:
        lines.append(
            "- "
            f"{variant.name}: {variant.description} "
            f"| min_adrp_pct={variant.min_adrp_pct} "
            f"| min_relative_volume_pct={variant.min_relative_volume_pct} "
            f"| min_value_traded_pct={variant.min_value_traded_pct} "
            f"| min_liquidity_core_pct={variant.min_liquidity_core_pct} "
            f"| min_momentum_context_pct={variant.min_momentum_context_pct} "
            f"| min_volatility_core_pct={variant.min_volatility_core_pct}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_volatility_liquidity_setup_pass(
    *,
    snapshot_database_path: str | Path,
    group_by: str = "universe",
    group_values: Iterable[str] | None = None,
    symbols: Iterable[str] | None = None,
    custom_group_csv: str | Path | None = None,
    ranking_horizon: int | None = None,
    min_occurrence_count: int = 10,
    min_symbol_occurrence_count: int = 3,
    output_root: str | Path | None = None,
    variants: Sequence[
        VolatilityLiquiditySetupVariant
    ] = DEFAULT_VOLATILITY_LIQUIDITY_SETUP_VARIANTS,
) -> dict[str, Any]:
    resolved_snapshot_database_path = Path(snapshot_database_path)
    if not resolved_snapshot_database_path.exists():
        raise FileNotFoundError(
            f"Snapshot database was not found: {resolved_snapshot_database_path}"
        )
    normalized_group_by = str(group_by).strip().lower() or "universe"
    allowed_groupings = {
        "universe": "'universe'",
        "sector": "COALESCE(sector, 'Unknown')",
        "industry": "COALESCE(industry, 'Unknown')",
        "exchange": "COALESCE(exchange, 'Unknown')",
        "country": "COALESCE(country, 'Unknown')",
        "custom_group": "COALESCE(custom_group, 'Unmapped')",
        "symbol": "COALESCE(symbol, bare_ticker)",
    }
    if normalized_group_by not in allowed_groupings:
        raise ValueError(
            f"Unsupported group_by '{group_by}'. Expected one of: {', '.join(sorted(allowed_groupings))}."
        )
    normalized_group_values = _normalize_string_list(group_values)
    normalized_symbols = tuple(
        value.upper() for value in _normalize_string_list(symbols)
    )
    resolved_custom_group_csv = (
        Path(custom_group_csv) if custom_group_csv is not None else None
    )

    output_paths = resolve_edge_research_paths(output_root=output_root)
    context = build_edge_research_run_context(
        prefix="vol_liq_setup",
        output_root=output_paths.output_root,
    )
    occurrences_csv_path = (
        context.output_dir / "volatility_liquidity_setup_occurrences.csv"
    )
    summary_csv_path = context.output_dir / "volatility_liquidity_setup_summary.csv"
    best_names_csv_path = context.output_dir / "volatility_liquidity_best_names.csv"
    report_md = context.output_dir / "volatility_liquidity_setup_report.md"
    manifest_path = context.output_dir / "volatility_liquidity_setup_manifest.json"

    duckdb = _import_duckdb()
    connection = duckdb.connect(
        resolved_snapshot_database_path.as_posix(), read_only=False
    )
    try:
        if not _table_exists(connection, "symbol_day_feature_values"):
            raise ValueError(
                "symbol_day_feature_values was not found. Rebuild the snapshot with the expanded feature tables first."
            )
        if not _table_exists(connection, "symbol_day_sleeve_scores"):
            raise ValueError(
                "symbol_day_sleeve_scores was not found. Rebuild the snapshot with the expanded sleeve tables first."
            )
        if not _table_exists(connection, "symbol_day_forward_labels"):
            raise ValueError(
                "symbol_day_forward_labels was not found. Run the labels command on the snapshot database first."
            )

        horizons = _discover_label_horizons(connection)
        resolved_ranking_horizon = (
            ranking_horizon if ranking_horizon is not None else max(horizons)
        )
        if resolved_ranking_horizon not in horizons:
            raise ValueError(
                f"ranking_horizon {resolved_ranking_horizon} is not available in the forward label table. Available horizons: {', '.join(str(h) for h in horizons)}"
            )

        if resolved_custom_group_csv is not None:
            if not resolved_custom_group_csv.exists():
                raise FileNotFoundError(
                    f"custom_group_csv was not found: {resolved_custom_group_csv}"
                )
            connection.execute(f"""
                CREATE OR REPLACE TEMP TABLE custom_group_map AS
                SELECT
                    UPPER(TRIM(CAST(symbol AS VARCHAR))) AS symbol_key,
                    TRIM(CAST(group_name AS VARCHAR)) AS custom_group
                FROM read_csv_auto({_quote_sql_literal(resolved_custom_group_csv.as_posix())}, HEADER=TRUE)
                """)
        else:
            connection.execute("""
                CREATE OR REPLACE TEMP TABLE custom_group_map (
                    symbol_key VARCHAR,
                    custom_group VARCHAR
                )
                """)

        connection.execute("""
            CREATE OR REPLACE TEMP VIEW vol_liq_setup_inputs AS
            WITH field_pivot AS (
                SELECT
                    source_day_label,
                    symbol,
                    MAX(CASE WHEN field_name = 'ADRP' THEN raw_value END) AS adrp_raw,
                    MAX(CASE WHEN field_name = 'ADRP' THEN directional_universe_percentile END) AS adrp_directional_universe_percentile,
                    MAX(CASE WHEN field_name = 'ATRP' THEN directional_universe_percentile END) AS atrp_directional_universe_percentile,
                    MAX(CASE WHEN field_name = 'relative_volume_10d_calc' THEN directional_universe_percentile END) AS relative_volume_directional_universe_percentile,
                    MAX(CASE WHEN field_name = 'Value.Traded' THEN directional_universe_percentile END) AS value_traded_directional_universe_percentile,
                    MAX(CASE WHEN field_name = 'Volatility.M' THEN directional_universe_percentile END) AS volatility_month_directional_universe_percentile,
                    MAX(CASE WHEN field_name = 'ADRP' THEN directional_industry_percentile END) AS adrp_directional_industry_percentile,
                    MAX(CASE WHEN field_name = 'relative_volume_10d_calc' THEN directional_industry_percentile END) AS relative_volume_directional_industry_percentile,
                    MAX(CASE WHEN field_name = 'Value.Traded' THEN directional_industry_percentile END) AS value_traded_directional_industry_percentile
                FROM symbol_day_feature_values
                GROUP BY source_day_label, symbol
            ),
            sleeve_pivot AS (
                SELECT
                    source_day_label,
                    symbol,
                    MAX(CASE WHEN sleeve_name = 'volatility_core' THEN avg_directional_universe_percentile END) AS volatility_core_directional_universe_percentile,
                    MAX(CASE WHEN sleeve_name = 'liquidity_core' THEN avg_directional_universe_percentile END) AS liquidity_core_directional_universe_percentile,
                    MAX(CASE WHEN sleeve_name = 'momentum_context' THEN avg_directional_universe_percentile END) AS momentum_context_directional_universe_percentile,
                    MAX(CASE WHEN sleeve_name = 'volatility_core' THEN avg_directional_industry_percentile END) AS volatility_core_directional_industry_percentile,
                    MAX(CASE WHEN sleeve_name = 'liquidity_core' THEN avg_directional_industry_percentile END) AS liquidity_core_directional_industry_percentile,
                    MAX(CASE WHEN sleeve_name = 'momentum_context' THEN avg_directional_industry_percentile END) AS momentum_context_directional_industry_percentile
                FROM symbol_day_sleeve_scores
                GROUP BY source_day_label, symbol
            )
            SELECT
                snap.*,
                field_pivot.adrp_raw,
                field_pivot.adrp_directional_universe_percentile,
                field_pivot.atrp_directional_universe_percentile,
                field_pivot.relative_volume_directional_universe_percentile,
                field_pivot.value_traded_directional_universe_percentile,
                field_pivot.volatility_month_directional_universe_percentile,
                field_pivot.adrp_directional_industry_percentile,
                field_pivot.relative_volume_directional_industry_percentile,
                field_pivot.value_traded_directional_industry_percentile,
                sleeve_pivot.volatility_core_directional_universe_percentile,
                sleeve_pivot.liquidity_core_directional_universe_percentile,
                sleeve_pivot.momentum_context_directional_universe_percentile,
                sleeve_pivot.volatility_core_directional_industry_percentile,
                sleeve_pivot.liquidity_core_directional_industry_percentile,
                sleeve_pivot.momentum_context_directional_industry_percentile,
                lbl.* EXCLUDE (
                    source_database_path,
                    source_day_label,
                    source_date,
                    run_id,
                    run_created_at_utc,
                    symbol,
                    bare_ticker,
                    company_name,
                    exchange,
                    country,
                    close_price,
                    market_cap_basic,
                    sector,
                    industry,
                    type,
                    is_primary
                )
            FROM symbol_day_feature_snapshot AS snap
            LEFT JOIN field_pivot
                ON field_pivot.source_day_label = snap.source_day_label
                AND field_pivot.symbol = snap.symbol
            LEFT JOIN sleeve_pivot
                ON sleeve_pivot.source_day_label = snap.source_day_label
                AND sleeve_pivot.symbol = snap.symbol
            LEFT JOIN symbol_day_forward_labels AS lbl
                ON lbl.source_day_label = snap.source_day_label
                AND lbl.symbol = snap.symbol
            """)
        connection.execute("""
            CREATE OR REPLACE TEMP VIEW vol_liq_inputs_scoped AS
            SELECT
                base.*,
                custom_group_map.custom_group
            FROM vol_liq_setup_inputs AS base
            LEFT JOIN custom_group_map
                ON UPPER(COALESCE(base.symbol, base.bare_ticker)) = custom_group_map.symbol_key
                OR UPPER(COALESCE(base.bare_ticker, base.symbol)) = custom_group_map.symbol_key
            """)

        occurrences_union_sql = _build_variant_occurrence_union(variants)
        group_expr = allowed_groupings[normalized_group_by]
        filters = ["1 = 1"]
        if normalized_symbols:
            symbol_literals = ", ".join(
                _quote_sql_literal(value) for value in normalized_symbols
            )
            filters.append(
                f"(UPPER(COALESCE(symbol, '')) IN ({symbol_literals}) OR UPPER(COALESCE(bare_ticker, '')) IN ({symbol_literals}))"
            )
        if normalized_group_values:
            group_literals = ", ".join(
                _quote_sql_literal(value.upper()) for value in normalized_group_values
            )
            filters.append(f"UPPER(COALESCE({group_expr}, '')) IN ({group_literals})")
        filter_sql = " AND\n                ".join(filters)

        connection.execute(f"""
            CREATE OR REPLACE TEMP TABLE selected_setup_occurrences AS
            SELECT
                occurrence_source.*,
                {_quote_sql_literal(normalized_group_by)} AS grouping_mode,
                {group_expr} AS group_value
            FROM (
                {occurrences_union_sql}
            ) AS occurrence_source
            WHERE {filter_sql}
            """)
        connection.execute(
            f"COPY (SELECT * FROM selected_setup_occurrences ORDER BY source_date, setup_name, symbol) TO {_quote_sql_literal(occurrences_csv_path.as_posix())} (HEADER, DELIMITER ',')"
        )

        summary_metric_columns_sql = _build_summary_metric_columns(horizons)
        connection.execute(f"""
            CREATE OR REPLACE TEMP TABLE volatility_liquidity_setup_summary AS
            SELECT
                setup_name,
                setup_description,
                grouping_mode,
                group_value,
                COUNT(*) AS occurrence_count,
                COUNT(DISTINCT symbol) AS distinct_symbol_count,
                MIN(source_date) AS first_source_date,
                MAX(source_date) AS last_source_date,
                {summary_metric_columns_sql}
            FROM selected_setup_occurrences
            GROUP BY setup_name, setup_description, grouping_mode, group_value
            HAVING COUNT(*) >= {max(1, int(min_occurrence_count))}
            ORDER BY median_forward_return_{resolved_ranking_horizon}d_pct DESC NULLS LAST,
                occurrence_count DESC,
                setup_name,
                group_value
            """)
        connection.execute(
            f"COPY (SELECT * FROM volatility_liquidity_setup_summary) TO {_quote_sql_literal(summary_csv_path.as_posix())} (HEADER, DELIMITER ',')"
        )

        connection.execute(f"""
            CREATE OR REPLACE TEMP TABLE volatility_liquidity_best_names AS
            SELECT
                setup_name,
                setup_description,
                grouping_mode,
                group_value,
                symbol,
                bare_ticker,
                company_name,
                exchange,
                country,
                sector,
                industry,
                COUNT(*) AS occurrence_count,
                MIN(source_date) AS first_source_date,
                MAX(source_date) AS last_source_date,
                {summary_metric_columns_sql}
            FROM selected_setup_occurrences
            GROUP BY
                setup_name,
                setup_description,
                grouping_mode,
                group_value,
                symbol,
                bare_ticker,
                company_name,
                exchange,
                country,
                sector,
                industry
            HAVING COUNT(*) >= {max(1, int(min_symbol_occurrence_count))}
            ORDER BY median_forward_return_{resolved_ranking_horizon}d_pct DESC NULLS LAST,
                occurrence_count DESC,
                symbol
            """)
        connection.execute(
            f"COPY (SELECT * FROM volatility_liquidity_best_names) TO {_quote_sql_literal(best_names_csv_path.as_posix())} (HEADER, DELIMITER ',')"
        )

        occurrence_row_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM selected_setup_occurrences"
            ).fetchone()[0]
        )
        summary_row_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM volatility_liquidity_setup_summary"
            ).fetchone()[0]
        )
        best_name_row_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM volatility_liquidity_best_names"
            ).fetchone()[0]
        )
    finally:
        connection.close()

    _write_setup_report(
        report_md,
        snapshot_database_path=resolved_snapshot_database_path,
        output_dir=context.output_dir,
        group_by=normalized_group_by,
        group_values=normalized_group_values,
        symbols=normalized_symbols,
        custom_group_csv=(
            resolved_custom_group_csv.as_posix()
            if resolved_custom_group_csv is not None
            else None
        ),
        ranking_horizon=resolved_ranking_horizon,
        horizons=horizons,
        occurrence_row_count=occurrence_row_count,
        summary_row_count=summary_row_count,
        best_name_row_count=best_name_row_count,
        variants=variants,
        summary_csv_path=summary_csv_path,
        best_names_csv_path=best_names_csv_path,
        occurrences_csv_path=occurrences_csv_path,
    )
    manifest_path.write_text(
        json.dumps(
            {
                "snapshot_database_path": resolved_snapshot_database_path.as_posix(),
                "output_dir": context.output_dir.as_posix(),
                "group_by": normalized_group_by,
                "group_values": list(normalized_group_values),
                "symbols": list(normalized_symbols),
                "custom_group_csv": (
                    resolved_custom_group_csv.as_posix()
                    if resolved_custom_group_csv is not None
                    else None
                ),
                "ranking_horizon": resolved_ranking_horizon,
                "min_occurrence_count": min_occurrence_count,
                "min_symbol_occurrence_count": min_symbol_occurrence_count,
                "occurrence_row_count": occurrence_row_count,
                "summary_row_count": summary_row_count,
                "best_name_row_count": best_name_row_count,
                "occurrences_csv_path": occurrences_csv_path.as_posix(),
                "summary_csv_path": summary_csv_path.as_posix(),
                "best_names_csv_path": best_names_csv_path.as_posix(),
                "report_md": report_md.as_posix(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "snapshot_database_path": resolved_snapshot_database_path.as_posix(),
        "output_dir": context.output_dir.as_posix(),
        "occurrences_csv_path": occurrences_csv_path.as_posix(),
        "summary_csv_path": summary_csv_path.as_posix(),
        "best_names_csv_path": best_names_csv_path.as_posix(),
        "report_md": report_md.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "occurrence_row_count": occurrence_row_count,
        "summary_row_count": summary_row_count,
        "best_name_row_count": best_name_row_count,
        "ranking_horizon": resolved_ranking_horizon,
    }
