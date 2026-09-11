from __future__ import annotations

import shutil
import uuid
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from backtests.analysis import (
    build_overlay_signal_rows,
    evaluate_signal_rows,
    summarize_overlay_lift,
)
from backtests.config import BacktestConfig, load_backtest_config
from backtests.contracts import SignalRow
from backtests.reporting import write_backtest_outputs
from backtests.signals import (
    AllFieldsAdapter,
    FinancialProjectionAdapter,
    MarketTimingAdapter,
    MovePredictionAdapter,
    UpsideOpportunityAdapter,
)
from backtests.timeline import build_artifact_calendar, build_calendar_coverage_rows, build_outcome_rows


def _adapter_list(config: BacktestConfig):
    adapters = []
    if config.families.move_prediction.enabled:
        adapters.append(MovePredictionAdapter())
    if config.families.all_fields.enabled:
        adapters.append(AllFieldsAdapter())
    if config.families.upside_opportunity.enabled:
        adapters.append(UpsideOpportunityAdapter())
    if config.families.market_timing.enabled:
        adapters.append(MarketTimingAdapter())
    if config.families.financial_projection.enabled:
        adapters.append(FinancialProjectionAdapter())
    return adapters


def run_backtests(
    *,
    config_path: str | Path | None = None,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    max_days: int | None = None,
) -> dict[str, Any]:
    config = load_backtest_config(config_path)
    if start_day_label is not None:
        config = replace(
            config,
            calendar=replace(
                config.calendar,
                start_day_label=start_day_label,
            ),
        )
    if end_day_label is not None:
        config = replace(
            config,
            calendar=replace(
                config.calendar,
                end_day_label=end_day_label,
            ),
        )
    if max_days is not None:
        config = replace(
            config,
            calendar=replace(
                config.calendar,
                max_days=max_days,
            ),
        )

    calendar_days = build_artifact_calendar(config)
    if not calendar_days:
        raise ValueError(
            "No all-fields trading days were discovered for the selected window. "
            "Run export_all_tradingview_fields_duckdb() first or adjust start/end."
        )

    label_temp_dir = (
        config.output.root_dir
        / "_label_reference_tmp"
        / f"{uuid.uuid4().hex[:8]}"
    )
    label_result = build_outcome_rows(
        calendar_days,
        horizons=config.evaluation.horizons,
        output_dir=label_temp_dir,
    )

    all_signals: list[SignalRow] = []
    adapter_coverage: dict[str, dict[str, Any]] = {}
    diagnostics_tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for adapter in _adapter_list(config):
        result = adapter.load(calendar_days, config=config)
        all_signals.extend(result.signals)
        adapter_coverage[adapter.family] = result.coverage
        for table_name, rows in result.diagnostics.items():
            diagnostics_tables[table_name].extend(rows)

    overlay_rows = build_overlay_signal_rows(all_signals, config=config)
    all_signals.extend(overlay_rows)
    adapter_coverage["overlay"] = {"signal_rows": len(overlay_rows)}

    evaluation = evaluate_signal_rows(
        all_signals,
        label_result.rows,
        horizons=config.evaluation.horizons,
        top_ns=config.evaluation.top_ns,
        quintile_count=config.evaluation.quintile_count,
        min_pairs_for_metric=config.evaluation.min_pairs_for_metric,
    )
    overlay_comparison = summarize_overlay_lift(
        evaluation["family_metrics"], top_ns=config.evaluation.top_ns
    )

    calendar_rows = [day.to_record() for day in calendar_days]
    coverage_rows = build_calendar_coverage_rows(calendar_days)
    run_manifest_table = [
        {
            "config_id": config.config_id,
            "source_config": config.source_path.as_posix(),
            "calendar_day_count": len(calendar_rows),
            "signal_row_count": len(all_signals),
            "outcome_row_count": len(label_result.rows),
        }
    ]

    tables: dict[str, list[Any]] = {
        "artifact_calendar": calendar_rows,
        "coverage_ledger": coverage_rows,
        "signal_rows": all_signals,
        "outcome_rows": label_result.rows,
        "family_metrics": evaluation["family_metrics"],
        "overlay_comparison": overlay_comparison,
        "field_patterns": evaluation["field_patterns"],
        "timing_calibration": diagnostics_tables.get("timing_calibration", []),
        "run_manifest": run_manifest_table,
    }
    for table_name, rows in diagnostics_tables.items():
        if table_name in tables:
            continue
        tables[table_name] = rows

    write_result = write_backtest_outputs(
        config=config,
        tables=tables,
        adapter_coverage=adapter_coverage,
        label_metadata=label_result.metadata,
    )
    shutil.rmtree(label_temp_dir, ignore_errors=True)
    return {
        **write_result,
        "calendar_day_count": len(calendar_days),
        "signal_row_count": len(all_signals),
        "outcome_row_count": len(label_result.rows),
    }

