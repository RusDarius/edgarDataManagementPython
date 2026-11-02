"""Downside risk: how far the market, a cluster and today's Book fell in the store's own history, and what a market move costs.

From the panel store (US floor, close to close between consecutive day files, split-guarded) and the pack's Book
(PACK/book_risk, NAV from the pack's ``capital``):

- ``steps``: one row per pair of consecutive store files: the floor's cap-weighted (``cap``) and median (``med``) return,
  the cluster's cap-weighted return (recipe ``cluster``: theme industries + ids) and today's Book held through the step
  (``book``: today's USD weights; a line without a step takes its beta x the cap step, ``proxy_usd`` says how much), with
  each series' level and drawdown from its running high.
- ``windows``: one row per start file x horizon (calendar days to the first file at least that far, within the tolerance):
  each series' return over the window, the Book's dollars (buy and hold from today's values) and % of NAV.
- ``dist``: per horizon x series: windows, independent windows (span / horizon), mean, the recipe quantiles, the worst
  window and its start, and the share of windows at or under each loss mark.
- ``episodes``: the deepest peak-to-trough drawdowns of each series (non-overlapping): peak, trough, depth, the day it was
  regained (empty = still under), and what the Book, cluster, cap and median series did over the same window.
- ``replay``: each Book line's return and dollars over each episode's window (today's dollars).
- ``lines``: each Book line: weight, beta to the cap step (store OLS, down steps only, TradingView's), correlation,
  one-month volatility from its steps, its own 28-day q05 / worst, its deepest store drawdown, the levels under the close
  (% and dollars to each), the deterioration cells' odds, and the loss for each market move at its store beta.
- ``scenarios``: per market move: the Book's dollars and % of NAV at store beta, down-step beta and TradingView beta.
- ``plans``: each row of the pack's capital plans (recipe ``plans``, PACK/<plan>/plan.csv): the dollars it moves and the beta
  dollars with them (``beta_delta_usd``); ``spec.plans_after`` gives the Book's beta and cluster as % of NAV once a plan fills.

No ranking, no cutoff, no course: floor, horizons, moves and marks are recipe knobs (config/generic_utils/downside_risk.json).
Store windows overlap and the store is months, not cycles: ``n_indep`` says how many independent windows stand behind a tail.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from generic_utils.panel_store import REPO_ROOT, default_store_path, load_store_recipe
from generic_utils.ranking import to_float
from generic_utils.scan_sources import rows_from_csv

_f = to_float
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "downside_risk.json"
THEMES = REPO_ROOT / "config" / "generic_utils" / "themes.json"


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def _lit(values: Sequence[str]) -> str:
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values) or "''"


def quantile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated quantile (numpy's default) of a non-empty list."""
    xs = sorted(values)
    if not xs:
        return None
    pos = (len(xs) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def ols_beta(ys: Sequence[float], xs: Sequence[float]) -> float | None:
    """Slope of ys on xs; None when xs has no variance."""
    if len(xs) < 2:
        return None
    mx, my = mean(xs), mean(ys)
    var = sum((x - mx) ** 2 for x in xs)
    return None if var == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var


def correlation(ys: Sequence[float], xs: Sequence[float]) -> float | None:
    if len(xs) < 2:
        return None
    sx, sy = pstdev(xs), pstdev(ys)
    if not sx or not sy:
        return None
    mx, my = mean(xs), mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) * sx * sy)


def levels_from_steps(steps: Sequence[float]) -> list[float]:
    """Level path from step returns in percent, starting at 100 (one more point than steps)."""
    out = [100.0]
    for s in steps:
        out.append(out[-1] * (1.0 + s / 100.0))
    return out


def drawdowns(levels: Sequence[float]) -> list[float]:
    """Percent under the running high at each point."""
    out, hi = [], -math.inf
    for v in levels:
        hi = max(hi, v)
        out.append((v / hi - 1.0) * 100.0)
    return out


