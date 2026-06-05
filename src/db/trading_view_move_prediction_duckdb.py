from __future__ import annotations

import csv
import hashlib
import json
import math
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

DUCKDB_INSTALL_HINT = (
    "DuckDB support requires the 'duckdb' package. Install it with "
    "'python -m pip install duckdb' or add it from requirements.txt."
)

ANALYSIS_INDEX_SPECS = [
    (
        "idx_profile_rows_lookup",
        "profile_prediction_rows",
        ["run_id", "profile_name", "symbol"],
    ),
    (
        "idx_profile_horizon_lookup",
        "profile_horizon_scores",
        ["run_id", "profile_name", "horizon_name", "symbol"],
    ),
    (
        "idx_consensus_horizon_lookup",
        "consensus_horizon_scores",
        ["run_id", "horizon_name", "symbol"],
    ),
    (
        "idx_consensus_profile_horizon_lookup",
        "consensus_profile_horizon_scores",
        ["run_id", "profile_name", "horizon_name", "symbol"],
    ),
]


RUN_METADATA_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("created_at_utc", "TIMESTAMP"),
    ("run_date_utc", "DATE"),
    ("run_minute_utc", "TIMESTAMP"),
    ("iso_year", "BIGINT"),
    ("iso_week", "BIGINT"),
    ("suite_name", "VARCHAR"),
    ("scan_data_count", "BIGINT"),
    ("profile_names_json", "VARCHAR"),
    ("profile_config_hashes_json", "VARCHAR"),
    ("industries_json", "VARCHAR"),
    ("min_market_cap_usd", "DOUBLE"),
    ("max_market_cap_usd", "DOUBLE"),
    ("include_blind_spot_sections", "BOOLEAN"),
    ("api_request_json", "VARCHAR"),
    ("api_request_payload_sha256", "VARCHAR"),
    ("api_request_markets_json", "VARCHAR"),
    ("api_request_columns_json", "VARCHAR"),
    ("code_version_json", "VARCHAR"),
    ("git_commit", "VARCHAR"),
    ("git_branch", "VARCHAR"),
    ("git_dirty", "BOOLEAN"),
    ("run_label", "VARCHAR"),
    ("run_id_generated", "BOOLEAN"),
    ("database_path", "VARCHAR"),
    ("parquet_dir", "VARCHAR"),
    ("notes", "VARCHAR"),
]

PROFILE_CONFIG_SNAPSHOTS_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("profile_name", "VARCHAR"),
    ("profile_config_schema_version", "VARCHAR"),
    ("profile_config_hash", "VARCHAR"),
    ("profile_config_json", "VARCHAR"),
    ("captured_at_utc", "TIMESTAMP"),
]

GENERATED_REPORTS_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("report_key", "VARCHAR"),
    ("report_type", "VARCHAR"),
    ("profile_name", "VARCHAR"),
    ("file_path", "VARCHAR"),
    ("created_at_utc", "TIMESTAMP"),
]

PARQUET_EXPORTS_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("table_name", "VARCHAR"),
    ("parquet_path", "VARCHAR"),
    ("row_count", "BIGINT"),
    ("exported_at_utc", "TIMESTAMP"),
]

PROFILE_COMPONENTS_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("profile_name", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("market_cap_basic", "DOUBLE"),
    ("close", "DOUBLE"),
    ("manager_action_signal", "VARCHAR"),
    ("attention", "DOUBLE"),
    ("event", "DOUBLE"),
    ("momentum", "DOUBLE"),
    ("trend", "DOUBLE"),
    ("quality", "DOUBLE"),
    ("valuation", "DOUBLE"),
    ("safety", "DOUBLE"),
    ("scale", "DOUBLE"),
]

