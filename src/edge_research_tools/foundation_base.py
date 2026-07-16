"""Ongoing foundation snapshot base for incremental all-fields extension.

The edge research parent suite can maintain a canonical foundation database under
``foundations/edge_ongoing_base_min{cap}/`` instead of rebuilding the full
symbol-day snapshot from scratch on every run. Use ``rebuild_foundation_snapshot``:

- ``False`` — reuse an existing snapshot (aggregate-only; current default)
- ``True`` — full rebuild into the ongoing base directory
- ``"extend"`` — append only new all-fields days to the ongoing base, in chunks
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    _extract_day_label_from_database_path,
    discover_all_fields_daily_databases,
)

from .config import resolve_edge_research_paths
from .dataset_builder import (
    extend_symbol_day_feature_snapshot,
    run_symbol_day_feature_snapshot,
)
from .labeling import DEFAULT_FORWARD_LABEL_HORIZONS, run_forward_label_generation

FoundationSnapshotMode = Literal["reuse", "rebuild", "extend"]
FOUNDATION_BASE_DIR_PREFIX = "edge_ongoing_base"
FOUNDATION_BASE_STATE_FILENAME = "foundation_base_state.json"


def normalize_foundation_snapshot_mode(
    rebuild_foundation_snapshot: bool | Literal["extend"] | None,
) -> FoundationSnapshotMode:
    if rebuild_foundation_snapshot is True:
        return "rebuild"
    if rebuild_foundation_snapshot == "extend":
        return "extend"
    return "reuse"


def _format_min_cap_suffix(min_market_cap_usd: float | None) -> str:
    if min_market_cap_usd is None:
        return "all"
    cap_m = int(round(float(min_market_cap_usd) / 1_000_000))
    return f"min{cap_m}m"


def resolve_ongoing_foundation_base_dir(
    *,
    output_root: str | Path | None = None,
    min_market_cap_usd: float | None = None,
    foundation_base_ref: str | Path | None = None,
) -> Path:
    if foundation_base_ref is not None:
        return Path(foundation_base_ref).resolve()
    paths = resolve_edge_research_paths(output_root=output_root)
    cap_suffix = _format_min_cap_suffix(min_market_cap_usd)
    return (paths.foundation_root / f"{FOUNDATION_BASE_DIR_PREFIX}_{cap_suffix}").resolve()


def _try_parse_day_label(day_label: str) -> datetime | None:
    try:
        return datetime.strptime(day_label, "%d_%m_%Y")
    except ValueError:
        return None


def _day_label_to_iso_date(day_label: str) -> str:
    parsed = _try_parse_day_label(day_label)
    if parsed is None:
        raise ValueError(f"Unsupported day label format: {day_label}")
    return parsed.date().isoformat()


def _sort_day_labels(day_labels: list[str]) -> list[str]:
    return sorted(
        day_labels,
        key=lambda value: _try_parse_day_label(value) or datetime.min,
    )


def _next_day_label(day_label: str) -> str:
    parsed = _try_parse_day_label(day_label)
    if parsed is None:
        raise ValueError(f"Unsupported day label format: {day_label}")
    return (parsed + timedelta(days=1)).strftime("%d_%m_%Y")


def read_snapshot_manifest(snapshot_dir: Path) -> dict[str, Any]:
    manifest_path = snapshot_dir / "symbol_day_feature_snapshot_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            "Snapshot manifest not found for foundation base: "
            f"{manifest_path.as_posix()}"
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def read_foundation_base_state(base_dir: Path) -> dict[str, Any]:
    state_path = base_dir / FOUNDATION_BASE_STATE_FILENAME
    if not state_path.exists():
        return {
            "base_dir": base_dir.as_posix(),
            "extend_chunks": [],
        }
    return json.loads(state_path.read_text(encoding="utf-8"))


def write_foundation_base_state(base_dir: Path, payload: dict[str, Any]) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    state_path = base_dir / FOUNDATION_BASE_STATE_FILENAME
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return state_path


def discover_days_to_append(
    *,
    all_fields_root: Path,
    existing_day_labels: set[str],
    after_day_label: str | None,
    end_day_label: str | None = None,
) -> list[Path]:
    start_day_label = (
        _next_day_label(after_day_label) if after_day_label is not None else None
    )
    candidates = discover_all_fields_daily_databases(
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )
    pending: list[Path] = []
    for database_path in candidates:
        day_label = _extract_day_label_from_database_path(database_path)
        if day_label in existing_day_labels:
            continue
        pending.append(database_path)
    return pending


def _chunk_paths(
    database_paths: list[Path],
    *,
    chunk_size: int,
) -> list[list[Path]]:
    if chunk_size <= 0:
        return [database_paths]
    return [
        database_paths[index : index + chunk_size]
        for index in range(0, len(database_paths), chunk_size)
    ]


def _existing_snapshot_day_labels(snapshot_db: Path) -> set[str]:
    import duckdb

    connection = duckdb.connect(snapshot_db.as_posix(), read_only=True)
    try:
        tables = {
            str(row[0]) for row in connection.execute("SHOW TABLES").fetchall()
        }
        if "symbol_day_feature_snapshot" not in tables:
            return set()
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT source_day_label FROM symbol_day_feature_snapshot"
            ).fetchall()
        }
    finally:
        connection.close()


def _prune_oldest_snapshot_days(
    *,
    snapshot_db: Path,
    max_trading_days: int,
    duckdb_threads: int,
    memory_limit_gb: float,
) -> dict[str, Any]:
    import duckdb

    connection = duckdb.connect(snapshot_db.as_posix())
    connection.execute(f"SET threads TO {int(duckdb_threads)}")
    connection.execute(f"SET memory_limit = '{memory_limit_gb:.1f}GB'")
    try:
        day_labels = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT source_day_label
                FROM symbol_day_feature_snapshot
                GROUP BY source_day_label
                ORDER BY MIN(source_date)
                """
            ).fetchall()
        ]
        if len(day_labels) <= max_trading_days:
            return {
                "pruned": False,
                "day_count_before": len(day_labels),
                "day_count_after": len(day_labels),
                "pruned_day_labels": [],
            }
        prune_count = len(day_labels) - max_trading_days
        pruned_day_labels = day_labels[:prune_count]
        placeholders = ", ".join(
            "'" + label.replace("'", "''") + "'" for label in pruned_day_labels
        )
        for table_name in (
            "symbol_day_feature_values",
            "symbol_day_sleeve_scores",
            "symbol_day_feature_snapshot",
        ):
            connection.execute(
                f"DELETE FROM {table_name} WHERE source_day_label IN ({placeholders})"
            )
        if _table_exists(connection, "symbol_day_forward_labels"):
            connection.execute(
                "DELETE FROM symbol_day_forward_labels "
                f"WHERE source_day_label IN ({placeholders})"
            )
        connection.execute("CHECKPOINT")
        return {
            "pruned": True,
            "day_count_before": len(day_labels),
            "day_count_after": max_trading_days,
            "pruned_day_labels": pruned_day_labels,
        }
    finally:
        connection.close()


