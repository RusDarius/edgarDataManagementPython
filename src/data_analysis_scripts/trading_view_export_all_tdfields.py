from __future__ import annotations

import csv
import json
import sqlite3
import sys
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

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
from generic_utils.log_to_files_util import log_rows_to_csv


USER_AGENT = "Barnnabass daniOO7XbX@gmail.com"
FIELD_CATALOG_CSV = Path(
    r"d:\FinanceProjects\edgarDataManagementPython\savedData\trading_view_stock_fields.csv"
)
OUTPUT_DIR = Path(
    r"d:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis"
)
CHUNK_SIZE = 600
REQUEST_TIMEOUT_SECONDS = 60
SCAN_RANGE_END = 100_000
DB_MERGE_BATCH_SIZE = 500


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


def _serialize_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _build_output_file_name(output_dir: Path) -> Path:
    today_segment = datetime.now().strftime("%d_%m_%Y")
    return output_dir / f"tradingview_global_all_tdfields_{today_segment}.csv"


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


if __name__ == "__main__":
    exported_file = export_all_tradingview_fields()
    print(f"TradingView all-fields export written to: {exported_file}")
