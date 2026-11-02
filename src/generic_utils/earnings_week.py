"""Earnings week: every name with a print this week (W0) and next week (W1), one row each.

A row carries the calendar slot (UTC time, BMO / AMC, the day the move shows), the tape
into the print, the estimate for the coming quarter against the last one, value to
industry peers and to the name's own day-file history, the reactions to its past prints,
and whatever the day's other suites already scored (pack tape, earnings play, book, movers
tails, GMV lens, prediction earnings consensus, edge), joined on exact EXCHANGE:TICKER.

Past reactions come from the all-fields day files. The reaction file is the first file
on the print date (BMO) or after it (AMC); its ``change`` is the move. Run-up is the
close into the print against about two weeks earlier; drift is the close a few files
after the reaction against the reaction close.

The universe floor in config/generic_utils/earnings_week.json (markets, types, market
cap) is the only filter, and named ids always stay in. No ranking, no composite, no
course: orders are the page's, courses are the judgment's.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from generic_utils.print_pointers import rev_surprise_pct, surprise_mix
from generic_utils.ranking import to_float
from generic_utils.series import qident

_f = to_float

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "earnings_week.json"
ALL_FIELDS_ROOT = REPO_ROOT / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"

# short name -> all-fields column (today's day file)
RAW_FIELDS: dict[str, str] = {
    "company": "description", "ind": "industry", "sector": "sector", "market": "market", "type": "type",
    "close": "close", "mcap": "market_cap_basic", "day": "change", "gap": "gap", "pre_chg": "premarket_change",
    "post_chg": "postmarket_change", "w": "Perf.W", "m1": "Perf.1M", "m3": "Perf.3M", "ytd": "Perf.YTD", "rsi": "RSI",
    "sma50": "SMA50", "sma200": "SMA200", "hi52": "price_52_week_high", "lo52": "price_52_week_low",
    "relvol": "relative_volume_10d_calc", "atrp": "ATRP", "vol_d": "Volatility.D", "pt_1y": "price_target_1y",
    "pt_avg": "price_target_average",
    "pt_high": "price_target_high", "pt_low": "price_target_low", "rec": "Recommend.All",
    "eps_next_q": "earnings_per_share_forecast_next_fq", "eps_last_q": "earnings_per_share_fq",
    "rev_next_q": "revenue_forecast_next_fq", "rev_last_q": "total_revenue_fq", "last_surprise": "eps_surprise_percent_fq",
    "rev_est_last_q": "revenue_forecast_fq", "rev_surprise_tv": "revenue_surprise_percent_fq",
    "eps_nfy": "earnings_per_share_forecast_next_fy", "eps_ttm": "earnings_per_share_diluted_ttm",
    "rev_nfy": "revenue_forecast_next_fy", "rev_ttm": "total_revenue_ttm", "pe": "price_earnings_ttm",
    "pe_fwd_tv": "non_gaap_price_to_earnings_per_share_forecast_next_fy", "peg": "price_earnings_growth_ttm",
    "evebitda": "enterprise_value_ebitda_ttm", "evrev": "enterprise_value_to_revenue_ttm",
    "rev_yoy": "total_revenue_yoy_growth_ttm", "eps_yoy": "earnings_per_share_diluted_yoy_growth_ttm",
    "rev_qoq": "total_revenue_qoq_growth_fq", "opm": "operating_margin_ttm", "fcf_margin": "free_cash_flow_margin_ttm",
    "nd_ebitda": "net_debt_to_ebitda_fq", "earn_next": "earnings_release_next_date",
    "earn_next_time": "earnings_release_next_time", "earn_last": "earnings_release_date",
    "earn_last_time": "earnings_release_time",
}
TEXT_FIELDS = {"company", "ind", "sector", "market", "type", "earn_next", "earn_next_time", "earn_last", "earn_last_time"}

HISTORY_FIELDS: tuple[str, ...] = (
    "symbol", "close", "change", "market_cap_basic", "relative_volume_10d_calc", "price_target_1y", "price_target_average",
    "earnings_per_share_forecast_next_fy", "eps_surprise_percent_fq", "revenue_surprise_percent_fq",
    "total_revenue_fq", "revenue_forecast_fq", "earnings_release_date", "earnings_release_time",
)
HISTORY_TEXT = {"symbol", "earnings_release_date", "earnings_release_time"}

CALENDAR_COLUMNS: tuple[str, ...] = (
    "symbol", "company", "ind", "sector", "market", "mcap", "week", "earn_date", "earn_dow", "earn_utc", "slot",
    "react_on", "printed", "dte", "close", "day", "gap", "pre_chg", "post_chg", "w", "m1", "m3", "ytd", "runup",
    "since_print", "rsi", "vs50", "vs200", "rng", "relvol", "atrp", "vol_d", "pt", "pt_up", "pt_high_up", "pt_low_up",
    "rec", "eps_next_q", "eps_last_q", "eps_q_step", "rev_next_q", "rev_last_q", "rev_q_step", "last_surprise",
    "rev_surprise", "surprise_mix", "rev_posted", "eps_nfy", "eps_nfy_g", "rev_nfy_g", "rev_yoy", "eps_yoy", "rev_qoq", "opm", "fcf_margin", "nd_ebitda", "pe",
    "pe_nfy", "pe_fwd_tv", "pe_nfy_ind_med", "pe_nfy_vs_ind", "pe_nfy_own_med", "pe_nfy_vs_own", "pe_nfy_seat", "peg",
    "evebitda", "evebitda_vs_ind", "evrev", "evrev_vs_ind", "eps_rev_1m", "eps_rev_3m", "pt_drift_1m", "n_prints",
    "react_last", "react_abs_mean", "react_up", "runup_last", "drift_last", "surprise_last", "react_hist", "hist_n",
    "splits",
)

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WHEN = {"1": "AMC", "1.0": "AMC", "-1": "BMO", "-1.0": "BMO"}


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: Any, digits: int = 2) -> float | None:
    number = _f(value)
    return None if number is None else round(number, digits)


def _pct(now: Any, ref: Any) -> float | None:
    a, b = _f(now), _f(ref)
    if a is None or b is None or b <= 0:
        return None
    return round((a / b - 1.0) * 100.0, 2)


def epoch_dt(value: Any) -> datetime | None:
    """TradingView date cell (epoch seconds, or an ISO date) -> aware UTC datetime."""
    if value is None or value == "":
        return None
    number = _f(value)
    if number is not None:
        if number <= 0:
            return None
        return datetime.fromtimestamp(number, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def slot_label(time_code: Any, at: datetime | None, amc_from_hour_utc: int = 13) -> str:
    """BMO / AMC from TradingView's time code (-1 / 1); otherwise BMO~ / AMC~ inferred from the UTC hour."""
    text = "" if time_code is None else str(time_code).strip()
    if text in _WHEN:
        return _WHEN[text]
    if at is None:
        return "UNSPEC"
    return "AMC~" if at.hour >= amc_from_hour_utc else "BMO~"


