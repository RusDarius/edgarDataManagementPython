"""Write a run folder the way edge research does: DuckDB tables + overview.log.

No eligibility cutoffs. Callers name tables and source ids.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.scan_sources import rows_from_csv, rows_to_csv

_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_ident(name: str) -> str:
    if not _TABLE_NAME.match(name):
        raise ValueError(f"Invalid table name {name!r}")
    return name


def _quote_sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def git_stamp(cwd: Path | None = None) -> dict[str, Any]:
    """Best-effort git identity. Missing git is not a failure."""
    import subprocess

    root = Path(cwd) if cwd else Path(__file__).resolve().parents[2]
    out: dict[str, Any] = {"git_commit": None, "git_branch": None, "git_dirty": None}
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        out["git_commit"] = commit or None
        out["git_branch"] = branch or None
        out["git_dirty"] = bool(dirty)
    except (OSError, subprocess.CalledProcessError):
        pass
    return out


def write_overview_log(
    path: str | Path,
    *,
    tool: str,
    created_at: str | None = None,
    schema_version: str | None = None,
    sources: Mapping[str, Any] | None = None,
    tables: Mapping[str, int] | None = None,
    notes: Sequence[str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Path:
    """Short run header: version, timestamp, source ids, table counts."""
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = git_stamp()
    created = created_at or datetime.now(tz=timezone.utc).isoformat()
    lines = [
        f"tool={tool}",
        f"created_at_utc={created}",
        f"schema_version={schema_version or ''}",
        f"git_commit={stamp.get('git_commit') or ''}",
        f"git_branch={stamp.get('git_branch') or ''}",
        f"git_dirty={stamp.get('git_dirty')}",
    ]
    for key, value in (sources or {}).items():
        if value is None or value == "":
            continue
        lines.append(f"source.{key}={value}")
    for name, count in (tables or {}).items():
        lines.append(f"table.{name}={count}")
    for key, value in (extra or {}).items():
        lines.append(f"{key}={value}")
    for note in notes or ():
        lines.append(f"note={note}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


def rows_to_duckdb(
    path: str | Path,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Path:
    """Replace `path` with a DuckDB file, one table per mapping key.

    Same pattern as edge_research_tools (CSV round-trip -> CREATE TABLE AS
    read_csv). Empty tables get a placeholder VARCHAR column.
    """
    import duckdb

    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = duckdb.connect(db_path.as_posix())
    try:
        for name, rows in tables.items():
            ident = _quote_ident(name)
            materialised = [dict(row) for row in rows]
            if not materialised:
                conn.execute(f"CREATE TABLE {ident} (placeholder VARCHAR)")
                continue
            csv_path = db_path.with_name(f".{name}.csv")
            rows_to_csv(csv_path, materialised)
            conn.execute(
                f"CREATE TABLE {ident} AS SELECT * FROM read_csv("
                f"{_quote_sql_literal(csv_path.as_posix())}, "
                f"header=true, auto_detect=true)"
            )
            csv_path.unlink(missing_ok=True)
    finally:
        conn.close()
    return db_path


def write_run_export(
    out_dir: str | Path,
    *,
    tool: str,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    sources: Mapping[str, Any] | None = None,
    schema_version: str | None = None,
    notes: Sequence[str] | None = None,
    extra: Mapping[str, Any] | None = None,
    duckdb_name: str = "scan.duckdb",
    log_name: str = "overview.log",
    write_csv: bool = True,
) -> dict[str, Any]:
    """Write CSV copies (optional), DuckDB, and overview.log into `out_dir`."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    counts = {name: len(rows) for name, rows in tables.items()}
    csv_paths: dict[str, str] = {}
    if write_csv:
        for name, rows in tables.items():
            csv_paths[name] = rows_to_csv(
                directory / f"{name}.csv", [dict(r) for r in rows]
            ).as_posix()
    db_path = rows_to_duckdb(directory / duckdb_name, tables)
    created = datetime.now(tz=timezone.utc).isoformat()
    log_path = write_overview_log(
        directory / log_name,
        tool=tool,
        created_at=created,
        schema_version=schema_version,
        sources=sources,
        tables=counts,
        notes=notes,
        extra=extra,
    )
    return {
        "out_dir": directory.as_posix(),
        "duckdb": db_path.as_posix(),
        "overview_log": log_path.as_posix(),
        "csv": csv_paths,
        "tables": counts,
        "created_at_utc": created,
    }


def load_json_recipe(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Recipe {path} must be a JSON object")
    return payload


def parse_table_specs(items: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """Parse `name=csv_path` specs into table mapping."""
    tables: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Expected name=path, got {item!r}")
        name, csv_path = item.split("=", 1)
        tables[name.strip()] = rows_from_csv(csv_path.strip())
    return tables
