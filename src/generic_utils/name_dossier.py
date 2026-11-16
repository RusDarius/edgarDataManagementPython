"""Name dossier (the deep-dive agent's data): one dossier per name (or a suite of names) from everything the project measures, for the agent to put
outside context on.

For each requested id (``EXCHANGE:TICKER``, bare tickers qualified by ``symbol_resolver``, a ``themes.json`` theme adds
its ids) it gathers, read-only:

- **Now**: the pack's widest row (board: tape, bounce band, entry book, catalysts, beat bar, alpha model, value, peers,
  deterioration, event risk), plus the pack's forward value bear / base / bull, the alpha bands and their out-of-sample
  state, peer value, cycle scenarios, event risk crowd, print night, the stance helper and a few all-fields fields.
- **Progression** over the panel store period (daily since 29 Mar 2026): price and averages, valuation, street targets,
  estimates, margins, revisions and model scores; a per-metric first / last / range summary and checkpoints.
- **Movement anatomy**: split-guarded index, largest drawdown and run-up with dates, time above the 50 / 200-day, volume
  spikes by direction, the largest moves (and whether a print caused them), the name's print history.
- **Analogs** (targeted movement): the nearest US $2B+ name-days to the name's newest store row (features ranked within
  each day, distance over the ranks) and what they did over 7 / 14 / 28 / 56 days, raw and in excess of the day's
  median name, with the distinct days behind them; turned into a price path (close x analog quantile) beside the alpha
  model's band.
- **Evidence** per horizon class (short / mid / long): one measured direction per item from fixed sign rules, counted.
- **Research plan**: query templates the agent runs for the outside read (OUTSIDE, with sources).

``summary`` puts the names side by side in the requested order. No 0-100, no ranking, no course, no cutoff on the
names: arrows are counts, the analog quantiles are measured, the verdict and the horizon class are the agent's
(``dossier_page`` renders them from a judgment file). Not the watchlist deep-dive page (generic_utils.deep_dive).
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from generic_utils.symbol_identity import parse_symbol

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "name_dossier.json"


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _f(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _r(value: Any, digits: int = 2) -> float | None:
    v = _f(value)
    return round(v, digits) if v is not None else None


def _lit(values: Iterable[str]) -> str:
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values) or "''"


def _qi(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:48] or "dossier"


# --- ids --------------------------------------------------------------------------------------------------


def pack_dir(pack: str | Path | None = None) -> Path:
    if pack and str(pack) != "latest":
        p = Path(pack)
        return p if p.is_absolute() else REPO_ROOT / p
    from operator_briefing.discovery import latest_briefing_pack_path

    found = latest_briefing_pack_path()
    if found is None:
        raise FileNotFoundError("no briefing pack found: run the daily refresh first")
    return Path(found).parent


def theme_ids(theme: str | None) -> list[str]:
    if not theme:
        return []
    themes = json.loads((REPO_ROOT / "config" / "generic_utils" / "themes.json").read_text(encoding="utf-8"))
    if theme not in themes:
        raise KeyError(f"theme {theme!r} not in themes.json; known: {sorted(k for k in themes if not k.startswith('_'))}")
    return [str(i) for i in (themes[theme].get("ids") or [])]


def resolve_ids(ids: Sequence[str], theme: str | None = None) -> tuple[list[str], list[str]]:
    """Qualified current ids in request order (aliases followed, bare tickers qualified on the newest all-fields run),
    and notes for ids that stayed bare."""
    from generic_utils.symbol_resolver import canonical, load_aliases, newest_all_fields, qualify_bare

    wanted = [s.strip() for s in list(ids) + theme_ids(theme) if s and s.strip()]
    aliases = load_aliases()
    bare = sorted({parse_symbol(s).normalized for s in wanted if not parse_symbol(s).qualified})
    quals: dict[str, tuple[str | None, str]] = {}
    if bare:
        db, run = newest_all_fields()
        quals = qualify_bare(bare, db, run) if db is not None and run is not None else {}
    out, notes = [], []
    for s in wanted:
        sid = parse_symbol(s)
        full = sid.normalized if sid.qualified else (quals.get(sid.normalized) or (None, "not_found"))[0]
        if not full:
            notes.append(f"{s}: not qualified ({(quals.get(sid.normalized) or (None, 'not_found'))[1]})")
            continue
        cur = canonical(full, aliases)
        if cur != full:
            notes.append(f"{full} -> {cur} (alias)")
        if cur not in out:
            out.append(cur)
    return out, notes


# --- joins ------------------------------------------------------------------------------------------------


def _read_rows(path: Path, table: str | None, fields: Any, ids: Sequence[str], prefix: str = "") -> dict[str, dict[str, Any]]:
    """{symbol: row} from a DuckDB table or a CSV for ``ids``: the listed fields that exist ('*' = all), prefixed."""
    import duckdb

    if not path.exists() or not ids:
        return {}
    con = duckdb.connect(str(path), read_only=True) if path.suffix == ".duckdb" else duckdb.connect()
    try:
        src = _qi(str(table)) if path.suffix == ".duckdb" else f"read_csv_auto('{path.as_posix()}', header=true)"
        cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
        if "symbol" not in cols:
            return {}
        want = cols if fields == "*" else [c for c in fields if c in cols]
        sel = ", ".join(_qi(c) for c in ["symbol", *[c for c in want if c != "symbol"]])
        cur = con.execute(f"SELECT {sel} FROM {src} WHERE symbol IN ({_lit(ids)})")
        names = [d[0] for d in cur.description]
        out: dict[str, dict[str, Any]] = {}
        for values in cur.fetchall():
            rec = {(k if k == "symbol" else prefix + k): _iso(v) for k, v in zip(names, values)}
            out.setdefault(str(rec["symbol"]), rec)
        return out
    finally:
        con.close()


def load_joins(pack: Path, ids: Sequence[str], recipe: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Every recipe join merged per symbol (a later join fills only empty cells). Returns (rows, {join: path|None})."""
    rows: dict[str, dict[str, Any]] = {s: {"symbol": s} for s in ids}
    used: dict[str, Any] = {}
    for spec in recipe.get("joins") or []:
        path = Path(str(spec["path"]).replace("{PACK}", pack.as_posix()))
        path = path if path.is_absolute() else REPO_ROOT / path
        found = _read_rows(path, spec.get("table"), spec.get("fields") or "*", ids, str(spec.get("prefix") or ""))
        used[str(spec["name"])] = {"path": path.as_posix() if path.exists() else None, "n": len(found)}
        for sym, rec in found.items():
            for k, v in rec.items():
                if rows[sym].get(k) in (None, ""):
                    rows[sym][k] = v
    return rows, used


def alpha_states(pack: Path, recipe: Mapping[str, Any]) -> dict[str, str]:
    path = Path(str(recipe.get("alpha_state") or "").replace("{PACK}", pack.as_posix()))
    path = path if path.is_absolute() else REPO_ROOT / path
    if not path.exists():
        return {}
    from generic_utils.scan_sources import rows_from_csv

    return {str(r.get("horizon")): str(r.get("state")) for r in rows_from_csv(path)}


def all_fields_extra(ids: Sequence[str], fields: Sequence[str]) -> dict[str, dict[str, Any]]:
    """A few descriptive fields from the newest all-fields run (narrow SELECT; only fields that exist)."""
    import duckdb

    from generic_utils.symbol_resolver import newest_all_fields

    db, run = newest_all_fields()
    if db is None or run is None or not ids:
        return {}
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'all_fields_rows'").fetchall()}
        want = [c for c in fields if c in cols]
        cur = con.execute(f"SELECT symbol, {', '.join(_qi(c) for c in want) or 'NULL AS none'} FROM all_fields_rows "
                          f"WHERE run_id = ? AND symbol IN ({_lit(ids)})", [run])
        names = [d[0] for d in cur.description]
        return {str(r[0]): {f"af_{k}": v for k, v in zip(names[1:], r[1:])} for r in cur.fetchall()}
    finally:
        con.close()


