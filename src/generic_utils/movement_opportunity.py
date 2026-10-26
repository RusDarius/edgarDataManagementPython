"""Trading movement opportunity: day-to-day variation and auditable 1-10 ratings.

Formulas only. Every input and sub-component stays a separate column.
The 1-10 figures are weighted visible parts, not a hidden 0-100 and not leftover.

This is a first-iteration join of datasets the project already has:
all-fields tape, prediction weeks, edge leftover, MTP climate, projection
fp_*, and an all-fields close/PT/PE span. It does not rewrite leftover,
MTP, or projection math.

Knobs live in config/generic_utils/movement_opportunity.json.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.derive import apply_derived, leftover_carrier, pct_vs
from generic_utils.ranking import group_capped_top_n, to_float
from generic_utils.run_export import load_json_recipe
from generic_utils.setup import attach_setup
from operator_briefing.extract import AF_CANDIDATES
from operator_briefing.sleeves import classify_punished_tape

_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = _PROJECT / "config" / "generic_utils" / "movement_opportunity.json"

_f = to_float

# All-fields names loaded by the runner. Aliases come from AF_CANDIDATES.
LOAD_FIELDS: tuple[str, ...] = (
    "symbol",
    "name",
    "Company",
    "exchange",
    "country",
    "sector",
    "industry",
    "close",
    "market_cap_basic",
    "change",
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "relative_volume_10d_calc",
    "RSI",
    "ADX",
    "SMA20",
    "SMA50",
    "SMA200",
    "price_target_average",
    "price_target_1y",
    "price_52_week_high",
    "price_52_week_low",
    "earnings_release_next_date",
    "earnings_release_next_trading_date_fq",
    "earnings_release_next_calendar_date",
    "price_earnings_ttm",
    "net_income_ttm",
    "net_income_yoy_growth_ttm",
    "net_income_cagr_5y",
    "ebitda_ttm",
    "ebitda_yoy_growth_ttm",
    "total_revenue_ttm",
    "total_revenue_yoy_growth_ttm",
    "total_revenue_cagr_5y",
    "free_cash_flow_ttm",
    "operating_margin",
    "enterprise_value_current",
    "enterprise_value_ebitda_ttm",
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_to_free_cash_flow_ttm",
    "price_book_ratio",
    "price_earnings_growth_ttm",
    "net_debt",
    "total_debt",
    "cash_n_equivalents_fq",
    "Average True Range",
    "ATR",
    "ATRP",
    "Volatility.D",
    "beta_1_year",
)

HISTORY_FIELDS: dict[str, str] = {
    "close": "close",
    "price_target_average": "pt",
    "RSI": "rsi",
    "ATRP": "atrp",
    "price_earnings_ttm": "pe",
}

# First-iteration IC citations. Live family_metrics overwrites ic_live when present.
RESEARCH_EVIDENCE: tuple[dict[str, Any], ...] = (
    {
        "family": "expected_move_proxy_pct",
        "horizon": "20d",
        "ic_cited": 0.233,
        "n_days_cited": 6,
        "role": "move_fuel size",
        "read": "Best live magnitude proxy in the cited backtest. Thin calendar. Size of expected move, not leftover quality.",
    },
    {
        "family": "move_magnitude_score",
        "horizon": "20d",
        "ic_cited": 0.228,
        "n_days_cited": 6,
        "role": "move_fuel size",
        "read": "Same thin window as expected_move_proxy. Joined from edge when present. Not a leftover gate.",
    },
    {
        "family": "value_recovery_v3",
        "horizon": "20d",
        "ic_cited": 0.112,
        "n_days_cited": None,
        "role": "structure_conviction",
        "read": "Weeks recov. Support for bounce / repair, not a rank by itself.",
    },
    {
        "family": "consensus_weeks",
        "horizon": "60d",
        "ic_cited": 0.138,
        "n_days_cited": None,
        "role": "structure_conviction",
        "read": "Weeks climate. Mix avoid still cuts the conviction component.",
    },
    {
        "family": "timing_filter",
        "horizon": "picks overlay",
        "ic_cited": None,
        "n_days_cited": None,
        "role": "timing_catalyst drag",
        "read": "Timing overlay IC versus picks_only was negative in the cited run. MTP ENTER_SMALL stays climate.",
    },
    {
        "family": "ATRP / ADRP",
        "horizon": "20d",
        "ic_cited": None,
        "n_days_cited": None,
        "role": "vol size / downside amp",
        "read": "Negative IC versus later return when used as a rank. Used here as envelope size and downside amplification, not as upside quality.",
    },
    {
        "family": "financial_projection",
        "horizon": "join coverage",
        "ic_cited": None,
        "n_days_cited": None,
        "role": "valuation_growth",
        "read": "Coverage is sparse on a 500M tape. Missing fp_* does not drop the row. Do not gate on projection.",
    },
    {
        "family": "decision_review",
        "horizon": "briefing ranks",
        "ic_cited": None,
        "n_days_cited": None,
        "role": "warning",
        "read": "Published leftover ranks were FLAT through later closes in the cited review. This 1-10 is a heuristic of trading opportunity, not a validated predictor.",
    },
)


def load_movement_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_RECIPE
    payload = load_json_recipe(recipe_path) if recipe_path.exists() else {}
    payload.setdefault("name", "movement_opportunity")
    payload.setdefault("min_market_cap_usd", 500_000_000)
    payload.setdefault("history_days", 30)
    payload.setdefault("hist_min_n", 6)
    payload.setdefault("horizons", [1, 5, 10, 20, 60])
    payload.setdefault("primary_horizon_days", 20)
    payload.setdefault("top_upside", 50)
    payload.setdefault("top_downside", 30)
    payload.setdefault("top_bounce", 25)
    payload.setdefault("group_field", "ind")
    payload.setdefault("cap", 4)
    payload.setdefault("min_peer_n", 6)
    payload.setdefault("us_listings_only", True)
    payload.setdefault(
        "upside_weights",
        {
            "asymmetric_payoff": 0.25,
            "structure_conviction": 0.25,
            "valuation_growth": 0.20,
            "timing_catalyst": 0.15,
            "move_fuel": 0.15,
        },
    )
    payload.setdefault(
        "downside_weights",
        {
            "fragility_trend": 0.25,
            "valuation_ceiling": 0.25,
            "exhaustion_reversal": 0.20,
            "edge_risk": 0.15,
            "vol_amplification": 0.15,
        },
    )
    payload.setdefault(
        "path_rules",
        {
            "opening_close_max_pct": 5.0,
            "opening_pt_min_pct": -2.0,
            "opening_left_min_pct": 18.0,
            "dying_close_min_pct": 8.0,
            "dying_pt_max_pct": 2.0,
            "cheaper_val_max_pct": -5.0,
            "cheaper_rng_max": 85.0,
            "runner_pct": 15.0,
            "pump_pct": 20.0,
            "break_pct": -12.0,
            "fade_pct": -8.0,
        },
    )
    payload.setdefault(
        "volatility_bands",
        {
            "coiled_max_pct": 2.0,
            "active_swing_max_pct": 5.0,
            "high_velocity_max_pct": 9.0,
        },
    )
    return payload


def _r(value: Any, digits: int = 2) -> float | None:
    number = _f(value)
    if number is None:
        return None
    return round(number, digits)


def _clip01(value: Any) -> float | None:
    number = _f(value)
    if number is None:
        return None
    return max(0.0, min(1.0, float(number)))


def _scale(value: Any, lo: float, hi: float) -> float | None:
    """Map [lo, hi] onto [0, 1]. None stays None. hi must be greater than lo."""
    number = _f(value)
    if number is None or hi <= lo:
        return None
    return _clip01((number - lo) / (hi - lo))


def _mean_available(parts: Sequence[float | None]) -> float | None:
    values = [part for part in parts if part is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        num = float(value)
        if num > 1e12:
            num /= 1000.0
        if 1e9 < num < 4e10:
            return datetime.fromtimestamp(num, tz=timezone.utc).date()
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def rating_1_10(weighted: Any, *, coverage_frac: Any) -> int | None:
    """Map a 0-1 weighted mean onto 1-10.

    Thin coverage (under 0.4 of the recipe weights) cannot print above 6.
    Missing weighted input stays empty. This is a heuristic, not a probability.
    """
    score = _f(weighted)
    if score is None:
        return None
    rating = int(round(1.0 + 9.0 * max(0.0, min(1.0, score))))
    rating = max(1, min(10, rating))
    cover = _f(coverage_frac)
    if cover is not None and cover < 0.4:
        rating = min(rating, 6)
    return rating


def path_class(row: Mapping[str, Any], recipe: Mapping[str, Any] | None = None) -> str:
    """OPENING / DYING / CHEAPER / FLAT / THIN_HIST.

    OPENING / DYING / CHEAPER match analysis-style. DYING wins over CHEAPER.
    pe_pct is the first-iteration val_pct (trailing PE span). Enough history
    that is none of those three is FLAT. Missing span is THIN_HIST. Not a score.
    """
    rules = dict((recipe or {}).get("path_rules") or {})
    hist_min = int((recipe or {}).get("hist_min_n") or 6)
    count = _f(row.get("n") or row.get("close_n"))
    close_pct = _f(row.get("close_pct"))
    pt_pct = _f(row.get("pt_pct"))
    leftover = _f(row.get("left"))
    rng = _f(row.get("rng"))
    val_pct = _f(row.get("pe_pct") or row.get("val_pct"))
    if count is None or count < hist_min or close_pct is None or pt_pct is None:
        return "THIN_HIST"
    opening_close = float(rules.get("opening_close_max_pct") or 5.0)
    opening_pt = float(rules.get("opening_pt_min_pct") or -2.0)
    opening_left = float(rules.get("opening_left_min_pct") or 18.0)
    dying_close = float(rules.get("dying_close_min_pct") or 8.0)
    dying_pt = float(rules.get("dying_pt_max_pct") or 2.0)
    cheaper_val = float(rules.get("cheaper_val_max_pct") or -5.0)
    cheaper_rng = float(rules.get("cheaper_rng_max") or 85.0)
    if close_pct >= dying_close and pt_pct <= dying_pt:
        return "DYING"
    if (
        close_pct <= opening_close
        and pt_pct >= opening_pt
        and leftover is not None
        and leftover >= opening_left
    ):
        return "OPENING"
    if (
        val_pct is not None
        and val_pct <= cheaper_val
        and leftover is not None
        and leftover >= opening_left
        and (rng is None or rng < cheaper_rng)
    ):
        return "CHEAPER"
    return "FLAT"


def vol_class(row: Mapping[str, Any], recipe: Mapping[str, Any] | None = None) -> str:
    """coiled / active_swing / high_velocity / extreme / thin.

    Prefers realized close_step_abs_mean_pct. Falls back to ATRP.
    """
    bands = dict((recipe or {}).get("volatility_bands") or {})
    coiled = float(bands.get("coiled_max_pct") or 2.0)
    swing = float(bands.get("active_swing_max_pct") or 5.0)
    fast = float(bands.get("high_velocity_max_pct") or 9.0)
    realized = _f(row.get("close_step_abs_mean_pct"))
    atrp = _f(row.get("atrp"))
    level = realized if realized is not None else atrp
    if level is None:
        return "thin"
    if level <= coiled:
        return "coiled"
    if level <= swing:
        return "active_swing"
    if level <= fast:
        return "high_velocity"
    return "extreme"


def expected_moves(
    row: Mapping[str, Any],
    *,
    horizons: Sequence[int] | None = None,
) -> dict[str, Any]:
    """ATRP (else daily vol) times sqrt(horizon). Reuses the edge proxy formula."""
    from edge_research_tools.upside_move_potential_scanner import (
        compute_expected_move_proxy_pct,
    )

    payload = {
        "atrp": row.get("atrp"),
        "volatility_d": row.get("vol_d") or row.get("volatility_d"),
    }
    out: dict[str, Any] = {}
    for days in horizons or (1, 5, 10, 20, 60):
        horizon = int(days)
        out[f"exp_move_{horizon}d"] = compute_expected_move_proxy_pct(
            payload, horizon_days=horizon
        )
    return out


def alias_all_fields_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map all-fields names onto pack aliases. Keeps original keys."""
    rec = dict(row)
    rec["symbol"] = str(row.get("symbol") or "")
    rec["company"] = row.get("company") or row.get("name") or row.get("Company")
    rec["ind"] = row.get("ind") or row.get("industry")
    rec["mcap"] = _f(row.get("mcap") or row.get("market_cap_basic"))
    for alias, candidates in AF_CANDIDATES.items():
        if rec.get(alias) not in (None, ""):
            continue
        for candidate in candidates:
            value = row.get(candidate)
            if value not in (None, ""):
                rec[alias] = value
                break
    rec["ind"] = rec.get("ind") or rec.get("industry")
    rec["mcap"] = _f(rec.get("mcap") or rec.get("market_cap_basic"))
    rec["close"] = _f(rec.get("close"))
    rec["pt"] = _f(rec.get("pt"))
    rec["rsi"] = _f(rec.get("rsi"))
    rec["atrp"] = _f(rec.get("atrp"))
    rec["vol_d"] = _f(rec.get("vol_d"))
    rec["relvol"] = _f(rec.get("relvol"))
    rec["day"] = _f(rec.get("day"))
    rec["d5"] = _f(rec.get("d5"))
    rec["w"] = _f(rec.get("w"))
    rec["m1"] = _f(rec.get("m1"))
    rec["m3"] = _f(rec.get("m3"))
    rec["adx"] = _f(rec.get("adx"))
    rec["beta"] = _f(rec.get("beta"))
    rec["pe"] = _f(rec.get("pe"))
    rec["opm"] = _f(rec.get("opm"))
    rec["sma50"] = _f(rec.get("sma50"))
    rec["sma200"] = _f(rec.get("sma200"))
    rec["hi52"] = _f(rec.get("hi52"))
    rec["lo52"] = _f(rec.get("lo52"))
    return rec


