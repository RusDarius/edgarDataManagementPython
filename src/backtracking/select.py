from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from generic_utils.focus import build_focus
from generic_utils.ranking import to_float
from generic_utils.scan_sources import rows_from_duckdb, rows_to_csv

from .reporting import load_run_manifest, resolve_run_dir


def _infer_source_layer(field: str) -> str:
    if field.startswith("edge_"):
        return "edge"
    if field in {"bo", "cont", "fwd", "early", "sms", "recov", "rev", "frag", "exh", "conviction_score", "rank_overall"}:
        return "prediction"
    if field == "left":
        return "mixed"
    return "raw"


def _normalize_selector(selector: str) -> str:
    text = str(selector or "").strip()
    if not text:
        raise ValueError("selector must be a non-empty string")
    return text


def select_program_rows(
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
    selector = _normalize_selector(selector)
    field = str(field or "").strip()
    if not field:
        raise ValueError("field is required")
    if group_field and cap is None:
        raise ValueError("group_field requires --cap")

    run_path = resolve_run_dir(run_dir)
    load_run_manifest(run_path)
    db_path = run_path / "backtracking.duckdb"
    if not db_path.exists():
        raise FileNotFoundError(f"Missing backtracking.duckdb in {run_path}")

    panel_rows = rows_from_duckdb(
        db_path,
        "SELECT * FROM panel_rows ORDER BY decision_date, symbol",
    )
    by_decision: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in panel_rows:
        decision_date = str(row.get("decision_date") or "").strip()
        symbol = str(row.get("symbol") or "").strip().upper()
        if not decision_date or not symbol:
            continue
        row["symbol"] = symbol
        by_decision[decision_date].append(row)

    picks: list[dict[str, Any]] = []
    per_week_summary: list[dict[str, Any]] = []
    for decision_date in sorted(by_decision):
        week_rows = by_decision[decision_date]
        numeric_rows = [row for row in week_rows if to_float(row.get(field)) is not None]
        focus_payload = build_focus(
            numeric_rows,
            where=where,
            field=field,
            n=max(1, int(top)),
            reverse=reverse,
            tie_breaker=tie_breaker,
            group_field=group_field,
            cap=cap,
            id_field="symbol",
        )
        selected = list(focus_payload.get("rows") or [])
        source_layer = _infer_source_layer(field)
        n_selected = len(selected)
        for idx, row in enumerate(selected, start=1):
            cited = [field]
            if tie_breaker:
                cited.append(tie_breaker)
            picks.append(
                {
                    "decision_date": decision_date,
                    "symbol": str(row.get("symbol") or "").strip().upper(),
                    "weight": round(1.0 / n_selected, 8) if n_selected else None,
                    "source_layer": source_layer,
                    "cited_fields": ",".join(cited),
                    "note": "",
                    "selector": selector,
                    "rank_in_week": idx,
                    "field_value": row.get(field),
                }
            )
        per_week_summary.append(
            {
                "decision_date": decision_date,
                "source_rows": len(week_rows),
                "numeric_rows": len(numeric_rows),
                "selected_rows": n_selected,
            }
        )

    picks_path = run_path / f"picks_{selector}.csv"
    rows_to_csv(picks_path, picks)
    return {
        "run_dir": run_path.as_posix(),
        "selector": selector,
        "field": field,
        "picks_path": picks_path.as_posix(),
        "pick_count": len(picks),
        "weeks": per_week_summary,
    }
