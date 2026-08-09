"""Industry / sector / global peer scales for projection assumptions."""

from __future__ import annotations

from statistics import median
from typing import Any, Mapping, Sequence

from .fields import PEER_METRIC_FIELDS


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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
        # Multiples must be positive; growth rates may be negative.
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
        stats[field_name] = {
            "median": float(median(trimmed)),
            "count": len(trimmed),
            "raw_count": len(values),
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


def build_peer_scale_context(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_peer_group_size: int = 8,
    peer_trim_fraction: float = 0.05,
    relative_scale_clip_low: float = 0.25,
    relative_scale_clip_high: float = 3.0,
    peer_mcap_band_low: float = 0.25,
    peer_mcap_band_high: float = 4.0,
    peer_mcap_refine_min_industry_n: int = 40,
) -> dict[str, dict[str, Any]]:
    """
    Build per-symbol peer context with industry → sector → global fallback.

    When an industry group is large (``peer_mcap_refine_min_industry_n``), refine
    to market-cap peers within ``[band_low, band_high] × own mcap`` if that band
    still meets ``min_peer_group_size`` (scope becomes ``industry_mcap``).

    Returns mapping symbol -> peer context dict.
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

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        sector = str(row.get("sector") or "").strip() or "Unknown"
        industry = str(row.get("industry") or "").strip() or "Unknown"
        market_cap = _safe_float(row.get("market_cap_basic"))

        industry_key = (sector, industry)
        industry_rows = by_industry.get(industry_key, [])
        industry_group = industry_stats.get(industry_key, {})
        sector_group = sector_stats.get(sector, {})

        peer_scope = "global"
        peer_stats = global_stats
        peer_n = int(global_stats.get("enterprise_value_to_revenue_ttm", {}).get("count", 0))

        industry_n = int(
            industry_group.get("enterprise_value_to_revenue_ttm", {}).get("count", 0)
        )
        sector_n = int(
            sector_group.get("enterprise_value_to_revenue_ttm", {}).get("count", 0)
        )

        if industry_n >= min_peer_group_size:
            peer_scope = "industry"
            peer_stats = industry_group
            peer_n = industry_n

            # Cap-matched refinement for oversized industries (e.g. Packaged Software).
            if (
                industry_n >= peer_mcap_refine_min_industry_n
                and market_cap is not None
                and market_cap > 0
                and peer_mcap_band_high > peer_mcap_band_low > 0
            ):
                band_rows = _mcap_band_peers(
                    industry_rows,
                    market_cap=market_cap,
                    band_low=peer_mcap_band_low,
                    band_high=peer_mcap_band_high,
                )
                band_stats = _build_stats(band_rows, trim_fraction=peer_trim_fraction)
                band_n = int(
                    band_stats.get("enterprise_value_to_revenue_ttm", {}).get("count", 0)
                )
                if band_n >= min_peer_group_size:
                    peer_scope = "industry_mcap"
                    peer_stats = band_stats
                    peer_n = band_n
        elif sector_n >= min_peer_group_size:
            peer_scope = "sector"
            peer_stats = sector_group
            peer_n = sector_n

        ev_rev_peer = _safe_float(
            peer_stats.get("enterprise_value_to_revenue_ttm", {}).get("median")
        )
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

        company_ev_rev = _safe_float(row.get("enterprise_value_to_revenue_ttm"))
        company_growth = _safe_float(row.get("total_revenue_yoy_growth_ttm"))
        company_cagr = _safe_float(row.get("total_revenue_cagr_5y"))
        if company_growth is None:
            company_growth = company_cagr

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
            and rev_growth_peer is not None
            and abs(rev_growth_peer) > 1e-9
        ):
            growth_rel = _clip(
                company_growth / rev_growth_peer,
                relative_scale_clip_low,
                relative_scale_clip_high,
            )

        cagr_rel = None
        if (
            company_cagr is not None
            and rev_cagr_peer is not None
            and abs(rev_cagr_peer) > 1e-9
        ):
            cagr_rel = _clip(
                company_cagr / rev_cagr_peer,
                relative_scale_clip_low,
                relative_scale_clip_high,
            )

        out[symbol] = {
            "peer_scope": peer_scope,
            "peer_n": peer_n,
            "sector": sector,
            "industry": industry,
            "ev_rev_peer_median": ev_rev_peer,
            "ev_ebitda_peer_median": ev_ebitda_peer,
            "rev_growth_peer_median": rev_growth_peer,
            "rev_cagr_peer_median": rev_cagr_peer,
            "ev_rev_relative_scale": ev_rev_rel,
            "rev_growth_relative_scale": growth_rel,
            "rev_cagr_relative_scale": cagr_rel,
            "peer_metric_medians": {
                field: peer_stats.get(field, {}).get("median")
                for field in PEER_METRIC_FIELDS
                if field in peer_stats
            },
        }
    return out
