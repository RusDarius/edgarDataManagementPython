from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import BacktrackingConfig, load_backtracking_config
from .daily import attach_daily_closes
from .panel import build_panel_rows
from .paths import build_best_book
from .reporting import write_prepare_outputs
from .score import score_run
from .select import select_program_rows
from .timeline import build_decision_timeline


def _with_runtime_overrides(
    config: BacktrackingConfig,
    *,
    raw_preset: str | None,
) -> BacktrackingConfig:
    if raw_preset is None:
        return config
    preset = str(raw_preset).strip().lower()
    if preset not in {"core", "focused"}:
        raise ValueError("raw_preset must be one of: core, focused")
    return replace(config, raw_fields=replace(config.raw_fields, preset=preset))


def prepare_backtracking_run(
    *,
    config_path: str | Path | None = None,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    raw_preset: str | None = None,
) -> dict[str, Any]:
    config = load_backtracking_config(config_path)
    config = _with_runtime_overrides(config, raw_preset=raw_preset)

    timeline = build_decision_timeline(
        config,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )
    if not timeline.decision_weeks:
        raise ValueError(
            "No weekly prediction decisions were resolved for this window. "
            "Check prediction_analysis/duckdb_runs or adjust --start-day/--end-day."
        )

    resolved_preset = raw_preset or config.raw_fields.preset
    panel = build_panel_rows(
        config,
        decision_weeks=timeline.decision_weeks,
        raw_preset=resolved_preset,
    )
    payload = write_prepare_outputs(
        config=config,
        timeline=timeline,
        panel=panel,
        raw_preset=resolved_preset,
    )
    daily = attach_daily_closes(payload["run_dir"])
    payload["daily_sessions"] = daily["sessions"]
    payload["daily_close_rows"] = daily["rows"]
    return payload


def select_backtracking_run(
    *,
    run_dir: str | Path,
    selector: str,
    field: str,
    top: int = 15,
    where: str | None = None,
    tie_breaker: str | None = None,
    group_field: str | None = None,
    cap: int | None = None,
    reverse: bool = True,
) -> dict[str, Any]:
    return select_program_rows(
        run_dir=run_dir,
        selector=selector,
        field=field,
        top=top,
        where=where,
        tie_breaker=tie_breaker,
        group_field=group_field,
        cap=cap,
        reverse=reverse,
    )


def days_backtracking_run(*, run_dir: str | Path) -> dict[str, Any]:
    return attach_daily_closes(run_dir)


def best_backtracking_run(
    *,
    run_dir: str | Path,
    max_names: int = 20,
    min_sessions: int = 5,
) -> dict[str, Any]:
    return build_best_book(run_dir, max_names=max_names, min_sessions=min_sessions)


def score_backtracking_run(
    *,
    run_dir: str | Path,
    picks_path: str | Path,
) -> dict[str, Any]:
    return score_run(run_dir=run_dir, picks_path=picks_path)