def join_overlays(
    rows: Sequence[Mapping[str, Any]],
    *,
    pred: Mapping[str, Mapping[str, Any]] | None = None,
    conviction: Mapping[str, Mapping[str, Any]] | None = None,
    edge: Mapping[str, Mapping[str, Any]] | None = None,
    mtp: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Left-join prediction weeks, mix, edge leftover, and MTP action."""
    pred = pred or {}
    conviction = conviction or {}
    edge = edge or {}
    mtp = mtp or {}
    out: list[dict[str, Any]] = []
    for raw in rows:
        rec = dict(raw)
        symbol = str(rec.get("symbol") or "")
        weeks = pred.get(symbol) or {}
        for key in ("bo", "cont", "fwd", "early", "sms", "recov", "rev", "frag", "exh"):
            if rec.get(key) is None and weeks.get(key) is not None:
                rec[key] = _r(weeks.get(key), 3)
        conv = conviction.get(symbol) or {}
        if rec.get("mix") is None:
            rec["mix"] = conv.get("manager_action_signal") or conv.get("mix")
        if rec.get("conv") is None:
            rec["conv"] = _r(conv.get("conviction_score"), 3)
        edge_row = edge.get(symbol) or {}
        if rec.get("edge_left") is None:
            rec["edge_left"] = _r(edge_row.get("forward_valuation_upside_pct"), 1)
        if rec.get("opp") is None:
            rec["opp"] = _f(edge_row.get("upside_opportunity_rank"))
        if rec.get("lean") is None:
            rec["lean"] = edge_row.get("directional_lean")
        if rec.get("move_magnitude_score") is None:
            rec["move_magnitude_score"] = _f(edge_row.get("move_magnitude_score"))
        if rec.get("opp_downside_risk") is None:
            rec["opp_downside_risk"] = _f(
                edge_row.get("opp_downside_risk") or edge_row.get("downside_risk")
            )
        mtp_row = mtp.get(symbol) or {}
        if rec.get("mtp") is None:
            rec["mtp"] = mtp_row.get("action")
        if rec.get("mtp_setup") is None:
            rec["mtp_setup"] = mtp_row.get("primary_setup")
        out.append(rec)
    return out


def attach_span(
    rows: Sequence[Mapping[str, Any]],
    span_rows: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Join first/last/step stats. Missing span leaves path_class to be THIN_HIST."""
    by_symbol = {
        str(row.get("symbol") or ""): dict(row)
        for row in (span_rows or [])
        if row.get("symbol")
    }
    out: list[dict[str, Any]] = []
    for raw in rows:
        rec = dict(raw)
        span = by_symbol.get(str(rec.get("symbol") or "")) or {}
        if span:
            rec["n"] = span.get("n") or rec.get("n")
            rec["first_as_of"] = span.get("first_as_of")
            rec["last_as_of"] = span.get("last_as_of")
            for key, value in span.items():
                if key in {"symbol", "n", "first_as_of", "last_as_of"}:
                    continue
                rec[key] = value
        out.append(rec)
    return out


def attach_dte(rows: Sequence[Mapping[str, Any]], scan: date | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in rows:
        rec = dict(raw)
        if rec.get("dte") is None:
            earn = _parse_date(rec.get("earn") or rec.get("earnings_release_next_date"))
            rec["earn"] = earn.isoformat() if earn else rec.get("earn")
            rec["dte"] = (earn - scan).days if earn and scan else None
        out.append(rec)
    return out


US_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "CBOE"})


