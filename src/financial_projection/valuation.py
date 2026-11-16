"""Forward value over at most five years: what a company is worth today on its projected earnings, and what that implies.

Pure math, no I/O. Per name and scenario (bear / base / bull, knobs in config/financial_projection/value_v1.json):

1. Year 1 is the street's: revenue growth from ``revenue_forecast_next_fy`` and EPS from ``earnings_per_share_forecast_next_fy``
   (fallbacks: TTM YoY / 5y CAGR / the industry median for growth, TTM EPS carried at the TTM margin for EPS).
2. Years 2-H: growth decays from year 1 (capped after year 1) toward a long-run rate (half the industry's 5y revenue CAGR,
   half a GDP-like rate); the net margin moves in a straight line from the year-1 margin part of the way to the industry
   median (pull down / up per scenario, a lift capped in points and relative to today's margin); buybacks shrink the share count.
3. Year H price: EPS_H x an exit P/E (own forward P/E blended with the industry median, scaled, floored and capped). Names
   with no earnings at year 1 or year H, outside the earnings-only sectors, use an exit EV/Sales on projected revenue less
   projected net debt.
4. Fair value today = dividends and the year-H price discounted at the cost of equity (risk-free + beta x equity premium);
   ``irr`` is the annual return the scenario pays from today's price; ``excess_irr`` = irr - the hurdle.
5. Cross-checks: an FCF-to-equity DCF (H years of FCF + a low-growth terminal value) and a reverse solve: the constant revenue
   CAGR the price needs to earn the hurdle on the base margins and exit multiple (``priced_in_cagr``).

The scenario-weighted fair value is the probability-weighted value of one measure under three paths (weights in the recipe),
not a score. No ranking, no cutoff, no course.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping, Sequence

SCENARIOS: tuple[str, ...] = ("bear", "base", "bull")


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _pct(value: Any) -> float | None:
    """TradingView percent points (12.5) to a fraction (0.125).

    Every field read through this (net margin, revenue growth, 5y CAGR, dividend and buyback yield) is in percent points
    on the all-fields scan, so the division is unconditional: a magnitude guess would read a 1.7% buyback yield or a 2%
    net margin as 170% / 200% (about half of the buyback yields and a tenth of the margins sit under 2.5 points)."""
    number = _f(value)
    return None if number is None else number / 100.0


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _r(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


# --- building blocks --------------------------------------------------------------------------------------


def cost_of_equity(beta: float | None, discount: Mapping[str, Any]) -> float:
    """Risk-free + beta x equity premium (beta clipped); a flat risk-free + premium when beta is off or missing."""
    rf = float(discount.get("risk_free", 0.0475))
    erp = float(discount.get("equity_risk_premium", 0.05))
    if not discount.get("use_beta", True) or beta is None:
        return rf + erp
    return rf + _clip(beta, float(discount.get("beta_floor", 0.6)), float(discount.get("beta_cap", 2.0))) * erp


def growth_path(g1: float, *, g_end: float, cap_after_y1: float, decay: float, horizon: int) -> list[float]:
    """Year 1 = g1; from year 2 the gap to g_end (starting from g1 capped) shrinks by ``decay`` a year."""
    path = [g1]
    start = min(g1, cap_after_y1)
    for t in range(2, horizon + 1):
        path.append(g_end + (start - g_end) * decay ** (t - 1))
    return path


def margin_path(m1: float, m_target: float, horizon: int) -> list[float]:
    """Straight line from the year-1 margin to the target at year H."""
    if horizon <= 1:
        return [m1]
    return [m1 + (m_target - m1) * (t - 1) / (horizon - 1) for t in range(1, horizon + 1)]


def present_value(dividends: Sequence[float], terminal: float, rate: float) -> float:
    """Dividends paid at the end of years 1..H plus the year-H price, discounted at ``rate``."""
    pv = sum(d / (1.0 + rate) ** t for t, d in enumerate(dividends, 1))
    return pv + terminal / (1.0 + rate) ** len(dividends)


def internal_rate(price0: float, dividends: Sequence[float], terminal: float, *, low: float = -0.95, high: float = 5.0) -> float | None:
    """Annual return that prices the cash flows at ``price0`` (bisection; None when the bracket holds no root)."""
    if price0 <= 0 or terminal + sum(dividends) <= 0:
        return None
    f_low = present_value(dividends, terminal, low) - price0
    f_high = present_value(dividends, terminal, high) - price0
    if f_low * f_high > 0:
        return None
    for _ in range(200):
        mid = (low + high) / 2.0
        f_mid = present_value(dividends, terminal, mid) - price0
        if abs(f_mid) < 1e-9:
            break
        if (f_mid > 0) == (f_low > 0):
            low, f_low = mid, f_mid
        else:
            high = mid
    return (low + high) / 2.0


# --- inputs and industry context --------------------------------------------------------------------------


@dataclass(frozen=True)
class ValueInputs:
    symbol: str
    sector: str
    industry: str
    close: float
    mcap: float
    rev0: float
    rev_fy: float | None
    rev_yoy: float | None
    rev_cagr5: float | None
    eps_ttm: float | None
    eps_fy: float | None
    net_margin: float | None
    net_debt: float
    ev_rev: float | None
    fcf_ttm: float | None
    ni_ttm: float | None
    beta: float | None
    div_yield: float | None
    buyback_yield: float | None
    pe_ttm: float | None
    ev: float | None
    shares_out: float | None

    @property
    def rev0_m(self) -> float:
        """TTM revenue in the market cap's currency (TradingView EV / EV-to-sales); the raw figure when EV is missing."""
        if self.ev and self.ev > 0 and self.ev_rev and self.ev_rev > 0:
            return self.ev / self.ev_rev
        return self.rev0

    @property
    def net_debt_m(self) -> float:
        """Net debt in the market cap's currency (EV - market cap); the reported figure when EV is missing."""
        return self.ev - self.mcap if self.ev and self.ev > 0 else self.net_debt


