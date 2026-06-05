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
from typing import Any
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
