from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    DEFAULT_ALL_FIELDS_ROOT,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCAN_PERIOD_RUNS_ROOT = DEFAULT_ALL_FIELDS_ROOT / "pattern_analysis" / "runs"
DEFAULT_TAXONOMY_ROOT = PROJECT_ROOT / "savedData" / "trading_view_field_taxonomy"
DEFAULT_EDGE_RESEARCH_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "edge_research_tools"
)
DEFAULT_EDGE_RESEARCH_OUTPUT_ROOT = DEFAULT_EDGE_RESEARCH_ROOT / "runs"
DEFAULT_EDGE_RESEARCH_FOUNDATION_ROOT = DEFAULT_EDGE_RESEARCH_ROOT / "foundations"


@dataclass(frozen=True)
class EdgeResearchPaths:
    project_root: Path
    all_fields_root: Path
    scan_period_runs_root: Path
    taxonomy_root: Path
    edge_research_root: Path
    foundation_root: Path
    output_root: Path


@dataclass(frozen=True)
class EdgeResearchRunContext:
    run_id: str
    created_at_utc: datetime
    output_dir: Path


def resolve_edge_research_paths(
    *,
    project_root: str | Path | None = None,
    all_fields_root: str | Path | None = None,
    scan_period_runs_root: str | Path | None = None,
    taxonomy_root: str | Path | None = None,
    output_root: str | Path | None = None,
) -> EdgeResearchPaths:
    def _resolve_edge_research_root(path_value: str | Path | None) -> Path:
        if path_value is None:
            return DEFAULT_EDGE_RESEARCH_ROOT
        candidate = Path(path_value)
        if candidate.name.lower() in {"runs", "foundations"}:
            return candidate.parent
        return candidate

    resolved_project_root = (
        Path(project_root) if project_root is not None else PROJECT_ROOT
    )
    resolved_all_fields_root = (
        Path(all_fields_root)
        if all_fields_root is not None
        else DEFAULT_ALL_FIELDS_ROOT
    )
    resolved_scan_period_runs_root = (
        Path(scan_period_runs_root)
        if scan_period_runs_root is not None
        else DEFAULT_SCAN_PERIOD_RUNS_ROOT
    )
    resolved_taxonomy_root = (
        Path(taxonomy_root) if taxonomy_root is not None else DEFAULT_TAXONOMY_ROOT
    )
    resolved_edge_research_root = _resolve_edge_research_root(output_root)
    resolved_output_root = resolved_edge_research_root / "runs"
    resolved_foundation_root = resolved_edge_research_root / "foundations"
    return EdgeResearchPaths(
        project_root=resolved_project_root,
        all_fields_root=resolved_all_fields_root,
        scan_period_runs_root=resolved_scan_period_runs_root,
        taxonomy_root=resolved_taxonomy_root,
        edge_research_root=resolved_edge_research_root,
        foundation_root=resolved_foundation_root,
        output_root=resolved_output_root,
    )


def build_edge_research_run_context(
    *,
    prefix: str,
    output_root: str | Path = DEFAULT_EDGE_RESEARCH_OUTPUT_ROOT,
) -> EdgeResearchRunContext:
    created_at_utc = datetime.now(tz=timezone.utc)
    run_id = (
        f"{prefix}_{created_at_utc.strftime('%Y%m%d_%H%M')}_utc_"
        f"{uuid.uuid4().hex[:8]}"
    )
    output_dir = Path(output_root) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    return EdgeResearchRunContext(
        run_id=run_id,
        created_at_utc=created_at_utc,
        output_dir=output_dir,
    )
