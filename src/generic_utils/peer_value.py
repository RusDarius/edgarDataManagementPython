"""Peer value: is a name's multiple a premium or a discount to what its industry pays for the same growth and quality?

A name can have its growth or quality priced in on its own and still trade under (or over) what its peers get for the
same profile. Per day on the panel store (US floor, the TradingView industry as the peer group; the sector when the
industry is thin):

1. Each lens (recipe ``lenses``: forward P/E from the next-FY EPS yield, EV/EBITDA, EV/sales) takes the names with a
   positive yield, works in the log multiple, and fits it inside the group on z-scored, winsorized growth and quality
   drivers (ridge). The fitted multiple is what peers pay for that profile.
2. Per name and lens: ``<lens>_mult`` (the multiple), ``<lens>_ind_med`` (the group median), ``<lens>_prem_pct``
   (premium to the median), ``<lens>_just_pct`` (the premium its growth / quality earns in the group fit),
   ``<lens>_gap_pct`` (fitted vs actual multiple: positive = cheaper than peers with the same profile), ``<lens>_px_fit``
   (the price at the fitted multiple). ``lens`` / ``gap_pct`` / ``prem_pct`` / ``just_pct`` / ``px_fit`` repeat the first
   lens present in ``primary_order``; ``disc_pct`` = -``prem_pct`` (the plain peer discount).
3. Percentiles inside the group (value, growth, EPS growth, quality, ROE, FCF, revisions), spreads to the group median
   (``<field>_vs_ind``), PEG against the group, the forward-value run's priced-in growth and IRR against the group
   (from the pack tape), and the k nearest peers by market cap (``peers``).
4. ``evidence`` / ``quantiles``: the same scores on every ``every_files``-th store day, against the store's forward
   returns in excess of that day's median name: daily rank IC, its t on independent periods, and quantile means. It says
   whether being cheap against peers' fitted multiple paid, and whether it paid more than a plain peer discount.

Separate columns, separate orders: no 0-100 composite, no ranking, no cutoff, no course (knobs in
config/generic_utils/peer_value.json).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float

_f = to_float
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "peer_value.json"
STORE_FIELDS = ("ind", "sector", "mcap", "close", "ey_fwd", "ebitda_yield", "sales_yield", "fcf_yield", "rev_nfy_g", "eps_nfy_g",
                "rev_yoy", "opm", "roe", "fcf_margin", "eps_rev_3m", "rev_rev_3m", "r_1m")


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else round(float(value), digits)


def _quantile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    pos = (len(sorted_vals) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def winsor_z(values: Sequence[float | None], pct: float) -> list[float | None]:
    """Winsorize at pct / 100 - pct and z-score; None stays None (constant columns give 0)."""
    known = sorted(v for v in values if v is not None)
    if len(known) < 2:
        return [0.0 if v is not None else None for v in values]
    lo, hi = _quantile(known, pct / 100.0), _quantile(known, 1.0 - pct / 100.0)
    clipped = [min(max(v, lo), hi) if v is not None else None for v in values]
    kc = [v for v in clipped if v is not None]
    mean = sum(kc) / len(kc)
    sd = math.sqrt(sum((v - mean) ** 2 for v in kc) / (len(kc) - 1))
    return [((v - mean) / sd if sd > 0 else 0.0) if v is not None else None for v in clipped]


def ridge_fit(y: Sequence[float], x: Sequence[Sequence[float]], lam: float) -> dict[str, Any]:
    """y = a + x b with an L2 penalty lam on b (x already centred / scaled); fitted values, coefficients, R2."""
    import numpy as np

    ya = np.asarray(y, dtype=float)
    xa = np.asarray(x, dtype=float)
    a = float(ya.mean())
    if xa.size == 0 or xa.shape[1] == 0:
        return {"a": a, "b": [], "fitted": [a] * len(ya), "r2": 0.0}
    xc = xa - xa.mean(axis=0)
    b = np.linalg.solve(xc.T @ xc + lam * np.eye(xc.shape[1]), xc.T @ (ya - a))
    fitted = a + xc @ b
    ss_tot = float(((ya - a) ** 2).sum())
    r2 = 1.0 - float(((ya - fitted) ** 2).sum()) / ss_tot if ss_tot > 0 else 0.0
    return {"a": a, "b": [float(v) for v in b], "fitted": [float(v) for v in fitted], "r2": r2}


def percentile_ranks(values: Sequence[float | None]) -> list[float | None]:
    """0-100 rank inside the list (ties share the average rank); None stays None."""
    known = sorted((v, i) for i, v in enumerate(values) if v is not None)
    out: list[float | None] = [None] * len(values)
    n = len(known)
    if n == 0:
        return out
    j = 0
    while j < n:
        k = j
        while k + 1 < n and known[k + 1][0] == known[j][0]:
            k += 1
        rank = (j + k) / 2.0
        for m in range(j, k + 1):
            out[known[m][1]] = 100.0 * rank / (n - 1) if n > 1 else 50.0
        j = k + 1
    return out


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 3:
        return None
    rx, ry = percentile_ranks(list(xs)), percentile_ranks(list(ys))
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return sxy / (sx * sy) if sx > 0 and sy > 0 else None


# --- one day -------------------------------------------------------------------------------------------------


def assign_groups(rows: Sequence[dict[str, Any]], min_peers: int) -> None:
    """``group`` = 'ind: X' when the industry has min_peers names, else 'sector: Y' when the sector has, else None."""
    ind_n: dict[str, int] = defaultdict(int)
    sec_n: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.get("ind"):
            ind_n[r["ind"]] += 1
        if r.get("sector"):
            sec_n[r["sector"]] += 1
    for r in rows:
        if r.get("ind") and ind_n[r["ind"]] >= min_peers:
            r["group"], r["group_kind"] = f"ind: {r['ind']}", "ind"
        elif r.get("sector") and sec_n[r["sector"]] >= min_peers:
            r["group"], r["group_kind"] = f"sector: {r['sector']}", "sector"
        else:
            r["group"], r["group_kind"] = None, None


def score_day(rows: list[dict[str, Any]], recipe: Mapping[str, Any], *, detail: bool = True) -> list[dict[str, Any]]:
    """Fill every lens / percentile / spread column on ``rows`` (in place) and return one row per group x lens."""
    assign_groups(rows, int(recipe.get("min_peers", 6)))
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("group"):
            by_group[r["group"]].append(r)
    pct, lam, min_fit = float(recipe.get("winsor_pct", 5.0)), float(recipe.get("ridge", 1.0)), int(recipe.get("min_fit_n", 8))
    groups: list[dict[str, Any]] = []
    for gname, members in by_group.items():
        for lens in recipe.get("lenses") or []:
            name, yfield, drivers = str(lens["name"]), str(lens["yield"]), list(lens.get("drivers") or [])
            cand = [r for r in members if (_f(r.get(yfield)) or 0) > 0]
            if not cand:
                continue
            m = [math.log(100.0 / _f(r[yfield])) for r in cand]
            med = median(m)
            zcols = [winsor_z([_f(r.get(d)) for r in cand], pct) for d in drivers]
            fitted = [med] * len(cand)
            fit = None
            if len(cand) >= min_fit and drivers:
                x = [[(z[i] if z[i] is not None else 0.0) for z in zcols] for i in range(len(cand))]
                fit = ridge_fit(m, x, lam)
                fitted = fit["fitted"]
            for r, mi, fi in zip(cand, m, fitted):
                r[f"{name}_mult"] = _r(math.exp(mi))
                r[f"{name}_ind_med"] = _r(math.exp(med))
                r[f"{name}_prem_pct"] = _r((math.exp(mi - med) - 1.0) * 100.0)
                if fit is not None:
                    r[f"{name}_just_pct"] = _r((math.exp(fi - med) - 1.0) * 100.0)
                    r[f"{name}_gap_pct"] = _r((math.exp(fi - mi) - 1.0) * 100.0)
                    close = _f(r.get("close"))
                    r[f"{name}_px_fit"] = _r(close * math.exp(fi - mi)) if close else None
                r[f"{name}_n"] = len(cand)
            g = {"group": gname, "group_kind": members[0].get("group_kind"), "lens": name, "n": len(cand),
                 "ind_med": _r(math.exp(med)), "fitted": fit is not None, "r2": _r(fit["r2"], 3) if fit else None}
            if fit:
                for d, b in zip(drivers, fit["b"]):
                    g[f"pay_{d}"] = _r((math.exp(b) - 1.0) * 100.0)  # multiple change per 1 sd of the driver, %
            groups.append(g)
        if detail:
            for key, field in (recipe.get("percentiles") or {}).items():
                ranks = percentile_ranks([_f(r.get(field)) for r in members])
                for r, p in zip(members, ranks):
                    r[f"pctl_{key}"] = _r(p, 1)
            for field in recipe.get("spreads") or []:
                vals = sorted(v for v in (_f(r.get(field)) for r in members) if v is not None)
                gm = _quantile(vals, 0.5) if vals else None
                for r in members:
                    v = _f(r.get(field))
                    r[f"{field}_ind_med"] = _r(gm)
                    r[f"{field}_vs_ind"] = _r(v - gm) if v is not None and gm is not None and not math.isnan(gm) else None
            pegs = []
            for r in members:
                pe, g = _f(r.get("pe_mult")), _f(r.get("eps_nfy_g"))
                r["peg"] = _r(pe / g) if pe and g and g > 0 else None
                if r["peg"] is not None:
                    pegs.append(r["peg"])
            gpeg = median(pegs) if pegs else None
            for r in members:
                r["peg_ind_med"] = _r(gpeg)
                r["peg_vs_ind_pct"] = _r((r["peg"] / gpeg - 1.0) * 100.0) if r.get("peg") is not None and gpeg else None
    for r in rows:
        for name in recipe.get("primary_order") or []:
            if r.get(f"{name}_gap_pct") is not None or r.get(f"{name}_prem_pct") is not None:
                r["lens"] = name
                for k in ("gap_pct", "prem_pct", "just_pct", "px_fit", "mult", "ind_med"):
                    r[k] = r.get(f"{name}_{k}")
                r["disc_pct"] = _r(-r["prem_pct"]) if r.get("prem_pct") is not None else None
                break
    return groups


def nearest_peers(rows: Sequence[Mapping[str, Any]], symbols: Sequence[str], k: int) -> list[dict[str, Any]]:
    by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("group") and (_f(r.get("mcap")) or 0) > 0:
            by_group[r["group"]].append(r)
    index = {r["symbol"]: r for r in rows}
    out: list[dict[str, Any]] = []
    for sym in symbols:
        me = index.get(sym)
        if not me or not me.get("group") or not (_f(me.get("mcap")) or 0) > 0:
            continue
        lm = math.log(_f(me["mcap"]))
        others = sorted((p for p in by_group[me["group"]] if p["symbol"] != sym), key=lambda p: abs(math.log(_f(p["mcap"])) - lm))[:k]
        for rank, p in enumerate(others, 1):
            out.append({"symbol": sym, "rank": rank, "peer": p["symbol"], "group": me["group"],
                        "mcap_ratio": _r(_f(p["mcap"]) / _f(me["mcap"]), 2), "lens": me.get("lens"),
                        "mult": me.get("mult"), "peer_mult": p.get(f"{me.get('lens')}_mult") if me.get("lens") else None,
                        "gap_pct": me.get("gap_pct"), "peer_gap_pct": p.get("gap_pct"),
                        "rev_nfy_g": _r(_f(me.get("rev_nfy_g"))), "peer_rev_nfy_g": _r(_f(p.get("rev_nfy_g"))),
                        "opm": _r(_f(me.get("opm"))), "peer_opm": _r(_f(p.get("opm"))),
                        "roe": _r(_f(me.get("roe"))), "peer_roe": _r(_f(p.get("roe"))),
                        "eps_rev_3m": _r(_f(me.get("eps_rev_3m"))), "peer_eps_rev_3m": _r(_f(p.get("eps_rev_3m"))),
                        "r_1m": _r(_f(me.get("r_1m"))), "peer_r_1m": _r(_f(p.get("r_1m")))})
    return out


# --- store reads ---------------------------------------------------------------------------------------------


def _day_rows(con: Any, day: Any, floor: float) -> list[dict[str, Any]]:
    from generic_utils.series import qident

    cols = ", ".join(qident(c) for c in STORE_FIELDS)
    cur = con.execute(f"SELECT symbol, {cols} FROM features WHERE as_of = ? AND mcap >= ?", [day, floor])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def evidence(con: Any, recipe: Mapping[str, Any], store_recipe: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Daily rank IC and quantile excess of each signal against the store's forward returns (excess over the day's median)."""
    from generic_utils.panel_store import horizon_specs

    ev = recipe.get("evidence") or {}
    every = max(1, int(ev.get("every_files", 5)))
    hz = horizon_specs(store_recipe, list(ev.get("horizons") or ["28d"]))
    signals = list(ev.get("signals") or ["gap_pct", "disc_pct"])
    nq = int(ev.get("quantiles", 5))
    floor = float(recipe.get("list_floor_mcap_usd", 2e9))
    days = [r[0] for r in con.execute("SELECT DISTINCT as_of FROM features ORDER BY as_of").fetchall()]
    picked = days[::every]
    ic: dict[tuple[str, str], list[tuple[Any, float]]] = defaultdict(list)
    qsum: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    lab_cols = ", ".join(f"fwd_{h['name']}" for h in hz)
    for day in picked:
        rows = _day_rows(con, day, float(recipe.get("peer_floor_mcap_usd", 1e9)))
        score_day(rows, recipe, detail=False)
        labels = {r[0]: r[1:] for r in con.execute(f"SELECT symbol, {lab_cols} FROM labels WHERE as_of = ?", [day]).fetchall()}
        listed = [r for r in rows if (_f(r.get("mcap")) or 0) >= floor and r["symbol"] in labels]
        for hi, h in enumerate(hz):
            fwd = [(r, labels[r["symbol"]][hi]) for r in listed if labels[r["symbol"]][hi] is not None]
            if len(fwd) < 30:
                continue
            mid = median(v for _, v in fwd)
            for sig in signals:
                pairs = [(_f(r.get(sig)), v - mid) for r, v in fwd if _f(r.get(sig)) is not None]
                if len(pairs) < 30:
                    continue
                rho = spearman([p[0] for p in pairs], [p[1] for p in pairs])
                if rho is not None:
                    ic[(h["name"], sig)].append((day, rho))
                ranks = percentile_ranks([p[0] for p in pairs])
                for (s, x), rk in zip(pairs, ranks):
                    q = min(nq - 1, int(rk / 100.0 * nq)) if rk is not None else None
                    if q is not None:
                        qsum[(h["name"], sig, q + 1)].append(x)
    out, quant = [], []
    hdays = {h["name"]: int(h["days"]) for h in hz}
    for (hname, sig), series in sorted(ic.items()):
        vals = [v for _, v in series]
        n = len(vals)
        mean = sum(vals) / n
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n > 1 else 0.0
        span = (series[-1][0] - series[0][0]).days if n > 1 else 0
        n_indep = max(1.0, min(float(n), span / max(hdays[hname], 1)))
        out.append({"horizon": hname, "signal": sig, "n_days": n, "ic_mean": _r(mean, 4), "ic_sd": _r(sd, 4),
                    "n_indep": _r(n_indep, 1), "t_indep": _r(mean / sd * math.sqrt(n_indep), 2) if sd > 0 else None,
                    "hit_days": _r(sum(1 for v in vals if v > 0) / n, 3), "first_day": series[0][0].isoformat(),
                    "last_day": series[-1][0].isoformat()})
    for (hname, sig, q), xs in sorted(qsum.items()):
        quant.append({"horizon": hname, "signal": sig, "quantile": q, "n": len(xs), "fx_mean": _r(sum(xs) / len(xs), 3),
                      "fx_med": _r(median(xs), 3), "hit": _r(sum(1 for x in xs if x > 0) / len(xs), 3)})
    return out, quant, {"days": len(picked), "first": picked[0].isoformat() if picked else None,
                        "last": picked[-1].isoformat() if picked else None}


