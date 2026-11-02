"""Week plays: the day's suites side by side for every name printing in the earnings-week window, and the Book.

Table ``plays`` has one row per earnings-week calendar name (W0 + W1, printed or not): the calendar row
(slot, tape into the print, estimates, value, past reactions, balance sheet), the alpha model's deciles
and 7d / 28d price bands, the entry book's buy-in-red band and kill (US $2B tape rows), and the signal-lab
print cells the name sits in now, each with the cell's measured mean, t and n. The safety band (bounce
band under the close, its kill, margin of safety to own / peer PE) is priced for this week's names and the
Book, the rows a trade this week can use (``levels_weeks`` in the recipe). Table ``book`` does the same
for every Book line, printing or not (fundamentals from the day file, lenses from the GMV book lens).
Table ``cells`` lists the measured cells those columns point at.

Today's row is put on the print-event schema the lab measured. Before the print (phase pre): runup,
the previous print's reaction (react_last), eps_rev_3m and pt_up. After it (phase post): the surprise,
the reaction (the session change when the reaction session is the scan day, the after-hours change
when it is the next session) less the median change of the US $2B tape (react_fx), and the surprise mix.
Quintile edges are recomputed from the lab's print_events.csv with signal_lab.print_cell_studies, so a cell
here is the same cell as in print_cells.

No ranking, no cutoffs, no composite, no course: which cell matters and what to buy is the page's and the
judgment's. Distances are percent of the close (``*_pct``) or of the bounce band middle (``*_vs_bmid_pct``).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any, Callable, Mapping, Sequence

from generic_utils.ranking import to_float

_f = to_float

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "week_plays.json"


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _abs(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def _pct(now: Any, ref: Any) -> float | None:
    a, b = _f(now), _f(ref)
    if a is None or b is None or b <= 0:
        return None
    return round((a / b - 1.0) * 100.0, 2)


def _csv(path: Path) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_csv

    return rows_from_csv(path) if path.exists() else []


def _by_symbol(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r.get("symbol") or ""): dict(r) for r in rows if r.get("symbol")}


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def scan_day_of(pack: Path) -> str:
    """The pack's compile date (UTC), else today."""
    path = pack / "briefing_pack.json"
    if path.exists():
        created = str(json.loads(path.read_text(encoding="utf-8")).get("created_at_utc") or "")
        if len(created) >= 10:
            return created[:10]
    return date.today().isoformat()


def market_day(tape: Sequence[Mapping[str, Any]]) -> float | None:
    """Median session change of the tape rows (the react_fx reference)."""
    xs = [x for x in (_f(r.get("day")) for r in tape) if x is not None]
    return round(median(xs), 3) if xs else None


def phase_of(row: Mapping[str, Any] | None) -> str:
    if not row:
        return "none"
    return "post" if _truthy(row.get("printed")) else "pre"


def pre_event(row: Mapping[str, Any]) -> dict[str, Any]:
    """A pre-print row on the print-event schema."""
    return {"runup": _f(row.get("runup")), "prev_react": _f(row.get("react_last")),
            "eps_rev_3m_pre": _f(row.get("eps_rev_3m")), "pt_up_pre": _f(row.get("pt_up"))}


def post_event(row: Mapping[str, Any], *, scan_day: str, mkt_day: float | None) -> dict[str, Any]:
    """A printed row on the print-event schema; react is empty when the reaction session is before the scan day.

    On the print night (reaction session after the scan day) the after-hours change stands in for the reaction,
    ``ah_read`` carries it for AMC rows, and the run-up / previous reaction still describe the set-up before
    this print, so the row also lands in the set-up x reaction cell. On the reaction day they would include it.
    """
    on = str(row.get("react_on") or "")[:10]
    night = bool(on) and on > scan_day
    react = _f(row.get("day")) if on == scan_day else _f(row.get("post_chg")) if night else None
    react_fx = None if react is None or mkt_day is None else round(react - mkt_day, 3)
    amc = str(row.get("slot") or "").startswith("AMC")
    mix = row.get("surprise_mix") or None
    if mix and "-" in str(mix) and str(row.get("rev_posted")).lower() == "false":
        mix = None  # revenue actual not posted yet (earnings_week.revenue_posted): no mix cell until it is
    return {"eps_surprise": _f(row.get("last_surprise")), "rev_surprise": _f(row.get("rev_surprise")),
            "surprise_mix": mix, "react": react, "react_fx": react_fx,
            "ah_read": _f(row.get("post_chg")) if night and amc else None,
            "runup": _f(row.get("runup")) if night else None, "prev_react": _f(row.get("react_last")) if night else None}


