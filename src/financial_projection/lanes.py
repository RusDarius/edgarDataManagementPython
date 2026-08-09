"""Four parallel decision lanes: core, street, history, peer."""

from __future__ import annotations

from typing import Any, Mapping

from .config import ScenarioParams


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
) -> float:
    """
    0..1 trust in peer-relative signals.

    Low when peers are thin, lens unsuitable, or relative scale is pinned at the
    clip (often a polluted / incomparable peer set).
    """
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
    return _clip(trust, 0.0, 1.0)


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
    # Extreme YoY vs CAGR gap ⇒ regime break / noisy history.
    if cagr_fraction is not None and yoy_fraction is not None:
        gap = abs(yoy_fraction - cagr_fraction)
        if gap >= 0.35:
            trust *= 0.6
        elif gap >= 0.20:
            trust *= 0.8
    # Peer pollution should not erase company history, only damp peer-relative blend.
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
        # Street much hotter than history → pull growth scale down toward persistence.
        gap = street_fy_fraction - hist_anchor
        # Soft response: 20pp gap → ~0.10 scale move, damped by trust.
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
        # Faster history than peers → slightly slower fade (keep growth longer).
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


def build_decision_lanes(
    *,
    row: Mapping[str, Any],
    summary: Mapping[str, Any],
    scenario: ScenarioParams,
    min_peer_n_trust: int = 15,
    relative_scale_clip_high: float = 3.0,
) -> dict[str, Any]:
    """
    Emit four parallel lanes (core / street / history / peer).

    Core remains the ranking baseline. Other lanes are explicit cross-checks.
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
    peer_cagr_pct = _safe_float(summary.get("rev_cagr_peer_median"))
    if peer_cagr_pct is None:
        peer_cagr_pct = _safe_float(
            (summary.get("peer_metric_medians") or {}).get("total_revenue_cagr_5y")
            if isinstance(summary.get("peer_metric_medians"), dict)
            else None
        )
    peer_cagr_fraction = _pct_to_fraction(peer_cagr_pct)
    cagr_rel = _safe_float(summary.get("rev_cagr_relative_scale"))

    peer_trust = peer_lane_trust(
        peer_n=peer_n,
        min_peer_n_trust=min_peer_n_trust,
        valuation_lens=valuation_lens,
        ev_rev_relative_scale=ev_rev_rel,
        relative_scale_clip_high=relative_scale_clip_high,
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
        # Lane 4 — peer medians (multiple anchor; may be polluted)
        "lane_peer_scope": summary.get("peer_scope") or "",
        "lane_peer_n": peer_n,
        "lane_peer_ev_rev_median": _safe_float(summary.get("ev_rev_peer_median")),
        "lane_peer_rev_cagr_median": peer_cagr_pct,
        "lane_peer_ev_rev_rel": ev_rev_rel,
        "lane_peer_trust": peer_trust,
        "lane_peer_source": "industry_mcap_sector_global",
        # Helper for optional hist-adjusted re-run
        "_hist_adjusted_scenario": coeff["adjusted_scenario"],
    }