def next_weekday(day: date) -> date:
    out = day + timedelta(days=1)
    while out.weekday() >= 5:
        out += timedelta(days=1)
    return out


def react_on(at: datetime, slot: str) -> date:
    """The day the move shows: the print date for BMO, the next weekday for AMC."""
    day = at.date()
    return next_weekday(day) if slot.startswith("AMC") else day


def week_window(scan_day: date, weeks: int = 2) -> tuple[date, date]:
    """Monday of the scan week and the Monday ``weeks`` later (exclusive)."""
    start = scan_day - timedelta(days=scan_day.weekday())
    return start, start + timedelta(days=7 * max(1, int(weeks)))


def newest_run(database: str | Path) -> tuple[str, datetime | None]:
    import duckdb

    con = duckdb.connect(str(database), read_only=True)
    try:
        row = con.execute("SELECT run_id, created_at_utc FROM run_metadata ORDER BY created_at_utc DESC LIMIT 1").fetchone()
    finally:
        con.close()
    created = row[1]
    if isinstance(created, datetime) and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return str(row[0]), created


def latest_day_file(root: str | Path | None = None) -> Path:
    from generic_utils.series import list_dated_files

    files = list_dated_files(Path(root) if root else ALL_FIELDS_ROOT, "**/tradingview_all_fields_*.duckdb")
    if not files:
        raise FileNotFoundError(f"no all-fields day file under {root or ALL_FIELDS_ROOT}")
    return Path(files[-1]["path"])


