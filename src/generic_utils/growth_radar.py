"""Growth radar from all-fields plus the financial projection.

Two thresholds, both caller-supplied:

* market cap, so nano and thin names stay off the tape
* a growth percent, cleared on revenue or on earnings

Revenue growth and earnings growth stay separate columns and separate
rankings. Peer discount and the name's own PE history sit beside them.
Nothing here is a 0-100 blend.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from generic_utils.derive import attach_vs_group, pct_vs, range_position_pct
from generic_utils.ranking import drop_duplicate_id_suffixes, group_capped_top_n, top_n, to_float

MIN_MARKET_CAP_USD = 1_000_000_000
MIN_GROWTH_PCT = 15.0
DISCOUNT_PCT = -5.0
PREMIUM_PCT = 5.0
EARN_OUTRUN_GAP = 10.0
PEER_MIN_N = 6
# Projection growth is clipped at 150%. A print on that ceiling is not a rank.
PROJECTION_CLIP_PCT = 149.0
# A four-digit year-over-year print is a base effect, not a growth rate to sort on.
GROWTH_QUOTE_MAX_PCT = 300.0

GROWTH_LOAD_FIELDS: tuple[str, ...] = (
    "symbol",
    "name",
    "exchange",
    "sector",
    "industry",
    "close",
    "market_cap_basic",
    "total_revenue_ttm",
    "total_revenue_yoy_growth_ttm",
    "total_revenue_cagr_5y",
    "ebitda_ttm",
    "ebitda_yoy_growth_ttm",
    "net_income_ttm",
    "net_income_yoy_growth_ttm",
    "net_income_cagr_5y",
    "operating_margin_ttm",
    "operating_margin",
    "price_earnings_ttm",
    "enterprise_value_ebitda_ttm",
    "enterprise_value_current",
    "price_target_average",
    "RSI",
    "SMA50",
    "price_52_week_high",
    "price_52_week_low",
    "relative_volume_10d_calc",
)


@dataclass(frozen=True)
class GrowthRadarSpec:
    """Edit these in ``run_growth_radar_local_main``. They are not ranking cutoffs."""

    min_market_cap_usd: float = MIN_MARKET_CAP_USD
    min_growth_pct: float = MIN_GROWTH_PCT
    top: int = 40
    industry_cap: int = 4
    overlooked_rng_max: float = 60.0
    hist_newest: int = 12
    hist_cheap_pct: float = DISCOUNT_PCT

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def as_growth_percent(value: Any) -> float | None:
    """TradingView growth is usually already in percent. A fraction under 2.5 is scaled."""
    number = to_float(value)
    if number is None:
        return None
    if abs(number) <= 2.5:
        return number * 100.0
    return number


def _step_pct(future: Any, current: Any) -> float | None:
    ahead = to_float(future)
    base = to_float(current)
    if ahead is None or base is None or base <= 0 or ahead <= 0:
        return None
    return (ahead / base - 1.0) * 100.0


def _usable_growth(value: float | None, *, projected: bool) -> float | None:
    if value is None:
        return None
    if projected and value >= PROJECTION_CLIP_PCT:
        return None
    if abs(value) > GROWTH_QUOTE_MAX_PCT:
        return None
    return round(value, 1)


def quote_revenue_growth(row: Mapping[str, Any]) -> tuple[float | None, str | None]:
    """One revenue number. Projection year-1 first, then trailing year-over-year, then CAGR."""
    step = _usable_growth(
        _step_pct(row.get("fp_rev_y1"), row.get("total_revenue_ttm") or row.get("rev_ttm")),
        projected=True,
    )
    if step is not None:
        return step, "fp_rev_y1"
    for key, source in (
        ("total_revenue_yoy_growth_ttm", "rev_yoy"),
        ("rev_yoy", "rev_yoy"),
        ("total_revenue_cagr_5y", "rev_cagr"),
        ("rev_cagr", "rev_cagr"),
    ):
        value = _usable_growth(as_growth_percent(row.get(key)), projected=False)
        if value is not None:
            return value, source
    return None, None


def quote_earnings_growth(row: Mapping[str, Any]) -> tuple[float | None, str | None]:
    """One earnings number. Projection net-income step first, then trailing income, then EBITDA."""
    step = _usable_growth(
        _step_pct(row.get("fp_ni_y1"), row.get("net_income_ttm") or row.get("ni")),
        projected=True,
    )
    if step is not None:
        return step, "fp_ni_y1"
    for key, source in (
        ("net_income_yoy_growth_ttm", "earn_yoy"),
        ("earn_yoy", "earn_yoy"),
        ("ebitda_yoy_growth_ttm", "ebitda_yoy"),
        ("ebitda_yoy", "ebitda_yoy"),
        ("net_income_cagr_5y", "earn_cagr"),
        ("earn_cagr", "earn_cagr"),
    ):
        value = _usable_growth(as_growth_percent(row.get(key)), projected=False)
        if value is not None:
            return value, source
    return None, None


def peer_bin(percent: Any, *, thin: bool = False) -> str:
    """Standing DISCOUNT / PREMIUM / INLINE rule. DISCOUNT is <= -5. PREMIUM is >= 5."""
    if thin:
        return "THIN"
    value = to_float(percent)
    if value is None:
        return "NA"
    if value <= DISCOUNT_PCT:
        return "DISCOUNT"
    if value >= PREMIUM_PCT:
        return "PREMIUM"
    return "INLINE"


def _round(value: Any, digits: int = 1) -> float | None:
    number = to_float(value)
    if number is None:
        return None
    return round(number, digits)


def prepare_growth_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Alias the raw scan and quote one revenue growth and one earnings growth."""
    rev_growth, rev_source = quote_revenue_growth(row)
    earn_growth, earn_source = quote_earnings_growth(row)
    close = to_float(row.get("close"))
    out = {
        "symbol": str(row.get("symbol") or ""),
        "company": row.get("name") or row.get("company") or "",
        "exchange": row.get("exchange") or "",
        "sector": row.get("sector") or "",
        "ind": row.get("ind") or row.get("industry") or "",
        "close": _round(close, 2),
        "mcap": _round(row.get("mcap") if row.get("mcap") is not None else row.get("market_cap_basic"), 0),
        "rev_ttm": _round(row.get("total_revenue_ttm") or row.get("rev_ttm"), 0),
        "rev_yoy": _round(as_growth_percent(row.get("total_revenue_yoy_growth_ttm") or row.get("rev_yoy"))),
        "rev_cagr": _round(as_growth_percent(row.get("total_revenue_cagr_5y") or row.get("rev_cagr"))),
        "rev_growth": rev_growth,
        "rev_growth_source": rev_source or "",
        "ni": _round(row.get("net_income_ttm") or row.get("ni"), 0),
        "earn_yoy": _round(as_growth_percent(row.get("net_income_yoy_growth_ttm") or row.get("earn_yoy"))),
        "earn_cagr": _round(as_growth_percent(row.get("net_income_cagr_5y") or row.get("earn_cagr"))),
        "ebitda_yoy": _round(as_growth_percent(row.get("ebitda_yoy_growth_ttm") or row.get("ebitda_yoy"))),
        "earn_growth": earn_growth,
        "earn_growth_source": earn_source or "",
        "opm": _round(row.get("operating_margin_ttm") or row.get("operating_margin") or row.get("opm")),
        "pe": _round(row.get("price_earnings_ttm") or row.get("pe")),
        "pe_fwd": _round(row.get("pe_fwd")),
        "evebitda": _round(row.get("enterprise_value_ebitda_ttm") or row.get("evebitda")),
        "evebitda_fwd": _round(row.get("evebitda_fwd")),
        "fp_pe_y1": _round(row.get("fp_pe_y1")),
        "fp_rev_y1": _round(row.get("fp_rev_y1"), 0),
        "fp_ni_y1": _round(row.get("fp_ni_y1"), 0),
        "rsi": _round(row.get("RSI") if row.get("RSI") is not None else row.get("rsi")),
        "relvol": _round(row.get("relative_volume_10d_calc") if row.get("relative_volume_10d_calc") is not None else row.get("relvol"), 2),
        "vs50": _round(pct_vs(close, row.get("SMA50") or row.get("sma50"))),
        "rng": _round(
            range_position_pct(
                close,
                row.get("price_52_week_low") or row.get("lo52"),
                row.get("price_52_week_high") or row.get("hi52"),
            )
        ),
        "pt": _round(row.get("price_target_average") or row.get("pt"), 2),
    }
    gap = None
    if earn_growth is not None and rev_growth is not None:
        gap = round(earn_growth - rev_growth, 1)
    out["earn_minus_rev"] = gap
    out["earn_outrun"] = bool(gap is not None and gap >= EARN_OUTRUN_GAP)
    return out


