"""Deterioration: what a drawdown did next, split by what else was deteriorating, and where each name sits now.

Backward (table ``cells``): on the signal lab's study frame (features joined to labels on the study floor,
``fx_<h>`` = the later return minus that day's universe median, winsorized), every (as_of, name) gets a state:

- ``dd``: the drawdown from the 52-week high (``hi_prox``) in the recipe's buckets;
- ``det``: deterioration letters beyond the price (recipe rules, in recipe order, ``-`` = none). Default E next-FY
  EPS estimate cut over 3 months, R next-FY revenue estimate cut over 3 months, C negative FCF margin, S last EPS
  under its estimate. ``n_det`` counts them (``count_cap`` and more share one label). With ``count_needs_all`` both
  are empty unless every letter's field is known (the 3-month revisions start three months into the store, so an
  unknown letter would otherwise read as off). ``det_<K>`` is that letter alone (``K on`` / ``K off``);
- the recipe's splits (1-month direction, 12-month run, market regime at the scan, trend vs the 200-day, street
  target revisions). Trend and targets are splits, not letters: both follow the price.

Each study groups rows by its keys. A cell carries, per horizon, the mean / median / hit / q10 / q90 of ``fx_<h>``,
the raw mean ``fwd_<h>``, ``p_dn`` / ``p_up`` (raw forward return at or under -X% / at or over +X%), ``p_half``
(half the gap to the 52-week high regained by the horizon) and two t-stats of the cell's daily means: ``t_nw``
(Newey-West, lag = the horizon in files) and ``t`` over non-overlapping days (``n_indep`` of them). Rows of one
day are not independent (one sector can fill a cell), so the day, not the row, is the unit of the t.

Live (table ``names``): every name of the newest store day on the study floor, plus the Book and named ids, with
the same state, the cells it sits in now (recipe ``live_cells``), reads beyond price (estimate and target
revisions; margin, growth and FCF margin against the panel row a quarter earlier), the recovery levels (SMA50,
SMA200, half-way to the high, the high, the low) and the joins (Book value, alpha forecast, entry book / safety
band, lenses). A Book line also gets the dollars of its cell: ``carry_<h>_usd`` (value x cell mean excess) and
``tail_<h>_usd`` (value x cell q10).

No ranking, no composite, no course. Bucket edges, letter rules and the cell row floor are recipe knobs
(config/generic_utils/deterioration.json); hold, cut or add is the judgment's (deterioration skill).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.panel_store import REPO_ROOT, horizon_specs, load_store_recipe
from generic_utils.ranking import to_float
from generic_utils.series import qident

_f = to_float
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "deterioration.json"
SEP = " | "


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: Any, digits: int = 3) -> float | None:
    number = _f(value)
    return None if number is None or math.isnan(number) else round(number, digits)


def _lit(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


# --- state (one SQL expression per key, shared by the backward frame and the live rows) ---------------------


_OPS = {"<": "<", "<=": "<=", ">": ">", ">=": ">="}


def bucket_sql(field: str, edges: Sequence[float], labels: Sequence[str] | None = None) -> str:
    """CASE over ascending ``edges``: ``labels[k]`` for edges[k-1] <= x < edges[k]; NULL when the field is NULL."""
    es = sorted(float(e) for e in edges)
    names = list(labels or [])
    if len(names) != len(es) + 1:
        bounds = ["min"] + [f"{e:g}" for e in es] + ["max"]
        names = [f"[{bounds[k]}, {bounds[k + 1]})" for k in range(len(es) + 1)]
    col = qident(field)
    parts = [f"WHEN {col} IS NULL THEN NULL"]
    parts += [f"WHEN {col} < {e!r} THEN {_lit(lab)}" for e, lab in zip(es, names)]
    parts.append(f"ELSE {_lit(names[-1])}")
    return "CASE " + " ".join(parts) + " END"


def letter_condition(spec: Mapping[str, Any]) -> str:
    op = _OPS.get(str(spec.get("op") or "<"))
    if op is None:
        raise ValueError(f"letter {spec.get('key')}: unknown op {spec.get('op')!r}")
    return f"{qident(str(spec['field']))} {op} {float(spec['value'])!r}"


def state_columns(recipe: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(alias, SQL) for dd, det, n_det, det_<K> per letter and each split."""
    dd = dict(recipe.get("drawdown") or {})
    cols = [("dd", bucket_sql(str(dd.get("field") or "hi_prox"), dd.get("edges") or [-50, -35, -20, -10], dd.get("labels")))]
    letters = list(recipe.get("letters") or [])
    if letters:
        flags = " || ".join(f"(CASE WHEN {letter_condition(s)} THEN {_lit(s['key'])} ELSE '' END)" for s in letters)
        count = " + ".join(f"(CASE WHEN {letter_condition(s)} THEN 1 ELSE 0 END)" for s in letters)
        cap = int(recipe.get("count_cap") or len(letters))
        unknown = (" OR ".join(f"{qident(str(s['field']))} IS NULL" for s in letters)
                   if recipe.get("count_needs_all") else "FALSE")
        cols.append(("det", f"CASE WHEN {unknown} THEN NULL WHEN ({flags}) = '' THEN '-' ELSE ({flags}) END"))
        cols.append(("n_det", f"CASE WHEN {unknown} THEN NULL WHEN ({count}) >= {cap} THEN '{cap}+' "
                              f"ELSE CAST(({count}) AS VARCHAR) END"))
        for s in letters:
            k = str(s["key"])
            cols.append((f"det_{k}", f"CASE WHEN {qident(str(s['field']))} IS NULL THEN NULL WHEN {letter_condition(s)} "
                                     f"THEN {_lit(k + ' on')} ELSE {_lit(k + ' off')} END"))
    for name, spec in (recipe.get("splits") or {}).items():
        cols.append((str(name), bucket_sql(str(spec["field"]), spec.get("edges") or [0], spec.get("labels"))))
    return cols


