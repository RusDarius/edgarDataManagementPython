"""Catalyst outlook: every US name on the universe floor with its catalysts over the next weeks and the day's suites side by side.

Table ``names`` has one row per name on the floor (day file newest run, recipe markets / types / market cap,
plus named ids and the Book), its columns in the recipe's ``column_groups`` order: technical (tape, trend,
participation), setup (the next catalyst, the earnings slot, the pre-print cell, the entry book's 50-day
band and kill, the buy-in-red bounce band for names with a catalyst inside ``levels_window_days``),
earnings surprise (last print, reaction, drift cell), value (forward P/E to peers and to its own history,
cash flow, balance sheet, street target, leftover), progression (estimate revisions, the alpha model's
deciles and bands, the GMV lenses, analyst coverage) and the pack lists the name is on (empty = no list).

Table ``events`` has one row per catalyst inside the horizon: earnings (day file next release date and
slot), ex-dividend (upcoming ex-date, amount, yield), IPO lockup (offer date + ``lockup_days``), and the
dated OUTSIDE rows of ``config/generic_utils/catalyst_calendar.json`` (macro, central banks, index
reviews, regulatory decisions, conferences). Table ``days`` is one row per session with the counts and
the largest names. Weeks are W0 (the scan's Monday-Sunday) to W<n>.

The catalyst fields (ex-dividend, IPO date, analyst count, 52-week-high date) are read from the newest run
of the day file that carries them (the full scan; intraday refreshes leave them empty): ``spec`` names it.

No ranking, no cutoffs beyond the universe floor, no composite, no course: which window, which cell and
what to buy are the page's and the judgment's. Distances are percent of the close unless named otherwise.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float
from generic_utils.series import qident

_f = to_float

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "catalyst_outlook.json"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _abs(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def _csv(path: Path) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_csv

    return rows_from_csv(path) if path.exists() else []


def _by_symbol(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r.get("symbol") or ""): dict(r) for r in rows if r.get("symbol")}


def _pick(row: Mapping[str, Any] | None, fields: Sequence[str]) -> dict[str, Any]:
    row = row or {}
    return {f: row.get(f) for f in fields}


def week_label(day: date, scan_day: date) -> str:
    """W0 = the scan day's Monday-Sunday week, W1 the next, ..."""
    monday = scan_day - timedelta(days=scan_day.weekday())
    return f"W{(day - monday).days // 7}"


def column_order(recipe: Mapping[str, Any]) -> list[str]:
    return [f for _, fields in recipe.get("column_groups") or [] for f in fields]


# ---------------------------------------------------------------- day file


def catalyst_run(database: str | Path, probe: str) -> tuple[str | None, Any]:
    """Newest run of the day file whose ``probe`` column is filled (the full scan), with its time."""
    import duckdb

    con = duckdb.connect(str(database), read_only=True)
    try:
        present = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'all_fields_rows'").fetchall()}
        if probe not in present:
            return None, None
        for run_id, created in con.execute("SELECT run_id, created_at_utc FROM run_metadata ORDER BY created_at_utc DESC").fetchall():
            hit = con.execute(f"SELECT COUNT({qident(probe)}) FROM all_fields_rows WHERE run_id = ?", [run_id]).fetchone()[0]
            if hit:
                return str(run_id), created
        return None, None
    finally:
        con.close()


