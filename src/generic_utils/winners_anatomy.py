"""Winners anatomy: what the panel store's biggest later movers looked like before they moved.

The event-side complement of signal_lab's average rank IC (knobs in config/generic_utils/anatomy.json):

- a tail mover is a name in the top (``up``) or bottom (``down``) ``tail_pct`` of its as_of day's forward return
  at a horizon; the base rate is therefore about ``tail_pct`` on every day;
- ``single_lift``: every feature cut into day quantiles, how often each quantile produced a tail mover against
  the base rate (lift), its mean excess, both halves and a t on non-overlapping blocks of horizon files;
- ``pair_lift``: pairs of extreme quantiles (``feat:hi`` = top quantile, ``feat:lo`` = bottom) as patterns, the
  same numbers plus distinct names and the synergy over the better single leg;
- ``holdout``: patterns mined only on labels that closed before the last window's entry file, then read on that
  window (what the matched names did against the rest, and which window winners they flagged);
- ``winner_mix``: the share of tail movers with a print inside the window, a print beat / miss, an industry
  wave (their industry in the top quantile of industries over the window), against all names;
- ``live``: the newest file's names on the stable full-sample patterns.

Does not pick, size, gate, or score 0-100. The tail share and quantiles are statistical conventions; the labels
(STRONG / STABLE / REGIME / THIN / WEAK) are significance bands, and many pairs are tested (stated in the spec).
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.panel_store import REPO_ROOT, feature_labels, feature_names, horizon_specs, load_store_recipe
from generic_utils.ranking import to_float
from generic_utils.series import qident
from generic_utils.signal_lab import avg_rank

DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "anatomy.json"


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: Any, digits: int = 4) -> float | None:
    number = to_float(value)
    return None if number is None or math.isnan(number) else round(number, digits)


# --- frame -------------------------------------------------------------------------------------------------


def build_frame(con: Any, recipe: Mapping[str, Any], features: Sequence[str], horizons: Sequence[str]) -> list[str]:
    """TEMP table ``anat``: features (with industry medians) left-joined to labels and to prints inside each window."""
    study = dict(recipe.get("study") or {})
    where = [f"f.mcap >= {float(study.get('min_market_cap_usd') or 0)!r}"]
    if study.get("first_day"):
        where.append(f"f.as_of >= DATE '{date.fromisoformat(str(study['first_day'])).isoformat()}'")
    if study.get("last_day"):
        where.append(f"f.as_of <= DATE '{date.fromisoformat(str(study['last_day'])).isoformat()}'")
    ind = dict(recipe.get("industry_features") or {})
    by, min_n = str(ind.get("by") or "ind"), int(ind.get("min_n", 5))
    ind_fields = [f for f in ind.get("fields") or [] if f in features]
    ind_cols = "".join(
        f", CASE WHEN count(*) OVER w >= {min_n} THEN median(f.{qident(f)}) OVER w END AS {qident('ind_' + f)}" for f in ind_fields)
    feat_cols = ", ".join(f"f.{qident(f)}" for f in features)
    lab_cols = "".join(f", l.{qident('fwd_' + h)}, l.{qident('lab_' + h)}" for h in horizons)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE anat0 AS
        SELECT f.as_of, f.symbol, f.ind, f.mcap, f.close, f.split_seq, {feat_cols}{lab_cols}{ind_cols}
        FROM features f LEFT JOIN labels l USING (symbol, as_of)
        WHERE {' AND '.join(where)}
        WINDOW w AS (PARTITION BY f.as_of, f.{qident(by)})""")
    joins, picks = [], []
    for k, h in enumerate(horizons):
        lab = qident("lab_" + h)
        joins.append(f"""LEFT JOIN (SELECT a.symbol, a.as_of, count(*) AS n, bool_or(p.eps_surprise > 0) AS beat,
                bool_or(p.eps_surprise < 0) AS miss FROM anat0 a JOIN prints p ON p.symbol = a.symbol
                AND p.react_on > a.as_of AND p.react_on <= a.{lab} GROUP BY ALL) p{k} USING (symbol, as_of)""")
        picks.append(f"coalesce(p{k}.n, 0) AS {qident('pr_' + h)}, coalesce(p{k}.beat, false) AS {qident('beat_' + h)}, "
                     f"coalesce(p{k}.miss, false) AS {qident('miss_' + h)}")
    con.execute(f"CREATE OR REPLACE TEMP TABLE anat AS SELECT anat0.*, {', '.join(picks)} FROM anat0 {' '.join(joins)}")
    return list(features) + [f"ind_{f}" for f in ind_fields]


def frame_arrays(con: Any, features: Sequence[str], horizons: Sequence[str]) -> dict[str, Any]:
    import numpy as np

    cols = ["as_of", "symbol", "ind", "close", "split_seq"] + list(features)
    for h in horizons:
        cols += [f"fwd_{h}", f"lab_{h}", f"pr_{h}", f"beat_{h}", f"miss_{h}"]
    raw = con.execute(f"SELECT {', '.join(qident(c) for c in cols)} FROM anat ORDER BY as_of, symbol").fetchnumpy()

    def num(name: str) -> Any:
        arr = raw[name]
        if isinstance(arr, np.ma.MaskedArray):
            return np.ma.filled(arr.astype(float), np.nan)
        return np.asarray(arr, dtype=float)

    def text(name: str) -> Any:
        arr = raw[name]
        return (np.ma.filled(arr, "") if isinstance(arr, np.ma.MaskedArray) else np.asarray(arr)).astype(str)

    def day(name: str) -> Any:
        arr = raw[name]
        if isinstance(arr, np.ma.MaskedArray):
            arr = np.ma.filled(arr.astype("datetime64[D]"), np.datetime64("NaT"))
        return np.asarray(arr).astype("datetime64[D]")

    days = day("as_of")
    uniq, starts = np.unique(days, return_index=True)
    out = {"days": uniq, "bounds": list(zip(starts.tolist(), starts[1:].tolist() + [len(days)])), "day_of_row": days,
           "symbol": text("symbol"), "ind": text("ind"), "close": num("close"), "split_seq": num("split_seq"),
           "X": np.column_stack([num(f) for f in features]), "features": list(features), "horizons": list(horizons)}
    for h in horizons:
        out[f"fwd_{h}"], out[f"lab_{h}"] = num(f"fwd_{h}"), day(f"lab_{h}")
        out[f"pr_{h}"], out[f"beat_{h}"], out[f"miss_{h}"] = num(f"pr_{h}"), num(f"beat_{h}") > 0, num(f"miss_{h}") > 0
    return out