def keep_us_listing(row: Mapping[str, Any]) -> bool:
    """Same US tape as extract_all_fields. Dual listings stay off the ranking."""
    symbol = str(row.get("symbol") or "")
    prefix = symbol.split(":")[0].upper() if ":" in symbol else ""
    exch = str(row.get("exchange") or "").upper()
    country = str(row.get("country") or "")
    return (
        country in {"United States", "USA", "US"}
        or exch in US_EXCHANGES
        or prefix in US_EXCHANGES
    )


def _punished(row: Mapping[str, Any]) -> bool:
    """Multi-day laggard. A one-day dip on a runner is not punished tape."""
    d5 = _f(row.get("d5"))
    m1 = _f(row.get("m1"))
    week = _f(row.get("w"))
    vs50 = _f(row.get("vs50"))
    return bool(
        (d5 is not None and d5 <= -5)
        or (m1 is not None and m1 <= -8)
        or (week is not None and week <= -6)
        or (
            vs50 is not None
            and vs50 <= -8
            and (d5 is None or d5 <= -3)
        )
    )


def _mix_avoid(row: Mapping[str, Any]) -> bool:
    mix = str(row.get("mix") or "").lower()
    return mix.startswith("avoid")


def _asymmetric_payoff(row: Mapping[str, Any]) -> float | None:
    leftover = _scale(row.get("left"), 0.0, 40.0)
    rr = _scale(row.get("rr"), 0.0, 2.0)
    rng = _f(row.get("rng"))
    left = _f(row.get("left"))
    unused = None
    if left is not None and rng is not None:
        if left >= 18 and rng <= 40:
            unused = 1.0
        elif left >= 12 and rng <= 60:
            unused = 0.65
        elif left >= 8:
            unused = 0.35
        else:
            unused = 0.1
    path = str(row.get("path_class") or "")
    path_part = {
        "OPENING": 1.0,
        "CHEAPER": 0.8,
        "FLAT": 0.35,
        "THIN_HIST": 0.4 if leftover is not None else None,
        "DYING": 0.1,
    }.get(path)
    street = _f(row.get("street_left"))
    street_part = _scale(street, 0.0, 40.0) if street is not None and street > 0 else None
    if street is not None and street <= 0 and leftover is not None:
        street_part = 0.15
    return _mean_available((leftover, rr, unused, path_part, street_part))