def attach_cells(rec: dict[str, Any], event: Mapping[str, Any] | None, phase: str, specs: Sequence[Mapping[str, Any]],
                 keys: Mapping[str, Callable[[Mapping[str, Any]], str | None]],
                 lookup: Mapping[tuple[str, str], Mapping[str, Any]]) -> None:
    """``<col>_cell`` / ``_mean`` / ``_t`` / ``_n`` per recipe cell (+ ``_q10`` / ``_q90`` when the spec sets
    ``tails``); empty off-phase or when a field is missing."""
    for spec in specs:
        col, study, measure = spec["col"], spec["study"], spec["measure"]
        key = keys.get(study)
        label = key(event) if key and event is not None and spec.get("phase") == phase else None
        stats = lookup.get((study, label)) if label else None
        rec[f"{col}_cell"] = label
        rec[f"{col}_mean"] = _f((stats or {}).get(f"{measure}_mean"))
        rec[f"{col}_t"] = _f((stats or {}).get(f"{measure}_t"))
        rec[f"{col}_n"] = _f((stats or {}).get(f"{measure}_n"))
        if spec.get("tails"):
            rec[f"{col}_q10"] = _f((stats or {}).get(f"{measure}_q10"))
            rec[f"{col}_q90"] = _f((stats or {}).get(f"{measure}_q90"))


def attach_tail_usd(rec: dict[str, Any], col: str | None) -> None:
    """``rx_tail_usd``: the line's dollars times the ``<col>_q10`` reaction of its cell (a size read, not a stop)."""
    usd = _f(rec.get("book_usd")) if rec.get("book_usd") not in (None, "") else _f(rec.get("value_usd"))
    q10 = _f(rec.get(f"{col}_q10")) if col else None
    rec["rx_tail_usd"] = round(usd * q10 / 100.0, 0) if usd is not None and q10 is not None else None


def attach_distances(rec: dict[str, Any]) -> None:
    """Alpha band and bounce-band distances in percent (no ratio, no score)."""
    close = rec.get("close")
    for h in ("7d", "28d"):
        rec[f"q20_{h}_pct"] = _pct(rec.get(f"b{h}_q20"), close)
        rec[f"q80_{h}_pct"] = _pct(rec.get(f"b{h}_q80"), close)
    lo, hi = _f(rec.get("bounce_low")), _f(rec.get("bounce_high"))
    mid = round((lo + hi) / 2.0, 4) if lo is not None and hi is not None else None
    rec["bmid"] = mid
    rec["bmid_vs_close_pct"] = _pct(mid, close)
    rec["bkill_vs_bmid_pct"] = _pct(rec.get("bounce_kill"), mid)
    rec["street_vs_bmid_pct"] = _pct(rec.get("pt"), mid)
    rec["q80_7d_vs_bmid_pct"] = _pct(rec.get("b7d_q80"), mid)
    rec["q80_28d_vs_bmid_pct"] = _pct(rec.get("b28d_q80"), mid)


def _pick(row: Mapping[str, Any] | None, fields: Sequence[str]) -> dict[str, Any]:
    row = row or {}
    return {f: row.get(f) for f in fields}


