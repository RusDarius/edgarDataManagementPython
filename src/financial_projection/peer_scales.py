"""Multi-angle peer scales for projection assumptions and peer-lane views.

Core path defaults to **industry** when large enough (sector → global fallback).
Additional views (mcap / revenue / growth-maturity / profitable) are always
computed when possible so the peer lane can compare angles without silently
overwriting the industry anchor.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Mapping, Sequence

from .fields import PEER_METRIC_FIELDS

# Named peer views surfaced on every symbol (when buildable).
PEER_VIEW_INDUSTRY = "industry"
PEER_VIEW_MCAP = "industry_mcap"
PEER_VIEW_REV = "industry_rev"
PEER_VIEW_GROWTH = "industry_growth"
PEER_VIEW_PROFITABLE = "industry_profitable"
PEER_VIEW_SECTOR = "sector"
PEER_VIEW_GLOBAL = "global"
PEER_VIEW_CUSTOM = "custom_group"

DEFAULT_PEER_VIEW_ORDER: tuple[str, ...] = (
    PEER_VIEW_INDUSTRY,
    PEER_VIEW_MCAP,
    PEER_VIEW_REV,
    PEER_VIEW_GROWTH,
    PEER_VIEW_PROFITABLE,
    PEER_VIEW_SECTOR,
    PEER_VIEW_GLOBAL,
    PEER_VIEW_CUSTOM,
)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _pct_to_fraction(value: float | None) -> float | None:
    if value is None:
        return None
    if abs(value) > 2.5:
        return value / 100.0
    return value


def _trim_sorted(values: list[float], trim_fraction: float) -> list[float]:
    if not values:
        return []
    if trim_fraction <= 0 or len(values) < 20:
        return list(values)
    trim_each = int(len(values) * trim_fraction)
    if trim_each <= 0 or trim_each * 2 >= len(values):
        return list(values)
    return values[trim_each : len(values) - trim_each]


def _collect_metric_values(
    rows: Sequence[Mapping[str, Any]], field_name: str
) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = _safe_float(row.get(field_name))
        if value is None:
            continue
        if field_name.startswith(
            ("enterprise_value", "price_earnings", "price_sales", "non_gaap")
        ) and value <= 0:
            continue
        values.append(value)
    return values


def _build_stats(
    rows: Sequence[Mapping[str, Any]],
    *,
    trim_fraction: float,
) -> dict[str, dict[str, float | int]]:
    stats: dict[str, dict[str, float | int]] = {}
    for field_name in PEER_METRIC_FIELDS:
        values = sorted(_collect_metric_values(rows, field_name))
        trimmed = _trim_sorted(values, trim_fraction)
        if not trimmed:
            continue
        q25 = float(trimmed[max(0, int(0.25 * (len(trimmed) - 1)))])
        q75 = float(trimmed[min(len(trimmed) - 1, int(0.75 * (len(trimmed) - 1)))])
        med = float(median(trimmed))
        iqr = q75 - q25
        stats[field_name] = {
            "median": med,
            "count": len(trimmed),
            "raw_count": len(values),
            "p25": q25,
            "p75": q75,
            "iqr": iqr,
            "iqr_over_median": (iqr / med) if med else 0.0,
        }
    return stats


def _mcap_band_peers(
    rows: Sequence[Mapping[str, Any]],
    *,
    market_cap: float,
    band_low: float,
    band_high: float,
) -> list[Mapping[str, Any]]:
    low = market_cap * band_low
    high = market_cap * band_high
    selected: list[Mapping[str, Any]] = []
    for row in rows:
        peer_mcap = _safe_float(row.get("market_cap_basic"))
        if peer_mcap is None or peer_mcap <= 0:
            continue
        if low <= peer_mcap <= high:
            selected.append(row)
    return selected


def _revenue_band_peers(
    rows: Sequence[Mapping[str, Any]],
    *,
    revenue: float,
    band_low: float,
    band_high: float,
) -> list[Mapping[str, Any]]:
    low = revenue * band_low
    high = revenue * band_high
    selected: list[Mapping[str, Any]] = []
    for row in rows:
        peer_rev = _safe_float(row.get("total_revenue_ttm"))
        if peer_rev is None:
            peer_rev = _safe_float(row.get("total_revenue"))
        if peer_rev is None or peer_rev <= 0:
            continue
        if low <= peer_rev <= high:
            selected.append(row)
    return selected


def _growth_band_peers(
    rows: Sequence[Mapping[str, Any]],
    *,
    growth_fraction: float,
    abs_pp: float,
    rel_low: float,
    rel_high: float,
) -> list[Mapping[str, Any]]:
    """
    Maturity / growth-stage peers.

    Prefer absolute ±pp band when |growth| is moderate; switch to relative
    band when growth is extreme so hypergrowth names stay comparable.
    """
    selected: list[Mapping[str, Any]] = []
    use_relative = abs(growth_fraction) >= 0.50
    for row in rows:
        peer_g = _pct_to_fraction(_safe_float(row.get("total_revenue_yoy_growth_ttm")))
        if peer_g is None:
            peer_g = _pct_to_fraction(_safe_float(row.get("total_revenue_cagr_5y")))
        if peer_g is None:
            continue
        if use_relative and abs(growth_fraction) > 1e-9:
            ratio = peer_g / growth_fraction
            if rel_low <= ratio <= rel_high:
                selected.append(row)
        else:
            if abs(peer_g - growth_fraction) <= abs_pp:
                selected.append(row)
    return selected


def _profitable_peers(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    selected: list[Mapping[str, Any]] = []
    for row in rows:
        ebitda = _safe_float(row.get("ebitda"))
        eve = _safe_float(row.get("enterprise_value_ebitda_ttm"))
        if ebitda is not None and ebitda > 0:
            selected.append(row)
        elif eve is not None and eve > 0:
            selected.append(row)
    return selected


def _expand_mcap_band_peers(
    rows: Sequence[Mapping[str, Any]],
    *,
    market_cap: float,
    band_low: float,
    band_high: float,
    min_peer_group_size: int,
    expand_steps: Sequence[tuple[float, float]] | None = None,
) -> tuple[list[Mapping[str, Any]], float, float]:
    """Try primary band, then wider bands until min size or exhausted."""
    steps = list(expand_steps or ())
    steps.insert(0, (band_low, band_high))
    # Always end with a very wide attempt before giving up.
    if (0.10, 10.0) not in steps:
        steps.append((0.10, 10.0))
    last_rows: list[Mapping[str, Any]] = []
    last_lo, last_hi = band_low, band_high
    for lo, hi in steps:
        last_lo, last_hi = lo, hi
        last_rows = _mcap_band_peers(
            rows, market_cap=market_cap, band_low=lo, band_high=hi
        )
        if len(last_rows) >= min_peer_group_size:
            return last_rows, lo, hi
    return last_rows, last_lo, last_hi


def _view_payload(
    *,
    view_name: str,
    peer_stats: Mapping[str, Mapping[str, float | int]],
    company_row: Mapping[str, Any],
    relative_scale_clip_low: float,
    relative_scale_clip_high: float,
    band_low: float | None = None,
    band_high: float | None = None,
) -> dict[str, Any] | None:
    ev_key = "enterprise_value_to_revenue_ttm"
    peer_n = int(peer_stats.get(ev_key, {}).get("count", 0) or 0)
    if peer_n <= 0:
        return None

    ev_rev_peer = _safe_float(peer_stats.get(ev_key, {}).get("median"))
    ev_ebitda_peer = _safe_float(
        peer_stats.get("enterprise_value_ebitda_ttm", {}).get("median")
    )
    rev_growth_peer = _safe_float(
        peer_stats.get("total_revenue_yoy_growth_ttm", {}).get("median")
    )
    rev_cagr_peer = _safe_float(
        peer_stats.get("total_revenue_cagr_5y", {}).get("median")
    )
    if rev_growth_peer is None:
        rev_growth_peer = rev_cagr_peer

    company_ev_rev = _safe_float(company_row.get("enterprise_value_to_revenue_ttm"))
    company_growth = _pct_to_fraction(
        _safe_float(company_row.get("total_revenue_yoy_growth_ttm"))
    )
    company_cagr = _pct_to_fraction(
        _safe_float(company_row.get("total_revenue_cagr_5y"))
    )
    if company_growth is None:
        company_growth = company_cagr
    # Peer growth medians are stored in raw TV units (often percent).
    peer_growth_frac = _pct_to_fraction(rev_growth_peer)
    peer_cagr_frac = _pct_to_fraction(rev_cagr_peer)

    ev_rev_rel = None
    if company_ev_rev is not None and ev_rev_peer and ev_rev_peer > 0:
        ev_rev_rel = _clip(
            company_ev_rev / ev_rev_peer,
            relative_scale_clip_low,
            relative_scale_clip_high,
        )

    growth_rel = None
    if (
        company_growth is not None
        and peer_growth_frac is not None
        and abs(peer_growth_frac) > 1e-9
    ):
        growth_rel = _clip(
            company_growth / peer_growth_frac,
            relative_scale_clip_low,
            relative_scale_clip_high,
        )

    cagr_rel = None
    if (
        company_cagr is not None
        and peer_cagr_frac is not None
        and abs(peer_cagr_frac) > 1e-9
    ):
        cagr_rel = _clip(
            company_cagr / peer_cagr_frac,
            relative_scale_clip_low,
            relative_scale_clip_high,
        )

    dispersion = _safe_float(peer_stats.get(ev_key, {}).get("iqr_over_median"))
    return {
        "view": view_name,
        "peer_n": peer_n,
        "ev_rev_peer_median": ev_rev_peer,
        "ev_ebitda_peer_median": ev_ebitda_peer,
        "rev_growth_peer_median": rev_growth_peer,
        "rev_cagr_peer_median": rev_cagr_peer,
        "ev_rev_relative_scale": ev_rev_rel,
        "rev_growth_relative_scale": growth_rel,
        "rev_cagr_relative_scale": cagr_rel,
        "dispersion_iqr_over_median": dispersion,
        "band_low": band_low,
        "band_high": band_high,
        "peer_metric_medians": {
            field: peer_stats.get(field, {}).get("median")
            for field in PEER_METRIC_FIELDS
            if field in peer_stats
        },
    }


def _promote_default_view(
    views: Mapping[str, Mapping[str, Any]],
    *,
    prefer_industry: bool = True,
) -> str:
    """Pick the core-path peer view. Industry wins when available."""
    if prefer_industry and PEER_VIEW_INDUSTRY in views:
        return PEER_VIEW_INDUSTRY
    if PEER_VIEW_CUSTOM in views:
        return PEER_VIEW_CUSTOM
    for name in (
        PEER_VIEW_INDUSTRY,
        PEER_VIEW_MCAP,
        PEER_VIEW_SECTOR,
        PEER_VIEW_GLOBAL,
    ):
        if name in views:
            return name
    return next(iter(views), PEER_VIEW_GLOBAL)


def _suggest_view(
    views: Mapping[str, Mapping[str, Any]],
    *,
    view_trust: Mapping[str, float],
    default_view: str,
) -> str:
    """Highest-trust view among built angles; ties keep default."""
    best_name = default_view
    best_trust = float(view_trust.get(default_view, 0.0))
    for name, trust in view_trust.items():
        if name not in views:
            continue
        if trust > best_trust + 1e-9:
            best_name = name
            best_trust = trust
    return best_name


def view_trust_score(
    *,
    peer_n: int,
    min_peer_n_trust: int,
    valuation_lens: str,
    ev_rev_relative_scale: float | None,
    relative_scale_clip_high: float,
    dispersion_iqr_over_median: float | None,
    dispersion_soft_threshold: float = 1.25,
    dispersion_hard_threshold: float = 2.0,
) -> float:
    """0..1 trust for a single peer view."""
    if valuation_lens == "unsuitable":
        return 0.0
    trust = 1.0
    if peer_n < max(1, int(min_peer_n_trust)):
        trust *= 0.45 if peer_n >= 8 else 0.2
    if (
        ev_rev_relative_scale is not None
        and relative_scale_clip_high > 0
        and ev_rev_relative_scale >= relative_scale_clip_high * 0.999
    ):
        trust *= 0.55
    if dispersion_iqr_over_median is not None:
        if dispersion_iqr_over_median >= dispersion_hard_threshold:
            trust *= 0.40
        elif dispersion_iqr_over_median >= dispersion_soft_threshold:
            trust *= 0.70
    return _clip(trust, 0.0, 1.0)


def build_peer_scale_context(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_peer_group_size: int = 8,
    peer_trim_fraction: float = 0.05,
    relative_scale_clip_low: float = 0.25,
    relative_scale_clip_high: float = 3.0,
    peer_mcap_band_low: float = 0.25,
    peer_mcap_band_high: float = 4.0,
    peer_mcap_refine_min_industry_n: int = 0,
    peer_revenue_band_low: float = 0.40,
    peer_revenue_band_high: float = 2.50,
    peer_growth_abs_pp: float = 0.15,
    peer_growth_rel_low: float = 0.50,
    peer_growth_rel_high: float = 2.00,
    min_peer_n_trust: int = 15,
    dispersion_soft_threshold: float = 1.25,
    dispersion_hard_threshold: float = 2.0,
    custom_peer_mode: bool = False,
) -> dict[str, dict[str, Any]]:
    """
    Build per-symbol peer context with multi-angle views.

    ``peer_mcap_refine_min_industry_n`` is retained for compatibility but no
    longer gates mcap view construction (mcap is always attempted when the
    industry set exists). Core path still defaults to industry.

    When ``custom_peer_mode`` is True, ``rows`` itself is the peer universe and
    the primary view is ``custom_group`` (plus size/growth/profitable angles
    within that set).
    """
    global_stats = _build_stats(rows, trim_fraction=peer_trim_fraction)

    by_sector: dict[str, list[Mapping[str, Any]]] = {}
    by_industry: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        sector = str(row.get("sector") or "").strip() or "Unknown"
        industry = str(row.get("industry") or "").strip() or "Unknown"
        by_sector.setdefault(sector, []).append(row)
        by_industry.setdefault((sector, industry), []).append(row)

    sector_stats = {
        sector: _build_stats(group, trim_fraction=peer_trim_fraction)
        for sector, group in by_sector.items()
    }
    industry_stats = {
        key: _build_stats(group, trim_fraction=peer_trim_fraction)
        for key, group in by_industry.items()
    }

    # Silence unused legacy gate while keeping the kwarg for call sites.
    _ = peer_mcap_refine_min_industry_n

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        sector = str(row.get("sector") or "").strip() or "Unknown"
        industry = str(row.get("industry") or "").strip() or "Unknown"
        market_cap = _safe_float(row.get("market_cap_basic"))
        revenue = _safe_float(row.get("total_revenue_ttm"))
        if revenue is None:
            revenue = _safe_float(row.get("total_revenue"))
        growth_frac = _pct_to_fraction(
            _safe_float(row.get("total_revenue_yoy_growth_ttm"))
        )
        if growth_frac is None:
            growth_frac = _pct_to_fraction(
                _safe_float(row.get("total_revenue_cagr_5y"))
            )
        own_ebitda = _safe_float(row.get("ebitda"))
        own_eve = _safe_float(row.get("enterprise_value_ebitda_ttm"))
        own_profitable = (own_ebitda is not None and own_ebitda > 0) or (
            own_eve is not None and own_eve > 0
        )

        industry_key = (sector, industry)
        industry_rows = by_industry.get(industry_key, [])
        industry_group = industry_stats.get(industry_key, {})
        sector_group = sector_stats.get(sector, {})
        sector_rows = by_sector.get(sector, [])

        views: dict[str, dict[str, Any]] = {}

        def _add_view(
            name: str,
            stats: Mapping[str, Mapping[str, float | int]],
            *,
            min_n: int | None = None,
            band_low: float | None = None,
            band_high: float | None = None,
        ) -> None:
            required = min_peer_group_size if min_n is None else min_n
            n = int(stats.get("enterprise_value_to_revenue_ttm", {}).get("count", 0) or 0)
            if n < required:
                return
            payload = _view_payload(
                view_name=name,
                peer_stats=stats,
                company_row=row,
                relative_scale_clip_low=relative_scale_clip_low,
                relative_scale_clip_high=relative_scale_clip_high,
                band_low=band_low,
                band_high=band_high,
            )
            if payload is not None:
                views[name] = payload

        if custom_peer_mode:
            custom_stats = global_stats
            _add_view(PEER_VIEW_CUSTOM, custom_stats, min_n=min_peer_group_size)
            base_rows_for_angles = list(rows)
        else:
            industry_n = int(
                industry_group.get("enterprise_value_to_revenue_ttm", {}).get(
                    "count", 0
                )
                or 0
            )
            sector_n = int(
                sector_group.get("enterprise_value_to_revenue_ttm", {}).get("count", 0)
                or 0
            )
            if industry_n >= min_peer_group_size:
                _add_view(PEER_VIEW_INDUSTRY, industry_group)
            if sector_n >= min_peer_group_size:
                _add_view(PEER_VIEW_SECTOR, sector_group)
            _add_view(PEER_VIEW_GLOBAL, global_stats, min_n=min_peer_group_size)
            base_rows_for_angles = industry_rows if industry_n >= min_peer_group_size else []

        # Size / maturity / profitability angles inside industry (or custom set).
        angle_rows = base_rows_for_angles
        if angle_rows:
            if market_cap is not None and market_cap > 0:
                band_rows, used_lo, used_hi = _expand_mcap_band_peers(
                    angle_rows,
                    market_cap=market_cap,
                    band_low=peer_mcap_band_low,
                    band_high=peer_mcap_band_high,
                    min_peer_group_size=min_peer_group_size,
                    expand_steps=(
                        (peer_mcap_band_low, peer_mcap_band_high),
                        (0.20, 5.0),
                        (0.15, 6.0),
                        (0.10, 8.0),
                    ),
                )
                band_stats = _build_stats(band_rows, trim_fraction=peer_trim_fraction)
                _add_view(
                    PEER_VIEW_MCAP,
                    band_stats,
                    band_low=used_lo,
                    band_high=used_hi,
                )

            if revenue is not None and revenue > 0:
                rev_rows = _revenue_band_peers(
                    angle_rows,
                    revenue=revenue,
                    band_low=peer_revenue_band_low,
                    band_high=peer_revenue_band_high,
                )
                rev_stats = _build_stats(rev_rows, trim_fraction=peer_trim_fraction)
                _add_view(
                    PEER_VIEW_REV,
                    rev_stats,
                    band_low=peer_revenue_band_low,
                    band_high=peer_revenue_band_high,
                )

            if growth_frac is not None:
                growth_rows = _growth_band_peers(
                    angle_rows,
                    growth_fraction=growth_frac,
                    abs_pp=peer_growth_abs_pp,
                    rel_low=peer_growth_rel_low,
                    rel_high=peer_growth_rel_high,
                )
                growth_stats = _build_stats(
                    growth_rows, trim_fraction=peer_trim_fraction
                )
                _add_view(PEER_VIEW_GROWTH, growth_stats)

            if own_profitable:
                profit_rows = _profitable_peers(angle_rows)
                profit_stats = _build_stats(
                    profit_rows, trim_fraction=peer_trim_fraction
                )
                _add_view(PEER_VIEW_PROFITABLE, profit_stats)

        if not views:
            # Force global even if thin so every symbol has a peer payload.
            payload = _view_payload(
                view_name=PEER_VIEW_GLOBAL,
                peer_stats=global_stats,
                company_row=row,
                relative_scale_clip_low=relative_scale_clip_low,
                relative_scale_clip_high=relative_scale_clip_high,
            )
            if payload is not None:
                views[PEER_VIEW_GLOBAL] = payload

        default_view = _promote_default_view(
            views, prefer_industry=not custom_peer_mode
        )
        default = views.get(default_view, {})

        view_trust: dict[str, float] = {}
        for name, payload in views.items():
            view_trust[name] = view_trust_score(
                peer_n=int(payload.get("peer_n") or 0),
                min_peer_n_trust=min_peer_n_trust,
                valuation_lens="ev_revenue",  # lens applied later in lanes
                ev_rev_relative_scale=_safe_float(payload.get("ev_rev_relative_scale")),
                relative_scale_clip_high=relative_scale_clip_high,
                dispersion_iqr_over_median=_safe_float(
                    payload.get("dispersion_iqr_over_median")
                ),
                dispersion_soft_threshold=dispersion_soft_threshold,
                dispersion_hard_threshold=dispersion_hard_threshold,
            )
            payload["trust"] = view_trust[name]

        suggested = _suggest_view(
            views, view_trust=view_trust, default_view=default_view
        )

        # Agreement: share of non-default views whose rich/cheap side matches.
        default_rel = _safe_float(default.get("ev_rev_relative_scale"))
        agreement = None
        comparable = 0
        matching = 0
        if default_rel is not None:
            default_side = (
                "rich"
                if default_rel >= 2.0
                else "cheap"
                if default_rel <= 0.6
                else "mid"
            )
            for name, payload in views.items():
                if name == default_view:
                    continue
                rel = _safe_float(payload.get("ev_rev_relative_scale"))
                if rel is None:
                    continue
                side = "rich" if rel >= 2.0 else "cheap" if rel <= 0.6 else "mid"
                comparable += 1
                if side == default_side:
                    matching += 1
            if comparable > 0:
                agreement = matching / comparable

        out[symbol] = {
            "peer_scope": default_view,
            "peer_n": int(default.get("peer_n") or 0),
            "sector": sector,
            "industry": industry,
            "ev_rev_peer_median": default.get("ev_rev_peer_median"),
            "ev_ebitda_peer_median": default.get("ev_ebitda_peer_median"),
            "rev_growth_peer_median": default.get("rev_growth_peer_median"),
            "rev_cagr_peer_median": default.get("rev_cagr_peer_median"),
            "ev_rev_relative_scale": default.get("ev_rev_relative_scale"),
            "rev_growth_relative_scale": default.get("rev_growth_relative_scale"),
            "rev_cagr_relative_scale": default.get("rev_cagr_relative_scale"),
            "peer_metric_medians": default.get("peer_metric_medians") or {},
            "peer_dispersion_iqr_over_median": default.get(
                "dispersion_iqr_over_median"
            ),
            "peer_views": views,
            "peer_view_trust": view_trust,
            "peer_view_suggested": suggested,
            "peer_view_agreement": agreement,
            "peer_view_names": [
                name for name in DEFAULT_PEER_VIEW_ORDER if name in views
            ],
            "custom_peer_mode": bool(custom_peer_mode),
            # retained for diagnostics / sector fallback visibility
            "sector_peer_n": int(
                sector_group.get("enterprise_value_to_revenue_ttm", {}).get("count", 0)
                or 0
            )
            if not custom_peer_mode
            else len(rows),
            "industry_peer_n": int(
                industry_group.get("enterprise_value_to_revenue_ttm", {}).get(
                    "count", 0
                )
                or 0
            )
            if not custom_peer_mode
            else len(rows),
        }
        # Avoid unused var warning for sector_rows in custom mode.
        _ = sector_rows
    return out
