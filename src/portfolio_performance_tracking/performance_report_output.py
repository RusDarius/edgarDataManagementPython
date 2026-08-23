# === AI GENERATED CODE START (GitHub Copilot - Claude Sonnet 5) ===
# Generated on: 2026-08-22
# Purpose: Write a human-readable historical performance log plus CSVs (return
#   curve + benchmark comparison) mirroring the existing
#   portofolio_integration_analysis.portfolio_analysis_output style/output layout.
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from generic_utils.log_to_files_util import log_rows_to_csv, log_to_file
from portfolio_performance_tracking.benchmark_comparator import (
    BenchmarkComparisonPoint,
)
from portfolio_performance_tracking.performance_calculator import (
    PerformancePoint,
    annualize_return_pct,
)

if TYPE_CHECKING:
    from portfolio_performance_tracking.portfolio_performance_tracker import (
        PortfolioPerformanceTracker,
    )


LOG_DIR = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\portfolio_performance_tracking"
)

# Reused for both the early-return (no history yet) and final CSV writes below
# so the two paths can never drift out of sync with each other.
PERFORMANCE_CSV_HEADERS = [
    "as_of",
    "nav",
    "period_return_pct",
    "cumulative_return_pct",
    "cumulative_contributions",
    "cumulative_withdrawals",
]
BENCHMARK_CSV_HEADERS = [
    "as_of",
    "portfolio_nav",
    "portfolio_cumulative_return_pct",
    "shadow_benchmark_value",
    "shadow_benchmark_cumulative_return_pct",
    "alpha_pct",
]
ACTIVE_CAPITAL_CSV_HEADERS = [
    "executed_at",
    "flow_type",
    "amount",
    "days_active",
    "running_active_capital",
    "note",
]


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


def _build_output_paths(
    portfolio_name: str, output_dir: str | Path | None = None
) -> dict[str, Path]:
    now = datetime.now()
    base_dir = Path(output_dir) if output_dir is not None else LOG_DIR
    portfolio_dir = (
        base_dir / _slugify_portfolio_name(portfolio_name) / now.strftime("%d_%m_%Y")
    )
    timestamp = now.strftime("%H%M%S")
    return {
        "directory": portfolio_dir,
        "log": portfolio_dir / f"performance_history_{timestamp}.log",
        "performance_csv": portfolio_dir / f"performance_history_{timestamp}.csv",
        "benchmark_csv": portfolio_dir / f"benchmark_comparison_{timestamp}.csv",
        "active_capital_csv": portfolio_dir
        / f"active_capital_breakdown_{timestamp}.csv",
    }


def _performance_csv_rows(history: list[PerformancePoint]) -> list[list[str]]:
    return [
        [
            point.as_of.isoformat(),
            str(point.nav),
            str(point.period_return_pct) if point.period_return_pct is not None else "",
            str(point.cumulative_return_pct),
            str(point.cumulative_contributions),
            str(point.cumulative_withdrawals),
        ]
        for point in history
    ]


def _benchmark_csv_rows(
    comparison: list[BenchmarkComparisonPoint],
) -> list[list[str]]:
    return [
        [
            point.as_of.isoformat(),
            str(point.portfolio_nav),
            str(point.portfolio_cumulative_return_pct),
            (
                str(point.shadow_benchmark_value)
                if point.shadow_benchmark_value is not None
                else ""
            ),
            (
                str(point.shadow_benchmark_cumulative_return_pct)
                if point.shadow_benchmark_cumulative_return_pct is not None
                else ""
            ),
            str(point.alpha_pct) if point.alpha_pct is not None else "",
        ]
        for point in comparison
    ]


def _active_capital_csv_rows(breakdown: list[dict[str, object]]) -> list[list[str]]:
    return [
        [
            row["executed_at"].isoformat(),
            str(row["flow_type"]),
            str(row["amount"]),
            str(round(row["days_active"], 2)),
            str(row["running_active_capital"]),
            row.get("note") or "",
        ]
        for row in breakdown
    ]


def _write_csv_reports(
    paths: dict[str, Path],
    history: list[PerformancePoint],
    comparison: list[BenchmarkComparisonPoint],
    active_capital_breakdown: list[dict[str, object]],
) -> None:
    log_rows_to_csv(
        paths["performance_csv"],
        PERFORMANCE_CSV_HEADERS,
        _performance_csv_rows(history),
    )
    log_rows_to_csv(
        paths["benchmark_csv"], BENCHMARK_CSV_HEADERS, _benchmark_csv_rows(comparison)
    )
    log_rows_to_csv(
        paths["active_capital_csv"],
        ACTIVE_CAPITAL_CSV_HEADERS,
        _active_capital_csv_rows(active_capital_breakdown),
    )


