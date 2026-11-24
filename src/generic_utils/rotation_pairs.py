"""Rotation pairs: what moves against what on the panel store.

Baskets (recipe ``baskets``: TradingView industries, theme industries / ids, tickers) and every industry with enough names on
the floor become cap-weighted step series (close to close between consecutive store files, split-guarded, start-file market
caps; the same steps as generic_utils.downside_risk). From them (``write_rotation_pairs`` -> DuckDB + CSV + overview.log +
queries.txt + highlights.log):

- ``baskets``: per basket today: names, cap, the return over each window (``r_<w>``), the excess over the floor's cap step
  (``xs_<w>``), the drawdown from its store high, beta and correlation to the floor, and the baskets it moves most with / against
  once the floor's step is taken out (``with_*`` / ``against_*``).
- ``pairs``: per recipe pair (A, B): correlation of the step returns (``corr_raw``; ``corr_resid`` = each minus the floor's cap
  step; ``corr_resid_recent`` over the last ``recent_steps``), A's return over B's per window (``rel_<w>``), the relative line's
  distance from its store high / low, today's ``z_window``-step relative return against every past one (``rel_z``, ``rel_pct``,
  ``side`` top / bottom / middle at ``extreme_q``), and what the relative line did over the next ``after_steps`` when it sat in its
  top (``hi_*``) or bottom (``lo_*``) tail before: mean, share that reverted, n and ``n_indep`` (starts at least h steps apart).
- ``ind_pairs``: the industry pairs with the lowest residual correlation (they move against each other once the market is
  out) and each one's relative return now.
- ``series``: per store file the floor, each ``series_baskets`` level and its floor-relative level, and each pair's relative
  level (A / B), for charts.

What it does not do: no ranking across pairs, no cutoff, no course. Baskets, pairs, windows and the extreme quantile are recipe
knobs (config/generic_utils/rotation_pairs.json). The store is months, not cycles: the tails are in-sample percentiles of a few
episodes (``n_indep``), direction not proof. Which side to fund, and when, is judgment (lane-book skill).
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from generic_utils.downside_risk import THEMES, _lit, _step_cte, correlation, drawdowns, levels_from_steps, ols_beta, quantile
from generic_utils.panel_store import REPO_ROOT, default_store_path, load_store_recipe

DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "rotation_pairs.json"


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def _bare(sym: Any) -> str:
    return str(sym).split(":")[-1].upper()


def basket_specs(recipe: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """key -> {label, industries, ids (bare)} with theme members expanded."""
    themes = json.loads(THEMES.read_text(encoding="utf-8")) if THEMES.exists() else {}
    out: dict[str, dict[str, Any]] = {}
    for key, spec in (recipe.get("baskets") or {}).items():
        if key.startswith("_"):
            continue
        inds, ids = set(spec.get("industries") or []), {_bare(i) for i in spec.get("ids") or []}
        for name in spec.get("themes") or []:
            t = themes.get(name) or {}
            inds |= set(t.get("industries") or [])
            ids |= {_bare(i) for i in t.get("ids") or []}
        out[key] = {"label": spec.get("label") or key, "industries": sorted(inds), "ids": sorted(ids)}
    return out


# --- store reads -------------------------------------------------------------------------------------------


def _floor(recipe: Mapping[str, Any]) -> str:
    return (f"mcap >= {float(recipe.get('floor_mcap') or 0)} AND exchange IN ({_lit(recipe.get('exchanges') or ['NASDAQ', 'NYSE', 'AMEX'])})"
            f" AND type IN ({_lit(recipe.get('types') or ['stock', 'dr'])})")


def store_steps(con: Any, recipe: Mapping[str, Any], specs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Floor, basket and industry cap-weighted steps keyed by the start file."""
    rows = [(k, ind, None) for k, s in specs.items() for ind in s["industries"]] + [(k, None, t) for k, s in specs.items() for t in s["ids"]]
    con.execute("CREATE OR REPLACE TEMP TABLE bm (basket VARCHAR, ind VARCHAR, tick VARCHAR)")
    if rows:
        con.execute("INSERT INTO bm VALUES " + ", ".join("(?, ?, ?)" for _ in rows), [v for r in rows for v in r])
    con.execute(f"CREATE OR REPLACE TEMP TABLE fl AS {_step_cte(recipe)} SELECT as_of, nxt, symbol, ind, mcap, ret FROM r WHERE {_floor(recipe)}")
    mkt = con.execute("SELECT as_of, nxt, sum(mcap * ret) / sum(mcap), median(ret), count(*) FROM fl GROUP BY as_of, nxt ORDER BY as_of").fetchall()
    bsk = con.execute("""
        WITH m AS (SELECT DISTINCT b.basket, f.as_of, f.symbol, f.mcap, f.ret
                   FROM fl f JOIN bm b ON f.ind = b.ind OR split_part(f.symbol, ':', 2) = b.tick)
        SELECT basket, as_of, sum(mcap * ret) / sum(mcap), count(*), sum(mcap) / 1e9 FROM m GROUP BY basket, as_of""").fetchall()
    ind = con.execute("SELECT ind, as_of, sum(mcap * ret) / sum(mcap), count(*), sum(mcap) / 1e9 FROM fl WHERE ind IS NOT NULL GROUP BY ind, as_of").fetchall()
    return {"market": mkt, "baskets": bsk, "industries": ind}


