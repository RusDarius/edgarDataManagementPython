# === AI GENERATED CODE START (GitHub Copilot - Claude Sonnet 5) ===
# Generated on: 2026-08-22
# Purpose: Flow-aware performance math. Solves the "funds added/withdrawn at
#   different times" problem using the Modified Dietz method for each sub-period
#   between consecutive NAV snapshots, then geometrically links (compounds) the
#   sub-period returns into a single cumulative percentage return series. This
#   is the standard practical approximation of a true time-weighted return when
#   you only have periodic NAV snapshots rather than a daily valuation feed.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CashFlowPoint:
    executed_at: datetime
    amount: float  # signed: positive = money in, negative = money out


@dataclass(frozen=True)
class PeriodReturn:
    period_start: datetime
    period_end: datetime
    begin_nav: float
    end_nav: float
    net_external_flow: float
    period_return_pct: float | None


@dataclass(frozen=True)
class PerformancePoint:
    as_of: datetime
    nav: float
    period_return_pct: float | None
    cumulative_return_pct: float
    cumulative_contributions: float
    cumulative_withdrawals: float


def modified_dietz_return(
    begin_value: float,
    end_value: float,
    flows: list[CashFlowPoint],
    period_start: datetime,
    period_end: datetime,
) -> float | None:
    """Modified Dietz return (%) for one period, given intra-period external flows.

    R = (end_value - begin_value - sum(F_i)) / (begin_value + sum(F_i * w_i))
    where w_i is the fraction of the period remaining after flow i lands, so a
    contribution made near the end of the period is weighted less (it hasn't had
    time to earn a return) and one near the start is weighted more.
    """
    total_seconds = (period_end - period_start).total_seconds()
    if total_seconds <= 0:
        return None

    net_flow_sum = 0.0
    weighted_flow_sum = 0.0
    for flow in flows:
        if flow.executed_at < period_start or flow.executed_at > period_end:
            continue
        elapsed_seconds = (period_end - flow.executed_at).total_seconds()
        weight = max(0.0, min(1.0, elapsed_seconds / total_seconds))
        net_flow_sum += flow.amount
        weighted_flow_sum += flow.amount * weight

    denominator = begin_value + weighted_flow_sum
    if denominator == 0:
        return None

    numerator = end_value - begin_value - net_flow_sum
    return (numerator / denominator) * 100.0


def build_cumulative_performance_series(
    nav_snapshots: list[dict[str, Any]],
    external_flows: list[dict[str, Any]],
) -> list[PerformancePoint]:
    """Chain Modified Dietz sub-period returns across ordered NAV snapshots.

    ``nav_snapshots`` must be ordered ascending by ``snapshot_at`` and each row
    must have ``total_nav`` + ``snapshot_at``. ``external_flows`` rows must have
    ``executed_at`` + ``amount`` (contributions positive, withdrawals negative --
    exactly what ``cash_flow_ledger.get_external_flows`` returns).
    """
    if not nav_snapshots:
        return []

    flow_points = [
        CashFlowPoint(executed_at=flow["executed_at"], amount=float(flow["amount"]))
        for flow in external_flows
    ]

    results: list[PerformancePoint] = []
    cumulative_growth = 1.0
    cumulative_contributions = 0.0
    cumulative_withdrawals = 0.0

    first_snapshot = nav_snapshots[0]
    results.append(
        PerformancePoint(
            as_of=first_snapshot["snapshot_at"],
            nav=float(first_snapshot["total_nav"]),
            period_return_pct=None,
            cumulative_return_pct=0.0,
            cumulative_contributions=cumulative_contributions,
            cumulative_withdrawals=cumulative_withdrawals,
        )
    )

    for previous_snapshot, current_snapshot in zip(nav_snapshots, nav_snapshots[1:]):
        period_start = previous_snapshot["snapshot_at"]
        period_end = current_snapshot["snapshot_at"]
        begin_nav = float(previous_snapshot["total_nav"])
        end_nav = float(current_snapshot["total_nav"])

        period_flows = [
            flow
            for flow in flow_points
            if period_start < flow.executed_at <= period_end
        ]
        for flow in period_flows:
            if flow.amount > 0:
                cumulative_contributions += flow.amount
            else:
                cumulative_withdrawals += -flow.amount

        period_return_pct = modified_dietz_return(
            begin_value=begin_nav,
            end_value=end_nav,
            flows=period_flows,
            period_start=period_start,
            period_end=period_end,
        )
        if period_return_pct is not None:
            cumulative_growth *= 1.0 + (period_return_pct / 100.0)

        results.append(
            PerformancePoint(
                as_of=period_end,
                nav=end_nav,
                period_return_pct=period_return_pct,
                cumulative_return_pct=(cumulative_growth - 1.0) * 100.0,
                cumulative_contributions=cumulative_contributions,
                cumulative_withdrawals=cumulative_withdrawals,
            )
        )

    return results


def compute_active_capital_timeline(
    external_flows: list[dict[str, Any]],
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    """Simple-mode view: one row per contribution/withdrawal with its age and the
    running net capital that has been "active" in the fund since that flow.

    No NAV/pricing data required -- this is pure bookkeeping over the flows
    themselves, meant for the simple mode (fund-level inflows only, no
    per-symbol trade history) as a lightweight "since when did my money go in"
    view. Pair it with ``build_cumulative_performance_series`` for the actual
    % return once NAV snapshots exist.
    """
    as_of_reference = as_of or datetime.now()
    ordered_flows = sorted(external_flows, key=lambda flow: flow["executed_at"])

    rows: list[dict[str, Any]] = []
    running_active_capital = 0.0
    for flow in ordered_flows:
        amount = float(flow["amount"])
        running_active_capital += amount
        days_active = max(
            (as_of_reference - flow["executed_at"]).total_seconds() / 86400.0, 0.0
        )
        rows.append(
            {
                "executed_at": flow["executed_at"],
                "flow_type": flow.get("flow_type"),
                "amount": amount,
                "days_active": days_active,
                "running_active_capital": running_active_capital,
                "note": flow.get("note"),
            }
        )
    return rows


def annualize_return_pct(
    cumulative_return_pct: float | None,
    start: datetime | None,
    end: datetime | None,
) -> float | None:
    if cumulative_return_pct is None or start is None or end is None:
        return None
    elapsed_days = (end - start).total_seconds() / 86400.0
    if elapsed_days <= 0:
        return None
    growth_ratio = 1.0 + (cumulative_return_pct / 100.0)
    if growth_ratio <= 0:
        return None
    return ((growth_ratio ** (365.0 / elapsed_days)) - 1.0) * 100.0


# === AI GENERATED CODE END ===