def universe_rows(database: str | Path, run_id: str, recipe: Mapping[str, Any], ids: Sequence[str] = ()) -> list[dict[str, Any]]:
    """The day file's newest run inside the recipe floor (markets, types, market cap), plus the named ids."""
    import duckdb

    con = duckdb.connect(str(database), read_only=True)
    try:
        present = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'all_fields_rows'").fetchall()}
        select = ["symbol"]
        for short, col in RAW_FIELDS.items():
            if col not in present:
                select.append(f"NULL AS {qident(short)}")
            elif short in TEXT_FIELDS:
                select.append(f"CAST({qident(col)} AS VARCHAR) AS {qident(short)}")
            else:
                select.append(f"TRY_CAST({qident(col)} AS DOUBLE) AS {qident(short)}")
        markets = list(recipe.get("markets") or [])
        types = list(recipe.get("types") or [])
        floor = float(recipe.get("min_market_cap_usd") or 0)
        keep = ["TRY_CAST(market_cap_basic AS DOUBLE) >= ?"]
        params: list[Any] = [run_id, floor]
        if markets:
            keep.append(f"market IN ({', '.join('?' for _ in markets)})")
            params.extend(markets)
        if types:
            keep.append(f"type IN ({', '.join('?' for _ in types)})")
            params.extend(types)
        where = "(" + " AND ".join(keep) + ")"
        if ids:
            where += f" OR symbol IN ({', '.join('?' for _ in ids)})"
            params.extend(ids)
        cur = con.execute(f"SELECT {', '.join(select)} FROM all_fields_rows WHERE run_id = ? AND ({where})", params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, values)) for values in cur.fetchall()]
    finally:
        con.close()


