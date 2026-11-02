"""Alpha model: a walk-forward, self-correcting cross-sectional forecast of excess return per horizon.

Method (knobs in config/generic_utils/alpha_model.json), on the panel store's study floor:

1. Each day, every feature becomes a standardized rank (missing = 0, the middle), and each feature's rank IC
   against the later excess return (``fx_<h>``: return minus that day's universe median, winsorized) is kept.
2. A forecast for day t uses only days whose labels were already known at t (the label file is on or before
   t), within ``lookback_days``. Each feature's ICs are averaged with an age decay (``half_life_days``) and
   shrunk by their own evidence: IC * t^2 / (t^2 + shrink_c), where t counts the non-overlapping periods only;
   a feature under ``select_t`` gets no weight. A feature whose IC keeps its sign keeps its weight; one that
   flips or fades loses it. That is the self-correction: no weight is set by hand.
3. ``pred_<h>`` = the standardized rank of (today's ranks x weights), times that composite's own slope on the
   newest known days: a forecast in percent excess return over the horizon, against the universe median. It
   is a separate column and a separate order, never added to the pack's rr / bo / left, never a 0-100.
   ``fit.mode = ridge`` fits all features together each day instead; on 2026 data it lost to the best single
   feature at every horizon (correlated screener scores flip each other's signs), so it is an option only.
4. Every past forecast is scored out of sample (rank IC, top and bottom decile excess) next to baselines that
   use the same past-only data: equal-weight signs, the best single feature, and fixed single features.
   ``state`` says whether the horizon has earned trust (OK), is unproven (WEAK / NO_TRACK), or has lost its
   recent edge (DEGRADED / FAILING).
5. Bands: the out-of-sample excess return of each forecast decile, divided by each name's own volatility over
   the horizon, gives decile quantiles; a live name's band is close * (1 + quantile * its volatility). The
   band assumes a flat market: shift it by the market's move.

No eligibility cutoffs: the universe floor is the study floor, and courses stay with the operator.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from generic_utils.panel_store import (
    REPO_ROOT,
    feature_family,
    feature_labels,
    feature_names,
    horizon_specs,
    load_store_recipe,
)
from generic_utils.ranking import to_float
from generic_utils.series import qident
from generic_utils.signal_lab import avg_rank, build_study, newey_west_t, plain_t

_f = to_float
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "alpha_model.json"


def load_model_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def recipe_hash(recipe: Mapping[str, Any]) -> str:
    return hashlib.sha1(json.dumps(recipe, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]


def _r(value: Any, digits: int = 4) -> float | None:
    number = _f(value)
    return None if number is None or math.isnan(number) or math.isinf(number) else round(number, digits)


# --- data --------------------------------------------------------------------------------------------------


def model_arrays(con: Any, store_recipe: Mapping[str, Any], recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Study rows as numpy arrays: per-day bounds, raw features, excess labels, label dates, context columns."""
    import numpy as np

    known = {h["name"]: h for h in horizon_specs(store_recipe)}
    horizons = [h for h in recipe.get("horizons") or [] if h in known]
    fit = dict(recipe.get("fit") or {})
    universe = dict(recipe.get("universe") or {})
    lab_like = {"study": {"min_market_cap_usd": universe.get("min_market_cap_usd"), "winsor_pct": fit.get("winsor_pct", 1.0)},
                "regime_field": "r_1m"}
    build_study(con, store_recipe, lab_like, horizons)
    available = set(feature_names(store_recipe))
    features = [f for f in recipe.get("features") or [] if f in available]
    bands = dict(recipe.get("bands") or {})
    context = ["close", "mcap", str(bands.get("vol_field") or "rvol_20"), str(bands.get("vol_fallback") or "atrp")]
    numeric = list(dict.fromkeys(features + context))
    cols = ["as_of", "symbol", "ind"] + numeric + [f"fx_{h}" for h in horizons] + [f"lab_{h}" for h in horizons]
    raw = con.execute(f"SELECT {', '.join(qident(c) for c in cols)} FROM study ORDER BY as_of, symbol").fetchnumpy()

    def num(name: str) -> Any:
        arr = raw[name]
        return np.ma.filled(arr.astype(float), np.nan) if isinstance(arr, np.ma.MaskedArray) else np.asarray(arr, dtype=float)

    def text(name: str) -> Any:
        arr = raw[name]
        return np.ma.filled(arr, "") if isinstance(arr, np.ma.MaskedArray) else np.asarray(arr)

    days = np.asarray(raw["as_of"]).astype("datetime64[D]")
    uniq, starts = np.unique(days, return_index=True)
    bounds = list(zip(starts.tolist(), starts[1:].tolist() + [len(days)]))
    lab_max = []
    for lo, hi in bounds:
        per = {}
        for h in horizons:
            arr = raw[f"lab_{h}"][lo:hi]
            arr = arr.compressed() if isinstance(arr, np.ma.MaskedArray) else arr
            vals = np.asarray(arr).astype("datetime64[D]")
            vals = vals[~np.isnat(vals)] if len(vals) else vals
            per[h] = vals.max() if len(vals) else None
        lab_max.append(per)
    return {
        "days": uniq, "bounds": bounds, "symbols": text("symbol"), "ind": text("ind"),
        "X": np.column_stack([num(f) for f in features]), "features": features,
        "Y": {h: num(f"fx_{h}") for h in horizons}, "lab_max": lab_max, "horizons": horizons,
        "files": {h: int(known[h].get("files") or 1) for h in horizons},
        "context": {c: num(c) for c in context},
    }


