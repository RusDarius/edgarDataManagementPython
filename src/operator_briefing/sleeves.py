"""Deterministic leftover / sleeve filters for the operator briefing pack.

These are the second-layer rules that stop leftover-first dumps
(biotech micros, crashed ADR junk, RSI-70 refiners) from becoming
the radar. Agents should not re-invent this list in SQL.
"""

from __future__ import annotations

from typing import Any, Mapping

from generic_utils.derive import leftover_pct, leftover_to_range, pct_vs, range_position_pct
from generic_utils.ranking import group_capped_top_n, to_float

_f = to_float

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

# Paid/crowd tape clusters. Not an automatic skip for Book names;
# stance treats them as a conflict that cuts NEW/ADD support.
CROWD_INDUSTRIES = frozenset(
    {
        "Precious Metals",
        "Oil Refining/Marketing",
        "Biotechnology",
        "Agricultural Chemicals",
        "Chemicals: Agricultural",
        "Agricultural Commodities/Milling",
        "Marine Shipping",
        "Insurance Brokers/Services",
        "Property/Casualty Insurance",
        "Integrated Oil",
        "Oil & Gas Production",
        "Oil & Gas Pipelines",
        "Contract Drilling",
        "Oilfield Services/Equipment",
        "Pharmaceuticals: Major",
        "Pharmaceuticals: Other",
        "Medical Specialties",
    }
)

PREFERRED_US_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "CBOE"})

BLOCKED_LISTING_PREFIXES = frozenset(
    {
        "OTC",
        "CSE",
        "NEO",
        "TSX",
        "TSXV",
        "HKEX",
        "KRX",
        "LSE",
        "XETR",
        "SWX",
        "OMXCOP",
        "OMXSTO",
        "TASE",
        "JPX",
        "TSE",
        "ASX",
        "BSE",
        "NSE",
    }
)

PROFILE_LIVE_MIN = 0.35
LEFTOVER_UNPAID_MIN = 25.0
LEFTOVER_UNPAID_MAX = 90.0
LEFTOVER_SHORT_FLOOR = -25.0
RSI_UNPAID_MAX = 68.0
RSI_PAID_MIN = 70.0
MCAP_PRIMARY_USD = 2_000_000_000.0
MCAP_SATELLITE_USD = 500_000_000.0
CLOSE_MIN = 5.0
BOUNCE_DAY_MIN = 4.0


def unpaid_sort_key(row: Mapping[str, Any]) -> tuple:
    """Live profile and edge rank first — not leftover-first dumps."""
    live = 0 if profile_live(row) else 1
    opp = _f(row.get("opp"))
    opp_key = opp if opp is not None else 9999.0
    leftover = -(_f(row.get("left")) or 0.0)
    return (live, opp_key, leftover)


def profile_live(row: Mapping[str, Any]) -> bool:
    for key in ("bo", "cont", "fwd"):
        value = _f(row.get(key))
        if value is not None and value >= PROFILE_LIVE_MIN:
            return True
    return False


def is_us_listed(row: Mapping[str, Any]) -> bool:
    symbol = str(row.get("symbol") or "")
    prefix = symbol.split(":", 1)[0].upper() if ":" in symbol else ""
    if prefix in BLOCKED_LISTING_PREFIXES:
        return False
    exchange = str(row.get("exchange") or "").upper()
    country = str(row.get("country") or "").strip().lower()
    if prefix in PREFERRED_US_EXCHANGES or exchange in PREFERRED_US_EXCHANGES:
        return True
    return country in {"united states", "usa", "us"}


def unpaid_eligible(
    row: Mapping[str, Any], *, book_symbols: set[str] | None = None
) -> bool:
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
    if leftover > LEFTOVER_UNPAID_MAX and not (book_symbols and symbol in book_symbols):
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
    close = _f(row.get("close"))
    if close is None or close < CLOSE_MIN:
        return False
    if (_f(row.get("mcap")) or 0.0) < MCAP_PRIMARY_USD:
        return False
    leftover = _f(row.get("left"))
    if leftover is not None and leftover > LEFTOVER_UNPAID_MAX:
        return False
    rng = _f(row.get("rng"))
    rsi = _f(row.get("rsi"))
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