def derive_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Row-local derivations (no peers, no history)."""
    rec = dict(row)
    close = _f(rec.get("close"))
    rec["vs50"] = _pct(close, rec.get("sma50"))
    rec["vs200"] = _pct(close, rec.get("sma200"))
    lo, hi = _f(rec.get("lo52")), _f(rec.get("hi52"))
    rec["rng"] = round((close - lo) / (hi - lo) * 100.0, 1) if close is not None and lo is not None and hi and hi > lo else None
    # Outside the US price_target_average (and low / high) is in USD while price_target_1y is in the quote currency
    pt_1y, pt_avg = _f(rec.get("pt_1y")), _f(rec.get("pt_avg"))
    fx = pt_1y / pt_avg if pt_1y and pt_avg and pt_avg > 0 else 1.0
    rec["pt"] = _r(pt_1y if pt_1y else pt_avg)
    for key in ("pt_high", "pt_low"):
        rec[key] = _r(_f(rec.get(key)) * fx) if _f(rec.get(key)) is not None else None
    rec["pt_up"] = _pct(rec.get("pt"), close)
    rec["pt_high_up"] = _pct(rec.get("pt_high"), close)
    rec["pt_low_up"] = _pct(rec.get("pt_low"), close)
    rec["rev_surprise"] = rev_surprise_pct(rec.get("rev_last_q"), rec.get("rev_est_last_q"), rec.get("rev_surprise_tv"))
    rec["surprise_mix"] = surprise_mix(rec.get("last_surprise"), rec["rev_surprise"])
    rec["eps_q_step"] = _pct(rec.get("eps_next_q"), rec.get("eps_last_q"))
    rec["rev_q_step"] = _pct(rec.get("rev_next_q"), rec.get("rev_last_q"))
    rec["eps_nfy_g"] = _pct(rec.get("eps_nfy"), rec.get("eps_ttm"))
    rec["rev_nfy_g"] = _pct(rec.get("rev_nfy"), rec.get("rev_ttm"))
    # P/E on next-FY EPS from TradingView's own P/E: currency-consistent when a listing quotes in another currency
    pe, eps_ttm, eps_nfy = _f(rec.get("pe")), _f(rec.get("eps_ttm")), _f(rec.get("eps_nfy"))
    if pe and pe > 0 and eps_ttm and eps_ttm > 0 and eps_nfy and eps_nfy > 0:
        rec["pe_nfy"] = round(pe * eps_ttm / eps_nfy, 2)
    else:
        tv = _f(rec.get("pe_fwd_tv"))
        rec["pe_nfy"] = round(tv, 2) if tv and tv > 0 else None
    return rec


def place_on_calendar(row: Mapping[str, Any], *, start: date, end: date, scan_at: datetime | None,
                      amc_from_hour_utc: int = 13) -> dict[str, Any] | None:
    """Calendar columns when the next (or a just-reported) print falls in [start, end); else None."""
    nxt = epoch_dt(row.get("earn_next"))
    last = epoch_dt(row.get("earn_last"))
    if nxt is not None and start <= nxt.date() < end:
        at, code = nxt, row.get("earn_next_time")
    elif last is not None and start <= last.date() < end:
        at, code = last, row.get("earn_last_time")
    else:
        return None
    slot = slot_label(code, at, amc_from_hour_utc)
    scan_day = scan_at.date() if scan_at else None
    return {
        "week": f"W{(at.date() - start).days // 7}",
        "earn_date": at.date().isoformat(),
        "earn_dow": WEEKDAYS[at.weekday()],
        "earn_utc": at.strftime("%Y-%m-%d %H:%M"),
        "slot": slot,
        "react_on": react_on(at, slot).isoformat(),
        "printed": bool(scan_at and at <= scan_at),
        "dte": (at.date() - scan_day).days if scan_day else None,
    }


# --- history -----------------------------------------------------------------------------------------------


def _day(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def past_prints(points: Sequence[Mapping[str, Any]], before: date, *, min_days_apart: int = 20) -> list[tuple[datetime, Any]]:
    """Distinct earlier print dates seen in ``earnings_release_date``, oldest-first.

    One per UTC date (newest time code kept); dates closer than ``min_days_apart`` are one print whose
    date TradingView moved, and the later date is kept.
    """
    seen: dict[date, tuple[datetime, Any]] = {}
    for point in points:
        at = epoch_dt(point.get("earnings_release_date"))
        if at is None or at.date() >= before:
            continue
        seen[at.date()] = (at, point.get("earnings_release_time"))
    out: list[tuple[datetime, Any]] = []
    for day in sorted(seen):
        if out and (day - out[-1][0].date()).days < min_days_apart:
            out[-1] = seen[day]
        else:
            out.append(seen[day])
    return out


def _base_index(days: Sequence[date], i: int, back: int, tolerance: int) -> int | None:
    from bisect import bisect_right

    target = days[i] - timedelta(days=back)
    j = bisect_right(days, target, 0, i) - 1
    if j < 0 or (target - days[j]).days > tolerance:
        return None
    return j


def print_reactions(points: Sequence[Mapping[str, Any]], prints: Sequence[tuple[datetime, Any]],
                    recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per past print: reaction file, day move, relative volume, run-up before, drift after, surprises."""
    spec = dict(recipe.get("reaction") or {})
    max_lag = int(spec.get("max_lag_days", 4))
    max_gap = int(spec.get("max_gap_days", 5))
    drift_files = int(spec.get("drift_files", 4))
    runup_days = int(spec.get("runup_days", 14))
    tolerance = int(spec.get("base_tolerance_days", 6))
    amc_hour = int((recipe.get("slot") or {}).get("amc_from_hour_utc", 13))
    days = [_day(p.get("as_of")) for p in points]
    out: list[dict[str, Any]] = []
    for at, code in prints:
        slot = slot_label(code, at, amc_hour)
        on = react_on(at, slot)
        idx = next((k for k, d in enumerate(days) if d and d >= on), None)
        rec: dict[str, Any] = {"print_date": at.date().isoformat(), "slot": slot, "react_on": on.isoformat(),
                               "react_file": None, "react": None, "react_px": None, "react_relvol": None,
                               "runup": None, "drift": None, "eps_surprise": None, "rev_surprise": None,
                               "pre_file": None, "pre_close": None, "react_close": None}
        if idx is None or idx == 0 or (days[idx] - on).days > max_lag or (days[idx] - days[idx - 1]).days > max_gap:
            rec["note"] = "no reaction file"
            out.append(rec)
            continue
        pre, hit = points[idx - 1], points[idx]
        rec["react_file"] = days[idx].isoformat()
        rec["pre_file"] = days[idx - 1].isoformat()
        rec["pre_close"] = _r(pre.get("close"), 4)
        rec["react_close"] = _r(hit.get("close"), 4)
        rec["react_px"] = _pct(hit.get("close"), pre.get("close"))
        # a file gap puts the reaction session between files: that file's change is a later day, the closes span the move
        rec["react"] = _r(hit.get("change")) if days[idx] == on else rec["react_px"]
        if days[idx] != on:
            rec["note"] = "file gap: react from closes"
        rec["react_relvol"] = _r(hit.get("relative_volume_10d_calc"))
        base = _base_index(days, idx - 1, runup_days, tolerance)
        rec["runup"] = _pct(pre.get("close"), points[base].get("close")) if base is not None else None
        if idx + drift_files < len(points):
            rec["drift"] = _pct(points[idx + drift_files].get("close"), hit.get("close"))
        before = next((points[k] for k in range(idx - 1, -1, -1) if days[k] and days[k] < at.date()), None)
        for later in points[idx:idx + max_lag + 1]:
            seen = epoch_dt(later.get("earnings_release_date"))
            if seen is None or seen.date() != at.date():
                continue
            if rec["eps_surprise"] is None:
                rec["eps_surprise"] = _r(later.get("eps_surprise_percent_fq"))
            if revenue_posted(before, later.get("total_revenue_fq")) is False:
                # still last quarter's actual: TradingView's own figure until a later file posts the new one
                rec["rev_surprise"] = _r(later.get("revenue_surprise_percent_fq"))
                continue
            rec["rev_surprise"] = rev_surprise_pct(later.get("total_revenue_fq"), later.get("revenue_forecast_fq"),
                                                   later.get("revenue_surprise_percent_fq"))
            break
        out.append(rec)
    return out


