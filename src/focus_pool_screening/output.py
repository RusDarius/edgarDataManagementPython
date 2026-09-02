"""Persist focus pool screening outputs: DuckDB focus map, CSV, human log."""

from __future__ import annotations

import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import FocusPoolConfig
from .discovery import ResolvedSources

DUCKDB_FILE_NAME = "focus_pool_screening.duckdb"
CSV_FILE_NAME = "focus_pool_rows.csv"
LOG_FILE_NAME = "focus_pool__overview.log"
TABLE_NAME = "focus_pool_rows"


def build_run_id(prefix: str = "focus_pool") -> tuple[str, datetime]:
    created_at = datetime.now(tz=timezone.utc)
    run_id = f"{prefix}_{created_at.strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"
    return run_id, created_at


def _import_duckdb():
    import duckdb

    return duckdb


def build_row_schema(
    config: FocusPoolConfig,
    *,
    derived_keys: Sequence[str],
    include_conviction: bool,
    edge_enabled: bool,
) -> list[tuple[str, str]]:
    """Ordered (column, duckdb_type) schema for the focus_pool_rows table."""
    schema: list[tuple[str, str]] = [
        ("symbol", "VARCHAR"),
        ("company", "VARCHAR"),
        ("name", "VARCHAR"),
        ("exchange", "VARCHAR"),
        ("country", "VARCHAR"),
        ("sector", "VARCHAR"),
        ("industry", "VARCHAR"),
        ("market", "VARCHAR"),
        ("type", "VARCHAR"),
        ("close", "DOUBLE"),
        ("market_cap_basic", "DOUBLE"),
        ("earnings_release_date", "VARCHAR"),
        ("earnings_release_next_date", "VARCHAR"),
    ]
    field_keys: set[str] = set()
    for family in config.families:
        for spec in family.fields:
            schema.append((spec.key, "DOUBLE"))
            field_keys.add(spec.key)
    for key in derived_keys:
        if key not in field_keys:
            schema.append((key, "DOUBLE"))

    schema.extend(
        [
            ("pred_consensus_score", "DOUBLE"),
            ("pred_consensus_ras", "DOUBLE"),
            ("pred_consensus_direction", "VARCHAR"),
            ("pred_consensus_confidence", "DOUBLE"),
            ("pred_agreement_ratio", "DOUBLE"),
            ("pred_manager_action_signal", "VARCHAR"),
        ]
    )
    for profile in config.sources.move_prediction.profile_columns:
        safe_profile = profile.replace("-", "_").replace(".", "_")
        schema.append((f"pred_profile_{safe_profile}_score", "DOUBLE"))
        schema.append((f"pred_profile_{safe_profile}_ras", "DOUBLE"))
    if include_conviction:
        schema.extend(
            [
                ("pred_conviction_score", "DOUBLE"),
                ("pred_conviction_rank_overall", "DOUBLE"),
                ("pred_entry_readiness", "VARCHAR"),
                ("pred_size_tier", "VARCHAR"),
            ]
        )
    if edge_enabled:
        schema.extend(
            [
                ("edge_unified_score", "DOUBLE"),
                ("edge_unified_rank", "DOUBLE"),
                ("edge_consensus_score", "DOUBLE"),
                ("edge_best_outlook_name", "VARCHAR"),
                ("edge_best_outlook_score", "DOUBLE"),
                ("edge_forward_valuation_upside_pct", "DOUBLE"),
                ("edge_historical_validation_bucket", "VARCHAR"),
                ("edge_historical_validation_score", "DOUBLE"),
                ("edge_upside_prediction_score", "DOUBLE"),
                ("edge_tradeable_safety_blend_score", "DOUBLE"),
                ("edge_safety_companion_score", "DOUBLE"),
                ("edge_big_mover_score", "DOUBLE"),
                ("edge_confidence_score", "DOUBLE"),
            ]
        )

    for family in config.families:
        schema.append((f"{family.name}_score", "DOUBLE"))
        schema.append((f"{family.name}_coverage", "DOUBLE"))
    schema.extend(
        [
            ("prediction_component", "DOUBLE"),
            ("edge_component", "DOUBLE"),
            ("focus_score", "DOUBLE"),
            ("composite_coverage", "DOUBLE"),
            ("focus_rank", "BIGINT"),
            ("lanes", "VARCHAR"),
        ]
    )
    for lane in config.lanes:
        schema.append((f"lane_{lane.lane_id}", "BOOLEAN"))
        schema.append((f"lane_{lane.lane_id}_rank", "BIGINT"))
    return schema