def state_fields(recipe: Mapping[str, Any]) -> list[str]:
    """Feature columns the state keys read (drawdown, letters, splits); ``mkt_regime`` is computed, not a column."""
    out = [str((recipe.get("drawdown") or {}).get("field") or "hi_prox")]
    out += [str(s["field"]) for s in recipe.get("letters") or []]
    out += [str(s["field"]) for s in (recipe.get("splits") or {}).values()]
    return [f for i, f in enumerate(out) if f != "mkt_regime" and f not in out[:i]]


def study_specs(recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The recipe's studies, plus one ``drawdown x <K>`` per letter when ``letter_studies`` is on."""
    out = [dict(s) for s in recipe.get("studies") or []]
    if recipe.get("letter_studies"):
        for s in recipe.get("letters") or []:
            out.append({"study": f"drawdown x {s['key']}", "keys": ["dd", f"det_{s['key']}"]})
    return out


# --- backward cells ----------------------------------------------------------------------------------------


def build_state_frame(con: Any, recipe: Mapping[str, Any], store_recipe: Mapping[str, Any]) -> dict[str, Any]:
    """TEMP table ``det_state``: the lab's study frame with the state keys, ``half_need`` and fx / fwd per horizon."""
    from generic_utils.signal_lab import build_study

    hs = list(recipe.get("horizons") or ["7d", "28d", "56d"])
    info = build_study(con, store_recipe, {"study": recipe.get("study") or {}, "regime_field": recipe.get("regime_field") or "r_1m"}, hs)
    state = ", ".join(f"{sql} AS {qident(alias)}" for alias, sql in state_columns(recipe))
    dd_field = qident(str((recipe.get("drawdown") or {}).get("field") or "hi_prox"))
    half = f"CASE WHEN {dd_field} > -100 THEN (100.0 / (100.0 + {dd_field}) - 1.0) * 50.0 END AS half_need"
    labels = ", ".join(f"{qident('fx_' + h)}, {qident('fwd_' + h)}" for h in hs)
    con.execute(f"CREATE OR REPLACE TEMP TABLE det_state AS SELECT as_of, symbol, ind, {state}, {half}, {labels} FROM study")
    return info


def _daily_t(series: Sequence[tuple[int, float]], files: int) -> tuple[float | None, float | None, int]:
    """(t_nw over every day, t over days whose global index is a multiple of ``files``, how many such days)."""
    from generic_utils.signal_lab import newey_west_t, plain_t

    values = [v for _, v in series]
    indep = [v for i, v in series if i % max(files, 1) == 0]
    return (newey_west_t(values, max(files - 1, 0)) if len(values) >= 3 else None,
            plain_t(indep) if len(indep) >= 3 else None, len(indep))


def study_cells(con: Any, study: Mapping[str, Any], horizons: Sequence[str], files: Mapping[str, int],
                tails: Mapping[str, Any], day_index: Mapping[Any, int], min_rows: int) -> list[dict[str, Any]]:
    keys = [str(k) for k in study["keys"]]
    cell = f" || {_lit(SEP)} || ".join(f"CAST({qident(k)} AS VARCHAR)" for k in keys)
    where = " AND ".join(f"{qident(k)} IS NOT NULL" for k in keys)
    dn, up = float(tails.get("dn_pct", 10)), float(tails.get("up_pct", 10))
    aggs: list[str] = []
    for h in horizons:
        fx, fwd = qident(f"fx_{h}"), qident(f"fwd_{h}")
        aggs += [
            f"count({fx}) AS {qident('n_' + h)}",
            f"avg({fx}) AS {qident('fx_' + h + '_mean')}",
            f"median({fx}) AS {qident('fx_' + h + '_med')}",
            f"avg(CASE WHEN {fx} IS NULL THEN NULL WHEN {fx} > 0 THEN 1.0 ELSE 0.0 END) AS {qident('fx_' + h + '_hit')}",
            f"quantile_cont({fx}, 0.1) AS {qident('fx_' + h + '_q10')}",
            f"quantile_cont({fx}, 0.9) AS {qident('fx_' + h + '_q90')}",
            f"avg({fwd}) AS {qident('fwd_' + h + '_mean')}",
            f"avg(CASE WHEN {fwd} IS NULL THEN NULL WHEN {fwd} <= {-dn!r} THEN 1.0 ELSE 0.0 END) AS {qident('p_dn_' + h)}",
            f"avg(CASE WHEN {fwd} IS NULL THEN NULL WHEN {fwd} >= {up!r} THEN 1.0 ELSE 0.0 END) AS {qident('p_up_' + h)}",
            f"avg(CASE WHEN {fwd} IS NULL OR half_need IS NULL THEN NULL WHEN {fwd} >= half_need THEN 1.0 ELSE 0.0 END) AS {qident('p_half_' + h)}",
        ]
    cur = con.execute(f"SELECT {cell} AS cell, count(*) AS n, count(DISTINCT as_of) AS days, {', '.join(aggs)} "
                      f"FROM det_state WHERE {where} GROUP BY 1 HAVING count(*) >= {int(min_rows)} ORDER BY 1")
    names = [d[0] for d in cur.description]
    rows = {r[0]: dict(zip(names, r)) for r in cur.fetchall()}
    if not rows:
        return []
    means = ", ".join(f"avg({qident('fx_' + h)})" for h in horizons)
    daily: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for values in con.execute(f"SELECT {cell} AS cell, as_of, {means} FROM det_state WHERE {where} GROUP BY 1, 2 ORDER BY 1, 2").fetchall():
        if values[0] in rows:
            daily[values[0]].append(values[1:])
    out = []
    for label, rec in rows.items():
        item: dict[str, Any] = {"study": study["study"], "keys": " x ".join(keys), "cell": label}
        for k, v in rec.items():
            if k != "cell":
                item[k] = v if isinstance(v, int) else _r(v, 4 if k.startswith("p_") or k.endswith("_hit") else 3)
        for j, h in enumerate(horizons):
            series = [(day_index[d[0]], float(d[j + 1])) for d in daily[label] if d[j + 1] is not None and d[0] in day_index]
            t_nw, t_ind, n_ind = _daily_t(series, files.get(h, 1))
            item[f"fx_{h}_t_nw"], item[f"fx_{h}_t"], item[f"fx_{h}_n_indep"] = _r(t_nw, 2), _r(t_ind, 2), n_ind
        out.append(item)
    return out


def backward_cells(con: Any, recipe: Mapping[str, Any], store_recipe: Mapping[str, Any]) -> dict[str, Any]:
    info = build_state_frame(con, recipe, store_recipe)
    hs = list(recipe.get("horizons") or ["7d", "28d", "56d"])
    files = {h["name"]: int(h.get("files") or 1) for h in horizon_specs(store_recipe)}
    days = [r[0] for r in con.execute("SELECT DISTINCT as_of FROM det_state ORDER BY 1").fetchall()]
    day_index = {d: i for i, d in enumerate(days)}
    cells: list[dict[str, Any]] = []
    for study in study_specs(recipe):
        cells += study_cells(con, study, hs, files, recipe.get("tails") or {}, day_index, int(recipe.get("min_cell_rows") or 1))
    return {"cells": cells, "frame": {k: info[k] for k in ("rows", "days", "first", "last")}}


# --- live rows ---------------------------------------------------------------------------------------------


def _resolve_ids(ids: Sequence[str], symbols: Sequence[str]) -> list[str]:
    from generic_utils.symbol_identity import ticker_map

    by_ticker = ticker_map(symbols)
    return [i if ":" in i else by_ticker.get(i.upper(), i.upper()) for i in ids if i]


def live_rows(con: Any, recipe: Mapping[str, Any], *, extra_ids: Sequence[str]) -> dict[str, Any]:
    """The newest store day: floor names plus ``extra_ids``, with state, quarter-ago reads and recovery levels."""
    day = con.execute("SELECT max(as_of) FROM features").fetchone()[0]
    floor = float((recipe.get("study") or {}).get("min_market_cap_usd") or 0)
    regime = str(recipe.get("regime_field") or "r_1m")
    mkt = con.execute(f"SELECT median({qident(regime)}) FROM features WHERE as_of = ? AND mcap >= ?", [day, floor]).fetchone()[0]
    fields = [f for f in list(recipe.get("live_fields") or []) + state_fields(recipe) if f not in ("symbol", "as_of")]
    known = {r[0] for r in con.execute("DESCRIBE features").fetchall()}
    fields = [f for i, f in enumerate(fields) if f in known and f not in fields[:i]]
    ids = sorted({i for i in extra_ids if i})
    id_list = ", ".join(_lit(i) for i in ids) or "''"
    q_days = int(recipe.get("quarter_ago_days") or 91)
    state = ", ".join(f"{sql} AS {qident(alias)}" for alias, sql in state_columns(recipe))
    sel = ", ".join(f"f.{qident(c)}" for c in fields)
    cur = con.execute(f"""
        WITH cur AS (
            SELECT f.symbol, f.as_of, {sel}, p.close, p.hi52, p.lo52, p.sma50, p.sma200, p.change AS day_chg,
                   p.opm AS opm_now, p.rev_yoy AS rev_yoy_now, p.fcf_margin AS fcf_margin_now,
                   CAST(f.as_of - INTERVAL {q_days} DAY AS DATE) AS q_day, {(_r(mkt, 4) if mkt is not None else 'NULL')} AS mkt_regime
            FROM features f JOIN panel p USING (symbol, as_of)
            WHERE f.as_of = ? AND (f.mcap >= {floor!r} OR f.symbol IN ({id_list}))),
        st AS (SELECT cur.*, {state} FROM cur)
        SELECT st.*, q.as_of AS q_ago_on, q.opm AS opm_q_ago, q.rev_yoy AS rev_yoy_q_ago, q.fcf_margin AS fcf_margin_q_ago
        FROM st ASOF LEFT JOIN panel q ON st.symbol = q.symbol AND st.q_day >= q.as_of
        ORDER BY st.symbol""", [day])
    names = [d[0] for d in cur.description]
    rows = []
    letters = list(recipe.get("letters") or [])
    for values in cur.fetchall():
        rec = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in zip(names, values)}
        rec.pop("q_day", None)
        rec["det_seen"] = seen_letters(rec, letters)
        rows.append(derive_live(rec))
    return {"rows": rows, "day": day.isoformat() if day else None, "mkt_regime": _r(mkt, 3), "ids": ids}


