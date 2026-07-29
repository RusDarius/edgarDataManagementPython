from __future__ import annotations

import csv
import json
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    _extract_day_label_from_database_path,
    discover_all_fields_daily_databases,
)

from .config import build_edge_research_run_context, resolve_edge_research_paths
from .taxonomy import SleeveFieldSpec, resolve_sleeve_field_specs

DEFAULT_BASE_FIELD_SPECS: tuple[tuple[str, str, str], ...] = (
    ("name", "company_name", "VARCHAR"),
    ("market", "market", "VARCHAR"),
    ("exchange", "exchange", "VARCHAR"),
    ("country", "country", "VARCHAR"),
    ("close", "close_price", "DOUBLE"),
    ("Perf.5D", "perf_5d", "DOUBLE"),
    ("Perf.1M", "perf_1m", "DOUBLE"),
    ("Perf.3M", "perf_3m", "DOUBLE"),
    ("volume", "volume", "DOUBLE"),
    ("average_volume_30d_calc", "average_volume_30d_calc", "DOUBLE"),
    ("relative_volume_10d_calc", "relative_volume_10d_calc", "DOUBLE"),
    ("Value.Traded", "value_traded", "DOUBLE"),
    ("market_cap_basic", "market_cap_basic", "DOUBLE"),
    ("sector", "sector", "VARCHAR"),
    ("industry", "industry", "VARCHAR"),
    ("type", "type", "VARCHAR"),
    ("is_primary", "is_primary", "VARCHAR"),
)


def _import_duckdb():
    import duckdb

    return duckdb


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _try_parse_day_label(day_label: str) -> datetime | None:
    try:
        return datetime.strptime(day_label, "%d_%m_%Y")
    except ValueError:
        return None


def _day_label_to_iso_date(day_label: str) -> str:
    parsed = _try_parse_day_label(day_label)
    if parsed is None:
        raise ValueError(f"Unsupported day label format: {day_label}")
    return parsed.date().isoformat()


def _optional_field_select(
    *,
    field_name: str,
    alias: str,
    sql_type: str,
    available_columns: set[str],
) -> str:
    if field_name not in available_columns:
        return f"CAST(NULL AS {sql_type}) AS {_quote_identifier(alias)}"
    if sql_type == "DOUBLE":
        return (
            f"TRY_CAST(r.{_quote_identifier(field_name)} AS DOUBLE) "
            f"AS {_quote_identifier(alias)}"
        )
    return (
        f"CAST(r.{_quote_identifier(field_name)} AS VARCHAR) "
        f"AS {_quote_identifier(alias)}"
    )


