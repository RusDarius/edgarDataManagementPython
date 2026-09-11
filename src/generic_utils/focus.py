"""FOCUS prune: caller filters -> rank -> optional group cap -> optional CSV.

The FOCUS set is generic. Domain screens (unpaid leftover, US $2B, skip
biotech) belong in the calling prompt or in operator_briefing.sleeves —
pass them here as `--where` if you want them, do not bake them in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.predicates import filter_rows
from generic_utils.ranking import group_capped_top_n, top_n
from generic_utils.scan_sources import rows_to_csv


def pack_path_rows(pack: Mapping[str, Any], path: str) -> list[dict[str, Any]]:
    """Walk a dotted pack path (`sleeves.radar_curated_25`, `names`)."""
    current: Any = pack
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            raise KeyError(f"Pack path {path!r} failed at {part!r}")
    if current is None:
        raise KeyError(f"Pack path {path!r} is missing")
    if isinstance(current, Mapping):
        return [dict(v) for v in current.values() if isinstance(v, Mapping)]
    if isinstance(current, list):
        return [dict(row) for row in current if isinstance(row, Mapping)]
    raise TypeError(f"Pack path {path!r} is not a list or object of rows")


def pack_us2b_rows(
    pack: Mapping[str, Any],
    *,
    pack_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """US $2B tape: JSON `us2b_tape` if present, else `briefing_pack.duckdb`.

    Compile keeps the tape in DuckDB (not the JSON). ``SELECT *`` is OK here —
    this is the compact tape table, not ``all_fields_rows``.
    """
    from generic_utils.scan_sources import rows_from_duckdb

    raw = pack.get("us2b_tape")
    if isinstance(raw, list) and raw:
        return [dict(row) for row in raw if isinstance(row, Mapping)]
    path = pack_path or pack.get("_pack_path") or (pack.get("output") or {}).get("json")
    if path:
        database = Path(str(path)).parent / "briefing_pack.duckdb"
        if database.exists():
            try:
                rows = rows_from_duckdb(database, "SELECT * FROM us2b_tape")
                if rows:
                    return rows
            except Exception:
                pass
    names = pack.get("names") or {}
    if isinstance(names, Mapping):
        return [dict(row) for row in names.values() if isinstance(row, Mapping)]
    if isinstance(names, list):
        return [dict(row) for row in names if isinstance(row, Mapping)]
    return []


def build_focus(
    rows: Sequence[Mapping[str, Any]],
    *,
    where: str | Sequence[str] | None = None,
    field: str,
    n: int = 100,
    reverse: bool = True,
    tie_breaker: str | None = None,
    group_field: str | None = None,
    cap: int | None = None,
    exempt: set[str] | None = None,
    id_field: str = "symbol",
) -> dict[str, Any]:
    filtered, clauses = filter_rows(rows, where)
    spec: dict[str, Any] = {
        "source_rows": len(rows),
        "filtered_rows": len(filtered),
        "where": clauses,
        "field": field,
        "top": n,
        "reverse": reverse,
        "tie_breaker": tie_breaker,
        "id_field": id_field,
    }
    if group_field:
        if cap is None:
            raise ValueError("group_field requires cap")
        kept, overflow = group_capped_top_n(
            filtered,
            group_field=group_field,
            cap=cap,
            field=field,
            reverse=reverse,
            n=n,
            exempt=exempt,
            id_field=id_field,
        )
        spec.update(
            {
                "group_field": group_field,
                "cap": cap,
                "exempt_n": len(exempt or ()),
                "overflow_n": len(overflow),
            }
        )
        return {"spec": spec, "rows": kept, "overflow": overflow}
    kept = top_n(
        filtered, field=field, n=n, reverse=reverse, tie_breaker=tie_breaker
    )
    return {"spec": spec, "rows": kept}


def write_focus(payload: Mapping[str, Any], path: str | Path) -> Path:
    rows = list(payload.get("rows") or [])
    return rows_to_csv(path, rows)
