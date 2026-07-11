from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    _extract_day_label_from_database_path,
    discover_all_fields_daily_databases,
)

from .config import (
    EdgeResearchPaths,
    build_edge_research_run_context,
    resolve_edge_research_paths,
)


@dataclass(frozen=True)
class DailyDatabaseInventoryRow:
    day_label: str
    database_path: str


@dataclass(frozen=True)
class ScanPeriodRunInventoryRow:
    run_name: str
    run_root: str
    modified_at_utc: str | None
    has_period_total: bool
    has_aggregates: bool
    has_progression: bool
    has_rolling_windows: bool
    has_upside_edge_research: bool
    period_patterns_parquet: str | None
    stability_parquet: str | None
    progression_parquet: str | None


def _try_parse_day_label(day_label: str) -> datetime | None:
    try:
        return datetime.strptime(day_label, "%d_%m_%Y")
    except ValueError:
        return None


def _iso_modified_time(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
    except OSError:
        return None


def _discover_scan_period_runs(
    root: Path, *, limit: int
) -> list[ScanPeriodRunInventoryRow]:
    if not root.exists() or not root.is_dir():
        return []

    candidates = sorted(
        [path for path in root.iterdir() if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    rows: list[ScanPeriodRunInventoryRow] = []
    for candidate in candidates[: max(1, limit)]:
        period_patterns_parquet = (
            candidate / "period_total" / "period_field_performance_patterns.parquet"
        )
        stability_parquet = (
            candidate / "aggregates" / "cross_run_field_stability.parquet"
        )
        progression_parquet = (
            candidate / "progression" / "period_symbol_progression.parquet"
        )
        rows.append(
            ScanPeriodRunInventoryRow(
                run_name=candidate.name,
                run_root=candidate.as_posix(),
                modified_at_utc=_iso_modified_time(candidate),
                has_period_total=period_patterns_parquet.exists(),
                has_aggregates=stability_parquet.exists(),
                has_progression=progression_parquet.exists(),
                has_rolling_windows=(candidate / "rolling_windows").exists(),
                has_upside_edge_research=(candidate / "upside_edge_research").exists(),
                period_patterns_parquet=(
                    period_patterns_parquet.as_posix()
                    if period_patterns_parquet.exists()
                    else None
                ),
                stability_parquet=(
                    stability_parquet.as_posix() if stability_parquet.exists() else None
                ),
                progression_parquet=(
                    progression_parquet.as_posix()
                    if progression_parquet.exists()
                    else None
                ),
            )
        )
    return rows


def _build_inventory_payload(
    *,
    paths: EdgeResearchPaths,
    limit: int,
) -> dict[str, Any]:
    daily_paths = discover_all_fields_daily_databases(
        all_fields_root=paths.all_fields_root
    )
    daily_rows = [
        DailyDatabaseInventoryRow(
            day_label=_extract_day_label_from_database_path(path),
            database_path=path.as_posix(),
        )
        for path in daily_paths
    ]
    daily_rows.sort(
        key=lambda row: (
            _try_parse_day_label(row.day_label) or datetime.min,
            row.database_path,
        )
    )
    scan_period_rows = _discover_scan_period_runs(
        paths.scan_period_runs_root,
        limit=limit,
    )

    taxonomy_variant_root = paths.taxonomy_root / "variants"
    master_registry_csv = taxonomy_variant_root / "master_registry.csv"
    predictor_eligible_csv = (
        taxonomy_variant_root / "by_usage" / "predictor_eligible.csv"
    )
    taxonomy_csv_count = (
        len(list(taxonomy_variant_root.rglob("*.csv")))
        if taxonomy_variant_root.exists()
        else 0
    )

    return {
        "roots": {
            "project_root": paths.project_root.as_posix(),
            "all_fields_root": paths.all_fields_root.as_posix(),
            "scan_period_runs_root": paths.scan_period_runs_root.as_posix(),
            "taxonomy_root": paths.taxonomy_root.as_posix(),
            "edge_research_root": paths.edge_research_root.as_posix(),
            "foundation_root": paths.foundation_root.as_posix(),
            "output_root": paths.output_root.as_posix(),
        },
        "daily_all_fields": {
            "database_count": len(daily_rows),
            "earliest_day_label": daily_rows[0].day_label if daily_rows else None,
            "latest_day_label": daily_rows[-1].day_label if daily_rows else None,
            "latest_database_path": (
                daily_rows[-1].database_path if daily_rows else None
            ),
            "sample_rows": [asdict(row) for row in daily_rows[-max(1, limit) :]],
        },
        "scan_period_runs": {
            "run_count": (
                len(
                    [
                        path
                        for path in paths.scan_period_runs_root.iterdir()
                        if path.is_dir()
                    ]
                )
                if paths.scan_period_runs_root.exists()
                else 0
            ),
            "sample_rows": [asdict(row) for row in scan_period_rows],
        },
        "taxonomy": {
            "variant_root": taxonomy_variant_root.as_posix(),
            "taxonomy_csv_count": taxonomy_csv_count,
            "master_registry_csv": (
                master_registry_csv.as_posix() if master_registry_csv.exists() else None
            ),
            "predictor_eligible_csv": (
                predictor_eligible_csv.as_posix()
                if predictor_eligible_csv.exists()
                else None
            ),
        },
    }


def _write_inventory_report(path: Path, payload: dict[str, Any]) -> None:
    daily = payload["daily_all_fields"]
    scan_period = payload["scan_period_runs"]
    taxonomy = payload["taxonomy"]
    roots = payload["roots"]

    lines = [
        "# Edge Research Source Inventory",
        "",
        "## Roots",
        "",
        f"- project_root: `{roots['project_root']}`",
        f"- all_fields_root: `{roots['all_fields_root']}`",
        f"- scan_period_runs_root: `{roots['scan_period_runs_root']}`",
        f"- taxonomy_root: `{roots['taxonomy_root']}`",
        f"- edge_research_root: `{roots['edge_research_root']}`",
        f"- foundation_root: `{roots['foundation_root']}`",
        f"- output_root: `{roots['output_root']}`",
        "",
        "## Daily All-Fields Databases",
        "",
        f"- database_count: {daily['database_count']}",
        f"- earliest_day_label: {daily['earliest_day_label'] or 'n/a'}",
        f"- latest_day_label: {daily['latest_day_label'] or 'n/a'}",
        f"- latest_database_path: `{daily['latest_database_path'] or ''}`",
        "",
        "### Latest Daily Samples",
        "",
        "| Day Label | Database Path |",
        "|---|---|",
    ]
    for row in daily["sample_rows"]:
        lines.append(f"| {row['day_label']} | `{row['database_path']}` |")

    lines.extend(
        [
            "",
            "## Scan-Period Tracking Runs",
            "",
            f"- run_count: {scan_period['run_count']}",
            "",
            "| Run | Period Total | Aggregates | Progression | Edge Research | Modified |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in scan_period["sample_rows"]:
        lines.append(
            "| "
            f"{row['run_name']} | "
            f"{row['has_period_total']} | "
            f"{row['has_aggregates']} | "
            f"{row['has_progression']} | "
            f"{row['has_upside_edge_research']} | "
            f"{row['modified_at_utc'] or ''} |"
        )

    lines.extend(
        [
            "",
            "## Taxonomy",
            "",
            f"- taxonomy_csv_count: {taxonomy['taxonomy_csv_count']}",
            f"- master_registry_csv: `{taxonomy['master_registry_csv'] or ''}`",
            f"- predictor_eligible_csv: `{taxonomy['predictor_eligible_csv'] or ''}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_edge_research_source_inventory(
    *,
    limit: int = 10,
    project_root: str | Path | None = None,
    all_fields_root: str | Path | None = None,
    scan_period_runs_root: str | Path | None = None,
    taxonomy_root: str | Path | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    paths = resolve_edge_research_paths(
        project_root=project_root,
        all_fields_root=all_fields_root,
        scan_period_runs_root=scan_period_runs_root,
        taxonomy_root=taxonomy_root,
        output_root=output_root,
    )
    context = build_edge_research_run_context(
        prefix="edge_research_inventory",
        output_root=paths.output_root,
    )
    payload = _build_inventory_payload(paths=paths, limit=limit)

    manifest_path = context.output_dir / "source_inventory_manifest.json"
    report_md = context.output_dir / "source_inventory_report.md"
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_inventory_report(report_md, payload)

    return {
        "run_id": context.run_id,
        "output_dir": context.output_dir.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "report_md": report_md.as_posix(),
        "daily_database_count": payload["daily_all_fields"]["database_count"],
        "latest_day_label": payload["daily_all_fields"]["latest_day_label"],
        "scan_period_run_count": payload["scan_period_runs"]["run_count"],
        "taxonomy_csv_count": payload["taxonomy"]["taxonomy_csv_count"],
    }
