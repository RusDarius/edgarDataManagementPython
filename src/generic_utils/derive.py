"""Caller-named field transforms. Formulas only — no eligibility cutoffs.

These are the leftover / range / vs-ref calculations agents otherwise
re-implement in every `_tmp_*.py`. Thresholds (what counts as unpaid,
paid, satellite mcap) stay in the calling prompt or in
`operator_briefing.sleeves`.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float


def leftover_pct(
    close: Any,
    street_pt: Any = None,
    edge_upside_pct: Any = None,
) -> float | None:
    """max(street PT upside %, edge forward_valuation_upside_pct)."""
    close_f = to_float(close)
    street = None
    pt = to_float(street_pt)
    if close_f and close_f > 0 and pt and pt > 0:
        street = (pt / close_f - 1.0) * 100.0
    edge = to_float(edge_upside_pct)
    candidates = [x for x in (street, edge) if x is not None]
    if not candidates:
        return None
    return max(candidates)


def implied_price(close: Any, leftover_pct_value: Any, *, digits: int = 2) -> float | None:
    """Close * (1 + leftover%/100). Street leftover -> street_px; pack leftover -> target_px."""
    close_f = to_float(close)
    leftover = to_float(leftover_pct_value)
    if close_f is None or leftover is None:
        return None
    return round(close_f * (1.0 + leftover / 100.0), digits)


def range_position_pct(close: Any, week52_low: Any, week52_high: Any) -> float | None:
    close_f = to_float(close)
    lo = to_float(week52_low)
    hi = to_float(week52_high)
    if close_f is None or lo is None or hi is None or hi <= lo:
        return None
    return (close_f - lo) / (hi - lo) * 100.0


def pct_vs(close: Any, ref: Any) -> float | None:
    close_f = to_float(close)
    ref_f = to_float(ref)
    if close_f is None or ref_f is None or ref_f == 0:
        return None
    return (close_f / ref_f - 1.0) * 100.0


def leftover_to_range(
    leftover: Any,
    rng: Any,
    *,
    leftover_max: float | None = None,
    rng_floor: float = 15.0,
    rng_default: float = 50.0,
) -> float | None:
    """Leftover % per unit of 52w range already used.

    High = unused upside vs already-paid range. Not a probability.
    `leftover_max` is optional and caller-supplied (the operator scan
    passes 90 to drop street-PT dumps). No default dump cutoff lives here.
    """
    leftover_f = to_float(leftover)
    if leftover_f is None or leftover_f <= 0:
        return None
    if leftover_max is not None and leftover_f > leftover_max:
        return None
    rng_f = to_float(rng)
    denom = max(rng_f if rng_f is not None else rng_default, rng_floor)
    return round(leftover_f / denom, 2)


def apply_derived(
    rows: list[dict[str, Any]],
    *,
    close_field: str = "close",
    pt_field: str = "pt",
    edge_field: str = "edge_left",
    lo52_field: str = "lo52",
    hi52_field: str = "hi52",
    sma50_field: str = "sma50",
    leftover_max: float | None = None,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    """Attach street leftover, combined leftover, 52w range, vs-SMA50, rr.

    By default skips a derived key that is already set. `refresh=True`
    recomputes from the named inputs so a later close can update leftover.
    Does not filter rows.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        close = rec.get(close_field)
        if refresh or rec.get("street_left") is None:
            rec["street_left"] = leftover_pct(close, rec.get(pt_field), None)
        if refresh or rec.get("left") is None:
            rec["left"] = leftover_pct(close, rec.get(pt_field), rec.get(edge_field))
        if refresh or rec.get("rng") is None:
            rec["rng"] = range_position_pct(
                close, rec.get(lo52_field), rec.get(hi52_field)
            )
        if refresh or rec.get("vs50") is None:
            rec["vs50"] = pct_vs(close, rec.get(sma50_field))
        if refresh or rec.get("rr") is None:
            rec["rr"] = leftover_to_range(
                rec.get("left"), rec.get("rng"), leftover_max=leftover_max
            )
        if refresh or rec.get("street_px") is None:
            rec["street_px"] = rec.get(pt_field) or implied_price(
                close, rec.get("street_left")
            )
        if refresh or rec.get("target_px") is None:
            rec["target_px"] = implied_price(close, rec.get("left"))
        out.append(rec)
    return out


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def attach_vs_group(
    rows: list[dict[str, Any]],
    *,
    group_field: str = "ind",
    fields: Sequence[str],
    min_n: int = 6,
    suffix: str = "_vs_ind",
    median_suffix: str = "_ind_med",
    digits: int = 1,
) -> list[dict[str, Any]]:
    """Attach pct vs group median for each field. No eligibility cutoff.

    For multiples (EV/Rev, PE), a negative `{field}_vs_ind` is cheaper than
    the industry median (a discount). For leftover / returns, positive means
    more leftover / more move than peers. Groups smaller than `min_n` (or
    with fewer than `min_n` clean values) get null relatives.
    """
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        key = str(row.get(group_field) or "")
        if key:
            groups[key].append(index)

    medians: dict[tuple[str, str], float | None] = {}
    for key, indexes in groups.items():
        members = [rows[i] for i in indexes]
        n = len(members)
        for field in fields:
            clean = [v for v in (to_float(m.get(field)) for m in members) if v is not None]
            if n < min_n or len(clean) < min_n:
                medians[(key, field)] = None
            else:
                medians[(key, field)] = _median(clean)

    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        key = str(rec.get(group_field) or "")
        for field in fields:
            med = medians.get((key, field))
            rec[f"{field}{median_suffix}"] = (
                round(med, digits) if med is not None else None
            )
            rel = pct_vs(rec.get(field), med) if med is not None else None
            rec[f"{field}{suffix}"] = round(rel, digits) if rel is not None else None
        out.append(rec)
    return out