def value_inputs(row: Mapping[str, Any]) -> ValueInputs | None:
    """The fields the model reads (exact all-fields names); None when price, size or revenue is missing."""
    close, mcap = _f(row.get("close")), _f(row.get("market_cap_basic"))
    rev0 = _f(row.get("total_revenue_ttm")) or _f(row.get("total_revenue"))
    if not close or close <= 0 or not mcap or mcap <= 0 or not rev0 or rev0 <= 0:
        return None
    net_debt = _f(row.get("net_debt"))
    if net_debt is None:
        debt, cash = _f(row.get("total_debt")), _f(row.get("cash_n_equivalents_fq"))
        net_debt = (debt or 0.0) - (cash or 0.0)
    return ValueInputs(
        symbol=str(row.get("symbol") or ""), sector=str(row.get("sector") or ""), industry=str(row.get("industry") or ""),
        close=close, mcap=mcap, rev0=rev0, rev_fy=_f(row.get("revenue_forecast_next_fy")),
        rev_yoy=_pct(row.get("total_revenue_yoy_growth_ttm")), rev_cagr5=_pct(row.get("total_revenue_cagr_5y")),
        eps_ttm=_f(row.get("earnings_per_share_diluted_ttm")), eps_fy=_f(row.get("earnings_per_share_forecast_next_fy")),
        net_margin=_pct(row.get("net_margin_ttm")), net_debt=net_debt, ev_rev=_f(row.get("enterprise_value_to_revenue_ttm")),
        fcf_ttm=_f(row.get("free_cash_flow_ttm")), ni_ttm=_f(row.get("net_income_ttm")), beta=_f(row.get("beta_1_year")),
        # dividends_yield_current is the populated field on the all-fields scan (dividend_yield_recent comes back empty).
        div_yield=_pct(row.get("dividends_yield_current") if _f(row.get("dividends_yield_current")) is not None
                       else row.get("dividend_yield_recent")),
        buyback_yield=_pct(row.get("buyback_yield")),
        pe_ttm=_f(row.get("price_earnings_ttm")), ev=_f(row.get("enterprise_value_current")),
        shares_out=_f(row.get("total_shares_outstanding")),
    )


