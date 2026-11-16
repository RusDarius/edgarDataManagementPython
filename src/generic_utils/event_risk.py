"""Event risk: dated catalysts against each name's crowd, measured on the panel store, with a WARN level per line.

Earnings risk has its own suites (beat bar, print night). This module covers the moves earnings tables miss: a policy
vote, a macro print or a theme repricing that takes a whole crowd down at once (the 4 Nov 2026 Democratic sweep took
the AI power / data-center builders -10% to -20% in a session while solar rose). From the panel store (US floor, close
to close between consecutive day files, split-guarded), the pack's Book and the OUTSIDE calendar:

- ``names``: every floor name, the Book and named ids. Crowd = the k floor names whose steps correlate most with its own
  (recipe ``crowd``); the crowd's cap-weighted step series, its day / 5 / 21-step move, drawdown and z; the line's beta
  and down-beta to it and its mean step on the crowd's tail steps (``capture_pct``); the crowd's worst 1-step and
  multi-step moves and the stress = worst multi-step x down-beta (``stress_5s_pct``; dollars and % of NAV for held
  lines); industry z and beta (without the line); the nearest dated event touching the line, the nearest binary event,
  the last event that moved its crowd; ``er_letters`` and ``warn_level`` from the recipe rules; the evidence row of its
  measured S / B / T state.
- ``book``: the Book's lines (and named ids) from ``names``, with value, weight and the stress in dollars.
- ``sell_book`` / ``buy_book``: candidate lists, not budgets. Sell = WARN_STRONG / WARN names (Book first). Buy = names
  whose crowd an event lifted (U) or that sit on the up side of an upcoming mapped event, not at WARN_STRONG.
- ``events``: calendar rows inside the look-back / look-ahead window, with odds from the OUTSIDE readings.
- ``reactions``: every calendar event inside the store: each industry's step on it, its z and the next 5 steps.
- ``crashes`` / ``follow``: industry crash steps (1-step z <= -crash_z) and what the industry did next vs the median one.
- ``evidence``: the S / B / T states checked point-in-time on the store against the labels' forward returns.

No 0-100, no ranking, no course: letters are measured, the level is a fixed rule over the letters (a review trigger),
OUTSIDE maps only say which dated event touches which line (timing and Grade, never a sort key or a size).
The store holds months, not cycles: read ``n_indep`` before trusting an evidence row.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from generic_utils.downside_risk import _book_lines, _lit, _step_cte
from generic_utils.panel_store import REPO_ROOT, default_store_path, load_store_recipe
from generic_utils.ranking import to_float
from generic_utils.scan_sources import rows_from_csv

_f = to_float
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "event_risk.json"
SBT = ("S", "B", "T")


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: Any, digits: int = 2) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v, digits) if math.isfinite(v) else None


def _path(value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else REPO_ROOT / p


# --- pure helpers (tested) ---------------------------------------------------------------------------------


def compound(steps: Sequence[float]) -> float | None:
    """Compounded % return of % steps; NaN steps count as flat. None when empty."""
    vals = [s for s in steps if s is not None and math.isfinite(s)]
    if not vals:
        return None
    out = 1.0
    for s in vals:
        out *= 1 + s / 100
    return (out - 1) * 100


def z_last(series: Sequence[float], k: int, window: int) -> float | None:
    """series[k] over the standard deviation of the `window` steps before it (at least half present)."""
    if k < 1:
        return None
    hist = np.asarray(series[max(0, k - window):k], dtype=float)
    hist = hist[np.isfinite(hist)]
    cur = series[k]
    if len(hist) < max(5, window // 2) or cur is None or not math.isfinite(cur):
        return None
    sd = float(np.std(hist))
    return cur / sd if sd > 0 else None


def multi_z(series: Sequence[float], k: int, steps: int, window: int) -> float | None:
    """Compounded return of the `steps` steps ending at k, over the 1-step sd of the `window` before them x sqrt(steps)."""
    if k - steps + 1 < 1:
        return None
    seg = list(series[k - steps + 1:k + 1])
    if sum(1 for s in seg if s is not None and math.isfinite(s)) < steps - 1:
        return None
    hist = np.asarray(series[max(0, k - steps + 1 - window):k - steps + 1], dtype=float)
    hist = hist[np.isfinite(hist)]
    if len(hist) < max(5, window // 2):
        return None
    sd = float(np.std(hist))
    move = compound(seg)
    return move / (sd * math.sqrt(steps)) if sd > 0 and move is not None else None


def ols_beta(ys: Sequence[float], xs: Sequence[float], min_n: int) -> float | None:
    y, x = np.asarray(ys, dtype=float), np.asarray(xs, dtype=float)
    m = np.isfinite(y) & np.isfinite(x)
    if m.sum() < min_n:
        return None
    y, x = y[m], x[m]
    var = float(np.var(x))
    return float(np.mean((x - x.mean()) * (y - y.mean())) / var) if var > 0 else None


def letters_of(flags: Mapping[str, bool], order: str) -> str:
    return "".join(ch for ch in order if flags.get(ch)) or "-"


def warn_level(letters: str, levels: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """First level in levels['order'] with an alternative whose letters are all on: (level, the alternative)."""
    on = set(letters.replace("-", ""))
    for level in levels.get("order") or []:
        for alt in levels.get(level) or []:
            if alt and set(alt) <= on:
                return level, alt
    return None, None


def theme_members(theme: Mapping[str, Any]) -> tuple[set[str], set[str], set[str]]:
    """(full ids, bare tickers, industries) of a themes.json entry."""
    ids = [str(i) for i in theme.get("ids") or []]
    return ({i for i in ids if ":" in i}, {i.split(":")[-1].upper() for i in ids}, {str(i) for i in theme.get("industries") or []})


def in_theme(symbol: str, ind: Any, members: tuple[set[str], set[str], set[str]]) -> bool:
    full, bare, inds = members
    return symbol in full or symbol.split(":")[-1].upper() in bare or (ind is not None and str(ind) in inds)


def event_targets(event: Mapping[str, Any], symbol: str, ind: Any, crowd: Sequence[tuple[str, Any, float]],
                  themes: Mapping[str, Any], share_min: float) -> tuple[str | None, str | None]:
    """(side, how) when a calendar row touches the line: its symbols ('either' unless the exposure says), its exposure
    themes (down / up / either) by membership or by crowd cap share. None when it does not touch the line."""
    exposure = event.get("exposure") or {}
    if symbol in [str(s) for s in event.get("symbols") or []]:
        for side in ("down", "either", "up"):
            for name in exposure.get(side) or []:
                if in_theme(symbol, ind, theme_members(themes.get(name) or {})):
                    return side, f"symbol + theme {name}"
        return "either", "symbol"
    total = sum(w for _, _, w in crowd) or 0.0
    for side in ("down", "either", "up"):
        for name in exposure.get(side) or []:
            members = theme_members(themes.get(name) or {})
            if in_theme(symbol, ind, members):
                return side, f"theme {name}"
            if total > 0:
                share = sum(w for s, i, w in crowd if in_theme(s, i, members)) / total
                if share >= share_min:
                    return side, f"crowd {share:.0%} in {name}"
    return None, None


def winsorize_rows(X: np.ndarray, clip_sd: float) -> np.ndarray:
    """Each row clipped at its mean +- clip_sd standard deviations (NaN kept); clip_sd <= 0 returns X."""
    if clip_sd <= 0:
        return X
    import warnings

    with warnings.catch_warnings(), np.errstate(invalid="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)  # an all-NaN row (a name not on the floor then) stays NaN
        mu, sd = np.nanmean(X, axis=1, keepdims=True), np.nanstd(X, axis=1, keepdims=True)
        return np.where(np.isfinite(X), np.clip(X, mu - clip_sd * sd, mu + clip_sd * sd), np.nan)


# --- store reads ------------------------------------------------------------------------------------------


def recent_prints(con: Any, files: Sequence[date]) -> set[tuple[str, date]]:
    """(symbol, react_file) of the store's prints whose reaction landed on one of these files."""
    if not files:
        return set()
    lits = ", ".join(f"DATE '{d.isoformat()}'" for d in files)
    return {(str(s), d) for s, d in con.execute(f"SELECT symbol, react_file FROM prints WHERE react_file IN ({lits})").fetchall()}