def _pct(now: Any, ref: Any) -> float | None:
    a, b = _f(now), _f(ref)
    return None if a is None or b is None or b <= 0 else round((a / b - 1.0) * 100.0, 2)


def seen_letters(rec: Mapping[str, Any], letters: Sequence[Mapping[str, Any]]) -> str:
    """The letters on among the known fields, then ``?`` per letter whose field is empty (``-`` = none on, all known)."""
    on, unknown = "", ""
    for spec in letters:
        value = _f(rec.get(str(spec["field"])))
        if value is None:
            unknown += "?"
            continue
        limit, op = float(spec["value"]), str(spec.get("op") or "<")
        hit = {"<": value < limit, "<=": value <= limit, ">": value > limit, ">=": value >= limit}[op]
        on += str(spec["key"]) if hit else ""
    return (on + unknown) or "-"


def derive_live(rec: dict[str, Any]) -> dict[str, Any]:
    """Quarter-ago changes (pp, both sides from the panel) and the recovery levels with their distance from the close (%)."""
    for field in ("opm", "rev_yoy", "fcf_margin"):
        now_raw = rec.pop(f"{field}_now", None)
        now, ago = _f(now_raw if now_raw is not None else rec.get(field)), _f(rec.get(f"{field}_q_ago"))
        rec[f"{field}_chg_q"] = _r(now - ago, 2) if now is not None and ago is not None else None
    close, hi = _f(rec.get("close")), _f(rec.get("hi52"))
    rec["half_px"] = _r(close + (hi - close) / 2.0, 2) if close is not None and hi is not None and hi > close else None
    for level in ("sma50", "sma200", "half_px", "hi52", "lo52"):
        rec[f"to_{level}_pct"] = _pct(rec.get(level), close)
    for k in ("close", "hi52", "lo52", "sma50", "sma200"):
        rec[k] = _r(rec.get(k), 2)
    return rec