def entry_rows(tape: Mapping[str, Mapping[str, Any]], symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
    """generic_utils.entry_book on the US $2B tape rows of ``symbols`` (name-levels field list)."""
    from generic_utils.entry_book import attach_entry_book, load_entry_recipe
    from generic_utils.name_levels import TAPE_FIELDS

    rows = [{f: tape[s].get(f) for f in TAPE_FIELDS} for s in dict.fromkeys(symbols) if s in tape]
    return _by_symbol(attach_entry_book(rows, load_entry_recipe()))


def safety_rows(entries: Mapping[str, Mapping[str, Any]], symbols: Sequence[str], lens: Sequence[Mapping[str, Any]],
                *, history_root: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """generic_utils.safety_band (bounce band, own / peer margin of safety) on entry rows, with day-file history."""
    from generic_utils.name_levels import history_rows
    from generic_utils.safety_band import build_safety_band, load_safety_recipe

    ids = [s for s in dict.fromkeys(symbols) if s in entries]
    if not ids:
        return {}
    history = history_rows(ids, root=history_root)["rows"]
    return _by_symbol(build_safety_band([entries[s] for s in ids], lens=lens, history=history, recipe=load_safety_recipe()))


def day_file_rows(symbols: Sequence[str], database: str | Path | None) -> dict[str, dict[str, Any]]:
    """Earnings-week field set for exact ids from the day file's newest run (no universe floor)."""
    from generic_utils.earnings_week import derive_row, latest_day_file, newest_run, universe_rows

    ids = [s for s in symbols if ":" in s]
    if not ids:
        return {}
    db = Path(database) if database else latest_day_file()
    run_id, _ = newest_run(db)
    return _by_symbol(derive_row(r) for r in universe_rows(db, run_id, {"min_market_cap_usd": 1e30}, ids))


def month_tables(live: Sequence[Mapping[str, Any]], lens: Mapping[str, Mapping[str, Any]], recipe: Mapping[str, Any],
                 *, horizon: str = "28d") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The month-ahead read: every alpha live row with its GMV profile (``month``) and one row per industry (``month_ind``).

    ``month_ind`` holds the count, the median ``pred_<h>``, the mean decile, the share of names in deciles 10 and 1,
    the median trailing month and ``p_def``, the three names with the highest ``pred_<h>`` and the Book names in it.
    """
    pred, dec = f"pred_{horizon}", f"dec_{horizon}"
    month: list[dict[str, Any]] = []
    for row in live:
        rec = _pick(row, recipe.get("month_fields") or [])
        rec.update(_pick(lens.get(str(row.get("symbol") or "")), recipe.get("month_lens_fields") or []))
        month.append(rec)
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in live:
        groups.setdefault(str(row.get("ind") or "-"), []).append(row)

    def med(rows: Sequence[Mapping[str, Any]], field: str) -> float | None:
        xs = [x for x in (_f(r.get(field)) for r in rows) if x is not None]
        return round(median(xs), 3) if xs else None

    month_ind: list[dict[str, Any]] = []
    for ind, rows in groups.items():
        decs = [d for d in (_f(r.get(dec)) for r in rows) if d is not None]
        ranked = sorted((r for r in rows if _f(r.get(pred)) is not None), key=lambda r: -_f(r.get(pred)))
        month_ind.append({
            "ind": ind, "n": len(rows), f"{pred}_med": med(rows, pred),
            f"{dec}_mean": round(sum(decs) / len(decs), 2) if decs else None,
            "dec10_share": round(sum(1 for d in decs if d >= 10) / len(decs), 3) if decs else None,
            "dec1_share": round(sum(1 for d in decs if d <= 1) / len(decs), 3) if decs else None,
            "r_1m_med": med(rows, "r_1m"), "p_def_med": med(rows, "p_def"),
            "leaders": " ".join(str(r["symbol"]) for r in ranked[:3]) or None,
            "book": " ".join(str(r["symbol"]) for r in rows if _f(r.get("book_usd"))) or None,
        })
    return month, month_ind


def build_week_plays(pack_dir: str | Path, *, recipe: Mapping[str, Any] | None = None, ids: Sequence[str] = (),
                     database: str | Path | None = None, history_root: str | Path | None = None) -> dict[str, Any]:
    """Tables ``plays`` (calendar names), ``book`` (Book lines) and ``cells`` (measured cells referenced)."""
    from generic_utils.signal_lab import load_lab_recipe, print_cell_studies
    from generic_utils.symbol_identity import ticker_map

    pack = _abs(pack_dir)
    recipe = dict(recipe or load_recipe())
    cal_path = pack / "earnings_week" / "calendar.csv"
    if not cal_path.exists():
        raise FileNotFoundError(f"{cal_path} missing: run tv_scan_cli.py earnings-week --pack {pack} first")
    calendar = _csv(cal_path)
    if ids:
        by_ticker = ticker_map(r["symbol"] for r in calendar)
        wanted = {i if ":" in i else by_ticker.get(i.upper(), i) for i in ids}
        calendar = [r for r in calendar if r["symbol"] in wanted]
    live = _by_symbol(_csv(pack / "alpha_model" / "live.csv"))
    events = _csv(pack / "signal_lab" / "print_events.csv")
    cell_rows = _csv(pack / "signal_lab" / "print_cells.csv")
    keys = {name: key for name, key, _ in print_cell_studies(events, load_lab_recipe())} if events else {}
    lookup = {(str(r.get("study")), str(r.get("cell"))): r for r in cell_rows}
    tape_rows = _csv(pack / "movers" / "us2b_tape.csv")
    tape = _by_symbol(tape_rows)
    mkt = market_day(tape_rows)
    scan_day = scan_day_of(pack)
    book_rows = _csv(pack / "book_risk" / "book_risk.csv")
    book_ids = [str(r.get("symbol") or "") for r in book_rows if r.get("symbol")]
    specs = list(recipe.get("cells") or [])
    tail_col = recipe.get("tail_cell")
    alpha_f = list(recipe.get("alpha_fields") or [])
    entry_f = list(recipe.get("entry_fields") or [])
    entries = entry_rows(tape, [r["symbol"] for r in calendar] + book_ids)
    level_weeks = set(recipe.get("levels_weeks") or ["W0"])
    level_ids = [r["symbol"] for r in calendar if r.get("week") in level_weeks] + book_ids
    safety = safety_rows(entries, level_ids, _csv(pack / "gmv" / "lens_tape.csv"), history_root=history_root)
    safety_f = list(recipe.get("safety_fields") or [])

    def event_for(row: Mapping[str, Any], phase: str) -> dict[str, Any] | None:
        if phase == "pre":
            return pre_event(row)
        if phase == "post":
            return post_event(row, scan_day=scan_day, mkt_day=mkt)
        return None

    plays: list[dict[str, Any]] = []
    for row in calendar:
        phase = phase_of(row)
        rec = {"symbol": row["symbol"], "phase": phase}
        rec.update(_pick(row, [f for f in recipe.get("calendar_fields") or [] if f != "symbol"]))
        rec["react_fx"] = (event_for(row, phase) or {}).get("react_fx") if phase == "post" else None
        rec.update(_pick(live.get(row["symbol"]), alpha_f))
        rec.update(_pick(entries.get(row["symbol"]), entry_f))
        rec.update(_pick(safety.get(row["symbol"]), safety_f))
        attach_cells(rec, event_for(row, phase), phase, specs, keys, lookup)
        attach_tail_usd(rec, tail_col if phase == "pre" else None)
        attach_distances(rec)
        plays.append(rec)

    cal_by = _by_symbol(_csv(cal_path))
    lens = _by_symbol(_csv(pack / "gmv" / "book_lens.csv"))
    funda = day_file_rows(book_ids, database)
    book: list[dict[str, Any]] = []
    for row in book_rows:
        sym = str(row.get("symbol") or "")
        if not sym:
            continue
        cal = cal_by.get(sym)
        phase = phase_of(cal)
        rec = {"symbol": sym, "phase": phase}
        rec.update(_pick(cal, ["week", "earn_date", "earn_dow", "slot", "react_on", "dte", "runup", "react_last",
                               "react_abs_mean", "react_hist"]))
        rec.update(_pick(row, recipe.get("book_fields") or []))
        base = funda.get(sym) or {}
        rec.update(_pick(base, recipe.get("fundamental_fields") or []))
        if rec.get("close") in (None, ""):
            rec["close"] = row.get("close")
        rec.update(_pick(lens.get(sym), recipe.get("lens_fields") or []))
        rec.update(_pick(live.get(sym), alpha_f))
        rec.update(_pick(entries.get(sym), entry_f))
        rec.update(_pick(safety.get(sym), safety_f))
        attach_cells(rec, event_for(cal, phase) if cal else None, phase, specs, keys, lookup)
        attach_tail_usd(rec, tail_col if phase == "pre" else None)
        attach_distances(rec)
        book.append(rec)

    studies = {s["study"] for s in specs}
    cells = [dict(r) for r in cell_rows if r.get("study") in studies]
    month, month_ind = month_tables(list(live.values()), _by_symbol(_csv(pack / "gmv" / "lens_tape.csv")), recipe)
    return {
        "plays": plays, "book": book, "cells": cells, "month": month, "month_ind": month_ind,
        "spec": {"pack": pack.as_posix(), "scan_day": scan_day, "mkt_day": mkt, "n_plays": len(plays),
                 "n_book": len(book), "n_events": len(events), "alpha": bool(live), "lab": bool(cell_rows),
                 "entry_rows": len(entries), "safety_rows": len(safety), "levels_weeks": sorted(level_weeks),
                 "cells": specs, "tail_cell": tail_col},
    }


def write_week_plays(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"scan day {spec['scan_day']}; react_fx reference = median session change of the pack US $2B tape ({spec['mkt_day']}).",
        "plays: one row per earnings-week calendar name (W0 + W1). phase pre = not printed yet, post = printed before the scan.",
        "book: one row per Book line; phase none when the line has no print in the window. Fundamentals from the day file, lenses from PACK/gmv/book_lens.csv.",
        "Alpha columns (dec_*, pred_*, b<h>_q20/q50/q80, why_*) are PACK/alpha_model/live.csv unchanged: a forecast with an out-of-sample record, never a gate or a size.",
        "Entry columns are generic_utils.entry_book on the US $2B tape row (name-levels field list); empty outside that tape. entry_low..entry_high is the 50-day band (sma_role reclaim when the close is under it).",
        f"bounce_* / mos_* / trim_zone are generic_utils.safety_band with day-file history, for weeks {', '.join(spec['levels_weeks'])} and the Book: the buy-in-red band under the close and its kill.",
        "<col>_cell is the signal-lab print cell the row sits in now; <col>_mean / _t / _n (and _q10 / _q90 where the spec sets tails) are that cell's measured value of the spec's measure (recipe cells: hold_fx_28d or react_fx pre, drift_fx_28d or ah_to_close post). Print t-stats treat prints as independent: optimistic.",
        f"On the print night (AMC, reaction session after the scan day) the after-hours change stands in for the reaction in react_fx and the post cells; rx_tail_usd is the line's dollars times the {spec.get('tail_cell') or 'tail'} cell's 10th-percentile reaction (pre rows).",
        "Cells need every input field; a missing field leaves the cell empty. A post row whose reaction session is before the scan day has no react_fx.",
        "q20_<h>_pct / q80_<h>_pct: alpha band against the close. bmid is the bounce band middle; bmid_vs_close_pct, bkill_vs_bmid_pct, street_vs_bmid_pct, q80_<h>_vs_bmid_pct are against it.",
        "month: every alpha live row (the 28d / 56d forecast, bands, drivers) with its GMV lens profile; month_ind: one row per industry "
        "(median pred_28d, mean decile, decile 10 / 1 shares, median trailing month and p_def, the three highest pred_28d names, Book names). A regime read, not a buy list.",
        "No ranking, no composite, no course: order by one column at a time.",
    ]
    return write_run_export(
        out_dir, tool="generic_utils.week_plays",
        tables={k: payload.get(k) or [] for k in ("plays", "book", "cells", "month", "month_ind")},
        sources={"pack": spec["pack"]}, notes=notes, extra={"spec": spec}, duckdb_name="week_plays.duckdb")


def week_plays_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_week_plays(build_week_plays(pack), pack / "week_plays"))


if __name__ == "__main__":
    week_plays_local_main()