def rank_z(block: Any) -> tuple[Any, Any]:
    """Columns of ``block`` as standardized average ranks (NaN -> 0) and each column's coverage."""
    import numpy as np

    n_rows, n_cols = block.shape
    z = np.zeros((n_rows, n_cols))
    cover = np.zeros(n_cols)
    for j in range(n_cols):
        col = block[:, j]
        ok = ~np.isnan(col)
        n = int(ok.sum())
        cover[j] = n / n_rows if n_rows else 0.0
        if n >= 3:
            ranks = avg_rank(col[ok])
            z[ok, j] = (ranks - (n + 1) / 2.0) / math.sqrt((n * n - 1) / 12.0)
    return z, cover


def _spearman(a: Any, b: Any) -> float | None:
    import numpy as np

    if len(a) < 3:
        return None
    ra, rb = avg_rank(a), avg_rank(b)
    ra, rb = ra - ra.mean(), rb - rb.mean()
    den = float(np.sqrt((ra * ra).sum() * (rb * rb).sum()))
    return float((ra * rb).sum() / den) if den > 0 else None


# --- fit ---------------------------------------------------------------------------------------------------


def day_fits(arrays: Mapping[str, Any], recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Per day: standardized ranks (kept for prediction) and, per horizon, each feature's rank IC (exact Spearman on the
    names that carry it) and, in ridge mode, the ridge coefficients of all usable features together."""
    import numpy as np

    fit = dict(recipe.get("fit") or {})
    mode = str(fit.get("mode") or "ic")
    lam = float(fit.get("ridge_lambda", 0.1))
    min_cov = float(fit.get("min_coverage", 0.5))
    min_names = int((recipe.get("universe") or {}).get("min_names", 200))
    n_feat = len(arrays["features"])
    zs, covers = [], []
    coefs = {h: np.full((len(arrays["bounds"]), n_feat), np.nan) for h in arrays["horizons"]}
    ics = {h: np.full((len(arrays["bounds"]), n_feat), np.nan) for h in arrays["horizons"]}
    for k, (lo, hi) in enumerate(arrays["bounds"]):
        block = arrays["X"][lo:hi]
        z, cover = rank_z(block)
        zs.append(z)
        covers.append(cover)
        usable = cover >= min_cov
        for h in arrays["horizons"]:
            y = arrays["Y"][h][lo:hi]
            ok = ~np.isnan(y)
            if ok.sum() < min_names or not usable.any():
                continue
            for j in np.flatnonzero(usable):
                both = ok & ~np.isnan(block[:, j])
                if both.sum() >= min_names:
                    ics[h][k, j] = _spearman(block[both, j], y[both])
            if mode == "ridge":
                zu, yv = z[ok][:, usable], y[ok]
                n = len(yv)
                coefs[h][k, usable] = np.linalg.solve(zu.T @ zu + lam * n * np.eye(zu.shape[1]), zu.T @ yv)
    return {"z": zs, "cover": covers, "coef": coefs, "ic": ics, "mode": mode}


def _weighted(values: Any, weights: Any) -> tuple[Any, Any, Any]:
    """Column-wise weighted mean, weighted sd and Kish effective count, ignoring NaN cells."""
    import numpy as np

    ok = ~np.isnan(values)
    w = np.where(ok, weights[:, None], 0.0)
    v = np.where(ok, values, 0.0)
    sw = w.sum(axis=0)
    mean = np.divide((w * v).sum(axis=0), sw, out=np.full(values.shape[1], np.nan), where=sw > 0)
    var = np.divide((w * (v - np.nan_to_num(mean)) ** 2).sum(axis=0), sw, out=np.full(values.shape[1], np.nan), where=sw > 0)
    n_eff = np.divide(sw ** 2, (w ** 2).sum(axis=0), out=np.zeros(values.shape[1]), where=sw > 0)
    return mean, np.sqrt(var), n_eff


def _trailing(values: Any, w: Any, files: int, c: float) -> tuple[Any, Any, Any]:
    """Age-weighted mean, its t on non-overlapping periods, and the mean shrunk by t^2 / (t^2 + c)."""
    import numpy as np

    mean, sd, n_eff = _weighted(values, w)
    n_ind = np.maximum(n_eff / files, 1.0)
    t_stat = np.divide(mean, sd / np.sqrt(n_ind), out=np.zeros_like(mean), where=(sd > 0) & ~np.isnan(mean))
    return mean, t_stat, np.nan_to_num(mean) * t_stat ** 2 / (t_stat ** 2 + c)


def _zscore_rank(values: Any) -> Any:
    n = len(values)
    if n < 3:
        return values * 0.0
    return (avg_rank(values) - (n + 1) / 2.0) / math.sqrt((n * n - 1) / 12.0)


def walk_forward(arrays: Mapping[str, Any], fits: Mapping[str, Any], recipe: Mapping[str, Any]) -> dict[str, Any]:
    """For every day and horizon: weights from past-known days only, the forecast, and its out-of-sample score.

    ic mode (default): weight = trailing rank IC shrunk by its t (features under select_t get none); the
    composite's standardized rank is turned into percent by its own slope on the newest known days (calib_days).
    ridge mode: the trailing ridge coefficients, shrunk the same way, times today's ranks.
    """
    import numpy as np

    fit = dict(recipe.get("fit") or {})
    mode = fits.get("mode") or "ic"
    lookback = int(fit.get("lookback_days", 150))
    half_life = float(fit.get("half_life_days", 25))
    min_train = int(fit.get("min_train_days", 15))
    embargo = int(fit.get("embargo_days", 0))
    c = float(fit.get("shrink_c", 4.0))
    select_t = float(fit.get("select_t", 0.0))
    top_k = int(fit.get("top_k", 0))
    calib_days = int(fit.get("calib_days", 30))
    min_names = int((recipe.get("universe") or {}).get("min_names", 200))
    days = arrays["days"]
    feats = arrays["features"]
    baselines = [b for b in recipe.get("baselines") or [] if b in feats]
    out: dict[str, Any] = {}
    for h in arrays["horizons"]:
        files = arrays["files"][h]
        source = fits["ic"][h] if mode == "ic" else fits["coef"][h]
        coef_hist = np.full((len(days), len(feats)), np.nan)
        raw_hist = np.full((len(days), len(feats)), np.nan)
        t_hist = np.full((len(days), len(feats)), np.nan)
        ic_hist = np.full((len(days), len(feats)), np.nan)
        slope_hist = np.full(len(days), np.nan)
        preds: list[Any] = [None] * len(days)
        oos: list[dict[str, Any]] = []
        calib: list[tuple[int, Any, Any]] = []
        for k, t in enumerate(days):
            known = [s for s in range(k)
                     if arrays["lab_max"][s].get(h) is not None
                     and arrays["lab_max"][s][h] + np.timedelta64(embargo, "D") <= t
                     and (t - days[s]).astype(int) <= lookback
                     and not np.all(np.isnan(source[s]))]
            if len(known) < min_train:
                continue
            idx = np.array(known)
            w = 0.5 ** ((k - idx) / half_life)
            raw_mean, t_stat, shrunk = _trailing(source[idx], w, files, c)
            if select_t > 0:
                shrunk = np.where(np.abs(t_stat) >= select_t, shrunk, 0.0)
            if top_k > 0 and np.count_nonzero(shrunk) > top_k:
                keep = np.argsort(-np.abs(np.where(shrunk != 0, t_stat, 0.0)))[:top_k]
                shrunk = np.where(np.isin(np.arange(len(shrunk)), keep), shrunk, 0.0)
            ic_mean, ic_t, _ = _trailing(fits["ic"][h][idx], w, files, c)
            z = fits["z"][k]
            if mode == "ic":
                if not np.any(shrunk):
                    continue
                slopes, sw = [], []
                for s in idx[-calib_days:]:
                    lo_s, hi_s = arrays["bounds"][s]
                    y_s = arrays["Y"][h][lo_s:hi_s]
                    ok_s = ~np.isnan(y_s)
                    if ok_s.sum() >= min_names:
                        zc_s = _zscore_rank((fits["z"][s] @ shrunk)[ok_s])
                        slopes.append(float((zc_s * y_s[ok_s]).mean()))
                        sw.append(0.5 ** ((k - s) / half_life))
                if not slopes:
                    continue
                slope = float(np.dot(slopes, sw) / np.sum(sw))
                pred = slope * _zscore_rank(z @ shrunk)
                slope_hist[k] = slope
            else:
                pred = z @ shrunk
            coef_hist[k], raw_hist[k], t_hist[k], ic_hist[k] = shrunk, raw_mean, t_stat, ic_mean
            preds[k] = pred
            lo, hi = arrays["bounds"][k]
            y = arrays["Y"][h][lo:hi]
            ok = ~np.isnan(y)
            if ok.sum() < min_names:
                continue
            active = ~np.isnan(ic_mean)
            methods = {"model": pred}
            if active.any():
                methods["ew_sign"] = z[:, active] @ np.sign(ic_mean[active]) / active.sum()
                best = int(np.nanargmax(np.where(active, np.abs(ic_t), -1)))
                methods["best_single"] = z[:, best] * np.sign(ic_mean[best])
            for b in baselines:
                j = feats.index(b)
                if fits["cover"][k][j] > 0:
                    methods[b] = z[:, j]
            rec: dict[str, Any] = {"horizon": h, "as_of": str(t), "n": int(ok.sum()), "n_train": len(known),
                                   "slope": _r(slope_hist[k], 3)}
            yv = y[ok]
            for name, p in methods.items():
                rec[f"ic_{name}"] = _r(_spearman(p[ok], yv))
            order = np.argsort(pred[ok])
            tenth = max(1, len(order) // 10)
            rec["top"] = _r(yv[order[-tenth:]].mean(), 3)
            rec["bottom"] = _r(yv[order[:tenth]].mean(), 3)
            rec["top_hit"] = _r((yv[order[-tenth:]] > 0).mean(), 3)
            oos.append(rec)
            calib.append((k, pred[ok], ok))
        out[h] = {"coef": coef_hist, "raw": raw_hist, "t": t_hist, "ic": ic_hist, "slope": slope_hist, "preds": preds,
                  "oos": oos, "calib": calib, "mode": mode}
    return out


# --- evaluation, calibration, live -------------------------------------------------------------------------


def oos_summary(wf: Mapping[str, Any], arrays: Mapping[str, Any], recipe: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    state_spec = dict(recipe.get("state") or {})
    recent = int(state_spec.get("recent_days", 20))
    ok_t = float(state_spec.get("ok_t", 2.0))
    summary, states = [], []
    for h, block in wf.items():
        files = arrays["files"][h]
        rows = block["oos"]
        methods = sorted({k[3:] for r in rows for k in r if k.startswith("ic_")}, key=lambda m: (m != "model", m))
        for m in methods:
            ics = [r[f"ic_{m}"] for r in rows if r.get(f"ic_{m}") is not None]
            if not ics:
                continue
            t_plain = plain_t(ics)
            rec = {"horizon": h, "method": m, "n_days": len(ics), "n_indep": round(len(ics) / files, 1),
                   "ic_mean": _r(sum(ics) / len(ics)), "ic_t_indep": _r(t_plain / math.sqrt(files), 2) if t_plain is not None else None,
                   "ic_t_nw": _r(newey_west_t(ics, files - 1), 2), "ic_recent": _r(sum(ics[-recent:]) / len(ics[-recent:])),
                   "ic_h1": _r(sum(ics[: len(ics) // 2]) / max(1, len(ics) // 2)),
                   "ic_h2": _r(sum(ics[len(ics) // 2:]) / max(1, len(ics) - len(ics) // 2)),
                   "ic_pos": _r(sum(1 for v in ics if v > 0) / len(ics), 3)}
            if m == "model":
                tops = [r["top"] for r in rows if r.get("top") is not None]
                bots = [r["bottom"] for r in rows if r.get("bottom") is not None]
                rec["top"] = _r(sum(tops) / len(tops), 3) if tops else None
                rec["bottom"] = _r(sum(bots) / len(bots), 3) if bots else None
                rec["spread"] = _r(rec["top"] - rec["bottom"], 3) if tops and bots else None
                rec["top_hit"] = _r(sum(r["top_hit"] for r in rows if r.get("top_hit") is not None) / len(rows), 3) if rows else None
                rec["first"], rec["last"] = rows[0]["as_of"], rows[-1]["as_of"]
            summary.append(rec)
        model = next((s for s in summary if s["horizon"] == h and s["method"] == "model"), None)
        if model is None or model["n_days"] < 10:
            state = "NO_TRACK"
        elif (model["ic_mean"] or 0) <= 0:
            state = "FAILING"
        elif (model["ic_recent"] or 0) <= 0:
            state = "DEGRADED"
        elif (model["ic_t_indep"] or 0) >= ok_t:
            state = "OK"
        else:
            state = "WEAK"
        # fixed baselines are features named in the recipe (picked with hindsight); the fair bar is the walk-forward ones
        others = [s for s in summary if s["horizon"] == h and s["method"] in {"ew_sign", "best_single"} and s.get("ic_mean") is not None]
        best_other = max(others, key=lambda s: s["ic_mean"], default=None)
        fixed = [s for s in summary if s["horizon"] == h and s["method"] not in {"model", "ew_sign", "best_single"}
                 and s.get("ic_mean") is not None]
        best_fixed = max(fixed, key=lambda s: s["ic_mean"], default=None)
        states.append({"horizon": h, "state": state, "oos_days": model["n_days"] if model else 0,
                       "n_indep": model["n_indep"] if model else 0,
                       "ic_mean": model["ic_mean"] if model else None, "ic_t_indep": model["ic_t_indep"] if model else None,
                       "ic_recent": model["ic_recent"] if model else None, "spread": model.get("spread") if model else None,
                       "best_baseline": best_other["method"] if best_other else None,
                       "best_baseline_ic": best_other["ic_mean"] if best_other else None,
                       "beats_baselines": bool(model and best_other and (model["ic_mean"] or 0) > (best_other["ic_mean"] or 0)),
                       "best_fixed": best_fixed["method"] if best_fixed else None,
                       "best_fixed_ic": best_fixed["ic_mean"] if best_fixed else None})
    return summary, states


def calibrate(wf: Mapping[str, Any], arrays: Mapping[str, Any], recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Per horizon and forecast decile: realized excess and its volatility-scaled quantiles, out of sample."""
    import numpy as np

    spec = dict(recipe.get("bands") or {})
    qs = [float(q) for q in spec.get("quantiles") or [0.2, 0.5, 0.8]]
    n_dec = int(spec.get("deciles", 10))
    vol = arrays["context"][str(spec.get("vol_field") or "rvol_20")]
    fallback = arrays["context"][str(spec.get("vol_fallback") or "atrp")]
    table: list[dict[str, Any]] = []
    quant: dict[str, dict[int, list[float]]] = {}
    for h, block in wf.items():
        files = arrays["files"][h]
        dec_all, y_all, u_all = [], [], []
        for k, pred_ok, ok in block["calib"]:
            lo, hi = arrays["bounds"][k]
            y = arrays["Y"][h][lo:hi][ok]
            sigma = np.where(np.isnan(vol[lo:hi][ok]), fallback[lo:hi][ok], vol[lo:hi][ok])
            dec = np.minimum(n_dec, (avg_rank(pred_ok) - 1) * n_dec // len(pred_ok) + 1).astype(int)
            scale = sigma * math.sqrt(files)
            good = scale > 0
            dec_all.append(dec[good])
            y_all.append(y[good])
            u_all.append(y[good] / scale[good])
        quant[h] = {}
        if not dec_all:
            continue
        dec_v, y_v, u_v = np.concatenate(dec_all), np.concatenate(y_all), np.concatenate(u_all)
        for d in range(1, n_dec + 1):
            sel = dec_v == d
            if sel.sum() < int(spec.get("min_obs", 200)):
                continue
            uq = [float(np.quantile(u_v[sel], q)) for q in qs]
            quant[h][d] = uq
            rec = {"horizon": h, "decile": d, "n": int(sel.sum()), "fx_mean": _r(y_v[sel].mean(), 3),
                   "fx_med": _r(np.median(y_v[sel]), 3), "hit": _r((y_v[sel] > 0).mean(), 3)}
            for q, v in zip(qs, uq):
                rec[f"u_q{int(round(q * 100))}"] = _r(v, 3)
            table.append(rec)
    return {"table": table, "quant": quant, "qs": qs}


def live_rows(arrays: Mapping[str, Any], fits: Mapping[str, Any], wf: Mapping[str, Any], calib: Mapping[str, Any],
              recipe: Mapping[str, Any], store_recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Forecasts, deciles, bands and top drivers for every name on the newest day."""
    import numpy as np

    k = len(arrays["days"]) - 1
    lo, hi = arrays["bounds"][k]
    feats = arrays["features"]
    n_dec = int((recipe.get("bands") or {}).get("deciles", 10))
    n_drivers = int(recipe.get("drivers", 3))
    spec = dict(recipe.get("bands") or {})
    vol = arrays["context"][str(spec.get("vol_field") or "rvol_20")][lo:hi]
    fallback = arrays["context"][str(spec.get("vol_fallback") or "atrp")][lo:hi]
    sigma = np.where(np.isnan(vol), fallback, vol)
    close = arrays["context"]["close"][lo:hi]
    mcap = arrays["context"]["mcap"][lo:hi]
    z = fits["z"][k]
    raw = arrays["X"][lo:hi]
    rows = [{"symbol": str(arrays["symbols"][lo + i]), "ind": str(arrays["ind"][lo + i]) or None,
             "close": _r(close[i], 4), "mcap_b": _r(mcap[i] / 1e9, 2), "vol_d": _r(sigma[i], 3)} for i in range(hi - lo)]
    labels = {q: int(round(q * 100)) for q in calib["qs"]}
    for h, block in wf.items():
        pred = block["preds"][k]
        if pred is None:
            continue
        coef = block["coef"][k]
        if block.get("mode") == "ic":
            # pred = slope x standardized rank of (ranks @ weights): per-feature contributions in percent, to first order
            score = z @ coef
            coef = coef * (block["slope"][k] / score.std()) if score.std() > 0 else coef * 0.0
        dec = np.minimum(n_dec, (avg_rank(pred) - 1) * n_dec // len(pred) + 1).astype(int)
        files = arrays["files"][h]
        for i, rec in enumerate(rows):
            rec[f"pred_{h}"] = _r(pred[i], 3)
            rec[f"dec_{h}"] = int(dec[i])
            uq = calib["quant"].get(h, {}).get(int(dec[i]))
            move = sigma[i] * math.sqrt(files) if sigma[i] == sigma[i] else None
            rec[f"move_{h}"] = _r(move, 2)
            if uq and move and close[i] == close[i]:
                for q, u in zip(calib["qs"], uq):
                    rec[f"b{h}_q{labels[q]}"] = _r(close[i] * (1 + u * move / 100.0), 4)
            contrib = coef * z[i]
            order = np.argsort(-np.abs(contrib))[:n_drivers]
            rec[f"why_{h}"] = ", ".join(f"{feats[j]} {contrib[j]:+.2f}" for j in order if abs(contrib[j]) >= 0.005) or None
    for name in ("last_surprise", "rev_surprise", "dsp", "dte", "p_def", "p_dvc", "r_1m", "pt_up", "eps_rev_3m"):
        if name in feats:
            j = feats.index(name)
            for i, rec in enumerate(rows):
                rec[name] = _r(raw[i, j], 3)
    return rows


def weights_table(wf: Mapping[str, Any], arrays: Mapping[str, Any], fits: Mapping[str, Any],
                  store_recipe: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Coefficients on the newest day (shrunk, raw, t, trailing IC), 20 days earlier, sign flips; and a history."""
    import numpy as np

    family, labels = feature_family(store_recipe), feature_labels(store_recipe)
    feats = arrays["features"]
    k = len(arrays["days"]) - 1
    table, history = [], []
    for h, block in wf.items():
        coef = block["coef"]
        valid = [i for i in range(len(arrays["days"])) if not np.all(np.isnan(coef[i]))]
        if not valid:
            continue
        last = valid[-1]
        prev = valid[max(0, len(valid) - 21)]
        window = coef[valid[-60:]]
        for j, f in enumerate(feats):
            series = window[:, j]
            signs = np.sign(series[~np.isnan(series) & (np.abs(series) > 1e-9)])
            table.append({
                "horizon": h, "feature": f, "family": family.get(f), "label": labels.get(f),
                "coef": _r(coef[last, j]), "coef_raw": _r(block["raw"][last, j]), "t": _r(block["t"][last, j], 2),
                "ic_trail": _r(block["ic"][last, j]), "coef_20d_ago": _r(coef[prev, j]),
                "flips_60d": int((np.diff(signs) != 0).sum()) if len(signs) > 1 else 0,
                "cover_today": _r(fits["cover"][k][j], 3),
            })
        top = np.argsort(-np.abs(np.nan_to_num(coef[last])))[:12]
        for i in valid:
            for j in top:
                history.append({"horizon": h, "as_of": str(arrays["days"][i]), "feature": feats[j], "coef": _r(coef[i, j])})
    return table, history


# --- build / export ----------------------------------------------------------------------------------------


def build_alpha_model(*, store: str | Path | None = None, recipe: Mapping[str, Any] | None = None,
                      store_recipe: Mapping[str, Any] | None = None, pack: str | Path | None = None) -> dict[str, Any]:
    from generic_utils.earnings_week import attach_joins
    from generic_utils.panel_store import connect_read

    recipe = dict(recipe or load_model_recipe())
    store_recipe = dict(store_recipe or load_store_recipe())
    con = connect_read(store)
    try:
        arrays = model_arrays(con, store_recipe, recipe)
    finally:
        con.close()
    fits = day_fits(arrays, recipe)
    wf = walk_forward(arrays, fits, recipe)
    summary, states = oos_summary(wf, arrays, recipe)
    calib = calibrate(wf, arrays, recipe)
    live = live_rows(arrays, fits, wf, calib, recipe, store_recipe)
    joins = attach_joins(live, list(recipe.get("joins") or []), pack=pack) if pack else {}
    weights, history = weights_table(wf, arrays, fits, store_recipe)
    oos_days = [r for block in wf.values() for r in block["oos"]]
    spec = {"live_day": str(arrays["days"][-1]), "first_day": str(arrays["days"][0]), "days": len(arrays["days"]),
            "features": len(arrays["features"]), "horizons": arrays["horizons"], "recipe_hash": recipe_hash(recipe),
            "pack": Path(pack).as_posix() if pack else None, "joins": joins}
    return {"tables": {"live": live, "state": states, "oos_summary": summary, "oos_days": oos_days,
                       "deciles": calib["table"], "weights": weights, "coef_days": history},
            "spec": spec}


def log_live(payload: Mapping[str, Any], recipe: Mapping[str, Any] | None = None, *, path: str | Path | None = None,
             store: str | Path | None = None) -> dict[str, Any]:
    """Append today's forecasts to the live log (one set per as_of and horizon) and score the matured ones."""
    import duckdb

    from generic_utils.panel_store import default_store_path

    recipe = dict(recipe or load_model_recipe())
    target = Path(path) if path else REPO_ROOT / str(recipe.get("live_log"))
    target.parent.mkdir(parents=True, exist_ok=True)
    spec = payload["spec"]
    day = date.fromisoformat(spec["live_day"])
    rows = []
    for rec in payload["tables"]["live"]:
        for h in spec["horizons"]:
            if rec.get(f"pred_{h}") is not None:
                rows.append([day, rec["symbol"], h, rec[f"pred_{h}"], rec.get(f"dec_{h}"), rec.get("close"),
                             spec["recipe_hash"], datetime.now(timezone.utc).replace(tzinfo=None)])
    con = duckdb.connect(str(target))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS live (as_of DATE, symbol VARCHAR, horizon VARCHAR, pred DOUBLE, decile INTEGER, "
                    "close DOUBLE, recipe_hash VARCHAR, logged_at TIMESTAMP)")
        con.execute("DELETE FROM live WHERE as_of = ?", [day])
        if rows:
            con.executemany("INSERT INTO live VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        track: list[dict[str, Any]] = []
        store_path = Path(store) if store else default_store_path()
        if store_path.exists():
            con.execute(f"ATTACH '{store_path.as_posix()}' AS ps (READ_ONLY)")
            try:
                for h in spec["horizons"]:
                    cur = con.execute(
                        f"""WITH j AS (
                              SELECT v.as_of, v.symbol, v.pred, v.decile, l.{qident('fwd_' + h)} AS fwd
                              FROM live v JOIN ps.labels l ON l.symbol = v.symbol AND l.as_of = v.as_of
                              WHERE v.horizon = ? AND l.{qident('fwd_' + h)} IS NOT NULL),
                            m AS (SELECT as_of, median(fwd) AS med FROM j GROUP BY as_of)
                            SELECT j.as_of, count(*) AS n, corr(j.pred, j.fwd) AS ic_pearson,
                                   avg(j.fwd - m.med) FILTER (WHERE j.decile = 10) AS top_fx,
                                   avg(j.fwd - m.med) FILTER (WHERE j.decile = 1) AS bottom_fx
                            FROM j JOIN m USING (as_of) GROUP BY j.as_of ORDER BY j.as_of""", [h])
                    for values in cur.fetchall():
                        track.append({"horizon": h, "as_of": values[0].isoformat(), "n": values[1], "ic_pearson": _r(values[2]),
                                      "top_fx": _r(values[3], 3), "bottom_fx": _r(values[4], 3)})
            finally:
                con.execute("DETACH ps")
        logged = con.execute("SELECT count(DISTINCT as_of) FROM live").fetchone()[0]
    finally:
        con.close()
    return {"path": target.as_posix(), "logged_days": logged, "rows": len(rows), "track": track}


def highlights_text(payload: Mapping[str, Any]) -> str:
    tables, spec = payload["tables"], payload["spec"]
    lines = [f"alpha model: live {spec['live_day']}, {spec['days']} days from {spec['first_day']}, {spec['features']} features, recipe {spec['recipe_hash']}.", ""]
    for s in tables["state"]:
        lines.append(f"[{s['horizon']}] state {s['state']}: OOS IC {s['ic_mean']} (t_indep {s['ic_t_indep']}, {s['oos_days']} days, "
                     f"{s['n_indep']} independent), recent {s['ic_recent']}, top-bottom decile {s['spread']}%, "
                     f"best walk-forward baseline {s['best_baseline']} {s['best_baseline_ic']}, beats it {s['beats_baselines']}; "
                     f"best fixed feature {s['best_fixed']} {s['best_fixed_ic']}")
    lines.append("")
    for h in spec["horizons"]:
        rows = sorted((w for w in tables["weights"] if w["horizon"] == h and w.get("coef")), key=lambda w: -abs(w["coef"]))
        if rows:
            lines.append(f"[{h}] weights: " + ", ".join(f"{w['feature']} {w['coef']:+.3f} (t {w['t']})" for w in rows[:10]))
    live = tables["live"]
    for h in ("7d", "28d"):
        key = f"pred_{h}"
        ranked = sorted((r for r in live if r.get(key) is not None), key=lambda r: -r[key])
        if ranked:
            lines.append("")
            lines.append(f"[{h}] top: " + ", ".join(f"{r['symbol']} {r[key]:+.2f}" for r in ranked[:15]))
            lines.append(f"[{h}] bottom: " + ", ".join(f"{r['symbol']} {r[key]:+.2f}" for r in ranked[-10:]))
    return "\n".join(lines) + "\n"


def write_alpha_model(payload: Mapping[str, Any], out_dir: str | Path, *, store: str | Path | None = None,
                      live_log: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from generic_utils.panel_store import default_store_path
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    tables = dict(payload["tables"])
    if live_log:
        tables["live_track"] = live_log.get("track") or []
    notes = [
        f"live day {spec['live_day']}; horizons {', '.join(spec['horizons'])}; recipe hash {spec['recipe_hash']} (config/generic_utils/alpha_model.json).",
        "pred_<h> is a forecast in percent excess return over the horizon against the universe median (US $1B+). It is one order, never summed with rr / bo / left, never a 0-100.",
        "Coefficients are re-fitted every day from days whose labels were known by then, age-decayed and shrunk by their own t (non-overlapping periods). weights.coef is what today's forecast uses.",
        "oos_summary: every past forecast scored on its later excess return. ew_sign / best_single are walk-forward baselines on the same past-only data (the fair bar); the fixed features in the recipe's baselines were picked from the full-sample signal lab, so they carry hindsight. state says whether the horizon earned trust.",
        "In ic mode weights.coef is the shrunk trailing IC each feature gets (dimensionless), coef_raw the unshrunk IC, t its t on non-overlapping periods. The default variant was chosen from 9 walk-forward variants on the same window (a mild selection bias).",
        "pred_<h> is slope x standardized composite rank, so it tops out near +-1.73 x slope; the order, the decile and the decile table (deciles: realized out-of-sample excess per forecast decile) are what to read.",
        "Bands b<h>_q20 / q50 / q80: close * (1 + decile quantile of out-of-sample excess / own volatility x own volatility over the horizon). A flat-market band: shift it by the market's move.",
        "why_<h>: the largest contributions (coefficient x standardized rank) to the forecast.",
        "56d has few independent periods: its weights are shrunk hard until more history accrues. 1d is scan-to-scan and noisy.",
        "live_track: the logged forecasts of earlier runs scored once their labels exist (starts empty, grows daily).",
    ]
    exported = write_run_export(
        out_dir, tool="generic_utils.alpha_model", tables=tables,
        sources={"panel_store": Path(store or default_store_path()).as_posix(), "pack": spec.get("pack"),
                 **{f"join_{k}": (v or {}).get("path") for k, v in (spec.get("joins") or {}).items()}},
        notes=notes, extra={"spec": {k: v for k, v in spec.items() if k != "joins"}}, duckdb_name="alpha_model.duckdb")
    (Path(out_dir) / "highlights.log").write_text(highlights_text(payload), encoding="utf-8")
    return exported