def catalyst_rows(database: str | Path, run_id: str, fields: Mapping[str, str], symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
    """``fields`` (short name -> day-file column) for exact ids from one run; missing columns come back empty."""
    import duckdb

    ids = [s for s in dict.fromkeys(symbols) if s]
    if not ids:
        return {}
    con = duckdb.connect(str(database), read_only=True)
    try:
        present = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'all_fields_rows'").fetchall()}
        select = ["symbol"] + [f"TRY_CAST({qident(col)} AS DOUBLE) AS {qident(short)}" if col in present else f"NULL AS {qident(short)}"
                               for short, col in fields.items()]
        cur = con.execute(f"SELECT {', '.join(select)} FROM all_fields_rows WHERE run_id = ? AND symbol IN ({', '.join('?' for _ in ids)})",
                          [run_id, *ids])
        names = [d[0] for d in cur.description]
        return _by_symbol([dict(zip(names, values)) for values in cur.fetchall()])
    finally:
        con.close()


# ---------------------------------------------------------------- catalysts


def name_events(row: Mapping[str, Any], *, scan_day: date, end: date, lockup_days: int, amc_from_hour_utc: int = 13) -> list[dict[str, Any]]:
    """Earnings, ex-dividend and IPO-lockup events of one name inside [scan_day, end]."""
    from generic_utils.earnings_week import epoch_dt, slot_label

    out: list[dict[str, Any]] = []
    at = epoch_dt(row.get("earn_next"))
    if at is not None and scan_day <= at.date() <= end:
        out.append({"kind": "earnings", "date": at.date(), "slot": slot_label(row.get("earn_next_time"), at, amc_from_hour_utc),
                    "detail": None, "status": "day file"})
    ex = epoch_dt(row.get("exdiv"))
    if ex is not None and scan_day <= ex.date() <= end:
        amount, yld = _f(row.get("div_amt")), _f(row.get("div_yield"))
        detail = " ".join(x for x in (f"{amount:g}/sh" if amount else "", f"yield {yld:.2f}%" if yld else "") if x) or None
        out.append({"kind": "ex_dividend", "date": ex.date(), "slot": None, "detail": detail, "status": "day file"})
    ipo = epoch_dt(row.get("ipo_date"))
    if ipo is not None:
        lock = (ipo + timedelta(days=lockup_days)).date()
        if scan_day <= lock <= end:
            out.append({"kind": "ipo_lockup", "date": lock, "slot": None, "detail": f"IPO {ipo.date().isoformat()} + {lockup_days}d",
                        "status": "estimated"})
    return out


def calendar_events(path: str | Path | None, *, scan_day: date, end: date) -> list[dict[str, Any]]:
    """Dated OUTSIDE rows of the catalyst calendar inside [scan_day, end]; one row per symbol (or one without)."""
    if not path or not _abs(path).exists():
        return []
    rows = json.loads(_abs(path).read_text(encoding="utf-8")).get("events") or []
    out = []
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("date"))[:10])
        except ValueError:
            continue
        if not scan_day <= day <= end:
            continue
        base = {"kind": row.get("kind") or "other", "date": day, "slot": None, "detail": row.get("event"),
                "status": row.get("status"), "source": row.get("source"), "read": row.get("read")}
        for sym in row.get("symbols") or [None]:
            out.append({**base, "symbol": sym})
    return out


def pack_lists(pack: Path) -> dict[str, str]:
    """Per symbol, the pack lists it is on: radar50#k, earn_up, earn_down, short15, payoff10, <stance>, <h> <tail>, book."""
    import duckdb

    found: dict[str, list[str]] = {}
    db = pack / "briefing_pack.duckdb"
    if db.exists():
        con = duckdb.connect(str(db), read_only=True)
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}

            def add(sym: Any, tag: str) -> None:
                if sym:
                    found.setdefault(str(sym), []).append(tag)

            if "radar_curated_50" in tables:
                for i, (sym,) in enumerate(con.execute("SELECT symbol FROM radar_curated_50").fetchall(), 1):
                    add(sym, f"radar50#{i}")
            for table, tag in (("earnings_upside_curated", "earn_up"), ("earnings_downside_curated", "earn_down"),
                               ("short_book_15_curated", "short15"), ("book", "book")):
                if table in tables:
                    for (sym,) in con.execute(f"SELECT symbol FROM {table}").fetchall():
                        add(sym, tag)
            if "suggested_courses" in tables:
                for sym, stance in con.execute("SELECT symbol, stance FROM suggested_courses").fetchall():
                    add(sym, str(stance))
            if "movers_tails" in tables:
                for sym, horizon, tail in con.execute("SELECT symbol, horizon, tail FROM movers_tails").fetchall():
                    add(sym, f"{horizon} {tail}")
        finally:
            con.close()
    for row in _csv(pack / "payoff_focus" / "payoff_focus.csv"):
        if row.get("symbol"):
            found.setdefault(str(row["symbol"]), []).append("payoff10")
    return {s: " ".join(dict.fromkeys(tags)) for s, tags in found.items()}


# ---------------------------------------------------------------- build


