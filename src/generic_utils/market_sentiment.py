"""Market sentiment: the market's mood from the store's day files, each reading measured against what the market did next.

From the panel store (US floor, one row per day file) plus the market timing policy runs and dated OUTSIDE readings:

- ``daily``: one row per store file: the cap-weighted day move and index (5 / 21 sessions, drawdown from the store high),
  breadth (names up, over the 50 / 200-day, near 52-week highs / lows), volume (up-volume share, volume pace scaled for the
  session elapsed at scan time, distribution / accumulation days), stretch (RSI, dispersion, gaps), appetite spreads (high
  minus low beta, prior-month winners minus losers, defensive sectors, the AI cluster), earnings reactions (beats, beats
  sold, misses over the last sessions' prints), street revisions breadth, the move-prediction scores' medians, the MTP
  ENTER share, the OUTSIDE readings of that date, and the cap index's forward return per horizon (``fwd_<h>``, sessions).
- ``readings``: one row per indicator: today's value, its percentile and quintile in the store's history, and per horizon
  the rank IC with the forward cap return (``ic_<h>``), an overlap-corrected t (``t_<h>``, n_indep = days / h), the IC in
  each half, the mean forward return in today's quintile against all days (``lift_<h>``) and ``read_<h>``: ``▲`` / ``▼``
  with the lift only when |t| >= t_min, n_indep >= min_indep, both halves agree, today's percentile leans (>= hi_pctl or
  <= lo_pctl) and the quintile's lift points the same way; a bare number otherwise.
- ``cells``: indicator x horizon x quintile: n, mean forward return, hit, lift (the evidence behind each read).
- ``families``: per family and horizon: readings, measured up / down arrows and their names. Counts, never a sum.
- ``patterns``: named technical patterns (recipe ``patterns``: SQL conditions on ``daily``): days matched, episodes,
  the last match, whether today matches, and the forward cap return after a match against all days.
- ``outside``: the OUTSIDE file's dated readings (fear / greed, AAII, VIX, prediction markets ...). Shown, never measured
  into an arrow: OUTSIDE changes timing or Grade, not a sort key or a size.

No ranking, no cutoff, no course, no 0-100 composite. The store holds months, not cycles: n_indep says how thin a read is.
Day files are scanned at different times (intraday marks); ``scan_frac`` says how much of the US session each had seen.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

from generic_utils.downside_risk import _lit, cluster_members, market_steps
from generic_utils.panel_store import REPO_ROOT, default_store_path, load_store_recipe

DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "market_sentiment.json"
FAMILY_ORDER = ["tape", "breadth", "volume", "stretch", "appetite", "earnings", "revisions", "model"]


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, digits)


def _abs(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


# --- session clock ------------------------------------------------------------------------------------------


def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(days=7 * (n - 1))


def us_dst(day: date) -> bool:
    """US daylight time: second Sunday of March to the first Sunday of November."""
    return _nth_sunday(day.year, 3, 2) <= day < _nth_sunday(day.year, 11, 1)


def session_frac(scan_at: datetime | None, session: Mapping[str, Any]) -> float | None:
    """Share of the US cash session elapsed at the scan (1.0 after the close, None without a scan time)."""
    if scan_at is None:
        return None
    if scan_at.tzinfo is not None:
        scan_at = scan_at.astimezone(timezone.utc).replace(tzinfo=None)
    lo, hi = (session.get("dst") if us_dst(scan_at.date()) else session.get("std")) or [14.5, 21.0]
    hour = scan_at.hour + scan_at.minute / 60.0
    frac = (hour - float(lo)) / (float(hi) - float(lo))
    return max(float(session.get("min_frac") or 0.15), min(1.0, frac))


# --- store reads ----------------------------------------------------------------------------------------------


def _floor_sql(recipe: Mapping[str, Any], alias: str = "p") -> str:
    return (f"{alias}.mcap >= {float(recipe.get('floor_mcap') or 0)} AND {alias}.exchange IN ({_lit(recipe.get('exchanges') or [])})"
            f" AND {alias}.type IN ({_lit(recipe.get('types') or [])})")


def daily_aggregates(con: Any, recipe: Mapping[str, Any], inds: Sequence[str], ids: Sequence[str]) -> list[dict[str, Any]]:
    near = float(recipe.get("near_high_pct") or 2.0) / 100.0
    hi, lo = float(recipe.get("rsi_hi") or 70), float(recipe.get("rsi_lo") or 30)
    defensive = _lit(recipe.get("defensive_sectors") or ["Utilities"])
    clus = f"(b.ind IN ({_lit(inds) if inds else repr('')}) OR split_part(b.symbol, ':', 2) IN ({_lit(ids) if ids else repr('')}))"
    first = recipe.get("first_day")
    since = f" AND p.as_of >= DATE '{date.fromisoformat(str(first)).isoformat()}'" if first else ""
    sql = f"""
