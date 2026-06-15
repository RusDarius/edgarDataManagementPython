from __future__ import annotations

import csv
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import uuid

import requests

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from constants.trading_view_constants import TRADING_VIEW_ALL_MARKETS_ARRAY
from data_loaders.api_tradingview_client import (
    ApiTradingViewClient,
    GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD,
    TRADINGVIEW_GLOBAL_SCAN_URL,
)
from db.trading_view_all_fields_duckdb import TradingViewAllFieldsDuckDBStore
from generic_utils.log_to_files_util import log_to_file

USER_AGENT = "Barnnabass daniOO7XbX@gmail.com"
FIELD_CATALOG_CSV = Path(
    r"d:\FinanceProjects\edgarDataManagementPython\savedData\trading_view_stock_fields.csv"
)
OUTPUT_DIR = Path(
    r"d:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis"
)
DUCKDB_OUTPUT_DIR = OUTPUT_DIR / "trading_view_all_fields_data"
CHUNK_SIZE = 600
REQUEST_TIMEOUT_SECONDS = 60
SCAN_RANGE_END = 100_000
DB_MERGE_BATCH_SIZE = 500
ALL_FIELDS_API_REQUEST_PROVENANCE_SCHEMA_VERSION = (
    "tradingview_all_fields_api_request_provenance_v1"
)
ALL_FIELDS_CODE_VERSION_SCHEMA_VERSION = "tradingview_all_fields_code_version_v1"

# Backfill constants
BACKFILL_DATE_FORMAT = "%d_%m_%Y"
BACKFILL_CSV_GLOB = "tradingview_global_all_tdfields_*.csv"
ALL_FIELDS_BACKFILL_SCHEMA_VERSION = "all_fields_csv_backfill_v1"
ALL_FIELDS_BACKFILL_MANIFEST_NAME = "_all_fields_csv_duckdb_backfill_manifest.csv"
DEFAULT_BACKFILL_MANIFEST_DIR = DUCKDB_OUTPUT_DIR
DEFAULT_BACKFILL_PARALLEL_WORKERS = 1
DEFAULT_BACKFILL_SYSTEM_MEMORY_GB = 30.0
DEFAULT_BACKFILL_MEMORY_RESERVE_GB = 4.0
DEFAULT_BACKFILL_DUCKDB_THREADS = 8
DEFAULT_BACKFILL_MEMORY_LIMIT_GB = 20.0
DEFAULT_BACKFILL_SHA256_CHUNK_BYTES = 8 * 1024 * 1024
DEFAULT_BACKFILL_ESTIMATED_DAY_MEMORY_GB = 6.0
BACKFILL_METADATA_PARQUET_TABLES = (
    "run_metadata",
    "generated_reports",
    "parquet_exports",
)
BACKFILL_FALLBACK_MAX_CSV_BYTES = 250 * 1024 * 1024


@dataclass(frozen=True)
class TradingViewAllFieldsDailyStorageLayout:
    period_dir: Path
    run_output_dir: Path
    database_path: Path
    parquet_dir: Path


def _load_field_names(field_catalog_csv: Path) -> list[str]:
    field_names: list[str] = []
    seen: set[str] = set()

    with field_catalog_csv.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            field_name = str(row.get("Name", "")).strip()
            if not field_name or field_name in seen:
                continue
            seen.add(field_name)
            field_names.append(field_name)

    return field_names


def _canonical_json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _slugify(text: str) -> str:
    slug = []
    previous_was_separator = False
    for char in str(text).lower():
        if char.isalnum():
            slug.append(char)
            previous_was_separator = False
            continue
        if previous_was_separator:
            continue
        slug.append("_")
        previous_was_separator = True
    return "".join(slug).strip("_")


