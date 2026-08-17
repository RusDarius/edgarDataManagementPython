from __future__ import annotations

from typing import Any, Iterable, Sequence

from db.trading_view_move_prediction_duckdb import (
    DuckDBTableWriteResult,
    MovePredictionDuckDBStore,
    _quote_identifier,
    query_move_prediction_duckdb,
)

ETF_RANKED_SCORES_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("rank", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("name", "VARCHAR"),
    ("description", "VARCHAR"),
    ("category", "VARCHAR"),
    ("focus", "VARCHAR"),
    ("asset_class", "VARCHAR"),
    ("brand", "VARCHAR"),
    ("country", "VARCHAR"),
    ("market", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("aum", "DOUBLE"),
    ("nav", "DOUBLE"),
    ("expense_ratio", "DOUBLE"),
    ("nav_discount_premium", "DOUBLE"),
    ("close", "DOUBLE"),
    ("relative_volume_10d_calc", "DOUBLE"),
    ("fund_flows_1m", "DOUBLE"),
    ("perf_5d", "DOUBLE"),
    ("perf_1m", "DOUBLE"),
    ("perf_3m", "DOUBLE"),
    ("perf_ytd", "DOUBLE"),
    ("perf_1y", "DOUBLE"),
    ("momentum_near", "DOUBLE"),
    ("momentum_medium", "DOUBLE"),
    ("momentum_long", "DOUBLE"),
    ("trend", "DOUBLE"),
    ("attention", "DOUBLE"),
    ("structure", "DOUBLE"),
    ("composite_score", "DOUBLE"),
    ("direction", "VARCHAR"),
    ("coverage", "DOUBLE"),
]

ETF_CATEGORY_LEADERS_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("category", "VARCHAR"),
    ("category_rank", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("description", "VARCHAR"),
    ("aum", "DOUBLE"),
    ("composite_score", "DOUBLE"),
    ("direction", "VARCHAR"),
    ("focus", "VARCHAR"),
    ("expense_ratio", "DOUBLE"),
]

ETF_BOOK_ROWS_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("ticker", "VARCHAR"),
    ("description", "VARCHAR"),
    ("product_class", "VARCHAR"),
    ("asset_class", "VARCHAR"),
    ("category", "VARCHAR"),
    ("focus", "VARCHAR"),
    ("niche", "VARCHAR"),
    ("peer_group", "VARCHAR"),
    ("peer_level", "VARCHAR"),
    ("sleeve_key", "VARCHAR"),
    ("aum", "DOUBLE"),
    ("expense_ratio", "DOUBLE"),
    ("nav_discount_premium", "DOUBLE"),
    ("perf_5d", "DOUBLE"),
    ("perf_1m", "DOUBLE"),
    ("flow_to_aum_1m", "DOUBLE"),
    ("organic_demand_1m", "DOUBLE"),
    ("tracking_gap_1m", "DOUBLE"),
    ("close_vs_sma50", "DOUBLE"),
    ("pct_from_high_52w", "DOUBLE"),
    ("composite_score", "DOUBLE"),
    ("book_consensus", "DOUBLE"),
    ("book_direction", "VARCHAR"),
    ("sleeve_continuation", "DOUBLE"),
    ("flow_confirmed", "DOUBLE"),
    ("early_rotation", "DOUBLE"),
    ("catch_up", "DOUBLE"),
    ("vehicle_quality", "DOUBLE"),
    ("macro_hedge", "DOUBLE"),
    ("crowded", "DOUBLE"),
    ("dead_product", "DOUBLE"),
]

ETF_REGIME_TAPE_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("sleeve", "VARCHAR"),
    ("question", "VARCHAR"),
    ("symbol", "VARCHAR"),
    ("ticker", "VARCHAR"),
    ("description", "VARCHAR"),
    ("aum", "DOUBLE"),
    ("perf_5d", "DOUBLE"),
    ("perf_1m", "DOUBLE"),
    ("flow_to_aum_1m", "DOUBLE"),
    ("organic_demand_1m", "DOUBLE"),
    ("book_consensus", "DOUBLE"),
    ("product_class", "VARCHAR"),
]

ETF_SLEEVE_HEAT_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("rank", "BIGINT"),
    ("sleeve_key", "VARCHAR"),
    ("row_count", "BIGINT"),
    ("median_perf_5d", "DOUBLE"),
    ("median_perf_1m", "DOUBLE"),
    ("median_flow_to_aum_1m", "DOUBLE"),
    ("median_organic_demand_1m", "DOUBLE"),
    ("median_book_consensus", "DOUBLE"),
    ("best_perf_symbol", "VARCHAR"),
    ("best_perf_1m", "DOUBLE"),
]

ETF_DIVERGENCES_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("description", "VARCHAR"),
    ("peer_group", "VARCHAR"),
    ("flags", "VARCHAR"),
    ("perf_1m", "DOUBLE"),
    ("flow_to_aum_1m", "DOUBLE"),
    ("nav_discount_premium", "DOUBLE"),
    ("book_consensus", "DOUBLE"),
]

ETF_CATCH_UP_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("sleeve_key", "VARCHAR"),
    ("role", "VARCHAR"),
    ("symbol", "VARCHAR"),
    ("description", "VARCHAR"),
    ("perf_1m", "DOUBLE"),
    ("book_consensus", "DOUBLE"),
    ("flow_to_aum_1m", "DOUBLE"),
    ("pct_from_high_52w", "DOUBLE"),
    ("expense_ratio", "DOUBLE"),
]

