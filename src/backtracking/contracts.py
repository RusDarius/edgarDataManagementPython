from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DecisionWeek:
    decision_date: date
    decision_day_label: str
    iso_year: int
    iso_week: int
    prediction_database: Path
    prediction_run_id: str
    all_fields_database: Path | None = None
    all_fields_run_id: str | None = None
    all_fields_day_label: str | None = None
    all_fields_lag_days: int | None = None
    edge_parent_dir: Path | None = None
    edge_unified_database: Path | None = None

    def to_record(self) -> dict[str, Any]:
        payload = {
            "decision_date": self.decision_date.isoformat(),
            "decision_day_label": self.decision_day_label,
            "iso_year": self.iso_year,
            "iso_week": self.iso_week,
            "prediction_database": self.prediction_database.as_posix(),
            "prediction_run_id": self.prediction_run_id,
            "all_fields_database": (
                self.all_fields_database.as_posix()
                if self.all_fields_database is not None
                else None
            ),
            "all_fields_run_id": self.all_fields_run_id,
            "all_fields_day_label": self.all_fields_day_label,
            "all_fields_lag_days": self.all_fields_lag_days,
            "edge_parent_dir": (
                self.edge_parent_dir.as_posix() if self.edge_parent_dir is not None else None
            ),
            "edge_unified_database": (
                self.edge_unified_database.as_posix()
                if self.edge_unified_database is not None
                else None
            ),
        }
        return payload


@dataclass(frozen=True)
class TimelineBuildResult:
    decision_weeks: list[DecisionWeek]
    coverage_rows: list[dict[str, Any]]
    start_day_label: str | None
    end_day_label: str | None


@dataclass(frozen=True)
class PanelBuildResult:
    rows: list[dict[str, Any]]
    notes: list[str]
    raw_columns: tuple[str, ...]
    edge_columns: tuple[str, ...]
