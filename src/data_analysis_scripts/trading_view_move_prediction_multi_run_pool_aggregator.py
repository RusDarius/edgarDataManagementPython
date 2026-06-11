"""Pool multiple move-prediction DuckDB runs for long-horizon pattern research.

``run_full_analysis_suite_duckdb`` writes one weekly ``move_prediction_*.duckdb``
per ISO week under ``prediction_analysis/duckdb_runs/iso_year=YYYY/week=WW/``.
Each run folder under ``runs/<run_id>/`` holds text logs; tabular data lives in
the weekly database (``raw_scan_rows``, ``profile_horizon_scores``, etc.).

This module is a *starting point* for pooling those weekly stores across many
weeks (tens of weeks × several runs per week) into one queryable DuckDB file
suited to deep multi-month/year pattern recognition.

Compared with ``trading_view_move_prediction_history_aggregator.py``:
  - history: snapshot progression, rank deltas, calibration-oriented views
  - pool:    flat append of source scoring + raw-field tables with provenance

Typical usage::

    from data_analysis_scripts.trading_view_move_prediction_multi_run_pool_aggregator import (
        run_move_prediction_run_pool_aggregation,
    )

    result = run_move_prediction_run_pool_aggregation(
        iso_year=2026,
        start_week=13,
        end_week=24,
        show_progress=True,  # stderr progress bars in Git Bash / TTY
    )
    print(result["database_path"])
    print(result["overview_log"])
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

from data_analysis_scripts.trading_view_move_prediction_analysis import LOG_DIR
from data_analysis_scripts.trading_view_move_prediction_batch_pattern_analysis import (
    discover_weekly_prediction_databases,
)
from data_analysis_scripts.trading_view_move_prediction_history_aggregator import (
    _chunked_paths,
    _iter_candidate_duckdb_paths,
)
from db.trading_view_move_prediction_duckdb import (
    open_move_prediction_duckdb_connection,
)

DEFAULT_DUCKDB_RUNS_ROOT = LOG_DIR / "duckdb_runs"
DEFAULT_POOL_OUTPUT_ROOT = LOG_DIR / "multi_run_pool"
DEFAULT_POOL_RUN_PREFIX = "move_prediction_run_pool"
DEFAULT_POOL_ROLLING_ID = "move_prediction_run_pool_rolling"
DEFAULT_ATTACH_BATCH_SIZE = 6
DEFAULT_POOL_MEMORY_GB = 28.0
DEFAULT_POOL_DUCKDB_THREADS = 20

PARQUET_SOURCE_TABLES: dict[str, str] = {
    "run_metadata": "run_metadata.parquet",
    "raw_scan_rows": "raw_scan_rows.parquet",
    "profile_horizon_scores": "profile_horizon_scores.parquet",
    "profile_components": "profile_components.parquet",
    "profile_performance_tracking": "profile_performance_tracking.parquet",
    "consensus_horizon_scores": "consensus_horizon_scores.parquet",
    "profile_config_snapshots": "profile_config_snapshots.parquet",
}

ISO_YEAR_FOLDER_PATTERN = re.compile(r"iso_year=(?P<year>\d{4})", re.IGNORECASE)
ISO_WEEK_FOLDER_PATTERN = re.compile(r"week=(?P<week>\d{1,2})", re.IGNORECASE)

POOL_PROVENANCE_COLUMNS: tuple[str, ...] = (
    "pool_aggregation_id",
    "source_database_path",
    "source_iso_year",
    "source_iso_week",
)

POOL_SOURCE_TABLES: tuple[str, ...] = (
    "run_metadata",
    "raw_scan_rows",
    "profile_horizon_scores",
    "profile_components",
    "profile_performance_tracking",
    "consensus_horizon_scores",
    "profile_config_snapshots",
)

POOL_AGGREGATION_RUNS_SCHEMA: tuple[tuple[str, str], ...] = (
    ("pool_aggregation_id", "VARCHAR"),
    ("created_at_utc", "TIMESTAMP"),
    ("output_dir", "VARCHAR"),
    ("database_path", "VARCHAR"),
    ("input_paths_json", "VARCHAR"),
    ("include_run_ids_json", "VARCHAR"),
    ("include_profiles_json", "VARCHAR"),
    ("source_database_count", "BIGINT"),
    ("source_run_count", "BIGINT"),
    ("chunk_index", "BIGINT"),
    ("chunk_count", "BIGINT"),
    ("notes", "VARCHAR"),
)

POOL_SOURCE_DATABASES_SCHEMA: tuple[tuple[str, str], ...] = (
    ("pool_aggregation_id", "VARCHAR"),
    ("source_database_path", "VARCHAR"),
    ("source_iso_year", "BIGINT"),
    ("source_iso_week", "BIGINT"),
    ("run_count", "BIGINT"),
    ("ingested_at_utc", "TIMESTAMP"),
)

RAW_SYMBOL_JOIN_SQL = """
(
    scores.symbol = raw.symbol
    OR raw.symbol LIKE '%:' || scores.symbol
    OR scores.symbol LIKE '%:' || raw.symbol
)
"""


@dataclass(frozen=True)
class MovePredictionRunPoolLayout:
    run_dir: Path
    database_path: Path
    pool_aggregation_id: str
    created_at_utc: datetime


@dataclass(frozen=True)
class PoolExecutionConfig:
    memory_gb: float = DEFAULT_POOL_MEMORY_GB
    duckdb_threads: int = DEFAULT_POOL_DUCKDB_THREADS
    attach_batch_size: int = DEFAULT_ATTACH_BATCH_SIZE
    prefer_parquet_inputs: bool = True
    preserve_insertion_order: bool = False


def resolve_pool_execution_config(
    *,
    memory_gb: float | None = None,
    duckdb_threads: int | None = None,
    attach_batch_size: int | None = None,
    prefer_parquet_inputs: bool | None = None,
) -> PoolExecutionConfig:
    """Default resource profile for a 32 GB / high-core desktop (e.g. i9-14900KF)."""
    cpu_count = os.cpu_count() or 8
    return PoolExecutionConfig(
        memory_gb=memory_gb if memory_gb is not None else DEFAULT_POOL_MEMORY_GB,
        duckdb_threads=(
            duckdb_threads
            if duckdb_threads is not None
            else min(DEFAULT_POOL_DUCKDB_THREADS, cpu_count)
        ),
        attach_batch_size=(
            attach_batch_size
            if attach_batch_size is not None
            else DEFAULT_ATTACH_BATCH_SIZE
        ),
        prefer_parquet_inputs=(
            prefer_parquet_inputs if prefer_parquet_inputs is not None else True
        ),
    )


def _format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _progress_bar(completed: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return f"[{'?' * width}]"
    filled = int(width * min(1.0, completed / total))
    return f"[{'=' * filled}{' ' * (width - filled)}] {completed}/{total}"


class PoolProgressReporter:
    """Emit real-time pool aggregation progress to stderr (Git Bash / TTY friendly)."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        stream: TextIO | None = None,
        label: str = "pool",
    ) -> None:
        self.enabled = enabled
        self.stream = stream or sys.stderr
        self.label = label
        self._use_color = (
            enabled and hasattr(self.stream, "isatty") and self.stream.isatty()
        )
        self._lock = threading.Lock()
        self._week_times: list[float] = []
        self._chunk_times: list[float] = []

    def _emit(
        self,
        message: str,
        *,
        end: str = "\n",
        color: str | None = None,
        clear_line: bool = False,
    ) -> None:
        if not self.enabled:
            return
        text = message
        if color and self._use_color:
            text = f"\033[{color}m{message}\033[0m"
        if clear_line:
            text = f"{text:<120}"
        with self._lock:
            self.stream.write(text + end)
            self.stream.flush()

    def banner(self, message: str) -> None:
        self._emit(f"[{self.label}] {message}", color="1;36")

    def phase(self, message: str) -> None:
        self._emit(f"[{self.label}] {message}", color="1;34")

    def week_started(
        self,
        *,
        week_index: int,
        week_total: int,
        iso_year: int | None,
        iso_week: int | None,
        database_name: str,
    ) -> None:
        week_label = (
            f"iso_year={iso_year} week={iso_week:02d}"
            if iso_year is not None and iso_week is not None
            else "unknown-week"
        )
        bar = _progress_bar(week_index - 1, week_total)
        self._emit(
            f"[{self.label}] {bar} starting {week_label} ({database_name})",
            color="36",
        )

    def week_step(self, week_label: str, step: str, detail: str = "") -> None:
        suffix = f" {detail}" if detail else ""
        self._emit(f"[{self.label}]   {week_label}: {step}{suffix}")

    def week_table_progress(
        self,
        *,
        week_label: str,
        table_name: str,
        rows_ingested: int,
        elapsed_seconds: float,
    ) -> None:
        rate = rows_ingested / elapsed_seconds if elapsed_seconds > 0 else 0.0
        self._emit(
            f"[{self.label}]   {week_label}: {table_name} "
            f"+{rows_ingested:,} rows in {_format_duration(elapsed_seconds)} "
            f"({rate:,.0f} rows/s)",
            color="33",
        )

    def week_finished(
        self,
        *,
        week_index: int,
        week_total: int,
        iso_year: int | None,
        iso_week: int | None,
        run_count: int,
        rows_ingested: int,
        elapsed_seconds: float,
        source_kind: str = "duckdb",
    ) -> None:
        self._week_times.append(elapsed_seconds)
        week_label = (
            f"iso_year={iso_year} week={iso_week:02d}"
            if iso_year is not None and iso_week is not None
            else "unknown-week"
        )
        bar = _progress_bar(week_index, week_total)
        remaining = week_total - week_index
        eta = (
            _format_duration(
                (sum(self._week_times) / len(self._week_times)) * remaining
            )
            if remaining > 0
            else "0s"
        )
        rate = rows_ingested / elapsed_seconds if elapsed_seconds > 0 else 0.0
        self._emit(
            f"[{self.label}] {bar} done {week_label} | "
            f"runs={run_count} rows={rows_ingested:,} source={source_kind} | "
            f"week={_format_duration(elapsed_seconds)} "
            f"({rate:,.0f} rows/s) | ETA remaining ~{eta}",
            color="32",
        )

    def chunk_started(
        self,
        *,
        chunk_index: int,
        chunk_total: int,
        week_start: int | None,
        week_end: int | None,
    ) -> None:
        bar = _progress_bar(chunk_index - 1, chunk_total)
        week_range = (
            f"weeks {week_start:02d}-{week_end:02d}"
            if week_start is not None and week_end is not None
            else "weeks ?"
        )
        self._emit(
            f"[{self.label}] {bar} starting chunk {chunk_index}/{chunk_total} ({week_range})",
            color="36",
        )

    def chunk_finished(
        self,
        *,
        chunk_index: int,
        chunk_total: int,
        elapsed_seconds: float,
        source_run_count: int,
        rows_ingested: int,
    ) -> None:
        self._chunk_times.append(elapsed_seconds)
        bar = _progress_bar(chunk_index, chunk_total)
        remaining = chunk_total - chunk_index
        eta = (
            _format_duration(
                (sum(self._chunk_times) / len(self._chunk_times)) * remaining
            )
            if remaining > 0
            else "0s"
        )
        self._emit(
            f"[{self.label}] {bar} done chunk {chunk_index}/{chunk_total} | "
            f"runs={source_run_count} rows={rows_ingested:,} | "
            f"chunk={_format_duration(elapsed_seconds)} | ETA remaining ~{eta}",
            color="32",
        )

    def merge_started(self, chunk_total: int) -> None:
        self.phase(f"merging {chunk_total} chunk database(s) into consolidated pool")

    def merge_chunk_progress(
        self,
        *,
        chunk_index: int,
        chunk_total: int,
        chunk_name: str,
        elapsed_seconds: float,
    ) -> None:
        bar = _progress_bar(chunk_index, chunk_total, width=12)
        self._emit(
            f"[{self.label}]   merge {bar} {chunk_name} "
            f"({_format_duration(elapsed_seconds)})",
            color="33",
        )

    def phase_timing(self, phase: str, elapsed_seconds: float) -> None:
        self._emit(
            f"[{self.label}] {phase} finished in {_format_duration(elapsed_seconds)}",
            color="1;32",
        )

    def summary(self, timing_seconds: Mapping[str, float]) -> None:
        if not self.enabled:
            return
        self._emit(f"[{self.label}] === timing summary ===", color="1;36")
        for phase, elapsed in timing_seconds.items():
            self._emit(f"[{self.label}]   {phase}: {_format_duration(elapsed)}")


