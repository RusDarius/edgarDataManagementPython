"""Resolve the concrete data sources for a focus pool screening run.

All resolution is deterministic: explicit config paths win; otherwise the
latest available snapshot is discovered from the standard logs roots
(same helpers the financial projection flow uses).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb
from financial_projection.load_all_fields import (
    discover_latest_all_fields_db,
    discover_latest_prediction_db,
    resolve_all_fields_day_database,
)

from .config import FocusPoolConfig, PROJECT_ROOT

DEFAULT_EDGE_RUNS_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "edge_research_tools" / "runs"
)


@dataclass(frozen=True)
class ResolvedSources:
    prediction_db: Path | None
    prediction_run_id: str | None
    prediction_created_at_utc: str | None
    all_fields_db: Path | None
    all_fields_run_id: str | None
    all_fields_created_at_utc: str | None
    edge_parent_dir: Path | None
    edge_unified_db: Path | None
    notes: tuple[str, ...]


def _latest_run_in_db(database_path: Path) -> tuple[str, str]:
    rows = query_move_prediction_duckdb(
        str(database_path),
        """
        SELECT run_id, CAST(created_at_utc AS VARCHAR) AS created_at_utc
        FROM run_metadata
        ORDER BY created_at_utc DESC NULLS LAST, run_id DESC
        LIMIT 1
        """,
    )
    if not rows:
        raise ValueError(f"No runs recorded in run_metadata of {database_path.as_posix()}")
    return str(rows[0]["run_id"]), str(rows[0].get("created_at_utc") or "")


def _run_exists(database_path: Path, run_id: str) -> tuple[str, str]:
    rows = query_move_prediction_duckdb(
        str(database_path),
        """
        SELECT run_id, CAST(created_at_utc AS VARCHAR) AS created_at_utc
        FROM run_metadata
        WHERE run_id = ?
        LIMIT 1
        """,
        [run_id],
    )
    if not rows:
        raise ValueError(
            f"run_id {run_id!r} not present in run_metadata of {database_path.as_posix()}"
        )
    return str(rows[0]["run_id"]), str(rows[0].get("created_at_utc") or "")


def discover_latest_edge_parent_dir(
    runs_root: str | Path = DEFAULT_EDGE_RUNS_ROOT,
) -> Path | None:
    root = Path(runs_root)
    if not root.exists():
        return None
    candidates = [
        path
        for path in root.iterdir()
        if path.is_dir() and (path / "parent_run_manifest.json").exists()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def resolve_edge_unified_db(parent_dir: Path) -> Path | None:
    """Locate edge_unified_highlights.duckdb under a parent run (usually
    ``<parent>/aggregate/edge_unified_highlights/``)."""
    matches = sorted(parent_dir.rglob("edge_unified_highlights.duckdb"))
    if not matches:
        return None
    aggregate_first = [p for p in matches if "aggregate" in [part.lower() for part in p.parts]]
    return (aggregate_first or matches)[0]


def resolve_sources(config: FocusPoolConfig) -> ResolvedSources:
    notes: list[str] = []

    prediction_db: Path | None = None
    prediction_run_id: str | None = None
    prediction_created_at: str | None = None
    mp = config.sources.move_prediction
    if mp.enabled:
        if mp.database_path is not None:
            prediction_db = Path(mp.database_path)
            if not prediction_db.exists():
                raise FileNotFoundError(
                    f"move_prediction database not found: {prediction_db.as_posix()}"
                )
        else:
            prediction_db = discover_latest_prediction_db()
            if prediction_db is None:
                raise FileNotFoundError(
                    "No move_prediction_*.duckdb discovered under the prediction "
                    "duckdb_runs root; run daily_prediction_move_analysis_suite first "
                    "or set sources.move_prediction.database_path."
                )
        if mp.run_id:
            prediction_run_id, prediction_created_at = _run_exists(prediction_db, mp.run_id)
        else:
            prediction_run_id, prediction_created_at = _latest_run_in_db(prediction_db)
        notes.append(f"move_prediction run {prediction_run_id} ({prediction_created_at})")

    all_fields_db: Path | None = None
    all_fields_run_id: str | None = None
    all_fields_created_at: str | None = None
    af = config.sources.all_fields
    if af.enabled:
        if af.database_path is not None:
            all_fields_db = Path(af.database_path)
            if not all_fields_db.exists():
                raise FileNotFoundError(
                    f"all_fields database not found: {all_fields_db.as_posix()}"
                )
        elif af.day_label:
            all_fields_db = resolve_all_fields_day_database(af.day_label)
        else:
            all_fields_db = discover_latest_all_fields_db()
            if all_fields_db is None:
                raise FileNotFoundError(
                    "No tradingview_all_fields_*.duckdb discovered; run "
                    "export_all_tradingview_fields_duckdb first or set "
                    "sources.all_fields.database_path."
                )
        if af.run_id:
            all_fields_run_id, all_fields_created_at = _run_exists(all_fields_db, af.run_id)
        else:
            all_fields_run_id, all_fields_created_at = _latest_run_in_db(all_fields_db)
        notes.append(f"all_fields run {all_fields_run_id} ({all_fields_created_at})")

    edge_parent_dir: Path | None = None
    edge_unified_db: Path | None = None
    edge = config.sources.edge_research
    if edge.enabled:
        edge_parent_dir = (
            Path(edge.parent_run_dir)
            if edge.parent_run_dir is not None
            else discover_latest_edge_parent_dir()
        )
        if edge_parent_dir is None or not edge_parent_dir.exists():
            notes.append(
                "edge_research disabled at runtime: no parent run dir found "
                "(overlay weight redistributes to other components)"
            )
            edge_parent_dir = None
        else:
            edge_unified_db = resolve_edge_unified_db(edge_parent_dir)
            if edge_unified_db is None:
                notes.append(
                    f"edge_research parent {edge_parent_dir.name} has no "
                    "edge_unified_highlights.duckdb; overlay skipped"
                )
            else:
                notes.append(f"edge_research parent {edge_parent_dir.name}")

    return ResolvedSources(
        prediction_db=prediction_db,
        prediction_run_id=prediction_run_id,
        prediction_created_at_utc=prediction_created_at,
        all_fields_db=all_fields_db,
        all_fields_run_id=all_fields_run_id,
        all_fields_created_at_utc=all_fields_created_at,
        edge_parent_dir=edge_parent_dir,
        edge_unified_db=edge_unified_db,
        notes=tuple(notes),
    )