def _write_day_count_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    headers = ["source_day_label", "row_count"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _write_snapshot_report(
    path: Path,
    *,
    start_day_label: str,
    end_day_label: str,
    row_count: int,
    feature_value_row_count: int,
    sleeve_score_row_count: int,
    database_count: int,
    primary_only: bool,
    min_market_cap_usd: float | None,
    database_path: Path,
    day_counts_csv: Path,
    selected_fields: Sequence[SleeveFieldSpec],
) -> None:
    grouped_fields: dict[str, list[SleeveFieldSpec]] = defaultdict(list)
    for spec in selected_fields:
        grouped_fields[spec.sleeve_name].append(spec)

    lines = [
        "# Symbol-Day Feature Snapshot",
        "",
        "## Why Run This",
        "",
        "This command creates a reusable symbol-day research database from the raw daily TradingView all-fields DuckDB exports.",
        "It is the base layer for later forward labels, setup scans, grouped edge summaries, peer studies, and single-name history reviews.",
        "",
        "## How To Run",
        "",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        "python -m run_edge_research_tools snapshot --start-day-label 29_03_2026 --end-day-label 26_06_2026 --min-market-cap-usd 1000000000",
        "```",
        "",
        "## Scope",
        "",
        f"- start_day_label: {start_day_label}",
        f"- end_day_label: {end_day_label}",
        f"- database_count: {database_count}",
        f"- row_count: {row_count}",
        f"- feature_value_row_count: {feature_value_row_count}",
        f"- sleeve_score_row_count: {sleeve_score_row_count}",
        f"- primary_only: {primary_only}",
        f"- min_market_cap_usd: {min_market_cap_usd if min_market_cap_usd is not None else 'none'}",
        "",
        "## Output Locations",
        "",
        f"- snapshot database: `{database_path.as_posix()}`",
        f"- day counts csv: `{day_counts_csv.as_posix()}`",
        "",
        "## Output Meaning",
        "",
        "- `symbol_day_feature_snapshot`: one row per symbol per day with core context fields such as close, trailing performance, market cap, sector, and industry.",
        "- `symbol_day_feature_values`: one row per symbol per day per selected sleeve field with raw value, universe percentile, universe z-score, and sector/industry-relative percentiles and z-scores.",
        "- `symbol_day_sleeve_scores`: one row per symbol per day per sleeve, averaging the selected field percentiles into a reusable relative-state summary.",
        "",
        "## Usage",
        "",
        "- Run this first for a short range to verify shape and row counts.",
        "- Run it again over the full oldest-to-latest daily range once the shape looks correct.",
        "- Use the resulting database as the input for forward labels and later setup passes.",
        "",
        "## Selected Sleeve Fields",
        "",
    ]

    for sleeve_name in sorted(grouped_fields):
        lines.append(f"### {sleeve_name}")
        lines.append("")
        for spec in grouped_fields[sleeve_name]:
            lines.append(
                "- "
                f"{spec.field_name} | display={spec.display_name} | bucket={spec.semantic_bucket} | "
                f"tier={spec.relevance_tier} | direction={spec.relevance_direction} | "
                f"stability_gate={spec.passes_stability_gate}"
            )
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_feature_value_insert_sql(
    selected_fields: Sequence[SleeveFieldSpec],
) -> str:
    union_selects: list[str] = []
    for spec in selected_fields:
        relevance_score_sql = (
            str(spec.composite_relevance_score)
            if spec.composite_relevance_score is not None
            else "NULL"
        )
        fill_rate_sql = (
            str(spec.predictor_fill_rate)
            if spec.predictor_fill_rate is not None
            else "NULL"
        )
        union_selects.append(f"""
            SELECT
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
                sector,
                industry,
                {_quote_sql_literal(spec.sleeve_name)} AS sleeve_name,
                {_quote_sql_literal(spec.field_name)} AS field_name,
                {_quote_sql_literal(spec.display_name)} AS display_name,
                {_quote_sql_literal(spec.semantic_bucket)} AS semantic_bucket,
                {_quote_sql_literal(spec.indicator_family)} AS indicator_family,
                {_quote_sql_literal(spec.relevance_tier)} AS relevance_tier,
                {_quote_sql_literal(spec.relevance_direction)} AS relevance_direction,
                {relevance_score_sql} AS composite_relevance_score,
                {fill_rate_sql} AS predictor_fill_rate,
                {str(spec.passes_stability_gate).upper()} AS passes_stability_gate,
                TRY_CAST({_quote_identifier(spec.field_name)} AS DOUBLE) AS raw_value
            FROM day_symbol_stage
            WHERE TRY_CAST({_quote_identifier(spec.field_name)} AS DOUBLE) IS NOT NULL
            """)
    return "\nUNION ALL\n".join(union_selects)


def _ensure_snapshot_tables(connection: Any) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS symbol_day_feature_snapshot (
            source_database_path VARCHAR,
            source_day_label VARCHAR,
            source_date DATE,
            run_id VARCHAR,
            run_created_at_utc TIMESTAMP,
            symbol VARCHAR,
            bare_ticker VARCHAR,
            company_name VARCHAR,
            market VARCHAR,
            exchange VARCHAR,
            country VARCHAR,
            close_price DOUBLE,
            perf_5d DOUBLE,
            perf_1m DOUBLE,
            perf_3m DOUBLE,
            volume DOUBLE,
            average_volume_30d_calc DOUBLE,
            relative_volume_10d_calc DOUBLE,
            value_traded DOUBLE,
            market_cap_basic DOUBLE,
            sector VARCHAR,
            industry VARCHAR,
            type VARCHAR,
            is_primary VARCHAR
        )
        """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS symbol_day_feature_values (
            source_database_path VARCHAR,
            source_day_label VARCHAR,
            source_date DATE,
            run_id VARCHAR,
            run_created_at_utc TIMESTAMP,
            symbol VARCHAR,
            bare_ticker VARCHAR,
            company_name VARCHAR,
            exchange VARCHAR,
            country VARCHAR,
            sector VARCHAR,
            industry VARCHAR,
            sleeve_name VARCHAR,
            field_name VARCHAR,
            display_name VARCHAR,
            semantic_bucket VARCHAR,
            indicator_family VARCHAR,
            relevance_tier VARCHAR,
            relevance_direction VARCHAR,
            composite_relevance_score DOUBLE,
            predictor_fill_rate DOUBLE,
            passes_stability_gate BOOLEAN,
            raw_value DOUBLE,
            universe_count BIGINT,
            universe_percentile DOUBLE,
            universe_zscore DOUBLE,
            directional_universe_percentile DOUBLE,
            sector_count BIGINT,
            sector_percentile DOUBLE,
            sector_zscore DOUBLE,
            directional_sector_percentile DOUBLE,
            industry_count BIGINT,
            industry_percentile DOUBLE,
            industry_zscore DOUBLE,
            directional_industry_percentile DOUBLE
        )
        """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS symbol_day_sleeve_scores (
            source_database_path VARCHAR,
            source_day_label VARCHAR,
            source_date DATE,
            run_id VARCHAR,
            run_created_at_utc TIMESTAMP,
            symbol VARCHAR,
            bare_ticker VARCHAR,
            company_name VARCHAR,
            exchange VARCHAR,
            country VARCHAR,
            sector VARCHAR,
            industry VARCHAR,
            sleeve_name VARCHAR,
            sleeve_field_count BIGINT,
            avg_universe_percentile DOUBLE,
            avg_universe_zscore DOUBLE,
            avg_directional_universe_percentile DOUBLE,
            avg_sector_percentile DOUBLE,
            avg_directional_sector_percentile DOUBLE,
            avg_industry_percentile DOUBLE,
            avg_directional_industry_percentile DOUBLE
        )
        """)


def _append_snapshot_days_to_connection(
    output_conn: Any,
    *,
    resolved_databases: Sequence[Path],
    selected_fields: Sequence[SleeveFieldSpec],
    primary_only: bool,
    min_market_cap_usd: float | None,
    checkpoint_every_n: int,
    progress_offset: int = 0,
    progress_total: int | None = None,
) -> None:
    stage_base_aliases = {
        alias_name for _, alias_name, _ in DEFAULT_BASE_FIELD_SPECS
    }
    dynamic_stage_fields = [
        spec for spec in selected_fields if spec.field_name not in stage_base_aliases
    ]
    feature_value_insert_sql = _build_feature_value_insert_sql(selected_fields)
    n_total = progress_total if progress_total is not None else len(resolved_databases)

    for index, database_path in enumerate(resolved_databases):
        _t_day_start = time.monotonic()
        source_day_label = _extract_day_label_from_database_path(database_path)
        source_date = _day_label_to_iso_date(source_day_label)
        display_index = progress_offset + index + 1
        print(
            f"  [{display_index}/{n_total}] {source_day_label} ...",
            end="",
            flush=True,
        )
        alias = f"src_{progress_offset + index}"
        output_conn.execute(
            f"ATTACH {_quote_sql_literal(database_path.as_posix())} "
            f"AS {_quote_identifier(alias)} (READ_ONLY)"
        )
        available_columns = {
            str(row[0])
            for row in output_conn.execute(
                "SELECT column_name FROM duckdb_columns() "
                f"WHERE database_name = {_quote_sql_literal(alias)} "
                "AND table_name = 'all_fields_rows'"
            ).fetchall()
        }
        try:
            base_select_fields_sql = ",\n                            ".join(
                _optional_field_select(
                    field_name=field_name,
                    alias=alias_name,
                    sql_type=sql_type,
                    available_columns=available_columns,
                )
                for field_name, alias_name, sql_type in DEFAULT_BASE_FIELD_SPECS
            )
            dynamic_select_fields_sql = ",\n                            ".join(
                _optional_field_select(
                    field_name=spec.field_name,
                    alias=spec.field_name,
                    sql_type="DOUBLE",
                    available_columns=available_columns,
                )
                for spec in dynamic_stage_fields
            )
            filters = ["1 = 1"]
            if primary_only and "is_primary" in available_columns:
                filters.append(
                    "COALESCE(LOWER(TRIM(CAST(r.is_primary AS VARCHAR))), '') "
                    "IN ('true', '1', 'yes', 'y')"
                )
            if (
                min_market_cap_usd is not None
                and "market_cap_basic" in available_columns
            ):
                filters.append(
                    "TRY_CAST(r.market_cap_basic AS DOUBLE) "
                    f">= {float(min_market_cap_usd)}"
                )
            filter_sql = " AND\n                        ".join(filters)
            stage_select_blocks = [base_select_fields_sql]
            if dynamic_select_fields_sql:
                stage_select_blocks.append(dynamic_select_fields_sql)
            all_stage_fields_sql = ",\n                            ".join(
                block for block in stage_select_blocks if block
            )
            output_conn.execute(f"""
                CREATE OR REPLACE TEMP TABLE day_symbol_stage AS
                SELECT * EXCLUDE (row_num)
                FROM (
                    SELECT
                        {_quote_sql_literal(database_path.as_posix())} AS source_database_path,
                        {_quote_sql_literal(source_day_label)} AS source_day_label,
                        CAST({_quote_sql_literal(source_date)} AS DATE) AS source_date,
                        CAST(r.run_id AS VARCHAR) AS run_id,
                        m.created_at_utc AS run_created_at_utc,
                        CAST(r.symbol AS VARCHAR) AS symbol,
                        COALESCE(
                            NULLIF(split_part(CAST(r.symbol AS VARCHAR), ':', 2), ''),
                            CAST(r.symbol AS VARCHAR)
                        ) AS bare_ticker,
                        {all_stage_fields_sql},
                        ROW_NUMBER() OVER (
                            PARTITION BY r.symbol
                            ORDER BY m.created_at_utc DESC NULLS LAST,
                                r.run_id DESC,
                                r.row_number DESC
                        ) AS row_num
                    FROM {_quote_identifier(alias)}.all_fields_rows AS r
                    LEFT JOIN {_quote_identifier(alias)}.run_metadata AS m USING (run_id)
                    WHERE {filter_sql}
                )
                WHERE row_num = 1
                """)
            output_conn.execute("""
                INSERT INTO symbol_day_feature_snapshot
                SELECT
                    source_database_path,
                    source_day_label,
                    source_date,
                    run_id,
                    run_created_at_utc,
                    symbol,
                    bare_ticker,
                    company_name,
                    market,
                    exchange,
                    country,
                    close_price,
                    perf_5d,
                    perf_1m,
                    perf_3m,
                    volume,
                    average_volume_30d_calc,
                    relative_volume_10d_calc,
                    value_traded,
                    market_cap_basic,
                    sector,
                    industry,
                    type,
                    is_primary
                FROM day_symbol_stage
                """)
            output_conn.execute(f"""
                CREATE OR REPLACE TEMP TABLE day_feature_windowed AS
                WITH day_raw AS (
                    {feature_value_insert_sql}
                ),
                day_base AS (
                    SELECT
                        dr.*,
                        COUNT(*) OVER (PARTITION BY field_name) AS universe_count,
                        AVG(raw_value) OVER (PARTITION BY field_name) AS universe_avg,
                        STDDEV_SAMP(raw_value) OVER (PARTITION BY field_name) AS universe_stddev,
                        COUNT(*) OVER (PARTITION BY field_name, sector) AS sector_count,
                        AVG(raw_value) OVER (PARTITION BY field_name, sector) AS sector_avg,
                        STDDEV_SAMP(raw_value) OVER (PARTITION BY field_name, sector) AS sector_stddev,
                        COUNT(*) OVER (PARTITION BY field_name, industry) AS industry_count,
                        AVG(raw_value) OVER (PARTITION BY field_name, industry) AS industry_avg,
                        STDDEV_SAMP(raw_value) OVER (PARTITION BY field_name, industry) AS industry_stddev
                    FROM day_raw AS dr
                )
                SELECT
                    source_database_path, source_day_label, source_date,
                    run_id, run_created_at_utc, symbol, bare_ticker,
                    company_name, exchange, country, sector, industry,
                    sleeve_name, field_name, display_name, semantic_bucket,
                    indicator_family, relevance_tier, relevance_direction,
                    composite_relevance_score, predictor_fill_rate, passes_stability_gate,
                    raw_value, universe_count,
                    CASE WHEN universe_count > 1
                        THEN PERCENT_RANK() OVER (PARTITION BY field_name ORDER BY raw_value)
                        ELSE NULL END AS universe_percentile,
                    CASE WHEN universe_stddev IS NOT NULL AND universe_stddev > 0
                        THEN (raw_value - universe_avg) / universe_stddev
                        ELSE NULL END AS universe_zscore,
                    CASE
                        WHEN universe_count > 1 AND LOWER(relevance_direction) = 'bearish'
                            THEN 1.0 - PERCENT_RANK() OVER (PARTITION BY field_name ORDER BY raw_value)
                        WHEN universe_count > 1
                            THEN PERCENT_RANK() OVER (PARTITION BY field_name ORDER BY raw_value)
                        ELSE NULL
                    END AS directional_universe_percentile,
                    sector_count,
                    CASE WHEN sector_count > 1
                        THEN PERCENT_RANK() OVER (PARTITION BY field_name, sector ORDER BY raw_value)
                        ELSE NULL END AS sector_percentile,
                    CASE WHEN sector_stddev IS NOT NULL AND sector_stddev > 0
                        THEN (raw_value - sector_avg) / sector_stddev
                        ELSE NULL END AS sector_zscore,
                    CASE
                        WHEN sector_count > 1 AND LOWER(relevance_direction) = 'bearish'
                            THEN 1.0 - PERCENT_RANK() OVER (PARTITION BY field_name, sector ORDER BY raw_value)
                        WHEN sector_count > 1
                            THEN PERCENT_RANK() OVER (PARTITION BY field_name, sector ORDER BY raw_value)
                        ELSE NULL
                    END AS directional_sector_percentile,
                    industry_count,
                    CASE WHEN industry_count > 1
                        THEN PERCENT_RANK() OVER (PARTITION BY field_name, industry ORDER BY raw_value)
                        ELSE NULL END AS industry_percentile,
                    CASE WHEN industry_stddev IS NOT NULL AND industry_stddev > 0
                        THEN (raw_value - industry_avg) / industry_stddev
                        ELSE NULL END AS industry_zscore,
                    CASE
                        WHEN industry_count > 1 AND LOWER(relevance_direction) = 'bearish'
                            THEN 1.0 - PERCENT_RANK() OVER (PARTITION BY field_name, industry ORDER BY raw_value)
                        WHEN industry_count > 1
                            THEN PERCENT_RANK() OVER (PARTITION BY field_name, industry ORDER BY raw_value)
                        ELSE NULL
                    END AS directional_industry_percentile
                FROM day_base
                """)
            output_conn.execute(
                "INSERT INTO symbol_day_feature_values SELECT * FROM day_feature_windowed"
            )
            output_conn.execute("""
                INSERT INTO symbol_day_sleeve_scores
                SELECT
                    source_database_path, source_day_label, source_date,
                    run_id, run_created_at_utc, symbol, bare_ticker,
                    company_name, exchange, country, sector, industry,
                    sleeve_name,
                    COUNT(*) AS sleeve_field_count,
                    AVG(universe_percentile) AS avg_universe_percentile,
                    AVG(universe_zscore) AS avg_universe_zscore,
                    AVG(directional_universe_percentile) AS avg_directional_universe_percentile,
                    AVG(sector_percentile) AS avg_sector_percentile,
                    AVG(directional_sector_percentile) AS avg_directional_sector_percentile,
                    AVG(industry_percentile) AS avg_industry_percentile,
                    AVG(directional_industry_percentile) AS avg_directional_industry_percentile
                FROM day_feature_windowed
                GROUP BY
                    source_database_path, source_day_label, source_date,
                    run_id, run_created_at_utc, symbol, bare_ticker,
                    company_name, exchange, country, sector, industry,
                    sleeve_name
                """)
            output_conn.execute("DROP TABLE day_feature_windowed")
            output_conn.execute("DROP TABLE day_symbol_stage")
            _elapsed_day = time.monotonic() - _t_day_start
            print(f" done in {_elapsed_day:.1f}s", flush=True)
        finally:
            output_conn.execute(f"DETACH {_quote_identifier(alias)}")
        if checkpoint_every_n > 0 and (index + 1) % checkpoint_every_n == 0:
            print(f"  CHECKPOINT after {source_day_label} ...", flush=True)
            output_conn.execute("CHECKPOINT")


def _write_manifest(
    path: Path,
    *,
    start_day_label: str,
    end_day_label: str,
    all_fields_root: Path,
    resolved_databases: Sequence[Path],
    primary_only: bool,
    min_market_cap_usd: float | None,
    row_count: int,
    feature_value_row_count: int,
    sleeve_score_row_count: int,
    output_database_path: Path,
    day_counts_csv: Path,
    report_md: Path,
    selected_fields: Sequence[SleeveFieldSpec],
) -> None:
    path.write_text(
        json.dumps(
            {
                "start_day_label": start_day_label,
                "end_day_label": end_day_label,
                "all_fields_root": all_fields_root.as_posix(),
                "database_paths": [
                    db_path.as_posix() for db_path in resolved_databases
                ],
                "database_count": len(resolved_databases),
                "primary_only": primary_only,
                "min_market_cap_usd": min_market_cap_usd,
                "row_count": row_count,
                "feature_value_row_count": feature_value_row_count,
                "sleeve_score_row_count": sleeve_score_row_count,
                "output_database_path": output_database_path.as_posix(),
                "day_counts_csv": day_counts_csv.as_posix(),
                "report_md": report_md.as_posix(),
                "selected_fields": [asdict(spec) for spec in selected_fields],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def run_symbol_day_feature_snapshot(
    *,
    start_day_label: str,
    end_day_label: str,
    all_fields_root: str | Path | None = None,
    taxonomy_root: str | Path | None = None,
    output_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    primary_only: bool = True,
    min_market_cap_usd: float | None = None,
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
    checkpoint_every_n: int = 10,
) -> dict[str, Any]:
    paths = resolve_edge_research_paths(
        all_fields_root=all_fields_root,
        taxonomy_root=taxonomy_root,
        output_root=output_root,
    )
    if output_dir is not None:
        resolved_output_dir = Path(output_dir)
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        context_output_dir = resolved_output_dir
    else:
        context = build_edge_research_run_context(
            prefix="edge_feature_snapshot",
            output_root=paths.foundation_root,
        )
        context_output_dir = context.output_dir
    resolved_databases = discover_all_fields_daily_databases(
        all_fields_root=paths.all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )
    if not resolved_databases:
        raise ValueError(
            "No daily all-fields databases resolved for the requested date range."
        )
    selected_fields = resolve_sleeve_field_specs(paths.taxonomy_root)
    if not selected_fields:
        raise ValueError(
            "No taxonomy-driven sleeve fields were resolved from predictor_eligible.csv."
        )

    output_database_path = context_output_dir / "symbol_day_feature_snapshot.duckdb"
    day_counts_csv = context_output_dir / "symbol_day_snapshot_day_counts.csv"
    report_md = context_output_dir / "symbol_day_feature_snapshot_report.md"
    manifest_path = context_output_dir / "symbol_day_feature_snapshot_manifest.json"

    duckdb = _import_duckdb()
    output_conn = duckdb.connect(output_database_path.as_posix())
    output_conn.execute(f"SET threads TO {int(duckdb_threads)}")
    output_conn.execute(f"SET memory_limit = '{memory_limit_gb:.1f}GB'")
    output_conn.execute("SET preserve_insertion_order = false")
    try:
        if output_dir is not None and output_database_path.exists():
            output_conn.execute("DROP TABLE IF EXISTS symbol_day_feature_snapshot")
            output_conn.execute("DROP TABLE IF EXISTS symbol_day_feature_values")
            output_conn.execute("DROP TABLE IF EXISTS symbol_day_sleeve_scores")
        _ensure_snapshot_tables(output_conn)

        n_total = len(resolved_databases)
        _t_suite_start = time.monotonic()
        _append_snapshot_days_to_connection(
            output_conn,
            resolved_databases=resolved_databases,
            selected_fields=selected_fields,
            primary_only=primary_only,
            min_market_cap_usd=min_market_cap_usd,
            checkpoint_every_n=checkpoint_every_n,
            progress_total=n_total,
        )
        _elapsed_total = time.monotonic() - _t_suite_start
        print(
            f"Daily loop complete: {n_total} days in {_elapsed_total:.1f}s total",
            flush=True,
        )

        row_count = int(
            output_conn.execute(
                "SELECT COUNT(*) FROM symbol_day_feature_snapshot"
            ).fetchone()[0]
        )
        feature_value_row_count = int(
            output_conn.execute(
                "SELECT COUNT(*) FROM symbol_day_feature_values"
            ).fetchone()[0]
        )
        sleeve_score_row_count = int(
            output_conn.execute(
                "SELECT COUNT(*) FROM symbol_day_sleeve_scores"
            ).fetchone()[0]
        )
        day_count_rows = [
            {"source_day_label": row[0], "row_count": int(row[1])}
            for row in output_conn.execute("""
                SELECT source_day_label, COUNT(*) AS row_count
                FROM symbol_day_feature_snapshot
                GROUP BY source_day_label
                ORDER BY MIN(source_date)
                """).fetchall()
        ]
    finally:
        output_conn.close()

    _write_day_count_csv(day_counts_csv, day_count_rows)
    _write_snapshot_report(
        report_md,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        row_count=row_count,
        feature_value_row_count=feature_value_row_count,
        sleeve_score_row_count=sleeve_score_row_count,
        database_count=len(resolved_databases),
        primary_only=primary_only,
        min_market_cap_usd=min_market_cap_usd,
        database_path=output_database_path,
        day_counts_csv=day_counts_csv,
        selected_fields=selected_fields,
    )
    _write_manifest(
        manifest_path,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        all_fields_root=paths.all_fields_root,
        resolved_databases=resolved_databases,
        primary_only=primary_only,
        min_market_cap_usd=min_market_cap_usd,
        row_count=row_count,
        feature_value_row_count=feature_value_row_count,
        sleeve_score_row_count=sleeve_score_row_count,
        output_database_path=output_database_path,
        day_counts_csv=day_counts_csv,
        report_md=report_md,
        selected_fields=selected_fields,
    )
    return {
        "output_dir": context_output_dir.as_posix(),
        "database_path": output_database_path.as_posix(),
        "day_counts_csv": day_counts_csv.as_posix(),
        "report_md": report_md.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "row_count": row_count,
        "feature_value_row_count": feature_value_row_count,
        "sleeve_score_row_count": sleeve_score_row_count,
        "database_count": len(resolved_databases),
    }


def extend_symbol_day_feature_snapshot(
    *,
    snapshot_database_path: str | Path,
    database_paths: Sequence[str | Path] | None = None,
    all_fields_root: str | Path | None = None,
    taxonomy_root: str | Path | None = None,
    primary_only: bool = True,
    min_market_cap_usd: float | None = None,
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
    checkpoint_every_n: int = 10,
) -> dict[str, Any]:
    """Append or refresh all-fields days on an existing symbol-day snapshot database.

    Existing ``source_day_label`` rows for the provided daily databases are deleted
    first, so calling this with a day that already exists (e.g. a newer same-day
    TradingView all-fields scan) replaces that day with the latest run.
    """
    resolved_snapshot_database_path = Path(snapshot_database_path)
    if not resolved_snapshot_database_path.exists():
        raise FileNotFoundError(
            f"Snapshot database was not found: {resolved_snapshot_database_path}"
        )
    snapshot_output_dir = resolved_snapshot_database_path.parent
    manifest_path = snapshot_output_dir / "symbol_day_feature_snapshot_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            "Snapshot manifest is required for extension: "
            f"{manifest_path.as_posix()}"
        )
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    start_day_label = str(manifest_payload["start_day_label"])

    paths = resolve_edge_research_paths(
        all_fields_root=all_fields_root,
        taxonomy_root=taxonomy_root,
    )
    if database_paths is None:
        raise ValueError("database_paths is required for extend_symbol_day_feature_snapshot.")
    resolved_databases = [Path(path) for path in database_paths]
    if not resolved_databases:
        return {
            "database_path": resolved_snapshot_database_path.as_posix(),
            "days_appended": 0,
            "start_day_label": start_day_label,
            "end_day_label": str(manifest_payload["end_day_label"]),
        }

    selected_fields = resolve_sleeve_field_specs(paths.taxonomy_root)
    if not selected_fields:
        raise ValueError(
            "No taxonomy-driven sleeve fields were resolved from predictor_eligible.csv."
        )

    day_counts_csv = snapshot_output_dir / "symbol_day_snapshot_day_counts.csv"
    report_md = snapshot_output_dir / "symbol_day_feature_snapshot_report.md"

    duckdb = _import_duckdb()
    output_conn = duckdb.connect(resolved_snapshot_database_path.as_posix())
    output_conn.execute(f"SET threads TO {int(duckdb_threads)}")
    output_conn.execute(f"SET memory_limit = '{memory_limit_gb:.1f}GB'")
    output_conn.execute("SET preserve_insertion_order = false")
    try:
        _ensure_snapshot_tables(output_conn)
        append_day_labels = [
            _extract_day_label_from_database_path(path) for path in resolved_databases
        ]
        placeholders = ", ".join(
            "'" + label.replace("'", "''") + "'" for label in append_day_labels
        )
        for table_name in (
            "symbol_day_feature_values",
            "symbol_day_sleeve_scores",
            "symbol_day_feature_snapshot",
        ):
            output_conn.execute(
                f"DELETE FROM {table_name} WHERE source_day_label IN ({placeholders})"
            )

        existing_day_count = int(
            output_conn.execute(
                "SELECT COUNT(DISTINCT source_day_label) FROM symbol_day_feature_snapshot"
            ).fetchone()[0]
        )
        _append_snapshot_days_to_connection(
            output_conn,
            resolved_databases=resolved_databases,
            selected_fields=selected_fields,
            primary_only=primary_only,
            min_market_cap_usd=min_market_cap_usd,
            checkpoint_every_n=checkpoint_every_n,
            progress_offset=existing_day_count,
            progress_total=existing_day_count + len(resolved_databases),
        )
        output_conn.execute("CHECKPOINT")

        row_count = int(
            output_conn.execute(
                "SELECT COUNT(*) FROM symbol_day_feature_snapshot"
            ).fetchone()[0]
        )
        feature_value_row_count = int(
            output_conn.execute(
                "SELECT COUNT(*) FROM symbol_day_feature_values"
            ).fetchone()[0]
        )
        sleeve_score_row_count = int(
            output_conn.execute(
                "SELECT COUNT(*) FROM symbol_day_sleeve_scores"
            ).fetchone()[0]
        )
        day_count_rows = [
            {"source_day_label": row[0], "row_count": int(row[1])}
            for row in output_conn.execute("""
                SELECT source_day_label, COUNT(*) AS row_count
                FROM symbol_day_feature_snapshot
                GROUP BY source_day_label
                ORDER BY MIN(source_date)
                """).fetchall()
        ]
        end_day_label = str(day_count_rows[-1]["source_day_label"])
    finally:
        output_conn.close()

    _write_day_count_csv(day_counts_csv, day_count_rows)
    _write_snapshot_report(
        report_md,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        row_count=row_count,
        feature_value_row_count=feature_value_row_count,
        sleeve_score_row_count=sleeve_score_row_count,
        database_count=len(day_count_rows),
        primary_only=primary_only,
        min_market_cap_usd=min_market_cap_usd,
        database_path=resolved_snapshot_database_path,
        day_counts_csv=day_counts_csv,
        selected_fields=selected_fields,
    )
    _write_manifest(
        manifest_path,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        all_fields_root=paths.all_fields_root,
        resolved_databases=resolved_databases,
        primary_only=primary_only,
        min_market_cap_usd=min_market_cap_usd,
        row_count=row_count,
        feature_value_row_count=feature_value_row_count,
        sleeve_score_row_count=sleeve_score_row_count,
        output_database_path=resolved_snapshot_database_path,
        day_counts_csv=day_counts_csv,
        report_md=report_md,
        selected_fields=selected_fields,
    )
    return {
        "output_dir": snapshot_output_dir.as_posix(),
        "database_path": resolved_snapshot_database_path.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "days_appended": len(resolved_databases),
        "start_day_label": start_day_label,
        "end_day_label": end_day_label,
        "row_count": row_count,
        "feature_value_row_count": feature_value_row_count,
        "sleeve_score_row_count": sleeve_score_row_count,
    }
