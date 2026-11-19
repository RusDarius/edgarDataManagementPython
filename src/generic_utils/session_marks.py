"""US session marks on one all-fields row: the premarket and after-hours price, how much volume traded there, and the
close against the session VWAP.

TradingView's ``premarket_change`` is measured against the last cash close *before* that premarket. During the
premarket that close is still the row's ``close``; once the cash session opens, ``close`` is the live price and the
base is the prior close (``close / (1 + change / 100)``). Measured on 29 full-catalog runs (Sep to Nov 2026): the
formula ``close x (1 + premarket_change)`` is exact on scans before the open and off by the day's move (median about
1%) on every scan after it. ``postmarket_change`` is measured against the cash close of the session it follows,
which is the row's ``close`` except during the next cash session. When the scan carries ``premarket_close`` /
``postmarket_close`` those prices are used as they are and nothing is derived.

What this does not do: no cutoff, no score, no judgment of whether a move is large. US market holidays are not in
the clock: a scan on a holiday weekday reads as that session's phase (rare; the derived price can then be off).
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any, Mapping

from generic_utils.ranking import to_float

US_EASTERN = "America/New_York"
PRE_OPEN, CASH_OPEN, CASH_CLOSE, POST_CLOSE = time(4, 0), time(9, 30), time(16, 0), time(20, 0)

# all-fields column -> short name, for callers that fetch the raw row themselves
SESSION_FIELDS: dict[str, str] = {
    "close": "close",
    "change": "change",
    "premarket_change": "pre_chg",
    "premarket_gap": "pre_gap",
    "premarket_close": "pre_close",
    "premarket_volume": "pre_vol",
    "postmarket_change": "post_chg",
    "postmarket_close": "post_close",
    "postmarket_volume": "post_vol",
    "average_volume_10d_calc": "avgvol10",
    "VWAP": "vwap",
}

# what session_marks adds to a row (pack tables, premarket book, earnings week)
MARK_COLUMNS: tuple[str, ...] = ("us_phase", "pre_chg", "pre_px", "pre_vol_x", "post_chg", "post_px", "post_vol_x", "vs_vwap")


def us_phase(scan_at: datetime | None) -> str | None:
    """'pre' (04:00-09:30 New York, weekday), 'cash' (09:30-16:00), 'post' (16:00-20:00), 'closed' (overnight and
    weekends); None without a scan time. A naive datetime is read as UTC (run_metadata.created_at_utc)."""
    if scan_at is None:
        return None
    from zoneinfo import ZoneInfo

    stamp = scan_at if scan_at.tzinfo else scan_at.replace(tzinfo=timezone.utc)
    local = stamp.astimezone(ZoneInfo(US_EASTERN))
    if local.weekday() >= 5:
        return "closed"
    clock = local.time()
    if PRE_OPEN <= clock < CASH_OPEN:
        return "pre"
    if CASH_OPEN <= clock < CASH_CLOSE:
        return "cash"
    if CASH_CLOSE <= clock < POST_CLOSE:
        return "post"
    return "closed"


def prior_close(close: Any, change: Any) -> float | None:
    """The cash close before the row's session: close / (1 + change / 100)."""
    px, ch = to_float(close), to_float(change)
    if px is None or ch is None or ch <= -100:
        return None
    return px / (1.0 + ch / 100.0)


def _ratio(num: Any, den: Any) -> float | None:
    a, b = to_float(num), to_float(den)
    return a / b if a is not None and b else None


def _rnd(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def session_marks(row: Mapping[str, Any], scan_at: datetime | None) -> dict[str, Any]:
    """Marks for one row keyed by the short names of ``SESSION_FIELDS`` (``close``, ``change``, ``pre_chg``,
    ``pre_close``, ``pre_vol``, ``post_chg``, ``post_close``, ``post_vol``, ``avgvol10``, ``vwap``).

    - ``pre_px``: ``pre_close`` when the scan carries it, else the base close (see the module note) times
      ``1 + pre_chg / 100``. Empty without a premarket change or, when it must be derived, without a scan time.
    - ``post_px``: ``post_close``, else the cash close of the session it follows times ``1 + post_chg / 100``.
    - ``pre_vol_x`` / ``post_vol_x``: shares traded in that session over the 10-day average daily volume (0.05 = 5% of
      a normal day). A large move on a small ratio is a thin print.
    - ``vs_vwap``: close against the session VWAP, percent (during the premarket both are the prior session's).
    """
    phase = us_phase(scan_at)
    close, change = to_float(row.get("close")), to_float(row.get("change"))
    pre_chg, post_chg = to_float(row.get("pre_chg")), to_float(row.get("post_chg"))
    before = prior_close(close, change)
    pre_px = to_float(row.get("pre_close"))
    if not (pre_px and pre_px > 0):
        base = None if phase is None else close if phase == "pre" else before
        pre_px = base * (1.0 + pre_chg / 100.0) if base is not None and pre_chg is not None else None
    post_px = to_float(row.get("post_close"))
    if not (post_px and post_px > 0):
        base = None if phase is None else before if phase == "cash" else close
        post_px = base * (1.0 + post_chg / 100.0) if base is not None and post_chg is not None else None
    vwap = to_float(row.get("vwap"))
    return {
        "us_phase": phase,
        "pre_chg": _rnd(pre_chg, 2),
        "pre_px": _rnd(pre_px, 4),
        "pre_vol_x": _rnd(_ratio(row.get("pre_vol"), row.get("avgvol10")), 4),
        "post_chg": _rnd(post_chg, 2),
        "post_px": _rnd(post_px, 4),
        "post_vol_x": _rnd(_ratio(row.get("post_vol"), row.get("avgvol10")), 4),
        "vs_vwap": _rnd((close / vwap - 1.0) * 100.0, 2) if close and vwap and vwap > 0 else None,
    }


def marks_from_raw(row: Mapping[str, Any], scan_at: datetime | None) -> dict[str, Any]:
    """``session_marks`` on a row keyed by all-fields column names."""
    return session_marks({short: row.get(col) for col, short in SESSION_FIELDS.items()}, scan_at)


def run_scan_time(database_path: Any, run_id: str | None) -> datetime | None:
    """created_at_utc of one all-fields run (None when the run or the column is missing)."""
    if not run_id:
        return None
    from generic_utils.scan_sources import rows_from_duckdb

    try:
        rows = rows_from_duckdb(database_path, "SELECT created_at_utc FROM run_metadata WHERE run_id = ?", [run_id])
    except Exception:  # an old file without run_metadata: the marks fall back to the explicit prices only
        return None
    value = rows[0].get("created_at_utc") if rows else None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
