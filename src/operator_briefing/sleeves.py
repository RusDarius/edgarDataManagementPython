"""Deterministic leftover / sleeve filters for the operator briefing pack.

These are the second-layer rules that stop leftover-first dumps
(biotech micros, crashed ADR junk, RSI-70 refiners) from becoming
the radar. Agents should not re-invent this list in SQL.
"""

from __future__ import annotations

from typing import Any, Mapping

SKIP_UNPAID_INDUSTRIES = frozenset(
    {
        "Biotechnology",
        "Precious Metals",
        "Pharmaceuticals: Major",
        "Pharmaceuticals: Other",
        "Pharmaceuticals: Generic",
        "Life/Health Insurance",
        "Property/Casualty Insurance",
        "Insurance Brokers/Services",
        "Managed Health Care",
        "Coal",
        "Steel",
        "Aluminum",
    }
)

PREFERRED_US_EXCHANGES = frozenset(
    {"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "CBOE"}
)

PROFILE_LIVE_MIN = 0.35
LEFTOVER_UNPAID_MIN = 25.0
RSI_UNPAID_MAX = 68.0
RSI_PAID_MIN = 70.0
MCAP_PRIMARY_USD = 2_000_000_000.0
MCAP_SATELLITE_USD = 500_000_000.0
CLOSE_MIN = 5.0


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def leftover_pct(
    close: Any,
    street_pt: Any = None,
    edge_upside_pct: Any = None,
) -> float | None:
    """max(street PT upside %, edge forward_valuation_upside_pct)."""
    close_f = _f(close)
    street = None
    pt = _f(street_pt)
    if close_f and close_f > 0 and pt and pt > 0:
        street = (pt / close_f - 1.0) * 100.0
    edge = _f(edge_upside_pct)
    candidates = [x for x in (street, edge) if x is not None]
    if not candidates:
        return None
    return max(candidates)


def range_position_pct(close: Any, week52_low: Any, week52_high: Any) -> float | None:
    close_f = _f(close)
    lo = _f(week52_low)
    hi = _f(week52_high)
    if close_f is None or lo is None or hi is None or hi <= lo:
        return None
    return (close_f - lo) / (hi - lo) * 100.0


def pct_vs(close: Any, ref: Any) -> float | None:
    close_f = _f(close)
    ref_f = _f(ref)
    if close_f is None or ref_f is None or ref_f == 0:
        return None
    return (close_f / ref_f - 1.0) * 100.0


def profile_live(row: Mapping[str, Any]) -> bool:
    for key in ("bo", "cont", "fwd"):
        value = _f(row.get(key))
        if value is not None and value >= PROFILE_LIVE_MIN:
            return True
    return False


def is_us_listed(row: Mapping[str, Any]) -> bool:
    exchange = str(row.get("exchange") or "").upper()
    country = str(row.get("country") or "").strip().lower()
    if not exchange and not country:
        return True
    if exchange in PREFERRED_US_EXCHANGES:
        return True
    return country in {"united states", "usa", "us"}


def unpaid_eligible(row: Mapping[str, Any], *, book_symbols: set[str] | None = None) -> bool:
    """Quality unpaid leftover: leftover + unused range + not junk industry.

    Book names always pass industry skip (they are already held).
    """
    symbol = str(row.get("symbol") or "")
    if book_symbols and symbol in book_symbols:
        return True
    if not is_us_listed(row):
        return False
    close = _f(row.get("close"))
    if close is None or close < CLOSE_MIN:
        return False
    mcap = _f(row.get("mcap")) or 0.0
    leftover = _f(row.get("left"))
    if leftover is None or leftover < LEFTOVER_UNPAID_MIN:
        return False
    rsi = _f(row.get("rsi"))
    if rsi is not None and rsi > RSI_UNPAID_MAX:
        return False
    industry = str(row.get("ind") or row.get("industry") or "")
    opp = _f(row.get("opp"))
    live = profile_live(row)
    if mcap < MCAP_PRIMARY_USD:
        if mcap < MCAP_SATELLITE_USD:
            return False
        if not live and (opp is None or opp > 15):
            return False
    if industry in SKIP_UNPAID_INDUSTRIES:
        if opp is not None and opp <= 8 and live and mcap >= MCAP_PRIMARY_USD:
            return True
        return False
    if not live and (opp is None or opp > 20):
        return False
    return True


def forming_eligible(row: Mapping[str, Any]) -> bool:
    """Unused range or rising profiles, not already paid."""
    if not is_us_listed(row):
        return False
    rng = _f(row.get("rng"))
    rsi = _f(row.get("rsi"))
    leftover = _f(row.get("left"))
    bo = _f(row.get("bo")) or 0.0
    if rng is not None and rng <= 25 and (leftover or 0) >= 15:
        return True
    if 40 <= (rsi or 50) <= 58 and bo >= 0.4 and (leftover or 0) >= 10:
        return True
    return False


def continuation_paid(row: Mapping[str, Any]) -> bool:
    """Leadership that is already paid — watch, do not chase."""
    if not is_us_listed(row):
        return False
    rsi = _f(row.get("rsi")) or 0.0
    rng = _f(row.get("rng")) or 0.0
    leftover = _f(row.get("left"))
    bo = _f(row.get("bo")) or 0.0
    if bo < 1.2:
        return False
    if rsi >= RSI_PAID_MIN and rng >= 85:
        return True
    if leftover is not None and leftover <= 0 and rsi >= 66 and bo >= 1.4:
        return True
    return False


def short_limited_upside(row: Mapping[str, Any]) -> bool:
    """Failed structure with leftover not large enough to squeeze the short."""
    if not is_us_listed(row):
        return False
    leftover = _f(row.get("left"))
    if leftover is not None and leftover > 25:
        return False
    d5 = _f(row.get("d5"))
    bo = _f(row.get("bo"))
    cont = _f(row.get("cont"))
    vs50 = _f(row.get("vs50"))
    deteriorated = False
    if d5 is not None and d5 <= -8:
        deteriorated = True
    if bo is not None and bo <= 0.15 and cont is not None and cont <= 0.15:
        deteriorated = True
    if vs50 is not None and vs50 <= -3 and (d5 or 0) <= -5:
        deteriorated = True
    return deteriorated


MTP_BOUNCE_ACTIONS = frozenset({"ENTER_SMALL", "ENTER_PROBE"})
MTP_AVOID_ACTIONS = frozenset({"AVOID_CHASE", "AVOID"})