PROFILE_HORIZON_SCORES_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("profile_name", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("market_cap_basic", "DOUBLE"),
    ("close", "DOUBLE"),
    ("horizon_name", "VARCHAR"),
    ("score", "DOUBLE"),
    ("direction", "VARCHAR"),
    ("confidence", "DOUBLE"),
    ("coverage", "DOUBLE"),
    ("setup", "VARCHAR"),
    ("risk_adjusted_score", "DOUBLE"),
    ("risk_tier", "VARCHAR"),
    ("manager_action_signal", "VARCHAR"),
]

PROFILE_PERFORMANCE_TRACKING_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("profile_name", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("horizon_name", "VARCHAR"),
    ("performance_field", "VARCHAR"),
    ("performance_label", "VARCHAR"),
    ("score", "DOUBLE"),
    ("performance_value", "DOUBLE"),
    ("direction", "VARCHAR"),
    ("manager_action_signal", "VARCHAR"),
]

CONSENSUS_COMPONENTS_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("market_cap", "DOUBLE"),
    ("manager_action_signal", "VARCHAR"),
    ("attention", "DOUBLE"),
    ("event", "DOUBLE"),
    ("momentum", "DOUBLE"),
    ("trend", "DOUBLE"),
    ("quality", "DOUBLE"),
    ("valuation", "DOUBLE"),
    ("safety", "DOUBLE"),
    ("scale", "DOUBLE"),
]

CONSENSUS_HORIZON_SCORES_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("market_cap", "DOUBLE"),
    ("horizon_name", "VARCHAR"),
    ("score", "DOUBLE"),
    ("direction", "VARCHAR"),
    ("confidence", "DOUBLE"),
    ("agreement_ratio", "DOUBLE"),
    ("opinions", "BIGINT"),
    ("risk_adjusted_score", "DOUBLE"),
    ("risk_tier", "VARCHAR"),
    ("manager_action_signal", "VARCHAR"),
]

CONSENSUS_PROFILE_HORIZON_SCORES_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("market_cap", "DOUBLE"),
    ("profile_name", "VARCHAR"),
    ("horizon_name", "VARCHAR"),
    ("score", "DOUBLE"),
    ("direction", "VARCHAR"),
    ("confidence", "DOUBLE"),
    ("coverage", "DOUBLE"),
    ("setup", "VARCHAR"),
    ("risk_adjusted_score", "DOUBLE"),
    ("risk_tier", "VARCHAR"),
]


_TEXT_COLUMNS_EXACT = {
    "run_id",
    "profile_name",
    "symbol",
    "ticker",
    "name",
    "company",
    "sector",
    "industry",
    "market",
    "exchange",
    "country",
    "type",
    "typespecs",
    "scoring_profile",
    "manager_action_signal",
    "direction",
    "setup",
    "risk_tier",
    "bucket",
    "report_key",
    "report_type",
    "file_path",
    "database_path",
    "parquet_dir",
    "parquet_path",
    "notes",
}

_TEXT_COLUMN_MARKERS = (
    "date",
    "time",
    "direction",
    "setup",
    "risk_tier",
    "signal",
    "json",
    "path",
)


@dataclass(frozen=True)
class DuckDBTableWriteResult:
    table_name: str
    row_count: int


def _import_duckdb() -> Any:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ImportError(DUCKDB_INSTALL_HINT) from exc
    return duckdb


def _is_duckdb_file_lock_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "cannot open file" in message and (
        "being used by another process" in message
        or "already open" in message
        or "conflicting lock" in message
    )


def _build_duckdb_file_lock_message(database_path: Path, exc: BaseException) -> str:
    return (
        f"Cannot open DuckDB database for writing: {database_path}. "
        "Another process already has this .duckdb file open. DuckDB permits one "
        "writer process for a database file, and SQLTools, DBeaver, or a DuckDB "
        "CLI session can keep the lock while it is connected. Disconnect the "
        "DuckDB connection in SQLTools or close the other process, then rerun "
        f"the analysis. Original DuckDB error: {exc}"
    )


