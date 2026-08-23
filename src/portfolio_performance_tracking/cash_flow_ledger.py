# === AI GENERATED CODE START (GitHub Copilot - Claude Sonnet 5) ===
# Generated on: 2026-08-22
# Purpose: Thin ledger API over portfolio_cash_flows for recording money that
#   crosses the portfolio boundary (contributions/withdrawals) plus informational
#   income events (dividends/realized gains) that stay inside the portfolio.
from __future__ import annotations

from datetime import datetime
from typing import Any

from db.portfolio_performance_tracking_operations import (
    EXTERNAL_FLOW_TYPES,
    INCOME_EVENT_TYPES,
    get_cash_flows,
    insert_cash_flow,
)


def record_contribution(
    portfolio_id: int,
    amount: float,
    currency: str = "USD",
    fx_rate_to_portfolio: float | None = None,
    benchmark_symbol: str | None = None,
    benchmark_price: float | None = None,
    note: str | None = None,
    executed_at: datetime | None = None,
) -> dict[str, Any]:
    """Record external money added to the portfolio (initial funding or a top-up).

    ``benchmark_symbol``/``benchmark_price`` are optional but required if you want
    this contribution to feed the shadow-benchmark comparison (see
    ``benchmark_comparator.py``): pass the benchmark instrument's close price on
    the same date so the simulator can "buy" the benchmark at that price.
    """
    if amount <= 0:
        raise ValueError("contribution amount must be positive")
    return insert_cash_flow(
        portfolio_id=portfolio_id,
        flow_type="CONTRIBUTION",
        amount=amount,
        currency=currency,
        fx_rate_to_portfolio=fx_rate_to_portfolio,
        benchmark_symbol=benchmark_symbol,
        benchmark_price=benchmark_price,
        note=note,
        executed_at=executed_at,
    )


def record_withdrawal(
    portfolio_id: int,
    amount: float,
    currency: str = "USD",
    fx_rate_to_portfolio: float | None = None,
    benchmark_symbol: str | None = None,
    benchmark_price: float | None = None,
    note: str | None = None,
    executed_at: datetime | None = None,
) -> dict[str, Any]:
    """Record external money removed from the portfolio (cash withdrawn out)."""
    if amount <= 0:
        raise ValueError("withdrawal amount must be positive")
    return insert_cash_flow(
        portfolio_id=portfolio_id,
        flow_type="WITHDRAWAL",
        amount=-amount,
        currency=currency,
        fx_rate_to_portfolio=fx_rate_to_portfolio,
        benchmark_symbol=benchmark_symbol,
        benchmark_price=benchmark_price,
        note=note,
        executed_at=executed_at,
    )


def record_dividend(
    portfolio_id: int,
    amount: float,
    related_symbol: str | None = None,
    currency: str = "USD",
    note: str | None = None,
    executed_at: datetime | None = None,
) -> dict[str, Any]:
    """Record dividend income that stays inside the portfolio (informational only).

    This does NOT count as an external flow for Modified Dietz purposes -- the
    cash is already reflected in NAV growth. If the dividend was paid OUT of the
    portfolio (e.g. to your bank account), also call ``record_withdrawal``.
    """
    if amount <= 0:
        raise ValueError("dividend amount must be positive")
    return insert_cash_flow(
        portfolio_id=portfolio_id,
        flow_type="DIVIDEND",
        amount=amount,
        currency=currency,
        related_symbol=related_symbol,
        note=note,
        executed_at=executed_at,
    )


def record_realized_gain(
    portfolio_id: int,
    amount: float,
    related_symbol: str | None = None,
    currency: str = "USD",
    note: str | None = None,
    executed_at: datetime | None = None,
) -> dict[str, Any]:
    """Record a realized capital gain/loss from a sale as an informational event.

    Pass the signed amount (negative for a realized loss). Like dividends, this
    is bookkeeping only -- it is already reflected in NAV via cash or proceeds
    and must NOT also be entered as a contribution.
    """
    if amount == 0:
        raise ValueError("realized gain amount must be non-zero")
    return insert_cash_flow(
        portfolio_id=portfolio_id,
        flow_type="REALIZED_GAIN",
        amount=amount,
        currency=currency,
        related_symbol=related_symbol,
        note=note,
        executed_at=executed_at,
    )


def get_external_flows(
    portfolio_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    """Contributions/withdrawals only -- the flows that affect Modified Dietz."""
    return get_cash_flows(
        portfolio_id=portfolio_id,
        flow_types=list(EXTERNAL_FLOW_TYPES),
        start=start,
        end=end,
    )


def get_income_events(
    portfolio_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    """Dividends/realized gains only -- informational, does not affect returns."""
    return get_cash_flows(
        portfolio_id=portfolio_id,
        flow_types=list(INCOME_EVENT_TYPES),
        start=start,
        end=end,
    )


def get_all_cash_flows(
    portfolio_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    return get_cash_flows(portfolio_id=portfolio_id, start=start, end=end)


def get_active_capital_breakdown(
    portfolio_id: int,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    """Simple-mode reporting: per-contribution/withdrawal age + running net capital.

    Thin DB-fetch wrapper around the pure
    ``performance_calculator.compute_active_capital_timeline``.
    """
    from portfolio_performance_tracking.performance_calculator import (
        compute_active_capital_timeline,
    )

    external_flows = get_external_flows(portfolio_id)
    return compute_active_capital_timeline(external_flows, as_of=as_of)


def summarize_cash_flows(portfolio_id: int) -> dict[str, float]:
    """Lifetime totals: net funding in, dividends received, realized gains booked."""
    flows = get_all_cash_flows(portfolio_id)
    totals = {
        "total_contributions": 0.0,
        "total_withdrawals": 0.0,
        "net_external_flow": 0.0,
        "total_dividends": 0.0,
        "total_realized_gains": 0.0,
    }
    for flow in flows:
        flow_type = flow["flow_type"]
        amount = float(flow["amount"])
        if flow_type == "CONTRIBUTION":
            totals["total_contributions"] += amount
            totals["net_external_flow"] += amount
        elif flow_type == "WITHDRAWAL":
            totals["total_withdrawals"] += -amount
            totals["net_external_flow"] += amount
        elif flow_type == "DIVIDEND":
            totals["total_dividends"] += amount
        elif flow_type == "REALIZED_GAIN":
            totals["total_realized_gains"] += amount
    return totals


# === AI GENERATED CODE END ===
