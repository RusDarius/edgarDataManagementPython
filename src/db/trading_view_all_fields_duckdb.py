from __future__ import annotations

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
