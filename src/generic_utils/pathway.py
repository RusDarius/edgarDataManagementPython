"""Pathway: where the Book is tilted against what has been paying, and what a trade would do to that tilt.

Tables (knobs in config/generic_utils/pathway.json):

- ``exposure``: one row per factor. ``book_pctl`` is the Book's NAV-weighted cross-sectional percentile (0..1)
  of that factor among the newest store day's floor names (0.5 = the median name), ``gap`` = book_pctl - 0.5.
  Beside it, what the factor paid on the signal lab: the full-sample IC and verdict at ``ic_horizon``, the IC
  in up / down markets, this month's and last month's IC at ``month_horizon``, and the trailing tape (mean daily
  IC over the last ``tape_window_days`` with labels, the window before it, the share of positive days).
  ``tilt_month`` / ``tilt_tape`` = gap x that IC: the sign says whether the tilt was paid (+) or cost (-).
  Per factor only; never summed across factors.
- ``lines``: one row per Book line: the 1M / 3M return split into market (floor median), industry (the
  industry median minus the market) and stock (the line minus its industry median), the line's percentile on
  each ``move_factors`` factor, ``d_<factor>_trim`` (the Book's exposure change if the line were trimmed by
  ``trim_frac``), and the joins (deterioration letters, alpha band, safety band).
- ``attribution``: the Book's NAV-weighted sum of those parts per horizon, in percent and dollars of today's
  values (an approximation: today's weights, not the path).
- ``regime``: one row per store day: median 1-day and 1-month return, breadth (share up on the day), share over
  the 200-day, median realized volatility and distance to the 52-week high, on the floor.
- ``factor_tape``: daily IC per factor at ``tape_horizon`` with its trailing mean.
- ``candidates``: every floor name outside the Book (plus named ids) with its percentiles, ``d_<factor>_add``
  (the Book's exposure change if a ``starter_usd`` line were added), the joins and the industry's month-ahead
  read (week_plays month_ind).

No ranking, no composite, no course: the tilt, the attribution and the deltas are separate reads.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from generic_utils.panel_store import REPO_ROOT, feature_family, feature_labels, load_store_recipe
from generic_utils.ranking import to_float
from generic_utils.series import qident

_f = to_float
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "pathway.json"


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: Any, digits: int = 3) -> float | None:
    number = _f(value)
    return None if number is None or math.isnan(number) else round(number, digits)


def _lit(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


# --- formulas ----------------------------------------------------------------------------------------------


def percentiles(values: Sequence[Any]) -> list[float | None]:
    """Average-rank percentile in 0..1 of each value among the non-empty ones (ties share a rank)."""
    pairs = sorted((float(v), i) for i, v in enumerate(values) if _f(v) is not None)
    out: list[float | None] = [None] * len(values)
    n = len(pairs)
    k = 0
    while k < n:
        j = k
        while j + 1 < n and pairs[j + 1][0] == pairs[k][0]:
            j += 1
        pct = ((k + j) / 2.0) / (n - 1) if n > 1 else 0.5
        for m in range(k, j + 1):
            out[pairs[m][1]] = pct
        k = j + 1
    return out


def weighted_mean(pairs: Sequence[tuple[float, float]]) -> float | None:
    """Mean of values weighted by dollars; pairs are (weight, value)."""
    w = sum(a for a, _ in pairs)
    return sum(a * b for a, b in pairs) / w if w > 0 else None


def exposure_after(pairs: Sequence[tuple[float, float]], add_usd: float, value: float | None) -> float | None:
    """The weighted mean after adding ``add_usd`` (negative = sell) of a line whose value is ``value``."""
    if value is None:
        return None
    w = sum(a for a, _ in pairs) + add_usd
    return (sum(a * b for a, b in pairs) + add_usd * value) / w if w > 0 else None


def split_return(r: Any, mkt: Any, ind: Any) -> dict[str, float | None]:
    """Total vs market, split into the industry part (industry median - market) and the stock part (line - industry)."""
    a, m, i = _f(r), _f(mkt), _f(ind)
    return {"rel": _r(a - m, 2) if a is not None and m is not None else None,
            "ind_part": _r(i - m, 2) if i is not None and m is not None else None,
            "stock_part": _r(a - i, 2) if a is not None and i is not None else None}


# --- store reads -------------------------------------------------------------------------------------------


def newest_rows(con: Any, recipe: Mapping[str, Any], ids: Sequence[str]) -> tuple[list[dict[str, Any]], str]:
    known = {r[0] for r in con.execute("DESCRIBE features").fetchall()}
    cols = ["ind", "mcap"] + [c for c in list(recipe.get("factors") or []) + list(recipe.get("returns") or []) + ["r_1d"] if c in known]
    cols = [c for i, c in enumerate(cols) if c not in cols[:i]]
    floor = float((recipe.get("study") or {}).get("min_market_cap_usd") or 0)
    day = con.execute("SELECT max(as_of) FROM features").fetchone()[0]
    id_list = ", ".join(_lit(i) for i in ids) or "''"
    cur = con.execute(f"SELECT symbol, {', '.join(qident(c) for c in cols)}, mcap >= {floor!r} AS on_floor FROM features "
                      f"WHERE as_of = ? AND (mcap >= {floor!r} OR symbol IN ({id_list})) ORDER BY symbol", [day])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()], day.isoformat()


def regime_rows(con: Any, recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    floor = float((recipe.get("study") or {}).get("min_market_cap_usd") or 0)
    days = int(recipe.get("regime_days") or 90)
    cur = con.execute(f"""
        SELECT as_of, count(*) AS n, median(r_1d) AS mkt_1d, median(r_1m) AS mkt_1m,
               avg(CASE WHEN r_1d IS NULL THEN NULL WHEN r_1d > 0 THEN 1.0 ELSE 0.0 END) AS breadth_1d,
               avg(CASE WHEN vs200 IS NULL THEN NULL WHEN vs200 > 0 THEN 1.0 ELSE 0.0 END) AS above_200d,
               median(rvol_20) AS med_rvol_20, median(hi_prox) AS med_hi_prox
        FROM features WHERE mcap >= {floor!r} AND as_of >= (SELECT max(as_of) FROM features) - INTERVAL {days} DAY
        GROUP BY as_of ORDER BY as_of""")
    names = [d[0] for d in cur.description]
    return [{k: (v.isoformat() if isinstance(v, date) else v if k == "n" else _r(v, 4)) for k, v in zip(names, r)} for r in cur.fetchall()]


# --- signal-lab reads --------------------------------------------------------------------------------------


def lab_reads(lab_db: Path | None, recipe: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """{factor: IC reads} and the daily tape rows, from PACK/signal_lab/signal_lab.duckdb (empty when absent)."""
    import duckdb

    factors = list(recipe.get("factors") or [])
    if lab_db is None or not lab_db.exists() or not factors:
        return {}, []
    ich, mh, th = str(recipe.get("ic_horizon") or "28d"), str(recipe.get("month_horizon") or "7d"), str(recipe.get("tape_horizon") or "7d")
    window = int(recipe.get("tape_window_days") or 20)
    flist = ", ".join(_lit(f) for f in factors)
    con = duckdb.connect(str(lab_db), read_only=True)
    try:
        out: dict[str, dict[str, Any]] = {f: {} for f in factors}
        for f, verdict, ic, t, up, down in con.execute(
                f"SELECT feature, verdict, ic_mean, ic_t_indep, ic_up_mkt, ic_down_mkt FROM factor_summary WHERE horizon = ? AND feature IN ({flist})", [ich]).fetchall():
            out[f].update(ic_28d=_r(ic, 4), verdict_28d=verdict, t_indep_28d=_r(t, 2), ic_up_mkt=_r(up, 4), ic_down_mkt=_r(down, 4))
        months: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for f, month, ic in con.execute(f"SELECT feature, month, ic FROM factor_month WHERE horizon = ? AND feature IN ({flist}) ORDER BY month", [mh]).fetchall():
            months[f].append((str(month), ic))
        for f, seq in months.items():
            out[f].update(month=seq[-1][0], ic_month=_r(seq[-1][1], 4), ic_prev_month=_r(seq[-2][1], 4) if len(seq) > 1 else None)
        daily: dict[str, list[tuple[Any, float]]] = defaultdict(list)
        for f, day, ic in con.execute(f"SELECT feature, as_of, ic FROM factor_days WHERE horizon = ? AND feature IN ({flist}) AND ic IS NOT NULL ORDER BY as_of", [th]).fetchall():
            daily[f].append((day, float(ic)))
    finally:
        con.close()
    tape: list[dict[str, Any]] = []
    for f, seq in daily.items():
        last = [v for _, v in seq[-window:]]
        prev = [v for _, v in seq[-2 * window:-window]]
        out[f].update(ic_tape=_r(sum(last) / len(last), 4) if last else None, ic_tape_prev=_r(sum(prev) / len(prev), 4) if prev else None,
                      tape_pos=_r(sum(1 for v in last if v > 0) / len(last), 3) if last else None,
                      tape_last=seq[-1][0].isoformat() if seq and isinstance(seq[-1][0], date) else None)
        for i, (day, v) in enumerate(seq[-int(recipe.get("regime_days") or 90):]):
            j = len(seq) - min(len(seq), int(recipe.get("regime_days") or 90)) + i
            win = [x for _, x in seq[max(0, j - window + 1): j + 1]]
            tape.append({"factor": f, "as_of": day.isoformat() if isinstance(day, date) else day, "ic": _r(v, 4),
                         "ic_trail": _r(sum(win) / len(win), 4) if win else None})
    return out, tape


# --- build -------------------------------------------------------------------------------------------------


def build_pathway(pack_dir: str | Path | None = None, *, recipe: Mapping[str, Any] | None = None, ids: Sequence[str] = (),
                  store: str | Path | None = None) -> dict[str, Any]:
    import duckdb

    from generic_utils.earnings_week import _resolve, attach_joins, load_join
    from generic_utils.symbol_identity import ticker_map

    recipe = dict(recipe or load_recipe())
    store_recipe = load_store_recipe()
    path = Path(store or store_recipe.get("path") or "")
    path = path if path.is_absolute() else REPO_ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run tv_scan_cli.py panel-store first")
    pack = Path(pack_dir) if pack_dir else None
    joins = list(recipe.get("joins") or [])
    book_spec = next((j for j in joins if j.get("name") == "book"), None)
    book = load_join(book_spec, pack=pack)[0] if (book_spec and pack) else {}
    con = duckdb.connect(str(path), read_only=True)
    try:
        symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM features WHERE as_of = (SELECT max(as_of) FROM features)").fetchall()]
        by_ticker = ticker_map(symbols)
        named = [i if ":" in i else by_ticker.get(i.upper(), i.upper()) for i in ids if i]
        rows, day = newest_rows(con, recipe, sorted(set(book) | set(named)))
        regime = regime_rows(con, recipe)
    finally:
        con.close()
    lab, tape = lab_reads(_resolve("{PACK}/signal_lab/signal_lab.duckdb", pack) if pack else None, recipe)

    factors = [f for f in recipe.get("factors") or [] if rows and f in rows[0]]
    for f in factors:
        for rec, p in zip(rows, percentiles([r.get(f) if r.get("on_floor") else None for r in rows])):
            rec[f"pctl_{f}"] = _r(p, 4)
    # Book lines under the floor take their place in the floor's distribution.
    for f in factors:
        floor_vals = sorted(float(r[f]) for r in rows if r.get("on_floor") and _f(r.get(f)) is not None)
        for rec in rows:
            if rec.get(f"pctl_{f}") is None and _f(rec.get(f)) is not None and floor_vals:
                below = sum(1 for v in floor_vals if v < float(rec[f]))
                rec[f"pctl_{f}"] = _r(below / max(len(floor_vals) - 1, 1), 4)

    floor_rows = [r for r in rows if r.get("on_floor")]
    min_n = int(recipe.get("industry_min_n") or 5)
    mkt = {h: median([float(r[h]) for r in floor_rows if _f(r.get(h)) is not None]) for h in recipe.get("returns") or [] if floor_rows}
    ind_vals: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in floor_rows:
        for h in recipe.get("returns") or []:
            if _f(r.get(h)) is not None:
                ind_vals[(str(r.get("ind")), h)].append(float(r[h]))
    ind_med = {k: median(v) for k, v in ind_vals.items() if len(v) >= min_n}

    by_sym = {r["symbol"]: r for r in rows}
    book_rows = []
    for sym, b in book.items():
        rec = dict(by_sym.get(sym) or {"symbol": sym})
        rec["in_store"] = sym in by_sym
        rec["book_usd"] = _f(b.get("book_usd"))
        book_rows.append(rec)
    store_book = [r for r in book_rows if r["in_store"] and (r.get("book_usd") or 0) > 0]
    book_total = sum(r["book_usd"] or 0 for r in book_rows)
    move = [f for f in recipe.get("move_factors") or [] if f in factors]
    pairs = {f: [(r["book_usd"], float(r[f"pctl_{f}"])) for r in store_book if r.get(f"pctl_{f}") is not None] for f in factors}
    book_pctl = {f: weighted_mean(pairs[f]) for f in factors}
    store_usd = sum(r["book_usd"] for r in store_book)

    exposure = []
    for f in factors:
        rec = {"factor": f, "family": feature_family(store_recipe).get(f), "label": feature_labels(store_recipe).get(f),
               "book_pctl": _r(book_pctl[f], 3), "gap": _r(book_pctl[f] - 0.5, 3) if book_pctl[f] is not None else None,
               "coverage_pct": _r(100.0 * sum(a for a, _ in pairs[f]) / store_usd, 1) if store_usd else None}
        rec.update(lab.get(f) or {})
        for key, ic in (("tilt_month", rec.get("ic_month")), ("tilt_tape", rec.get("ic_tape"))):
            rec[key] = _r(rec["gap"] * ic, 4) if rec["gap"] is not None and ic is not None else None
        exposure.append(rec)

    trim = float(recipe.get("trim_frac") or 0.33)
    starter = float(recipe.get("starter_usd") or 5000)
    lines = []
    for rec in book_rows:
        out = {"symbol": rec["symbol"], "ind": rec.get("ind"), "book_usd": rec.get("book_usd"), "in_store": rec["in_store"]}
        for h in recipe.get("returns") or []:
            ind = ind_med.get((str(rec.get("ind")), h))
            out[h] = _r(rec.get(h), 2)
            out[f"mkt_{h}"], out[f"ind_{h}"] = _r(mkt.get(h), 2), _r(ind, 2)
            for k, v in split_return(rec.get(h), mkt.get(h), ind).items():
                out[f"{k}_{h}"] = v
        for f in move:
            p = _f(rec.get(f"pctl_{f}"))
            out[f"pctl_{f}"] = _r(p, 3)
            after = exposure_after(pairs[f], -trim * (rec.get("book_usd") or 0), p) if rec["in_store"] and p is not None else None
            out[f"d_{f}_trim"] = _r(after - book_pctl[f], 4) if after is not None and book_pctl[f] is not None else None
        lines.append(out)

    attribution = []
    for h in recipe.get("returns") or []:
        rec: dict[str, Any] = {"horizon": h}
        for part in ("rel", "ind_part", "stock_part"):
            got = [(ln["book_usd"], float(ln[f"{part}_{h}"])) for ln in lines if ln["in_store"] and ln.get("book_usd") and _f(ln.get(f"{part}_{h}")) is not None]
            rec[f"book_{part}"] = _r(weighted_mean(got), 2)
            rec[f"{part}_usd"] = _r(sum(a * b / 100.0 for a, b in got), 0)
        rec["mkt"] = _r(mkt.get(h), 2)
        rec["coverage_pct"] = _r(100.0 * store_usd / book_total, 1) if book_total else None
        attribution.append(rec)

    in_book = set(book)
    named_set = set(named)
    candidates = []
    for rec in rows:
        if rec["symbol"] in in_book or not (rec.get("on_floor") or rec["symbol"] in named_set):
            continue
        out = {"symbol": rec["symbol"], "ind": rec.get("ind"), "mcap": rec.get("mcap"), "named": rec["symbol"] in named_set}
        for f in move:
            p = _f(rec.get(f"pctl_{f}"))
            out[f] = _r(rec.get(f), 3)
            out[f"pctl_{f}"] = _r(p, 3)
            after = exposure_after(pairs[f], starter, p)
            out[f"d_{f}_add"] = _r(after - book_pctl[f], 4) if after is not None and book_pctl[f] is not None else None
        for h in recipe.get("returns") or []:
            out[h] = _r(rec.get(h), 2)
        candidates.append(out)

    used = attach_joins(lines, [j for j in joins if j.get("name") != "book"], pack=pack) if pack else {}
    if pack:
        attach_joins(candidates, [j for j in joins if j.get("name") != "book"], pack=pack)
        ind_spec = recipe.get("industry_join")
        if ind_spec:
            ind_index = load_join(ind_spec, pack=pack)[0]
            for rec in candidates + lines:
                for k, v in (ind_index.get(str(rec.get("ind"))) or {}).items():
                    rec.setdefault(k, v)
    lines.sort(key=lambda r: -(r.get("book_usd") or 0))
    candidates.sort(key=lambda r: r["symbol"])
    return {
        "exposure": exposure, "lines": lines, "attribution": attribution, "regime": regime, "factor_tape": tape, "candidates": candidates,
        "spec": {"pack": pack.as_posix() if pack else None, "store": path.as_posix(), "day": day, "n_floor": len(floor_rows),
                 "book_usd": _r(book_total, 0), "store_usd": _r(store_usd, 0), "n_book": len(book_rows), "n_candidates": len(candidates),
                 "named_ids": named, "factors": factors, "move_factors": move, "starter_usd": starter, "trim_frac": trim,
                 "ic_horizon": recipe.get("ic_horizon"), "month_horizon": recipe.get("month_horizon"), "tape_horizon": recipe.get("tape_horizon"),
                 "tape_window_days": recipe.get("tape_window_days"), "lab": bool(lab), "joins": used},
    }


def write_pathway(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"Store day {spec['day']}, {spec['n_floor']} floor names. Book ${spec['book_usd']}, of which ${spec['store_usd']} is in the US store (the rest has no percentile).",
        "exposure.book_pctl: NAV-weighted cross-sectional percentile of the Book's in-store lines (0.5 = the median name); gap = book_pctl - 0.5.",
        f"IC columns are the signal lab's: ic_28d / verdict_28d / t_indep_28d at {spec['ic_horizon']}; ic_month / ic_prev_month at {spec['month_horizon']} "
        f"by calendar month; ic_tape = mean daily IC over the last {spec['tape_window_days']} labelled days at {spec['tape_horizon']} (labels lag by the horizon).",
        "tilt_month / tilt_tape = gap x that IC: + = the tilt was paid, - = it cost. One factor at a time; never summed.",
        "lines: rel = return minus the floor median; ind_part = industry median minus the floor median; stock_part = return minus the industry median "
        "(industries with fewer names than industry_min_n have no split).",
        f"d_<factor>_trim / d_<factor>_add: the Book's book_pctl change if that line were trimmed by {spec['trim_frac']} / a ${spec['starter_usd']:.0f} line were added.",
        "attribution: NAV-weighted parts at today's weights (an approximation of the path); *_usd = today's dollars x the part.",
        "No ranking, no composite, no course.",
    ]
    return write_run_export(
        out_dir, tool="generic_utils.pathway",
        tables={k: payload.get(k) or [] for k in ("exposure", "lines", "attribution", "regime", "factor_tape", "candidates")},
        sources={"pack": spec["pack"], "store": spec["store"]}, notes=notes, extra={"spec": spec}, duckdb_name="pathway.duckdb")


def pathway_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_pathway(build_pathway(pack), pack / "pathway"))


if __name__ == "__main__":
    pathway_local_main()