def _week_label(iso_year: int | None, iso_week: int | None) -> str:
    if iso_year is not None and iso_week is not None:
        return f"iso_year={iso_year} week={iso_week:02d}"
    return "unknown-week"


def _resolve_parquet_source_path(
    database_path: Path,
    source_table_name: str,
) -> Path | None:
    parquet_name = PARQUET_SOURCE_TABLES.get(source_table_name)
    if parquet_name is None:
        return None
    parquet_path = database_path.parent / "parquet" / parquet_name
    return parquet_path if parquet_path.is_file() else None


def _resolved_pool_source_relation(
    database_path: Path,
    source_table_name: str,
    *,
    alias: str,
    prefer_parquet_inputs: bool,
) -> tuple[str, str]:
    parquet_path = _resolve_parquet_source_path(database_path, source_table_name)
    if prefer_parquet_inputs and parquet_path is not None:
        return (
            f"read_parquet({_quote_sql_literal(parquet_path.as_posix())}, union_by_name=true)",
            "parquet",
        )
    return (
        f"{_quote_identifier(alias)}.{_quote_identifier(source_table_name)}",
        "duckdb",
    )


def _source_table_available(
    conn: Any,
    *,
    database_path: Path,
    source_table_name: str,
    alias: str,
    prefer_parquet_inputs: bool,
) -> bool:
    if (
        _resolve_parquet_source_path(database_path, source_table_name)
        and prefer_parquet_inputs
    ):
        return True
    return _attached_table_exists(conn, alias, source_table_name)


