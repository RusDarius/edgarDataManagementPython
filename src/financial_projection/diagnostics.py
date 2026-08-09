"""Case diagnostics: lens routing, regimes, conflicts, outlier gates."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

# Default industry/sector routing when config omits explicit lists.
DEFAULT_EARNINGS_PREFERRED_INDUSTRIES: frozenset[str] = frozenset(
    {
        "Managed Health Care",
        "Major Banks",
        "Regional Banks",
        "Investment Banks/Brokers",
        "Life/Health Insurance",
        "Multi-Line Insurance",
        "Property/Casualty Insurance",
        "Specialty Insurance",
        "Finance/Rental/Leasing",
        "Real Estate Investment Trusts",
        "Financial Conglomerates",
        "Savings Banks",
    }
)
DEFAULT_EARNINGS_PREFERRED_SECTORS: frozenset[str] = frozenset({"Finance"})
DEFAULT_EV_REV_UNSUITABLE_SECTORS: frozenset[str] = frozenset({"Finance"})


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm_label(value: Any) -> str:
    return str(value or "").strip()


def resolve_valuation_lens(
    *,
    industry: str,
    sector: str,
    earnings_preferred_industries: Sequence[str] | None = None,
    earnings_preferred_sectors: Sequence[str] | None = None,
    ev_rev_unsuitable_sectors: Sequence[str] | None = None,
) -> str:
    """
    Choose the decision lens for interpreting model output.

    - ``ev_revenue``: EV/Rev path is the primary long-term signal
    - ``earnings``: prefer street / EPS / P/E style signals (EV/Rev kept as secondary)
    - ``unsuitable``: EV/Rev systematically misleading; do not rank on terminal upside
    """
    industry_label = _norm_label(industry)
    sector_label = _norm_label(sector)
    earn_ind = {
        _norm_label(item)
        for item in (
            earnings_preferred_industries
            if earnings_preferred_industries is not None
            else DEFAULT_EARNINGS_PREFERRED_INDUSTRIES
        )
        if _norm_label(item)
    }
    earn_sec = {
        _norm_label(item)
        for item in (
            earnings_preferred_sectors
            if earnings_preferred_sectors is not None
            else DEFAULT_EARNINGS_PREFERRED_SECTORS
        )
        if _norm_label(item)
    }
    bad_sec = {
        _norm_label(item)
        for item in (
            ev_rev_unsuitable_sectors
            if ev_rev_unsuitable_sectors is not None
            else DEFAULT_EV_REV_UNSUITABLE_SECTORS
        )
        if _norm_label(item)
    }

    if sector_label in bad_sec:
        return "unsuitable"
    if industry_label in earn_ind or sector_label in earn_sec:
        return "earnings"
    return "ev_revenue"


def classify_valuation_regime(
    *,
    ev_rev_relative_scale: float | None,
    relative_scale_clip_high: float,
    street_fy_rev_growth_pct: float | None,
    rich_growth_fy_rev_pct: float,
) -> str:
    """
    Tag multiple regime.

    ``rich_growth``: relative EV/S is at/near the high clip AND street FY rev is hot.
    These names often look like model sells only because multiples mean-revert in
    the EV/Rev engine — treat as a sustainability case, not a raw short.
    """
    if (
        ev_rev_relative_scale is not None
        and relative_scale_clip_high > 0
        and ev_rev_relative_scale >= relative_scale_clip_high * 0.999
        and street_fy_rev_growth_pct is not None
        and street_fy_rev_growth_pct >= rich_growth_fy_rev_pct
    ):
        return "rich_growth"
    if ev_rev_relative_scale is not None and ev_rev_relative_scale <= 0.6:
        return "cheap_vs_peers"
    if ev_rev_relative_scale is not None and ev_rev_relative_scale >= 2.0:
        return "rich_vs_peers"
    return "normal"


def build_case_diagnostics(
    *,
    industry: str,
    sector: str,
    valid: bool,
    terminal_upside_pct: float | None,
    enterprise_value_to_revenue_ttm: float | None,
    ev_rev_relative_scale: float | None,
    relative_scale_clip_high: float,
    street_fy_rev_growth_pct: float | None,
    next_fq_eps_growth_pct: float | None,
    street_price_upside_pct: float | None,
    fy_eps_implied_upside_pct: float | None,
    fy_eps_implied_usable: bool,
    ev_ebitda_crosscheck_upside_pct: float | None,
    peer_n: int | None,
    forecast_rejected: bool,
    earnings_preferred_industries: Sequence[str] | None = None,
    earnings_preferred_sectors: Sequence[str] | None = None,
    ev_rev_unsuitable_sectors: Sequence[str] | None = None,
    rich_growth_fy_rev_pct: float = 15.0,
    valuation_conflict_pp: float = 40.0,
    outlier_terminal_upside_pct: float = 400.0,
    outlier_max_ev_rev: float = 1.5,
    rev_eps_divergence_hot_pp: float = 25.0,
    min_peer_n_trust: int = 15,
) -> dict[str, Any]:
    """Attach decision-oriented diagnostics to a projection summary row."""
    lens = resolve_valuation_lens(
        industry=industry,
        sector=sector,
        earnings_preferred_industries=earnings_preferred_industries,
        earnings_preferred_sectors=earnings_preferred_sectors,
        ev_rev_unsuitable_sectors=ev_rev_unsuitable_sectors,
    )
    regime = classify_valuation_regime(
        ev_rev_relative_scale=ev_rev_relative_scale,
        relative_scale_clip_high=relative_scale_clip_high,
        street_fy_rev_growth_pct=street_fy_rev_growth_pct,
        rich_growth_fy_rev_pct=rich_growth_fy_rev_pct,
    )

    conflict_pp = None
    if terminal_upside_pct is not None and ev_ebitda_crosscheck_upside_pct is not None:
        conflict_pp = abs(terminal_upside_pct - ev_ebitda_crosscheck_upside_pct)

    lens_conflict = bool(
        conflict_pp is not None and conflict_pp >= valuation_conflict_pp
    )
    preferred_when_conflict = "ev_revenue"
    if lens_conflict and ev_ebitda_crosscheck_upside_pct is not None:
        # Prefer EBITDA cross-check when conflict is large and EBITDA path exists.
        preferred_when_conflict = "ev_ebitda"

    rev_eps_divergence_pp = None
    if street_fy_rev_growth_pct is not None and next_fq_eps_growth_pct is not None:
        rev_eps_divergence_pp = street_fy_rev_growth_pct - next_fq_eps_growth_pct
    margin_risk = bool(
        rev_eps_divergence_pp is not None
        and rev_eps_divergence_pp >= rev_eps_divergence_hot_pp
    )

    outlier_flag = bool(
        valid
        and terminal_upside_pct is not None
        and terminal_upside_pct >= outlier_terminal_upside_pct
        and enterprise_value_to_revenue_ttm is not None
        and enterprise_value_to_revenue_ttm < outlier_max_ev_rev
    )

    peer_n_value = int(peer_n or 0)
    thin_peer_set = peer_n_value > 0 and peer_n_value < min_peer_n_trust

    # Primary decision upside used for ranking / reading.
    primary_upside = None
    primary_source = "none"
    if lens == "ev_revenue" and valid and not outlier_flag and regime != "rich_growth":
        if lens_conflict and preferred_when_conflict == "ev_ebitda":
            primary_upside = ev_ebitda_crosscheck_upside_pct
            primary_source = "ev_ebitda_crosscheck"
        else:
            primary_upside = terminal_upside_pct
            primary_source = "ev_revenue_terminal"
    elif lens == "ev_revenue" and regime == "rich_growth":
        primary_upside = street_price_upside_pct
        primary_source = "street_target_rich_growth"
    elif lens == "earnings":
        if fy_eps_implied_usable and fy_eps_implied_upside_pct is not None:
            primary_upside = fy_eps_implied_upside_pct
            primary_source = "fy_eps_x_forward_pe"
        else:
            primary_upside = street_price_upside_pct
            primary_source = "street_target"
    else:  # unsuitable
        primary_upside = street_price_upside_pct
        primary_source = "street_target_lens_unsuitable"

    # Rank screens: hide unsuitable / outlier / broken equity bridges.
    rank_eligible = bool(
        valid
        and not outlier_flag
        and lens != "unsuitable"
        and regime != "rich_growth"
    )

    flags: list[str] = []
    if lens == "unsuitable":
        flags.append("ev_rev_lens_unsuitable")
    if lens == "earnings":
        flags.append("earnings_lens_preferred")
    if regime == "rich_growth":
        flags.append("rich_growth_regime")
    if regime == "rich_vs_peers":
        flags.append("rich_vs_peers")
    if regime == "cheap_vs_peers":
        flags.append("cheap_vs_peers")
    if lens_conflict:
        flags.append("valuation_lens_conflict")
    if outlier_flag:
        flags.append("outlier_equity_bridge")
    if thin_peer_set:
        flags.append("thin_peer_set")
    if forecast_rejected:
        flags.append("forecast_growth_rejected")
    if margin_risk:
        flags.append("rev_eps_divergence_hot")
    if not rank_eligible and valid:
        flags.append("excluded_from_rank")

    return {
        "valuation_lens": lens,
        "valuation_regime": regime,
        "valuation_lens_conflict_pp": conflict_pp,
        "valuation_lens_conflict": lens_conflict,
        "valuation_preferred_on_conflict": preferred_when_conflict if lens_conflict else "",
        "st_rev_eps_divergence_pp": rev_eps_divergence_pp,
        "margin_risk_flag": margin_risk,
        "outlier_flag": outlier_flag,
        "thin_peer_set_flag": thin_peer_set,
        "rank_eligible": rank_eligible,
        "primary_upside_pct": primary_upside,
        "primary_upside_source": primary_source,
        "decision_flags": "|".join(flags),
    }


def attach_scenario_width(
    summaries: list[dict[str, Any]],
    *,
    wide_scenario_width_pp: float = 100.0,
) -> None:
    """
    Mutate summaries in place: add bull-bear terminal upside width per symbol.

    Width is identical across bear/base/bull rows for the same symbol.
    """
    by_symbol: dict[str, dict[str, float | None]] = {}
    for row in summaries:
        symbol = str(row.get("symbol") or "")
        scenario = str(row.get("scenario") or "")
        if not symbol or scenario not in {"bear", "base", "bull"}:
            continue
        bucket = by_symbol.setdefault(
            symbol, {"bear": None, "base": None, "bull": None}
        )
        bucket[scenario] = _safe_float(row.get("terminal_upside_pct"))

    widths: dict[str, float | None] = {}
    for symbol, bucket in by_symbol.items():
        bear = bucket.get("bear")
        bull = bucket.get("bull")
        if bear is None or bull is None:
            widths[symbol] = None
        else:
            widths[symbol] = bull - bear

    for row in summaries:
        symbol = str(row.get("symbol") or "")
        width = widths.get(symbol)
        row["scenario_width_y5_pp"] = width
        row["scenario_width_wide_flag"] = bool(
            width is not None and width >= wide_scenario_width_pp
        )
        if row.get("scenario_width_wide_flag"):
            flags = str(row.get("decision_flags") or "")
            extra = "wide_scenario_width"
            row["decision_flags"] = f"{flags}|{extra}" if flags else extra