def _build_all_fields_duckdb_run_id(
    run_label: str | None = None,
    reference_time: datetime | None = None,
) -> str:
    reference_time = reference_time or datetime.now(tz=timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    reference_time = reference_time.astimezone(timezone.utc)
    timestamp = reference_time.strftime("%Y%m%d_%H%M")
    unique_suffix = uuid.uuid4().hex[:8]
    if run_label:
        slugified_label = _slugify(run_label)
        if slugified_label:
            return f"{slugified_label}_{timestamp}_utc_{unique_suffix}"
    return f"tradingview_all_fields_{timestamp}_utc_{unique_suffix}"


@contextmanager
def _duckdb_daily_writer_lock(database_path: Path):
    lock_path = database_path.with_name(f"{database_path.name}.write.lock")
    lock_acquired = False
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        lock_acquired = True
        with os.fdopen(lock_fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(
                _canonical_json_dump(
                    {
                        "database_path": str(database_path),
                        "created_at_utc": datetime.now(tz=timezone.utc).isoformat(),
                        "process_id": os.getpid(),
                    }
                )
            )
        yield lock_path
    except FileExistsError as exc:
        raise RuntimeError(
            f"DuckDB daily writer lock already exists: {lock_path}. "
            "Only one writer should update a daily all-fields database at a time."
        ) from exc
    finally:
        if lock_acquired:
            lock_path.unlink(missing_ok=True)


def _build_all_fields_daily_storage_layout(
    run_id: str,
    created_at_utc: datetime,
    output_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    parquet_dir: str | Path | None = None,
) -> TradingViewAllFieldsDailyStorageLayout:
    if created_at_utc.tzinfo is None:
        created_at_utc = created_at_utc.replace(tzinfo=timezone.utc)
    created_at_utc = created_at_utc.astimezone(timezone.utc)
    base_output_dir = Path(output_dir) if output_dir is not None else DUCKDB_OUTPUT_DIR
    day_label = created_at_utc.strftime("%d_%m_%Y")
    period_dir = base_output_dir / day_label
    run_output_dir = period_dir / "runs" / run_id
    resolved_database_path = (
        Path(database_path)
        if database_path is not None
        else period_dir / f"tradingview_all_fields_{day_label}.duckdb"
    )
    resolved_parquet_dir = (
        Path(parquet_dir) if parquet_dir is not None else period_dir / "parquet"
    )
    return TradingViewAllFieldsDailyStorageLayout(
        period_dir=period_dir,
        run_output_dir=run_output_dir,
        database_path=resolved_database_path,
        parquet_dir=resolved_parquet_dir,
    )


def _chunked(values: list[str], chunk_size: int) -> list[list[str]]:
    return [
        values[index : index + chunk_size]
        for index in range(0, len(values), chunk_size)
    ]


def _build_scan_payload(columns: list[str]) -> dict[str, Any]:
    payload = deepcopy(GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD)
    payload["columns"] = list(columns)
    payload["markets"] = list(TRADING_VIEW_ALL_MARKETS_ARRAY)
    payload["ignore_unknown_fields"] = True
    payload["range"] = [0, SCAN_RANGE_END]
    return payload


def _run_git_command(repo_root: Path, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _hash_source_file(path: Path, repo_root: Path) -> dict[str, Any]:
    try:
        file_bytes = path.read_bytes()
    except OSError:
        return {"path": str(path), "sha256": None, "size_bytes": None}

    try:
        relative_path = path.relative_to(repo_root)
    except ValueError:
        relative_path = path
    return {
        "path": relative_path.as_posix(),
        "sha256": hashlib.sha256(file_bytes).hexdigest(),
        "size_bytes": len(file_bytes),
    }


def _collect_all_fields_code_version_metadata() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    status_short = _run_git_command(repo_root, ["status", "--short"])
    source_paths = [
        Path(__file__).resolve(),
        repo_root / "src" / "db" / "trading_view_all_fields_duckdb.py",
        repo_root / "src" / "data_loaders" / "api_tradingview_client.py",
    ]
    return {
        "schema_version": ALL_FIELDS_CODE_VERSION_SCHEMA_VERSION,
        "python_version": sys.version.split()[0],
        "git": {
            "commit": _run_git_command(repo_root, ["rev-parse", "HEAD"]),
            "branch": _run_git_command(repo_root, ["branch", "--show-current"]),
            "dirty": bool(status_short),
            "status_short": status_short or "",
        },
        "source_files": [_hash_source_file(path, repo_root) for path in source_paths],
    }


def _fetch_scan_chunk(
    client: ApiTradingViewClient,
    columns: list[str],
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    payload = _build_scan_payload(columns)
    response = requests.post(
        TRADINGVIEW_GLOBAL_SCAN_URL,
        headers=client.headers,
        data=json.dumps(payload),
        timeout=timeout,
    )
    response.raise_for_status()

    response_payload = response.json()
    mapped_payload = ApiTradingViewClient._attach_mapped_rows(response_payload, columns)
    return list(mapped_payload.get("data", []))


def _build_all_fields_api_request_metadata(
    field_names: list[str],
    field_chunks: list[list[str]],
    field_catalog_csv: Path,
    timeout: int,
    chunk_size: int,
    created_at_utc: datetime,
) -> dict[str, Any]:
    request_payload = _build_scan_payload(field_names)
    return {
        "schema_version": ALL_FIELDS_API_REQUEST_PROVENANCE_SCHEMA_VERSION,
        "captured_at_utc": created_at_utc.isoformat(),
        "scan_input_type": "tradingview_chunked_all_fields_export",
        "request_payload": request_payload,
        "request_metadata": {
            "url": TRADINGVIEW_GLOBAL_SCAN_URL,
            "timeout_seconds": timeout,
            "markets": list(request_payload.get("markets", [])),
            "columns": list(request_payload.get("columns", [])),
            "range": list(request_payload.get("range", [])),
            "ignore_unknown_fields": bool(request_payload.get("ignore_unknown_fields")),
            "chunk_size": chunk_size,
            "chunk_count": len(field_chunks),
            "field_catalog_csv": str(field_catalog_csv),
        },
        "chunk_column_groups": field_chunks,
    }


def _merge_rows_by_symbol(
    merged_rows: dict[str, dict[str, Any]], chunk_rows: list[dict[str, Any]]
) -> None:
    for row in chunk_rows:
        symbol = str(row.get("symbol", "")).strip()
        if not symbol:
            continue
        existing_row = merged_rows.setdefault(symbol, {"symbol": symbol})
        existing_row.update(row)


def _flush_symbol_batch(
    conn: sqlite3.Connection, batch: dict[str, dict[str, Any]]
) -> None:
    symbols = list(batch.keys())
    placeholders = ",".join("?" * len(symbols))
    cursor = conn.execute(
        f"SELECT symbol, data FROM symbol_data WHERE symbol IN ({placeholders})",
        symbols,
    )
    existing: dict[str, dict[str, Any]] = {
        sym: json.loads(data_json) for sym, data_json in cursor
    }

    upserts: list[tuple[str, str]] = []
    for sym in symbols:
        merged = existing.get(sym, {})
        merged.update(batch[sym])
        upserts.append((sym, json.dumps(merged, ensure_ascii=False)))

    conn.executemany(
        "INSERT OR REPLACE INTO symbol_data (symbol, data) VALUES (?, ?)",
        upserts,
    )


def _merge_chunk_to_db(
    conn: sqlite3.Connection, chunk_rows: list[dict[str, Any]]
) -> None:
    batch: dict[str, dict[str, Any]] = {}

    for row in chunk_rows:
        symbol = str(row.get("symbol", "")).strip()
        if not symbol:
            continue
        batch[symbol] = {k: v for k, v in row.items() if k != "symbol"}

        if len(batch) >= DB_MERGE_BATCH_SIZE:
            _flush_symbol_batch(conn, batch)
            batch.clear()

    if batch:
        _flush_symbol_batch(conn, batch)

    conn.commit()


def _stream_csv_from_db(
    conn: sqlite3.Connection,
    field_names: list[str],
    output_file: Path,
) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    headers = ["symbol", *field_names]

    with output_file.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)

        cursor = conn.execute("SELECT symbol, data FROM symbol_data ORDER BY symbol")
        for symbol, data_json in cursor:
            data = json.loads(data_json)
            row = [symbol] + [
                _serialize_csv_value(data.get(field)) for field in field_names
            ]
            writer.writerow(row)


def _iter_merged_rows_from_db(
    conn: sqlite3.Connection,
    field_names: list[str],
    run_id: str,
) -> Any:
    cursor = conn.execute("SELECT symbol, data FROM symbol_data ORDER BY symbol")
    for row_number, (symbol, data_json) in enumerate(cursor, start=1):
        data = json.loads(data_json)
        record: dict[str, Any] = {
            "run_id": run_id,
            "row_number": row_number,
            "symbol": symbol,
        }
        for field_name in field_names:
            record[field_name] = data.get(field_name)
        yield record


def _count_merged_rows(conn: sqlite3.Connection) -> int:
    result = conn.execute("SELECT COUNT(*) FROM symbol_data").fetchone()
    return int(result[0]) if result is not None else 0


def _serialize_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _build_output_file_name(output_dir: Path) -> Path:
    today_segment = datetime.now().strftime("%d_%m_%Y")
    return output_dir / f"tradingview_global_all_tdfields_{today_segment}.csv"


def _log_all_fields_duckdb_run_overview(
    overview_log: Path,
    run_id: str,
    storage_period_dir: Path,
    run_output_dir: Path,
    database_path: Path,
    parquet_dir: Path | None,
    field_catalog_csv: Path,
    field_count: int,
    chunk_count: int,
    row_count: int,
    parquet_exports: dict[str, Path],
) -> None:
    overview_log.parent.mkdir(parents=True, exist_ok=True)
    overview_log.write_text("", encoding="utf-8")
    log_to_file(overview_log, "TradingView all-fields DuckDB export")
    log_to_file(overview_log, "=" * 160)
    log_to_file(overview_log, f"Run id: {run_id}")
    log_to_file(overview_log, f"Storage day directory: {storage_period_dir}")
    log_to_file(overview_log, f"Run report directory: {run_output_dir}")
    log_to_file(overview_log, f"DuckDB database: {database_path}")
    log_to_file(overview_log, f"Parquet directory: {parquet_dir or 'disabled'}")
    log_to_file(overview_log, f"Field catalog CSV: {field_catalog_csv}")
    log_to_file(overview_log, f"Field count: {field_count}")
    log_to_file(overview_log, f"Chunk count: {chunk_count}")
    log_to_file(overview_log, f"Rows exported: {row_count}")
    log_to_file(overview_log, "")
    log_to_file(overview_log, "Parquet exports")
    log_to_file(overview_log, "-" * 160)
    if not parquet_exports:
        log_to_file(overview_log, "disabled")
        return
    for table_name, parquet_path in parquet_exports.items():
        log_to_file(overview_log, f"{table_name}: {parquet_path}")


def _set_safe_csv_field_size_limit() -> None:
    """Increase CSV field size limit for wide all-fields CSVs."""
    field_size_limit = sys.maxsize
    while field_size_limit > 0:
        try:
            csv.field_size_limit(field_size_limit)
            return
        except OverflowError:
            field_size_limit //= 10
    raise OverflowError("Unable to configure csv.field_size_limit for this platform.")


def _is_tdfields_chunk_csv(csv_path: Path) -> bool:
    """Return True for row-split exports like ``*_chunk_001.csv``."""
    return "_chunk_" in csv_path.stem


def _parse_date_label(label: str) -> datetime | None:
    """Parse date label in dd_mm_yyyy format."""
    try:
        parsed = datetime.strptime(label, BACKFILL_DATE_FORMAT)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _discover_main_csv_in_dated_folder(folder_path: Path) -> Path:
    """Discover one main (non-chunk) CSV in a dated folder.

    Raises:
        FileNotFoundError: If no CSV or multiple main CSVs found.
    """
    if not folder_path.is_dir():
        raise FileNotFoundError(f"Dated folder not found: {folder_path}")

    candidates = [
        candidate
        for candidate in sorted(folder_path.glob(BACKFILL_CSV_GLOB))
        if not _is_tdfields_chunk_csv(candidate)
    ]

    if not candidates:
        all_csv = sorted(folder_path.glob("*.csv"))
        candidates = [c for c in all_csv if not _is_tdfields_chunk_csv(c)]

    if not candidates:
        raise FileNotFoundError(
            f"No main CSV found in {folder_path} matching {BACKFILL_CSV_GLOB}"
        )
    if len(candidates) > 1:
        raise ValueError(
            f"Multiple main CSVs found in {folder_path}: "
            f"{[c.name for c in candidates]}. Expected exactly one."
        )
    return candidates[0]


def _compute_file_sha256(
    path: Path,
    chunk_size: int = DEFAULT_BACKFILL_SHA256_CHUNK_BYTES,
) -> str | None:
    """Compute SHA256 hash of file contents using bounded memory."""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as file_handle:
            while True:
                chunk = file_handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _resolve_backfill_worker_resources(
    *,
    max_parallel_workers: int,
    duckdb_threads: int | None,
    duckdb_memory_limit_gb: float | None,
    max_system_memory_gb: float,
    memory_reserve_gb: float,
) -> tuple[int, int, float]:
    """Return effective workers, per-worker threads, and per-worker DuckDB memory."""
    available_memory_gb = max(1.0, max_system_memory_gb - memory_reserve_gb)
    max_workers_by_memory = max(
        1,
        int(available_memory_gb // DEFAULT_BACKFILL_ESTIMATED_DAY_MEMORY_GB),
    )
    effective_workers = max(1, min(max_parallel_workers, max_workers_by_memory))

    if duckdb_memory_limit_gb is None:
        per_worker_memory_gb = available_memory_gb / effective_workers
    else:
        per_worker_memory_gb = min(
            duckdb_memory_limit_gb,
            available_memory_gb / effective_workers,
        )

    cpu_count = os.cpu_count() or DEFAULT_BACKFILL_DUCKDB_THREADS
    default_threads = min(DEFAULT_BACKFILL_DUCKDB_THREADS, cpu_count)
    if duckdb_threads is None:
        per_worker_threads = max(1, default_threads // effective_workers)
    elif effective_workers <= 1:
        per_worker_threads = max(1, duckdb_threads)
    else:
        per_worker_threads = max(1, duckdb_threads // effective_workers)

    return effective_workers, per_worker_threads, per_worker_memory_gb


def _resolve_backfill_parquet_table_names(
    export_parquet: bool,
    export_all_fields_parquet: bool,
    parquet_table_names: list[str] | None,
) -> list[str] | None:
    if not export_parquet:
        return None
    if parquet_table_names is not None:
        return parquet_table_names
    if export_all_fields_parquet:
        return None
    return list(BACKFILL_METADATA_PARQUET_TABLES)


def _progress_bar(completed: int, total: int, width: int = 20) -> str:
    """Build ASCII progress bar: [=========>          ] 3/10"""
    if total <= 0:
        return "[" + " " * width + "] 0/0"
    filled = int(width * completed / total)
    filled = min(filled, width)
    return f"[{'=' * filled}{'>' if filled < width else ''}{' ' * (width - filled - (1 if filled < width else 0))}] {completed}/{total}"


@dataclass
class _BackfillDayResult:
    """Result of backfilling a single day."""

    day_label: str
    status: str  # 'completed', 'skipped', 'error'
    skip_reason: str
    source_csv: Path
    source_csv_sha256: str | None
    rows_source: int
    rows_duckdb: int
    run_id: str
    database_path: Path
    execution_seconds: float
    error_message: str = ""


class _AllFieldsCsvDuckDBBackfillProgress:
    """Progress reporter for Git Bash visualization."""

    def __init__(self, total_days: int) -> None:
        self.total_days = total_days
        self.completed = 0
        self.started_at = datetime.now(tz=timezone.utc)
        self._day_times: list[float] = []

    def _elapsed(self) -> float:
        return (datetime.now(tz=timezone.utc) - self.started_at).total_seconds()

    def _eta_seconds(self) -> float:
        if not self._day_times or self.completed >= self.total_days:
            return 0.0
        avg = sum(self._day_times) / len(self._day_times)
        remaining = self.total_days - self.completed
        return avg * remaining

    def day_started(self, day_label: str) -> None:
        print(
            f"[{_progress_bar(self.completed, self.total_days)}] Starting {day_label}...",
            flush=True,
        )

    def day_finished(
        self, day_label: str, status: str, rows: int, elapsed: float
    ) -> None:
        self.completed += 1
        self._day_times.append(elapsed)
        eta = self._eta_seconds()
        eta_str = f" | ETA ~{int(eta // 60)}m {int(eta % 60)}s" if eta > 0 else ""
        print(
            f"[{_progress_bar(self.completed, self.total_days)}] {day_label}: {status.upper()} "
            f"({rows:,} rows in {elapsed:.1f}s){eta_str}",
            flush=True,
        )

    def summary(
        self, processed: int, skipped: int, errors: int, total_seconds: float
    ) -> None:
        print("=" * 80, flush=True)
        print(
            f"Backfill complete: {processed} processed, {skipped} skipped, {errors} errors "
            f"in {total_seconds:.1f}s",
            flush=True,
        )


# Manifest handling
_BACKFILL_MANIFEST_HEADERS = [
    "day_label",
    "status",
    "skip_reason",
    "source_csv",
    "source_csv_sha256",
    "rows_source",
    "rows_duckdb",
    "run_id",
    "database_path",
    "execution_seconds",
]


def _read_manifest(manifest_path: Path) -> list[dict[str, str]]:
    """Read existing manifest CSV if present."""
    if not manifest_path.exists():
        return []
    rows: list[dict[str, str]] = []
    with manifest_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def _write_manifest(manifest_path: Path, rows: list[dict[str, str]]) -> None:
    """Write manifest CSV, overwriting existing."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_BACKFILL_MANIFEST_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in _BACKFILL_MANIFEST_HEADERS})


def _find_existing_manifest_row(
    manifest_rows: list[dict[str, str]], day_label: str
) -> dict[str, str] | None:
    """Find existing manifest row for a day."""
    for row in manifest_rows:
        if row.get("day_label") == day_label and row.get("status") == "completed":
            return row
    return None


def _should_skip_day(
    day_label: str,
    source_csv: Path,
    source_sha256: str | None,
    manifest_rows: list[dict[str, str]],
    skip_existing: bool,
    force_rerun: bool,
) -> tuple[bool, str]:
    """Determine if a day should be skipped."""
    if force_rerun:
        return False, ""
    if not skip_existing:
        return False, ""
    existing = _find_existing_manifest_row(manifest_rows, day_label)
    if existing is None:
        return False, ""
    # Check SHA256 match for idempotency
    existing_sha = existing.get("source_csv_sha256", "")
    if source_sha256 and existing_sha and existing_sha == source_sha256:
        return True, "source_csv_unchanged"
    return True, "previous_run_exists"


def _quote_identifier_sqlite(identifier: str) -> str:
    """Quote SQL identifier for safety."""
    # Simple quoting: wrap in double quotes, escape internal quotes
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _read_csv_header_columns(csv_path: Path) -> list[str]:
    """Read CSV header column names without loading row data."""
    _set_safe_csv_field_size_limit()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.reader(csv_file)
        header = next(reader, None)
    if not header:
        return []
    return [str(column).strip() for column in header if str(column).strip()]


@dataclass(frozen=True)
class _BackfillColumnCoverage:
    csv_columns: tuple[str, ...]
    catalog_fields: tuple[str, ...]
    matched_fields: tuple[str, ...]
    missing_in_csv: tuple[str, ...]
    extra_in_csv: tuple[str, ...]


def _analyze_backfill_column_coverage(
    data_field_names: list[str],
    csv_columns: list[str],
) -> _BackfillColumnCoverage:
    """Compare catalog fields to CSV header columns for tolerant backfill."""
    csv_set = set(csv_columns)
    matched_fields = [field for field in data_field_names if field in csv_set]
    missing_in_csv = [field for field in data_field_names if field not in csv_set]
    extra_in_csv = [
        column
        for column in csv_columns
        if column not in data_field_names and column != "symbol"
    ]
    return _BackfillColumnCoverage(
        csv_columns=tuple(csv_columns),
        catalog_fields=tuple(data_field_names),
        matched_fields=tuple(matched_fields),
        missing_in_csv=tuple(missing_in_csv),
        extra_in_csv=tuple(extra_in_csv),
    )


def _build_backfill_field_select_expressions(
    data_field_names: list[str],
    csv_column_set: set[str],
) -> list[str]:
    """Build SELECT expressions mapping catalog fields to CSV values or NULL."""
    expressions: list[str] = []
    for field in data_field_names:
        quoted_field = _quote_identifier_sqlite(field)
        if field in csv_column_set:
            expressions.append(
                f"NULLIF(TRIM(CAST({quoted_field} AS VARCHAR)), '') AS {quoted_field}"
            )
        else:
            expressions.append(f"CAST(NULL AS VARCHAR) AS {quoted_field}")
    return expressions


def _build_backfill_column_coverage_metadata(
    coverage: _BackfillColumnCoverage,
) -> dict[str, Any]:
    return {
        "csv_column_count": len(coverage.csv_columns),
        "catalog_field_count": len(coverage.catalog_fields),
        "matched_field_count": len(coverage.matched_fields),
        "catalog_fields_missing_in_csv_count": len(coverage.missing_in_csv),
        "csv_columns_not_in_catalog_count": len(coverage.extra_in_csv),
        "catalog_fields_missing_in_csv": list(coverage.missing_in_csv),
        "csv_columns_not_in_catalog": list(coverage.extra_in_csv),
    }


def _log_backfill_column_coverage(
    csv_path: Path,
    coverage: _BackfillColumnCoverage,
) -> None:
    if not coverage.missing_in_csv and not coverage.extra_in_csv:
        return
    print(
        f"  Column coverage for {csv_path.name}: "
        f"{len(coverage.matched_fields)} matched, "
        f"{len(coverage.missing_in_csv)} catalog-only (NULL), "
        f"{len(coverage.extra_in_csv)} csv-only (ignored)",
        flush=True,
    )
    if coverage.missing_in_csv:
        sample = ", ".join(coverage.missing_in_csv[:10])
        suffix = "..." if len(coverage.missing_in_csv) > 10 else ""
        print(
            f"    Catalog fields missing in CSV (sample): {sample}{suffix}",
            flush=True,
        )
    if coverage.extra_in_csv:
        sample = ", ".join(coverage.extra_in_csv[:10])
        suffix = "..." if len(coverage.extra_in_csv) > 10 else ""
        print(
            f"    CSV columns not in catalog (sample): {sample}{suffix}",
            flush=True,
        )


def _backfill_single_day_duckdb_native(
    csv_path: Path,
    field_names: list[str],
    storage_layout: TradingViewAllFieldsDailyStorageLayout,
    run_id: str,
    run_label: str,
    source_date_utc: datetime,
    source_csv_sha256: str | None,
    export_parquet: bool,
    export_all_fields_parquet: bool,
    parquet_table_names: list[str] | None,
    create_indexes: bool,
    duckdb_threads: int | None,
    duckdb_memory_limit_gb: float,
    duckdb_temp_directory: Path | None = None,
) -> _BackfillDayResult:
    """Backfill a single day using DuckDB native read_csv for performance.

    Uses INSERT...SELECT FROM read_csv to avoid loading multi-GB CSV into Python.
    """
    import duckdb as _duckdb_module

    started_at = datetime.now(tz=timezone.utc)

    # Build list of data field names (excluding symbol)
    data_field_names = [fn for fn in field_names if fn != "symbol"]
    csv_columns = _read_csv_header_columns(csv_path)
    column_coverage = _analyze_backfill_column_coverage(data_field_names, csv_columns)
    _log_backfill_column_coverage(csv_path, column_coverage)
    csv_column_set = set(csv_columns)

    # Ensure directories exist
    storage_layout.run_output_dir.mkdir(parents=True, exist_ok=True)
    storage_layout.database_path.parent.mkdir(parents=True, exist_ok=True)
    if export_parquet:
        storage_layout.parquet_dir.mkdir(parents=True, exist_ok=True)

    # Acquire write lock
    with _duckdb_daily_writer_lock(storage_layout.database_path):
        # Build metadata for backfill
        code_version_metadata = _collect_all_fields_code_version_metadata()
        api_request_metadata = {
            "schema_version": ALL_FIELDS_BACKFILL_SCHEMA_VERSION,
            "captured_at_utc": source_date_utc.isoformat(),
            "scan_input_type": "historical_all_fields_csv_backfill",
            "source_csv": str(csv_path),
            "source_csv_sha256": source_csv_sha256,
            "source_csv_size_bytes": (
                csv_path.stat().st_size if csv_path.exists() else None
            ),
            "field_count": len(data_field_names),
            **_build_backfill_column_coverage_metadata(column_coverage),
        }

        # Build SQL for field columns: use CSV values when present, else NULL.
        field_selects = _build_backfill_field_select_expressions(
            data_field_names,
            csv_column_set,
        )

        field_sql = ",\n    ".join(field_selects) if field_selects else ""
        resolved_parquet_tables = _resolve_backfill_parquet_table_names(
            export_parquet=export_parquet,
            export_all_fields_parquet=export_all_fields_parquet,
            parquet_table_names=parquet_table_names,
        )
        resolved_temp_directory = (
            duckdb_temp_directory
            if duckdb_temp_directory is not None
            else storage_layout.period_dir / ".duckdb_temp"
        )
        resolved_temp_directory.mkdir(parents=True, exist_ok=True)

        parquet_exports: dict[str, Path] = {}

        with TradingViewAllFieldsDuckDBStore(
            database_path=storage_layout.database_path,
            parquet_dir=storage_layout.parquet_dir if export_parquet else None,
            threads=duckdb_threads,
            memory_limit=f"{duckdb_memory_limit_gb}GB",
            preserve_insertion_order=False,
            enable_object_cache=False,
            temp_directory=resolved_temp_directory,
        ) as duckdb_store:
            duckdb_store.delete_run_data(run_id)

            # Ensure all_fields_rows table exists by calling append with empty iterator
            duckdb_store.append_all_fields_rows(
                run_id=run_id,
                field_names=data_field_names,
                rows=iter([]),
            )

            csv_read_sql = f"""
                read_csv(
                    {str(csv_path)!r},
                    header=true,
                    all_varchar=true,
                    strict_mode=false
                )
            """
            symbol_filter_sql = "TRIM(COALESCE(CAST(\"symbol\" AS VARCHAR), '')) <> ''"

            if data_field_names:
                source_select_sql = f"""
                    SELECT
                        CAST("symbol" AS VARCHAR) AS symbol,
                        {field_sql}
                    FROM {csv_read_sql}
                    WHERE {symbol_filter_sql}
                """
            else:
                source_select_sql = f"""
                    SELECT CAST("symbol" AS VARCHAR) AS symbol
                    FROM {csv_read_sql}
                    WHERE {symbol_filter_sql}
                """

            duckdb_store.conn.execute(
                "CREATE OR REPLACE TEMP TABLE backfill_csv_source AS "
                f"{source_select_sql}"
            )
            pre_count_result = duckdb_store.conn.execute(
                "SELECT COUNT(*) FROM backfill_csv_source"
            ).fetchone()
            expected_rows = int(pre_count_result[0]) if pre_count_result else 0

            # Register the run
            duckdb_store.begin_transaction()
            try:
                duckdb_store.register_run(
                    run_id=run_id,
                    created_at_utc=source_date_utc,
                    suite_name="tradingview_all_fields_export_duckdb",
                    scan_data_count=expected_rows,
                    profile_names=[],
                    industries=None,
                    min_market_cap_usd=None,
                    max_market_cap_usd=None,
                    include_blind_spot_sections=False,
                    api_request_metadata=api_request_metadata,
                    code_version_metadata=code_version_metadata,
                    run_label=run_label,
                    run_id_generated=True,
                    notes=(
                        "Historical backfill from CSV to daily all-fields DuckDB storage. "
                        f"Source: {csv_path.name}. Native read_csv ingest (single pass)."
                    ),
                )
                duckdb_store.commit()
            except Exception:
                duckdb_store.rollback()
                raise

            if data_field_names:
                columns_sql = ", ".join(
                    f"{_quote_identifier_sqlite(fn)}"
                    for fn in ["run_id", "row_number", "symbol"] + data_field_names
                )
                insert_sql = f"""
                    INSERT INTO all_fields_rows ({columns_sql})
                    SELECT
                        {run_id!r} AS run_id,
                        ROW_NUMBER() OVER (ORDER BY symbol) AS row_number,
                        symbol,
                        {", ".join(_quote_identifier_sqlite(field) for field in data_field_names)}
                    FROM backfill_csv_source
                """
            else:
                insert_sql = f"""
                    INSERT INTO all_fields_rows (run_id, row_number, symbol)
                    SELECT
                        {run_id!r} AS run_id,
                        ROW_NUMBER() OVER (ORDER BY symbol) AS row_number,
                        symbol
                    FROM backfill_csv_source
                """

            duckdb_store.conn.execute(insert_sql)
            duckdb_store.conn.execute("DROP TABLE IF EXISTS backfill_csv_source")
            duckdb_store.conn.execute("CHECKPOINT")

            verify_result = duckdb_store.conn.execute(
                "SELECT COUNT(*) FROM all_fields_rows WHERE run_id = ?",
                [run_id],
            ).fetchone()
            actual_rows = int(verify_result[0]) if verify_result else 0

            if actual_rows != expected_rows:
                raise RuntimeError(
                    f"Row count mismatch for {csv_path}: expected {expected_rows}, got {actual_rows}"
                )

            if create_indexes or export_parquet:
                duckdb_store.close()
                duckdb_store.open()

            if create_indexes:
                duckdb_store.create_analysis_indexes()

            # Write overview log
            overview_log = storage_layout.run_output_dir / "_duckdb_run_overview.log"
            _log_all_fields_duckdb_run_overview(
                overview_log=overview_log,
                run_id=run_id,
                storage_period_dir=storage_layout.period_dir,
                run_output_dir=storage_layout.run_output_dir,
                database_path=storage_layout.database_path,
                parquet_dir=storage_layout.parquet_dir if export_parquet else None,
                field_catalog_csv=FIELD_CATALOG_CSV,
                field_count=len(data_field_names),
                chunk_count=1,
                row_count=actual_rows,
                parquet_exports=parquet_exports,
            )

            duckdb_store.register_report(
                run_id=run_id,
                report_key="_duckdb_run_overview",
                report_type="duckdb_overview_log",
                file_path=overview_log,
            )

            if export_parquet:
                parquet_exports = duckdb_store.export_tables_to_parquet(
                    run_id=run_id,
                    parquet_dir=storage_layout.parquet_dir,
                    table_names=resolved_parquet_tables,
                )

    elapsed = (datetime.now(tz=timezone.utc) - started_at).total_seconds()

    return _BackfillDayResult(
        day_label=storage_layout.period_dir.name,
        status="completed",
        skip_reason="",
        source_csv=csv_path,
        source_csv_sha256=source_csv_sha256,
        rows_source=expected_rows,
        rows_duckdb=actual_rows,
        run_id=run_id,
        database_path=storage_layout.database_path,
        execution_seconds=elapsed,
    )


def _backfill_single_day_fallback(
    csv_path: Path,
    field_names: list[str],
    storage_layout: TradingViewAllFieldsDailyStorageLayout,
    run_id: str,
    run_label: str,
    source_date_utc: datetime,
    source_csv_sha256: str | None,
    export_parquet: bool,
    export_all_fields_parquet: bool,
    parquet_table_names: list[str] | None,
    create_indexes: bool,
    duckdb_threads: int | None,
    duckdb_memory_limit_gb: float,
    duckdb_temp_directory: Path | None = None,
) -> _BackfillDayResult:
    """Fallback backfill using Python iteration when native read_csv fails."""
    csv_size_bytes = csv_path.stat().st_size if csv_path.exists() else 0
    if csv_size_bytes > BACKFILL_FALLBACK_MAX_CSV_BYTES:
        raise RuntimeError(
            f"Fallback ingest refused for large CSV ({csv_size_bytes:,} bytes): {csv_path}. "
            "Native DuckDB ingest must succeed for multi-GB snapshots."
        )

    _set_safe_csv_field_size_limit()

    started_at = datetime.now(tz=timezone.utc)
    data_field_names = [fn for fn in field_names if fn != "symbol"]
    csv_columns = _read_csv_header_columns(csv_path)
    column_coverage = _analyze_backfill_column_coverage(data_field_names, csv_columns)
    _log_backfill_column_coverage(csv_path, column_coverage)

    # Read CSV using Python csv
    rows: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw_row in reader:
            symbol = str(raw_row.get("symbol", "")).strip()
            if not symbol:
                continue
            row = {"symbol": symbol}
            for field in data_field_names:
                val = raw_row.get(field, "")
                # Normalize blank to None
                row[field] = val if val and val.strip() else None
            rows.append(row)

    expected_rows = len(rows)

    # Build insert records
    def _iter_records():
        for row_number, row in enumerate(rows, start=1):
            record: dict[str, Any] = {
                "run_id": run_id,
                "row_number": row_number,
                "symbol": row["symbol"],
            }
            for field in data_field_names:
                record[field] = row.get(field)
            yield record

    # Ensure directories
    storage_layout.run_output_dir.mkdir(parents=True, exist_ok=True)
    storage_layout.database_path.parent.mkdir(parents=True, exist_ok=True)
    if export_parquet:
        storage_layout.parquet_dir.mkdir(parents=True, exist_ok=True)

    with _duckdb_daily_writer_lock(storage_layout.database_path):
        code_version_metadata = _collect_all_fields_code_version_metadata()
        api_request_metadata = {
            "schema_version": ALL_FIELDS_BACKFILL_SCHEMA_VERSION,
            "captured_at_utc": source_date_utc.isoformat(),
            "scan_input_type": "historical_all_fields_csv_backfill",
            "source_csv": str(csv_path),
            "source_csv_sha256": source_csv_sha256,
            "source_csv_size_bytes": (
                csv_path.stat().st_size if csv_path.exists() else None
            ),
            "field_count": len(data_field_names),
            "ingest_method": "fallback_python_iteration",
            **_build_backfill_column_coverage_metadata(column_coverage),
        }

        parquet_exports: dict[str, Path] = {}
        resolved_parquet_tables = _resolve_backfill_parquet_table_names(
            export_parquet=export_parquet,
            export_all_fields_parquet=export_all_fields_parquet,
            parquet_table_names=parquet_table_names,
        )
        resolved_temp_directory = (
            duckdb_temp_directory
            if duckdb_temp_directory is not None
            else storage_layout.period_dir / ".duckdb_temp"
        )
        resolved_temp_directory.mkdir(parents=True, exist_ok=True)

        with TradingViewAllFieldsDuckDBStore(
            database_path=storage_layout.database_path,
            parquet_dir=storage_layout.parquet_dir if export_parquet else None,
            threads=duckdb_threads,
            memory_limit=f"{duckdb_memory_limit_gb}GB",
            preserve_insertion_order=False,
            enable_object_cache=False,
            temp_directory=resolved_temp_directory,
        ) as duckdb_store:
            duckdb_store.delete_run_data(run_id)

            duckdb_store.begin_transaction()
            try:
                duckdb_store.register_run(
                    run_id=run_id,
                    created_at_utc=source_date_utc,
                    suite_name="tradingview_all_fields_export_duckdb",
                    scan_data_count=expected_rows,
                    profile_names=[],
                    industries=None,
                    min_market_cap_usd=None,
                    max_market_cap_usd=None,
                    include_blind_spot_sections=False,
                    api_request_metadata=api_request_metadata,
                    code_version_metadata=code_version_metadata,
                    run_label=run_label,
                    run_id_generated=True,
                    notes=(
                        "Historical backfill from CSV to daily all-fields DuckDB storage. "
                        f"Source: {csv_path.name}. Fallback Python iteration ingest."
                    ),
                )
                duckdb_store.commit()
            except Exception:
                duckdb_store.rollback()
                raise

            # Append rows
            duckdb_store.append_all_fields_rows(
                run_id=run_id,
                field_names=data_field_names,
                rows=_iter_records(),
            )

            # Verify
            verify_sql = "SELECT COUNT(*) FROM all_fields_rows WHERE run_id = ?"
            verify_result = duckdb_store.conn.execute(verify_sql, [run_id]).fetchone()
            actual_rows = int(verify_result[0]) if verify_result else 0

            if actual_rows != expected_rows:
                raise RuntimeError(
                    f"Row count mismatch for {csv_path}: expected {expected_rows}, got {actual_rows}"
                )

            if create_indexes or export_parquet:
                duckdb_store.close()
                duckdb_store.open()

            if create_indexes:
                duckdb_store.create_analysis_indexes()

            overview_log = storage_layout.run_output_dir / "_duckdb_run_overview.log"
            _log_all_fields_duckdb_run_overview(
                overview_log=overview_log,
                run_id=run_id,
                storage_period_dir=storage_layout.period_dir,
                run_output_dir=storage_layout.run_output_dir,
                database_path=storage_layout.database_path,
                parquet_dir=storage_layout.parquet_dir if export_parquet else None,
                field_catalog_csv=FIELD_CATALOG_CSV,
                field_count=len(data_field_names),
                chunk_count=1,
                row_count=actual_rows,
                parquet_exports=parquet_exports,
            )

            duckdb_store.register_report(
                run_id=run_id,
                report_key="_duckdb_run_overview",
                report_type="duckdb_overview_log",
                file_path=overview_log,
            )

            if export_parquet:
                parquet_exports = duckdb_store.export_tables_to_parquet(
                    run_id=run_id,
                    parquet_dir=storage_layout.parquet_dir,
                    table_names=resolved_parquet_tables,
                )

    elapsed = (datetime.now(tz=timezone.utc) - started_at).total_seconds()

    return _BackfillDayResult(
        day_label=storage_layout.period_dir.name,
        status="completed",
        skip_reason="",
        source_csv=csv_path,
        source_csv_sha256=source_csv_sha256,
        rows_source=expected_rows,
        rows_duckdb=actual_rows,
        run_id=run_id,
        database_path=storage_layout.database_path,
        execution_seconds=elapsed,
    )


def _backfill_single_day(
    day_label: str,
    base_data_dir: Path,
    field_catalog_csv: Path,
    run_label_prefix: str,
    export_parquet: bool,
    export_all_fields_parquet: bool,
    parquet_table_names: list[str] | None,
    create_indexes: bool,
    duckdb_threads: int | None,
    duckdb_memory_limit_gb: float,
    duckdb_temp_directory: Path | None,
    manifest_rows: list[dict[str, str]],
    skip_existing: bool,
    force_rerun: bool,
) -> _BackfillDayResult:
    """Backfill a single day from CSV to DuckDB.

    Returns _BackfillDayResult with status 'completed', 'skipped', or 'error'.
    """
    import time

    started_at = time.perf_counter()

    try:
        # Validate day label format
        source_date_utc = _parse_date_label(day_label)
        if source_date_utc is None:
            return _BackfillDayResult(
                day_label=day_label,
                status="error",
                skip_reason="",
                source_csv=Path(),
                source_csv_sha256=None,
                rows_source=0,
                rows_duckdb=0,
                run_id="",
                database_path=Path(),
                execution_seconds=time.perf_counter() - started_at,
                error_message=f"Invalid day label format: {day_label} (expected dd_mm_yyyy)",
            )

        # Resolve folder and CSV
        folder_path = base_data_dir / day_label
        try:
            csv_path = _discover_main_csv_in_dated_folder(folder_path)
        except (FileNotFoundError, ValueError) as e:
            return _BackfillDayResult(
                day_label=day_label,
                status="error",
                skip_reason="",
                source_csv=Path(),
                source_csv_sha256=None,
                rows_source=0,
                rows_duckdb=0,
                run_id="",
                database_path=Path(),
                execution_seconds=time.perf_counter() - started_at,
                error_message=str(e),
            )

        # Compute SHA256 for idempotency
        source_csv_sha256 = _compute_file_sha256(csv_path)

        # Check skip conditions
        should_skip, skip_reason = _should_skip_day(
            day_label,
            csv_path,
            source_csv_sha256,
            manifest_rows,
            skip_existing,
            force_rerun,
        )
        if should_skip:
            return _BackfillDayResult(
                day_label=day_label,
                status="skipped",
                skip_reason=skip_reason,
                source_csv=csv_path,
                source_csv_sha256=source_csv_sha256,
                rows_source=0,
                rows_duckdb=0,
                run_id="",
                database_path=Path(),
                execution_seconds=time.perf_counter() - started_at,
            )

        # Load field catalog
        field_names = _load_field_names(field_catalog_csv)
        if not field_names:
            return _BackfillDayResult(
                day_label=day_label,
                status="error",
                skip_reason="",
                source_csv=csv_path,
                source_csv_sha256=source_csv_sha256,
                rows_source=0,
                rows_duckdb=0,
                run_id="",
                database_path=Path(),
                execution_seconds=time.perf_counter() - started_at,
                error_message=f"No field names found in {field_catalog_csv}",
            )

        # Build storage layout and run ID
        run_label = f"{run_label_prefix}_{day_label}"
        run_id = _build_all_fields_duckdb_run_id(run_label, source_date_utc)

        storage_layout = _build_all_fields_daily_storage_layout(
            run_id=run_id,
            created_at_utc=source_date_utc,
            output_dir=base_data_dir,
        )

        # Attempt native DuckDB ingest first, fallback to Python iteration
        try:
            result = _backfill_single_day_duckdb_native(
                csv_path=csv_path,
                field_names=field_names,
                storage_layout=storage_layout,
                run_id=run_id,
                run_label=run_label,
                source_date_utc=source_date_utc,
                source_csv_sha256=source_csv_sha256,
                export_parquet=export_parquet,
                export_all_fields_parquet=export_all_fields_parquet,
                parquet_table_names=parquet_table_names,
                create_indexes=create_indexes,
                duckdb_threads=duckdb_threads,
                duckdb_memory_limit_gb=duckdb_memory_limit_gb,
                duckdb_temp_directory=duckdb_temp_directory,
            )
        except Exception as native_exc:
            # Log native failure and try fallback
            print(
                f"  Native ingest failed for {day_label}: {native_exc}. Trying fallback...",
                flush=True,
            )
            result = _backfill_single_day_fallback(
                csv_path=csv_path,
                field_names=field_names,
                storage_layout=storage_layout,
                run_id=run_id,
                run_label=run_label,
                source_date_utc=source_date_utc,
                source_csv_sha256=source_csv_sha256,
                export_parquet=export_parquet,
                export_all_fields_parquet=export_all_fields_parquet,
                parquet_table_names=parquet_table_names,
                create_indexes=create_indexes,
                duckdb_threads=duckdb_threads,
                duckdb_memory_limit_gb=duckdb_memory_limit_gb,
                duckdb_temp_directory=duckdb_temp_directory,
            )

        return result

    except Exception as e:
        elapsed = time.perf_counter() - started_at
        return _BackfillDayResult(
            day_label=day_label,
            status="error",
            skip_reason="",
            source_csv=csv_path if "csv_path" in dir() else Path(),
            source_csv_sha256=(
                source_csv_sha256 if "source_csv_sha256" in dir() else None
            ),
            rows_source=0,
            rows_duckdb=0,
            run_id=run_id if "run_id" in dir() else "",
            database_path=(
                storage_layout.database_path if "storage_layout" in dir() else Path()
            ),
            execution_seconds=elapsed,
            error_message=str(e),
        )


# !!! RAN THIS TO BACKFILL - 13-06-2026 !!!
# DO NOT DELETE THIS COMMENTED CODE - it is a test case for the backfill function
# result = backfill_historical_all_fields_csv_folders_to_duckdb(
#     day_folder_labels=["01_04_2026", "02_04_2026"],
#     max_parallel_workers=2,           # Use 2-3 for bulk backfill
#     export_parquet=True,                # Also export parquet files
#     skip_existing=True,                 # Skip unchanged CSVs
#     duckdb_memory_limit_gb=24.0,        # Tune for 32GB RAM
# )
def backfill_historical_all_fields_csv_folders_to_duckdb(
    day_folder_labels: list[str],
    base_data_dir: Path = DUCKDB_OUTPUT_DIR,
    field_catalog_csv: Path = FIELD_CATALOG_CSV,
    export_parquet: bool = True,
    export_all_fields_parquet: bool = False,
    parquet_table_names: list[str] | None = None,
    create_indexes: bool = False,
    run_label_prefix: str = "csv_all_fields_backfill",
    skip_existing: bool = True,
    force_rerun: bool = False,
    max_parallel_workers: int = DEFAULT_BACKFILL_PARALLEL_WORKERS,
    parallel_mode: Literal["process", "thread"] = "thread",
    duckdb_threads: int | None = None,
    duckdb_memory_limit_gb: float | None = None,
    max_system_memory_gb: float = DEFAULT_BACKFILL_SYSTEM_MEMORY_GB,
    memory_reserve_gb: float = DEFAULT_BACKFILL_MEMORY_RESERVE_GB,
    duckdb_temp_directory: Path | None = None,
    manifest_dir: Path | None = None,
) -> dict[str, Any]:
    """Backfill historical all-fields CSV snapshots into daily DuckDB format.

    Converts past CSV exports to the same DuckDB layout as export_all_tradingview_fields_duckdb.
    Each day folder (e.g., "01_04_2026") is processed independently.

    Args:
        day_folder_labels: List of dated folder names in dd_mm_yyyy format (e.g., ["01_04_2026"]).
        base_data_dir: Base directory containing dated folders (default: DUCKDB_OUTPUT_DIR).
        field_catalog_csv: Path to field catalog CSV defining expected columns.
        export_parquet: Whether to export parquet files alongside DuckDB.
        export_all_fields_parquet: When True, also export the wide all_fields_rows table.
            Defaults to False because that duplicate is very memory-heavy during backfill.
        parquet_table_names: Optional explicit parquet table list override.
        create_indexes: Whether to create analysis indexes (off by default for speed).
        run_label_prefix: Prefix for run labels in metadata.
        skip_existing: Skip days already successfully backfilled with matching SHA256.
        force_rerun: Delete and re-import existing runs regardless of SHA256.
        max_parallel_workers: Number of parallel workers (default: 1).
        parallel_mode: "thread" (default, lower overhead) or "process".
        duckdb_threads: DuckDB threads per worker (auto-tuned when None).
        duckdb_memory_limit_gb: Optional per-worker DuckDB memory cap. When None, derived
            from max_system_memory_gb and max_parallel_workers.
        max_system_memory_gb: Total RAM budget for the backfill run (default: 30).
        memory_reserve_gb: Headroom reserved for OS/Python (default: 4).
        duckdb_temp_directory: Optional spill directory for DuckDB temp files.
        manifest_dir: Directory for backfill manifest CSV (default: base_data_dir).

    Returns:
        dict with:
            - 'results': list of _BackfillDayResult (as dicts)
            - 'manifest_path': Path to manifest CSV
            - 'processed_count': Number of successfully processed days
            - 'skipped_count': Number of skipped days
            - 'error_count': Number of days with errors
            - 'total_execution_seconds': Total wall-clock time

    Example:
        result = backfill_historical_all_fields_csv_folders_to_duckdb(
            day_folder_labels=["01_04_2026", "02_04_2026"],
            max_parallel_workers=2,
            export_parquet=True,
        )
    """
    import time

    total_start = time.perf_counter()

    # Validate day labels
    for label in day_folder_labels:
        if _parse_date_label(label) is None:
            raise ValueError(
                f"Invalid day_folder_labels format: '{label}'. Expected dd_mm_yyyy (e.g., 01_04_2026)."
            )

    # Resolve manifest path
    manifest_path = (manifest_dir or base_data_dir) / ALL_FIELDS_BACKFILL_MANIFEST_NAME
    manifest_rows = _read_manifest(manifest_path)

    # Setup progress reporter
    progress = _AllFieldsCsvDuckDBBackfillProgress(len(day_folder_labels))

    effective_workers, per_worker_threads, per_worker_memory_gb = (
        _resolve_backfill_worker_resources(
            max_parallel_workers=max_parallel_workers,
            duckdb_threads=duckdb_threads,
            duckdb_memory_limit_gb=duckdb_memory_limit_gb,
            max_system_memory_gb=max_system_memory_gb,
            memory_reserve_gb=memory_reserve_gb,
        )
    )
    if effective_workers != max_parallel_workers:
        print(
            f"Backfill worker count capped for memory budget: "
            f"{max_parallel_workers} requested -> {effective_workers} effective "
            f"({per_worker_memory_gb:.1f} GB DuckDB/worker, {per_worker_threads} threads/worker)",
            flush=True,
        )

    results: list[_BackfillDayResult] = []
    processed_count = 0
    skipped_count = 0
    error_count = 0

    # Sequential or parallel execution
    if effective_workers <= 1 or len(day_folder_labels) == 1:
        # Sequential execution
        for day_label in day_folder_labels:
            progress.day_started(day_label)
            result = _backfill_single_day(
                day_label=day_label,
                base_data_dir=base_data_dir,
                field_catalog_csv=field_catalog_csv,
                run_label_prefix=run_label_prefix,
                export_parquet=export_parquet,
                export_all_fields_parquet=export_all_fields_parquet,
                parquet_table_names=parquet_table_names,
                create_indexes=create_indexes,
                duckdb_threads=per_worker_threads,
                duckdb_memory_limit_gb=per_worker_memory_gb,
                duckdb_temp_directory=duckdb_temp_directory,
                manifest_rows=manifest_rows,
                skip_existing=skip_existing,
                force_rerun=force_rerun,
            )
            results.append(result)

            if result.status == "completed":
                processed_count += 1
            elif result.status == "skipped":
                skipped_count += 1
            else:
                error_count += 1

            progress.day_finished(
                day_label=day_label,
                status=result.status,
                rows=result.rows_duckdb,
                elapsed=result.execution_seconds,
            )
    else:
        # Parallel execution using thread/process pool
        # Note: Each day writes to a different .duckdb file, so no lock contention
        ExecutorClass = (
            ProcessPoolExecutor if parallel_mode == "process" else ThreadPoolExecutor
        )

        with ExecutorClass(max_workers=effective_workers) as executor:
            # Submit all tasks
            future_to_label = {
                executor.submit(
                    _backfill_single_day,
                    day_label=day_label,
                    base_data_dir=base_data_dir,
                    field_catalog_csv=field_catalog_csv,
                    run_label_prefix=run_label_prefix,
                    export_parquet=export_parquet,
                    export_all_fields_parquet=export_all_fields_parquet,
                    parquet_table_names=parquet_table_names,
                    create_indexes=create_indexes,
                    duckdb_threads=per_worker_threads,
                    duckdb_memory_limit_gb=per_worker_memory_gb,
                    duckdb_temp_directory=duckdb_temp_directory,
                    manifest_rows=manifest_rows,
                    skip_existing=skip_existing,
                    force_rerun=force_rerun,
                ): day_label
                for day_label in day_folder_labels
            }

            # Collect results as they complete
            for future in as_completed(future_to_label):
                day_label = future_to_label[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = _BackfillDayResult(
                        day_label=day_label,
                        status="error",
                        skip_reason="",
                        source_csv=Path(),
                        source_csv_sha256=None,
                        rows_source=0,
                        rows_duckdb=0,
                        run_id="",
                        database_path=Path(),
                        execution_seconds=0.0,
                        error_message=str(e),
                    )

                results.append(result)

                if result.status == "completed":
                    processed_count += 1
                elif result.status == "skipped":
                    skipped_count += 1
                else:
                    error_count += 1

                progress.day_finished(
                    day_label=day_label,
                    status=result.status,
                    rows=result.rows_duckdb,
                    elapsed=result.execution_seconds,
                )

    # Update and write manifest
    manifest_map = {row.get("day_label", ""): row for row in manifest_rows}
    for result in results:
        manifest_map[result.day_label] = {
            "day_label": result.day_label,
            "status": result.status,
            "skip_reason": result.skip_reason,
            "source_csv": str(result.source_csv),
            "source_csv_sha256": result.source_csv_sha256 or "",
            "rows_source": str(result.rows_source),
            "rows_duckdb": str(result.rows_duckdb),
            "run_id": result.run_id,
            "database_path": str(result.database_path),
            "execution_seconds": f"{result.execution_seconds:.3f}",
        }

    _write_manifest(manifest_path, list(manifest_map.values()))

    total_seconds = time.perf_counter() - total_start

    progress.summary(
        processed=processed_count,
        skipped=skipped_count,
        errors=error_count,
        total_seconds=total_seconds,
    )

    return {
        "results": [
            {
                "day_label": r.day_label,
                "status": r.status,
                "skip_reason": r.skip_reason,
                "source_csv": str(r.source_csv),
                "source_csv_sha256": r.source_csv_sha256,
                "rows_source": r.rows_source,
                "rows_duckdb": r.rows_duckdb,
                "run_id": r.run_id,
                "database_path": str(r.database_path),
                "execution_seconds": r.execution_seconds,
                "error_message": r.error_message,
            }
            for r in results
        ],
        "manifest_path": manifest_path,
        "processed_count": processed_count,
        "skipped_count": skipped_count,
        "error_count": error_count,
        "total_execution_seconds": total_seconds,
    }


def export_all_tradingview_fields(
    field_catalog_csv: Path = FIELD_CATALOG_CSV,
    output_dir: Path = OUTPUT_DIR,
    chunk_size: int = CHUNK_SIZE,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> Path:
    field_names = _load_field_names(field_catalog_csv)
    if not field_names:
        raise ValueError(f"No TradingView field names found in {field_catalog_csv}")

    client = ApiTradingViewClient(user_agent=USER_AGENT)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "merge_buffer.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute(
            "CREATE TABLE symbol_data ("
            "  symbol TEXT PRIMARY KEY,"
            "  data TEXT NOT NULL DEFAULT '{}'"
            ")"
        )

        for field_chunk in _chunked(field_names, chunk_size):
            chunk_rows = _fetch_scan_chunk(
                client=client, columns=field_chunk, timeout=timeout
            )
            _merge_chunk_to_db(conn, chunk_rows)

        output_file = _build_output_file_name(output_dir)
        _stream_csv_from_db(conn, field_names, output_file)
        conn.close()

    return output_file


def export_all_tradingview_fields_duckdb(
    field_catalog_csv: Path = FIELD_CATALOG_CSV,
    output_dir: Path = DUCKDB_OUTPUT_DIR,
    chunk_size: int = CHUNK_SIZE,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    database_path: str | Path | None = None,
    parquet_dir: str | Path | None = None,
    run_label: str | None = None,
    reference_time: datetime | None = None,
    export_parquet: bool = True,
    create_indexes: bool = False,
) -> dict[str, Any]:
    field_names = _load_field_names(field_catalog_csv)
    if not field_names:
        raise ValueError(f"No TradingView field names found in {field_catalog_csv}")

    data_field_names = [
        field_name for field_name in field_names if field_name != "symbol"
    ]
    field_chunks = _chunked(field_names, chunk_size)
    created_at_utc = reference_time or datetime.now(tz=timezone.utc)
    if created_at_utc.tzinfo is None:
        created_at_utc = created_at_utc.replace(tzinfo=timezone.utc)
    created_at_utc = created_at_utc.astimezone(timezone.utc)
    run_id = _build_all_fields_duckdb_run_id(run_label, created_at_utc)
    run_id_generated = not bool(_slugify(run_label) if run_label else None)
    storage_layout = _build_all_fields_daily_storage_layout(
        run_id=run_id,
        created_at_utc=created_at_utc,
        output_dir=output_dir,
        database_path=database_path,
        parquet_dir=parquet_dir,
    )
    storage_layout.run_output_dir.mkdir(parents=True, exist_ok=True)
    storage_layout.database_path.parent.mkdir(parents=True, exist_ok=True)
    if export_parquet:
        storage_layout.parquet_dir.mkdir(parents=True, exist_ok=True)

    api_request_metadata = _build_all_fields_api_request_metadata(
        field_names=field_names,
        field_chunks=field_chunks,
        field_catalog_csv=field_catalog_csv,
        timeout=timeout,
        chunk_size=chunk_size,
        created_at_utc=created_at_utc,
    )
    code_version_metadata = _collect_all_fields_code_version_metadata()
    parquet_exports: dict[str, Path] = {}

    client = ApiTradingViewClient(user_agent=USER_AGENT)
    with tempfile.TemporaryDirectory() as tmp_dir:
        merge_db_path = Path(tmp_dir) / "merge_buffer.db"
        conn = sqlite3.connect(str(merge_db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute(
            "CREATE TABLE symbol_data ("
            "  symbol TEXT PRIMARY KEY,"
            "  data TEXT NOT NULL DEFAULT '{}'"
            ")"
        )

        try:
            for field_chunk in field_chunks:
                chunk_rows = _fetch_scan_chunk(
                    client=client,
                    columns=field_chunk,
                    timeout=timeout,
                )
                _merge_chunk_to_db(conn, chunk_rows)

            row_count = _count_merged_rows(conn)

            with _duckdb_daily_writer_lock(storage_layout.database_path):
                with TradingViewAllFieldsDuckDBStore(
                    database_path=storage_layout.database_path,
                    parquet_dir=storage_layout.parquet_dir if export_parquet else None,
                ) as duckdb_store:
                    duckdb_store.begin_transaction()
                    try:
                        duckdb_store.drop_analysis_indexes()
                        duckdb_store.delete_run_data(run_id)
                        duckdb_store.register_run(
                            run_id=run_id,
                            created_at_utc=created_at_utc,
                            suite_name="tradingview_all_fields_export_duckdb",
                            scan_data_count=row_count,
                            profile_names=[],
                            industries=None,
                            min_market_cap_usd=None,
                            max_market_cap_usd=None,
                            include_blind_spot_sections=False,
                            api_request_metadata=api_request_metadata,
                            code_version_metadata=code_version_metadata,
                            run_label=run_label,
                            run_id_generated=run_id_generated,
                            notes=(
                                "Day-level DuckDB/Parquet storage for the raw TradingView "
                                "all-fields export. Multiple intraday runs append to the same "
                                "daily database and remain isolated by run_id."
                            ),
                        )
                        duckdb_store.append_all_fields_rows(
                            run_id=run_id,
                            field_names=data_field_names,
                            rows=_iter_merged_rows_from_db(
                                conn,
                                data_field_names,
                                run_id,
                            ),
                        )
                    except Exception:
                        duckdb_store.rollback()
                        raise
                    else:
                        duckdb_store.commit()

                    if create_indexes or export_parquet:
                        duckdb_store.close()
                        duckdb_store.open()

                    if create_indexes:
                        duckdb_store.create_analysis_indexes()
                    if export_parquet:
                        parquet_exports = duckdb_store.export_tables_to_parquet(
                            run_id=run_id,
                            parquet_dir=storage_layout.parquet_dir,
                        )

                    overview_log = (
                        storage_layout.run_output_dir / "_duckdb_run_overview.log"
                    )
                    _log_all_fields_duckdb_run_overview(
                        overview_log=overview_log,
                        run_id=run_id,
                        storage_period_dir=storage_layout.period_dir,
                        run_output_dir=storage_layout.run_output_dir,
                        database_path=storage_layout.database_path,
                        parquet_dir=(
                            storage_layout.parquet_dir if export_parquet else None
                        ),
                        field_catalog_csv=field_catalog_csv,
                        field_count=len(data_field_names),
                        chunk_count=len(field_chunks),
                        row_count=row_count,
                        parquet_exports=parquet_exports,
                    )
                    duckdb_store.register_report(
                        run_id=run_id,
                        report_key="_duckdb_run_overview",
                        report_type="duckdb_overview_log",
                        file_path=overview_log,
                    )
                    if export_parquet:
                        parquet_exports.update(
                            duckdb_store.export_tables_to_parquet(
                                run_id=run_id,
                                parquet_dir=storage_layout.parquet_dir,
                                table_names=["generated_reports"],
                            )
                        )
        finally:
            conn.close()

    return {
        "_duckdb_database": storage_layout.database_path,
        "_duckdb_parquet_dir": (storage_layout.parquet_dir if export_parquet else None),
        "_duckdb_run_id": run_id,
        "_duckdb_day_label": storage_layout.period_dir.name,
        "_duckdb_day_dir": storage_layout.period_dir,
        "_duckdb_period_dir": storage_layout.period_dir,
        "_duckdb_run_output_dir": storage_layout.run_output_dir,
        "_duckdb_overview_log": (
            storage_layout.run_output_dir / "_duckdb_run_overview.log"
        ),
        "_duckdb_parquet_exports": parquet_exports,
        "_duckdb_run_label": run_label,
        "row_count": row_count,
        "field_count": len(data_field_names),
        "table_name": "all_fields_rows",
    }


if __name__ == "__main__":
    exported_file = export_all_tradingview_fields()
    print(f"TradingView all-fields export written to: {exported_file}")
