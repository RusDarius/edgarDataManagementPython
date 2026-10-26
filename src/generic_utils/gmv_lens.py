"""Growth / estimates / momentum / participation / value lenses on the all-fields day history.

Five lenses stay five columns, and every part behind a lens is its own column:

* G growth: street forward revenue and EPS growth, trailing revenue and EPS growth
* E estimates: next-FY EPS and revenue revisions, revision breadth, price-target drift, last surprise
* M momentum: 3-month return, trend quality (signed R^2), volatility-adjusted return, distance from the high
* P participation: up-day versus down-day volume, accumulation, volume trend
* V value: forward PE and EV/EBITDA versus industry, forward PEG, price-target room

A lens percentile is the median percentile of its present parts. Lenses are
never added together. A profile orders names by its weakest lens (an order
statistic, no weights), so no name is cut by a threshold: a weak lens only
lowers its place. Volatility is a denominator (risk-adjusted return) and an
envelope size, never a lens or a rank of its own.

The same feature code runs on the latest day and on every past evaluation day,
so ``lens_evidence`` is a walk-forward check of each part, lens, and profile
against later closes on this project's own history. It is not a hit rate.

Knobs live in config/generic_utils/gmv_lens.json. Labels (tape_state) use the
recipe's bands; nothing here chooses a buy.

Run it with ``python src/run_gmv_lens.py`` (``--full-flow`` produces the inputs first;
``--page`` / ``--print-prompt`` / ``--agent`` add the canvas and prompt mode; the runner's
docstring lists every form). The daily refresh runs it as suite ``gmv`` into PACK/gmv.

Pipeline (each step is a function here):

    panel.load_panel -> panel.series_by_id      day files -> {symbol: points oldest-first}
    prepare_series                              split guard, day index per name
    day_universe -> point_features              every lens part as of one day (no look-ahead)
    score_cross_section                         industry percents, part / lens percentiles,
                                                profile floors, tape_state
    join_pack (runner)                          pack us2b_tape overlay + Book flag (support only)
    select_profiles                             top N per profile after the industry cap,
                                                overflow, Focus round-robin
    lens_evidence                               the same steps on past days vs later closes

Output tables (gmv_lens.duckdb): focus, profile_top (profile, rank, floor), profile_overflow,
book_lens, lens_tape (the whole universe with every part), evidence (one row per part / lens /
profile and horizon), evidence_days (per evaluation day). Column meanings are Ledger entries
(group "GMV lens" in config/generic_utils/canvas_ledger.json) and the page is
config/generic_utils/pages/gmv.json.

Coverage: profiles x ``top`` names (about 10-12 each, ``industry_cap`` per industry), Focus up
to ``focus_max`` (50); the cap's overflow and the whole universe stay in the DuckDB. A part that
is missing leaves the lens on its other parts (``min_parts``); a name without a lens is not
ordered in the profiles that need it, and still sits in lens_tape.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from pathlib import Path
from statistics import median, pstdev
from typing import Any, Iterable, Mapping, Sequence

from generic_utils.derive import attach_vs_group
from generic_utils.ranking import group_capped_top_n, to_float
from generic_utils.run_export import load_json_recipe

_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = _PROJECT / "config" / "generic_utils" / "gmv_lens.json"

_f = to_float

PANEL_TEXT: tuple[str, ...] = ("symbol", "description", "industry", "sector", "type", "earnings_release_next_date")
PANEL_NUMERIC: tuple[str, ...] = (
    "close",
    "change",
    "relative_volume_10d_calc",
    "average_volume_10d_calc",
    "average_volume_30d_calc",
    "market_cap_basic",
    "total_revenue_ttm",
    "total_revenue_yoy_growth_ttm",
    "earnings_per_share_diluted_ttm",
    "earnings_per_share_diluted_yoy_growth_ttm",
    "earnings_per_share_forecast_next_fy",
    "revenue_forecast_next_fy",
    "eps_surprise_percent_fq",
    "price_target_average",
    "enterprise_value_ebitda_ttm",
    "ATRP",
)
# Per-share prints move with a split; volume ratios and revenue do not.
PER_SHARE: tuple[str, ...] = ("close", "price_target_average", "earnings_per_share_forecast_next_fy", "earnings_per_share_diluted_ttm")


def load_gmv_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_RECIPE
    payload = load_json_recipe(recipe_path) if recipe_path.exists() else {}
    payload.setdefault("name", "gmv_lens")
    payload.setdefault("min_market_cap_usd", 1_000_000_000)
    payload.setdefault("history_mcap_frac", 0.5)
    payload.setdefault("exchanges", ["NASDAQ", "NYSE", "AMEX"])
    payload.setdefault("types", ["stock", "dr"])
    payload.setdefault("group_field", "ind")
    payload.setdefault("peer_min_n", 6)
    payload.setdefault("windows", {})
    payload.setdefault("split_guard", {})
    payload.setdefault("revision_step_max_pct", 25.0)
    payload.setdefault("growth_quote_max_pct", 300.0)
    payload.setdefault("min_parts", 2)
    payload.setdefault("lenses", {})
    payload.setdefault("profiles", {})
    payload.setdefault("industry_cap", 2)
    payload.setdefault("focus_max", 50)
    payload.setdefault("tape_state", {})
    payload.setdefault("evidence", {})
    return payload


def panel_where(recipe: Mapping[str, Any]) -> tuple[str, list[Any]]:
    """WHERE clause and params for ``panel.load_panel``: listing venue, type, and the history cap floor."""
    exchanges = list(recipe.get("exchanges") or [])
    types = list(recipe.get("types") or [])
    floor = float(recipe.get("min_market_cap_usd") or 0) * float(recipe.get("history_mcap_frac") or 1.0)
    clauses = ["market_cap_basic >= ?"]
    params: list[Any] = [floor]
    if exchanges:
        clauses.append(f"split_part(symbol, ':', 1) IN ({', '.join('?' for _ in exchanges)})")
        params += exchanges
    if types:
        clauses.append(f"(type IS NULL OR type IN ({', '.join('?' for _ in types)}))")
        params += types
    return " AND ".join(clauses), params


# ---------------------------------------------------------------- series helpers


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _r(value: Any, digits: int = 2) -> float | None:
    number = _f(value)
    return None if number is None or math.isnan(number) else round(number, digits)


def split_adjust(points: Sequence[Mapping[str, Any]], guard: Mapping[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]:
    """Rescale per-share prints before a split-like jump. The newest print is the reference.

    A jump counts as a split when consecutive snapshots are at most ``max_gap_days``
    apart, the close ratio is at least ``jump_ratio`` (either way), and the later
    day's own ``change`` is under ``day_change_max_pct`` (a real crash shows in change).
    Returns (points, number of split events).
    """
    spec = dict(guard or {})
    max_gap = float(spec.get("max_gap_days", 5))
    jump = float(spec.get("jump_ratio", 1.8))
    day_max = float(spec.get("day_change_max_pct", 25.0))
    out = [dict(point) for point in points]
    raw = [_f(point.get("close")) for point in points]
    days = [_day(point.get("as_of")) for point in points]
    scale = 1.0
    events = 0
    for i in range(len(out) - 2, -1, -1):
        a, b = raw[i], raw[i + 1]
        if a and b and a > 0 and b > 0 and days[i] and days[i + 1]:
            ratio = a / b
            gap = (days[i + 1] - days[i]).days
            change = abs(_f(points[i + 1].get("change")) or 0.0)
            if gap <= max_gap and (ratio >= jump or ratio <= 1.0 / jump) and change < day_max:
                scale /= ratio
                events += 1
        if scale != 1.0:
            for key in PER_SHARE:
                value = _f(out[i].get(key))
                if value is not None:
                    out[i][key] = value * scale
    return out, events


def _base_index(days: Sequence[date], i: int, back: int, tolerance: int) -> int | None:
    """Index of the last snapshot on or before ``days[i] - back``, if it is within ``tolerance`` days of that target."""
    target = days[i] - timedelta(days=back)
    j = bisect_right(days, target, 0, i) - 1
    if j < 0 or (target - days[j]).days > tolerance:
        return None
    return j


def _window(days: Sequence[date], i: int, back: int) -> range:
    """Indexes of snapshots in (days[i] - back, days[i]]."""
    start = bisect_right(days, days[i] - timedelta(days=back), 0, i + 1)
    return range(start, i + 1)


def _ratio_pct(now: Any, then: Any) -> float | None:
    a, b = _f(now), _f(then)
    if a is None or b is None or b <= 0 or a <= 0:
        return None
    return (a / b - 1.0) * 100.0


def _trend(days: Sequence[date], closes: Sequence[float | None], idx: Iterable[int]) -> tuple[float | None, float | None]:
    """OLS of ln(close) on calendar day. Returns (annualized slope %, R^2)."""
    xs: list[float] = []
    ys: list[float] = []
    for k in idx:
        value = closes[k]
        if value is not None and value > 0:
            xs.append(float(days[k].toordinal()))
            ys.append(math.log(value))
    if len(xs) < 3:
        return None, None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0:
        return None, None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    r2 = 0.0 if syy <= 0 else (slope * slope * sxx) / syy
    return (math.exp(slope * 365.0) - 1.0) * 100.0, r2


def _revision(values: Sequence[float | None], step_max: float) -> tuple[float | None, float | None, int]:
    """Compounded step change, breadth (up - down) / moved, and the number of steps skipped as a roll.

    A single step beyond ``step_max`` percent is skipped: a next-FY estimate that
    rolls to the following year jumps in one print, which is not a revision.
    """
    growth = 1.0
    up = down = skipped = steps = 0
    previous = None
    for value in values:
        if value is None:
            continue
        if previous is not None and previous != 0:
            step = (value - previous) / abs(previous) * 100.0
            if abs(step) > step_max:
                skipped += 1
            else:
                steps += 1
                growth *= 1.0 + step / 100.0
                if step > 0:
                    up += 1
                elif step < 0:
                    down += 1
        previous = value
    if steps == 0:
        return None, None, skipped
    moved = up + down
    return (growth - 1.0) * 100.0, ((up - down) / moved if moved else 0.0), skipped


def point_features(points: Sequence[Mapping[str, Any]], i: int, recipe: Mapping[str, Any], days: Sequence[date] | None = None) -> dict[str, Any]:
    """Every lens part for one name as of ``points[i]`` (split-adjusted points, oldest-first)."""
    win = dict(recipe.get("windows") or {})
    short = int(win.get("short_days", 30))
    trend_days = int(win.get("trend_days", 91))
    vol_days = int(win.get("volume_days", 30))
    tol = int(win.get("base_tolerance_days", 10))
    trend_min = int(win.get("trend_min_points", 20))
    vol_min = int(win.get("volume_min_points", 8))
    days = days or [_day(p.get("as_of")) for p in points]
    point = points[i]
    closes = [_f(p.get("close")) for p in points]
    close = closes[i]
    rec: dict[str, Any] = {
        "symbol": point.get("symbol"),
        "company": point.get("description") or "",
        "ind": point.get("industry") or "",
        "sector": point.get("sector") or "",
        "as_of": str(point.get("as_of"))[:10],
        "close": _r(close, 2),
        "mcap": _r(point.get("market_cap_basic"), 0),
        "atrp": _r(point.get("ATRP"), 2),
        "earn": point.get("earnings_release_next_date"),
    }
    # Growth (G)
    rev_ttm = _f(point.get("total_revenue_ttm"))
    eps_ttm = _f(point.get("earnings_per_share_diluted_ttm"))
    eps_fc = _f(point.get("earnings_per_share_forecast_next_fy"))
    # A growth print beyond growth_quote_max_pct is a base effect (a tiny prior year), not a rate to rank:
    # the part stays empty and base_effect names it (same rule as the growth radar).
    quote_max = float(recipe.get("growth_quote_max_pct") or 300.0)
    base_effect: list[str] = []
    for key, value in (
        ("fwd_rev_g", _ratio_pct(point.get("revenue_forecast_next_fy"), rev_ttm)),
        ("fwd_eps_g", _ratio_pct(eps_fc, eps_ttm)),
        ("rev_yoy", _f(point.get("total_revenue_yoy_growth_ttm"))),
        ("earn_yoy", _f(point.get("earnings_per_share_diluted_yoy_growth_ttm"))),
    ):
        if value is not None and abs(value) > quote_max:
            base_effect.append(key)
            value = None
        rec[key] = _r(value, 1)
    rec["base_effect"] = ",".join(base_effect)
    # Momentum (M)
    b_short = _base_index(days, i, short, tol)
    b_trend = _base_index(days, i, trend_days, tol)
    rec["r_1m"] = _r(_ratio_pct(close, closes[b_short]) if b_short is not None else None, 1)
    rec["r_3m"] = _r(_ratio_pct(close, closes[b_trend]) if b_trend is not None else None, 1)
    t_idx = _window(days, i, trend_days)
    rec["trend_n"] = len(t_idx)
    slope = r2 = None
    if len(t_idx) >= trend_min:
        slope, r2 = _trend(days, closes, t_idx)
    rec["trend_slope_ann"] = _r(slope, 1)
    rec["trend_r2"] = _r(r2, 3)
    rec["trend_q"] = None if slope is None or r2 is None else round(math.copysign(r2, slope), 3)
    changes = [_f(points[k].get("change")) for k in t_idx]
    changes = [c for c in changes if c is not None]
    vol_d = pstdev(changes) if len(changes) >= trend_min else None
    rec["vol_d"] = _r(vol_d, 2)
    rec["r_3m_per_vol"] = (
        _r(rec["r_3m"] / (vol_d * math.sqrt(63.0)), 2) if rec["r_3m"] is not None and vol_d else None
    )
    window_closes = [closes[k] for k in t_idx if closes[k] is not None]
    rec["hi_prox"] = _r(_ratio_pct(close, max(window_closes)) if window_closes and len(t_idx) >= trend_min else None, 1)
    atrp = _f(point.get("ATRP"))
    rec["exp_move_20d"] = _r(atrp * math.sqrt(20.0), 1) if atrp is not None else None
    # Participation (P)
    v_idx = _window(days, i, vol_days)
    up_w = down_w = 0.0
    seen = 0
    for k in v_idx:
        change = _f(points[k].get("change"))
        weight = _f(points[k].get("relative_volume_10d_calc"))
        if change is None or weight is None or weight <= 0:
            continue
        seen += 1
        if change > 0:
            up_w += weight
        elif change < 0:
            down_w += weight
    rec["updown_vol"] = _r(up_w / max(down_w, 0.1), 2) if seen >= vol_min else None
    signed = total = 0.0
    for k in t_idx:
        change = _f(points[k].get("change"))
        weight = _f(points[k].get("relative_volume_10d_calc"))
        if change is None or weight is None or weight <= 0:
            continue
        total += weight
        signed += weight if change > 0 else (-weight if change < 0 else 0.0)
    rec["accum_3m"] = _r(signed / total, 3) if total > 0 and len(t_idx) >= trend_min else None
    rec["vol_trend"] = _r(_ratio_pct(point.get("average_volume_10d_calc"), point.get("average_volume_30d_calc")), 1)
    # Estimates (E)
    step_max = float(recipe.get("revision_step_max_pct") or 25.0)
    r_idx = range(b_trend, i + 1) if b_trend is not None else t_idx
    eps_rev, breadth, eps_skip = _revision([_f(points[k].get("earnings_per_share_forecast_next_fy")) for k in r_idx], step_max)
    rev_rev, _breadth, rev_skip = _revision([_f(points[k].get("revenue_forecast_next_fy")) for k in r_idx], step_max)
    rec["eps_rev_3m"] = _r(eps_rev, 2)
    rec["eps_rev_breadth"] = _r(breadth, 3)
    rec["rev_rev_3m"] = _r(rev_rev, 2)
    rec["fy_roll_skips"] = eps_skip + rev_skip
    pt = _f(point.get("price_target_average"))
    rec["pt_drift_3m"] = _r(_ratio_pct(pt, points[b_trend].get("price_target_average")) if b_trend is not None else None, 1)
    rec["eps_surprise"] = _r(point.get("eps_surprise_percent_fq"), 1)
    # Value (V) raw multiples; industry percents come from attach_cross_section
    rec["pe_nfy"] = _r(close / eps_fc, 1) if close and eps_fc and eps_fc > 0 else None
    evebitda = _f(point.get("enterprise_value_ebitda_ttm"))
    rec["evebitda"] = _r(evebitda, 1) if evebitda is not None and evebitda > 0 else None
    growth = _f(rec.get("fwd_eps_g"))
    rec["peg_fwd"] = _r(rec["pe_nfy"] / growth, 2) if rec["pe_nfy"] and growth and growth > 0 else None
    rec["pt_left"] = _r(_ratio_pct(pt, close), 1)
    return rec


def attach_cross_section(rows: list[dict[str, Any]], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Forward PE and EV/EBITDA versus the industry median of the same day's universe."""
    attached = attach_vs_group(
        rows,
        group_field=str(recipe.get("group_field") or "ind"),
        fields=("pe_nfy", "evebitda"),
        min_n=int(recipe.get("peer_min_n") or 6),
    )
    return attached