def episodes_of(levels: Sequence[float], *, min_pct: float, top: int) -> list[dict[str, Any]]:
    """Non-overlapping peak-to-trough drawdowns (index positions), deepest first; ``recovered_i`` None = still under."""
    found: list[dict[str, Any]] = []
    peak, cur = 0, None
    for k in range(1, len(levels)):
        if levels[k] >= levels[peak]:
            if cur is not None:
                cur["recovered_i"] = k
                found.append(cur)
                cur = None
            peak = k
            continue
        depth = (levels[k] / levels[peak] - 1.0) * 100.0
        if cur is None:
            cur = {"peak_i": peak, "trough_i": k, "depth_pct": depth, "recovered_i": None}
        elif depth < cur["depth_pct"]:
            cur.update(trough_i=k, depth_pct=depth)
    if cur is not None:
        found.append(cur)
    found = [e for e in found if e["depth_pct"] <= -abs(min_pct)]
    return sorted(found, key=lambda e: e["depth_pct"])[:top]


def window_ends(files: Sequence[date], days: int, tolerance: int) -> list[tuple[int, int]]:
    """(start, end) file positions: the end is the first file at least ``days`` after the start, within the tolerance."""
    out, j = [], 0
    for i, d in enumerate(files):
        j = max(j, i + 1)
        while j < len(files) and files[j] < d + timedelta(days=days):
            j += 1
        if j < len(files) and (files[j] - d).days <= days + tolerance:
            out.append((i, j))
    return out