def _structure_conviction(row: Mapping[str, Any]) -> float | None:
    live = None
    scores = [_clip01(row.get(key)) for key in ("bo", "cont", "fwd")]
    present = [item for item in scores if item is not None]
    if present:
        live = max(present)
    recov = _clip01(row.get("recov"))
    early = _clip01(row.get("early"))
    mix_part = 0.0 if _mix_avoid(row) else (0.7 if row.get("mix") else None)
    mean = _mean_available((live, recov, early, mix_part))
    if mean is None:
        return None
    if _mix_avoid(row):
        return round(mean * 0.5, 4)
    return mean


def _valuation_growth(row: Mapping[str, Any]) -> float | None:
    peer = str(row.get("peer") or "")
    peer_part = {
        "DISCOUNT": 1.0,
        "INLINE": 0.5,
        "PREMIUM": 0.15,
        "THIN": None,
        "NA": None,
    }.get(peer)
    vs = _f(row.get("val_vs_ind"))
    vs_part = _scale(-vs, 0.0, 30.0) if vs is not None else None
    growth = _f(row.get("fp_rev_cagr_own") or row.get("rev_cagr") or row.get("rev_yoy"))
    growth_part = _scale(growth, 0.0, 30.0) if growth is not None and growth > 0 else None
    model = _f(row.get("fp_primary_upside_pct"))
    model_part = None
    if model is not None and 0 < model <= 120:
        model_part = _scale(model, 0.0, 80.0)
    cheaper = None
    if str(row.get("path_class") or "") == "CHEAPER":
        cheaper = 0.85
    elif _f(row.get("pe_pct")) is not None and _f(row.get("pe_pct")) <= -5:
        cheaper = 0.7
    return _mean_available((peer_part, vs_part, growth_part, model_part, cheaper))