# ---------------------------------------------------------------- percentiles, lenses, profiles


def percentile_ranks(values: Sequence[Any]) -> list[float | None]:
    """(rank - 0.5) / n among present values, ties share the average rank. None stays None."""
    present = sorted((v, k) for k, v in enumerate(_f(x) for x in values) if v is not None)
    out: list[float | None] = [None] * len(values)
    n = len(present)
    k = 0
    while k < n:
        j = k
        while j + 1 < n and present[j + 1][0] == present[k][0]:
            j += 1
        rank = (k + j) / 2.0 + 1.0
        for m in range(k, j + 1):
            out[present[m][1]] = (rank - 0.5) / n
        k = j + 1
    return out


def lens_parts(recipe: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    return {lens: dict(spec.get("parts") or {}) for lens, spec in (recipe.get("lenses") or {}).items()}


def attach_lenses(rows: list[dict[str, Any]], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``{part}_pct`` per part (direction applied), ``{lens}_pct`` = median of present parts, ``{lens}_n``."""
    min_parts = int(recipe.get("min_parts") or 2)
    for lens, parts in lens_parts(recipe).items():
        for part, direction in parts.items():
            signed = [None if _f(r.get(part)) is None else _f(r.get(part)) * (1 if direction >= 0 else -1) for r in rows]
            for rec, pct in zip(rows, percentile_ranks(signed)):
                rec[f"{part}_pct"] = None if pct is None else round(pct, 3)
        for rec in rows:
            present = [rec[f"{part}_pct"] for part in parts if rec.get(f"{part}_pct") is not None]
            rec[f"{lens}_n"] = len(present)
            rec[f"{lens}_pct"] = round(median(present), 3) if len(present) >= min_parts else None
    letters = list(lens_parts(recipe))
    for rec in rows:
        rec["lenses"] = " ".join(
            f"{lens}{'--' if rec.get(f'{lens}_pct') is None else int(round(rec[f'{lens}_pct'] * 100)):>02}" for lens in letters
        )
        scored = [(rec[f"{lens}_pct"], lens) for lens in letters if rec.get(f"{lens}_pct") is not None]
        rec["weak_lens"] = min(scored)[1] if scored else None
        rec["strong_lens"] = max(scored)[1] if scored else None
    return rows


def attach_profiles(rows: list[dict[str, Any]], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``{profile}_floor`` = weakest lens of the profile (1 - pct when inverted); ``_mean`` is the tie-break."""
    for name, spec in (recipe.get("profiles") or {}).items():
        lenses = list(spec.get("lenses") or [])
        invert = bool(spec.get("invert"))
        key = name.lower()
        for rec in rows:
            values = [_f(rec.get(f"{lens}_pct")) for lens in lenses]
            if not lenses or any(v is None for v in values):
                rec[f"{key}_floor"] = None
                rec[f"{key}_mean"] = None
                continue
            values = [1.0 - v for v in values] if invert else values
            rec[f"{key}_floor"] = round(min(values), 3)
            rec[f"{key}_mean"] = round(sum(values) / len(values), 3)
    return rows


def tape_state(row: Mapping[str, Any], recipe: Mapping[str, Any]) -> str:
    """THIN_TAPE / DOWNTREND / PULLBACK / EXTENDED / TREND_UP / BASE from the recipe bands. A label, not a rank."""
    bands = dict(recipe.get("tape_state") or {})
    quality = _f(row.get("trend_q"))
    r_1m = _f(row.get("r_1m"))
    hi = _f(row.get("hi_prox"))
    if quality is None:
        return "THIN_TAPE"
    if quality <= float(bands.get("trend_q_down", -0.35)):
        return "DOWNTREND"
    if quality >= float(bands.get("trend_q_up", 0.35)):
        if r_1m is not None and r_1m <= float(bands.get("pullback_r_1m_max", -4.0)):
            return "PULLBACK"
        if hi is not None and hi >= float(bands.get("extended_hi_prox_min", -2.0)) and r_1m is not None and r_1m >= float(bands.get("extended_r_1m_min", 15.0)):
            return "EXTENDED"
        return "TREND_UP"
    return "BASE"


def score_cross_section(rows: list[dict[str, Any]], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Industry percents, part and lens percentiles, profile floors, and the tape label for one day's universe."""
    scored = attach_profiles(attach_lenses(attach_cross_section(rows, recipe), recipe), recipe)
    for rec in scored:
        rec["tape_state"] = tape_state(rec, recipe)
    return scored


# ---------------------------------------------------------------- live day


def prepare_series(series: Mapping[str, Sequence[Mapping[str, Any]]], recipe: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Split-adjust each name once and index its days: {symbol: {points, days, at, splits}}."""
    out: dict[str, dict[str, Any]] = {}
    for symbol, points in series.items():
        adjusted, splits = split_adjust(points, recipe.get("split_guard"))
        days = [_day(p.get("as_of")) for p in adjusted]
        keep = [k for k, d in enumerate(days) if d is not None]
        adjusted = [adjusted[k] for k in keep]
        days = [days[k] for k in keep]
        out[symbol] = {"points": adjusted, "days": days, "at": {d: k for k, d in enumerate(days)}, "splits": splits}
    return out


def day_universe(prepared: Mapping[str, Mapping[str, Any]], as_of: date, recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Features for every name with a snapshot on ``as_of`` and market cap at the floor."""
    floor = float(recipe.get("min_market_cap_usd") or 0)
    rows: list[dict[str, Any]] = []
    for symbol, item in prepared.items():
        k = item["at"].get(as_of)
        if k is None:
            continue
        mcap = _f(item["points"][k].get("market_cap_basic"))
        if mcap is None or mcap < floor:
            continue
        rec = point_features(item["points"], k, recipe, item["days"])
        rec["splits"] = item["splits"]
        rows.append(rec)
    return rows


def latest_day(prepared: Mapping[str, Mapping[str, Any]]) -> date | None:
    days = [item["days"][-1] for item in prepared.values() if item["days"]]
    return max(days) if days else None


def build_live(prepared: Mapping[str, Mapping[str, Any]], recipe: Mapping[str, Any], *, as_of: date | None = None) -> list[dict[str, Any]]:
    day = as_of or latest_day(prepared)
    if day is None:
        return []
    return score_cross_section(day_universe(prepared, day, recipe), recipe)


# ---------------------------------------------------------------- selection


def _profile_order(rows: Sequence[Mapping[str, Any]], key: str) -> list[Mapping[str, Any]]:
    eligible = [r for r in rows if _f(r.get(f"{key}_floor")) is not None]
    return sorted(
        eligible,
        key=lambda r: (_f(r.get(f"{key}_floor")), _f(r.get(f"{key}_mean")) or 0.0, _f(r.get("mcap")) or 0.0),
        reverse=True,
    )


def select_profiles(rows: Sequence[Mapping[str, Any]], recipe: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Top names per profile after the industry cap, the overflow the cap held back, and the focus union.

    Focus takes rank 1 of every long profile, then rank 2, and so on, until
    ``focus_max`` names; inverted profiles (FADE) stay out of focus. Each focus
    row lists every profile it ranked in.
    """
    group = str(recipe.get("group_field") or "ind")
    cap = int(recipe.get("industry_cap") or 2)
    top_rows: list[dict[str, Any]] = []
    overflow_rows: list[dict[str, Any]] = []
    by_profile: dict[str, list[dict[str, Any]]] = {}
    for name, spec in (recipe.get("profiles") or {}).items():
        key = name.lower()
        kept, overflow = group_capped_top_n(
            _profile_order(rows, key), group_field=group, cap=cap, n=int(spec.get("top") or 10),
            overflow_fields=(f"{key}_floor", "lenses", "mcap"),
        )
        ranked = []
        for rank, row in enumerate(kept, start=1):
            rec = dict(row)
            rec.update(profile=name, rank=rank, horizon=spec.get("horizon") or "", floor=row.get(f"{key}_floor"))
            ranked.append(rec)
        by_profile[name] = ranked
        top_rows += ranked
        overflow_rows += [dict(item, profile=name) for item in overflow[: int(spec.get("top") or 10)]]
    long_profiles = [n for n, s in (recipe.get("profiles") or {}).items() if not s.get("invert")]
    member: dict[str, list[str]] = {}
    for name in long_profiles:
        for rec in by_profile.get(name) or []:
            member.setdefault(str(rec["symbol"]), []).append(name)
    focus: list[dict[str, Any]] = []
    seen: set[str] = set()
    depth = max((len(by_profile.get(n) or []) for n in long_profiles), default=0)
    limit = int(recipe.get("focus_max") or 50)
    for place in range(depth):
        for name in long_profiles:
            ranked = by_profile.get(name) or []
            if place >= len(ranked) or len(focus) >= limit:
                continue
            rec = ranked[place]
            symbol = str(rec["symbol"])
            if symbol in seen:
                continue
            seen.add(symbol)
            item = dict(rec)
            item["best_profile"] = name
            item["profiles"] = ",".join(member.get(symbol) or [name])
            item["n_profiles"] = len(member.get(symbol) or [name])
            item["focus_order"] = len(focus) + 1
            focus.append(item)
    return {"profile_top": top_rows, "profile_overflow": overflow_rows, "focus": focus}


# ---------------------------------------------------------------- walk-forward evidence


def rank_ic(xs: Sequence[Any], ys: Sequence[Any], *, min_n: int = 30) -> float | None:
    """Spearman correlation on pairs where both sides are present (average ranks on ties)."""
    pairs = [(_f(x), _f(y)) for x, y in zip(xs, ys)]
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pairs) < min_n:
        return None
    rx = percentile_ranks([p[0] for p in pairs])
    ry = percentile_ranks([p[1] for p in pairs])
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def evaluation_days(prepared: Mapping[str, Mapping[str, Any]], recipe: Mapping[str, Any]) -> list[date]:
    """Snapshot days with a full trend window behind them and the longest horizon ahead, every ``step_days``."""
    evidence = dict(recipe.get("evidence") or {})
    win = dict(recipe.get("windows") or {})
    every = sorted({d for item in prepared.values() for d in item["days"]})
    if not every:
        return []
    start = every[0] + timedelta(days=int(win.get("trend_days", 91)) + int(win.get("base_tolerance_days", 10)))
    end = every[-1] - timedelta(days=max(evidence.get("horizons_days") or [28]))
    step = int(evidence.get("step_days") or 7)
    out: list[date] = []
    for day in every:
        if start <= day <= end and (not out or (day - out[-1]).days >= step):
            out.append(day)
    return out


def _forward_return(item: Mapping[str, Any], k: int, horizon: int, tolerance: int) -> float | None:
    days = item["days"]
    target = days[k] + timedelta(days=horizon)
    j = bisect_left(days, target, k + 1)
    if j >= len(days) or (days[j] - target).days > tolerance:
        return None
    return _ratio_pct(item["points"][j].get("close"), item["points"][k].get("close"))


def evidence_keys(recipe: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """(kind, name, column) for every part, lens, and profile."""
    keys: list[tuple[str, str, str]] = []
    for lens, parts in lens_parts(recipe).items():
        keys += [("part", f"{lens}.{part}", f"{part}_pct") for part in parts]
        keys.append(("lens", lens, f"{lens}_pct"))
    keys += [("profile", name, f"{name.lower()}_floor") for name in (recipe.get("profiles") or {})]
    return keys


def lens_evidence(prepared: Mapping[str, Mapping[str, Any]], recipe: Mapping[str, Any], *, days: Sequence[date] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Walk-forward rank IC and top-slice excess return of every part, lens, and profile.

    Each evaluation day rebuilds the universe and every feature from snapshots
    up to that day only, then compares with the close ``h`` calendar days later.
    Horizons overlap from one evaluation day to the next, so the days are not
    independent; ``ic_t`` is a rough size check, not a significance test.
    """
    evidence = dict(recipe.get("evidence") or {})
    horizons = [int(h) for h in evidence.get("horizons_days") or [14, 28]]
    tolerance = int(evidence.get("forward_tolerance_days") or 6)
    min_names = int(evidence.get("min_names") or 200)
    top_frac = float(evidence.get("top_frac") or 0.2)
    keys = evidence_keys(recipe)
    per_day: list[dict[str, Any]] = []
    for day in days if days is not None else evaluation_days(prepared, recipe):
        rows = day_universe(prepared, day, recipe)
        if len(rows) < min_names:
            continue
        for rec in rows:
            item = prepared[str(rec["symbol"])]
            k = item["at"][day]
            for horizon in horizons:
                rec[f"fwd_{horizon}d"] = _forward_return(item, k, horizon, tolerance)
        rows = score_cross_section(rows, recipe)
        for horizon in horizons:
            fwd = [r.get(f"fwd_{horizon}d") for r in rows]
            present = [_f(v) for v in fwd if _f(v) is not None]
            base = sum(present) / len(present) if present else None
            for kind, name, column in keys:
                values = [r.get(column) for r in rows]
                ic = rank_ic(values, fwd)
                pairs = sorted(((_f(v), _f(y)) for v, y in zip(values, fwd) if _f(v) is not None and _f(y) is not None), reverse=True)
                top = pairs[: max(1, int(len(pairs) * top_frac))] if pairs else []
                top_mean = sum(y for _x, y in top) / len(top) if top else None
                per_day.append({
                    "as_of": day.isoformat(), "kind": kind, "name": name, "column": column, "horizon_days": horizon,
                    "n": len(pairs), "ic": _r(ic, 4),
                    "top_mean_pct": _r(top_mean, 2), "universe_mean_pct": _r(base, 2),
                    "top_excess_pct": _r(top_mean - base, 2) if top_mean is not None and base is not None else None,
                })
    summary: list[dict[str, Any]] = []
    for kind, name, column in keys:
        for horizon in horizons:
            sample = [r for r in per_day if r["name"] == name and r["horizon_days"] == horizon and r["ic"] is not None]
            ics = [r["ic"] for r in sample]
            excess = [r["top_excess_pct"] for r in sample if r["top_excess_pct"] is not None]
            n_days = len(ics)
            mean_ic = sum(ics) / n_days if n_days else None
            sd = pstdev(ics) if n_days >= 2 else None
            summary.append({
                "kind": kind, "name": name, "column": column, "horizon_days": horizon, "n_days": n_days,
                "ic_mean": _r(mean_ic, 4), "ic_sd": _r(sd, 4),
                "ic_t": _r(mean_ic / sd * math.sqrt(n_days), 2) if mean_ic is not None and sd else None,
                "ic_pos_rate": _r(sum(1 for v in ics if v > 0) / n_days, 3) if n_days else None,
                # Regime read: a lens can flip sign inside the window (momentum often does).
                "ic_recent3": _r(sum(ics[-3:]) / len(ics[-3:]), 4) if ics else None,
                "ic_last": _r(ics[-1], 4) if ics else None,
                "top_excess_mean_pct": _r(sum(excess) / len(excess), 2) if excess else None,
                "first_day": sample[0]["as_of"] if sample else None, "last_day": sample[-1]["as_of"] if sample else None,
            })
    return {"evidence": summary, "evidence_days": per_day}


# ---------------------------------------------------------------- overlays and text


PACK_OVERLAY: tuple[str, ...] = ("left", "street_left", "rr", "bo", "cont", "fwd", "mix", "mtp", "dte", "rng", "rsi", "vs50", "pe_fwd")


def join_pack(rows: Sequence[Mapping[str, Any]], tape: Sequence[Mapping[str, Any]], *, held: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Left-join pack tape columns (support, never a lens) and the Book flag by EXCHANGE:TICKER."""
    by_symbol = {str(r.get("symbol") or ""): r for r in tape}
    held_set = {str(s) for s in held}
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        extra = by_symbol.get(str(rec.get("symbol") or "")) or {}
        for key in PACK_OVERLAY:
            rec[key] = extra.get(key)
        rec["in_pack_tape"] = bool(extra)
        rec["book"] = str(rec.get("symbol") or "") in held_set
        out.append(rec)
    return out


def highlights_text(*, recipe: Mapping[str, Any], tables: Mapping[str, Sequence[Mapping[str, Any]]], sources: Mapping[str, Any]) -> str:
    lines = [
        "GMV lens — growth, estimates, momentum, participation, value",
        f"as_of={sources.get('as_of')} history_days={sources.get('history_days')} universe={len(tables.get('lens_tape') or [])}",
        f"mcap_floor={recipe.get('min_market_cap_usd')} industry_cap={recipe.get('industry_cap')} focus_max={recipe.get('focus_max')}",
        "Each lens is the median percentile of its parts. Lenses are never added. A profile orders by its weakest lens.",
        "",
        "Focus (rank 1 of each long profile, then rank 2, ...)",
    ]
    for row in list(tables.get("focus") or [])[:25]:
        lines.append(
            f"  {row.get('focus_order'):>2} {row.get('symbol')} {row.get('lenses')} best={row.get('best_profile')} "
            f"in={row.get('profiles')} tape={row.get('tape_state')} r_3m={row.get('r_3m')} eps_rev={row.get('eps_rev_3m')}"
        )
    lines += ["", "FADE (short candidates)"]
    for row in [r for r in tables.get("profile_top") or [] if r.get("profile") == "FADE"][:12]:
        lines.append(f"  {row.get('rank'):>2} {row.get('symbol')} {row.get('lenses')} tape={row.get('tape_state')} r_3m={row.get('r_3m')}")
    lines += ["", "Evidence (walk-forward rank IC; 28d)"]
    for row in tables.get("evidence") or []:
        if row.get("kind") != "part" and row.get("horizon_days") == max((recipe.get("evidence") or {}).get("horizons_days") or [28]):
            lines.append(
                f"  {row.get('kind'):<7} {row.get('name'):<11} ic={row.get('ic_mean')} t={row.get('ic_t')} "
                f"pos={row.get('ic_pos_rate')} top_excess={row.get('top_excess_mean_pct')} days={row.get('n_days')}"
            )
    return "\n".join(lines) + "\n"


def task_queries(database: str) -> str:
    db = database.replace("\\", "/")
    return f"""GMV lens — task SQL
database={db}

Lens percentiles (G E M P V) are 0-1 ranks in the day's universe; the lens is the
median of its parts. Profiles order by the weakest lens (floor). Nothing is added.

1. Focus in order
   SELECT focus_order, symbol, ind, lenses, best_profile, profiles, tape_state, r_3m, trend_q, eps_rev_3m, pe_nfy_vs_ind, "left", dte, book
   FROM focus ORDER BY focus_order;

2. One profile with its overflow
   SELECT rank, symbol, ind, floor, lenses, tape_state FROM profile_top WHERE profile = 'RERATE' ORDER BY rank;
   SELECT symbol, ind, rerate_floor, lenses FROM profile_overflow WHERE profile = 'RERATE';

3. Every part behind one name
   SELECT symbol, lenses, fwd_rev_g, fwd_eps_g, rev_yoy, earn_yoy, eps_rev_3m, eps_rev_breadth, rev_rev_3m, pt_drift_3m,
          eps_surprise, r_1m, r_3m, trend_q, r_3m_per_vol, hi_prox, updown_vol, accum_3m, vol_trend,
          pe_nfy, pe_nfy_vs_ind, evebitda_vs_ind, peg_fwd, pt_left, vol_d, exp_move_20d
   FROM lens_tape WHERE symbol = 'NASDAQ:MU';

4. Which parts and lenses led later closes on this history
   SELECT kind, name, horizon_days, n_days, ic_mean, ic_t, ic_pos_rate, ic_recent3, ic_last, top_excess_mean_pct
   FROM evidence ORDER BY horizon_days, ic_mean DESC;

5. Book read
   SELECT symbol, lenses, weak_lens, tape_state, r_3m, eps_rev_3m, pe_nfy_vs_ind, "left", mix, mtp FROM book_lens;
"""