def vs_group_from_recipe(recipe: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Parse movers recipe `vs_group` {by, fields, min_n}."""
    block = (recipe or {}).get("vs_group")
    if not isinstance(block, Mapping):
        return None
    fields = [str(item) for item in (block.get("fields") or []) if item]
    by = str(block.get("by") or "").strip()
    if not by or not fields:
        return None
    return {
        "group_field": by,
        "fields": fields,
        "min_n": int(block.get("min_n") or 6),
    }


def enrich_mover_rows(
    rows: list[dict[str, Any]],
    recipe: Mapping[str, Any] | None = None,
    *,
    vs_by: str | None = None,
    vs_fields: Sequence[str] | None = None,
    vs_min_n: int | None = None,
) -> list[dict[str, Any]]:
    """Attach industry-relative valuation / leftover / tape after the universe filter.

    Peer set is the filtered tape (US $2B), not the 50-name tail.
    """
    parsed = vs_group_from_recipe(recipe) or {}
    by = vs_by or parsed.get("group_field")
    fields = list(vs_fields) if vs_fields else list(parsed.get("fields") or [])
    min_n = parsed.get("min_n") if vs_min_n is None else vs_min_n
    if min_n is None:
        min_n = 6
    if not by or not fields:
        enriched = list(rows)
    else:
        enriched = attach_vs_group(
            rows, group_field=str(by), fields=fields, min_n=int(min_n)
        )
    from generic_utils.setup import attach_setup

    return attach_setup(attach_implied_targets(enriched))


def attach_implied_targets(
    rows: Sequence[Mapping[str, Any]],
    *,
    close_field: str = "close",
    pt_field: str = "pt",
) -> list[dict[str, Any]]:
    """Attach street_px (analyst PT or street leftover) and target_px (pack leftover)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        if rec.get("street_px") is None:
            rec["street_px"] = rec.get(pt_field) or implied_price(
                rec.get(close_field), rec.get("street_left")
            )
        if rec.get("target_px") is None:
            rec["target_px"] = implied_price(rec.get(close_field), rec.get("left"))
        out.append(rec)
    return out


def keep_with_vs_group(
    keep: Sequence[str], recipe: Mapping[str, Any] | None = None
) -> list[str]:
    """Union recipe keep with `{field}_vs_ind` / `{field}_ind_med` columns."""
    out = list(keep)
    parsed = vs_group_from_recipe(recipe)
    if parsed:
        for field in parsed["fields"]:
            for suffix in ("_vs_ind", "_ind_med"):
                key = f"{field}{suffix}"
                if key not in out:
                    out.append(key)
    for extra in ("close", "street_px", "target_px"):
        if extra not in out:
            out.append(extra)
    from generic_utils.setup import keep_with_setup

    return keep_with_setup(out)
