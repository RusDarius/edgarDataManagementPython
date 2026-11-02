"""Signal lab: backward evidence on the panel store (generic_utils.panel_store).

Three studies, each a set of tables (knobs in config/generic_utils/signal_lab.json):

- ``factor``: for every feature and horizon, the daily rank IC (Spearman between the feature and the later
  excess return, the return minus that day's universe median, winsorized), its industry-neutral twin (both
  ranks demeaned inside the industry), and the top-minus-bottom quantile spread. Summaries carry a plain t, a
  Newey-West t whose lag is the horizon in files (daily samples of a 28-day return overlap, so the plain t
  overstates), the two halves of the sample, the last ``recent_days``, and the IC on days when the universe's
  trailing month was up against down (a point-in-time regime).
- ``prints``: one row per print on the study floor with the state before it (run-up, revisions, street, the
  previous reaction), the reaction against the market, the drift after it, and the hold-through return from
  the close before the print. Cells group those rows by surprise, reaction, run-up and set-up.
- ``scans``: the names of each day's briefing-pack lists (radar, earnings lanes, short book, movers tails by
  class, suggested courses by bucket, the whole tape) against their later closes, as excess over the pack's
  own US $2B tape.

The verdict column is a significance label (|t| bands and sign stability from the recipe), not a gate, a
weight, or a course. Overlapping windows, one regime of seven months, and many tests are stated in the notes.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable, Mapping, Sequence

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

_f = to_float
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "signal_lab.json"


def load_lab_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: Any, digits: int = 4) -> float | None:
    number = _f(value)
    return None if number is None or math.isnan(number) else round(number, digits)


# --- statistics --------------------------------------------------------------------------------------------


def newey_west_t(values: Sequence[float], lag: int) -> float | None:
    """t of the mean with a Bartlett-kernel Newey-West variance (``lag`` = overlap in samples minus one)."""
    xs = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    n = len(xs)
    if n < 3:
        return None
    mean = sum(xs) / n
    d = [x - mean for x in xs]
    var = sum(x * x for x in d) / n
    lag = max(0, min(int(lag), n - 1))
    for k in range(1, lag + 1):
        gamma = sum(d[i] * d[i - k] for i in range(k, n)) / n
        var += 2.0 * (1.0 - k / (lag + 1.0)) * gamma
    if var <= 0:
        return None
    return mean / math.sqrt(var / n)


def plain_t(values: Sequence[float]) -> float | None:
    xs = [float(v) for v in values if v is not None]
    n = len(xs)
    if n < 3:
        return None
    mean = sum(xs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))
    return mean / (sd / math.sqrt(n)) if sd > 0 else None


def _mean(values: Iterable[Any]) -> float | None:
    xs = [float(v) for v in values if v is not None]
    return sum(xs) / len(xs) if xs else None


def verdict(t_nw: float | None, halves: tuple[float | None, float | None], regimes: tuple[float | None, float | None],
            mean: float | None, spec: Mapping[str, Any]) -> str:
    """STRONG / CONSISTENT / REGIME / WEAK from the recipe's t bands, half-sample signs and regime split."""
    if t_nw is None or mean is None:
        return "WEAK"
    strong, base, gap = float(spec.get("strong_t", 3.0)), float(spec.get("t", 2.0)), float(spec.get("regime_ic", 0.02))
    same = all(h is not None and h * mean > 0 for h in halves)
    up, down = regimes
    flips = up is not None and down is not None and up * down < 0 and min(abs(up), abs(down)) >= gap
    if abs(t_nw) >= strong and same and not flips:
        return "STRONG"
    if abs(t_nw) >= base and same and not flips:
        return "CONSISTENT"
    if abs(t_nw) >= base or flips:
        return "REGIME"
    return "WEAK"


# --- study frame -------------------------------------------------------------------------------------------


