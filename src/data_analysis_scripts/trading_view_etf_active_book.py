"""Active-management ETF book on top of the v1 global composite.

Peer-relative lenses, regime tape from canonical baskets, sleeve heat,
flow/price divergences, catch-up vs extended, vehicle quality, day-over-day
diffs, and a holdings-sleeve overlay. Stock fundamental fields are not used.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import re
from pathlib import Path
from statistics import median
from typing import Any, Callable, Mapping, Sequence

from data_analysis_scripts._shared_analysis_utils import (
    build_report_title,
    coerce_numeric,
    format_market_cap,
    format_number,
    format_signed_percent,
    median_absolute_deviation,
    reset_log_file,
)
from generic_utils.log_to_files_util import log_rows_to_csv, log_to_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = (
    PROJECT_ROOT / "config" / "etf_analysis" / "active_manager_v1.json"
)
DEFAULT_HOLDINGS_PATH = (
    PROJECT_ROOT / "config" / "holdings_scoring" / "current_holdings.json"
)

LENS_NAMES = (
    "sleeve_continuation",
    "flow_confirmed",
    "early_rotation",
    "catch_up",
    "vehicle_quality",
    "macro_hedge",
    "crowded",
    "dead_product",
)

PEER_LEVELS: tuple[tuple[str, ...], ...] = (
    ("product_class", "asset_class", "category", "focus", "niche"),
    ("product_class", "asset_class", "category", "focus"),
    ("product_class", "asset_class", "category"),
    ("product_class", "asset_class"),
    ("product_class",),
)

_INVERSE_RE = re.compile(
    r"\binverse\b|\bbear\b|-\s*[123](?:\.5)?x\b",
    re.IGNORECASE,
)
_LEVERED_RE = re.compile(
    r"\bleverag|\b[123](?:\.5)?x\b|\bultrapro\b|\bultra\s*(pro|bull|bear)|\bdaily\s+(bull|bear)",
    re.IGNORECASE,
)
_SHORT_TERM_RE = re.compile(
    r"short[\s-]*(term|duration|maturity|bond)",
    re.IGNORECASE,
)
_MIN_VOL_RE = re.compile(
    r"min(?:imum)?\s*vol|low\s*vol|usmv|defensive",
    re.IGNORECASE,
)

_DEFAULT_LENS_WEIGHTS = {
    "sleeve_continuation": 0.18,
    "flow_confirmed": 0.18,
    "early_rotation": 0.14,
    "catch_up": 0.12,
    "vehicle_quality": 0.12,
    "macro_hedge": 0.08,
    "crowded": 0.10,
    "dead_product": 0.08,
}


def load_etf_active_book_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return {
            "schema_version": "etf_active_book_v1",
            "suite_id": "etf_active_manager_v1",
            "suite_version": "1.0.0",
            "min_peer_group_size": 8,
            "min_sleeve_size": 3,
            "catch_up_per_sleeve": 8,
            "extended_per_sleeve": 5,
            "top_sleeves_by_tape": 15,
            "vehicle_quality_per_sleeve": 3,
            "lens_leaders_per_lens": 15,
            "lens_weights": dict(_DEFAULT_LENS_WEIGHTS),
            "inverted_lenses": ["crowded", "dead_product"],
            "hedge_asset_classes": [
                "Fixed Income",
                "Commodities",
                "Currency",
                "Alternative",
                "Alternatives",
            ],
            "canonical_baskets": [],
            "holdings_sleeve_map": [],
            "source_path": None,
        }
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["source_path"] = str(config_path)
    payload.setdefault("lens_weights", dict(_DEFAULT_LENS_WEIGHTS))
    payload.setdefault("inverted_lenses", ["crowded", "dead_product"])
    payload.setdefault("hedge_asset_classes", [])
    payload.setdefault("canonical_baskets", [])
    payload.setdefault("holdings_sleeve_map", [])
    payload.setdefault("min_peer_group_size", 8)
    payload.setdefault("min_sleeve_size", 3)
    return payload


def load_holdings_tickers(path: str | Path | None = None) -> list[str]:
    holdings_path = Path(path) if path is not None else DEFAULT_HOLDINGS_PATH
    if not holdings_path.exists():
        return []
    payload = json.loads(holdings_path.read_text(encoding="utf-8"))
    tickers: list[str] = []
    for holding in payload.get("holdings") or []:
        ticker = str(holding.get("ticker") or "").strip().upper()
        if ticker:
            tickers.append(ticker)
    return tickers


def _bare_ticker(symbol: str, name: str = "") -> str:
    text = str(symbol or "").strip()
    if ":" in text:
        return text.split(":", 1)[1].upper()
    return str(name or text or "").strip().upper()


def _join_text(*parts: Any) -> str:
    chunks: list[str] = []
    for part in parts:
        if part in (None, ""):
            continue
        if isinstance(part, (list, tuple)):
            chunks.append(" ".join(str(item) for item in part if item not in (None, "")))
            continue
        chunks.append(str(part))
    return " ".join(chunks)


def classify_product_class(row: Mapping[str, Any], *, name: str = "", description: str = "") -> str:
    niche = str(row.get("niche.tr") or row.get("niche") or "")
    category = str(row.get("category.tr") or row.get("category") or "")
    focus = str(row.get("focus.tr") or row.get("focus") or "")
    blob = _join_text(
        name,
        description,
        niche,
        category,
        focus,
        row.get("typespecs"),
    )
    taxonomy = _join_text(niche, category, focus)
    name_blob = _join_text(name, description)
    if _SHORT_TERM_RE.search(blob):
        cleaned = _SHORT_TERM_RE.sub(" ", blob)
    else:
        cleaned = blob
    inverse = bool(_INVERSE_RE.search(taxonomy) or _INVERSE_RE.search(cleaned))
    if not inverse and re.search(r"\bshort\b", name_blob, re.IGNORECASE):
        if not _SHORT_TERM_RE.search(name_blob):
            inverse = True
    levered = bool(_LEVERED_RE.search(taxonomy) or _LEVERED_RE.search(cleaned))
    if inverse and levered:
        return "inverse_levered"
    if inverse:
        return "inverse"
    if levered:
        return "levered"
    return "1x"


def _price_vs_ma(close: float | None, moving_average: float | None) -> float | None:
    if close is None or moving_average is None or moving_average == 0:
        return None
    return (close / moving_average) - 1.0


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _subtract(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _pct_from_high(close: float | None, high: float | None) -> float | None:
    if close is None or high is None or high == 0:
        return None
    return (close / high) - 1.0


def _log_aum(aum: float | None) -> float | None:
    if aum is None or aum <= 0:
        return None
    return math.log10(aum)


def _mean_ignore_none(values: Sequence[float | None]) -> float | None:
    finite = [value for value in values if value is not None]
    if not finite:
        return None
    return sum(finite) / len(finite)


def _robust_z_map(values: list[float | None], *, min_n: int = 3) -> list[float | None]:
    finite = [value for value in values if value is not None and math.isfinite(value)]
    if len(finite) < min_n:
        return [None for _ in values]
    center = median(finite)
    mad = median_absolute_deviation(finite, center)
    scale = 1.4826 * mad
    if scale == 0:
        return [0.0 if value is not None else None for value in values]
    mapped: list[float | None] = []
    for value in values:
        if value is None or not math.isfinite(value):
            mapped.append(None)
            continue
        z_score = (value - center) / scale
        mapped.append(max(-3.0, min(3.0, z_score)))
    return mapped


def _z_to_score(z_score: float | None) -> float | None:
    if z_score is None:
        return None
    return 50.0 + z_score * (50.0 / 3.0)


def _direction_from_score(score: float | None) -> str:
    if score is None:
        return "N/A"
    if score >= 65:
        return "Up"
    if score >= 55:
        return "Mild Up"
    if score <= 35:
        return "Down"
    if score <= 45:
        return "Mild Down"
    return "Neutral"


def _format_score(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:5.1f}"


def _percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = q * (len(ordered) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _is_hedge_sleeve(item: Mapping[str, Any], hedge_asset_classes: Sequence[str]) -> bool:
    asset_class = str(item.get("asset_class") or "")
    for label in hedge_asset_classes:
        if label.lower() in asset_class.lower():
            return True
    blob = _join_text(item.get("focus"), item.get("niche"), item.get("category"), item.get("description"))
    return bool(_MIN_VOL_RE.search(blob) or re.search(r"\b(treasury|gold|usd|dollar)\b", blob, re.IGNORECASE))


def derive_etf_metrics(item: dict[str, Any]) -> dict[str, Any]:
    row = item.get("row") or {}
    close = coerce_numeric(row.get("close"))
    aum = coerce_numeric(row.get("aum"))
    product_class = classify_product_class(
        row,
        name=str(item.get("name") or ""),
        description=str(item.get("description") or ""),
    )
    flow_1m = coerce_numeric(row.get("fund_flows.1M"))
    flow_3m = coerce_numeric(row.get("fund_flows.3M"))
    nav_tr_1m = coerce_numeric(row.get("nav_total_return.1M"))
    nav_tr_3m = coerce_numeric(row.get("nav_total_return.3M"))
    nav_perf_1m = coerce_numeric(row.get("nav_perf.1M"))
    nav_perf_3m = coerce_numeric(row.get("nav_perf.3M"))
    basket_1m = nav_tr_1m if nav_tr_1m is not None else nav_perf_1m
    basket_3m = nav_tr_3m if nav_tr_3m is not None else nav_perf_3m
    perf_1m = coerce_numeric(row.get("Perf.1M"))
    perf_5d = coerce_numeric(row.get("Perf.5D"))
    aum_perf_1m = coerce_numeric(row.get("aum_perf.1M"))
    aum_perf_3m = coerce_numeric(row.get("aum_perf.3M"))
    dollar_volume = coerce_numeric(row.get("Value.Traded"))
    if dollar_volume is None:
        dollar_volume = coerce_numeric(row.get("AvgValue.Traded_10d"))
    macd = coerce_numeric(row.get("MACD.macd"))
    macd_signal = coerce_numeric(row.get("MACD.signal"))
    return {
        "ticker": _bare_ticker(str(item.get("symbol") or ""), str(item.get("name") or "")),
        "product_class": product_class,
        "niche": str(row.get("niche.tr") or row.get("niche") or item.get("niche") or ""),
        "holdings_region": str(row.get("holdings_region") or ""),
        "flow_to_aum_1m": _ratio(flow_1m, aum),
        "flow_to_aum_3m": _ratio(flow_3m, aum),
        "organic_demand_1m": _subtract(aum_perf_1m, basket_1m if basket_1m is not None else nav_perf_1m),
        "organic_demand_3m": _subtract(aum_perf_3m, basket_3m if basket_3m is not None else nav_perf_3m),
        "tracking_gap_1m": _subtract(perf_1m, basket_1m),
        "tracking_gap_5d": _subtract(perf_5d, basket_1m),
        "nav_total_return_1m": basket_1m,
        "nav_total_return_3m": basket_3m,
        "close_vs_sma20": _price_vs_ma(close, coerce_numeric(row.get("SMA20"))),
        "close_vs_sma50": _price_vs_ma(close, coerce_numeric(row.get("SMA50"))),
        "close_vs_sma200": _price_vs_ma(close, coerce_numeric(row.get("SMA200"))),
        "pct_from_high_52w": _pct_from_high(close, coerce_numeric(row.get("price_52_week_high"))),
        "pct_from_high_1m": _pct_from_high(close, coerce_numeric(row.get("High.1M"))),
        "adx": coerce_numeric(row.get("ADX")),
        "macd_spread": _subtract(macd, macd_signal),
        "dollar_liquidity": dollar_volume,
        "recommend_week": coerce_numeric(row.get("Recommend.All|1W")),
        "recommend_month": coerce_numeric(row.get("Recommend.MA|1M")),
        "rsi": coerce_numeric(row.get("RSI")),
        "cmf": coerce_numeric(row.get("ChaikinMoneyFlow")),
        "rel_vol": coerce_numeric(row.get("relative_volume_10d_calc")),
        "aum_log": _log_aum(aum),
        "expense_ratio": coerce_numeric(row.get("expense_ratio")),
        "nav_discount_premium": coerce_numeric(row.get("nav_discount_premium")),
        "perf_5d": perf_5d,
        "perf_1m": perf_1m,
        "perf_3m": coerce_numeric(row.get("Perf.3M")),
    }


def _group_key(item: Mapping[str, Any], fields: Sequence[str]) -> str:
    parts: list[str] = []
    for field in fields:
        value = str(item.get(field) or "").strip() or "Unclassified"
        parts.append(value)
    return " | ".join(parts)


def assign_peer_groups(
    items: Sequence[dict[str, Any]],
    *,
    min_peer_group_size: int,
) -> list[dict[str, Any]]:
    counts_by_level: list[dict[str, int]] = []
    for fields in PEER_LEVELS:
        counts: dict[str, int] = defaultdict(int)
        for item in items:
            counts[_group_key(item, fields)] += 1
        counts_by_level.append(dict(counts))

    assigned: list[dict[str, Any]] = []
    for item in items:
        peer_group = "_universe"
        peer_level = "universe"
        for level_index, fields in enumerate(PEER_LEVELS):
            key = _group_key(item, fields)
            if counts_by_level[level_index].get(key, 0) >= min_peer_group_size:
                peer_group = key
                peer_level = "+".join(fields)
                break
        sleeve_key = _group_key(
            item, ("product_class", "asset_class", "category", "focus")
        )
        enriched = dict(item)
        enriched["peer_group"] = peer_group
        enriched["peer_level"] = peer_level
        enriched["sleeve_key"] = sleeve_key
        assigned.append(enriched)
    return assigned


def _peer_scores(
    items: Sequence[dict[str, Any]],
    getter: Callable[[dict[str, Any]], float | None],
    *,
    invert: bool = False,
) -> list[float | None]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        grouped[str(item.get("peer_group") or "_universe")].append(index)
    scores: list[float | None] = [None] * len(items)
    for indices in grouped.values():
        raw: list[float | None] = []
        for index in indices:
            value = getter(items[index])
            if invert and value is not None:
                value = -value
            raw.append(value)
        z_scores = _robust_z_map(raw, min_n=3)
        for local_index, item_index in enumerate(indices):
            scores[item_index] = _z_to_score(z_scores[local_index])
    return scores


def _combine_lens(
    field_scores: Sequence[list[float | None]],
    row_count: int,
) -> list[float | None]:
    combined: list[float | None] = []
    for index in range(row_count):
        combined.append(_mean_ignore_none([field[index] for field in field_scores]))
    return combined


def score_etf_lenses(
    items: Sequence[dict[str, Any]],
    *,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    hedge_asset_classes = list(config.get("hedge_asset_classes") or [])
    peer_perf_5d = _peer_scores(items, lambda item: item.get("perf_5d"))
    peer_perf_1m = _peer_scores(items, lambda item: item.get("perf_1m"))
    peer_nav_1m = _peer_scores(items, lambda item: item.get("nav_total_return_1m"))
    peer_nav_3m = _peer_scores(items, lambda item: item.get("nav_total_return_3m"))
    peer_flow_1m = _peer_scores(items, lambda item: item.get("flow_to_aum_1m"))
    peer_flow_3m = _peer_scores(items, lambda item: item.get("flow_to_aum_3m"))
    peer_organic_1m = _peer_scores(items, lambda item: item.get("organic_demand_1m"))
    peer_organic_3m = _peer_scores(items, lambda item: item.get("organic_demand_3m"))
    peer_sma50 = _peer_scores(items, lambda item: item.get("close_vs_sma50"))
    peer_sma200 = _peer_scores(items, lambda item: item.get("close_vs_sma200"))
    peer_adx = _peer_scores(items, lambda item: item.get("adx"))
    peer_recommend_w = _peer_scores(items, lambda item: item.get("recommend_week"))
    peer_cmf = _peer_scores(items, lambda item: item.get("cmf"))
    peer_rel_vol = _peer_scores(items, lambda item: item.get("rel_vol"))
    peer_rsi = _peer_scores(items, lambda item: item.get("rsi"))
    peer_off_high = _peer_scores(items, lambda item: item.get("pct_from_high_52w"), invert=True)
    peer_near_high = _peer_scores(items, lambda item: item.get("pct_from_high_52w"))
    peer_aum = _peer_scores(items, lambda item: item.get("aum_log"))
    peer_liquidity = _peer_scores(items, lambda item: item.get("dollar_liquidity"))
    peer_fee = _peer_scores(items, lambda item: item.get("expense_ratio"), invert=True)
    peer_nav_tight = _peer_scores(
        items,
        lambda item: abs(item["nav_discount_premium"])
        if item.get("nav_discount_premium") is not None
        else None,
        invert=True,
    )
    peer_premium = _peer_scores(items, lambda item: item.get("nav_discount_premium"))

    continuation = _combine_lens(
        [peer_nav_1m, peer_nav_3m, peer_sma50, peer_sma200, peer_adx, peer_recommend_w, peer_flow_1m],
        len(items),
    )
    flow_confirmed = _combine_lens(
        [peer_organic_1m, peer_organic_3m, peer_cmf, peer_rel_vol, peer_flow_1m],
        len(items),
    )
    early_rotation = _combine_lens(
        [peer_perf_5d, peer_flow_1m, peer_organic_1m, peer_off_high],
        len(items),
    )
    catch_up = _combine_lens(
        [continuation, flow_confirmed, peer_off_high],
        len(items),
    )
    vehicle_quality = _combine_lens(
        [peer_aum, peer_liquidity, peer_fee, peer_nav_tight],
        len(items),
    )
    crowded = _combine_lens(
        [peer_rsi, peer_near_high, peer_flow_1m, peer_premium, peer_sma50],
        len(items),
    )
    dead_product = _combine_lens(
        [
            _peer_scores(items, lambda item: item.get("flow_to_aum_1m"), invert=True),
            _peer_scores(items, lambda item: item.get("organic_demand_1m"), invert=True),
            _peer_scores(items, lambda item: item.get("dollar_liquidity"), invert=True),
            _peer_scores(
                items,
                lambda item: abs(item["nav_discount_premium"])
                if item.get("nav_discount_premium") is not None
                else None,
            ),
        ],
        len(items),
    )

    lens_weights = dict(config.get("lens_weights") or _DEFAULT_LENS_WEIGHTS)
    inverted = {str(name) for name in (config.get("inverted_lenses") or [])}
    scored: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        hedge_score = None
        if item.get("product_class") == "1x" and _is_hedge_sleeve(item, hedge_asset_classes):
            hedge_score = _mean_ignore_none([continuation[index], flow_confirmed[index]])
        vehicle_score = vehicle_quality[index]
        if item.get("product_class") != "1x":
            vehicle_score = None
        lenses = {
            "sleeve_continuation": continuation[index],
            "flow_confirmed": flow_confirmed[index],
            "early_rotation": early_rotation[index],
            "catch_up": catch_up[index],
            "vehicle_quality": vehicle_score,
            "macro_hedge": hedge_score,
            "crowded": crowded[index],
            "dead_product": dead_product[index],
        }
        weighted_total = 0.0
        weight_sum = 0.0
        for lens_name, weight in lens_weights.items():
            value = lenses.get(lens_name)
            if value is None:
                continue
            if lens_name in inverted:
                value = 100.0 - value
            weighted_total += value * float(weight)
            weight_sum += float(weight)
        consensus = weighted_total / weight_sum if weight_sum else None
        enriched = dict(item)
        enriched.update(lenses)
        enriched["book_consensus"] = consensus
        enriched["book_direction"] = _direction_from_score(consensus)
        scored.append(enriched)
    return scored


def _match_aliases(
    items: Sequence[dict[str, Any]],
    aliases: Sequence[str],
) -> list[dict[str, Any]]:
    wanted = {str(alias).strip().upper() for alias in aliases if str(alias).strip()}
    matches = [item for item in items if str(item.get("ticker") or "").upper() in wanted]
    matches.sort(
        key=lambda item: (
            0 if str(item.get("market") or "").lower() in {"america", "united states"} else 1,
            item.get("aum") if item.get("aum") is not None else float("-inf"),
        )
    )
    return matches


def _best_alias_match(
    items: Sequence[dict[str, Any]],
    aliases: Sequence[str],
) -> dict[str, Any] | None:
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for alias in aliases:
        for item in _match_aliases(items, [alias]):
            ticker = str(item.get("ticker") or "")
            if ticker in seen:
                continue
            seen.add(ticker)
            ordered.append(item)
    if not ordered:
        return None
    return max(ordered, key=lambda item: item.get("aum") if item.get("aum") is not None else float("-inf"))


def build_regime_tape(
    items: Sequence[dict[str, Any]],
    *,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    row_number = 0
    for basket in config.get("canonical_baskets") or []:
        aliases = list(basket.get("aliases") or [])
        matches = _match_aliases(items, aliases)
        if not matches:
            row_number += 1
            records.append(
                {
                    "row_number": row_number,
                    "sleeve": basket.get("sleeve"),
                    "question": basket.get("question"),
                    "symbol": "",
                    "ticker": "",
                    "description": "not in scan universe",
                    "aum": None,
                    "perf_5d": None,
                    "perf_1m": None,
                    "flow_to_aum_1m": None,
                    "organic_demand_1m": None,
                    "book_consensus": None,
                    "product_class": "",
                }
            )
            continue
        seen: set[str] = set()
        for alias in aliases:
            alias_matches = [
                item for item in matches if str(item.get("ticker") or "").upper() == alias.upper()
            ]
            if not alias_matches:
                continue
            pick = alias_matches[0]
            ticker = str(pick.get("ticker") or "")
            if ticker in seen:
                continue
            seen.add(ticker)
            row_number += 1
            records.append(
                {
                    "row_number": row_number,
                    "sleeve": basket.get("sleeve"),
                    "question": basket.get("question"),
                    "symbol": pick.get("symbol"),
                    "ticker": ticker,
                    "description": pick.get("description"),
                    "aum": pick.get("aum"),
                    "perf_5d": pick.get("perf_5d"),
                    "perf_1m": pick.get("perf_1m"),
                    "flow_to_aum_1m": pick.get("flow_to_aum_1m"),
                    "organic_demand_1m": pick.get("organic_demand_1m"),
                    "book_consensus": pick.get("book_consensus"),
                    "product_class": pick.get("product_class"),
                }
            )
    return records


def build_sleeve_heat(
    items: Sequence[dict[str, Any]],
    *,
    min_sleeve_size: int,
    top_n: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item.get("product_class") != "1x":
            continue
        grouped[str(item.get("sleeve_key") or "Unclassified")].append(item)
    records: list[dict[str, Any]] = []
    for sleeve_key, group in grouped.items():
        if len(group) < min_sleeve_size:
            continue
        perf_values = [item["perf_1m"] for item in group if item.get("perf_1m") is not None]
        perf_5d_values = [item["perf_5d"] for item in group if item.get("perf_5d") is not None]
        flow_values = [
            item["flow_to_aum_1m"] for item in group if item.get("flow_to_aum_1m") is not None
        ]
        organic_values = [
            item["organic_demand_1m"]
            for item in group
            if item.get("organic_demand_1m") is not None
        ]
        consensus_values = [
            item["book_consensus"] for item in group if item.get("book_consensus") is not None
        ]
        best = max(
            group,
            key=lambda item: item["perf_1m"] if item.get("perf_1m") is not None else float("-inf"),
        )
        records.append(
            {
                "sleeve_key": sleeve_key,
                "row_count": len(group),
                "median_perf_5d": median(perf_5d_values) if perf_5d_values else None,
                "median_perf_1m": median(perf_values) if perf_values else None,
                "median_flow_to_aum_1m": median(flow_values) if flow_values else None,
                "median_organic_demand_1m": median(organic_values) if organic_values else None,
                "median_book_consensus": median(consensus_values) if consensus_values else None,
                "best_perf_symbol": best.get("symbol"),
                "best_perf_1m": best.get("perf_1m"),
            }
        )
    records.sort(
        key=lambda record: (
            record["median_perf_1m"]
            if record["median_perf_1m"] is not None
            else float("-inf")
        ),
        reverse=True,
    )
    for rank, record in enumerate(records, start=1):
        record["rank"] = rank
    return records[:top_n]


def build_divergences(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item.get("product_class") != "1x":
            continue
        grouped[str(item.get("peer_group") or "_universe")].append(item)
    records: list[dict[str, Any]] = []
    for peer_group, group in grouped.items():
        perf_values = [item["perf_1m"] for item in group if item.get("perf_1m") is not None]
        if len(perf_values) < 3:
            continue
        median_perf = median(perf_values)
        for item in group:
            perf = item.get("perf_1m")
            flow = item.get("flow_to_aum_1m")
            premium = item.get("nav_discount_premium")
            flags: list[str] = []
            if perf is not None and flow is not None:
                if perf > median_perf and flow < 0:
                    flags.append("price_up_flow_out")
                if perf < median_perf and flow > 0:
                    flags.append("price_down_flow_in")
            if premium is not None and premium >= 0.5:
                flags.append("rich_nav_premium")
            if premium is not None and premium <= -0.5:
                flags.append("nav_discount")
            if not flags:
                continue
            records.append(
                {
                    "symbol": item.get("symbol"),
                    "description": item.get("description"),
                    "peer_group": peer_group,
                    "flags": ", ".join(flags),
                    "perf_1m": perf,
                    "flow_to_aum_1m": flow,
                    "nav_discount_premium": premium,
                    "book_consensus": item.get("book_consensus"),
                }
            )
    records.sort(
        key=lambda record: (
            record["book_consensus"]
            if record["book_consensus"] is not None
            else float("-inf")
        ),
        reverse=True,
    )
    return records


def build_catch_up_vs_extended(
    items: Sequence[dict[str, Any]],
    *,
    min_sleeve_size: int,
    catch_up_per_sleeve: int,
    extended_per_sleeve: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item.get("product_class") != "1x":
            continue
        grouped[str(item.get("sleeve_key") or "Unclassified")].append(item)
    records: list[dict[str, Any]] = []
    for sleeve_key, group in grouped.items():
        if len(group) < min_sleeve_size:
            continue
        scores = [item["book_consensus"] for item in group if item.get("book_consensus") is not None]
        perfs = [item["perf_1m"] for item in group if item.get("perf_1m") is not None]
        if not scores or not perfs:
            continue
        median_score = median(scores)
        median_perf = median(perfs)
        high_perf = _percentile(perfs, 0.75)
        catch_up: list[dict[str, Any]] = []
        for item in group:
            score = item.get("book_consensus")
            perf = item.get("perf_1m")
            if score is None or perf is None:
                continue
            if score >= median_score and perf < median_perf:
                catch_up.append(item)
        catch_up.sort(
            key=lambda item: (
                (item["book_consensus"] - median_score) + (median_perf - item["perf_1m"])
            ),
            reverse=True,
        )
        extended: list[dict[str, Any]] = []
        for item in group:
            perf = item.get("perf_1m")
            rsi = item.get("rsi")
            off_high = item.get("pct_from_high_52w")
            if perf is None:
                continue
            near_high = off_high is not None and off_high >= -0.03
            hot_rsi = rsi is not None and rsi >= 70
            hot_perf = high_perf is not None and perf >= high_perf
            if hot_perf or near_high or hot_rsi:
                extended.append(item)
        extended.sort(
            key=lambda item: item["perf_1m"] if item.get("perf_1m") is not None else float("-inf"),
            reverse=True,
        )
        for item in catch_up[:catch_up_per_sleeve]:
            records.append(
                {
                    "sleeve_key": sleeve_key,
                    "role": "catch_up",
                    "symbol": item.get("symbol"),
                    "description": item.get("description"),
                    "perf_1m": item.get("perf_1m"),
                    "book_consensus": item.get("book_consensus"),
                    "flow_to_aum_1m": item.get("flow_to_aum_1m"),
                    "pct_from_high_52w": item.get("pct_from_high_52w"),
                    "expense_ratio": item.get("expense_ratio"),
                }
            )
        for item in extended[:extended_per_sleeve]:
            records.append(
                {
                    "sleeve_key": sleeve_key,
                    "role": "extended",
                    "symbol": item.get("symbol"),
                    "description": item.get("description"),
                    "perf_1m": item.get("perf_1m"),
                    "book_consensus": item.get("book_consensus"),
                    "flow_to_aum_1m": item.get("flow_to_aum_1m"),
                    "pct_from_high_52w": item.get("pct_from_high_52w"),
                    "expense_ratio": item.get("expense_ratio"),
                }
            )
    return records


def build_vehicle_quality(
    items: Sequence[dict[str, Any]],
    *,
    per_sleeve: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item.get("product_class") != "1x":
            continue
        grouped[str(item.get("sleeve_key") or "Unclassified")].append(item)
    records: list[dict[str, Any]] = []
    for sleeve_key, group in grouped.items():
        ranked = sorted(
            group,
            key=lambda item: (
                item["vehicle_quality"]
                if item.get("vehicle_quality") is not None
                else float("-inf"),
                item.get("aum") if item.get("aum") is not None else float("-inf"),
            ),
            reverse=True,
        )
        for sleeve_rank, item in enumerate(ranked[:per_sleeve], start=1):
            records.append(
                {
                    "sleeve_key": sleeve_key,
                    "sleeve_rank": sleeve_rank,
                    "symbol": item.get("symbol"),
                    "description": item.get("description"),
                    "brand": item.get("brand"),
                    "aum": item.get("aum"),
                    "expense_ratio": item.get("expense_ratio"),
                    "nav_discount_premium": item.get("nav_discount_premium"),
                    "dollar_liquidity": item.get("dollar_liquidity"),
                    "vehicle_quality": item.get("vehicle_quality"),
                }
            )
    return records


def build_lens_leaders(
    items: Sequence[dict[str, Any]],
    *,
    per_lens: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    one_x = [item for item in items if item.get("product_class") == "1x"]
    for lens_name in LENS_NAMES:
        ranked = sorted(
            one_x,
            key=lambda item, field=lens_name: (
                item[field] if item.get(field) is not None else float("-inf")
            ),
            reverse=True,
        )
        for rank, item in enumerate(ranked[:per_lens], start=1):
            if item.get(lens_name) is None:
                continue
            records.append(
                {
                    "lens": lens_name,
                    "lens_rank": rank,
                    "symbol": item.get("symbol"),
                    "description": item.get("description"),
                    "sleeve_key": item.get("sleeve_key"),
                    "score": item.get(lens_name),
                    "book_consensus": item.get("book_consensus"),
                    "perf_1m": item.get("perf_1m"),
                }
            )
    return records


def build_holdings_overlay(
    items: Sequence[dict[str, Any]],
    *,
    config: Mapping[str, Any],
    holding_tickers: Sequence[str],
) -> list[dict[str, Any]]:
    held = {ticker.upper() for ticker in holding_tickers}
    records: list[dict[str, Any]] = []
    for mapping in config.get("holdings_sleeve_map") or []:
        mapped_holdings = [
            ticker
            for ticker in (mapping.get("holding_tickers") or [])
            if str(ticker).upper() in held
        ]
        pick = _best_alias_match(items, mapping.get("aliases") or [])
        records.append(
            {
                "overlay_id": mapping.get("id"),
                "note": mapping.get("note"),
                "held_tickers": ", ".join(mapped_holdings) if mapped_holdings else "",
                "thermometer_symbol": pick.get("symbol") if pick else "",
                "thermometer_ticker": pick.get("ticker") if pick else "",
                "description": pick.get("description") if pick else "not in scan universe",
                "perf_5d": pick.get("perf_5d") if pick else None,
                "perf_1m": pick.get("perf_1m") if pick else None,
                "flow_to_aum_1m": pick.get("flow_to_aum_1m") if pick else None,
                "book_consensus": pick.get("book_consensus") if pick else None,
                "book_direction": pick.get("book_direction") if pick else "",
            }
        )
    return records


def build_day_over_day(
    items: Sequence[dict[str, Any]],
    prior_rows: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not prior_rows:
        return []
    prior_by_symbol = {
        str(row.get("symbol") or ""): row
        for row in prior_rows
        if row.get("symbol")
    }
    records: list[dict[str, Any]] = []
    for item in items:
        symbol = str(item.get("symbol") or "")
        prior = prior_by_symbol.get(symbol)
        if prior is None:
            continue
        consensus_delta = _subtract(item.get("book_consensus"), coerce_numeric(prior.get("book_consensus")))
        flow_delta = _subtract(item.get("flow_to_aum_1m"), coerce_numeric(prior.get("flow_to_aum_1m")))
        perf_delta = _subtract(item.get("perf_1m"), coerce_numeric(prior.get("perf_1m")))
        if consensus_delta is None and flow_delta is None and perf_delta is None:
            continue
        notable = False
        if consensus_delta is not None and abs(consensus_delta) >= 5:
            notable = True
        if flow_delta is not None and abs(flow_delta) >= 0.005:
            notable = True
        if not notable:
            continue
        records.append(
            {
                "symbol": symbol,
                "description": item.get("description"),
                "consensus_delta": consensus_delta,
                "flow_to_aum_1m_delta": flow_delta,
                "perf_1m_delta": perf_delta,
                "book_consensus": item.get("book_consensus"),
                "prior_consensus": coerce_numeric(prior.get("book_consensus")),
            }
        )
    records.sort(
        key=lambda record: abs(record["consensus_delta"] or 0.0)
        + 100.0 * abs(record["flow_to_aum_1m_delta"] or 0.0),
        reverse=True,
    )
    return records[:80]


def _serialize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.8f}".rstrip("0").rstrip(".")
    return str(value)


def _write_regime_log(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    reset_log_file(path)
    log_to_file(path, build_report_title("ETF regime tape (canonical baskets)"))
    log_to_file(path, "-" * 160)
    log_to_file(
        path,
        "Research thermometers from the same scan. 1x primary listing, largest AUM per ticker.",
    )
    current_sleeve = None
    for record in records:
        if record.get("sleeve") != current_sleeve:
            current_sleeve = record.get("sleeve")
            log_to_file(path, "")
            log_to_file(path, f"{current_sleeve} — {record.get('question')}")
        symbol = str(record.get("symbol") or record.get("ticker") or "N/A")
        log_to_file(
            path,
            f"  {symbol:<16} {format_signed_percent(record.get('perf_5d')):>8} 5D  "
            f"{format_signed_percent(record.get('perf_1m')):>8} 1M  "
            f"flow/AUM {format_number(record.get('flow_to_aum_1m'), 4):>8}  "
            f"{record.get('description')}",
        )


def _write_sleeve_heat_log(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    reset_log_file(path)
    log_to_file(path, build_report_title("ETF sleeve heat (1x, peer-relative tape)"))
    log_to_file(path, "-" * 160)
    header = (
        f"{'Rk':>3}  {'n':>4}  {'1M':>8}  {'5D':>8}  {'Flow/AUM':>10}  "
        f"{'Org1M':>8}  {'Book':>5}  Sleeve"
    )
    log_to_file(path, header)
    for record in records:
        log_to_file(
            path,
            f"{int(record.get('rank') or 0):3d}  {int(record.get('row_count') or 0):4d}  "
            f"{format_signed_percent(record.get('median_perf_1m')):>8}  "
            f"{format_signed_percent(record.get('median_perf_5d')):>8}  "
            f"{format_number(record.get('median_flow_to_aum_1m'), 4):>10}  "
            f"{format_number(record.get('median_organic_demand_1m'), 2):>8}  "
            f"{_format_score(record.get('median_book_consensus'))}  "
            f"{record.get('sleeve_key')}",
        )


def _write_divergence_log(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    reset_log_file(path)
    log_to_file(path, build_report_title("ETF flow vs price divergences"))
    log_to_file(path, "-" * 160)
    if not records:
        log_to_file(path, "No 1x divergences with peer groups of 3+.")
        return
    for record in records[:80]:
        log_to_file(
            path,
            f"{record.get('symbol'):<16} {record.get('flags'):<28} "
            f"{format_signed_percent(record.get('perf_1m')):>8}  "
            f"flow/AUM {format_number(record.get('flow_to_aum_1m'), 4):>8}  "
            f"{record.get('description')}",
        )


def _write_catch_up_log(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    reset_log_file(path)
    log_to_file(path, build_report_title("ETF catch-up vs extended (1x sleeves)"))
    log_to_file(path, "-" * 160)
    current = None
    for record in records:
        sleeve = record.get("sleeve_key")
        if sleeve != current:
            current = sleeve
            log_to_file(path, "")
            log_to_file(path, str(current))
        log_to_file(
            path,
            f"  {str(record.get('role')):<10} {record.get('symbol'):<16} "
            f"book {_format_score(record.get('book_consensus'))}  "
            f"{format_signed_percent(record.get('perf_1m')):>8}  "
            f"{record.get('description')}",
        )
    if not records:
        log_to_file(path, "No 1x sleeves large enough for catch-up / extended.")


def _write_vehicle_log(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    reset_log_file(path)
    log_to_file(path, build_report_title("ETF vehicle quality (1x share class)"))
    log_to_file(path, "-" * 160)
    current = None
    for record in records:
        sleeve = record.get("sleeve_key")
        if sleeve != current:
            current = sleeve
            log_to_file(path, "")
            log_to_file(path, str(current))
        log_to_file(
            path,
            f"  {int(record.get('sleeve_rank') or 0):2d}. {record.get('symbol'):<16} "
            f"{_format_score(record.get('vehicle_quality'))}  "
            f"AUM {format_market_cap(record.get('aum')):>8}  "
            f"exp {format_number(record.get('expense_ratio'), 2):>6}  "
            f"{record.get('description')}",
        )


def _write_lens_log(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    reset_log_file(path)
    log_to_file(path, build_report_title("ETF lens leaders (1x)"))
    log_to_file(path, "-" * 160)
    current = None
    for record in records:
        lens = record.get("lens")
        if lens != current:
            current = lens
            log_to_file(path, "")
            log_to_file(path, str(current))
        log_to_file(
            path,
            f"  {int(record.get('lens_rank') or 0):2d}. {record.get('symbol'):<16} "
            f"{_format_score(record.get('score'))}  "
            f"{format_signed_percent(record.get('perf_1m')):>8}  "
            f"{record.get('description')}",
        )


def _write_holdings_log(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    reset_log_file(path)
    log_to_file(path, build_report_title("ETF holdings-sleeve overlay"))
    log_to_file(path, "-" * 160)
    if not records:
        log_to_file(path, "No holdings overlay mappings.")
        return
    for record in records:
        held = record.get("held_tickers") or "(none held)"
        log_to_file(path, "")
        log_to_file(path, f"{record.get('overlay_id')} — {record.get('note')}")
        log_to_file(path, f"  holdings: {held}")
        log_to_file(
            path,
            f"  tape: {record.get('thermometer_symbol') or 'N/A':<16} "
            f"{format_signed_percent(record.get('perf_5d')):>8} 5D  "
            f"{format_signed_percent(record.get('perf_1m')):>8} 1M  "
            f"{record.get('book_direction') or ''}  "
            f"{record.get('description')}",
        )


def _write_dod_log(path: Path, records: Sequence[Mapping[str, Any]], *, had_prior: bool) -> None:
    reset_log_file(path)
    log_to_file(path, build_report_title("ETF day-over-day changes"))
    log_to_file(path, "-" * 160)
    if not had_prior:
        log_to_file(path, "No prior run in this week DuckDB — first session in the file.")
        return
    if not records:
        log_to_file(path, "Prior run found; no notable consensus/flow flips.")
        return
    for record in records:
        log_to_file(
            path,
            f"{record.get('symbol'):<16} book {_format_score(record.get('book_consensus'))} "
            f"Δ {_format_score(record.get('consensus_delta'))}  "
            f"flow Δ {format_number(record.get('flow_to_aum_1m_delta'), 4):>8}  "
            f"{record.get('description')}",
        )


def _write_book_csv(path: Path, items: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "symbol",
        "ticker",
        "description",
        "product_class",
        "asset_class",
        "category",
        "focus",
        "niche",
        "peer_group",
        "sleeve_key",
        "aum",
        "expense_ratio",
        "nav_discount_premium",
        "perf_5d",
        "perf_1m",
        "flow_to_aum_1m",
        "organic_demand_1m",
        "tracking_gap_1m",
        "close_vs_sma50",
        "pct_from_high_52w",
        "composite_score",
        "book_consensus",
        "book_direction",
        *LENS_NAMES,
    ]
    rows = [[_serialize(item.get(header)) for header in headers] for item in items]
    log_rows_to_csv(path, headers, rows)


def build_book_records(items: Sequence[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    ranked = sorted(
        items,
        key=lambda item: (
            item["book_consensus"] is not None,
            item["book_consensus"] if item.get("book_consensus") is not None else float("-inf"),
        ),
        reverse=True,
    )
    for row_number, item in enumerate(ranked, start=1):
        records.append(
            {
                "run_id": run_id,
                "row_number": row_number,
                "symbol": item.get("symbol"),
                "ticker": item.get("ticker"),
                "description": item.get("description"),
                "product_class": item.get("product_class"),
                "asset_class": item.get("asset_class"),
                "category": item.get("category"),
                "focus": item.get("focus"),
                "niche": item.get("niche"),
                "peer_group": item.get("peer_group"),
                "peer_level": item.get("peer_level"),
                "sleeve_key": item.get("sleeve_key"),
                "aum": item.get("aum"),
                "expense_ratio": item.get("expense_ratio"),
                "nav_discount_premium": item.get("nav_discount_premium"),
                "perf_5d": item.get("perf_5d"),
                "perf_1m": item.get("perf_1m"),
                "flow_to_aum_1m": item.get("flow_to_aum_1m"),
                "organic_demand_1m": item.get("organic_demand_1m"),
                "tracking_gap_1m": item.get("tracking_gap_1m"),
                "close_vs_sma50": item.get("close_vs_sma50"),
                "pct_from_high_52w": item.get("pct_from_high_52w"),
                "composite_score": item.get("composite_score"),
                "book_consensus": item.get("book_consensus"),
                "book_direction": item.get("book_direction"),
                "sleeve_continuation": item.get("sleeve_continuation"),
                "flow_confirmed": item.get("flow_confirmed"),
                "early_rotation": item.get("early_rotation"),
                "catch_up": item.get("catch_up"),
                "vehicle_quality": item.get("vehicle_quality"),
                "macro_hedge": item.get("macro_hedge"),
                "crowded": item.get("crowded"),
                "dead_product": item.get("dead_product"),
            }
        )
    return records


def _attach_run_id(run_id: str, records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    attached: list[dict[str, Any]] = []
    for row_number, record in enumerate(records, start=1):
        payload = dict(record)
        payload.setdefault("run_id", run_id)
        payload.setdefault("row_number", row_number)
        attached.append(payload)
    return attached


def run_etf_active_book(
    scored_rows: list[dict[str, Any]],
    *,
    run_id: str,
    run_output_dir: Path,
    config_path: str | Path | None = None,
    holdings_config_path: str | Path | None = None,
    prior_book_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the daily ETF book from v1 scored rows and write run artifacts."""
    config = load_etf_active_book_config(config_path)
    holding_tickers = load_holdings_tickers(holdings_config_path)
    run_output_dir.mkdir(parents=True, exist_ok=True)

    enriched: list[dict[str, Any]] = []
    for item in scored_rows:
        payload = dict(item)
        payload.update(derive_etf_metrics(item))
        enriched.append(payload)
    grouped = assign_peer_groups(
        enriched, min_peer_group_size=int(config.get("min_peer_group_size") or 8)
    )
    book_rows = score_etf_lenses(grouped, config=config)

    regime_records = build_regime_tape(book_rows, config=config)
    sleeve_records = build_sleeve_heat(
        book_rows,
        min_sleeve_size=int(config.get("min_sleeve_size") or 3),
        top_n=int(config.get("top_sleeves_by_tape") or 15),
    )
    divergence_records = build_divergences(book_rows)
    catch_up_records = build_catch_up_vs_extended(
        book_rows,
        min_sleeve_size=int(config.get("min_sleeve_size") or 3),
        catch_up_per_sleeve=int(config.get("catch_up_per_sleeve") or 8),
        extended_per_sleeve=int(config.get("extended_per_sleeve") or 5),
    )
    vehicle_records = build_vehicle_quality(
        book_rows,
        per_sleeve=int(config.get("vehicle_quality_per_sleeve") or 3),
    )
    lens_records = build_lens_leaders(
        book_rows,
        per_lens=int(config.get("lens_leaders_per_lens") or 15),
    )
    holdings_records = build_holdings_overlay(
        book_rows, config=config, holding_tickers=holding_tickers
    )
    dod_records = build_day_over_day(book_rows, prior_book_rows)

    reports = {
        "regime_tape_log": run_output_dir / "etf_regime_tape.log",
        "sleeve_heat_log": run_output_dir / "etf_sleeve_heat.log",
        "sleeve_heat_csv": run_output_dir / "etf_sleeve_heat.csv",
        "divergences_log": run_output_dir / "etf_divergences.log",
        "catch_up_log": run_output_dir / "etf_catch_up_vs_extended.log",
        "vehicle_quality_log": run_output_dir / "etf_vehicle_quality.log",
        "vehicle_quality_csv": run_output_dir / "etf_vehicle_quality.csv",
        "lens_leaders_log": run_output_dir / "etf_lens_leaders.log",
        "holdings_overlay_log": run_output_dir / "etf_holdings_overlay.log",
        "dod_log": run_output_dir / "etf_dod_changes.log",
        "book_ranked_csv": run_output_dir / "etf_book_ranked.csv",
    }
    _write_regime_log(reports["regime_tape_log"], regime_records)
    _write_sleeve_heat_log(reports["sleeve_heat_log"], sleeve_records)
    _write_divergence_log(reports["divergences_log"], divergence_records)
    _write_catch_up_log(reports["catch_up_log"], catch_up_records)
    _write_vehicle_log(reports["vehicle_quality_log"], vehicle_records)
    _write_lens_log(reports["lens_leaders_log"], lens_records)
    _write_holdings_log(reports["holdings_overlay_log"], holdings_records)
    _write_dod_log(
        reports["dod_log"],
        dod_records,
        had_prior=prior_book_rows is not None,
    )
    _write_book_csv(reports["book_ranked_csv"], book_rows)

    sleeve_csv_headers = [
        "rank",
        "sleeve_key",
        "row_count",
        "median_perf_5d",
        "median_perf_1m",
        "median_flow_to_aum_1m",
        "median_organic_demand_1m",
        "median_book_consensus",
        "best_perf_symbol",
        "best_perf_1m",
    ]
    log_rows_to_csv(
        reports["sleeve_heat_csv"],
        sleeve_csv_headers,
        [[_serialize(record.get(header)) for header in sleeve_csv_headers] for record in sleeve_records],
    )
    vehicle_csv_headers = [
        "sleeve_key",
        "sleeve_rank",
        "symbol",
        "description",
        "brand",
        "aum",
        "expense_ratio",
        "nav_discount_premium",
        "dollar_liquidity",
        "vehicle_quality",
    ]
    log_rows_to_csv(
        reports["vehicle_quality_csv"],
        vehicle_csv_headers,
        [[_serialize(record.get(header)) for header in vehicle_csv_headers] for record in vehicle_records],
    )

    one_x_count = sum(1 for item in book_rows if item.get("product_class") == "1x")
    return {
        "suite_id": config.get("suite_id"),
        "config_path": config.get("source_path"),
        "book_rows": book_rows,
        "one_x_count": one_x_count,
        "levered_or_inverse_count": len(book_rows) - one_x_count,
        "reports": reports,
        "book_records": build_book_records(book_rows, run_id),
        "regime_records": _attach_run_id(run_id, regime_records),
        "sleeve_records": _attach_run_id(run_id, sleeve_records),
        "divergence_records": _attach_run_id(run_id, divergence_records),
        "catch_up_records": _attach_run_id(run_id, catch_up_records),
        "vehicle_records": _attach_run_id(run_id, vehicle_records),
        "lens_records": _attach_run_id(run_id, lens_records),
        "holdings_records": _attach_run_id(run_id, holdings_records),
        "dod_records": _attach_run_id(run_id, dod_records),
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
    }
