"""Three parallel growth lanes for revenue / EBIT / EBITDA / margins.

Lanes (not merged):
  1. ``universe`` — absolute path from universe/peer-median growth priors +
     global scenario scale/fade.
  2. ``own`` — company forecast / YoY / CAGR persistence.
  3. ``peer`` — blend toward peer-median growth and fade margins toward peers.

TV input names are preserved. Produced path metrics use explicit lane prefixes.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .config import FinancialProjectionConfig, ScenarioParams
from .fields import REQUIRED_SCENARIO_KEYS

GROWTH_LANE_UNIVERSE = "universe"
GROWTH_LANE_OWN = "own"
GROWTH_LANE_PEER = "peer"
GROWTH_LANES: tuple[str, ...] = (
    GROWTH_LANE_UNIVERSE,
    GROWTH_LANE_OWN,
    GROWTH_LANE_PEER,
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


def _margin_fraction(value: float | None) -> float | None:
    """Margins in TV are usually percent points (e.g. 31.2 = 31.2%)."""
    return _pct_to_fraction(value)


def _first_level(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def resolve_revenue_level(row: Mapping[str, Any]) -> float | None:
    return _first_level(
        _safe_float(row.get("total_revenue_ttm")),
        _safe_float(row.get("total_revenue")),
    )


def resolve_ebitda_level(row: Mapping[str, Any]) -> float | None:
    return _first_level(
        _safe_float(row.get("ebitda_ttm")),
        _safe_float(row.get("ebitda")),
    )


def resolve_ebit_level(row: Mapping[str, Any]) -> float | None:
    return _first_level(
        _safe_float(row.get("ebit_ttm")),
        _safe_float(row.get("oper_income_ttm")),
    )


def resolve_net_income_level(row: Mapping[str, Any]) -> float | None:
    return _safe_float(row.get("net_income_ttm"))


def resolve_starting_margins(
    row: Mapping[str, Any],
    *,
    revenue: float | None,
    ebitda: float | None,
    ebit: float | None,
    net_income: float | None,
) -> dict[str, float | None]:
    ebitda_m = _margin_fraction(_safe_float(row.get("ebitda_margin_ttm")))
    if ebitda_m is None and revenue and ebitda is not None and revenue != 0:
        ebitda_m = ebitda / revenue

    operating_m = _margin_fraction(_safe_float(row.get("operating_margin_ttm")))
    if operating_m is None and revenue and ebit is not None and revenue != 0:
        operating_m = ebit / revenue

    net_m = _margin_fraction(_safe_float(row.get("net_margin_ttm")))
    if net_m is None and revenue and net_income is not None and revenue != 0:
        net_m = net_income / revenue

    return {
        "ebitda_margin_ttm": ebitda_m,
        "operating_margin_ttm": operating_m,
        "net_margin_ttm": net_m,
    }


def resolve_own_revenue_growth(
    row: Mapping[str, Any],
    *,
    forecast_growth_max_fraction: float,
    max_starting_growth_fraction: float,
) -> dict[str, Any]:
    """Prefer street FY, then YoY, then 5y CAGR (same priority as price model)."""
    revenue = resolve_revenue_level(row)
    forecast = _safe_float(row.get("revenue_forecast_next_fy"))
    implied = None
    if revenue is not None and revenue > 0 and forecast is not None:
        implied = forecast / revenue - 1.0

    yoy = _pct_to_fraction(_safe_float(row.get("total_revenue_yoy_growth_ttm")))
    cagr = _pct_to_fraction(_safe_float(row.get("total_revenue_cagr_5y")))

    source = "missing"
    growth = None
    forecast_rejected = False
    if implied is not None:
        if abs(implied) > forecast_growth_max_fraction:
            forecast_rejected = True
            source = "revenue_forecast_next_fy_rejected"
        else:
            growth = implied
            source = "revenue_forecast_next_fy"
    if growth is None and yoy is not None:
        growth = yoy
        source = "total_revenue_yoy_growth_ttm"
    if growth is None and cagr is not None:
        growth = cagr
        source = "total_revenue_cagr_5y"

    if growth is not None:
        growth = _clip(growth, -max_starting_growth_fraction, max_starting_growth_fraction)

    return {
        "own_revenue_growth_fraction": growth,
        "own_revenue_growth_source": source,
        "forecast_implied_growth_fraction": implied,
        "forecast_rejected": forecast_rejected,
        "total_revenue_yoy_growth_fraction": yoy,
        "total_revenue_cagr_5y_fraction": cagr,
    }


def _growth_path(
    *,
    starting_growth: float,
    scenario: ScenarioParams,
    horizon_years: int,
) -> list[float]:
    if scenario.revenue_growth_override is not None:
        g0 = float(scenario.revenue_growth_override)
    else:
        g0 = starting_growth * float(scenario.revenue_growth_scale)
    path: list[float] = []
    g = g0
    for _ in range(horizon_years):
        path.append(g)
        g *= float(scenario.growth_fade_per_year)
    return path


def _fade_margin(
    start: float | None,
    target: float | None,
    *,
    year: int,
    horizon_years: int,
    blend_speed: float,
) -> float | None:
    if start is None:
        return target
    if target is None or horizon_years <= 0:
        return start
    weight = min(1.0, (year / horizon_years) * blend_speed)
    return start * (1.0 - weight) + target * weight


def _peer_metric_median(
    peer: Mapping[str, Any], field_name: str
) -> float | None:
    medians = peer.get("peer_metric_medians") or {}
    if isinstance(medians, dict) and field_name in medians:
        return _safe_float(medians.get(field_name))
    # Fallbacks already surfaced on peer context for growth.
    if field_name == "total_revenue_yoy_growth_ttm":
        return _safe_float(peer.get("rev_growth_peer_median"))
    if field_name == "total_revenue_cagr_5y":
        return _safe_float(peer.get("rev_cagr_peer_median"))
    return None


def project_symbol_growth_lanes(
    row: Mapping[str, Any],
    *,
    peer_context: Mapping[str, Any] | None,
    config: FinancialProjectionConfig,
    universe_growth_fraction: float,
    universe_ebitda_margin: float | None,
    universe_operating_margin: float | None,
    universe_net_margin: float | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Emit summary + year-grid rows for all growth lanes × scenarios.

    Summary is one row per (growth_lane, scenario).
    Year grid is one row per (growth_lane, scenario, year).
    """
    peer = peer_context or {}
    symbol = str(row.get("symbol") or "").strip()
    revenue0 = resolve_revenue_level(row)
    ebitda0 = resolve_ebitda_level(row)
    ebit0 = resolve_ebit_level(row)
    net0 = resolve_net_income_level(row)
    margins0 = resolve_starting_margins(
        row, revenue=revenue0, ebitda=ebitda0, ebit=ebit0, net_income=net0
    )

    own = resolve_own_revenue_growth(
        row,
        forecast_growth_max_fraction=config.forecast_growth_max_fraction,
        max_starting_growth_fraction=config.max_starting_growth_fraction,
    )

    peer_growth = _pct_to_fraction(
        _peer_metric_median(peer, "total_revenue_yoy_growth_ttm")
    )
    if peer_growth is None:
        peer_growth = _pct_to_fraction(
            _peer_metric_median(peer, "total_revenue_cagr_5y")
        )
    peer_ebitda_m = _margin_fraction(
        _peer_metric_median(peer, "ebitda_margin_ttm")
    )
    peer_op_m = _margin_fraction(
        _peer_metric_median(peer, "operating_margin_ttm")
    )
    peer_net_m = _margin_fraction(_peer_metric_median(peer, "net_margin_ttm"))

    own_growth = own["own_revenue_growth_fraction"]
    if own_growth is None and peer_growth is not None:
        own_growth = peer_growth

    lane_starts: dict[str, dict[str, Any]] = {
        GROWTH_LANE_UNIVERSE: {
            "revenue_growth": universe_growth_fraction,
            "growth_source": "universe_peer_median_or_default",
            "ebitda_margin_target": universe_ebitda_margin,
            "operating_margin_target": universe_operating_margin,
            "net_margin_target": universe_net_margin,
            "margin_blend_speed": 0.85,
        },
        GROWTH_LANE_OWN: {
            "revenue_growth": own_growth,
            "growth_source": own["own_revenue_growth_source"],
            "ebitda_margin_target": margins0["ebitda_margin_ttm"],
            "operating_margin_target": margins0["operating_margin_ttm"],
            "net_margin_target": margins0["net_margin_ttm"],
            "margin_blend_speed": 0.25,
        },
        GROWTH_LANE_PEER: {
            "revenue_growth": (
                None
                if own_growth is None and peer_growth is None
                else (
                    own_growth
                    if peer_growth is None
                    else (
                        peer_growth
                        if own_growth is None
                        else 0.5 * own_growth + 0.5 * peer_growth
                    )
                )
            ),
            "growth_source": "blend_own_and_peer_median",
            "ebitda_margin_target": peer_ebitda_m,
            "operating_margin_target": peer_op_m,
            "net_margin_target": peer_net_m,
            "margin_blend_speed": 1.0,
        },
    }

    summaries: list[dict[str, Any]] = []
    year_grids: list[dict[str, Any]] = []
    horizon = int(config.horizon_years)

    for lane_name, lane_cfg in lane_starts.items():
        start_g = lane_cfg["revenue_growth"]
        for scenario_name in REQUIRED_SCENARIO_KEYS:
            scenario = config.scenarios[scenario_name]
            valid = (
                revenue0 is not None
                and revenue0 > 0
                and start_g is not None
            )
            invalid_reason = None
            if revenue0 is None or revenue0 <= 0:
                invalid_reason = "missing_revenue"
            elif start_g is None:
                invalid_reason = "missing_growth"

            summary: dict[str, Any] = {
                "symbol": symbol,
                "name": str(row.get("name") or ""),
                "sector": str(row.get("sector") or ""),
                "industry": str(row.get("industry") or ""),
                "growth_lane": lane_name,
                "scenario": scenario_name,
                "close": _safe_float(row.get("close")),
                "market_cap_basic": _safe_float(row.get("market_cap_basic")),
                "total_revenue_ttm": revenue0,
                "ebitda": ebitda0,
                "ebitda_ttm": _safe_float(row.get("ebitda_ttm")),
                "ebit_ttm": ebit0,
                "oper_income_ttm": _safe_float(row.get("oper_income_ttm")),
                "net_income_ttm": net0,
                "ebitda_margin_ttm": margins0["ebitda_margin_ttm"],
                "operating_margin_ttm": margins0["operating_margin_ttm"],
                "net_margin_ttm": margins0["net_margin_ttm"],
                "total_revenue_yoy_growth_ttm": _safe_float(
                    row.get("total_revenue_yoy_growth_ttm")
                ),
                "total_revenue_cagr_5y": _safe_float(row.get("total_revenue_cagr_5y")),
                "revenue_forecast_next_fy": _safe_float(
                    row.get("revenue_forecast_next_fy")
                ),
                "ebitda_yoy_growth_ttm": _safe_float(row.get("ebitda_yoy_growth_ttm")),
                "peer_scope": peer.get("peer_scope") or "",
                "peer_n": peer.get("peer_n") or 0,
                "rev_growth_peer_median": peer.get("rev_growth_peer_median"),
                "rev_cagr_peer_median": peer.get("rev_cagr_peer_median"),
                "peer_ebitda_margin_median": peer_ebitda_m,
                "peer_operating_margin_median": peer_op_m,
                "peer_net_margin_median": peer_net_m,
                "universe_growth_fraction": universe_growth_fraction,
                "growth_lane_source": lane_cfg["growth_source"],
                "starting_growth_fraction": start_g,
                "own_revenue_growth_fraction": own["own_revenue_growth_fraction"],
                "own_revenue_growth_source": own["own_revenue_growth_source"],
                "forecast_implied_growth_fraction": own[
                    "forecast_implied_growth_fraction"
                ],
                "forecast_rejected": own["forecast_rejected"],
                "valid": False,
                "invalid_reason": invalid_reason,
                "terminal_total_revenue_ttm": None,
                "terminal_ebitda": None,
                "terminal_ebit_ttm": None,
                "terminal_net_income_ttm": None,
                "terminal_ebitda_margin_ttm": None,
                "terminal_operating_margin_ttm": None,
                "terminal_net_margin_ttm": None,
                "revenue_cagr_implied_pct": None,
                "ebitda_cagr_implied_pct": None,
                "ebit_cagr_implied_pct": None,
                "net_income_cagr_implied_pct": None,
                "y1_revenue_growth_pct": None,
                "y1_ebitda_growth_pct": None,
            }
            if not valid:
                summaries.append(summary)
                continue

            assert revenue0 is not None
            assert start_g is not None
            g_path = _growth_path(
                starting_growth=float(start_g),
                scenario=scenario,
                horizon_years=horizon,
            )

            rev = float(revenue0)
            y1_ebitda_growth_pct: float | None = None

            for year in range(0, horizon + 1):
                if year > 0:
                    rev = rev * (1.0 + g_path[year - 1])
                ebitda_m_t = _fade_margin(
                    margins0["ebitda_margin_ttm"],
                    lane_cfg["ebitda_margin_target"],
                    year=year,
                    horizon_years=horizon,
                    blend_speed=float(lane_cfg["margin_blend_speed"]),
                )
                op_m_t = _fade_margin(
                    margins0["operating_margin_ttm"],
                    lane_cfg["operating_margin_target"],
                    year=year,
                    horizon_years=horizon,
                    blend_speed=float(lane_cfg["margin_blend_speed"]),
                )
                net_m_t = _fade_margin(
                    margins0["net_margin_ttm"],
                    lane_cfg["net_margin_target"],
                    year=year,
                    horizon_years=horizon,
                    blend_speed=float(lane_cfg["margin_blend_speed"]),
                )
                ebitda_t = None if ebitda_m_t is None else rev * ebitda_m_t
                ebit_t = None if op_m_t is None else rev * op_m_t
                net_t = None if net_m_t is None else rev * net_m_t
                g_frac = None if year == 0 else g_path[year - 1]
                ebitda_vs_y0 = (
                    None
                    if ebitda_t is None or ebitda0 in (None, 0)
                    else (ebitda_t / float(ebitda0) - 1.0) * 100.0
                )
                if year == 1:
                    y1_ebitda_growth_pct = ebitda_vs_y0

                year_grids.append(
                    {
                        "symbol": symbol,
                        "growth_lane": lane_name,
                        "scenario": scenario_name,
                        "year": year,
                        "total_revenue_ttm": rev,
                        "growth_fraction": g_frac,
                        "ebitda": ebitda_t,
                        "ebit_ttm": ebit_t,
                        "net_income_ttm": net_t,
                        "ebitda_margin_ttm": ebitda_m_t,
                        "operating_margin_ttm": op_m_t,
                        "net_margin_ttm": net_m_t,
                        "revenue_growth_vs_y0_pct": (
                            (rev / float(revenue0) - 1.0) * 100.0
                        ),
                        "ebitda_growth_vs_y0_pct": ebitda_vs_y0,
                        "ebit_growth_vs_y0_pct": (
                            None
                            if ebit_t is None or ebit0 in (None, 0)
                            else (ebit_t / float(ebit0) - 1.0) * 100.0
                        ),
                        "net_income_growth_vs_y0_pct": (
                            None
                            if net_t is None or net0 in (None, 0)
                            else (net_t / float(net0) - 1.0) * 100.0
                        ),
                    }
                )

                if year == horizon:
                    summary["valid"] = True
                    summary["invalid_reason"] = None
                    summary["terminal_total_revenue_ttm"] = rev
                    summary["terminal_ebitda"] = ebitda_t
                    summary["terminal_ebit_ttm"] = ebit_t
                    summary["terminal_net_income_ttm"] = net_t
                    summary["terminal_ebitda_margin_ttm"] = ebitda_m_t
                    summary["terminal_operating_margin_ttm"] = op_m_t
                    summary["terminal_net_margin_ttm"] = net_m_t
                    summary["effective_starting_growth_fraction"] = g_path[0]
                    summary["y1_revenue_growth_pct"] = g_path[0] * 100.0
                    summary["y1_ebitda_growth_pct"] = y1_ebitda_growth_pct
                    summary["revenue_cagr_implied_pct"] = (
                        ((rev / float(revenue0)) ** (1.0 / horizon) - 1.0) * 100.0
                    )
                    if ebitda0 not in (None, 0) and ebitda_t is not None:
                        summary["ebitda_cagr_implied_pct"] = (
                            ((ebitda_t / float(ebitda0)) ** (1.0 / horizon) - 1.0)
                            * 100.0
                        )
                    if ebit0 not in (None, 0) and ebit_t is not None:
                        summary["ebit_cagr_implied_pct"] = (
                            ((ebit_t / float(ebit0)) ** (1.0 / horizon) - 1.0) * 100.0
                        )
                    if net0 not in (None, 0) and net_t is not None:
                        summary["net_income_cagr_implied_pct"] = (
                            ((net_t / float(net0)) ** (1.0 / horizon) - 1.0) * 100.0
                        )

            summaries.append(summary)

    return summaries, year_grids