def stance_rows(ids: Sequence[str], pack: Path) -> dict[str, dict[str, Any]]:
    try:
        from operator_briefing.lookup import lookup_symbols

        found = lookup_symbols(list(ids), pack_path=str(pack / "briefing_pack.json"))
    except Exception:  # an older pack: the dossier goes on without the stance
        return {}
    out = {}
    for d in found.get("dossiers") or []:
        st = d.get("stance") or {}
        out[str(d.get("symbol"))] = {"stance": st.get("stance"), "support": st.get("suggested_conviction"),
                                     "conflicts": "; ".join(st.get("conflicts") or []) or None}
    return out


# --- store ------------------------------------------------------------------------------------------------


def progression(con: Any, symbol: str, recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    pf = recipe["progression_fields"]
    have_p = {r[0] for r in con.execute("DESCRIBE panel").fetchall()}
    have_f = {r[0] for r in con.execute("DESCRIBE features").fetchall()}
    sel = [f"p.{_qi(c)}" for c in pf.get("panel") or [] if c in have_p]
    sel += [f"f.{_qi(c)}" for c in pf.get("features") or [] if c in have_f and c not in (pf.get("panel") or [])]
    cur = con.execute(f"""SELECT p.as_of, f.split_seq, {', '.join(sel)} FROM panel p
                          LEFT JOIN features f ON f.symbol = p.symbol AND f.as_of = p.as_of
                          WHERE p.symbol = ? ORDER BY p.as_of""", [symbol])
    names = [d[0] for d in cur.description]
    return [{k: _iso(v) for k, v in zip(names, r)} for r in cur.fetchall()]


def metric_summary(rows: Sequence[Mapping[str, Any]], metrics: Sequence[str]) -> list[dict[str, Any]]:
    out = []
    for m in metrics:
        pts = [(r["as_of"], _f(r.get(m))) for r in rows if _f(r.get(m)) is not None]
        if not pts:
            continue
        vals = [v for _, v in pts]
        lo, hi = min(vals), max(vals)
        first, last = vals[0], vals[-1]
        out.append({"metric": m, "first_on": pts[0][0], "first": _r(first, 4), "last_on": pts[-1][0], "last": _r(last, 4),
                    "chg": _r(last - first, 4), "chg_pct": _r((last / first - 1) * 100) if first not in (0, None) and first > 0 and last >= 0 else None,
                    "min": _r(lo, 4), "max": _r(hi, 4), "pos_in_range": _r((last - lo) / (hi - lo) * 100, 1) if hi > lo else None, "n": len(vals)})
    return out


def checkpoints(rows: Sequence[Mapping[str, Any]], every: int, fields: Sequence[str]) -> list[dict[str, Any]]:
    if not rows:
        return []
    idx = sorted(set(list(range(0, len(rows), max(1, every))) + [len(rows) - 1]))
    return [{"as_of": rows[i]["as_of"], **{f: _r(rows[i].get(f), 3) for f in fields}} for i in idx]


def step_returns(rows: Sequence[Mapping[str, Any]]) -> list[float | None]:
    """Close-to-close % per row (first row None); a split step (split_seq change) is None, not a crash."""
    out: list[float | None] = [None]
    for prev, cur in zip(rows, rows[1:]):
        a, b = _f(prev.get("close")), _f(cur.get("close"))
        split = prev.get("split_seq") != cur.get("split_seq") and prev.get("split_seq") is not None and cur.get("split_seq") is not None
        out.append(None if (a in (None, 0) or b is None or split) else (b / a - 1) * 100)
    return out


def anatomy(rows: Sequence[Mapping[str, Any]], prints: Sequence[Mapping[str, Any]], recipe: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(one anatomy row, the largest moves) on the split-guarded index."""
    if len(rows) < 2:
        return {}, []
    rets = step_returns(rows)
    idx, level = [], 100.0
    for r in rets:
        level *= 1 + (r or 0.0) / 100
        idx.append(level)
    days = [r["as_of"] for r in rows]
    # Largest drawdown: running peak to a later trough.
    peak_i, dd, dd_pk, dd_tr = 0, 0.0, 0, 0
    for i, v in enumerate(idx):
        if v > idx[peak_i]:
            peak_i = i
        cur = (v / idx[peak_i] - 1) * 100
        if cur < dd:
            dd, dd_pk, dd_tr = cur, peak_i, i
    trough_i, ru, ru_tr, ru_pk = 0, 0.0, 0, 0
    for i, v in enumerate(idx):
        if v < idx[trough_i]:
            trough_i = i
        cur = (v / idx[trough_i] - 1) * 100
        if cur > ru:
            ru, ru_tr, ru_pk = cur, trough_i, i
    hi_i = int(np.argmax(idx))
    a50 = [r for r in rows if _f(r.get("close")) is not None and _f(r.get("sma50")) is not None]
    a200 = [r for r in rows if _f(r.get("close")) is not None and _f(r.get("sma200")) is not None]
    spike = float((recipe.get("moves") or {}).get("spike_relvol", 2.0))
    ups = sum(1 for r, ret in zip(rows, rets) if ret is not None and ret > 0 and (_f(r.get("relvol")) or 0) >= spike)
    dns = sum(1 for r, ret in zip(rows, rets) if ret is not None and ret < 0 and (_f(r.get("relvol")) or 0) >= spike)
    fin = [x for x in rets if x is not None]
    print_days = {str(p.get("react_file")) for p in prints}
    row = {"first_on": days[0], "last_on": days[-1], "n_days": len(rows), "period_ret_pct": _r(idx[-1] - 100),
           "max_dd_pct": _r(dd), "max_dd_peak": days[dd_pk], "max_dd_trough": days[dd_tr],
           "max_runup_pct": _r(ru), "max_runup_low": days[ru_tr], "max_runup_high": days[ru_pk],
           "dd_now_pct": _r((idx[-1] / idx[hi_i] - 1) * 100), "period_high_on": days[hi_i],
           "above50_pct": _r(sum(1 for r in a50 if _f(r["close"]) > _f(r["sma50"])) / len(a50) * 100, 1) if a50 else None,
           "above200_pct": _r(sum(1 for r in a200 if _f(r["close"]) > _f(r["sma200"])) / len(a200) * 100, 1) if a200 else None,
           "up_days_pct": _r(sum(1 for x in fin if x > 0) / len(fin) * 100, 1) if fin else None,
           "day_sd_pct": _r(float(np.std(fin)), 2) if len(fin) > 2 else None,
           "spikes_up": ups, "spikes_dn": dns, "n_prints": len(prints)}
    order = sorted([i for i, x in enumerate(rets) if x is not None], key=lambda i: -abs(rets[i]))
    moves = [{"as_of": days[i], "move_pct": _r(rets[i]), "close": _r(rows[i].get("close")), "relvol": _r(rows[i].get("relvol")),
              "print_day": str(days[i]) in print_days} for i in order[: int((recipe.get("moves") or {}).get("top_n", 8))]]
    return row, sorted(moves, key=lambda m: m["as_of"])


def print_history(con: Any, symbol: str) -> list[dict[str, Any]]:
    cur = con.execute("""SELECT print_date, slot, react_file, eps_surprise, rev_surprise, runup, react, react_relvol, drift
                         FROM prints WHERE symbol = ? ORDER BY print_date""", [symbol])
    names = [d[0] for d in cur.description]
    return [{k: (_iso(v) if not isinstance(v, float) else _r(v)) for k, v in zip(names, r)} for r in cur.fetchall()]


# --- analogs ----------------------------------------------------------------------------------------------


def analog_frame(con: Any, recipe: Mapping[str, Any], extra: Sequence[str]) -> dict[str, Any]:
    """Every floor name-day (plus ``extra`` ids): its features ranked within the day (0-1) and its forward labels, raw
    and less the day's median floor name."""
    an = recipe["analogs"]
    have = {r[0] for r in con.execute("DESCRIBE features").fetchall()}
    feats = [f for f in an["features"] if f in have]
    hz = [(n, c) for n, c in an["horizons"]]
    floor = float(recipe.get("floor_mcap") or 0)
    ex = _lit(recipe.get("exchanges") or [])
    ranks = ", ".join(f"CASE WHEN {_qi(f)} IS NULL THEN NULL ELSE percent_rank() OVER (PARTITION BY as_of, ({_qi(f)} IS NULL) "
                      f"ORDER BY {_qi(f)}) END AS q_{f}" for f in feats)
    fwd = ", ".join(f"l.{c}" for _, c in hz)
    xs = ", ".join(f"l.{c} - median(l.{c}) OVER (PARTITION BY l.as_of) AS xs_{c}" for _, c in hz)
    sql = f"""
        WITH fl AS (SELECT symbol, as_of, ind, {', '.join(_qi(f) for f in feats)} FROM features
                    WHERE (mcap >= {floor!r} AND exchange IN ({ex})) OR symbol IN ({_lit(extra)})),
        rk AS (SELECT symbol, as_of, ind, {ranks} FROM fl),
        lb AS (SELECT l.symbol, l.as_of, {fwd}, {xs} FROM labels l JOIN fl USING (symbol, as_of))
        SELECT rk.*, {', '.join(f'lb.{c}' for _, c in hz)}, {', '.join(f'lb.xs_{c}' for _, c in hz)}
        FROM rk LEFT JOIN lb USING (symbol, as_of)"""
    data = con.execute(sql).fetchnumpy()
    Q = np.column_stack([_num(data[f"q_{f}"]) for f in feats]) if feats else np.zeros((0, 0))
    days = np.array([str(v)[:10] for v in np.asarray(data["as_of"])], dtype=object)
    return {"feats": feats, "horizons": hz, "Q": Q, "sym": np.asarray(data["symbol"], dtype=object),
            "as_of": days, "ind": np.asarray(data["ind"], dtype=object),
            "fwd": {c: _num(data[c]) for _, c in hz}, "xs": {c: _num(data[f"xs_{c}"]) for _, c in hz}}


def _num(values: Any) -> np.ndarray:
    """Float array with NULLs as NaN: DuckDB's fetchnumpy returns masked arrays, and np.asarray would turn a NULL into 0."""
    if isinstance(values, np.ma.MaskedArray):
        return values.astype(float).filled(np.nan)
    return np.asarray(values, dtype=float)


def analogs_for(frame: Mapping[str, Any], symbol: str, recipe: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(rows per horizon x scope, flat fields an_<h>_q50 / an_<h>_xs_q50 / ... for the name)."""
    an = recipe["analogs"]
    sym, Q = frame["sym"], frame["Q"]
    mine = np.where(sym == symbol)[0]
    if not len(mine) or Q.size == 0:
        return [], {}
    t = mine[np.argmax(frame["as_of"][mine])]
    target = Q[t]
    use = np.isfinite(target)
    if use.sum() < 3:
        return [], {"an_note": "fewer than 3 ranked features on the newest store row"}
    D = np.sqrt(np.nansum((Q[:, use] - target[use]) ** 2, axis=1))
    ok = np.isfinite(Q[:, use]).all(axis=1) & (sym != symbol)
    qs = list(an.get("quantiles") or [0.1, 0.25, 0.5, 0.75, 0.9])
    rows, flat = [], {"an_as_of": _iso(frame["as_of"][t]), "an_features": ", ".join(f for f, u in zip(frame["feats"], use) if u)}
    for scope, k, mask in (("all", int(an.get("k", 250)), ok), ("industry", int(an.get("k_ind", 60)), ok & (frame["ind"] == frame["ind"][t]))):
        for name, col in frame["horizons"]:
            fwd, xs = frame["fwd"][col], frame["xs"][col]
            cand = np.where(mask & np.isfinite(fwd))[0]
            if len(cand) < 10:
                continue
            near = cand[np.argsort(D[cand])[:k]]
            f, x = fwd[near], xs[near]
            rec = {"symbol": symbol, "scope": scope, "horizon": name, "n": int(len(near)),
                   "n_dates": int(len(set(frame["as_of"][near].tolist()))), "dist_med": _r(float(np.median(D[near])), 3),
                   "hit_pct": _r(float((f > 0).mean() * 100), 1), "xs_hit_pct": _r(float((x > 0).mean() * 100), 1),
                   "xs_q50": _r(float(np.nanmedian(x)))}
            for q in qs:
                rec[f"q{int(q * 100)}"] = _r(float(np.quantile(f, q)))
            rows.append(rec)
            if scope == "all":
                flat.update({f"an_{name}_q{int(q * 100)}": rec[f"q{int(q * 100)}"] for q in qs})
                flat.update({f"an_{name}_xs_q50": rec["xs_q50"], f"an_{name}_hit": rec["hit_pct"], f"an_{name}_n_dates": rec["n_dates"]})
    return rows, flat


def analog_base(frame: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every floor name-day with a label: the unconditional distribution the analogs are read against."""
    out = []
    for name, col in frame["horizons"]:
        f = frame["fwd"][col]
        f = f[np.isfinite(f)]
        if not len(f):
            continue
        out.append({"symbol": "BASE (all floor name-days)", "scope": "base", "horizon": name, "n": int(len(f)),
                    "n_dates": int(len(set(frame["as_of"][np.isfinite(frame["fwd"][col])].tolist()))), "hit_pct": _r(float((f > 0).mean() * 100), 1),
                    "xs_q50": 0.0, **{f"q{int(q * 100)}": _r(float(np.quantile(f, q))) for q in (0.1, 0.25, 0.5, 0.75, 0.9)}})
    return out


# --- derived ----------------------------------------------------------------------------------------------


def _cond(value: Any, rule: str | None) -> bool:
    if not rule:
        return False
    rule = str(rule).strip()
    if rule.startswith("in "):
        return str(value) in rule[3:].split("|")
    v = _f(value)
    if v is None:
        return False
    m = re.match(r"^(>=|<=|>|<|=)\s*(-?[\d.]+)$", rule)
    if not m:
        raise ValueError(f"bad sign rule {rule!r}")
    op, x = m.group(1), float(m.group(2))
    return {">": v > x, "<": v < x, ">=": v >= x, "<=": v <= x, "=": v == x}[op]


def evidence(row: Mapping[str, Any], recipe: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """One direction per sign rule ('up' / 'down' / 'flat' / 'n/a') and the counts per horizon class."""
    rows, counts = [], defaultdict(int)
    for rule in (recipe.get("evidence") or {}).get("rules") or []:
        value = row.get(rule["field"])
        if value in (None, ""):
            direction = "n/a"
        elif _cond(value, rule.get("up")):
            direction = "up"
        elif _cond(value, rule.get("down")):
            direction = "down"
        else:
            direction = "flat"
        if direction in ("up", "down"):
            counts[f"{rule['horizon']}_{'up' if direction == 'up' else 'dn'}"] += 1
        rows.append({"symbol": row["symbol"], "horizon": rule["horizon"], "item": rule["item"], "field": rule["field"],
                     "value": _r(value, 3) if _f(value) is not None else value, "direction": direction,
                     "rule": f"up {rule.get('up') or '-'} / down {rule.get('down') or '-'}"})
    return rows, dict(counts)


def levels(row: Mapping[str, Any]) -> dict[str, Any]:
    close = _f(row.get("close"))

    def pct(px: Any) -> float | None:
        p = _f(px)
        return _r((p / close - 1) * 100) if p is not None and close else None

    # A kill or band set when the price was higher can sit above the close (already broken in a selloff): the live
    # levels are then the bounce band and its kill. Both are kept; *_live says which one stands and why.
    kill, bkill = _f(row.get("kill_px")), _f(row.get("bounce_kill"))
    e_lo, e_hi, b_lo, b_hi = (_f(row.get(k)) for k in ("entry_low", "entry_high", "bounce_low", "bounce_high"))
    if close and kill is not None and kill < close:
        kill_live, kill_src = kill, "kill_px"
    elif close and bkill is not None and bkill < close:
        kill_live, kill_src = bkill, "bounce_kill (kill_px above the close)" if kill is not None else "bounce_kill"
    else:
        kill_live, kill_src = None, "none under the close"
    if close and e_lo is not None and e_lo <= close:
        band_live, band_src = (e_lo, e_hi), "entry book"
    elif b_lo is not None:
        band_live, band_src = (b_lo, b_hi), "bounce band (entry book above the close)" if e_lo is not None else "bounce band"
    else:
        band_live, band_src = (None, None), "none"
    return {"symbol": row["symbol"], "close": _r(close),
            "band_live_low": _r(band_live[0]), "band_live_high": _r(band_live[1]), "band_src": band_src,
            "kill_live": _r(kill_live), "kill_src": kill_src, "to_kill_live_pct": pct(kill_live),
            "entry_low": _r(row.get("entry_low")), "entry_high": _r(row.get("entry_high")),
            "bounce_low": _r(row.get("bounce_low")), "bounce_high": _r(row.get("bounce_high")), "band_state": row.get("band_state"),
            "kill_px": _r(row.get("kill_px")), "bounce_kill": _r(row.get("bounce_kill")), "first_target": _r(row.get("first_target")),
            "street_px": _r(row.get("street_px")), "fv_bear_px": _r(row.get("fv_bear_px")), "fv_base_px": _r(row.get("fv_base_px")),
            "fv_bull_px": _r(row.get("fv_bull_px")), "b28d_q20": _r(row.get("b28d_q20")), "b28d_q80": _r(row.get("b28d_q80")),
            "to_target_pct": pct(row.get("first_target")), "to_kill_pct": pct(row.get("kill_px")),
            "to_street_pct": pct(row.get("street_px")), "to_fv_base_pct": pct(row.get("fv_base_px"))}


def trajectory(row: Mapping[str, Any], states: Mapping[str, str], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    close = _f(row.get("close"))
    out = []
    for name, _ in recipe["analogs"]["horizons"]:
        rec: dict[str, Any] = {"symbol": row["symbol"], "horizon": name}
        for q in (10, 25, 50, 75, 90):
            ret = _f(row.get(f"an_{name}_q{q}"))
            rec[f"px_q{q}"] = _r(close * (1 + ret / 100)) if (close and ret is not None) else None
        rec["an_q50"] = row.get(f"an_{name}_q50")
        rec["an_xs_q50"] = row.get(f"an_{name}_xs_q50")
        rec["an_hit"] = row.get(f"an_{name}_hit")
        rec["an_n_dates"] = row.get(f"an_{name}_n_dates")
        h = name.rstrip("d")
        rec["alpha_q20"], rec["alpha_q50"], rec["alpha_q80"] = (_r(row.get(f"b{h}d_q{q}")) for q in (20, 50, 80))
        rec["alpha_pred"] = _r(row.get(f"pred_{h}d"), 3)
        rec["alpha_state"] = states.get(name)
        out.append(rec)
    return out


def research_plan(row: Mapping[str, Any], recipe: Mapping[str, Any], question: str | None) -> list[dict[str, Any]]:
    company = str(row.get("company") or row.get("af_description") or parse_symbol(row["symbol"]).ticker)
    company = re.sub(r",?\s+(Inc\.?|Corp\.?|Corporation|Ltd\.?|plc|N\.V\.|S\.A\.|Holdings?)$", "", company).strip()
    ctx = {"company": company, "ticker": parse_symbol(row["symbol"]).ticker, "symbol": row["symbol"], "industry": row.get("ind") or "",
           "year": str(date.today().year), "next_earn": row.get("earn_date") or row.get("next_date") or "", "question": question or ""}
    out = [{"symbol": row["symbol"], "query": t.format(**ctx)} for t in (recipe.get("research") or {}).get("per_name") or []]
    return out


SUMMARY_FIELDS = ("company", "ind", "mcap_b", "in_book", "book_usd", "close", "m1", "m3", "r_12m", "warn_level", "er_letters", "next_kind", "next_date",
                  "earn_date", "earn_slot", "band_state", "pred_28d", "dec_28d", "an_28d_q50", "an_28d_xs_q50", "an_56d_xs_q50", "an_28d_n_dates",
                  "fv_base_px", "fv_upside_pct", "irr_exp_pct", "hurdle_pct", "street_left", "peer_gap_pct", "peer_prem_pct", "det", "stance")


def build_dossier(ids: Sequence[str], *, pack: str | Path | None = None, store: str | Path | None = None,
                    recipe: Mapping[str, Any] | None = None, question: str | None = None, theme: str | None = None,
                    label: str | None = None) -> dict[str, Any]:
    import duckdb

    from generic_utils.panel_store import default_store_path, load_store_recipe

    recipe = dict(recipe or load_recipe())
    pk = pack_dir(pack)
    symbols, notes = resolve_ids(ids, theme)
    if not symbols:
        raise ValueError(f"no id resolved from {list(ids)} / theme {theme!r}: {notes}")
    rows, used = load_joins(pk, symbols, recipe)
    states = alpha_states(pk, recipe)
    for sym, extra in all_fields_extra(symbols, recipe.get("all_fields_fields") or []).items():
        rows[sym].update({k: v for k, v in extra.items() if v not in (None, "")})
    for sym, st in stance_rows(symbols, pk).items():
        if sym in rows:
            rows[sym].update(st)

    store_path = Path(store) if store else Path(default_store_path(load_store_recipe()))
    prog_rows, summ_rows, check_rows, anat_rows, move_rows, print_rows, analog_rows, base_rows = [], [], [], [], [], [], [], []
    store_span = None
    if store_path.exists():
        con = duckdb.connect(str(store_path), read_only=True)
        try:
            store_span = [_iso(v) for v in con.execute("SELECT min(as_of), max(as_of) FROM panel").fetchone()]
            frame = analog_frame(con, recipe, symbols)
            base_rows = analog_base(frame)
            for sym in symbols:
                prog = progression(con, sym, recipe)
                prints = print_history(con, sym)
                prog_rows += [{"symbol": sym, **r} for r in prog]
                summ_rows += [{"symbol": sym, **r} for r in metric_summary(prog, recipe.get("summary_metrics") or [])]
                check_rows += [{"symbol": sym, **r} for r in checkpoints(prog, int(recipe.get("checkpoint_every") or 10), recipe.get("checkpoint_fields") or [])]
                an_row, moves = anatomy(prog, prints, recipe)
                if an_row:
                    anat_rows.append({"symbol": sym, **an_row})
                move_rows += [{"symbol": sym, **m} for m in moves]
                print_rows += [{"symbol": sym, **p} for p in prints]
                a_rows, flat = analogs_for(frame, sym, recipe)
                analog_rows += a_rows
                rows[sym].update(flat)
                if prog and rows[sym].get("close") in (None, ""):
                    rows[sym]["close"] = prog[-1].get("close")
        finally:
            con.close()
    else:
        notes.append(f"panel store missing ({store_path}): no progression, anatomy or analogs")

    level_rows, traj_rows, ev_rows, summary, plan_rows, peer_rows, event_rows = [], [], [], [], [], [], []
    peer_all = _peer_rows(pk, symbols)
    events = _event_rows(pk, symbols)
    for sym in symbols:
        row = rows[sym]
        irr, hurdle = _f(row.get("irr_exp_pct")), _f(row.get("hurdle_pct"))
        row["irr_minus_hurdle"] = _r(irr - hurdle) if irr is not None and hurdle is not None else None
        if not row.get("company") and row.get("af_description"):
            row["company"] = row["af_description"]
        lv = levels(row)
        level_rows.append(lv)
        row.update({k: v for k, v in lv.items() if k.startswith("to_") or k.endswith("_live") or k.startswith("band_live") or k in ("kill_src", "band_src")})
        traj_rows += trajectory(row, states, recipe)
        ev, counts = evidence(row, recipe)
        ev_rows += ev
        row.update({f"{h}_{d}": counts.get(f"{h}_{d}", 0) for h in ("short", "mid", "long") for d in ("up", "dn")})
        plan_rows += research_plan(row, recipe, question)
        summary.append({"symbol": sym, **{k: (_r(row.get(k)) if _f(row.get(k)) is not None and k not in ("in_book",) else row.get(k)) for k in SUMMARY_FIELDS},
                        "to_target_pct": row.get("to_target_pct"), "to_kill_pct": row.get("to_kill_pct"),
                        "band_live_low": row.get("band_live_low"), "band_live_high": row.get("band_live_high"), "kill_live": row.get("kill_live"),
                        "kill_src": row.get("kill_src"), "to_kill_live_pct": row.get("to_kill_live_pct"),
                        "to_street_pct": row.get("to_street_pct"), "to_fv_base_pct": row.get("to_fv_base_pct"),
                        **{f"{h}_{d}": row[f"{h}_{d}"] for h in ("short", "mid", "long") for d in ("up", "dn")},
                        "in_store": any(p["symbol"] == sym for p in prog_rows), "in_board": bool(row.get("ind"))})
        peer_rows += peer_all.get(sym, [])
        event_rows += events.get(sym, [])
        if not row.get("ind"):
            notes.append(f"{sym}: not on the pack's board (outside the US $2B+ outlook universe): the dossier has store / all-fields data only")
    q_plan = [{"symbol": "QUESTION", "query": t.format(question=question)} for t in (recipe.get("research") or {}).get("question") or []] if question else []
    created = datetime.now(timezone.utc)
    spec = {"label": label or (slug(question) if question else slug("-".join(parse_symbol(s).ticker for s in symbols[:6]))),
            "created_utc": created.strftime("%Y-%m-%d %H:%M UTC"), "ids": symbols, "requested": list(ids), "theme": theme,
            "question": question, "pack": pk.as_posix(), "store": store_path.as_posix(), "store_span": store_span,
            "joins": used, "alpha_states": states, "notes": notes}
    snapshot = [{k: v for k, v in rows[s].items()} for s in symbols]
    return {"spec": spec, "summary": summary, "snapshot": snapshot, "levels": level_rows, "trajectory": traj_rows,
            "evidence": ev_rows, "progression": prog_rows, "metrics": summ_rows, "checkpoints": check_rows, "anatomy": anat_rows,
            "moves": move_rows, "prints": print_rows, "analogs": analog_rows + base_rows, "peers": peer_rows, "events": event_rows,
            "research": q_plan + plan_rows}


def _peer_rows(pk: Path, ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    import duckdb

    path = pk / "peer_value" / "peer_value.duckdb"
    if not path.exists():
        return {}
    con = duckdb.connect(str(path), read_only=True)
    try:
        cur = con.execute(f"""SELECT p.symbol, p.rank, p.peer, p.mcap_ratio, p.lens, p.mult, p.peer_mult, p.gap_pct, p.peer_gap_pct,
                                     p.rev_nfy_g, p.peer_rev_nfy_g, p.opm, p.peer_opm, p.eps_rev_3m, p.peer_eps_rev_3m, p.r_1m, p.peer_r_1m
                              FROM peers p WHERE p.symbol IN ({_lit(ids)}) ORDER BY p.symbol, p.rank""")
        names = [d[0] for d in cur.description]
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in cur.fetchall():
            rec = dict(zip(names, r))
            out[str(rec["symbol"])].append({k: (_r(v, 3) if isinstance(v, float) else v) for k, v in rec.items()})
        return out
    except Exception:
        return {}
    finally:
        con.close()


def _event_rows(pk: Path, ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    rows = _read_rows_all(pk / "outlook" / "outlook.duckdb", "events", ids)
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[str(r.get("symbol"))].append({k: r.get(k) for k in ("symbol", "date", "dow", "days", "kind", "slot", "detail", "status", "source", "read")})
    return out


def _read_rows_all(path: Path, table: str, ids: Sequence[str]) -> list[dict[str, Any]]:
    import duckdb

    if not path.exists():
        return []
    con = duckdb.connect(str(path), read_only=True)
    try:
        cur = con.execute(f"SELECT * FROM {_qi(table)} WHERE symbol IN ({_lit(ids)}) ORDER BY date")
        names = [d[0] for d in cur.description]
        return [{k: _iso(v) for k, v in zip(names, r)} for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        con.close()


# --- output -----------------------------------------------------------------------------------------------


def _v(value: Any, digits: int = 2) -> str:
    if value in (None, ""):
        return "-"
    f = _f(value)
    if f is None:
        return str(value)
    return f"{f:,.{digits}f}" if abs(f) < 1e6 else f"{f / 1e9:,.1f}B"


def _kv(row: Mapping[str, Any], fields: Sequence[str], digits: int = 2) -> str:
    return ", ".join(f"{f} {_v(row.get(f), digits)}" for f in fields if row.get(f) not in (None, ""))


def dossier_md(payload: Mapping[str, Any]) -> str:
    """The agent's read: a compact section per name (~4-6 KB), the suite table first. Data only."""
    spec = payload["spec"]
    by = lambda table: defaultdict(list, {k: v for k, v in _group(payload[table]).items()})  # noqa: E731
    snap = {r["symbol"]: r for r in payload["snapshot"]}
    lv = {r["symbol"]: r for r in payload["levels"]}
    an = {r["symbol"]: r for r in payload["anatomy"]}
    traj, ev, metrics, checks = by("trajectory"), by("evidence"), by("metrics"), by("checkpoints")
    moves, prints, analogs, peers, events = by("moves"), by("prints"), by("analogs"), by("peers"), by("events")
    out = [f"# Dossier: {spec['label']}", "",
           f"Created {spec['created_utc']}; pack `{Path(spec['pack']).name}`; store {spec['store_span'][0] if spec['store_span'] else '-'}.."
           f"{spec['store_span'][1] if spec['store_span'] else '-'}; alpha states {spec['alpha_states'] or '-'}."]
    if spec.get("question"):
        out += ["", f"**Question:** {spec['question']}"]
    if spec.get("notes"):
        out += ["", "Notes: " + "; ".join(spec["notes"])]
    out += ["", "## Suite (request order, not a ranking)", "",
            "| symbol | close | m3 | warn | next | band | to_target | to_street | to_kill | pred_28d | an_28d_xs | an_ind_56d_xs | fv_up | irr-hurdle | peer_gap | short ▲/▼ | mid ▲/▼ | long ▲/▼ |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    ind56 = {a["symbol"]: a.get("xs_q50") for a in payload["analogs"] if a.get("scope") == "industry" and a.get("horizon") == "56d"}
    for s in payload["summary"]:
        r = snap[s["symbol"]]
        nxt = f"{s.get('next_kind') or ''} {s.get('next_date') or ''}".strip() or "-"
        out.append(f"| {s['symbol']} | {_v(s.get('close'))} | {_v(s.get('m3'), 1)} | {s.get('warn_level') or '-'} | {nxt} | {s.get('band_state') or '-'} | "
                   f"{_v(s.get('to_target_pct'), 1)} | {_v(s.get('to_street_pct'), 1)} | {_v(s.get('to_kill_live_pct'), 1)} | {_v(s.get('pred_28d'))} | "
                   f"{_v(s.get('an_28d_xs_q50'))} | {_v(ind56.get(s['symbol']))} | "
                   f"{_v(s.get('fv_upside_pct'), 1)} | {_v(r.get('irr_minus_hurdle'), 1)} | {_v(s.get('peer_gap_pct'), 1)} | "
                   f"{s['short_up']}/{s['short_dn']} | {s['mid_up']}/{s['mid_dn']} | {s['long_up']}/{s['long_dn']} |")
    base = [a for a in payload["analogs"] if a.get("scope") == "base"]
    if base:
        out += ["", "Analog base (every floor name-day): " + "; ".join(f"{b['horizon']} median {_v(b.get('q50'))}% (q10 {_v(b.get('q10'))}, q90 {_v(b.get('q90'))}, hit {_v(b.get('hit_pct'), 1)}%)" for b in base)]
    for s in payload["summary"]:
        sym = s["symbol"]
        r, L, A = snap[sym], lv.get(sym, {}), an.get(sym, {})
        out += ["", f"## {sym} {r.get('company') or ''} ({r.get('ind') or r.get('af_industry') or '-'}; ${_v(r.get('mcap_b'), 1)}B)"
                + (f" BOOK ${_v(r.get('book_usd'), 0)}" if r.get("in_book") in (True, "true", "True", 1) else "")]
        if r.get("pack_lists"):
            out.append(f"Pack lists: {r['pack_lists']}")
        out.append("Now: " + _kv(r, ["close", "day", "w", "m1", "m3", "r_12m", "rsi", "rng", "vs50", "vs200", "atrp", "relvol", "tape_state", "trend_q", "updown_vol", "hi52_days"], 1))
        out.append("Levels: " + _kv(L, ["band_live_low", "band_live_high", "band_src", "kill_live", "kill_src", "to_kill_live_pct", "entry_low", "entry_high", "band_state", "bounce_low", "bounce_high", "kill_px", "bounce_kill", "first_target",
                                        "to_target_pct", "to_kill_pct", "street_px", "to_street_pct", "fv_bear_px", "fv_base_px", "fv_bull_px", "b28d_q20", "b28d_q80"]))
        out.append("Value: " + _kv(r, ["val_label", "pe_nfy", "pe_nfy_vs_ind", "pe_nfy_vs_own", "peg_fwd", "evebitda", "peer_lens", "peer_mult", "peer_ind_med",
                                       "peer_prem_pct", "peer_just_pct", "peer_gap_pct", "fv_upside_pct", "irr_exp_pct", "hurdle_pct", "priced_in_cagr_pct",
                                       "fv_flags", "cy_drop_in_k0", "cy_drop_state_k0", "div_yield"], 1))
        out.append("Quality / growth: " + _kv(r, ["opm", "fcf_margin", "nd_ebitda", "rev_yoy", "eps_yoy", "fwd_eps_g", "pv_rev_nfy_g", "pv_pctl_growth", "pv_pctl_quality"], 1))
        out.append("Street: " + _kv(r, ["analysts", "rating", "pt_up", "street_left", "eps_rev_3m", "eps_rev_breadth", "rev_rev_3m", "pt_drift_3m"], 1))
        out.append("Print: " + _kv(r, ["earn_date", "earn_slot", "earn_days", "last_surprise", "rev_surprise", "react_last", "since_print", "c_bar_text",
                                       "c_beat_rate", "c_rx_beat", "c_rx_miss", "c_beat_sold", "p_beat", "ev_rx", "own_beats", "own_n", "beat_q90_usd", "miss_q10_usd"], 1))
        out.append("Model: " + _kv(r, ["pred_7d", "dec_7d", "pred_28d", "dec_28d", "pred_56d", "dec_56d", "why_28d", "stance", "support", "conflicts", "mix", "rr", "bo"]))
        out.append("Risk: " + _kv(r, ["warn_level", "er_letters", "stress_5s_pct", "crowd_day_pct", "crowd_5s_pct", "er_crowd_dd_pct", "crowd_z", "beta", "rvol_20",
                                      "er_own_worst_5s_pct", "ev_next", "ev_binary", "crowd_top", "det", "cnt_cell", "cnt_fx_28d_mean", "cnt_p_dn_28d", "cnt_p_half_56d"], 1))
        if r.get("catalysts"):
            out.append(f"Catalysts (outlook): {r['catalysts']}")
        if A:
            out.append("Movement (store): " + _kv(A, ["first_on", "last_on", "period_ret_pct", "max_dd_pct", "max_dd_peak", "max_dd_trough", "max_runup_pct",
                                                     "max_runup_low", "max_runup_high", "dd_now_pct", "above50_pct", "above200_pct", "up_days_pct", "day_sd_pct",
                                                     "spikes_up", "spikes_dn", "n_prints"], 1))
        if moves[sym]:
            out.append("Largest moves: " + "; ".join(f"{m['as_of']} {_v(m['move_pct'], 1)}% (relvol {_v(m.get('relvol'), 1)}{', print' if m.get('print_day') else ''})" for m in moves[sym]))
        if metrics[sym]:
            out += ["", "| metric | first | last | chg % | min | max | pos |", "|---|---|---|---|---|---|---|"]
            out += [f"| {m['metric']} | {_v(m['first'])} | {_v(m['last'])} | {_v(m.get('chg_pct'), 1)} | {_v(m['min'])} | {_v(m['max'])} | {_v(m.get('pos_in_range'), 0)} |" for m in metrics[sym]]
        if checks[sym]:
            fields = [k for k in checks[sym][0] if k not in ("symbol", "as_of")]
            out += ["", "| as_of | " + " | ".join(fields) + " |", "|---" * (len(fields) + 1) + "|"]
            out += ["| " + str(c["as_of"]) + " | " + " | ".join(_v(c.get(f)) for f in fields) + " |" for c in checks[sym]]
        if prints[sym]:
            out.append("Prints: " + "; ".join(f"{p['print_date']} {p.get('slot') or ''} eps {_v(p.get('eps_surprise'), 1)}% rev {_v(p.get('rev_surprise'), 1)}% "
                                               f"runup {_v(p.get('runup'), 1)}% react {_v(p.get('react'), 1)}% drift {_v(p.get('drift'), 1)}%" for p in prints[sym]))
        al = [a for a in analogs[sym]]
        if al:
            out.append(f"Analogs (as of {r.get('an_as_of')}; features {r.get('an_features')}): "
                       + "; ".join(f"{a['scope']} {a['horizon']}: q10 {_v(a.get('q10'))} / q50 {_v(a.get('q50'))} / q90 {_v(a.get('q90'))}%, xs {_v(a.get('xs_q50'))}, "
                                   f"hit {_v(a.get('hit_pct'), 0)}%, {a['n']} on {a['n_dates']} days" for a in al))
        if traj[sym]:
            out.append("Trajectory (price): " + "; ".join(f"{t['horizon']} analog {_v(t.get('px_q10'))} / {_v(t.get('px_q50'))} / {_v(t.get('px_q90'))}"
                                                         + (f", alpha {_v(t.get('alpha_q20'))}-{_v(t.get('alpha_q80'))} ({t.get('alpha_state') or '-'})" if t.get("alpha_q20") else "")
                                                         for t in traj[sym]))
        for h in ("short", "mid", "long"):
            items = [e for e in ev[sym] if e["horizon"] == h]
            out.append(f"Evidence {h} ({s[h + '_up']}▲ {s[h + '_dn']}▼): " + "; ".join(
                f"{e['item']} {_v(e['value'])} {'▲' if e['direction'] == 'up' else '▼' if e['direction'] == 'down' else '·' if e['direction'] == 'flat' else 'n/a'}" for e in items))
        if peers[sym]:
            out.append("Peers: " + "; ".join(f"{p['peer']} ({p.get('lens')} {_v(p.get('peer_mult'), 1)} vs {_v(p.get('mult'), 1)}, gap {_v(p.get('peer_gap_pct'), 0)}%, "
                                              f"r_1m {_v(p.get('peer_r_1m'), 1)}%)" for p in peers[sym]))
        if events[sym]:
            out.append("Dated (outlook): " + "; ".join(f"{e['date']} {e['kind']} {e.get('slot') or ''} {e.get('detail') or ''}".strip() for e in events[sym][:8]))
    out += ["", "## Research plan (run after reading; tag findings OUTSIDE with the link)", ""]
    out += [f"- {p['symbol']}: {p['query']}" for p in payload["research"]]
    return "\n".join(out) + "\n"


def _group(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        out[str(r.get("symbol"))].append(r)
    return out


def highlights_text(payload: Mapping[str, Any]) -> str:
    spec = payload["spec"]
    lines = [f"Dossier {spec['label']}: {len(spec['ids'])} names ({', '.join(spec['ids'])}); store {spec['store_span']}; pack {Path(spec['pack']).name}."]
    for s in payload["summary"]:
        lines.append(f"  {s['symbol']:<16} close {_v(s.get('close'))} warn {s.get('warn_level') or '-'} band {s.get('band_state') or '-'} "
                     f"to_target {_v(s.get('to_target_pct'), 1)}% to_kill_live {_v(s.get('to_kill_live_pct'), 1)}% an_28d_xs {_v(s.get('an_28d_xs_q50'))} "
                     f"pred_28d {_v(s.get('pred_28d'))} fv_up {_v(s.get('fv_upside_pct'), 1)}% short {s['short_up']}/{s['short_dn']} "
                     f"mid {s['mid_up']}/{s['mid_dn']} long {s['long_up']}/{s['long_dn']}")
    if spec.get("notes"):
        lines.append("Notes: " + "; ".join(spec["notes"]))
    return "\n".join(lines) + "\n"


def default_out_dir(spec: Mapping[str, Any], recipe: Mapping[str, Any] | None = None) -> Path:
    root = Path(str((recipe or load_recipe()).get("out_root") or "logs/tradingview_analysis/name_dossier"))
    root = root if root.is_absolute() else REPO_ROOT / root
    return root / f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}_{spec['label']}"


def write_dossier(payload: Mapping[str, Any], out_dir: str | Path | None = None) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    out = Path(out_dir) if out_dir else default_out_dir(spec)
    tables = {k: payload[k] for k in ("summary", "snapshot", "levels", "trajectory", "evidence", "progression", "metrics", "checkpoints",
                                      "anatomy", "moves", "prints", "analogs", "peers", "events", "research")}
    exported = write_run_export(out, tool="generic_utils.name_dossier", tables=tables, sources={"pack": spec["pack"], "store": spec["store"]},
                                notes=list(spec.get("notes") or []), extra={"spec": spec}, duckdb_name="dossier.duckdb")
    (out / "dossier.md").write_text(dossier_md(payload), encoding="utf-8")
    (out / "highlights.log").write_text(highlights_text(payload), encoding="utf-8")
    (out / "spec.json").write_text(json.dumps(spec, indent=1, default=str), encoding="utf-8")
    exported["out_dir"] = out.as_posix()
    exported["dossier"] = (out / "dossier.md").as_posix()
    return exported


# --- page -------------------------------------------------------------------------------------------------


JUDGMENT_TONES = {"BUY_BAND": "good", "ACCUMULATE": "good", "WAIT": "warning", "AVOID": "bad", "TRIM": "warning", "SHORT_WATCH": "bad"}


def judgment_skeleton(dd_dir: str | Path) -> dict[str, Any]:
    """The judgment file the agent fills (dossier.judgment.json): per-name verdict, horizon classes, levels,
    trajectory, catalysts and outside lines; the suite order; the question read. Data fields are pre-filled."""
    d = Path(dd_dir)
    spec = json.loads((d / "spec.json").read_text(encoding="utf-8"))
    from generic_utils.scan_sources import rows_from_csv

    lv = {r["symbol"]: r for r in rows_from_csv(d / "levels.csv")}
    names = {}
    for sym in spec["ids"]:
        L = lv.get(sym, {})
        names[sym] = {
            "verdict": "", "grade": "", "horizon": "", "thesis": "", "bull": [], "bear": [],
            "classes": {"short": "", "mid": "", "long": ""},
            "levels": {"entry_low": _f(L.get("band_live_low")), "entry_high": _f(L.get("band_live_high")), "kill": _f(L.get("kill_live")),
                       "target_short": None, "target_mid": _f(L.get("first_target")), "target_long": _f(L.get("fv_base_px"))},
            "trajectory": [{"horizon": "SHORT", "path": "", "price_lo": None, "price_mid": None, "price_hi": None, "class": "", "evidence": ""},
                           {"horizon": "MID", "path": "", "price_lo": None, "price_mid": None, "price_hi": None, "class": "", "evidence": ""},
                           {"horizon": "LONG", "path": "", "price_lo": None, "price_mid": None, "price_hi": None, "class": "", "evidence": ""}],
            "catalysts": [{"date": "", "event": "", "direction": "", "source": ""}],
            "outside": [{"text": "", "source": ""}],
            "plan": "", "size": "",
        }
    return {"label": spec["label"], "question": spec.get("question"), "title": "", "summary": {"title": "", "text": "", "tone": "info"},
            "order": {"by": "", "ids": list(spec["ids"])}, "question_read": {"text": "", "rows": []}, "names": names, "sources": []}


def _tbl(title: str, db: Path, sql: str, **kw: Any) -> dict[str, Any]:
    return {"type": "table", "title": title, "source": {"duckdb": db.as_posix(), "sql": sql}, **kw}


def build_page_spec(dd_dir: str | Path, judgment: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Page spec from the dossier folder (data tables point at dossier.duckdb) plus the agent's judgment."""
    d = Path(dd_dir).resolve()
    spec = json.loads((d / "spec.json").read_text(encoding="utf-8"))
    db = d / "dossier.duckdb"
    j = dict(judgment or {})
    names = j.get("names") or {}
    order = [s for s in ((j.get("order") or {}).get("ids") or spec["ids"]) if s in spec["ids"]] + [s for s in spec["ids"] if s not in ((j.get("order") or {}).get("ids") or [])]
    extra = {s: {"Verdict": n.get("verdict") or "", "Grade": n.get("grade") or "", "Horizon": n.get("horizon") or "",
                 "Class S/M/L": "/".join((n.get("classes") or {}).get(h) or "-" for h in ("short", "mid", "long")),
                 "Thesis": n.get("thesis") or ""} for s, n in names.items()}
    verdict_blocks: list[dict[str, Any]] = []
    if j.get("summary") and (j["summary"].get("text") or j["summary"].get("title")):
        verdict_blocks.append({"type": "callout", "tone": j["summary"].get("tone") or "info", "title": j["summary"].get("title") or "Read",
                               "text": j["summary"].get("text") or ""})
    if spec.get("question"):
        verdict_blocks.append({"type": "callout", "tone": "info", "title": "Question", "text": spec["question"]})
    if (j.get("order") or {}).get("by"):
        verdict_blocks.append({"type": "text", "text": f"Order: {j['order']['by']}", "tone": "secondary"})
    verdict_blocks.append(_tbl("Suite (judgment order)", db,
                               "SELECT symbol, company, ind, close, warn_level, next_kind, next_date, band_state, band_live_low, band_live_high, kill_live, to_target_pct, to_street_pct, to_kill_live_pct, pred_28d, "
                               "an_28d_xs_q50, fv_upside_pct, street_left, peer_gap_pct, short_up, short_dn, mid_up, mid_dn, long_up, long_dn FROM summary",
                               ids=order, id_field="symbol", extra=extra,
                               extra_columns=["Verdict", "Grade", "Horizon", "Class S/M/L", "Thesis"], tone_field="Verdict", tone_map=JUDGMENT_TONES))
    plan_rows = []
    for s in order:
        n = names.get(s) or {}
        lv = n.get("levels") or {}
        plan_rows.append([s, n.get("verdict") or "-", _v(lv.get("entry_low")) + " - " + _v(lv.get("entry_high")), _v(lv.get("kill")),
                          _v(lv.get("target_short")), _v(lv.get("target_mid")), _v(lv.get("target_long")), n.get("size") or "-", n.get("plan") or "-"])
    if any(names.get(s) for s in order):
        verdict_blocks.append({"type": "table", "title": "Plan (agent)", "headers": ["symbol", "Verdict", "Band", "Kill", "Target SHORT", "Target MID", "Target LONG", "Size", "Plan"],
                               "rows": plan_rows, "tones": [JUDGMENT_TONES.get((names.get(s) or {}).get("verdict") or "") for s in order]})
    qr = j.get("question_read") or {}
    if qr.get("text") or qr.get("rows"):
        verdict_blocks.append({"type": "callout", "tone": "info", "title": "Question read", "text": qr.get("text") or ""})
        if qr.get("rows"):
            verdict_blocks.append({"type": "table", "title": "Question: how it plays", "headers": qr.get("headers") or ["Item", "Read", "Source"], "rows": qr["rows"]})
    tabs = [{"label": "Verdict", "blocks": verdict_blocks}]
    for s in order:
        n = names.get(s) or {}
        tk = parse_symbol(s).ticker
        blocks: list[dict[str, Any]] = []
        if n.get("thesis"):
            blocks.append({"type": "callout", "tone": JUDGMENT_TONES.get(n.get("verdict") or "", "info"),
                           "title": f"{s} {n.get('verdict') or ''} ({n.get('horizon') or '-'}, {n.get('grade') or '-'})", "text": n["thesis"]})
        if n.get("bull") or n.get("bear"):
            blocks.append({"type": "table", "title": "Bull / bear", "headers": ["Bull", "Bear"],
                           "rows": [[(n.get("bull") or [])[i] if i < len(n.get("bull") or []) else "", (n.get("bear") or [])[i] if i < len(n.get("bear") or []) else ""]
                                    for i in range(max(len(n.get("bull") or []), len(n.get("bear") or [])))]})
        if n.get("trajectory"):
            blocks.append({"type": "table", "title": "Trajectory (agent)", "headers": ["Horizon", "Path", "Low", "Mid", "High", "Class", "Evidence"],
                           "rows": [[t.get("horizon"), t.get("path"), _v(t.get("price_lo")), _v(t.get("price_mid")), _v(t.get("price_hi")), t.get("class"), t.get("evidence")]
                                    for t in n["trajectory"] if t.get("path")]})
        blocks.append(_tbl("Levels", db, f"SELECT * FROM levels WHERE symbol = '{s}'"))
        blocks.append(_tbl("Analog and model paths", db,
                           f"SELECT horizon, px_q10, px_q25, px_q50, px_q75, px_q90, an_q50, an_xs_q50, an_hit, an_n_dates, alpha_q20, alpha_q50, alpha_q80, alpha_state "
                           f"FROM trajectory WHERE symbol = '{s}'"))
        blocks.append(_tbl("Evidence by horizon (measured directions)", db,
                           f"SELECT horizon, item, field, value, direction, rule FROM evidence WHERE symbol = '{s}'"))
        blocks.append({"type": "chart", "title": f"{s}: close, 50-day, 200-day and the street's average target", "x": "as_of",
                       "source": {"duckdb": db.as_posix(), "sql": f"SELECT as_of, close, sma50, sma200, pt_avg FROM progression WHERE symbol = '{s}' ORDER BY as_of"},
                       "series": [["close", "Close"], ["sma50", "50-day"], ["sma200", "200-day"], ["pt_avg", "Street average target"]], "y_label": "price"})
        blocks.append(_tbl("Progression over the store period", db,
                           f"SELECT metric, first_on, first, last_on, last, chg, chg_pct, min, max, pos_in_range FROM metrics WHERE symbol = '{s}'"))
        blocks.append(_tbl("Movement anatomy", db, f"SELECT * EXCLUDE (symbol) FROM anatomy WHERE symbol = '{s}'"))
        blocks.append(_tbl("Largest moves", db, f"SELECT as_of, move_pct, close, relvol, print_day FROM moves WHERE symbol = '{s}' ORDER BY as_of", optional=True))
        blocks.append(_tbl("Prints", db, f"SELECT * EXCLUDE (symbol) FROM prints WHERE symbol = '{s}'", optional=True))
        blocks.append(_tbl("Analogs (all floor / same industry)", db,
                           f"SELECT scope, horizon, n, n_dates, q10, q25, q50, q75, q90, xs_q50, hit_pct, xs_hit_pct FROM analogs WHERE symbol IN ('{s}', 'BASE (all floor name-days)') "
                           f"ORDER BY scope, horizon"))
        blocks.append(_tbl("Nearest peers", db, f"SELECT * EXCLUDE (symbol) FROM peers WHERE symbol = '{s}'", optional=True))
        cats = [c for c in n.get("catalysts") or [] if c.get("event")]
        if cats:
            blocks.append({"type": "table", "title": "Catalysts (agent, OUTSIDE)", "headers": ["Date", "Event", "Direction", "Source"],
                           "rows": [[c.get("date"), c.get("event"), c.get("direction"), c.get("source")] for c in cats]})
        blocks.append(_tbl("Dated events (outlook)", db, f"SELECT date, dow, days, kind, slot, detail, status FROM events WHERE symbol = '{s}'", optional=True))
        outside = [o for o in n.get("outside") or [] if o.get("text")]
        if outside:
            blocks.append({"type": "list", "title": "Outside read", "items": [f"OUTSIDE: {o['text']}" + (f" ({o['source']})" if o.get("source") else "") for o in outside]})
        tabs.append({"label": tk, "blocks": blocks})
    tabs.append({"label": "Research", "blocks": [_tbl("Research plan", db, "SELECT symbol, query FROM research"),
                                                 {"type": "list", "items": [str(x) for x in j.get("sources") or []] or ["(agent sources)"]}]})
    recipe = load_recipe()
    return {"name": f"dossier-{spec['label']}", "title": j.get("title") or f"Dossier: {spec['label']}",
            "subtitle": f"{len(order)} names; pack {Path(spec['pack']).name}; store {spec['store_span']}; created {spec['created_utc']}",
            "sources": [f"pack `{Path(spec['pack']).name}`", f"store `{Path(spec['store']).name}` {spec['store_span']}", f"dossier `{d.name}`"],
            "tabs": tabs, "ledger": {"auto": True, "entries": [{"group": "Page", "rows": [list(r) for r in recipe.get("ledger") or []]}]}}


def dossier_page(dd_dir: str | Path, judgment: str | Path | Mapping[str, Any] | None = None, out_dir: str | Path | None = None) -> dict[str, Any]:
    """Render the dossier page into ``out_dir`` (default the dossier folder) from its tables and the judgment."""
    from generic_utils.canvas_page import write_page

    d = Path(dd_dir)
    if isinstance(judgment, (str, Path)):
        jpath = Path(judgment)
        jdata = json.loads(jpath.read_text(encoding="utf-8")) if jpath.exists() else None
    else:
        jdata = dict(judgment) if judgment else None
        if jdata is None and (d / "dossier.judgment.json").exists():
            jdata = json.loads((d / "dossier.judgment.json").read_text(encoding="utf-8"))
    spec = build_page_spec(d, jdata)
    target = Path(out_dir) if out_dir else d
    target.mkdir(parents=True, exist_ok=True)
    spec_path = target / f"{spec['name']}.json"
    spec_path.write_text(json.dumps(spec, indent=1, default=str), encoding="utf-8")
    return write_page(spec_path)


def name_dossier_local_main() -> None:
    payload = build_dossier(["NYSE:DOW"])
    print(write_dossier(payload))


if __name__ == "__main__":
    name_dossier_local_main()
