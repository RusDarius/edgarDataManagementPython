# === AI GENERATED CODE START (GitHub Copilot - Claude Sonnet 5) ===
# Generated on: 2026-08-22
# Purpose: Benchmark the portfolio against a symbol (e.g. SPY) using a
#   "shadow portfolio" that receives the exact same external cash flows, at the
#   exact same times, but buys/sells the benchmark instrument instead. This is
#   the cleanest way to answer "was I better off than just buying SPY with the
#   same money on the same schedule?" without needing a Modified Dietz twin
#   computation on the benchmark side.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class BenchmarkComparisonPoint:
    as_of: datetime
    portfolio_nav: float
    portfolio_cumulative_return_pct: float
    shadow_benchmark_value: float | None
    shadow_benchmark_cumulative_return_pct: float | None
    alpha_pct: float | None  # portfolio return minus benchmark return, same window


def _extract_priced_flows(
    external_flows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only flows that carry a benchmark_price -- required to simulate a buy/sell."""
    priced = []
    for flow in external_flows:
        if flow.get("benchmark_price"):
            priced.append(flow)
    return priced


def build_shadow_benchmark_series(
    nav_snapshots: list[dict[str, Any]],
    external_flows: list[dict[str, Any]],
) -> list[BenchmarkComparisonPoint]:
    """Simulate a benchmark-only portfolio fed by the same contributions/withdrawals.

    The shadow value series is run through the exact same Modified Dietz
    chaining used for the real portfolio (``build_cumulative_performance_series``,
    fed the identical ``external_flows``), so both cumulative-return numbers are
    computed with the same methodology and are directly comparable -- there is
    no separate/ad-hoc formula for the benchmark leg.

    Requirements to get a benchmark data point at a given NAV snapshot:
      - The snapshot itself must carry ``benchmark_price`` (the benchmark close
        on/around that date).
      - Every external flow that happened before that snapshot must also carry
        ``benchmark_price`` so it can be converted into simulated benchmark shares.

    Flows/snapshots missing a benchmark_price are skipped for the benchmark leg
    only; the portfolio's own cumulative return is unaffected.
    """
    from portfolio_performance_tracking.performance_calculator import (
        build_cumulative_performance_series,
    )

    portfolio_series = build_cumulative_performance_series(
        nav_snapshots=nav_snapshots, external_flows=external_flows
    )
    if not portfolio_series:
        return []

    priced_flows = sorted(
        _extract_priced_flows(external_flows), key=lambda flow: flow["executed_at"]
    )

    # Build a parallel "NAV" series for the shadow benchmark: at each snapshot
    # timestamp that carries a benchmark_price, value = accumulated shadow
    # shares * that price. Snapshots without a benchmark_price are skipped.
    shadow_shares = 0.0
    flow_cursor = 0
    shadow_nav_snapshots: list[dict[str, Any]] = []
    for snapshot in sorted(nav_snapshots, key=lambda row: row["snapshot_at"]):
        while (
            flow_cursor < len(priced_flows)
            and priced_flows[flow_cursor]["executed_at"] <= snapshot["snapshot_at"]
        ):
            flow = priced_flows[flow_cursor]
            benchmark_price = float(flow["benchmark_price"])
            if benchmark_price > 0:
                shadow_shares += float(flow["amount"]) / benchmark_price
            flow_cursor += 1

        benchmark_price_at_snapshot = snapshot.get("benchmark_price")
        if benchmark_price_at_snapshot:
            shadow_nav_snapshots.append(
                {
                    "snapshot_at": snapshot["snapshot_at"],
                    "total_nav": shadow_shares * float(benchmark_price_at_snapshot),
                }
            )

    shadow_series = build_cumulative_performance_series(
        nav_snapshots=shadow_nav_snapshots, external_flows=external_flows
    )
    shadow_series_by_date = {point.as_of: point for point in shadow_series}

    results: list[BenchmarkComparisonPoint] = []
    for point in portfolio_series:
        shadow_point = shadow_series_by_date.get(point.as_of)
        shadow_value = shadow_point.nav if shadow_point is not None else None
        shadow_cumulative_return_pct = (
            shadow_point.cumulative_return_pct if shadow_point is not None else None
        )
        alpha_pct = (
            point.cumulative_return_pct - shadow_cumulative_return_pct
            if shadow_cumulative_return_pct is not None
            else None
        )
        results.append(
            BenchmarkComparisonPoint(
                as_of=point.as_of,
                portfolio_nav=point.nav,
                portfolio_cumulative_return_pct=point.cumulative_return_pct,
                shadow_benchmark_value=shadow_value,
                shadow_benchmark_cumulative_return_pct=shadow_cumulative_return_pct,
                alpha_pct=alpha_pct,
            )
        )

    return results


def fetch_benchmark_price_from_scan(
    scan_data: list[dict[str, Any]],
    benchmark_symbol: str,
    symbol_key: str = "ticker-view",
    price_field: str = "close",
) -> float | None:
    """Pull a benchmark's close price out of an already-fetched TradingView scan.

    Reuses the same scan payloads other flows in this repo already fetch (e.g.
    ``scan_global_market_move_prediction``), so you rarely need an extra API call
    just to price the benchmark -- most scans already include ``close``.
    """
    normalized_symbol = benchmark_symbol.strip().upper()
    for row in scan_data:
        candidate = str(row.get(symbol_key) or "").strip().upper()
        bare_candidate = candidate.split(":")[-1] if ":" in candidate else candidate
        if normalized_symbol in (candidate, bare_candidate):
            price = row.get(price_field)
            if price is not None:
                return float(price)
    return None


# === AI GENERATED CODE END ===