# --- build ---------------------------------------------------------------------------------------------------


def _pack_book(pack: Path) -> dict[str, float]:
    import duckdb

    db = pack / "briefing_pack.duckdb"
    if not db.exists():
        return {}
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = {r[0] for r in con.execute("DESCRIBE book").fetchall()}
        val = "value_usd" if "value_usd" in cols else None
        rows = con.execute(f"SELECT symbol{', ' + val if val else ''} FROM book").fetchall()
    except Exception:  # noqa: BLE001 - a pack without a book table
        return {}
    finally:
        con.close()
    return {str(r[0]): (_f(r[1]) if val else None) for r in rows}


def _tape(pack: Path, spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_csv

    path = Path(str(spec.get("csv") or "").replace("{PACK}", str(pack)))
    if not path.exists():
        return {}
    fields = dict(spec.get("fields") or {})
    out = {}
    for r in rows_from_csv(path):
        rec = {}
        for src, dest in fields.items():
            v = r.get(src)
            num = _f(v)
            rec[dest] = num if num is not None else (v if v not in (None, "") else None)
        out[str(r.get("symbol"))] = rec
    return out


def build_peer_value(pack_dir: str | Path | None = None, *, recipe: Mapping[str, Any] | None = None, ids: Sequence[str] = (),
                     store: str | Path | None = None, with_evidence: bool = True) -> dict[str, Any]:
    from generic_utils.panel_store import connect_read, load_store_recipe
    from generic_utils.symbol_identity import ticker_map

    recipe = dict(recipe or load_recipe())
    store_recipe = load_store_recipe()
    pack = Path(pack_dir) if pack_dir else None
    con = connect_read(store)
    try:
        day = con.execute("SELECT max(as_of) FROM features").fetchone()[0]
        rows = _day_rows(con, day, float(recipe.get("peer_floor_mcap_usd", 1e9)))
        groups = score_day(rows, recipe)
        ev_rows, quant, ev_spec = evidence(con, recipe, store_recipe) if with_evidence else ([], [], {})
    finally:
        con.close()
    book = _pack_book(pack) if pack else {}
    by_ticker = ticker_map([r["symbol"] for r in rows])
    named = [i if ":" in i else by_ticker.get(i.upper(), i.upper()) for i in ids if i]
    index = {r["symbol"]: r for r in rows}
    floor = float(recipe.get("list_floor_mcap_usd", 2e9))
    tape_spec = recipe.get("tape_join") or {}
    tape = _tape(pack, tape_spec) if pack else {}
    # forward-value reads against the group (median over the peer rows that carry them)
    for r in rows:
        r.update(tape.get(r["symbol"]) or {})
    for field in tape_spec.get("vs_group") or []:
        by_g: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            v = _f(r.get(field))
            if r.get("group") and v is not None:
                by_g[r["group"]].append(v)
        for r in rows:
            v, vals = _f(r.get(field)), by_g.get(r.get("group") or "")
            r[f"{field}_vs_ind"] = _r(v - median(vals)) if v is not None and vals else None
    listed = [r for r in rows if (_f(r.get("mcap")) or 0) >= floor or r["symbol"] in book or r["symbol"] in named]
    for r in listed:
        r["in_book"] = r["symbol"] in book
        r["book_usd"] = _r(book.get(r["symbol"]), 0) if r["symbol"] in book else None
        r["named"] = r["symbol"] in named
        r["mcap_b"] = _r((_f(r.get("mcap")) or 0) / 1e9, 2)
    missing = [s for s in list(book) + named if s not in index]
    focus = [r["symbol"] for r in listed if r["in_book"] or r["named"]]
    peers = nearest_peers(rows, focus, int(recipe.get("peers_k", 5)))
    spec = {"pack": str(pack) if pack else None, "day": day.isoformat(), "n_peer_rows": len(rows), "n_listed": len(listed),
            "n_groups": len({g["group"] for g in groups}), "n_fitted": sum(1 for g in groups if g["fitted"]),
            "missing_ids": missing, "evidence": ev_spec, "recipe": {k: v for k, v in recipe.items() if not k.startswith("_")}}
    order = ["symbol", "company", "group", "group_kind", "in_book", "named", "book_usd", "mcap_b", "close", "lens", "mult", "ind_med",
             "prem_pct", "just_pct", "gap_pct", "disc_pct", "px_fit"]
    names = [{**{k: r.get(k) for k in order}, **{k: v for k, v in r.items() if k not in order and k not in ("mcap", "ind", "sector")}}
             for r in sorted(listed, key=lambda x: x["symbol"])]
    keys = list(dict.fromkeys(k for n in names for k in n))
    names = [{k: n.get(k) for k in keys} for n in names]
    gkeys = list(dict.fromkeys(k for g in groups for k in g))
    groups = [{k: g.get(k) for k in gkeys} for g in groups]
    return {"spec": spec, "names": names, "groups": groups, "peers": peers, "evidence": ev_rows, "quantiles": quant}


def write_peer_value(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    rec = spec["recipe"]
    lenses = ", ".join(f"{l['name']} = {l['label']} on {l['drivers']}" for l in rec.get("lenses") or [])
    notes = [
        f"Peers: the TradingView industry on the store day {spec['day']} (names over ${rec.get('peer_floor_mcap_usd'):,.0f}); the sector when the "
        f"industry has fewer than {rec.get('min_peers')}. names lists ${rec.get('list_floor_mcap_usd'):,.0f}+ plus the Book and named ids.",
        f"Lenses: {lenses}. Per group the log multiple is fitted on winsorized ({rec.get('winsor_pct')}%) z-scores with ridge {rec.get('ridge')} "
        f"when the group has {rec.get('min_fit_n')}+ names with a positive yield.",
        "<lens>_prem_pct = premium to the group median multiple; <lens>_just_pct = the premium the name's drivers earn in the group fit; "
        "<lens>_gap_pct = fitted vs actual multiple, % (positive = cheaper than peers with the same growth / quality); <lens>_px_fit = the price at "
        "the fitted multiple. lens / gap_pct / prem_pct / just_pct / px_fit = the first lens present in primary_order; disc_pct = -prem_pct.",
        "pctl_* = percentile inside the group (value on the forward EPS yield: high = cheap); <field>_vs_ind = the name minus the group median; "
        "peg = forward P/E / next-FY EPS growth; <fv field>_vs_ind = the forward-value run's priced-in growth / IRR / upside minus the group median.",
        "groups: per group and lens n, median multiple, R2 and pay_<driver> = % change in the multiple per 1 sd of the driver (what the group pays for).",
        f"evidence / quantiles: every {rec.get('evidence', {}).get('every_files')}th store day {spec['evidence'].get('first')}..{spec['evidence'].get('last')}, "
        "daily Spearman IC of each signal with the forward return minus that day's median, t on independent periods (span / horizon), quantile means (5 = highest signal).",
        "Separate columns, separate orders: no 0-100 composite, no ranking, no cutoff, no course.",
    ]
    tables = {k: payload.get(k) or [] for k in ("names", "groups", "peers", "evidence", "quantiles")}
    return write_run_export(out_dir, tool="generic_utils.peer_value", tables=tables, sources={"pack": spec["pack"]},
                            notes=notes, extra={"spec": spec}, duckdb_name="peer_value.duckdb")


def peer_value_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_peer_value(build_peer_value(pack), pack / "peer_value"))


if __name__ == "__main__":
    peer_value_local_main()
