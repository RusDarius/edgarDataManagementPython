"""Optional symbol sources for focused financial-projection runs.

Peer/industry scales still use the full eligible all-fields universe.
These helpers only restrict *which* symbols get year-grid projections.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def normalize_symbols(symbols: Sequence[str] | None) -> list[str]:
    if not symbols:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        symbol = str(raw or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    return out


def parse_symbols_csv(csv_values: str | None) -> list[str]:
    if csv_values is None:
        return []
    return normalize_symbols(part.strip() for part in str(csv_values).split(","))


def load_symbols_from_move_prediction_duckdb(
    database_path: str | Path,
    *,
    run_id: str | None = None,
    table_name: str = "profile_prediction_rows",
    top_n: int | None = None,
    min_score: float | None = None,
) -> list[str]:
    """
    Pull distinct EXCHANGE:TICKER symbols from a move-prediction DuckDB.

    Typical source: ``base_duckdb_result['database_path']`` from
    ``run_full_analysis_suite_duckdb`` (see main.py prediction-analysis block).
    """
    path = Path(database_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Move-prediction database not found: {path.as_posix()}"
        )

    duckdb = _import_duckdb()
    conn = duckdb.connect(str(path), read_only=True)
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        if table_name not in tables:
            raise ValueError(
                f"Table '{table_name}' missing in {path.as_posix()}. "
                f"Available: {sorted(tables)}"
            )

        columns = {
            str(col[0])
            for col in conn.execute(f"DESCRIBE {table_name}").fetchall()
        }

        resolved_run_id = run_id
        if not resolved_run_id and "run_metadata" in tables:
            row = conn.execute(
                """
                SELECT run_id
                FROM run_metadata
                ORDER BY created_at_utc DESC NULLS LAST, run_id DESC
                LIMIT 1
                """
            ).fetchone()
            if row:
                resolved_run_id = str(row[0])

        where_parts = ["COALESCE(TRIM(CAST(symbol AS VARCHAR)), '') <> ''"]
        if resolved_run_id:
            where_parts.append(f"run_id = {_q(resolved_run_id)}")
        if min_score is not None and "score" in columns:
            where_parts.append(f"TRY_CAST(score AS DOUBLE) >= {float(min_score)}")

        limit_sql = f"LIMIT {int(top_n)}" if top_n is not None and top_n > 0 else ""
        if "score" in columns:
            query = f"""
                SELECT symbol
                FROM (
                    SELECT
                        CAST(symbol AS VARCHAR) AS symbol,
                        MAX(TRY_CAST(score AS DOUBLE)) AS best_score
                    FROM {table_name}
                    WHERE {" AND ".join(where_parts)}
                    GROUP BY 1
                )
                ORDER BY best_score DESC NULLS LAST, symbol
                {limit_sql}
            """
        else:
            query = f"""
                SELECT DISTINCT CAST(symbol AS VARCHAR) AS symbol
                FROM {table_name}
                WHERE {" AND ".join(where_parts)}
                ORDER BY symbol
                {limit_sql}
            """
        rows = conn.execute(query).fetchall()
    finally:
        conn.close()

    return normalize_symbols(str(row[0]) for row in rows if row and row[0])


def resolve_symbols_argument(
    *,
    symbols: Sequence[str] | None = None,
    symbols_csv: str | None = None,
    prediction_database: str | Path | None = None,
    prediction_run_id: str | None = None,
    prediction_top_n: int | None = None,
    prediction_min_score: float | None = None,
) -> list[str]:
    """Merge explicit symbols with optional move-prediction DuckDB extraction."""
    resolved = normalize_symbols(symbols)
    resolved.extend(parse_symbols_csv(symbols_csv))
    if prediction_database is not None:
        resolved.extend(
            load_symbols_from_move_prediction_duckdb(
                prediction_database,
                run_id=prediction_run_id,
                top_n=prediction_top_n,
                min_score=prediction_min_score,
            )
        )
    return normalize_symbols(resolved)