def currency_mismatch(inp: ValueInputs, tolerance: float = 0.5) -> bool:
    """True when the price, the per-share figures and the market cap are not in one currency (secondary listings, ADRs,
    pence quotes): market cap / close against the share count, close / EPS against TradingView's P/E and against
    market cap / net income. Per-share values could not be compared with the price."""
    def off(ratio: float) -> bool:
        return abs(ratio - 1.0) > tolerance

    if inp.shares_out and inp.shares_out > 0 and off(inp.mcap / inp.close / inp.shares_out):
        return True
    if inp.eps_ttm is not None and inp.eps_ttm > 0:
        if inp.pe_ttm is not None and inp.pe_ttm > 0 and off(inp.close / inp.eps_ttm / inp.pe_ttm):
            return True
        if inp.ni_ttm is not None and inp.ni_ttm > 0 and off((inp.close / inp.eps_ttm) / (inp.mcap / inp.ni_ttm)):
            return True
    return False


def street_growth(inp: ValueInputs, growth: Mapping[str, Any], industry_g1: float | None) -> tuple[float | None, str]:
    """Year-1 revenue growth and where it came from (street forecast, TTM YoY, 5y CAGR, industry median), clipped."""
    if inp.rev_fy is not None and inp.rev_fy > 0:
        g, source = inp.rev_fy / inp.rev0 - 1.0, "street_revenue"
    elif inp.rev_yoy is not None:
        g, source = inp.rev_yoy, "ttm_yoy"
    elif inp.rev_cagr5 is not None:
        g, source = inp.rev_cagr5, "cagr_5y"
    elif industry_g1 is not None:
        g, source = industry_g1, "industry_median"
    else:
        return None, "missing"
    return _clip(g, float(growth.get("g1_floor", -0.5)), float(growth.get("g1_cap", 1.5))), source


def year1_eps(inp: ValueInputs, g1: float) -> tuple[float | None, float | None, str]:
    """(EPS year 1, net margin year 1, source). The street EPS sets year 1; the margin is derived from it without currency mixing."""
    if inp.eps_fy is not None:
        eps1, source = inp.eps_fy, "street_eps"
    elif inp.eps_ttm is not None and inp.eps_ttm > 0:
        eps1, source = inp.eps_ttm * (1.0 + g1), "ttm_eps_at_ttm_margin"
    elif inp.net_margin is not None:
        eps1, source = inp.close * inp.rev0_m * (1.0 + g1) * inp.net_margin / inp.mcap, "ttm_margin"
    else:
        return None, None, "missing"
    if inp.net_margin is not None and inp.eps_ttm is not None and inp.eps_ttm > 0 and inp.net_margin > 0:
        m1 = inp.net_margin * (eps1 / inp.eps_ttm) / (1.0 + g1)
    else:
        m1 = eps1 * inp.mcap / inp.close / (inp.rev0_m * (1.0 + g1))
    return eps1, m1, source


