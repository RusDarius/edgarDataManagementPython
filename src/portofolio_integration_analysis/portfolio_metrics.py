from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Iterable


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _compute_holding_days(
    opened_at: datetime | None,
    closed_at: datetime | None = None,
    as_of: datetime | None = None,
) -> float | None:
    normalized_opened_at = _normalize_datetime(opened_at)
    if normalized_opened_at is None:
        return None
    normalized_end = (
        _normalize_datetime(closed_at) or _normalize_datetime(as_of) or datetime.now()
    )
    elapsed_days = (normalized_end - normalized_opened_at).total_seconds() / 86400.0
    return max(elapsed_days, 0.0)


def _annualize_return(
    total_return_pct: float | None,
    holding_days: float | None,
) -> float | None:
    if total_return_pct is None or holding_days in (None, 0):
        return None
    total_return_ratio = total_return_pct / 100.0
    if total_return_ratio <= -1.0:
        return None
    return ((1.0 + total_return_ratio) ** (365.0 / holding_days) - 1.0) * 100.0


@dataclass(frozen=True)
class PortfolioPositionRecord:
    portfolio_item_id: int | None
    portfolio_id: int
    portfolio_name: str
    company_internal_id: int
    symbol: str
    company_name: str
    shares_held: float
    average_entry_price: float
    cost_basis_total: float
    realized_pnl: float = 0.0
    fees_total: float = 0.0
    last_price: float | None = None
    last_market_cap_basic: float | None = None
    last_change_pct: float | None = None
    last_perf_w: float | None = None
    last_perf_1m: float | None = None
    last_perf_y: float | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    exchange: str | None = None
    market: str | None = None
    currency: str | None = None
    entry_currency: str | None = None
    entry_fx_to_portfolio: float | None = None
    notes: str | None = None


@dataclass(frozen=True)
class PortfolioPositionMetrics:
    portfolio_item_id: int | None
    portfolio_id: int
    symbol: str
    company_name: str
    shares_held: float
    average_entry_price: float
    cost_basis_total: float
    market_value: float | None
    price_coverage: bool
    unrealized_pnl: float | None
    unrealized_return_pct: float | None
    realized_pnl: float
    total_pnl: float | None
    total_return_pct: float | None
    holding_days: float | None = None
    annualized_return_pct: float | None = None
    opened_at: datetime | None = None
    weight_by_market_value: float | None = None
    weight_by_cost_basis: float | None = None
    last_price: float | None = None
    last_change_pct: float | None = None
    last_perf_w: float | None = None
    last_perf_1m: float | None = None
    last_perf_y: float | None = None
    exchange: str | None = None
    market: str | None = None
    currency: str | None = None
    entry_currency: str | None = None
    entry_fx_to_portfolio: float | None = None


@dataclass(frozen=True)
class PortfolioSummaryMetrics:
    portfolio_id: int
    portfolio_name: str
    positions_count: int
    priced_positions_count: int
    price_coverage_ratio: float
    total_cost_basis: float
    total_market_value: float | None
    total_realized_pnl: float
    total_unrealized_pnl: float | None
    total_pnl: float | None
    total_return_pct: float | None
    annualized_total_return_pct: float | None
    weighted_change_pct: float | None
    weighted_perf_w: float | None
    weighted_perf_1m: float | None
    weighted_perf_y: float | None
    average_holding_days: float | None
    weighted_holding_days: float | None
    oldest_position_days: float | None
    largest_position_weight: float | None
    concentration_hhi: float | None


def compute_position_metrics(
    position: PortfolioPositionRecord,
    total_market_value: float | None = None,
    total_cost_basis: float | None = None,
    as_of: datetime | None = None,
) -> PortfolioPositionMetrics:
    market_value = None
    unrealized_pnl = None
    unrealized_return_pct = None
    total_pnl = None
    total_return_pct = None

    if position.last_price is not None:
        market_value = position.last_price * position.shares_held
        unrealized_pnl = market_value - position.cost_basis_total
        unrealized_return_pct = _safe_ratio(unrealized_pnl, position.cost_basis_total)
        if unrealized_return_pct is not None:
            unrealized_return_pct *= 100.0

    if unrealized_pnl is not None:
        total_pnl = unrealized_pnl + position.realized_pnl
    elif position.realized_pnl != 0:
        total_pnl = position.realized_pnl

    total_return_ratio = _safe_ratio(total_pnl, position.cost_basis_total)
    if total_return_ratio is not None:
        total_return_pct = total_return_ratio * 100.0

    holding_days = _compute_holding_days(
        opened_at=position.opened_at,
        closed_at=position.closed_at,
        as_of=as_of,
    )
    annualized_return_pct = _annualize_return(total_return_pct, holding_days)

    weight_by_market_value = None
    if market_value is not None and total_market_value not in (None, 0):
        weight_by_market_value = market_value / total_market_value

    weight_by_cost_basis = None
    if total_cost_basis not in (None, 0):
        weight_by_cost_basis = position.cost_basis_total / total_cost_basis

    return PortfolioPositionMetrics(
        portfolio_item_id=position.portfolio_item_id,
        portfolio_id=position.portfolio_id,
        symbol=position.symbol,
        company_name=position.company_name,
        shares_held=position.shares_held,
        average_entry_price=position.average_entry_price,
        cost_basis_total=position.cost_basis_total,
        market_value=market_value,
        price_coverage=market_value is not None,
        unrealized_pnl=unrealized_pnl,
        unrealized_return_pct=unrealized_return_pct,
        realized_pnl=position.realized_pnl,
        total_pnl=total_pnl,
        total_return_pct=total_return_pct,
        holding_days=holding_days,
        annualized_return_pct=annualized_return_pct,
        opened_at=position.opened_at,
        weight_by_market_value=weight_by_market_value,
        weight_by_cost_basis=weight_by_cost_basis,
        last_price=position.last_price,
        last_change_pct=position.last_change_pct,
        last_perf_w=position.last_perf_w,
        last_perf_1m=position.last_perf_1m,
        last_perf_y=position.last_perf_y,
        exchange=position.exchange,
        market=position.market,
        currency=position.currency,
        entry_currency=position.entry_currency,
        entry_fx_to_portfolio=position.entry_fx_to_portfolio,
    )