def build_study(con: Any, store_recipe: Mapping[str, Any], lab: Mapping[str, Any], horizons: Sequence[str]) -> dict[str, Any]:
    """TEMP table ``study``: features joined to labels on the study floor, with winsorized excess ``fx_<h>``."""
    study = dict(lab.get("study") or {})
    floor = float(study.get("min_market_cap_usd") or 0)
    p = float(study.get("winsor_pct", 1.0)) / 100.0
    where = [f"f.mcap >= {floor!r}"]
    if study.get("first_day"):
        where.append(f"f.as_of >= DATE '{date.fromisoformat(str(study['first_day'])).isoformat()}'")
    if study.get("last_day"):
        where.append(f"f.as_of <= DATE '{date.fromisoformat(str(study['last_day'])).isoformat()}'")
    fwd = ", ".join(f"l.{qident('fwd_' + h)}, l.{qident('lab_' + h)}" for h in horizons)
    stats = ", ".join(
        f"median({qident('fwd_' + h)}) AS {qident('m_' + h)}, quantile_cont({qident('fwd_' + h)}, {p!r}) AS {qident('lo_' + h)}, "
        f"quantile_cont({qident('fwd_' + h)}, {1 - p!r}) AS {qident('hi_' + h)}" for h in horizons)
    fx = ", ".join(
        f"least(greatest(s.{qident('fwd_' + h)}, m.{qident('lo_' + h)}), m.{qident('hi_' + h)}) - m.{qident('m_' + h)} AS {qident('fx_' + h)}"
        for h in horizons)
    regime = str(lab.get("regime_field") or "r_1m")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE study AS
        WITH s AS (SELECT f.*, {fwd} FROM features f JOIN labels l USING (symbol, as_of) WHERE {' AND '.join(where)}),
        m AS (SELECT as_of, count(*) AS n_universe, median(r_1d) AS mkt_1d, median({qident(regime)}) AS mkt_regime, {stats}
              FROM s GROUP BY as_of)
        SELECT s.*, m.n_universe, m.mkt_1d, m.mkt_regime, {', '.join(qident('m_' + h) for h in horizons)}, {fx}
        FROM s JOIN m USING (as_of)""")
    row = con.execute("SELECT count(*), count(DISTINCT as_of), min(as_of), max(as_of) FROM study").fetchone()
    market = con.execute(
        f"SELECT as_of, any_value(n_universe) AS n, any_value(mkt_1d) AS mkt_1d, any_value(mkt_regime) AS mkt_regime, "
        f"{', '.join('any_value(' + qident('m_' + h) + ') AS ' + qident('mkt_fwd_' + h) for h in horizons)} "
        f"FROM study GROUP BY as_of ORDER BY as_of").fetchall()
    names = ["as_of", "n", "mkt_1d", "mkt_regime"] + [f"mkt_fwd_{h}" for h in horizons]
    return {"rows": row[0], "days": row[1], "first": row[2].isoformat() if row[2] else None,
            "last": row[3].isoformat() if row[3] else None,
            "market": [{k: (v.isoformat() if isinstance(v, date) else _r(v, 3) if k != "n" else v) for k, v in zip(names, r)} for r in market]}


# --- factor study ------------------------------------------------------------------------------------------


def avg_rank(values: Any) -> Any:
    """1-based average ranks (ties share their mean rank) of a 1-D numpy array without NaN."""
    import numpy as np

    order = np.argsort(values, kind="mergesort")
    sorted_vals = values[order]
    new_group = np.r_[True, sorted_vals[1:] != sorted_vals[:-1]]
    group = np.cumsum(new_group) - 1
    counts = np.bincount(group)
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = (starts + (counts - 1) / 2.0 + 1.0)[group]
    return ranks


def _corr(a: Any, b: Any) -> float | None:
    import numpy as np

    a = a - a.mean()
    b = b - b.mean()
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 0 else None


def _demean_by(values: Any, codes: Any, min_n: int) -> Any:
    """Values minus their group mean; NaN where the group has fewer than ``min_n`` rows."""
    import numpy as np

    counts = np.bincount(codes)
    sums = np.bincount(codes, weights=values)
    means = sums / np.maximum(counts, 1)
    out = values - means[codes]
    out[counts[codes] < min_n] = np.nan
    return out


def study_arrays(con: Any, features: Sequence[str], horizons: Sequence[str]) -> dict[str, Any]:
    """The study table as numpy arrays (rows ordered by as_of): days, industry codes, X, Y (fx per horizon)."""
    import numpy as np

    cols = ["as_of", "ind"] + list(features) + [f"fx_{h}" for h in horizons]
    raw = con.execute(f"SELECT {', '.join(qident(c) for c in cols)} FROM study ORDER BY as_of, symbol").fetchnumpy()

    def numeric(name: str) -> Any:
        arr = raw[name]
        if isinstance(arr, np.ma.MaskedArray):
            return np.ma.filled(arr.astype(float), np.nan)
        return np.asarray(arr, dtype=float)

    days = np.asarray(raw["as_of"]).astype("datetime64[D]")
    ind = raw["ind"]
    ind = np.ma.filled(ind, "") if isinstance(ind, np.ma.MaskedArray) else np.asarray(ind)
    _, ind_codes = np.unique(ind.astype(str), return_inverse=True)
    uniq, starts = np.unique(days, return_index=True)
    bounds = list(zip(starts.tolist(), starts[1:].tolist() + [len(days)]))
    return {"days": [str(d) for d in uniq], "bounds": bounds, "ind": ind_codes,
            "X": np.column_stack([numeric(f) for f in features]) if features else np.empty((len(days), 0)),
            "Y": np.column_stack([numeric(f"fx_{h}") for h in horizons]), "features": list(features), "horizons": list(horizons)}


def factor_days(arrays: Mapping[str, Any], lab: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Daily IC, industry-neutral IC and quantile means of every feature for every horizon (exact Spearman per subset)."""
    import numpy as np

    study = dict(lab.get("study") or {})
    q, ind_min, min_names = int(study.get("quantiles", 5)), int(study.get("industry_min_n", 5)), int(study.get("min_names", 200))
    X, Y, codes = arrays["X"], arrays["Y"], arrays["ind"]
    out: list[dict[str, Any]] = []
    for day, (lo, hi) in zip(arrays["days"], arrays["bounds"]):
        for hj, h in enumerate(arrays["horizons"]):
            y_all = Y[lo:hi, hj]
            y_ok = ~np.isnan(y_all)
            if y_ok.sum() < min_names:
                continue
            for fj, feature in enumerate(arrays["features"]):
                x_all = X[lo:hi, fj]
                ok = y_ok & ~np.isnan(x_all)
                n = int(ok.sum())
                if n < min_names:
                    continue
                x, y, c = x_all[ok], y_all[ok], codes[lo:hi][ok]
                rx, ry = avg_rank(x), avg_rank(y)
                bucket = np.minimum(q, np.floor((rx - 1.0) * q / n).astype(int) + 1)
                _, local = np.unique(c, return_inverse=True)
                rxi, ryi = _demean_by(rx, local, ind_min), _demean_by(ry, local, ind_min)
                both = ~np.isnan(rxi) & ~np.isnan(ryi)
                top, bottom = y[bucket == q], y[bucket == 1]
                out.append({
                    "feature": feature, "horizon": h, "as_of": day, "n": n,
                    "ic": _r(_corr(rx, ry)),
                    "ic_ind": _r(_corr(rxi[both], ryi[both])) if both.sum() >= min_names // 2 else None,
                    "top": _r(top.mean()) if len(top) else None, "bottom": _r(bottom.mean()) if len(bottom) else None,
                    "top_hit": _r((top > 0).mean()) if len(top) else None,
                })
    return out