def build_outlook(pack_dir: str | Path, *, recipe: Mapping[str, Any] | None = None, ids: Sequence[str] = (),
                  database: str | Path | None = None, history_root: str | Path | None = None) -> dict[str, Any]:
    """Tables ``names``, ``events``, ``days`` and the ``groups`` the names columns are ordered by."""
    from generic_utils.earnings_week import derive_row, latest_day_file, newest_run, universe_rows
    from generic_utils.week_plays import entry_rows, safety_rows, scan_day_of

    pack = _abs(pack_dir)
    recipe = dict(recipe or load_recipe())
    db = Path(database) if database else latest_day_file()
    run_id, _ = newest_run(db)
    scan_day = date.fromisoformat(scan_day_of(pack))
    horizon = int(recipe.get("horizon_days") or 42)
    end = scan_day + timedelta(days=horizon)
    book_rows = _csv(pack / "book_risk" / "book_risk.csv")
    book_ids = [str(r.get("symbol")) for r in book_rows if r.get("symbol")]
    book_value = {str(r["symbol"]): r.get("value_usd") for r in book_rows if r.get("symbol")}
    wanted = list(dict.fromkeys([*ids, *book_ids]))
    universe = [derive_row(r) for r in universe_rows(db, run_id, dict(recipe.get("universe") or {}), wanted)]
    symbols = [r["symbol"] for r in universe]

    cat_run, _ = catalyst_run(db, str(recipe.get("catalyst_probe_field") or "recommendation_total"))
    cats = catalyst_rows(db, cat_run, recipe.get("catalyst_fields") or {}, symbols) if cat_run else {}
    live = _by_symbol(_csv(pack / "alpha_model" / "live.csv"))
    lens = _by_symbol(_csv(pack / "gmv" / "lens_tape.csv"))
    tape = _by_symbol(_csv(pack / "movers" / "us2b_tape.csv"))
    plays = _by_symbol(_csv(pack / "week_plays" / "plays.csv"))
    calendar = _by_symbol(_csv(pack / "earnings_week" / "calendar.csv"))
    from generic_utils.event_risk import warn_lookup

    risk = warn_lookup(pack)  # the core warning field (PACK/event_risk); empty before that suite has run
    lists = pack_lists(pack)
    joins = recipe.get("joins") or {}
    lockup = int(recipe.get("lockup_days") or 180)
    amc_hour = int(recipe.get("amc_from_hour_utc") or 13)

    events: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for base in universe:
        sym = base["symbol"]
        rec = dict(base)
        rec.update(cats.get(sym) or {})
        mine = [{**e, "symbol": sym} for e in name_events(rec, scan_day=scan_day, end=end, lockup_days=lockup, amc_from_hour_utc=amc_hour)]
        events.extend(mine)
        rec["mcap_b"] = round(_f(rec.get("mcap")) / 1e9, 1) if _f(rec.get("mcap")) else None
        hi = _f(rec.get("hi52_date"))
        rec["hi52_days"] = (scan_day - datetime.fromtimestamp(hi, tz=timezone.utc).date()).days if hi and hi > 0 else None
        earn = next((e for e in mine if e["kind"] == "earnings"), None)
        rec["earn_date"] = earn["date"].isoformat() if earn else None
        rec["earn_slot"] = earn["slot"] if earn else None
        rec["earn_days"] = (earn["date"] - scan_day).days if earn else None
        rec["earn_week"] = week_label(earn["date"], scan_day) if earn else None
        for source, fields in (("alpha", live), ("lens", lens), ("tape", tape), ("plays", plays), ("calendar", calendar), ("risk", risk)):
            for f in joins.get(source) or []:
                if rec.get(f) in (None, ""):
                    rec[f] = (fields.get(sym) or {}).get(f)
        rec["pack_lists"] = lists.get(sym)
        if rec.get("book_usd") in (None, "") and sym in book_value:
            rec["book_usd"] = book_value[sym]
        rows.append(rec)

    outside = calendar_events(recipe.get("calendar_path"), scan_day=scan_day, end=end)
    events.extend(outside)
    by_symbol_events: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        if e.get("symbol"):
            by_symbol_events.setdefault(str(e["symbol"]), []).append(e)

    entry_f, safety_f = list(recipe.get("entry_fields") or []), list(recipe.get("safety_fields") or [])
    entries = entry_rows(tape, symbols)
    window = scan_day + timedelta(days=int(recipe.get("levels_window_days") or 21))
    level_ids = [s for s in symbols if any(e["date"] <= window for e in by_symbol_events.get(s, []))] + wanted
    safety = safety_rows(entries, level_ids, list(lens.values()), history_root=history_root) if level_ids else {}
    for rec in rows:
        sym = rec["symbol"]
        mine = sorted(by_symbol_events.get(sym, []), key=lambda e: (e["date"], e["kind"]))
        nxt = mine[0] if mine else None
        rec["next_kind"] = nxt["kind"] if nxt else None
        rec["next_date"] = nxt["date"].isoformat() if nxt else None
        rec["next_dow"] = WEEKDAYS[nxt["date"].weekday()] if nxt else None
        rec["next_days"] = (nxt["date"] - scan_day).days if nxt else None
        rec["catalysts"] = "; ".join(f"{e['kind']} {e['date'].strftime('%m-%d')}" + (f" {e['slot']}" if e.get("slot") else "") for e in mine) or None
        rec.update(_pick(entries.get(sym), entry_f))
        # The safety band where this run priced it; otherwise the week-plays band (joined above) stands.
        rec.update({k: v for k, v in _pick(safety.get(sym), safety_f).items() if v not in (None, "")})
        close, bl, bh = _f(rec.get("close")), _f(rec.get("bounce_low")), _f(rec.get("bounce_high"))
        rec["bmid_vs_close_pct"] = round(((bl + bh) / 2 / close - 1) * 100, 2) if close and bl and bh else None

    order = column_order(recipe)
    names = [{f: rec.get(f) for f in order} for rec in rows]

    by_sym = {r["symbol"]: r for r in rows}
    event_rows: list[dict[str, Any]] = []
    for e in sorted(events, key=lambda e: (e["date"], str(e.get("symbol") or ""))):
        rec = by_sym.get(str(e.get("symbol") or "")) or {}
        event_rows.append({"date": e["date"].isoformat(), "dow": WEEKDAYS[e["date"].weekday()], "week": week_label(e["date"], scan_day),
                           "days": (e["date"] - scan_day).days, "kind": e["kind"], "symbol": e.get("symbol"), "slot": e.get("slot"),
                           "detail": e.get("detail"), "status": e.get("status"), "source": e.get("source"), "read": e.get("read"),
                           "mcap_b": rec.get("mcap_b"), "in_book": bool(_f(rec.get("book_usd"))) if rec else None})

    days: list[dict[str, Any]] = []
    top_n = int(recipe.get("names_per_day") or 8)
    for i in range(horizon + 1):
        d = scan_day + timedelta(days=i)
        todays = [e for e in event_rows if e["date"] == d.isoformat()]
        if not todays:
            continue
        earn = sorted((e for e in todays if e["kind"] == "earnings"), key=lambda e: -(_f(e["mcap_b"]) or 0))
        other = [e for e in todays if e["kind"] not in {"earnings", "ex_dividend", "ipo_lockup"}]
        days.append({"date": d.isoformat(), "dow": WEEKDAYS[d.weekday()], "week": week_label(d, scan_day),
                     "n_earnings": len(earn), "n_ex_dividend": sum(1 for e in todays if e["kind"] == "ex_dividend"),
                     "n_ipo_lockup": sum(1 for e in todays if e["kind"] == "ipo_lockup"),
                     "largest_earnings": " ".join(f"{e['symbol']} {e['slot']}" for e in earn[:top_n]) or None,
                     "book_earnings": " ".join(f"{e['symbol']} {e['slot']}" for e in earn if e.get("in_book")) or None,
                     "other": " | ".join(f"{e['detail']}" + (f" ({e['symbol']})" if e.get("symbol") else "") for e in other) or None})

    groups = [{"group": label, "n": len(fields), "columns": ", ".join(fields)} for label, fields in recipe.get("column_groups") or []]
    return {
        "names": names, "events": event_rows, "days": days, "groups": groups,
        "spec": {"pack": pack.as_posix(), "scan_day": scan_day.isoformat(), "end": end.isoformat(), "horizon_days": horizon,
                 "day_file": db.as_posix(), "run_id": run_id, "catalyst_run": cat_run, "n_names": len(names),
                 "n_events": len(event_rows), "n_outside": len(outside), "n_levels": len(safety),
                 "alpha": bool(live), "lens": bool(lens), "plays": bool(plays)},
    }