def _apply_pool_bulk_load_settings(conn: Any) -> None:
    for statement in (
        "SET preserve_insertion_order=false",
        "SET enable_progress_bar=false",
    ):
        try:
            conn.execute(statement)
        except Exception:
            pass


def _describe_relation_column_types(
    conn: Any,
    relation_sql: str,
) -> list[tuple[str, str]]:
    rows = conn.execute(f"DESCRIBE SELECT * FROM {relation_sql}").fetchall()
    return [(str(row[0]), str(row[1]).upper()) for row in rows]


def _write_pool_overview_log(
    layout: MovePredictionRunPoolLayout,
    *,
    result: Mapping[str, Any],
    timing_seconds: Mapping[str, float],
    execution_config: PoolExecutionConfig,
    week_metrics: Sequence[Mapping[str, Any]] | None = None,
) -> Path:
    overview_path = layout.run_dir / "pool_aggregation_overview.log"
    lines = [
        "Move Prediction Run Pool Aggregation Overview",
        f"pool_aggregation_id: {layout.pool_aggregation_id}",
        f"created_at_utc: {layout.created_at_utc.isoformat()}",
        f"database_path: {layout.database_path.as_posix()}",
        f"input_database_count: {result.get('input_database_count')}",
        f"source_run_count: {result.get('source_run_count')}",
        "",
        "Execution config:",
        f"  memory_gb: {execution_config.memory_gb}",
        f"  duckdb_threads: {execution_config.duckdb_threads}",
        f"  attach_batch_size: {execution_config.attach_batch_size}",
        f"  prefer_parquet_inputs: {execution_config.prefer_parquet_inputs}",
        "",
        "Timing:",
    ]
    for phase, elapsed in timing_seconds.items():
        lines.append(f"  {phase}: {_format_duration(elapsed)}")
    if week_metrics:
        lines.extend(["", "Per-week metrics:"])
        for metric in week_metrics:
            lines.append(
                "  "
                + " | ".join(
                    f"{key}={value}"
                    for key, value in metric.items()
                    if value is not None
                )
            )
    overview_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return overview_path


def discover_weekly_prediction_databases_for_pool(
    *,
    iso_year: int,
    start_week: int,
    end_week: int | None = None,
    duckdb_runs_root: str | Path | None = None,
) -> list[Path]:
    """Resolve weekly move_prediction DuckDB files for an ISO week range."""
    return discover_weekly_prediction_databases(
        iso_year=iso_year,
        start_week=start_week,
        end_week=end_week,
        duckdb_runs_root=duckdb_runs_root,
    )


def resolve_move_prediction_run_pool_layout(
    output_dir: str | Path | None = None,
    *,
    rolling: bool = False,
    pool_aggregation_id: str | None = None,
) -> MovePredictionRunPoolLayout:
    created_at_utc = datetime.now(tz=timezone.utc)
    base_dir = Path(output_dir) if output_dir is not None else DEFAULT_POOL_OUTPUT_ROOT
    if rolling:
        aggregation_id = pool_aggregation_id or DEFAULT_POOL_ROLLING_ID
        run_dir = base_dir
    else:
        aggregation_id = pool_aggregation_id or (
            f"{DEFAULT_POOL_RUN_PREFIX}_"
            f"{created_at_utc.strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"
        )
        run_dir = base_dir / "runs" / aggregation_id
    return MovePredictionRunPoolLayout(
        run_dir=run_dir,
        database_path=run_dir / "pooled_move_prediction_runs.duckdb",
        pool_aggregation_id=aggregation_id,
        created_at_utc=created_at_utc,
    )


def _quote_identifier(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _quote_sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _build_optional_in_clause(values: Sequence[str]) -> str:
    return ", ".join(_quote_sql_literal(value) for value in values)


def _parse_iso_partition_from_path(
    database_path: Path,
) -> tuple[int | None, int | None]:
    iso_year: int | None = None
    iso_week: int | None = None
    for part in database_path.parts:
        year_match = ISO_YEAR_FOLDER_PATTERN.search(part)
        if year_match:
            iso_year = int(year_match.group("year"))
        week_match = ISO_WEEK_FOLDER_PATTERN.search(part)
        if week_match:
            iso_week = int(week_match.group("week"))
    return iso_year, iso_week


def _normalize_input_database_paths(
    input_paths: Sequence[str | Path] | str | Path,
    *,
    recursive: bool = True,
) -> list[Path]:
    if isinstance(input_paths, (str, Path)):
        normalized_paths: Sequence[str | Path] = [input_paths]
    else:
        normalized_paths = input_paths
    resolved = list(_iter_candidate_duckdb_paths(normalized_paths, recursive=recursive))
    if not resolved:
        raise ValueError("No weekly move_prediction_*.duckdb files were resolved.")
    return resolved


def _chunk_input_paths(
    input_paths: Sequence[Path],
    *,
    chunk_size: int | None,
) -> list[list[Path]]:
    if chunk_size is None or chunk_size <= 0 or chunk_size >= len(input_paths):
        return [list(input_paths)]
    return [list(chunk) for chunk in _chunked_paths(input_paths, chunk_size)]


def _ensure_pool_metadata_tables(conn: Any) -> None:
    for table_name, schema in (
        ("pool_aggregation_runs", POOL_AGGREGATION_RUNS_SCHEMA),
        ("pool_source_databases", POOL_SOURCE_DATABASES_SCHEMA),
    ):
        columns_sql = ", ".join(
            f"{_quote_identifier(column_name)} {sql_type}"
            for column_name, sql_type in schema
        )
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_quote_identifier(table_name)} ({columns_sql})"
        )


def _ensure_pool_source_table_shell(conn: Any, table_name: str) -> None:
    pool_table_name = f"pool_{table_name}"
    provenance_sql = ", ".join(
        (
            f"{_quote_identifier(column_name)} VARCHAR"
            if column_name.endswith("_id") or column_name.endswith("_path")
            else f"{_quote_identifier(column_name)} BIGINT"
        )
        for column_name in POOL_PROVENANCE_COLUMNS
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_quote_identifier(pool_table_name)} ({provenance_sql})"
    )