def _table_exists(connection: Any, table_name: str) -> bool:
    return bool(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_name = ?
            """,
            [table_name],
        ).fetchone()[0]
    )


def rebuild_ongoing_foundation_base(
    *,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    use_full_range: bool = False,
    all_fields_root: str | Path | None = None,
    taxonomy_root: str | Path | None = None,
    output_root: str | Path | None = None,
    foundation_base_ref: str | Path | None = None,
    primary_only: bool = True,
    min_market_cap_usd: float | None = None,
    horizons: tuple[int, ...] = DEFAULT_FORWARD_LABEL_HORIZONS,
    target_pct: float = 10.0,
    stop_pct: float = 7.0,
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
) -> dict[str, Any]:
    """Build a fresh foundation snapshot directly into the ongoing base directory."""
    from .suite import _resolve_full_day_range

    paths = resolve_edge_research_paths(
        all_fields_root=all_fields_root,
        taxonomy_root=taxonomy_root,
        output_root=output_root,
    )
    base_dir = resolve_ongoing_foundation_base_dir(
        output_root=output_root,
        min_market_cap_usd=min_market_cap_usd,
        foundation_base_ref=foundation_base_ref,
    )
    resolved_start_day_label = start_day_label
    resolved_end_day_label = end_day_label
    if use_full_range or not resolved_start_day_label or not resolved_end_day_label:
        resolved_start_day_label, resolved_end_day_label = _resolve_full_day_range(
            paths.all_fields_root
        )

    if base_dir.exists():
        backup_dir = base_dir.with_name(base_dir.name + "_rebuild_backup")
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.move(base_dir.as_posix(), backup_dir.as_posix())
    base_dir.mkdir(parents=True, exist_ok=True)

    snapshot_result = run_symbol_day_feature_snapshot(
        start_day_label=resolved_start_day_label,
        end_day_label=resolved_end_day_label,
        all_fields_root=paths.all_fields_root,
        taxonomy_root=paths.taxonomy_root,
        output_dir=base_dir,
        primary_only=primary_only,
        min_market_cap_usd=min_market_cap_usd,
        duckdb_threads=duckdb_threads,
        memory_limit_gb=memory_limit_gb,
    )
    label_result = run_forward_label_generation(
        snapshot_database_path=snapshot_result["database_path"],
        horizons=horizons,
        target_pct=target_pct,
        stop_pct=stop_pct,
        duckdb_threads=duckdb_threads,
        memory_limit_gb=memory_limit_gb,
    )
    state_path = write_foundation_base_state(
        base_dir,
        {
            "mode": "rebuild",
            "base_dir": base_dir.as_posix(),
            "start_day_label": resolved_start_day_label,
            "end_day_label": resolved_end_day_label,
            "min_market_cap_usd": min_market_cap_usd,
            "extend_chunks": [],
            "last_rebuild_at_utc": datetime.utcnow().isoformat() + "Z",
        },
    )
    return {
        "mode": "rebuild",
        "output_dir": base_dir.as_posix(),
        "foundation_base_state_path": state_path.as_posix(),
        "start_day_label": resolved_start_day_label,
        "end_day_label": resolved_end_day_label,
        "snapshot_result": snapshot_result,
        "label_result": label_result,
        "suite_manifest": None,
        "suite_report": None,
        "setup_results": [],
    }


def extend_ongoing_foundation_base(
    *,
    all_fields_root: str | Path | None = None,
    taxonomy_root: str | Path | None = None,
    output_root: str | Path | None = None,
    foundation_base_ref: str | Path | None = None,
    end_day_label: str | None = None,
    min_market_cap_usd: float | None = None,
    primary_only: bool = True,
    extend_chunk_days: int = 30,
    max_trading_days: int | None = 504,
    horizons: tuple[int, ...] = DEFAULT_FORWARD_LABEL_HORIZONS,
    target_pct: float = 10.0,
    stop_pct: float = 7.0,
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
    checkpoint_every_n: int = 10,
) -> dict[str, Any]:
    """Append new all-fields days to the canonical ongoing foundation base."""
    paths = resolve_edge_research_paths(
        all_fields_root=all_fields_root,
        taxonomy_root=taxonomy_root,
        output_root=output_root,
    )
    base_dir = resolve_ongoing_foundation_base_dir(
        output_root=output_root,
        min_market_cap_usd=min_market_cap_usd,
        foundation_base_ref=foundation_base_ref,
    )
    snapshot_db = base_dir / "symbol_day_feature_snapshot.duckdb"
    if not snapshot_db.exists():
        raise FileNotFoundError(
            "No ongoing foundation base snapshot exists at "
            f"{snapshot_db.as_posix()}. "
            "Set rebuild_foundation_snapshot=True once to seed the base, then use "
            'rebuild_foundation_snapshot="extend" for daily updates.'
        )

    manifest = read_snapshot_manifest(base_dir)
    existing_after_day_label = str(manifest["end_day_label"])
    existing_day_labels = _existing_snapshot_day_labels(snapshot_db)
    pending_paths = discover_days_to_append(
        all_fields_root=paths.all_fields_root,
        existing_day_labels=existing_day_labels,
        after_day_label=existing_after_day_label,
        end_day_label=end_day_label,
    )
    if not pending_paths:
        label_result = run_forward_label_generation(
            snapshot_database_path=snapshot_db,
            horizons=horizons,
            target_pct=target_pct,
            stop_pct=stop_pct,
            duckdb_threads=duckdb_threads,
            memory_limit_gb=memory_limit_gb,
        )
        state = read_foundation_base_state(base_dir)
        state.update(
            {
                "mode": "extend",
                "end_day_label": existing_after_day_label,
                "days_appended": 0,
                "last_extend_at_utc": datetime.utcnow().isoformat() + "Z",
                "last_extend_note": "no_new_all_fields_days",
            }
        )
        state_path = write_foundation_base_state(base_dir, state)
        return {
            "mode": "extend",
            "output_dir": base_dir.as_posix(),
            "foundation_base_state_path": state_path.as_posix(),
            "start_day_label": str(manifest["start_day_label"]),
            "end_day_label": existing_after_day_label,
            "days_appended": 0,
            "extend_chunks": [],
            "prune_result": None,
            "snapshot_result": {
                "output_dir": base_dir.as_posix(),
                "database_path": snapshot_db.as_posix(),
                "manifest_path": (
                    base_dir / "symbol_day_feature_snapshot_manifest.json"
                ).as_posix(),
            },
            "label_result": label_result,
            "suite_manifest": None,
            "suite_report": None,
            "setup_results": [],
        }

    chunk_results: list[dict[str, Any]] = []
    for chunk_index, chunk_paths in enumerate(
        _chunk_paths(pending_paths, chunk_size=extend_chunk_days)
    ):
        chunk_day_labels = [
            _extract_day_label_from_database_path(path) for path in chunk_paths
        ]
        extend_result = extend_symbol_day_feature_snapshot(
            snapshot_database_path=snapshot_db,
            database_paths=chunk_paths,
            all_fields_root=paths.all_fields_root,
            taxonomy_root=paths.taxonomy_root,
            primary_only=primary_only,
            min_market_cap_usd=min_market_cap_usd,
            duckdb_threads=duckdb_threads,
            memory_limit_gb=memory_limit_gb,
            checkpoint_every_n=checkpoint_every_n,
        )
        chunk_results.append(
            {
                "chunk_index": chunk_index,
                "start_day_label": chunk_day_labels[0],
                "end_day_label": chunk_day_labels[-1],
                "day_count": len(chunk_day_labels),
                "extend_result": extend_result,
            }
        )

    prune_result = None
    if max_trading_days is not None and max_trading_days > 0:
        prune_result = _prune_oldest_snapshot_days(
            snapshot_db=snapshot_db,
            max_trading_days=max_trading_days,
            duckdb_threads=duckdb_threads,
            memory_limit_gb=memory_limit_gb,
        )

    updated_manifest = read_snapshot_manifest(base_dir)
    label_result = run_forward_label_generation(
        snapshot_database_path=snapshot_db,
        horizons=horizons,
        target_pct=target_pct,
        stop_pct=stop_pct,
        duckdb_threads=duckdb_threads,
        memory_limit_gb=memory_limit_gb,
    )
    state = read_foundation_base_state(base_dir)
    extend_chunks = list(state.get("extend_chunks") or [])
    extend_chunks.extend(chunk_results)
    state.update(
        {
            "mode": "extend",
            "base_dir": base_dir.as_posix(),
            "start_day_label": updated_manifest.get("start_day_label"),
            "end_day_label": updated_manifest.get("end_day_label"),
            "min_market_cap_usd": min_market_cap_usd,
            "extend_chunks": extend_chunks[-50:],
            "days_appended": len(pending_paths),
            "last_extend_at_utc": datetime.utcnow().isoformat() + "Z",
            "prune_result": prune_result,
        }
    )
    state_path = write_foundation_base_state(base_dir, state)
    return {
        "mode": "extend",
        "output_dir": base_dir.as_posix(),
        "foundation_base_state_path": state_path.as_posix(),
        "start_day_label": str(updated_manifest["start_day_label"]),
        "end_day_label": str(updated_manifest["end_day_label"]),
        "days_appended": len(pending_paths),
        "extend_chunks": chunk_results,
        "prune_result": prune_result,
        "snapshot_result": {
            "output_dir": base_dir.as_posix(),
            "database_path": snapshot_db.as_posix(),
            "manifest_path": (
                base_dir / "symbol_day_feature_snapshot_manifest.json"
            ).as_posix(),
            "row_count": updated_manifest.get("row_count"),
        },
        "label_result": label_result,
        "suite_manifest": None,
        "suite_report": None,
        "setup_results": [],
    }
