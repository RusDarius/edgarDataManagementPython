"""Margin of safety, buy-in-red band, kill, add trigger, and trim zone for a named catalog.

Formulas only, one row per name, every input and level its own column:

* value anchors: today's next-FY EPS forecast times (a) the lowest and (b) the median forward
  PE the name traded at across the history window, and (c) the industry median forward PE.
  ``mos_*`` is that price versus the close, %. Street target stays a separate column.
* window path on split-adjusted closes: high / low (and dates), giveback, rebound, how far the
  forward PE de-rated while the estimate moved (``derate_pct`` vs ``eps_fc_pct``).
* bounce band below the close (``bounce_low``..``bounce_high``), anchored on the window low
  when it sits inside the band; ``bounce_kill`` under that support; first target the 50-day.
* add trigger: the entry book's reclaim band when the 50-day is overhead (a confirmation add).
* trim zone: the lower of street target and window high, when it is above the close.

No ranking, no buy / sell choice, no 0-100. The ATR multiples are recipe knobs
(config/generic_utils/safety_band.json). A cyclical's low PE at peak estimates is not a
margin of safety by itself: ``eps_fc_pct`` and ``pe_nfy_seat`` sit beside every anchor.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float
from generic_utils.run_export import load_json_recipe, write_run_export

_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = _PROJECT / "config" / "generic_utils" / "safety_band.json"

_f = to_float


def load_safety_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_RECIPE
    payload = load_json_recipe(recipe_path) if recipe_path.exists() else {}
    payload.setdefault("bounce_high_atr", 0.5)
    payload.setdefault("bounce_low_atr", 1.5)
    payload.setdefault("kill_atr", 0.75)
    payload.setdefault("support_snap_atr", 1.0)
    payload.setdefault("split_guard", {"max_gap_days": 5, "jump_ratio": 1.8, "day_change_max_pct": 25.0})
    return payload


def _r(value: Any, digits: int = 2) -> float | None:
    number = _f(value)
    return None if number is None else round(number, digits)


def _pct(now: Any, ref: Any) -> float | None:
    a, b = _f(now), _f(ref)
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1.0) * 100.0


def history_reads(points: Sequence[Mapping[str, Any]], guard: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Window reads from one name's day rows (oldest-first): split-adjusted path and forward-PE range."""
    from generic_utils.gmv_lens import split_adjust

    adjusted, splits = split_adjust(points, guard)
    closes = [(p.get("as_of"), _f(p.get("close"))) for p in adjusted if _f(p.get("close"))]
    if not closes:
        return {"hist_n": 0, "splits": splits}
    hi = max(closes, key=lambda item: item[1])
    lo = min(closes, key=lambda item: item[1])
    last = closes[-1][1]
    pes = []
    for p in adjusted:
        close, eps = _f(p.get("close")), _f(p.get("earnings_per_share_forecast_next_fy"))
        if close and eps and eps > 0:
            pes.append(close / eps)
    eps_series = [_f(p.get("earnings_per_share_forecast_next_fy")) for p in adjusted]
    eps_series = [e for e in eps_series if e is not None]
    pts = [_f(p.get("price_target_average")) for p in adjusted]
    pts = [x for x in pts if x]
    out = {
        "hist_n": len(closes),
        "first_as_of": str(adjusted[0].get("as_of")),
        "splits": splits,
        "window_high": _r(hi[1]),
        "window_high_as_of": hi[0],
        "window_low": _r(lo[1]),
        "window_low_as_of": lo[0],
        "giveback_pct": _r(_pct(last, hi[1]), 1),
        "rebound_pct": _r(_pct(last, lo[1]), 1),
        "close_window_pct": _r(_pct(last, closes[0][1]), 1),
        "eps_fc_pct": _r(_pct(eps_series[-1], eps_series[0]), 1) if len(eps_series) >= 2 else None,
        "pt_window_pct": _r(_pct(pts[-1], pts[0]), 1) if len(pts) >= 2 else None,
        "pe_nfy_min": _r(min(pes), 1) if pes else None,
        "pe_nfy_med": _r(median(pes), 1) if pes else None,
        "pe_nfy_max": _r(max(pes), 1) if pes else None,
        "pe_nfy_first": _r(pes[0], 1) if pes else None,
    }
    if pes and len(pes) >= 2:
        span = max(pes) - min(pes)
        out["pe_nfy_seat"] = _r((pes[-1] - min(pes)) / span * 100.0, 1) if span > 0 else None
        out["derate_pct"] = _r(_pct(pes[-1], pes[0]), 1)
    return out


