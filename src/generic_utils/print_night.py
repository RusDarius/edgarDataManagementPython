"""Print night: every name whose print reaction lands on one session, with the measured pointers it sits on.

A pointer is one field known before the print (the recipe's ``pointers``: tape, print history, estimates,
street, value, model, the industry's prints this season) or, once the print is out, a print field
(``post_pointers``: surprise, mix, after-hours read, reaction). Each pointer is cut into quintiles on the
signal lab's past prints (``PACK/signal_lab/print_events.csv``, store features at each print's pre-print
file); a category pointer (slot, mix) uses its values. For every cell and measure (recipe ``measures``:
reaction vs the market and hold-through excess before the print; after-hours-to-close and drift after it)
the cell's mean, hit rate and n, its ``lift`` against every other print, the Welch t of that lift and the
lift in each half of the sample (split at the median print date). ``dir`` is ``up`` / ``down`` only when
the lift clears ``t_min`` on ``min_n`` prints and keeps its sign in both halves (``require_halves``),
``-`` when measured without a clear direction.

Tables: ``names`` (one row per reporter: calendar row, week-plays cells / bands / tails, alpha deciles with
their measured decile mean, and per measure the count of up / down pointers, the count of families they
come from and their lists), ``matrix`` (one row per name and measure, one ``x_<pointer>`` column per
pointer: ``▲`` / ``▼`` and the lift where the direction is clear, the lift alone otherwise), ``pointers``
(the long form behind every matrix cell), ``cells`` (every pointer quintile's measured row, names or not),
``base`` (all prints per measure) and ``history`` (the reporters' own past prints).

Pre-print pointers that the print itself rewrites (``print_moves``: estimates, last surprise) are left
empty on a printed row. Counts are counts of measured cells; correlated pointers (run-up, the month, RSI)
can point together. No ranking, no composite, no course: which name to enter is the page's and the judgment's.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from generic_utils.panel_store import REPO_ROOT, default_store_path, load_store_recipe
from generic_utils.ranking import to_float
from generic_utils.series import qident

_f = to_float
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "print_night.json"
UP, DOWN = "▲", "▼"


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: Any, digits: int = 3) -> float | None:
    number = _f(value)
    return None if number is None or math.isnan(number) else round(number, digits)


def _csv(path: Path) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_csv

    return rows_from_csv(path) if path.exists() else []


def _by_symbol(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r.get("symbol") or ""): dict(r) for r in rows if r.get("symbol")}


def category(key: str, value: Any) -> str | None:
    """Category pointer value: the slot without the inferred mark, the mix as written."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text.rstrip("~") if key == "slot" else text


# --- measured cells ----------------------------------------------------------------------------------------


def welch_t(a: Sequence[float], b: Sequence[float]) -> float | None:
    if len(a) < 3 or len(b) < 3:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    return (ma - mb) / se if se > 0 else None


def _lift(inside: Sequence[float], outside: Sequence[float]) -> float | None:
    if not inside or not outside:
        return None
    return sum(inside) / len(inside) - sum(outside) / len(outside)


def direction(cell: Mapping[str, Any], *, t_min: float, min_n: int, require_halves: bool, require_hit: bool = True) -> str | None:
    """``up`` / ``down`` when the lift is clear (t, n, both halves, the hit rate moving the same way), ``-`` when measured
    but not clear, None unmeasured. The hit rule keeps a cell whose mean is carried by a few large moves from pointing."""
    lift, t, n = _f(cell.get("lift")), _f(cell.get("lift_t")), _f(cell.get("n"))
    if lift is None or n is None:
        return None
    if n < min_n or t is None or abs(t) < t_min:
        return "-"
    hit_lift = _f(cell.get("hit_lift"))
    if require_hit and (hit_lift is None or hit_lift == 0 or (hit_lift > 0) != (lift > 0)):
        return "-"
    if require_halves:
        h1, h2 = _f(cell.get("half1_lift")), _f(cell.get("half2_lift"))
        if h1 is None or h2 is None or (h1 > 0) != (lift > 0) or (h2 > 0) != (lift > 0):
            return "-"
    return "up" if lift > 0 else "down"


