from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

from db.trading_view_move_prediction_duckdb import (
    DuckDBTableWriteResult,
    MovePredictionDuckDBStore,
    _quote_identifier,
    open_move_prediction_duckdb_connection,
    query_move_prediction_duckdb,
)

BACKWARDS_ANALYSIS_RUNS_SCHEMA = [
    ("backwards_analysis_id", "VARCHAR"),
    ("created_at_utc", "TIMESTAMP"),
    ("current_run_id", "VARCHAR"),
    ("current_created_at_utc", "TIMESTAMP"),
    ("current_database_path", "VARCHAR"),
    ("current_snapshot_label", "VARCHAR"),
    ("duckdb_runs_root", "VARCHAR"),
    ("anchor_count", "BIGINT"),
    ("notes", "VARCHAR"),
]

BACKWARDS_ANALYSIS_ANCHORS_SCHEMA = [
    ("backwards_analysis_id", "VARCHAR"),
    ("anchor_name", "VARCHAR"),
    ("anchor_run_id", "VARCHAR"),
    ("anchor_created_at_utc", "TIMESTAMP"),
    ("anchor_snapshot_label", "VARCHAR"),
    ("source_database_path", "VARCHAR"),
    ("resolution_method", "VARCHAR"),
    ("resolution_spec_json", "VARCHAR"),
    ("offset_days_from_current", "DOUBLE"),
    ("runs_back_from_current", "BIGINT"),
]

