"""Thin, generic data-loading glue for the ranking engine (src/generic_utils/ranking.py).

No table/column assumptions and no eligibility filtering -- just "give me
rows as list[dict]" from a CSV or a DuckDB file, so any agent prompt can rank
whatever it just scanned without writing its own loader.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Sequence

from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb


def rows_from_duckdb(
    database_path: str | Path,
    sql: str,
    parameters: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    """Run arbitrary read-only SQL against any DuckDB file, return list[dict].

    Thin re-export of the existing generic query helper -- do not duplicate
    DuckDB connection handling here.
    """
    return query_move_prediction_duckdb(database_path, sql, parameters)


def rows_from_csv(
    path: str | Path, *, encoding: str = "utf-8-sig"
) -> list[dict[str, Any]]:
    """Read any CSV into list[dict] keyed by its header row. No symbol-key assumption."""
    csv_path = Path(path)
    with csv_path.open("r", encoding=encoding, newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def rows_to_csv(
    path: str | Path,
    rows: list[dict[str, Any]],
    *,
    encoding: str = "utf-8-sig",
) -> Path:
    """Write list[dict] to CSV. Header is the union of keys in first-seen order."""
    csv_path = Path(path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with csv_path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return csv_path
