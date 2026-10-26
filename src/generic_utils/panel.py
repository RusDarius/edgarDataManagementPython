"""Dated DuckDB day files -> one long panel of named columns.

One in-memory DuckDB connection ATTACHes each day file read-only and pulls the
newest run's rows through a caller WHERE clause, so a ~140-day all-fields
history of a few thousand names loads in seconds (``field_history`` walks the
same files row by row in Python and is ~5x slower on the full tape).

No ranking and no cutoffs: the columns, the numeric casts, and the WHERE clause
are the caller's. A column a day file does not carry comes back as NULL.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from generic_utils.series import qident


def _file_columns(con: Any, alias: str, table: str) -> set[str]:
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_catalog = ? AND table_name = ?",
        [alias, table],
    ).fetchall()
    return {str(r[0]) for r in rows}


def _newest_run(con: Any, alias: str, columns: set[str]) -> str | None:
    if "run_id" not in columns:
        return None
    try:
        row = con.execute(
            f"SELECT run_id FROM {alias}.run_metadata ORDER BY created_at_utc DESC NULLS LAST, run_id DESC LIMIT 1"
        ).fetchone()
    except Exception:  # noqa: BLE001 — no run_metadata table: every row is one run
        row = None
    return str(row[0]) if row and row[0] else None


def load_panel(
    sources: Sequence[Mapping[str, Any]],
    *,
    columns: Sequence[str],
    numeric: Iterable[str] = (),
    where_sql: str = "",
    params: Sequence[Any] = (),
    table: str = "all_fields_rows",
    latest_run_only: bool = True,
    skip_without_runs: bool = False,
) -> dict[str, Any]:
    """Rows of ``columns`` from every source, oldest-first, each tagged ``as_of``.

    ``skip_without_runs`` leaves out a day file that has no run_metadata row, the
    way ``series.field_history`` does (by default such a file loads all its rows).

    ``sources`` are ``list_dated_files`` items (``path`` + ``as_of``). ``numeric``
    columns are TRY_CAST to DOUBLE (older day files store some as VARCHAR), and
    ``where_sql`` may reference them by name after that cast. ``params`` bind
    the ``?`` placeholders in ``where_sql`` once per file.
    """
    import duckdb

    numeric_set = set(numeric)
    con = duckdb.connect()
    rows: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    try:
        for index, source in enumerate(sources):
            path = Path(source["path"])
            alias = f"d{index}"
            con.execute(f"ATTACH '{path.as_posix()}' AS {alias} (READ_ONLY)")
            try:
                present = _file_columns(con, alias, table)
                run_id = _newest_run(con, alias, present) if latest_run_only else None
                if skip_without_runs and latest_run_only and run_id is None:
                    snapshots.append({"path": path.as_posix(), "as_of": source.get("as_of"), "run_id": None, "n": 0,
                                      "note": "no run_metadata match"})
                    continue
                select = []
                for col in columns:
                    if col in present:
                        expr = f"TRY_CAST({qident(col)} AS DOUBLE)" if col in numeric_set else qident(col)
                    else:
                        expr = "CAST(NULL AS DOUBLE)" if col in numeric_set else "CAST(NULL AS VARCHAR)"
                    select.append(f"{expr} AS {qident(col)}")
                inner = f"SELECT {', '.join(select)} FROM {alias}.{qident(table)}"
                bind: list[Any] = []
                if run_id:
                    inner += " WHERE run_id = ?"
                    bind.append(run_id)
                sql = f"SELECT * FROM ({inner}) AS t"
                if where_sql:
                    sql += f" WHERE {where_sql}"
                    bind.extend(params)
                cur = con.execute(sql, bind)
                names = [d[0] for d in cur.description]
                as_of = source.get("as_of")
                fetched = cur.fetchall()
                for values in fetched:
                    rec = dict(zip(names, values))
                    rec["as_of"] = as_of
                    rows.append(rec)
                snapshots.append({"path": path.as_posix(), "as_of": as_of, "run_id": run_id, "n": len(fetched),
                                  "missing_columns": [c for c in columns if c not in present]})
            finally:
                con.execute(f"DETACH {alias}")
    finally:
        con.close()
    return {
        "spec": {"table": table, "columns": list(columns), "where": where_sql, "n_sources": len(sources), "n": len(rows)},
        "snapshots": snapshots,
        "rows": rows,
    }


def series_by_id(rows: Iterable[Mapping[str, Any]], *, id_field: str = "symbol", as_of_field: str = "as_of") -> dict[str, list[dict[str, Any]]]:
    """{id: points oldest-first}. One point per (id, as_of): a later duplicate replaces an earlier one."""
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        key = str(row.get(id_field) or "").strip()
        if key:
            grouped[key][str(row.get(as_of_field) or "")] = dict(row)
    return {key: [points[stamp] for stamp in sorted(points)] for key, points in grouped.items()}
