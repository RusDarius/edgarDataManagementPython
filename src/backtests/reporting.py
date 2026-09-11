from __future__ import annotations

import csv
import gc
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from backtests.config import BacktestConfig, load_backtest_config

RAW_ROW_TABLES = frozenset({"signal_rows", "outcome_rows"})


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _as_record(row: Any) -> dict[str, Any]:
    if hasattr(row, "to_record"):
        return row.to_record()
    if isinstance(row, Mapping):
        return dict(row)
    raise TypeError(f"Cannot persist row of type {type(row)!r}")


def build_output_layout(*, output_root: Path) -> dict[str, Any]:
    created_at = datetime.now(tz=timezone.utc)
    run_id = f"backtests_{created_at.strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return {"run_id": run_id, "created_at_utc": created_at, "run_dir": run_dir}


def _union_fieldnames(rows: Sequence[Any]) -> list[str]:
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        record = row if isinstance(row, Mapping) else _as_record(row)
        for key in record:
            if key in seen:
                continue
            seen.add(key)
            fieldnames.append(key)
    return fieldnames


def _write_csv(path: Path, rows: Sequence[Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return 0
    first_record = _as_record(rows[0])
    if hasattr(rows[0], "to_record"):
        fieldnames = list(first_record.keys())
    else:
        fieldnames = _union_fieldnames(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerow({key: first_record.get(key, "") for key in fieldnames})
        for row in rows[1:]:
            record = _as_record(row)
            writer.writerow({key: record.get(key, "") for key in fieldnames})
    return len(rows)


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _csv_row_count(path: Path) -> int | None:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header is None:
                return 0
            return sum(1 for _ in reader)
    except OSError:
        return None


def _configure_duckdb(conn: Any, *, config: BacktestConfig, temp_dir: Path) -> None:
    temp_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET memory_limit={_q(config.output.duckdb_memory_limit)}")
    conn.execute(f"SET temp_directory={_q(temp_dir.as_posix())}")
    conn.execute("SET preserve_insertion_order=false")
    try:
        conn.execute("SET threads=2")
    except Exception:
        pass


def _write_duckdb(
    database_path: Path,
    table_to_csv: Mapping[str, Path],
    *,
    config: BacktestConfig,
) -> dict[str, str]:
    duckdb = _import_duckdb()
    temp_dir = database_path.parent / "_duckdb_tmp"
    building_path = database_path.with_name(database_path.stem + ".building.duckdb")
    if building_path.exists():
        building_path.unlink()
    persist_modes: dict[str, str] = {}
    conn = duckdb.connect(building_path.as_posix())
    try:
        _configure_duckdb(conn, config=config, temp_dir=temp_dir)
        max_bytes = config.output.duckdb_materialize_max_bytes
        for table_name, csv_path in table_to_csv.items():
            if not csv_path.exists() or csv_path.stat().st_size == 0:
                conn.execute(f"CREATE TABLE {table_name}(placeholder VARCHAR)")
                persist_modes[table_name] = "empty_table"
                continue
            csv_size = csv_path.stat().st_size
            csv_sql = _q(csv_path.as_posix())
            scan_sql = (
                f"SELECT * FROM read_csv_auto({csv_sql}, HEADER=TRUE, SAMPLE_SIZE=20480)"
            )
            if csv_size > max_bytes:
                conn.execute(f"CREATE VIEW {table_name} AS {scan_sql}")
                persist_modes[table_name] = "csv_view"
                continue
            conn.execute(f"CREATE TABLE {table_name} AS {scan_sql}")
            persist_modes[table_name] = "table"
    except Exception:
        conn.close()
        if building_path.exists():
            building_path.unlink()
        raise
    conn.close()
    if database_path.exists():
        database_path.unlink()
    building_path.replace(database_path)
    try:
        if temp_dir.is_dir() and not any(temp_dir.iterdir()):
            temp_dir.rmdir()
    except OSError:
        pass
    return persist_modes


def _best_rows(
    metric_rows: Sequence[Mapping[str, Any]], *, max_rows: int
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    scored = []
    for row in metric_rows:
        ic = row.get("spearman_ic")
        lift = row.get("top_20_lift_pct")
        if ic is None or lift is None or str(ic).strip() == "" or str(lift).strip() == "":
            continue
        try:
            score = float(ic)
        except (TypeError, ValueError):
            continue
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    best = [row for _, row in scored[:max_rows]]
    worst = [row for _, row in sorted(scored, key=lambda item: item[0])[:max_rows]]
    return best, worst


def _format_float(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def write_evidence_report(
    *,
    run_dir: Path,
    config: BacktestConfig,
    coverage_rows: Sequence[Mapping[str, Any]],
    metric_rows: Sequence[Mapping[str, Any]],
    overlay_rows: Sequence[Mapping[str, Any]],
    adapter_coverage: Mapping[str, Mapping[str, Any]],
    persist_notes: Sequence[str] | None = None,
) -> Path:
    best_rows, worst_rows = _best_rows(
        metric_rows, max_rows=config.output.max_report_signals
    )
    lines = [
        "# Generic Backtests Evidence Report",
        "",
        f"- config_id: `{config.config_id}`",
        f"- source_config: `{config.source_path.as_posix()}`",
        f"- calendar_days: `{len(coverage_rows)}`",
        f"- metric_rows: `{len(metric_rows)}`",
        f"- overlay_rows: `{len(overlay_rows)}`",
        "",
        "## Dataset Coverage Ledger",
        "",
    ]

    if coverage_rows:
        total = len(coverage_rows)
        coverage_keys = [
            "has_prediction",
            "has_edge_parent",
            "has_upside_scan",
            "has_timing_run",
            "has_projection_growth",
            "has_projection_price",
        ]
        for key in coverage_keys:
            count = sum(int(row.get(key) or 0) for row in coverage_rows)
            lines.append(f"- {key}: {count}/{total}")
    else:
        lines.append("- no calendar rows were resolved")

    lines.extend(["", "### Adapter Coverage", ""])
    if adapter_coverage:
        for family, payload in sorted(adapter_coverage.items()):
            lines.append(f"- {family}: `{json.dumps(payload, sort_keys=True)}`")
    else:
        lines.append("- adapter coverage was not available for this report")

    lines.extend(["", "## What Works (current evidence)", ""])
    if not best_rows:
        lines.append("- not enough scored rows to claim strong positive evidence yet")
    else:
        for row in best_rows:
            lines.append(
                "- "
                + f"{row.get('family')}:{row.get('signal_name')} "
                + f"h={row.get('horizon_days')}d "
                + f"IC={_format_float(row.get('spearman_ic'))} "
                + f"QSpread={row.get('quintile_spread_pct')} "
                + f"Top20Lift={row.get('top_20_lift_pct')}"
            )

    lines.extend(["", "## Needs Change / Refinement", ""])
    if not worst_rows:
        lines.append("- no clearly negative rows were ranked")
    else:
        for row in worst_rows:
            lines.append(
                "- "
                + f"{row.get('family')}:{row.get('signal_name')} "
                + f"h={row.get('horizon_days')}d "
                + f"IC={_format_float(row.get('spearman_ic'))} "
                + f"QSpread={row.get('quintile_spread_pct')} "
                + f"Top20Lift={row.get('top_20_lift_pct')}"
            )

    lines.extend(["", "## Overlay Lift vs Picks Only", ""])
    if not overlay_rows:
        lines.append("- overlay rows not available for this run")
    else:
        for row in overlay_rows:
            lines.append(
                "- "
                + f"{row.get('variant')} h={row.get('horizon_days')}d "
                + f"ICLift={row.get('spearman_ic_lift_vs_picks_only')} "
                + f"Top20Lift={row.get('top_20_avg_return_pct_lift_vs_picks_only')}"
            )

    lines.extend(
        [
            "",
            "## Interpretation Boundaries",
            "",
            "- This report is diagnostic evidence, not a standalone trading system.",
            "- Costs/slippage/FX/capacity are out of scope for this iteration.",
            "- Missing artifacts are reported as coverage gaps rather than silently dropped.",
            "- Extreme all-fields Top20Lift values can be outlier-driven; prefer IC and coverage.",
        ]
    )
    if persist_notes:
        lines.extend(["", "## Persist Status", ""])
        for note in persist_notes:
            lines.append(f"- {note}")

    report_path = run_dir / "evidence_report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _write_manifest(
    *,
    run_dir: Path,
    run_id: str,
    created_at_utc: str,
    config: BacktestConfig,
    table_row_counts: Mapping[str, int],
    adapter_coverage: Mapping[str, Mapping[str, Any]],
    label_metadata: Mapping[str, Any],
    table_csv_paths: Mapping[str, Path],
    database_path: Path,
    report_path: Path,
    duckdb_error: str | None,
    duckdb_persist_modes: Mapping[str, str],
) -> Path:
    manifest_payload = {
        "run_id": run_id,
        "created_at_utc": created_at_utc,
        "config_id": config.config_id,
        "config_path": config.source_path.as_posix(),
        "table_row_counts": dict(table_row_counts),
        "adapter_coverage": adapter_coverage,
        "label_metadata": label_metadata,
        "duckdb_error": duckdb_error,
        "duckdb_persist_modes": dict(duckdb_persist_modes),
        "outputs": {
            "run_dir": run_dir.as_posix(),
            "database_path": database_path.as_posix(),
            "report_path": report_path.as_posix(),
            "csv_tables": {
                table_name: csv_path.as_posix()
                for table_name, csv_path in table_csv_paths.items()
            },
        },
    }
    manifest_path = run_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest_payload, indent=2), encoding="utf-8")
    return manifest_path


def _mapping_rows(rows: Sequence[Any]) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            out.append(row)
        elif hasattr(row, "to_record"):
            out.append(row.to_record())
        else:
            out.append({"value": str(row)})
    return out


def write_backtest_outputs(
    *,
    config: BacktestConfig,
    tables: Mapping[str, Sequence[Any]],
    adapter_coverage: Mapping[str, Mapping[str, Any]],
    label_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    layout = build_output_layout(output_root=config.output.root_dir)
    run_dir: Path = layout["run_dir"]
    created_at_utc = layout["created_at_utc"].isoformat()

    table_csv_paths: dict[str, Path] = {}
    table_row_counts: dict[str, int] = {}
    for table_name, rows in tables.items():
        if not config.output.write_csv:
            table_row_counts[table_name] = len(rows)
            continue
        if table_name in RAW_ROW_TABLES and not config.output.persist_raw_row_tables:
            table_row_counts[table_name] = len(rows)
            continue
        csv_path = run_dir / f"{table_name}.csv"
        table_row_counts[table_name] = _write_csv(csv_path, rows)
        table_csv_paths[table_name] = csv_path
        if table_name in RAW_ROW_TABLES:
            gc.collect()

    persist_notes = [
        "CSV tables are written before DuckDB ingest.",
        "Tables larger than duckdb_materialize_max_bytes are stored as CSV-backed views.",
    ]
    report_path = write_evidence_report(
        run_dir=run_dir,
        config=config,
        coverage_rows=_mapping_rows(tables.get("coverage_ledger", [])),
        metric_rows=_mapping_rows(tables.get("family_metrics", [])),
        overlay_rows=_mapping_rows(tables.get("overlay_comparison", [])),
        adapter_coverage=adapter_coverage,
        persist_notes=persist_notes,
    )

    database_path = run_dir / "backtests.duckdb"
    duckdb_error: str | None = None
    duckdb_persist_modes: dict[str, str] = {}
    try:
        duckdb_persist_modes = _write_duckdb(
            database_path, table_csv_paths, config=config
        )
    except Exception as exc:
        duckdb_error = f"{type(exc).__name__}: {exc}"
        persist_notes = list(persist_notes) + [
            f"DuckDB ingest failed after CSVs and the evidence report were written: {duckdb_error}",
            "Re-run with --finalize-run <run_dir> after upgrading persist, or query the CSVs directly.",
        ]
        report_path = write_evidence_report(
            run_dir=run_dir,
            config=config,
            coverage_rows=_mapping_rows(tables.get("coverage_ledger", [])),
            metric_rows=_mapping_rows(tables.get("family_metrics", [])),
            overlay_rows=_mapping_rows(tables.get("overlay_comparison", [])),
            adapter_coverage=adapter_coverage,
            persist_notes=persist_notes,
        )

    manifest_path = _write_manifest(
        run_dir=run_dir,
        run_id=layout["run_id"],
        created_at_utc=created_at_utc,
        config=config,
        table_row_counts=table_row_counts,
        adapter_coverage=adapter_coverage,
        label_metadata=label_metadata,
        table_csv_paths=table_csv_paths,
        database_path=database_path,
        report_path=report_path,
        duckdb_error=duckdb_error,
        duckdb_persist_modes=duckdb_persist_modes,
    )

    return {
        "run_id": layout["run_id"],
        "created_at_utc": created_at_utc,
        "run_dir": run_dir.as_posix(),
        "database_path": database_path.as_posix(),
        "report_path": report_path.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "duckdb_error": duckdb_error,
        "duckdb_persist_modes": duckdb_persist_modes,
    }


def finalize_backtest_run(
    run_dir: str | Path,
    *,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Backtest run directory does not exist: {run_dir}")
    config = load_backtest_config(config_path)

    table_csv_paths = {
        path.stem: path
        for path in sorted(run_dir.glob("*.csv"))
        if path.is_file()
    }
    table_row_counts: dict[str, int] = {}
    for table_name, csv_path in table_csv_paths.items():
        if table_name in RAW_ROW_TABLES:
            continue
        counted = _csv_row_count(csv_path)
        table_row_counts[table_name] = counted if counted is not None else -1

    coverage_rows = _read_csv_rows(run_dir / "coverage_ledger.csv")
    metric_rows = _read_csv_rows(run_dir / "family_metrics.csv")
    overlay_rows = _read_csv_rows(run_dir / "overlay_comparison.csv")
    existing_manifest: dict[str, Any] = {}
    existing_manifest_path = run_dir / "run_manifest.json"
    if existing_manifest_path.exists() and existing_manifest_path.stat().st_size > 0:
        existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))

    adapter_coverage = existing_manifest.get("adapter_coverage") or {}
    label_metadata = existing_manifest.get("label_metadata") or {
        "finalized_from_csv": True,
        "source_run_dir": run_dir.as_posix(),
    }
    manifest_csv_rows = _read_csv_rows(run_dir / "run_manifest.csv")
    if manifest_csv_rows:
        row = manifest_csv_rows[0]
        key_map = {
            "signal_row_count": "signal_rows",
            "outcome_row_count": "outcome_rows",
            "calendar_day_count": "calendar_day_count",
        }
        for source_key, table_name in key_map.items():
            if source_key in row and row[source_key] not in (None, ""):
                try:
                    table_row_counts[table_name] = int(float(row[source_key]))
                except (TypeError, ValueError):
                    pass

    persist_notes = [
        "This report was finalized from already-written CSVs (DuckDB ingest is non-blocking).",
        "signal_rows.csv / outcome_rows.csv are left on disk; DuckDB stores large files as views.",
    ]
    report_path = write_evidence_report(
        run_dir=run_dir,
        config=config,
        coverage_rows=coverage_rows,
        metric_rows=metric_rows,
        overlay_rows=overlay_rows,
        adapter_coverage=adapter_coverage,
        persist_notes=persist_notes,
    )

    database_path = run_dir / "backtests.duckdb"
    duckdb_error: str | None = None
    duckdb_persist_modes: dict[str, str] = {}
    try:
        duckdb_persist_modes = _write_duckdb(
            database_path, table_csv_paths, config=config
        )
    except Exception as exc:
        duckdb_error = f"{type(exc).__name__}: {exc}"
        persist_notes = list(persist_notes) + [f"DuckDB finalize failed: {duckdb_error}"]
        report_path = write_evidence_report(
            run_dir=run_dir,
            config=config,
            coverage_rows=coverage_rows,
            metric_rows=metric_rows,
            overlay_rows=overlay_rows,
            adapter_coverage=adapter_coverage,
            persist_notes=persist_notes,
        )

    created_at = existing_manifest.get("created_at_utc") or datetime.now(
        tz=timezone.utc
    ).isoformat()
    manifest_path = _write_manifest(
        run_dir=run_dir,
        run_id=str(existing_manifest.get("run_id") or run_dir.name),
        created_at_utc=str(created_at),
        config=config,
        table_row_counts=table_row_counts,
        adapter_coverage=adapter_coverage,
        label_metadata=label_metadata,
        table_csv_paths=table_csv_paths,
        database_path=database_path,
        report_path=report_path,
        duckdb_error=duckdb_error,
        duckdb_persist_modes=duckdb_persist_modes,
    )
    return {
        "run_id": run_dir.name,
        "created_at_utc": created_at,
        "run_dir": run_dir.as_posix(),
        "database_path": database_path.as_posix(),
        "report_path": report_path.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "duckdb_error": duckdb_error,
        "duckdb_persist_modes": duckdb_persist_modes,
        "calendar_day_count": len(coverage_rows),
        "signal_row_count": table_row_counts.get("signal_rows"),
        "outcome_row_count": table_row_counts.get("outcome_rows"),
    }