def store_det(con: Any, day: date | None) -> dict[str, str]:
    """Deterioration letters (deterioration.json ``letters``, the same SQL as that suite) on the newest store file at or
    before ``day``: point-in-time for ``--as-of`` and no wait on the deterioration suite. Empty when a field is missing."""
    from generic_utils.deterioration import load_recipe as det_recipe, state_columns

    if day is None:
        return {}
    rec = det_recipe()
    sql = dict(state_columns(rec)).get("det")
    known = {r[0] for r in con.execute("DESCRIBE features").fetchall()}
    if not sql or any(str(s.get("field")) not in known for s in rec.get("letters") or []):
        return {}
    rows = con.execute(f"SELECT symbol, {sql} FROM features WHERE as_of = (SELECT max(as_of) FROM features WHERE as_of <= ?)",
                       [day]).fetchall()
    return {str(s): str(d) for s, d in rows if d}


# The core warning field: one short token per name that every reader shares (stance / lookup lines, update flags,
# --line, highlights). Absent level = no token, so an unflagged name costs nothing.
WARN_FIELDS: tuple[str, ...] = ("warn_level", "er_letters", "warn_rule", "stress_5s_usd", "stress_5s_nav", "ev_next", "ev_recent_z")


def warn_token(row: Mapping[str, Any] | None) -> str | None:
    """'WARN_STRONG RBTV$ $-1616 (-1.13% NAV) evz -4.09' or None when the line has no level."""
    if not row or not row.get("warn_level"):
        return None
    out = [str(row["warn_level"]), str(row.get("er_letters") or "-")]
    if row.get("stress_5s_usd") is not None:
        out.append(f"${round(float(row['stress_5s_usd']))}" + (f" ({row['stress_5s_nav']}% NAV)" if row.get("stress_5s_nav") is not None else ""))
    if row.get("ev_recent_z") is not None:
        out.append(f"evz {row['ev_recent_z']}")
    if row.get("ev_next"):
        out.append(f"next {str(row['ev_next'])[:40]}")
    return " ".join(out)


def warn_lookup(pack_dir: str | Path | None, symbols: Sequence[str] = ()) -> dict[str, dict[str, Any]]:
    """{symbol: WARN_FIELDS} from PACK/event_risk/event_risk.duckdb ``names`` (narrow SELECT; all rows when ``symbols`` is
    empty). Empty when the suite has not run on that pack."""
    import duckdb

    if not pack_dir:
        return {}
    db = Path(pack_dir) / "event_risk" / "event_risk.duckdb"
    if not db.exists():
        return {}
    con = duckdb.connect(str(db), read_only=True)
    try:
        have = {r[0] for r in con.execute("DESCRIBE names").fetchall()}
        cols = [c for c in ("symbol", *WARN_FIELDS) if c in have]
        if "er_letters" not in have and "letters" in have:  # packs written before the rename
            cols.append("letters AS er_letters")
        where = f" WHERE symbol IN ({_lit(list(symbols))})" if symbols else ""
        cur = con.execute(f"SELECT {', '.join(cols)} FROM names{where}")
        names = [d[0] for d in cur.description]
        return {str(r[0]): dict(zip(names, r)) for r in cur.fetchall()}
    finally:
        con.close()


def _load_steps(con: Any, recipe: Mapping[str, Any], extra: Sequence[str], as_of: date | None) -> dict[str, Any]:
    floor = (f"mcap >= {float(recipe.get('floor_mcap') or 0)} AND exchange IN ({_lit(recipe.get('exchanges') or [])})"
             f" AND type IN ({_lit(recipe.get('types') or [])})")
    cut = f" AND nxt <= DATE '{as_of.isoformat()}'" if as_of else ""
    extra_sql = f" OR symbol IN ({_lit(extra)})" if extra else ""
    sql = _step_cte(recipe) + f"""
SELECT as_of, nxt, symbol, ind, mcap, ret, ({floor}) AS on_floor FROM r WHERE nxt IS NOT NULL{cut} AND (({floor}){extra_sql})"""
    rows = con.execute(sql).fetchall()
    starts = sorted({r[0] for r in rows})
    ends = {r[0]: r[1] for r in rows}
    syms = sorted({r[2] for r in rows})
    si, ti = {s: i for i, s in enumerate(syms)}, {d: k for k, d in enumerate(starts)}
    R = np.full((len(syms), len(starts)), np.nan)
    M = np.full((len(syms), len(starts)), np.nan)
    floor_mask = np.zeros((len(syms), len(starts)), dtype=bool)
    ind: dict[str, tuple[date, Any]] = {}
    for as_of_, _, sym, ind_, mcap, ret, on_floor in rows:
        i, k = si[sym], ti[as_of_]
        R[i, k], M[i, k], floor_mask[i, k] = float(ret), float(mcap or 0.0), bool(on_floor)
        if sym not in ind or as_of_ >= ind[sym][0]:
            ind[sym] = (as_of_, ind_)
    return {"syms": syms, "starts": starts, "ends": [ends[d] for d in starts], "R": R, "M": M, "floor": floor_mask,
            "ind": {s: v[1] for s, v in ind.items()}}