# --- pure reads (tested) -----------------------------------------------------------------------------------


def window_return(levels: Sequence[float], w: int) -> float | None:
    return None if len(levels) <= w else (levels[-1] / levels[-1 - w] - 1.0) * 100.0


def _excess(r: float | None, base: float | None) -> float | None:
    """r minus base; empty when either is empty (a short series is not a 0% return)."""
    return None if r is None or base is None else r - base


def rolling_returns(levels: Sequence[float], w: int) -> list[float]:
    """Every w-step return of the level path; position k is the return ending at levels[k + w]."""
    return [(levels[k + w] / levels[k] - 1.0) * 100.0 for k in range(len(levels) - w)]


def thin(starts: Sequence[int], gap: int) -> int:
    """How many starts stay when each kept start is at least ``gap`` steps after the last kept one."""
    kept, last = 0, None
    for s in sorted(starts):
        if last is None or s - last >= gap:
            kept, last = kept + 1, s
    return kept


def extreme_after(levels: Sequence[float], z: int, q: float, h: int) -> dict[str, Any]:
    """After the z-step relative return sat in its top / bottom q tail (in-sample): the next h-step move of the line."""
    rr = rolling_returns(levels, z)
    if len(rr) < 10:
        return {}
    hi_cut, lo_cut = quantile(rr, 1.0 - q), quantile(rr, q)
    out: dict[str, Any] = {}
    for side, pick, revert in (("hi", lambda v: v >= hi_cut, lambda f: f < 0), ("lo", lambda v: v <= lo_cut, lambda f: f > 0)):
        starts = [k + z for k, v in enumerate(rr) if pick(v) and k + z + h < len(levels)]
        fwd = [(levels[t + h] / levels[t] - 1.0) * 100.0 for t in starts]
        out[f"{side}_fwd_{h}"] = _r(mean(fwd)) if fwd else None
        out[f"{side}_rev_{h}"] = _r(100.0 * sum(1 for f in fwd if revert(f)) / len(fwd), 1) if fwd else None
        out[f"{side}_n_{h}"] = len(fwd)
        out[f"{side}_nind_{h}"] = thin(starts, h)
    return out