def industry_medians(rows: Sequence[Mapping[str, Any]], *, min_n: int, growth: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Per symbol: industry (else sector, else universe) medians of forward P/E, EV/Sales, net margin, street growth, 5y CAGR."""
    keys = ("pe_fwd", "ev_rev", "net_margin", "g1", "cagr5")
    per_row: list[tuple[str, str, str, dict[str, float]]] = []
    for row in rows:
        inp = value_inputs(row)
        if inp is None:
            continue
        vals: dict[str, float] = {}
        if inp.eps_fy is not None and inp.eps_fy > 0:
            vals["pe_fwd"] = _clip(inp.close / inp.eps_fy, 1.0, 200.0)
        if inp.ev_rev is not None and inp.ev_rev > 0:
            vals["ev_rev"] = min(inp.ev_rev, 80.0)
        if inp.net_margin is not None:
            vals["net_margin"] = _clip(inp.net_margin, -1.0, 1.0)
        g1, src = street_growth(inp, growth, None)
        if g1 is not None and src != "missing":
            vals["g1"] = g1
        if inp.rev_cagr5 is not None:
            vals["cagr5"] = _clip(inp.rev_cagr5, -0.5, 1.0)
        per_row.append((inp.symbol, inp.industry, inp.sector, vals))

    def collect(level: int) -> dict[str, dict[str, list[float]]]:
        out: dict[str, dict[str, list[float]]] = {}
        for _sym, ind, sec, vals in per_row:
            group = (ind, sec, "*")[level]
            for k, v in vals.items():
                out.setdefault(group, {}).setdefault(k, []).append(v)
        return out

    by_ind, by_sec, by_all = collect(0), collect(1), collect(2)
    result: dict[str, dict[str, Any]] = {}
    for sym, ind, sec, _vals in per_row:
        rec: dict[str, Any] = {}
        for k in keys:
            for label, group in (("industry", by_ind.get(ind, {})), ("sector", by_sec.get(sec, {})), ("universe", by_all.get("*", {}))):
                values = group.get(k) or []
                if len(values) >= min_n:
                    rec[k], rec[f"{k}_scope"] = median(values), label
                    break
        result[sym] = rec
    return result


# --- one scenario -----------------------------------------------------------------------------------------


def _path(inp: ValueInputs, *, name: str, g1: float, eps1: float, m1: float, med: Mapping[str, Any], scen: Mapping[str, Any],
          recipe: Mapping[str, Any], horizon: int, constant_growth: float | None = None) -> dict[str, Any]:
    """Revenue, margin, EPS, dividends and share count for one scenario (or a constant-growth path for the reverse solve)."""
    growth = recipe.get("growth") or {}
    cash = recipe.get("cash") or {}
    if constant_growth is not None:
        gs = [constant_growth] * horizon
        eps1_s = eps1 * (1.0 + constant_growth) / (1.0 + g1)
        capped = False
    else:
        g1_s = g1 + (float(scen["g1_scale"]) - 1.0) * abs(g1)
        cagr_ind = med.get("cagr5")
        long_run = float(growth.get("long_run_growth", 0.04))
        w = float(growth.get("industry_long_run_weight", 0.5))
        anchor = long_run if cagr_ind is None else w * _clip(cagr_ind, 0.0, 0.12) + (1.0 - w) * long_run
        g_end = _clip(anchor + float(scen["g_end_shift"]), float(growth.get("g_end_floor", 0.0)), float(growth.get("g_end_cap", 0.10)))
        gs = growth_path(g1_s, g_end=g_end, cap_after_y1=float(scen["growth_cap_after_y1"]), decay=float(scen["growth_decay"]), horizon=horizon)
        eps1_s = eps1 * (1.0 + float(scen["eps1_adjust"])) * (1.0 + g1_s) / (1.0 + g1)
        capped = g1_s > float(scen["growth_cap_after_y1"])
    m_ind = med.get("net_margin")
    m_target = m1
    if m_ind is not None:
        # Toward the industry margin: down at margin_pull_down when above it, up at margin_pull_up when below it.
        pull = float(scen["margin_pull_down"] if m_ind < m1 else scen["margin_pull_up"])
        m_target = m1 + (m_ind - m1) * pull
    lift_cap = float(growth.get("margin_lift_cap", 0.05))
    if m1 > 0:
        lift_cap = min(lift_cap, float(growth.get("margin_lift_rel_cap", 0.5)) * m1)
    m_target = min(m_target, m1 + lift_cap)
    ms = margin_path(m1, m_target, horizon)
    drift = _clip(-(inp.buyback_yield or 0.0) * float(scen["buyback_weight"]), float(cash.get("share_drift_floor", -0.05)),
                  float(cash.get("share_drift_cap", 0.03)))
    payout = 0.0
    if inp.div_yield and inp.eps_ttm and inp.eps_ttm > 0:
        payout = _clip(inp.div_yield * inp.close / inp.eps_ttm, 0.0, 1.0)
    ratio_floor = float(growth.get("eps_ratio_min_margin", 0.02))
    rev, revs, eps, divs, shares = inp.rev0, [], [], [], []
    for t in range(1, horizon + 1):
        rev *= 1.0 + gs[t - 1]
        sh = (1.0 + drift) ** (t - 1)
        if m1 >= ratio_floor and eps1_s > 0:
            e = eps1_s * (rev / revs[0] if revs else 1.0) * (ms[t - 1] / m1) / sh
        else:  # thin or negative margins: net income on revenue in the market cap's currency
            e = inp.close * inp.rev0_m * (rev / inp.rev0) * ms[t - 1] / inp.mcap / sh
        revs.append(rev)
        eps.append(e)
        shares.append(sh)
        divs.append(payout * max(e, 0.0))
    return {"growth": gs, "rev": revs, "margin": ms, "eps": eps, "div": divs, "shares": shares, "payout": payout,
            "m_target": m_target, "g1_capped": capped, "drift": drift}


def _exit_price(inp: ValueInputs, path: Mapping[str, Any], *, eps1: float, med: Mapping[str, Any], scen: Mapping[str, Any],
                recipe: Mapping[str, Any], lens: str | None = None) -> dict[str, Any]:
    """Year-H price on the given lens (default: earnings when year-1 and year-H EPS are positive or the sector is earnings-only).
    An earnings-lens path whose year-H EPS is not positive falls back to the sales lens."""
    mult = recipe.get("multiples") or {}
    w_own, scale = float(scen["own_multiple_weight"]), float(scen["multiple_scale"])
    eps_h = path["eps"][-1]
    earnings_only = inp.sector in set(mult.get("earnings_only_sectors") or [])
    if lens is None:
        lens = "earnings" if earnings_only or (eps1 > 0 and eps_h > 0) else "sales"
    if lens == "earnings" and (eps_h > 0 or earnings_only):
        pe_own = inp.close / eps1 if eps1 > 0 else None
        pe_ind = med.get("pe_fwd")
        parts = [(w_own, pe_own), (1.0 - w_own, pe_ind)]
        used = [(w, v) for w, v in parts if v is not None]
        if not used or eps_h <= 0:
            return {"lens": "earnings", "exit_multiple": None, "price_h": None, "multiple_capped": False}
        raw = sum(w * v for w, v in used) / sum(w for w, _ in used) * scale
        pe = _clip(raw, float(mult.get("pe_floor", 6.0)), float(mult.get("pe_cap", 35.0)))
        return {"lens": "earnings", "exit_multiple": pe, "price_h": eps_h * pe, "multiple_capped": pe != raw}
    evs_own, evs_ind = inp.ev_rev, med.get("ev_rev")
    m_ind, m_h = med.get("net_margin"), path["margin"][-1]
    if evs_ind is not None and m_ind is not None and m_ind > 0:
        # EV/Sales scales with margin: the industry multiple applies in proportion to the margin the name reaches.
        evs_ind = evs_ind * _clip(m_h / m_ind, 0.0, 1.0)
    used = [(w, v) for w, v in ((w_own, evs_own), (1.0 - w_own, evs_ind)) if v is not None and v > 0]
    if not used:
        return {"lens": "sales", "exit_multiple": None, "price_h": None, "multiple_capped": False}
    raw = sum(w * v for w, v in used) / sum(w for w, _ in used) * scale
    evs = _clip(raw, float(mult.get("evs_floor", 0.2)), float(mult.get("evs_cap", 25.0)))
    # Net debt at year H: today's, less retained earnings (losses add to it), in the reporting currency of the totals.
    retained = sum(inp.mcap / inp.close * e * sh * (1.0 - path["payout"]) for e, sh in zip(path["eps"], path["shares"]))
    equity_h = evs * inp.rev0_m * path["rev"][-1] / inp.rev0 - (inp.net_debt_m - retained)
    price_h = inp.close * equity_h / inp.mcap / path["shares"][-1] if equity_h > 0 else None
    return {"lens": "sales", "exit_multiple": evs, "price_h": price_h, "multiple_capped": evs != raw}


def _dcf(inp: ValueInputs, path: Mapping[str, Any], *, rate: float, scen: Mapping[str, Any], recipe: Mapping[str, Any]) -> float | None:
    """FCF to equity per share for H years plus a low-growth terminal value, discounted at the cost of equity."""
    cash = recipe.get("cash") or {}
    horizon = len(path["eps"])
    if inp.fcf_ttm is None or inp.sector in set((recipe.get("multiples") or {}).get("earnings_only_sectors") or []):
        return None  # financials: operating cash flow carries float and deposits, not owner cash
    fcf_ps0 = inp.fcf_ttm / inp.mcap * inp.close
    conv0 = None
    if inp.ni_ttm and inp.ni_ttm > 0:
        conv0 = _clip(inp.fcf_ttm / inp.ni_ttm, float(cash.get("fcf_conversion_floor", 0.2)), float(cash.get("fcf_conversion_cap", 2.0)))
    conv_end = float(cash.get("fcf_conversion_end", 1.0))
    flows = []
    for t, e in enumerate(path["eps"], 1):
        if conv0 is not None and e > 0:
            conv = conv0 + (conv_end - conv0) * (t - 1) / max(horizon - 1, 1)
            flows.append(e * conv)
        else:
            flows.append(fcf_ps0 * path["rev"][t - 1] / inp.rev0 / path["shares"][t - 1])
    g_term = float(scen["terminal_growth"])
    if rate - g_term < 0.01 or flows[-1] <= 0:
        return None
    terminal = flows[-1] * (1.0 + g_term) / (rate - g_term)
    return present_value(flows, terminal, rate)


def value_scenario(inp: ValueInputs, *, scenario: str, med: Mapping[str, Any], recipe: Mapping[str, Any],
                   lens: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Summary row and year rows for one name under one scenario (``lens`` fixed by the caller keeps scenarios comparable)."""
    horizon = max(1, min(int(recipe.get("horizon_years", 5)), 5))
    scen = recipe["scenarios"][scenario]
    rate = cost_of_equity(inp.beta, recipe.get("discount") or {})
    g1, g_source = street_growth(inp, recipe.get("growth") or {}, med.get("g1"))
    out: dict[str, Any] = {"symbol": inp.symbol, "scenario": scenario, "close": inp.close, "hurdle": _r(rate), "g1": _r(g1),
                           "g1_source": g_source, "valid": False, "invalid_reason": ""}
    if currency_mismatch(inp):
        out["invalid_reason"] = "currency_mismatch"
        return out, []
    if g1 is None:
        out["invalid_reason"] = "missing_growth"
        return out, []
    eps1, m1, eps_source = year1_eps(inp, g1)
    out.update({"eps1": _r(eps1), "m1": _r(m1), "eps1_source": eps_source})
    if eps1 is None or m1 is None:
        out["invalid_reason"] = "missing_earnings"
        return out, []
    if eps1 > 0 and inp.close / eps1 < float((recipe.get("multiples") or {}).get("min_plausible_pe", 2.0)):
        out["invalid_reason"] = "implausible_pe"  # a forward P/E under 2 is a data or currency error, not a bargain
        return out, []
    path = _path(inp, name=scenario, g1=g1, eps1=eps1, m1=m1, med=med, scen=scen, recipe=recipe, horizon=horizon)
    exit_ = _exit_price(inp, path, eps1=eps1, med=med, scen=scen, recipe=recipe, lens=lens)
    years = [{"symbol": inp.symbol, "scenario": scenario, "year": t, "growth": _r(path["growth"][t - 1]),
              "revenue": _r(path["rev"][t - 1], 0), "net_margin": _r(path["margin"][t - 1]), "eps": _r(path["eps"][t - 1]),
              "dividend": _r(path["div"][t - 1]), "share_factor": _r(path["shares"][t - 1])} for t in range(1, horizon + 1)]
    out.update({"lens": exit_["lens"], "exit_multiple": _r(exit_["exit_multiple"], 2), "multiple_capped": exit_["multiple_capped"],
                "g1_capped": path["g1_capped"], "rev_cagr": _r((path["rev"][-1] / inp.rev0) ** (1.0 / horizon) - 1.0),
                "eps_h": _r(path["eps"][-1]), "margin_h": _r(path["margin"][-1]), "payout": _r(path["payout"]),
                "share_drift": _r(path["drift"]), "dividends_sum": _r(sum(path["div"]))})
    price_h = exit_["price_h"]
    if price_h is None or price_h <= 0:
        out["invalid_reason"] = "no_positive_exit_value"
        return out, years
    fv = present_value(path["div"], price_h, rate)
    irr = internal_rate(inp.close, path["div"], price_h)
    dcf = _dcf(inp, path, rate=rate, scen=scen, recipe=recipe)
    out.update({"valid": True, "price_h": _r(price_h, 2), "fair_value": _r(fv, 2), "fv_upside_pct": _r((fv / inp.close - 1.0) * 100, 2),
                "irr": _r(irr), "excess_irr": _r(irr - rate) if irr is not None else None,
                "dcf_value": _r(dcf, 2), "dcf_upside_pct": _r((dcf / inp.close - 1.0) * 100, 2) if dcf else None,
                "_div": path["div"], "_price_h": price_h})
    return out, years


def _reprice(summary: dict[str, Any], close: float, price_h: float) -> None:
    """Recompute a scenario's value fields on a new year-H price (same dividends and hurdle)."""
    rate = summary["hurdle"]
    fv = present_value(summary["_div"], price_h, rate)
    irr = internal_rate(close, summary["_div"], price_h)
    summary.update({"_price_h": price_h, "price_h": _r(price_h, 2), "fair_value": _r(fv, 2),
                    "fv_upside_pct": _r((fv / close - 1.0) * 100, 2), "irr": _r(irr),
                    "excess_irr": _r(irr - rate) if irr is not None else None})


def priced_in_cagr(inp: ValueInputs, *, med: Mapping[str, Any], recipe: Mapping[str, Any]) -> float | None:
    """Constant revenue CAGR over the horizon at which the base path's fair value equals today's price (None if not bracketed)."""
    horizon = max(1, min(int(recipe.get("horizon_years", 5)), 5))
    scen = recipe["scenarios"]["base"]
    rate = cost_of_equity(inp.beta, recipe.get("discount") or {})
    g1, _ = street_growth(inp, recipe.get("growth") or {}, med.get("g1"))
    if g1 is None:
        return None
    eps1, m1, _ = year1_eps(inp, g1)
    if eps1 is None or m1 is None or m1 <= 0 or eps1 <= 0 or currency_mismatch(inp):
        return None

    def gap(c: float) -> float | None:
        path = _path(inp, name="base", g1=g1, eps1=eps1, m1=m1, med=med, scen=scen, recipe=recipe, horizon=horizon, constant_growth=c)
        exit_ = _exit_price(inp, path, eps1=eps1, med=med, scen=scen, recipe=recipe)
        if exit_["price_h"] is None:
            return None
        return present_value(path["div"], exit_["price_h"], rate) - inp.close

    rev = recipe.get("reverse") or {}
    low, high = float(rev.get("cagr_low", -0.6)), float(rev.get("cagr_high", 1.5))
    f_low, f_high = gap(low), gap(high)
    if f_low is None or f_high is None or f_low * f_high > 0:
        return None
    for _ in range(80):
        mid = (low + high) / 2.0
        f_mid = gap(mid)
        if f_mid is None:
            return None
        if (f_mid > 0) == (f_low > 0):
            low, f_low = mid, f_mid
        else:
            high = mid
    return (low + high) / 2.0


def value_name(row: Mapping[str, Any], *, med: Mapping[str, Any], recipe: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """One wide row per name (bear / base / bull side by side, scenario-weighted value and IRR, priced-in growth),
    plus the per-scenario summaries and year rows."""
    inp = value_inputs(row)
    if inp is None:
        return None, [], []
    weights = {k: float(v) for k, v in (recipe.get("scenario_weights") or {}).items()}
    summaries, years, by = [], [], {}
    lens = None
    for s in ("base", "bear", "bull"):  # the base path sets the lens for all three
        summary, rows = value_scenario(inp, scenario=s, med=med, recipe=recipe, lens=lens)
        lens = lens or summary.get("lens")
        by[s] = summary
        years.extend(rows)
    base = by["base"]
    flags: list[str] = []
    if base.get("valid"):
        # Keep year-H prices ordered bear <= base <= bull (lens fallbacks and thin equity slices can invert them); flagged.
        for s, wrong in (("bear", lambda p: p > base["_price_h"]), ("bull", lambda p: p < base["_price_h"])):
            r = by[s]
            if r.get("valid") and wrong(r["_price_h"]):
                _reprice(r, inp.close, base["_price_h"])
                flags.append(f"{s}_set_to_base")
    wide: dict[str, Any] = {
        "symbol": inp.symbol, "name": row.get("name"), "sector": inp.sector, "industry": inp.industry, "close": inp.close,
        "market_cap_basic": inp.mcap, "beta_1_year": inp.beta, "hurdle_pct": _r((base.get("hurdle") or 0) * 100, 2),
        "lens": base.get("lens"), "g1_pct": _r(base["g1"] * 100, 2) if base.get("g1") is not None else None, "g1_source": base.get("g1_source"),
        "eps_ttm": inp.eps_ttm, "eps1": base.get("eps1"), "eps1_source": base.get("eps1_source"),
        "pe_fwd_now": _r(inp.close / base["eps1"], 2) if base.get("eps1") and base["eps1"] > 0 else None,
        "pe_ind_med": _r(med.get("pe_fwd"), 2), "pe_scope": med.get("pe_fwd_scope"), "evs_ind_med": _r(med.get("ev_rev"), 2),
        "m1_pct": _r(base["m1"] * 100, 2) if base.get("m1") is not None else None,
        "margin_ind_med_pct": _r(med["net_margin"] * 100, 2) if med.get("net_margin") is not None else None,
    }
    for s in SCENARIOS:
        r = by[s]
        wide[f"fv_{s}_px"] = r.get("fair_value")
        wide[f"irr_{s}_pct"] = _r(r["irr"] * 100, 2) if r.get("irr") is not None else None
        wide[f"px_y5_{s}"] = r.get("price_h")
        wide[f"eps_y5_{s}"] = r.get("eps_h")
        wide[f"exit_mult_{s}"] = r.get("exit_multiple")
        wide[f"rev_cagr_{s}_pct"] = _r(r["rev_cagr"] * 100, 2) if r.get("rev_cagr") is not None else None
        if r.get("multiple_capped"):
            flags.append(f"{s}_multiple_capped")
        if not r.get("valid"):
            flags.append(f"{s}_{r.get('invalid_reason') or 'invalid'}")
    if base.get("g1_capped"):
        flags.append("y1_growth_above_cap")
    if inp.mcap > 0 and inp.net_debt_m / inp.mcap > 1.0:
        flags.append("net_debt_over_mcap")  # a low P/E on a levered equity is partly the debt's risk
    if wide.get("pe_fwd_now") and wide.get("pe_ind_med") and wide["pe_fwd_now"] < 0.5 * wide["pe_ind_med"]:
        flags.append("pe_under_half_industry")  # often a cyclical at peak earnings: read the bear case
    valid = [s for s in SCENARIOS if by[s].get("valid")]
    if valid and "base" in valid:
        total = sum(weights.get(s, 0.0) for s in valid) or 1.0
        fv_exp = sum(weights.get(s, 0.0) * by[s]["fair_value"] for s in valid) / total
        horizon = len(by["base"]["_div"])
        divs = [sum(weights.get(s, 0.0) * by[s]["_div"][t] for s in valid) / total for t in range(horizon)]
        price_h = sum(weights.get(s, 0.0) * by[s]["_price_h"] for s in valid) / total
        irr = internal_rate(inp.close, divs, price_h)
        hurdle = base["hurdle"]
        wide.update({"fv_exp_px": _r(fv_exp, 2), "fv_upside_pct": _r((fv_exp / inp.close - 1.0) * 100, 2),
                     "irr_exp_pct": _r(irr * 100, 2) if irr is not None else None,
                     "excess_irr_pp": _r((irr - hurdle) * 100, 2) if irr is not None else None,
                     "fv_range_pct": _r((by["bull"]["fair_value"] - by["bear"]["fair_value"]) / inp.close * 100, 2)
                     if by["bull"].get("valid") and by["bear"].get("valid") else None,
                     "dcf_base_px": base.get("dcf_value"), "dcf_upside_pct": base.get("dcf_upside_pct")})
    cagr = priced_in_cagr(inp, med=med, recipe=recipe)
    wide["priced_in_cagr_pct"] = _r(cagr * 100, 2) if cagr is not None else None
    if cagr is not None and wide.get("rev_cagr_base_pct") is not None:
        wide["growth_gap_pp"] = _r(wide["rev_cagr_base_pct"] - cagr * 100, 2)
    wide["n_valid_scenarios"] = len(valid)
    wide["flags"] = "|".join(flags)
    for s in SCENARIOS:
        summary = {k: v for k, v in by[s].items() if not k.startswith("_")}
        summaries.append(summary)
    return wide, summaries, years


def value_universe(rows: Sequence[Mapping[str, Any]], *, recipe: Mapping[str, Any], project: Sequence[Mapping[str, Any]] | None = None,
                   ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Industry medians over ``rows``; values for ``project`` (default every row)."""
    meds = industry_medians(rows, min_n=int(recipe.get("min_industry_n", 8)), growth=recipe.get("growth") or {})
    names, summaries, years = [], [], []
    for row in project if project is not None else rows:
        sym = str(row.get("symbol") or "")
        wide, summ, yrs = value_name(row, med=meds.get(sym) or {}, recipe=recipe)
        if wide is None:
            continue
        names.append(wide)
        summaries.extend(summ)
        years.extend(yrs)
    return names, summaries, years
