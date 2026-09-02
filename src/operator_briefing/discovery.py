"""Lock the newest (or pinned) scan artifacts for one briefing pack."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb
from financial_projection.load_all_fields import (
    discover_latest_all_fields_db,
    discover_latest_prediction_db,
)
from focus_pool_screening.discovery import discover_latest_edge_parent_dir, resolve_edge_unified_db

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_ROOT = PROJECT_ROOT / "logs" / "tradingview_analysis"
HOLDINGS_CONFIG = PROJECT_ROOT / "config" / "holdings_scoring" / "current_holdings.json"
PRIORS_DIR = PROJECT_ROOT / "logs" / "AI_ANALYSIS_UTILS" / "priors"
MTP_RUNS = LOG_ROOT / "market_timing_policy" / "runs"
HOLDINGS_RUNS = LOG_ROOT / "holdings_scoring_analysis" / "runs"
FOCUS_RUNS = LOG_ROOT / "focus_pool_screening" / "runs"


@dataclass(frozen=True)
class LockedSources:
    prediction_db: Path
    prediction_run_id: str
    prediction_created_at_utc: str
    prediction_prior_run_ids: tuple[str, ...]
    all_fields_db: Path
    all_fields_run_id: str
    all_fields_created_at_utc: str
    all_fields_day_label: str
    edge_parent_dir: Path | None
    edge_unified_db: Path | None
    edge_opportunity_csv: Path | None
    mtp_dir: Path | None
    mtp_policy_csv: Path | None
    mtp_catalyst_csv: Path | None
    holdings_run_dir: Path | None
    holdings_summary_csv: Path | None
    holdings_config: Path | None
    focus_pool_dir: Path | None
    priors_path: Path | None
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        def _p(path: Path | None) -> str | None:
            return path.as_posix() if path else None

        return {
            "prediction_db": _p(self.prediction_db),
            "prediction_run_id": self.prediction_run_id,
            "prediction_created_at_utc": self.prediction_created_at_utc,
            "prediction_prior_run_ids": list(self.prediction_prior_run_ids),
            "all_fields_db": _p(self.all_fields_db),
            "all_fields_run_id": self.all_fields_run_id,
            "all_fields_created_at_utc": self.all_fields_created_at_utc,
            "all_fields_day_label": self.all_fields_day_label,
            "edge_parent_dir": _p(self.edge_parent_dir),
            "edge_unified_db": _p(self.edge_unified_db),
            "edge_opportunity_csv": _p(self.edge_opportunity_csv),
            "mtp_dir": _p(self.mtp_dir),
            "mtp_policy_csv": _p(self.mtp_policy_csv),
            "mtp_catalyst_csv": _p(self.mtp_catalyst_csv),
            "holdings_run_dir": _p(self.holdings_run_dir),
            "holdings_summary_csv": _p(self.holdings_summary_csv),
            "holdings_config": _p(self.holdings_config),
            "focus_pool_dir": _p(self.focus_pool_dir),
            "priors_path": _p(self.priors_path),
            "notes": list(self.notes),
        }


def _latest_run_rows(database_path: Path, limit: int = 8) -> list[dict[str, Any]]:
    return query_move_prediction_duckdb(
        database_path,
        """
        SELECT run_id, CAST(created_at_utc AS VARCHAR) AS created_at_utc
        FROM run_metadata
        ORDER BY created_at_utc DESC NULLS LAST, run_id DESC
        LIMIT ?
        """,
        [limit],
    )


def _latest_dir(root: Path, prefix: str) -> Path | None:
    if not root.exists():
        return None
    matches = [p for p in root.iterdir() if p.is_dir() and p.name.startswith(prefix)]
    if not matches:
        return None
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0]


def _latest_priors() -> Path | None:
    if not PRIORS_DIR.exists():
        return None
    files = sorted(PRIORS_DIR.glob("*_operator_actions.json"))
    return files[-1] if files else None


def _opportunity_csv(parent: Path | None) -> Path | None:
    if parent is None:
        return None
    candidate = (
        parent
        / "aggregate"
        / "upside_opportunity_scan"
        / "upside_opportunity_candidates.csv"
    )
    return candidate if candidate.exists() else None


def lock_sources(
    *,
    prediction_run_id: str | None = None,
    all_fields_run_id: str | None = None,
) -> LockedSources:
    notes: list[str] = []
    prediction_db = discover_latest_prediction_db()
    if prediction_db is None:
        raise FileNotFoundError("No move_prediction DuckDB discovered.")
    pred_runs = _latest_run_rows(prediction_db, 8)
    if not pred_runs:
        raise ValueError(f"No run_metadata in {prediction_db}")
    if prediction_run_id:
        match = next((r for r in pred_runs if r["run_id"] == prediction_run_id), None)
        if match is None:
            extra = query_move_prediction_duckdb(
                prediction_db,
                "SELECT run_id, CAST(created_at_utc AS VARCHAR) AS created_at_utc FROM run_metadata WHERE run_id = ?",
                [prediction_run_id],
            )
            if not extra:
                raise ValueError(f"prediction run_id not found: {prediction_run_id}")
            match = extra[0]
        pred_run = match
    else:
        pred_run = pred_runs[0]
    prior_ids = tuple(
        str(r["run_id"])
        for r in pred_runs
        if r["run_id"] != pred_run["run_id"]
    )[:6]

    all_fields_db = discover_latest_all_fields_db()
    if all_fields_db is None:
        raise FileNotFoundError("No all-fields DuckDB discovered.")
    af_runs = _latest_run_rows(all_fields_db, 5)
    if not af_runs:
        raise ValueError(f"No run_metadata in {all_fields_db}")
    if all_fields_run_id:
        af_run = next((r for r in af_runs if r["run_id"] == all_fields_run_id), None)
        if af_run is None:
            raise ValueError(f"all-fields run_id not found: {all_fields_run_id}")
    else:
        af_run = af_runs[0]
    day_label = all_fields_db.parent.name

    edge_parent = discover_latest_edge_parent_dir()
    edge_db = resolve_edge_unified_db(edge_parent) if edge_parent else None
    if edge_parent is None:
        notes.append("edge parent missing")

    mtp_dir = _latest_dir(MTP_RUNS, "market_timing_policy_")
    mtp_policy = (mtp_dir / "action_policy_recommendations.csv") if mtp_dir else None
    mtp_catalyst = (
        mtp_dir / "catalyst_event_action_recommendations.csv" if mtp_dir else None
    )
    if mtp_policy is not None and not mtp_policy.exists():
        mtp_policy = None

    holdings_dir = _latest_dir(HOLDINGS_RUNS, "holdings_scoring_")
    holdings_csv = None
    if holdings_dir is not None:
        candidate = (
            holdings_dir / "scans" / "move_prediction_v1" / "holdings__summary.csv"
        )
        holdings_csv = candidate if candidate.exists() else None

    holdings_config = HOLDINGS_CONFIG if HOLDINGS_CONFIG.exists() else None
    focus_dir = _latest_dir(FOCUS_RUNS, "focus_pool_")
    if focus_dir is None:
        notes.append("focus pool missing — sleeves from pred + leftover + tape")
    priors = _latest_priors()
    if priors is None:
        notes.append("no operator_actions prior — polar gate has no yesterday map")

    return LockedSources(
        prediction_db=prediction_db,
        prediction_run_id=str(pred_run["run_id"]),
        prediction_created_at_utc=str(pred_run.get("created_at_utc") or ""),
        prediction_prior_run_ids=prior_ids,
        all_fields_db=all_fields_db,
        all_fields_run_id=str(af_run["run_id"]),
        all_fields_created_at_utc=str(af_run.get("created_at_utc") or ""),
        all_fields_day_label=day_label,
        edge_parent_dir=edge_parent,
        edge_unified_db=edge_db,
        edge_opportunity_csv=_opportunity_csv(edge_parent),
        mtp_dir=mtp_dir,
        mtp_policy_csv=mtp_policy,
        mtp_catalyst_csv=mtp_catalyst if mtp_catalyst and mtp_catalyst.exists() else None,
        holdings_run_dir=holdings_dir,
        holdings_summary_csv=holdings_csv,
        holdings_config=holdings_config,
        focus_pool_dir=focus_dir,
        priors_path=priors,
        notes=tuple(notes),
    )