def _timing_catalyst(row: Mapping[str, Any]) -> float | None:
    action = str(row.get("mtp") or "").upper()
    mtp_part = {
        "ENTER_SMALL": 1.0,
        "ENTER_PROBE": 0.7,
        "WATCH": 0.4,
        "AVOID_CHASE": 0.0,
        "AVOID": 0.0,
    }.get(action)
    dte = _f(row.get("dte"))
    dte_part = None
    if dte is not None:
        if 8 <= dte <= 21:
            dte_part = 1.0
        elif 1 <= dte <= 7:
            dte_part = 0.7
        elif 22 <= dte <= 45:
            dte_part = 0.5
        elif dte > 45:
            dte_part = 0.2
        else:
            dte_part = 0.35
    bounce = 0.8 if str(row.get("down_class") or "") == "BOUNCE" else None
    return _mean_available((mtp_part, dte_part, bounce))


def _move_fuel(row: Mapping[str, Any], *, primary_horizon: int = 20) -> float | None:
    envelope = _f(row.get(f"exp_move_{int(primary_horizon)}d") or row.get("exp_move_20d"))
    size = _scale(envelope, 4.0, 25.0)
    magnitude = _clip01(row.get("move_magnitude_score"))
    relvol = _scale(row.get("relvol"), 0.6, 2.2)
    vol = str(row.get("vol_class") or "")
    leftover = _f(row.get("left"))
    rng = _f(row.get("rng"))
    coiled = None
    if vol == "coiled" and leftover is not None and leftover >= 18 and (rng is None or rng <= 60):
        coiled = 1.0
    elif vol == "coiled":
        coiled = 0.55
    elif vol == "active_swing":
        coiled = 0.45
    elif vol in {"high_velocity", "extreme"}:
        coiled = 0.2 if leftover is not None and leftover >= 18 else 0.1
    up_rate = _f(row.get("close_step_up_rate"))
    trend = up_rate if up_rate is None else _clip01(up_rate)
    return _mean_available((size, magnitude, relvol, coiled, trend))


def _fragility_trend(row: Mapping[str, Any]) -> float | None:
    frag = _clip01(row.get("frag"))
    vs50 = _f(row.get("vs50"))
    vs_part = _scale(-vs50, 0.0, 20.0) if vs50 is not None and vs50 < 0 else (
        0.1 if vs50 is not None else None
    )
    dying = 1.0 if str(row.get("path_class") or "") == "DYING" else None
    cont_down = 1.0 if str(row.get("down_class") or "") == "CONTINUE_DOWN" else None
    streak = _f(row.get("close_streak_down"))
    streak_part = _scale(streak, 0.0, 6.0)
    draw = _f(row.get("close_max_drawdown_pct"))
    draw_part = _scale(-draw, 0.0, 25.0) if draw is not None and draw < 0 else None
    d5 = _f(row.get("d5"))
    d5_part = _scale(-d5, 0.0, 15.0) if d5 is not None and d5 < 0 else None
    return _mean_available((frag, vs_part, dying, cont_down, streak_part, draw_part, d5_part))


