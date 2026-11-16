"""Lane book: lanes of opportunity tested on the panel store's history and read on today's names, beside the catalyst
timeline, value against catalyst timing, industry rotation and the cash at hand.

A lane is one SQL condition on the store's features (recipe ``lanes``; ``defs`` are ``{macro}`` snippets; ``ai`` is the cluster
flag and ``<field>_pr`` the day's percent rank of each ``pct_fields`` entry). The same condition gives the lane's record on every
past store day (precedent) and its names on the newest day (live), so the record and the names share one definition.

Tables (``write_lane_book`` -> DuckDB + CSV + overview.log + queries.txt + highlights.log):

- ``lanes``: lane x split (all / ai / other): rows, days, names; per horizon h (``horizons_days``): ``fwd_h`` raw mean (clipped),
  ``med_h`` raw median, ``xs_h`` mean excess over that day's floor median, ``hit_h`` share beating it, ``p_up_h`` / ``p_dn_h`` the odds
  of a raw move of +big / -big % or more, ``q10_h`` / ``q90_h``, ``t_h`` (t across calendar blocks of h days, ``n_blk_h`` blocks: the
  independent pieces), ``xs_h_h1`` / ``xs_h_h2`` (the store's first / second half of labelled days); ``n_live`` today and ``live_top``.
- ``lane_months``: lane x split x month: the same reads per month (a lane that paid in one month only is one episode).
- ``when_value``: value field x split x value quintile (per day; Q5 = the highest value, i.e. the cheapest for a yield) x catalyst
  timing bucket (first matching ``when.timing`` row, plus ``any``); ``when_spread``: Q5 minus Q1 per field, split and bucket.
- ``rotation``: every past (day, industry): the industry's cap-weighted 1-month return over the floor's (``x``) and the last week's
  excess over a week's share of it (``y``) -> quadrant (Leading x+ y+, Weakening x+ y-, Lagging x- y-, Improving x- y+) and the 1-month
  excess quintile -> the industry's names' next 7 / 14 / 28-day excess (equal weight).
- ``industries``: the newest day's industry tape, quadrant, breadth, revisions, value level, the Book's NAV share, live lane names.
- ``names``: every live name in a long or avoid lane plus the Book and named ids: lane flags, the best measured long lane for its
  split, store features, its value x timing cell and that cell's record, the board's joins (band, kill, targets, model, peers,
  letters, catalysts) and ``entry_px`` / ``month_up_pct`` / ``risk_pct`` / ``payoff`` (the focus-book formulas).
- ``timeline`` / ``weeks``: every dated event in ``timeline.horizon_days`` (PACK/outlook events plus calendar rows the pack has not
  seen) with the lanes of the name it touches; one row per week with its prints, the Book's prints and the OUTSIDE events.
- ``capital``: NAV, cash, the cash floor and the starters / adds the cash above it buys at the hint sizes.

What it does not do: no composite, no ranking across lanes, no course, no size. Lane conditions, horizons, the big-move mark,
timing buckets and the cash floor are recipe knobs (config/generic_utils/lane_book.json). Lanes were written from earlier findings
on the same store, so their record is in-sample and mostly one regime: frequencies with their n, direction not proof. Which lane
gets capital, and when, is the judgment's (``.claude/skills/lane-book``).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.panel_store import REPO_ROOT, default_store_path, load_store_recipe
from generic_utils.ranking import to_float
from generic_utils.series import qident

_f = to_float
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "lane_book.json"
CALENDAR = REPO_ROOT / "config" / "generic_utils" / "catalyst_calendar.json"

FEATURE_FIELDS = ("r_1d", "r_1w", "r_1m", "r_3m", "vs50", "vs200", "hi_prox", "rsi", "relvol", "ey_fwd", "ey_fwd_ri", "sales_yield_ri",
                  "pt_up", "eps_rev_3m", "rev_rev_3m", "rev_yoy", "opm", "fcf_margin", "nd_ebitda", "dte", "dsp", "last_surprise",
                  "rev_surprise", "react_last", "since_print", "p_cont")
BOARD_FIELDS = ("company", "close", "day", "w", "m1", "band_state", "bounce_low", "bounce_high", "bounce_kill", "to_band_high_pct",
                "kill_dist_pct", "street_px", "b28d_q20", "b28d_q80", "dec_7d", "dec_28d", "pred_28d", "peer_gap_pct", "peer_prem_pct",
                "pe_nfy", "mos_peer_pct", "fv_upside_pct", "det", "n_det", "warn_level", "er_letters", "updown_vol", "beta", "rvol_20",
                "earn_date", "earn_slot", "earn_days", "setup_cell", "setup_mean", "c_beat_sold", "p_beat", "ev_h28", "next_kind",
                "next_date", "in_book", "book_usd")


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: Any, digits: int = 2) -> float | None:
    v = _f(value)
    return None if v is None else round(v, digits)


def _or(value: float | None, default: float) -> float:
    """``value`` unless it is missing: a measured 0.0 is a value, not a gap."""
    return default if value is None else value


def _lit(values: Any) -> str:
    if isinstance(values, (list, tuple, set)):
        return ", ".join(_lit(v) for v in values) or "NULL"
    return "'" + str(values).replace("'", "''") + "'"


def expand(where: str, defs: Mapping[str, str]) -> str:
    """Replace ``{macro}`` snippets (nested allowed) with their parenthesised SQL; an unknown macro is an error."""
    out = str(where)
    for _ in range(6):
        new = out
        for key, sql in defs.items():
            new = new.replace("{" + key + "}", f"({sql})")
        if new == out:
            break
        out = new
    if "{" in out or "}" in out:
        raise ValueError(f"unknown macro in lane condition: {where}")
    return out


def cluster_lists(recipe: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    from generic_utils.downside_risk import cluster_members

    return cluster_members(recipe)


# --- store frame --------------------------------------------------------------------------------------------


def build_frame(con: Any, recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Temp table ``lb``: floor rows of the attached store ``s`` with labels, excess, percent ranks, the cluster flag and lane flags."""
    floor = recipe.get("floor") or {}
    hs = [int(h) for h in recipe.get("horizons_days") or [7, 28]]
    inds, ids = cluster_lists(recipe)
    defs = dict(recipe.get("defs") or {})
    known = {r[0] for r in con.execute("DESCRIBE s.features").fetchall()}
    first = recipe.get("first_day")
    since = f" AND f.as_of >= DATE '{date.fromisoformat(str(first)).isoformat()}'" if first else ""
    lab_cols = ", ".join(f"l.fwd_{h}d" for h in hs)
    xs_cols = ", ".join(f"fwd_{h}d - median(fwd_{h}d) OVER (PARTITION BY as_of) AS xs_{h}d" for h in hs)
    prs = [p for p in recipe.get("pct_fields") or [] if p in known]
    pr_cols = "".join(f", CASE WHEN {qident(p)} IS NOT NULL THEN percent_rank() OVER (PARTITION BY as_of, ({qident(p)} IS NULL) "
                      f"ORDER BY {qident(p)}) END AS {qident(p + '_pr')}" for p in prs)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE lb0 AS
        SELECT f.*, coalesce(f.ind IN ({_lit(inds)}) OR split_part(f.symbol, ':', 2) IN ({_lit(ids)}), FALSE) AS ai, {lab_cols}
        FROM s.features f LEFT JOIN s.labels l USING (symbol, as_of)
        WHERE f.mcap >= {float(floor.get('mcap_min') or 0)} AND f.exchange IN ({_lit(floor.get('exchanges') or ['NASDAQ', 'NYSE', 'AMEX'])}){since}""")
    con.execute(f"CREATE OR REPLACE TEMP TABLE lb1 AS SELECT *, {xs_cols}{pr_cols} FROM lb0")
    lanes = list(recipe.get("lanes") or [])
    flags = ", ".join(f"coalesce(({expand(ln['where'], defs)}), FALSE) AS {qident('l_' + ln['key'])}" for ln in lanes)
    con.execute(f"CREATE OR REPLACE TEMP TABLE lb AS SELECT *, {flags} FROM lb1")
    con.execute("DROP TABLE lb0")
    con.execute("DROP TABLE lb1")
    day = con.execute("SELECT max(as_of) FROM lb").fetchone()[0]
    first_day = con.execute("SELECT min(as_of) FROM lb").fetchone()[0]
    mids, last_lab = {}, {}
    for h in hs:
        lo, hi = con.execute(f"SELECT min(as_of), max(as_of) FROM lb WHERE fwd_{h}d IS NOT NULL").fetchone()
        last_lab[h] = hi
        mids[h] = lo + (hi - lo) / 2 if lo and hi else None
    return {"day": day, "first_day": first_day, "horizons": hs, "mids": mids, "last_labelled": last_lab, "cluster_inds": inds,
            "cluster_ids": ids, "pct_fields": prs, "lanes": lanes, "defs": defs}


# --- generic cell statistics --------------------------------------------------------------------------------


def stats_rows(con: Any, source_sql: str, keys: Sequence[str], frame: Mapping[str, Any], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per key cell of ``source_sql`` (columns: keys, as_of, symbol, fwd_<h>d, xs_<h>d): counts and the per-horizon reads."""
    hs, mids = frame["horizons"], frame["mids"]
    big = float(recipe.get("big_move_pct") or 10)
    lo, hi = (recipe.get("clip_pct") or [-90, 300])[:2]
    kq = ", ".join(qident(k) for k in keys)
    parts = ["count(*) AS n_rows", "count(DISTINCT as_of) AS n_days", "count(DISTINCT symbol) AS n_names"]
    for h in hs:
        fx, xs = f"fwd_{h}d", f"xs_{h}d"
        mid = f"DATE '{mids[h].isoformat()}'" if mids.get(h) else "NULL"
        parts += [
            f"count({xs}) AS n_{h}",
            f"round(avg(greatest(least({fx}, {float(hi)}), {float(lo)})), 2) AS fwd_{h}",
            f"round(median({fx}), 2) AS med_{h}",
            f"round(avg({xs}), 2) AS xs_{h}",
            f"round(100.0 * avg(CASE WHEN {xs} > 0 THEN 1.0 WHEN {xs} IS NOT NULL THEN 0.0 END), 1) AS hit_{h}",
            f"round(100.0 * avg(CASE WHEN {fx} >= {big} THEN 1.0 WHEN {fx} IS NOT NULL THEN 0.0 END), 1) AS p_up_{h}",
            f"round(100.0 * avg(CASE WHEN {fx} <= {-big} THEN 1.0 WHEN {fx} IS NOT NULL THEN 0.0 END), 1) AS p_dn_{h}",
            f"round(quantile_cont({fx}, 0.1), 2) AS q10_{h}",
            f"round(quantile_cont({fx}, 0.9), 2) AS q90_{h}",
            f"round(avg({xs}) FILTER (WHERE as_of < {mid}), 2) AS xs_{h}_h1",
            f"round(avg({xs}) FILTER (WHERE as_of >= {mid}), 2) AS xs_{h}_h2",
        ]
    rows = _query(con, f"WITH src AS ({source_sql}) SELECT {kq}, {', '.join(parts)} FROM src GROUP BY {kq}")
    first = frame["first_day"].isoformat()
    for h in hs:
        tsql = f"""
            WITH src AS ({source_sql}),
            d AS (SELECT {kq}, as_of, avg(xs_{h}d) AS m FROM src WHERE xs_{h}d IS NOT NULL GROUP BY {kq}, as_of),
            b AS (SELECT {kq}, floor(date_diff('day', DATE '{first}', as_of) / {h}) AS blk, avg(m) AS bm FROM d GROUP BY {kq}, blk)
            SELECT {kq}, count(*) AS n_blk, CASE WHEN count(*) >= 3 AND stddev_samp(bm) > 0
                   THEN avg(bm) / (stddev_samp(bm) / sqrt(count(*))) END AS t FROM b GROUP BY {kq}"""
        tmap = {tuple(r[k] for k in keys): r for r in _query(con, tsql)}
        for row in rows:
            got = tmap.get(tuple(row[k] for k in keys)) or {}
            row[f"t_{h}"] = _r(got.get("t"))
            row[f"n_blk_{h}"] = got.get("n_blk") or 0
    return rows


