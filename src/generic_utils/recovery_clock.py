"""Recovery clock: how long a drawdown state took to climb back, and how far it fell first.

Backward, on the panel store (split-guarded closes, the deterioration study floor and states): every name on every
``start_every_files``-th store day is a start. From its close the path forward gives, per start:

- the first day the close reached each ``rises_pct`` rise, half the gap to the 52-week high (``half``) and the high
  itself (``high``), or the last day seen when it never did (censored: the store ends, a split cuts the path);
- per ``windows_days`` window (complete windows only): the close at the window (``fwd``), its excess over that start
  day's median start (``fx``), the lowest close inside the window (``floor``) and the days to it (``trough``).

Table ``cells`` (per recipe study and cell): starts, names, days, the window reads (median / mean / quantiles of the
floor, odds of a further fall past each ``floor_marks_pct``); table ``curves`` (cell x target x checkpoint): the
Kaplan-Meier odds the target was reached by the checkpoint (censored starts count while they are seen), the starts
whose status is known there (``n_obs``) and the median days to the target. A checkpoint with fewer than
``min_obs_rows`` known starts or ``min_obs_names`` names stays empty: the store is young, so long checkpoints rest on
the first months only.

Live (table ``lines``, long table ``reach``): every Book line (PACK/recovery_ladder ``lines``) and named id in its
store state today (``proxies`` lends a US listing's state to a foreign line), the rise it needs to each level
(cost, cost +10%, the 50-day, half-way, the street price, the high), the odds its cells reached that rise by each
checkpoint (linear between the two nearest rises; empty past the largest) and the median days, the cell's floor
in price and dollars, and its window returns.

Starts overlap (a name every week), so the odds are frequencies of a young, mostly rising store, not forecasts.
No ranking, no composite, no course: rises, checkpoints, windows and floors are recipe knobs
(config/generic_utils/recovery_clock.json); hold / cut / add is the judgment's (deterioration skill).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.panel_store import REPO_ROOT, load_store_recipe
from generic_utils.ranking import to_float
from generic_utils.series import qident

_f = to_float
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "recovery_clock.json"
SEP = " | "


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: Any, digits: int = 2) -> float | None:
    v = _f(value)
    return None if v is None else round(v, digits)


def _lit(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _quantile(values: Sequence[float], q: float) -> float | None:
    vals = sorted(values)
    if not vals:
        return None
    pos = (len(vals) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


# --- survival ------------------------------------------------------------------------------------------------


def km_reach(obs: Sequence[tuple[float, bool, str, float]], checkpoints: Sequence[int], *, min_rows: int, min_names: int
             ) -> dict[int, dict[str, Any]]:
    """Kaplan-Meier odds of reaching the target by each checkpoint.

    ``obs``: (days, reached, symbol, seen): days to the target when reached, else days seen; ``seen`` = the days
    the start's path was observed. A checkpoint reads ``p`` only when at least ``min_rows`` starts (``min_names``
    names) were observed through it (``seen`` >= the checkpoint), so a flat tail past the store's reach is not read
    as an answer. ``med_days``: the first event day where the odds pass one half.
    """
    events = sorted(obs, key=lambda o: o[0])
    n_at_risk = len(events)
    surv = 1.0
    curve: list[tuple[float, float]] = []
    i = 0
    med = None
    while i < len(events):
        t = events[i][0]
        d = c = 0
        while i < len(events) and events[i][0] == t:
            if events[i][1]:
                d += 1
            else:
                c += 1
            i += 1
        if d and n_at_risk:
            surv *= 1.0 - d / n_at_risk
            curve.append((t, surv))
            if med is None and surv <= 0.5:
                med = t
        n_at_risk -= d + c
    out: dict[int, dict[str, Any]] = {}
    for cp in checkpoints:
        known = [o for o in events if o[3] >= cp]
        names = {o[2] for o in known}
        s = 1.0
        for t, v in curve:
            if t > cp:
                break
            s = v
        ok = len(known) >= min_rows and len(names) >= min_names
        out[cp] = {"p": round(1.0 - s, 4) if ok else None, "n_obs": len(known), "n_names": len(names)}
    return {"cp": out, "med_days": med}


def interp_reach(need: float | None, rises: Sequence[float], values: Mapping[float, float | None]) -> float | None:
    """Odds at ``need`` between the two nearest grid rises (1 at or under 0; empty past the largest rise)."""
    if need is None:
        return None
    if need <= 0:
        return 1.0
    grid = sorted(float(r) for r in rises)
    if need > grid[-1]:
        return None
    lo_r, lo_v = 0.0, 1.0
    for r in grid:
        v = values.get(r)
        if need <= r:
            if v is None or lo_v is None:
                return None
            return round(lo_v + (v - lo_v) * (need - lo_r) / (r - lo_r), 4)
        lo_r, lo_v = r, v
    return None


def interp_days(need: float | None, rises: Sequence[float], meds: Mapping[float, float | None]) -> float | None:
    """Median days at ``need`` between the two nearest grid rises (0 at or under 0; empty when either side is)."""
    if need is None:
        return None
    if need <= 0:
        return 0.0
    grid = sorted(float(r) for r in rises)
    if need > grid[-1]:
        return None
    lo_r, lo_v = 0.0, 0.0
    for r in grid:
        v = meds.get(r)
        if need <= r:
            if v is None or lo_v is None:
                return None
            return round(lo_v + (v - lo_v) * (need - lo_r) / (r - lo_r), 1)
        lo_r, lo_v = r, v
    return None


# --- backward ------------------------------------------------------------------------------------------------


def start_paths(con: Any, recipe: Mapping[str, Any], keys: Sequence[str]) -> list[dict[str, Any]]:
    """One row per sampled start: state keys, days to each target (or None) and days seen, window reads."""
    every = max(int(recipe.get("start_every_files") or 1), 1)
    rises = [float(r) for r in recipe.get("rises_pct") or []]
    wins = [int(w) for w in recipe.get("windows_days") or []]
    tol = int(recipe.get("window_tolerance_days") or 4)
    con.execute("CREATE OR REPLACE TEMP TABLE rc_px AS SELECT p.symbol, p.as_of, p.close, s.split_seq FROM panel p "
                "JOIN series s USING (symbol, as_of) WHERE p.close > 0")
    days = [r[0] for r in con.execute("SELECT DISTINCT as_of FROM det_state ORDER BY 1").fetchall()]
    picked = [d for i, d in enumerate(days) if i % every == 0]
    day_list = ", ".join(f"DATE '{d.isoformat()}'" for d in picked) or "NULL"
    key_sel = ", ".join(f"d.{qident(k)}" for k in keys)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE rc_start AS
        SELECT d.as_of, d.symbol, {key_sel}, d.half_need, x.close, x.split_seq
        FROM det_state d JOIN rc_px x USING (symbol, as_of) WHERE d.as_of IN ({day_list})""")
    aggs = ["max(b.as_of) - s.as_of AS seen"]
    for r in rises:
        aggs.append(f"min(b.as_of) FILTER (WHERE b.close >= s.close * {1 + r / 100.0!r}) - s.as_of AS {qident('t_' + f'{r:g}')}")
    aggs.append("min(b.as_of) FILTER (WHERE s.half_need IS NOT NULL AND b.close >= s.close * (1 + s.half_need / 100.0)) - s.as_of AS t_half")
    aggs.append("min(b.as_of) FILTER (WHERE s.half_need IS NOT NULL AND b.close >= s.close * (1 + 2 * s.half_need / 100.0)) - s.as_of AS t_high")
    for w in wins:
        inside = f"b.as_of - s.as_of <= {w}"
        aggs.append(f"(arg_max(b.close, b.as_of) FILTER (WHERE {inside}) / s.close - 1) * 100 AS {qident(f'fwd_{w}')}")
        aggs.append(f"(min(b.close) FILTER (WHERE {inside}) / s.close - 1) * 100 AS {qident(f'floor_{w}')}")
        aggs.append(f"arg_min(b.as_of, b.close) FILTER (WHERE {inside}) - s.as_of AS {qident(f'trough_{w}')}")
    outer = ", ".join(f"s.{qident(k)}" for k in keys)
    cur = con.execute(f"""
        SELECT s.as_of, s.symbol, {outer}, {', '.join(aggs)}
        FROM rc_start s JOIN rc_px b ON b.symbol = s.symbol AND b.as_of > s.as_of AND b.split_seq IS NOT DISTINCT FROM s.split_seq
        GROUP BY s.as_of, s.symbol, s.close, s.half_need, {outer}""")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, v)) for v in cur.fetchall()]
    for rec in rows:
        seen = rec["seen"]
        for w in wins:
            if seen is None or seen < w - tol:
                rec[f"fwd_{w}"] = rec[f"floor_{w}"] = rec[f"trough_{w}"] = None
    # excess over the start day's median start, winsorized on the whole sample
    p = float(recipe.get("winsor_pct", 1.0)) / 100.0
    for w in wins:
        vals = [rec[f"fwd_{w}"] for rec in rows if rec[f"fwd_{w}"] is not None]
        lo, hi = (_quantile(vals, p), _quantile(vals, 1 - p)) if vals else (None, None)
        by_day: dict[Any, list[float]] = defaultdict(list)
        for rec in rows:
            v = rec[f"fwd_{w}"]
            if v is not None:
                rec[f"fwd_{w}"] = v = min(max(v, lo), hi)
                by_day[rec["as_of"]].append(v)
        med = {d: _quantile(v, 0.5) for d, v in by_day.items()}
        for rec in rows:
            v = rec[f"fwd_{w}"]
            rec[f"fx_{w}"] = None if v is None else v - med[rec["as_of"]]
    return rows


