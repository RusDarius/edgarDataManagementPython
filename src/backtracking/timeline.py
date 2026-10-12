from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence, TypeVar

from backtests.timeline import (
    discover_all_fields_days,
    discover_edge_parent_runs,
    discover_prediction_runs,
)
from focus_pool_screening.discovery import resolve_edge_unified_db

from .config import BacktrackingConfig
from .contracts import DecisionWeek, TimelineBuildResult

_DAY_LABEL_FMT = "%d_%m_%Y"
TRef = TypeVar("TRef")


def parse_day_label(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, _DAY_LABEL_FMT).date()
    except ValueError:
        return None


def day_label(value: date) -> str:
    return value.strftime(_DAY_LABEL_FMT)


def _in_window(value: date, *, start: date | None, end: date | None) -> bool:
    if start is not None and value < start:
        return False
    if end is not None and value > end:
        return False
    return True


def _lookup_on_or_before(lookup: Mapping[date, TRef], target_day: date) -> tuple[date, TRef] | None:
    if not lookup:
        return None
    ordered_days = sorted(lookup)
    idx = bisect_right(ordered_days, target_day) - 1
    if idx < 0:
        return None
    resolved_day = ordered_days[idx]
    return resolved_day, lookup[resolved_day]


def _effective_window(
    config: BacktrackingConfig,
    *,
    start_day_label: str | None,
    end_day_label: str | None,
) -> tuple[str | None, str | None, date | None, date | None]:
    start_label = start_day_label or config.window.start_day_label
    end_label = end_day_label or config.window.end_day_label
    start_day = parse_day_label(start_label)
    end_day = parse_day_label(end_label)
    if start_label and start_day is None:
        raise ValueError(f"Invalid start day label: {start_label!r} (expected dd_mm_yyyy)")
    if end_label and end_day is None:
        raise ValueError(f"Invalid end day label: {end_label!r} (expected dd_mm_yyyy)")
    if start_day is not None and end_day is not None and start_day > end_day:
        raise ValueError("start day must be earlier than or equal to end day")
    return start_label, end_label, start_day, end_day


def build_decision_timeline(
    config: BacktrackingConfig,
    *,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
) -> TimelineBuildResult:
    start_label, end_label, start_day, end_day = _effective_window(
        config,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )

    all_fields_lookup = discover_all_fields_days(
        all_fields_root=config.paths.all_fields_root,
        start_day_label=None,
        end_day_label=None,
        max_days=None,
    )
    prediction_lookup = discover_prediction_runs(config.paths.prediction_root)
    edge_lookup = discover_edge_parent_runs(config.paths.edge_runs_root)

    in_window_prediction_days = [
        day
        for day in prediction_lookup
        if _in_window(day, start=start_day, end=end_day)
    ]
    latest_per_week: dict[tuple[int, int], date] = {}
    for pred_day in sorted(in_window_prediction_days):
        iso = pred_day.isocalendar()
        key = (iso.year, iso.week)
        previous = latest_per_week.get(key)
        if previous is None or pred_day > previous:
            latest_per_week[key] = pred_day

    decision_weeks: list[DecisionWeek] = []
    coverage_rows: list[dict[str, Any]] = []
    for decision_day in sorted(latest_per_week.values()):
        pred_ref = prediction_lookup[decision_day]
        iso = decision_day.isocalendar()

        all_fields_hit = _lookup_on_or_before(all_fields_lookup, decision_day)
        all_fields_day = all_fields_hit[0] if all_fields_hit else None
        all_fields_ref = all_fields_hit[1] if all_fields_hit else None

        edge_hit = _lookup_on_or_before(edge_lookup, decision_day)
        edge_parent_dir = edge_hit[1] if edge_hit else None
        edge_unified = (
            resolve_edge_unified_db(edge_parent_dir)
            if edge_parent_dir is not None
            else None
        )

        lag_days = (
            (decision_day - all_fields_day).days
            if all_fields_day is not None
            else None
        )
        week = DecisionWeek(
            decision_date=decision_day,
            decision_day_label=day_label(decision_day),
            iso_year=iso.year,
            iso_week=iso.week,
            prediction_database=pred_ref.database,
            prediction_run_id=pred_ref.run_id,
            all_fields_database=(all_fields_ref.database if all_fields_ref else None),
            all_fields_run_id=(all_fields_ref.run_id if all_fields_ref else None),
            all_fields_day_label=(
                day_label(all_fields_day) if all_fields_day is not None else None
            ),
            all_fields_lag_days=lag_days,
            edge_parent_dir=edge_parent_dir,
            edge_unified_database=edge_unified,
        )
        decision_weeks.append(week)
        coverage_rows.append(
            {
                "decision_date": decision_day.isoformat(),
                "decision_day_label": day_label(decision_day),
                "iso_year": iso.year,
                "iso_week": iso.week,
                "prediction_run_id": pred_ref.run_id,
                "has_all_fields": int(week.all_fields_database is not None),
                "all_fields_day_label": week.all_fields_day_label or "",
                "all_fields_lag_days": week.all_fields_lag_days,
                "has_edge_parent": int(week.edge_parent_dir is not None),
                "has_edge_unified": int(week.edge_unified_database is not None),
            }
        )

    return TimelineBuildResult(
        decision_weeks=decision_weeks,
        coverage_rows=coverage_rows,
        start_day_label=start_label,
        end_day_label=end_label,
    )


def latest_all_fields_on_or_before(
    *,
    all_fields_root: Path,
    end_day_label: str | None,
) -> tuple[date, Any] | None:
    refs = discover_all_fields_days(
        all_fields_root=all_fields_root,
        start_day_label=None,
        end_day_label=end_day_label,
        max_days=None,
    )
    if not refs:
        return None
    latest_day = max(refs)
    return latest_day, refs[latest_day]


def find_decision_week_for_date(
    decision_weeks: Sequence[DecisionWeek], target_date: date
) -> DecisionWeek | None:
    for week in decision_weeks:
        if week.decision_date == target_date:
            return week
    return None