def project_growth_universe(
    rows: Sequence[Mapping[str, Any]],
    *,
    config: FinancialProjectionConfig,
    peer_context_by_symbol: Mapping[str, Mapping[str, Any]],
    universe_growth_fraction: float,
    universe_ebitda_margin: float | None,
    universe_operating_margin: float | None,
    universe_net_margin: float | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summaries: list[dict[str, Any]] = []
    year_grids: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        s_rows, y_rows = project_symbol_growth_lanes(
            row,
            peer_context=peer_context_by_symbol.get(symbol, {}),
            config=config,
            universe_growth_fraction=universe_growth_fraction,
            universe_ebitda_margin=universe_ebitda_margin,
            universe_operating_margin=universe_operating_margin,
            universe_net_margin=universe_net_margin,
        )
        summaries.extend(s_rows)
        year_grids.extend(y_rows)
    return summaries, year_grids


def estimate_universe_growth_priors(
    peer_context_by_symbol: Mapping[str, Mapping[str, Any]],
) -> dict[str, float | None]:
    """Universe absolute priors from median of default peer medians."""
    growth_vals: list[float] = []
    ebitda_m_vals: list[float] = []
    op_m_vals: list[float] = []
    net_m_vals: list[float] = []
    for peer in peer_context_by_symbol.values():
        g = _pct_to_fraction(_safe_float(peer.get("rev_growth_peer_median")))
        if g is None:
            g = _pct_to_fraction(_safe_float(peer.get("rev_cagr_peer_median")))
        if g is not None:
            growth_vals.append(g)
        medians = peer.get("peer_metric_medians") or {}
        if isinstance(medians, dict):
            em = _margin_fraction(_safe_float(medians.get("ebitda_margin_ttm")))
            om = _margin_fraction(_safe_float(medians.get("operating_margin_ttm")))
            nm = _margin_fraction(_safe_float(medians.get("net_margin_ttm")))
            if em is not None:
                ebitda_m_vals.append(em)
            if om is not None:
                op_m_vals.append(om)
            if nm is not None:
                net_m_vals.append(nm)

    def _median(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        return float(ordered[len(ordered) // 2])

    return {
        "universe_growth_fraction": _median(growth_vals) if growth_vals else 0.08,
        "universe_ebitda_margin": _median(ebitda_m_vals),
        "universe_operating_margin": _median(op_m_vals),
        "universe_net_margin": _median(net_m_vals),
    }
