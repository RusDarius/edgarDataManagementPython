"""Join two all-fields scans for names that reported on a given date.

No leftover, market-cap, or RSI cutoff. The date and the exchange list are
caller filters. Premarket on the later file is copied through; it is not
treated as the next morning unless pre_chg actually changed.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from operator_briefing.extract import _parse_date

SESSION_COLUMNS: tuple[str, ...] = (
    "symbol",
    "name",
    "exchange",
    "industry",
    "close",
    "change",
    "premarket_change",
    "premarket_gap",
    "premarket_close",
    "postmarket_change",
    "postmarket_close",
    "earnings_release_date",
    "earnings_release_time",
    "price_target_average",
    "RSI",
)

_WHEN = {
    "1": "AMC",
    "1.0": "AMC",
    "-1": "BMO",
    "-1.0": "BMO",
    "0": "UNSPEC",
    "0.0": "UNSPEC",
}


def when_label(value: Any) -> str:
    """TradingView earnings_release_time. 1 after close, -1 before open."""
    if value is None or value == "":
        return "UNSPEC"
    text = str(value).strip()
    return _WHEN.get(text, "UNSPEC")


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def reported_on(row: Mapping[str, Any], earn_on: date) -> bool:
    """True when the last release date is the caller's day."""
    return _parse_date(row.get("earnings_release_date")) == earn_on


def _index(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            out[symbol] = row
    return out


def join_post_earn(
    prior_rows: Sequence[Mapping[str, Any]],
    current_rows: Sequence[Mapping[str, Any]],
    earn_on: date,
    *,
    exchanges: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """One row per symbol that reported on earn_on in either scan.

    Sorted by absolute session-day return, then absolute after-hours return.
    Missing returns sort last. This order is not a score.
    """
    allowed = {item.strip().upper() for item in exchanges} if exchanges else None
    prior = _index(prior_rows)
    current = _index(current_rows)
    symbols = set(prior) | set(current)
    out: list[dict[str, Any]] = []
    for symbol in symbols:
        now = current.get(symbol) or {}
        then = prior.get(symbol) or {}
        if not (reported_on(now, earn_on) or reported_on(then, earn_on)):
            continue
        exchange = str(now.get("exchange") or then.get("exchange") or "")
        if allowed is not None and exchange.upper() not in allowed:
            continue
        afternoon = _num(then.get("close"))
        session = _num(now.get("close"))
        vs_afternoon = None
        if afternoon and session:
            vs_afternoon = (session / afternoon - 1.0) * 100.0
        pre_now = _num(now.get("premarket_change"))
        pre_then = _num(then.get("premarket_change"))
        pre_repeat = (
            pre_now is not None
            and pre_then is not None
            and abs(pre_now - pre_then) < 0.05
        )
        source = now or then
        out.append(
            {
                "symbol": symbol,
                "company": source.get("name") or source.get("company"),
                "ind": source.get("industry"),
                "exchange": exchange or None,
                "when_label": when_label(
                    source.get("earnings_release_time")
                    if now
                    else then.get("earnings_release_time")
                ),
                "earn_on": earn_on.isoformat(),
                "afternoon_close": _round(afternoon, 2),
                "afternoon_day": _round(_num(then.get("change")), 2),
                "session_close": _round(session, 2),
                "session_day": _round(_num(now.get("change")), 2),
                "vs_afternoon": _round(vs_afternoon, 2),
                "pre_chg": _round(pre_now, 2),
                "pre_gap": _round(_num(now.get("premarket_gap")), 2),
                "pre_close": _round(_num(now.get("premarket_close")), 2),
                "pre_repeat": pre_repeat,
                "post_chg": _round(_num(now.get("postmarket_change")), 2),
                "post_close": _round(_num(now.get("postmarket_close")), 2),
                "pt": _round(_num(source.get("price_target_average")), 2),
                "rsi": _round(_num(source.get("RSI")), 1),
            }
        )

    def _sort_key(row: Mapping[str, Any]) -> tuple[float, float]:
        day = row.get("session_day")
        post = row.get("post_chg")
        day_key = -(abs(day)) if isinstance(day, (int, float)) else 0.0
        post_key = -(abs(post)) if isinstance(post, (int, float)) else 0.0
        if day is None:
            day_key = 1.0
        return (day_key, post_key)

    out.sort(key=_sort_key)
    return out


def newest_run_id(database_path: str | Path) -> str:
    from generic_utils.scan_sources import rows_from_duckdb

    rows = rows_from_duckdb(
        database_path,
        "SELECT run_id FROM run_metadata ORDER BY created_at_utc DESC NULLS LAST LIMIT 1",
        [],
    )
    if not rows or not rows[0].get("run_id"):
        raise FileNotFoundError(f"no run_metadata in {database_path}")
    return str(rows[0]["run_id"])


def load_session_rows(database_path: str | Path, run_id: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    from generic_utils.series import fetch_named

    chosen = run_id or newest_run_id(database_path)
    fetched = fetch_named(
        database_path,
        table="all_fields_rows",
        columns=list(SESSION_COLUMNS),
        run_id=chosen,
    )
    return chosen, list(fetched["rows"])


def parse_earn_on(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = _parse_date(value)
    if parsed is None:
        raise ValueError(f"earn-on date {value!r} is not YYYY-MM-DD")
    return parsed


def build_post_earn(
    *,
    prior_db: str | Path,
    current_db: str | Path,
    earn_on: str | date,
    prior_run: str | None = None,
    current_run: str | None = None,
    exchanges: Sequence[str] | None = ("NASDAQ", "NYSE", "AMEX"),
) -> dict[str, Any]:
    """Load two scans and return the post-earn join. No cutoff beyond the date."""
    day = parse_earn_on(earn_on)
    prior_id, prior_rows = load_session_rows(prior_db, prior_run)
    current_id, current_rows = load_session_rows(current_db, current_run)
    rows = join_post_earn(prior_rows, current_rows, day, exchanges=exchanges)
    return {
        "rows": rows,
        "spec": {
            "earn_on": day.isoformat(),
            "prior_db": str(prior_db),
            "prior_run": prior_id,
            "current_db": str(current_db),
            "current_run": current_id,
            "exchanges": list(exchanges) if exchanges else None,
            "n": len(rows),
            "notes": [
                "afternoon_* is the prior scan, which can be an intraday mark",
                "session_* is the later scan",
                "pre_repeat means premarket_change was not refreshed",
                "no leftover, RSI, or market-cap cutoff",
            ],
        },
    }