def _to_storage(value: Any, duckdb_type: str) -> Any:
    if value is None:
        return None
    if duckdb_type == "BIGINT":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if duckdb_type == "BOOLEAN":
        return bool(value)
    if duckdb_type == "DOUBLE":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return str(value)


def _write_duckdb(
    database_path: Path,
    *,
    config: FocusPoolConfig,
    schema: Sequence[tuple[str, str]],
    rows: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, str],
    field_notes: Sequence[Mapping[str, Any]],
    lane_summary: Sequence[Mapping[str, Any]],
) -> None:
    duckdb = _import_duckdb()
    conn = duckdb.connect(str(database_path))
    try:
        columns_ddl = ", ".join(f'"{name}" {dtype}' for name, dtype in schema)
        conn.execute(f"CREATE OR REPLACE TABLE {TABLE_NAME} ({columns_ddl})")
        placeholders = ", ".join(["?"] * len(schema))
        insert_sql = f"INSERT INTO {TABLE_NAME} VALUES ({placeholders})"
        payload = [
            tuple(_to_storage(row.get(name), dtype) for name, dtype in schema)
            for row in rows
        ]
        if payload:
            conn.executemany(insert_sql, payload)

        conn.execute(
            "CREATE OR REPLACE TABLE run_manifest (key VARCHAR, value VARCHAR)"
        )
        conn.executemany(
            "INSERT INTO run_manifest VALUES (?, ?)",
            [(str(k), str(v)) for k, v in manifest.items()],
        )

        conn.execute(
            """
            CREATE OR REPLACE TABLE field_dictionary (
                key VARCHAR, family VARCHAR, column_name VARCHAR, derived VARCHAR,
                direction VARCHAR, weight DOUBLE, fill_rate DOUBLE,
                included BOOLEAN, note VARCHAR, source_hits_json VARCHAR
            )
            """
        )
        conn.executemany(
            "INSERT INTO field_dictionary VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    note["key"],
                    note["family"],
                    note.get("column"),
                    note.get("derived"),
                    note["direction"],
                    float(note["weight"]),
                    float(note["fill_rate"]),
                    bool(note["included"]),
                    note["note"],
                    json.dumps(note.get("source_hits") or {}, sort_keys=True),
                )
                for note in field_notes
            ],
        )

        conn.execute(
            """
            CREATE OR REPLACE TABLE lane_summary (
                lane_id VARCHAR, member_count BIGINT, avg_focus_score DOUBLE,
                avg_value_score DOUBLE, avg_fundamental_score DOUBLE,
                avg_technical_score DOUBLE, avg_momentum_score DOUBLE,
                top_symbol VARCHAR
            )
            """
        )
        conn.executemany(
            "INSERT INTO lane_summary VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item["lane_id"],
                    int(item["member_count"]),
                    item.get("avg_focus_score"),
                    item.get("avg_value_score"),
                    item.get("avg_fundamental_score"),
                    item.get("avg_technical_score"),
                    item.get("avg_momentum_score"),
                    item.get("top_symbol"),
                )
                for item in lane_summary
            ],
        )

        conn.execute(
            f"""
            CREATE OR REPLACE VIEW v_focus_top AS
            SELECT * FROM {TABLE_NAME}
            WHERE focus_score IS NOT NULL
            ORDER BY focus_rank
            """
        )
        for lane in config.lanes:
            if not lane.enabled:
                continue
            conn.execute(
                f"""
                CREATE OR REPLACE VIEW v_lane_{lane.lane_id} AS
                SELECT * FROM {TABLE_NAME}
                WHERE lane_{lane.lane_id}
                ORDER BY lane_{lane.lane_id}_rank
                """
            )
    finally:
        conn.close()