def pointer_cells(events: Sequence[Mapping[str, Any]], pointers: Sequence[Mapping[str, Any]], measures: Sequence[Sequence[str]],
                  recipe: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, list[float]]]:
    """One row per pointer x bucket x measure on ``events`` (each event carries ``ptr_<key>``), plus the quintile edges."""
    from generic_utils.signal_lab import _bucket, _quantile_edges

    q = int(recipe.get("quantiles", 5))
    t_min, min_n = float(recipe.get("t_min", 2.0)), int(recipe.get("min_n", 40))
    halves, hits = bool(recipe.get("require_halves", True)), bool(recipe.get("require_hit", True))
    dates = sorted(str(e.get("print_date") or "") for e in events if e.get("print_date"))
    split = dates[len(dates) // 2] if dates else ""
    edges: dict[str, list[float]] = {}
    out: list[dict[str, Any]] = []
    for ptr in pointers:
        key = ptr["key"]
        if ptr.get("kind") == "category":
            label = lambda e, k=key: category(k, e.get(f"ptr_{k}"))  # noqa: E731
        else:
            edges[key] = _quantile_edges([float(e[f"ptr_{key}"]) for e in events if _f(e.get(f"ptr_{key}")) is not None], q)
            label = lambda e, k=key: _bucket(e.get(f"ptr_{k}"), edges[k])  # noqa: E731
        labelled = [(label(e), e) for e in events]
        for measure, code, _ in measures:
            if measure == ptr.get("event") or (ptr.get("measures") and measure not in ptr["measures"]):
                continue  # a field measured on itself (or on a measure it is built into) says nothing
            rows = [(b, float(e[measure]), str(e.get("print_date") or "") < split) for b, e in labelled
                    if b is not None and _f(e.get(measure)) is not None]
            for bucket in sorted({b for b, _, _ in rows}):
                inside = [x for b, x, _ in rows if b == bucket]
                outside = [x for b, x, _ in rows if b != bucket]
                lift = _lift(inside, outside)
                h1 = _lift([x for b, x, h in rows if b == bucket and h], [x for b, x, h in rows if b != bucket and h])
                h2 = _lift([x for b, x, h in rows if b == bucket and not h], [x for b, x, h in rows if b != bucket and not h])
                cell = {"pointer": key, "family": ptr.get("family"), "label": ptr.get("label"), "bucket": bucket,
                        "measure": measure, "code": code, "n": len(inside),
                        "mean": _r(sum(inside) / len(inside)), "hit": _r(_hit(inside)),
                        "q10": _r(_pctl(inside, 0.1)) if len(inside) >= 10 else None,
                        "q90": _r(_pctl(inside, 0.9)) if len(inside) >= 10 else None,
                        "lift": _r(lift), "lift_t": _r(welch_t(inside, outside), 2), "half1_lift": _r(h1), "half2_lift": _r(h2),
                        "hit_lift": _r(_hit(inside) - _hit(outside)) if inside and outside else None}
                cell["dir"] = direction(cell, t_min=t_min, min_n=min_n, require_halves=halves, require_hit=hits)
                out.append(cell)
    return out, edges


def _hit(values: Sequence[float]) -> float:
    return sum(1 for x in values if x > 0) / len(values)


def _pctl(values: Sequence[float], p: float) -> float | None:
    from generic_utils.signal_lab import _pctl as pctl

    return pctl(values, p)


def base_rows(events: Sequence[Mapping[str, Any]], measures: Sequence[Sequence[str]], phase: str) -> list[dict[str, Any]]:
    out = []
    for measure, code, label in measures:
        xs = [float(e[measure]) for e in events if _f(e.get(measure)) is not None]
        out.append({"phase": phase, "measure": measure, "code": code, "label": label, "n": len(xs),
                    "mean": _r(sum(xs) / len(xs)) if xs else None, "median": _r(median(xs)) if xs else None,
                    "hit": _r(sum(1 for x in xs if x > 0) / len(xs)) if xs else None,
                    "q10": _r(_pctl(xs, 0.1)) if len(xs) >= 10 else None, "q90": _r(_pctl(xs, 0.9)) if len(xs) >= 10 else None})
    return out


def peer_reaction(events: Sequence[Mapping[str, Any]], ind: str | None, before: str, *, days: int, min_n: int,
                  exclude: str | None = None) -> tuple[float | None, int]:
    """Mean ``react_fx`` of the industry's prints that reacted in [before - days, before), the symbol itself excluded."""
    if not ind or not before:
        return None, 0
    start = (date.fromisoformat(before[:10]) - timedelta(days=days)).isoformat()
    xs = [float(e["react_fx"]) for e in events
          if e.get("ind") == ind and e.get("symbol") != exclude and _f(e.get("react_fx")) is not None
          and start <= str(e.get("react_on") or "")[:10] < before[:10]]
    return (_r(sum(xs) / len(xs)) if len(xs) >= min_n else None), len(xs)


_OPS = {">=": lambda x, v: x >= v, "<=": lambda x, v: x <= v, ">": lambda x, v: x > v, "<": lambda x, v: x < v}


def tail_flags(event: Mapping[str, Any], recipe: Mapping[str, Any]) -> dict[str, float | None]:
    """Recipe ``tail_measures``: 1.0 / 0.0 per print when its ``from`` field passes ``op value`` (None when the field is
    missing), so a pointer cell's mean is the share of prints that moved that far. The threshold is the recipe's, not a gate."""
    out: dict[str, float | None] = {}
    for t in recipe.get("tail_measures") or []:
        x, v, op = _f(event.get(t["from"])), float(t["value"]), t.get("op", ">=")
        if x is None:
            out[t["key"]] = None
        elif op == "balance":  # +1 at or beyond +value, -1 at or beyond -value, 0 between: the mean is up odds minus down odds
            out[t["key"]] = 1.0 if x >= v else -1.0 if x <= -v else 0.0
        else:
            out[t["key"]] = 1.0 if _OPS[op](x, v) else 0.0
    return out


def own_abs_move(events: Sequence[Mapping[str, Any]], symbol: Any, before: str) -> float | None:
    """Mean |react_fx| of ``symbol``'s lab prints dated before ``before`` (the name's own size of move, known at the time)."""
    xs = [abs(float(e["react_fx"])) for e in events
          if e.get("symbol") == symbol and str(e.get("print_date") or "") < before[:10] and _f(e.get("react_fx")) is not None]
    return _r(sum(xs) / len(xs)) if xs else None


# --- inputs ------------------------------------------------------------------------------------------------


def store_fields(recipe: Mapping[str, Any]) -> list[str]:
    return list(dict.fromkeys(p["store"] for p in recipe.get("pointers") or [] if p.get("store")))


def event_rows(pack: Path, store: Path, recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The lab's print events with ``ptr_<key>`` per pointer (store features at the pre-print file, peers derived)."""
    import duckdb

    events = _csv(pack / "signal_lab" / "print_events.csv")
    if not events:
        raise FileNotFoundError(f"{pack / 'signal_lab' / 'print_events.csv'} missing: run tv_scan_cli.py signal-lab --pack {pack} first")
    fields = store_fields(recipe)
    feats: dict[tuple[str, str], dict[str, Any]] = {}
    if fields and store.exists():
        keys = sorted({f"{e['symbol']}|{str(e.get('pre_file') or '')[:10]}" for e in events if e.get("pre_file")})
        con = duckdb.connect(str(store), read_only=True)
        try:
            cur = con.execute(
                f"SELECT symbol, CAST(as_of AS VARCHAR) AS as_of, {', '.join(qident(c) for c in fields)} FROM features "
                "WHERE symbol || '|' || CAST(as_of AS VARCHAR) IN (SELECT UNNEST(?::VARCHAR[]))", [keys])
            names = [d[0] for d in cur.description]
            for values in cur.fetchall():
                row = dict(zip(names, values))
                feats[(row["symbol"], row["as_of"])] = row
        finally:
            con.close()
    days, min_n = int(recipe.get("peer_window_days", 35)), int(recipe.get("peer_min_n", 3))
    by_ind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_ind[str(e.get("ind") or "")].append(e)
        by_sym[str(e.get("symbol") or "")].append(e)
    out = []
    for e in events:
        rec = dict(e)
        rec.update(tail_flags(e, recipe))
        f = feats.get((str(e.get("symbol")), str(e.get("pre_file") or "")[:10])) or {}
        for ptr in list(recipe.get("pointers") or []) + list(recipe.get("post_pointers") or []):
            key = ptr["key"]
            if ptr.get("store"):
                rec[f"ptr_{key}"] = _f(f.get(ptr["store"]))
            elif ptr.get("derived") == "own_abs":
                rec[f"ptr_{key}"] = own_abs_move(by_sym.get(str(e.get("symbol") or ""), []), e.get("symbol"), str(e.get("print_date") or ""))
            elif ptr.get("derived") == "peer_rx":
                rec[f"ptr_{key}"] = peer_reaction(by_ind.get(str(e.get("ind") or ""), []), e.get("ind"), str(e.get("react_on") or ""),
                                                  days=days, min_n=min_n, exclude=e.get("symbol"))[0]
            elif ptr.get("kind") == "category":
                rec[f"ptr_{key}"] = category(key, e.get(ptr["event"]))
            else:
                rec[f"ptr_{key}"] = _f(e.get(ptr["event"]))
        out.append(rec)
    return out


def live_features(store: Path, symbols: Sequence[str], fields: Sequence[str]) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Store features on the newest store day for ``symbols`` (the same definitions the past prints were measured on)."""
    import duckdb

    if not store.exists() or not symbols or not fields:
        return {}, None
    con = duckdb.connect(str(store), read_only=True)
    try:
        day = con.execute("SELECT CAST(max(as_of) AS VARCHAR) FROM features").fetchone()[0]
        cur = con.execute(f"SELECT symbol, {', '.join(qident(c) for c in fields)} FROM features "
                          "WHERE as_of = CAST(? AS DATE) AND list_contains(?::VARCHAR[], symbol)", [day, list(symbols)])
        names = [d[0] for d in cur.description]
        return _by_symbol([dict(zip(names, v)) for v in cur.fetchall()]), day
    finally:
        con.close()


def history_rows(store: Path, symbols: Sequence[str], events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every stored print of ``symbols``, newest first, with the lab's excess reads where the print is on its floor."""
    import duckdb

    if not store.exists() or not symbols:
        return []
    lab = {(str(e.get("symbol")), str(e.get("print_date"))[:10]): e for e in events}
    con = duckdb.connect(str(store), read_only=True)
    try:
        cur = con.execute("SELECT symbol, CAST(print_date AS VARCHAR) AS print_date, slot, CAST(react_on AS VARCHAR) AS react_on, "
                          "runup, prev_react, eps_surprise, rev_surprise, react, drift FROM prints "
                          "WHERE list_contains(?::VARCHAR[], symbol) ORDER BY symbol, print_date DESC", [list(symbols)])
        names = [d[0] for d in cur.description]
        out = []
        for values in cur.fetchall():
            rec = {k: (_r(v) if isinstance(v, float) else v) for k, v in zip(names, values)}
            e = lab.get((rec["symbol"], str(rec["print_date"])[:10])) or {}
            for k in ("react_fx", "ah_read", "ah_to_close", "hold_fx_7d", "hold_fx_28d", "drift_fx_7d", "drift_fx_28d"):
                rec[k] = _r(e.get(k))
            out.append(rec)
        return out
    finally:
        con.close()


def alpha_reads(pack: Path) -> tuple[dict[tuple[str, int], dict[str, Any]], dict[str, str]]:
    """Alpha model decile outcomes (horizon, decile) -> {fx_mean, hit} and the state per horizon."""
    deciles = {(str(r.get("horizon")), int(_f(r.get("decile")) or 0)): r for r in _csv(pack / "alpha_model" / "deciles.csv")}
    states = {str(r.get("horizon")): str(r.get("state") or "") for r in _csv(pack / "alpha_model" / "state.csv")}
    return deciles, states


def pick_session(calendar: Sequence[Mapping[str, Any]], scan_day: str, react_on: str | None) -> str | None:
    """``react_on`` as given (a date), the scan day ('today'), or the first reaction session after the scan day."""
    if react_on and react_on not in {"next", "today"}:
        return str(react_on)[:10]
    if react_on == "today":
        return scan_day
    later = sorted({str(r.get("react_on") or "")[:10] for r in calendar if str(r.get("react_on") or "")[:10] > scan_day})
    return later[0] if later else None


# --- build -------------------------------------------------------------------------------------------------


def _cell_text(cell: Mapping[str, Any] | None) -> str | None:
    if not cell or cell.get("lift") is None:
        return None
    arrow = UP if cell.get("dir") == "up" else DOWN if cell.get("dir") == "down" else ""
    return f"{arrow}{float(cell['lift']):+.1f}"


def build_print_night(pack_dir: str | Path, *, recipe: Mapping[str, Any] | None = None, ids: Sequence[str] = (),
                      react_on: str | None = None, sessions: int = 1, store: str | Path | None = None) -> dict[str, Any]:
    """Tables ``names``, ``matrix``, ``pointers``, ``cells``, ``base`` and ``history`` for the reporters of the chosen sessions."""
    from generic_utils.signal_lab import _bucket
    from generic_utils.symbol_identity import ticker_map
    from generic_utils.week_plays import market_day, post_event, scan_day_of

    pack = Path(pack_dir) if Path(pack_dir).is_absolute() else REPO_ROOT / pack_dir
    recipe = dict(recipe or load_recipe())
    store_path = Path(store) if store else default_store_path(load_store_recipe())
    calendar = _csv(pack / "earnings_week" / "calendar.csv")
    if not calendar:
        raise FileNotFoundError(f"{pack / 'earnings_week' / 'calendar.csv'} missing: run tv_scan_cli.py earnings-week --pack {pack} first")
    scan_day = scan_day_of(pack)
    first = pick_session(calendar, scan_day, react_on)
    sessions_on = sorted({str(r.get("react_on") or "")[:10] for r in calendar if first and str(r.get("react_on") or "")[:10] >= first})
    chosen = set(sessions_on[: max(1, int(sessions))])
    rows = [r for r in calendar if str(r.get("react_on") or "")[:10] in chosen]
    if ids:
        by_ticker = ticker_map(r["symbol"] for r in calendar)
        wanted = {i if ":" in i else by_ticker.get(i.upper(), i) for i in ids}
        rows = rows + [r for r in calendar if r["symbol"] in wanted and r not in rows]  # named ids join the session's names

    events = event_rows(pack, store_path, recipe)
    pre_m = [tuple(m) for m in recipe["measures"]["pre"]]
    post_m = [tuple(m) for m in recipe["measures"]["post"]]
    pre_p = list(recipe.get("pointers") or [])
    post_p = list(recipe.get("post_pointers") or [])
    cells_pre, edges_pre = pointer_cells(events, pre_p, pre_m, recipe)
    cells_post, edges_post = pointer_cells(events, post_p, post_m, recipe)
    edges = {**edges_pre, **edges_post}
    lookup = {(c["pointer"], c["bucket"], c["measure"]): c for c in cells_pre + cells_post}

    symbols = [r["symbol"] for r in rows]
    feats, feat_day = live_features(store_path, symbols, store_fields(recipe))
    plays = _by_symbol(_csv(pack / "week_plays" / "plays.csv"))
    tape_rows = _csv(pack / "movers" / "us2b_tape.csv")
    mkt = market_day(tape_rows)
    deciles, states = alpha_reads(pack)
    starter = float(recipe.get("starter_usd") or 5000)
    days, peer_min = int(recipe.get("peer_window_days", 35)), int(recipe.get("peer_min_n", 3))
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_sym[str(e.get("symbol") or "")].append(e)
    tail_codes = {t["key"]: t["code"] for t in recipe.get("tail_measures") or []}
    cell_min_n = int(recipe.get("min_n", 40))

    names: list[dict[str, Any]] = []
    matrix: list[dict[str, Any]] = []
    long_rows: list[dict[str, Any]] = []
    for row in rows:
        sym = row["symbol"]
        phase = "post" if str(row.get("printed")).strip().lower() in {"true", "1"} else "pre"
        rec: dict[str, Any] = {"symbol": sym, "phase": phase, "in_store": sym in feats}
        rec.update({f: row.get(f) for f in recipe.get("name_fields") or [] if f != "symbol"})
        rec["mcap_b"] = _r((_f(row.get("mcap")) or 0) / 1e9, 1) if _f(row.get("mcap")) else None
        play = plays.get(sym) or {}
        rec.update({f: play.get(f) for f in recipe.get("plays_fields") or []})
        for h in recipe.get("alpha_horizons") or []:
            dec = _f(play.get(f"dec_{h}"))
            d = deciles.get((h, int(dec))) if dec is not None else None
            rec[f"dec_{h}_fx"] = _r((d or {}).get("fx_mean"))
            rec[f"dec_{h}_hit"] = _r((d or {}).get("hit"))
            rec[f"state_{h}"] = states.get(h)
        q10, q90 = _f(play.get("setup_rx_q10")), _f(play.get("setup_rx_q90"))
        rec["tail_starter_usd"] = round(starter * q10 / 100.0) if q10 is not None else None
        rec["up_starter_usd"] = round(starter * q90 / 100.0) if q90 is not None else None
        own = _f(row.get("react_abs_mean"))
        rec["own_move_starter_usd"] = round(starter * own / 100.0) if own is not None else None
        peer, peer_n = peer_reaction(events, row.get("ind"), str(row.get("react_on") or ""), days=days, min_n=peer_min, exclude=sym)
        rec["peer_rx"], rec["peer_n"] = peer, peer_n

        live: dict[str, Any] = {}
        f = feats.get(sym) or {}
        for ptr in pre_p:
            key = ptr["key"]
            if phase == "post" and ptr.get("print_moves"):
                live[key] = None
            elif ptr.get("store"):
                live[key] = _f(f.get(ptr["store"])) if sym in feats else _f(row.get(ptr.get("calendar") or "")) if ptr.get("calendar") else None
            elif ptr.get("derived") == "peer_rx":
                live[key] = peer
            elif ptr.get("derived") == "own_abs":
                live[key] = own_abs_move(by_sym.get(sym, []), sym, str(row.get("earn_date") or "9999")[:10])
            elif ptr.get("kind") == "category":
                live[key] = category(key, row.get(ptr["live"]))
            else:
                live[key] = _f(row.get(ptr["live"]))
        post = post_event(row, scan_day=scan_day, mkt_day=mkt) if phase == "post" else {}
        for ptr in post_p:
            value = post.get(ptr["live"])
            live[ptr["key"]] = category(ptr["key"], value) if ptr.get("kind") == "category" else _f(value)

        groups = [("pre", pre_p, pre_m)] + ([("post", post_p, post_m)] if phase == "post" else [])
        for side, ptrs, measures in groups:
            for measure, code, _ in measures:
                mrow: dict[str, Any] = {"symbol": sym, "phase": phase, "side": side, "measure": measure, "code": code}
                ups, downs = [], []
                best: tuple[float, str] | None = None
                for ptr in ptrs:
                    key = ptr["key"]
                    value = live.get(key)
                    if value is None or measure == ptr.get("event") or (ptr.get("measures") and measure not in ptr["measures"]):
                        mrow[f"x_{key}"] = None
                        continue
                    bucket = category(key, value) if ptr.get("kind") == "category" else _bucket(value, edges.get(key) or [])
                    cell = lookup.get((key, bucket, measure)) if bucket else None
                    mrow[f"x_{key}"] = _cell_text(cell)
                    if cell and cell.get("dir") in {"up", "down"}:
                        (ups if cell["dir"] == "up" else downs).append((key, ptr.get("family"), bucket))
                    if measure in tail_codes and cell and _f(cell.get("mean")) is not None and (_f(cell.get("n")) or 0) >= cell_min_n:
                        if best is None or float(cell["mean"]) > best[0]:
                            best = (float(cell["mean"]), key)
                    long_rows.append({"symbol": sym, "side": side, "measure": measure, "code": code, "pointer": key,
                                      "family": ptr.get("family"), "value": value if isinstance(value, str) else _r(value),
                                      "bucket": bucket, "n": (cell or {}).get("n"), "mean": (cell or {}).get("mean"),
                                      "hit": (cell or {}).get("hit"), "lift": (cell or {}).get("lift"),
                                      "lift_t": (cell or {}).get("lift_t"), "half1_lift": (cell or {}).get("half1_lift"),
                                      "half2_lift": (cell or {}).get("half2_lift"),
                                      "hit_lift": (cell or {}).get("hit_lift"), "dir": (cell or {}).get("dir")})
                mrow["ptr_up"], mrow["ptr_dn"] = len(ups), len(downs)
                mrow["fam_up"], mrow["fam_dn"] = len({fam for _, fam, _ in ups}), len({fam for _, fam, _ in downs})
                matrix.append(mrow)
                rec[f"{code}_up"], rec[f"{code}_dn"] = mrow["ptr_up"], mrow["ptr_dn"]
                rec[f"{code}_fam_up"], rec[f"{code}_fam_dn"] = mrow["fam_up"], mrow["fam_dn"]
                rec[f"{code}_up_list"] = ", ".join(k for k, _, _ in ups) or None
                rec[f"{code}_dn_list"] = ", ".join(k for k, _, _ in downs) or None
                if measure in tail_codes:  # the highest odds among the name's measured cells, and which pointer gives it
                    rec[f"{code}_hi"] = _r(best[0]) if best else None
                    rec[f"{code}_hi_ptr"] = best[1] if best else None
        names.append(rec)

    base = base_rows(events, pre_m, "pre") + base_rows(events, post_m, "post")
    return {
        "names": names, "matrix": matrix, "pointers": long_rows, "cells": cells_pre + cells_post, "base": base,
        "history": history_rows(store_path, symbols, events),
        "spec": {"pack": pack.as_posix(), "store": store_path.as_posix(), "scan_day": scan_day, "sessions": sorted(chosen),
                 "feature_day": feat_day, "n_names": len(names), "n_pre": sum(1 for n in names if n["phase"] == "pre"),
                 "n_post": sum(1 for n in names if n["phase"] == "post"), "n_events": len(events),
                 "first_print": min((str(e.get("print_date")) for e in events), default=None),
                 "last_print": max((str(e.get("print_date")) for e in events), default=None),
                 "n_arrows": sum(1 for c in cells_pre + cells_post if c["dir"] in {"up", "down"}),
                 "n_cells": len(cells_pre) + len(cells_post), "mkt_day": mkt,
                 "t_min": recipe.get("t_min"), "min_n": recipe.get("min_n"), "require_halves": recipe.get("require_halves"),
                 "require_hit": recipe.get("require_hit", True)},
    }


def write_print_night(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"scan day {spec['scan_day']}; reaction sessions {', '.join(spec['sessions']) or '-'}; {spec['n_names']} names "
        f"({spec['n_pre']} before the print, {spec['n_post']} printed); store features of {spec['feature_day']}.",
        f"Pointer cells measured on {spec['n_events']} lab prints {spec['first_print']}..{spec['last_print']}: quintiles (category for slot / mix), "
        f"lift = cell mean minus every other print's mean, Welch t. dir up / down needs |t| >= {spec['t_min']}, n >= {spec['min_n']} "
        f"the same sign in both halves (split at the median print date) and the hit rate moving the same way (hit_lift): "
        f"{spec['n_arrows']} of {spec['n_cells']} cells.",
        "names: <code>_up / _dn = pointers whose cell points up / down for that measure (rx reaction, h7 / h28 hold from the pre-print close, "
        "ac after-hours to close, d28 drift); _fam_up / _fam_dn = how many families they come from; _up_list / _dn_list name them.",
        "matrix: x_<pointer> = ▲ / ▼ and the lift (pp) where the direction is clear, the lift alone otherwise, empty when the name has no value.",
        "Tail measures (recipe tail_measures): u10 / d10 = 0/1 per print, the reaction at or beyond +10 / -10 pp vs the market; a cell's mean is "
        "those odds, u10_up / d10_up count pointers that raise them, u10_hi / d10_hi the highest odds among the name's cells (n >= min_n) and "
        "*_hi_ptr its pointer. Size pointers (own_abs, mcap, rev_yoy) are measured on the tail measures only. Upside lean = u10 up with d10 not up.",
        "Print t-stats treat prints as independent (they cluster by date and industry): optimistic. Many cells are tested; the halves rule is the guard.",
        "Pre-print pointers the print rewrites (estimates, last surprise) are empty on printed rows. Week-plays cells, bands and tails are joined unchanged.",
        "No ranking, no composite, no course.",
    ]
    return write_run_export(out_dir, tool="generic_utils.print_night",
                            tables={k: payload.get(k) or [] for k in ("names", "matrix", "pointers", "cells", "base", "history")},
                            sources={"pack": spec["pack"], "store": spec["store"]}, notes=notes, extra={"spec": spec},
                            duckdb_name="print_night.duckdb")


def print_night_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_print_night(build_print_night(pack), pack / "print_night"))


if __name__ == "__main__":
    print_night_local_main()