def safety_levels(row: Mapping[str, Any], recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Anchors and levels for one joined row (catalog + lens + history reads)."""
    close = _f(row.get("close"))
    atr = _f(row.get("atr"))
    if atr is None and close and _f(row.get("atrp")) is not None:
        atr = close * _f(row.get("atrp")) / 100.0
    out: dict[str, Any] = {}
    pe_now = _f(row.get("pe_nfy"))
    eps_fc = close / pe_now if close and pe_now and pe_now > 0 else None
    out["eps_fc_now"] = _r(eps_fc, 2)
    for key, multiple in (("own_low", row.get("pe_nfy_min")), ("own_med", row.get("pe_nfy_med")), ("peer", row.get("pe_nfy_ind_med"))):
        m = _f(multiple)
        px = eps_fc * m if eps_fc and m and m > 0 else None
        out[f"px_{key}_pe"] = _r(px)
        out[f"mos_{key}_pct"] = _r(_pct(px, close), 1) if px else None
    vs50 = _f(row.get("vs50"))
    sma50 = close / (1.0 + vs50 / 100.0) if close and vs50 is not None and vs50 > -100 else None
    out["sma50"] = _r(sma50)
    if not close or not atr:
        return out
    support = _f(row.get("window_low"))
    high = close - float(recipe["bounce_high_atr"]) * atr
    low = close - float(recipe["bounce_low_atr"]) * atr
    anchor = "atr"
    if support is not None and low <= support <= high:
        low, anchor = support, "window_low"
    kill_base = low
    if support is not None and low - float(recipe["support_snap_atr"]) * atr <= support < low:
        kill_base, anchor = support, anchor + "+support_kill"
    kill = kill_base - float(recipe["kill_atr"]) * atr
    target = sma50 if sma50 and sma50 > high else _f(row.get("street_px"))
    out.update(
        bounce_high=_r(high),
        bounce_low=_r(low),
        bounce_anchor=anchor,
        bounce_kill=_r(kill),
        bounce_kill_pct=_r(_pct(kill, close), 1),
        first_target=_r(target),
        first_target_src="sma50" if target is not None and target == sma50 else "street_px",
        rr_bounce=_r((target - high) / (high - kill), 2) if target and high > kill else None,
    )
    if str(row.get("sma_role") or "") == "reclaim":
        out["add_trigger_low"] = _r(row.get("entry_low"))
        out["add_trigger_high"] = _r(row.get("entry_high"))
    street = _f(row.get("street_px"))
    window_high = _f(row.get("window_high"))
    caps = [x for x in (street, window_high) if x]
    zone = min(caps) if caps else None
    out["trim_zone"] = _r(zone) if zone and zone > close else None
    out["trim_zone_state"] = "above_street" if street and close >= street else ("open" if out["trim_zone"] else "")
    return out


def build_safety_band(
    catalog: Sequence[Mapping[str, Any]],
    *,
    lens: Sequence[Mapping[str, Any]] = (),
    history: Sequence[Mapping[str, Any]] = (),
    recipe: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Join lens and history reads onto the catalog by EXCHANGE:TICKER, then price the levels."""
    spec = recipe or load_safety_recipe()
    by_lens = {str(r.get("symbol")): r for r in lens}
    series: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in history:
        series[str(r.get("symbol"))].append(r)
    out: list[dict[str, Any]] = []
    for raw in catalog:
        rec = dict(raw)
        symbol = str(rec.get("symbol") or "")
        for key in ("pe_nfy", "pe_nfy_ind_med", "pe_nfy_vs_ind"):
            if rec.get(key) in (None, "") and (by_lens.get(symbol) or {}).get(key) not in (None, ""):
                rec[key] = by_lens[symbol][key]
        points = sorted(series.get(symbol) or [], key=lambda p: str(p.get("as_of")))
        if points:
            rec.update(history_reads(points, spec.get("split_guard")))
        rec.update(safety_levels(rec, spec))
        out.append(rec)
    return out


def write_safety_band(rows: Sequence[Mapping[str, Any]], out_dir: str | Path, *, source_note: str | None = None) -> dict[str, Any]:
    return write_run_export(
        out_dir,
        tool="safety_band",
        tables={"safety_band": list(rows)},
        sources={"note": source_note or ""},
        schema_version="safety_band_v1",
        notes=[
            "px_own_low_pe / px_own_med_pe / px_peer_pe: today's next-FY EPS forecast x the window's lowest / median forward PE / the industry median forward PE. mos_* is that price vs the close, %.",
            "A low forward PE on estimates that tripled (eps_fc_pct) is a cycle question, not a margin of safety by itself.",
            "Window path is split-adjusted (splits counts the events). bounce_* is a buy-in-red band under the close; bounce_kill ends that entry.",
            "add_trigger_* is the entry book's 50-day reclaim band (confirmation add). trim_zone is min(street_px, window_high) above the close.",
        ],
        duckdb_name="safety_band.duckdb",
    )
