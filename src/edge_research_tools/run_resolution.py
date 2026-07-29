"""Shared run-reference resolution helpers.

Extracted from ``run_edge_research_tools.py`` so that other standalone entry
points (e.g. the upside-opportunity scanner) can resolve a prior edge-research
parent run directory or a bare ``symbol_day_feature_snapshot.duckdb`` path
without duplicating this resolution logic. ``run_edge_research_tools.py``
keeps thin private wrappers around these functions so its own call sites and
behavior are unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .config import resolve_edge_research_paths


def resolve_project_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def discover_edge_snapshot_run_refs(
    output_root: str | Path | None = None,
) -> list[Path]:
    paths = resolve_edge_research_paths(output_root=output_root)
    discovered: list[tuple[float, Path]] = []
    seen: set[str] = set()
    for root in (paths.output_root, paths.foundation_root):
        if not root.exists():
            continue
        for db_path in root.glob("**/symbol_day_feature_snapshot.duckdb"):
            parent = db_path.parent.resolve()
            key = parent.as_posix()
            if key in seen:
                continue
            seen.add(key)
            discovered.append((db_path.stat().st_mtime, parent))
    discovered.sort(key=lambda item: item[0], reverse=True)
    return [parent for _, parent in discovered]


def coalesce_existing_run_ref(
    existing_run_ref: str | Path | None,
    *,
    output_root: str | Path | None = None,
    auto_discover_latest: bool = False,
) -> Path | None:
    if existing_run_ref is None:
        if not auto_discover_latest:
            return None
        discovered = discover_edge_snapshot_run_refs(output_root=output_root)
        if not discovered:
            raise FileNotFoundError(
                "No snapshot runs found under edge_research_tools. "
                "Set rebuild_foundation_snapshot=True in run_edge_research_local_main() "
                "to seed the ongoing foundation base, or "
                'rebuild_foundation_snapshot="extend" to append missing days '
                "or refresh the current day when a newer same-day scan exists."
            )
        return discovered[0]

    path = resolve_project_path(existing_run_ref)
    if path.exists():
        return path

    if path.name.startswith(
        ("edge_feature_snapshot_", "edge_latest_500m_full_parent_")
    ):
        alt_roots = []
        paths = resolve_edge_research_paths(output_root=output_root)
        alt_roots.extend([paths.foundation_root, paths.output_root])
        for alt_root in alt_roots:
            alt_path = alt_root / path.name
            if alt_path.exists():
                return alt_path.resolve()

    discovered = discover_edge_snapshot_run_refs(output_root=output_root)
    message_lines = [f"Edge research run reference not found: {path}"]
    if discovered:
        message_lines.append("Available snapshot runs:")
        for candidate in discovered[:10]:
            try:
                rel = candidate.relative_to(PROJECT_ROOT).as_posix()
            except ValueError:
                rel = candidate.as_posix()
            message_lines.append(f"  - {rel}")
    else:
        message_lines.append(
            "No snapshot runs found under edge_research_tools. "
            "Set existing_run_ref = None to build a fresh foundation snapshot."
        )
    raise FileNotFoundError("\n".join(message_lines))


def day_label_to_iso_date(day_label: str) -> str:
    return datetime.strptime(day_label, "%d_%m_%Y").date().isoformat()


def read_snapshot_day_range(
    *,
    snapshot_db: str | Path,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
) -> tuple[str, str]:
    if start_day_label and end_day_label:
        return start_day_label, end_day_label

    manifest_path = (
        Path(snapshot_db).parent / "symbol_day_feature_snapshot_manifest.json"
    )
    if not manifest_path.exists():
        raise ValueError(
            "Could not resolve snapshot day range. Provide start_day_label and "
            f"end_day_label, or use a snapshot with manifest: {manifest_path}"
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    resolved_start = payload.get("start_day_label")
    resolved_end = payload.get("end_day_label")
    if not resolved_start or not resolved_end:
        raise ValueError(
            f"Snapshot manifest is missing start_day_label/end_day_label: {manifest_path}"
        )
    return str(resolved_start), str(resolved_end)


def resolve_edge_research_run_reference(
    run_ref: str | Path,
) -> dict[str, Any]:
    """Resolve a prior run folder or snapshot DB into reusable scan context."""
    path = resolve_project_path(run_ref)
    if not path.exists():
        raise FileNotFoundError(f"Edge research run reference not found: {path}")

    def _context_from_snapshot_db(
        snapshot_db: Path,
        *,
        source_run_ref: Path | None = None,
        parent_run_dir: Path | None = None,
    ) -> dict[str, Any]:
        start_day_label, end_day_label = read_snapshot_day_range(
            snapshot_db=snapshot_db
        )
        return {
            "snapshot_db": snapshot_db.resolve(),
            "start_day_label": start_day_label,
            "end_day_label": end_day_label,
            "scan_day": day_label_to_iso_date(end_day_label),
            "window_start_date": day_label_to_iso_date(start_day_label),
            "source_run_ref": (source_run_ref or path).resolve(),
            "parent_run_dir": parent_run_dir.resolve() if parent_run_dir else None,
        }

    def _apply_parent_manifest_dates(
        context: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if payload.get("start_day_label"):
            context["start_day_label"] = str(payload["start_day_label"])
        if payload.get("end_day_label"):
            context["end_day_label"] = str(payload["end_day_label"])
        if payload.get("scan_day"):
            context["scan_day"] = str(payload["scan_day"])
        if payload.get("window_start_date"):
            context["window_start_date"] = str(payload["window_start_date"])
        elif context.get("start_day_label") and context.get("end_day_label"):
            context["window_start_date"] = day_label_to_iso_date(
                str(context["start_day_label"])
            )
            context["scan_day"] = day_label_to_iso_date(str(context["end_day_label"]))
        return context

    def _context_from_suite_manifest(
        manifest_path: Path,
        *,
        source_run_ref: Path,
        parent_run_dir: Path | None = None,
    ) -> dict[str, Any]:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        snapshot_result = payload.get("snapshot_result")
        if not isinstance(snapshot_result, dict):
            raise ValueError(
                f"Suite manifest is missing snapshot_result: {manifest_path}"
            )
        snapshot_db_raw = snapshot_result.get("database_path")
        if not snapshot_db_raw:
            raise ValueError(
                f"Suite manifest is missing snapshot_result.database_path: {manifest_path}"
            )
        snapshot_db = Path(str(snapshot_db_raw))
        if not snapshot_db.exists():
            raise FileNotFoundError(
                f"Snapshot database from suite manifest does not exist: {snapshot_db}"
            )
        context = _context_from_snapshot_db(
            snapshot_db,
            source_run_ref=source_run_ref,
            parent_run_dir=parent_run_dir,
        )
        if payload.get("start_day_label"):
            context["start_day_label"] = str(payload["start_day_label"])
        if payload.get("end_day_label"):
            context["end_day_label"] = str(payload["end_day_label"])
        if context.get("start_day_label") and context.get("end_day_label"):
            context["window_start_date"] = day_label_to_iso_date(
                str(context["start_day_label"])
            )
            context["scan_day"] = day_label_to_iso_date(str(context["end_day_label"]))
        return context

    if path.is_file():
        if path.suffix.lower() != ".duckdb":
            raise ValueError(f"Expected a .duckdb file, got: {path}")
        return _context_from_snapshot_db(path)

    direct_db = path / "symbol_day_feature_snapshot.duckdb"
    if direct_db.exists():
        return _context_from_snapshot_db(direct_db, source_run_ref=path)

    parent_manifest_path = path / "parent_run_manifest.json"
    if parent_manifest_path.exists():
        parent_payload = json.loads(parent_manifest_path.read_text(encoding="utf-8"))
        snapshot_db_raw = parent_payload.get("snapshot_db")
        if snapshot_db_raw:
            snapshot_db = Path(str(snapshot_db_raw))
            if snapshot_db.exists():
                context = _context_from_snapshot_db(
                    snapshot_db,
                    source_run_ref=path,
                    parent_run_dir=path,
                )
                return _apply_parent_manifest_dates(context, parent_payload)

        foundation_suite_manifest = (
            path / "foundation" / "volatility_liquidity_edge_suite_manifest.json"
        )
        if foundation_suite_manifest.exists():
            return _context_from_suite_manifest(
                foundation_suite_manifest,
                source_run_ref=path,
                parent_run_dir=path,
            )

    suite_manifest_path = path / "volatility_liquidity_edge_suite_manifest.json"
    if suite_manifest_path.exists():
        return _context_from_suite_manifest(
            suite_manifest_path,
            source_run_ref=path,
        )

    foundation_suite_manifest = (
        path / "foundation" / "volatility_liquidity_edge_suite_manifest.json"
    )
    if foundation_suite_manifest.exists():
        return _context_from_suite_manifest(
            foundation_suite_manifest,
            source_run_ref=path,
            parent_run_dir=path,
        )

    matches = sorted(path.glob("**/symbol_day_feature_snapshot.duckdb"))
    if len(matches) == 1:
        return _context_from_snapshot_db(
            matches[0],
            source_run_ref=path,
            parent_run_dir=path if matches[0].parent != path else None,
        )
    if len(matches) > 1:
        preview = ", ".join(match.as_posix() for match in matches[:5])
        raise ValueError(
            "Ambiguous edge research run reference; multiple snapshot databases "
            f"found under {path.as_posix()}: {preview}"
        )

    raise ValueError(
        "Could not resolve an edge research snapshot database from run reference: "
        f"{path.as_posix()}"
    )


def discover_latest_edge_parent_run_dir(
    *,
    output_root: str | Path | None,
) -> Path:
    output_paths = resolve_edge_research_paths(output_root=output_root)
    candidates = sorted(
        (
            path
            for path in output_paths.output_root.iterdir()
            if path.is_dir() and (path / "parent_run_manifest.json").exists()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            "No parent edge-research run directories were found under "
            f"{output_paths.output_root.as_posix()}."
        )
    return candidates[0]


def resolve_edge_parent_run_dir(
    *,
    run_ref: str | Path | None,
    output_root: str | Path | None,
    auto_discover_latest: bool,
) -> Path:
    if run_ref is None:
        if not auto_discover_latest:
            raise ValueError("run_ref is required when auto_discover_latest is False.")
        return discover_latest_edge_parent_run_dir(output_root=output_root)

    path = resolve_project_path(run_ref)
    if path.is_file():
        current = path.parent
    else:
        current = path
    for candidate in (current, *current.parents):
        if (candidate / "parent_run_manifest.json").exists():
            return candidate

    context = resolve_edge_research_run_reference(path)
    parent_run_dir = context.get("parent_run_dir")
    if parent_run_dir is None:
        raise ValueError(
            "Run reference does not resolve to an aggregate parent run directory: "
            f"{path.as_posix()}"
        )
    return Path(parent_run_dir)