ETF_VEHICLE_QUALITY_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("sleeve_key", "VARCHAR"),
    ("sleeve_rank", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("description", "VARCHAR"),
    ("brand", "VARCHAR"),
    ("aum", "DOUBLE"),
    ("expense_ratio", "DOUBLE"),
    ("nav_discount_premium", "DOUBLE"),
    ("dollar_liquidity", "DOUBLE"),
    ("vehicle_quality", "DOUBLE"),
]

ETF_LENS_LEADERS_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("lens", "VARCHAR"),
    ("lens_rank", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("description", "VARCHAR"),
    ("sleeve_key", "VARCHAR"),
    ("score", "DOUBLE"),
    ("book_consensus", "DOUBLE"),
    ("perf_1m", "DOUBLE"),
]

ETF_HOLDINGS_OVERLAY_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("overlay_id", "VARCHAR"),
    ("note", "VARCHAR"),
    ("held_tickers", "VARCHAR"),
    ("thermometer_symbol", "VARCHAR"),
    ("thermometer_ticker", "VARCHAR"),
    ("description", "VARCHAR"),
    ("perf_5d", "DOUBLE"),
    ("perf_1m", "DOUBLE"),
    ("flow_to_aum_1m", "DOUBLE"),
    ("book_consensus", "DOUBLE"),
    ("book_direction", "VARCHAR"),
]

ETF_DOD_CHANGES_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("description", "VARCHAR"),
    ("consensus_delta", "DOUBLE"),
    ("flow_to_aum_1m_delta", "DOUBLE"),
    ("perf_1m_delta", "DOUBLE"),
    ("book_consensus", "DOUBLE"),
    ("prior_consensus", "DOUBLE"),
]

ETF_INDEX_SPECS = [
    (
        "idx_etf_ranked_lookup",
        "etf_ranked_scores",
        ["run_id", "symbol"],
    ),
    (
        "idx_etf_category_lookup",
        "etf_category_leaders",
        ["run_id", "category", "symbol"],
    ),
    (
        "idx_etf_book_lookup",
        "etf_book_rows",
        ["run_id", "symbol"],
    ),
    (
        "idx_etf_sleeve_lookup",
        "etf_sleeve_heat",
        ["run_id", "sleeve_key"],
    ),
]


class EtfScanDuckDBStore(MovePredictionDuckDBStore):
    """DuckDB writer for global ETF scan + first-pass analysis."""

    def append_ranked_scores(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records(
            "etf_ranked_scores", records, ETF_RANKED_SCORES_SCHEMA
        )

    def append_category_leaders(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records(
            "etf_category_leaders", records, ETF_CATEGORY_LEADERS_SCHEMA
        )

    def append_book_rows(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records("etf_book_rows", records, ETF_BOOK_ROWS_SCHEMA)

    def append_regime_tape(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records("etf_regime_tape", records, ETF_REGIME_TAPE_SCHEMA)

    def append_sleeve_heat(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records("etf_sleeve_heat", records, ETF_SLEEVE_HEAT_SCHEMA)

    def append_divergences(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records("etf_divergences", records, ETF_DIVERGENCES_SCHEMA)

    def append_catch_up(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records("etf_catch_up", records, ETF_CATCH_UP_SCHEMA)

    def append_vehicle_quality(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records(
            "etf_vehicle_quality", records, ETF_VEHICLE_QUALITY_SCHEMA
        )

    def append_lens_leaders(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records("etf_lens_leaders", records, ETF_LENS_LEADERS_SCHEMA)

    def append_holdings_overlay(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records(
            "etf_holdings_overlay", records, ETF_HOLDINGS_OVERLAY_SCHEMA
        )

    def append_dod_changes(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records("etf_dod_changes", records, ETF_DOD_CHANGES_SCHEMA)

    def fetch_prior_book_rows(
        self, *, suite_name: str, current_run_id: str
    ) -> tuple[str | None, list[dict[str, Any]]]:
        tables = set(self.list_tables())
        if "run_metadata" not in tables:
            return None, []
        prior = self.conn.execute(
            """
            SELECT run_id
            FROM run_metadata
            WHERE suite_name = ? AND run_id != ?
            ORDER BY created_at_utc DESC
            LIMIT 1
            """,
            [suite_name, current_run_id],
        ).fetchone()
        if prior is None:
            return None, []
        prior_run_id = str(prior[0])
        if "etf_book_rows" not in tables:
            return prior_run_id, []
        rows = self.conn.execute(
            """
            SELECT symbol, book_consensus, flow_to_aum_1m, perf_1m
            FROM etf_book_rows
            WHERE run_id = ?
            """,
            [prior_run_id],
        ).fetchall()
        columns = ["symbol", "book_consensus", "flow_to_aum_1m", "perf_1m"]
        return prior_run_id, [dict(zip(columns, row)) for row in rows]

    def drop_analysis_indexes(self) -> None:
        for index_name, _, _ in ETF_INDEX_SPECS:
            self.conn.execute(f"DROP INDEX IF EXISTS {_quote_identifier(index_name)}")

    def create_analysis_indexes(self) -> None:
        for index_name, table_name, columns in ETF_INDEX_SPECS:
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


def query_etf_scan_duckdb(
    database_path: str,
    sql: str,
    parameters: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    return query_move_prediction_duckdb(database_path, sql, parameters)
