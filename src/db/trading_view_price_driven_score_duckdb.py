from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

from db.trading_view_move_prediction_duckdb import (
    PROFILE_COMPONENTS_SCHEMA,
    DuckDBTableWriteResult,
    MovePredictionDuckDBStore,
    _quote_identifier,
    open_move_prediction_duckdb_connection,
    query_move_prediction_duckdb,
)

PERFORMANCE_COHORT_ROWS_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("market_cap_basic", "DOUBLE"),
    ("performance_lens", "VARCHAR"),
    ("performance_field", "VARCHAR"),
    ("performance_value", "DOUBLE"),
    ("decile", "BIGINT"),
    ("decile_rank", "BIGINT"),
    ("perf_percentile", "DOUBLE"),
    ("premarket_change", "DOUBLE"),
    ("postmarket_change", "DOUBLE"),
    ("gap", "DOUBLE"),
    ("premarket_gap", "DOUBLE"),
]

PRICE_DRIVEN_PROFILE_HORIZON_SCORES_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("profile_name", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("market_cap_basic", "DOUBLE"),
    ("close", "DOUBLE"),
    ("performance_lens", "VARCHAR"),
    ("decile", "BIGINT"),
    ("performance_value", "DOUBLE"),
    ("horizon_name", "VARCHAR"),
    ("score", "DOUBLE"),
    ("direction", "VARCHAR"),
    ("confidence", "DOUBLE"),
    ("coverage", "DOUBLE"),
    ("setup", "VARCHAR"),
    ("risk_adjusted_score", "DOUBLE"),
    ("risk_tier", "VARCHAR"),
    ("manager_action_signal", "VARCHAR"),
    ("is_upward_exemplar", "BOOLEAN"),
]