def summarize_factor(days: Sequence[Mapping[str, Any]], *, feature: str, horizon: str, files: int,
                     regime_by_day: Mapping[str, Any], lab: Mapping[str, Any]) -> dict[str, Any]:
    rows = sorted((d for d in days if d.get("ic") is not None), key=lambda d: d["as_of"])
    ics = [d["ic"] for d in rows]
    n = len(ics)
    rec: dict[str, Any] = {"feature": feature, "horizon": horizon, "n_days": n,
                           "n_indep": round(n / max(1, files), 1) if n else 0}
    if n < 3:
        rec["verdict"] = "WEAK"
        return rec
    mean = sum(ics) / n
    half = n // 2
    recent = int((lab.get("study") or {}).get("recent_days", 30))
    up = [d["ic"] for d in rows if (_f(regime_by_day.get(d["as_of"])) or 0) > 0]
    down = [d["ic"] for d in rows if (_f(regime_by_day.get(d["as_of"])) or 0) <= 0]
    ind = [d["ic_ind"] for d in rows if d.get("ic_ind") is not None]
    spreads = [d["top"] - d["bottom"] for d in rows if d.get("top") is not None and d.get("bottom") is not None]
    t_nw = newey_west_t(ics, files - 1)
    t_plain = plain_t(ics)
    # the plain t on non-overlapping periods: Newey-West runs optimistic when the lag nears the sample size
    t_indep = t_plain / math.sqrt(max(1, files)) if t_plain is not None else None
    t_use = min((t for t in (t_nw, t_indep) if t is not None), key=abs, default=None)
    rec.update(
        ic_mean=_r(mean), ic_t=_r(t_plain, 2), ic_t_nw=_r(t_nw, 2), ic_t_indep=_r(t_indep, 2),
        ic_pos=_r(sum(1 for v in ics if v > 0) / n, 3),
        ic_ind=_r(_mean(ind)), ic_ind_t_nw=_r(newey_west_t(ind, files - 1), 2),
        spread=_r(_mean(spreads), 3), spread_t_nw=_r(newey_west_t(spreads, files - 1), 2),
        top=_r(_mean(d.get("top") for d in rows), 3), bottom=_r(_mean(d.get("bottom") for d in rows), 3),
        top_hit=_r(_mean(d.get("top_hit") for d in rows), 3),
        ic_h1=_r(_mean(ics[:half])), ic_h2=_r(_mean(ics[half:])), ic_recent=_r(_mean(ics[-recent:])),
        ic_up_mkt=_r(_mean(up)), n_up=len(up), ic_down_mkt=_r(_mean(down)), n_down=len(down),
        first=rows[0]["as_of"], last=rows[-1]["as_of"],
    )
    rec["direction"] = "higher" if mean > 0 else "lower"
    rec["verdict"] = verdict(t_use, (rec["ic_h1"], rec["ic_h2"]), (rec["ic_up_mkt"], rec["ic_down_mkt"]), mean,
                             lab.get("verdict") or {})
    return rec


