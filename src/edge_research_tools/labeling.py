from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

DEFAULT_FORWARD_LABEL_HORIZONS: tuple[int, ...] = (3, 5, 10, 20)


def _import_duckdb():
    import duckdb

    return duckdb


def _quote_sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sanitize_horizons(horizons: Iterable[int]) -> tuple[int, ...]:
    cleaned = sorted({int(value) for value in horizons if int(value) > 0})
    if not cleaned:
        raise ValueError("At least one positive horizon is required.")
    return tuple(cleaned)


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


def _build_aggregate_columns(
    horizons: tuple[int, ...], *, target_pct: float, stop_pct: float
) -> str:
    columns: list[str] = []
    for horizon in horizons:
        columns.extend(
            [
                f"MAX(CASE WHEN step.future_offset = {horizon} THEN step.future_return_pct END) AS forward_return_{horizon}d_pct",
                f"MAX(CASE WHEN step.future_offset <= {horizon} THEN step.future_return_pct END) AS max_favorable_excursion_{horizon}d_pct",
                f"MIN(CASE WHEN step.future_offset <= {horizon} THEN step.future_return_pct END) AS max_adverse_excursion_{horizon}d_pct",
                f"MAX(CASE WHEN step.future_offset <= {horizon} THEN step.future_offset END) AS available_forward_days_{horizon}d",
                f"MIN(CASE WHEN step.future_offset <= {horizon} AND step.future_return_pct >= {target_pct} THEN step.future_offset END) AS target_hit_offset_{horizon}d",
                f"MIN(CASE WHEN step.future_offset <= {horizon} AND step.future_return_pct <= {-abs(stop_pct)} THEN step.future_offset END) AS stop_hit_offset_{horizon}d",
            ]
        )
    return ",\n                ".join(columns)


def _build_final_label_columns(horizons: tuple[int, ...]) -> str:
    columns: list[str] = []
    for horizon in horizons:
        columns.extend(
            [
                f"forward_return_{horizon}d_pct",
                f"max_favorable_excursion_{horizon}d_pct",
                f"max_adverse_excursion_{horizon}d_pct",
                f"available_forward_days_{horizon}d",
                f"target_hit_offset_{horizon}d",
                f"stop_hit_offset_{horizon}d",
                f"CASE WHEN target_hit_offset_{horizon}d IS NOT NULL THEN TRUE ELSE FALSE END AS target_hit_{horizon}d_flag",
                f"CASE WHEN stop_hit_offset_{horizon}d IS NOT NULL THEN TRUE ELSE FALSE END AS stop_hit_{horizon}d_flag",
                (
                    f"CASE "
                    f"WHEN target_hit_offset_{horizon}d IS NOT NULL "
                    f"AND (stop_hit_offset_{horizon}d IS NULL OR target_hit_offset_{horizon}d < stop_hit_offset_{horizon}d) "
                    f"THEN TRUE "
                    f"WHEN stop_hit_offset_{horizon}d IS NOT NULL "
                    f"AND (target_hit_offset_{horizon}d IS NULL OR stop_hit_offset_{horizon}d <= target_hit_offset_{horizon}d) "
                    f"THEN FALSE "
                    f"ELSE NULL END AS target_before_stop_{horizon}d_flag"
                ),
            ]
        )
    return ",\n            ".join(columns)


def _build_summary_union_sql(horizons: tuple[int, ...]) -> str:
    selects: list[str] = []
    for horizon in horizons:
        selects.append(f"""
            SELECT
                {horizon} AS horizon_days,
                COUNT(forward_return_{horizon}d_pct) AS sample_count,
                MEDIAN(forward_return_{horizon}d_pct) AS median_forward_return_pct,
                AVG(forward_return_{horizon}d_pct) AS avg_forward_return_pct,
                AVG(
                    CASE
                        WHEN forward_return_{horizon}d_pct IS NULL THEN NULL
                        WHEN forward_return_{horizon}d_pct > 0 THEN 1.0
                        ELSE 0.0
                    END
                ) AS win_rate,
                MEDIAN(max_favorable_excursion_{horizon}d_pct) AS median_mfe_pct,
                MEDIAN(max_adverse_excursion_{horizon}d_pct) AS median_mae_pct,
                AVG(
                    CASE
                        WHEN target_before_stop_{horizon}d_flag IS NULL THEN NULL
                        WHEN target_before_stop_{horizon}d_flag THEN 1.0
                        ELSE 0.0
                    END
                ) AS target_before_stop_rate,
                AVG(
                    CASE
                        WHEN stop_hit_{horizon}d_flag IS NULL THEN NULL
                        WHEN stop_hit_{horizon}d_flag THEN 1.0
                        ELSE 0.0
                    END
                ) AS stop_hit_rate
            FROM symbol_day_forward_labels
            """)
    return "\nUNION ALL\n".join(selects)