def _labels(con: Any, syms: Sequence[str], ends: Sequence[date]) -> dict[str, np.ndarray]:
    si, ti = {s: i for i, s in enumerate(syms)}, {d: k for k, d in enumerate(ends)}
    out = {h: np.full((len(syms), len(ends)), np.nan) for h in ("fwd_7d", "fwd_28d")}
    if not ends:
        return out
    sql = (f"SELECT symbol, as_of, fwd_7d, fwd_28d FROM labels WHERE as_of >= DATE '{min(ends).isoformat()}' "
           f"AND as_of <= DATE '{max(ends).isoformat()}'")
    for sym, as_of, f7, f28 in con.execute(sql).fetchall():
        i, k = si.get(sym), ti.get(as_of)
        if i is None or k is None:
            continue
        if f7 is not None:
            out["fwd_7d"][i, k] = float(f7)
        if f28 is not None:
            out["fwd_28d"][i, k] = float(f28)
    return out


# --- industry series --------------------------------------------------------------------------------------


def industry_series(data: Mapping[str, Any]) -> dict[str, Any]:
    """Cap-weighted industry steps on the floor: all members (``all``), and per name without itself (``ex``)."""
    R, M, fl, syms = data["R"], data["M"], data["floor"], data["syms"]
    names = sorted({str(data["ind"].get(s)) for s in syms})
    code = np.array([names.index(str(data["ind"].get(s))) for s in syms])
    present = np.isfinite(R) & fl
    MR = np.where(present, M * np.nan_to_num(R), 0.0)
    Mp = np.where(present, M, 0.0)
    G, T = len(names), R.shape[1]
    SW, SM, N = np.zeros((G, T)), np.zeros((G, T)), np.zeros((G, T))
    np.add.at(SW, code, MR)
    np.add.at(SM, code, Mp)
    np.add.at(N, code, present.astype(float))
    with np.errstate(invalid="ignore", divide="ignore"):
        all_ = np.where(SM > 0, SW / SM, np.nan)
        swi, smi, ni = SW[code] - MR, SM[code] - Mp, N[code] - present
        ex = np.where((ni >= 1) & (smi > 0), swi / smi, np.nan)
    return {"names": names, "code": code, "all": all_, "n": N, "ex": ex}


# --- evidence ---------------------------------------------------------------------------------------------