def _targets(recipe: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(target label, start column) for each rise, half and high."""
    return [(f"rise {float(r):g}", f"t_{float(r):g}") for r in recipe.get("rises_pct") or []] + [("half", "t_half"), ("high", "t_high")]


def cell_rows(rows: Sequence[Mapping[str, Any]], study: Mapping[str, Any], recipe: Mapping[str, Any]
              ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    keys = [str(k) for k in study["keys"]]
    wins = [int(w) for w in recipe.get("windows_days") or []]
    cps = [int(c) for c in recipe.get("checkpoints_days") or []]
    marks = [float(m) for m in recipe.get("floor_marks_pct") or []]
    min_rows, min_names = int(recipe.get("min_obs_rows") or 1), int(recipe.get("min_obs_names") or 1)
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for rec in rows:
        if all(rec.get(k) is not None for k in keys):
            groups[SEP.join(str(rec[k]) for k in keys)].append(rec)
    cells, curves = [], []
    for label in sorted(groups):
        grp = groups[label]
        item: dict[str, Any] = {"study": study["study"], "keys": " x ".join(keys), "cell": label, "n": len(grp),
                                "n_names": len({g["symbol"] for g in grp}), "n_days": len({g["as_of"] for g in grp}),
                                "first_start": min(g["as_of"] for g in grp).isoformat(), "last_start": max(g["as_of"] for g in grp).isoformat()}
        for w in wins:
            fwd = [g[f"fwd_{w}"] for g in grp if g[f"fwd_{w}"] is not None]
            fx = [g[f"fx_{w}"] for g in grp if g[f"fx_{w}"] is not None]
            fl = [g[f"floor_{w}"] for g in grp if g[f"floor_{w}"] is not None]
            tr = [g[f"trough_{w}"] for g in grp if g[f"trough_{w}"] is not None]
            item[f"n_{w}"] = len(fwd)
            item[f"names_{w}"] = len({g["symbol"] for g in grp if g[f"fwd_{w}"] is not None})
            item[f"fwd_{w}_med"] = _r(_quantile(fwd, 0.5))
            item[f"fwd_{w}_mean"] = _r(sum(fwd) / len(fwd)) if fwd else None
            item[f"fwd_{w}_q10"] = _r(_quantile(fwd, 0.1))
            item[f"fwd_{w}_q90"] = _r(_quantile(fwd, 0.9))
            item[f"fx_{w}_mean"] = _r(sum(fx) / len(fx)) if fx else None
            item[f"fx_{w}_hit"] = _r(sum(1 for v in fx if v > 0) / len(fx), 3) if fx else None
            item[f"floor_{w}_med"] = _r(_quantile(fl, 0.5))
            item[f"floor_{w}_q25"] = _r(_quantile(fl, 0.25))
            item[f"floor_{w}_q10"] = _r(_quantile(fl, 0.1))
            for m in marks:
                item[f"p_floor{abs(m):g}_{w}"] = _r(sum(1 for v in fl if v <= m) / len(fl), 3) if fl else None
            item[f"trough_{w}_med"] = _r(_quantile(tr, 0.5), 0)
        for target, col in _targets(recipe):
            obs = []
            for g in grp:
                t, seen = g.get(col), g.get("seen")
                if seen is None:
                    continue
                obs.append((float(t), True, g["symbol"], float(seen)) if t is not None else (float(seen), False, g["symbol"], float(seen)))
            if not obs:
                continue
            km = km_reach(obs, cps, min_rows=min_rows, min_names=min_names)
            for cp in cps:
                curves.append({"study": study["study"], "cell": label, "target": target, "days": cp, "p_reach": km["cp"][cp]["p"],
                               "n_obs": km["cp"][cp]["n_obs"], "n_names": km["cp"][cp]["n_names"], "med_days": km["med_days"]})
            if target in ("half", "high"):
                for cp in cps:
                    item[f"p_{target}_{cp}"] = km["cp"][cp]["p"]
                item[f"med_days_{target}"] = km["med_days"]
        cells.append(item)
    return cells, curves


# --- live ----------------------------------------------------------------------------------------------------


def _pct(to: Any, frm: Any) -> float | None:
    a, b = _f(to), _f(frm)
    return None if a is None or not b else round((a / b - 1.0) * 100.0, 2)


def _ladder_lines(pack: Path) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_csv

    path = pack / "recovery_ladder" / "lines.csv"
    return [dict(r) for r in rows_from_csv(path)] if path.exists() else []


def pack_states(pack: Path) -> dict[str, dict[str, Any]]:
    """Live state per Book / named id from PACK/deterioration ``names`` (it carries Book lines under the study floor)."""
    from generic_utils.scan_sources import rows_from_csv

    path = pack / "deterioration" / "names.csv"
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for r in rows_from_csv(path):
        if str(r.get("in_book")).lower() == "true" or str(r.get("named")).lower() == "true":
            hp = _f(r.get("hi_prox"))
            out[str(r["symbol"])] = {"dd": r.get("dd") or None, "det": r.get("det") or None, "n_det": r.get("n_det") or None,
                                     "run_12m": r.get("run_12m") or None, "mkt": r.get("mkt") or None,
                                     "half_need": (100.0 / (100.0 + hp) - 1.0) * 50.0 if hp is not None and hp > -100 else None,
                                     "st_close": _f(r.get("close")), "st_sma50": _f(r.get("sma50")), "st_hi52": _f(r.get("hi52"))}
    return out


def pack_sma50(pack: Path) -> dict[str, float]:
    import duckdb

    path = pack / "briefing_pack.duckdb"
    if not path.exists():
        return {}
    con = duckdb.connect(str(path), read_only=True)
    try:
        return {str(s): float(v) for s, v in con.execute("SELECT symbol, sma50 FROM book WHERE sma50 IS NOT NULL").fetchall()}
    except Exception:
        return {}
    finally:
        con.close()


def live_states(con: Any, symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    day = con.execute("SELECT max(as_of) FROM det_state").fetchone()[0]
    cur = con.execute(f"""SELECT d.*, p.close AS st_close, p.sma50 AS st_sma50, p.hi52 AS st_hi52
        FROM det_state d JOIN panel p USING (symbol, as_of)
        WHERE d.as_of = ? AND d.symbol IN ({', '.join(_lit(s) for s in symbols)})""", [day])
    names = [d[0] for d in cur.description]
    return {r[names.index("symbol")]: dict(zip(names, r)) for r in cur.fetchall()}


def _needs(line: Mapping[str, Any], state: Mapping[str, Any] | None) -> dict[str, float | None]:
    close = _f(line.get("close"))
    half = _f(state.get("half_need")) if state else None
    proxied = bool(line.get("proxy"))
    out = {
        "cost": _f(line.get("be_from_close_pct")),
        "gain10": _f(line.get("gain10_from_close_pct")),
        "sma50": _pct(line.get("sma50"), close),
        "half": _pct(line.get("half_px"), close),
        "street": _pct(line.get("street_px"), close),
        "high": _pct(line.get("hi52"), close),
    }
    if state and (proxied or out["sma50"] is None):
        out["sma50"] = out["sma50"] if out["sma50"] is not None else _pct(state.get("st_sma50"), state.get("st_close"))
        out["half"] = out["half"] if out["half"] is not None else _r(half)
        out["high"] = out["high"] if out["high"] is not None else _r(2 * half) if half is not None else None
    return out


def build_recovery_clock(pack_dir: str | Path, *, recipe: Mapping[str, Any] | None = None, ids: Sequence[str] = (),
                         store: str | Path | None = None) -> dict[str, Any]:
    import duckdb

    from generic_utils.deterioration import _resolve_ids, build_state_frame
    from generic_utils.deterioration import load_recipe as det_recipe

    recipe = dict(recipe or load_recipe())
    det = det_recipe()
    store_recipe = load_store_recipe()
    path = Path(store or store_recipe.get("path") or "")
    path = path if path.is_absolute() else REPO_ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run tv_scan_cli.py panel-store first")
    pack = Path(pack_dir)
    studies = list(recipe.get("studies") or [])
    keys = sorted({str(k) for s in studies for k in s["keys"]})
    rises = [float(r) for r in recipe.get("rises_pct") or []]
    cps = [int(c) for c in recipe.get("line_checkpoints_days") or recipe.get("checkpoints_days") or []]
    proxies = {str(k): str(v) for k, v in (recipe.get("proxies") or {}).items()}
    lines = _ladder_lines(pack)
    con = duckdb.connect(str(path), read_only=True)
    try:
        frame = build_state_frame(con, det, store_recipe)
        symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM det_state WHERE as_of = (SELECT max(as_of) FROM det_state)").fetchall()]
        named = [s for s in _resolve_ids(list(ids), symbols) if s not in {ln["symbol"] for ln in lines}]
        starts = start_paths(con, recipe, keys)
        lookups = [proxies.get(ln["symbol"], ln["symbol"]) for ln in lines] + named
        states = live_states(con, lookups)
    finally:
        con.close()
    for sym, st in pack_states(pack).items():
        states.setdefault(sym, st)
    sma = pack_sma50(pack)
    for ln in lines:
        if not _f(ln.get("sma50")) and ln["symbol"] in sma:
            ln["sma50"] = sma[ln["symbol"]]
    cells: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    for st in studies:
        c, k = cell_rows(starts, st, recipe)
        cells += c
        curves += k
    curve_ix: dict[tuple[str, str, str], dict[int, Mapping[str, Any]]] = defaultdict(dict)
    for cv in curves:
        curve_ix[(cv["study"], cv["cell"], cv["target"])][cv["days"]] = cv
    cell_ix = {(c["study"], c["cell"]): c for c in cells}
    for nm in named:
        st = states.get(nm) or {}
        close = _f(st.get("st_close"))
        lines.append({"symbol": nm, "close": close, "sma50": st.get("st_sma50"), "hi52": st.get("st_hi52"), "named": True})
    live_specs = list(recipe.get("live_cells") or [])
    order = list(recipe.get("primary_order") or [c["col"] for c in live_specs])
    out_lines, reach = [], []
    for ln in lines:
        sym = str(ln["symbol"])
        proxy = proxies.get(sym)
        ln["proxy"] = proxy
        st = states.get(proxy or sym)
        needs = _needs(ln, st)
        row: dict[str, Any] = {"symbol": sym, "proxy": proxy, "in_book": not ln.get("named"), "value_usd": _f(ln.get("value_usd")),
                               "pnl_usd": _f(ln.get("pnl_usd")), "pnl_pct": _f(ln.get("pnl_pct")), "close": _f(ln.get("close")),
                               "dd": st.get("dd") if st else None, "det": st.get("det") if st else None,
                               "n_det": st.get("n_det") if st else None, "run_12m": st.get("run_12m") if st else None,
                               "mkt": st.get("mkt") if st else None}
        for k, v in needs.items():
            row[f"need_{k}_pct"] = _r(v)
        cell_of: dict[str, str | None] = {}
        for spec in live_specs:
            study = next((s for s in studies if s["study"] == spec["study"]), None)
            label = SEP.join(str(st.get(k)) for k in study["keys"]) if (st and study and all(st.get(k) is not None for k in study["keys"])) else None
            cell_of[spec["col"]] = label if label and (spec["study"], label) in cell_ix else None
            row[f"{spec['col']}_cell"] = cell_of[spec["col"]]
            for need, pct in needs.items():
                if label is None or (spec["study"], label) not in cell_ix:
                    continue
                for cp in cps:
                    vals = {r: (curve_ix[(spec["study"], label, f"rise {r:g}")].get(cp) or {}).get("p_reach") for r in rises}
                    meds = {r: (curve_ix[(spec["study"], label, f"rise {r:g}")].get(cp) or {}).get("med_days") for r in rises}
                    nobs = {r: (curve_ix[(spec["study"], label, f"rise {r:g}")].get(cp) or {}).get("n_obs") for r in rises}
                    near = next((r for r in sorted(rises) if pct is not None and pct <= r), None)
                    reach.append({"symbol": sym, "col": spec["col"], "study": spec["study"], "cell": label, "need": need,
                                  "need_pct": _r(pct), "days": cp, "p_reach": interp_reach(pct, rises, vals),
                                  "n_obs": nobs.get(near) if near is not None else None,
                                  "med_days": interp_days(pct, rises, meds)})
        # primary cell: the first in order with a reading at the longest line checkpoint for the cost need
        prim = None
        for col in order:
            label = cell_of.get(col)
            spec = next((s for s in live_specs if s["col"] == col), None)
            if label and spec:
                prim = (col, spec["study"], label)
                break
        row["primary"] = prim[0] if prim else None
        if prim:
            col, study, label = prim
            c = cell_ix[(study, label)]
            for need in recipe.get("needs") or []:
                for cp in cps:
                    hit = next((r for r in reach if r["symbol"] == sym and r["col"] == col and r["need"] == need and r["days"] == cp), None)
                    row[f"p_{need}_{cp}"] = hit["p_reach"] if hit else None
                hit = next((r for r in reach if r["symbol"] == sym and r["col"] == col and r["need"] == need), None)
                row[f"days_{need}"] = hit["med_days"] if hit else None
            close, value = row["close"], row["value_usd"]
            for w in [int(x) for x in recipe.get("windows_days") or []]:
                for q in ("med", "q10"):
                    fl = _f(c.get(f"floor_{w}_{q}"))
                    row[f"floor_{w}_{q}"] = fl
                    row[f"floor_{w}_{q}_px"] = _r(close * (1 + fl / 100.0)) if (fl is not None and close) else None
                    row[f"floor_{w}_{q}_usd"] = _r(value * fl / 100.0, 0) if (fl is not None and value) else None
                row[f"fwd_{w}_med"] = c.get(f"fwd_{w}_med")
                row[f"fx_{w}_mean"] = c.get(f"fx_{w}_mean")
                row[f"n_{w}"] = c.get(f"n_{w}")
        out_lines.append(row)
    spec = {"pack": pack.as_posix(), "store": path.as_posix(), "frame": {k: frame[k] for k in ("rows", "days", "first", "last")},
            "starts": len(starts), "start_every_files": recipe.get("start_every_files"), "rises_pct": rises,
            "checkpoints_days": recipe.get("checkpoints_days"), "windows_days": recipe.get("windows_days"),
            "min_obs_rows": recipe.get("min_obs_rows"), "min_obs_names": recipe.get("min_obs_names"),
            "n_cells": len(cells), "n_lines": len(out_lines), "proxies": proxies, "named_ids": named,
            "missing_state": [r["symbol"] for r in out_lines if r["dd"] is None]}
    return {"spec": spec, "cells": cells, "curves": curves, "lines": out_lines, "reach": reach}


def write_recovery_clock(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"Store frame {spec['frame']['first']} to {spec['frame']['last']}; a start every {spec['start_every_files']} files ({spec['starts']} starts) "
        "in its deterioration state (drawdown bucket, letter count, 12-month run, market month).",
        "cells: per study and cell, per window W days (complete windows only): fwd_W (close at W vs the start, winsorized), fx_W (minus that "
        "start day's median start), floor_W (lowest close inside W vs the start: med / q25 / q10, p_floor<k>_W = odds it fell k% or more), "
        "trough_W_med (days to that low); p_half_<d> / p_high_<d> and med_days_*: Kaplan-Meier odds of regaining half the gap to the 52-week high / the high by day d.",
        f"curves: cell x target (rise k% from the start, half, high) x checkpoint: p_reach (Kaplan-Meier, censored starts count while seen), "
        f"n_obs (starts whose status is known there), med_days. Empty when n_obs < {spec['min_obs_rows']} or names < {spec['min_obs_names']}: long "
        "checkpoints rest on the store's first months only.",
        "lines / reach: each Book line (and named id) in its cells today; need_<level>_pct = the rise from the close to the level (cost = the average "
        "price, gain10 = cost +10%, sma50, half = half-way to the 52-week high, street, high); p_<need>_<d> = the primary cell's odds of that rise by "
        "day d (linear between grid rises), days_<need> = its median days; floor_W_* = the cell's floor read in price and USD on the line.",
        "Starts overlap (a name every week) and the store is young and mostly rising: frequencies, not forecasts. No ranking, no cutoff, no course.",
    ]
    return write_run_export(out_dir, tool="generic_utils.recovery_clock",
                            tables={"lines": payload["lines"], "reach": payload["reach"], "cells": payload["cells"], "curves": payload["curves"]},
                            sources={"pack": spec["pack"], "store": spec["store"]}, notes=notes, extra={"spec": spec},
                            duckdb_name="recovery_clock.duckdb")


def recovery_clock_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_recovery_clock(build_recovery_clock(pack), pack / "recovery_clock"))


if __name__ == "__main__":
    recovery_clock_local_main()