def attach_live_cells(rows: Sequence[dict[str, Any]], cells: Sequence[Mapping[str, Any]], recipe: Mapping[str, Any]) -> None:
    """``<col>_cell`` and ``<col>_<measure>`` per recipe ``live_cells``: the cell the row sits in now, same label as ``cells``."""
    lookup = {(c["study"], c["cell"]): c for c in cells}
    keys = {s["study"]: [str(k) for k in s["keys"]] for s in study_specs(recipe)}
    measures = list(recipe.get("live_measures") or [])
    for spec in recipe.get("live_cells") or []:
        col, study = str(spec["col"]), str(spec["study"])
        parts = keys.get(study) or []
        for rec in rows:
            values = [rec.get(k) for k in parts]
            label = SEP.join(str(v) for v in values) if parts and all(v not in (None, "") for v in values) else None
            rec[f"{col}_cell"] = label
            hit = lookup.get((study, label)) if label else None
            for m in measures:
                rec[f"{col}_{m}"] = hit.get(m) if hit else None


def attach_dollars(rows: Sequence[dict[str, Any]], recipe: Mapping[str, Any]) -> None:
    col = str(recipe.get("dollar_cell") or "cnt")
    for rec in rows:
        value = _f(rec.get("book_usd"))
        for h in recipe.get("dollar_horizons") or ["28d"]:
            mean, q10 = _f(rec.get(f"{col}_fx_{h}_mean")), _f(rec.get(f"{col}_fx_{h}_q10"))
            rec[f"carry_{h}_usd"] = _r(value * mean / 100.0, 0) if value is not None and mean is not None else None
            rec[f"tail_{h}_usd"] = _r(value * q10 / 100.0, 0) if value is not None and q10 is not None else None