WITH b AS (
  SELECT p.as_of, p.symbol, p.sector, p.ind, p.mcap, p.change, p.gap, p.close, p.sma50, p.sma200, p.hi52, p.lo52, p.rsi,
         p.relvol, p.volume, p.avgvol30, p.beta, f.eps_rev_1m, f.pt_rev_1m, f.rec_tech, f.p_exh, f.p_frag, f.p_bo,
         lag(p.perf_1m) OVER (PARTITION BY p.symbol ORDER BY p.as_of) AS prev_1m
  FROM panel p LEFT JOIN features f ON f.as_of = p.as_of AND f.symbol = p.symbol
  WHERE {_floor_sql(recipe)} AND p.change IS NOT NULL{since}),
t AS (SELECT *, ntile(3) OVER (PARTITION BY as_of ORDER BY beta) AS bt,
             ntile(3) OVER (PARTITION BY as_of ORDER BY prev_1m) AS mt FROM b)
SELECT as_of, count(*) AS n,
  sum(mcap * change) / sum(mcap) AS ret_cap, median(change) AS ret_med,
  100.0 * avg(CASE WHEN change > 0 THEN 1 ELSE 0 END) AS adv_pct,
  100.0 * sum(volume) FILTER (WHERE change > 0) / nullif(sum(volume), 0) AS upvol_pct,
  sum(volume) FILTER (WHERE avgvol30 > 0) / nullif(sum(avgvol30) FILTER (WHERE volume IS NOT NULL), 0) AS vol_ratio,
  100.0 * avg(CASE WHEN close > sma50 THEN 1 ELSE 0 END) FILTER (WHERE sma50 > 0) AS above50_pct,
  100.0 * avg(CASE WHEN close > sma200 THEN 1 ELSE 0 END) FILTER (WHERE sma200 > 0) AS above200_pct,
  100.0 * avg(CASE WHEN close >= hi52 * (1 - {near}) THEN 1 ELSE 0 END) FILTER (WHERE hi52 > 0) AS nh_pct,
  100.0 * avg(CASE WHEN close <= lo52 * (1 + {near}) THEN 1 ELSE 0 END) FILTER (WHERE lo52 > 0) AS nl_pct,
  median(rsi) AS rsi_med,
  100.0 * avg(CASE WHEN rsi >= {hi} THEN 1 ELSE 0 END) FILTER (WHERE rsi IS NOT NULL) AS rsi70_pct,
  100.0 * avg(CASE WHEN rsi <= {lo} THEN 1 ELSE 0 END) FILTER (WHERE rsi IS NOT NULL) AS rsi30_pct,
  stddev_samp(change) AS disp, median(gap) AS gap_med, median(relvol) AS relvol_med,
  100.0 * avg(CASE WHEN eps_rev_1m > 0 THEN 1 WHEN eps_rev_1m < 0 THEN -1 ELSE 0 END) FILTER (WHERE eps_rev_1m IS NOT NULL) AS rev_breadth,
  100.0 * avg(CASE WHEN pt_rev_1m > 0 THEN 1 WHEN pt_rev_1m < 0 THEN -1 ELSE 0 END) FILTER (WHERE pt_rev_1m IS NOT NULL) AS pt_rev_breadth,
  100.0 * avg(CASE WHEN rec_tech > 0 THEN 1 ELSE 0 END) FILTER (WHERE rec_tech IS NOT NULL) AS tech_buy_pct,
  median(p_exh) AS p_exh_med, median(p_frag) AS p_frag_med, median(p_bo) AS p_bo_med,
  avg(change) FILTER (WHERE bt = 3 AND beta IS NOT NULL) - avg(change) FILTER (WHERE bt = 1 AND beta IS NOT NULL) AS beta_spread,
  avg(change) FILTER (WHERE mt = 3 AND prev_1m IS NOT NULL) - avg(change) FILTER (WHERE mt = 1 AND prev_1m IS NOT NULL) AS mom_spread,
  avg(change) FILTER (WHERE sector IN ({defensive})) - avg(change) AS def_spread,
  sum(mcap * change) FILTER (WHERE {clus}) / nullif(sum(mcap) FILTER (WHERE {clus}), 0) - sum(mcap * change) / sum(mcap) AS ai_spread