BACKWARDS_PROFILE_HORIZON_DELTAS_SCHEMA = [
    ("backwards_analysis_id", "VARCHAR"),
    ("anchor_name", "VARCHAR"),
    ("profile_name", "VARCHAR"),
    ("profile_family", "VARCHAR"),
    ("anchor_profile_name", "VARCHAR"),
    ("profile_version_exact_match", "BOOLEAN"),
    ("horizon_name", "VARCHAR"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("market_cap_basic", "DOUBLE"),
    ("in_current", "BOOLEAN"),
    ("in_anchor", "BOOLEAN"),
    ("current_score", "DOUBLE"),
    ("current_direction", "VARCHAR"),
    ("current_risk_adjusted_score", "DOUBLE"),
    ("current_confidence", "DOUBLE"),
    ("current_rank", "BIGINT"),
    ("current_close", "DOUBLE"),
    ("anchor_score", "DOUBLE"),
    ("anchor_direction", "VARCHAR"),
    ("anchor_risk_adjusted_score", "DOUBLE"),
    ("anchor_confidence", "DOUBLE"),
    ("anchor_rank", "BIGINT"),
    ("anchor_close", "DOUBLE"),
    ("score_delta", "DOUBLE"),
    ("rank_delta", "BIGINT"),
    ("close_delta_pct", "DOUBLE"),
    ("direction_changed", "BOOLEAN"),
    ("current_perf_5d", "DOUBLE"),
    ("anchor_perf_5d", "DOUBLE"),
    ("perf_5d_delta", "DOUBLE"),
    ("current_perf_w", "DOUBLE"),
    ("anchor_perf_w", "DOUBLE"),
    ("perf_w_delta", "DOUBLE"),
    ("current_perf_1m", "DOUBLE"),
    ("anchor_perf_1m", "DOUBLE"),
    ("perf_1m_delta", "DOUBLE"),
    ("current_perf_ytd", "DOUBLE"),
    ("anchor_perf_ytd", "DOUBLE"),
    ("perf_ytd_delta", "DOUBLE"),
]

BACKWARDS_CONSENSUS_HORIZON_DELTAS_SCHEMA = [
    ("backwards_analysis_id", "VARCHAR"),
    ("anchor_name", "VARCHAR"),
    ("horizon_name", "VARCHAR"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("market_cap", "DOUBLE"),
    ("in_current", "BOOLEAN"),
    ("in_anchor", "BOOLEAN"),
    ("current_score", "DOUBLE"),
    ("current_direction", "VARCHAR"),
    ("current_risk_adjusted_score", "DOUBLE"),
    ("current_confidence", "DOUBLE"),
    ("current_rank", "BIGINT"),
    ("current_close", "DOUBLE"),
    ("anchor_score", "DOUBLE"),
    ("anchor_direction", "VARCHAR"),
    ("anchor_risk_adjusted_score", "DOUBLE"),
    ("anchor_confidence", "DOUBLE"),
    ("anchor_rank", "BIGINT"),
    ("anchor_close", "DOUBLE"),
    ("score_delta", "DOUBLE"),
    ("rank_delta", "BIGINT"),
    ("close_delta_pct", "DOUBLE"),
    ("direction_changed", "BOOLEAN"),
    ("current_perf_5d", "DOUBLE"),
    ("anchor_perf_5d", "DOUBLE"),
    ("perf_5d_delta", "DOUBLE"),
    ("current_perf_w", "DOUBLE"),
    ("anchor_perf_w", "DOUBLE"),
    ("perf_w_delta", "DOUBLE"),
    ("current_perf_1m", "DOUBLE"),
    ("anchor_perf_1m", "DOUBLE"),
    ("perf_1m_delta", "DOUBLE"),
    ("current_perf_ytd", "DOUBLE"),
    ("anchor_perf_ytd", "DOUBLE"),
    ("perf_ytd_delta", "DOUBLE"),
]

BACKWARDS_PROFILE_COMPONENT_DELTAS_SCHEMA = [
    ("backwards_analysis_id", "VARCHAR"),
    ("anchor_name", "VARCHAR"),
    ("profile_name", "VARCHAR"),
    ("profile_family", "VARCHAR"),
    ("anchor_profile_name", "VARCHAR"),
    ("profile_version_exact_match", "BOOLEAN"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("in_current", "BOOLEAN"),
    ("in_anchor", "BOOLEAN"),
    ("current_attention", "DOUBLE"),
    ("anchor_attention", "DOUBLE"),
    ("attention_delta", "DOUBLE"),
    ("current_event", "DOUBLE"),
    ("anchor_event", "DOUBLE"),
    ("event_delta", "DOUBLE"),
    ("current_momentum", "DOUBLE"),
    ("anchor_momentum", "DOUBLE"),
    ("momentum_delta", "DOUBLE"),
    ("current_trend", "DOUBLE"),
    ("anchor_trend", "DOUBLE"),
    ("trend_delta", "DOUBLE"),
    ("current_quality", "DOUBLE"),
    ("anchor_quality", "DOUBLE"),
    ("quality_delta", "DOUBLE"),
    ("current_valuation", "DOUBLE"),
    ("anchor_valuation", "DOUBLE"),
    ("valuation_delta", "DOUBLE"),
    ("current_safety", "DOUBLE"),
    ("anchor_safety", "DOUBLE"),
    ("safety_delta", "DOUBLE"),
    ("current_scale", "DOUBLE"),
    ("anchor_scale", "DOUBLE"),
    ("scale_delta", "DOUBLE"),
    ("current_close", "DOUBLE"),
    ("anchor_close", "DOUBLE"),
    ("close_delta_pct", "DOUBLE"),
]

BACKWARDS_ANCHOR_SNAPSHOTS_SCHEMA = [
    ("backwards_analysis_id", "VARCHAR"),
    ("anchor_name", "VARCHAR"),
    ("is_current", "BOOLEAN"),
    ("run_id", "VARCHAR"),
    ("snapshot_label", "VARCHAR"),
    ("profile_name", "VARCHAR"),
    ("profile_family", "VARCHAR"),
    ("anchor_profile_name", "VARCHAR"),
    ("horizon_name", "VARCHAR"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("market_cap_basic", "DOUBLE"),
    ("score", "DOUBLE"),
    ("direction", "VARCHAR"),
    ("confidence", "DOUBLE"),
    ("risk_adjusted_score", "DOUBLE"),
    ("risk_tier", "VARCHAR"),
    ("close", "DOUBLE"),
    ("perf_5d", "DOUBLE"),
    ("perf_w", "DOUBLE"),
    ("perf_1m", "DOUBLE"),
    ("perf_ytd", "DOUBLE"),
    ("profile_rank", "BIGINT"),
]

BACKWARDS_INDEX_SPECS = [
    (
        "idx_backwards_profile_horizon_deltas_lookup",
        "backwards_profile_horizon_deltas",
        ["backwards_analysis_id", "anchor_name", "profile_name", "horizon_name"],
    ),
    (
        "idx_backwards_consensus_horizon_deltas_lookup",
        "backwards_consensus_horizon_deltas",
        ["backwards_analysis_id", "anchor_name", "horizon_name"],
    ),
    (
        "idx_backwards_anchor_snapshots_lookup",
        "backwards_anchor_snapshots",
        ["backwards_analysis_id", "anchor_name", "symbol"],
    ),
]

STARTER_VIEW_DEFINITIONS = {
    "vw_backwards_input_runs": """
        SELECT
            runs.backwards_analysis_id,
            runs.current_run_id,
            runs.current_created_at_utc,
            runs.current_snapshot_label,
            anchors.anchor_name,
            anchors.anchor_run_id,
            anchors.anchor_created_at_utc,
            anchors.anchor_snapshot_label,
            anchors.resolution_method,
            anchors.offset_days_from_current,
            anchors.runs_back_from_current
        FROM backwards_analysis_runs AS runs
        LEFT JOIN backwards_analysis_anchors AS anchors
            ON runs.backwards_analysis_id = anchors.backwards_analysis_id
    """,
    "vw_backwards_top_score_movers": """
        SELECT
            deltas.*,
            ROW_NUMBER() OVER (
                PARTITION BY deltas.backwards_analysis_id,
                    deltas.anchor_name,
                    deltas.profile_name,
                    deltas.horizon_name
                ORDER BY ABS(deltas.score_delta) DESC NULLS LAST,
                    deltas.symbol ASC
            ) AS mover_rank
        FROM backwards_profile_horizon_deltas AS deltas
        WHERE deltas.score_delta IS NOT NULL
    """,
    "vw_backwards_price_score_alignment": """
        SELECT
            backwards_analysis_id,
            anchor_name,
            profile_name,
            horizon_name,
            COUNT(*) AS symbol_count,
            AVG(score_delta) AS avg_score_delta,
            AVG(close_delta_pct) AS avg_close_delta_pct,
            CORR(score_delta, close_delta_pct) AS score_price_corr,
            SUM(
                CASE
                    WHEN score_delta IS NULL OR close_delta_pct IS NULL THEN 0
                    WHEN score_delta > 0 AND close_delta_pct > 0 THEN 1
                    WHEN score_delta < 0 AND close_delta_pct < 0 THEN 1
                    ELSE 0
                END
            ) AS aligned_direction_count,
            SUM(
                CASE
                    WHEN score_delta IS NULL OR close_delta_pct IS NULL THEN 0
                    WHEN score_delta > 0 AND close_delta_pct < 0 THEN 1
                    ELSE 0
                END
            ) AS false_positive_count
        FROM backwards_profile_horizon_deltas
        GROUP BY backwards_analysis_id, anchor_name, profile_name, horizon_name
    """,
    "vw_backwards_universe_drift": """
        SELECT
            backwards_analysis_id,
            anchor_name,
            profile_name,
            horizon_name,
            SUM(CASE WHEN in_current AND NOT in_anchor THEN 1 ELSE 0 END) AS symbols_added,
            SUM(CASE WHEN in_anchor AND NOT in_current THEN 1 ELSE 0 END) AS symbols_dropped,
            SUM(CASE WHEN in_current AND in_anchor THEN 1 ELSE 0 END) AS symbols_retained
        FROM backwards_profile_horizon_deltas
        GROUP BY backwards_analysis_id, anchor_name, profile_name, horizon_name
    """,
}


class BackwardsPredictionDuckDBStore(MovePredictionDuckDBStore):
    """Write backwards prediction score-evolution outputs to DuckDB."""

    def _ensure_base_schema(self) -> None:
        self._create_table_if_absent(
            "backwards_analysis_runs", BACKWARDS_ANALYSIS_RUNS_SCHEMA
        )
        self._create_table_if_absent(
            "backwards_analysis_anchors", BACKWARDS_ANALYSIS_ANCHORS_SCHEMA
        )
        self._create_table_if_absent(
            "backwards_profile_horizon_deltas", BACKWARDS_PROFILE_HORIZON_DELTAS_SCHEMA
        )
        self._create_table_if_absent(
            "backwards_consensus_horizon_deltas",
            BACKWARDS_CONSENSUS_HORIZON_DELTAS_SCHEMA,
        )
        self._create_table_if_absent(
            "backwards_profile_component_deltas",
            BACKWARDS_PROFILE_COMPONENT_DELTAS_SCHEMA,
        )
        self._create_table_if_absent(
            "backwards_anchor_snapshots", BACKWARDS_ANCHOR_SNAPSHOTS_SCHEMA
        )

    def append_backwards_analysis_run(
        self, record: dict[str, Any]
    ) -> DuckDBTableWriteResult:
        return self._insert_single_record(
            "backwards_analysis_runs",
            record,
            BACKWARDS_ANALYSIS_RUNS_SCHEMA,
        )

    def append_backwards_analysis_anchors(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self._insert_records_direct(
            "backwards_analysis_anchors",
            records,
            BACKWARDS_ANALYSIS_ANCHORS_SCHEMA,
        )

    def _insert_single_record(
        self,
        table_name: str,
        record: dict[str, Any],
        schema: Sequence[tuple[str, str]],
    ) -> DuckDBTableWriteResult:
        result = self._insert_records_direct(table_name, [record], schema)
        return DuckDBTableWriteResult(
            table_name=table_name,
            row_count=result.row_count,
        )

    def _insert_records_direct(
        self,
        table_name: str,
        records: Iterable[dict[str, Any]],
        schema: Sequence[tuple[str, str]],
    ) -> DuckDBTableWriteResult:
        from db.trading_view_move_prediction_duckdb import _coerce_value

        self._ensure_record_table_schema(table_name, schema)
        columns = [column for column, _ in schema]
        sql_type_by_column = dict(schema)
        placeholders = ", ".join("?" for _ in columns)
        columns_sql = ", ".join(_quote_identifier(column) for column in columns)
        insert_sql = (
            f"INSERT INTO {_quote_identifier(table_name)} ({columns_sql}) "
            f"VALUES ({placeholders})"
        )
        row_count = 0
        for record in records:
            values = [
                _coerce_value(record.get(column), sql_type_by_column[column])
                for column in columns
            ]
            self.conn.execute(insert_sql, values)
            row_count += 1
        return DuckDBTableWriteResult(table_name=table_name, row_count=row_count)

    def create_analysis_indexes(self) -> None:
        for index_name, table_name, columns in BACKWARDS_INDEX_SPECS:
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

    def create_starter_views(self) -> list[str]:
        created_views: list[str] = []
        for view_name, view_sql in STARTER_VIEW_DEFINITIONS.items():
            self.conn.execute(f"DROP VIEW IF EXISTS {_quote_identifier(view_name)}")
            self.conn.execute(
                f"CREATE VIEW {_quote_identifier(view_name)} AS {view_sql.strip()}"
            )
            created_views.append(view_name)
        return created_views


def query_backwards_prediction_duckdb(
    database_path: str | Path,
    sql: str,
    parameters: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    return query_move_prediction_duckdb(database_path, sql, parameters)


__all__ = [
    "BACKWARDS_ANALYSIS_ANCHORS_SCHEMA",
    "BACKWARDS_ANALYSIS_RUNS_SCHEMA",
    "BACKWARDS_ANCHOR_SNAPSHOTS_SCHEMA",
    "BACKWARDS_CONSENSUS_HORIZON_DELTAS_SCHEMA",
    "BACKWARDS_PROFILE_COMPONENT_DELTAS_SCHEMA",
    "BACKWARDS_PROFILE_HORIZON_DELTAS_SCHEMA",
    "BackwardsPredictionDuckDBStore",
    "open_move_prediction_duckdb_connection",
    "query_backwards_prediction_duckdb",
]