def cluster_members(recipe: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """(industries, bare tickers) of the recipe cluster from its themes and ids."""
    spec = recipe.get("cluster") or {}
    themes = json.loads(THEMES.read_text(encoding="utf-8")) if THEMES.exists() else {}
    inds, ids = [], [str(i).split(":")[-1].upper() for i in spec.get("ids") or []]
    for name in spec.get("themes") or []:
        t = themes.get(name) or {}
        inds += [str(i) for i in t.get("industries") or []]
        ids += [str(i).split(":")[-1].upper() for i in t.get("ids") or []]
    return sorted(set(inds)), sorted(set(ids))


# --- store reads -------------------------------------------------------------------------------------------


def _step_cte(recipe: Mapping[str, Any]) -> str:
    lo, hi = (recipe.get("clip_pct") or [-90, 300])[:2]
    first = recipe.get("first_day")
    since = f"WHERE as_of >= DATE '{date.fromisoformat(str(first)).isoformat()}'" if first else ""
    return f"""
WITH f AS (SELECT as_of, lead(as_of) OVER (ORDER BY as_of) AS nxt FROM (SELECT DISTINCT as_of FROM panel {since})),
a AS (SELECT p.as_of, p.symbol, p.exchange, p.type, p.ind, p.mcap, p.close, s.split_seq
      FROM panel p JOIN series s ON s.symbol = p.symbol AND s.as_of = p.as_of WHERE p.close > 0),
r AS (SELECT f.as_of, f.nxt, a.symbol, a.exchange, a.type, a.ind, a.mcap,
             greatest(least((b.close / a.close - 1) * 100, {float(hi)}), {float(lo)}) AS ret
      FROM f JOIN a ON a.as_of = f.as_of
      JOIN a b ON b.symbol = a.symbol AND b.as_of = f.nxt AND b.split_seq IS NOT DISTINCT FROM a.split_seq)"""


def market_steps(con: Any, recipe: Mapping[str, Any], inds: Sequence[str], ids: Sequence[str]) -> list[dict[str, Any]]:
    floor = (f"mcap >= {float(recipe.get('floor_mcap') or 0)} AND exchange IN ({_lit(recipe.get('exchanges') or [])})"
             f" AND type IN ({_lit(recipe.get('types') or [])})")
    clus = f"(ind IN ({_lit(inds)}) OR split_part(symbol, ':', 2) IN ({_lit(ids)}))"
    sql = _step_cte(recipe) + f"""
SELECT as_of, nxt,
       sum(mcap * ret) FILTER (WHERE {floor}) / sum(mcap) FILTER (WHERE {floor}) AS cap,
       median(ret) FILTER (WHERE {floor}) AS med,
       count(*) FILTER (WHERE {floor}) AS n_floor,
       sum(mcap * ret) FILTER (WHERE {floor} AND {clus}) / sum(mcap) FILTER (WHERE {floor} AND {clus}) AS cluster,
       count(*) FILTER (WHERE {floor} AND {clus}) AS n_cluster
FROM r GROUP BY as_of, nxt ORDER BY as_of"""
    cols = ["as_of", "nxt", "cap", "med", "n_floor", "cluster", "n_cluster"]
    return [dict(zip(cols, row)) for row in con.execute(sql).fetchall()]


def symbol_steps(con: Any, recipe: Mapping[str, Any], symbols: Sequence[str]) -> dict[str, dict[date, float]]:
    if not symbols:
        return {}
    sql = _step_cte(recipe) + f"\nSELECT symbol, as_of, ret FROM r WHERE symbol IN ({_lit(symbols)})"
    out: dict[str, dict[date, float]] = {}
    for sym, as_of, ret in con.execute(sql).fetchall():
        out.setdefault(sym, {})[as_of] = float(ret)
    return out


# --- build -------------------------------------------------------------------------------------------------


def _in_cluster(symbol: str, ind: Any, inds: set[str], ids: set[str]) -> bool:
    return ind in inds or symbol.split(":")[-1].upper() in ids


def _book_lines(pack: Path, recipe: Mapping[str, Any], inds: set[str], ids: set[str]) -> list[dict[str, Any]]:
    names_path = pack / "deterioration" / "names.csv"
    state = {str(r.get("symbol")): r for r in (rows_from_csv(names_path) if names_path.exists() else [])}
    proxy = {str(k): float(v) for k, v in (recipe.get("proxy_beta") or {}).items()}
    lines = []
    for b in rows_from_csv(pack / "book_risk" / "book_risk.csv"):
        sym, value = str(b.get("symbol") or ""), _f(b.get("value_usd"))
        if not sym or not value:
            continue
        ind = b.get("industry") or b.get("ind")
        beta_tv = _f(b.get("beta"))
        lines.append({
            "symbol": sym, "ind": ind, "in_cluster": _in_cluster(sym, ind, inds, ids),
            "value_usd": value, "close": _f(b.get("close_quote") or b.get("close")),
            "beta_tv": proxy.get(sym, beta_tv if beta_tv is not None else _f(b.get("pack_beta"))),
            "_state": state.get(sym) or {},
        })
    return lines


def build_downside_risk(pack_dir: str | Path, *, recipe: Mapping[str, Any] | None = None, store: str | Path | None = None) -> dict[str, Any]:
    import duckdb

    recipe = dict(recipe or load_recipe())
    pack = Path(pack_dir)
    path = Path(store) if store else default_store_path(load_store_recipe())
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run tv_scan_cli.py panel-store first")
    pack_json = pack / "briefing_pack.json"
    capital = (json.loads(pack_json.read_text(encoding="utf-8")).get("capital") or {}) if pack_json.exists() else {}
    ind_list, id_list = cluster_members(recipe)
    inds, ids = set(ind_list), set(id_list)
    lines = _book_lines(pack, recipe, inds, ids)
    plan_rows = []
    for name in recipe.get("plans") or []:
        plan_csv = pack / str(name) / "plan.csv"
        for r in rows_from_csv(plan_csv) if plan_csv.exists() else []:
            plan_rows.append({"plan": str(name), **r})
    book_syms = {ln["symbol"] for ln in lines}
    extra = sorted({str(r.get("symbol")) for r in plan_rows if r.get("symbol") and str(r.get("symbol")) not in book_syms})
    con = duckdb.connect(str(path), read_only=True)
    try:
        mkt = market_steps(con, recipe, ind_list, id_list)
        own = symbol_steps(con, recipe, sorted(book_syms) + extra)
    finally:
        con.close()
    mkt = [m for m in mkt if m["nxt"] is not None and m["cap"] is not None]
    if not mkt:
        raise ValueError(f"{path}: no store steps on the floor")
    files = [m["as_of"] for m in mkt] + [mkt[-1]["nxt"]]
    cap = [float(m["cap"]) for m in mkt]
    equity = sum(ln["value_usd"] for ln in lines)
    nav = _f(capital.get("nav_usd")) or equity + (_f(capital.get("cash_usd")) or 0.0)
    min_steps = int(recipe.get("beta_min_steps") or 40)
    per_month = float(recipe.get("steps_per_month") or 21)

    def betas(rec: dict[str, Any], mine: Mapping[date, float]) -> None:
        ys = [mine[m["as_of"]] for m in mkt if m["as_of"] in mine]
        xs = [float(m["cap"]) for m in mkt if m["as_of"] in mine]
        down = [(y, x) for y, x in zip(ys, xs) if x < 0]
        enough = len(ys) >= min_steps
        rec["steps_n"] = len(ys)
        rec["beta_store"] = ols_beta(ys, xs) if enough else None
        rec["down_beta_store"] = ols_beta([y for y, _ in down], [x for _, x in down]) if len(down) >= min_steps // 2 else None
        rec["corr_cap"] = correlation(ys, xs) if enough else None
        rec["beta_used"] = next((b for b in (rec["beta_store"], rec.get("beta_tv")) if b is not None), 1.0)
        rec["beta_src"] = "store" if rec["beta_store"] is not None else ("tv" if rec.get("beta_tv") is not None else "one")
        rec["vol_1m_pct"] = pstdev(ys) * math.sqrt(per_month) if enough else None

    # Betas from the line's own steps, then each line's step path (own, else beta x cap).
    for ln in lines:
        mine = own.get(ln["symbol"]) or {}
        betas(ln, mine)
        ln["_path"] = [mine.get(m["as_of"], ln["beta_used"] * float(m["cap"])) for m in mkt]
        ln["_proxied"] = [m["as_of"] not in mine for m in mkt]
        ln["_lvl"] = levels_from_steps(ln["_path"])

    book_steps, proxy_usd = [], []
    for k in range(len(mkt)):
        book_steps.append(sum(ln["value_usd"] * ln["_path"][k] for ln in lines) / equity if equity else 0.0)
        proxy_usd.append(sum(ln["value_usd"] for ln in lines if ln["_proxied"][k]))
    series_steps = {
        "cap": cap,
        "med": [float(m["med"]) if m["med"] is not None else 0.0 for m in mkt],
        "cluster": [float(m["cluster"]) if m["cluster"] is not None else 0.0 for m in mkt],
        "book": book_steps,
    }
    series = [s for s in recipe.get("series") or list(series_steps) if s in series_steps]
    lvl = {s: levels_from_steps(series_steps[s]) for s in series}
    dds = {s: drawdowns(lvl[s]) for s in series}

    steps_rows = []
    for k, m in enumerate(mkt):
        row: dict[str, Any] = {"as_of": m["as_of"], "nxt": m["nxt"], "n_floor": m["n_floor"], "n_cluster": m["n_cluster"],
                               "proxy_usd": _r(proxy_usd[k], 0)}
        for s in series:
            row[f"{s}_pct"] = _r(series_steps[s][k], 3)
            row[f"{s}_lvl"] = _r(lvl[s][k + 1], 3)
            row[f"{s}_dd_pct"] = _r(dds[s][k + 1], 2)
        row["book_usd"] = _r(equity * book_steps[k] / 100.0, 0)
        steps_rows.append(row)

    # Windows and their distribution.
    tol = int(recipe.get("window_tolerance_days") or 5)
    qs = [float(q) for q in recipe.get("quantiles") or []]
    marks = [float(x) for x in recipe.get("loss_marks_pct") or []]
    span = (files[-1] - files[0]).days
    windows, dist = [], []
    for h in [int(x) for x in recipe.get("horizons_days") or []]:
        got: dict[str, list[tuple[float, date]]] = {s: [] for s in series}
        usd: list[float] = []
        for i, j in window_ends(files, h, tol):
            row = {"horizon_d": h, "start": files[i], "end": files[j]}
            for s in series:
                if s == "book":
                    ret_usd = sum(ln["value_usd"] * (ln["_lvl"][j] / ln["_lvl"][i] - 1.0) for ln in lines)
                    ret = ret_usd / equity * 100.0 if equity else 0.0
                    row["book_usd"], row["book_nav_pct"] = _r(ret_usd, 0), _r(ret_usd / nav * 100.0 if nav else None, 2)
                    usd.append(ret_usd)
                else:
                    ret = (lvl[s][j] / lvl[s][i] - 1.0) * 100.0
                row[f"{s}_pct"] = _r(ret, 2)
                got[s].append((ret, files[i]))
            windows.append(row)
        for s in series:
            vals = [v for v, _ in got[s]]
            if not vals:
                continue
            worst = min(got[s])
            d = {"horizon_d": h, "series": s, "n_windows": len(vals), "n_indep": min(len(vals), max(1, span // h)),
                 "mean_pct": _r(mean(vals)), "worst_pct": _r(worst[0]), "worst_start": worst[1]}
            for q in qs:
                d[f"q{round(q * 100):02d}_pct"] = _r(quantile(vals, q))
            for mk in marks:
                d[f"p_le_{abs(mk):g}"] = _r(sum(1 for v in vals if round(v, 6) <= mk) / len(vals), 3)
            if s == "book":
                d["worst_usd"] = _r(min(usd), 0)
                for q in qs:
                    d[f"q{round(q * 100):02d}_usd"] = _r(quantile(usd, q), 0)
            dist.append(d)

    # Episodes and the Book's lines replayed through them.
    episodes, replay = [], []
    for s in series:
        for n, e in enumerate(episodes_of(lvl[s], min_pct=float(recipe.get("episode_min_pct") or 0),
                                          top=int(recipe.get("episodes_per_series") or 4)), 1):
            i, j = e["peak_i"], e["trough_i"]
            eid = f"{s}_{n}"
            book_usd = 0.0
            for ln in lines:
                r = (ln["_lvl"][j] / ln["_lvl"][i] - 1.0) * 100.0
                book_usd += ln["value_usd"] * r / 100.0
                replay.append({"episode": eid, "series": s, "peak": files[i], "trough": files[j], "symbol": ln["symbol"],
                               "in_cluster": ln["in_cluster"], "ret_pct": _r(r), "usd": _r(ln["value_usd"] * r / 100.0, 0),
                               "proxied_steps": sum(ln["_proxied"][i:j])})
            row = {"episode": eid, "series": s, "peak": files[i], "trough": files[j], "days": (files[j] - files[i]).days,
                   "depth_pct": _r(e["depth_pct"]), "recovered_on": files[e["recovered_i"]] if e["recovered_i"] is not None else None,
                   "book_usd": _r(book_usd, 0), "book_pct": _r(book_usd / equity * 100.0 if equity else None),
                   "book_nav_pct": _r(book_usd / nav * 100.0 if nav else None)}
            for o in series:
                if o != "book":
                    row[f"{o}_pct"] = _r((lvl[o][j] / lvl[o][i] - 1.0) * 100.0)
            episodes.append(row)

    # Lines and market-move scenarios.
    moves = [float(x) for x in recipe.get("market_moves_pct") or []]
    out_lines, scenarios = [], []
    to_levels: dict[str, dict[str, float]] = {}
    w28 = window_ends(files, 28, tol)
    for ln in lines:
        s = ln["_state"]
        mine = own.get(ln["symbol"]) or {}
        own_lvl = levels_from_steps([mine[m["as_of"]] for m in mkt if m["as_of"] in mine])
        own28 = [(ln["_lvl"][j] / ln["_lvl"][i] - 1.0) * 100.0 for i, j in w28 if not any(ln["_proxied"][i:j])]
        row = {"symbol": ln["symbol"], "ind": ln["ind"], "in_cluster": ln["in_cluster"], "value_usd": _r(ln["value_usd"], 0),
               "wt_nav": _r(ln["value_usd"] / nav * 100.0 if nav else None), "close": ln["close"], "beta_tv": _r(ln["beta_tv"]),
               "beta_store": _r(ln["beta_store"]), "down_beta_store": _r(ln["down_beta_store"]), "corr_cap": _r(ln["corr_cap"]),
               "beta_used": _r(ln["beta_used"]), "beta_src": ln["beta_src"], "steps_n": ln["steps_n"], "vol_1m_pct": _r(ln["vol_1m_pct"]),
               "own_q05_28d_pct": _r(quantile(own28, 0.05)) if own28 else None, "own_worst_28d_pct": _r(min(own28)) if own28 else None,
               "maxdd_store_pct": _r(min(drawdowns(own_lvl))) if len(own_lvl) > 1 else None,
               "dd_store_now_pct": _r(drawdowns(own_lvl)[-1]) if len(own_lvl) > 1 else None}
        for f in recipe.get("level_fields") or []:
            px = _f(s.get(f))
            pct = (px / ln["close"] - 1.0) * 100.0 if px and ln["close"] else None
            row[f], row[f"to_{f}_pct"] = px, _r(pct)
            row[f"to_{f}_usd"] = _r(ln["value_usd"] * pct / 100.0, 0) if pct is not None else None
            if pct is not None and pct < 0:
                acc = to_levels.setdefault(f, {"usd": 0.0, "n": 0, "value_usd": 0.0})
                acc["usd"] += ln["value_usd"] * pct / 100.0
                acc["n"] += 1
                acc["value_usd"] += ln["value_usd"]
        for f in recipe.get("state_fields") or []:
            row[f] = s.get(f)
        for mv in moves:
            row[f"mkt{abs(mv):g}_usd"] = _r(ln["value_usd"] * ln["beta_used"] * mv / 100.0, 0)
        out_lines.append(row)
    for mv in moves:
        sc = {"cap_move_pct": mv}
        for tag, pick in (("store", lambda ln: ln["beta_used"]),
                          ("down", lambda ln: ln["down_beta_store"] if ln["down_beta_store"] is not None else ln["beta_used"]),
                          ("tv", lambda ln: ln["beta_tv"] if ln["beta_tv"] is not None else 1.0)):
            usd = sum(ln["value_usd"] * pick(ln) * mv / 100.0 for ln in lines)
            sc[f"book_usd_{tag}"], sc[f"nav_pct_{tag}"] = _r(usd, 0), _r(usd / nav * 100.0 if nav else None)
            sc[f"cluster_usd_{tag}"] = _r(sum(ln["value_usd"] * pick(ln) * mv / 100.0 for ln in lines if ln["in_cluster"]), 0)
        scenarios.append(sc)

    beta_usd = sum(ln["value_usd"] * ln["beta_used"] for ln in lines)
    down_usd = sum(ln["value_usd"] * (ln["down_beta_store"] if ln["down_beta_store"] is not None else ln["beta_used"]) for ln in lines)
    clus_usd = sum(ln["value_usd"] for ln in lines if ln["in_cluster"])
    book_vol = pstdev(book_steps) * math.sqrt(per_month) if len(book_steps) > 1 else None

    # Each capital plan's rows: the dollars it moves and the beta dollars that go with them.
    by_line = {ln["symbol"]: ln for ln in lines}
    plans, plan_sums = [], {}
    for r in plan_rows:
        sym = str(r.get("symbol") or "")
        rec = by_line.get(sym)
        if rec is None:
            rec = {"symbol": sym, "beta_tv": None}
            betas(rec, own.get(sym) or {})
        now, after = _f(r.get("value_now_usd")) or 0.0, _f(r.get("value_after_usd")) or 0.0
        ind = r.get("ind")
        clus = _in_cluster(sym, ind, inds, ids)
        plans.append({"plan": r["plan"], "symbol": sym, "course": r.get("course"), "ind": ind, "in_cluster": clus,
                      "value_now_usd": _r(now, 0), "value_after_usd": _r(after, 0), "delta_usd": _r(after - now, 0),
                      "beta_used": _r(rec["beta_used"]), "beta_src": rec["beta_src"], "beta_delta_usd": _r((after - now) * rec["beta_used"], 0),
                      "vol_1m_pct": _r(rec.get("vol_1m_pct"))})
        s = plan_sums.setdefault(r["plan"], {"beta_usd": 0.0, "cluster_usd": 0.0})
        s["beta_usd"] += (after - now) * rec["beta_used"]
        s["cluster_usd"] += (after - now) if clus else 0.0
    plan_after = {name: {"beta_nav_after_pct": _r((beta_usd + s["beta_usd"]) / nav * 100.0 if nav else None),
                         "cluster_nav_after_pct": _r((clus_usd + s["cluster_usd"]) / nav * 100.0 if nav else None)}
                  for name, s in plan_sums.items()}
    spec = {"pack": pack.as_posix(), "store": path.as_posix(), "first_file": str(files[0]), "last_file": str(files[-1]), "n_files": len(files),
            "n_steps": len(mkt), "nav_usd": _r(nav, 0), "equity_usd": _r(equity, 0), "cash_usd": _r(_f(capital.get("cash_usd")), 0),
            "beta_nav_pct": _r(beta_usd / nav * 100.0 if nav else None), "down_beta_nav_pct": _r(down_usd / nav * 100.0 if nav else None),
            "cluster_usd": _r(clus_usd, 0), "cluster_nav_pct": _r(clus_usd / nav * 100.0 if nav else None),
            "cluster_label": (recipe.get("cluster") or {}).get("label"), "cluster_industries": ind_list, "cluster_ids": id_list,
            "book_vol_1m_pct": _r(book_vol), "book_vol_1m_usd": _r(equity * book_vol / 100.0, 0) if book_vol else None,
            "dd_now": {s: _r(dds[s][-1]) for s in series}, "maxdd": {s: _r(min(dds[s])) for s in series},
            "proxied_lines": [ln["symbol"] for ln in lines if ln["steps_n"] == 0], "plans_after": plan_after,
            "to_levels": {f: {"usd": _r(a["usd"], 0), "nav_pct": _r(a["usd"] / nav * 100.0 if nav else None), "n_lines": a["n"],
                              "value_usd": _r(a["value_usd"], 0)} for f, a in to_levels.items()}}
    return {"spec": spec, "steps": steps_rows, "windows": windows, "dist": dist, "episodes": episodes, "replay": replay,
            "lines": out_lines, "scenarios": scenarios, "plans": plans}


def write_downside_risk(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"Store {spec['first_file']} to {spec['last_file']} ({spec['n_files']} files). Steps are close to close between consecutive files on the US floor "
        "(split-guarded, clipped): cap = market-cap-weighted mean, med = median name, cluster = cap-weighted cluster names "
        f"({spec['cluster_label']}), book = today's Book with today's USD weights (a line without a step takes beta_used x the cap step).",
        "windows / dist: start file x horizon (calendar days to the first file at least that far); book_usd is buy-and-hold from today's values. "
        "Windows overlap: n_indep = span / horizon is how many independent windows stand behind a quantile.",
        "episodes: deepest non-overlapping peak-to-trough drawdowns per series; recovered_on empty = still under its high. replay: each Book line over that window, today's dollars.",
        "lines: beta_store = OLS slope of the line's steps on the cap step (down_beta_store: cap-down steps only); beta_used = store, else TradingView, else 1; "
        "vol_1m_pct = step stdev x sqrt(steps_per_month); to_<level>_pct / _usd = the level against the close and the dollars to it; mkt<k>_usd = value x beta_used x -k% (a cap-weighted market move of -k%).",
        "scenarios: a cap-weighted market move and the Book's dollars at store, down-step and TradingView beta. Linear in beta: large moves and cluster shocks run past it (read the episodes).",
        "to_levels: per level, the dollars if every line still above it fell to it (n_lines of them, value_usd at the mark); lines already under it are left out.",
        f"plans: each capital-plan row (value now -> after) with beta_delta_usd = (after - now) x beta_used; plans_after = the Book's beta and cluster as % of NAV once that plan fills: {spec['plans_after']}.",
        "Scenarios, not advice: no ranking, no cutoff, no course.",
    ]
    tables = {k: payload.get(k) or [] for k in ("steps", "windows", "dist", "episodes", "replay", "lines", "scenarios", "plans")}
    return write_run_export(out_dir, tool="generic_utils.downside_risk", tables=tables,
                            sources={"pack": spec["pack"], "store": spec["store"]}, notes=notes, extra={"spec": spec},
                            duckdb_name="downside_risk.duckdb")


def downside_risk_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_downside_risk(build_downside_risk(pack), pack / "downside"))


if __name__ == "__main__":
    downside_risk_local_main()