FROM t b GROUP BY as_of ORDER BY as_of"""
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def scan_times(con: Any) -> dict[date, datetime]:
    out: dict[date, datetime] = {}
    for as_of, scan_at in con.execute("SELECT as_of, max(scan_at) FROM ingest_log GROUP BY as_of").fetchall():
        if scan_at is not None:
            out[as_of] = scan_at
    return out


def print_reads(con: Any, recipe: Mapping[str, Any], files: Sequence[date]) -> dict[date, dict[str, Any]]:
    """Per file: the last ``print_window`` files' reactions (beats, beats sold, misses) on the floor."""
    floor = float(recipe.get("print_min_mcap") or recipe.get("floor_mcap") or 0)
    rows = con.execute(
        "SELECT react_on, react, eps_surprise FROM prints WHERE react IS NOT NULL AND eps_surprise IS NOT NULL "
        f"AND coalesce(mcap_pre, 0) >= {floor} AND react_on >= ?", [files[0] - timedelta(days=30)] if files else [date(2000, 1, 1)]).fetchall()
    window = int(recipe.get("print_window") or 10)
    out: dict[date, dict[str, Any]] = {}
    for i, day in enumerate(files):
        lo = files[max(0, i - window + 1)]
        inside = [(float(rx), float(s)) for d, rx, s in rows if lo <= d <= day]
        beats = [rx for rx, s in inside if s > 0]
        misses = [rx for rx, s in inside if s < 0]
        out[day] = {"n_prints": len(inside), "n_beats": len(beats),
                    "beat_rx": mean(beats) if beats else None,
                    "beats_sold_pct": 100.0 * sum(1 for rx in beats if rx < 0) / len(beats) if beats else None,
                    "miss_rx": mean(misses) if misses else None}
    return out


def mtp_climate(root: str | Path) -> dict[date, dict[str, Any]]:
    """The newest market timing policy run per scan day: its ENTER share (climate, never a buy list)."""
    best: dict[date, tuple[str, dict[str, Any]]] = {}
    base = _abs(root)
    if not base.exists():
        return {}
    for path in base.glob("*/market_timing_policy_manifest.json"):
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
            day = date.fromisoformat(str(m.get("scan_day"))[:10])
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        stamp = str(m.get("created_at_utc") or "")
        if day not in best or stamp > best[day][0]:
            best[day] = (stamp, m)
    out: dict[date, dict[str, Any]] = {}
    for day, (_, m) in best.items():
        counts = m.get("action_counts") or {}
        total = sum(int(v) for v in counts.values()) or None
        enter = int(counts.get("ENTER_SMALL") or 0) + int(counts.get("ENTER_PROBE") or 0)
        out[day] = {"mtp_enter_pct": 100.0 * enter / total if total else None, "mtp_n": total, "mtp_run": m.get("run_id")}
    return out


def outside_rows(path: str | Path | None) -> list[dict[str, Any]]:
    p = _abs(path) if path else None
    if not p or not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    rows = [dict(r) for r in (data.get("rows") if isinstance(data, dict) else data) or []]
    rows.sort(key=lambda r: (str(r.get("kind")), str(r.get("date"))))
    by_kind: dict[str, list[float]] = {}
    for r in rows:
        v = _r(r.get("value"), 4)
        hist = by_kind.setdefault(str(r.get("kind")), [])
        r["n_kind"] = len(hist) + 1
        r["pctl_own"] = _r(100.0 * sum(1 for x in hist if x < v) / len(hist), 0) if v is not None and len(hist) >= 10 else None
        r["chg_prev"] = _r(v - hist[-1], 4) if v is not None and hist else None
        if v is not None:
            hist.append(v)
        r["tag"] = "OUTSIDE"
    return rows