def write_outlook(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"scan day {spec['scan_day']}, horizon to {spec['end']} ({spec['horizon_days']} days); universe from {spec['run_id']}; "
        f"catalyst fields (ex-dividend, IPO date, analysts, 52-week-high date) from {spec['catalyst_run']}.",
        "names: one row per name on the universe floor (plus named ids and the Book), columns in column_groups order (table groups).",
        "events: earnings (day-file next release, slot BMO / AMC, ~ = inferred from the UTC hour), ex_dividend, ipo_lockup "
        "(offer date + lockup_days, estimated) and the OUTSIDE rows of config/generic_utils/catalyst_calendar.json (status, source).",
        "days: one row per session with events: counts, the largest earnings names by market cap, Book prints, other catalysts.",
        "Alpha, lens, tape, week-plays and earnings-week columns are joined unchanged from the pack; bounce_* only for names with an "
        "event inside levels_window_days, the Book and named ids. pack_lists empty = the name is on no pack list.",
        "No ranking, no cutoff beyond the universe floor, no composite, no course.",
    ]
    return write_run_export(out_dir, tool="generic_utils.catalyst_outlook",
                            tables={k: payload[k] for k in ("names", "events", "days", "groups")},
                            sources={"pack": spec["pack"], "day_file": spec["day_file"]}, notes=notes, extra={"spec": spec},
                            duckdb_name="outlook.duckdb")


def outlook_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_outlook(build_outlook(pack), pack / "outlook"))


if __name__ == "__main__":
    outlook_local_main()