# --- build / write -----------------------------------------------------------------------------------------


def build_deterioration(pack_dir: str | Path | None = None, *, recipe: Mapping[str, Any] | None = None,
                        ids: Sequence[str] = (), store: str | Path | None = None) -> dict[str, Any]:
    """Tables ``cells`` (backward), ``names`` (live state, cells, joins, dollars) and ``letters`` (the rules)."""
    import duckdb

    from generic_utils.earnings_week import attach_joins, load_join

    recipe = dict(recipe or load_recipe())
    store_recipe = load_store_recipe()
    path = Path(store or store_recipe.get("path") or "")
    path = path if path.is_absolute() else REPO_ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run tv_scan_cli.py panel-store first")
    pack = Path(pack_dir) if pack_dir else None
    joins = list(recipe.get("joins") or [])
    book_spec = next((j for j in joins if j.get("name") == "book"), None)
    book_ids = sorted(load_join(book_spec, pack=pack)[0]) if (book_spec and pack) else []
    con = duckdb.connect(str(path), read_only=True)
    try:
        symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM features WHERE as_of = (SELECT max(as_of) FROM features)").fetchall()]
        named = _resolve_ids(list(ids), symbols)
        back = backward_cells(con, recipe, store_recipe)
        live = live_rows(con, recipe, extra_ids=named + book_ids)
    finally:
        con.close()
    rows = live["rows"]
    seen = {r["symbol"] for r in rows}
    for sym in book_ids + named:
        if sym not in seen:
            rows.append({"symbol": sym, "as_of": None})
            seen.add(sym)
    book_set, named_set = set(book_ids), set(named)
    for rec in rows:
        rec["in_book"] = rec["symbol"] in book_set
        rec["named"] = rec["symbol"] in named_set
        rec["in_store"] = rec.get("as_of") is not None
    attach_live_cells(rows, back["cells"], recipe)
    used = attach_joins(rows, joins, pack=pack) if pack else {}
    attach_dollars(rows, recipe)
    rows.sort(key=lambda r: (not r["in_book"], not r["named"], r["symbol"]))
    letters = [{"key": s["key"], "field": s["field"], "op": s.get("op", "<"), "value": s["value"], "label": s.get("label")}
               for s in recipe.get("letters") or []]
    return {
        "cells": back["cells"], "names": rows, "letters": letters,
        "spec": {"pack": pack.as_posix() if pack else None, "store": path.as_posix(), "day": live["day"], "mkt_regime": live["mkt_regime"],
                 "frame": back["frame"], "n_cells": len(back["cells"]), "n_names": len(rows), "book_ids": book_ids,
                 "named_ids": named, "missing_ids": [s for s in book_ids + named if not next((r for r in rows if r["symbol"] == s and r["in_store"]), None)],
                 "joins": used, "horizons": recipe.get("horizons"), "live_cells": recipe.get("live_cells"),
                 "dollar_cell": recipe.get("dollar_cell"), "tails": recipe.get("tails"), "min_cell_rows": recipe.get("min_cell_rows")},
    }


