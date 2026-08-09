"""Pure 5y EV/Revenue projection math (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .config import FinancialProjectionConfig, ScenarioParams
from .diagnostics import attach_scenario_width, build_case_diagnostics
from .fields import REQUIRED_SCENARIO_KEYS
from .lanes import build_decision_lanes
from .short_term_outlook import build_short_term_outlook


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_to_fraction(value: float | None) -> float | None:
    """TradingView growth fields are typically percent points (e.g. 12.5 = 12.5%)."""
    if value is None:
        return None
    # Heuristic: absolute values > 2.5 are treated as percent points.
    if abs(value) > 2.5:
        return value / 100.0
    return value


@dataclass(frozen=True)
class StartingGrowthResolution:
    growth: float | None
    source: str
    street_fy_rev_growth_fraction: float | None
    forecast_rejected: bool
    forecast_implied_growth_fraction: float | None


def resolve_starting_revenue_growth(
    row: Mapping[str, Any],
    *,
    forecast_growth_max_fraction: float = 1.5,
    max_starting_growth_fraction: float = 1.0,
) -> StartingGrowthResolution:
    """
    Prefer street Year-1 implied growth, then TTM YoY, then 5y CAGR.

    Forecast-implied growth above ``forecast_growth_max_fraction`` is rejected
    (falls back to YoY/CAGR) but the street FY growth is still surfaced.
    Final model growth is clipped to ``+/- max_starting_growth_fraction``.
    """
    revenue = _safe_float(row.get("total_revenue_ttm"))
    if revenue is None or revenue <= 0:
        revenue = _safe_float(row.get("total_revenue"))

    forecast = _safe_float(row.get("revenue_forecast_next_fy"))
    forecast_implied: float | None = None
    street_fy: float | None = None
    forecast_rejected = False
    if revenue is not None and revenue > 0 and forecast is not None and forecast > 0:
        forecast_implied = (forecast / revenue) - 1.0
        street_fy = forecast_implied

    growth: float | None = None
    source = "missing"

    if forecast_implied is not None:
        if forecast_implied <= forecast_growth_max_fraction:
            growth = forecast_implied
            source = "revenue_forecast_next_fy"
        else:
            forecast_rejected = True
            source = "forecast_rejected_over_cap"

    if growth is None:
        yoy = _pct_to_fraction(_safe_float(row.get("total_revenue_yoy_growth_ttm")))
        if yoy is not None:
            growth = yoy
            source = (
                "forecast_rejected_fallback_yoy"
                if forecast_rejected
                else "total_revenue_yoy_growth_ttm"
            )

    if growth is None:
        cagr = _pct_to_fraction(_safe_float(row.get("total_revenue_cagr_5y")))
        if cagr is not None:
            growth = cagr
            source = (
                "forecast_rejected_fallback_cagr"
                if forecast_rejected
                else "total_revenue_cagr_5y"
            )

    if growth is None:
        return StartingGrowthResolution(
            growth=None,
            source=(
                "missing"
                if not forecast_rejected
                else "forecast_rejected_no_fallback"
            ),
            street_fy_rev_growth_fraction=street_fy,
            forecast_rejected=forecast_rejected,
            forecast_implied_growth_fraction=forecast_implied,
        )

    cap = abs(float(max_starting_growth_fraction))
    if growth > cap:
        growth = cap
        source = f"{source}_clipped"
    elif growth < -cap:
        growth = -cap
        source = f"{source}_clipped"
    return StartingGrowthResolution(
        growth=growth,
        source=source,
        street_fy_rev_growth_fraction=street_fy,
        forecast_rejected=forecast_rejected,
        forecast_implied_growth_fraction=forecast_implied,
    )


def _apply_sgr_cap(
    growth: float, *, sgr_pct: float | None, sgr_cap_multiplier: float
) -> float:
    if sgr_pct is None or sgr_pct <= 0:
        return growth
    sgr_fraction = sgr_pct / 100.0 if abs(sgr_pct) > 2.5 else sgr_pct
    cap = sgr_fraction * sgr_cap_multiplier
    if growth > cap:
        return cap
    return growth


def _growth_path(
    *,
    starting_growth: float,
    scenario: ScenarioParams,
    horizon_years: int,
    sgr_pct: float | None,
) -> list[float]:
    if scenario.revenue_growth_override is not None:
        g0 = _pct_to_fraction(scenario.revenue_growth_override)
        assert g0 is not None
    else:
        g0 = starting_growth * scenario.revenue_growth_scale

    g0 = _apply_sgr_cap(
        g0, sgr_pct=sgr_pct, sgr_cap_multiplier=scenario.sgr_cap_multiplier
    )

    path: list[float] = []
    current = g0
    for _year in range(1, horizon_years + 1):
        capped = _apply_sgr_cap(
            current, sgr_pct=sgr_pct, sgr_cap_multiplier=scenario.sgr_cap_multiplier
        )
        path.append(capped)
        current = capped * scenario.growth_fade_per_year
    return path


def _blended_starting_multiple(
    *,
    company_ev_rev: float,
    peer_ev_rev: float | None,
    own_multiple_blend: float,
) -> float:
    if peer_ev_rev is None or peer_ev_rev <= 0:
        return company_ev_rev
    blend = max(0.0, min(1.0, own_multiple_blend))
    return blend * company_ev_rev + (1.0 - blend) * peer_ev_rev


def _multiple_path(
    *,
    start_multiple: float,
    peer_ev_rev: float | None,
    scenario: ScenarioParams,
    horizon_years: int,
) -> list[float]:
    peer_anchor = peer_ev_rev if peer_ev_rev is not None and peer_ev_rev > 0 else start_multiple
    terminal = peer_anchor * scenario.terminal_multiple_scale_vs_peer
    if terminal <= 0:
        terminal = start_multiple

    path = [start_multiple]  # t=0
    if horizon_years <= 0:
        return path
    for t in range(1, horizon_years + 1):
        weight = t / float(horizon_years)
        path.append(start_multiple * (1.0 - weight) + terminal * weight)
    return path


def _path_terminal_upside(
    *,
    starting_growth: float,
    scenario: ScenarioParams,
    horizon_years: int,
    sgr_pct: float | None,
    revenue0: float,
    ev_rev0: float,
    peer_ev_rev: float | None,
    net_debt0: float,
    close: float,
    market_cap: float,
) -> tuple[float | None, float | None]:
    """Return (terminal_price, terminal_upside_pct) for a scenario path."""
    growth_path = _growth_path(
        starting_growth=starting_growth,
        scenario=scenario,
        horizon_years=horizon_years,
        sgr_pct=sgr_pct,
    )
    start_multiple = _blended_starting_multiple(
        company_ev_rev=ev_rev0,
        peer_ev_rev=peer_ev_rev,
        own_multiple_blend=scenario.own_multiple_blend,
    )
    multiple_path = _multiple_path(
        start_multiple=start_multiple,
        peer_ev_rev=peer_ev_rev,
        scenario=scenario,
        horizon_years=horizon_years,
    )
    revenue = revenue0
    terminal_price = None
    for t in range(0, horizon_years + 1):
        if t > 0:
            revenue = revenue * (1.0 + growth_path[t - 1])
        ev_t = multiple_path[t] * revenue
        net_debt_t = net_debt0 * ((1.0 + scenario.net_debt_growth_per_year) ** t)
        equity_t = ev_t - net_debt_t
        if equity_t <= 0:
            return None, None
        terminal_price = close * (equity_t / market_cap)
    if terminal_price is None or terminal_price <= 0:
        return None, None
    return terminal_price, ((terminal_price / close) - 1.0) * 100.0


def _attach_diagnostics(
    summary: dict[str, Any],
    *,
    config: FinancialProjectionConfig | None,
    relative_scale_clip_high: float,
) -> None:
    rules = config.valuation_lens_rules if config is not None else None
    summary.update(
        build_case_diagnostics(
            industry=str(summary.get("industry") or ""),
            sector=str(summary.get("sector") or ""),
            valid=bool(summary.get("valid")),
            terminal_upside_pct=_safe_float(summary.get("terminal_upside_pct")),
            enterprise_value_to_revenue_ttm=_safe_float(
                summary.get("enterprise_value_to_revenue_ttm")
            ),
            ev_rev_relative_scale=_safe_float(summary.get("ev_rev_relative_scale")),
            relative_scale_clip_high=relative_scale_clip_high,
            street_fy_rev_growth_pct=_safe_float(
                summary.get("st_next_fy_rev_growth_pct")
            ),
            next_fq_eps_growth_pct=_safe_float(
                summary.get("st_next_fq_eps_growth_pct")
            ),
            street_price_upside_pct=_safe_float(
                summary.get("st_street_price_upside_pct")
            ),
            fy_eps_implied_upside_pct=_safe_float(
                summary.get("st_fy_eps_implied_upside_pct")
            ),
            fy_eps_implied_usable=bool(summary.get("st_fy_eps_implied_usable")),
            ev_ebitda_crosscheck_upside_pct=_safe_float(
                summary.get("ev_ebitda_crosscheck_upside_pct")
            ),
            peer_n=int(summary.get("peer_n") or 0),
            forecast_rejected=bool(summary.get("forecast_rejected")),
            earnings_preferred_industries=(
                list(rules.earnings_preferred_industries) if rules else None
            ),
            earnings_preferred_sectors=(
                list(rules.earnings_preferred_sectors) if rules else None
            ),
            ev_rev_unsuitable_sectors=(
                list(rules.ev_rev_unsuitable_sectors) if rules else None
            ),
            rich_growth_fy_rev_pct=(
                config.rich_growth_fy_rev_pct if config is not None else 15.0
            ),
            valuation_conflict_pp=(
                config.valuation_conflict_pp if config is not None else 40.0
            ),
            outlier_terminal_upside_pct=(
                config.outlier_terminal_upside_pct if config is not None else 400.0
            ),
            outlier_max_ev_rev=(
                config.outlier_max_ev_rev if config is not None else 1.5
            ),
            rev_eps_divergence_hot_pp=(
                config.rev_eps_divergence_hot_pp if config is not None else 25.0
            ),
            min_peer_n_trust=(
                config.min_peer_n_trust if config is not None else 15
            ),
        )
    )


def _attach_lanes(
    summary: dict[str, Any],
    *,
    row: Mapping[str, Any],
    scenario: ScenarioParams,
    config: FinancialProjectionConfig | None,
    relative_scale_clip_high: float,
    starting_growth: float | None = None,
    revenue_ttm: float | None = None,
    ev_rev_ttm: float | None = None,
    peer_ev_rev: float | None = None,
    net_debt: float | None = None,
    close: float | None = None,
    market_cap: float | None = None,
    horizon_years: int = 5,
    sgr_pct: float | None = None,
) -> None:
    """Attach four decision lanes after diagnostics (needs valuation_lens)."""
    min_peer_n_trust = config.min_peer_n_trust if config is not None else 15
    lanes = build_decision_lanes(
        row=row,
        summary=summary,
        scenario=scenario,
        min_peer_n_trust=min_peer_n_trust,
        relative_scale_clip_high=relative_scale_clip_high,
    )
    hist_scenario = lanes.pop("_hist_adjusted_scenario", None)
    summary.update(lanes)

    summary["lane_hist_adjusted_terminal_price"] = None
    summary["lane_hist_adjusted_upside_pct"] = None
    summary["lane_hist_vs_core_gap_pp"] = None
    if (
        bool(summary.get("valid"))
        and hist_scenario is not None
        and starting_growth is not None
        and revenue_ttm is not None
        and ev_rev_ttm is not None
        and close is not None
        and market_cap is not None
        and net_debt is not None
    ):
        hist_price, hist_upside = _path_terminal_upside(
            starting_growth=starting_growth,
            scenario=hist_scenario,
            horizon_years=horizon_years,
            sgr_pct=sgr_pct,
            revenue0=revenue_ttm,
            ev_rev0=ev_rev_ttm,
            peer_ev_rev=peer_ev_rev,
            net_debt0=net_debt,
            close=close,
            market_cap=market_cap,
        )
        summary["lane_hist_adjusted_terminal_price"] = hist_price
        summary["lane_hist_adjusted_upside_pct"] = hist_upside
        core_upside = _safe_float(summary.get("lane_core_upside_pct"))
        if hist_upside is not None and core_upside is not None:
            summary["lane_hist_vs_core_gap_pp"] = hist_upside - core_upside


def _finalize_summary(
    summary: dict[str, Any],
    *,
    row: Mapping[str, Any],
    scenario: ScenarioParams,
    config: FinancialProjectionConfig | None,
    relative_scale_clip_high: float,
    starting_growth: float | None = None,
    revenue_ttm: float | None = None,
    ev_rev_ttm: float | None = None,
    peer_ev_rev: float | None = None,
    net_debt: float | None = None,
    close: float | None = None,
    market_cap: float | None = None,
    horizon_years: int = 5,
    sgr_pct: float | None = None,
) -> None:
    _attach_diagnostics(
        summary, config=config, relative_scale_clip_high=relative_scale_clip_high
    )
    _attach_lanes(
        summary,
        row=row,
        scenario=scenario,
        config=config,
        relative_scale_clip_high=relative_scale_clip_high,
        starting_growth=starting_growth,
        revenue_ttm=revenue_ttm,
        ev_rev_ttm=ev_rev_ttm,
        peer_ev_rev=peer_ev_rev,
        net_debt=net_debt,
        close=close,
        market_cap=market_cap,
        horizon_years=horizon_years,
        sgr_pct=sgr_pct,
    )


def project_symbol_scenario(
    row: Mapping[str, Any],
    *,
    scenario_name: str,
    scenario: ScenarioParams,
    peer_context: Mapping[str, Any] | None,
    horizon_years: int,
    forecast_growth_max_fraction: float = 1.5,
    max_starting_growth_fraction: float = 1.0,
    max_ev_to_revenue: float | None = 80.0,
    config: FinancialProjectionConfig | None = None,
    relative_scale_clip_high: float = 3.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Project one symbol under one scenario.

    Returns (summary_row, year_grid_rows).
    """
    peer = peer_context or {}
    close = _safe_float(row.get("close"))
    market_cap = _safe_float(row.get("market_cap_basic"))
    revenue_ttm = _safe_float(row.get("total_revenue_ttm"))
    if revenue_ttm is None or revenue_ttm <= 0:
        revenue_ttm = _safe_float(row.get("total_revenue"))
    ev_rev_ttm = _safe_float(row.get("enterprise_value_to_revenue_ttm"))
    enterprise_value = _safe_float(row.get("enterprise_value_current"))
    net_debt = _safe_float(row.get("net_debt"))
    if net_debt is None:
        net_debt = 0.0
    ebitda = _safe_float(row.get("ebitda"))
    sgr_pct = _safe_float(row.get("sustainable_growth_rate_ttm"))

    symbol = str(row.get("symbol") or "").strip()
    growth_res = resolve_starting_revenue_growth(
        row,
        forecast_growth_max_fraction=forecast_growth_max_fraction,
        max_starting_growth_fraction=max_starting_growth_fraction,
    )
    starting_growth = growth_res.growth
    growth_source = growth_res.source

    invalid_reason = ""
    if close is None or close <= 0:
        invalid_reason = "missing_close"
    elif market_cap is None or market_cap <= 0:
        invalid_reason = "missing_market_cap"
    elif revenue_ttm is None or revenue_ttm <= 0:
        invalid_reason = "missing_revenue"
    elif ev_rev_ttm is None or ev_rev_ttm <= 0:
        invalid_reason = "missing_ev_rev"
    elif (
        max_ev_to_revenue is not None
        and ev_rev_ttm is not None
        and ev_rev_ttm > max_ev_to_revenue
    ):
        invalid_reason = "ev_rev_above_max"
    elif starting_growth is None:
        invalid_reason = "missing_growth"

    peer_ev_rev = _safe_float(peer.get("ev_rev_peer_median"))
    peer_ev_ebitda = _safe_float(peer.get("ev_ebitda_peer_median"))
    clip_high = (
        float(config.relative_scale_clip_high)
        if config is not None
        else float(relative_scale_clip_high)
    )

    summary: dict[str, Any] = {
        "symbol": symbol,
        "name": str(row.get("name") or ""),
        "exchange": str(row.get("exchange") or ""),
        "market": str(row.get("market") or ""),
        "sector": str(row.get("sector") or peer.get("sector") or ""),
        "industry": str(row.get("industry") or peer.get("industry") or ""),
        "scenario": scenario_name,
        # Exact all-fields input names (no aliases like revenue_0 / ev_rev_0).
        "close": close,
        "market_cap_basic": market_cap,
        "total_revenue_ttm": revenue_ttm,
        "total_revenue": _safe_float(row.get("total_revenue")),
        "enterprise_value_current": enterprise_value,
        "enterprise_value_to_revenue_ttm": ev_rev_ttm,
        "enterprise_value_ebitda_ttm": _safe_float(
            row.get("enterprise_value_ebitda_ttm")
        ),
        "net_debt": net_debt,
        "ebitda": ebitda,
        "total_revenue_yoy_growth_ttm": _safe_float(
            row.get("total_revenue_yoy_growth_ttm")
        ),
        "total_revenue_cagr_5y": _safe_float(row.get("total_revenue_cagr_5y")),
        "revenue_forecast_next_fy": _safe_float(row.get("revenue_forecast_next_fy")),
        "price_target_median": _safe_float(row.get("price_target_median")),
        "sustainable_growth_rate_ttm": sgr_pct,
        # Model-derived (not all-fields renames)
        "starting_growth_fraction": starting_growth,
        "starting_growth_source": growth_source,
        "street_fy_rev_growth_fraction": growth_res.street_fy_rev_growth_fraction,
        "forecast_implied_growth_fraction": growth_res.forecast_implied_growth_fraction,
        "forecast_rejected": growth_res.forecast_rejected,
        "peer_scope": peer.get("peer_scope") or "",
        "peer_n": peer.get("peer_n") or 0,
        "ev_rev_peer_median": peer_ev_rev,
        "rev_growth_peer_median": peer.get("rev_growth_peer_median"),
        "rev_cagr_peer_median": peer.get("rev_cagr_peer_median"),
        "ev_rev_relative_scale": peer.get("ev_rev_relative_scale"),
        "rev_growth_relative_scale": peer.get("rev_growth_relative_scale"),
        "rev_cagr_relative_scale": peer.get("rev_cagr_relative_scale"),
        "valid": False,
        "invalid_reason": invalid_reason,
        "terminal_price": None,
        "terminal_upside_pct": None,
        "implied_price_cagr_pct": None,
        "terminal_ev_rev": None,
        "effective_starting_growth_fraction": None,
        "ev_ebitda_crosscheck_price_t5": None,
        "ev_ebitda_crosscheck_upside_pct": None,
        "model_y1_price": None,
        "model_y1_upside_pct": None,
    }
    # Short-term outlook is available even when the 5y path is invalid
    # (still useful when prediction names join all-fields forecasts).
    summary.update(build_short_term_outlook(row, close=close))

    if invalid_reason:
        _finalize_summary(
            summary,
            row=row,
            scenario=scenario,
            config=config,
            relative_scale_clip_high=clip_high,
        )
        return summary, []

    assert starting_growth is not None
    assert revenue_ttm is not None
    assert ev_rev_ttm is not None
    assert close is not None
    assert market_cap is not None

    growth_path = _growth_path(
        starting_growth=starting_growth,
        scenario=scenario,
        horizon_years=horizon_years,
        sgr_pct=sgr_pct,
    )
    start_multiple = _blended_starting_multiple(
        company_ev_rev=ev_rev_ttm,
        peer_ev_rev=peer_ev_rev,
        own_multiple_blend=scenario.own_multiple_blend,
    )
    multiple_path = _multiple_path(
        start_multiple=start_multiple,
        peer_ev_rev=peer_ev_rev,
        scenario=scenario,
        horizon_years=horizon_years,
    )

    year_rows: list[dict[str, Any]] = []
    revenue = revenue_ttm
    equity_invalid = False
    terminal_price = close

    for t in range(0, horizon_years + 1):
        if t > 0:
            revenue = revenue * (1.0 + growth_path[t - 1])
        ev_rev_t = multiple_path[t]
        ev_t = ev_rev_t * revenue
        net_debt_t = net_debt * ((1.0 + scenario.net_debt_growth_per_year) ** t)
        equity_t = ev_t - net_debt_t
        if equity_t <= 0:
            equity_invalid = True
            price_t = None
            upside_pct = None
        else:
            price_t = close * (equity_t / market_cap)
            upside_pct = ((price_t / close) - 1.0) * 100.0
            terminal_price = price_t

        year_rows.append(
            {
                "symbol": symbol,
                "scenario": scenario_name,
                "year": t,
                "total_revenue_ttm": revenue,  # projected path from starting TTM
                "growth_fraction": None if t == 0 else growth_path[t - 1],
                "enterprise_value_to_revenue_ttm": ev_rev_t,
                "enterprise_value_current": ev_t,
                "net_debt": net_debt_t,
                "equity_value": equity_t,
                "price": price_t,
                "upside_pct": upside_pct,
            }
        )

    if equity_invalid or terminal_price is None or terminal_price <= 0:
        summary["invalid_reason"] = "non_positive_equity"
        summary["effective_starting_growth_fraction"] = (
            growth_path[0] if growth_path else None
        )
        summary["terminal_ev_rev"] = multiple_path[-1] if multiple_path else None
        _finalize_summary(
            summary,
            row=row,
            scenario=scenario,
            config=config,
            relative_scale_clip_high=clip_high,
            starting_growth=starting_growth,
            revenue_ttm=revenue_ttm,
            ev_rev_ttm=ev_rev_ttm,
            peer_ev_rev=peer_ev_rev,
            net_debt=net_debt,
            close=close,
            market_cap=market_cap,
            horizon_years=horizon_years,
            sgr_pct=sgr_pct,
        )
        return summary, year_rows

    terminal_upside_pct = ((terminal_price / close) - 1.0) * 100.0
    implied_cagr = (terminal_price / close) ** (1.0 / float(horizon_years)) - 1.0

    # Light EV/EBITDA cross-check at t=horizon (grow EBITDA with same growth path).
    cross_price = None
    cross_upside = None
    if (
        ebitda is not None
        and ebitda > 0
        and peer_ev_ebitda is not None
        and peer_ev_ebitda > 0
    ):
        ebitda_t = ebitda
        for g in growth_path:
            ebitda_t *= 1.0 + g
        company_ev_ebitda = _safe_float(row.get("enterprise_value_ebitda_ttm"))
        terminal_ev_ebitda = peer_ev_ebitda * scenario.terminal_multiple_scale_vs_peer
        if company_ev_ebitda is not None and company_ev_ebitda > 0:
            terminal_ev_ebitda = (
                scenario.own_multiple_blend * company_ev_ebitda
                + (1.0 - scenario.own_multiple_blend) * peer_ev_ebitda
            ) * scenario.terminal_multiple_scale_vs_peer
        implied_ev = ebitda_t * terminal_ev_ebitda
        terminal_net_debt = year_rows[-1]["net_debt"]
        implied_equity = implied_ev - terminal_net_debt
        if implied_equity > 0:
            cross_price = close * (implied_equity / market_cap)
            cross_upside = ((cross_price / close) - 1.0) * 100.0

    summary.update(
        {
            "valid": True,
            "invalid_reason": "",
            "terminal_price": terminal_price,
            "terminal_upside_pct": terminal_upside_pct,
            "implied_price_cagr_pct": implied_cagr * 100.0,
            "terminal_ev_rev": multiple_path[-1],
            "effective_starting_growth_fraction": growth_path[0] if growth_path else None,
            "ev_ebitda_crosscheck_price_t5": cross_price,
            "ev_ebitda_crosscheck_upside_pct": cross_upside,
        }
    )

    # Year-1 model price as short-term bridge vs street forecasts.
    y1 = next((item for item in year_rows if item.get("year") == 1), None)
    model_y1_price = _safe_float(y1.get("price")) if y1 else None
    model_y1_upside = _safe_float(y1.get("upside_pct")) if y1 else None
    summary["model_y1_price"] = model_y1_price
    summary["model_y1_upside_pct"] = model_y1_upside
    summary.update(
        build_short_term_outlook(
            row,
            close=close,
            model_y1_price=model_y1_price,
            model_y1_upside_pct=model_y1_upside,
        )
    )
    _finalize_summary(
        summary,
        row=row,
        scenario=scenario,
        config=config,
        relative_scale_clip_high=clip_high,
        starting_growth=starting_growth,
        revenue_ttm=revenue_ttm,
        ev_rev_ttm=ev_rev_ttm,
        peer_ev_rev=peer_ev_rev,
        net_debt=net_debt,
        close=close,
        market_cap=market_cap,
        horizon_years=horizon_years,
        sgr_pct=sgr_pct,
    )
    return summary, year_rows


def project_universe(
    rows: list[Mapping[str, Any]],
    *,
    config: FinancialProjectionConfig,
    peer_context_by_symbol: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project all symbols across bear/base/bull."""
    summaries: list[dict[str, Any]] = []
    year_grids: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        peer = peer_context_by_symbol.get(symbol, {})
        for scenario_name in REQUIRED_SCENARIO_KEYS:
            summary, year_rows = project_symbol_scenario(
                row,
                scenario_name=scenario_name,
                scenario=config.scenarios[scenario_name],
                peer_context=peer,
                horizon_years=config.horizon_years,
                forecast_growth_max_fraction=config.forecast_growth_max_fraction,
                max_starting_growth_fraction=config.max_starting_growth_fraction,
                max_ev_to_revenue=config.max_ev_to_revenue,
                config=config,
            )
            summaries.append(summary)
            year_grids.extend(year_rows)
    attach_scenario_width(
        summaries, wide_scenario_width_pp=config.scenario_width_wide_pp
    )
    return summaries, year_grids
