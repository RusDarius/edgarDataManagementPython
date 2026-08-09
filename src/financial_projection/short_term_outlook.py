"""Short-term (next quarter / next fiscal year) outlook from all-fields forecasts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_earnings_date(value: Any) -> str:
    """
    Normalize TradingView earnings date fields to ISO ``YYYY-MM-DD``.

    All-fields often stores Unix epoch seconds (sometimes as float). ISO strings
    and ``YYYYMMDD`` integers are also accepted. Returns "" when unusable.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()

    text = str(value).strip()
    if not text:
        return ""

    # Already ISO-like.
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]

    number = _safe_float(value)
    if number is None:
        return text

    # Epoch seconds (TradingView common) or milliseconds.
    if number >= 1_000_000_000_000:  # ms
        number = number / 1000.0
    if 1_000_000_000 <= number <= 4_000_000_000:
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""

    # YYYYMMDD integer
    if 19_000_000 <= number <= 21_000_000 and abs(number - round(number)) < 1e-9:
        raw = str(int(round(number)))
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"

    return ""


def _growth_pct(current: float | None, forward: float | None) -> float | None:
    if current is None or forward is None or current == 0:
        return None
    if current < 0 and forward < 0:
        # Both negative: relative improvement still meaningful as % change.
        return ((forward - current) / abs(current)) * 100.0
    if current <= 0:
        return None
    return ((forward / current) - 1.0) * 100.0


def _upside_pct(close: float | None, target: float | None) -> float | None:
    if close is None or target is None or close <= 0:
        return None
    return ((target / close) - 1.0) * 100.0


def _classify_outlook(
    *,
    fq_eps_growth_pct: float | None,
    fy_rev_growth_pct: float | None,
    street_price_upside_pct: float | None,
) -> str:
    signals: list[float] = []
    if fq_eps_growth_pct is not None:
        signals.append(fq_eps_growth_pct)
    if fy_rev_growth_pct is not None:
        signals.append(fy_rev_growth_pct)
    if street_price_upside_pct is not None:
        signals.append(street_price_upside_pct)
    if not signals:
        return "unavailable"
    avg = sum(signals) / len(signals)
    if avg >= 8.0:
        return "constructive"
    if avg <= -5.0:
        return "cautious"
    return "neutral"


def _resolve_forward_pe(row: Mapping[str, Any]) -> tuple[float | None, str]:
    """
    Prefer a true forward / next-FY P/E.

    ``price_earnings_forward_fy`` is often empty in all-fields exports; fall back
    to non-GAAP next-FY forecast P/E. Do **not** silently use trailing P/E for
    implied price (that collapses to ~EPS growth).
    """
    pe_fwd = _safe_float(row.get("price_earnings_forward_fy"))
    if pe_fwd is not None and pe_fwd > 0:
        return pe_fwd, "price_earnings_forward_fy"
    pe_nongaap = _safe_float(
        row.get("non_gaap_price_to_earnings_per_share_forecast_next_fy")
    )
    if pe_nongaap is not None and pe_nongaap > 0:
        return pe_nongaap, "non_gaap_price_to_earnings_per_share_forecast_next_fy"
    return None, "missing"


