"""Persist financial projection runs to DuckDB + parquet."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_output_layout(
    *,
    output_root: Path,
    created_at_utc: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, Path | str]:
    stamp = created_at_utc or datetime.now(tz=timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    day_label = stamp.astimezone(timezone.utc).strftime("%d_%m_%Y")
    resolved_run_id = run_id or f"finproj_{stamp.strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = Path(output_root) / day_label / resolved_run_id
    parquet_dir = run_dir / "parquet"
    database_path = run_dir / f"{resolved_run_id}.duckdb"
    return {
        "day_label": day_label,
        "run_id": resolved_run_id,
        "run_dir": run_dir,
        "parquet_dir": parquet_dir,
        "database_path": database_path,
    }


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _union_fieldnames(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    if not rows:
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def persist_projection_run(
    *,
    output_root: str | Path,
    summaries: Sequence[Mapping[str, Any]],
    year_grids: Sequence[Mapping[str, Any]],
    run_metadata: Mapping[str, Any],
    export_parquet: bool = True,
    created_at_utc: datetime | None = None,
    run_id: str | None = None,
    summary_table: str = "projection_summary",
    year_grid_table: str = "projection_year_grid",
) -> dict[str, Any]:
    duckdb = _import_duckdb()
    layout = build_output_layout(
        output_root=Path(output_root),
        created_at_utc=created_at_utc,
        run_id=run_id,
    )
    run_dir = Path(layout["run_dir"])
    parquet_dir = Path(layout["parquet_dir"])
    database_path = Path(layout["database_path"])
    run_dir.mkdir(parents=True, exist_ok=True)
    if export_parquet:
        parquet_dir.mkdir(parents=True, exist_ok=True)

    metadata_row = {
        **dict(run_metadata),
        "run_id": layout["run_id"],
        "day_label": layout["day_label"],
        "created_at_utc": (
            created_at_utc or datetime.now(tz=timezone.utc)
        )
        .astimezone(timezone.utc)
        .isoformat(),
        "summary_row_count": len(summaries),
        "year_grid_row_count": len(year_grids),
        "summary_table": summary_table,
        "year_grid_table": year_grid_table,
    }

    summary_csv = run_dir / f"{summary_table}.csv"
    year_csv = run_dir / f"{year_grid_table}.csv"
    _write_csv(summary_csv, summaries, _union_fieldnames(summaries))
    _write_csv(year_csv, year_grids, _union_fieldnames(year_grids))

    if database_path.exists():
        database_path.unlink()

    conn = duckdb.connect(str(database_path))
    parquet_paths: dict[str, str] = {}
    try:
        conn.execute(
            """
            CREATE TABLE run_metadata (
                run_id VARCHAR,
                day_label VARCHAR,
                created_at_utc VARCHAR,
                payload_json VARCHAR
            )
            """
        )
        conn.execute(
            "INSERT INTO run_metadata VALUES (?, ?, ?, ?)",
            [
                metadata_row["run_id"],
                metadata_row["day_label"],
                metadata_row["created_at_utc"],
                json.dumps(metadata_row, default=str),
            ],
        )

        if summary_csv.exists() and summary_csv.stat().st_size > 0:
            conn.execute(
                f"CREATE TABLE {summary_table} AS "
                f"SELECT * FROM read_csv_auto({_q(summary_csv.as_posix())}, HEADER=TRUE)"
            )
        else:
            conn.execute(f"CREATE TABLE {summary_table}(symbol VARCHAR)")

        if year_csv.exists() and year_csv.stat().st_size > 0:
            conn.execute(
                f"CREATE TABLE {year_grid_table} AS "
                f"SELECT * FROM read_csv_auto({_q(year_csv.as_posix())}, HEADER=TRUE)"
            )
        else:
            conn.execute(f"CREATE TABLE {year_grid_table}(symbol VARCHAR)")

        if export_parquet:
            summary_parquet = parquet_dir / f"{summary_table}.parquet"
            year_parquet = parquet_dir / f"{year_grid_table}.parquet"
            if summaries:
                conn.execute(
                    f"COPY {summary_table} TO {_q(summary_parquet.as_posix())} "
                    "(FORMAT PARQUET)"
                )
                parquet_paths[summary_table] = summary_parquet.as_posix()
            if year_grids:
                conn.execute(
                    f"COPY {year_grid_table} TO {_q(year_parquet.as_posix())} "
                    "(FORMAT PARQUET)"
                )
                parquet_paths[year_grid_table] = year_parquet.as_posix()
    finally:
        conn.close()

    metadata_json_path = run_dir / "run_metadata.json"
    metadata_json_path.write_text(
        json.dumps(metadata_row, indent=2, default=str), encoding="utf-8"
    )

    return {
        "run_id": layout["run_id"],
        "day_label": layout["day_label"],
        "run_dir": run_dir.as_posix(),
        "database_path": database_path.as_posix(),
        "parquet_dir": parquet_dir.as_posix(),
        "summary_csv": summary_csv.as_posix(),
        "year_grid_csv": year_csv.as_posix(),
        "metadata_json": metadata_json_path.as_posix(),
        "parquet_paths": parquet_paths,
        "summary_row_count": len(summaries),
        "year_grid_row_count": len(year_grids),
    }


def persist_growth_run(
    *,
    output_root: str | Path,
    summaries: Sequence[Mapping[str, Any]],
    year_grids: Sequence[Mapping[str, Any]],
    run_metadata: Mapping[str, Any],
    export_parquet: bool = True,
    created_at_utc: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Persist growth-lane outputs under ``fingrowth_*`` run ids."""
    stamp = created_at_utc or datetime.now(tz=timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    resolved_run_id = run_id or f"fingrowth_{stamp.strftime('%Y%m%dT%H%M%SZ')}"
    return persist_projection_run(
        output_root=output_root,
        summaries=summaries,
        year_grids=year_grids,
        run_metadata=run_metadata,
        export_parquet=export_parquet,
        created_at_utc=stamp,
        run_id=resolved_run_id,
        summary_table="growth_summary",
        year_grid_table="growth_year_grid",
    )