# --- derived series ---------------------------------------------------------------------------------------------


def _rolling(values: Sequence[float | None], window: int, fn) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        xs = [v for v in values[max(0, i - window + 1): i + 1] if v is not None]
        out.append(fn(xs) if len(xs) >= max(2, window // 2) else None)
    return out


def derive(rows: list[dict[str, Any]], steps: Sequence[Mapping[str, Any]], recipe: Mapping[str, Any]) -> None:
    """Index levels, forward returns, smoothings and the distribution / accumulation counts, in place (rows by date)."""
    step = {s["as_of"]: s.get("cap") for s in steps}
    level = [100.0]
    for r in rows[:-1]:
        cap = step.get(r["as_of"])
        level.append(level[-1] * (1 + float(cap) / 100.0) if cap is not None else level[-1])
    high = -math.inf
    for i, r in enumerate(rows):
        r["cap_level"] = level[i]
        high = max(high, level[i])
        r["dd"] = 100.0 * (level[i] / high - 1)
        r["ret_5"] = 100.0 * (level[i] / level[i - 5] - 1) if i >= 5 else None
        r["ret_21"] = 100.0 * (level[i] / level[i - 21] - 1) if i >= 21 else None
        r["dd_min_10"] = min(rr["dd"] for rr in rows[max(0, i - 10): i]) if i else None
        r["cap_med"] = (r["ret_cap"] - r["ret_med"]) if r.get("ret_cap") is not None and r.get("ret_med") is not None else None
        r["nh_nl"] = (r["nh_pct"] - r["nl_pct"]) if r.get("nh_pct") is not None and r.get("nl_pct") is not None else None
        frac = r.get("scan_frac")
        r["vol_pace"] = r["vol_ratio"] / frac if r.get("vol_ratio") is not None and frac else None
        for h in recipe.get("horizons") or [1, 5, 10, 21]:
            r[f"fwd_{h}"] = 100.0 * (level[i + h] / level[i] - 1) if i + h < len(rows) else None
    smooth = int(recipe.get("smooth_window") or 10)
    for src, dst in (("adv_pct", "adv_10"), ("upvol_pct", "upvol_10"), ("beta_spread", "beta_spread_10"),
                     ("mom_spread", "mom_spread_10"), ("def_spread", "def_spread_10")):
        for r, v in zip(rows, _rolling([r.get(src) for r in rows], smooth, mean)):
            r[dst] = v
    for r, v in zip(rows, _rolling([r.get("ai_spread") for r in rows], 5, sum)):
        r["ai_spread_5"] = v
    move = float(recipe.get("move_pct") or 0.2)
    flags: list[tuple[int | None, int | None]] = []
    for i, r in enumerate(rows):
        prev = rows[i - 1].get("vol_pace") if i else None
        up = None if prev is None or r.get("vol_pace") is None else int(r["vol_pace"] > prev)
        r["vol_up"] = up
        ret = r.get("ret_cap")
        dist = None if up is None or ret is None else int(ret <= -move and up == 1)
        acc = None if up is None or ret is None else int(ret >= move and up == 1)
        flags.append((dist, acc))
        r["dist_day"], r["acc_day"] = dist, acc
    window = int(recipe.get("count_window") or 25)
    for i, r in enumerate(rows):
        span = flags[max(0, i - window + 1): i + 1]
        known = [f for f in span if f[0] is not None]
        r["dist_25"] = sum(f[0] for f in known) if len(known) >= window // 2 else None
        r["acc_25"] = sum(f[1] for f in known) if len(known) >= window // 2 else None


# --- measurement ------------------------------------------------------------------------------------------------


def _ranks(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 5:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = mean(rx), mean(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def quantile_edges(values: Sequence[float], k: int) -> list[float]:
    s = sorted(values)
    out = []
    for j in range(1, k):
        pos = (len(s) - 1) * j / k
        lo = int(math.floor(pos))
        out.append(s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (pos - lo))
    return out


def bucket(x: float, edges: Sequence[float]) -> int:
    return 1 + sum(1 for e in edges if x > e)


def measure(rows: Sequence[Mapping[str, Any]], recipe: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cfg = recipe.get("measure") or {}
    k = int(cfg.get("quantiles") or 5)
    t_min, min_indep, min_cell = float(cfg.get("t_min") or 2.0), float(cfg.get("min_indep") or 5), int(cfg.get("min_cell_n") or 8)
    hi_p, lo_p = float(cfg.get("hi_pctl") or 60), float(cfg.get("lo_pctl") or 40)
    horizons = [int(h) for h in recipe.get("horizons") or [1, 5, 10, 21]]
    today = rows[-1]
    readings, cells = [], []
    for name, meta in (recipe.get("indicators") or {}).items():
        hist = [(r["as_of"], float(r[name])) for r in rows if r.get(name) is not None]
        now = today.get(name)
        row: dict[str, Any] = {"indicator": name, "family": meta.get("family"), "label": meta.get("label"), "value": _r(now, 3),
                               "n_days": len(hist), "read_note": meta.get("read")}
        prev5 = rows[-6].get(name) if len(rows) > 5 else None
        row["chg_5"] = _r(float(now) - float(prev5), 3) if now is not None and prev5 is not None else None
        if len(hist) < 20 or now is None:
            readings.append(row)
            continue
        vals = [v for _, v in hist]
        below = sum(1 for v in vals if v < now) + 0.5 * sum(1 for v in vals if v == now)
        row["pctl"] = _r(100.0 * below / len(vals), 0)
        edges = quantile_edges(vals, k)
        q_now = bucket(float(now), edges)
        row["q"] = q_now
        for h in horizons:
            pairs = [(r["as_of"], float(r[name]), float(r[f"fwd_{h}"])) for r in rows if r.get(name) is not None and r.get(f"fwd_{h}") is not None]
            n = len(pairs)
            ic = spearman([p[1] for p in pairs], [p[2] for p in pairs]) if n >= 10 else None
            n_indep = n / h if h else n
            t = ic * math.sqrt((n_indep - 2) / max(1e-9, 1 - ic * ic)) if ic is not None and n_indep > 2 else None
            half = n // 2
            ic1 = spearman([p[1] for p in pairs[:half]], [p[2] for p in pairs[:half]]) if half >= 10 else None
            ic2 = spearman([p[1] for p in pairs[half:]], [p[2] for p in pairs[half:]]) if n - half >= 10 else None
            base = mean(p[2] for p in pairs) if pairs else None
            by_q: dict[int, list[float]] = {}
            for _, x, y in pairs:
                by_q.setdefault(bucket(x, edges), []).append(y)
            for q in range(1, k + 1):
                ys = by_q.get(q) or []
                cells.append({"indicator": name, "family": meta.get("family"), "horizon": h, "q": q, "n": len(ys),
                              "mean_fwd": _r(mean(ys), 3) if ys else None, "hit": _r(100.0 * sum(1 for y in ys if y > 0) / len(ys), 0) if ys else None,
                              "lift": _r(mean(ys) - base, 3) if ys and base is not None else None, "today": q == q_now})
            ys = by_q.get(q_now) or []
            lift = mean(ys) - base if ys and base is not None else None
            lean = 0
            if ic is not None and row["pctl"] is not None:
                sign = 1 if ic > 0 else -1
                lean = sign if row["pctl"] >= hi_p else -sign if row["pctl"] <= lo_p else 0
            sure = (t is not None and abs(t) >= t_min and n_indep >= min_indep and ic1 is not None and ic2 is not None
                    and ic1 * ic > 0 and ic2 * ic > 0 and len(ys) >= min_cell and lift is not None and lean != 0 and lift * lean > 0)
            row.update({f"ic_{h}": _r(ic, 3), f"t_{h}": _r(t, 2), f"n_indep_{h}": _r(n_indep, 1), f"ic_h1_{h}": _r(ic1, 3), f"ic_h2_{h}": _r(ic2, 3),
                        f"cell_n_{h}": len(ys), f"cell_fwd_{h}": _r(mean(ys), 3) if ys else None, f"base_fwd_{h}": _r(base, 3),
                        f"lift_{h}": _r(lift, 3), f"dir_{h}": ("up" if lean > 0 else "down") if sure else ""})
            row[f"read_{h}"] = (f"{'▲' if lean > 0 else '▼'}{lift:+.2f}" if sure else (f"{lift:+.2f}" if lift is not None else None))
        readings.append(row)
    return readings, cells


def family_counts(readings: Sequence[Mapping[str, Any]], horizons: Sequence[int]) -> list[dict[str, Any]]:
    out = []
    fams = [f for f in FAMILY_ORDER if any(r.get("family") == f for r in readings)]
    fams += sorted({str(r.get("family")) for r in readings} - set(fams))
    for fam in fams:
        rs = [r for r in readings if r.get("family") == fam]
        row: dict[str, Any] = {"family": fam, "n_readings": len(rs)}
        for h in horizons:
            ups = [r["indicator"] for r in rs if r.get(f"dir_{h}") == "up"]
            dns = [r["indicator"] for r in rs if r.get(f"dir_{h}") == "down"]
            row.update({f"up_{h}": len(ups), f"dn_{h}": len(dns), f"up_list_{h}": " ".join(ups), f"dn_list_{h}": " ".join(dns)})
        out.append(row)
    return out


def pattern_stats(rows: Sequence[Mapping[str, Any]], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    import duckdb

    horizons = [int(h) for h in recipe.get("horizons") or [1, 5, 10, 21]]
    cols = sorted({k for r in rows for k in r.keys()})
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE daily (" + ", ".join(f'"{c}" {"DATE" if c == "as_of" else "VARCHAR" if c in ("mtp_run",) else "DOUBLE"}' for c in cols) + ")")
        con.executemany("INSERT INTO daily VALUES (" + ", ".join("?" for _ in cols) + ")",
                        [[r.get(c) if c in ("as_of", "mtp_run") else _r(r.get(c), 6) for c in cols] for r in rows])
        index = {r["as_of"]: i for i, r in enumerate(rows)}
        out = []
        for pat in recipe.get("patterns") or []:
            try:
                hits = sorted(d for (d,) in con.execute(f"SELECT as_of FROM daily WHERE {pat['where']}").fetchall())
            except Exception as exc:  # noqa: BLE001 - a recipe condition naming a missing column is reported, not fatal
                out.append({"pattern": pat.get("name"), "family": pat.get("family"), "where": pat.get("where"), "error": str(exc).splitlines()[0]})
                continue
            pos = [index[d] for d in hits if d in index]
            episodes = sum(1 for j, p in enumerate(pos) if j == 0 or p != pos[j - 1] + 1)
            row: dict[str, Any] = {"pattern": pat.get("name"), "family": pat.get("family"), "where": pat.get("where"), "read": pat.get("read"),
                                   "days": len(pos), "episodes": episodes, "last": hits[-1] if hits else None,
                                   "live": bool(pos and pos[-1] == len(rows) - 1)}
            for h in horizons:
                ys = [rows[p][f"fwd_{h}"] for p in pos if rows[p].get(f"fwd_{h}") is not None]
                base = [r[f"fwd_{h}"] for r in rows if r.get(f"fwd_{h}") is not None]
                row.update({f"n_{h}": len(ys), f"fwd_{h}": _r(mean(ys), 3) if ys else None,
                            f"hit_{h}": _r(100.0 * sum(1 for y in ys if y > 0) / len(ys), 0) if ys else None,
                            f"lift_{h}": _r(mean(ys) - mean(base), 3) if ys and base else None})
            out.append(row)
        return out
    finally:
        con.close()


# --- build -----------------------------------------------------------------------------------------------------


def build_market_sentiment(*, recipe: Mapping[str, Any] | None = None, store: str | Path | None = None) -> dict[str, Any]:
    import duckdb

    recipe = dict(recipe or load_recipe())
    path = Path(store) if store else default_store_path(load_store_recipe())
    inds, ids = cluster_members(recipe)
    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = daily_aggregates(con, recipe, inds, ids)
        if len(rows) < 2:
            raise ValueError(f"{path}: fewer than two store files on the floor")
        steps = market_steps(con, recipe, inds, ids)
        times = scan_times(con)
        files = [r["as_of"] for r in rows]
        prints = print_reads(con, recipe, files)
    finally:
        con.close()
    mtp = mtp_climate(recipe.get("mtp_root") or "")
    outside = outside_rows(recipe.get("outside_file"))
    for r in rows:
        r["scan_at"] = times.get(r["as_of"]).isoformat(timespec="minutes") if times.get(r["as_of"]) else None
        r["scan_frac"] = session_frac(times.get(r["as_of"]), recipe.get("session_utc") or {})
        r.update(prints.get(r["as_of"]) or {})
        r.update(mtp.get(r["as_of"]) or {})
        for o in outside:
            if str(o.get("date"))[:10] == r["as_of"].isoformat() and o.get("value") is not None:
                r[f"out_{o.get('kind')}"] = o.get("value")
    derive(rows, steps, recipe)
    readings, cells = measure(rows, recipe)
    horizons = [int(h) for h in recipe.get("horizons") or [1, 5, 10, 21]]
    families = family_counts(readings, horizons)
    patterns = pattern_stats(rows, recipe)
    today = rows[-1]
    latest_out: dict[str, dict[str, Any]] = {}
    for o in outside:
        latest_out[str(o.get("kind"))] = o
    spec = {
        "store": path.as_posix(), "first_file": files[0].isoformat(), "last_file": files[-1].isoformat(), "n_files": len(files),
        "scan_at": today.get("scan_at"), "scan_frac": _r(today.get("scan_frac"), 2), "partial": bool((today.get("scan_frac") or 0) < 1),
        "cluster_label": (recipe.get("cluster") or {}).get("label"), "horizons": horizons,
        "families": {f["family"]: {str(h): [f[f"up_{h}"], f[f"dn_{h}"]] for h in horizons} for f in families},
        "live_patterns": [p["pattern"] for p in patterns if p.get("live")],
        "outside_latest": {k: {"date": v.get("date"), "value": v.get("value"), "unit": v.get("unit"), "source": v.get("source")} for k, v in latest_out.items()},
        "today": {k: _r(today.get(k), 2) for k in ("ret_cap", "ret_5", "ret_21", "dd", "adv_pct", "upvol_pct", "above50_pct", "above200_pct",
                                                    "nh_nl", "dist_25", "acc_25", "rsi_med", "beats_sold_pct", "rev_breadth", "mtp_enter_pct")},
    }
    daily = [{k: (v.isoformat() if isinstance(v, date) else _r(v, 4) if isinstance(v, float) else v) for k, v in r.items()} for r in rows]
    return {"spec": spec, "daily": daily, "readings": readings, "cells": cells, "families": families, "patterns": patterns, "outside": outside}


def highlights_text(payload: Mapping[str, Any]) -> str:
    spec = payload["spec"]
    hs = spec["horizons"]
    t = spec["today"]
    lines = [f"Market sentiment {spec['last_file']} (scan {spec['scan_at']} UTC, {round(100 * (spec['scan_frac'] or 0))}% of the US session seen; "
             f"store {spec['first_file']}..{spec['last_file']}, {spec['n_files']} files).",
             f"Tape: day {t['ret_cap']}%, 5 sessions {t['ret_5']}%, 21 sessions {t['ret_21']}%, {t['dd']}% under the store high. "
             f"Breadth: {t['adv_pct']}% up, up-volume {t['upvol_pct']}%, {t['above50_pct']}% over the 50-day, {t['above200_pct']}% over the 200-day, "
             f"highs minus lows {t['nh_nl']}. Volume: {t['dist_25']} distribution / {t['acc_25']} accumulation days in 25.",
             "Measured arrows per family (up / down) at " + ", ".join(f"{h}s" for h in hs) + ":"]
    for f in payload["families"]:
        lines.append("  " + f["family"].ljust(10) + "  ".join(f"{h}s {f[f'up_{h}']}/{f[f'dn_{h}']}" for h in hs)
                     + "".join(f"  [{h}s up: {f[f'up_list_{h}']}]" for h in hs if f[f"up_list_{h}"])
                     + "".join(f"  [{h}s down: {f[f'dn_list_{h}']}]" for h in hs if f[f"dn_list_{h}"]))
    live = [p for p in payload["patterns"] if p.get("live")]
    lines.append("Patterns live today: " + (", ".join(f"{p['pattern']} ({p['episodes']} episodes; after it 5s {p.get('lift_5')} / 21s {p.get('lift_21')} pp vs all days)"
                                                   for p in live) or "none"))
    if spec["outside_latest"]:
        lines.append("OUTSIDE (shown, never measured into an arrow): " + "; ".join(f"{k} {v['value']}{v.get('unit') or ''} ({v['date']}, {v['source']})"
                                                                              for k, v in spec["outside_latest"].items()))
    lines.append("Read the arrows as direction, not proof: the store holds months, n_indep at 21 sessions is about "
                 + str(round(spec['n_files'] / 21, 1)) + ". No composite: count families, never sum them.")
    return "\n".join(lines) + "\n"


def write_market_sentiment(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    notes = [
        f"Store {spec['first_file']} to {spec['last_file']} ({spec['n_files']} files), US floor. daily: one row per file; day reads (ret_cap, adv_pct, upvol_pct ...) "
        "are at each file's scan time (scan_at, scan_frac = share of the US session seen); the cap index (cap_level, ret_5 / ret_21, dd, fwd_<h>) chains "
        "close-to-close steps between consecutive files (downside_risk.market_steps).",
        "vol_pace = total volume / 30-day average volume / scan_frac: rough on intraday files. dist_day / acc_day: cap day move beyond move_pct with vol_pace "
        "above the file before; dist_25 / acc_25 count them over count_window files.",
        "readings: pctl / q = today's value in the store's history; ic_<h> = rank IC with fwd_<h> (sessions ahead), t_<h> uses n_indep = days / h; "
        "ic_h1 / ic_h2 the halves; lift_<h> = today's quintile mean fwd minus all days. read_<h> is ▲ / ▼ only when |t| >= t_min, n_indep >= min_indep, "
        "both halves agree, today's percentile leans and the quintile's lift agrees (recipe measure).",
        "families: counts of measured arrows per family; never summed into a score. patterns: recipe SQL conditions on daily; lift_<h> = mean fwd after a "
        "match minus all days; episodes = runs of consecutive matches.",
        "outside: dated OUTSIDE readings (config sentiment_outside.json, tv_scan_cli.py sentiment-add); out_<kind> columns on daily. Timing and Grade only.",
    ]
    tables = {k: payload.get(k) or [] for k in ("daily", "readings", "cells", "families", "patterns", "outside")}
    exported = write_run_export(out_dir, tool="generic_utils.market_sentiment", tables=tables, sources={"store": spec["store"]},
                                notes=notes, extra={"spec": spec}, duckdb_name="market_sentiment.duckdb")
    (Path(out_dir) / "highlights.log").write_text(highlights_text(payload), encoding="utf-8")
    return exported


def add_outside(path: str | Path, *, kind: str, value: float, day: str, unit: str = "", source: str = "", link: str = "", read: str = "") -> dict[str, Any]:
    """Append (or replace the same kind and date) one OUTSIDE reading in the sentiment file."""
    p = _abs(path)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"rows": []}
    rows = [r for r in data.get("rows") or [] if not (r.get("kind") == kind and str(r.get("date")) == day)]
    row = {"date": day, "kind": kind, "value": value, "unit": unit, "source": source, "link": link, "read": read}
    rows.append(row)
    rows.sort(key=lambda r: (str(r.get("date")), str(r.get("kind"))))
    data["rows"] = rows
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return row


def market_sentiment_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    out = (pack / "sentiment") if pack else REPO_ROOT / "logs" / "tradingview_analysis" / "market_sentiment"
    print(write_market_sentiment(build_market_sentiment(), out))


if __name__ == "__main__":
    market_sentiment_local_main()
