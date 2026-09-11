"""Generic, zero-cutoff ranking/scanning primitives for any agent prompt.

Every function here is pure (no I/O, no fixed thresholds). The caller always
supplies the field name(s), direction, top-N, and any group cap. ``cover_rows``
is a size helper (optional sort, then take N) — not an eligibility cutoff.
This module does not decide what counts as "good" (no RSI/leftover/mcap-style
filters) -- that judgment belongs to whichever domain-specific prompt or package
(e.g. operator_briefing) calls it.

Layer map (move prediction, edge, leftover, Build-50):
src/generic_utils/specs/scoring_layer_order.md.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Sequence

_ID_TWO_DIGIT_SUFFIX = re.compile(r"^([A-Z]{1,5})\d{2}$")


def ticker_of(symbol: Any) -> str:
    """Bare ticker from `EXCHANGE:TICKER` or a bare id."""
    return str(symbol or "").split(":")[-1].upper()


def drop_duplicate_id_suffixes(
    rows: Sequence[Mapping[str, Any]],
    *,
    id_field: str = "symbol",
) -> list[Mapping[str, Any]]:
    """Drop `TICKER23` / `TICKER03` rows when the unsuffixed ticker is also present.

    Identity hygiene for warrant/unit listing leaks (e.g. NVTS23 next to NVTS).
    Not an eligibility cutoff: a suffixed id is kept when the stem is absent.
    """
    tickers = {ticker_of(row.get(id_field)) for row in rows}
    kept: list[Mapping[str, Any]] = []
    for row in rows:
        ticker = ticker_of(row.get(id_field))
        match = _ID_TWO_DIGIT_SUFFIX.match(ticker)
        if match and match.group(1) in tickers:
            continue
        kept.append(row)
    return kept


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


def cover_rows(
    rows: Sequence[Mapping[str, Any]],
    n: int | None,
    *,
    field: str | None = None,
    reverse: bool = True,
    tie_breaker: str | None = None,
) -> list[Mapping[str, Any]]:
    """Hard size limit. Not an eligibility cutoff and not a group cap.

    ``n is None`` or ``n <= 0`` returns every row. With ``field``, sort first
    (same missing-last rule as ``top_n``). Without ``field``, keep input order.
    """
    materialised = [dict(row) for row in rows]
    if not materialised:
        return []
    if field:
        ranked = top_n(
            materialised,
            field=field,
            n=len(materialised),
            reverse=reverse,
            tie_breaker=tie_breaker,
        )
        materialised = [dict(row) for row in ranked]
    if n is None or n <= 0:
        return materialised
    return materialised[: int(n)]


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


def _slim_row(
    row: Mapping[str, Any], keys: Sequence[str]
) -> dict[str, Any]:
    return {key: row.get(key) for key in keys}


def split_cover(n: int, up_share: float = 0.75) -> tuple[int, int]:
    """Split a per-horizon cover into leader / laggard sizes.

    Default when no size is passed: 50 names, 75% up / 25% down -> 38 / 12.
    Operator recipes pin n_leaders/n_laggards instead (movers_day = 25/25).
    Leaders take `round(n * up_share)`; laggards take the remainder. Each
    tail is at least 1 when `n >= 2`.
    """
    if n < 1:
        raise ValueError("cover n must be >= 1")
    if not 0 < up_share < 1:
        raise ValueError("up_share must be between 0 and 1 (exclusive)")
    n_leaders = int(round(n * up_share))
    n_laggards = n - n_leaders
    if n >= 2:
        if n_leaders < 1:
            n_leaders = 1
            n_laggards = n - 1
        if n_laggards < 1:
            n_laggards = 1
            n_leaders = n - 1
    return n_leaders, n_laggards


def resolve_movers_tail_n(
    *,
    n: int | None = None,
    cover: int | None = None,
    up_share: float | None = None,
    n_leaders: int | None = None,
    n_laggards: int | None = None,
    default_cover: int = 50,
    default_up_share: float = 0.75,
) -> tuple[int, int]:
    """Resolve (n_leaders, n_laggards) for a movers scan.

    Explicit `n_leaders`/`n_laggards` win. `cover` (optionally with
    `up_share`, default 0.75) is the per-horizon total. Legacy `n` alone
    is the per-tail size (both tails equal). `n` plus `up_share` treats
    `n` as cover. With no sizes, default cover is 50 at 75/25.
    """
    if n_leaders is not None or n_laggards is not None:
        if n_leaders is None or n_laggards is None:
            raise ValueError("n_leaders and n_laggards must be passed together")
        if n_leaders < 1 or n_laggards < 1:
            raise ValueError("n_leaders and n_laggards must be >= 1")
        return int(n_leaders), int(n_laggards)
    if cover is not None:
        share = default_up_share if up_share is None else float(up_share)
        return split_cover(int(cover), share)
    if n is not None:
        if up_share is not None:
            return split_cover(int(n), float(up_share))
        return int(n), int(n)
    share = default_up_share if up_share is None else float(up_share)
    return split_cover(default_cover, share)


def movers_size_kwargs_from_recipe(recipe: Mapping[str, Any] | None) -> dict[str, Any]:
    """Map a movers JSON recipe onto horizon_movers size kwargs."""
    rec = dict(recipe or {})
    if rec.get("n_leaders") is not None or rec.get("n_laggards") is not None:
        return {
            "n_leaders": int(rec["n_leaders"]),
            "n_laggards": int(rec["n_laggards"]),
        }
    if rec.get("cover") is not None or rec.get("up_share") is not None:
        cover = rec.get("cover")
        if cover is None:
            cover = rec.get("top") or 50
        share = rec.get("up_share")
        return {
            "cover": int(cover),
            "up_share": float(share) if share is not None else 0.75,
        }
    if rec.get("top") is not None:
        return {"n": int(rec["top"])}
    return {"cover": 50, "up_share": 0.75}


def both_tails(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
    n: int | None = None,
    n_leaders: int | None = None,
    n_laggards: int | None = None,
    cover: int | None = None,
    up_share: float | None = None,
    group_field: str | None = None,
    cap: int | None = None,
    id_field: str = "symbol",
    keep: Sequence[str] = (),
) -> dict[str, Any]:
    """Leaders and laggards for one numeric field. No eligibility cutoff.

    Optional industry-style cap applies independently to each tail.
    `keep` extra keys are copied onto slim rows with id, group, and `field`.
    Default cover is 50 names at 75% up / 25% down when no size is passed.
    Operator recipes should pass n_leaders/n_laggards (25/25, no cap).
    """
    n_up, n_down = resolve_movers_tail_n(
        n=n,
        cover=cover,
        up_share=up_share,
        n_leaders=n_leaders,
        n_laggards=n_laggards,
    )
    keep_keys = list(
        dict.fromkeys(
            [id_field, *([group_field] if group_field else []), field, *keep]
        )
    )
    overflow_leaders: list[dict[str, Any]] = []
    overflow_laggards: list[dict[str, Any]] = []
    if group_field:
        if cap is None:
            raise ValueError("group_field requires cap")
        leaders, overflow_leaders = group_capped_top_n(
            rows,
            group_field=group_field,
            cap=cap,
            field=field,
            reverse=True,
            n=n_up,
            id_field=id_field,
        )
        laggards, overflow_laggards = group_capped_top_n(
            rows,
            group_field=group_field,
            cap=cap,
            field=field,
            reverse=False,
            n=n_down,
            id_field=id_field,
        )
    else:
        leaders = top_n(rows, field=field, n=n_up, reverse=True)
        laggards = top_n(rows, field=field, n=n_down, reverse=False)
    return {
        "field": field,
        "leaders": [_slim_row(row, keep_keys) for row in leaders],
        "laggards": [_slim_row(row, keep_keys) for row in laggards],
        "n_leaders": n_up,
        "n_laggards": n_down,
        "overflow_leaders_n": len(overflow_leaders),
        "overflow_laggards_n": len(overflow_laggards),
    }


def horizon_movers(
    rows: Sequence[Mapping[str, Any]],
    *,
    fields: Sequence[str],
    n: int | None = None,
    cover: int | None = None,
    up_share: float | None = None,
    n_leaders: int | None = None,
    n_laggards: int | None = None,
    group_field: str | None = None,
    cap: int | None = None,
    id_field: str = "symbol",
    keep: Sequence[str] = (),
) -> dict[str, Any]:
    """Both tails for each named return field (day / d5 / w / m1 / m3).

    Generic default: 50 names per horizon, 75% leaders / 25% laggards.
    Operator recipes pin n_leaders/n_laggards (25/25) with no group cap.
    """
    n_up, n_down = resolve_movers_tail_n(
        n=n,
        cover=cover,
        up_share=up_share,
        n_leaders=n_leaders,
        n_laggards=n_laggards,
    )
    cover_used = n_up + n_down
    if up_share is not None:
        share_used = float(up_share)
    elif n is not None and cover is None:
        share_used = 0.5
    elif n_leaders is not None and cover_used:
        share_used = round(n_up / cover_used, 4)
    else:
        share_used = 0.75
    horizons = {
        field: both_tails(
            rows,
            field=field,
            n_leaders=n_up,
            n_laggards=n_down,
            group_field=group_field,
            cap=cap,
            id_field=id_field,
            keep=keep,
        )
        for field in fields
    }
    return {
        "spec": {
            "fields": list(fields),
            "cover": cover_used,
            "up_share": share_used,
            "n_leaders": n_up,
            "n_laggards": n_down,
            "top": cover_used,
            "group_field": group_field,
            "cap": cap,
            "id_field": id_field,
            "keep": list(keep),
            "source_rows": len(rows),
        },
        "horizons": horizons,
    }


def flatten_movers(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Long rows for CSV: horizon, tail, rank, plus slim name fields."""
    out: list[dict[str, Any]] = []
    horizons = payload.get("horizons") or {}
    for field, block in horizons.items():
        if not isinstance(block, Mapping):
            continue
        for tail in ("leaders", "laggards"):
            for index, row in enumerate(block.get(tail) or [], start=1):
                rec = {"horizon": field, "tail": tail, "rank": index}
                rec.update(dict(row))
                out.append(rec)
    return out


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
