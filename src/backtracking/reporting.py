from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.run_export import write_run_export

from .config import BacktrackingConfig
from .contracts import PanelBuildResult, TimelineBuildResult


def build_run_id(prefix: str = "backtracking") -> tuple[str, datetime]:
    created_at = datetime.now(tz=timezone.utc)
    run_id = f"{prefix}_{created_at.strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"
    return run_id, created_at


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _append_query_examples(path: Path) -> None:
    extra = [
        "",
        "# Backtracking quick queries",
        "",
        "-- Most recent decision week by bo",
        'SELECT decision_date, symbol, bo, cont, fwd, "left", RSI, industry',
        "FROM panel_rows",
        "ORDER BY decision_date DESC, bo DESC NULLS LAST",
        "LIMIT 25;",
        "",
        "-- Street/edge leftover lens (quote left)",
        'SELECT decision_date, symbol, close, price_target_average, edge_forward_valuation_upside_pct, "left"',
        "FROM panel_rows",
        'ORDER BY "left" DESC NULLS LAST',
        "LIMIT 25;",
        "",
        "-- Programmatic selector template",
        "SELECT * FROM panel_rows",
        "WHERE decision_date = 'YYYY-MM-DD'",
        "ORDER BY bo DESC NULLS LAST;",
        "",
    ]
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(extra))


def _build_picks_schema() -> dict[str, Any]:
    return {
        "required_columns": ["decision_date", "symbol", "selector"],
        "optional_columns": [
            "weight",
            "source_layer",
            "cited_fields",
            "note",
            "rank_in_week",
        ],
        "rules": [
            "decision_date must match a decision week in decision_weeks table",
            "blank weight means equal-weight before week-level renormalization",
            "write one row per selected symbol per decision week",
            "selector should identify strategy (agent or program_<field>)",
        ],
    }


def write_prepare_outputs(
    *,
    config: BacktrackingConfig,
    timeline: TimelineBuildResult,
    panel: PanelBuildResult,
    raw_preset: str,
) -> dict[str, Any]:
    run_id, created_at = build_run_id()
    run_dir = config.output.root_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    decision_rows = [week.to_record() for week in timeline.decision_weeks]
    tables = {
        "decision_weeks": decision_rows,
        "panel_rows": panel.rows,
        "coverage_ledger": timeline.coverage_rows,
    }
    notes = [
        f"window start: {timeline.start_day_label or 'none'}",
        f"window end: {timeline.end_day_label or 'none'}",
        f"raw preset: {raw_preset}",
        f"panel raw columns: {len(panel.raw_columns)}",
        f"panel edge columns: {len(panel.edge_columns)}",
        *panel.notes,
    ]

    export_result = write_run_export(
        run_dir,
        tool="backtracking.prepare",
        tables=tables,
        sources={
            "all_fields_root": config.paths.all_fields_root.as_posix(),
            "prediction_root": config.paths.prediction_root.as_posix(),
            "edge_runs_root": config.paths.edge_runs_root.as_posix(),
            "focused_catalog_csv": config.paths.focused_catalog_csv.as_posix(),
        },
        schema_version=config.schema_version,
        notes=notes,
        extra={
            "config_id": config.config_id,
            "config_path": config.source_path.as_posix(),
            "raw_preset": raw_preset,
        },
        duckdb_name="backtracking.duckdb",
        log_name="overview.log",
        write_csv=config.output.write_csv,
    )

    queries_path = run_dir / "queries.txt"
    if queries_path.exists():
        _append_query_examples(queries_path)

    picks_schema_path = _write_json(run_dir / "picks_schema.json", _build_picks_schema())

    manifest = {
        "run_id": run_id,
        "created_at_utc": created_at.isoformat(),
        "schema_version": config.schema_version,
        "config_id": config.config_id,
        "config_path": config.source_path.as_posix(),
        "window": {
            "start_day_label": timeline.start_day_label,
            "end_day_label": timeline.end_day_label,
        },
        "raw_preset": raw_preset,
        "paths": {
            "all_fields_root": config.paths.all_fields_root.as_posix(),
            "prediction_root": config.paths.prediction_root.as_posix(),
            "edge_runs_root": config.paths.edge_runs_root.as_posix(),
            "focused_catalog_csv": config.paths.focused_catalog_csv.as_posix(),
        },
        "counts": {
            "decision_weeks": len(decision_rows),
            "panel_rows": len(panel.rows),
            "coverage_rows": len(timeline.coverage_rows),
        },
        "panel_schema": {
            "raw_columns": list(panel.raw_columns),
            "edge_columns": list(panel.edge_columns),
        },
        "outputs": {
            "run_dir": run_dir.as_posix(),
            "backtracking_duckdb": (run_dir / "backtracking.duckdb").as_posix(),
            "overview_log": (run_dir / "overview.log").as_posix(),
            "queries": (run_dir / "queries.txt").as_posix(),
            "picks_schema": picks_schema_path.as_posix(),
        },
        "run_export": export_result,
    }
    manifest_path = _write_json(run_dir / "run_manifest.json", manifest)

    return {
        "run_id": run_id,
        "run_dir": run_dir.as_posix(),
        "created_at_utc": created_at.isoformat(),
        "database_path": (run_dir / "backtracking.duckdb").as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "overview_log": (run_dir / "overview.log").as_posix(),
        "queries_path": (run_dir / "queries.txt").as_posix(),
        "decision_week_count": len(decision_rows),
        "panel_row_count": len(panel.rows),
    }


def resolve_run_dir(run_dir: str | Path) -> Path:
    resolved = Path(run_dir).resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"Backtracking run directory not found: {resolved}")
    return resolved


def load_run_manifest(run_dir: str | Path) -> dict[str, Any]:
    resolved = resolve_run_dir(run_dir)
    manifest_path = resolved / "run_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing run_manifest.json in {resolved}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def save_run_manifest(run_dir: str | Path, manifest: Mapping[str, Any]) -> Path:
    resolved = resolve_run_dir(run_dir)
    path = resolved / "run_manifest.json"
    path.write_text(json.dumps(dict(manifest), indent=2), encoding="utf-8")
    return path


def record_score_run(
    *,
    run_dir: str | Path,
    selector: str,
    picks_path: Path,
    book_rows: Sequence[Mapping[str, Any]],
    name_rows: Sequence[Mapping[str, Any]],
) -> Path:
    manifest = load_run_manifest(run_dir)
    score_runs = list(manifest.get("score_runs") or [])
    score_runs.append(
        {
            "scored_at_utc": datetime.now(tz=timezone.utc).isoformat(),
            "selector": selector,
            "picks_path": picks_path.as_posix(),
            "book_weeks_rows": len(book_rows),
            "name_returns_rows": len(name_rows),
            "outputs": {
                "book_weeks_csv": str(Path(run_dir) / "book_weeks.csv"),
                "name_returns_csv": str(Path(run_dir) / "name_returns.csv"),
                "investing_run_md": str(Path(run_dir) / "investing_run.md"),
            },
        }
    )
    updated = dict(manifest)
    updated["score_runs"] = score_runs
    return save_run_manifest(run_dir, updated)