def export_performance_history(
    tracker: "PortfolioPerformanceTracker",
    output_dir: str | Path | None = None,
    benchmark_symbol: str | None = None,
) -> dict[str, Path]:
    paths = _build_output_paths(tracker.portfolio_name, output_dir=output_dir)

    history = tracker.get_performance_history()
    comparison = tracker.compare_to_benchmark()
    cash_flow_totals = tracker.cash_flow_summary()
    active_capital_breakdown = tracker.get_active_capital_breakdown()

    log_file = paths["log"]
    log_to_file(
        log_file, f"Portfolio Historical Performance | {tracker.portfolio_name}"
    )
    log_to_file(log_file, "=" * 120)
    log_to_file(log_file, "")

    if not history:
        log_to_file(
            log_file,
            "No NAV snapshots recorded yet. Call take_nav_snapshot()/"
            "take_nav_snapshot_from_holdings() at least twice to build a history.",
        )
        _write_csv_reports(
            paths,
            history=[],
            comparison=[],
            active_capital_breakdown=active_capital_breakdown,
        )
        return paths

    first_point = history[0]
    last_point = history[-1]
    annualized = annualize_return_pct(
        cumulative_return_pct=last_point.cumulative_return_pct,
        start=first_point.as_of,
        end=last_point.as_of,
    )

    log_to_file(log_file, "Summary")
    log_to_file(log_file, "-" * 120)
    log_to_file(log_file, f"Portfolio ID: {tracker.portfolio_id}")
    log_to_file(log_file, f"First snapshot: {first_point.as_of}")
    log_to_file(log_file, f"Latest snapshot: {last_point.as_of}")
    log_to_file(log_file, f"Latest NAV: {_format_number(last_point.nav)}")
    log_to_file(
        log_file,
        f"Cumulative return (flow-adjusted, Modified Dietz linked): {_format_percent(last_point.cumulative_return_pct)}",
    )
    log_to_file(log_file, f"Annualized return: {_format_percent(annualized)}")
    log_to_file(
        log_file,
        f"Cumulative contributions: {_format_number(last_point.cumulative_contributions)}",
    )
    log_to_file(
        log_file,
        f"Cumulative withdrawals: {_format_number(last_point.cumulative_withdrawals)}",
    )
    log_to_file(
        log_file,
        f"Total dividends received (informational): {_format_number(cash_flow_totals['total_dividends'])}",
    )
    log_to_file(
        log_file,
        f"Total realized gains booked (informational): {_format_number(cash_flow_totals['total_realized_gains'])}",
    )
    log_to_file(log_file, "")

    log_to_file(
        log_file, "Performance curve (Modified Dietz, chained across snapshots)"
    )
    log_to_file(log_file, "-" * 120)
    log_to_file(
        log_file,
        f"{'As of':<20} {'NAV':>14} {'Period Ret':>12} {'Cumulative Ret':>16}",
    )
    for point in history:
        log_to_file(
            log_file,
            f"{point.as_of.strftime('%Y-%m-%d %H:%M'):<20} {_format_number(point.nav):>14} "
            f"{_format_percent(point.period_return_pct):>12} {_format_percent(point.cumulative_return_pct):>16}",
        )
    log_to_file(log_file, "")

    if benchmark_symbol:
        log_to_file(log_file, f"Benchmark comparison vs {benchmark_symbol}")
        log_to_file(log_file, "-" * 120)
    else:
        log_to_file(log_file, "Benchmark comparison (shadow portfolio)")
        log_to_file(log_file, "-" * 120)
    priced_points = [
        point
        for point in comparison
        if point.shadow_benchmark_cumulative_return_pct is not None
    ]
    if not priced_points:
        log_to_file(
            log_file,
            "No benchmark data points -- record benchmark_price on cash flows/"
            "NAV snapshots to enable this comparison.",
        )
    else:
        log_to_file(
            log_file,
            f"{'As of':<20} {'Portfolio Ret':>14} {'Benchmark Ret':>14} {'Alpha':>10}",
        )
        for point in priced_points:
            log_to_file(
                log_file,
                f"{point.as_of.strftime('%Y-%m-%d %H:%M'):<20} "
                f"{_format_percent(point.portfolio_cumulative_return_pct):>14} "
                f"{_format_percent(point.shadow_benchmark_cumulative_return_pct):>14} "
                f"{_format_percent(point.alpha_pct):>10}",
            )
    log_to_file(log_file, "")

    log_to_file(
        log_file, "Active capital breakdown (simple-mode, per contribution/withdrawal)"
    )
    log_to_file(log_file, "-" * 120)
    if not active_capital_breakdown:
        log_to_file(log_file, "No contributions/withdrawals recorded yet.")
    else:
        log_to_file(
            log_file,
            f"{'Executed at':<20} {'Type':<14} {'Amount':>14} {'Days active':>12} {'Net active capital':>20}",
        )
        for row in active_capital_breakdown:
            log_to_file(
                log_file,
                f"{row['executed_at'].strftime('%Y-%m-%d %H:%M'):<20} {str(row['flow_type']):<14} "
                f"{_format_number(row['amount']):>14} {row['days_active']:>12.1f} "
                f"{_format_number(row['running_active_capital']):>20}",
            )

    _write_csv_reports(
        paths,
        history=history,
        comparison=comparison,
        active_capital_breakdown=active_capital_breakdown,
    )
    return paths


# === AI GENERATED CODE END ===
