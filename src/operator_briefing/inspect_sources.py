"""Lock sources and describe tables/columns so agents do not write _tmp_inspect_*.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.trading_view_move_prediction_duckdb import (
    describe_move_prediction_duckdb,
    query_move_prediction_duckdb,
)

from .discovery import LockedSources, latest_briefing_pack_path, lock_sources
from .extract import AF_CANDIDATES, table_columns

PRED_KEY_TABLES = (
    "run_metadata",
    "raw_scan_rows",
    "profile_horizon_scores",
    "consensus_horizon_scores",
    "consensus_rows",
    "conviction_rankings",
    "regime_context_scores",
)

WIDE_TABLE_PREVIEW = (
    "run_id",
    "symbol",
    "ticker",
    "name",
    "Company",
    "company",
    "exchange",
    "country",
    "industry",
    "sector",
    "close",
    "change",
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "relative_volume_10d_calc",
    "RSI",
    "manager_action_signal",
    "consensus_weeks_score",
    "consensus_weeks_risk_adjusted_score",
    "score",
    "profile_name",
    "horizon_name",
)

SCHEMA_NOTES = (
    "manager_action_signal is the mix column, not manager_action.",
    "conviction rank is rank_overall; leftover is max(street PT, edge forward_valuation_upside_pct).",
    "regime_context_scores uses symbol + active_mgmt_tier (not ticker / regime_fit_tier).",
    "consensus_rows uses ticker; profile_horizon_scores and conviction_rankings use symbol.",
    "all_fields_rows is global (~49k). Filter US listed. Never SELECT *.",
    "DuckDB paths containing '=' must be quoted.",
    "Do not write _tmp_*.py under logs/. Use inspect / lookup / compare / stance.",
)


def _table_names(database_path: Path) -> list[str]:
    try:
        rows = describe_move_prediction_duckdb(database_path)
    except Exception as exc:  # noqa: BLE001
        return [f"_error:{exc}"]
    names = []
    for row in rows:
        name = row.get("table_name") or row.get("name")
        if name:
            names.append(str(name))
    return names


def _column_list(database_path: Path, table: str) -> list[str]:
    try:
        return sorted(table_columns(database_path, table))
    except Exception as exc:  # noqa: BLE001
        return [f"_error:{exc}"]


def _column_payload(database_path: Path, table: str) -> dict[str, Any]:
    cols = _column_list(database_path, table)
    if cols and cols[0].startswith("_error:"):
        return {"error": cols[0]}
    payload: dict[str, Any] = {"count": len(cols)}
    if len(cols) > 40:
        present = set(cols)
        payload["preview"] = [name for name in WIDE_TABLE_PREVIEW if name in present]
        payload["note"] = "Wide table; preview only. Never SELECT *."
    else:
        payload["names"] = cols
    return payload


def _run_rows(database_path: Path, limit: int = 8) -> list[dict[str, Any]]:
    try:
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
    except Exception as exc:  # noqa: BLE001
        return [{"_error": str(exc)}]


def inspect_locked_sources(
    *,
    prediction_run_id: str | None = None,
    all_fields_run_id: str | None = None,
    sources: LockedSources | None = None,
) -> dict[str, Any]:
    locked = sources or lock_sources(
        prediction_run_id=prediction_run_id,
        all_fields_run_id=all_fields_run_id,
    )
    pred_tables = _table_names(locked.prediction_db)
    pred_columns = {
        table: _column_payload(locked.prediction_db, table)
        for table in PRED_KEY_TABLES
        if table in pred_tables
    }
    af_tables = _table_names(locked.all_fields_db)
    af_available = table_columns(locked.all_fields_db, "all_fields_rows") if "all_fields_rows" in af_tables else set()
    whitelist = {
        key: next((c for c in candidates if c in af_available), None)
        for key, candidates in AF_CANDIDATES.items()
    }
    pack = latest_briefing_pack_path()
    return {
        "sources": locked.as_dict(),
        "latest_briefing_pack": pack.as_posix() if pack else None,
        "prediction": {
            "tables": pred_tables,
            "runs": _run_rows(locked.prediction_db),
            "columns": pred_columns,
        },
        "all_fields": {
            "tables": af_tables,
            "runs": _run_rows(locked.all_fields_db, 5),
            "all_fields_rows_column_count": len(af_available),
            "whitelist_present": whitelist,
        },
        "notes": list(SCHEMA_NOTES) + list(locked.notes),
    }