def attach_revenue_posted(rec: dict[str, Any], points: Sequence[Mapping[str, Any]]) -> None:
    """``rev_posted`` on a live row; when it is False the derived revenue surprise (and the quarter step on the
    stale actual) is replaced by TradingView's own figure, usually empty, and the mix is recomputed."""
    at = epoch_dt(rec.get("earn_last"))
    before = None
    if at is not None:
        before = next((p for p in reversed(points) if (_day(p.get("as_of")) or at.date()) < at.date()), None)
    posted = revenue_posted(before, rec.get("rev_last_q"))
    rec["rev_posted"] = posted
    if posted is False:
        rec["rev_surprise"] = _r(rec.get("rev_surprise_tv"))
        rec["surprise_mix"] = surprise_mix(rec.get("last_surprise"), rec["rev_surprise"])
        rec["rev_q_step"] = None


def revenue_posted(before: Mapping[str, Any] | None, actual: Any) -> bool | None:
    """False when ``actual`` (total_revenue_fq after the print) is still the figure of ``before``, the last day file
    before the print date. TradingView rolls revenue_forecast_fq to the new quarter before it posts the new actual,
    so actual / forecast would set last quarter's revenue against this quarter's estimate. None when unknown."""
    old, new = _f((before or {}).get("total_revenue_fq")), _f(actual)
    if old is None or new is None or old <= 0:
        return None
    return abs(new - old) > 1e-9 * old


def _fmt_signed(value: Any, digits: int = 1) -> str:
    number = _f(value)
    return "-" if number is None else f"{number:+.{digits}f}"