def day_buckets(X: Any, bounds: Sequence[tuple[int, int]], q: int, min_n: int) -> Any:
    """Per-day quantile (1..q, 0 = missing or too few names that day) of every column of ``X``."""
    import numpy as np

    B = np.zeros(X.shape, dtype=np.int8)
    for lo, hi in bounds:
        block = X[lo:hi]
        for j in range(block.shape[1]):
            x = block[:, j]
            ok = ~np.isnan(x)
            n = int(ok.sum())
            if n < max(min_n, q):
                continue
            rank = avg_rank(x[ok])
            col = np.zeros(hi - lo, dtype=np.int8)
            col[ok] = np.minimum(q, np.floor((rank - 1.0) * q / n).astype(int) + 1)
            B[lo:hi, j] = col
    return B


def day_tails(y: Any, bounds: Sequence[tuple[int, int]], tail: float, min_names: int, winsor_pct: float) -> dict[str, Any]:
    """Top / bottom ``tail`` of each day's ``y`` and the day-median excess (winsorized); days under ``min_names`` drop out."""
    import numpy as np

    ok = np.zeros(len(y), dtype=bool)
    up, dn, fx = ok.copy(), ok.copy(), np.full(len(y), np.nan)
    for lo, hi in bounds:
        v = y[lo:hi]
        k = ~np.isnan(v)
        n = int(k.sum())
        if n < min_names:
            continue
        rank = avg_rank(v[k])
        idx = np.arange(lo, hi)[k]
        ok[idx] = True
        up[idx] = rank > (1.0 - tail) * n
        dn[idx] = rank <= tail * n
        low, high = np.percentile(v[k], [winsor_pct, 100.0 - winsor_pct])
        fx[idx] = np.clip(v[k], low, high) - np.median(v[k])
    return {"ok": ok, "up": up, "dn": dn, "fx": fx}


# --- counting ----------------------------------------------------------------------------------------------


def leg_matrix(B: Any, features: Sequence[str], q: int, extremes_only: bool) -> tuple[Any, list[tuple[str, int]]]:
    """Boolean leg columns: every quantile of every feature, or only the bottom (lo) and top (hi) one."""
    import numpy as np

    legs: list[tuple[str, int]] = []
    cols = []
    for j, f in enumerate(features):
        for b in ((1, q) if extremes_only else range(1, q + 1)):
            legs.append((f, b))
            cols.append(B[:, j] == b)
    return np.column_stack(cols), legs


def leg_name(leg: tuple[str, int], q: int) -> str:
    f, b = leg
    return f"{f}:{'lo' if b == 1 else 'hi' if b == q else 'q' + str(b)}"


def day_counts(L: Any, t: Mapping[str, Any], bounds: Sequence[tuple[int, int]], day_keep: Any, pairs: bool) -> dict[str, Any]:
    """Per kept day: totals, up / down tail counts, excess sums and positive counts by leg (or leg pair)."""
    import numpy as np

    keys = ("tot", "up", "dn", "sfx", "pos")
    out: dict[str, list[Any]] = {k: [] for k in keys}
    for (lo, hi), keep in zip(bounds, day_keep):
        if not keep:
            continue
        ok = t["ok"][lo:hi]
        if not ok.any():
            continue
        A = L[lo:hi][ok].astype(np.float32)
        weights = {"tot": None, "up": t["up"][lo:hi][ok], "dn": t["dn"][lo:hi][ok],
                   "sfx": t["fx"][lo:hi][ok], "pos": t["fx"][lo:hi][ok] > 0}
        for k in keys:
            w = weights[k]
            if pairs:
                out[k].append(A.T @ A if w is None else A.T @ (A * w.astype(np.float32)[:, None]))
            else:
                out[k].append(A.sum(axis=0) if w is None else w.astype(np.float32) @ A)
    return {k: np.stack(v) if v else None for k, v in out.items()}


