"""Dated-source history: named-column fetch across DuckDB snapshots.

Oldest-to-latest field series without `SELECT *` or `logs/_tmp_*.py`.
No eligibility cutoffs. The caller names the table, columns, ids, and
optional run-id prefix.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from generic_utils.derive import pct_vs
from generic_utils.ranking import to_float
from generic_utils.scan_sources import rows_from_duckdb
from generic_utils.symbol_identity import bare_symbol, symbol_matches_request

_DAY_FOLDER = re.compile(r"^(\d{2})_(\d{2})_(\d{4})$")
_ISO_YEAR = re.compile(r"iso_year=(\d{4})$", re.I)
_ISO_WEEK = re.compile(r"week=(\d{1,2})$", re.I)


def qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def parse_path_date(path: str | Path) -> date | None:
    """Best-effort as-of date from a DuckDB path (DD_MM_YYYY folder or ISO week)."""
    p = Path(path)
    for part in (p.parent.name, p.stem, p.parent.parent.name):
        match = _DAY_FOLDER.match(part)
        if match:
            day, month, year = match.groups()
            try:
                return date(int(year), int(month), int(day))
            except ValueError:
                continue
    iso_year = None
    iso_week = None
    for part in p.parts:
        y = _ISO_YEAR.match(part)
        w = _ISO_WEEK.match(part)
        if y:
            iso_year = int(y.group(1))
        if w:
            iso_week = int(w.group(1))
    if iso_year is not None and iso_week is not None:
        return date.fromisocalendar(iso_year, iso_week, 1)
    return None


def list_dated_files(
    root: str | Path,
    glob_pattern: str,
    *,
    newest: int | None = None,
) -> list[dict[str, Any]]:
    """Glob files under `root`, oldest-first. `newest` keeps the last N after sort."""
    base = Path(root)
    if not base.exists():
        return []
    found: list[dict[str, Any]] = []
    iterator = (
        base.glob(glob_pattern)
        if any(sep in glob_pattern for sep in ("/", "\\", "**"))
        else base.rglob(glob_pattern)
    )
    for path in iterator:
        if not path.is_file():
            continue
        as_of = parse_path_date(path)
        mtime = path.stat().st_mtime
        found.append(
            {
                "path": path,
                "as_of": as_of.isoformat() if as_of else None,
                "as_of_date": as_of,
                "mtime": mtime,
                "label": path.parent.name,
            }
        )
    found.sort(
        key=lambda row: (
            row["as_of_date"] or date.min,
            row["mtime"],
            str(row["path"]),
        )
    )
    if newest is not None:
        keep = max(0, int(newest))
        found = found[-keep:] if keep else []
    return found


def table_columns(database_path: str | Path, table: str) -> set[str]:
    rows = rows_from_duckdb(database_path, f"PRAGMA table_info({qident(table)})")
    return {str(r.get("name")) for r in rows if r.get("name")}


def list_run_ids(
    database_path: str | Path,
    *,
    run_prefix: str | None = None,
    all_runs: bool = False,
) -> list[dict[str, Any]]:
    """Run ids from `run_metadata`, oldest-first.

    `all_runs=False` returns only the newest matching row (still as a 1-item
    list). `run_prefix` keeps ids that start with that string.
    """
    rows = rows_from_duckdb(
        database_path,
        """
        SELECT run_id, CAST(created_at_utc AS VARCHAR) AS created_at_utc
        FROM run_metadata
        ORDER BY created_at_utc ASC NULLS LAST, run_id ASC
        """,
    )
    kept: list[dict[str, Any]] = []
    prefix = str(run_prefix or "")
    for row in rows:
        run_id = str(row.get("run_id") or "")
        if not run_id:
            continue
        if prefix and not run_id.startswith(prefix):
            continue
        kept.append(
            {
                "run_id": run_id,
                "created_at_utc": row.get("created_at_utc"),
            }
        )
    if not kept:
        return []
    if all_runs:
        return kept
    return kept[-1:]


def expand_ids(ids: Iterable[str], *, also_bare: bool = False) -> list[str]:
    """Deduped, first-seen order. Prefixed ids stay prefixed.

    `also_bare=True` also keeps the last `:` segment (NASDAQ:MU -> MU). Off by
    default so a prefixed Book id does not pull every other listing of MU.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in ids:
        text = str(raw or "").strip()
        if not text:
            continue
        pieces = [text]
        if also_bare:
            pieces.append(text.split(":")[-1])
        for piece in pieces:
            if piece not in seen:
                seen.add(piece)
                out.append(piece)
    return out