def build_short_term_outlook(
    row: Mapping[str, Any],
    *,
    close: float | None = None,
    model_y1_price: float | None = None,
    model_y1_upside_pct: float | None = None,
) -> dict[str, Any]:
    """
    Build next-quarter / next-FY summary datapoints from all-fields forecast fields.

    Compares reported levels to street forecasts so move-prediction tickers still
    inherit full all-fields short-term context.
    """
    resolved_close = close if close is not None else _safe_float(row.get("close"))

    revenue_ttm = _safe_float(row.get("total_revenue_ttm"))
    if revenue_ttm is None or revenue_ttm <= 0:
        revenue_ttm = _safe_float(row.get("total_revenue"))

    revenue_fq = _safe_float(row.get("total_revenue_fq"))
    revenue_forecast_fq = _safe_float(row.get("revenue_forecast_fq"))
    revenue_forecast_next_fq = _safe_float(row.get("revenue_forecast_next_fq"))
    revenue_forecast_next_fy = _safe_float(row.get("revenue_forecast_next_fy"))

    eps_fq = _safe_float(row.get("earnings_per_share_fq"))
    if eps_fq is None:
        eps_fq = _safe_float(row.get("earnings_per_share_diluted_fq"))
    eps_ttm = _safe_float(row.get("earnings_per_share_diluted_ttm"))
    if eps_ttm is None:
        eps_ttm = _safe_float(row.get("earnings_per_share_basic_ttm"))
    eps_forecast_fq = _safe_float(row.get("earnings_per_share_forecast_fq"))
    eps_forecast_next_fq = _safe_float(row.get("earnings_per_share_forecast_next_fq"))
    eps_forecast_next_fy = _safe_float(row.get("earnings_per_share_forecast_next_fy"))

    # Next-quarter expected growth vs latest reported quarter (prefer next FQ forecast).
    fq_rev_base = revenue_fq if revenue_fq is not None and revenue_fq > 0 else None
    fq_rev_forward = (
        revenue_forecast_next_fq
        if revenue_forecast_next_fq is not None and revenue_forecast_next_fq > 0
        else revenue_forecast_fq
    )
    st_next_fq_rev_growth_pct = _growth_pct(fq_rev_base, fq_rev_forward)

    fq_eps_base = eps_fq
    fq_eps_forward = (
        eps_forecast_next_fq
        if eps_forecast_next_fq is not None
        else eps_forecast_fq
    )
    st_next_fq_eps_growth_pct = _growth_pct(fq_eps_base, fq_eps_forward)

    # Current-quarter actual vs consensus (surprise) when both exist.
    st_fq_eps_vs_forecast_pct = _growth_pct(eps_forecast_fq, eps_fq)
    st_fq_rev_vs_forecast_pct = _growth_pct(revenue_forecast_fq, revenue_fq)

    # Next fiscal year vs TTM / FY run-rate.
    st_next_fy_rev_growth_pct = _growth_pct(revenue_ttm, revenue_forecast_next_fy)
    st_next_fy_eps_growth_pct = _growth_pct(eps_ttm, eps_forecast_next_fy)

    pe_fwd, pe_fwd_source = _resolve_forward_pe(row)
    pe_ttm = _safe_float(row.get("price_earnings_ttm"))

    target_median = _safe_float(row.get("price_target_median"))
    target_average = _safe_float(row.get("price_target_average"))
    target_1y = _safe_float(row.get("price_target_1y"))
    target_low = _safe_float(row.get("price_target_low"))
    target_high = _safe_float(row.get("price_target_high"))
    street_anchor = target_median or target_average or target_1y
    st_street_price_upside_pct = _upside_pct(resolved_close, street_anchor)
    st_street_1y_upside_pct = _upside_pct(resolved_close, target_1y)

    # Implied short-term price from next-FY EPS × forward P/E only (no TTM fallback).
    st_fy_eps_implied_price = None
    st_fy_eps_implied_upside_pct = None
    st_fy_eps_implied_usable = False
    if (
        eps_forecast_next_fy is not None
        and eps_forecast_next_fy > 0
        and pe_fwd is not None
        and pe_fwd > 0
    ):
        st_fy_eps_implied_price = eps_forecast_next_fy * pe_fwd
        st_fy_eps_implied_upside_pct = _upside_pct(
            resolved_close, st_fy_eps_implied_price
        )
        st_fy_eps_implied_usable = True

    # FY rev vs next-FQ EPS divergence (margin / quality risk).
    st_rev_eps_divergence_pp = None
    if st_next_fy_rev_growth_pct is not None and st_next_fq_eps_growth_pct is not None:
        st_rev_eps_divergence_pp = (
            st_next_fy_rev_growth_pct - st_next_fq_eps_growth_pct
        )

    outlook = _classify_outlook(
        fq_eps_growth_pct=st_next_fq_eps_growth_pct,
        fy_rev_growth_pct=st_next_fy_rev_growth_pct,
        street_price_upside_pct=st_street_price_upside_pct,
    )

    # Gap: model year-1 upside vs street target upside (positive => model more bullish).
    st_model_vs_street_upside_gap_pct = None
    if model_y1_upside_pct is not None and st_street_price_upside_pct is not None:
        st_model_vs_street_upside_gap_pct = (
            model_y1_upside_pct - st_street_price_upside_pct
        )

    # Prefer release/trading date over fiscal-period calendar date when present.
    earn_date = (
        normalize_earnings_date(row.get("earnings_release_next_date"))
        or normalize_earnings_date(row.get("earnings_release_next_trading_date_fq"))
        or normalize_earnings_date(row.get("earnings_release_next_calendar_date"))
    )

    return {
        # Exact all-fields levels / forecasts (no renamed aliases)
        "total_revenue_fq": revenue_fq,
        "revenue_forecast_fq": revenue_forecast_fq,
        "revenue_forecast_next_fq": revenue_forecast_next_fq,
        "revenue_forecast_next_fy": revenue_forecast_next_fy,
        "earnings_per_share_fq": eps_fq,
        "earnings_per_share_forecast_fq": eps_forecast_fq,
        "earnings_per_share_forecast_next_fq": eps_forecast_next_fq,
        "earnings_per_share_diluted_ttm": eps_ttm,
        "earnings_per_share_forecast_next_fy": eps_forecast_next_fy,
        "price_earnings_forward_fy": pe_fwd,
        "price_earnings_ttm": pe_ttm,
        "price_target_median": target_median,
        "price_target_average": target_average,
        "price_target_1y": target_1y,
        "price_target_low": target_low,
        "price_target_high": target_high,
        # Computed short-term metrics (st_* = derived, not all-fields renames)
        "st_next_fq_rev_growth_pct": st_next_fq_rev_growth_pct,
        "st_next_fq_eps_growth_pct": st_next_fq_eps_growth_pct,
        "st_fq_eps_vs_forecast_pct": st_fq_eps_vs_forecast_pct,
        "st_fq_rev_vs_forecast_pct": st_fq_rev_vs_forecast_pct,
        "st_earnings_release_next_calendar_date": earn_date,
        "st_next_fy_rev_growth_pct": st_next_fy_rev_growth_pct,
        "st_next_fy_eps_growth_pct": st_next_fy_eps_growth_pct,
        "st_price_earnings_forward_source": pe_fwd_source,
        "st_fy_eps_implied_price": st_fy_eps_implied_price,
        "st_fy_eps_implied_upside_pct": st_fy_eps_implied_upside_pct,
        "st_fy_eps_implied_usable": st_fy_eps_implied_usable,
        "st_rev_eps_divergence_pp": st_rev_eps_divergence_pp,
        "st_street_price_upside_pct": st_street_price_upside_pct,
        "st_street_1y_upside_pct": st_street_1y_upside_pct,
        "st_model_y1_price": model_y1_price,
        "st_model_y1_upside_pct": model_y1_upside_pct,
        "st_model_vs_street_upside_gap_pct": st_model_vs_street_upside_gap_pct,
        "st_outlook": outlook,
    }
