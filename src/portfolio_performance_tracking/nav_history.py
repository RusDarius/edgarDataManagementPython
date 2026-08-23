# === AI GENERATED CODE START (GitHub Copilot - Claude Sonnet 5) ===
# Generated on: 2026-08-22
# Purpose: Thin API over portfolio_nav_snapshots for recording point-in-time NAV
#   (net asset value) readings that build up the historical performance curve.
from __future__ import annotations

from datetime import datetime
from typing import Any

from db.portfolio_performance_tracking_operations import (
    get_latest_nav_snapshot,
    get_nav_snapshots,
    insert_nav_snapshot,
)


def record_nav_snapshot(
    portfolio_id: int,
    total_market_value: float,
    cash_balance: float = 0.0,
    benchmark_symbol: str | None = None,
    benchmark_price: float | None = None,
    note: str | None = None,
    snapshot_at: datetime | None = None,
) -> dict[str, Any]:
    """Persist one NAV reading: total_nav = total_market_value + cash_balance.

    Take snapshots as often as you like (daily/weekly/whenever you refresh
    holdings) -- the performance calculator links whatever snapshots exist into
    a cumulative return series. Pass ``benchmark_price`` (the benchmark's close
    on this date) to keep the shadow-benchmark comparison in sync.
    """
    if total_market_value < 0:
        raise ValueError("total_market_value cannot be negative")
    if cash_balance < 0:
        raise ValueError("cash_balance cannot be negative")
    return insert_nav_snapshot(
        portfolio_id=portfolio_id,
        total_market_value=total_market_value,
        cash_balance=cash_balance,
        benchmark_symbol=benchmark_symbol,
        benchmark_price=benchmark_price,
        note=note,
        snapshot_at=snapshot_at,
    )


def list_nav_snapshots(
    portfolio_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    return get_nav_snapshots(portfolio_id=portfolio_id, start=start, end=end)


def latest_nav_snapshot(portfolio_id: int) -> dict[str, Any] | None:
    return get_latest_nav_snapshot(portfolio_id)


# === AI GENERATED CODE END ===