def _write_forward_label_report(
    path: Path,
    *,
    snapshot_database_path: Path,
    horizons: tuple[int, ...],
    target_pct: float,
    stop_pct: float,
    label_row_count: int,
    summary_rows: list[tuple[Any, ...]],
    summary_csv_path: Path,
) -> None:
    lines = [
        "# Forward Label Generation",
        "",
        "## Why Run This",
        "",
        "This step converts the symbol-day snapshot into future-only outcome labels so later setup scans can measure return, risk, and target-versus-stop behavior on multiple horizons.",
        "",
        "## How To Run",
        "",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        f"python -m run_edge_research_tools labels --snapshot-db \"{snapshot_database_path.as_posix()}\" --horizons {' '.join(str(h) for h in horizons)} --target-pct {target_pct} --stop-pct {stop_pct}",
        "```",
        "",
        "## Output Meaning",
        "",
        "- `symbol_day_forward_labels` lives inside the snapshot DuckDB and stores one row per symbol-day with future return, MFE, MAE, target hit, stop hit, and target-before-stop flags for each requested horizon.",
        "- `forward_label_horizon_summary.csv` gives the whole-universe label shape per horizon so you can quickly confirm coverage and risk profile before running setup scans.",
        "",
        "## Usage",
        "",
        "- Run this immediately after a snapshot build.",
        "- Use the same snapshot DB for later setup passes so the labels and feature tables stay aligned.",
        "- If you want stricter risk templates, rerun labels with different target and stop percentages.",
        "",
        "## Scope",
        "",
        f"- snapshot_database_path: `{snapshot_database_path.as_posix()}`",
        f"- horizons: {', '.join(str(h) for h in horizons)}",
        f"- target_pct: {target_pct}",
        f"- stop_pct: {stop_pct}",
        f"- label_row_count: {label_row_count}",
        f"- summary_csv_path: `{summary_csv_path.as_posix()}`",
        "",
        "## Horizon Summary",
        "",
        "| Horizon | Sample Count | Median Return % | Avg Return % | Win Rate | Median MFE % | Median MAE % | Target Before Stop Rate | Stop Hit Rate |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in summary_rows:
        lines.append(
            "| "
            f"{row[0]}d | {row[1]} | {row[2] if row[2] is not None else ''} | {row[3] if row[3] is not None else ''} | "
            f"{row[4] if row[4] is not None else ''} | {row[5] if row[5] is not None else ''} | {row[6] if row[6] is not None else ''} | "
            f"{row[7] if row[7] is not None else ''} | {row[8] if row[8] is not None else ''} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_forward_label_generation(
    *,
    snapshot_database_path: str | Path,
    horizons: Iterable[int] = DEFAULT_FORWARD_LABEL_HORIZONS,
    target_pct: float = 10.0,
    stop_pct: float = 7.0,
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
) -> dict[str, Any]:
    resolved_snapshot_database_path = Path(snapshot_database_path)
    if not resolved_snapshot_database_path.exists():
        raise FileNotFoundError(
            f"Snapshot database was not found: {resolved_snapshot_database_path}"
        )
    resolved_horizons = _sanitize_horizons(horizons)
    snapshot_output_dir = resolved_snapshot_database_path.resolve().parent
    summary_csv_path = snapshot_output_dir / "forward_label_horizon_summary.csv"
    report_md = snapshot_output_dir / "forward_label_report.md"
    manifest_path = snapshot_output_dir / "forward_label_manifest.json"

    duckdb = _import_duckdb()
    connection = duckdb.connect(resolved_snapshot_database_path.as_posix())
    connection.execute(f"SET threads TO {int(duckdb_threads)}")
    connection.execute(f"SET memory_limit = '{memory_limit_gb:.1f}GB'")
    connection.execute("SET preserve_insertion_order = false")
    try:
        if not _table_exists(connection, "symbol_day_feature_snapshot"):
            raise ValueError(
                "symbol_day_feature_snapshot was not found in the provided snapshot database."
            )

        max_horizon = max(resolved_horizons)
        aggregate_columns_sql = _build_aggregate_columns(
            resolved_horizons,
            target_pct=target_pct,
            stop_pct=stop_pct,
        )
        final_columns_sql = _build_final_label_columns(resolved_horizons)

        connection.execute("DROP TABLE IF EXISTS symbol_day_forward_labels")
        connection.execute("DROP TABLE IF EXISTS symbol_day_forward_label_summary")

        connection.execute("""
            CREATE OR REPLACE TEMP TABLE ordered_snapshot AS
            SELECT
                snap.*,
                ROW_NUMBER() OVER (
                    PARTITION BY symbol
                    ORDER BY source_date
                ) AS symbol_day_index
            FROM symbol_day_feature_snapshot AS snap
            WHERE close_price IS NOT NULL
                AND close_price > 0
            """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_ordered_snap_sym_day "
            "ON ordered_snapshot (symbol, symbol_day_index)"
        )
        connection.execute(f"""
            CREATE OR REPLACE TEMP TABLE symbol_day_forward_steps AS
            SELECT
                base.symbol,
                base.symbol_day_index AS base_symbol_day_index,
                future.symbol_day_index - base.symbol_day_index AS future_offset,
                100.0 * ((future.close_price / NULLIF(base.close_price, 0)) - 1.0) AS future_return_pct
            FROM ordered_snapshot AS base
            JOIN ordered_snapshot AS future
                ON future.symbol = base.symbol
                AND future.symbol_day_index > base.symbol_day_index
                AND future.symbol_day_index <= base.symbol_day_index + {max_horizon}
            """)
        connection.execute(f"""
            CREATE OR REPLACE TEMP TABLE symbol_day_forward_label_base AS
            SELECT
                base.source_database_path,
                base.source_day_label,
                base.source_date,
                base.run_id,
                base.run_created_at_utc,
                base.symbol,
                base.bare_ticker,
                base.company_name,
                base.exchange,
                base.country,
                base.close_price,
                base.market_cap_basic,
                base.sector,
                base.industry,
                base.type,
                base.is_primary,
                {aggregate_columns_sql}
            FROM ordered_snapshot AS base
            LEFT JOIN symbol_day_forward_steps AS step
                ON step.symbol = base.symbol
                AND step.base_symbol_day_index = base.symbol_day_index
            GROUP BY
                base.source_database_path,
                base.source_day_label,
                base.source_date,
                base.run_id,
                base.run_created_at_utc,
                base.symbol,
                base.bare_ticker,
                base.company_name,
                base.exchange,
                base.country,
                base.close_price,
                base.market_cap_basic,
                base.sector,
                base.industry,
                base.type,
                base.is_primary
            """)
        connection.execute(f"""
            CREATE TABLE symbol_day_forward_labels AS
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
                close_price,
                market_cap_basic,
                sector,
                industry,
                type,
                is_primary,
                {final_columns_sql}
            FROM symbol_day_forward_label_base
            """)
        connection.execute(f"""
            CREATE TABLE symbol_day_forward_label_summary AS
            {_build_summary_union_sql(resolved_horizons)}
            """)
        connection.execute(
            f"COPY (SELECT * FROM symbol_day_forward_label_summary ORDER BY horizon_days) TO {_quote_sql_literal(summary_csv_path.as_posix())} (HEADER, DELIMITER ',')"
        )
        label_row_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM symbol_day_forward_labels"
            ).fetchone()[0]
        )
        summary_rows = connection.execute(
            "SELECT * FROM symbol_day_forward_label_summary ORDER BY horizon_days"
        ).fetchall()
    finally:
        connection.close()

    _write_forward_label_report(
        report_md,
        snapshot_database_path=resolved_snapshot_database_path,
        horizons=resolved_horizons,
        target_pct=target_pct,
        stop_pct=stop_pct,
        label_row_count=label_row_count,
        summary_rows=summary_rows,
        summary_csv_path=summary_csv_path,
    )
    manifest_path.write_text(
        json.dumps(
            {
                "snapshot_database_path": resolved_snapshot_database_path.as_posix(),
                "horizons": list(resolved_horizons),
                "target_pct": target_pct,
                "stop_pct": stop_pct,
                "label_row_count": label_row_count,
                "summary_csv_path": summary_csv_path.as_posix(),
                "report_md": report_md.as_posix(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "snapshot_database_path": resolved_snapshot_database_path.as_posix(),
        "label_row_count": label_row_count,
        "summary_csv_path": summary_csv_path.as_posix(),
        "report_md": report_md.as_posix(),
        "manifest_path": manifest_path.as_posix(),
    }