def _valuation_ceiling(row: Mapping[str, Any]) -> float | None:
    peer = str(row.get("peer") or "")
    peer_part = {"PREMIUM": 1.0, "INLINE": 0.45, "DISCOUNT": 0.1}.get(peer)
    rng = _f(row.get("rng"))
    rng_part = _scale(rng, 70.0, 100.0)
    leftover = _f(row.get("left"))
    left_part = None
    if leftover is not None:
        if leftover <= 8:
            left_part = 1.0
        elif leftover <= 15:
            left_part = 0.55
        else:
            left_part = 0.15
    rsi = _f(row.get("rsi"))
    rsi_paid = _scale(rsi, 65.0, 85.0)
    return _mean_available((peer_part, rng_part, left_part, rsi_paid))


def _exhaustion_reversal(row: Mapping[str, Any]) -> float | None:
    rsi = _scale(row.get("rsi"), 60.0, 85.0)
    exh = _clip01(row.get("exh"))
    giveback = _f(row.get("close_giveback_from_max_pct"))
    give_part = _scale(-giveback, 0.0, 20.0) if giveback is not None and giveback < 0 else None
    m1 = _f(row.get("m1"))
    run_part = _scale(m1, 12.0, 40.0) if m1 is not None and m1 > 0 else None
    return _mean_available((rsi, exh, give_part, run_part))


def _edge_risk(row: Mapping[str, Any]) -> float | None:
    lean = str(row.get("lean") or "").upper()
    lean_part = None
    if "AVOID" in lean or "DOWNSIDE" in lean or "SHORT" in lean:
        lean_part = 1.0
    elif lean:
        lean_part = 0.25
    risk = _clip01(row.get("opp_downside_risk"))
    mix_part = 1.0 if _mix_avoid(row) else None
    frag = _clip01(row.get("frag"))
    return _mean_available((lean_part, risk, mix_part, frag))


def _vol_amplification(row: Mapping[str, Any], *, primary_horizon: int = 20) -> float | None:
    vol = str(row.get("vol_class") or "")
    vol_part = {
        "extreme": 1.0,
        "high_velocity": 0.8,
        "active_swing": 0.4,
        "coiled": 0.15,
        "thin": None,
    }.get(vol)
    envelope = _f(row.get(f"exp_move_{int(primary_horizon)}d") or row.get("exp_move_20d"))
    size = _scale(envelope, 8.0, 30.0)
    tilt = None
    vs50 = _f(row.get("vs50"))
    if vol in {"high_velocity", "extreme"} and vs50 is not None and vs50 < 0:
        tilt = 1.0
    elif str(row.get("path_class") or "") == "DYING":
        tilt = 0.85
    atrp = _scale(row.get("atrp"), 3.0, 12.0)
    return _mean_available((vol_part, size, tilt, atrp))


def _weighted(
    parts: Mapping[str, float | None],
    weights: Mapping[str, Any],
) -> tuple[float | None, int, float]:
    used = 0.0
    total = 0.0
    available = 0
    for name, weight in weights.items():
        value = parts.get(name)
        w = _f(weight) or 0.0
        if value is None or w <= 0:
            continue
        used += w
        total += w * float(value)
        available += 1
    weight_sum = sum(_f(w) or 0.0 for w in weights.values())
    if used <= 0 or weight_sum <= 0:
        return None, available, 0.0
    return round(total / used, 4), available, round(used / weight_sum, 4)