def history_summary(points: Sequence[Mapping[str, Any]], reactions: Sequence[Mapping[str, Any]],
                    recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Per-name history reads: past-print summary, run-up now, estimate and target revisions, own forward P/E seat."""
    from generic_utils.gmv_lens import _revision

    spec = dict(recipe.get("reaction") or {})
    tolerance = int(spec.get("base_tolerance_days", 6))
    rev_days = dict(recipe.get("revision_days") or {})
    step_max = float(recipe.get("revision_step_max_pct", 25.0))
    out: dict[str, Any] = {"hist_n": len(points)}
    done = [r for r in reactions if r.get("react") is not None]
    out["n_prints"] = len(done)
    if done:
        last = done[-1]
        out["react_last"] = last["react"]
        out["runup_last"] = last.get("runup")
        out["drift_last"] = last.get("drift")
        out["surprise_last"] = last.get("eps_surprise")
        out["react_abs_mean"] = round(sum(abs(r["react"]) for r in done) / len(done), 2)
        out["react_up"] = f"{sum(1 for r in done if r['react'] > 0)}/{len(done)}"
        out["react_hist"] = " / ".join(
            f"{r['print_date'][5:]} {r['slot']} {_fmt_signed(r['react'])}% (s {_fmt_signed(r.get('eps_surprise'), 0)})"
            for r in reversed(done))
    if not points:
        return out
    days = [_day(p.get("as_of")) for p in points]
    i = len(points) - 1
    base = _base_index(days, i, int(spec.get("runup_days", 14)), tolerance)
    out["runup"] = _pct(points[i].get("close"), points[base].get("close")) if base is not None else None
    if done and done[-1].get("react_file"):
        k = next((j for j, d in enumerate(days) if d and d.isoformat() == done[-1]["react_file"]), None)
        out["since_print"] = _pct(points[i].get("close"), points[k].get("close")) if k is not None else None
    for key, field in (("eps_rev_1m", "earnings_per_share_forecast_next_fy"), ("eps_rev_3m", "earnings_per_share_forecast_next_fy")):
        back = int(rev_days.get(key, 30 if key.endswith("1m") else 91))
        start = days[i] - timedelta(days=back)
        values = [_f(p.get(field)) for p, d in zip(points, days) if d and d >= start]
        growth, _breadth, _skips = _revision(values, step_max)
        out[key] = _r(growth)
    back = int(rev_days.get("pt_drift_1m", 30))
    base_pt = _base_index(days, i, back, tolerance)
    pt_field = "price_target_1y" if _f(points[i].get("price_target_1y")) else "price_target_average"
    out["pt_drift_1m"] = _pct(points[i].get(pt_field), points[base_pt].get(pt_field)) if base_pt is not None else None
    pes = []
    for p in points:
        c, e = _f(p.get("close")), _f(p.get("earnings_per_share_forecast_next_fy"))
        pes.append(c / e if c and e and e > 0 else None)
    clean = [v for v in pes if v is not None]
    now = pes[-1]
    if now is not None and len(clean) >= 10:
        med = median(clean)
        out["pe_nfy_own_med"] = round(med, 2)
        out["pe_nfy_vs_own"] = _pct(now, med)
        out["pe_nfy_seat"] = round(sum(1 for v in clean if v < now) / len(clean) * 100.0)
    return out


def load_history(symbols: Sequence[str], recipe: Mapping[str, Any], *, root: str | Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """{symbol: day-file points oldest-first} for exact ids (newest run per file), per-share fields split-adjusted by gmv_lens."""
    from generic_utils.gmv_lens import split_adjust
    from generic_utils.panel import load_panel, series_by_id
    from generic_utils.series import list_dated_files

    ids = [s for s in symbols if s]
    if not ids:
        return {}
    sources = list_dated_files(Path(root) if root else ALL_FIELDS_ROOT, "**/tradingview_all_fields_*.duckdb",
                               newest=int(recipe.get("history_newest") or 200))
    panel = load_panel(sources, columns=list(HISTORY_FIELDS), numeric=[f for f in HISTORY_FIELDS if f not in HISTORY_TEXT],
                       where_sql=f"symbol IN ({', '.join('?' for _ in ids)})", params=ids, skip_without_runs=True)
    guard = recipe.get("split_guard") or {}
    out: dict[str, list[dict[str, Any]]] = {}
    for symbol, points in series_by_id(panel["rows"]).items():
        adjusted, events = split_adjust(points, guard)
        for point in adjusted:
            point["_splits"] = events
        out[symbol] = adjusted
    return out


# --- joins -------------------------------------------------------------------------------------------------


def _resolve(path: str, pack: str | Path | None) -> Path | None:
    if "{PACK}" in path:
        if not pack:
            return None
        path = path.replace("{PACK}", Path(pack).as_posix())
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def _newest_glob(pattern: str) -> Path | None:
    matches = [p for p in REPO_ROOT.glob(pattern) if p.is_file()]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def load_join(spec: Mapping[str, Any], *, pack: str | Path | None = None, ids: Iterable[str] = ()) -> tuple[dict[str, dict[str, Any]], str | None]:
    """{symbol: {dest: value}} for one join spec, and the file it read (None when absent)."""
    import duckdb

    if spec.get("glob"):
        path = _newest_glob(str(spec["glob"]))
    else:
        path = _resolve(str(spec.get("duckdb") or spec.get("csv") or ""), pack)
    if path is None or not path.exists():
        return {}, None
    id_field = str(spec.get("id_field") or "symbol")
    fields = dict(spec.get("fields") or {})
    concat = spec.get("concat") or None
    con = duckdb.connect(str(path), read_only=True) if path.suffix == ".duckdb" else duckdb.connect()
    try:
        source = qident(str(spec.get("table"))) if path.suffix == ".duckdb" else f"read_csv_auto('{path.as_posix()}', all_varchar=true)"
        cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
        if id_field not in cols:
            return {}, path.as_posix()
        wanted = list(fields) if fields else []
        if concat:
            wanted = sorted({part.split("}")[0] for part in str(concat["format"]).split("{")[1:]})
        select = [qident(id_field)] + [qident(c) for c in wanted if c in cols]
        where = []
        if spec.get("where"):
            where.append(str(spec["where"]))
        if spec.get("newest_run") and "run_id" in cols:
            where.append(f"run_id = (SELECT max(run_id) FROM {source})")
        sql = f"SELECT {', '.join(select)} FROM {source}" + (f" WHERE {' AND '.join(where)}" if where else "")
        cur = con.execute(sql)
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, values)) for values in cur.fetchall()]
    finally:
        con.close()
    out: dict[str, dict[str, Any]] = {}
    if concat:
        grouped: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            key = str(row.get(id_field) or "")
            try:
                grouped[key].append(str(concat["format"]).format(**row))
            except (KeyError, ValueError):
                continue
        return {k: {concat["dest"]: "; ".join(v)} for k, v in grouped.items()}, path.as_posix()
    for row in rows:
        key = str(row.get(id_field) or "")
        if key and key not in out:
            out[key] = {dest: _cell(row.get(src)) for src, dest in fields.items() if src in row}
    return out, path.as_posix()


def _cell(value: Any) -> Any:
    """A CSV join reads every cell as text: numbers come back as numbers, labels stay text."""
    if isinstance(value, str):
        number = _f(value)
        return number if number is not None else (value or None)
    return value


def attach_joins(rows: list[dict[str, Any]], joins: Sequence[Mapping[str, Any]], *, pack: str | Path | None = None) -> dict[str, Any]:
    """Join every spec onto ``rows`` in place by exact symbol. A later join fills only empty cells. Returns {name: path|None}."""
    used: dict[str, Any] = {}
    for spec in joins:
        index, path = load_join(spec, pack=pack)
        used[str(spec.get("name"))] = {"path": path, "n": len(index)}
        for row in rows:
            match = index.get(str(row.get("symbol")))
            if not match:
                continue
            for dest, value in match.items():
                if row.get(dest) in (None, ""):
                    row[dest] = value
    return used


# --- build -------------------------------------------------------------------------------------------------


SUMMARY_MEDIANS: tuple[str, ...] = ("day", "w", "runup", "pt_up", "pe_nfy_vs_ind", "pe_nfy_vs_own", "eps_rev_3m", "react_abs_mean")


def summarize(rows: Sequence[Mapping[str, Any]], keys: Sequence[str], *, names_per_group: int = 8,
              medians: Sequence[str] = SUMMARY_MEDIANS) -> list[dict[str, Any]]:
    """One row per ``keys`` group: names, $10B+ names, the median of each ``medians`` field, the largest names."""
    groups: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row.get(k)) for k in keys)].append(row)
    out = []
    for key, items in sorted(groups.items()):
        rec: dict[str, Any] = dict(zip(keys, key))
        rec["n"] = len(items)
        rec["n_10b"] = sum(1 for r in items if (_f(r.get("mcap")) or 0) >= 1e10)
        for field in medians:
            clean = [v for v in (_f(r.get(field)) for r in items) if v is not None]
            rec[f"med_{field}"] = round(median(clean), 2) if clean else None
        big = sorted(items, key=lambda r: -(_f(r.get("mcap")) or 0))
        rec["largest"] = ", ".join(str(r.get("symbol")) for r in big[:names_per_group])
        out.append(rec)
    return out


def build_earnings_week(
    *,
    database: str | Path | None = None,
    scan_day: date | None = None,
    pack: str | Path | None = None,
    ids: Sequence[str] = (),
    recipe: Mapping[str, Any] | None = None,
    history_root: str | Path | None = None,
) -> dict[str, Any]:
    """Tables ``calendar`` (one row per name), ``prints`` (one row per past print), ``by_day`` and ``by_industry``."""
    from generic_utils.derive import attach_vs_group

    recipe = dict(recipe or load_recipe())
    database = Path(database) if database else latest_day_file(history_root)
    run_id, scan_at = newest_run(database)
    scan_day = scan_day or (scan_at.date() if scan_at else date.today())
    start, end = week_window(scan_day, int(recipe.get("weeks") or 2))
    amc_hour = int((recipe.get("slot") or {}).get("amc_from_hour_utc", 13))

    from generic_utils.symbol_identity import ticker_map

    qualified = [i for i in ids if ":" in i]
    universe = [derive_row(r) for r in universe_rows(database, run_id, recipe, qualified)]
    by_ticker = ticker_map(r["symbol"] for r in universe)
    ids = qualified + [by_ticker.get(i.upper(), i) for i in ids if ":" not in i]
    peer = dict(recipe.get("peer") or {})
    universe = attach_vs_group(universe, group_field=str(peer.get("group_field", "ind")),
                               fields=list(peer.get("fields") or ["pe_nfy"]), min_n=int(peer.get("min_n", 6)))
    named = set(ids)
    rows: list[dict[str, Any]] = []
    missing = [i for i in ids if i not in {r["symbol"] for r in universe}]
    for row in universe:
        slot = place_on_calendar(row, start=start, end=end, scan_at=scan_at, amc_from_hour_utc=amc_hour)
        if slot is None and row["symbol"] not in named:
            continue
        rec = dict(row)
        rec.update(slot or {"week": "OUT"})
        rows.append(rec)

    history = load_history([r["symbol"] for r in rows], recipe, root=history_root)
    prints_rows: list[dict[str, Any]] = []
    for rec in rows:
        points = history.get(rec["symbol"]) or []
        reactions = print_reactions(points, past_prints(points, start, min_days_apart=int(
            (recipe.get("reaction") or {}).get("min_days_between_prints", 20))), recipe)
        for reaction in reactions:
            prints_rows.append({"symbol": rec["symbol"], "ind": rec.get("ind"), **reaction})
        summary = history_summary(points, reactions, recipe)
        summary["splits"] = points[-1].get("_splits") if points else None
        for key, value in summary.items():
            rec[key] = value
        attach_revenue_posted(rec, points)

    joins_used = attach_joins(rows, list(recipe.get("joins") or []), pack=pack)
    join_cols: list[str] = []
    for spec in recipe.get("joins") or []:
        dests = list((spec.get("fields") or {}).values()) or ([spec["concat"]["dest"]] if spec.get("concat") else [])
        join_cols.extend(d for d in dests if d not in join_cols)
    columns = list(CALENDAR_COLUMNS) + [c for c in join_cols if c not in CALENDAR_COLUMNS]
    calendar = [{c: rec.get(c) for c in columns} for rec in rows]
    calendar.sort(key=lambda r: (str(r.get("earn_utc") or "9"), -(_f(r.get("mcap")) or 0)))
    for rec in calendar:
        rec["named"] = rec["symbol"] in named
    return {
        "calendar": calendar,
        "prints": prints_rows,
        "by_day": summarize([r for r in calendar if r.get("week") in {"W0", "W1"}], ("week", "earn_date", "earn_dow", "slot")),
        "by_industry": summarize([r for r in calendar if r.get("week") in {"W0", "W1"}], ("week", "ind")),
        "spec": {"database": Path(database).as_posix(), "run_id": run_id,
                 "scan_at_utc": scan_at.isoformat() if scan_at else None, "week_start": start.isoformat(),
                 "week_end": end.isoformat(), "n": len(calendar), "n_prints": len(prints_rows),
                 "missing_ids": missing, "joins": joins_used},
    }


def write_earnings_week(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"scan {spec['run_id']} at {spec['scan_at_utc']}; window {spec['week_start']} to {spec['week_end']} (W0 this week, W1 next).",
        "calendar: one row per name. slot BMO/AMC is TradingView's time code; BMO~/AMC~ is inferred from the UTC hour. react_on is the day the move shows.",
        "printed true means the print time is before the scan; that row's tape already holds the reaction.",
        "pe_nfy = TradingView P/E x EPS ttm / next-FY EPS (currency-consistent); pe_nfy_vs_ind is percent versus the industry median (negative is cheaper).",
        "pe_nfy_vs_own and pe_nfy_seat compare close / next-FY EPS with the name's own day-file history (seat 0-100, 0 = cheapest of the window).",
        "prints: one row per past print. react is the reaction file's change (percent); runup is into the print; drift is after it.",
        "Joined columns keep their source names (pack us2b_tape, GMV lens_tape, prediction earnings_priority_consensus_rows, edge unified). Empty when the name is outside that suite.",
        "No composite and no course. Order by one column at a time.",
    ]
    return write_run_export(
        out_dir, tool="generic_utils.earnings_week",
        tables={k: payload[k] for k in ("calendar", "prints", "by_day", "by_industry")},
        sources={"database": spec["database"], "run_id": spec["run_id"],
                 **{f"join_{k}": (v or {}).get("path") for k, v in (spec.get("joins") or {}).items()}},
        notes=notes, extra={"spec": {k: v for k, v in spec.items() if k != "joins"}},
        duckdb_name="earnings_week.duckdb",
    )
