from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from generic_utils.log_to_files_util import log_rows_to_csv, log_to_file
from portofolio_integration_analysis.portfolio_metrics import (
    PortfolioPositionMetrics,
    PortfolioSummaryMetrics,
)

if TYPE_CHECKING:
    from portofolio_integration_analysis.portfolio_tracker import PortfolioTracker


LOG_DIR = Path(r"D:\FinanceProjects\edgarDataManagementPython\logs\portfolio_analysis")


def _slugify_portfolio_name(portfolio_name: str) -> str:
    return "_".join(portfolio_name.strip().lower().split()) or "portfolio"


def _format_number(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f}"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def _format_weight(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2%}"


def _build_output_paths(
    portfolio_name: str,
    output_dir: str | Path | None = None,
) -> dict[str, Path]:
    now = datetime.now()
    base_dir = Path(output_dir) if output_dir is not None else LOG_DIR
    portfolio_dir = (
        base_dir / _slugify_portfolio_name(portfolio_name) / now.strftime("%d_%m_%Y")
    )
    timestamp = now.strftime("%H%M%S")
    return {
        "directory": portfolio_dir,
        "log": portfolio_dir / f"portfolio_status_{timestamp}.log",
        "csv": portfolio_dir / f"portfolio_positions_metrics_{timestamp}.csv",
    }


def _build_csv_headers() -> list[str]:
    return [
        "symbol",
        "company_name",
        "opened_at",
        "holding_days",
        "shares_held",
        "average_entry_price",
        "cost_basis_total",
        "market_value",
        "price_coverage",
        "unrealized_pnl",
        "unrealized_return_pct",
        "realized_pnl",
        "total_pnl",
        "total_return_pct",
        "annualized_return_pct",
        "weight_by_market_value",
        "weight_by_cost_basis",
        "last_price",
        "last_change_pct",
        "last_perf_w",
        "last_perf_1m",
        "last_perf_y",
        "exchange",
        "market",
        "currency",
        "entry_currency",
        "entry_fx_to_portfolio",
    ]


def _build_csv_rows(
    position_metrics: list[PortfolioPositionMetrics],
) -> list[list[str]]:
    return [
        [
            metric.symbol,
            metric.company_name,
            metric.opened_at.isoformat() if metric.opened_at else "",
            str(metric.holding_days or ""),
            str(metric.shares_held),
            str(metric.average_entry_price),
            str(metric.cost_basis_total),
            str(metric.market_value or ""),
            str(metric.price_coverage),
            str(metric.unrealized_pnl or ""),
            str(metric.unrealized_return_pct or ""),
            str(metric.realized_pnl),
            str(metric.total_pnl or ""),
            str(metric.total_return_pct or ""),
            str(metric.annualized_return_pct or ""),
            str(metric.weight_by_market_value or ""),
            str(metric.weight_by_cost_basis or ""),
            str(metric.last_price or ""),
            str(metric.last_change_pct or ""),
            str(metric.last_perf_w or ""),
            str(metric.last_perf_1m or ""),
            str(metric.last_perf_y or ""),
            metric.exchange or "",
            metric.market or "",
            metric.currency or "",
            metric.entry_currency or "",
            str(metric.entry_fx_to_portfolio or ""),
        ]
        for metric in position_metrics
    ]