def _stamp_peers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    attached = attach_vs_group(
        rows,
        group_field="ind",
        fields=("pe_fwd", "evebitda_fwd", "pe", "rev_growth", "earn_growth"),
        min_n=PEER_MIN_N,
    )
    for rec in attached:
        for field in ("pe_fwd", "evebitda_fwd", "pe"):
            rec[f"{field}_peer"] = peer_bin(rec.get(f"{field}_vs_ind"))
    return attached


def _clears(value: Any, floor: float) -> bool:
    number = to_float(value)
    return number is not None and number >= floor


def lane_flags(row: Mapping[str, Any], spec: GrowthRadarSpec) -> str:
    revenue = _clears(row.get("rev_growth"), spec.min_growth_pct)
    earnings = _clears(row.get("earn_growth"), spec.min_growth_pct)
    if revenue and earnings:
        return "revenue+earnings"
    if revenue:
        return "revenue"
    if earnings:
        return "earnings"
    return ""


def mark_overlooked(row: dict[str, Any], spec: GrowthRadarSpec) -> None:
    """Discount to peers on a forward multiple, and range not already crowded."""
    cheap = row.get("pe_fwd_peer") == "DISCOUNT" or row.get("evebitda_fwd_peer") == "DISCOUNT"
    rng = to_float(row.get("rng"))
    quiet = rng is None or rng < spec.overlooked_rng_max
    row["overlooked"] = bool(cheap and quiet and row.get("lanes"))