def bare_id(value: Any) -> str:
    return bare_symbol(value)


def match_wanted_id(value: Any, wanted: Sequence[str]) -> str | None:
    """Map a stored id onto the caller's id.

    Exact match wins. A bare stored ticker (older week files use ``MU``)
    maps to the single prefixed caller id that shares that ticker
    (``NASDAQ:MU``). A stored prefixed id never maps to a different prefixed
    caller id merely because its bare ticker matches (``ASX:MMS`` must not
    satisfy a request for ``NYSE:MMS``). Bare caller ids may still match
    prefixed stored ids because the caller explicitly omitted the exchange.
    """
    text = str(value or "").strip()
    if not text:
        return None
    wanted_list = [str(w).strip() for w in wanted if str(w).strip()]
    if not wanted_list:
        return text
    if text in wanted_list:
        return text
    hits = [w for w in wanted_list if symbol_matches_request(text, w)]
    if len(hits) == 1:
        return hits[0]
    return None


def row_id_matches(row: Mapping[str, Any], wanted: Sequence[str], id_fields: Sequence[str]) -> bool:
    wanted_list = [str(x) for x in wanted if x]
    if not wanted_list:
        return True
    for field in id_fields:
        if match_wanted_id(row.get(field), wanted_list):
            return True
    return False


def build_named_sql(
    *,
    table: str,
    columns: Sequence[str],
    available: Sequence[str],
    id_field: str | None = None,
    n_ids: int = 0,
    filter_field: str | None = None,
    n_filter: int = 0,
    equals: Mapping[str, Any] | None = None,
    has_run_id: bool = True,
) -> tuple[str, list[str]]:
    """Build a parameterized SELECT of named columns. Never SELECT *."""
    avail = set(available)
    selected: list[str] = []
    missing: list[str] = []
    for col in columns:
        if col in avail:
            selected.append(col)
        else:
            missing.append(col)
    if not selected:
        raise ValueError(
            f"None of the requested columns exist on {table}. "
            f"missing={missing}"
        )
    select_sql = ", ".join(qident(c) for c in selected)
    clauses: list[str] = []
    if has_run_id and "run_id" in avail:
        clauses.append("run_id = ?")
    if id_field and n_ids:
        if id_field not in avail:
            raise ValueError(f"id_field {id_field!r} is not on {table}")
        placeholders = ",".join(["?"] * n_ids)
        id_sql = f"{qident(id_field)} IN ({placeholders})"
        bare = f"list_extract(string_split(CAST({qident(id_field)} AS VARCHAR), ':'), -1) IN ({placeholders})"
        clauses.append(f"({id_sql} OR {bare})")
    if filter_field and n_filter:
        if filter_field not in avail:
            raise ValueError(f"filter_field {filter_field!r} is not on {table}")
        placeholders = ",".join(["?"] * n_filter)
        clauses.append(f"{qident(filter_field)} IN ({placeholders})")
    for field in (equals or {}):
        if field not in avail:
            raise ValueError(f"equals field {field!r} is not on {table}")
        clauses.append(f"{qident(field)} = ?")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT {select_sql} FROM {qident(table)} {where}".strip()
    return sql, missing


