"""Four parallel decision lanes: core, street, history, peer (multi-view)."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .config import ScenarioParams
from .peer_scales import (
    PEER_VIEW_GROWTH,
    PEER_VIEW_INDUSTRY,
    PEER_VIEW_MCAP,
    PEER_VIEW_PROFITABLE,
    PEER_VIEW_REV,
    view_trust_score,
)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_to_fraction(value: float | None) -> float | None:
    if value is None:
        return None
    if abs(value) > 2.5:
        return value / 100.0
    return value


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def peer_lane_trust(
    *,
    peer_n: int,
    min_peer_n_trust: int,
    valuation_lens: str,
    ev_rev_relative_scale: float | None,
    relative_scale_clip_high: float,
    dispersion_iqr_over_median: float | None = None,
    dispersion_soft_threshold: float = 1.25,
    dispersion_hard_threshold: float = 2.0,
) -> float:
    """
    0..1 trust in the default peer-relative signal.

    Low when peers are thin, lens unsuitable, relative scale is pinned at the
    clip, or the peer-set EV/Rev dispersion is high.
    """
    return view_trust_score(
        peer_n=peer_n,
        min_peer_n_trust=min_peer_n_trust,
        valuation_lens=valuation_lens,
        ev_rev_relative_scale=ev_rev_relative_scale,
        relative_scale_clip_high=relative_scale_clip_high,
        dispersion_iqr_over_median=dispersion_iqr_over_median,
        dispersion_soft_threshold=dispersion_soft_threshold,
        dispersion_hard_threshold=dispersion_hard_threshold,
    )


def history_lane_trust(
    *,
    peer_trust: float,
    cagr_fraction: float | None,
    yoy_fraction: float | None,
) -> float:
    """Own-history trust; peer pollution only partially damps it."""
    if cagr_fraction is None and yoy_fraction is None:
        return 0.0
    trust = 0.85
    if cagr_fraction is not None and yoy_fraction is not None:
        gap = abs(yoy_fraction - cagr_fraction)
        if gap >= 0.35:
            trust *= 0.6
        elif gap >= 0.20:
            trust *= 0.8
    trust = 0.65 * trust + 0.35 * trust * max(peer_trust, 0.35)
    return _clip(trust, 0.0, 1.0)


def suggest_history_coeff_adjustments(
    *,
    scenario: ScenarioParams,
    cagr_fraction: float | None,
    yoy_fraction: float | None,
    street_fy_fraction: float | None,
    peer_cagr_fraction: float | None,
    hist_trust: float,
    peer_trust: float,
    adj_clip_low: float = 0.70,
    adj_clip_high: float = 1.30,
) -> dict[str, Any]:
    """
    Suggest multiplicative tweaks to global scenario coeffs from history.

    Does **not** replace core coeffs. Returns adjustments + an adjusted
    ScenarioParams for an optional parallel hist path.
    """
    hist_anchor = cagr_fraction if cagr_fraction is not None else yoy_fraction
    growth_scale_adj = 1.0
    fade_adj = 1.0
    terminal_mult_adj = 1.0
    notes: list[str] = []

    if hist_anchor is not None and street_fy_fraction is not None and hist_trust > 0:
        gap = street_fy_fraction - hist_anchor
        growth_scale_adj -= _clip(gap, -0.50, 0.80) * 0.50 * hist_trust
        if gap > 0.15:
            notes.append("street_above_history")
        elif gap < -0.10:
            notes.append("street_below_history")

    if (
        cagr_fraction is not None
        and peer_cagr_fraction is not None
        and abs(peer_cagr_fraction) > 1e-9
        and peer_trust > 0
    ):
        rel = cagr_fraction / peer_cagr_fraction
        if rel > 1.25:
            fade_adj += 0.03 * peer_trust * hist_trust
            terminal_mult_adj += 0.04 * peer_trust * hist_trust
            notes.append("hist_cagr_above_peers")
        elif rel < 0.75:
            fade_adj -= 0.03 * peer_trust * hist_trust
            terminal_mult_adj -= 0.04 * peer_trust * hist_trust
            notes.append("hist_cagr_below_peers")

    growth_scale_adj = _clip(growth_scale_adj, adj_clip_low, adj_clip_high)
    fade_adj = _clip(fade_adj, adj_clip_low, adj_clip_high)
    terminal_mult_adj = _clip(terminal_mult_adj, adj_clip_low, adj_clip_high)

    adjusted = ScenarioParams(
        revenue_growth_override=scenario.revenue_growth_override,
        revenue_growth_scale=scenario.revenue_growth_scale * growth_scale_adj,
        growth_fade_per_year=_clip(
            scenario.growth_fade_per_year * fade_adj, 0.50, 1.05
        ),
        terminal_multiple_scale_vs_peer=(
            scenario.terminal_multiple_scale_vs_peer * terminal_mult_adj
        ),
        own_multiple_blend=scenario.own_multiple_blend,
        net_debt_growth_per_year=scenario.net_debt_growth_per_year,
        sgr_cap_multiplier=scenario.sgr_cap_multiplier,
    )
    return {
        "lane_hist_growth_scale_adj": growth_scale_adj,
        "lane_hist_fade_adj": fade_adj,
        "lane_hist_terminal_mult_adj": terminal_mult_adj,
        "lane_hist_coeff_notes": "|".join(notes) if notes else "",
        "adjusted_scenario": adjusted,
    }


def _recompute_view_trusts(
    peer_views: Mapping[str, Mapping[str, Any]],
    *,
    valuation_lens: str,
    min_peer_n_trust: int,
    relative_scale_clip_high: float,
    dispersion_soft_threshold: float,
    dispersion_hard_threshold: float,
) -> dict[str, float]:
    trusts: dict[str, float] = {}
    for name, payload in peer_views.items():
        trusts[name] = view_trust_score(
            peer_n=int(payload.get("peer_n") or 0),
            min_peer_n_trust=min_peer_n_trust,
            valuation_lens=valuation_lens,
            ev_rev_relative_scale=_safe_float(payload.get("ev_rev_relative_scale")),
            relative_scale_clip_high=relative_scale_clip_high,
            dispersion_iqr_over_median=_safe_float(
                payload.get("dispersion_iqr_over_median")
            ),
            dispersion_soft_threshold=dispersion_soft_threshold,
            dispersion_hard_threshold=dispersion_hard_threshold,
        )
    return trusts


def _flatten_view_fields(
    peer_views: Mapping[str, Mapping[str, Any]],
    view_trust: Mapping[str, float],
) -> dict[str, Any]:
    """Emit stable columns for the main peer angles."""
    out: dict[str, Any] = {}
    for key, prefix in (
        (PEER_VIEW_INDUSTRY, "industry"),
        (PEER_VIEW_MCAP, "mcap"),
        (PEER_VIEW_REV, "rev"),
        (PEER_VIEW_GROWTH, "growth"),
        (PEER_VIEW_PROFITABLE, "profitable"),
    ):
        payload = peer_views.get(key) or {}
        out[f"lane_peer_{prefix}_n"] = (
            int(payload["peer_n"]) if payload.get("peer_n") is not None else None
        )
        out[f"lane_peer_{prefix}_ev_rev_median"] = _safe_float(
            payload.get("ev_rev_peer_median")
        )
        out[f"lane_peer_{prefix}_ev_rev_rel"] = _safe_float(
            payload.get("ev_rev_relative_scale")
        )
        out[f"lane_peer_{prefix}_rev_cagr_median"] = _safe_float(
            payload.get("rev_cagr_peer_median")
        )
        out[f"lane_peer_{prefix}_dispersion"] = _safe_float(
            payload.get("dispersion_iqr_over_median")
        )
        out[f"lane_peer_{prefix}_trust"] = (
            float(view_trust[key]) if key in view_trust else None
        )
    return out


def build_decision_lanes(
    *,
    row: Mapping[str, Any],
    summary: Mapping[str, Any],
    scenario: ScenarioParams,
    min_peer_n_trust: int = 15,
    relative_scale_clip_high: float = 3.0,
    dispersion_soft_threshold: float = 1.25,
    dispersion_hard_threshold: float = 2.0,
) -> dict[str, Any]:
    """
    Emit four parallel lanes (core / street / history / peer).

    Peer lane carries multi-angle views (industry / mcap / rev / growth /
    profitable). Core ranking still uses the default industry-anchored path.
    """
    cagr_pct = _safe_float(row.get("total_revenue_cagr_5y"))
    yoy_pct = _safe_float(row.get("total_revenue_yoy_growth_ttm"))
    cagr_fraction = _pct_to_fraction(cagr_pct)
    yoy_fraction = _pct_to_fraction(yoy_pct)
    street_fy_fraction = _safe_float(summary.get("street_fy_rev_growth_fraction"))
    if street_fy_fraction is None:
        street_fy_pct = _safe_float(summary.get("st_next_fy_rev_growth_pct"))
        street_fy_fraction = (
            street_fy_pct / 100.0 if street_fy_pct is not None else None
        )

    peer_n = int(summary.get("peer_n") or 0)
    valuation_lens = str(summary.get("valuation_lens") or "ev_revenue")
    ev_rev_rel = _safe_float(summary.get("ev_rev_relative_scale"))
    dispersion = _safe_float(summary.get("peer_dispersion_iqr_over_median"))
    peer_cagr_pct = _safe_float(summary.get("rev_cagr_peer_median"))
    if peer_cagr_pct is None:
        peer_cagr_pct = _safe_float(
            (summary.get("peer_metric_medians") or {}).get("total_revenue_cagr_5y")
            if isinstance(summary.get("peer_metric_medians"), dict)
            else None
        )
    peer_cagr_fraction = _pct_to_fraction(peer_cagr_pct)
    cagr_rel = _safe_float(summary.get("rev_cagr_relative_scale"))

    raw_views = summary.get("peer_views") or {}
    if not isinstance(raw_views, dict):
        raw_views = {}

    view_trust = _recompute_view_trusts(
        raw_views,
        valuation_lens=valuation_lens,
        min_peer_n_trust=min_peer_n_trust,
        relative_scale_clip_high=relative_scale_clip_high,
        dispersion_soft_threshold=dispersion_soft_threshold,
        dispersion_hard_threshold=dispersion_hard_threshold,
    )

    peer_trust = peer_lane_trust(
        peer_n=peer_n,
        min_peer_n_trust=min_peer_n_trust,
        valuation_lens=valuation_lens,
        ev_rev_relative_scale=ev_rev_rel,
        relative_scale_clip_high=relative_scale_clip_high,
        dispersion_iqr_over_median=dispersion,
        dispersion_soft_threshold=dispersion_soft_threshold,
        dispersion_hard_threshold=dispersion_hard_threshold,
    )
    hist_trust = history_lane_trust(
        peer_trust=peer_trust,
        cagr_fraction=cagr_fraction,
        yoy_fraction=yoy_fraction,
    )

    hist_vs_street_gap_pp = None
    if cagr_fraction is not None and street_fy_fraction is not None:
        hist_vs_street_gap_pp = (cagr_fraction - street_fy_fraction) * 100.0
    elif yoy_fraction is not None and street_fy_fraction is not None:
        hist_vs_street_gap_pp = (yoy_fraction - street_fy_fraction) * 100.0

    hist_vs_yoy_gap_pp = None
    if cagr_fraction is not None and yoy_fraction is not None:
        hist_vs_yoy_gap_pp = (yoy_fraction - cagr_fraction) * 100.0

    coeff = suggest_history_coeff_adjustments(
        scenario=scenario,
        cagr_fraction=cagr_fraction,
        yoy_fraction=yoy_fraction,
        street_fy_fraction=street_fy_fraction,
        peer_cagr_fraction=peer_cagr_fraction,
        hist_trust=hist_trust,
        peer_trust=peer_trust,
    )

    core_upside = _safe_float(summary.get("terminal_upside_pct"))
    street_upside = _safe_float(summary.get("st_street_price_upside_pct"))

    view_names = summary.get("peer_view_names") or list(raw_views.keys())
    compact_views = {
        name: {
            "n": (raw_views.get(name) or {}).get("peer_n"),
            "ev_rev_med": (raw_views.get(name) or {}).get("ev_rev_peer_median"),
            "ev_rev_rel": (raw_views.get(name) or {}).get("ev_rev_relative_scale"),
            "disp": (raw_views.get(name) or {}).get("dispersion_iqr_over_median"),
            "trust": view_trust.get(name),
        }
        for name in view_names
        if name in raw_views
    }

    return {
        # Lane 1 — core (global scenario coeffs)
        "lane_core_upside_pct": core_upside,
        "lane_core_y1_upside_pct": _safe_float(summary.get("model_y1_upside_pct")),
        "lane_core_source": "global_scenario_coeffs",
        # Lane 2 — street / forecast
        "lane_street_upside_pct": street_upside,
        "lane_street_fy_rev_growth_pct": _safe_float(
            summary.get("st_next_fy_rev_growth_pct")
        ),
        "lane_street_outlook": summary.get("st_outlook") or "",
        "lane_street_source": "price_target_and_forecasts",
        # Lane 3 — history / CAGR / YoY
        "total_revenue_cagr_5y": cagr_pct,
        "total_revenue_yoy_growth_ttm": yoy_pct,
        "total_revenue_5y_growth_fy": _safe_float(row.get("total_revenue_5y_growth_fy")),
        "free_cash_flow_cagr_5y": _safe_float(row.get("free_cash_flow_cagr_5y")),
        "lane_hist_vs_street_gap_pp": hist_vs_street_gap_pp,
        "lane_hist_yoy_vs_cagr_gap_pp": hist_vs_yoy_gap_pp,
        "lane_hist_vs_peer_cagr_rel": cagr_rel,
        "lane_hist_trust": hist_trust,
        "lane_hist_growth_scale_adj": coeff["lane_hist_growth_scale_adj"],
        "lane_hist_fade_adj": coeff["lane_hist_fade_adj"],
        "lane_hist_terminal_mult_adj": coeff["lane_hist_terminal_mult_adj"],
        "lane_hist_coeff_notes": coeff["lane_hist_coeff_notes"],
        "lane_hist_source": "cagr_yoy_persistence",
        # Lane 4 — peer multi-view (default = industry when available)
        "lane_peer_scope": summary.get("peer_scope") or "",
        "lane_peer_n": peer_n,
        "lane_peer_ev_rev_median": _safe_float(summary.get("ev_rev_peer_median")),
        "lane_peer_rev_cagr_median": peer_cagr_pct,
        "lane_peer_ev_rev_rel": ev_rev_rel,
        "lane_peer_dispersion": dispersion,
        "lane_peer_trust": peer_trust,
        "lane_peer_view_suggested": summary.get("peer_view_suggested") or "",
        "lane_peer_view_agreement": _safe_float(summary.get("peer_view_agreement")),
        "lane_peer_view_names": "|".join(str(n) for n in view_names),
        "lane_peer_views_json": json.dumps(compact_views, separators=(",", ":")),
        "lane_peer_source": "multi_view_industry_default",
        **_flatten_view_fields(raw_views, view_trust),
        "_hist_adjusted_scenario": coeff["adjusted_scenario"],
    }