def _rank_lane(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
    tie: str,
    spec: GrowthRadarSpec,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not rows:
        return [], []
    ordered = top_n(
        rows,
        field=field,
        n=len(rows),
        reverse=True,
        tie_breaker=tie,
    )
    kept, overflow = group_capped_top_n(
        ordered,
        group_field="ind",
        cap=spec.industry_cap,
        n=spec.top,
        id_field="symbol",
        overflow_fields=(field, tie, "mcap", "lanes"),
    )
    ranked: list[dict[str, Any]] = []
    for index, row in enumerate(kept, start=1):
        rec = dict(row)
        rec["rank"] = index
        ranked.append(rec)
    return ranked, overflow


def build_growth_tables(
    rows: Sequence[Mapping[str, Any]],
    spec: GrowthRadarSpec | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Split a prepared universe into revenue, earnings, and overlooked tables.

    ``rows`` already carry quoted growth and peer columns. Market cap is not
    re-checked here; the loader applies threshold 1.
    """
    spec = spec or GrowthRadarSpec()
    universe: list[dict[str, Any]] = []
    revenue_pool: list[dict[str, Any]] = []
    earnings_pool: list[dict[str, Any]] = []
    for raw in rows:
        rec = dict(raw)
        lanes = lane_flags(rec, spec)
        rec["lanes"] = lanes
        mark_overlooked(rec, spec)
        universe.append(rec)
        if "revenue" in lanes:
            revenue_pool.append(rec)
        if "earnings" in lanes:
            earnings_pool.append(rec)
    revenue, revenue_overflow = _rank_lane(
        revenue_pool, field="rev_growth", tie="earn_growth", spec=spec
    )
    earnings, earnings_overflow = _rank_lane(
        earnings_pool, field="earn_growth", tie="rev_growth", spec=spec
    )
    overlooked = [row for row in revenue + earnings if row.get("overlooked")]
    # A name on both ranked lists should appear once, revenue rank first.
    seen: set[str] = set()
    overlooked_unique: list[dict[str, Any]] = []
    for row in overlooked:
        symbol = str(row.get("symbol") or "")
        if symbol in seen:
            continue
        seen.add(symbol)
        overlooked_unique.append(dict(row))
    return {
        "universe": universe,
        "revenue_growth": revenue,
        "revenue_overflow": revenue_overflow,
        "earnings_delivery": earnings,
        "earnings_overflow": earnings_overflow,
        "overlooked": overlooked_unique,
    }


def apply_hist_span(
    tables: dict[str, list[dict[str, Any]]],
    span_rows: Sequence[Mapping[str, Any]],
    *,
    spec: GrowthRadarSpec,
) -> None:
    """Join a shortlist PE span. ``pe_pct`` negative means the multiple got cheaper."""
    by_symbol = {str(row.get("symbol") or ""): row for row in span_rows}
    for name in ("universe", "revenue_growth", "earnings_delivery", "overlooked"):
        for rec in tables.get(name) or []:
            span = by_symbol.get(str(rec.get("symbol") or ""))
            n = int(to_float(span.get("n")) or 0) if span else 0
            pe_pct = to_float(span.get("pe_pct")) if span else None
            rec["hist_n"] = n or None
            rec["pe_hist_pct"] = None if pe_pct is None else round(pe_pct, 1)
            if n < 4 or pe_pct is None:
                rec["hist_pe"] = "THIN_HIST"
            elif pe_pct <= spec.hist_cheap_pct:
                rec["hist_pe"] = "CHEAPER"
            elif pe_pct >= PREMIUM_PCT:
                rec["hist_pe"] = "RICHER"
            else:
                rec["hist_pe"] = "FLAT"


def prepare_universe(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Quote growth, then peer gaps on the whole loaded set (the $1B peer group)."""
    prepared = [prepare_growth_row(row) for row in rows if str(row.get("symbol") or "")]
    prepared = [dict(row) for row in drop_duplicate_id_suffixes(prepared)]
    return _stamp_peers(prepared)


def highlights_text(
    *,
    spec: GrowthRadarSpec,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    sources: Mapping[str, Any],
) -> str:
    """Readable shortlist. The DuckDB keeps every column; this file is the scan headline."""
    lines = [
        "Growth radar",
        f"all_fields={sources.get('all_fields_db')}",
        f"all_fields_run={sources.get('all_fields_run_id')}",
        f"threshold_1_min_market_cap_usd={spec.min_market_cap_usd:.0f}",
        f"threshold_2_min_growth_pct={spec.min_growth_pct}",
        "Revenue lane sorts by rev_growth. Earnings lane sorts by earn_growth. They are not added together.",
        f"universe={len(tables.get('universe') or [])}",
        f"revenue_growth={len(tables.get('revenue_growth') or [])} overflow={len(tables.get('revenue_overflow') or [])}",
        f"earnings_delivery={len(tables.get('earnings_delivery') or [])} overflow={len(tables.get('earnings_overflow') or [])}",
        f"overlooked={len(tables.get('overlooked') or [])}",
        "",
        "Revenue growth",
    ]
    lines.extend(_highlight_rows(tables.get("revenue_growth") or [], "rev_growth"))
    lines.append("")
    lines.append("Earnings delivery")
    lines.extend(_highlight_rows(tables.get("earnings_delivery") or [], "earn_growth"))
    lines.append("")
    lines.append("Overlooked — forward multiple DISCOUNT and range under the spec max")
    lines.extend(_highlight_rows(tables.get("overlooked") or [], "rev_growth"))
    outrun = [
        row
        for row in (tables.get("earnings_delivery") or [])
        if row.get("earn_outrun")
    ]
    lines.append("")
    lines.append("Earnings outrun revenue by 10 points or more")
    if not outrun:
        lines.append("  (none on the earnings lane)")
    else:
        lines.extend(_highlight_rows(outrun, "earn_growth"))
    return "\n".join(lines) + "\n"


def _highlight_rows(rows: Sequence[Mapping[str, Any]], field: str) -> list[str]:
    if not rows:
        return ["  (none)"]
    out = []
    for row in rows:
        out.append(
            "  {rank} {symbol} {ind} {field}={value} src={source} "
            "rev={rev} earn={earn} pe_fwd={pe} {peer} {gap}% rng={rng} "
            "hist={hist} overlooked={flag}".format(
                rank=row.get("rank") or "-",
                symbol=row.get("symbol"),
                ind=row.get("ind") or "-",
                field=field,
                value=row.get(field),
                source=row.get("rev_growth_source") if field == "rev_growth" else row.get("earn_growth_source"),
                rev=row.get("rev_growth"),
                earn=row.get("earn_growth"),
                pe=row.get("pe_fwd"),
                peer=row.get("pe_fwd_peer"),
                gap=row.get("pe_fwd_vs_ind"),
                rng=row.get("rng"),
                hist=row.get("hist_pe") or "-",
                flag=row.get("overlooked"),
            )
        )
    return out
