"""Generic, zero-cutoff ranking/scanning primitives for any agent prompt.

Every function here is pure (no I/O, no fixed thresholds). The caller always
supplies the field name(s), direction, top-N, and any group cap. This module
does not decide what counts as "good" (no RSI/leftover/mcap-style filters) --
that judgment belongs to whichever domain-specific prompt or package (e.g.
operator_briefing) calls it.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence


def to_float(value: Any) -> float | None:
    """Best-effort numeric coercion; None/blank/non-numeric -> None."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sort_key(
    field: str, *, reverse: bool, tie_breaker: str | None = None
) -> Callable[[Mapping[str, Any]], tuple]:
    def key(row: Mapping[str, Any]) -> tuple:
        value = to_float(row.get(field))
        missing = value is None
        primary = value if value is not None else 0.0
        # Missing values always sort last regardless of direction.
        rank = 1 if missing else 0
        if reverse:
            primary = -primary
        secondary = 0.0
        if tie_breaker is not None:
            tb = to_float(row.get(tie_breaker))
            secondary = -tb if (tb is not None and reverse) else (tb or 0.0)
        return (rank, primary, secondary)

    return key


def top_n(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
    n: int,
    reverse: bool = True,
    tie_breaker: str | None = None,
) -> list[Mapping[str, Any]]:
    """Sort `rows` by `field` (numeric coercion, missing sorts last) and take top n.

    `reverse=True` (default) means highest-first. No eligibility filtering.
    `tie_breaker`, if given, breaks ties in the same direction as `field`.
    """
    ranked = sorted(
        rows, key=_sort_key(field, reverse=reverse, tie_breaker=tie_breaker)
    )
    return list(ranked[:n])


def top_n_by(
    rows: Sequence[Mapping[str, Any]],
    *,
    key_fn: Callable[[Mapping[str, Any]], Any],
    n: int,
    reverse: bool = True,
) -> list[Mapping[str, Any]]:
    """Same as `top_n` but the caller supplies an arbitrary key function.

    Use this for derived/composite metrics that are not a single raw field.
    """
    ranked = sorted(rows, key=key_fn, reverse=reverse)
    return list(ranked[:n])


def rank_by_weights(
    rows: Sequence[Mapping[str, Any]],
    *,
    weights: Mapping[str, float],
    n: int,
    reverse: bool = True,
) -> list[dict[str, Any]]:
    """Transparent weighted composite: score = sum(weight * field value).

    Weights are entirely caller-supplied (no default weighting scheme). The
    computed score is attached to each returned row as `_composite_score` so
    it is visible, not a hidden fake 0-100 rank.
    """
    scored: list[dict[str, Any]] = []
    for row in rows:
        total = 0.0
        for field, weight in weights.items():
            value = to_float(row.get(field))
            if value is not None:
                total += weight * value
        out = dict(row)
        out["_composite_score"] = round(total, 6)
        scored.append(out)
    scored.sort(key=lambda r: r["_composite_score"], reverse=reverse)
    return scored[:n]


def group_capped_top_n(
    rows: Sequence[Mapping[str, Any]],
    *,
    group_field: str,
    cap: int,
    field: str | None = None,
    key_fn: Callable[[Mapping[str, Any]], Any] | None = None,
    reverse: bool = True,
    n: int | None = None,
    exempt: set[str] | None = None,
    id_field: str = "symbol",
    overflow_fields: Sequence[str] = (),
) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
    """Cap a ranked list at `cap` rows per distinct `group_field` value.

    Generalized version of a "no more than K per industry" filter: the group
    field, cap, and exempt id set are all caller-supplied -- there is no
    default cap and no assumption the group is an industry. Pass exactly one
    of `field` (sorts by that field first) or `key_fn` (pre-sorted input is
    also fine if both are omitted). Returns (kept, overflow); overflow rows
    are never silently dropped. `overflow_fields` copies extra keys onto
    each overflow record (e.g. leftover for a crowded-industry cut).
    """
    if field is not None:
        ordered = top_n(rows, field=field, n=len(rows), reverse=reverse)
    elif key_fn is not None:
        ordered = top_n_by(rows, key_fn=key_fn, n=len(rows), reverse=reverse)
    else:
        ordered = list(rows)

    exempt = exempt or set()
    counts: dict[str, int] = {}
    kept: list[Mapping[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    for row in ordered:
        group_value = str(row.get(group_field) or "")
        row_id = str(row.get(id_field) or "")
        if row_id in exempt or not group_value:
            kept.append(row)
            continue
        count = counts.get(group_value, 0)
        if count >= cap:
            extra = {key: row.get(key) for key in overflow_fields}
            overflow.append({id_field: row_id, group_field: group_value, **extra})
            continue
        counts[group_value] = count + 1
        kept.append(row)
    if n is not None:
        kept = kept[:n]
    return kept, overflow


def top_upside(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str = "left",
    n: int = 100,
) -> list[Mapping[str, Any]]:
    """Preset: top-N by an upside/leftover-style field. No eligibility filter."""
    return top_n(rows, field=field, n=n, reverse=True)


def top_movers(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str = "d5",
    n: int = 100,
    reverse: bool = True,
) -> list[Mapping[str, Any]]:
    """Preset: top-N by a change/performance field. `reverse=False` for laggards."""
    return top_n(rows, field=field, n=n, reverse=reverse)


def top_rankers(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
    n: int = 100,
    reverse: bool = False,
) -> list[Mapping[str, Any]]:
    """Preset: top-N by an existing rank/score column.

    Defaults to ascending (ties with "lower rank number is better" columns
    like rank_overall/focus_rank); pass reverse=True for "higher is better"
    score columns.
    """
    return top_n(rows, field=field, n=n, reverse=reverse)
