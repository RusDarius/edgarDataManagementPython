"""Beat bar: what a beat (and a miss) has paid on past prints set up like each coming reporter.

The positioning cell is the tercile of each grid axis (recipe ``grid.axes``: run-up into the print, the
three-month tape, the three-month EPS estimate change) cut on the signal lab's past prints
(``PACK/signal_lab/print_events.csv`` with the print-night pointer values at each pre-print file). Inside a
cell the prints are split by outcome (EPS ``beat`` / ``miss`` on ``beat_field``, and the B/B and B/M mixes)
and each split is measured on the recipe ``measures`` (reaction vs the market, hold-through excess from the
pre-print close at 7 and 28 days): mean, hit rate, n, q10 / q90, the share of beats sold on the reaction
(``beat_sold``) and of misses bought (``miss_bought``), and ``beat_lift`` (a beat here minus a beat anywhere
else, Welch t). A cell thinner than ``min_beats`` / ``min_misses`` falls back to the next axis set.

The bar is the lowest EPS-surprise quintile (edges on every lab print) whose reaction mean in the cell is
positive: how large a beat the set-up has needed. Pointer cells repeat the beat / miss split on one
pre-print field's quintile at a time (print-night pointers) with the halves rule.

Per reporter (week-plays rows of the recipe ``weeks`` before the print): the live axis values, the cell and
its reads, the name's own prints in the store (beats, mean reaction on beats), ``p_beat`` (the own beat
count shrunk toward the cell's beat rate with ``own_prior_prints``), and the mixture reads ``p_up_<m>`` /
``ev_<m>`` = p_beat x the beat read + (1 - p_beat) x the miss read. Those are measured frequencies mixed by
a stated weight, not a score: never a gate, a size or a sort key on their own. Week-plays bands and alpha
deciles are joined unchanged. Zero cutoffs beyond the floors; no ranking, no course.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.panel_store import REPO_ROOT, default_store_path, load_store_recipe
from generic_utils.ranking import to_float

_f = to_float
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "beat_bar.json"


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: Any, digits: int = 3) -> float | None:
    number = _f(value)
    return None if number is None or math.isnan(number) else round(number, digits)


def _mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _hit(xs: Sequence[float]) -> float | None:
    return sum(1 for x in xs if x > 0) / len(xs) if xs else None


def outcome(event: Mapping[str, Any], field: str, above: float) -> str | None:
    value = _f(event.get(field))
    if value is None:
        return None
    return "beat" if value > above else "miss"


def level_of(value: Any, edges: Sequence[float]) -> int | None:
    number = _f(value)
    if number is None or not edges:
        return None
    return sum(1 for e in edges if number >= e) + 1


def cell_label(axes: Sequence[str], key: Sequence[int]) -> str:
    return " & ".join(f"{a}:T{k}" for a, k in zip(axes, key))


def outcome_stats(events: Sequence[Mapping[str, Any]], measures: Sequence[Sequence[str]], *, field: str, above: float,
                  base_beats: Mapping[str, Sequence[float]] | None = None) -> dict[str, Any]:
    """Beat / miss / mix split of ``events`` on every measure (codes from the recipe)."""
    from generic_utils.print_night import welch_t
    from generic_utils.signal_lab import _pctl

    tagged = [(outcome(e, field, above), e) for e in events]
    beats = [e for o, e in tagged if o == "beat"]
    misses = [e for o, e in tagged if o == "miss"]
    out: dict[str, Any] = {"n": len(events), "n_beat": len(beats), "n_miss": len(misses),
                           "beat_rate": _r(len(beats) / (len(beats) + len(misses))) if beats or misses else None}
    for measure, code, _ in measures:
        groups = {"all": events, "beat": beats, "miss": misses,
                  "bb": [e for e in beats if str(e.get("surprise_mix") or "") == "B/B"],
                  "bm": [e for e in beats if str(e.get("surprise_mix") or "") == "B/M"]}
        for name, rows in groups.items():
            xs = [float(e[measure]) for e in rows if _f(e.get(measure)) is not None]
            out[f"{code}_{name}"] = _r(_mean(xs))
            if name in {"beat", "miss"}:
                out[f"{code}_{name}_hit"] = _r(_hit(xs))
                out[f"{code}_{name}_n"] = len(xs)
                out[f"{code}_{name}_q10"] = _r(_pctl(xs, 0.1)) if len(xs) >= 10 else None
                out[f"{code}_{name}_q90"] = _r(_pctl(xs, 0.9)) if len(xs) >= 10 else None
            if name == "bb":
                out[f"{code}_bb_n"] = len(xs)
        b = [float(e[measure]) for e in beats if _f(e.get(measure)) is not None]
        m = [float(e[measure]) for e in misses if _f(e.get(measure)) is not None]
        out[f"{code}_premium"] = _r(_mean(b) - _mean(m)) if b and m else None
        if base_beats is not None and b:
            rest = list(base_beats.get(measure) or [])
            out[f"{code}_beat_lift"] = _r(_mean(b) - _mean(rest)) if rest else None
            out[f"{code}_beat_lift_t"] = _r(welch_t(b, rest), 2) if rest else None
        if code == "rx":
            out["beat_sold"] = _r(sum(1 for x in b if x < 0) / len(b)) if b else None
            out["miss_bought"] = _r(sum(1 for x in m if x > 0) / len(m)) if m else None
    return out


def bar_reads(events: Sequence[Mapping[str, Any]], edges: Sequence[float], *, field: str, measure: str, min_n: int) -> dict[str, Any]:
    """Reaction mean per EPS-surprise quintile in ``events`` and the lowest quintile with a positive mean (the bar)."""
    bins: dict[int, list[float]] = defaultdict(list)
    for e in events:
        k = level_of(e.get(field), edges)
        if k is not None and _f(e.get(measure)) is not None:
            bins[k].append(float(e[measure]))
    out: dict[str, Any] = {}
    bar = None
    for k in range(1, len(edges) + 2):
        xs = bins.get(k) or []
        out[f"s{k}_rx"] = _r(_mean(xs)) if len(xs) >= min_n else None
        out[f"s{k}_n"] = len(xs)
        if bar is None and len(xs) >= min_n and (_mean(xs) or 0) > 0:
            bar = k
    out["bar_q"] = bar
    out["bar_eps"] = (None if bar is None else (_r(edges[bar - 2], 1) if bar > 1 else None))
    out["bar_text"] = ("no surprise quintile paid" if bar is None else
                       "any print (lowest quintile paid)" if bar == 1 else f"EPS surprise >= {edges[bar - 2]:+.1f}%")
    return out


def grid_cells(events: Sequence[Mapping[str, Any]], recipe: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Cells of the primary axis set and every fallback, with outcome splits and the bar; plus the edges used."""
    from generic_utils.signal_lab import _quantile_edges

    grid = recipe["grid"]
    levels = int(grid.get("levels", 3))
    field, above = str(recipe.get("beat_field", "eps_surprise")), float(recipe.get("beat_above", 0.0))
    measures = [tuple(m) for m in recipe["measures"]]
    all_axes = list(dict.fromkeys([a for a in grid["axes"]] + [a for fb in grid.get("fallbacks") or [] for a in fb]))
    edges = {a: _quantile_edges([float(e[f"ptr_{a}"]) for e in events if _f(e.get(f"ptr_{a}")) is not None], levels) for a in all_axes}
    sur_edges = _quantile_edges([float(e[field]) for e in events if _f(e.get(field)) is not None], int(recipe.get("surprise_bins", 5)))
    beats_all = [e for e in events if outcome(e, field, above) == "beat"]
    dates = sorted(str(e.get("print_date") or "") for e in events if e.get("print_date"))
    split = dates[len(dates) // 2] if dates else ""
    rows: list[dict[str, Any]] = []
    for axes in [list(grid["axes"])] + [list(fb) for fb in grid.get("fallbacks") or []]:
        groups: dict[tuple[int, ...], list[Mapping[str, Any]]] = defaultdict(list)
        for e in events:
            key = tuple(level_of(e.get(f"ptr_{a}"), edges[a]) for a in axes)
            if all(k is not None for k in key):
                groups[key].append(e)  # type: ignore[arg-type]
        for key in sorted(groups):
            inside = groups[key]
            ids = {id(e) for e in inside}
            rest = {m: [float(e[m]) for e in beats_all if id(e) not in ids and _f(e.get(m)) is not None] for m, _, _ in measures}
            row = {"axes": "+".join(axes), "cell": cell_label(axes, key)}
            for a, k in zip(axes, key):
                lo = f"{edges[a][k - 2]:+.1f}" if k > 1 else "min"
                hi = f"{edges[a][k - 1]:+.1f}" if k <= len(edges[a]) else "max"
                row[f"{a}_range"] = f"[{lo}, {hi})"
            row.update(outcome_stats(inside, measures, field=field, above=above, base_beats=rest))
            for half, keep in (("h1", lambda d: d < split), ("h2", lambda d: d >= split)):
                b_in = [float(e["react_fx"]) for e in inside if outcome(e, field, above) == "beat" and _f(e.get("react_fx")) is not None
                        and keep(str(e.get("print_date") or ""))]
                b_out = [float(e["react_fx"]) for e in beats_all if id(e) not in ids and _f(e.get("react_fx")) is not None
                         and keep(str(e.get("print_date") or ""))]
                row[f"rx_beat_lift_{half}"] = _r(_mean(b_in) - _mean(b_out)) if b_in and b_out else None
                row[f"n_beat_{half}"] = len(b_in)
            row.update(bar_reads(inside, sur_edges, field=field, measure="react_fx", min_n=int(recipe.get("bar_min_n", 12))))
            rows.append(row)
    return rows, {"axes": edges, "surprise": sur_edges}


def pointer_beat_cells(events: Sequence[Mapping[str, Any]], pointers: Sequence[Mapping[str, Any]], recipe: Mapping[str, Any],
                       pn_recipe: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, list[float]]]:
    """Per pointer quintile: beats inside vs beats outside (and misses), on each measure, with the print-night direction rule."""
    from generic_utils.print_night import _lift, direction, welch_t
    from generic_utils.signal_lab import _bucket, _quantile_edges

    q = int(pn_recipe.get("quantiles", 5))
    rule = {"t_min": float(pn_recipe.get("t_min", 2.0)), "min_n": int(pn_recipe.get("min_n", 40)),
            "require_halves": bool(pn_recipe.get("require_halves", True)), "require_hit": bool(pn_recipe.get("require_hit", True))}
    field, above = str(recipe.get("beat_field", "eps_surprise")), float(recipe.get("beat_above", 0.0))
    dates = sorted(str(e.get("print_date") or "") for e in events if e.get("print_date"))
    split = dates[len(dates) // 2] if dates else ""
    edges: dict[str, list[float]] = {}
    out: list[dict[str, Any]] = []
    for ptr in pointers:
        key = ptr["key"]
        if ptr.get("kind") == "category":
            continue
        edges[key] = _quantile_edges([float(e[f"ptr_{key}"]) for e in events if _f(e.get(f"ptr_{key}")) is not None], q)
        for side in ("beat", "miss"):
            sel = [e for e in events if outcome(e, field, above) == side]
            for measure, code, _ in recipe["measures"]:
                if measure == ptr.get("event"):
                    continue
                rows = [(_bucket(e.get(f"ptr_{key}"), edges[key]), float(e[measure]), str(e.get("print_date") or "") < split)
                        for e in sel if _f(e.get(measure)) is not None]
                rows = [r for r in rows if r[0] is not None]
                for bucket in sorted({b for b, _, _ in rows}):
                    inside = [x for b, x, _ in rows if b == bucket]
                    outside = [x for b, x, _ in rows if b != bucket]
                    cell = {"pointer": key, "family": ptr.get("family"), "label": ptr.get("label"), "bucket": bucket, "side": side,
                            "measure": measure, "code": code, "n": len(inside), "mean": _r(_mean(inside)), "hit": _r(_hit(inside)),
                            "lift": _r(_lift(inside, outside)), "lift_t": _r(welch_t(inside, outside), 2),
                            "half1_lift": _r(_lift([x for b, x, h in rows if b == bucket and h], [x for b, x, h in rows if b != bucket and h])),
                            "half2_lift": _r(_lift([x for b, x, h in rows if b == bucket and not h],
                                                   [x for b, x, h in rows if b != bucket and not h])),
                            "hit_lift": _r((_hit(inside) or 0) - (_hit(outside) or 0)) if inside and outside else None}
                    cell["dir"] = direction(cell, **rule)
                    out.append(cell)
    return out, edges


def own_prints(events: Sequence[Mapping[str, Any]], *, field: str, above: float) -> dict[str, dict[str, Any]]:
    by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for e in events:
        by[str(e.get("symbol"))].append(e)
    out = {}
    for sym, rows in by.items():
        tags = [(outcome(e, field, above), _f(e.get("react_fx"))) for e in rows]
        beats = [x for o, x in tags if o == "beat" and x is not None]
        misses = [x for o, x in tags if o == "miss" and x is not None]
        out[sym] = {"own_n": sum(1 for o, _ in tags if o), "own_beats": sum(1 for o, _ in tags if o == "beat"),
                    "own_rx_beat": _r(_mean(beats)), "own_rx_miss": _r(_mean(misses)),
                    "own_surprises": ", ".join(f"{_f(e.get(field)):+.0f}" for e in sorted(rows, key=lambda r: str(r.get("print_date")))
                                               if _f(e.get(field)) is not None)}
    return out


def _pick_cell(live: Mapping[str, Any], cells: Mapping[tuple[str, str], Mapping[str, Any]], edges: Mapping[str, Sequence[float]],
               grid: Mapping[str, Any]) -> tuple[dict[str, Any] | None, int]:
    for depth, axes in enumerate([list(grid["axes"])] + [list(fb) for fb in grid.get("fallbacks") or []]):
        key = tuple(level_of(live.get(a), edges.get(a) or []) for a in axes)
        if any(k is None for k in key):
            continue
        cell = cells.get(("+".join(axes), cell_label(axes, key)))  # type: ignore[arg-type]
        if cell and (cell.get("n_beat") or 0) >= int(grid.get("min_beats", 30)) and (cell.get("n_miss") or 0) >= int(grid.get("min_misses", 12)):
            return dict(cell), depth
    return None, -1


def build_beat_bar(pack_dir: str | Path, *, recipe: Mapping[str, Any] | None = None, ids: Sequence[str] = (),
                   weeks: Sequence[str] | None = None, store: str | Path | None = None) -> dict[str, Any]:
    """Tables ``names``, ``cells``, ``pointer_cells`` and ``base`` for the coming reporters of the pack."""
    from generic_utils.print_night import category, event_rows, live_features, own_abs_move, peer_reaction, store_fields
    from generic_utils.print_night import load_recipe as load_pn
    from generic_utils.print_night import _csv
    from generic_utils.signal_lab import _bucket
    from generic_utils.symbol_identity import ticker_map
    from generic_utils.week_plays import scan_day_of

    pack = Path(pack_dir) if Path(pack_dir).is_absolute() else REPO_ROOT / pack_dir
    recipe = dict(recipe or load_recipe())
    pn = load_pn()
    store_path = Path(store) if store else default_store_path(load_store_recipe())
    field, above = str(recipe.get("beat_field", "eps_surprise")), float(recipe.get("beat_above", 0.0))
    measures = [tuple(m) for m in recipe["measures"]]
    events = event_rows(pack, store_path, pn)
    plays_all = _csv(pack / "week_plays" / "plays.csv")
    if not plays_all:
        raise FileNotFoundError(f"{pack / 'week_plays' / 'plays.csv'} missing: run tv_scan_cli.py week-plays --pack {pack} first")
    wanted_weeks = set(weeks or recipe.get("weeks") or ["W0", "W1"])
    rows = [p for p in plays_all if str(p.get("phase")) == "pre" and str(p.get("week")) in wanted_weeks]
    if ids:
        by_ticker = ticker_map(p["symbol"] for p in plays_all)
        named = {i if ":" in i else by_ticker.get(i.upper(), i) for i in ids}
        rows = rows + [p for p in plays_all if p["symbol"] in named and p not in rows]

    cells, edges = grid_cells(events, recipe)
    by_cell = {(c["axes"], c["cell"]): c for c in cells}
    ptr_by_key = {p["key"]: p for p in pn.get("pointers") or []}
    pointers = [ptr_by_key[k] for k in recipe.get("pointer_keys") or [] if k in ptr_by_key]
    pcells, pedges = pointer_beat_cells(events, pointers, recipe, pn)
    plookup = {(c["pointer"], c["bucket"], c["side"], c["code"]): c for c in pcells}
    own = own_prints(events, field=field, above=above)
    base = [{"scope": "all prints", **outcome_stats(events, measures, field=field, above=above)}]

    symbols = [r["symbol"] for r in rows]
    need = list(dict.fromkeys(store_fields(pn)))
    feats, feat_day = live_features(store_path, symbols, need)
    outlook = {str(r.get("symbol")): r for r in _csv(pack / "outlook" / "names.csv")}
    anatomy = {str(r.get("symbol")): r for r in _csv(pack / "anatomy" / "candidates.csv")}
    prior = float(recipe.get("own_prior_prints", 4))
    starter = float(recipe.get("starter_usd", 5000))
    days, peer_min = int(pn.get("peer_window_days", 35)), int(pn.get("peer_min_n", 3))
    events_by_sym: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        events_by_sym.setdefault(str(e.get("symbol") or ""), []).append(e)

    names: list[dict[str, Any]] = []
    for row in rows:
        sym = row["symbol"]
        rec: dict[str, Any] = {"symbol": sym}
        rec.update({f: row.get(f) for f in recipe.get("plays_fields") or []})
        rec["mcap_b"] = _r((_f(row.get("mcap")) or 0) / 1e9, 1) if _f(row.get("mcap")) else None
        f = feats.get(sym) or {}
        live: dict[str, Any] = {}
        for ptr in pn.get("pointers") or []:
            key = ptr["key"]
            if ptr.get("store"):
                live[key] = _f(f.get(ptr["store"])) if sym in feats else (_f(row.get(ptr["calendar"])) if ptr.get("calendar") else None)
            elif ptr.get("derived") == "peer_rx":
                live[key] = peer_reaction(events, row.get("ind"), str(row.get("react_on") or ""), days=days, min_n=peer_min, exclude=sym)[0]
            elif ptr.get("derived") == "own_abs":
                live[key] = own_abs_move(events_by_sym.get(sym, []), sym, str(row.get("earn_date") or "9999")[:10])
            elif ptr.get("kind") == "category":
                live[key] = category(key, row.get(ptr.get("live") or ""))
            else:
                live[key] = _f(row.get(ptr.get("live") or ""))
        for a in recipe["grid"]["axes"]:
            rec[f"pos_{a}"] = _r(live.get(a), 2)
        cell, depth = _pick_cell(live, by_cell, edges["axes"], recipe["grid"])
        rec["cell"] = (cell or {}).get("cell")
        rec["cell_depth"] = depth if cell else None
        keep = ["n", "n_beat", "n_miss", "beat_rate", "beat_sold", "miss_bought", "bar_q", "bar_eps", "bar_text",
                "rx_beat_lift_h1", "rx_beat_lift_h2",
                "s1_rx", "s2_rx", "s3_rx", "s4_rx", "s5_rx"]
        for _, code, _ in measures:
            keep += [f"{code}_beat", f"{code}_beat_hit", f"{code}_miss", f"{code}_miss_hit", f"{code}_premium", f"{code}_bb", f"{code}_bm",
                     f"{code}_beat_lift", f"{code}_beat_lift_t", f"{code}_beat_q90", f"{code}_miss_q10"]
        rec.update({f"c_{k}": (cell or {}).get(k) for k in keep})
        mine = own.get(sym) or {}
        rec.update({k: mine.get(k) for k in ("own_n", "own_beats", "own_rx_beat", "own_rx_miss", "own_surprises")})
        base_rate = _f((cell or {}).get("beat_rate")) or _f(base[0].get("beat_rate")) or 0.0
        p_beat = ((mine.get("own_beats") or 0) + prior * base_rate) / ((mine.get("own_n") or 0) + prior)
        rec["p_beat"] = _r(p_beat)
        for _, code, _ in measures:
            hb, hm = _f((cell or {}).get(f"{code}_beat_hit")), _f((cell or {}).get(f"{code}_miss_hit"))
            mb, mm = _f((cell or {}).get(f"{code}_beat")), _f((cell or {}).get(f"{code}_miss"))
            rec[f"p_up_{code}"] = _r(p_beat * hb + (1 - p_beat) * hm) if hb is not None and hm is not None else None
            rec[f"ev_{code}"] = _r(p_beat * mb + (1 - p_beat) * mm, 2) if mb is not None and mm is not None else None
        q90, q10 = _f((cell or {}).get("rx_beat_q90")), _f((cell or {}).get("rx_miss_q10"))
        rec["beat_q90_usd"] = round(starter * q90 / 100) if q90 is not None else None
        rec["miss_q10_usd"] = round(starter * q10 / 100) if q10 is not None else None
        ups: dict[str, list[str]] = defaultdict(list)
        for ptr in pointers:
            key = ptr["key"]
            bucket = _bucket(live.get(key), pedges.get(key) or [])
            if not bucket:
                continue
            for side in ("beat", "miss"):
                for code in ("rx", "h28"):
                    c = plookup.get((key, bucket, side, code))
                    if c and c.get("dir") in {"up", "down"}:
                        ups[f"{side}_{code}_{c['dir']}"].append(key)
        for side in ("beat", "miss"):
            for code in ("rx", "h28"):
                for d, tag in (("up", "up"), ("down", "dn")):
                    lst = ups.get(f"{side}_{code}_{d}") or []
                    rec[f"{side}_{code}_{tag}"] = len(lst)
                    rec[f"{side}_{code}_{tag}_list"] = ", ".join(lst) or None
        o = outlook.get(sym) or {}
        rec.update({k: o.get(k) for k in recipe.get("outlook_fields") or []})
        an = anatomy.get(sym) or {}
        rec.update({k: an.get(k) for k in recipe.get("anatomy_fields") or []})
        names.append(rec)

    return {
        "names": names, "cells": cells, "pointer_cells": pcells, "base": base,
        "spec": {"pack": pack.as_posix(), "store": store_path.as_posix(), "scan_day": scan_day_of(pack), "weeks": sorted(wanted_weeks),
                 "feature_day": feat_day, "n_names": len(names), "n_events": len(events), "n_cells": len(cells),
                 "grid_axes": recipe["grid"]["axes"], "fallbacks": recipe["grid"].get("fallbacks"),
                 "axis_edges": {k: [round(x, 2) for x in v] for k, v in edges["axes"].items()},
                 "surprise_edges": [round(x, 2) for x in edges["surprise"]],
                 "n_pointer_arrows": sum(1 for c in pcells if c.get("dir") in {"up", "down"}), "n_pointer_cells": len(pcells),
                 "first_print": min((str(e.get("print_date")) for e in events), default=None),
                 "last_print": max((str(e.get("print_date")) for e in events), default=None)},
    }


def write_beat_bar(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"scan day {spec['scan_day']}; weeks {', '.join(spec['weeks'])}; {spec['n_names']} reporters before the print; store features of {spec['feature_day']}.",
        f"Cells on {spec['n_events']} lab prints {spec['first_print']}..{spec['last_print']}: terciles of {', '.join(spec['grid_axes'])} "
        f"(edges {spec['axis_edges']}); fallbacks {spec['fallbacks']} when the cell has too few beats or misses (cell_depth 0 = full grid).",
        "Outcome split: beat = EPS surprise > 0, miss otherwise; bb = B/B mix, bm = EPS beat with a revenue miss. <code>_beat / _miss = mean (pp), "
        "_hit = share > 0, _premium = beat minus miss, _beat_lift = a beat in this cell minus a beat elsewhere (Welch t). beat_sold = share of beats "
        "with a negative reaction vs the market; miss_bought = share of misses with a positive one.",
        f"Bar = the lowest EPS-surprise quintile (edges {spec['surprise_edges']}) whose mean reaction in the cell is positive; s1..s5_rx the means per quintile.",
        "p_beat = (own beats + prior x cell beat rate) / (own prints + prior). p_up_<code> / ev_<code> mix the beat and miss reads by p_beat: measured "
        "frequencies with a stated weight, not a score, never a gate or a size.",
        f"pointer_cells: beats (and misses) in one pointer quintile vs the rest of the beats (misses); dir by the print-night rule: "
        f"{spec['n_pointer_arrows']} of {spec['n_pointer_cells']} cells. Prints treated as independent (they cluster by date): optimistic.",
        "No ranking, no composite, no course.",
    ]
    return write_run_export(out_dir, tool="generic_utils.beat_bar",
                            tables={k: payload.get(k) or [] for k in ("names", "cells", "pointer_cells", "base")},
                            sources={"pack": spec["pack"], "store": spec["store"]}, notes=notes, extra={"spec": spec},
                            duckdb_name="beat_bar.duckdb")


def beat_bar_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_beat_bar(build_beat_bar(pack), pack / "beat_bar"))


if __name__ == "__main__":
    beat_bar_local_main()
