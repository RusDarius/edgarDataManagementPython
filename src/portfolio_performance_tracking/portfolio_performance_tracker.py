# === AI GENERATED CODE START (GitHub Copilot - Claude Sonnet 5) ===
# Generated on: 2026-08-22
# Purpose: High-level orchestrator tying cash-flow ledger, NAV history, the
#   Modified Dietz calculator, and the shadow-benchmark comparator together.
#   Uses the shared portfolio registry so flow-aware historical performance is
#   self-contained.
from __future__ import annotations

from datetime import datetime
from typing import Any

from db.portfolio_performance_tracking_operations import (
    create_or_get_performance_portfolio,
    delete_performance_portfolio_by_name,
)
from portfolio_performance_tracking.benchmark_comparator import (
    BenchmarkComparisonPoint,
    build_shadow_benchmark_series,
    fetch_benchmark_price_from_scan,
)
from portfolio_performance_tracking.cash_flow_ledger import (
    get_active_capital_breakdown,
    get_external_flows,
    record_contribution,
    record_dividend,
    record_realized_gain,
    record_withdrawal,
    summarize_cash_flows,
)
from portfolio_performance_tracking.nav_history import (
    latest_nav_snapshot,
    list_nav_snapshots,
    record_nav_snapshot,
)
from portfolio_performance_tracking.performance_calculator import (
    PerformancePoint,
    annualize_return_pct,
    build_cumulative_performance_series,
)