def write_deterioration(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    frame = spec["frame"]
    tails = spec.get("tails") or {}
    notes = [
        f"Backward frame: {frame['rows']} rows, {frame['days']} store days {frame['first']}..{frame['last']} (signal_lab.build_study on the recipe floor); "
        "fx_<h> = forward return minus that day's universe median (winsorized), fwd_<h> = raw forward return.",
        f"dd = drawdown from the 52-week high bucket; det = letters on (recipe order, '-' none); n_det = their count; det_<K> = one letter. "
        f"Splits: dir_1m, run_12m, mkt (universe median trailing month at the scan), surprise. A cell needs {spec.get('min_cell_rows')} rows.",
        f"p_dn_<h> / p_up_<h>: share of raw fwd_<h> at or under -{tails.get('dn_pct', 10)}% / at or over +{tails.get('up_pct', 10)}%; "
        "p_half_<h>: share that regained half the gap to the 52-week high by the horizon.",
        "fx_<h>_t is the t of the cell's daily means on non-overlapping days (fx_<h>_n_indep of them); fx_<h>_t_nw uses every day with a "
        "Newey-West lag of the horizon in files. Under 3 independent days the t is empty: 56d is direction only.",
        f"names: store day {spec['day']} on the floor plus the Book and named ids (missing from the store: {', '.join(spec['missing_ids']) or 'none'}). "
        "<col>_* = the cell that row sits in now (live_cells); *_chg_q = now minus the panel row about a quarter earlier (pp); to_<level>_pct = level vs the close.",
        f"carry_<h>_usd = book_usd x the {spec.get('dollar_cell')} cell's mean fx_<h> (what the median-relative drift of that state was worth on the line); "
        "tail_<h>_usd = book_usd x that cell's q10. Not a forecast of this name, not a size.",
        "Alpha columns are PACK/alpha_model/live.csv unchanged (a forecast with its own record and state); entry / bounce / kill from PACK/week_plays.",
        "No ranking, no composite, no course: read one column at a time.",
    ]
    return write_run_export(
        out_dir, tool="generic_utils.deterioration",
        tables={k: payload.get(k) or [] for k in ("names", "cells", "letters")},
        sources={"pack": spec["pack"], "store": spec["store"]}, notes=notes, extra={"spec": spec},
        duckdb_name="deterioration.duckdb")


def deterioration_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_deterioration(build_deterioration(pack), pack / "deterioration"))


if __name__ == "__main__":
    deterioration_local_main()
