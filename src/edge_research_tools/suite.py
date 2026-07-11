from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    _extract_day_label_from_database_path,
    discover_all_fields_daily_databases,
)

from .config import build_edge_research_run_context, resolve_edge_research_paths
from .dataset_builder import run_symbol_day_feature_snapshot
from .labeling import DEFAULT_FORWARD_LABEL_HORIZONS, run_forward_label_generation
from .setup_engine import run_volatility_liquidity_setup_pass


def _try_parse_day_label(day_label: str) -> datetime | None:
    try:
        return datetime.strptime(day_label, "%d_%m_%Y")
    except ValueError:
        return None


def _resolve_full_day_range(all_fields_root: str | Path) -> tuple[str, str]:
    daily_paths = discover_all_fields_daily_databases(all_fields_root=all_fields_root)
    if not daily_paths:
        raise ValueError(
            "No daily all-fields databases were discovered for the requested root."
        )
    day_labels = [_extract_day_label_from_database_path(path) for path in daily_paths]
    sorted_day_labels = sorted(
        day_labels,
        key=lambda value: _try_parse_day_label(value) or datetime.min,
    )
    return sorted_day_labels[0], sorted_day_labels[-1]


def _write_suite_report(
    path: Path,
    *,
    start_day_label: str,
    end_day_label: str,
    snapshot_result: dict[str, Any],
    label_result: dict[str, Any],
    setup_results: Sequence[dict[str, Any]],
) -> None:
    lines = [
        "# Volatility-Liquidity Edge Suite",
        "",
        "## Why Run This",
        "",
        "This suite runs the first end-to-end edge research chain: snapshot build, forward labels, and the first volatility/liquidity setup passes.",
        "It is the fastest way to move from raw daily all-fields exports into grouped, risk-aware candidate summaries.",
        "",
        "## How To Run",
        "",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        f"python -m run_edge_research_tools suite --start-day-label {start_day_label} --end-day-label {end_day_label} --min-market-cap-usd 1000000000",
        "```",
        "",
        "To use the full oldest-to-latest available daily range automatically:",
        "```powershell",
        "$env:PYTHONPATH='src;.'",
        "python -m run_edge_research_tools suite --use-full-range --min-market-cap-usd 1000000000",
        "```",
        "",
        "## Output Meaning",
        "",
        "- The snapshot output contains the reusable symbol-day feature store.",
        "- The label output adds future outcome tables inside that snapshot DB.",
        "- Each setup pass output ranks grouped setup quality and the best recurring names inside that scope.",
        "",
        "## Run Summary",
        "",
        f"- start_day_label: {start_day_label}",
        f"- end_day_label: {end_day_label}",
        f"- snapshot_database_path: `{snapshot_result['database_path']}`",
        f"- snapshot_report: `{snapshot_result['report_md']}`",
        f"- label_report: `{label_result['report_md']}`",
        "",
        "## Setup Pass Outputs",
        "",
    ]
    for result in setup_results:
        lines.append(
            "- "
            f"output_dir={result['output_dir']} | report={result['report_md']} | summary_csv={result['summary_csv_path']} | best_names_csv={result['best_names_csv_path']}"
        )
    lines.extend(
        [
            "",
            "## Suggested Next Runs",
            "",
            "- Use the universe-wide output first to confirm whether the setup family has any broad edge.",
            "- Use the industry grouped output next to see whether the edge is concentrated in specific groups.",
            "- Then rerun the setup pass with `--symbols` or `--custom-group-csv` for peers, bespoke lanes, or single-name history.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_volatility_liquidity_edge_suite(
    *,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    use_full_range: bool = False,
    all_fields_root: str | Path | None = None,
    taxonomy_root: str | Path | None = None,
    output_root: str | Path | None = None,
    primary_only: bool = True,
    min_market_cap_usd: float | None = None,
    horizons: Iterable[int] = DEFAULT_FORWARD_LABEL_HORIZONS,
    target_pct: float = 10.0,
    stop_pct: float = 7.0,
    ranking_horizon: int | None = None,
    groupings: Sequence[str] = ("universe", "industry"),
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
) -> dict[str, Any]:
    paths = resolve_edge_research_paths(
        all_fields_root=all_fields_root,
        taxonomy_root=taxonomy_root,
        output_root=output_root,
    )
    resolved_start_day_label = start_day_label
    resolved_end_day_label = end_day_label
    if use_full_range or not resolved_start_day_label or not resolved_end_day_label:
        resolved_start_day_label, resolved_end_day_label = _resolve_full_day_range(
            paths.all_fields_root
        )

    snapshot_result = run_symbol_day_feature_snapshot(
        start_day_label=resolved_start_day_label,
        end_day_label=resolved_end_day_label,
        all_fields_root=paths.all_fields_root,
        taxonomy_root=paths.taxonomy_root,
        output_root=paths.output_root,
        primary_only=primary_only,
        min_market_cap_usd=min_market_cap_usd,
        duckdb_threads=duckdb_threads,
        memory_limit_gb=memory_limit_gb,
    )
    label_result = run_forward_label_generation(
        snapshot_database_path=snapshot_result["database_path"],
        horizons=horizons,
        target_pct=target_pct,
        stop_pct=stop_pct,
        duckdb_threads=duckdb_threads,
        memory_limit_gb=memory_limit_gb,
    )

    setup_results: list[dict[str, Any]] = []
    for grouping in groupings:
        setup_results.append(
            run_volatility_liquidity_setup_pass(
                snapshot_database_path=snapshot_result["database_path"],
                group_by=grouping,
                ranking_horizon=ranking_horizon,
                output_root=paths.output_root,
            )
        )

    context = build_edge_research_run_context(
        prefix="edge_suite",
        output_root=paths.output_root,
    )
    suite_report = context.output_dir / "volatility_liquidity_edge_suite_report.md"
    suite_manifest = (
        context.output_dir / "volatility_liquidity_edge_suite_manifest.json"
    )
    _write_suite_report(
        suite_report,
        start_day_label=resolved_start_day_label,
        end_day_label=resolved_end_day_label,
        snapshot_result=snapshot_result,
        label_result=label_result,
        setup_results=setup_results,
    )
    suite_manifest.write_text(
        json.dumps(
            {
                "start_day_label": resolved_start_day_label,
                "end_day_label": resolved_end_day_label,
                "snapshot_result": snapshot_result,
                "label_result": label_result,
                "setup_results": setup_results,
                "suite_report": suite_report.as_posix(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": context.output_dir.as_posix(),
        "suite_report": suite_report.as_posix(),
        "suite_manifest": suite_manifest.as_posix(),
        "snapshot_result": snapshot_result,
        "label_result": label_result,
        "setup_results": setup_results,
    }