def _attached_table_exists(conn: Any, alias: str, table_name: str) -> bool:
    result = conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_catalog = ?
          AND table_schema = 'main'
          AND table_name = ?
        """,
        [alias, table_name],
    ).fetchone()
    return bool(result and result[0])


def _ingest_attached_source_table(
    conn: Any,
    *,
    alias: str,
    source_table_name: str,
    layout: MovePredictionRunPoolLayout,
    source_database_path: Path,
    source_iso_year: int | None,
    source_iso_week: int | None,
    include_run_ids: set[str] | None,
    include_profiles: set[str] | None,
    prefer_parquet_inputs: bool,
    on_table_completed: Callable[[str, int, float], None] | None = None,
) -> int:
    if not _source_table_available(
        conn,
        database_path=source_database_path,
        source_table_name=source_table_name,
        alias=alias,
        prefer_parquet_inputs=prefer_parquet_inputs,
    ):
        return 0

    source_relation_sql, _source_kind = _resolved_pool_source_relation(
        source_database_path,
        source_table_name,
        alias=alias,
        prefer_parquet_inputs=prefer_parquet_inputs,
    )
    pool_table_name = f"pool_{source_table_name}"
    _sync_pool_table_schema_from_source(
        conn,
        pool_table_name=pool_table_name,
        source_relation_sql=source_relation_sql,
        source_table_name=source_table_name,
    )

    attached_columns = {
        column_name
        for column_name, _ in _describe_relation_column_types(conn, source_relation_sql)
    }
    run_filter_sql = ""
    profile_filter_sql = ""
    if include_run_ids:
        run_filter_sql = (
            " AND src.run_id IN ("
            + _build_optional_in_clause(sorted(include_run_ids))
            + ")"
        )
    if include_profiles and "profile_name" in attached_columns:
        profile_filter_sql = (
            " AND LOWER(TRIM(CAST(src.profile_name AS VARCHAR))) IN ("
            + ", ".join(
                _quote_sql_literal(profile_name.lower())
                for profile_name in sorted(include_profiles)
            )
            + ")"
        )

    aggregation_id_sql = _quote_sql_literal(layout.pool_aggregation_id)
    source_path_sql = _quote_sql_literal(source_database_path.as_posix())
    source_year_sql = "NULL" if source_iso_year is None else str(source_iso_year)
    source_week_sql = "NULL" if source_iso_week is None else str(source_iso_week)

    count_result = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {source_relation_sql} AS src
        WHERE 1 = 1
            {run_filter_sql}
            {profile_filter_sql}
        """
    ).fetchone()
    rows_to_insert = int(count_result[0]) if count_result else 0
    if rows_to_insert <= 0:
        return 0

    table_started = time.perf_counter()
    conn.execute(
        f"""
        INSERT INTO {_quote_identifier(pool_table_name)} BY NAME
        SELECT
            {aggregation_id_sql} AS pool_aggregation_id,
            {source_path_sql} AS source_database_path,
            {source_year_sql} AS source_iso_year,
            {source_week_sql} AS source_iso_week,
            src.*
        FROM {source_relation_sql} AS src
        WHERE 1 = 1
            {run_filter_sql}
            {profile_filter_sql}
        """
    )
    table_elapsed = time.perf_counter() - table_started
    if on_table_completed is not None:
        on_table_completed(source_table_name, rows_to_insert, table_elapsed)
    return rows_to_insert


def _describe_pool_columns(conn: Any, table_name: str) -> set[str]:
    if not conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        """,
        [table_name],
    ).fetchone()[0]:
        return set()
    rows = conn.execute(
        f"PRAGMA table_info({_quote_identifier(table_name)})"
    ).fetchall()
    return {str(row[1]) for row in rows}


def _sync_pool_table_schema_from_source(
    conn: Any,
    *,
    pool_table_name: str,
    source_relation_sql: str,
    source_table_name: str,
) -> None:
    _ensure_pool_source_table_shell(conn, source_table_name)
    existing_columns = _describe_pool_columns(conn, pool_table_name)
    for column_name, column_type in _describe_relation_column_types(
        conn,
        source_relation_sql,
    ):
        if column_name in existing_columns:
            continue
        conn.execute(
            f"ALTER TABLE {_quote_identifier(pool_table_name)} "
            f"ADD COLUMN {_quote_identifier(column_name)} {column_type}"
        )
        existing_columns.add(column_name)


def _source_reads_require_attach(
    database_path: Path,
    pool_tables: Sequence[str],
    *,
    prefer_parquet_inputs: bool,
) -> bool:
    if not prefer_parquet_inputs:
        return True
    tables_to_check = set(pool_tables) | {"run_metadata"}
    return any(
        _resolve_parquet_source_path(database_path, table_name) is None
        for table_name in tables_to_check
    )


def _count_runs_in_source(
    conn: Any,
    *,
    database_path: Path,
    alias: str,
    include_run_ids: set[str] | None,
    prefer_parquet_inputs: bool,
) -> int:
    source_relation_sql, _source_kind = _resolved_pool_source_relation(
        database_path,
        "run_metadata",
        alias=alias,
        prefer_parquet_inputs=prefer_parquet_inputs,
    )
    if not _source_table_available(
        conn,
        database_path=database_path,
        source_table_name="run_metadata",
        alias=alias,
        prefer_parquet_inputs=prefer_parquet_inputs,
    ):
        return 0
    run_filter_sql = ""
    if include_run_ids:
        run_filter_sql = (
            " WHERE run_id IN ("
            + _build_optional_in_clause(sorted(include_run_ids))
            + ")"
        )
    result = conn.execute(
        f"SELECT COUNT(*) FROM {source_relation_sql}{run_filter_sql}"
    ).fetchone()
    return int(result[0]) if result else 0


def _dominant_source_kind(
    database_path: Path,
    pool_tables: Sequence[str],
    *,
    prefer_parquet_inputs: bool,
) -> str:
    if not prefer_parquet_inputs:
        return "duckdb"
    parquet_tables = sum(
        1
        for table_name in pool_tables
        if _resolve_parquet_source_path(database_path, table_name) is not None
    )
    if parquet_tables == 0:
        return "duckdb"
    if parquet_tables == len(pool_tables):
        return "parquet"
    return "mixed"


def _ingest_source_database(
    conn: Any,
    *,
    database_path: Path,
    source_index: int,
    layout: MovePredictionRunPoolLayout,
    include_run_ids: set[str] | None,
    include_profiles: set[str] | None,
    pool_tables: Sequence[str],
    prefer_parquet_inputs: bool,
    progress: PoolProgressReporter | None = None,
    week_index: int | None = None,
    week_total: int | None = None,
) -> dict[str, Any]:
    alias = f"src_pool_{source_index}"
    source_iso_year, source_iso_week = _parse_iso_partition_from_path(database_path)
    week_label = _week_label(source_iso_year, source_iso_week)
    requires_attach = _source_reads_require_attach(
        database_path,
        pool_tables,
        prefer_parquet_inputs=prefer_parquet_inputs,
    )
    if requires_attach:
        attach_sql = (
            f"ATTACH {_quote_sql_literal(database_path.as_posix())} "
            f"AS {_quote_identifier(alias)} (READ_ONLY)"
        )
        conn.execute(attach_sql)
    try:
        if progress is not None and week_index is not None and week_total is not None:
            progress.week_started(
                week_index=week_index,
                week_total=week_total,
                iso_year=source_iso_year,
                iso_week=source_iso_week,
                database_name=database_path.name,
            )

        run_count = _count_runs_in_source(
            conn,
            database_path=database_path,
            alias=alias,
            include_run_ids=include_run_ids,
            prefer_parquet_inputs=prefer_parquet_inputs,
        )
        if run_count <= 0:
            return {
                "source_database_path": database_path.as_posix(),
                "source_iso_year": source_iso_year,
                "source_iso_week": source_iso_week,
                "run_count": 0,
                "rows_ingested": 0,
                "table_row_counts": {},
                "source_kind": "skipped",
                "elapsed_seconds": 0.0,
                "skipped": True,
            }

        if progress is not None:
            progress.week_step(
                week_label,
                "ingesting",
                f"runs={run_count} source={_dominant_source_kind(database_path, pool_tables, prefer_parquet_inputs=prefer_parquet_inputs)}",
            )

        week_started = time.perf_counter()
        table_row_counts: dict[str, int] = {}

        def _on_table_completed(
            table_name: str, rows_ingested: int, elapsed_seconds: float
        ) -> None:
            if progress is not None and rows_ingested > 0:
                progress.week_table_progress(
                    week_label=week_label,
                    table_name=table_name,
                    rows_ingested=rows_ingested,
                    elapsed_seconds=elapsed_seconds,
                )

        for source_table_name in pool_tables:
            table_row_counts[source_table_name] = _ingest_attached_source_table(
                conn,
                alias=alias,
                source_table_name=source_table_name,
                layout=layout,
                source_database_path=database_path,
                source_iso_year=source_iso_year,
                source_iso_week=source_iso_week,
                include_run_ids=include_run_ids,
                include_profiles=include_profiles,
                prefer_parquet_inputs=prefer_parquet_inputs,
                on_table_completed=_on_table_completed,
            )

        rows_ingested = sum(table_row_counts.values())
        elapsed_seconds = time.perf_counter() - week_started
        source_kind = _dominant_source_kind(
            database_path,
            pool_tables,
            prefer_parquet_inputs=prefer_parquet_inputs,
        )

        conn.execute(
            """
            INSERT INTO pool_source_databases (
                pool_aggregation_id,
                source_database_path,
                source_iso_year,
                source_iso_week,
                run_count,
                ingested_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                layout.pool_aggregation_id,
                database_path.as_posix(),
                source_iso_year,
                source_iso_week,
                run_count,
                datetime.now(tz=timezone.utc),
            ],
        )

        if progress is not None and week_index is not None and week_total is not None:
            progress.week_finished(
                week_index=week_index,
                week_total=week_total,
                iso_year=source_iso_year,
                iso_week=source_iso_week,
                run_count=run_count,
                rows_ingested=rows_ingested,
                elapsed_seconds=elapsed_seconds,
                source_kind=source_kind,
            )

        return {
            "source_database_path": database_path.as_posix(),
            "source_iso_year": source_iso_year,
            "source_iso_week": source_iso_week,
            "run_count": run_count,
            "rows_ingested": rows_ingested,
            "table_row_counts": table_row_counts,
            "source_kind": source_kind,
            "elapsed_seconds": elapsed_seconds,
            "skipped": False,
        }
    finally:
        if requires_attach:
            conn.execute(f"DETACH {_quote_identifier(alias)}")


