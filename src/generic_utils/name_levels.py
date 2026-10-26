"""Levels for a named set in one call: tape row -> entry book -> GMV lens -> day history -> safety band.

The same five steps an agent used to run as five CLI calls (named, entry-book, named on
the GMV lens, history, safety-band), with the same field lists and the same CSV
round trips, written into one folder:

    names.csv  entry_book/  gmv_names.csv  history.csv  safety_band/

History reads the day files through ``panel.load_panel`` (newest run per file, a file
without run metadata skipped, as ``series.field_history`` does), which is several times
faster on ~140 files. Themes (named id lists) live in config/generic_utils/themes.json.

No ranking, no cutoffs, no buy / sell choice: the levels are generic_utils.entry_book
and generic_utils.safety_band, unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
THEMES_PATH = REPO_ROOT / "config" / "generic_utils" / "themes.json"
ALL_FIELDS_ROOT = REPO_ROOT / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"

TAPE_FIELDS: tuple[str, ...] = (
    "symbol", "company", "ind", "close", "mcap", "day", "d5", "m1", "m3", "rsi", "rng", "vs50", "vs200", "atr", "atrp",
    "pt", "street_px", "target_px", "left", "street_left", "rr", "bo", "cont", "fwd", "mix", "mtp", "dte", "pe",
    "pe_fwd", "peg", "evebitda", "opm",
)
LENS_FIELDS: tuple[str, ...] = (
    "symbol", "lenses", "weak_lens", "strong_lens", "tape_state", "r_1m", "r_3m", "trend_q", "hi_prox", "updown_vol",
    "accum_3m", "vol_trend", "fwd_rev_g", "fwd_eps_g", "rev_yoy", "earn_yoy", "eps_rev_3m", "eps_rev_breadth",
    "rev_rev_3m", "pt_drift_3m", "eps_surprise", "pe_nfy", "pe_nfy_vs_ind", "pe_nfy_ind_med", "evebitda_vs_ind",
    "peg_fwd", "pt_left", "vol_d", "exp_move_20d", "rerate_floor", "compounder_floor", "value_turn_floor",
    "breakout_floor", "fade_floor",
)
HISTORY_FIELDS: tuple[str, ...] = (
    "symbol", "close", "change", "price_target_average", "earnings_per_share_forecast_next_fy", "market_cap_basic",
)
THEME_SLICE_FIELDS: tuple[str, ...] = (
    "symbol", "company", "ind", "mcap", "lenses", "weak_lens", "tape_state", "rerate_floor", "value_turn_floor",
    "compounder_floor", "r_1m", "r_3m", "trend_q", "updown_vol", "eps_rev_3m", "eps_rev_breadth", "fwd_rev_g",
    "fwd_eps_g", "pe_nfy", "pe_nfy_vs_ind", "peg_fwd", "pt_left", "left", "street_left", "dte", "book",
)


def load_themes(path: str | Path | None = None) -> dict[str, Any]:
    payload = json.loads(Path(path or THEMES_PATH).read_text(encoding="utf-8"))
    return {k: v for k, v in payload.items() if not k.startswith("_")}


def theme_ids(names: Iterable[str], themes: Mapping[str, Any] | None = None) -> list[str]:
    """Ids of the named themes, first-seen order, deduplicated."""
    themes = themes or load_themes()
    out: list[str] = []
    for name in names:
        if name not in themes:
            raise KeyError(f"Unknown theme {name!r}; known: {sorted(themes)}")
        for value in themes[name].get("ids") or []:
            if value not in out:
                out.append(value)
    return out


def _abs(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def history_rows(symbols: Sequence[str], *, fields: Sequence[str] = HISTORY_FIELDS, root: str | Path | None = None) -> dict[str, Any]:
    """Day-file history for exact EXCHANGE:TICKER ids (newest run per file; files without a run skipped)."""
    from generic_utils.panel import load_panel
    from generic_utils.series import list_dated_files

    sources = list_dated_files(_abs(root) if root else ALL_FIELDS_ROOT, "**/tradingview_all_fields_*.duckdb")
    ids = [s for s in symbols if s]
    if not ids:
        return {"rows": [], "n_files": len(sources), "skipped_files": 0}
    panel = load_panel(sources, columns=list(fields), numeric=[f for f in fields if f != "symbol"],
                       where_sql=f"symbol IN ({', '.join('?' for _ in ids)})", params=ids, skip_without_runs=True)
    return {"rows": panel["rows"], "n_files": len(sources),
            "skipped_files": sum(1 for snap in panel["snapshots"] if not snap.get("run_id"))}


def run_name_levels(
    pack_dir: str | Path,
    ids: Sequence[str],
    out_dir: str | Path,
    *,
    source_note: str | None = None,
    history_root: str | Path | None = None,
) -> dict[str, Any]:
    """Write names.csv, entry_book/, gmv_names.csv, history.csv and safety_band/ for ``ids`` on one pack."""
    from generic_utils.entry_book import load_entry_recipe, write_entry_book
    from generic_utils.safety_band import build_safety_band, load_safety_recipe, write_safety_band
    from generic_utils.scan_sources import rows_from_csv, rows_to_csv
    from generic_utils.series import fetch_named, select_named_rows

    pack = _abs(pack_dir)
    out = _abs(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tape = select_named_rows(rows_from_csv(pack / "movers" / "us2b_tape.csv"), columns=list(TAPE_FIELDS), ids=list(ids),
                             source=str(pack / "movers" / "us2b_tape.csv"))
    names_csv = rows_to_csv(out / "names.csv", tape["rows"])
    write_entry_book(rows_from_csv(names_csv), out / "entry_book", recipe=load_entry_recipe())
    lens_db = pack / "gmv" / "gmv_lens.duckdb"
    lens_note = "gmv lens missing on this pack"
    lens_rows: list[dict[str, Any]] = []
    if lens_db.exists():
        lens = fetch_named(lens_db, table="lens_tape", columns=list(LENS_FIELDS), ids=list(ids))
        lens_rows = lens["rows"]
        lens_note = f"gmv lens rows {len(lens_rows)}"
    lens_csv = rows_to_csv(out / "gmv_names.csv", lens_rows)
    symbols = [str(r.get("symbol") or "") for r in tape["rows"]]
    history = history_rows(symbols, root=history_root)
    history_csv = rows_to_csv(out / "history.csv", history["rows"])
    rows = build_safety_band(rows_from_csv(out / "entry_book" / "entry_book.csv"), lens=rows_from_csv(lens_csv),
                             history=rows_from_csv(history_csv), recipe=load_safety_recipe())
    exported = write_safety_band(rows, out / "safety_band", source_note=source_note)
    return {
        "out_dir": out.as_posix(),
        "n": len(tape["rows"]),
        "missing_ids": tape["spec"].get("missing_ids") or [],
        "lens": lens_note,
        "history_rows": len(history["rows"]),
        "history_files": history["n_files"],
        "history_skipped_files": history["skipped_files"],
        "safety_band": exported.get("duckdb"),
    }


def theme_slice(pack_dir: str | Path, industries: Sequence[str], *, field: str = "rerate_floor", top: int = 30,
                cap: int = 4, out: str | Path | None = None) -> dict[str, Any]:
    """GMV lens rows in the theme's industries, ordered by ``field`` with an industry cap (generic_utils.focus)."""
    from generic_utils.focus import build_focus, write_focus
    from generic_utils.scan_sources import rows_from_duckdb
    from generic_utils.series import qident

    db = _abs(pack_dir) / "gmv" / "gmv_lens.duckdb"
    cols = ", ".join(qident(c) for c in THEME_SLICE_FIELDS)
    sql = f"SELECT {cols} FROM lens_tape WHERE ind IN ({', '.join('?' for _ in industries)})"
    rows = rows_from_duckdb(db, sql, list(industries))
    payload = build_focus(rows, where=None, field=field, n=top, reverse=True, tie_breaker=None,
                          group_field="ind", cap=cap, exempt=None, id_field="symbol")
    if out:
        payload["out"] = write_focus(payload, _abs(out)).as_posix()
    return payload