def month_table(days: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for d in days:
        if d.get("ic") is not None:
            groups[(d["feature"], d["horizon"], d["as_of"][:7])].append(d["ic"])
    return [{"feature": f, "horizon": h, "month": m, "n_days": len(v), "ic": _r(sum(v) / len(v))}
            for (f, h, m), v in sorted(groups.items())]


def feature_corr(arrays: Mapping[str, Any], features: Sequence[str], every_nth: int) -> list[dict[str, Any]]:
    """Average daily rank correlation between features (on every ``every_nth`` study day)."""
    import numpy as np

    index = [arrays["features"].index(f) for f in features if f in arrays["features"]]
    if len(index) < 2:
        return []
    sums: dict[tuple[int, int], list[float]] = defaultdict(list)
    for lo, hi in arrays["bounds"][::max(1, every_nth)]:
        block = arrays["X"][lo:hi][:, index]
        for i in range(len(index)):
            for j in range(i + 1, len(index)):
                ok = ~np.isnan(block[:, i]) & ~np.isnan(block[:, j])
                if ok.sum() >= 30:
                    rho = _corr(avg_rank(block[ok, i]), avg_rank(block[ok, j]))
                    if rho is not None:
                        sums[(i, j)].append(rho)
    names = [arrays["features"][k] for k in index]
    return [{"a": names[i], "b": names[j], "rho": _r(sum(v) / len(v), 3), "n_days": len(v)} for (i, j), v in sorted(sums.items())]


def factor_study(con: Any, store_recipe: Mapping[str, Any], lab: Mapping[str, Any], horizons: Sequence[str],
                 market: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    wanted = lab.get("features")
    names = feature_names(store_recipe) if wanted in (None, "all") else [f for f in wanted if f in feature_names(store_recipe)]
    files = {h["name"]: int(h.get("files") or 1) for h in horizon_specs(store_recipe)}
    family, labels = feature_family(store_recipe), feature_labels(store_recipe)
    regime = {m["as_of"]: m.get("mkt_regime") for m in market}
    arrays = study_arrays(con, names, horizons)
    all_days = factor_days(arrays, lab)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for d in all_days:
        grouped[(d["feature"], d["horizon"])].append(d)
    summary: list[dict[str, Any]] = []
    for feature in names:
        for h in horizons:
            rec = summarize_factor(grouped.get((feature, h), []), feature=feature, horizon=h, files=files.get(h, 1),
                                   regime_by_day=regime, lab=lab)
            rec["family"], rec["label"] = family.get(feature), labels.get(feature)
            summary.append(rec)
    spec = dict(lab.get("feature_corr") or {})
    ranked = sorted((s for s in summary if s["horizon"] == spec.get("horizon", "28d") and s.get("ic_t_nw") is not None),
                    key=lambda s: -abs(s["ic_t_nw"]))
    top = [s["feature"] for s in ranked[: int(spec.get("top", 20))]]
    return {"factor_summary": summary, "factor_days": all_days, "factor_month": month_table(all_days),
            "feature_corr": feature_corr(arrays, top, int(spec.get("every_nth_day", 5)))}


# --- print event study -------------------------------------------------------------------------------------


PRE_FEATURES: tuple[str, ...] = ("eps_rev_3m", "eps_rev_1m", "pt_up", "r_1m", "r_3m", "ey_fwd", "rev_yoy", "p_cons_w", "rvol_20", "eps_q_step")
_NEW_YORK = "America/New_York"


def after_close(scan_at: Any, day: Any, minutes: int) -> bool:
    """True when ``scan_at`` (UTC) is on ``day`` in New York, at least ``minutes`` after the 16:00 close.

    A placeholder midnight-UTC stamp lands on the New York evening before ``day``, so it never counts.
    """
    from zoneinfo import ZoneInfo

    if scan_at is None or day is None:
        return False
    at = scan_at if isinstance(scan_at, datetime) else datetime.fromisoformat(str(scan_at))
    local = (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(ZoneInfo(_NEW_YORK))
    return local.date().isoformat() == str(day)[:10] and local.hour * 60 + local.minute >= 16 * 60 + minutes


def print_events(con: Any, lab: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per print on the study floor: state before, reaction vs market, drift after, hold-through.

    Session reads beside the close-to-close reaction: ``react_gap`` (the reaction day's opening gap, only when
    the reaction file is the reaction session), ``react_oc`` (open to close that session), and for AMC prints
    ``ah_read`` (the after-hours change in the print-day file, only when that file's run was taken at least
    ``prints.ah_after_close_minutes`` after the close) with ``ah_to_close`` (reaction minus that read, pp).
    """
    spec = dict(lab.get("prints") or {})
    floor = float(spec.get("min_market_cap_usd") or 0)
    ah_minutes = int(spec.get("ah_after_close_minutes", 30))
    hs = list(spec.get("horizons") or ["7d", "28d"])
    pre = ", ".join(f"f.{qident(c)} AS {qident(c + '_pre')}" for c in PRE_FEATURES)
    drift = ", ".join(f"a.{qident('fx_' + h)} AS {qident('drift_fx_' + h)}" for h in hs)
    hold = ", ".join(f"b.{qident('fx_' + h)} AS {qident('hold_fx_' + h)}" for h in hs)
    cur = con.execute(f"""
        SELECT p.symbol, p.ind, p.print_date, p.slot, p.pre_file, p.react_file, p.react, p.react_px, p.react_relvol,
               p.runup, p.eps_surprise, p.rev_surprise, p.prev_react, p.mcap_pre,
               p.react - a.mkt_1d AS react_fx, {pre}, {drift}, {hold},
               p.react_on, g.gap AS react_gap, pb.post_chg AS pre_post_chg, il.scan_at AS pre_scan_at
        FROM prints p
        LEFT JOIN study a ON a.symbol = p.symbol AND a.as_of = p.react_file
        LEFT JOIN study b ON b.symbol = p.symbol AND b.as_of = p.pre_file
        LEFT JOIN features f ON f.symbol = p.symbol AND f.as_of = p.pre_file
        LEFT JOIN features g ON g.symbol = p.symbol AND g.as_of = p.react_file
        LEFT JOIN panel pb ON pb.symbol = p.symbol AND pb.as_of = p.pre_file
        LEFT JOIN ingest_log il ON il.as_of = p.pre_file
        WHERE p.react IS NOT NULL AND p.mcap_pre >= {floor!r}
        ORDER BY p.print_date, p.symbol""")
    names = [d[0] for d in cur.description]
    from generic_utils.print_pointers import surprise_mix

    out = []
    for values in cur.fetchall():
        raw = dict(zip(names, values))
        scan_at, post = raw.pop("pre_scan_at"), _f(raw.pop("pre_post_chg"))
        rec = {k: (v.isoformat() if isinstance(v, date) else _r(v, 3) if isinstance(v, float) else v) for k, v in raw.items()}
        rec["surprise_mix"] = surprise_mix(rec.get("eps_surprise"), rec.get("rev_surprise"))
        react, gap = _f(rec.get("react")), _f(rec.get("react_gap"))
        if rec.get("react_file") != rec.get("react_on"):
            gap = None
        rec["react_gap"] = gap
        rec["react_oc"] = (_r(((1 + react / 100) / (1 + gap / 100) - 1) * 100, 3)
                           if react is not None and gap is not None and gap > -100 else None)
        amc = str(rec.get("slot") or "").startswith("AMC")
        ah = post if amc and rec.get("pre_file") == rec.get("print_date") and after_close(scan_at, rec.get("pre_file"), ah_minutes) else None
        rec["ah_read"] = _r(ah, 3)
        rec["ah_to_close"] = _r(react - ah, 3) if react is not None and ah is not None else None
        out.append(rec)
    return out


def _quantile_edges(values: Sequence[float], q: int) -> list[float]:
    xs = sorted(values)
    return [xs[min(len(xs) - 1, int(len(xs) * k / q))] for k in range(1, q)] if xs else []


def _bucket(value: Any, edges: Sequence[float]) -> str | None:
    number = _f(value)
    if number is None or not edges:
        return None
    k = sum(1 for e in edges if number >= e) + 1
    lo = f"{edges[k - 2]:+.1f}" if k > 1 else "min"
    hi = f"{edges[k - 1]:+.1f}" if k <= len(edges) else "max"
    return f"Q{k} [{lo}, {hi})"


def _sign(value: Any, up: str, down: str) -> str | None:
    number = _f(value)
    return None if number is None else (up if number > 0 else down)


def cell_stats(rows: Sequence[Mapping[str, Any]], study: str, key: Callable[[Mapping[str, Any]], str | None],
               measures: Sequence[str]) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        cell = key(row)
        if cell is not None:
            groups[cell].append(row)
    out = []
    for cell, items in sorted(groups.items()):
        rec: dict[str, Any] = {"study": study, "cell": cell, "n": len(items)}
        for m in measures:
            xs = [float(r[m]) for r in items if _f(r.get(m)) is not None]
            rec[f"{m}_n"] = len(xs)
            rec[f"{m}_mean"] = _r(sum(xs) / len(xs), 3) if xs else None
            rec[f"{m}_med"] = _r(median(xs), 3) if xs else None
            rec[f"{m}_hit"] = _r(sum(1 for x in xs if x > 0) / len(xs), 3) if xs else None
            rec[f"{m}_t"] = _r(plain_t(xs), 2) if len(xs) >= 3 else None
            rec[f"{m}_q10"] = _r(_pctl(xs, 0.1), 3) if len(xs) >= 10 else None
            rec[f"{m}_q90"] = _r(_pctl(xs, 0.9), 3) if len(xs) >= 10 else None
        out.append(rec)
    return out


def _pctl(values: Sequence[float], p: float) -> float | None:
    """Linear-interpolated percentile (``p`` in 0..1)."""
    xs = sorted(values)
    if not xs:
        return None
    pos = (len(xs) - 1) * p
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def print_cell_studies(events: Sequence[Mapping[str, Any]], lab: Mapping[str, Any]
                       ) -> list[tuple[str, Callable[[Mapping[str, Any]], str | None], list[str]]]:
    """(study, cell key, measures) per print study; quintile edges come from ``events``.

    A key maps one print-event row to its cell label, so a live row put on the same schema
    (generic_utils.week_plays) lands in the same cell as the measured prints.
    """
    spec = dict(lab.get("prints") or {})
    q = int(spec.get("quantiles", 5))
    big = float(spec.get("big_react_pct", 5.0))
    hs = list(spec.get("horizons") or ["7d", "28d"])
    drift = [f"drift_fx_{h}" for h in hs]
    hold = [f"hold_fx_{h}" for h in hs]
    react = ["react", "react_fx"]
    edges = {k: _quantile_edges([float(e[k]) for e in events if _f(e.get(k)) is not None], q)
             for k in ("eps_surprise", "rev_surprise", "react_fx", "runup", "pt_up_pre", "eps_rev_3m_pre", "ah_read", "react_gap")}

    def setup(e: Mapping[str, Any]) -> str | None:
        if _f(e.get("prev_react")) is None or _f(e.get("runup")) is None or _f(e.get("eps_rev_3m_pre")) is None:
            return None
        hit = e["runup"] < 0 and e["eps_rev_3m_pre"] > 0 and e["prev_react"] > 0
        return "sold into + EPS rising + last print up" if hit else "other (all three measured)"

    def big_move(e: Mapping[str, Any]) -> str | None:
        s, r = _f(e.get("eps_surprise")), _f(e.get("react_fx"))
        if s is None or r is None:
            return None
        if s >= 0 and r <= -big:
            return f"beat, sold {big:.0f}%+ vs market"
        if s < 0 and r >= big:
            return f"miss, bought {big:.0f}%+ vs market"
        if s >= 0 and r >= big:
            return f"beat, bought {big:.0f}%+ vs market"
        if s < 0 and r <= -big:
            return f"miss, sold {big:.0f}%+ vs market"
        return "inside the band"

    def setup_label(e: Mapping[str, Any]) -> str | None:
        r, p = _f(e.get("runup")), _f(e.get("prev_react"))
        if r is None or p is None:
            return None
        return f"{'sold into' if r < 0 else 'ran into'} & previous print {'up' if p > 0 else 'down'}"

    def setup_reaction(e: Mapping[str, Any]) -> str | None:
        cell, fx = setup_label(e), _f(e.get("react_fx"))
        if cell is None or fx is None:
            return None
        move = f"sold {big:.0f}%+" if fx <= -big else f"bought {big:.0f}%+" if fx >= big else "inside the band"
        return f"{cell} | {move}"

    def gap_session(e: Mapping[str, Any]) -> str | None:
        g, oc = _f(e.get("react_gap")), _f(e.get("react_oc"))
        if g is None or oc is None:
            return None
        if abs(g) < big:
            return f"gap inside {big:.0f}%"
        return f"gapped {'up' if g > 0 else 'down'} {big:.0f}%+, {'bought' if oc > 0 else 'sold'} after the open"

    studies: list[tuple[str, Callable[[Mapping[str, Any]], str | None], list[str]]] = [
        ("all prints", lambda e: "all", react + drift + hold),
        ("surprise mix (EPS/revenue)", lambda e: e.get("surprise_mix"), react + drift),
        ("EPS surprise quintile", lambda e: _bucket(e.get("eps_surprise"), edges["eps_surprise"]), react + drift),
        ("revenue surprise quintile", lambda e: _bucket(e.get("rev_surprise"), edges["rev_surprise"]), react + drift),
        ("reaction vs market quintile", lambda e: _bucket(e.get("react_fx"), edges["react_fx"]), drift),
        ("reaction x surprise", lambda e: (None if _f(e.get("eps_surprise")) is None or _f(e.get("react_fx")) is None else
                                           f"{'beat' if e['eps_surprise'] >= 0 else 'miss'} & {'up' if e['react_fx'] > 0 else 'down'}"), drift),
        ("big reaction vs surprise", big_move, drift),
        ("run-up into the print quintile", lambda e: _bucket(e.get("runup"), edges["runup"]), react + hold),
        ("next-FY EPS revision 3m before", lambda e: _sign(e.get("eps_rev_3m_pre"), "rising", "flat or falling"), react + hold),
        ("revision quintile before", lambda e: _bucket(e.get("eps_rev_3m_pre"), edges["eps_rev_3m_pre"]), react + hold),
        ("street upside quintile before", lambda e: _bucket(e.get("pt_up_pre"), edges["pt_up_pre"]), react + hold),
        ("previous reaction", lambda e: _sign(e.get("prev_react"), "previous print up", "previous print down"), react + hold),
        ("run-up x previous reaction", setup_label, react + hold),
        ("set-up x reaction", setup_reaction, drift + hold),
        ("after-hours read quintile (AMC)", lambda e: _bucket(e.get("ah_read"), edges["ah_read"]), react + ["ah_to_close"] + drift),
        ("opening gap quintile (reaction day)", lambda e: _bucket(e.get("react_gap"), edges["react_gap"]), react + ["react_oc"] + drift),
        ("gap x session", gap_session, react + drift),
        ("earnings-week set-up", setup, react + hold),
        ("slot", lambda e: (e.get("slot") or "").rstrip("~") or None, react),
        ("print month", lambda e: str(e.get("print_date") or "")[:7] or None, react + drift),
    ]
    return studies


def print_cells(events: Sequence[Mapping[str, Any]], lab: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, key, measures in print_cell_studies(events, lab):
        out.extend(cell_stats(events, name, key, measures))
    return out


# --- scan-list study ---------------------------------------------------------------------------------------


def pack_days(runs_root: Path) -> list[dict[str, Any]]:
    """Newest briefing pack with a DuckDB per all-fields day: path, the pack's day file label, its run."""
    found: dict[str, dict[str, Any]] = {}
    for folder in sorted(runs_root.glob("briefing_pack_*")):
        db = folder / "briefing_pack.duckdb"
        meta = folder / "briefing_pack.json"
        if not db.exists() or not meta.exists():
            continue
        try:
            sources = json.loads(meta.read_text(encoding="utf-8")).get("sources") or {}
        except (OSError, ValueError):
            continue
        label = str(sources.get("all_fields_day_label") or "")
        parts = label.split("_")
        if len(parts) != 3:
            continue
        day = date(int(parts[2]), int(parts[1]), int(parts[0])).isoformat()
        found[day] = {"day": day, "pack": folder.as_posix(), "db": db.as_posix(), "all_fields_run": sources.get("all_fields_run_id")}
    return [found[k] for k in sorted(found)]


def scan_study(con: Any, store_recipe: Mapping[str, Any], lab: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    import duckdb

    spec = dict(lab.get("scans") or {})
    root = Path(str(spec.get("runs_root") or ""))
    root = root if root.is_absolute() else REPO_ROOT / root
    hs = list(spec.get("horizons") or ["1d", "7d", "14d", "28d"])
    files = {h["name"]: int(h.get("files") or 1) for h in horizon_specs(store_recipe)}
    short_subs = set(spec.get("short_subs") or [])
    obs: list[dict[str, Any]] = []
    packs = pack_days(root)
    for pack in packs:
        day = pack["day"]
        labels = {r[0]: r[1:] for r in con.execute(
            f"SELECT l.symbol, p.close, {', '.join('l.' + qident('fwd_' + h) for h in hs)} FROM labels l "
            "JOIN panel p USING (symbol, as_of) WHERE l.as_of = ?", [date.fromisoformat(day)]).fetchall()}
        if not labels:
            continue
        pcon = duckdb.connect(pack["db"], read_only=True)
        try:
            lists: dict[tuple[str, str], list[tuple[str, Any]]] = {}
            for item in spec.get("lists") or []:
                try:
                    cur = pcon.execute(str(item["sql"]))
                except Exception:  # noqa: BLE001 - an older pack without that table or column skips the list
                    continue
                cols = [d[0] for d in cur.description]
                for values in cur.fetchall():
                    rec = dict(zip(cols, values))
                    sub = str(rec.get("sub") or "all")
                    lists.setdefault((str(item["name"]), sub), []).append((str(rec["symbol"]), rec.get("close")))
            side_of = {str(i["name"]): str(i.get("side") or "long") for i in spec.get("lists") or []}
        finally:
            pcon.close()

        def fwd(symbol: str, close: Any, k: int) -> float | None:
            hit = labels.get(symbol)
            if not hit or hit[1 + k] is None:
                return None
            store_close, pack_close = _f(hit[0]), _f(close)
            # entry at the close the pack saw; the label runs from the store's (newest-run) close of that day
            scale = store_close / pack_close if store_close and pack_close and pack_close > 0 else 1.0
            return ((1 + float(hit[1 + k]) / 100.0) * scale - 1.0) * 100.0

        tape = lists.get(("tape", "all"), [])
        bench = {}
        for k, h in enumerate(hs):
            xs = sorted(v for v in (fwd(s, c, k) for s, c in tape) if v is not None)
            bench[h] = median(xs) if xs else None
        for (name, sub), members in lists.items():
            seen = set()
            side = side_of.get(name, "long")
            if side == "by_sub":
                side = "short" if sub in short_subs else "-"
            for symbol, close in members:
                if symbol in seen:
                    continue
                seen.add(symbol)
                rec: dict[str, Any] = {"day": day, "list": name, "sub": sub, "side": side, "symbol": symbol}
                for k, h in enumerate(hs):
                    raw = fwd(symbol, close, k)
                    rec[f"fx_{h}"] = _r(raw - bench[h], 3) if raw is not None and bench[h] is not None else None
                obs.append(rec)
    summary: list[dict[str, Any]] = []
    days_rows: list[dict[str, Any]] = []
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for rec in obs:
        groups[(rec["list"], rec["sub"], rec["side"])].append(rec)
    for (name, sub, side), items in sorted(groups.items()):
        by_day: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for rec in items:
            by_day[rec["day"]].append(rec)
        for h in hs:
            daily = []
            for day in sorted(by_day):
                xs = [r[f"fx_{h}"] for r in by_day[day] if r.get(f"fx_{h}") is not None]
                if xs:
                    daily.append(sum(xs) / len(xs))
                    days_rows.append({"day": day, "list": name, "sub": sub, "horizon": h, "n": len(xs), "fx_mean": _r(sum(xs) / len(xs), 3)})
            every = [r[f"fx_{h}"] for r in items if r.get(f"fx_{h}") is not None]
            if not every:
                continue
            summary.append({
                "list": name, "sub": sub, "side": side, "horizon": h, "n_days": len(daily), "n_obs": len(every),
                "fx_mean": _r(sum(daily) / len(daily), 3), "fx_t_nw": _r(newey_west_t(daily, files.get(h, 1) - 1), 2),
                "fx_med": _r(median(every), 3), "hit": _r(sum(1 for x in every if x > 0) / len(every), 3),
                "first": min(by_day), "last": max(by_day),
            })
    return {"scan_summary": summary, "scan_days": days_rows, "scan_obs": obs,
            "scan_packs": [{"day": p["day"], "pack": p["pack"], "all_fields_run": p["all_fields_run"]} for p in packs]}


# --- build / export ----------------------------------------------------------------------------------------


def build_signal_lab(*, store: str | Path | None = None, lab: Mapping[str, Any] | None = None,
                     store_recipe: Mapping[str, Any] | None = None, studies: Sequence[str] = ("factor", "prints", "scans")) -> dict[str, Any]:
    from generic_utils.panel_store import connect_read

    lab = dict(lab or load_lab_recipe())
    store_recipe = dict(store_recipe or load_store_recipe())
    known = [h["name"] for h in horizon_specs(store_recipe)]
    horizons = [h for h in lab.get("horizons") or known if h in known]
    con = connect_read(store)
    try:
        frame = build_study(con, store_recipe, lab, horizons)
        tables: dict[str, list[dict[str, Any]]] = {"market": frame.pop("market")}
        if "factor" in studies:
            tables.update(factor_study(con, store_recipe, lab, horizons, tables["market"]))
        if "prints" in studies:
            events = print_events(con, lab)
            tables["print_events"] = events
            tables["print_cells"] = print_cells(events, lab)
        if "scans" in studies:
            tables.update(scan_study(con, store_recipe, lab))
    finally:
        con.close()
    n_tests = sum(1 for s in tables.get("factor_summary", []) if s.get("ic_t_nw") is not None)
    spec = {"frame": frame, "horizons": horizons, "studies": list(studies), "n_tests": n_tests,
            "expected_false_at_t2": round(n_tests * 0.0455, 1)}
    return {"tables": tables, "spec": spec}


def highlights_text(payload: Mapping[str, Any], lab: Mapping[str, Any] | None = None) -> str:
    """Short readable digest: top features per horizon, print cells that stand out, scan lists by excess."""
    tables = payload["tables"]
    spec = payload["spec"]
    frame = spec["frame"]
    lines = [f"signal lab: {frame['days']} days {frame['first']}..{frame['last']}, {frame['rows']} name-days on the study floor.",
             f"tests with a t: {spec['n_tests']} (about {spec['expected_false_at_t2']} would clear |t| 2 by chance).", ""]
    summary = tables.get("factor_summary") or []
    for h in spec["horizons"]:
        rows = [s for s in summary if s["horizon"] == h and s.get("verdict") in {"STRONG", "CONSISTENT"}]
        rows.sort(key=lambda s: -abs(s.get("ic_t_nw") or 0))
        if rows:
            lines.append(f"[{h}] " + "; ".join(
                f"{s['feature']} {s['ic_mean']:+.3f} (t_nw {s['ic_t_nw']:+.1f}, t_indep {s['ic_t_indep']:+.1f}, "
                f"ind {s['ic_ind'] if s['ic_ind'] is not None else '-'}, {s['verdict']})"
                for s in rows[:8]))
    cells = tables.get("print_cells") or []
    if cells:
        lines.append("")
        lines.append("prints:")
        for c in cells:
            if c["study"] in {"all prints", "reaction x surprise", "earnings-week set-up", "surprise mix (EPS/revenue)",
                              "big reaction vs surprise", "run-up x previous reaction", "previous reaction"}:
                lines.append(f"  {c['study']} | {c['cell']} | n {c['n']} | react {c.get('react_mean')} | "
                             f"drift28 {c.get('drift_fx_28d_mean')} (t {c.get('drift_fx_28d_t')}) | hold28 {c.get('hold_fx_28d_mean')}")
            elif c["study"] in {"after-hours read quintile (AMC)", "gap x session", "set-up x reaction"}:
                lines.append(f"  {c['study']} | {c['cell']} | n {c['n']} | react_fx {c.get('react_fx_mean')} | "
                             f"ah_to_close {c.get('ah_to_close_mean')} (t {c.get('ah_to_close_t')}) | "
                             f"drift28 {c.get('drift_fx_28d_mean')} (t {c.get('drift_fx_28d_t')}) | hold28 {c.get('hold_fx_28d_mean')}")
    scans = tables.get("scan_summary") or []
    if scans:
        lines.append("")
        lines.append("scan lists (excess vs the pack's US $2B tape median, equal weight per day):")
        for s in sorted((s for s in scans if s["horizon"] == "7d"), key=lambda s: -(s.get("fx_mean") or 0)):
            lines.append(f"  7d {s['list']}:{s['sub']} ({s['side']}) days {s['n_days']} obs {s['n_obs']} "
                         f"mean {s['fx_mean']} (t_nw {s['fx_t_nw']}) med {s['fx_med']} hit {s['hit']}")
    return "\n".join(lines) + "\n"


def write_signal_lab(payload: Mapping[str, Any], out_dir: str | Path, *, store: str | Path | None = None) -> dict[str, Any]:
    from generic_utils.panel_store import default_store_path
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    frame = spec["frame"]
    notes = [
        f"study floor: US $1B+ on the as_of day; {frame['days']} days {frame['first']}..{frame['last']}; horizons {', '.join(spec['horizons'])} (calendar days to the first later file).",
        "fx_<h> is the forward return minus that day's universe median, winsorized at the recipe percentile. IC is Spearman per day; ic_ind demeans both ranks inside the industry.",
        "ic_t_nw is a Newey-West t with lag = horizon files - 1 (daily samples of a 28-day return overlap). n_indep = days / horizon files is the number of non-overlapping periods.",
        f"{spec['n_tests']} feature x horizon tests: about {spec['expected_false_at_t2']} clear |t| 2 by chance. Trust STRONG over CONSISTENT, and a 56d result least (few independent periods).",
        "ic_t_indep is the plain t divided by sqrt(horizon files): the t of non-overlapping periods. The verdict uses the smaller of ic_t_nw and ic_t_indep.",
        "verdict: STRONG |t| >= strong_t with both halves the same sign and no regime flip; CONSISTENT the same at t; REGIME clears t but flips across halves or market state; WEAK otherwise. A label, not a gate.",
        "print_events: react is the reaction session's change (closes across a file gap); react_fx subtracts the universe median change that day; drift_fx_<h> runs from the reaction close; hold_fx_<h> from the close before the print.",
        "print t-stats treat prints as independent; prints cluster in earnings season, so read them as optimistic.",
        "scan_*: entry is the close in the pack (its own scan), the exit the first later day file; excess is over the median of the same pack's US $2B tape. side short means a negative excess is the call working.",
        "Seven months of one market: evidence for weighting and for questions, not a promise.",
    ]
    tables = dict(payload["tables"])
    exported = write_run_export(
        out_dir, tool="generic_utils.signal_lab", tables=tables,
        sources={"panel_store": Path(store or default_store_path()).as_posix()},
        notes=notes, extra={"spec": spec}, duckdb_name="signal_lab.duckdb")
    (Path(out_dir) / "highlights.log").write_text(highlights_text(payload), encoding="utf-8")
    return exported
