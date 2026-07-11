from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from data_analysis_scripts.trading_view_backwards_execution_backtest import (
    DEFAULT_FRESH_RANK_THRESHOLD,
    ExecutionBacktestSummary,
    ExecutionRule,
    ExecutionTrade,
    run_backwards_execution_backtest,
)
from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
    run_backwards_prediction_sparse_weekly_analysis,
)
from data_analysis_scripts.trading_view_backwards_profile_cohort_attribution import (
    AttributionMethod,
    format_profile_cohort_markdown_table,
    run_backwards_profile_cohort_attribution,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_SUITE_PATH = (
    PROJECT_ROOT
    / "config"
    / "move_prediction_profiles"
    / "suites"
    / "active_manager_v3.json"
)
DEFAULT_EXISTING_BACKWARDS_DATABASE_PATH = (
    PROJECT_ROOT
    / "logs"
    / "tradingview_analysis"
    / "prediction_analysis"
    / "duckdb_runs"
    / "backwards_prediction_analysis"
    / "runs"
    / "backwards_prediction_analysis_20260625_1621_utc_b00e6c1d"
    / "backwards_prediction_analysis.duckdb"
)

FULL_HISTORY_WEEKS_PROFILES: tuple[str, ...] = (
    "breakout_long_v1",
    "early_momentum_inflection_v1",
    "fragility_short",
    "forward_edge_active_v2",
    "quality_value_compounder",
    "durable_value_compounder_v1",
    "asymmetric_value",
    "value_recovery_v3",
)

DEFAULT_EXISTING_EXECUTION_PROFILES: tuple[str, ...] = (
    "forward_edge_active_v2",
    "breakout_long_v1",
)

DEFAULT_COHORT_METHODS: tuple[AttributionMethod, ...] = (
    "inclusion_entry",
    "hold_until_rank_exit",
    "fresh_inclusion",
)

EXISTING_SPARSE_EXECUTION_RULES: tuple[ExecutionRule, ...] = (
    ExecutionRule(
        rule_id="top50_exit500",
        entry_rank_threshold=50,
        exit_rank_threshold=500,
        min_entry_score=0.0,
    ),
    ExecutionRule(
        rule_id="top50_exit500_score2neg",
        entry_rank_threshold=50,
        exit_rank_threshold=500,
        min_entry_score=0.0,
        exit_score_below=0.0,
        exit_score_below_consecutive_anchors=2,
    ),
    ExecutionRule(
        rule_id="top100_exit500",
        entry_rank_threshold=100,
        exit_rank_threshold=500,
        min_entry_score=0.0,
    ),
    ExecutionRule(
        rule_id="top100_fresh250_exit500",
        entry_rank_threshold=100,
        exit_rank_threshold=500,
        require_fresh_inclusion=True,
        fresh_rank_threshold=250,
        min_entry_score=0.0,
    ),
    ExecutionRule(
        rule_id="top100_exit500_score2neg",
        entry_rank_threshold=100,
        exit_rank_threshold=500,
        min_entry_score=0.0,
        exit_score_below=0.0,
        exit_score_below_consecutive_anchors=2,
    ),
    ExecutionRule(
        rule_id="top100_exit500_age15_no5pct",
        entry_rank_threshold=100,
        exit_rank_threshold=500,
        min_entry_score=0.0,
        max_holding_days_without_progress=15,
        min_progress_return_pct=5.0,
    ),
    ExecutionRule(
        rule_id="top50_exit500_score2neg_age15",
        entry_rank_threshold=50,
        exit_rank_threshold=500,
        min_entry_score=0.0,
        exit_score_below=0.0,
        exit_score_below_consecutive_anchors=2,
        max_holding_days_without_progress=15,
        min_progress_return_pct=5.0,
    ),
    ExecutionRule(
        rule_id="top100_exit250_score2neg_age10",
        entry_rank_threshold=100,
        exit_rank_threshold=250,
        min_entry_score=0.0,
        exit_score_below=0.0,
        exit_score_below_consecutive_anchors=2,
        max_holding_days_without_progress=10,
        min_progress_return_pct=3.0,
    ),
)


@dataclass(frozen=True)
class ExistingBackwardsExecutionExamplesConfig:
    database_path: str | Path = DEFAULT_EXISTING_BACKWARDS_DATABASE_PATH
    include_profiles: tuple[str, ...] = FULL_HISTORY_WEEKS_PROFILES
    execution_profiles: tuple[str, ...] = DEFAULT_EXISTING_EXECUTION_PROFILES
    horizon_name: str = "weeks"
    rank_scope: str = "global"
    top_n_values: tuple[int, ...] = (50, 100)
    exit_rank_threshold: int = 1000
    fresh_rank_threshold: int = DEFAULT_FRESH_RANK_THRESHOLD
    output_dir: str | Path | None = None
    execution_rules: tuple[ExecutionRule, ...] = EXISTING_SPARSE_EXECUTION_RULES
    include_source_context: bool = False
    max_observation_profile_rank: int | None = 750
    reuse_existing_cohort_outputs: bool = True
    min_report_trades: int = 10


@dataclass(frozen=True)
class ExecutionBacktestSuiteConfig:
    profile_suite_path: str | Path = DEFAULT_PROFILE_SUITE_PATH
    include_profiles: tuple[str, ...] = FULL_HISTORY_WEEKS_PROFILES
    horizon_name: str = "weeks"
    runs_per_week: int = 5
    selection: str = "even_spread"
    min_scan_data_count: int = 3000
    anchor_min_scan_data_count: int | None = None
    iso_year: int | None = 2026
    start_week: int | None = 13
    end_week: int | None = None
    duckdb_threads: int | None = None
    memory_gb: float | None = None
    top_n_values: tuple[int, ...] = (50, 100)
    rank_scope: str = "min1bil"
    exit_rank_threshold: int = 1000
    fresh_rank_threshold: int = DEFAULT_FRESH_RANK_THRESHOLD
    output_dir: str | Path | None = None
    export_parquet: bool = False
    execution_rules: tuple[ExecutionRule, ...] = field(
        default_factory=lambda: (
            ExecutionRule(
                rule_id="top50_fresh250_exit1000_relvol15",
                entry_rank_threshold=50,
                exit_rank_threshold=1000,
                require_fresh_inclusion=True,
                fresh_rank_threshold=250,
                min_entry_score=0.0,
                min_entry_relative_volume=1.5,
            ),
            ExecutionRule(
                rule_id="top100_fresh250_exit1000_relvol15",
                entry_rank_threshold=100,
                exit_rank_threshold=1000,
                require_fresh_inclusion=True,
                fresh_rank_threshold=250,
                min_entry_score=0.0,
                min_entry_relative_volume=1.5,
            ),
            ExecutionRule(
                rule_id="top100_exit1000_score2neg_age15",
                entry_rank_threshold=100,
                exit_rank_threshold=1000,
                min_entry_score=0.0,
                exit_score_below=0.0,
                exit_score_below_consecutive_anchors=2,
                max_holding_days_without_progress=15,
                min_progress_return_pct=5.0,
            ),
        )
    )


def _safe_path(value: str | Path | None) -> str | None:
    if value is None:
        return None
    return Path(value).as_posix()


def _cohort_output_dir(base_dir: Path, *, top_n: int) -> Path:
    return base_dir / "cohorts" / f"top_{top_n}__exit_1000__fresh_250"


def _format_cohort_tables(cohort_result: dict[str, Any]) -> dict[str, str]:
    summaries = cohort_result["summaries"]
    return {
        "inclusion_entry": format_profile_cohort_markdown_table(
            summaries,
            method="inclusion_entry",
        ),
        "hold_until_rank_exit": format_profile_cohort_markdown_table(
            summaries,
            method="hold_until_rank_exit",
        ),
        "fresh_inclusion": format_profile_cohort_markdown_table(
            summaries,
            method="fresh_inclusion",
        ),
    }


def _format_pct(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}%"


def _format_float(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def _summarize_trades_by_rule_profile(
    trades: Sequence[ExecutionTrade],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[ExecutionTrade]] = {}
    for trade in trades:
        grouped.setdefault((trade.rule_id, trade.profile_name), []).append(trade)

    rows: list[dict[str, Any]] = []
    for (rule_id, profile_name), group in sorted(grouped.items()):
        returns = [trade.return_pct for trade in group]
        holding_days = [trade.holding_days for trade in group]
        ordered = sorted(returns)
        mid = len(ordered) // 2
        if not ordered:
            median_return = None
        elif len(ordered) % 2 == 1:
            median_return = ordered[mid]
        else:
            median_return = (ordered[mid - 1] + ordered[mid]) / 2.0
        exit_counts: dict[str, int] = {}
        for trade in group:
            exit_counts[trade.exit_reason] = exit_counts.get(trade.exit_reason, 0) + 1
        rows.append(
            {
                "rule_id": rule_id,
                "profile_name": profile_name,
                "trade_count": len(group),
                "avg_return_pct": sum(returns) / len(returns) if returns else None,
                "median_return_pct": median_return,
                "win_rate": (
                    sum(1 for value in returns if value > 0) / len(returns)
                    if returns
                    else None
                ),
                "avg_holding_days": (
                    sum(holding_days) / len(holding_days) if holding_days else None
                ),
                "exit_reason_counts_json": json.dumps(exit_counts, sort_keys=True),
            }
        )
    return rows


def _write_profile_rule_summary_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    headers = [
        "rule_id",
        "profile_name",
        "trade_count",
        "avg_return_pct",
        "median_return_pct",
        "win_rate",
        "avg_holding_days",
        "exit_reason_counts_json",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        import csv

        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            payload = dict(row)
            for key in (
                "avg_return_pct",
                "median_return_pct",
                "win_rate",
                "avg_holding_days",
            ):
                if payload.get(key) is not None:
                    payload[key] = round(float(payload[key]), 4)
            writer.writerow(payload)


def _write_existing_execution_report(
    path: Path,
    *,
    database_path: Path,
    execution_result: dict[str, Any],
    profile_rule_rows: Sequence[dict[str, Any]],
    cohort_results: dict[str, dict[str, Any]],
    min_report_trades: int,
) -> None:
    summaries: list[ExecutionBacktestSummary] = list(execution_result["summaries"])
    eligible_summaries = [
        summary for summary in summaries if summary.trade_count >= min_report_trades
    ]
    best_by_median = sorted(
        eligible_summaries,
        key=lambda summary: (
            summary.median_return_pct
            if summary.median_return_pct is not None
            else float("-inf")
        ),
        reverse=True,
    )[:5]
    best_profile_rows = sorted(
        [
            row
            for row in profile_rule_rows
            if int(row["trade_count"]) >= max(3, min_report_trades // 2)
        ],
        key=lambda row: (
            row["median_return_pct"]
            if row["median_return_pct"] is not None
            else float("-inf")
        ),
        reverse=True,
    )[:10]

    lines = [
        "# Existing backwards execution examples",
        "",
        f"Database: `{database_path.as_posix()}`",
        "",
        "This pass uses the existing sparse backwards anchors, so treat timing conclusions as directional rather than final.",
        "It intentionally uses only fields already present in the backwards database by default: rank, score, close, and anchor dates.",
        "The execution rules use global profile ranks for this immediate pass; the reused cohort outputs remain available as min-1B comparison baselines.",
        "The default immediate execution examples run on `forward_edge_active_v2` and `breakout_long_v1` first; pass a wider `execution_profiles` tuple to the config for a broader rerun.",
        "The default existing-data run also limits loaded observations to the global top 750 profile ranks, which keeps this immediate example pass tractable while preserving room for exit-500 checks.",
        "",
        "## Rule Summary",
        "",
        "| Rule | Trades | Median | Avg | Win rate | Avg hold days | Exit reasons |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for summary in summaries:
        lines.append(
            "| "
            f"{summary.rule_id} | "
            f"{summary.trade_count} | "
            f"{_format_pct(summary.median_return_pct)} | "
            f"{_format_pct(summary.avg_return_pct)} | "
            f"{_format_pct(summary.win_rate * 100 if summary.win_rate is not None else None)} | "
            f"{_format_float(summary.avg_holding_days)} | "
            f"`{json.dumps(summary.exit_reason_counts, sort_keys=True)}` |"
        )

    lines.extend(["", "## Small Conclusions", ""])
    if best_by_median:
        for summary in best_by_median:
            lines.append(
                "- "
                f"`{summary.rule_id}` is among the better sparse-anchor candidates: "
                f"median {_format_pct(summary.median_return_pct)}, "
                f"avg {_format_pct(summary.avg_return_pct)}, "
                f"win rate {_format_pct(summary.win_rate * 100 if summary.win_rate is not None else None)}, "
                f"trades {summary.trade_count}."
            )
    else:
        lines.append(
            f"- No rule reached the minimum `{min_report_trades}` trades threshold; inspect the CSVs before trusting rule-level medians."
        )
    lines.append(
        "- Compare fresh-entry and relative-volume rules against plain top-N rules. If fresh/volume filters improve median but sharply reduce trade count, keep them as confirmation filters rather than standalone systems."
    )
    lines.append(
        "- Exit rules that concentrate exits in `window_end` are not really testing exits yet; denser anchors should be used before promoting those rules."
    )

    lines.extend(
        [
            "",
            "## Best Rule/Profile Pairs",
            "",
            "| Rule | Profile | Trades | Median | Avg | Win rate | Avg hold days |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in best_profile_rows:
        lines.append(
            "| "
            f"{row['rule_id']} | "
            f"{row['profile_name']} | "
            f"{row['trade_count']} | "
            f"{_format_pct(row['median_return_pct'])} | "
            f"{_format_pct(row['avg_return_pct'])} | "
            f"{_format_pct(row['win_rate'] * 100 if row['win_rate'] is not None else None)} | "
            f"{_format_float(row['avg_holding_days'])} |"
        )

    lines.extend(["", "## Output Locations", ""])
    lines.append(f"- Rule summary CSV: `{execution_result['summary_csv']}`")
    lines.append(f"- Execution manifest: `{execution_result['manifest_path']}`")
    for rule_id, path_value in execution_result["trade_csv_paths"].items():
        lines.append(f"- Positions CSV `{rule_id}`: `{path_value}`")
    for key, cohort in cohort_results.items():
        lines.append(f"- Cohort summary `{key}`: `{cohort['summary_csv']}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_execution_backtest_suite(
    config: ExecutionBacktestSuiteConfig | None = None,
) -> dict[str, Any]:
    """Run the first execution-backtest pass from existing move-prediction DuckDB runs.

    The suite intentionally stays trade-level: it densifies backwards anchors,
    writes honest cohort baselines, then writes per-rule ``positions`` CSVs.
    Portfolio allocation and equity curves are deliberately outside this runner.
    """
    started = time.perf_counter()
    resolved_config = config or ExecutionBacktestSuiteConfig()
    profile_suite_path = Path(resolved_config.profile_suite_path)

    backwards_result = run_backwards_prediction_sparse_weekly_analysis(
        profile_suite_path=profile_suite_path,
        include_profiles=resolved_config.include_profiles,
        runs_per_week=resolved_config.runs_per_week,
        selection=resolved_config.selection,
        min_scan_data_count=resolved_config.min_scan_data_count,
        anchor_min_scan_data_count=resolved_config.anchor_min_scan_data_count,
        iso_year=resolved_config.iso_year,
        start_week=resolved_config.start_week,
        end_week=resolved_config.end_week,
        output_dir=resolved_config.output_dir,
        export_parquet=resolved_config.export_parquet,
        duckdb_threads=resolved_config.duckdb_threads,
        memory_gb=resolved_config.memory_gb,
    )

    database_path = Path(backwards_result["database_path"])
    suite_output_dir = database_path.parent / "execution_suite"
    suite_output_dir.mkdir(parents=True, exist_ok=True)

    cohort_results: dict[str, dict[str, Any]] = {}
    cohort_tables: dict[str, dict[str, str]] = {}
    for top_n in resolved_config.top_n_values:
        cohort_result = run_backwards_profile_cohort_attribution(
            database_path=database_path,
            output_dir=_cohort_output_dir(suite_output_dir, top_n=top_n),
            horizon_name=resolved_config.horizon_name,
            profile_names=resolved_config.include_profiles,
            top_n=top_n,
            rank_scope=resolved_config.rank_scope,  # type: ignore[arg-type]
            exit_rank_threshold=resolved_config.exit_rank_threshold,
            fresh_rank_threshold=resolved_config.fresh_rank_threshold,
            methods=DEFAULT_COHORT_METHODS,
        )
        key = f"top_{top_n}"
        cohort_results[key] = {
            "output_dir": cohort_result["output_dir"],
            "symbol_csv": cohort_result["symbol_csv"],
            "summary_csv": cohort_result["summary_csv"],
            "summary_json": cohort_result["summary_json"],
            "elapsed_seconds": cohort_result["elapsed_seconds"],
        }
        cohort_tables[key] = _format_cohort_tables(cohort_result)

    execution_result = run_backwards_execution_backtest(
        database_path=database_path,
        output_dir=suite_output_dir / "execution_rules",
        horizon_name=resolved_config.horizon_name,
        profile_names=resolved_config.include_profiles,
        rules=resolved_config.execution_rules,
        rank_scope=resolved_config.rank_scope,  # type: ignore[arg-type]
        include_source_context=resolved_config.include_source_context,
        max_profile_rank=resolved_config.max_observation_profile_rank,
    )

    manifest_path = suite_output_dir / "execution_backtest_suite_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "config": {
                    **asdict(resolved_config),
                    "profile_suite_path": profile_suite_path.as_posix(),
                    "output_dir": _safe_path(resolved_config.output_dir),
                    "execution_rules": [
                        asdict(rule) for rule in resolved_config.execution_rules
                    ],
                },
                "backwards_result": {
                    key: value
                    for key, value in backwards_result.items()
                    if key not in {"anchor_plan"}
                },
                "cohorts": cohort_results,
                "execution": {
                    "output_dir": execution_result["output_dir"],
                    "summary_csv": execution_result["summary_csv"],
                    "manifest_path": execution_result["manifest_path"],
                    "trade_csv_paths": execution_result["trade_csv_paths"],
                    "elapsed_seconds": execution_result["elapsed_seconds"],
                },
                "elapsed_seconds": round(time.perf_counter() - started, 4),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    return {
        "database_path": database_path.as_posix(),
        "output_dir": suite_output_dir.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "backwards_result": backwards_result,
        "cohorts": cohort_results,
        "cohort_tables": cohort_tables,
        "execution": execution_result,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }


def run_existing_backwards_execution_examples(
    config: ExistingBackwardsExecutionExamplesConfig | None = None,
) -> dict[str, Any]:
    """Run entry/exit examples against the existing sparse backwards analysis DB."""
    started = time.perf_counter()
    resolved_config = config or ExistingBackwardsExecutionExamplesConfig()
    database_path = Path(resolved_config.database_path)
    if not database_path.exists():
        raise FileNotFoundError(
            f"Existing backwards database not found: {database_path.as_posix()}"
        )
    output_dir = (
        Path(resolved_config.output_dir)
        if resolved_config.output_dir is not None
        else database_path.parent
        / "execution_examples"
        / "sparse_existing_entry_exit_examples"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    cohort_results: dict[str, dict[str, Any]] = {}
    cohort_tables: dict[str, dict[str, str]] = {}
    for top_n in resolved_config.top_n_values:
        cohort_output_dir = output_dir / "cohorts" / f"top_{top_n}__execution_methods"
        existing_cohort_paths = {
            "output_dir": cohort_output_dir.as_posix(),
            "symbol_csv": (
                cohort_output_dir / "symbol_window_attribution__weeks.csv"
            ).as_posix(),
            "summary_csv": (
                cohort_output_dir / "profile_cohort_summary__weeks.csv"
            ).as_posix(),
            "summary_json": (
                cohort_output_dir / "profile_cohort_summary__weeks.json"
            ).as_posix(),
            "elapsed_seconds": 0.0,
            "reused_existing": True,
        }
        if resolved_config.reuse_existing_cohort_outputs and all(
            Path(existing_cohort_paths[key]).exists()
            for key in ("symbol_csv", "summary_csv", "summary_json")
        ):
            key = f"top_{top_n}"
            cohort_results[key] = existing_cohort_paths
            cohort_tables[key] = {}
            continue

        cohort_result = run_backwards_profile_cohort_attribution(
            database_path=database_path,
            output_dir=cohort_output_dir,
            horizon_name=resolved_config.horizon_name,
            profile_names=resolved_config.include_profiles,
            top_n=top_n,
            rank_scope=resolved_config.rank_scope,  # type: ignore[arg-type]
            exit_rank_threshold=resolved_config.exit_rank_threshold,
            fresh_rank_threshold=resolved_config.fresh_rank_threshold,
            methods=DEFAULT_COHORT_METHODS,
        )
        key = f"top_{top_n}"
        cohort_results[key] = {
            "output_dir": cohort_result["output_dir"],
            "symbol_csv": cohort_result["symbol_csv"],
            "summary_csv": cohort_result["summary_csv"],
            "summary_json": cohort_result["summary_json"],
            "elapsed_seconds": cohort_result["elapsed_seconds"],
            "reused_existing": False,
        }
        cohort_tables[key] = _format_cohort_tables(cohort_result)

    execution_result = run_backwards_execution_backtest(
        database_path=database_path,
        output_dir=output_dir / "execution_rules",
        horizon_name=resolved_config.horizon_name,
        profile_names=resolved_config.execution_profiles,
        rules=resolved_config.execution_rules,
        rank_scope=resolved_config.rank_scope,  # type: ignore[arg-type]
        include_source_context=resolved_config.include_source_context,
        max_profile_rank=resolved_config.max_observation_profile_rank,
    )

    profile_rule_rows = _summarize_trades_by_rule_profile(execution_result["trades"])
    profile_rule_summary_csv = output_dir / "execution_rule_profile_summary.csv"
    _write_profile_rule_summary_csv(profile_rule_summary_csv, profile_rule_rows)

    report_path = output_dir / "execution_entry_exit_guidance.md"
    _write_existing_execution_report(
        report_path,
        database_path=database_path,
        execution_result=execution_result,
        profile_rule_rows=profile_rule_rows,
        cohort_results=cohort_results,
        min_report_trades=resolved_config.min_report_trades,
    )

    manifest_path = output_dir / "existing_backwards_execution_examples_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "config": {
                    **asdict(resolved_config),
                    "database_path": database_path.as_posix(),
                    "output_dir": output_dir.as_posix(),
                    "execution_rules": [
                        asdict(rule) for rule in resolved_config.execution_rules
                    ],
                },
                "cohorts": cohort_results,
                "execution": {
                    "output_dir": execution_result["output_dir"],
                    "summary_csv": execution_result["summary_csv"],
                    "manifest_path": execution_result["manifest_path"],
                    "trade_csv_paths": execution_result["trade_csv_paths"],
                    "profile_rule_summary_csv": profile_rule_summary_csv.as_posix(),
                    "guidance_report": report_path.as_posix(),
                    "elapsed_seconds": execution_result["elapsed_seconds"],
                },
                "elapsed_seconds": round(time.perf_counter() - started, 4),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    return {
        "database_path": database_path.as_posix(),
        "output_dir": output_dir.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "guidance_report": report_path.as_posix(),
        "cohorts": cohort_results,
        "cohort_tables": cohort_tables,
        "execution": {
            **execution_result,
            "profile_rule_summary_csv": profile_rule_summary_csv.as_posix(),
            "guidance_report": report_path.as_posix(),
        },
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }


__all__ = [
    "DEFAULT_EXISTING_BACKWARDS_DATABASE_PATH",
    "ExecutionBacktestSuiteConfig",
    "ExistingBackwardsExecutionExamplesConfig",
    "FULL_HISTORY_WEEKS_PROFILES",
    "run_existing_backwards_execution_examples",
    "run_execution_backtest_suite",
]