def build_portfolio_summary(
    positions: Iterable[PortfolioPositionRecord],
    as_of: datetime | None = None,
) -> tuple[PortfolioSummaryMetrics | None, list[PortfolioPositionMetrics]]:
    position_list = list(positions)
    if not position_list:
        return None, []

    total_cost_basis = sum(position.cost_basis_total for position in position_list)
    priced_market_values = [
        position.last_price * position.shares_held
        for position in position_list
        if position.last_price is not None
    ]
    total_market_value = sum(priced_market_values) if priced_market_values else None

    metrics = [
        compute_position_metrics(
            position,
            total_market_value=total_market_value,
            total_cost_basis=total_cost_basis,
            as_of=as_of,
        )
        for position in position_list
    ]

    priced_metrics = [metric for metric in metrics if metric.market_value is not None]
    priced_positions_count = len(priced_metrics)
    price_coverage_ratio = priced_positions_count / len(metrics)

    total_realized_pnl = sum(metric.realized_pnl for metric in metrics)
    total_unrealized_pnl = (
        sum(metric.unrealized_pnl for metric in priced_metrics if metric.unrealized_pnl)
        if priced_metrics
        else None
    )

    total_pnl = None
    if total_unrealized_pnl is not None:
        total_pnl = total_realized_pnl + total_unrealized_pnl
    elif total_realized_pnl != 0:
        total_pnl = total_realized_pnl

    total_return_pct = None
    total_return_ratio = _safe_ratio(total_pnl, total_cost_basis)
    if total_return_ratio is not None:
        total_return_pct = total_return_ratio * 100.0

    def _weighted_metric(attribute: str) -> float | None:
        weighted_numerator = 0.0
        weighted_denominator = 0.0
        for metric in priced_metrics:
            value = getattr(metric, attribute)
            if value is None or metric.market_value in (None, 0):
                continue
            weighted_numerator += value * metric.market_value
            weighted_denominator += metric.market_value
        if weighted_denominator == 0:
            return None
        return weighted_numerator / weighted_denominator

    holding_day_values = [
        metric.holding_days for metric in metrics if metric.holding_days is not None
    ]
    average_holding_days = (
        sum(holding_day_values) / len(holding_day_values)
        if holding_day_values
        else None
    )
    oldest_position_days = max(holding_day_values) if holding_day_values else None

    weighted_holding_days = None
    if total_cost_basis not in (None, 0):
        holding_weighted_numerator = 0.0
        for metric in metrics:
            if metric.holding_days is None:
                continue
            holding_weighted_numerator += metric.holding_days * metric.cost_basis_total
        weighted_holding_days = holding_weighted_numerator / total_cost_basis

    annualized_total_return_pct = _annualize_return(
        total_return_pct=total_return_pct,
        holding_days=weighted_holding_days,
    )

    largest_position_weight = None
    concentration_hhi = None
    if total_market_value not in (None, 0):
        weights = [
            metric.market_value / total_market_value
            for metric in priced_metrics
            if metric.market_value is not None
        ]
        if weights:
            largest_position_weight = max(weights)
            concentration_hhi = sum(weight * weight for weight in weights)

    summary = PortfolioSummaryMetrics(
        portfolio_id=position_list[0].portfolio_id,
        portfolio_name=position_list[0].portfolio_name,
        positions_count=len(metrics),
        priced_positions_count=priced_positions_count,
        price_coverage_ratio=price_coverage_ratio,
        total_cost_basis=total_cost_basis,
        total_market_value=total_market_value,
        total_realized_pnl=total_realized_pnl,
        total_unrealized_pnl=total_unrealized_pnl,
        total_pnl=total_pnl,
        total_return_pct=total_return_pct,
        annualized_total_return_pct=annualized_total_return_pct,
        weighted_change_pct=_weighted_metric("last_change_pct"),
        weighted_perf_w=_weighted_metric("last_perf_w"),
        weighted_perf_1m=_weighted_metric("last_perf_1m"),
        weighted_perf_y=_weighted_metric("last_perf_y"),
        average_holding_days=average_holding_days,
        weighted_holding_days=weighted_holding_days,
        oldest_position_days=oldest_position_days,
        largest_position_weight=largest_position_weight,
        concentration_hhi=concentration_hhi,
    )
    return summary, metrics