def fetch_named(
    database_path: str | Path,
    *,
    table: str,
    columns: Sequence[str],
    run_id: str | None = None,
    id_field: str | None = None,
    ids: Sequence[str] | None = None,
    filter_field: str | None = None,
    filter_values: Sequence[str] | None = None,
    equals: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch named columns for optional ids / filter values / equality clauses."""
    available = sorted(table_columns(database_path, table))
    wanted_ids = expand_ids(ids or [])
    filters = [str(v) for v in (filter_values or []) if str(v)]
    eq = dict(equals or {})
    has_run_id = run_id is not None and "run_id" in set(available)
    sql, missing = build_named_sql(
        table=table,
        columns=columns,
        available=available,
        id_field=id_field if wanted_ids else None,
        n_ids=len(wanted_ids),
        filter_field=filter_field if filters else None,
        n_filter=len(filters),
        equals=eq,
        has_run_id=has_run_id,
    )
    params: list[Any] = []
    if has_run_id:
        params.append(run_id)
    if wanted_ids and id_field:
        params.extend(wanted_ids)
        params.extend(bare_id(item) for item in wanted_ids)
    if filters and filter_field:
        params.extend(filters)
    params.extend(eq.values())
    rows = rows_from_duckdb(database_path, sql, params)
    if wanted_ids:
        id_fields = [id_field] if id_field else []
        for extra in ("symbol", "ticker"):
            if extra not in id_fields and extra in available:
                id_fields.append(extra)
        rows = [r for r in rows if row_id_matches(r, wanted_ids, id_fields)]
    return {
        "spec": {
            "db": str(database_path),
            "table": table,
            "columns": list(columns),
            "missing_columns": missing,
            "run_id": run_id,
            "id_field": id_field,
            "n_ids": len(wanted_ids),
            "filter_field": filter_field,
            "n_filter": len(filters),
            "equals": eq,
            "n": len(rows),
        },
        "rows": rows,
    }


def select_named_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    columns: Sequence[str],
    id_field: str | None = None,
    ids: Sequence[str] | None = None,
    source: str = "",
) -> dict[str, Any]:
    """`fetch_named` for rows already in memory (a CSV). Same id matching, same payload shape.

    Ids come back in the caller's order; ids with no row are listed in `missing_ids`.
    """
    available = set(rows[0]) if rows else set()
    wanted_ids = expand_ids(ids or [])
    id_fields = [f for f in [id_field, "symbol", "ticker"] if f and f in available]
    picked: list[dict[str, Any]] = []
    found: set[str] = set()
    for wanted in wanted_ids or [None]:
        for row in rows:
            if wanted is None or row_id_matches(row, [wanted], id_fields):
                picked.append({c: row.get(c) for c in columns if c in available})
                if wanted is not None:
                    found.add(wanted)
                    break
    return {
        "spec": {
            "csv": source,
            "columns": list(columns),
            "missing_columns": [c for c in columns if c not in available],
            "id_field": id_field,
            "n_ids": len(wanted_ids),
            "missing_ids": [w for w in wanted_ids if w not in found],
            "n": len(picked),
        },
        "rows": picked,
    }


def alias_fields(
    rows: Sequence[Mapping[str, Any]],
    mapping: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Rename keys. Unmapped keys are kept. Does not drop rows."""
    if not mapping:
        return [dict(r) for r in rows]
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        for src, dest in mapping.items():
            if src in rec:
                rec[dest] = rec.pop(src)
        out.append(rec)
    return out


def field_history(
    sources: Sequence[Mapping[str, Any]],
    *,
    table: str,
    columns: Sequence[str],
    id_field: str | None = None,
    ids: Sequence[str] | None = None,
    filter_field: str | None = None,
    filter_values: Sequence[str] | None = None,
    equals: Mapping[str, Any] | None = None,
    run_prefix: str | None = None,
    all_runs: bool = False,
    recipe: str | None = None,
    recipe_params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Walk dated DuckDB files oldest-to-latest and concatenate named rows.

    Each output row is tagged with `as_of`, `source`, and `run_id`.
    Pass `recipe` to run a named SQL recipe per run instead of `fetch_named`.
    """
    trail: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for source in sources:
        path = Path(source["path"])
        as_of = source.get("as_of")
        runs = list_run_ids(path, run_prefix=run_prefix, all_runs=all_runs)
        if not runs:
            snapshots.append(
                {
                    "path": path.as_posix(),
                    "as_of": as_of,
                    "run_id": None,
                    "n": 0,
                    "note": "no run_metadata match",
                }
            )
            continue
        for run in runs:
            run_id = str(run["run_id"])
            created = run.get("created_at_utc")
            stamp = str(created)[:10] if created else as_of
            if recipe:
                from generic_utils.recipes import render_recipe

                values = dict(recipe_params or {})
                values.setdefault("run_id", run_id)
                sql, params = render_recipe(recipe, **values)
                rows = rows_from_duckdb(path, sql, params)
                spec = {
                    "recipe": recipe,
                    "run_id": run_id,
                    "n": len(rows),
                    "missing_columns": [],
                }
                wanted = expand_ids(ids or [])
                if wanted:
                    id_key = id_field or "symbol"
                    kept: list[dict[str, Any]] = []
                    for raw_row in rows:
                        canon = match_wanted_id(raw_row.get(id_key) or raw_row.get("symbol"), wanted)
                        if not canon:
                            continue
                        rec = dict(raw_row)
                        rec[id_key] = canon
                        if "symbol" in rec:
                            rec["symbol"] = canon
                        kept.append(rec)
                    rows = kept
                    spec["n"] = len(rows)
            else:
                fetched = fetch_named(
                    path,
                    table=table,
                    columns=columns,
                    run_id=run_id,
                    id_field=id_field,
                    ids=ids,
                    filter_field=filter_field,
                    filter_values=filter_values,
                    equals=equals,
                )
                rows = fetched["rows"]
                spec = fetched["spec"]
            for row in rows:
                rec = dict(row)
                rec.setdefault("as_of", stamp)
                rec.setdefault("source", path.as_posix())
                rec.setdefault("run_id", run_id)
                trail.append(rec)
            snapshots.append(
                {
                    "path": path.as_posix(),
                    "as_of": stamp,
                    "run_id": run_id,
                    "n": spec.get("n"),
                    "missing_columns": spec.get("missing_columns") or [],
                }
            )
    return {
        "spec": {
            "table": table,
            "columns": list(columns),
            "id_field": id_field,
            "n_ids": len(expand_ids(ids or [])),
            "filter_field": filter_field,
            "n_sources": len(sources),
            "n_snapshots": len(snapshots),
            "n": len(trail),
            "run_prefix": run_prefix,
            "all_runs": all_runs,
            "recipe": recipe,
        },
        "snapshots": snapshots,
        "rows": trail,
    }


def group_history(
    rows: Sequence[Mapping[str, Any]],
    *,
    as_of_field: str = "as_of",
    group_field: str,
    metrics: Sequence[str],
    min_n: int = 1,
    pct_positive_field: str | None = None,
) -> list[dict[str, Any]]:
    """`group_stats` per as-of stamp, oldest-first. One row per (as_of, group)."""
    from generic_utils.aggregations import group_stats

    by_stamp: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for row in rows:
        stamp = str(row.get(as_of_field) or "")
        if stamp not in by_stamp:
            order.append(stamp)
        by_stamp[stamp].append(row)
    out: list[dict[str, Any]] = []
    for stamp in order:
        grouped = group_stats(
            by_stamp[stamp],
            group_field=group_field,
            metrics=metrics,
            min_n=min_n,
            pct_positive_field=pct_positive_field,
        )
        for rec in grouped:
            item = dict(rec)
            item[as_of_field] = stamp
            out.append(item)
    return out


def _compute_field_step_stats(
    points: Sequence[Mapping[str, Any]],
    field: str,
) -> dict[str, Any]:
    """Day-to-day (step-to-step) movement stats for one field on an ordered series."""
    values = [
        to_float(point.get(field))
        for point in points
        if to_float(point.get(field)) is not None
    ]
    step_pcts: list[float] = []
    for idx in range(1, len(values)):
        prev_v = values[idx - 1]
        curr_v = values[idx]
        if prev_v is None or curr_v is None or prev_v <= 0:
            continue
        step_pcts.append((curr_v / prev_v - 1.0) * 100.0)

    step_n = len(step_pcts)
    if step_n == 0:
        step_mean = None
        step_abs_mean = None
        step_stdev = None
        step_max_up = None
        step_max_down = None
        step_up_rate = None
        streak_up = 0
        streak_down = 0
    else:
        step_mean = round(sum(step_pcts) / step_n, 3)
        step_abs_mean = round(sum(abs(x) for x in step_pcts) / step_n, 3)
        if step_n >= 2:
            mean_raw = sum(step_pcts) / step_n
            var = sum((x - mean_raw) ** 2 for x in step_pcts) / (step_n - 1)
            step_stdev = round(var ** 0.5, 3)
        else:
            step_stdev = 0.0
        step_max_up = round(max(step_pcts), 2)
        step_max_down = round(min(step_pcts), 2)
        step_up_rate = round(sum(1 for x in step_pcts if x > 0) / step_n, 3)
        streak_up = 0
        for x in reversed(step_pcts):
            if x > 0:
                streak_up += 1
            else:
                break
        streak_down = 0
        for x in reversed(step_pcts):
            if x < 0:
                streak_down += 1
            else:
                break

    # Peak-to-trough drawdown, giveback from max, rebound from min, seat in [min, max]
    positive_vals = [v for v in values if v is not None and v > 0]
    if not positive_vals:
        max_dd = None
        giveback = None
        rebound = None
        seat = None
    else:
        running_peak = positive_vals[0]
        worst_dd = 0.0
        for v in positive_vals:
            if v > running_peak:
                running_peak = v
            dd = (v / running_peak - 1.0) * 100.0
            if dd < worst_dd:
                worst_dd = dd
        max_dd = round(worst_dd, 2)
        lo_val = min(positive_vals)
        hi_val = max(positive_vals)
        last_val = positive_vals[-1]
        giveback = round((last_val / hi_val - 1.0) * 100.0, 2) if hi_val > 0 else None
        rebound = round((last_val / lo_val - 1.0) * 100.0, 2) if lo_val > 0 else None
        seat = (
            round((last_val - lo_val) / (hi_val - lo_val) * 100.0, 1)
            if hi_val > lo_val
            else None
        )

    return {
        f"{field}_step_n": step_n,
        f"{field}_step_mean_pct": step_mean,
        f"{field}_step_abs_mean_pct": step_abs_mean,
        f"{field}_step_stdev_pct": step_stdev,
        f"{field}_step_max_up_pct": step_max_up,
        f"{field}_step_max_down_pct": step_max_down,
        f"{field}_step_up_rate": step_up_rate,
        f"{field}_streak_up": streak_up,
        f"{field}_streak_down": streak_down,
        f"{field}_max_drawdown_pct": max_dd,
        f"{field}_giveback_from_max_pct": giveback,
        f"{field}_rebound_from_min_pct": rebound,
        f"{field}_seat_pct": seat,
    }


def series_step_stats(
    rows: Sequence[Mapping[str, Any]],
    *,
    id_field: str = "symbol",
    fields: Sequence[str] = ("close",),
    as_of_field: str = "as_of",
    normalize: bool = False,
) -> list[dict[str, Any]]:
    """Span plus day-to-day (step-to-step) movement statistics per id.

    Rows must already be ordered oldest-to-latest. Zero eligibility cutoffs.
    """
    return series_span(
        rows,
        id_field=id_field,
        fields=fields,
        as_of_field=as_of_field,
        normalize=normalize,
        include_steps=True,
    )


def series_span(
    rows: Sequence[Mapping[str, Any]],
    *,
    id_field: str,
    fields: Sequence[str],
    as_of_field: str = "as_of",
    normalize: bool = False,
    include_steps: bool = False,
) -> list[dict[str, Any]]:
    """First, last, low, and high per id (rows must already be oldest-to-latest).

    ``field_min`` / ``field_max`` are the lowest and highest prints in the
    window, with ``field_min_as_of`` and ``field_max_as_of``. First and last
    stay the window ends. A tie keeps the earlier print.

    `normalize=True` groups NASDAQ:MU with MU. Leave it off when dual listings
    exist (TSXV:MRVL vs NASDAQ:MRVL).
    `include_steps=True` also attaches day-to-day step stats (`{field}_step_*`,
    `{field}_max_drawdown_pct`, `{field}_giveback_from_max_pct`, `{field}_seat_pct`).
    """
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for row in rows:
        raw = str(row.get(id_field) or "").strip()
        if not raw:
            continue
        key = normalize_id(raw) if normalize else raw
        if key not in grouped:
            order.append(key)
        grouped[key].append(row)
    out: list[dict[str, Any]] = []
    for key in order:
        points = grouped[key]
        first = points[0]
        last = points[-1]
        rec: dict[str, Any] = {
            id_field: key,
            "n": len(points),
            "first_as_of": first.get(as_of_field),
            "last_as_of": last.get(as_of_field),
        }
        for field in fields:
            a = to_float(first.get(field))
            b = to_float(last.get(field))
            rec[f"{field}_first"] = a
            rec[f"{field}_last"] = b
            rec[f"{field}_chg"] = None if a is None or b is None else round(b - a, 4)
            rec[f"{field}_pct"] = (
                None if a is None or b is None else round(pct_vs(b, a) or 0.0, 2)
            )
            observed = [
                (to_float(point.get(field)), point.get(as_of_field))
                for point in points
                if to_float(point.get(field)) is not None
            ]
            if not observed:
                rec[f"{field}_min"] = None
                rec[f"{field}_min_as_of"] = None
                rec[f"{field}_max"] = None
                rec[f"{field}_max_as_of"] = None
            else:
                lo = min(observed, key=lambda item: (item[0], str(item[1] or "")))
                hi = min(observed, key=lambda item: (-item[0], str(item[1] or "")))
                rec[f"{field}_min"] = lo[0]
                rec[f"{field}_min_as_of"] = lo[1]
                rec[f"{field}_max"] = hi[0]
                rec[f"{field}_max_as_of"] = hi[1]
            if include_steps:
                rec.update(_compute_field_step_stats(points, field))
        out.append(rec)
    return out


def parse_equals(text: str | None) -> dict[str, str]:
    """`field=value,field2=value2` into a dict. Empty -> {}."""
    if not text:
        return {}
    out: dict[str, str] = {}
    for part in text.split(","):
        piece = part.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise ValueError(f"equals clause needs field=value, got {piece!r}")
        field, value = piece.split("=", 1)
        out[field.strip()] = value.strip()
    return out


def parse_rename(text: str | None) -> dict[str, str]:
    return parse_equals(text)


def parse_csv_list(text: str | None) -> list[str]:
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def normalize_id(value: Any) -> str:
    text = str(value or "").strip()
    return text.split(":")[-1] if text else ""


def join_rows(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
    *,
    left_on: str,
    right_on: str,
    how: str = "left",
    prefix: str = "",
) -> list[dict[str, Any]]:
    """Left/inner join on normalized ids (exchange prefix stripped).

    Right-hand keys are prefixed with `prefix` when set (e.g. `tape_`).
    Does not drop unprefixed left keys. `how` is `left` or `inner`.
    """
    if how not in {"left", "inner"}:
        raise ValueError("how must be 'left' or 'inner'")
    index_full: dict[str, Mapping[str, Any]] = {}
    index_ticker: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in right:
        full = str(row.get(right_on) or "").strip()
        if full:
            index_full[full] = row
            index_ticker[normalize_id(full)].append(row)
    out: list[dict[str, Any]] = []
    for row in left:
        rec = dict(row)
        full = str(row.get(left_on) or "").strip()
        alt = str(row.get("symbol") or "").strip()
        match = index_full.get(full) or index_full.get(alt)
        if match is None:
            ticker = normalize_id(full) or normalize_id(alt)
            cands = index_ticker.get(ticker) or []
            requested = full or alt
            compatible = [
                candidate
                for candidate in cands
                if symbol_matches_request(candidate.get(right_on), requested)
            ]
            if len(compatible) == 1:
                match = compatible[0]
        if match is None:
            if how == "inner":
                continue
            out.append(rec)
            continue
        for field, value in match.items():
            dest = f"{prefix}{field}" if prefix else field
            if dest not in rec or rec.get(dest) in (None, ""):
                rec[dest] = value
            elif prefix:
                rec[dest] = value
        rec["_joined"] = True
        out.append(rec)
    return out