def _register_pool_aggregation_run(
    conn: Any,
    *,
    layout: MovePredictionRunPoolLayout,
    input_paths: Sequence[Path],
    include_run_ids: Sequence[str] | None,
    include_profiles: Sequence[str] | None,
    source_database_count: int,
    source_run_count: int,
    chunk_index: int | None,
    chunk_count: int | None,
    notes: str,
) -> None:
    conn.execute(
        """
        INSERT INTO pool_aggregation_runs (
            pool_aggregation_id,
            created_at_utc,
            output_dir,
            database_path,
            input_paths_json,
            include_run_ids_json,
            include_profiles_json,
            source_database_count,
            source_run_count,
            chunk_index,
            chunk_count,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            layout.pool_aggregation_id,
            layout.created_at_utc,
            layout.run_dir.as_posix(),
            layout.database_path.as_posix(),
            json.dumps([path.as_posix() for path in input_paths], ensure_ascii=False),
            json.dumps(list(include_run_ids or []), ensure_ascii=False),
            json.dumps(list(include_profiles or []), ensure_ascii=False),
            source_database_count,
            source_run_count,
            chunk_index,
            chunk_count,
            notes,
        ],
    )


def create_move_prediction_run_pool_views(conn: Any) -> list[str]:
    """Create starter views that join pooled scores with raw scan fields."""
    view_names: list[str] = []

    conn.execute(
        f"""
        CREATE OR REPLACE VIEW vw_pool_run_inventory AS
        SELECT
            meta.pool_aggregation_id,
            meta.source_database_path,
            meta.source_iso_year,
            meta.source_iso_week,
            meta.run_id,
            meta.created_at_utc,
            meta.run_date_utc,
            meta.iso_year,
            meta.iso_week,
            meta.suite_name,
            meta.scan_data_count,
            meta.profile_names_json,
            COALESCE(raw_counts.raw_rows, 0) AS raw_rows,
            COALESCE(score_counts.profile_horizon_rows, 0) AS profile_horizon_rows,
            COALESCE(score_counts.profiles_scored, 0) AS profiles_scored
        FROM pool_run_metadata AS meta
        LEFT JOIN (
            SELECT pool_aggregation_id,
                source_database_path,
                run_id,
                COUNT(*) AS raw_rows
            FROM pool_raw_scan_rows
            GROUP BY 1, 2, 3
        ) AS raw_counts
            USING (pool_aggregation_id, source_database_path, run_id)
        LEFT JOIN (
            SELECT pool_aggregation_id,
                source_database_path,
                run_id,
                COUNT(*) AS profile_horizon_rows,
                COUNT(DISTINCT profile_name) AS profiles_scored
            FROM pool_profile_horizon_scores
            GROUP BY 1, 2, 3
        ) AS score_counts
            USING (pool_aggregation_id, source_database_path, run_id)
        """
    )
    view_names.append("vw_pool_run_inventory")

    conn.execute(
        f"""
        CREATE OR REPLACE VIEW vw_pool_profile_scores_with_raw AS
        SELECT
            scores.pool_aggregation_id,
            scores.source_database_path,
            scores.source_iso_year,
            scores.source_iso_week,
            scores.run_id,
            meta.created_at_utc,
            meta.run_date_utc,
            meta.iso_year,
            meta.iso_week,
            scores.profile_name,
            scores.horizon_name,
            scores.symbol,
            scores.company,
            scores.sector,
            scores.industry,
            scores.market_cap_basic,
            scores.close AS score_close,
            scores.score,
            scores.direction,
            scores.confidence,
            scores.coverage,
            scores.setup,
            scores.risk_adjusted_score,
            scores.risk_tier,
            scores.manager_action_signal,
            raw.symbol AS raw_symbol,
            raw.* EXCLUDE (
                pool_aggregation_id,
                source_database_path,
                source_iso_year,
                source_iso_week,
                run_id,
                row_number,
                symbol
            )
        FROM pool_profile_horizon_scores AS scores
        INNER JOIN pool_run_metadata AS meta
            ON meta.pool_aggregation_id = scores.pool_aggregation_id
            AND meta.source_database_path = scores.source_database_path
            AND meta.run_id = scores.run_id
        INNER JOIN pool_raw_scan_rows AS raw
            ON raw.pool_aggregation_id = scores.pool_aggregation_id
            AND raw.source_database_path = scores.source_database_path
            AND raw.run_id = scores.run_id
            AND {RAW_SYMBOL_JOIN_SQL}
        """
    )
    view_names.append("vw_pool_profile_scores_with_raw")

    return view_names


def aggregate_move_prediction_run_pool(
    input_paths: Sequence[str | Path] | str | Path,
    *,
    output_dir: str | Path | None = None,
    rolling: bool = False,
    include_run_ids: Sequence[str] | None = None,
    include_profiles: Sequence[str] | None = None,
    pool_tables: Sequence[str] | None = None,
    attach_batch_size: int | None = None,
    recursive: bool = True,
    duckdb_threads: int | None = None,
    memory_gb: float | None = None,
    memory_limit: str | int | float | None = None,
    temp_directory: str | Path | None = None,
    prefer_parquet_inputs: bool | None = None,
    create_views: bool = True,
    chunk_index: int | None = None,
    chunk_count: int | None = None,
    show_progress: bool = True,
    progress: PoolProgressReporter | None = None,
    write_overview_log: bool = True,
) -> dict[str, Any]:
    """Pool scoring-profile and raw-field tables from many weekly DuckDB files."""
    execution_config = resolve_pool_execution_config(
        memory_gb=memory_gb,
        duckdb_threads=duckdb_threads,
        attach_batch_size=attach_batch_size,
        prefer_parquet_inputs=prefer_parquet_inputs,
    )
    resolved_memory_limit = (
        memory_limit if memory_limit is not None else execution_config.memory_gb
    )
    progress_reporter = progress or PoolProgressReporter(enabled=show_progress)
    timing_seconds: dict[str, float] = {}
    pool_started = time.perf_counter()

    resolved_input_paths = _normalize_input_database_paths(
        input_paths,
        recursive=recursive,
    )
    selected_pool_tables = tuple(pool_tables or POOL_SOURCE_TABLES)
    normalized_run_ids = {
        run_id.strip()
        for run_id in (include_run_ids or [])
        if run_id and run_id.strip()
    } or None
    normalized_profiles = {
        profile_name.strip().lower()
        for profile_name in (include_profiles or [])
        if profile_name and profile_name.strip()
    } or None

    layout = resolve_move_prediction_run_pool_layout(output_dir, rolling=rolling)
    layout.run_dir.mkdir(parents=True, exist_ok=True)
    if temp_directory is None:
        temp_directory = layout.run_dir / "duckdb_temp"
    Path(temp_directory).mkdir(parents=True, exist_ok=True)

    week_total = len(resolved_input_paths)
    progress_reporter.banner(
        f"pool_aggregation_id={layout.pool_aggregation_id} "
        f"weekly_databases={week_total} "
        f"threads={execution_config.duckdb_threads} "
        f"memory={execution_config.memory_gb:.0f}GB "
        f"attach_batch={execution_config.attach_batch_size} "
        f"parquet={execution_config.prefer_parquet_inputs}"
    )
    progress_reporter.phase(
        f"ingesting {week_total} weekly database(s)"
        + (
            f" (chunk {chunk_index}/{chunk_count})"
            if chunk_index is not None and chunk_count is not None
            else ""
        )
    )

    ingested_sources: list[dict[str, Any]] = []
    week_metrics: list[dict[str, Any]] = []
    source_run_count = 0
    rows_ingested_total = 0
    ingest_started = time.perf_counter()

    with open_move_prediction_duckdb_connection(
        layout.database_path,
        threads=execution_config.duckdb_threads,
        memory_limit=resolved_memory_limit,
        temp_directory=temp_directory,
        preserve_insertion_order=execution_config.preserve_insertion_order,
    ) as conn:
        _apply_pool_bulk_load_settings(conn)
        _ensure_pool_metadata_tables(conn)
        for table_name in selected_pool_tables:
            _ensure_pool_source_table_shell(conn, table_name)

        week_index = 0
        for batch_index, batch_paths in enumerate(
            _chunk_input_paths(
                resolved_input_paths,
                chunk_size=execution_config.attach_batch_size,
            ),
            start=1,
        ):
            for source_index, database_path in enumerate(batch_paths, start=1):
                week_index += 1
                global_source_index = (
                    batch_index - 1
                ) * execution_config.attach_batch_size + source_index
                source_result = _ingest_source_database(
                    conn,
                    database_path=database_path,
                    source_index=global_source_index,
                    layout=layout,
                    include_run_ids=normalized_run_ids,
                    include_profiles=normalized_profiles,
                    pool_tables=selected_pool_tables,
                    prefer_parquet_inputs=execution_config.prefer_parquet_inputs,
                    progress=progress_reporter,
                    week_index=week_index,
                    week_total=week_total,
                )
                ingested_sources.append(source_result)
                week_metrics.append(
                    {
                        "week_index": week_index,
                        "iso_year": source_result.get("source_iso_year"),
                        "iso_week": source_result.get("source_iso_week"),
                        "runs": source_result.get("run_count"),
                        "rows": source_result.get("rows_ingested"),
                        "seconds": round(
                            float(source_result.get("elapsed_seconds") or 0.0), 2
                        ),
                        "source_kind": source_result.get("source_kind"),
                        "skipped": source_result.get("skipped"),
                    }
                )
                if not source_result.get("skipped"):
                    source_run_count += int(source_result.get("run_count") or 0)
                    rows_ingested_total += int(source_result.get("rows_ingested") or 0)

        _register_pool_aggregation_run(
            conn,
            layout=layout,
            input_paths=resolved_input_paths,
            include_run_ids=include_run_ids,
            include_profiles=include_profiles,
            source_database_count=len(resolved_input_paths),
            source_run_count=source_run_count,
            chunk_index=chunk_index,
            chunk_count=chunk_count,
            notes=(
                "Pooled move-prediction scoring profiles and raw_scan_rows across "
                "weekly DuckDB sources for long-horizon pattern research."
            ),
        )

        view_names: list[str] = []
        if create_views:
            views_started = time.perf_counter()
            progress_reporter.phase("creating analysis views")
            view_names = create_move_prediction_run_pool_views(conn)
            timing_seconds["create_views"] = time.perf_counter() - views_started
            progress_reporter.phase_timing(
                "create_views", timing_seconds["create_views"]
            )

    timing_seconds["ingest_weekly_sources"] = time.perf_counter() - ingest_started
    timing_seconds["total"] = time.perf_counter() - pool_started
    progress_reporter.phase_timing(
        "ingest_weekly_sources", timing_seconds["ingest_weekly_sources"]
    )
    progress_reporter.summary(timing_seconds)

    result = {
        "pool_aggregation_id": layout.pool_aggregation_id,
        "output_dir": layout.run_dir.as_posix(),
        "database_path": layout.database_path.as_posix(),
        "input_database_count": len(resolved_input_paths),
        "source_run_count": source_run_count,
        "rows_ingested_total": rows_ingested_total,
        "ingested_sources": ingested_sources,
        "week_metrics": week_metrics,
        "pool_tables": list(selected_pool_tables),
        "view_names": view_names,
        "chunk_index": chunk_index,
        "chunk_count": chunk_count,
        "timing_seconds": timing_seconds,
        "execution_config": {
            "memory_gb": execution_config.memory_gb,
            "duckdb_threads": execution_config.duckdb_threads,
            "attach_batch_size": execution_config.attach_batch_size,
            "prefer_parquet_inputs": execution_config.prefer_parquet_inputs,
            "preserve_insertion_order": execution_config.preserve_insertion_order,
        },
    }
    if write_overview_log:
        overview_log = _write_pool_overview_log(
            layout,
            result=result,
            timing_seconds=timing_seconds,
            execution_config=execution_config,
            week_metrics=week_metrics,
        )
        result["overview_log"] = overview_log.as_posix()
    return result


# DO NOT REMOVE THIS COMMENT - it is a reminder of how to run the pool aggregation
# # Discover weeks 13–24 of 2026 and pool them
# result = run_move_prediction_run_pool_aggregation(
#     iso_year=2026,
#     start_week=13,
#     end_week=24,
#     attach_batch_size=4,  # ATTACH N weekly DBs at a time for memory control
# )
def aggregate_move_prediction_run_pool_in_chunks(
    input_paths: Sequence[str | Path] | str | Path,
    *,
    chunk_size: int,
    output_dir: str | Path | None = None,
    merge_chunks: bool = True,
    show_progress: bool = True,
    progress: PoolProgressReporter | None = None,
    write_overview_log: bool = True,
    **pool_kwargs: Any,
) -> dict[str, Any]:
    """Split weekly inputs into chunk databases, optionally merge into one pool."""
    progress_reporter = progress or PoolProgressReporter(enabled=show_progress)
    timing_seconds: dict[str, float] = {}
    chunk_run_started = time.perf_counter()

    resolved_input_paths = _normalize_input_database_paths(input_paths)
    path_chunks = _chunk_input_paths(resolved_input_paths, chunk_size=chunk_size)
    chunk_count = len(path_chunks)
    chunk_root = (
        Path(output_dir) if output_dir is not None else DEFAULT_POOL_OUTPUT_ROOT
    )
    chunk_results: list[dict[str, Any]] = []

    progress_reporter.banner(
        f"chunked pool build chunk_size={chunk_size} chunk_count={chunk_count} "
        f"weekly_databases={len(resolved_input_paths)} output={chunk_root}"
    )
    progress_reporter.phase(f"building {chunk_count} chunk pool database(s)")

    for chunk_index, chunk_paths in enumerate(path_chunks, start=1):
        chunk_iso_weeks = [
            _parse_iso_partition_from_path(path)[1] for path in chunk_paths
        ]
        week_start = min(
            (week for week in chunk_iso_weeks if week is not None),
            default=None,
        )
        week_end = max(
            (week for week in chunk_iso_weeks if week is not None),
            default=None,
        )
        progress_reporter.chunk_started(
            chunk_index=chunk_index,
            chunk_total=chunk_count,
            week_start=week_start,
            week_end=week_end,
        )
        chunk_output_dir = (
            chunk_root / "chunks" / f"chunk_{chunk_index:03d}_of_{chunk_count:03d}"
        )
        chunk_started = time.perf_counter()
        chunk_result = aggregate_move_prediction_run_pool(
            chunk_paths,
            output_dir=chunk_output_dir,
            chunk_index=chunk_index,
            chunk_count=chunk_count,
            show_progress=show_progress,
            progress=progress_reporter,
            write_overview_log=False,
            **pool_kwargs,
        )
        chunk_elapsed = time.perf_counter() - chunk_started
        progress_reporter.chunk_finished(
            chunk_index=chunk_index,
            chunk_total=chunk_count,
            elapsed_seconds=chunk_elapsed,
            source_run_count=int(chunk_result.get("source_run_count") or 0),
            rows_ingested=int(chunk_result.get("rows_ingested_total") or 0),
        )
        chunk_results.append(chunk_result)

    timing_seconds["build_chunks"] = time.perf_counter() - chunk_run_started
    progress_reporter.phase_timing("build_chunks", timing_seconds["build_chunks"])

    merged_result: dict[str, Any] | None = None
    if merge_chunks and chunk_results:
        merge_started = time.perf_counter()
        progress_reporter.merge_started(chunk_count)
        merged_result = merge_move_prediction_run_pool_chunks(
            [result["database_path"] for result in chunk_results],
            output_dir=chunk_root,
            show_progress=show_progress,
            progress=progress_reporter,
            write_overview_log=write_overview_log,
            **{
                key: value
                for key, value in pool_kwargs.items()
                if key
                in {
                    "duckdb_threads",
                    "memory_gb",
                    "memory_limit",
                    "temp_directory",
                    "prefer_parquet_inputs",
                    "create_views",
                }
            },
        )
        timing_seconds["merge_chunks"] = time.perf_counter() - merge_started
        progress_reporter.phase_timing("merge_chunks", timing_seconds["merge_chunks"])

    timing_seconds["total"] = time.perf_counter() - chunk_run_started
    progress_reporter.summary(timing_seconds)

    return {
        "chunk_count": chunk_count,
        "chunk_results": chunk_results,
        "merged_result": merged_result,
        "timing_seconds": timing_seconds,
    }


def merge_move_prediction_run_pool_chunks(
    chunk_database_paths: Sequence[str | Path],
    *,
    output_dir: str | Path | None = None,
    rolling: bool = False,
    duckdb_threads: int | None = None,
    memory_gb: float | None = None,
    memory_limit: str | int | float | None = None,
    temp_directory: str | Path | None = None,
    create_views: bool = True,
    show_progress: bool = True,
    progress: PoolProgressReporter | None = None,
    write_overview_log: bool = True,
) -> dict[str, Any]:
    """Merge previously built chunk pool databases into one consolidated pool."""
    execution_config = resolve_pool_execution_config(
        memory_gb=memory_gb,
        duckdb_threads=duckdb_threads,
    )
    resolved_memory_limit = (
        memory_limit if memory_limit is not None else execution_config.memory_gb
    )
    progress_reporter = progress or PoolProgressReporter(enabled=show_progress)
    timing_seconds: dict[str, float] = {}
    merge_started = time.perf_counter()

    resolved_chunk_paths = [Path(path) for path in chunk_database_paths]
    missing_paths = [path for path in resolved_chunk_paths if not path.is_file()]
    if missing_paths:
        raise FileNotFoundError(
            "Chunk pool database(s) not found: "
            + ", ".join(path.as_posix() for path in missing_paths)
        )

    layout = resolve_move_prediction_run_pool_layout(output_dir, rolling=rolling)
    layout.run_dir.mkdir(parents=True, exist_ok=True)
    if temp_directory is None:
        temp_directory = layout.run_dir / "duckdb_temp"
    Path(temp_directory).mkdir(parents=True, exist_ok=True)

    chunk_total = len(resolved_chunk_paths)
    source_run_count = 0
    with open_move_prediction_duckdb_connection(
        layout.database_path,
        threads=execution_config.duckdb_threads,
        memory_limit=resolved_memory_limit,
        temp_directory=temp_directory,
        preserve_insertion_order=execution_config.preserve_insertion_order,
    ) as conn:
        _apply_pool_bulk_load_settings(conn)
        _ensure_pool_metadata_tables(conn)
        for table_name in POOL_SOURCE_TABLES:
            _ensure_pool_source_table_shell(conn, table_name)

        for chunk_index, chunk_path in enumerate(resolved_chunk_paths, start=1):
            alias = f"chunk_pool_{chunk_index}"
            chunk_merge_started = time.perf_counter()
            conn.execute(
                f"ATTACH {_quote_sql_literal(chunk_path.as_posix())} "
                f"AS {_quote_identifier(alias)} (READ_ONLY)"
            )
            try:
                for metadata_table in (
                    "pool_aggregation_runs",
                    "pool_source_databases",
                ):
                    conn.execute(
                        f"""
                        INSERT INTO {_quote_identifier(metadata_table)} BY NAME
                        SELECT src.*
                        FROM {_quote_identifier(alias)}.{_quote_identifier(metadata_table)} AS src
                        """
                    )

                for source_table_name in POOL_SOURCE_TABLES:
                    pool_table_name = f"pool_{source_table_name}"
                    if not _attached_table_exists(conn, alias, pool_table_name):
                        continue
                    source_relation_sql = f"{_quote_identifier(alias)}.{_quote_identifier(pool_table_name)}"
                    _sync_pool_table_schema_from_source(
                        conn,
                        pool_table_name=pool_table_name,
                        source_relation_sql=source_relation_sql,
                        source_table_name=source_table_name,
                    )
                    conn.execute(
                        f"""
                        INSERT INTO {_quote_identifier(pool_table_name)} BY NAME
                        SELECT src.*
                        FROM {source_relation_sql} AS src
                        """
                    )
            finally:
                conn.execute(f"DETACH {_quote_identifier(alias)}")

            progress_reporter.merge_chunk_progress(
                chunk_index=chunk_index,
                chunk_total=chunk_total,
                chunk_name=chunk_path.name,
                elapsed_seconds=time.perf_counter() - chunk_merge_started,
            )

        merged_table_counts = {
            f"pool_{source_table_name}": int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(f'pool_{source_table_name}')}"
                ).fetchone()[0]
            )
            for source_table_name in POOL_SOURCE_TABLES
            if _describe_pool_columns(conn, f"pool_{source_table_name}")
        }

        source_run_count = int(
            conn.execute("SELECT COUNT(*) FROM pool_run_metadata").fetchone()[0]
        )
        _register_pool_aggregation_run(
            conn,
            layout=layout,
            input_paths=resolved_chunk_paths,
            include_run_ids=None,
            include_profiles=None,
            source_database_count=len(resolved_chunk_paths),
            source_run_count=source_run_count,
            chunk_index=None,
            chunk_count=len(resolved_chunk_paths),
            notes="Merged move-prediction run pool chunk databases.",
        )

        view_names: list[str] = []
        if create_views:
            view_names = create_move_prediction_run_pool_views(conn)

    timing_seconds["merge_chunks"] = time.perf_counter() - merge_started
    progress_reporter.phase_timing("merge_chunks", timing_seconds["merge_chunks"])

    result = {
        "pool_aggregation_id": layout.pool_aggregation_id,
        "output_dir": layout.run_dir.as_posix(),
        "database_path": layout.database_path.as_posix(),
        "chunk_database_count": len(resolved_chunk_paths),
        "source_run_count": source_run_count,
        "merged_table_counts": merged_table_counts,
        "view_names": view_names,
        "timing_seconds": timing_seconds,
        "execution_config": {
            "memory_gb": execution_config.memory_gb,
            "duckdb_threads": execution_config.duckdb_threads,
        },
    }
    if write_overview_log:
        overview_log = _write_pool_overview_log(
            layout,
            result=result,
            timing_seconds=timing_seconds,
            execution_config=execution_config,
        )
        result["overview_log"] = overview_log.as_posix()
    return result


# DO NOT REMOVE THIS COMMENT - it is a reminder of how to run the pool aggregation
# # Discover weeks 13–24 of 2026 and pool them
# result = run_move_prediction_run_pool_aggregation(
#     iso_year=2026,
#     start_week=13,
#     end_week=24,
#     attach_batch_size=4,  # ATTACH N weekly DBs at a time for memory control
# )
def run_move_prediction_run_pool_aggregation(
    *,
    iso_year: int | None = None,
    start_week: int | None = None,
    end_week: int | None = None,
    input_paths: Sequence[str | Path] | str | Path | None = None,
    duckdb_runs_root: str | Path | None = None,
    chunk_size: int | None = None,
    show_progress: bool = True,
    **pool_kwargs: Any,
) -> dict[str, Any]:
    """Convenience wrapper: discover weekly DBs by ISO range or accept explicit paths."""
    if input_paths is None:
        if iso_year is None or start_week is None:
            raise ValueError(
                "Provide input_paths or both iso_year and start_week for discovery."
            )
        resolved_input_paths = discover_weekly_prediction_databases_for_pool(
            iso_year=iso_year,
            start_week=start_week,
            end_week=end_week,
            duckdb_runs_root=duckdb_runs_root,
        )
    else:
        resolved_input_paths = input_paths

    if chunk_size is not None and chunk_size > 0:
        return aggregate_move_prediction_run_pool_in_chunks(
            resolved_input_paths,
            chunk_size=chunk_size,
            show_progress=show_progress,
            **pool_kwargs,
        )

    return aggregate_move_prediction_run_pool(
        resolved_input_paths,
        show_progress=show_progress,
        **pool_kwargs,
    )