def _quote_identifier(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _quote_path_literal(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_json_dump(value).encode("utf-8")).hexdigest()


def _ensure_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _parse_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError:
            return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _is_text_column(column_name: str) -> bool:
    normalized = str(column_name).lower()
    if normalized in _TEXT_COLUMNS_EXACT:
        return True
    return any(marker in normalized for marker in _TEXT_COLUMN_MARKERS)


def _infer_sql_type(column_name: str, values: Sequence[Any]) -> str:
    if _is_text_column(column_name):
        return "VARCHAR"

    non_blank_values = [value for value in values if not _is_blank(value)]
    if not non_blank_values:
        return "VARCHAR"

    if all(_parse_float(value) is not None for value in non_blank_values):
        return "DOUBLE"
    return "VARCHAR"


def _coerce_value(value: Any, sql_type: str) -> Any:
    if _is_blank(value):
        return None

    normalized_type = sql_type.upper()
    if normalized_type in {"DOUBLE", "FLOAT", "REAL"}:
        return _parse_float(value)
    if normalized_type in {"BIGINT", "INTEGER", "INT"}:
        parsed = _parse_float(value)
        return int(parsed) if parsed is not None else None
    if normalized_type == "BOOLEAN":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no"}:
            return False
        return None
    if normalized_type == "TIMESTAMP":
        return value
    if isinstance(value, (dict, list)):
        return _json_dump(value)
    return str(value)


def _dedupe_headers(headers: Sequence[str]) -> list[str]:
    seen: dict[str, int] = {}
    deduped: list[str] = []
    for header in headers:
        column_name = str(header)
        count = seen.get(column_name, 0)
        seen[column_name] = count + 1
        if count:
            column_name = f"{column_name}__{count + 1}"
        deduped.append(column_name)
    return deduped


class MovePredictionDuckDBStore:
    """Write TradingView move-prediction outputs to one DuckDB database file."""

    def __init__(
        self,
        database_path: str | Path,
        parquet_dir: str | Path | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.parquet_dir = Path(parquet_dir) if parquet_dir is not None else None
        self._duckdb: Any | None = None
        self._conn: Any | None = None

    def __enter__(self) -> "MovePredictionDuckDBStore":
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    @property
    def conn(self) -> Any:
        if self._conn is None:
            raise RuntimeError("DuckDB store is not open.")
        return self._conn

    def open(self) -> None:
        if self._conn is not None:
            return

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._duckdb = _import_duckdb()
        try:
            self._conn = self._duckdb.connect(str(self.database_path))
        except Exception as exc:
            if _is_duckdb_file_lock_error(exc):
                raise RuntimeError(
                    _build_duckdb_file_lock_message(self.database_path, exc)
                ) from exc
            raise
        self.conn.execute("PRAGMA threads=4")
        self.conn.execute("PRAGMA enable_object_cache=true")
        try:
            self.conn.execute("SET preserve_insertion_order=false")
        except Exception:
            pass
        self._ensure_base_schema()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _ensure_base_schema(self) -> None:
        self._create_table_if_absent("run_metadata", RUN_METADATA_SCHEMA)
        self._create_table_if_absent(
            "profile_config_snapshots", PROFILE_CONFIG_SNAPSHOTS_SCHEMA
        )
        self._create_table_if_absent("generated_reports", GENERATED_REPORTS_SCHEMA)
        self._create_table_if_absent("parquet_exports", PARQUET_EXPORTS_SCHEMA)

    def _create_table_if_absent(
        self,
        table_name: str,
        schema: Sequence[tuple[str, str]],
    ) -> None:
        columns_sql = ", ".join(
            f"{_quote_identifier(column_name)} {sql_type}"
            for column_name, sql_type in schema
        )
        self.conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_quote_identifier(table_name)} ({columns_sql})"
        )

    def _table_exists(self, table_name: str) -> bool:
        result = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [table_name],
        ).fetchone()
        return bool(result and result[0])

    def _table_schema(self, table_name: str) -> list[tuple[str, str]]:
        if not self._table_exists(table_name):
            return []
        rows = self.conn.execute(
            f"PRAGMA table_info({_quote_identifier(table_name)})"
        ).fetchall()
        return [(str(row[1]), str(row[2]).upper()) for row in rows]

    def _table_columns(self, table_name: str) -> list[str]:
        return [column_name for column_name, _ in self._table_schema(table_name)]

    def register_run(
        self,
        *,
        run_id: str,
        created_at_utc: datetime,
        suite_name: str,
        scan_data_count: int,
        profile_names: Sequence[str],
        industries: Sequence[str] | None,
        min_market_cap_usd: float | None,
        max_market_cap_usd: float | None,
        include_blind_spot_sections: bool,
        profile_config_hashes: dict[str, str] | None = None,
        api_request_metadata: dict[str, Any] | None = None,
        code_version_metadata: dict[str, Any] | None = None,
        run_label: str | None = None,
        run_id_generated: bool = True,
        notes: str = "",
    ) -> None:
        created_at_utc = _ensure_utc_datetime(created_at_utc)
        run_minute_utc = created_at_utc.replace(second=0, microsecond=0)
        iso_calendar = created_at_utc.isocalendar()

        api_request_metadata = dict(api_request_metadata or {})
        api_request_payload = api_request_metadata.get("request_payload")
        if not isinstance(api_request_payload, dict):
            api_request_payload = api_request_metadata.get("payload")
        if not isinstance(api_request_payload, dict):
            api_request_payload = None

        request_metadata = api_request_metadata.get("request_metadata")
        if not isinstance(request_metadata, dict):
            request_metadata = {}

        request_markets = []
        request_columns = []
        if api_request_payload is not None:
            request_markets = list(api_request_payload.get("markets") or [])
            request_columns = list(api_request_payload.get("columns") or [])
        if not request_markets:
            request_markets = list(request_metadata.get("markets") or [])
        if not request_columns:
            request_columns = list(request_metadata.get("columns") or [])

        code_version_metadata = dict(code_version_metadata or {})
        git_metadata = code_version_metadata.get("git")
        if not isinstance(git_metadata, dict):
            git_metadata = {}

        self.append_records(
            "run_metadata",
            [
                {
                    "run_id": run_id,
                    "created_at_utc": created_at_utc,
                    "run_date_utc": created_at_utc.date(),
                    "run_minute_utc": run_minute_utc,
                    "iso_year": iso_calendar.year,
                    "iso_week": iso_calendar.week,
                    "suite_name": suite_name,
                    "scan_data_count": scan_data_count,
                    "profile_names_json": _json_dump(list(profile_names)),
                    "profile_config_hashes_json": _json_dump(
                        dict(profile_config_hashes or {})
                    ),
                    "industries_json": _json_dump(list(industries or [])),
                    "min_market_cap_usd": min_market_cap_usd,
                    "max_market_cap_usd": max_market_cap_usd,
                    "include_blind_spot_sections": include_blind_spot_sections,
                    "api_request_json": _json_dump(api_request_metadata),
                    "api_request_payload_sha256": (
                        _sha256_json(api_request_payload)
                        if api_request_payload is not None
                        else None
                    ),
                    "api_request_markets_json": _json_dump(request_markets),
                    "api_request_columns_json": _json_dump(request_columns),
                    "code_version_json": _json_dump(code_version_metadata),
                    "git_commit": git_metadata.get("commit"),
                    "git_branch": git_metadata.get("branch"),
                    "git_dirty": git_metadata.get("dirty"),
                    "run_label": run_label,
                    "run_id_generated": run_id_generated,
                    "database_path": str(self.database_path),
                    "parquet_dir": str(self.parquet_dir or ""),
                    "notes": notes,
                }
            ],
            RUN_METADATA_SCHEMA,
        )

    def append_profile_config_snapshots(
        self, records: Iterable[dict[str, Any]]
    ) -> None:
        self.append_records(
            "profile_config_snapshots",
            records,
            PROFILE_CONFIG_SNAPSHOTS_SCHEMA,
        )

    def register_report(
        self,
        *,
        run_id: str,
        report_key: str,
        report_type: str,
        file_path: str | Path,
        profile_name: str | None = None,
    ) -> None:
        self.append_records(
            "generated_reports",
            [
                {
                    "run_id": run_id,
                    "report_key": report_key,
                    "report_type": report_type,
                    "profile_name": profile_name,
                    "file_path": str(file_path),
                    "created_at_utc": datetime.now(tz=timezone.utc),
                }
            ],
            GENERATED_REPORTS_SCHEMA,
        )

    def delete_run_data(
        self,
        run_id: str,
        table_names: Sequence[str] | None = None,
    ) -> dict[str, int]:
        deleted_counts: dict[str, int] = {}
        for table_name in table_names or self.list_tables():
            if "run_id" not in self._table_columns(table_name):
                continue
            result = self.conn.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(table_name)} WHERE run_id = ?",
                [run_id],
            ).fetchone()
            row_count = int(result[0]) if result else 0
            if row_count <= 0:
                continue
            self.conn.execute(
                f"DELETE FROM {_quote_identifier(table_name)} WHERE run_id = ?",
                [run_id],
            )
            deleted_counts[table_name] = row_count
        return deleted_counts

    def append_tabular_output(
        self,
        table_name: str,
        headers: Sequence[str],
        rows: Sequence[Sequence[Any]],
        context: dict[str, Any] | None = None,
    ) -> DuckDBTableWriteResult:
        context = dict(context or {})
        deduped_headers = _dedupe_headers(headers)

        for row_index, row in enumerate(rows, start=1):
            if len(row) != len(deduped_headers):
                raise ValueError(
                    f"DuckDB row {row_index} for table {table_name} has "
                    f"{len(row)} values but expected {len(deduped_headers)}."
                )

        value_columns: dict[str, list[Any]] = {
            header: [row[column_index] for row in rows]
            for column_index, header in enumerate(deduped_headers)
        }

        schema: list[tuple[str, str]] = []
        for context_column in context:
            schema.append(
                (
                    context_column,
                    _infer_sql_type(context_column, [context[context_column]]),
                )
            )
        schema.append(("row_number", "BIGINT"))
        schema.extend(
            (header, _infer_sql_type(header, value_columns[header]))
            for header in deduped_headers
        )

        actual_schema = self._ensure_appendable_tabular_schema(table_name, schema)

        if not rows:
            return DuckDBTableWriteResult(table_name=table_name, row_count=0)

        sql_type_by_column = dict(actual_schema)
        insert_columns = [column for column, _ in actual_schema]

        def _iter_record_values() -> Iterable[tuple[Any, ...]]:
            for row_number, row in enumerate(rows, start=1):
                row_values_by_header = dict(zip(deduped_headers, row))
                record_values: list[Any] = []
                for column_name in insert_columns:
                    if column_name in context:
                        raw_value = context[column_name]
                    elif column_name == "row_number":
                        raw_value = row_number
                    else:
                        raw_value = row_values_by_header.get(column_name)
                    record_values.append(
                        _coerce_value(raw_value, sql_type_by_column[column_name])
                    )
                yield tuple(record_values)

        inserted_count = self._copy_rows_from_csv(
            table_name,
            insert_columns,
            _iter_record_values(),
        )

        return DuckDBTableWriteResult(table_name=table_name, row_count=inserted_count)

    def _ensure_appendable_tabular_schema(
        self,
        table_name: str,
        expected_schema: Sequence[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        normalized_expected_schema = [
            (column_name, sql_type.upper()) for column_name, sql_type in expected_schema
        ]
        if not self._table_exists(table_name):
            self._create_table_if_absent(table_name, normalized_expected_schema)
            return normalized_expected_schema

        existing_columns = set(self._table_columns(table_name))
        for column_name, sql_type in normalized_expected_schema:
            if column_name in existing_columns:
                continue
            self.conn.execute(
                f"ALTER TABLE {_quote_identifier(table_name)} "
                f"ADD COLUMN {_quote_identifier(column_name)} {sql_type}"
            )
            existing_columns.add(column_name)

        return self._table_schema(table_name)

    def _validate_table_columns(
        self,
        table_name: str,
        expected_columns: Sequence[str],
    ) -> None:
        actual_columns = self._table_columns(table_name)
        if list(actual_columns) != list(expected_columns):
            raise ValueError(
                f"DuckDB table {table_name} schema mismatch. "
                f"Expected {list(expected_columns)}, found {actual_columns}."
            )

    def _ensure_record_table_schema(
        self,
        table_name: str,
        schema: Sequence[tuple[str, str]],
    ) -> None:
        normalized_schema = [
            (column_name, sql_type.upper()) for column_name, sql_type in schema
        ]
        if not self._table_exists(table_name):
            self._create_table_if_absent(table_name, normalized_schema)
            return

        existing_columns = set(self._table_columns(table_name))
        for column_name, sql_type in normalized_schema:
            if column_name in existing_columns:
                continue
            self.conn.execute(
                f"ALTER TABLE {_quote_identifier(table_name)} "
                f"ADD COLUMN {_quote_identifier(column_name)} {sql_type}"
            )
            existing_columns.add(column_name)

    def begin_transaction(self) -> None:
        self.conn.execute("BEGIN TRANSACTION")

    def commit(self) -> None:
        self.conn.execute("COMMIT")

    def rollback(self) -> None:
        self.conn.execute("ROLLBACK")

    def _copy_rows_from_csv(
        self,
        table_name: str,
        columns: Sequence[str],
        rows: Iterable[Sequence[Any]],
    ) -> int:
        temporary_path: Path | None = None
        row_count = 0
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="",
                prefix=f"{table_name}_",
                suffix=".csv",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                writer = csv.writer(temporary_file)
                for row in rows:
                    writer.writerow(row)
                    row_count += 1

            if row_count <= 0:
                return 0

            columns_sql = ", ".join(_quote_identifier(column) for column in columns)
            self.conn.execute(
                f"COPY {_quote_identifier(table_name)} ({columns_sql}) "
                f"FROM {_quote_path_literal(temporary_path)} "
                "(FORMAT CSV, HEADER false, NULL '')"
            )
            return row_count
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def append_records(
        self,
        table_name: str,
        records: Iterable[dict[str, Any]],
        schema: Sequence[tuple[str, str]],
    ) -> DuckDBTableWriteResult:
        self._ensure_record_table_schema(table_name, schema)

        columns = [column for column, _ in schema]
        sql_type_by_column = dict(schema)

        def _iter_record_values() -> Iterable[tuple[Any, ...]]:
            for record in records:
                yield tuple(
                    _coerce_value(record.get(column), sql_type_by_column[column])
                    for column in columns
                )

        inserted_count = self._copy_rows_from_csv(
            table_name,
            columns,
            _iter_record_values(),
        )

        return DuckDBTableWriteResult(table_name=table_name, row_count=inserted_count)

    def append_profile_components(self, records: Iterable[dict[str, Any]]) -> None:
        self.append_records("profile_components", records, PROFILE_COMPONENTS_SCHEMA)

    def append_profile_horizon_scores(self, records: Iterable[dict[str, Any]]) -> None:
        self.append_records(
            "profile_horizon_scores", records, PROFILE_HORIZON_SCORES_SCHEMA
        )

    def append_profile_performance_tracking(
        self, records: Iterable[dict[str, Any]]
    ) -> None:
        self.append_records(
            "profile_performance_tracking",
            records,
            PROFILE_PERFORMANCE_TRACKING_SCHEMA,
        )

    def append_consensus_components(self, records: Iterable[dict[str, Any]]) -> None:
        self.append_records(
            "consensus_components", records, CONSENSUS_COMPONENTS_SCHEMA
        )

    def append_consensus_horizon_scores(
        self, records: Iterable[dict[str, Any]]
    ) -> None:
        self.append_records(
            "consensus_horizon_scores", records, CONSENSUS_HORIZON_SCORES_SCHEMA
        )

    def append_consensus_profile_horizon_scores(
        self, records: Iterable[dict[str, Any]]
    ) -> None:
        self.append_records(
            "consensus_profile_horizon_scores",
            records,
            CONSENSUS_PROFILE_HORIZON_SCORES_SCHEMA,
        )

    def drop_analysis_indexes(self) -> None:
        for index_name, _, _ in ANALYSIS_INDEX_SPECS:
            self.conn.execute(f"DROP INDEX IF EXISTS {_quote_identifier(index_name)}")

    def create_analysis_indexes(self) -> None:
        for index_name, table_name, columns in ANALYSIS_INDEX_SPECS:
            table_columns = set(self._table_columns(table_name))
            if not table_columns or any(
                column not in table_columns for column in columns
            ):
                continue
            columns_sql = ", ".join(_quote_identifier(column) for column in columns)
            try:
                self.conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {_quote_identifier(index_name)} "
                    f"ON {_quote_identifier(table_name)} ({columns_sql})"
                )
            except Exception:
                continue

        self.conn.execute("ANALYZE")

    def list_tables(self) -> list[str]:
        rows = self.conn.execute("SHOW TABLES").fetchall()
        return sorted(str(row[0]) for row in rows)

    def export_tables_to_parquet(
        self,
        run_id: str,
        parquet_dir: str | Path | None = None,
        table_names: Sequence[str] | None = None,
    ) -> dict[str, Path]:
        resolved_parquet_dir = Path(parquet_dir or self.parquet_dir or "parquet")
        resolved_parquet_dir.mkdir(parents=True, exist_ok=True)
        self.parquet_dir = resolved_parquet_dir

        exports: dict[str, Path] = {}
        tables = list(table_names or self.list_tables())
        for table_name in tables:
            if table_name == "parquet_exports":
                continue
            parquet_path = resolved_parquet_dir / f"{table_name}.parquet"
            self._copy_table_to_parquet(table_name, parquet_path)
            row_count = self._row_count(table_name)
            exports[table_name] = parquet_path
            self.conn.execute(
                "DELETE FROM parquet_exports WHERE run_id = ? AND table_name = ?",
                [run_id, table_name],
            )
            self.append_records(
                "parquet_exports",
                [
                    {
                        "run_id": run_id,
                        "table_name": table_name,
                        "parquet_path": str(parquet_path),
                        "row_count": row_count,
                        "exported_at_utc": datetime.now(tz=timezone.utc),
                    }
                ],
                PARQUET_EXPORTS_SCHEMA,
            )

        parquet_exports_path = resolved_parquet_dir / "parquet_exports.parquet"
        self._copy_table_to_parquet("parquet_exports", parquet_exports_path)
        exports["parquet_exports"] = parquet_exports_path
        return exports

    def _copy_table_to_parquet(self, table_name: str, parquet_path: Path) -> None:
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn.execute(
            f"COPY (SELECT * FROM {_quote_identifier(table_name)}) "
            f"TO {_quote_path_literal(parquet_path)} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )

    def _row_count(self, table_name: str) -> int:
        result = self.conn.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}"
        ).fetchone()
        return int(result[0]) if result else 0


def query_move_prediction_duckdb(
    database_path: str | Path,
    sql: str,
    parameters: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    duckdb = _import_duckdb()
    conn = duckdb.connect(str(database_path), read_only=True)
    try:
        cursor = conn.execute(sql, list(parameters or []))
        if cursor.description is None:
            return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        conn.close()


def describe_move_prediction_duckdb(database_path: str | Path) -> list[dict[str, Any]]:
    return query_move_prediction_duckdb(
        database_path,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main'
        ORDER BY table_name
        """,
    )
