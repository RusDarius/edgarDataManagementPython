#!/usr/bin/env python3
"""Resume interrupted scan-period rolling-window stability for an existing run.

Skips windows that already have period_field_performance_patterns.parquet,
deletes incomplete partial window folders, reruns missing windows, aggregates,
and writes _scan_period_close_forward_tracking.json when period_total exists.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from constants.trading_view_constants import PREFERRED_MARKETS
from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    PERIOD_PERFORMANCE_FIELD,
    aggregate_all_fields_pattern_summaries,
    analyze_period_pooled_performance_patterns,
    discover_all_fields_daily_databases,
    market_cap_basic_universe_filter,
    preferred_markets_universe_filter,
    resolve_scan_period_all_field_predictors,
    serialize_all_fields_universe_filter,
    normalize_all_fields_universe_filter,
    _extract_day_label_from_database_path,
    _try_parse_day_label,
)


def _build_rolling_window_specs(
    *,
    start_day_label: str,
    end_day_label: str,
    all_fields_root: Path,
    rolling_window_days: int = 7,
    rolling_window_step_days: int = 3,
) -> list[tuple[str, str, list[Path]]]:
    resolved_paths = discover_all_fields_daily_databases(
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )
    parsed_paths: list[tuple[date, Path]] = []
    for path in resolved_paths:
        parsed_day = _try_parse_day_label(_extract_day_label_from_database_path(path))
        if parsed_day is None:
            continue
        parsed_paths.append((parsed_day, path))
    parsed_paths.sort(key=lambda item: item[0])
    if len(parsed_paths) < 2:
        return []

    window_span = max(2, int(rolling_window_days))
    step_span = max(1, int(rolling_window_step_days))
    rolling_windows: list[tuple[str, str, list[Path]]] = []
    cursor = 0
    while cursor < len(parsed_paths):
        start_index = cursor
        end_index = min(len(parsed_paths) - 1, start_index + window_span - 1)
        if end_index <= start_index:
            break
        window_subset = [path for _, path in parsed_paths[start_index : end_index + 1]]
        window_start_label = _extract_day_label_from_database_path(window_subset[0])
        window_end_label = _extract_day_label_from_database_path(window_subset[-1])
        rolling_windows.append((window_start_label, window_end_label, window_subset))
        if end_index >= len(parsed_paths) - 1:
            break
        next_day = parsed_paths[start_index][0] + timedelta(days=step_span)
        next_index = start_index + 1
        while next_index < len(parsed_paths) and parsed_paths[next_index][0] < next_day:
            next_index += 1
        cursor = min(next_index, len(parsed_paths) - 1)
    return rolling_windows


def _remove_incomplete_window_dirs(rolling_root: Path) -> list[str]:
    removed: list[str] = []
    if not rolling_root.exists():
        return removed
    for window_dir in rolling_root.iterdir():
        if not window_dir.is_dir():
            continue
        parquet_path = window_dir / "period_field_performance_patterns.parquet"
        if parquet_path.exists():
            continue
        shutil.rmtree(window_dir)
        removed.append(window_dir.name)
    return removed


def _write_tracking_json(
    *,
    run_root: Path,
    start_day_label: str,
    end_day_label: str,
    run_lifecycle_id: str,
    field_universe: dict[str, Any],
    summary_paths: list[Path],
    aggregate_exports: dict[str, str] | None,
    universe_filter: dict[str, Any],
) -> Path:
    tracking_id = run_root.name
    period_overview_path = run_root / "period_total" / "_period_pattern_overview.json"
    period_total_result: dict[str, Any] = {}
    if period_overview_path.exists():
        period_total_result = json.loads(period_overview_path.read_text(encoding="utf-8"))

    progression_overview_path = run_root / "progression" / "_period_progression_overview.json"
    progression_result: dict[str, Any] = {}
    if progression_overview_path.exists():
        progression_result = json.loads(progression_overview_path.read_text(encoding="utf-8"))

    payload: dict[str, Any] = {
        "tracking_id": tracking_id,
        "run_lifecycle_id": run_lifecycle_id,
        "output_dir": run_root.as_posix(),
        "start_day_label": start_day_label,
        "end_day_label": end_day_label,
        "performance_lens": "period_total_return",
        "predictor_anchor": "period_start",
        "performance_target": PERIOD_PERFORMANCE_FIELD,
        "universe_filter": universe_filter,
        "scan_period_lens": {
            "database_count": field_universe.get("database_count"),
            "shared_column_count": field_universe.get("shared_column_count"),
            "predictor_field_count": len(field_universe.get("predictor_fields") or []),
            "predictor_fields_skipped_count": len(
                field_universe.get("predictor_fields_skipped") or []
            ),
        },
        "field_universe": field_universe,
        "min_runs_for_stability": 3,
        "require_sign_consistency": 0.6,
        "period_total_result": {
            "analysis_id": period_total_result.get("analysis_id"),
            "output_dir": period_total_result.get("output_dir"),
            "summary_parquet": period_total_result.get("summary_parquet"),
            "rows_emitted": period_total_result.get("rows_emitted"),
            "period_returns_result": period_total_result.get("period_returns_result"),
            "period_input_result": period_total_result.get("period_input_result"),
        },
        "progression_result": progression_result,
        "rolling_window_result": {
            "window_count": len(summary_paths),
            "summary_paths": [path.as_posix() for path in summary_paths],
        },
    }
    if aggregate_exports:
        payload["stability_aggregate"] = aggregate_exports

    tracking_path = run_root / "_scan_period_close_forward_tracking.json"
    tracking_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return tracking_path


def resume_scan_period_rolling(
    *,
    run_root: Path,
    start_day_label: str,
    end_day_label: str,
    run_lifecycle_id: str,
    all_fields_root: Path,
    duckdb_threads: int = 6,
    field_batch_size: int = 120,
    duckdb_memory_limit: str = "10GB",
    rolling_window_days: int = 7,
    rolling_window_step_days: int = 3,
    min_runs_for_stability: int = 3,
    require_sign_consistency: float = 0.6,
) -> dict[str, Any]:
    run_root = run_root.resolve()
    rolling_root = run_root / "rolling_windows"
    rolling_root.mkdir(parents=True, exist_ok=True)

    universe_filter_input = {
        **market_cap_basic_universe_filter(500_000_000),
        **preferred_markets_universe_filter(PREFERRED_MARKETS),
    }
    serialized_universe_filter = serialize_all_fields_universe_filter(
        normalize_all_fields_universe_filter(universe_filter_input)
    )

    removed = _remove_incomplete_window_dirs(rolling_root)
    if removed:
        print(f"[resume] removed incomplete window dirs: {', '.join(removed)}")

    field_universe = resolve_scan_period_all_field_predictors(
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        all_fields_root=all_fields_root,
        exclude_perf_from_predictors=True,
    )
    predictors = list(field_universe["predictor_fields"])
    print(
        f"[resume] predictors={len(predictors):,} scans={field_universe['database_count']}"
    )

    window_specs = _build_rolling_window_specs(
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        all_fields_root=all_fields_root,
        rolling_window_days=rolling_window_days,
        rolling_window_step_days=rolling_window_step_days,
    )
    print(f"[resume] expected rolling windows={len(window_specs)}")

    completed_results: list[dict[str, Any]] = []
    for index, (window_start, window_end, window_paths) in enumerate(window_specs, start=1):
        output_dir = rolling_root / f"{window_start}_to_{window_end}"
        parquet_path = output_dir / "period_field_performance_patterns.parquet"
        if parquet_path.exists():
            print(
                f"[resume] [{index}/{len(window_specs)}] skip {window_start}..{window_end} (done)"
            )
            continue

        print(
            f"[resume] [{index}/{len(window_specs)}] run {window_start}..{window_end} "
            f"scans={len(window_paths)}"
        )
        window_result = analyze_period_pooled_performance_patterns(
            input_paths=window_paths,
            all_fields_root=all_fields_root,
            start_day_label=window_start,
            end_day_label=window_end,
            output_dir=output_dir,
            include_predictor_fields=predictors,
            universe_filter=universe_filter_input,
            run_lifecycle_id=run_lifecycle_id,
            duckdb_threads=duckdb_threads,
            field_batch_size=field_batch_size,
            duckdb_memory_limit=duckdb_memory_limit,
            write_exports=True,
            synthetic_run_id=f"period_pooled_{window_start}_{window_end}_{run_lifecycle_id}",
        )
        completed_results.append(window_result)
        print(
            f"[resume] [{index}/{len(window_specs)}] finished rows="
            f"{window_result.get('rows_emitted')}"
        )

    summary_paths = sorted(rolling_root.glob("*/period_field_performance_patterns.parquet"))
    print(f"[resume] aggregating {len(summary_paths)} window summaries")
    aggregate_result = aggregate_all_fields_pattern_summaries(
        per_run_summary_paths=summary_paths,
        output_dir=run_root / "aggregates",
        min_runs_for_stability=min_runs_for_stability,
        require_sign_consistency=require_sign_consistency,
        run_lifecycle_id=run_lifecycle_id,
        nest_output_dir=False,
    )
    aggregate_exports = aggregate_result.exports

    tracking_path = _write_tracking_json(
        run_root=run_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        run_lifecycle_id=run_lifecycle_id,
        field_universe=field_universe,
        summary_paths=summary_paths,
        aggregate_exports=aggregate_exports,
        universe_filter=serialized_universe_filter,
    )
    print(f"[resume] wrote {tracking_path}")
    print(f"[resume] aggregate db={aggregate_exports.get('database_path')}")
    return {
        "run_root": run_root.as_posix(),
        "windows_aggregated": len(summary_paths),
        "windows_rerun": len(completed_results),
        "aggregate_exports": aggregate_exports,
        "tracking_json": tracking_path.as_posix(),
    }


def main() -> None:
    default_run_root = (
        PROJECT_ROOT
        / "logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs"
        / "scan_period_close_forward_tracking_29mar_15jun2026_b108820e"
    )
    default_all_fields_root = (
        PROJECT_ROOT / "logs/tradingview_analysis/trading_view_all_fields_data"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=default_run_root)
    parser.add_argument("--start-day-label", default="29_03_2026")
    parser.add_argument("--end-day-label", default="15_06_2026")
    parser.add_argument("--run-lifecycle-id", default="b108820e")
    parser.add_argument("--all-fields-root", type=Path, default=default_all_fields_root)
    parser.add_argument("--duckdb-threads", type=int, default=6)
    parser.add_argument("--field-batch-size", type=int, default=120)
    parser.add_argument("--duckdb-memory-limit", default="10GB")
    args = parser.parse_args()

    result = resume_scan_period_rolling(
        run_root=args.run_root,
        start_day_label=args.start_day_label,
        end_day_label=args.end_day_label,
        run_lifecycle_id=args.run_lifecycle_id,
        all_fields_root=args.all_fields_root,
        duckdb_threads=args.duckdb_threads,
        field_batch_size=args.field_batch_size,
        duckdb_memory_limit=args.duckdb_memory_limit,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
