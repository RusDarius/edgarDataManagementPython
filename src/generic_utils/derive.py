"""Caller-named field transforms. Formulas only — no eligibility cutoffs.

These are the leftover / range / vs-ref calculations agents otherwise
re-implement in every `_tmp_*.py`. Thresholds (what counts as unpaid,
paid, satellite mcap) stay in the calling prompt or in
`operator_briefing.sleeves`.
"""

from __future__ import annotations

from typing import Any

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
) -> list[dict[str, Any]]:
    """Attach street leftover, combined leftover, 52w range, and vs-SMA50.

    Skips a derived key when its inputs are missing. Does not filter rows.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        close = rec.get(close_field)
        if rec.get("street_left") is None:
            rec["street_left"] = leftover_pct(close, rec.get(pt_field), None)
        if rec.get("left") is None:
            rec["left"] = leftover_pct(close, rec.get(pt_field), rec.get(edge_field))
        if rec.get("rng") is None:
            rec["rng"] = range_position_pct(
                close, rec.get(lo52_field), rec.get(hi52_field)
            )
        if rec.get("vs50") is None:
            rec["vs50"] = pct_vs(close, rec.get(sma50_field))
        if rec.get("rr") is None:
            rec["rr"] = leftover_to_range(rec.get("left"), rec.get("rng"))
        out.append(rec)
    return out