def summarize_counts(c: Mapping[str, Any], files: int, base_up: float, base_dn: float, min_block_obs: int) -> dict[str, Any]:
    """Pooled lifts, mean excess, halves and block t (non-overlapping blocks of ``files`` days) along the day axis."""
    import numpy as np

    D = c["tot"].shape[0]
    with np.errstate(divide="ignore", invalid="ignore"):
        def pooled(sl: slice) -> dict[str, Any]:
            tot = c["tot"][sl].sum(axis=0)
            return {"tot": tot, "up": c["up"][sl].sum(axis=0) / tot / base_up, "dn": c["dn"][sl].sum(axis=0) / tot / base_dn,
                    "fx": c["sfx"][sl].sum(axis=0) / tot, "hit": c["pos"][sl].sum(axis=0) / tot}

        full, h1, h2 = pooled(slice(None)), pooled(slice(0, D // 2)), pooled(slice(D // 2, None))
        starts = np.arange(0, D, max(1, files))
        blk = {k: np.add.reduceat(c[k], starts, axis=0) for k in ("tot", "up", "dn", "sfx")}
        thin = blk["tot"] < min_block_obs

        def block_t(values: Any) -> tuple[Any, Any]:
            import warnings

            v = np.where(thin, np.nan, values)
            n = (~np.isnan(v)).sum(axis=0)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                mean = np.nanmean(v, axis=0)
                sd = np.nanstd(v, axis=0, ddof=1)
            t = np.where((n >= 3) & (sd > 0), mean / (sd / np.sqrt(np.maximum(n, 1))), np.nan)
            return t, n

        t_up, n_blocks = block_t(blk["up"] / blk["tot"] - base_up)
        t_dn, _ = block_t(blk["dn"] / blk["tot"] - base_dn)
        t_fx, _ = block_t(blk["sfx"] / blk["tot"])
        up_blocks = np.where(thin, np.nan, blk["up"] / blk["tot"] / base_up)
        dn_blocks = np.where(thin, np.nan, blk["dn"] / blk["tot"] / base_dn)
        return {"tot": full["tot"], "n_days": (c["tot"] > 0).sum(axis=0), "n_blocks": n_blocks,
                "lift_up": full["up"], "lift_dn": full["dn"], "fx": full["fx"], "hit": full["hit"],
                "lift_up_h1": h1["up"], "lift_up_h2": h2["up"], "lift_dn_h1": h1["dn"], "lift_dn_h2": h2["dn"],
                "fx_h1": h1["fx"], "fx_h2": h2["fx"], "t_up": t_up, "t_dn": t_dn, "t_fx": t_fx,
                "up_blocks_won": (up_blocks > 1).sum(axis=0), "dn_blocks_won": (dn_blocks > 1).sum(axis=0)}


def _asym(row: dict[str, Any]) -> None:
    """asym_up = lift_up / lift_dn (one tail over the other): above 1 the leg makes winners without making losers alike."""
    up, dn = row.get("lift_up"), row.get("lift_dn")
    row["asym_up"] = _r(up / dn, 3) if up and dn else None
    row["asym_dn"] = _r(dn / up, 3) if up and dn else None


def label(side: str, t: float | None, h1: float | None, h2: float | None, fx1: float | None, fx2: float | None,
          names: int | None, spec: Mapping[str, Any], *, t_need: float, min_names: int, t_fx: float | None = None) -> str:
    """STRONG / STABLE / REGIME / THIN / WEAK for one side of one leg or pattern (a significance band, not a gate).

    STABLE needs the tail-share t, both halves over the base, the mean excess on the side's sign in both halves and
    its block t at ``excess_t``: a leg that only adds volatility lifts both tails and fails the excess t.
    """
    if names is not None and names < min_names:
        return "THIN"
    if t is None or math.isnan(t):
        return "WEAK"
    sign = 1.0 if side == "up" else -1.0
    halves = all(v is not None and not math.isnan(v) and v > 1.0 for v in (h1, h2))
    excess = all(v is not None and not math.isnan(v) and v * sign > 0 for v in (fx1, fx2))
    excess = excess and t_fx is not None and not math.isnan(t_fx) and t_fx * sign >= float(spec.get("excess_t", 2.0))
    if t >= max(t_need, float(spec.get("strong_t", 3.0))) and halves and excess:
        return "STRONG"
    if t >= t_need and halves and excess:
        return "STABLE"
    if t >= t_need:
        return "REGIME"
    return "WEAK"


# --- studies -----------------------------------------------------------------------------------------------


def _tail_setup(arr: Mapping[str, Any], recipe: Mapping[str, Any], h: str) -> dict[str, Any]:
    study = dict(recipe.get("study") or {})
    return day_tails(arr[f"fwd_{h}"], arr["bounds"], float(recipe.get("tail_pct", 5.0)) / 100.0,
                     int(study.get("min_names", 200)), float(study.get("winsor_pct", 1.0)))


def _bases(t: Mapping[str, Any]) -> tuple[float, float]:
    n = max(1, int(t["ok"].sum()))
    return float(t["up"].sum()) / n, float(t["dn"].sum()) / n


def single_study(arr: Mapping[str, Any], B: Any, recipe: Mapping[str, Any], files: Mapping[str, int],
                 labels_txt: Mapping[str, str]) -> list[dict[str, Any]]:
    q = int(recipe.get("quantiles", 5))
    spec = dict(recipe.get("labels") or {})
    min_block = int((recipe.get("pairs") or {}).get("min_block_obs", 10))
    L, legs = leg_matrix(B, arr["features"], q, extremes_only=False)
    out: list[dict[str, Any]] = []
    for h in arr["horizons"]:
        t = _tail_setup(arr, recipe, h)
        base_up, base_dn = _bases(t)
        c = day_counts(L, t, arr["bounds"], [True] * len(arr["bounds"]), pairs=False)
        if c["tot"] is None:
            continue
        s = summarize_counts(c, files.get(h, 1), base_up, base_dn, min_block)
        for k, leg in enumerate(legs):
            row = {"horizon": h, "feature": leg[0], "bucket": leg[1], "leg": leg_name(leg, q), "label": labels_txt.get(leg[0])}
            row.update({key: _r(s[key][k], 3) for key in ("lift_up", "lift_dn", "fx", "hit", "lift_up_h1", "lift_up_h2",
                                                          "lift_dn_h1", "lift_dn_h2", "fx_h1", "fx_h2", "t_up", "t_dn", "t_fx")})
            row.update(n_obs=int(s["tot"][k]), n_days=int(s["n_days"][k]), n_blocks=int(s["n_blocks"][k]),
                       up_blocks_won=int(s["up_blocks_won"][k]), dn_blocks_won=int(s["dn_blocks_won"][k]))
            _asym(row)
            for side in ("up", "dn"):
                row[f"verdict_{side}"] = label(side, row[f"t_{side}"], row[f"lift_{side}_h1"], row[f"lift_{side}_h2"],
                                               row["fx_h1"], row["fx_h2"], None, spec, t_need=float(spec.get("t", 2.0)),
                                               min_names=0, t_fx=row["t_fx"])
            out.append(row)
    return out


def pair_study(arr: Mapping[str, Any], B: Any, recipe: Mapping[str, Any], files: Mapping[str, int], h: str,
               day_keep: Sequence[bool], singles: Mapping[tuple[str, str], Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Patterns (pairs of extreme legs) for one horizon on the kept days; ``keep`` per side by lift, then names and labels."""
    import numpy as np

    q = int(recipe.get("quantiles", 5))
    spec, ps = dict(recipe.get("labels") or {}), dict(recipe.get("pairs") or {})
    L, legs = leg_matrix(B, arr["features"], q, extremes_only=True)
    t = _tail_setup(arr, recipe, h)
    base_up, base_dn = _bases(t)
    c = day_counts(L, t, arr["bounds"], day_keep, pairs=True)
    if c["tot"] is None:
        return []
    s = summarize_counts(c, files.get(h, 1), base_up, base_dn, int(ps.get("min_block_obs", 10)))
    K = len(legs)
    feat = np.array([leg[0] for leg in legs])
    iu, ju = np.triu_indices(K, k=1)
    valid = (feat[iu] != feat[ju]) & (s["tot"][iu, ju] >= int(ps.get("min_obs", 300))) & (s["n_days"][iu, ju] >= int(ps.get("min_days", 20)))
    iu, ju = iu[valid], ju[valid]
    n_tests = int(len(iu))
    keep = int(ps.get("keep", 400))
    chosen: dict[tuple[int, int], set[str]] = {}
    with np.errstate(divide="ignore", invalid="ignore"):
        asym = {"up": s["lift_up"][iu, ju] / s["lift_dn"][iu, ju], "dn": s["lift_dn"][iu, ju] / s["lift_up"][iu, ju]}
    for side in ("up", "dn"):
        lift = s[f"lift_{side}"][iu, ju]
        # keep the top by lift (where the tail movers are) and the top by asymmetry among lifts over 1 (one tail, not both)
        by_asym = np.where(lift > 1.0, np.nan_to_num(asym[side], nan=-1.0, posinf=-1.0), -1.0)
        for score in (np.nan_to_num(lift, nan=-1.0), by_asym):
            for o in np.argsort(-score)[:keep]:
                chosen.setdefault((int(iu[o]), int(ju[o])), set()).add(side)
    pairs = list(chosen)
    if not pairs:
        return []
    names_tot, names_up, names_dn = _distinct_names(L, t, arr, day_keep, pairs)
    min_names = int(ps.get("min_names", 25))
    pair_t = float(spec.get("pair_t", spec.get("t", 2.0)))
    out = []
    for k, (i, j) in enumerate(pairs):
        a, b = leg_name(legs[i], q), leg_name(legs[j], q)
        row = {"horizon": h, "pattern": f"{a} & {b}", "leg_a": a, "leg_b": b, "sides": ",".join(sorted(chosen[(i, j)]))}
        row.update({key: _r(s[key][i, j], 3) for key in ("lift_up", "lift_dn", "fx", "hit", "lift_up_h1", "lift_up_h2",
                                                         "lift_dn_h1", "lift_dn_h2", "fx_h1", "fx_h2", "t_up", "t_dn", "t_fx")})
        _asym(row)
        row.update(n_obs=int(s["tot"][i, j]), n_days=int(s["n_days"][i, j]), n_blocks=int(s["n_blocks"][i, j]),
                   up_blocks_won=int(s["up_blocks_won"][i, j]), dn_blocks_won=int(s["dn_blocks_won"][i, j]),
                   n_names=int(names_tot[k]), n_names_up=int(names_up[k]), n_names_dn=int(names_dn[k]))
        if singles:
            for side in ("up", "dn"):
                best = max((v for v in ((singles.get((h, a)) or {}).get(f"lift_{side}"),
                                        (singles.get((h, b)) or {}).get(f"lift_{side}")) if v is not None), default=None)
                row[f"syn_{side}"] = _r(row[f"lift_{side}"] / best, 3) if best and row[f"lift_{side}"] is not None else None
        for side in ("up", "dn"):
            # THIN: few distinct names overall, or the side's tail hits come from a handful of names repeating
            tail_names = row["n_names_up"] if side == "up" else row["n_names_dn"]
            names = row["n_names"] if tail_names >= max(1, min_names // 3) else 0
            row[f"verdict_{side}"] = label(side, row[f"t_{side}"], row[f"lift_{side}_h1"], row[f"lift_{side}_h2"], row["fx_h1"],
                                           row["fx_h2"], names, spec, t_need=pair_t, min_names=min_names, t_fx=row["t_fx"])
        row["n_tests"] = n_tests
        out.append(row)
    return out


def _distinct_names(L: Any, t: Mapping[str, Any], arr: Mapping[str, Any], day_keep: Sequence[bool],
                    pairs: Sequence[tuple[int, int]]) -> tuple[Any, Any, Any]:
    import numpy as np

    sym_codes_all = np.unique(arr["symbol"], return_inverse=True)[1]
    S = int(sym_codes_all.max()) + 1 if len(sym_codes_all) else 0
    A = np.array([p[0] for p in pairs])
    Bj = np.array([p[1] for p in pairs])
    seen = {k: np.zeros((len(pairs), S), dtype=bool) for k in ("tot", "up", "dn")}
    for (lo, hi), keep in zip(arr["bounds"], day_keep):
        if not keep:
            continue
        ok = t["ok"][lo:hi]
        if not ok.any():
            continue
        block = L[lo:hi][ok]
        mask = block[:, A] & block[:, Bj]
        codes = sym_codes_all[lo:hi][ok]
        seen["tot"][:, codes] |= mask.T
        seen["up"][:, codes] |= (mask & t["up"][lo:hi][ok][:, None]).T
        seen["dn"][:, codes] |= (mask & t["dn"][lo:hi][ok][:, None]).T
    return seen["tot"].sum(axis=1), seen["up"].sum(axis=1), seen["dn"].sum(axis=1)


def winner_mix(arr: Mapping[str, Any], B: Any, recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Share of up / down tail movers with each driver (print in window, beat, miss, industry wave, quantile states) vs all."""
    import numpy as np

    q = int(recipe.get("quantiles", 5))
    ind_codes = np.unique(arr["ind"], return_inverse=True)[1]
    min_ind = int((recipe.get("industry_features") or {}).get("min_n", 5))
    out = []
    for h in arr["horizons"]:
        t = _tail_setup(arr, recipe, h)
        wave = np.zeros(len(t["ok"]), dtype=bool)
        for lo, hi in arr["bounds"]:
            ok = t["ok"][lo:hi]
            if not ok.any():
                continue
            codes, fx = ind_codes[lo:hi][ok], t["fx"][lo:hi][ok]
            meds = {}
            for code in np.unique(codes):
                vals = fx[codes == code]
                if len(vals) >= min_ind:
                    meds[code] = float(np.median(vals))
            if len(meds) < q:
                continue
            cut = np.percentile(list(meds.values()), 100.0 * (q - 1) / q)
            hot = {code for code, m in meds.items() if m >= cut}
            wave[np.arange(lo, hi)[ok]] = np.isin(codes, list(hot))
        pr = arr[f"pr_{h}"] > 0
        drivers = {"print in window": pr, "print beat in window": arr[f"beat_{h}"], "print miss in window": arr[f"miss_{h}"],
                   "industry wave (top-quantile industry over the window)": wave, "print or industry wave": pr | wave,
                   "neither print nor industry wave": ~(pr | wave)}
        for f in recipe.get("mix_features") or []:
            if f in arr["features"]:
                j = arr["features"].index(f)
                drivers[f"{f}:hi before"] = B[:, j] == q
                drivers[f"{f}:lo before"] = B[:, j] == 1
        ok = t["ok"]
        for name, flag in drivers.items():
            f_all, f_up, f_dn = flag[ok].mean(), flag[ok & t["up"]].mean(), flag[ok & t["dn"]].mean()
            fx = t["fx"][ok & flag]
            out.append({"horizon": h, "driver": name, "share_all": _r(f_all, 3), "share_up_tail": _r(f_up, 3),
                        "share_down_tail": _r(f_dn, 3), "lift_up": _r(f_up / f_all if f_all else None, 2),
                        "lift_dn": _r(f_dn / f_all if f_all else None, 2), "fx_mean": _r(fx.mean() if len(fx) else None, 3),
                        "hit": _r((fx > 0).mean() if len(fx) else None, 3), "n_obs": int((ok & flag).sum())})
    return out


def _legs_now(B_row: Any, features: Sequence[str], q: int) -> set[str]:
    return {f"{f}:{'lo' if b == 1 else 'hi'}" for f, b in zip(features, B_row) if b in (1, q)}


def _pattern_hits(legs_by_row: Sequence[set[str]], patterns: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    return [[p for p in patterns if p["leg_a"] in legs and p["leg_b"] in legs] for legs in legs_by_row]


def _stable(rows: Sequence[Mapping[str, Any]], side: str, n: int) -> list[Mapping[str, Any]]:
    """STRONG / STABLE patterns of a side, most one-sided first (asymmetry, then lift)."""
    good = [r for r in rows if r.get(f"verdict_{side}") in {"STRONG", "STABLE"}]
    good.sort(key=lambda r: (-(r.get(f"asym_{side}") or 0), -(r.get(f"lift_{side}") or 0)))
    return good[:n]


def holdout_study(arr: Mapping[str, Any], B: Any, recipe: Mapping[str, Any], files: Mapping[str, int],
                  singles: Mapping[tuple[str, str], Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Per horizon: mine patterns on labels closed by the window's entry file, then read the window (entry -> newest file)."""
    import numpy as np

    q = int(recipe.get("quantiles", 5))
    rec = dict(recipe.get("recent") or {})
    n_pat, top = int(rec.get("patterns_per_side", 40)), float(rec.get("top_pct", 5.0)) / 100.0
    days = arr["days"]
    last_lo, last_hi = arr["bounds"][-1]
    summary, patterns_out, winners_out = [], [], []
    for h in arr["horizons"]:
        k = files.get(h, 1)
        if len(days) <= k:
            continue
        entry = days[-1 - k]
        lab = arr[f"lab_{h}"]
        keep = []
        for lo, hi in arr["bounds"]:
            labs = lab[lo:hi]
            labs = labs[~np.isnat(labs)]
            keep.append(bool(len(labs)) and labs.max() <= entry)
        mined = pair_study(arr, B, recipe, files, h, keep, singles)
        e_lo, e_hi = arr["bounds"][len(days) - 1 - k]
        now = {s: i for i, s in enumerate(arr["symbol"][last_lo:last_hi])}
        rows = []
        for i in range(e_lo, e_hi):
            j = now.get(arr["symbol"][i])
            if j is None:
                continue
            j += last_lo
            if arr["split_seq"][i] != arr["split_seq"][j] or not arr["close"][i]:
                continue
            rows.append((i, (arr["close"][j] / arr["close"][i] - 1.0) * 100.0))
        if len(rows) < 50:
            continue
        idx = np.array([r[0] for r in rows])
        ret = np.array([r[1] for r in rows])
        fx = ret - np.median(ret)
        rank = avg_rank(ret)
        win_up, win_dn = rank > (1.0 - top) * len(ret), rank <= top * len(ret)
        legs_by_row = [_legs_now(B[i], arr["features"], q) for i in idx]
        for side, win in (("up", win_up), ("dn", win_dn)):
            pats = _stable(mined, side, n_pat)
            hits = _pattern_hits(legs_by_row, pats)
            matched = np.array([bool(hh) for hh in hits])
            summary.append({
                "horizon": h, "side": side, "entry": str(entry), "exit": str(days[-1]), "mined_days": int(sum(keep)),
                "n_patterns": len(pats), "n_names": len(ret), "n_matched": int(matched.sum()),
                "fx_matched": _r(fx[matched].mean() if matched.any() else None, 3),
                "fx_rest": _r(fx[~matched].mean() if (~matched).any() else None, 3),
                "hit_matched": _r((fx[matched] > 0).mean() if matched.any() else None, 3),
                "tail_share_matched": _r(win[matched].mean() if matched.any() else None, 3), "tail_share_base": _r(win.mean(), 3),
                "tail_lift_realized": _r(win[matched].mean() / win.mean() if matched.any() and win.mean() else None, 2),
                "tail_caught": int((win & matched).sum()), "tail_n": int(win.sum())})
            for p in pats:
                m = np.array([p in hh for hh in hits])
                patterns_out.append({"horizon": h, "side": side, "pattern": p["pattern"], "mined_lift": p[f"lift_{side}"],
                                     "mined_fx": p["fx"], "mined_t": p[f"t_{side}"], "n_now": int(m.sum()),
                                     "fx_window": _r(fx[m].mean() if m.any() else None, 3),
                                     "tail_hits": int((m & win).sum()),
                                     "tail_lift_window": _r(win[m].mean() / win.mean() if m.any() and win.mean() else None, 2)})
            order = np.argsort(-fx if side == "up" else fx)
            for o in order[: int(rec.get("winners_rows", 60))]:
                i = idx[o]
                winners_out.append({
                    "horizon": h, "side": side, "symbol": arr["symbol"][i], "ind": arr["ind"][i], "entry": str(entry),
                    "ret": _r(ret[o], 2), "fx": _r(fx[o], 2), "n_patterns": len(hits[o]),
                    "patterns": "; ".join(p["pattern"] for p in hits[o][:4]),
                    "legs_at_entry": ", ".join(sorted(legs_by_row[o]))[:400],
                    **{f: _r(arr["X"][i, arr["features"].index(f)], 2) for f in ("r_1m", "r_3m", "eps_rev_3m", "pt_up", "ey_fwd", "dte", "rvol_20", "ind_r_1m") if f in arr["features"]}})
    return {"holdout": summary, "holdout_patterns": patterns_out, "recent_movers": winners_out}


def live_study(arr: Mapping[str, Any], B: Any, recipe: Mapping[str, Any], pairs: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    q = int(recipe.get("quantiles", 5))
    lv = dict(recipe.get("live") or {})
    n_pat = int(lv.get("patterns_per_side", 40))
    lo, hi = arr["bounds"][-1]
    legs_by_row = [_legs_now(B[i], arr["features"], q) for i in range(lo, hi)]
    detail = [f for f in lv.get("detail_features") or [] if f in arr["features"]]
    wide: dict[str, dict[str, Any]] = {}
    long_rows = []
    for h in arr["horizons"]:
        for side in ("up", "dn"):
            pats = _stable([p for p in pairs if p["horizon"] == h], side, n_pat)
            for k, hits in enumerate(_pattern_hits(legs_by_row, pats)):
                if not hits:
                    continue
                i = lo + k
                sym = arr["symbol"][i]
                row = wide.setdefault(sym, {"symbol": sym, "ind": arr["ind"][i], "close": _r(arr["close"][i], 2),
                                            **{f: _r(arr["X"][i, arr["features"].index(f)], 2) for f in detail}})
                best = max(hits, key=lambda p: p[f"lift_{side}"] or 0)
                row[f"{side}_n_{h}"] = len(hits)
                row[f"{side}_lift_{h}"] = best[f"lift_{side}"]
                row[f"{side}_fx_{h}"] = best["fx"]
                row[f"{side}_best_{h}"] = best["pattern"]
                for p in hits:
                    long_rows.append({"symbol": sym, "ind": arr["ind"][i], "horizon": h, "side": side, "pattern": p["pattern"],
                                      "lift": p[f"lift_{side}"], "fx": p["fx"], "t": p[f"t_{side}"], "verdict": p[f"verdict_{side}"],
                                      "n_names": p["n_names"]})
    cols = [f"{s}_{k}_{h}" for s in ("up", "dn") for h in arr["horizons"] for k in ("n", "lift", "fx", "best")]
    rows = [{**{c: None for c in cols}, **r} for r in wide.values()]
    rows.sort(key=lambda r: -sum((r.get(f"up_n_{h}") or 0) for h in arr["horizons"]))
    return {"live": rows, "live_patterns": long_rows}


def parse_pattern(text: str, features: Sequence[str]) -> list[tuple[str, str, Any]]:
    """``feat:lo|hi|qN`` (day quantile) or ``feat<=x`` / ``>=`` / ``<`` / ``>`` (caller's bound), joined by ``&``."""
    import re

    legs = []
    for part in (p.strip() for p in str(text).split("&")):
        m = re.fullmatch(r"(\w+):(lo|hi|q[1-9])", part) or re.fullmatch(r"(\w+)\s*(<=|>=|<|>)\s*(-?[\d.]+)", part)
        if not m or m.group(1) not in features:
            raise ValueError(f"pattern leg {part!r}: expected feat:lo|hi|qN or feat<=x with a known feature")
        legs.append((m.group(1), m.group(2), float(m.group(3)) if m.lastindex == 3 else None))
    return legs


def pattern_mask(legs: Sequence[tuple[str, str, Any]], arr: Mapping[str, Any], B: Any, q: int) -> Any:
    import numpy as np

    mask = np.ones(len(arr["symbol"]), dtype=bool)
    for f, op, value in legs:
        j = arr["features"].index(f)
        if value is None:
            b = 1 if op == "lo" else q if op == "hi" else int(op[1:])
            mask &= B[:, j] == b
        else:
            x = arr["X"][:, j]
            with np.errstate(invalid="ignore"):
                mask &= {"<=": x <= value, ">=": x >= value, "<": x < value, ">": x > value}[op]
    return mask


def pattern_tests(arr: Mapping[str, Any], B: Any, recipe: Mapping[str, Any], files: Mapping[str, int],
                  tests: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """Caller-named patterns: full-sample lifts and excess per horizon, by month, on the last window, and today's names."""
    import numpy as np

    q = int(recipe.get("quantiles", 5))
    min_block = int((recipe.get("pairs") or {}).get("min_block_obs", 10))
    detail = [f for f in (recipe.get("live") or {}).get("detail_features") or [] if f in arr["features"]]
    months = np.array([str(d)[:7] for d in arr["day_of_row"]])
    days = arr["days"]
    last_lo, last_hi = arr["bounds"][-1]
    summary, by_month, live = [], [], []
    for text in tests:
        legs = parse_pattern(text, arr["features"])
        mask = pattern_mask(legs, arr, B, q)
        for i in np.nonzero(mask[last_lo:last_hi])[0] + last_lo:
            live.append({"test": text, "symbol": arr["symbol"][i], "ind": arr["ind"][i], "close": _r(arr["close"][i], 2),
                         **{f: _r(arr["X"][i, arr["features"].index(f)], 2) for f in detail}})
        for h in arr["horizons"]:
            t = _tail_setup(arr, recipe, h)
            base_up, base_dn = _bases(t)
            c = day_counts(mask[:, None], t, arr["bounds"], [True] * len(arr["bounds"]), pairs=False)
            if c["tot"] is None:
                continue
            s = summarize_counts(c, files.get(h, 1), base_up, base_dn, min_block)
            m = mask & t["ok"]
            row = {"test": text, "horizon": h, "n_obs": int(m.sum()), "n_names": int(len(set(arr["symbol"][m]))),
                   **{k: _r(s[k][0], 3) for k in ("lift_up", "lift_dn", "fx", "hit", "fx_h1", "fx_h2", "t_up", "t_fx")},
                   "n_blocks": int(s["n_blocks"][0])}
            _asym(row)
            k = files.get(h, 1)
            if len(days) > k:
                e_lo, e_hi = arr["bounds"][len(days) - 1 - k]
                now = {sym: j for j, sym in enumerate(arr["symbol"][last_lo:last_hi])}
                rets, hit_flags = [], []
                for i in range(e_lo, e_hi):
                    j = now.get(arr["symbol"][i])
                    if j is None or arr["split_seq"][i] != arr["split_seq"][j + last_lo] or not arr["close"][i]:
                        continue
                    rets.append((arr["close"][j + last_lo] / arr["close"][i] - 1.0) * 100.0)
                    hit_flags.append(bool(mask[i]))
                if rets:
                    ret, on = np.array(rets), np.array(hit_flags)
                    fx = ret - np.median(ret)
                    win = avg_rank(ret) > 0.95 * len(ret)
                    row.update(window_entry=str(days[-1 - k]), window_n=int(on.sum()),
                               window_fx=_r(fx[on].mean() if on.any() else None, 2), window_hit=_r((fx[on] > 0).mean() if on.any() else None, 2),
                               window_tail_lift=_r(win[on].mean() / win.mean() if on.any() else None, 2))
            summary.append(row)
            for month in sorted(set(months[m])):
                mm = m & (months == month)
                by_month.append({"test": text, "horizon": h, "month": month, "n_obs": int(mm.sum()),
                                 "fx": _r(t["fx"][mm].mean(), 3), "hit": _r((t["fx"][mm] > 0).mean(), 3),
                                 "lift_up": _r(t["up"][mm].mean() / base_up, 2), "lift_dn": _r(t["dn"][mm].mean() / base_dn, 2)})
    return {"tests": summary, "tests_month": by_month, "tests_live": live}


def candidate_rows(tests_live: Sequence[Mapping[str, Any]], recipe: Mapping[str, Any], pack: str | Path | None) -> dict[str, list[dict[str, Any]]]:
    """Letters per name (``pat_up`` / ``pat_dn`` in the recipe's letter order, ``-`` none) joined to the pack's outlook row.

    ``n_durable`` counts the up letters in ``letters.durable`` (patterns that held in every month); a count, not a score.
    Without an outlook CSV in the pack the rows carry the letters only.
    """
    import csv

    letters = dict(recipe.get("letters") or {})
    up, down = dict(letters.get("up") or {}), dict(letters.get("down") or {})
    durable = str(letters.get("durable") or "")
    by_test: dict[str, set[str]] = {}
    base: dict[str, Mapping[str, Any]] = {}
    for r in tests_live:
        by_test.setdefault(r["symbol"], set()).add(r["test"])
        base.setdefault(r["symbol"], r)
    spec = dict(recipe.get("candidates") or {})
    outlook: dict[str, dict[str, Any]] = {}
    path = Path(pack) / str(spec.get("outlook_csv") or "outlook/names.csv") if pack else None
    if path and path.exists():
        with path.open(encoding="utf-8-sig", newline="") as fh:
            outlook = {row["symbol"]: row for row in csv.DictReader(fh)}
    fields = [f for f in spec.get("fields") or []]
    longs, shorts = [], []
    for sym, tests in by_test.items():
        pat_up = "".join(k for k, v in up.items() if v in tests) or "-"
        pat_dn = "".join(k for k, v in down.items() if v in tests) or "-"
        o = outlook.get(sym)
        if outlook and o is None:
            continue
        row = {"symbol": sym, "ind": base[sym].get("ind"), "pat_up": pat_up,
               "n_durable": sum(1 for k in durable if k in pat_up), "pat_dn": pat_dn}
        for f in fields:
            v = (o or {}).get(f)
            row[f] = (to_float(v) if to_float(v) is not None else v) if v not in (None, "") else None
        if pat_up != "-":
            longs.append(row)
        if pat_dn != "-":
            shorts.append(row)
    longs.sort(key=lambda r: (-r["n_durable"], -len(r["pat_up"].strip("-"))))
    return {"candidates": longs, "short_candidates": shorts}


def build_anatomy(*, store: str | Path | None = None, recipe: Mapping[str, Any] | None = None,
                  store_recipe: Mapping[str, Any] | None = None, pack: str | Path | None = None) -> dict[str, Any]:
    from generic_utils.panel_store import connect_read

    recipe = dict(recipe or load_recipe())
    store_recipe = dict(store_recipe or load_store_recipe())
    known = {h["name"]: int(h.get("files") or 1) for h in horizon_specs(store_recipe)}
    horizons = [h for h in recipe.get("horizons") or known if h in known]
    wanted = recipe.get("features")
    names = feature_names(store_recipe) if wanted in (None, "all") else [f for f in wanted if f in feature_names(store_recipe)]
    names = [f for f in names if f not in set(recipe.get("exclude_features") or [])]
    labels_txt = feature_labels(store_recipe)
    con = connect_read(store)
    try:
        features = build_frame(con, recipe, names, horizons)
        arr = frame_arrays(con, features, horizons)
    finally:
        con.close()
    for f in features:
        labels_txt.setdefault(f, f"industry median of {f[4:]}" if f.startswith("ind_") else f)
    study = dict(recipe.get("study") or {})
    B = day_buckets(arr["X"], arr["bounds"], int(recipe.get("quantiles", 5)), int(study.get("min_feature_n", 100)))
    singles = single_study(arr, B, recipe, known, labels_txt)
    single_map = {(s["horizon"], s["leg"]): s for s in singles}
    pairs: list[dict[str, Any]] = []
    for h in horizons:
        pairs += pair_study(arr, B, recipe, known, h, [True] * len(arr["bounds"]), single_map)
    tables: dict[str, list[dict[str, Any]]] = {"single_lift": singles, "pair_lift": pairs, "winner_mix": winner_mix(arr, B, recipe)}
    tables.update(holdout_study(arr, B, recipe, known, single_map))
    tables.update(live_study(arr, B, recipe, pairs))
    if recipe.get("tests"):
        tables.update(pattern_tests(arr, B, recipe, known, recipe["tests"]))
        if recipe.get("letters"):
            tables.update(candidate_rows(tables["tests_live"], recipe, pack))
    n_tests = {h: next((p["n_tests"] for p in pairs if p["horizon"] == h), 0) for h in horizons}
    spec = {"days": len(arr["days"]), "first": str(arr["days"][0]), "last": str(arr["days"][-1]), "rows": int(len(arr["symbol"])),
            "horizons": horizons, "files": {h: known[h] for h in horizons}, "tail_pct": recipe.get("tail_pct"),
            "features": features, "pair_tests": n_tests,
            "pair_chance_t3": {h: round(n * 0.00135 * 2, 1) for h, n in n_tests.items()}}
    return {"tables": tables, "spec": spec}


def highlights_text(payload: Mapping[str, Any], top: int = 12) -> str:
    t, spec = payload["tables"], payload["spec"]
    lines = [f"anatomy: {spec['days']} days {spec['first']}..{spec['last']}, {spec['rows']} name-days, tail {spec['tail_pct']}%.",
             f"pair tests per horizon {spec['pair_tests']}; about {spec['pair_chance_t3']} clear t 3 on one side by chance.", ""]
    for h in spec["horizons"]:
        for side in ("up", "dn"):
            rows = [p for p in t["pair_lift"] if p["horizon"] == h and p.get(f"verdict_{side}") in {"STRONG", "STABLE"}]
            rows.sort(key=lambda p: (-(p.get(f"asym_{side}") or 0), -(p.get(f"lift_{side}") or 0)))
            lines.append(f"[{h} {side}] stable patterns {len(rows)}")
            for p in rows[:top]:
                lines.append(f"  {p['pattern']}: asym {p[f'asym_{side}']} lift {p[f'lift_{side}']} (h1 {p[f'lift_{side}_h1']} h2 {p[f'lift_{side}_h2']}, t {p[f't_{side}']}, "
                             f"blocks {p[f'{side}_blocks_won']}/{p['n_blocks']}) fx {p['fx']} hit {p['hit']} other-tail lift "
                             f"{p['lift_dn' if side == 'up' else 'lift_up']} names {p['n_names']} syn {p.get('syn_' + side)}")
    lines += ["", "holdout (patterns mined before the window's entry, read on the window):"]
    for s in t["holdout"]:
        lines.append(f"  {s['horizon']} {s['side']} {s['entry']}->{s['exit']}: {s['n_patterns']} patterns, matched {s['n_matched']}/{s['n_names']} "
                     f"fx {s['fx_matched']} vs rest {s['fx_rest']}, tail share {s['tail_share_matched']} vs {s['tail_share_base']} "
                     f"(lift {s['tail_lift_realized']}), caught {s['tail_caught']}/{s['tail_n']}")
    if t.get("tests"):
        lines += ["", "named patterns (full sample | last window | months):"]
        for r in t["tests"]:
            months = " ".join(f"{m['month'][5:]}:{m['fx']:+.1f}" for m in t["tests_month"]
                              if m["test"] == r["test"] and m["horizon"] == r["horizon"] and m["fx"] is not None)
            lines.append(f"  {r['horizon']} {r['test']}: n {r['n_obs']} names {r['n_names']} lift up {r['lift_up']} dn {r['lift_dn']} "
                         f"fx {r['fx']} (h1 {r['fx_h1']} h2 {r['fx_h2']}, t {r['t_fx']}) hit {r['hit']} | window {r.get('window_entry')} "
                         f"n {r.get('window_n')} fx {r.get('window_fx')} hit {r.get('window_hit')} | {months}")
        live = {}
        for r in t["tests_live"]:
            live[r["test"]] = live.get(r["test"], 0) + 1
        lines.append("  today: " + "; ".join(f"{k} {v}" for k, v in live.items()))
    lines += ["", "winner mix (share of up tail / down tail / all):"]
    for m in t["winner_mix"]:
        lines.append(f"  {m['horizon']} {m['driver']}: up {m['share_up_tail']} dn {m['share_down_tail']} all {m['share_all']} "
                     f"(lift up {m['lift_up']} dn {m['lift_dn']}) fx {m['fx_mean']}")
    return "\n".join(lines) + "\n"


def write_anatomy(payload: Mapping[str, Any], out_dir: str | Path, *, store: str | Path | None = None) -> dict[str, Any]:
    from generic_utils.panel_store import default_store_path
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"study floor US $1B+ on the as_of day; {spec['days']} files {spec['first']}..{spec['last']}; tail = top / bottom {spec['tail_pct']}% of each day's forward return.",
        "lift_up = share of a leg's (or pattern's) name-days that landed in the up tail, over the base share (about the tail %); lift_dn the same for the down tail. fx is the mean forward return minus the day median (winsorized).",
        "t_up / t_dn / t_fx: plain t across non-overlapping blocks of horizon files (block-pooled share minus base). h1 / h2 are the two halves of the sample.",
        "Legs: feat:lo = bottom day quantile, feat:hi = top day quantile, q2..q4 between. ind_<f> is the industry median of f that day (industries with min_n names).",
        f"Patterns are pairs of extreme legs; {spec['pair_tests']} pairs tested per horizon, so about {spec['pair_chance_t3']} clear t 3 on a side by chance. Labels use pair_t; n_names counts distinct symbols.",
        "holdout: patterns mined only on days whose label closed on or before the window's entry file; the window runs from that file to the newest one. This is the honest read; the full-sample patterns include the days they are scored on.",
        "winner_mix: an industry wave is a name in the top quantile of industries (median excess over the same window). A print in the window is a detected print whose reaction session falls inside it.",
        "live: the newest file's names on the stable full-sample patterns (STRONG / STABLE). Matching a pattern is evidence for reading order, not a buy list; sum of patterns is not a score.",
        "Seven months of one market; 28d has about six independent blocks.",
    ]
    exported = write_run_export(out_dir, tool="generic_utils.winners_anatomy", tables=dict(payload["tables"]),
                                sources={"panel_store": Path(store or default_store_path()).as_posix()},
                                notes=notes, extra={"spec": spec}, duckdb_name="anatomy.duckdb")
    (Path(out_dir) / "highlights.log").write_text(highlights_text(payload), encoding="utf-8")
    return exported