def score_row(row: Mapping[str, Any], recipe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Attach path/vol labels, expected-move envelope, components, and 1-10 ratings."""
    spec = recipe or load_movement_recipe()
    rec = dict(row)
    rec["path_class"] = path_class(rec, spec)
    rec["vol_class"] = vol_class(rec, spec)
    rec.update(expected_moves(rec, horizons=spec.get("horizons")))
    primary = int(spec.get("primary_horizon_days") or 20)
    rec["exp_move_primary_d"] = rec.get(f"exp_move_{primary}d")
    rec["left_carrier"] = leftover_carrier(rec.get("street_left"), rec.get("left"))
    if rec.get("vs200") is None:
        rec["vs200"] = pct_vs(rec.get("close"), rec.get("sma200"))
    if _punished(rec) and not rec.get("down_class"):
        rec["down_class"] = classify_punished_tape(rec)
    elif not rec.get("down_class"):
        rec["down_class"] = None

    up_parts = {
        "asymmetric_payoff": _asymmetric_payoff(rec),
        "structure_conviction": _structure_conviction(rec),
        "valuation_growth": _valuation_growth(rec),
        "timing_catalyst": _timing_catalyst(rec),
        "move_fuel": _move_fuel(rec, primary_horizon=primary),
    }
    dn_parts = {
        "fragility_trend": _fragility_trend(rec),
        "valuation_ceiling": _valuation_ceiling(rec),
        "exhaustion_reversal": _exhaustion_reversal(rec),
        "edge_risk": _edge_risk(rec),
        "vol_amplification": _vol_amplification(rec, primary_horizon=primary),
    }
    rec.update({f"up_{name}": _r(value, 4) for name, value in up_parts.items()})
    rec.update({f"dn_{name}": _r(value, 4) for name, value in dn_parts.items()})
    up_w, up_n, up_frac = _weighted(up_parts, spec.get("upside_weights") or {})
    dn_w, dn_n, dn_frac = _weighted(dn_parts, spec.get("downside_weights") or {})
    rec["up_weighted"] = up_w
    rec["dn_weighted"] = dn_w
    rec["up_cover_n"] = up_n
    rec["dn_cover_n"] = dn_n
    rec["up_cover_frac"] = up_frac
    rec["dn_cover_frac"] = dn_frac
    rec["thin_up"] = bool(up_frac < 0.4)
    rec["thin_dn"] = bool(dn_frac < 0.4)
    rec["upside_10"] = rating_1_10(up_w, coverage_frac=up_frac)
    rec["downside_10"] = rating_1_10(dn_w, coverage_frac=dn_frac)
    rec["up_minus_dn"] = None
    if rec["upside_10"] is not None and rec["downside_10"] is not None:
        rec["up_minus_dn"] = rec["upside_10"] - rec["downside_10"]
    return rec


def score_tape(
    rows: Sequence[Mapping[str, Any]],
    recipe: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    spec = recipe or load_movement_recipe()
    return [score_row(row, spec) for row in rows]


def prepare_tape(
    rows: Sequence[Mapping[str, Any]],
    recipe: Mapping[str, Any] | None = None,
    *,
    scan_day: date | None = None,
    pred: Mapping[str, Mapping[str, Any]] | None = None,
    conviction: Mapping[str, Mapping[str, Any]] | None = None,
    edge: Mapping[str, Mapping[str, Any]] | None = None,
    mtp: Mapping[str, Mapping[str, Any]] | None = None,
    span_rows: Sequence[Mapping[str, Any]] | None = None,
    peer_rows: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Alias, join overlays, leftover/range/setup, span, then score."""
    spec = recipe or load_movement_recipe()
    aliased = [alias_all_fields_row(row) for row in rows]
    if spec.get("us_listings_only", True):
        aliased = [row for row in aliased if keep_us_listing(row)]
    joined = join_overlays(
        aliased, pred=pred, conviction=conviction, edge=edge, mtp=mtp
    )
    derived = apply_derived(joined, refresh=True)
    dated = attach_dte(derived, scan_day)
    spanned = attach_span(dated, span_rows)
    setup = attach_setup(spanned, peer_rows=peer_rows)
    return score_tape(setup, spec)


def _rank_slice(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
    n: int,
    group_field: str,
    cap: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible = [row for row in rows if _f(row.get(field)) is not None]
    kept, overflow = group_capped_top_n(
        eligible,
        group_field=group_field,
        cap=cap,
        field=field,
        reverse=True,
        n=n,
        overflow_fields=(field, "ind", "left", "rr", "path_class"),
    )
    ranked: list[dict[str, Any]] = []
    for idx, row in enumerate(kept, start=1):
        rec = dict(row)
        rec["rank"] = idx
        ranked.append(rec)
    return ranked, [dict(item) for item in overflow]


def research_evidence_rows(
    family_metrics: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Cited IC plus live family_metrics when that CSV is on disk."""
    live: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in family_metrics or ():
        family = str(row.get("family") or row.get("signal") or row.get("name") or "")
        horizon = str(row.get("horizon") or row.get("horizon_days") or "")
        if family:
            live[(family.lower(), horizon)] = row
    out: list[dict[str, Any]] = []
    for item in RESEARCH_EVIDENCE:
        rec = dict(item)
        rec["ic_live"] = None
        rec["n_days_live"] = None
        key = str(item["family"]).lower()
        for (family, _horizon), row in live.items():
            if key in family or family in key:
                rec["ic_live"] = _r(row.get("spearman_ic"), 3)
                rec["n_days_live"] = _f(row.get("n_days") or row.get("n") or row.get("n_dates"))
                rec["family_metrics_family"] = row.get("family") or row.get("signal")
                rec["family_metrics_horizon"] = row.get("horizon") or row.get("horizon_days")
                break
        out.append(rec)
    return out


def build_movement_tables(
    rows: Sequence[Mapping[str, Any]],
    recipe: Mapping[str, Any] | None = None,
    *,
    family_metrics: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    spec = recipe or load_movement_recipe()
    tape = [dict(row) for row in rows]
    group_field = str(spec.get("group_field") or "ind")
    cap = int(spec.get("cap") or 4)
    upside, up_overflow = _rank_slice(
        tape,
        field="upside_10",
        n=int(spec.get("top_upside") or 50),
        group_field=group_field,
        cap=cap,
    )
    downside, dn_overflow = _rank_slice(
        tape,
        field="downside_10",
        n=int(spec.get("top_downside") or 30),
        group_field=group_field,
        cap=cap,
    )
    bounce_src = [row for row in tape if str(row.get("down_class") or "") == "BOUNCE"]
    bounce, bounce_overflow = _rank_slice(
        bounce_src,
        field="upside_10",
        n=int(spec.get("top_bounce") or 25),
        group_field=group_field,
        cap=cap,
    )
    return {
        "movement_tape": tape,
        "upside_50": upside,
        "downside_30": downside,
        "bounce": bounce,
        "upside_overflow": up_overflow,
        "downside_overflow": dn_overflow,
        "bounce_overflow": bounce_overflow,
        "evidence": research_evidence_rows(family_metrics),
    }


def highlights_text(
    *,
    spec: Mapping[str, Any],
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    sources: Mapping[str, Any] | None = None,
) -> str:
    sources = sources or {}
    lines = [
        "Movement opportunity — first iteration",
        f"mcap_floor={spec.get('min_market_cap_usd')} history_days={spec.get('history_days')}",
        f"primary_horizon_days={spec.get('primary_horizon_days')}",
        "upside_10 and downside_10 are weighted visible components, not leftover and not a 0-100.",
        "Thin coverage (under 0.4 of the recipe weights) cannot print above 6.",
        "MTP ENTER_SMALL is climate. Projection fp_* does not gate a row.",
        f"history={sources.get('history') or ''}",
        f"overlays={sources.get('overlays') or ''}",
        "",
        "Top upside (upside_10)",
    ]
    for row in list(tables.get("upside_50") or [])[:15]:
        lines.append(
            f"  {row.get('rank'):>2} {row.get('symbol')}  up={row.get('upside_10')} "
            f"dn={row.get('downside_10')} path={row.get('path_class')} "
            f"vol={row.get('vol_class')} left={_r(row.get('left'), 1)} "
            f"rr={row.get('rr')} peer={row.get('peer')} mtp={row.get('mtp')}"
        )
    lines.append("")
    lines.append("Top downside (downside_10)")
    for row in list(tables.get("downside_30") or [])[:10]:
        lines.append(
            f"  {row.get('rank'):>2} {row.get('symbol')}  dn={row.get('downside_10')} "
            f"up={row.get('upside_10')} path={row.get('path_class')} "
            f"down_class={row.get('down_class')} vs50={_r(row.get('vs50'), 1)}"
        )
    lines.append("")
    lines.append("Bounce (punished + down_class=BOUNCE, sorted by upside_10)")
    for row in list(tables.get("bounce") or [])[:10]:
        lines.append(
            f"  {row.get('rank'):>2} {row.get('symbol')}  up={row.get('upside_10')} "
            f"d5={_r(row.get('d5'), 1)} m1={_r(row.get('m1'), 1)} "
            f"left={_r(row.get('left'), 1)} mtp={row.get('mtp')} path={row.get('path_class')}"
        )
    lines.append("")
    lines.append("Evidence (cited IC; ic_live when family_metrics is on disk)")
    for row in tables.get("evidence") or []:
        lines.append(
            f"  {row.get('family')} {row.get('horizon')} cited={row.get('ic_cited')} "
            f"live={row.get('ic_live')} — {row.get('read')}"
        )
    return "\n".join(lines) + "\n"


def task_queries(database: str) -> str:
    db = database.replace("\\", "/")
    return f"""Movement opportunity — task SQL
database={db}

upside_10 / downside_10 are 1-10 heuristics from named components. They are
not leftover, not mix, and not a probability. Quote leftover as "left".

1. Highest upside_10 after the industry cap
   SELECT symbol, ind, upside_10, downside_10, path_class, vol_class, "left", rr, peer, bo, mtp, thin_up
   FROM upside_50
   ORDER BY rank;

2. Highest downside_10
   SELECT symbol, ind, downside_10, upside_10, path_class, down_class, vs50, rsi, rng, mtp
   FROM downside_30
   ORDER BY rank;

3. Punished bounce (leftover / unused range / MTP climate still live)
   SELECT symbol, ind, upside_10, d5, "left", rng, peer, mtp, path_class
   FROM bounce
   ORDER BY rank;

4. OPENING or CHEAPER on the full tape
   SELECT symbol, ind, path_class, upside_10, "left", rr, close_pct, pt_pct, pe_pct, n
   FROM movement_tape
   WHERE path_class IN ('OPENING', 'CHEAPER')
   ORDER BY upside_10 DESC NULLS LAST;

5. Components for one name (nothing is hidden behind the 1-10)
   SELECT symbol, up_asymmetric_payoff, up_structure_conviction, up_valuation_growth,
          up_timing_catalyst, up_move_fuel, up_cover_frac, upside_10,
          dn_fragility_trend, dn_valuation_ceiling, dn_exhaustion_reversal,
          dn_edge_risk, dn_vol_amplification, dn_cover_frac, downside_10
   FROM movement_tape
   WHERE symbol = 'NASDAQ:MU';

6. Cited IC versus live family_metrics
   SELECT family, horizon, role, ic_cited, ic_live, n_days_cited, n_days_live, read
   FROM evidence;

"""