PRICE_DRIVEN_CONSENSUS_HORIZON_SCORES_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("row_number", "BIGINT"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("market_cap", "DOUBLE"),
    ("performance_lens", "VARCHAR"),
    ("decile", "BIGINT"),
    ("performance_value", "DOUBLE"),
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

OPPORTUNITY_CANDIDATES_SCHEMA = [
    ("run_id", "VARCHAR"),
    ("symbol", "VARCHAR"),
    ("company", "VARCHAR"),
    ("sector", "VARCHAR"),
    ("industry", "VARCHAR"),
    ("market_cap_basic", "DOUBLE"),
    ("performance_lens", "VARCHAR"),
    ("performance_value", "DOUBLE"),
    ("decile", "BIGINT"),
    ("perf_percentile", "DOUBLE"),
    ("opportunity_score", "DOUBLE"),
    ("opportunity_archetype", "VARCHAR"),
    ("primary_horizon", "VARCHAR"),
    ("primary_horizon_score", "DOUBLE"),
    ("primary_horizon_direction", "VARCHAR"),
    ("primary_horizon_ras", "DOUBLE"),
    ("consensus_weeks_score", "DOUBLE"),
    ("consensus_weeks_direction", "VARCHAR"),
    ("bullish_profile_count", "BIGINT"),
    ("long_profile_coverage", "DOUBLE"),
    ("divergence_gap", "DOUBLE"),
    ("exemplar_profiles_json", "VARCHAR"),
    ("has_upward_exemplar", "BOOLEAN"),
    ("max_profile_score", "DOUBLE"),
    ("max_profile_name", "VARCHAR"),
]

PRICE_DRIVEN_INDEX_SPECS = [
    (
        "idx_pds_cohort_lookup",
        "performance_cohort_rows",
        ["run_id", "performance_lens", "symbol"],
    ),
    (
        "idx_pds_profile_horizon_lookup",
        "profile_horizon_scores",
        ["run_id", "profile_name", "performance_lens", "symbol"],
    ),
    (
        "idx_pds_opportunity_lookup",
        "opportunity_candidates",
        ["run_id", "performance_lens", "decile", "symbol"],
    ),
]

STARTER_VIEW_DEFINITIONS = {
    "v_worst_decile_upward_exemplars": """
        SELECT
            phs.run_id,
            phs.performance_lens,
            phs.decile,
            phs.symbol,
            phs.company,
            phs.profile_name,
            phs.horizon_name,
            phs.score,
            phs.direction,
            phs.performance_value,
            phs.is_upward_exemplar,
            phs.risk_adjusted_score,
            phs.setup
        FROM profile_horizon_scores phs
        WHERE phs.is_upward_exemplar = TRUE
          AND phs.decile <= 2
    """,
    "v_opportunity_ranked": """
        SELECT
            oc.*,
            ROW_NUMBER() OVER (
                PARTITION BY oc.run_id, oc.performance_lens
                ORDER BY oc.opportunity_score DESC NULLS LAST,
                    oc.bullish_profile_count DESC,
                    oc.decile ASC
            ) AS opportunity_rank
        FROM opportunity_candidates oc
    """,
    "v_profile_direction_by_decile": """
        SELECT
            run_id,
            performance_lens,
            profile_name,
            decile,
            direction,
            COUNT(*) AS direction_count
        FROM profile_horizon_scores
        WHERE horizon_name = 'weeks'
        GROUP BY run_id, performance_lens, profile_name, decile, direction
    """,
}


class PriceDrivenScoreDuckDBStore(MovePredictionDuckDBStore):
    """Write price-driven score analysis outputs to a weekly DuckDB database."""

    def _ensure_base_schema(self) -> None:
        super()._ensure_base_schema()
        self._create_table_if_absent(
            "performance_cohort_rows", PERFORMANCE_COHORT_ROWS_SCHEMA
        )
        self._create_table_if_absent(
            "profile_horizon_scores", PRICE_DRIVEN_PROFILE_HORIZON_SCORES_SCHEMA
        )
        self._create_table_if_absent("profile_components", PROFILE_COMPONENTS_SCHEMA)
        self._create_table_if_absent(
            "consensus_horizon_scores", PRICE_DRIVEN_CONSENSUS_HORIZON_SCORES_SCHEMA
        )
        self._create_table_if_absent(
            "opportunity_candidates", OPPORTUNITY_CANDIDATES_SCHEMA
        )

    def append_performance_cohort_rows(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records(
            "performance_cohort_rows",
            records,
            PERFORMANCE_COHORT_ROWS_SCHEMA,
        )

    def append_price_driven_profile_horizon_scores(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records(
            "profile_horizon_scores",
            records,
            PRICE_DRIVEN_PROFILE_HORIZON_SCORES_SCHEMA,
        )

    def append_price_driven_consensus_horizon_scores(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records(
            "consensus_horizon_scores",
            records,
            PRICE_DRIVEN_CONSENSUS_HORIZON_SCORES_SCHEMA,
        )

    def append_opportunity_candidates(
        self, records: Iterable[dict[str, Any]]
    ) -> DuckDBTableWriteResult:
        return self.append_records(
            "opportunity_candidates",
            records,
            OPPORTUNITY_CANDIDATES_SCHEMA,
        )

    def drop_analysis_indexes(self) -> None:
        super().drop_analysis_indexes()
        for index_name, _, _ in PRICE_DRIVEN_INDEX_SPECS:
            self.conn.execute(f"DROP INDEX IF EXISTS {_quote_identifier(index_name)}")

    def create_analysis_indexes(self) -> None:
        super().create_analysis_indexes()
        for index_name, table_name, columns in PRICE_DRIVEN_INDEX_SPECS:
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


def query_price_driven_score_duckdb(
    database_path: str | Path,
    sql: str,
    parameters: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    return query_move_prediction_duckdb(database_path, sql, parameters)


__all__ = [
    "OPPORTUNITY_CANDIDATES_SCHEMA",
    "PERFORMANCE_COHORT_ROWS_SCHEMA",
    "PRICE_DRIVEN_CONSENSUS_HORIZON_SCORES_SCHEMA",
    "PRICE_DRIVEN_PROFILE_HORIZON_SCORES_SCHEMA",
    "PriceDrivenScoreDuckDBStore",
    "open_move_prediction_duckdb_connection",
    "query_price_driven_score_duckdb",
]
