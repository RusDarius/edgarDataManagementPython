"""Intraday drift between two runs of the all-fields scan (early session vs later session).

One row per symbol present in both runs: close and day change at each run, the drift
between them, relative volume at each run, and whether the later run extends, fades or
reverses the early move. ``drift_by_group`` rolls the rows up per industry (median drift,
share drifting up, count) so a theme's session path (the "AI trade" opening strong and
fading, or building) is one table.

No ranking and no cutoffs: the universe filter (venue, market-cap floor) is the caller's
WHERE clause. ``session_path`` is a label from the signs of the two moves, not a score.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float
from generic_utils.run_export import write_run_export
from generic_utils.series import qident

_f = to_float

TEXT_COLUMNS = ("symbol", "description", "industry", "sector")
NUMERIC_COLUMNS = ("close", "change", "relative_volume_10d_calc", "market_cap_basic", "volume")


def list_runs(database: str | Path) -> list[dict[str, Any]]:
    """Runs in one day file, oldest-first."""
    import duckdb

    con = duckdb.connect(str(database), read_only=True)
    try:
        rows = con.execute("SELECT run_id, CAST(created_at_utc AS VARCHAR) FROM run_metadata ORDER BY created_at_utc").fetchall()
    finally:
        con.close()
    return [{"run_id": r[0], "created_at_utc": r[1]} for r in rows]


def _load_run(con: Any, run_id: str, where_sql: str, params: Sequence[Any]) -> dict[str, dict[str, Any]]:
    cols = [f"{qident(c)}" for c in TEXT_COLUMNS] + [f"TRY_CAST({qident(c)} AS DOUBLE) AS {qident(c)}" for c in NUMERIC_COLUMNS]
    sql = f"SELECT * FROM (SELECT {', '.join(cols)} FROM all_fields_rows WHERE run_id = ?) AS t"
    bind: list[Any] = [run_id]
    if where_sql:
        sql += f" WHERE {where_sql}"
        bind += list(params)
    cur = con.execute(sql, bind)
    names = [d[0] for d in cur.description]
    return {str(row[0]): dict(zip(names, row)) for row in cur.fetchall()}


def session_path(early: Any, drift: Any, *, flat_pct: float = 0.25) -> str:
    """EXTENDING / FADING / REVERSING_UP / REVERSING_DOWN / FLAT from the early day change and the later drift."""
    e, d = _f(early), _f(drift)
    if e is None or d is None:
        return ""
    if abs(d) < flat_pct:
        return "FLAT"
    if e >= 0:
        if d > 0:
            return "EXTENDING"
        return "FADING" if e + d > 0 else "REVERSING_DOWN"
    if d < 0:
        return "EXTENDING"
    return "FADING" if e + d < 0 else "REVERSING_UP"


def run_drift(
    database: str | Path,
    *,
    run_a: str,
    run_b: str,
    where_sql: str = "",
    params: Sequence[Any] = (),
    flat_pct: float = 0.25,
) -> list[dict[str, Any]]:
    """Join two runs of one day file by symbol."""
    import duckdb

    con = duckdb.connect(str(database), read_only=True)
    try:
        a = _load_run(con, run_a, where_sql, params)
        b = _load_run(con, run_b, where_sql, params)
    finally:
        con.close()
    out: list[dict[str, Any]] = []
    for symbol, early in a.items():
        late = b.get(symbol)
        if not late:
            continue
        ca, cb = _f(early.get("close")), _f(late.get("close"))
        drift = (cb / ca - 1.0) * 100.0 if ca and cb and ca > 0 else None
        rec = {
            "symbol": symbol,
            "company": late.get("description") or "",
            "ind": late.get("industry") or "",
            "sector": late.get("sector") or "",
            "mcap": _f(late.get("market_cap_basic")),
            "close_a": ca,
            "close_b": cb,
            "change_a": round(_f(early.get("change")), 2) if _f(early.get("change")) is not None else None,
            "change_b": round(_f(late.get("change")), 2) if _f(late.get("change")) is not None else None,
            "drift_pct": round(drift, 2) if drift is not None else None,
            "relvol_a": round(_f(early.get("relative_volume_10d_calc")), 2) if _f(early.get("relative_volume_10d_calc")) is not None else None,
            "relvol_b": round(_f(late.get("relative_volume_10d_calc")), 2) if _f(late.get("relative_volume_10d_calc")) is not None else None,
        }
        rec["session_path"] = session_path(rec["change_a"], drift, flat_pct=flat_pct)
        out.append(rec)
    return out


def drift_by_group(rows: Sequence[Mapping[str, Any]], *, group_field: str = "ind", min_n: int = 1) -> list[dict[str, Any]]:
    """Per group: n, median early change, median drift, share drifting up, median later change."""
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(row.get(group_field) or "")
        if key:
            groups[key].append(row)
    out: list[dict[str, Any]] = []
    for key, members in groups.items():
        drifts = [_f(m.get("drift_pct")) for m in members if _f(m.get("drift_pct")) is not None]
        if len(drifts) < min_n:
            continue
        early = [_f(m.get("change_a")) for m in members if _f(m.get("change_a")) is not None]
        late = [_f(m.get("change_b")) for m in members if _f(m.get("change_b")) is not None]
        out.append({
            group_field: key,
            "n": len(drifts),
            "change_a_med": round(median(early), 2) if early else None,
            "drift_med": round(median(drifts), 2),
            "drift_up_pct": round(100.0 * sum(1 for d in drifts if d > 0) / len(drifts), 1),
            "change_b_med": round(median(late), 2) if late else None,
        })
    return sorted(out, key=lambda r: r["drift_med"], reverse=True)


def write_intraday(rows: Sequence[Mapping[str, Any]], groups: Sequence[Mapping[str, Any]], out_dir: str | Path,
                   *, sources: Mapping[str, Any]) -> dict[str, Any]:
    return write_run_export(
        out_dir,
        tool="intraday",
        tables={"drift": list(rows), "drift_by_ind": list(groups)},
        sources=dict(sources),
        schema_version="intraday_v1",
        notes=[
            "change_a / change_b: the scan's day change at the early / later run. drift_pct: close_b vs close_a, %.",
            "session_path: EXTENDING (later run adds to the early move), FADING (gives some back, same sign), REVERSING_UP / REVERSING_DOWN (the day flipped sign), FLAT (|drift| under flat_pct).",
            "drift_by_ind: per industry n, median early change, median drift, share drifting up, median later change.",
        ],
        duckdb_name="intraday.duckdb",
    )
