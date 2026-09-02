from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class ArtifactDay:
    """Resolved point-in-time artifact references for one trading day."""

    as_of_date: date
    day_label: str
    all_fields_database: Path | None = None
    all_fields_run_id: str | None = None
    prediction_database: Path | None = None
    prediction_run_id: str | None = None
    edge_parent_dir: Path | None = None
    upside_opportunity_manifest: Path | None = None
    upside_move_manifest: Path | None = None
    market_timing_manifest: Path | None = None
    financial_projection_growth_metadata: Path | None = None
    financial_projection_price_metadata: Path | None = None

    def to_record(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["as_of_date"] = self.as_of_date.isoformat()
        for key, value in list(payload.items()):
            if isinstance(value, Path):
                payload[key] = value.as_posix()
        return payload


@dataclass(frozen=True)
class SignalRow:
    """Generic signal row emitted by one adapter or overlay rule."""

    as_of_date: date
    symbol: str
    family: str
    signal_name: str
    score: float | None
    rank: int | None = None
    action: str | None = None
    weight: float | None = None
    source_path: str | None = None
    run_id: str | None = None
    horizon_hint: str | None = None
    metadata_json: str | None = None

    def to_record(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["as_of_date"] = self.as_of_date.isoformat()
        return payload


@dataclass(frozen=True)
class OutcomeRow:
    """Forward-return label for one symbol/day/horizon."""

    as_of_date: date
    symbol: str
    horizon_days: int
    forward_return_pct: float
    entry_close: float | None = None
    exit_close: float | None = None
    entry_day_label: str | None = None
    exit_day_label: str | None = None

    def to_record(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["as_of_date"] = self.as_of_date.isoformat()
        return payload


@dataclass
class AdapterOutput:
    """Adapter result bundle."""

    family: str
    signals: list[SignalRow]
    diagnostics: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)


class SignalAdapter(Protocol):
    family: str

    def load(
        self,
        calendar_days: Sequence[ArtifactDay],
        *,
        config: Any,
    ) -> AdapterOutput:
        """Load signal rows from resolved calendar artifacts."""
        ...