class PortfolioPerformanceTracker:
    """Flow-aware historical fund performance tracker.

    This class answers
    "how has my money actually performed over time, given that it was added in
    chunks at different dates, some of it came back as realized gains/dividends,
    and I want to know if I'd have done better just buying an index instead".
    """

    def __init__(
        self,
        portfolio_name: str | None = None,
        portfolio_id: int | None = None,
        currency: str = "USD",
        benchmark_symbol: str | None = None,
    ) -> None:
        if portfolio_id is not None:
            from db.portfolio_performance_tracking_operations import (
                get_performance_portfolio_by_id,
            )

            portfolio_row = get_performance_portfolio_by_id(portfolio_id)
        elif portfolio_name is not None:
            portfolio_row = create_or_get_performance_portfolio(
                portfolio_name=portfolio_name,
                currency=currency,
                benchmark_symbol=benchmark_symbol,
            )
        else:
            raise ValueError("portfolio_name or portfolio_id must be provided")

        if portfolio_row is None:
            raise ValueError("portfolio could not be resolved")
        self._portfolio_row = portfolio_row

    @classmethod
    def create_simple_fund(
        cls,
        fund_name: str,
        currency: str = "USD",
        benchmark_symbol: str | None = None,
    ) -> "PortfolioPerformanceTracker":
        """Simple mode entry point: no per-symbol holdings, just fund-level cash
        flows (``record_contribution``/``record_withdrawal``) and whole-fund
        value updates (``record_fund_value``). Use this when logging every buy/
        sell/holding move is too tedious and you only care about "money in, and
        what the whole thing is worth now".
        """
        return cls(
            portfolio_name=fund_name,
            currency=currency,
            benchmark_symbol=benchmark_symbol,
        )

    @staticmethod
    def delete_fund(fund_name: str) -> bool:
        """Delete a fund by exact name, including its holdings and history.

        Returns ``True`` when a matching fund was deleted and ``False`` when no
        fund with that exact name exists. This is permanent database deletion.
        """
        return delete_performance_portfolio_by_name(fund_name)

    @staticmethod
    def delete_simple_fund(fund_name: str) -> bool:
        """Explicit alias for deleting a simple-mode fund by exact name."""
        return PortfolioPerformanceTracker.delete_fund(fund_name)

    @property
    def portfolio_id(self) -> int:
        return int(self._portfolio_row["portfolio_id"])

    @property
    def portfolio_name(self) -> str:
        return str(self._portfolio_row["portfolio_name"])

    # -- Cash flow recording -------------------------------------------------

    def record_contribution(
        self,
        amount: float,
        currency: str = "USD",
        fx_rate_to_portfolio: float | None = None,
        benchmark_symbol: str | None = None,
        benchmark_price: float | None = None,
        note: str | None = None,
        executed_at: datetime | None = None,
    ) -> dict[str, Any]:
        return record_contribution(
            portfolio_id=self.portfolio_id,
            amount=amount,
            currency=currency,
            fx_rate_to_portfolio=fx_rate_to_portfolio,
            benchmark_symbol=benchmark_symbol,
            benchmark_price=benchmark_price,
            note=note,
            executed_at=executed_at,
        )

    def record_withdrawal(
        self,
        amount: float,
        currency: str = "USD",
        fx_rate_to_portfolio: float | None = None,
        benchmark_symbol: str | None = None,
        benchmark_price: float | None = None,
        note: str | None = None,
        executed_at: datetime | None = None,
    ) -> dict[str, Any]:
        return record_withdrawal(
            portfolio_id=self.portfolio_id,
            amount=amount,
            currency=currency,
            fx_rate_to_portfolio=fx_rate_to_portfolio,
            benchmark_symbol=benchmark_symbol,
            benchmark_price=benchmark_price,
            note=note,
            executed_at=executed_at,
        )

    def record_dividend(
        self,
        amount: float,
        related_symbol: str | None = None,
        currency: str = "USD",
        note: str | None = None,
        executed_at: datetime | None = None,
    ) -> dict[str, Any]:
        return record_dividend(
            portfolio_id=self.portfolio_id,
            amount=amount,
            related_symbol=related_symbol,
            currency=currency,
            note=note,
            executed_at=executed_at,
        )

    def record_realized_gain(
        self,
        amount: float,
        related_symbol: str | None = None,
        currency: str = "USD",
        note: str | None = None,
        executed_at: datetime | None = None,
    ) -> dict[str, Any]:
        return record_realized_gain(
            portfolio_id=self.portfolio_id,
            amount=amount,
            related_symbol=related_symbol,
            currency=currency,
            note=note,
            executed_at=executed_at,
        )

    def cash_flow_summary(self) -> dict[str, float]:
        return summarize_cash_flows(self.portfolio_id)

    # -- NAV snapshots --------------------------------------------------------

    def take_nav_snapshot(
        self,
        total_market_value: float,
        cash_balance: float = 0.0,
        benchmark_symbol: str | None = None,
        benchmark_price: float | None = None,
        note: str | None = None,
        snapshot_at: datetime | None = None,
    ) -> dict[str, Any]:
        return record_nav_snapshot(
            portfolio_id=self.portfolio_id,
            total_market_value=total_market_value,
            cash_balance=cash_balance,
            benchmark_symbol=benchmark_symbol,
            benchmark_price=benchmark_price,
            note=note,
            snapshot_at=snapshot_at,
        )

    def latest_nav(self) -> dict[str, Any] | None:
        return latest_nav_snapshot(self.portfolio_id)

    def record_fund_value(
        self,
        total_value: float,
        benchmark_symbol: str | None = None,
        benchmark_price: float | None = None,
        note: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Simple mode: log "the whole fund is worth X today" as one number.

        No market-value/cash split needed -- pass the full current fund value.
        Use this instead of ``take_nav_snapshot`` when you are not tracking a
        market-value/cash split.
        """
        return record_nav_snapshot(
            portfolio_id=self.portfolio_id,
            total_market_value=total_value,
            cash_balance=0.0,
            benchmark_symbol=benchmark_symbol,
            benchmark_price=benchmark_price,
            note=note,
            snapshot_at=as_of,
        )

    def get_active_capital_breakdown(
        self, as_of: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Simple mode: one row per contribution/withdrawal with its age (days
        since it entered the fund) and the running net active capital -- answers
        "since when has this money been working" without needing NAV history.
        """
        return get_active_capital_breakdown(self.portfolio_id, as_of=as_of)

    @staticmethod
    def resolve_benchmark_price(
        scan_data: list[dict[str, Any]],
        benchmark_symbol: str,
        symbol_key: str = "ticker-view",
    ) -> float | None:
        return fetch_benchmark_price_from_scan(
            scan_data=scan_data,
            benchmark_symbol=benchmark_symbol,
            symbol_key=symbol_key,
        )

    # -- Performance / benchmark ----------------------------------------------

    def _fetch_snapshots_and_flows(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        nav_snapshots = list_nav_snapshots(self.portfolio_id, start=start, end=end)
        external_flows = get_external_flows(self.portfolio_id, start=start, end=end)
        return nav_snapshots, external_flows

    def get_performance_history(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[PerformancePoint]:
        nav_snapshots, external_flows = self._fetch_snapshots_and_flows(start, end)
        return build_cumulative_performance_series(
            nav_snapshots=nav_snapshots, external_flows=external_flows
        )

    def compare_to_benchmark(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[BenchmarkComparisonPoint]:
        nav_snapshots, external_flows = self._fetch_snapshots_and_flows(start, end)
        return build_shadow_benchmark_series(
            nav_snapshots=nav_snapshots, external_flows=external_flows
        )

    def get_latest_performance_summary(self) -> dict[str, Any]:
        history = self.get_performance_history()
        if not history:
            return {
                "portfolio_id": self.portfolio_id,
                "portfolio_name": self.portfolio_name,
                "has_data": False,
            }
        first_point = history[0]
        last_point = history[-1]
        annualized = annualize_return_pct(
            cumulative_return_pct=last_point.cumulative_return_pct,
            start=first_point.as_of,
            end=last_point.as_of,
        )
        return {
            "portfolio_id": self.portfolio_id,
            "portfolio_name": self.portfolio_name,
            "has_data": True,
            "first_snapshot_at": first_point.as_of,
            "latest_snapshot_at": last_point.as_of,
            "latest_nav": last_point.nav,
            "cumulative_return_pct": last_point.cumulative_return_pct,
            "annualized_return_pct": annualized,
            "cumulative_contributions": last_point.cumulative_contributions,
            "cumulative_withdrawals": last_point.cumulative_withdrawals,
            **self.cash_flow_summary(),
        }


# === AI GENERATED CODE END ===
