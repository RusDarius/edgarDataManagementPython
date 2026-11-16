"""Opportunity board: one row per name with every suite's read beside it, for grids and screens.

The base is the catalyst outlook's ``names`` (every US $2B+ name, the Book and named ids: tape, set-up, entry and bounce
bands, catalysts, value, progression, model). The recipe's ``joins`` (the same specs as earnings_week / deterioration:
``{name, duckdb|csv, table, id_field, fields{src: dest}, where}``) add peer value, deterioration letters and cells,
the beat bar of this week's and next week's reporters, forward value, pattern letters and store beta / volatility.

Derived columns are positions only: where the close sits against the bounce band (``band_state``), the distance to
the band top and to the kill, the beat-bar cell split into its tercile per axis. No ranking, no cutoff, no 0-100,
no course: screens and verdicts live on the page and in the judgment.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "opportunity_board.json"

CELL_PART = re.compile(r"([a-z0-9_]+):T(\d)")


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def band_position(close: Any, low: Any, high: Any, kill: Any) -> dict[str, Any]:
    """Where the close sits against a buy band: label, % to the band top (negative = above it), % to the kill."""
    c, lo, hi, k = _f(close), _f(low), _f(high), _f(kill)
    out: dict[str, Any] = {"band_state": None, "to_band_high_pct": None, "kill_dist_pct": None}
    if not c or lo is None or hi is None:
        return out
    if k is not None and c < k:
        state = "below_kill"
    elif c < lo:
        state = "under_band"
    elif c <= hi:
        state = "in_band"
    else:
        state = "over_band"
    out["band_state"] = state
    out["to_band_high_pct"] = round((hi / c - 1) * 100, 2)
    out["kill_dist_pct"] = round((k / c - 1) * 100, 2) if k is not None else None
    return out


def cell_terciles(cell: Any, axes: Mapping[str, str]) -> dict[str, Any]:
    """'runup:T2 & r_3m:T1 & eps_rev_3m:T1' -> {dest: 'T2', ...} for the axes the cell used (others stay empty)."""
    found = {axis: f"T{n}" for axis, n in CELL_PART.findall(str(cell or ""))}
    return {dest: found.get(axis) for axis, dest in axes.items()}


def _base_rows(pack: Path, base: Mapping[str, Any]) -> list[dict[str, Any]]:
    import duckdb

    path = Path(str(base["duckdb"]).replace("{PACK}", pack.as_posix()))
    path = path if path.is_absolute() else REPO_ROOT / path
    if not path.exists():
        raise SystemExit(f"Board base missing: {path} (run the outlook suite first)")
    con = duckdb.connect(str(path), read_only=True)
    try:
        cur = con.execute(f"SELECT * FROM {base.get('table') or 'names'}")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def column_order(recipe: Mapping[str, Any]) -> list[str]:
    return list(dict.fromkeys(f for _, fields in recipe.get("column_groups") or [] for f in fields))


def build_board(pack_dir: str | Path, *, recipe: Mapping[str, Any] | None = None, ids: Sequence[str] = ()) -> dict[str, Any]:
    """Tables ``names`` (column_groups order) and ``groups``; ``spec`` says which joins were found."""
    from generic_utils.earnings_week import attach_joins

    pack = Path(pack_dir)
    pack = pack if pack.is_absolute() else REPO_ROOT / pack
    recipe = dict(recipe or load_recipe())
    rows = _base_rows(pack, recipe.get("base") or {})
    # An id without an exchange (missing or listed twice) stays off the board and is reported, never guessed.
    bare = sorted(str(r.get("symbol")) for r in rows if ":" not in str(r.get("symbol") or ""))
    rows = [r for r in rows if ":" in str(r.get("symbol") or "")]
    have = {str(r.get("symbol")) for r in rows}
    for sym in ids:
        if sym not in have:
            rows.append({"symbol": sym})
    named = set(ids)
    used = attach_joins(rows, recipe.get("joins") or [], pack=pack)
    band = recipe.get("band") or {}
    axes = dict(recipe.get("cell_axes") or {})
    for row in rows:
        row.update(band_position(row.get("close"), row.get(band.get("low", "bounce_low")), row.get(band.get("high", "bounce_high")),
                                 row.get(band.get("kill", "bounce_kill"))))
        row.update(cell_terciles(row.get(recipe.get("cell_field") or "bb_cell"), axes))
        row["in_book"] = bool(_f(row.get("book_usd")))
        row["named"] = str(row.get("symbol")) in named
    order = column_order(recipe)
    names = [{f: r.get(f) for f in order} for r in rows]
    groups = [{"group": label, "n": len(fields), "columns": ", ".join(fields)} for label, fields in recipe.get("column_groups") or []]
    missing = [s for s in ids if s not in have]
    spec = {"pack": pack.as_posix(), "n_names": len(names), "joins": used, "missing_ids": missing, "bare_ids": bare,
            "n_in_band": sum(1 for r in rows if r.get("band_state") == "in_band"),
            "n_reporters": sum(1 for r in rows if r.get("bb_cell"))}
    return {"names": names, "groups": groups, "spec": spec}


def write_board(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    found = ", ".join(f"{k} ({v['n']})" if v.get("path") else f"{k} (absent)" for k, v in spec["joins"].items())
    notes = [
        "Base: PACK/outlook/outlook.duckdb names (US $2B+ floor, the Book and named ids). Joins (exact EXCHANGE:TICKER): " + found + ".",
        "band_state = the close against the bounce band: below_kill / under_band (between the kill and the band low) / in_band / over_band; "
        "to_band_high_pct = % from the close to the band top (negative = above it); kill_dist_pct = % from the close to the bounce kill.",
        "bar_runup / bar_tape / bar_rev = the beat-bar positioning cell split per axis (T1 = the lowest tercile on the lab's past prints: "
        "bar_rev T1 = estimates cut, bar_tape T1 = 3-month tape weakest). Empty when the cell fell back without that axis or the name does not print in W0 / W1.",
        "Every column is its own read in its own order: no 0-100 composite, no ranking, no cutoff, no course.",
    ]
    return write_run_export(out_dir, tool="generic_utils.opportunity_board", tables={"names": payload["names"], "groups": payload["groups"]},
                            sources={"pack": spec["pack"]}, notes=notes, extra={"spec": spec}, duckdb_name="board.duckdb")


def opportunity_board_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_board(build_board(pack), pack / "board"))


if __name__ == "__main__":
    opportunity_board_local_main()
