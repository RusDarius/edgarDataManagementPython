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