def _query(con: Any, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    cur = con.execute(sql, list(params or []))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _metric_cols(hs: Sequence[int]) -> str:
    return ", ".join(f"fwd_{h}d, xs_{h}d" for h in hs)


_SPLITS = (("all", "TRUE"), ("ai", "ai"), ("other", "NOT ai"))


def lane_tables(con: Any, frame: Mapping[str, Any], recipe: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hs, lanes, day = frame["horizons"], frame["lanes"], frame["day"]
    m = _metric_cols(hs)
    union = " UNION ALL ".join(
        f"SELECT {_lit(ln['key'])} AS lane, {_lit(sp)} AS split, as_of, symbol, {m} FROM lb WHERE {qident('l_' + ln['key'])} AND {cond}"
        for ln in lanes for sp, cond in _SPLITS)
    rows = stats_rows(con, union, ["lane", "split"], frame, recipe)
    months_src = f"SELECT lane, split, strftime(as_of, '%Y-%m') AS month, as_of, symbol, {m} FROM ({union})"
    months = stats_rows(con, months_src, ["lane", "split", "month"], frame, recipe)
    top_n = int(recipe.get("live_top") or 10)
    meta = {ln["key"]: ln for ln in lanes}
    live: dict[tuple[str, str], tuple[int, str]] = {}
    for ln in lanes:
        order = str(ln.get("order") or "mcap DESC")
        for sp, cond in _SPLITS:
            got = con.execute(f"SELECT count(*), array_to_string(list_slice(list(symbol ORDER BY {order} NULLS LAST), 1, {top_n}), ', ') "
                              f"FROM lb WHERE as_of = ? AND {qident('l_' + ln['key'])} AND {cond}", [day]).fetchone()
            live[(ln["key"], sp)] = (int(got[0] or 0), got[1] or "")
    order_idx = {ln["key"]: i for i, ln in enumerate(lanes)}
    split_idx = {sp: i for i, (sp, _) in enumerate(_SPLITS)}
    out = []
    for row in sorted(rows, key=lambda r: (order_idx.get(r["lane"], 99), split_idx.get(r["split"], 9))):
        ln = meta.get(row["lane"]) or {}
        n_live, top = live.get((row["lane"], row["split"]), (0, ""))
        out.append({"lane": row["lane"], "split": row["split"], "side": ln.get("side"), "family": ln.get("family"), "label": ln.get("label"),
                    **{k: v for k, v in row.items() if k not in ("lane", "split")}, "n_live": n_live, "live_top": top,
                    "where": ln.get("where"), "why": ln.get("why")})
    # lanes with no history rows still show their live names
    seen = {(r["lane"], r["split"]) for r in out}
    for (key, sp), (n_live, top) in live.items():
        if (key, sp) not in seen and n_live:
            ln = meta[key]
            out.append({"lane": key, "split": sp, "side": ln.get("side"), "family": ln.get("family"), "label": ln.get("label"),
                        "n_rows": 0, "n_live": n_live, "live_top": top, "where": ln.get("where"), "why": ln.get("why")})
    months.sort(key=lambda r: (order_idx.get(r["lane"], 99), split_idx.get(r["split"], 9), r["month"]))
    for r in months:
        r["side"] = (meta.get(r["lane"]) or {}).get("side")
    return out, months


def when_tables(con: Any, frame: Mapping[str, Any], recipe: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    spec = recipe.get("when") or {}
    defs, hs = frame["defs"], frame["horizons"]
    known = {r[0] for r in con.execute("DESCRIBE lb").fetchall()}
    fields = [f for f in spec.get("value_fields") or [] if f in known]
    nq = int(spec.get("quantiles") or 5)
    timing = [(str(lbl), expand(cond, defs)) for lbl, cond in spec.get("timing") or []]
    tcase = "CASE " + " ".join(f"WHEN {cond} THEN {_lit(lbl)}" for lbl, cond in timing) + " END" if timing else "'any'"
    splits = [(str(s), expand(c, defs)) for s, c in spec.get("splits") or [["all", "TRUE"]]]
    m = _metric_cols(hs)
    grid: list[dict[str, Any]] = []
    for vf in fields:
        base = (f"SELECT *, CASE WHEN {qident(vf)} IS NOT NULL THEN 'Q' || ntile({nq}) OVER (PARTITION BY as_of, ({qident(vf)} IS NULL) "
                f"ORDER BY {qident(vf)}) END AS vq, {tcase} AS timing FROM lb")
        union = " UNION ALL ".join(
            f"SELECT {_lit(vf)} AS field, {_lit(sp)} AS split, vq, timing, as_of, symbol, {m} FROM g WHERE vq IS NOT NULL AND ({cond}) "
            f"UNION ALL SELECT {_lit(vf)}, {_lit(sp)}, vq, 'any', as_of, symbol, {m} FROM g WHERE vq IS NOT NULL AND ({cond})"
            for sp, cond in splits)
        con.execute(f"CREATE OR REPLACE TEMP TABLE g AS {base}")
        grid += stats_rows(con, union, ["field", "split", "vq", "timing"], frame, recipe)
    order_t = {lbl: i for i, (lbl, _) in enumerate(timing)}
    order_t["any"] = len(timing)
    order_s = {s: i for i, (s, _) in enumerate(splits)}
    grid.sort(key=lambda r: (fields.index(r["field"]), order_s.get(r["split"], 9), order_t.get(r["timing"], 99), r["vq"]))
    labels = spec.get("value_labels") or {}
    for r in grid:
        r["field_label"] = labels.get(r["field"], r["field"])
    spread = []
    top, bot = f"Q{nq}", "Q1"
    cells = {(r["field"], r["split"], r["timing"], r["vq"]): r for r in grid}
    for (f, sp, tm, q), hi_row in cells.items():
        if q != top or (f, sp, tm, bot) not in cells:
            continue
        lo_row = cells[(f, sp, tm, bot)]
        row = {"field": f, "split": sp, "timing": tm, "n_top": hi_row.get("n_rows"), "n_bottom": lo_row.get("n_rows")}
        for h in hs:
            for k in (f"xs_{h}", f"p_up_{h}", f"p_dn_{h}"):
                a, b = _f(hi_row.get(k)), _f(lo_row.get(k))
                row[f"{k}_spread"] = None if a is None or b is None else round(a - b, 2)
        spread.append(row)
    spread.sort(key=lambda r: (fields.index(r["field"]), order_s.get(r["split"], 9), order_t.get(r["timing"], 99)))
    return grid, spread


def name_cells(con: Any, frame: Mapping[str, Any], recipe: Mapping[str, Any], symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Today's value quintile (first value field) and timing bucket per symbol."""
    spec = recipe.get("when") or {}
    known = {r[0] for r in con.execute("DESCRIBE lb").fetchall()}
    fields = [f for f in spec.get("value_fields") or [] if f in known]
    if not fields or not symbols:
        return {}
    vf, nq = fields[0], int(spec.get("quantiles") or 5)
    timing = [(str(lbl), expand(cond, frame["defs"])) for lbl, cond in spec.get("timing") or []]
    tcase = "CASE " + " ".join(f"WHEN {cond} THEN {_lit(lbl)}" for lbl, cond in timing) + " END" if timing else "'any'"
    sql = (f"WITH t AS (SELECT symbol, CASE WHEN {qident(vf)} IS NOT NULL THEN 'Q' || ntile({nq}) OVER (PARTITION BY ({qident(vf)} IS NULL) "
           f"ORDER BY {qident(vf)}) END AS vq, {tcase} AS timing FROM lb WHERE as_of = ?) SELECT * FROM t WHERE symbol IN ({_lit(list(symbols))})")
    return {r["symbol"]: {"vq": r["vq"], "timing": r["timing"], "vq_field": vf} for r in _query(con, sql, [frame["day"]])}


# --- rotation -----------------------------------------------------------------------------------------------


def rotation_tables(con: Any, frame: Mapping[str, Any], recipe: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    spec = recipe.get("rotation") or {}
    min_n = int(spec.get("min_names") or 5)
    share = float(spec.get("week_share") or 0.238)
    hs = frame["horizons"]
    fwd = ", ".join(f"avg(fwd_{h}d) AS fwd_{h}d, avg(xs_{h}d) AS xs_{h}d" for h in hs)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE ind_day AS
        WITH fl AS (SELECT as_of,
                      sum(mcap * r_1m) FILTER (WHERE r_1m IS NOT NULL) / sum(mcap) FILTER (WHERE r_1m IS NOT NULL) AS fm,
                      sum(mcap * r_1w) FILTER (WHERE r_1w IS NOT NULL) / sum(mcap) FILTER (WHERE r_1w IS NOT NULL) AS fw
                    FROM lb GROUP BY as_of),
        i AS (SELECT as_of, ind, count(*) AS n, sum(mcap) / 1e9 AS cap_b, bool_or(ai) AS ai,
                     sum(mcap * r_1m) FILTER (WHERE r_1m IS NOT NULL) / sum(mcap) FILTER (WHERE r_1m IS NOT NULL) AS im,
                     sum(mcap * r_1w) FILTER (WHERE r_1w IS NOT NULL) / sum(mcap) FILTER (WHERE r_1w IS NOT NULL) AS iw, {fwd}
              FROM lb WHERE ind IS NOT NULL GROUP BY as_of, ind HAVING count(*) >= {min_n})
        SELECT i.*, im - fm AS x, (iw - fw) - (im - fm) * {share} AS y,
               CASE WHEN im - fm >= 0 AND (iw - fw) - (im - fm) * {share} >= 0 THEN 'Leading'
                    WHEN im - fm >= 0 THEN 'Weakening'
                    WHEN (iw - fw) - (im - fm) * {share} >= 0 THEN 'Improving' ELSE 'Lagging' END AS quad,
               'X' || ntile(5) OVER (PARTITION BY as_of ORDER BY im - fm) AS xq
        FROM i JOIN fl USING (as_of) WHERE im IS NOT NULL AND iw IS NOT NULL""")
    m = _metric_cols(hs)
    src = (f"SELECT 'quadrant' AS dim, quad AS cell, as_of, ind AS symbol, {m} FROM ind_day "
           f"UNION ALL SELECT '1m excess quintile', xq, as_of, ind, {m} FROM ind_day "
           f"UNION ALL SELECT 'quadrant x quintile', quad || ' ' || xq, as_of, ind, {m} FROM ind_day "
           f"UNION ALL SELECT 'all industries', 'all', as_of, ind, {m} FROM ind_day")
    rot = stats_rows(con, src, ["dim", "cell"], frame, recipe)
    dim_order = {"all industries": 0, "quadrant": 1, "1m excess quintile": 2, "quadrant x quintile": 3}
    rot.sort(key=lambda r: (dim_order.get(r["dim"], 9), r["cell"]))
    for r in rot:
        r["unit"] = "industry-days (names equal weight)"
    day = frame["day"]
    live = _query(con, """
        WITH d AS (SELECT * FROM lb WHERE as_of = ?),
        fl AS (SELECT sum(mcap * r_3m) FILTER (WHERE r_3m IS NOT NULL) / sum(mcap) FILTER (WHERE r_3m IS NOT NULL) AS f3,
                      median(ey_fwd) AS fey FROM d)
        SELECT ind, round(sum(mcap * r_3m) FILTER (WHERE r_3m IS NOT NULL) / sum(mcap) FILTER (WHERE r_3m IS NOT NULL) - max(fl.f3), 2) AS r_3m_xs,
               round(100.0 * avg(CASE WHEN vs50 > 0 THEN 1.0 WHEN vs50 IS NOT NULL THEN 0.0 END), 1) AS above50_pct,
               round(100.0 * avg(CASE WHEN vs200 > 0 THEN 1.0 WHEN vs200 IS NOT NULL THEN 0.0 END), 1) AS above200_pct,
               round(median(relvol), 2) AS relvol_med, round(median(eps_rev_3m), 2) AS eps_rev_3m_med,
               round(median(ey_fwd) - max(fl.fey), 2) AS ey_fwd_vs_floor, round(median(pt_up), 1) AS pt_up_med,
               round(median(hi_prox), 1) AS hi_prox_med
        FROM d CROSS JOIN fl GROUP BY ind""", [day])
    extra = {r["ind"]: r for r in live}
    today = _query(con, "SELECT * FROM ind_day WHERE as_of = ?", [day])
    hist = {(r["dim"], r["cell"]): r for r in rot}
    out = []
    for r in sorted(today, key=lambda r: -(r["x"] or 0)):
        q = hist.get(("quadrant", r["quad"])) or {}
        e = extra.get(r["ind"]) or {}
        out.append({"ind": r["ind"], "ai": bool(r["ai"]), "n": r["n"], "cap_b": _r(r["cap_b"], 1), "r_1w": _r(r["iw"]), "r_1m": _r(r["im"]),
                    "x_1m_xs": _r(r["x"]), "y_pace": _r(r["y"]), "quad": r["quad"], "xq": r["xq"], "r_3m_xs": e.get("r_3m_xs"),
                    "above50_pct": e.get("above50_pct"), "above200_pct": e.get("above200_pct"), "relvol_med": e.get("relvol_med"),
                    "eps_rev_3m_med": e.get("eps_rev_3m_med"), "ey_fwd_vs_floor": e.get("ey_fwd_vs_floor"), "pt_up_med": e.get("pt_up_med"),
                    "hi_prox_med": e.get("hi_prox_med"), "quad_xs_28": q.get("xs_28"), "quad_hit_28": q.get("hit_28"), "quad_n_28": q.get("n_28")})
    return rot, out


# --- pack joins ---------------------------------------------------------------------------------------------


def _pack_rows(path: Path, sql: str) -> list[dict[str, Any]]:
    import duckdb

    if not path.exists():
        return []
    con = duckdb.connect(str(path), read_only=True)
    try:
        return _query(con, sql)
    finally:
        con.close()


def board_rows(pack: Path) -> dict[str, dict[str, Any]]:
    path = pack / "board" / "board.duckdb"
    if not path.exists():
        return {}
    import duckdb

    con = duckdb.connect(str(path), read_only=True)
    try:
        cols = {r[0] for r in con.execute("DESCRIBE names").fetchall()}
        pick = [c for c in BOARD_FIELDS if c in cols]
        return {r["symbol"]: r for r in _query(con, f"SELECT symbol, {', '.join(qident(c) for c in pick)} FROM names")}
    finally:
        con.close()


def pack_capital(pack: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    path = pack / "briefing_pack.json"
    if not path.exists():
        return {}, [], []
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data.get("capital") or {}), list(data.get("book") or []), list(data.get("industry_exposure") or [])


def timeline_rows(pack: Path, day: date, horizon: int, lanes_by_symbol: Mapping[str, Mapping[str, Any]],
                  calendar_path: Path = CALENDAR) -> list[dict[str, Any]]:
    end = day + timedelta(days=horizon)
    rows = _pack_rows(pack / "outlook" / "outlook.duckdb",
                      "SELECT date, kind, symbol, slot, detail, status, source, \"read\", mcap_b, in_book FROM events")
    rows = [r for r in rows if r.get("date") and day <= r["date"] <= end]
    seen = {(r["date"], str(r.get("detail") or ""), str(r.get("symbol") or "")) for r in rows}
    if calendar_path.exists():
        cal = json.loads(calendar_path.read_text(encoding="utf-8"))
        for ev in cal.get("events") or []:
            try:
                d = date.fromisoformat(str(ev.get("date")))
            except ValueError:
                continue
            if not day <= d <= end:
                continue
            for sym in ev.get("symbols") or [None]:
                key = (d, str(ev.get("event") or ""), str(sym or ""))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"date": d, "kind": ev.get("kind"), "symbol": sym, "slot": None, "detail": ev.get("event"), "status": ev.get("status"),
                             "source": ev.get("source"), "read": ev.get("read"), "mcap_b": None, "in_book": None, "from_calendar": True})
    monday = day - timedelta(days=day.weekday())
    out = []
    for r in rows:
        d = r["date"]
        lane = lanes_by_symbol.get(str(r.get("symbol") or "")) or {}
        out.append({"date": d, "dow": d.strftime("%a"), "days": (d - day).days, "week": f"W{(d - monday).days // 7}",
                    "kind": r.get("kind"), "symbol": r.get("symbol"), "slot": r.get("slot"), "detail": r.get("detail"),
                    "status": r.get("status"), "mcap_b": _r(r.get("mcap_b"), 1) if r.get("mcap_b") is not None else lane.get("mcap_b"),
                    "in_book": 1 if (r.get("in_book") or lane.get("in_book")) else 0, "lanes": lane.get("lanes") or "",
                    "avoid": lane.get("avoid") or "", "n_lanes": lane.get("n_lanes") or 0, "best_lane": lane.get("best_lane"),
                    "best_xs_28": lane.get("best_xs_28"), "read": r.get("read"), "source": r.get("source"),
                    "outside": 0 if r.get("kind") in ("earnings", "ex_dividend", "ipo_lockup") else 1})
    out.sort(key=lambda r: (r["date"], 0 if r["outside"] else 1, -(r["mcap_b"] or 0)))
    return out


def week_rows(timeline: Sequence[Mapping[str, Any]], day: date) -> list[dict[str, Any]]:
    monday = day - timedelta(days=day.weekday())
    weeks: dict[str, list[Mapping[str, Any]]] = {}
    for r in timeline:
        weeks.setdefault(r["week"], []).append(r)
    out = []
    for wk in sorted(weeks, key=lambda w: int(w[1:])):
        rs = weeks[wk]
        start = monday + timedelta(days=7 * int(wk[1:]))
        earn = [r for r in rs if r["kind"] == "earnings"]
        lane_earn = [r for r in earn if r["lanes"]]
        big = sorted(earn, key=lambda r: -(r["mcap_b"] or 0))[:8]
        outside, seen = [], set()
        for r in rs:
            if r["outside"] and r.get("detail") not in seen:
                seen.add(r.get("detail"))
                outside.append(f"{r['date'].strftime('%d %b')} {r['kind']}: {str(r.get('detail') or '')[:90]}")
        out.append({"week": wk, "start": start, "end": start + timedelta(days=4), "n_prints": len(earn), "n_lane_prints": len(lane_earn),
                    "lane_prints": ", ".join(f"{r['symbol']} {r['date'].strftime('%d')} ({r['lanes'].split(',')[0]})" for r in lane_earn[:14]),
                    "avoid_prints": ", ".join(f"{r['symbol']} {r['date'].strftime('%d')}" for r in earn if r["avoid"] and not r["lanes"])[:400],
                    "book_prints": ", ".join(f"{r['symbol']} {r['date'].strftime('%d %b')}" for r in rs if r["in_book"] and r["kind"] == "earnings"),
                    "big_prints": ", ".join(f"{r['symbol']} {r['date'].strftime('%d')}" for r in big),
                    "outside": " | ".join(outside)})
    return out


# --- build --------------------------------------------------------------------------------------------------


def build_lane_book(pack_dir: str | Path, *, recipe: Mapping[str, Any] | None = None, ids: Sequence[str] = (),
                    store: str | Path | None = None, calendar: str | Path | None = None) -> dict[str, Any]:
    import duckdb

    from generic_utils.deterioration import _resolve_ids

    recipe = dict(recipe or load_recipe())
    pack = Path(pack_dir)
    path = Path(store) if store else default_store_path(load_store_recipe())
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run tv_scan_cli.py panel-store first")
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH {_lit(str(path))} AS s (READ_ONLY)")
        frame = build_frame(con, recipe)
        day = frame["day"]
        lanes, months = lane_tables(con, frame, recipe)
        grid, spread = when_tables(con, frame, recipe)
        rotation, industries = rotation_tables(con, frame, recipe)
        capital, book, ind_exp = pack_capital(pack)
        board = board_rows(pack)
        symbols_today = [r[0] for r in con.execute("SELECT symbol FROM lb WHERE as_of = ?", [day]).fetchall()]
        named = [s for s in _resolve_ids([i.strip() for i in ids if i and i.strip()], symbols_today)]
        book_ids = [str(b.get("symbol")) for b in book if b.get("symbol")]
        book_ids += [s for s, r in board.items() if r.get("in_book") and s not in book_ids]
        known = {r[0] for r in con.execute("DESCRIBE lb").fetchall()}
        flag_cols = [f"l_{ln['key']}" for ln in frame["lanes"] if ln.get("side") != "base"]
        feats = [c for c in FEATURE_FIELDS if c in known]
        keep = f"({' OR '.join(qident(c) for c in flag_cols) or 'FALSE'}) OR symbol IN ({_lit(book_ids + named or ['-'])})"
        live = _query(con, f"SELECT symbol, ind, ai, round(mcap / 1e9, 1) AS mcap_b, {', '.join(qident(c) for c in feats)}, "
                           f"{', '.join(qident(c) for c in flag_cols)} FROM lb WHERE as_of = ? AND ({keep})", [day])
        cells = name_cells(con, frame, recipe, [r["symbol"] for r in live])
    finally:
        con.close()
    side = {ln["key"]: ln.get("side") for ln in frame["lanes"]}
    stat = {(r["lane"], r["split"]): r for r in lanes}
    when = {(r["split"], r["vq"], r["timing"]): r for r in grid if r["field"] == (recipe.get("when") or {}).get("value_fields", [None])[0]}
    min_best = int(recipe.get("min_rows_best") or 100)
    nav = _f(capital.get("nav_usd"))
    book_val = {str(b.get("symbol")): _f(b.get("value_usd")) for b in book}
    names = []
    for r in live:
        sym = r["symbol"]
        split = "ai" if r.get("ai") else "other"
        on = [c[2:] for c in flag_cols if r.get(c)]
        longs = [k for k in on if side.get(k) == "long"]
        avoid = [k for k in on if side.get(k) == "avoid"]
        best, best_row = None, {}
        for k in longs:
            st = stat.get((k, split)) or {}
            if (st.get("n_28") or 0) < min_best:
                st = stat.get((k, "all")) or {}
            if best is None or _or(_f(st.get("xs_28")), -99.0) > _or(_f(best_row.get("xs_28")), -99.0):
                best, best_row = k, st
        cell = cells.get(sym) or {}
        wc = when.get(("all", cell.get("vq"), cell.get("timing"))) or {}
        b = board.get(sym) or {}
        close = _f(b.get("close"))
        bh = _f(b.get("bounce_high"))
        entry = min(close, bh) if close and bh else close
        tgt = [v for v in (_f(b.get("b28d_q80")), _f(b.get("street_px"))) if v]
        up = round((min(tgt) / entry - 1) * 100, 1) if entry and tgt else None
        kill = _f(b.get("bounce_kill"))
        risk = round((1 - kill / entry) * 100, 1) if entry and kill else None
        value = _f(b.get("book_usd")) or book_val.get(sym)
        row = {"symbol": sym, "company": b.get("company"), "ind": r.get("ind"), "ai": 1 if r.get("ai") else 0, "mcap_b": r.get("mcap_b"),
               "in_book": 1 if (sym in book_ids) else 0, "book_usd": _r(value, 0), "nav_pct": _r(value / nav * 100, 2) if value and nav else None,
               "named": 1 if sym in named else 0, "lanes": ",".join(longs), "avoid": ",".join(avoid), "n_lanes": len(longs), "n_avoid": len(avoid),
               "best_lane": best, "best_xs_28": best_row.get("xs_28"), "best_p_up_28": best_row.get("p_up_28"),
               "best_p_dn_28": best_row.get("p_dn_28"), "best_t_28": best_row.get("t_28"), "best_n_28": best_row.get("n_28"),
               "vq": cell.get("vq"), "timing": cell.get("timing"), "cell_xs_28": wc.get("xs_28"), "cell_p_up_28": wc.get("p_up_28"),
               "cell_p_dn_28": wc.get("p_dn_28"), "cell_n_28": wc.get("n_28"),
               "entry_px": _r(entry), "month_up_pct": up, "risk_pct": risk,
               "payoff": round(up / risk, 2) if up is not None and risk and risk > 0 else None}
        for c in feats:
            row[c] = _r(r.get(c)) if isinstance(r.get(c), float) else r.get(c)
        for c in BOARD_FIELDS:
            if c in ("in_book", "book_usd") or c in row:
                continue
            v = b.get(c)
            row[c] = _r(v) if isinstance(v, float) else v
        for c in flag_cols:
            row[c] = 1 if r.get(c) else 0
        names.append(row)
    seen_syms = {n["symbol"] for n in names}
    for sym in [s for s in book_ids + named if s not in seen_syms]:
        # Off the store floor (a non-US Book line, a small named id): the board / pack row only, no lane.
        b = board.get(sym) or {}
        value = _f(b.get("book_usd")) or book_val.get(sym)
        row = {"symbol": sym, "company": b.get("company"), "ind": next((x.get("ind") for x in book if x.get("symbol") == sym), None),
               "ai": None, "mcap_b": None, "in_book": 1 if sym in book_ids else 0, "book_usd": _r(value, 0),
               "nav_pct": _r(value / nav * 100, 2) if value and nav else None, "named": 1 if sym in named else 0, "lanes": "", "avoid": "",
               "n_lanes": 0, "n_avoid": 0, "best_lane": "off the store floor"}
        for c in BOARD_FIELDS:
            if c not in ("in_book", "book_usd") and c not in row:
                v = b.get(c)
                row[c] = _r(v) if isinstance(v, float) else v
        names.append(row)
        seen_syms.add(sym)
    names.sort(key=lambda x: (-x["in_book"], -x["n_lanes"], -_or(_f(x.get("best_xs_28")), -99.0)))
    by_symbol = {n["symbol"]: n for n in names}
    horizon = int((recipe.get("timeline") or {}).get("horizon_days") or 42)
    timeline = timeline_rows(pack, day, horizon, by_symbol, Path(calendar) if calendar else CALENDAR)
    weeks = week_rows(timeline, day)
    nav_by_ind = {str(e.get("ind")): _f(e.get("nav_pct")) for e in ind_exp}
    lane_by_ind: dict[str, list[str]] = {}
    for n in names:
        if n["lanes"]:
            lane_by_ind.setdefault(str(n["ind"]), []).append(n["symbol"])
    for r in industries:
        r["book_nav_pct"] = nav_by_ind.get(str(r["ind"]))
        syms = lane_by_ind.get(str(r["ind"])) or []
        r["n_lane_names"] = len(syms)
        r["lane_names"] = ", ".join(syms[:8])
    cap = recipe.get("capital") or {}
    cash = _f(capital.get("cash_usd"))
    floor_pct = float(cap.get("cash_floor_pct") or 0)
    above = cash - nav * floor_pct / 100 if cash is not None and nav else None
    starter, add = float(cap.get("starter_usd") or 5000), float(cap.get("add_usd") or 2500)
    capital_rows = [{"nav_usd": _r(nav, 0), "cash_usd": _r(cash, 0), "cash_pct": _r(capital.get("cash_pct")), "equity_usd": _r(capital.get("equity_usd"), 0),
                     "unrealized_usd": _r(capital.get("unrealized_usd"), 0), "cash_floor_pct": floor_pct,
                     "cash_floor_usd": _r(nav * floor_pct / 100, 0) if nav else None, "above_floor_usd": _r(above, 0),
                     "starters_at_hint": int(above // starter) if above and above > 0 else 0, "starter_usd": starter,
                     "adds_at_hint": int(above // add) if above and above > 0 else 0, "add_usd": add, "book_lines": len(book)}]
    spec = {"pack": pack.as_posix(), "store": path.as_posix(), "day": day.isoformat(), "first_day": frame["first_day"].isoformat(),
            "last_labelled": {str(h): d.isoformat() for h, d in frame["last_labelled"].items() if d},
            "halves_split": {str(h): d.isoformat() for h, d in frame["mids"].items() if d}, "horizons": frame["horizons"],
            "big_move_pct": recipe.get("big_move_pct"), "n_lanes": len(frame["lanes"]), "n_names": len(names),
            "n_long_names": sum(1 for n in names if n["lanes"]), "n_avoid_names": sum(1 for n in names if n["avoid"]),
            "cluster_industries": frame["cluster_inds"], "cluster_ids": frame["cluster_ids"], "named_ids": named, "book_ids": book_ids,
            "board": bool(board), "timeline_days": horizon, "n_events": len(timeline)}
    return {"spec": spec, "lanes": lanes, "lane_months": months, "when_value": grid, "when_spread": spread, "rotation": rotation,
            "industries": industries, "names": names, "timeline": timeline, "weeks": weeks, "capital": capital_rows}


# --- write --------------------------------------------------------------------------------------------------


def highlight_lines(payload: Mapping[str, Any]) -> list[str]:
    spec = payload["spec"]
    out = [f"lane book {spec['day']} store {spec['first_day']}..{spec['day']} (28d labels to {spec['last_labelled'].get('28')}); "
           f"names={spec['n_names']} long={spec['n_long_names']} avoid={spec['n_avoid_names']} events={spec['n_events']}"]
    c = (payload.get("capital") or [{}])[0]
    out.append(f"capital nav=${c.get('nav_usd')} cash=${c.get('cash_usd')} ({c.get('cash_pct')}%) floor {c.get('cash_floor_pct')}% -> "
               f"above floor ${c.get('above_floor_usd')} = {c.get('starters_at_hint')} starters at ${c.get('starter_usd'):.0f}")
    out.append("lanes (split all): lane side n_28 days xs_7 xs_28 t_28 hit_28 p_up_28 p_dn_28 h1/h2 | live")
    for r in payload["lanes"]:
        if r["split"] != "all":
            continue
        out.append(f"  {r['lane']} {r['side']} n={r.get('n_28')} d={r.get('n_days')} xs7={r.get('xs_7')} xs28={r.get('xs_28')} t={r.get('t_28')} "
                   f"hit={r.get('hit_28')} up10={r.get('p_up_28')} dn10={r.get('p_dn_28')} h={r.get('xs_28_h1')}/{r.get('xs_28_h2')} | "
                   f"live {r.get('n_live')}: {str(r.get('live_top') or '')[:120]}")
    out.append("when (first field, split all): timing -> Q5 minus Q1 xs_7 / xs_28 / p_up_28")
    fields = [r["field"] for r in payload["when_spread"]]
    for r in payload["when_spread"]:
        if r["split"] == "all" and fields and r["field"] == fields[0]:
            out.append(f"  {r['timing']}: {r.get('xs_7_spread')} / {r.get('xs_28_spread')} / {r.get('p_up_28_spread')}")
    out.append("rotation precedent (quadrant): " + "; ".join(f"{r['cell']} xs28={r.get('xs_28')} hit={r.get('hit_28')} n={r.get('n_28')}"
                                                          for r in payload["rotation"] if r["dim"] == "quadrant"))
    lead = [r for r in payload["industries"] if r["quad"] in ("Leading", "Improving")][:10]
    out.append("industries leading / improving now: " + "; ".join(f"{r['ind']} {r['quad']} 1m {r['x_1m_xs']} pace {r['y_pace']}" for r in lead))
    for w in payload["weeks"][:3]:
        out.append(f"{w['week']} {w['start']}: prints {w['n_prints']} (lane {w['n_lane_prints']}) book [{w['book_prints']}] outside [{w['outside'][:220]}]")
    for n in payload["names"]:
        if n["in_book"] or n["named"]:
            out.append(f"  {n['symbol']} book=${n.get('book_usd')} lanes=[{n['lanes']}] avoid=[{n['avoid']}] best={n.get('best_lane')} "
                       f"xs28={n.get('best_xs_28')} cell={n.get('vq')}/{n.get('timing')} {n.get('cell_xs_28')}")
    return out


def write_lane_book(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    big = spec.get("big_move_pct")
    notes = [
        f"Store {spec['first_day']} to {spec['day']}, US floor (recipe floor); labels: " + ", ".join(f"{h}d to {d}" for h, d in spec["last_labelled"].items())
        + ". A lane is one SQL condition on the store's features (recipe lanes / defs); the same condition gives its record and today's names.",
        f"lanes / lane_months: per lane x split (all / ai = the AI cluster / other): n_h rows with an h-day label, fwd_h raw mean (clipped), med_h, "
        f"xs_h = mean excess over the day's floor median, hit_h = share beating it, p_up_h / p_dn_h = odds of a raw move of +{big}% / -{big}% or more, "
        f"q10_h / q90_h, t_h = t across calendar blocks of h days (n_blk_h), xs_h_h1 / _h2 = the store's halves (split {spec['halves_split']}).",
        "when_value: value field x split x value quintile (per day; Q5 = highest: cheapest for a yield, most street upside for pt_up) x catalyst "
        "timing bucket (dsp = days since the last print, dte = days to the next; first matching bucket) and 'any'; when_spread = Q5 minus Q1.",
        "rotation: every past (day, industry with min_names): x = cap-weighted 1m return minus the floor's, y = the week's excess minus week_share x x; "
        "quadrant Leading / Weakening / Lagging / Improving and the x quintile -> the industry's names' next h-day excess (equal weight). "
        "industries: today's rows with breadth, revisions, value level vs the floor, the Book's NAV share and live lane names.",
        "names: live names in a long or avoid lane plus the Book and named ids; best_lane = the long lane with the highest measured xs_28 for the "
        "name's split (all when the split has under min_rows_best rows); vq / timing = today's cell on the first value field, cell_* its record; "
        "entry_px = min(close, bounce_high); month_up_pct = min(b28d_q80, street_px) / entry_px - 1; risk_pct = 1 - bounce_kill / entry_px; payoff = up / risk.",
        "timeline / weeks: PACK/outlook events plus catalyst_calendar.json rows the pack has not seen, with the touched name's lanes; capital: the "
        "cash above the recipe floor and the starters / adds it buys at the hint sizes.",
        "In-sample, overlapping windows, mostly one regime: frequencies with their n, not forecasts. No composite, no ranking across lanes, no course.",
    ]
    exported = write_run_export(out_dir, tool="generic_utils.lane_book",
                                tables={k: payload[k] for k in ("lanes", "lane_months", "names", "when_value", "when_spread", "rotation",
                                                                "industries", "timeline", "weeks", "capital")},
                                sources={"pack": spec["pack"], "store": spec["store"], "calendar": CALENDAR.as_posix()}, notes=notes,
                                extra={"spec": spec}, duckdb_name="lane_book.duckdb")
    hl = Path(out_dir) / "highlights.log"
    hl.write_text("\n".join(highlight_lines(payload)) + "\n", encoding="utf-8")
    exported["highlights"] = hl.as_posix()
    return exported


def lane_book_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_lane_book(build_lane_book(pack), pack / "lane_book"))


if __name__ == "__main__":
    lane_book_local_main()