def pair_read(a: Sequence[float], b: Sequence[float], m: Sequence[float], recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Correlations, relative returns, z / percentile and the extreme-after reads for one aligned pair of step series."""
    la, lb = levels_from_steps(a), levels_from_steps(b)
    rel = [x / y * 100.0 for x, y in zip(la, lb)]
    z = int(recipe.get("z_window") or 21)
    q = float(recipe.get("extreme_q") or 0.1)
    recent = int(recipe.get("recent_steps") or 63)
    ra, rb = [x - y for x, y in zip(a, m)], [x - y for x, y in zip(b, m)]
    out: dict[str, Any] = {"n_steps": len(a), "corr_raw": _r(correlation(a, b), 3), "corr_resid": _r(correlation(ra, rb), 3),
                           "corr_resid_recent": _r(correlation(ra[-recent:], rb[-recent:]), 3)}
    for w in recipe.get("windows") or [5, 21, 63]:
        out[f"rel_{int(w)}"] = _r(window_return(rel, int(w)))
    out["rel_from_high"] = _r((rel[-1] / max(rel) - 1.0) * 100.0)
    out["rel_from_low"] = _r((rel[-1] / min(rel) - 1.0) * 100.0)
    rr = rolling_returns(rel, z)
    if len(rr) >= 10:
        now, sd = rr[-1], pstdev(rr)
        out["rel_z"] = _r((now - mean(rr)) / sd) if sd else None
        out["rel_pct"] = _r(100.0 * sum(1 for v in rr if v <= now) / len(rr), 1)
        out["side"] = "top" if now >= quantile(rr, 1.0 - q) else "bottom" if now <= quantile(rr, q) else "middle"
    for h in recipe.get("after_steps") or [5, 21]:
        out.update(extreme_after(rel, z, q, int(h)))
    out["_rel"] = rel
    return out


# --- build -------------------------------------------------------------------------------------------------


def _aligned(steps: Mapping[date, float], files: Sequence[date]) -> tuple[list[float], int]:
    """Steps on every file (a missing step is 0: the basket had no name that step), and how many were missing."""
    return [float(steps.get(d, 0.0) or 0.0) for d in files], sum(1 for d in files if d not in steps)


def build_rotation_pairs(*, recipe: Mapping[str, Any] | None = None, store: str | Path | None = None) -> dict[str, Any]:
    import duckdb

    recipe = dict(recipe or load_recipe())
    path = Path(store) if store else default_store_path(load_store_recipe())
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run tv_scan_cli.py panel-store first")
    specs = basket_specs(recipe)
    con = duckdb.connect(str(path), read_only=True)
    try:
        raw = store_steps(con, recipe, specs)
    finally:
        con.close()
    mkt = [r for r in raw["market"] if r[1] is not None and r[2] is not None]
    if len(mkt) < 10:
        raise ValueError(f"{path}: under 10 store steps on the floor")
    files = [r[0] for r in mkt]
    m = [float(r[2]) for r in mkt]
    m_lv = levels_from_steps(m)
    day = mkt[-1][1]

    def collect(rows: Sequence[tuple]) -> tuple[dict[str, dict[date, float]], dict[str, tuple[int, float]]]:
        steps: dict[str, dict[date, float]] = {}
        last: dict[str, tuple[int, float]] = {}
        for key, as_of, ret, n, cap in rows:
            if ret is None:
                continue
            steps.setdefault(key, {})[as_of] = float(ret)
            if as_of == files[-1]:
                last[key] = (int(n), float(cap))
        return steps, last

    b_steps, b_last = collect(raw["baskets"])
    i_steps, i_last = collect(raw["industries"])
    windows = [int(w) for w in recipe.get("windows") or [5, 21, 63]]

    def series_row(key: str, s: Sequence[float]) -> dict[str, Any]:
        lv = levels_from_steps(s)
        row: dict[str, Any] = {}
        for w in windows:
            r = window_return(lv, w)
            mw = window_return(m_lv, w)
            row[f"r_{w}"] = _r(r)
            row[f"xs_{w}"] = _r(r - mw) if r is not None and mw is not None else None
        row["dd_high"] = _r(drawdowns(lv)[-1])
        row["beta_mkt"] = _r(ols_beta(s, m), 2)
        row["corr_mkt"] = _r(correlation(s, m), 3)
        return row

    b_aligned = {k: _aligned(b_steps[k], files) for k in specs if k in b_steps}
    resid = {k: [x - y for x, y in zip(v[0], m)] for k, v in b_aligned.items()}
    baskets = []
    for key, spec in specs.items():
        if key not in b_aligned:
            baskets.append({"basket": key, "label": spec["label"], "n": 0})
            continue
        s, missing = b_aligned[key]
        n, cap = b_last.get(key, (0, 0.0))
        cors = sorted(((correlation(resid[key], resid[o]), o) for o in resid if o != key), key=lambda t: (t[0] is None, t[0]))
        cors = [c for c in cors if c[0] is not None]
        row = {"basket": key, "label": spec["label"], "n": n, "cap_b": _r(cap, 0), "missing_steps": missing, **series_row(key, s)}
        if cors:
            row.update({"against": cors[0][1], "against_corr": _r(cors[0][0], 3), "with": cors[-1][1], "with_corr": _r(cors[-1][0], 3)})
        baskets.append(row)

    pairs, pair_levels = [], {}
    for a_key, b_key in recipe.get("pairs") or []:
        if a_key not in b_aligned or b_key not in b_aligned:
            pairs.append({"pair": f"{a_key} / {b_key}", "a": a_key, "b": b_key, "n_steps": 0})
            continue
        read = pair_read(b_aligned[a_key][0], b_aligned[b_key][0], m, recipe)
        pair_levels[f"{a_key} / {b_key}"] = read.pop("_rel")
        pairs.append({"pair": f"{a_key} / {b_key}", "a": a_key, "b": b_key, **read})

    ind_cfg = recipe.get("industries") or {}
    min_n, min_cap = int(ind_cfg.get("min_names") or 5), float(ind_cfg.get("min_cap_b") or 0)
    keep = sorted(k for k, (n, cap) in i_last.items() if n >= min_n and cap >= min_cap and len(i_steps.get(k) or {}) >= len(files) * 0.9)
    i_al = {k: _aligned(i_steps[k], files)[0] for k in keep}
    i_res = {k: [x - y for x, y in zip(v, m)] for k, v in i_al.items()}
    i_lv = {k: levels_from_steps(v) for k, v in i_al.items()}
    cand = []
    for x in range(len(keep)):
        for y in range(x + 1, len(keep)):
            c = correlation(i_res[keep[x]], i_res[keep[y]])
            if c is not None:
                cand.append((c, keep[x], keep[y]))
    cand.sort()
    ind_pairs = []
    for c, a_key, b_key in cand[: int(ind_cfg.get("top_pairs") or 30)]:
        rel = [p / q * 100.0 for p, q in zip(i_lv[a_key], i_lv[b_key])]
        ind_pairs.append({"ind_a": a_key, "ind_b": b_key, "corr_resid": _r(c, 3), "corr_raw": _r(correlation(i_al[a_key], i_al[b_key]), 3),
                          "cap_a_b": _r(i_last[a_key][1], 0), "cap_b_b": _r(i_last[b_key][1], 0),
                          **{f"rel_{w}": _r(window_return(rel, w)) for w in windows},
                          "xs_21_a": _r(_excess(window_return(i_lv[a_key], 21), window_return(m_lv, 21))),
                          "xs_21_b": _r(_excess(window_return(i_lv[b_key], 21), window_return(m_lv, 21)))})

    series = []
    show = [k for k in recipe.get("series_baskets") or [] if k in b_aligned]
    lv = {k: levels_from_steps(b_aligned[k][0]) for k in show}
    for i, d in enumerate(list(files) + [day]):
        series.append({"as_of": d, "key": "floor", "kind": "floor", "level": _r(m_lv[i]), "vs_floor": 100.0})
        for k in show:
            series.append({"as_of": d, "key": k, "kind": "basket", "level": _r(lv[k][i]), "vs_floor": _r(lv[k][i] / m_lv[i] * 100.0)})
        for k, rel in pair_levels.items():
            series.append({"as_of": d, "key": k, "kind": "pair", "level": _r(rel[i]), "vs_floor": None})
    spec = {"day": day, "first_day": files[0], "n_steps": len(files), "store": path.as_posix(), "windows": windows,
            "z_window": int(recipe.get("z_window") or 21), "extreme_q": float(recipe.get("extreme_q") or 0.1),
            "after_steps": [int(h) for h in recipe.get("after_steps") or [5, 21]], "n_baskets": len(specs), "n_pairs": len(pairs),
            "n_industries": len(keep), "floor_r_21": _r(window_return(m_lv, 21)), "floor_dd": _r(drawdowns(m_lv)[-1])}
    return {"spec": spec, "baskets": baskets, "pairs": pairs, "ind_pairs": ind_pairs, "series": series}


# --- output ------------------------------------------------------------------------------------------------


def highlight_lines(payload: Mapping[str, Any]) -> list[str]:
    spec = payload["spec"]
    hs = spec["after_steps"]
    out = [f"rotation pairs {spec['day']} store {spec['first_day']}..{spec['day']} ({spec['n_steps']} steps); floor 21s {spec['floor_r_21']}% "
           f"dd {spec['floor_dd']}%; z window {spec['z_window']} steps, tails at {spec['extreme_q']}"]
    out.append("baskets: r_5 / r_21 / r_63 | xs_21 xs_63 | dd | beta | against (resid corr) | with")
    for b in sorted(payload["baskets"], key=lambda r: -(r.get("xs_21") or -999)):
        if not b.get("n"):
            continue
        out.append(f"  {b['basket']} n={b['n']} {b.get('r_5')} / {b.get('r_21')} / {b.get('r_63')} | {b.get('xs_21')} {b.get('xs_63')} | "
                   f"{b.get('dd_high')} | {b.get('beta_mkt')} | {b.get('against')} {b.get('against_corr')} | {b.get('with')} {b.get('with_corr')}")
    out.append("pairs (A over B): corr raw/resid/recent | rel_5 rel_21 rel_63 | from high/low | z pct side | after top / bottom tail: "
               + ", ".join(f"{h}s mean rev% n_indep" for h in hs))
    for p in payload["pairs"]:
        if not p.get("n_steps"):
            out.append(f"  {p['pair']}: missing basket")
            continue
        tails = "; ".join(f"{h}s hi {p.get(f'hi_fwd_{h}')} {p.get(f'hi_rev_{h}')}% ({p.get(f'hi_nind_{h}')}) lo {p.get(f'lo_fwd_{h}')} "
                          f"{p.get(f'lo_rev_{h}')}% ({p.get(f'lo_nind_{h}')})" for h in hs)
        out.append(f"  {p['pair']}: {p.get('corr_raw')}/{p.get('corr_resid')}/{p.get('corr_resid_recent')} | {p.get('rel_5')} {p.get('rel_21')} "
                   f"{p.get('rel_63')} | {p.get('rel_from_high')}/{p.get('rel_from_low')} | {p.get('rel_z')} {p.get('rel_pct')} {p.get('side')} | {tails}")
    out.append("industry pairs that move most against each other (resid corr | rel_21 | xs_21 a / b):")
    for r in payload["ind_pairs"][:15]:
        out.append(f"  {r['ind_a']} vs {r['ind_b']}: {r['corr_resid']} | {r.get('rel_21')} | {r.get('xs_21_a')} / {r.get('xs_21_b')}")
    return out


def write_rotation_pairs(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"Store {spec['first_day']} to {spec['day']}, {spec['n_steps']} steps between consecutive files on the recipe floor (split-guarded, "
        "start-file market caps, clipped). A basket's step is the cap-weighted mean of its names' steps; the floor's is the same over every name.",
        "baskets: r_<w> = the basket's return over the last w steps, xs_<w> = minus the floor's; dd_high = under the basket's store high; "
        "beta / corr to the floor's step; against / with = the basket whose residual step (minus the floor's) correlates least / most with it.",
        f"pairs: A over B. corr_raw on the steps, corr_resid on steps minus the floor's (moves against each other when negative), "
        f"corr_resid_recent on the last recipe recent_steps; rel_<w> = A's return over B's (relative line A / B); rel_z / rel_pct = today's "
        f"{spec['z_window']}-step relative return against every past one (in-sample); side top / bottom = in the {spec['extreme_q']} tail. "
        "hi_* / lo_* = after a past top / bottom tail, the relative line's next h steps: fwd mean, rev = share that moved back the other way, "
        "n starts and nind = starts at least h steps apart (the independent pieces).",
        "ind_pairs: industries with recipe min_names / min_cap_b and steps on 90% of files; the lowest residual correlations first.",
        "series: per file the floor level, each series_baskets level and vs_floor (level / floor x 100), and each pair's relative level.",
        "Months of store, one or two regimes: frequencies with their n, direction not proof. No composite, no ranking across pairs, no course.",
    ]
    exported = write_run_export(out_dir, tool="generic_utils.rotation_pairs",
                                tables={k: payload[k] for k in ("baskets", "pairs", "ind_pairs", "series")},
                                sources={"store": spec["store"], "recipe": DEFAULT_RECIPE.as_posix(), "themes": THEMES.as_posix()},
                                notes=notes, extra={"spec": spec}, duckdb_name="rotation_pairs.duckdb")
    hl = Path(out_dir) / "highlights.log"
    hl.write_text("\n".join(highlight_lines(payload)) + "\n", encoding="utf-8")
    exported["highlights"] = hl.as_posix()
    return exported


def rotation_pairs_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_rotation_pairs(build_rotation_pairs(), pack / "rotation_pairs"))


if __name__ == "__main__":
    rotation_pairs_local_main()