def continuation_unpaid(row: Mapping[str, Any]) -> bool:
    """Live weeks continuation/breakout with leftover still unpaid. Not a chase."""
    if not is_us_listed(row):
        return False
    if continuation_paid(row):
        return False
    if not profile_live(row):
        return False
    leftover = _f(row.get("left"))
    if leftover is None or leftover < 12:
        return False
    if leftover > LEFTOVER_UNPAID_MAX:
        return False
    rsi = _f(row.get("rsi"))
    if rsi is not None and rsi > RSI_UNPAID_MAX:
        return False
    close = _f(row.get("close"))
    if close is None or close < CLOSE_MIN:
        return False
    if (_f(row.get("mcap")) or 0.0) < MCAP_PRIMARY_USD:
        return False
    industry = str(row.get("ind") or row.get("industry") or "")
    if industry in SKIP_UNPAID_INDUSTRIES:
        return False
    bo = _f(row.get("bo")) or 0.0
    cont = _f(row.get("cont")) or 0.0
    return bo >= 0.5 or cont >= 0.5


def short_limited_upside(row: Mapping[str, Any]) -> bool:
    """Failed structure with leftover not large enough to squeeze the short."""
    if not is_us_listed(row):
        return False
    close = _f(row.get("close"))
    if close is None or close < CLOSE_MIN:
        return False
    if (_f(row.get("mcap")) or 0.0) < MCAP_PRIMARY_USD:
        return False
    leftover = _f(row.get("left"))
    if leftover is not None and leftover > 25:
        return False
    if leftover is not None and leftover < LEFTOVER_SHORT_FLOOR:
        return False
    industry = str(row.get("ind") or row.get("industry") or "")
    if industry in SKIP_UNPAID_INDUSTRIES:
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


def reward_risk(row: Mapping[str, Any]) -> float | None:
    """Leftover % per unit of 52w range already used. High = unused upside.

    Not a probability. Street-PT dumps (leftover > max) return None.
    """
    return leftover_to_range(
        row.get("left"),
        row.get("rng"),
        leftover_max=LEFTOVER_UNPAID_MAX,
    )


MTP_BOUNCE_ACTIONS = frozenset({"ENTER_SMALL", "ENTER_PROBE"})
MTP_AVOID_ACTIONS = frozenset({"AVOID_CHASE", "AVOID"})

# Headline curated sleeves cap names per industry so one crowded industry
# (e.g. Packaged Software) cannot fill the whole radar/short list. The
# uncapped sleeve stays available for research; curated is the headline view.
RADAR_INDUSTRY_CAP = 5
SHORT_INDUSTRY_CAP = 5
EARNINGS_INDUSTRY_CAP = 5


def dedupe_by_industry(
    rows: list[Mapping[str, Any]],
    *,
    cap: int,
    book_symbols: set[str] | None = None,
) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
    """Cap a pre-sorted (best-first) sleeve at `cap` names per industry.

    Domain wrapper around `group_capped_top_n`: maps `ind`/`industry`, keeps
    Book names, and returns overflow as symbol/industry/left so a crowded
    industry cut is visible instead of silently dropped.
    """
    prepared: list[Mapping[str, Any]] = []
    for row in rows:
        if row.get("ind"):
            prepared.append(row)
            continue
        rec = dict(row)
        rec["ind"] = str(rec.get("industry") or "")
        prepared.append(rec)
    kept, overflow = group_capped_top_n(
        prepared,
        group_field="ind",
        cap=cap,
        exempt=set(book_symbols or ()),
        id_field="symbol",
        overflow_fields=("left",),
    )
    mapped = [
        {
            "symbol": o.get("symbol"),
            "industry": o.get("ind") or o.get("industry"),
            "left": o.get("left"),
        }
        for o in overflow
    ]
    return kept, mapped
