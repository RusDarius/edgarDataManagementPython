from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from db.trading_view_move_prediction_duckdb import (
    DuckDBTableWriteResult,
    MovePredictionDuckDBStore,
    _quote_identifier,
    query_move_prediction_duckdb,
)

ALL_FIELDS_INDEX_SPECS = [
    (
        "idx_all_fields_run_symbol",
        "all_fields_rows",
        ["run_id", "symbol"],
    ),
]

# export_focused_tradingview_fields_duckdb requests ~243 columns vs. ~3.5k for
# export_all_tradingview_fields_duckdb; anything at/above this count is treated
# as a full-catalog run for fallback purposes.
FULL_ALL_FIELDS_MIN_COLUMN_COUNT = 1000


def resolve_latest_and_full_all_fields_run_ids(
    connection: Any,
    *,
    run_metadata_table: str = "run_metadata",
) -> dict[str, str | None]:
    """Resolve the freshest run plus the freshest full-catalog run to fall back to.

    Downstream readers should prefer field values from ``latest_run_id`` (most
    recent scan, which may be a fast focused-catalog refresh) and fall back to
    ``full_run_id`` for any column the latest run didn't fetch (left NULL).
    When no run qualifies as full-catalog (e.g. only focused runs exist so far
    today), ``full_run_id`` equals ``latest_run_id`` and callers should treat
    the fallback as a no-op.

    ``run_metadata_table`` may be a schema/alias-qualified reference (e.g.
    ``"src_0.run_metadata"`` for an ATTACHed database) and is used as-is, not
    quoted as a single identifier.

    Tolerates older/test ``run_metadata`` tables that predate the
    ``api_request_columns_json`` column: in that case every run is treated as
    non-full-catalog and ``full_run_id`` simply mirrors ``latest_run_id``.
    """
    has_columns_json = True
    try:
        described = connection.execute(f"DESCRIBE {run_metadata_table}").fetchall()
        has_columns_json = any(
            str(row[0]) == "api_request_columns_json" for row in described
        )
    except Exception:
        pass

    columns_json_select = (
        "api_request_columns_json" if has_columns_json else "CAST(NULL AS VARCHAR)"
    )
    rows = connection.execute(
        f"SELECT run_id, {columns_json_select} FROM {run_metadata_table} "
        "ORDER BY created_at_utc DESC NULLS LAST, run_id DESC"
    ).fetchall()
    if not rows:
        return {"latest_run_id": None, "full_run_id": None}

    def _column_count(payload: Any) -> int:
        if not payload:
            return 0
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            return 0
        return len(parsed) if isinstance(parsed, list) else 0

    latest_run_id = str(rows[0][0])
    full_run_id: str | None = None
    for run_id, columns_json in rows:
        if _column_count(columns_json) >= FULL_ALL_FIELDS_MIN_COLUMN_COUNT:
            full_run_id = str(run_id)
            break

    return {
        "latest_run_id": latest_run_id,
        "full_run_id": full_run_id or latest_run_id,
    }


class TradingViewAllFieldsDuckDBStore(MovePredictionDuckDBStore):
    """DuckDB writer for the daily TradingView all-fields export."""

    def append_all_fields_rows(
        self,
        *,
        run_id: str,
        field_names: Sequence[str],
        rows: Iterable[dict[str, Any]],
    ) -> DuckDBTableWriteResult:
        schema = [
            ("run_id", "VARCHAR"),
            ("row_number", "BIGINT"),
            ("symbol", "VARCHAR"),
            *((field_name, "VARCHAR") for field_name in field_names),
        ]
        return self.append_records("all_fields_rows", rows, schema)

    def drop_analysis_indexes(self) -> None:
        for index_name, _, _ in ALL_FIELDS_INDEX_SPECS:
            self.conn.execute(f"DROP INDEX IF EXISTS {_quote_identifier(index_name)}")

    def create_analysis_indexes(self) -> None:
        for index_name, table_name, columns in ALL_FIELDS_INDEX_SPECS:
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


def query_tradingview_all_fields_duckdb(
    database_path: str,
    sql: str,
    parameters: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    return query_move_prediction_duckdb(database_path, sql, parameters)