def _write_csv(
    csv_path: Path,
    *,
    schema: Sequence[tuple[str, str]],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    fieldnames = [name for name, _ in schema]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})


def _fmt(value: Any, *, decimals: int = 1, width: int = 0) -> str:
    if value is None:
        return " " * width if width else ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = f"{float(value):.{decimals}f}"
        except (TypeError, ValueError):
            text = str(value)
    if width:
        return text.rjust(width) if not isinstance(value, str) else text.ljust(width)
    return text


def _render_table(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[tuple[str, str, int, int]],
) -> list[str]:
    """columns: (header, row key, width, decimals)."""
    header_line = " ".join(header.rjust(width) for header, _, width, _ in columns)
    lines = [header_line, "-" * len(header_line)]
    for row in rows:
        parts = []
        for _, key, width, decimals in columns:
            value = row.get(key)
            if isinstance(value, str):
                parts.append(value[:width].ljust(width))
            else:
                parts.append(_fmt(value, decimals=decimals, width=width))
        lines.append(" ".join(parts))
    return lines


def _lane_summary_rows(
    config: FocusPoolConfig,
    lane_results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for lane in config.lanes:
        members = list(lane_results.get(lane.lane_id, {}).get("members") or [])
        if not lane.enabled:
            continue

        def _avg(key: str) -> float | None:
            values = [m.get(key) for m in members if m.get(key) is not None]
            if not values:
                return None
            return sum(float(v) for v in values) / len(values)

        family_avgs = {
            f"avg_{family.name}_score": _avg(f"{family.name}_score")
            for family in config.families
        }
        summary.append(
            {
                "lane_id": lane.lane_id,
                "member_count": len(members),
                "avg_focus_score": _avg("focus_score"),
                "avg_value_score": family_avgs.get("avg_value_score"),
                "avg_fundamental_score": family_avgs.get("avg_fundamental_score"),
                "avg_technical_score": family_avgs.get("avg_technical_score"),
                "avg_momentum_score": family_avgs.get("avg_momentum_score"),
                "top_symbol": members[0].get("symbol") if members else None,
            }
        )
    return summary


def _render_log(
    *,
    config: FocusPoolConfig,
    sources: ResolvedSources,
    run_id: str,
    created_at: datetime,
    config_hash: str,
    rows: Sequence[Mapping[str, Any]],
    field_notes: Sequence[Mapping[str, Any]],
    lane_results: Mapping[str, Mapping[str, Any]],
    lane_summary: Sequence[Mapping[str, Any]],
    notes: Sequence[str],
    top_n_override: int | None,
) -> str:
    family_names = [family.name for family in config.families]
    score_keys = [f"{name}_score" for name in family_names]

    table_columns: list[tuple[str, str, int, int]] = [
        ("rk", "focus_rank", 4, 0),
        ("symbol", "symbol", 14, 0),
        ("company", "company", 22, 0),
        ("close", "close", 9, 2),
        ("chg%", "change", 6, 1),
        ("p5d", "perf_5d", 6, 1),
        ("p1m", "perf_1m", 6, 1),
        ("p3m", "perf_3m", 6, 1),
    ]
    for name in family_names:
        table_columns.append((name[:5], f"{name}_score", 5, 0))
    table_columns.extend(
        [
            ("pred", "prediction_component", 5, 0),
            ("edge", "edge_component", 4, 0),
            ("focus", "focus_score", 5, 0),
            ("pe", "pe_ttm", 6, 1),
            ("ev/ebitda", "ev_ebitda", 6, 1),
            ("tgtUp%", "price_target_upside_pct", 6, 0),
            ("rsi", "rsi14", 5, 1),
            ("pred_dir", "pred_consensus_direction", 10, 0),
            ("edge_outlook", "edge_best_outlook_name", 14, 0),
        ]
    )

    lines: list[str] = []
    lines.append("=" * 100)
    lines.append("FOCUS POOL SCREENING — deterministic wide pool")
    lines.append(f"run_id:        {run_id}")
    lines.append(f"created_at_utc: {created_at.strftime('%Y-%m-%d %H:%M:%S')}Z")
    lines.append(f"config_id:     {config.config_id} (sha {config_hash})")
    lines.append(f"config_path:   {config.source_path.as_posix()}")
    if config.description:
        lines.append(f"description:   {config.description}")
    lines.append("")

    lines.append("SOURCES")
    lines.append("-" * 100)
    lines.append(
        f"  move_prediction db:  {(sources.prediction_db.as_posix() if sources.prediction_db else '-')}"
    )
    lines.append(f"    run_id:            {sources.prediction_run_id or '-'}")
    lines.append(f"    created_at_utc:    {sources.prediction_created_at_utc or '-'}")
    lines.append(
        f"  all_fields db:       {(sources.all_fields_db.as_posix() if sources.all_fields_db else '-')}"
    )
    lines.append(f"    run_id:            {sources.all_fields_run_id or '-'}")
    lines.append(f"    created_at_utc:    {sources.all_fields_created_at_utc or '-'}")
    lines.append(
        f"  edge parent run:     {(sources.edge_parent_dir.name if sources.edge_parent_dir else '-')}"
    )
    lines.append(
        f"  edge unified db:     {(sources.edge_unified_db.as_posix() if sources.edge_unified_db else '-')}"
    )
    lines.append("")

    lines.append("UNIVERSE & PIPELINE NOTES")
    lines.append("-" * 100)
    for note in notes:
        lines.append(f"  - {note}")
    lines.append(f"  - universe size (screened rows): {len(rows)}")
    scored_count = sum(1 for row in rows if row.get("focus_score") is not None)
    lines.append(f"  - rows with composite focus score: {scored_count}")
    lines.append("")

    lines.append("FAMILY WEIGHTS & FIELD DICTIONARY")
    lines.append("-" * 100)
    weight_parts = [f"{fam.name}={fam.weight:.2f}" for fam in config.families]
    weight_parts.append(f"prediction={config.overlays.prediction.weight:.2f}")
    weight_parts.append(f"edge={config.overlays.edge.weight:.2f}")
    lines.append("  configured weights: " + ", ".join(weight_parts))
    excluded = [n for n in field_notes if not n["included"]]
    lines.append(
        f"  fields included: {len(field_notes) - len(excluded)}/{len(field_notes)}"
    )
    for note in excluded:
        lines.append(
            f"  - EXCLUDED {note['key']} ({note['family']}): {note['note']}"
        )
    lines.append("")

    lines.append("LANE SUMMARY")
    lines.append("-" * 100)
    if lane_summary:
        summary_cols: list[tuple[str, str, int, int]] = [
            ("lane", "lane_id", 18, 0),
            ("members", "member_count", 7, 0),
            ("avg_focus", "avg_focus_score", 9, 1),
        ]
        for name in family_names:
            summary_cols.append((f"avg_{name[:5]}", f"avg_{name}_score", 9, 1))
        summary_cols.append(("top_symbol", "top_symbol", 14, 0))
        lines.extend(_render_table(lane_summary, summary_cols))
    else:
        lines.append("  (no lanes enabled)")
    lines.append("")

    top_focus_n = top_n_override or config.output.top_n_focus_section
    focus_rows = [row for row in rows if row.get("focus_score") is not None]
    focus_rows = sorted(focus_rows, key=lambda r: (r.get("focus_rank") or 10**9))
    lines.append(f"TOP FOCUS — top {top_focus_n} by focus_score")
    lines.append("-" * 100)
    lines.extend(_render_table(focus_rows[:top_focus_n], table_columns))
    lines.append("")

    for lane in config.lanes:
        if not lane.enabled:
            continue
        members = list(lane_results.get(lane.lane_id, {}).get("members") or [])
        top_n = top_n_override or lane.top_n
        lines.append(f"LANE: {lane.lane_id} — top {top_n} of {len(members)}")
        if lane.description:
            lines.append(f"  {lane.description}")
        lines.append("-" * 100)
        lane_columns = [("lrk", f"lane_{lane.lane_id}_rank", 4, 0)] + table_columns[1:]
        lines.extend(_render_table(members[:top_n], lane_columns))
        lines.append("")

    lines.append("=" * 100)
    lines.append(
        "DuckDB focus map: tables focus_pool_rows / run_manifest / field_dictionary / "
        "lane_summary; views v_focus_top and v_lane_<lane_id>."
    )
    return "\n".join(lines) + "\n"


def persist_outputs(
    *,
    config: FocusPoolConfig,
    sources: ResolvedSources,
    rows: Sequence[Mapping[str, Any]],
    field_notes: Sequence[Mapping[str, Any]],
    lane_results: Mapping[str, Mapping[str, Any]],
    notes: Sequence[str],
    config_hash: str,
    top_n_override: int | None,
    write_csv_override: bool | None,
) -> dict[str, Any]:
    from .engine import DERIVED_METRICS

    run_id, created_at = build_run_id()
    run_dir = Path(config.output.root_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    edge_enabled = sources.edge_unified_db is not None
    include_conviction = config.sources.move_prediction.include_conviction
    schema = build_row_schema(
        config,
        derived_keys=sorted(DERIVED_METRICS.keys()),
        include_conviction=include_conviction,
        edge_enabled=edge_enabled,
    )

    lane_summary = _lane_summary_rows(config, lane_results)

    manifest = {
        "run_id": run_id,
        "created_at_utc": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config_id": config.config_id,
        "config_sha256_12": config_hash,
        "config_path": config.source_path.as_posix(),
        "prediction_db": sources.prediction_db.as_posix() if sources.prediction_db else "",
        "prediction_run_id": sources.prediction_run_id or "",
        "prediction_created_at_utc": sources.prediction_created_at_utc or "",
        "all_fields_db": sources.all_fields_db.as_posix() if sources.all_fields_db else "",
        "all_fields_run_id": sources.all_fields_run_id or "",
        "all_fields_created_at_utc": sources.all_fields_created_at_utc or "",
        "edge_parent_dir": sources.edge_parent_dir.as_posix() if sources.edge_parent_dir else "",
        "edge_unified_db": sources.edge_unified_db.as_posix() if sources.edge_unified_db else "",
        "universe_size": str(len(rows)),
        "scored_rows": str(sum(1 for row in rows if row.get("focus_score") is not None)),
        "family_weights": json.dumps(
            {fam.name: fam.weight for fam in config.families}, sort_keys=True
        ),
        "overlay_weights": json.dumps(
            {
                "prediction": config.overlays.prediction.weight,
                "edge": config.overlays.edge.weight,
            },
            sort_keys=True,
        ),
    }

    duckdb_path = run_dir / DUCKDB_FILE_NAME
    _write_duckdb(
        duckdb_path,
        config=config,
        schema=schema,
        rows=rows,
        manifest=manifest,
        field_notes=field_notes,
        lane_summary=lane_summary,
    )

    write_csv = config.output.write_csv if write_csv_override is None else write_csv_override
    csv_path: Path | None = None
    if write_csv:
        csv_path = run_dir / CSV_FILE_NAME
        _write_csv(csv_path, schema=schema, rows=rows)

    log_text = _render_log(
        config=config,
        sources=sources,
        run_id=run_id,
        created_at=created_at,
        config_hash=config_hash,
        rows=rows,
        field_notes=field_notes,
        lane_results=lane_results,
        lane_summary=lane_summary,
        notes=notes,
        top_n_override=top_n_override,
    )
    log_path = run_dir / LOG_FILE_NAME
    log_path.write_text(log_text, encoding="utf-8")

    return {
        "run_id": run_id,
        "created_at_utc": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_dir": run_dir,
        "duckdb_path": duckdb_path,
        "csv_path": csv_path,
        "overview_log": log_path,
        "universe_size": len(rows),
        "scored_rows": manifest["scored_rows"],
        "lane_counts": {item["lane_id"]: item["member_count"] for item in lane_summary},
        "config_id": config.config_id,
        "config_hash": config_hash,
        "sources": sources,
    }