def write_portfolio_analysis_files(
    portfolio_name: str,
    summary: PortfolioSummaryMetrics | None,
    position_metrics: list[PortfolioPositionMetrics],
    output_dir: str | Path | None = None,
) -> dict[str, Path]:
    paths = _build_output_paths(portfolio_name=portfolio_name, output_dir=output_dir)

    log_file = paths["log"]
    log_to_file(log_file, f"Portfolio Analysis | {portfolio_name}")
    log_to_file(log_file, "=" * 120)
    log_to_file(log_file, "")

    if summary is None:
        log_to_file(log_file, "No positions found for this portfolio.")
        log_rows_to_csv(paths["csv"], _build_csv_headers(), [])
        return paths

    log_to_file(log_file, "Summary")
    log_to_file(log_file, "-" * 120)
    log_to_file(log_file, f"Portfolio ID: {summary.portfolio_id}")
    log_to_file(log_file, f"Positions: {summary.positions_count}")
    log_to_file(
        log_file,
        f"Price coverage: {summary.priced_positions_count}/{summary.positions_count} ({summary.price_coverage_ratio:.2%})",
    )
    log_to_file(
        log_file, f"Total cost basis: {_format_number(summary.total_cost_basis)}"
    )
    log_to_file(
        log_file, f"Total market value: {_format_number(summary.total_market_value)}"
    )
    log_to_file(
        log_file,
        f"Total unrealized PnL: {_format_number(summary.total_unrealized_pnl)}",
    )
    log_to_file(
        log_file, f"Total realized PnL: {_format_number(summary.total_realized_pnl)}"
    )
    log_to_file(log_file, f"Total PnL: {_format_number(summary.total_pnl)}")
    log_to_file(log_file, f"Total return: {_format_percent(summary.total_return_pct)}")
    log_to_file(
        log_file,
        f"Annualized total return: {_format_percent(summary.annualized_total_return_pct)}",
    )
    log_to_file(
        log_file, f"Weighted daily move: {_format_percent(summary.weighted_change_pct)}"
    )
    log_to_file(
        log_file, f"Weighted 1W performance: {_format_percent(summary.weighted_perf_w)}"
    )
    log_to_file(
        log_file,
        f"Weighted 1M performance: {_format_percent(summary.weighted_perf_1m)}",
    )
    log_to_file(
        log_file, f"Weighted 1Y performance: {_format_percent(summary.weighted_perf_y)}"
    )
    log_to_file(
        log_file,
        f"Largest position weight: {_format_weight(summary.largest_position_weight)}",
    )
    log_to_file(
        log_file, f"Concentration HHI: {_format_number(summary.concentration_hhi)}"
    )
    log_to_file(
        log_file,
        f"Average holding days: {_format_number(summary.average_holding_days)}",
    )
    log_to_file(
        log_file,
        f"Weighted holding days: {_format_number(summary.weighted_holding_days)}",
    )
    log_to_file(
        log_file,
        f"Oldest position days: {_format_number(summary.oldest_position_days)}",
    )
    log_to_file(log_file, "")

    log_to_file(log_file, "Positions")
    log_to_file(log_file, "-" * 120)
    log_to_file(
        log_file,
        f"{'Ticker':<10} {'Company':<24} {'Days':>8} {'Cost':>12} {'Market':>12} {'TotalPnL':>12} {'Return':>10} {'AnnRet':>10} {'1W':>8} {'1M':>8} {'1Y':>8}",
    )
    log_to_file(log_file, "-" * 120)
    for metric in position_metrics:
        log_to_file(
            log_file,
            f"{metric.symbol:<10} {metric.company_name[:24]:<24} {_format_number(metric.holding_days):>8} {_format_number(metric.cost_basis_total):>12} {_format_number(metric.market_value):>12} {_format_number(metric.total_pnl):>12} {_format_percent(metric.total_return_pct):>10} {_format_percent(metric.annualized_return_pct):>10} {_format_percent(metric.last_perf_w):>8} {_format_percent(metric.last_perf_1m):>8} {_format_percent(metric.last_perf_y):>8}",
        )

    log_rows_to_csv(
        paths["csv"], _build_csv_headers(), _build_csv_rows(position_metrics)
    )
    return paths


def export_portfolio_analysis(
    tracker: "PortfolioTracker",
    output_dir: str | Path | None = None,
    include_closed: bool = False,
) -> dict[str, Path]:
    summary, position_metrics = tracker.get_summary(include_closed=include_closed)
    return write_portfolio_analysis_files(
        portfolio_name=tracker.portfolio_name,
        summary=summary,
        position_metrics=position_metrics,
        output_dir=output_dir,
    )