def _state_arrays(R: np.ndarray, IX: np.ndarray, k: int, recipe: Mapping[str, Any]) -> dict[str, np.ndarray]:
    """S / B / T on every row from the steps up to k (point-in-time)."""
    lt = recipe["letters"]
    zs, ms, bs = int(recipe["z_steps"]), int(recipe["multi_steps"]), int(recipe["beta_steps"])
    min_b = int(recipe["min_beta_steps"])
    n = R.shape[0]
    out = {"S": np.zeros(n, bool), "B": np.zeros(n, bool), "T": np.zeros(n, bool)}
    if k - ms + 1 - zs // 2 >= 0:
        seg = IX[:, k - ms + 1:k + 1]
        cnt5 = np.isfinite(seg).sum(1)
        move = (np.nanprod(1 + seg / 100, axis=1) - 1) * 100
        hist = IX[:, max(0, k - ms + 1 - zs):k - ms + 1]
        cnth = np.isfinite(hist).sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            sd = np.nanstd(np.where(cnth[:, None] > 0, hist, 0.0), axis=1)
            z = move / (sd * math.sqrt(ms))
        ok = (cnt5 >= ms - 1) & (cnth >= max(5, zs // 2)) & (sd > 0)
        out["S"] = ok & (z <= -float(lt["z_shock"]))
    if k + 1 >= min_b:
        y, x = R[:, max(0, k - bs + 1):k + 1], IX[:, max(0, k - bs + 1):k + 1]
        m = np.isfinite(y) & np.isfinite(x)
        cnt = m.sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            xm = np.where(m, x, 0).sum(1) / cnt
            ym = np.where(m, y, 0).sum(1) / cnt
            cov = np.where(m, (x - xm[:, None]) * (y - ym[:, None]), 0).sum(1) / cnt
            var = np.where(m, (x - xm[:, None]) ** 2, 0).sum(1) / cnt
            beta = cov / var
        out["B"] = (cnt >= min_b) & (var > 0) & (beta >= float(lt["beta_hi"]))
    win = R[:, max(0, k - zs + 1):k + 1]
    cntw = np.isfinite(win).sum(1)
    worst = np.min(np.where(np.isfinite(win), win, np.inf), axis=1)
    out["T"] = (cntw >= max(5, zs // 2)) & (worst <= float(lt["tail_pct"]))
    return out


def state_key(s: bool, b: bool, t: bool) -> str:
    return "".join(ch for ch, on in zip(SBT, (s, b, t)) if on) or "none"


def build_evidence(data: Mapping[str, Any], ind: Mapping[str, Any], labels: Mapping[str, np.ndarray], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    ev = recipe.get("evidence") or {}
    fph = ev.get("files_per_horizon") or {"fwd_7d": 5, "fwd_28d": 20}
    dn = float(ev.get("dn_pct", -15.0))
    R, fl = data["R"], data["floor"]
    acc: dict[tuple[str, str], dict[str, list]] = {}
    for k in range(R.shape[1]):
        st = _state_arrays(R, ind["ex"], k, recipe)
        on = fl[:, k] & np.isfinite(R[:, k])
        keys = np.array([state_key(s, b, t) for s, b, t in zip(st["S"], st["B"], st["T"])])
        for h in fph:
            lab = labels[h][:, k]
            ok = on & np.isfinite(lab)
            if ok.sum() < 30:
                continue
            xs = lab - float(np.median(lab[ok]))
            for key in [*sorted(set(keys[ok])), "all"]:
                sel = ok if key == "all" else ok & (keys == key)
                if not sel.any():
                    continue
                a = acc.setdefault((key, h), {"xs": [], "raw": [], "date_means": []})
                a["xs"] += xs[sel].tolist()
                a["raw"] += lab[sel].tolist()
                a["date_means"].append(float(np.mean(xs[sel])))
    rows = []
    for (key, h), a in sorted(acc.items()):
        xs, dm = np.asarray(a["xs"]), np.asarray(a["date_means"])
        n_indep = len(dm) / float(fph[h])
        sd = float(np.std(dm)) if len(dm) > 1 else 0.0
        t = float(np.mean(dm)) / (sd / math.sqrt(n_indep)) if sd > 0 and n_indep > 0 else None
        rows.append({"state": key, "horizon": h, "n": len(xs), "n_files": len(dm), "n_indep": _r(n_indep, 1),
                     "xs_mean_pct": _r(np.mean(xs)), "xs_median_pct": _r(np.median(xs)), "t": _r(t),
                     "hit_pct": _r(np.mean(xs > 0) * 100, 1), "q10_xs_pct": _r(np.quantile(xs, 0.10)),
                     "p_dn_pct": _r(np.mean(np.asarray(a["raw"]) <= dn) * 100, 1) if h == "fwd_28d" else None})
    order = {s: i for i, s in enumerate(ev.get("states") or [])}
    rows.sort(key=lambda r: (r["horizon"], order.get(r["state"], 99)))
    return rows


# --- industry crashes and event reactions -----------------------------------------------------------------


def build_crashes(data: Mapping[str, Any], ind: Mapping[str, Any], recipe: Mapping[str, Any]) -> tuple[list[dict], list[dict]]:
    fo = recipe.get("follow") or {}
    crash_z, min_n = float(fo.get("crash_z", 2.5)), int(fo.get("min_names", 5))
    after = [int(a) for a in fo.get("after_steps") or [1, 5, 20]]
    zs = int(recipe["z_steps"])
    A, N, ends = ind["all"], ind["n"], data["ends"]
    T = A.shape[1]
    med = np.nanmedian(np.where(N >= min_n, A, np.nan), axis=0)
    crashes = []
    for g, name in enumerate(ind["names"]):
        series = [float(v) if N[g, k] >= min_n else float("nan") for k, v in enumerate(A[g])]
        for k in range(T):
            z = z_last(series, k, zs)
            if z is None or z > -crash_z:
                continue
            row = {"ind": name, "step_end": ends[k], "move_pct": _r(series[k]), "z": _r(z), "n_names": int(N[g, k])}
            for a in after:
                if k + a < T:
                    own = compound(series[k + 1:k + 1 + a])
                    base = compound(med[k + 1:k + 1 + a].tolist())
                    row[f"after_{a}_xs_pct"] = _r(own - base) if own is not None and base is not None else None
                else:
                    row[f"after_{a}_xs_pct"] = None
            crashes.append(row)
    follow = []
    for a in after:
        vals = [c[f"after_{a}_xs_pct"] for c in crashes if c.get(f"after_{a}_xs_pct") is not None]
        dates = {c["step_end"] for c in crashes if c.get(f"after_{a}_xs_pct") is not None}
        follow.append({"after_steps": a, "n_crashes": len(vals), "n_dates": len(dates),
                       "xs_mean_pct": _r(np.mean(vals)) if vals else None, "xs_median_pct": _r(np.median(vals)) if vals else None,
                       "hit_pct": _r(np.mean(np.asarray(vals) > 0) * 100, 1) if vals else None,
                       "q10_xs_pct": _r(np.quantile(vals, 0.10)) if vals else None,
                       "q90_xs_pct": _r(np.quantile(vals, 0.90)) if vals else None})
    crashes.sort(key=lambda c: (c["step_end"], c["z"] or 0), reverse=True)
    return crashes, follow


def event_steps(ends: Sequence[date], when: date) -> list[int]:
    """Indices of the steps that carry an event dated `when`: the step ending on the first file on / after it and the next."""
    first = next((k for k, e in enumerate(ends) if e >= when), None)
    return [] if first is None else [k for k in (first, first + 1) if k < len(ends)]


def build_reactions(data: Mapping[str, Any], ind: Mapping[str, Any], events: Sequence[Mapping[str, Any]],
                    book_inds: Mapping[str, list[str]], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    rc = recipe.get("reactions") or {}
    min_z, min_n, zs = float(rc.get("min_abs_z", 2.0)), int(rc.get("min_names", 5)), int(recipe["z_steps"])
    A, N, ends = ind["all"], ind["n"], data["ends"]
    T = A.shape[1]
    med = np.nanmedian(np.where(N >= min_n, A, np.nan), axis=0)
    rows = []
    # One row per (step, industry): every calendar event that lands on the step is listed, none is credited alone.
    by_step: dict[int, list[Mapping[str, Any]]] = {}
    for ev in events:
        when = date.fromisoformat(str(ev["date"]))
        for k in event_steps(ends, when):
            if ends[k] - when <= timedelta(days=4):
                by_step.setdefault(k, []).append(ev)
    series_of = {g: [float(v) if N[g, j] >= min_n else float("nan") for j, v in enumerate(A[g])] for g in range(len(ind["names"]))}
    for k, evs in by_step.items():
        label = "; ".join(f"{e['date']} {e.get('kind')}: {str(e.get('event') or '')[:60]}" for e in evs)
        kinds = ", ".join(sorted({str(e.get("kind")) for e in evs}))
        for g, name in enumerate(ind["names"]):
            if N[g, k] < min_n:
                continue
            series = series_of[g]
            z = z_last(series, k, zs)
            held = book_inds.get(name) or []
            if z is None or (abs(z) < min_z and not held):
                continue
            nxt5 = compound(series[k + 1:k + 6]) if k + 5 < T else None
            base5 = compound(med[k + 1:k + 6].tolist()) if k + 5 < T else None
            rows.append({"step_end": ends[k], "events": label, "kinds": kinds, "ind": name, "move_pct": _r(series[k]), "z": _r(z),
                         "n_names": int(N[g, k]), "book_lines": ", ".join(held),
                         "after_5_xs_pct": _r(nxt5 - base5) if nxt5 is not None and base5 is not None else None})
    rows.sort(key=lambda r: (r["step_end"], r["z"] or 0), reverse=True)
    return rows


# --- calendar and outside ---------------------------------------------------------------------------------


def load_events(recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = _path(recipe.get("calendar") or "config/generic_utils/catalyst_calendar.json")
    rows = json.loads(path.read_text(encoding="utf-8")).get("events") if path.exists() else []
    return [dict(r) for r in rows or [] if r.get("date") and str(r.get("status") or "") != "disputed"]


def latest_odds(recipe: Mapping[str, Any], as_of: date) -> dict[str, dict[str, Any]]:
    path = _path(recipe.get("outside") or "config/generic_utils/sentiment_outside.json")
    out: dict[str, dict[str, Any]] = {}
    for r in (json.loads(path.read_text(encoding="utf-8")).get("rows") if path.exists() else []) or []:
        try:
            d = date.fromisoformat(str(r.get("date")))
        except ValueError:
            continue
        kind = str(r.get("kind"))
        if d <= as_of and (kind not in out or d >= out[kind]["date"]):
            out[kind] = {"date": d, "value": r.get("value"), "unit": r.get("unit"), "source": r.get("source")}
    return out


def _event_label(ev: Mapping[str, Any]) -> str:
    return f"{ev['date']} {str(ev.get('event') or '')[:70]}"


def _odds_text(odds: Mapping[str, Any] | None) -> str | None:
    return f"{odds['value']}{odds.get('unit') or ''} ({odds['date']})" if odds else None


def _event_fields(symbol: str, ind: Any, crowd: Sequence[tuple[str, Any, float]], upcoming: Sequence[Mapping[str, Any]],
                  themes: Mapping[str, Any], share_min: float) -> dict[str, Any]:
    """The nearest mapped event ahead (down / either first: E), the nearest market-wide binary event that does not map
    the line (P), and the odds of either. ``_flags`` holds the letters."""
    mapped, binary = [], None
    for e in upcoming:
        side, how = event_targets(e, symbol, ind, crowd, themes, share_min)
        if side:
            mapped.append((e, side, how))
        elif e.get("_market_wide") and binary is None:
            binary = e
    adverse = [m for m in mapped if m[1] in ("down", "either")]
    first = adverse[0] if adverse else (mapped[0] if mapped else None)
    odds = (first[0].get("_odds") if first else None) or (binary.get("_odds") if binary else None)
    flags = {"E": bool(adverse), "P": binary is not None}
    return {"ev_next": _event_label(first[0]) if first else None, "ev_side": first[1] if first else None,
            "ev_how": first[2] if first else None, "ev_binary": _event_label(binary) if binary else None,
            "ev_odds": _odds_text(odds), "_flags": flags}


# --- build ------------------------------------------------------------------------------------------------


def build_event_risk(pack_dir: str | Path, *, recipe: Mapping[str, Any] | None = None, store: str | Path | None = None,
                     ids: Sequence[str] = (), as_of: date | None = None) -> dict[str, Any]:
    import duckdb

    recipe = dict(recipe or load_recipe())
    lt, cr = recipe["letters"], recipe["crowd"]
    pack = Path(pack_dir)
    pack = pack if pack.is_absolute() else REPO_ROOT / pack
    path = Path(store) if store else default_store_path(load_store_recipe())
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run tv_scan_cli.py panel-store first")
    pack_json = pack / "briefing_pack.json"
    capital = (json.loads(pack_json.read_text(encoding="utf-8")).get("capital") or {}) if pack_json.exists() else {}
    book = _book_lines(pack, {}, set(), set()) if (pack / "book_risk" / "book_risk.csv").exists() else []
    value = {ln["symbol"]: float(ln["value_usd"]) for ln in book}
    nav = _f(capital.get("nav_usd")) or (sum(value.values()) + (_f(capital.get("cash_usd")) or 0.0)) or None
    targets_extra = sorted({*value, *ids})
    con = duckdb.connect(str(path), read_only=True)
    try:
        data = _load_steps(con, recipe, targets_extra, as_of)
        labels = _labels(con, data["syms"], data["ends"])
        printed = recent_prints(con, data["ends"][-int(recipe.get("lookback_days", 5)) - 2:] if data["ends"] else [])
        det = store_det(con, data["ends"][-1] if data["ends"] else None)
    finally:
        con.close()
    syms, R, M, fl, ends = data["syms"], data["R"], data["M"], data["floor"], data["ends"]
    if not ends:
        raise ValueError(f"{path}: no store steps on the floor")
    T, today = R.shape[1], ends[-1]
    ind = industry_series(data)

    # Tape (V, tape fields) from the pack; deterioration (D) from the store above, the suite's names.csv only as a fallback.
    tape = {str(r.get("symbol")): r for r in rows_from_csv(pack / "movers" / "us2b_tape.csv")} if (pack / "movers" / "us2b_tape.csv").exists() else {}
    det_path = pack / "deterioration" / "names.csv"
    if not det and det_path.exists():
        det = {str(r.get("symbol")): r.get("det") for r in rows_from_csv(det_path)}

    # Crowds on the last corr_steps steps.
    # The crowd is built before the last skip_recent steps (an event step's shared shock would otherwise make today's
    # crashers each other's crowd), on rows winsorized at +-clip_sd so one outlier step cannot carry a correlation.
    W, skip = int(cr["corr_steps"]), int(cr.get("skip_recent", 0))
    X = winsorize_rows(R[:, max(0, T - skip - W):T - skip] if skip else R[:, -W:], float(cr.get("clip_sd", 0) or 0))
    last_on = fl[:, -1] & np.isfinite(R[:, -1])
    cand = np.where(np.isfinite(X).all(1) & last_on)[0]
    Xc = X[cand]
    Zc = (Xc - Xc.mean(1, keepdims=True)) / np.where(Xc.std(1, keepdims=True) > 0, Xc.std(1, keepdims=True), np.nan)
    mcap_now = np.array([M[i, np.where(np.isfinite(M[i]))[0][-1]] if np.isfinite(M[i]).any() else 0.0 for i in range(len(syms))])
    targets = [i for i in range(len(syms)) if (last_on[i] or syms[i] in value or syms[i] in ids)]

    events = load_events(recipe)
    themes = json.loads(_path(recipe.get("themes") or "config/generic_utils/themes.json").read_text(encoding="utf-8"))
    odds = latest_odds(recipe, today)
    ahead, back = int(recipe.get("lookahead_days", 10)), int(recipe.get("lookback_days", 5))
    upcoming = sorted([e for e in events if today < date.fromisoformat(str(e["date"])) <= today + timedelta(days=ahead)], key=lambda e: e["date"])
    recent = sorted([e for e in events if today - timedelta(days=back) <= date.fromisoformat(str(e["date"])) <= today], key=lambda e: e["date"])
    binary_kinds = {str(k) for k in recipe.get("binary_kinds") or []}
    reaction_kinds = {str(k) for k in recipe.get("reaction_kinds") or []}
    for e in upcoming + recent:
        e["_binary"] = bool(e.get("binary")) or str(e.get("kind")) in binary_kinds
        e["_market_wide"] = e["_binary"] and not e.get("symbols")
        e["_reacts"] = e["_binary"] or str(e.get("kind")) in reaction_kinds
        e["_odds"] = odds.get(str(e.get("odds_kind"))) if e.get("odds_kind") else None

    min_b, zs, ms, q = int(recipe["min_beta_steps"]), int(recipe["z_steps"]), int(recipe["multi_steps"]), float(recipe["tail_q"])
    k_crowd, same, min_ov = int(cr["k"]), float(cr["same_company_corr"]), int(cr["min_overlap"])
    min_corr, min_members = float(cr.get("min_corr", 0.0)), int(cr.get("min_members", 1))
    share_min = float(recipe.get("crowd_theme_share", 0.4))
    live_state = _state_arrays(R, ind["ex"], T - 1, recipe)
    order = str(lt["order"])
    names: list[dict[str, Any]] = []
    for i in targets:
        sym = syms[i]
        own = R[i]
        x = X[i]
        mask = np.isfinite(x)
        crowd_idx: list[int] = []
        corr_mean = None
        if mask.sum() >= min_ov and len(cand):
            xi = x[mask]
            sd = xi.std()
            if sd > 0:
                zi = (xi - xi.mean()) / sd
                sub = Xc[:, mask]
                zsub = (sub - sub.mean(1, keepdims=True)) / np.where(sub.std(1, keepdims=True) > 0, sub.std(1, keepdims=True), np.nan) \
                    if not mask.all() else Zc
                corr = (zsub @ zi) / mask.sum()
                corr = np.where(cand == i, -np.inf, np.where(np.isfinite(corr) & (corr < same) & (corr >= min_corr), corr, -np.inf))
                top = np.argsort(-corr)[:k_crowd]
                top = [t for t in top if np.isfinite(corr[t])]
                if len(top) >= min_members:
                    crowd_idx = [int(cand[t]) for t in top]
                    corr_mean = float(np.mean(corr[top]))
        if crowd_idx:
            Rc, Mc = R[crowd_idx], M[crowd_idx]
            ok = np.isfinite(Rc) & np.isfinite(Mc)
            with np.errstate(invalid="ignore", divide="ignore"):
                crowd_series = (np.where(ok, Rc * Mc, 0).sum(0) / np.where(ok, Mc, 0).sum(0)).tolist()
            crowd_src = "crowd"
        else:
            # No crowd that clears min_corr / min_members: the industry (without the line) stands in.
            crowd_series = ind["ex"][i].tolist()
            crowd_src = "industry"
        c = crowd_series
        cs = np.asarray(c, dtype=float)
        beta = ols_beta(own, c, min_b)
        dn = [(o, v) for o, v in zip(own, c) if math.isfinite(v) and v < 0 and math.isfinite(o)]
        beta_dn = ols_beta([o for o, _ in dn], [v for _, v in dn], max(10, min_b // 2))
        multi = [compound(c[k - ms + 1:k + 1]) for k in range(ms - 1, T)]
        multi = [m for m in multi if m is not None]
        fin = cs[np.isfinite(cs)]
        tail_cut = float(np.quantile(fin, q)) if len(fin) >= 20 else None
        cap_steps = [o for o, v in zip(own, c) if tail_cut is not None and math.isfinite(v) and v <= tail_cut and math.isfinite(o)]
        lvl = np.cumprod(1 + np.nan_to_num(cs) / 100)
        worst_1 = float(fin.min()) if len(fin) else None
        worst_m = float(min(multi)) if multi else None
        b_use = beta_dn if beta_dn is not None else (beta if beta is not None else 1.0)
        stress_1 = b_use * worst_1 if worst_1 is not None else None
        stress_crowd = b_use * worst_m if worst_m is not None else None
        own_multi = [compound(own[k - ms + 1:k + 1].tolist()) for k in range(ms - 1, T) if np.isfinite(own[k - ms + 1:k + 1]).sum() >= ms - 1]
        own_worst_m = min((m for m in own_multi if m is not None), default=None)
        stress_m, stress_src = stress_crowd, crowd_src
        if own_worst_m is not None and (stress_m is None or own_worst_m < stress_m):
            stress_m, stress_src = own_worst_m, "own"
        held = value.get(sym, 0.0)
        own_win = own[-zs:]
        own_fin = own_win[np.isfinite(own_win)]
        ind_name = data["ind"].get(sym)
        ind_ex = ind["ex"][i].tolist()
        beta_ind = ols_beta(own[-int(recipe["beta_steps"]):], ind_ex[-int(recipe["beta_steps"]):], min_b)
        crowd_members = [(syms[j], data["ind"].get(syms[j]), float(mcap_now[j])) for j in crowd_idx]

        flags = {"S": bool(live_state["S"][i]), "B": bool(live_state["B"][i]), "T": bool(live_state["T"][i])}
        tp = tape.get(sym) or {}
        fv_up, irr, hurdle = _f(tp.get("fv_upside_pct")), _f(tp.get("irr_exp_pct")), _f(tp.get("hurdle_pct"))
        flags["V"] = fv_up is not None and fv_up < 0 and irr is not None and hurdle is not None and irr < hurdle
        d = str(det.get(sym) or "").strip()
        flags["D"] = bool(d) and d not in ("-", "None", "nan")
        stress_usd = held * stress_m / 100 if held and stress_m is not None else None
        stress_nav = stress_usd / nav * 100 if stress_usd is not None and nav else None
        flags["$"] = stress_nav is not None and -stress_nav >= float(lt["stress_nav_pct"])

        # Events: the nearest mapped (E / up side), the nearest binary with no map (P), the last that moved the crowd (R / U).
        ev = _event_fields(sym, ind_name, crowd_members, upcoming, themes, share_min)
        flags.update(ev.pop("_flags"))
        rec_move = rec_z = own_move = ev_recent = None
        rec_print = False
        for e in recent:
            side, _ = event_targets(e, sym, ind_name, crowd_members, themes, share_min)
            if not (e["_reacts"] or side):
                continue
            for k in event_steps(ends, date.fromisoformat(str(e["date"]))):
                if ends[k] - date.fromisoformat(str(e["date"])) > timedelta(days=4):
                    continue
                z = z_last(c, k, zs)
                if z is None or abs(z) < float(lt["z_event"]) or abs(c[k]) < float(lt.get("min_event_move_pct", 0)):
                    continue
                if rec_z is None or abs(z) > abs(rec_z):
                    rec_z, rec_move = z, c[k]
                    own_move = own[k] if math.isfinite(own[k]) else None
                    ev_recent = f"{_event_label(e)} (step to {ends[k]})"
                    rec_print = (sym, ends[k]) in printed
        if rec_z is not None:
            flags["R" if rec_z < 0 else "U"] = True
        letters = letters_of(flags, order)
        level, rule = warn_level(letters, recipe["levels"])
        key = state_key(flags["S"], flags["B"], flags["T"])
        row = {
            "symbol": sym, "ind": ind_name, "mcap_b": _r(mcap_now[i] / 1e9, 1), "in_book": bool(held), "value_usd": _r(held, 0) if held else None,
            "wt_nav": _r(held / nav * 100) if held and nav else None, "on_floor": bool(last_on[i]),
            "warn_level": level, "warn_rule": rule, "er_letters": letters, "sbt_state": key,
            "crowd_src": crowd_src, "crowd_n": len(crowd_idx), "crowd_corr": _r(corr_mean),
            "crowd_top": ", ".join(s for s, _, _ in sorted(crowd_members, key=lambda m: -m[2])[:6]),
            "beta_crowd": _r(beta), "beta_crowd_dn": _r(beta_dn), "beta_ind": _r(beta_ind),
            "crowd_day_pct": _r(c[-1]), "crowd_5s_pct": _r(compound(c[-ms:])), "crowd_21s_pct": _r(compound(c[-21:])),
            "crowd_dd_pct": _r((lvl[-1] / lvl.max() - 1) * 100) if len(lvl) else None, "crowd_z": _r(z_last(c, T - 1, zs)),
            "crowd_z5": _r(multi_z(c, T - 1, ms, zs)), "ind_z5": _r(multi_z(ind_ex, T - 1, ms, zs)),
            "crowd_worst_1s_pct": _r(worst_1), "crowd_worst_5s_pct": _r(worst_m),
            "capture_pct": _r(np.mean(cap_steps)) if cap_steps else None, "capture_n": len(cap_steps),
            "own_worst_pct": _r(own_fin.min()) if len(own_fin) else None, "own_day_pct": _r(own[-1]),
            "stress_1s_pct": _r(stress_1), "stress_crowd_5s_pct": _r(stress_crowd), "own_worst_5s_pct": _r(own_worst_m),
            "stress_5s_pct": _r(stress_m), "stress_src": stress_src, "stress_5s_usd": _r(stress_usd, 0), "stress_5s_nav": _r(stress_nav),
            **ev,
            "ev_recent": ev_recent, "ev_recent_crowd_pct": _r(rec_move), "ev_recent_z": _r(rec_z), "ev_recent_own_pct": _r(own_move),
            "ev_recent_print": rec_print,
            "det": d or None, "steps_n": int(np.isfinite(own).sum()),
        }
        for f in recipe.get("tape_fields") or []:
            row.setdefault(f, _r(tp.get(f), 4) if _f(tp.get(f)) is not None else tp.get(f))
        names.append(row)

    # Book lines and named ids the US store cannot see (other listings, funds): event letters only.
    seen = {r["symbol"] for r in names}
    for sym in [s for s in [*value, *ids] if s not in seen]:
        tp = tape.get(sym) or {}
        ev = _event_fields(sym, tp.get("ind"), [], upcoming, themes, share_min)
        letters = letters_of(ev.pop("_flags"), order)
        level, rule = warn_level(letters, recipe["levels"])
        held = value.get(sym, 0.0)
        names.append({"symbol": sym, "ind": tp.get("ind"), "in_book": bool(held), "value_usd": _r(held, 0) if held else None,
                      "wt_nav": _r(held / nav * 100) if held and nav else None, "on_floor": False, "warn_level": level,
                      "warn_rule": rule, "er_letters": letters, "sbt_state": None, "crowd_src": "none", "crowd_n": 0, **ev, "steps_n": 0})
        seen.add(sym)

    evidence = build_evidence(data, ind, labels, recipe)
    ev_idx = {(r["state"], r["horizon"]): r for r in evidence}
    for row in names:
        e28, e7 = ev_idx.get((row["sbt_state"], "fwd_28d")) or {}, ev_idx.get((row["sbt_state"], "fwd_7d")) or {}
        row.update({"ev_xs_7d": e7.get("xs_mean_pct"), "ev_xs_28d": e28.get("xs_mean_pct"), "ev_t_28d": e28.get("t"),
                    "ev_p_dn_28d": e28.get("p_dn_pct"), "ev_n_indep_28d": e28.get("n_indep")})

    crashes, follow = build_crashes(data, ind, recipe)
    book_inds: dict[str, list[str]] = {}
    for sym in value:
        book_inds.setdefault(str(data["ind"].get(sym)), []).append(sym)
    in_store = [e for e in events if data["ends"][0] <= date.fromisoformat(str(e["date"])) <= today]
    reactions = build_reactions(data, ind, in_store, book_inds, recipe)

    rank_level = {lv: n for n, lv in enumerate(recipe["levels"]["order"])}
    book_rows = sorted([r for r in names if r["in_book"] or r["symbol"] in ids],
                       key=lambda r: (rank_level.get(r["warn_level"], 9), r.get("stress_5s_usd") if r.get("stress_5s_usd") is not None else 0.0))
    # Display order only (Book first, then level, then how hard the event moved the crowd, then size): the lists are
    # candidates, not a ranking. Slim columns (recipe sell_buy.fields); the full row stays in names.
    def _evz(r: Mapping[str, Any]) -> float:
        return float(r["ev_recent_z"]) if r.get("ev_recent_z") is not None else 0.0

    slim = [str(f) for f in (recipe.get("sell_buy") or {}).get("fields") or []]

    def _slim(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{f: r.get(f) for f in slim} for r in rows] if slim else rows

    sell = _slim(sorted([r for r in names if (r["in_book"] and r["warn_level"] in ("WARN_STRONG", "WARN"))
                         or (not r["in_book"] and not r.get("ev_recent_print") and ("R" in r["er_letters"] or "E" in r["er_letters"]))],
                        key=lambda r: (not r["in_book"], rank_level.get(r["warn_level"], 9), _evz(r), -(r.get("mcap_b") or 0))))
    buy = _slim(sorted([r for r in names if r["warn_level"] != "WARN_STRONG" and "R" not in r["er_letters"] and not r.get("ev_recent_print")
                        and ("U" in r["er_letters"] or r.get("ev_side") == "up")],
                       key=lambda r: (not r["in_book"], -_evz(r), -(r.get("mcap_b") or 0))))
    ev_rows = [{"date": e["date"], "kind": e.get("kind"), "event": e.get("event"), "window": "ahead" if e in upcoming else "recent",
                "binary": e["_binary"], "market_wide": e["_market_wide"], "credited": e["_reacts"],
                "symbols": ", ".join(e.get("symbols") or []), "exposure": json.dumps(e.get("exposure")) if e.get("exposure") else None,
                "odds": _odds_text(e.get("_odds")), "read": e.get("read")} for e in recent + upcoming]
    levels = {lv: sum(1 for r in book_rows if r["warn_level"] == lv) for lv in recipe["levels"]["order"]}
    spec = {"pack": pack.as_posix(), "store": path.as_posix(), "first_file": data["starts"][0], "last_file": today, "n_steps": T,
            "as_of": as_of, "nav_usd": _r(nav, 0), "n_names": len(names), "n_book": len(book_rows), "book_levels": levels,
            "book_stress_5s_usd": _r(sum(r.get("stress_5s_usd") or 0 for r in book_rows), 0),
            "n_upcoming": len(upcoming), "n_recent": len(recent), "n_sell": len(sell), "n_buy": len(buy),
            "highlights_top": int((recipe.get("sell_buy") or {}).get("highlights_top") or 8)}
    return {"names": names, "book": book_rows, "sell_book": sell, "buy_book": buy, "events": ev_rows, "reactions": reactions,
            "crashes": crashes, "follow": follow, "evidence": evidence, "spec": spec}


# --- output -----------------------------------------------------------------------------------------------


def highlights_text(payload: Mapping[str, Any]) -> str:
    spec = payload["spec"]
    out = [f"Event risk {spec['last_file']} (store {spec['first_file']}..{spec['last_file']}, {spec['n_steps']} steps; NAV ${spec['nav_usd']})."
           + (f" Point-in-time as of {spec['as_of']} (Book weights are today's)." if spec.get("as_of") else ""),
           f"Book levels: {spec['book_levels']}; Book stress (each line's crowd repeating its worst {5}-step move x its down-beta): ${spec['book_stress_5s_usd']}."]
    out.append("")
    out.append("Book lines at a level (level, letters, stress, crowd, events):")
    quiet = []
    for r in payload["book"]:
        g = r.get
        if not g("warn_level"):
            # No level: one short cell each on a shared line (the token economy: unflagged lines carry no detail).
            quiet.append(f"{r['symbol']} {r['er_letters']}" + (f" ${round(float(g('stress_5s_usd')))}" if g("stress_5s_usd") is not None else ""))
            continue
        if not g("steps_n"):
            out.append(f"  {r['symbol']:<15} {str(g('warn_level') or '-'):<11} {r['er_letters']:<8} not in the US store: event letters only"
                       + (f"; NEXT {g('ev_next')} [{g('ev_side')}]" if g("ev_next") else "") + (f"; BINARY {g('ev_binary')}" if g("ev_binary") else ""))
            continue
        out.append(f"  {r['symbol']:<15} {str(g('warn_level') or '-'):<11} {r['er_letters']:<8} stress {g('stress_5s_pct')}% ({g('stress_src')}) = ${g('stress_5s_usd')} "
                   f"({g('stress_5s_nav')}% NAV); crowd day {g('crowd_day_pct')}% 5s {g('crowd_5s_pct')}% dd {g('crowd_dd_pct')}% z {g('crowd_z')}; "
                   f"beta_dn {g('beta_crowd_dn')} ind_z5 {g('ind_z5')}; state {g('sbt_state')}: 28d xs {g('ev_xs_28d')} (t {g('ev_t_28d')}, "
                   f"n_indep {g('ev_n_indep_28d')}), p_dn {g('ev_p_dn_28d')}%"
                   + (f"; NEXT {g('ev_next')} [{g('ev_side')}: {g('ev_how')}]" if g("ev_next") else "")
                   + (f"; BINARY {g('ev_binary')}" + (f" odds {g('ev_odds')}" if g("ev_odds") else "") if g("ev_binary") else "")
                   + (f"; RECENT {g('ev_recent')} crowd {g('ev_recent_crowd_pct')}% z {g('ev_recent_z')} own {g('ev_recent_own_pct')}%" if g("ev_recent") else "")
                   + (f"; crowd {g('crowd_top')}" if g("crowd_top") else f"; crowd = {g('crowd_src')}"))
    if quiet:
        out.append("No level (letters, stress $): " + "; ".join(quiet))
    ups = [e for e in payload["events"] if e["window"] == "ahead"]
    out += ["", f"Events ahead ({len(ups)}): " + ("; ".join(f"{e['date']} {e['kind']}{' BINARY' if e['binary'] else ''}: {str(e['event'])[:60]}" for e in ups) or "none")]
    last = max((r["step_end"] for r in payload["reactions"]), default=None)
    rec = [r for r in payload["reactions"] if r["step_end"] == last and r["z"] is not None and abs(r["z"]) >= 2]
    if rec:
        out.append("")
        out.append(f"Industry steps to {last} with |z| >= 2 (events on the step: {rec[0]['events']}):")
        dn, up = sorted([r for r in rec if r["z"] < 0], key=lambda r: r["z"]), sorted([r for r in rec if r["z"] > 0], key=lambda r: -r["z"])
        for r in dn[:6] + up[:4]:
            out.append(f"  {r['ind'][:36]:<36} {r['move_pct']}% z {r['z']} (n {r['n_names']})" + (f" Book: {r['book_lines']}" if r["book_lines"] else ""))
        if len(dn) + len(up) > 10:
            out.append(f"  ... {len(dn) + len(up) - 10} more in reactions (step_end {last})")
    out.append("")
    out.append("After an industry crash step (z <= -crash_z), the industry vs the median one: "
               + "; ".join(f"{f['after_steps']}s {f['xs_mean_pct']}pp (hit {f['hit_pct']}%, n {f['n_crashes']} on {f['n_dates']} dates)" for f in payload["follow"]))
    # Evidence only for the states the Book sits in, plus all names (the base rate).
    held_states = {r.get("sbt_state") for r in payload["book"] if r.get("sbt_state")} | {"all"}
    ev28 = [e for e in payload["evidence"] if e["horizon"] == "fwd_28d" and e["state"] in held_states]
    out.append("Evidence for the Book's states (28d raw return less the median; p_dn = share of -15% months): "
               + "; ".join(f"{e['state']} {e['xs_mean_pct']} (t {e['t']}, n_indep {e['n_indep']}, p_dn {e['p_dn_pct']}%)" for e in ev28))
    top = int(spec.get("highlights_top") or 8)
    for title, rows in (("Sell list", payload["sell_book"]), ("Buy list", payload["buy_book"])):
        other = [r for r in rows if not r.get("in_book")][:top]
        out.append(f"{title} {len(rows)} names (candidates, not a budget); first {len(other)} off the Book: "
                   + ("; ".join(f"{r['symbol']} {r.get('er_letters')} evz {r.get('ev_recent_z')} own {r.get('ev_recent_own_pct')}%"
                                if r.get("ev_recent_z") is not None else f"{r['symbol']} {r.get('er_letters')} {r.get('ev_side') or ''}".rstrip()
                                for r in other) or "none"))
    out.append("Letters are measured; the level is a rule; OUTSIDE maps only say which event touches which line.")
    return "\n".join(out) + "\n"


def write_event_risk(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        "Crowd = the k floor names whose store steps correlate most with the line's over the last corr_steps (share classes and ADR twins skipped); "
        "crowd_* = its cap-weighted step series: day / 5 / 21-step move, drawdown from its high, z of the last step and of the last 5 steps.",
        "beta_crowd / beta_crowd_dn = OLS of the line's steps on the crowd's (all / crowd-down steps); capture_pct = the line's mean step on the crowd's "
        "worst tail_q steps. stress_5s_pct = the crowd's worst 5-step move in the store x beta_crowd_dn (else beta_crowd, else 1); _usd / _nav for held lines.",
        "letters (recipe order EPRUSBTVD$): E mapped event ahead on the down / either side; P binary event ahead with no map (direction unknown); "
        "R / U an event in the last lookback_days moved the crowd z <= -2 / >= +2; S industry (without the line) 5-step z <= -2; B beta to the "
        "industry >= 1.2; T own worst step <= -8%; V forward value under the price and IRR under the hurdle; D deterioration letters; $ stress >= 1% of NAV.",
        "warn_level = the first level (WARN_STRONG, WARN, WATCH) whose rule letters are all on (warn_rule). A level is a review trigger, not a course.",
        "ev_* = the evidence row of the line's S / B / T state (point-in-time on the store, industry as the crowd): mean 7d / 28d raw return less the "
        "file's median, t on the per-file means, p_dn = share with fwd_28d <= -15%, n_indep = files / 20.",
        "sell_book / buy_book are candidate lists, not budgets: the Daily's one budget and the judgment decide.",
    ]
    tables = {k: payload[k] for k in ("book", "names", "sell_book", "buy_book", "events", "reactions", "crashes", "follow", "evidence")}
    exported = write_run_export(out_dir, tool="generic_utils.event_risk", tables=tables, sources={"pack": spec["pack"], "store": spec["store"]},
                                notes=notes, extra={"spec": spec}, duckdb_name="event_risk.duckdb")
    (Path(out_dir) / "highlights.log").write_text(highlights_text(payload), encoding="utf-8")
    return exported


def event_risk_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    payload = build_event_risk(pack)
    print(write_event_risk(payload, pack / "event_risk"))
    print(highlights_text(payload), end="")


if __name__ == "__main__":
    event_risk_local_main()
