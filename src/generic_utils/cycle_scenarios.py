"""Cycle scenarios: what a price assumes about how long an earnings boom lasts and how deep the bust after it is.

Per name (the newest forward value run's ``value_names`` joined to PACK/deterioration ``names`` for the Book line,
margins and revisions):

1. Year 1 EPS is the street's (``eps1``; else TTM EPS x (1 + the street revenue growth)).
2. k extra boom years (recipe ``boom_years``): EPS keeps growing at the year-1 pace (the larger of the EPS and the
   revenue growth) halved each year (``boom_decay``), between ``boom_floor`` and ``boom_cap``. The peak is year 1 + k.
3. The bust: EPS falls by d of the peak (recipe ``drops``) for ``bust_years``, then climbs in a straight line over
   ``recover_years`` to the normalized level peak x (1 - d x ``norm_share_of_drop``) and stays there.
4. Value today = owner cash (``cash_conversion`` x EPS each year) plus the normalized EPS x a through-cycle exit
   P/E at year H (``exit_pe``: fixed multiples and the industry median forward P/E), discounted at the run's
   beta-based hurdle. ``irr`` is the annual return the path pays from today's close.
5. Reverse reads: ``drop_in_k<k>`` = the drop that makes the value equal today's close after k boom years (empty
   with ``drop_state_k<k>`` = needs_more when even no bust is not enough, covers_max when the price survives a drop of
   ``drop_solve_max``); ``pe_need_k<k>_d<d>`` = the exit P/E on the normalized EPS the close requires; ``k_needed_d<d>`` =
   the fewest boom years in the grid whose value covers the close at drop d.

``stress`` (optional ``shocks`` CSV: symbol + one column per scenario in percent) turns per-line price shocks into
Book dollars and NAV percent with a total row per scenario (empty symbol, role ``BOOK total``). Scenarios, not forecasts: no ranking, no
composite, no cutoff, no course; the knobs live in config/generic_utils/cycle_scenarios.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from financial_projection.valuation import internal_rate, present_value
from generic_utils.ranking import to_float
from generic_utils.scan_sources import rows_from_csv

_f = to_float
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "cycle_scenarios.json"
US_EXCHANGES = ("NASDAQ", "NYSE", "AMEX", "NYSE ARCA", "CBOE")


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _d_key(d: float) -> str:
    return str(int(round(d * 100)))


# --- the path and its value ---------------------------------------------------------------------------------


def eps_path(e1: float, boom_g: float, *, k: int, d: float, recipe: Mapping[str, Any]) -> dict[str, Any]:
    """EPS for years 1..H: k boom years after year 1, a bust of d from the peak, a recovery to the normalized level."""
    horizon = int(recipe.get("horizon_years", 5))
    decay, low, high = float(recipe.get("boom_decay", 0.5)), float(recipe.get("boom_floor", 0.0)), float(recipe.get("boom_cap", 0.3))
    bust, rec = max(1, int(recipe.get("bust_years", 1))), max(1, int(recipe.get("recover_years", 2)))
    share = float(recipe.get("norm_share_of_drop", 0.5))
    peak_year = min(1 + k, horizon)
    eps = [e1]
    for t in range(2, peak_year + 1):
        eps.append(eps[-1] * (1.0 + _clip(boom_g * decay ** (t - 1), low, high)))
    peak = eps[-1]
    trough, norm = peak * (1.0 - d), peak * (1.0 - d * share)
    for t in range(peak_year + 1, horizon + 1):
        since = t - peak_year
        if since <= bust:
            eps.append(trough)
        elif since <= bust + rec:
            eps.append(trough + (norm - trough) * (since - bust) / rec)
        else:
            eps.append(norm)
    return {"eps": eps, "peak": peak, "trough": trough, "norm": norm, "peak_year": peak_year}


def scenario_value(close: float, e1: float, boom_g: float, rate: float, *, k: int, d: float, pe: float,
                   recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Value today, IRR and cycle reads for one (k, d, exit P/E)."""
    path = eps_path(e1, boom_g, k=k, d=d, recipe=recipe)
    conv = float(recipe.get("cash_conversion", 0.8))
    cash = [conv * e for e in path["eps"]]
    terminal = path["norm"] * pe
    fv = present_value(cash, terminal, rate)
    irr = internal_rate(close, cash, terminal) if terminal > 0 else None
    horizon = len(cash)
    pe_need = ((close - present_value(cash, 0.0, rate)) * (1.0 + rate) ** horizon / path["norm"]) if path["norm"] > 0 else None
    return {"fair_value": fv, "upside_pct": (fv / close - 1.0) * 100.0, "irr_pct": irr * 100.0 if irr is not None else None,
            "peak_eps": path["peak"], "trough_eps": path["trough"], "norm_eps": path["norm"], "peak_year": path["peak_year"],
            "cum_eps": sum(path["eps"]), "pe_on_peak": close / path["peak"] if path["peak"] > 0 else None,
            "pe_on_norm": close / path["norm"] if path["norm"] > 0 else None, "pe_need": pe_need, "terminal": terminal,
            "eps": path["eps"]}


def priced_in_drop(close: float, e1: float, boom_g: float, rate: float, *, k: int, pe: float,
                   recipe: Mapping[str, Any]) -> tuple[float | None, str]:
    """The drop d at which the scenario value equals the close (bisection), with its state."""
    d_max = float(recipe.get("drop_solve_max", 1.5))

    def gap(d: float) -> float:
        return scenario_value(close, e1, boom_g, rate, k=k, d=d, pe=pe, recipe=recipe)["fair_value"] - close

    g0, g1 = gap(0.0), gap(d_max)
    if g0 < 0:
        return None, "needs_more"
    if g1 > 0:
        return None, "covers_max"
    low, high = 0.0, d_max
    for _ in range(60):
        mid = (low + high) / 2.0
        if gap(mid) > 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0, "solved"


# --- inputs ----------------------------------------------------------------------------------------------------


def _latest_value_csv() -> Path | None:
    from generic_utils.forward_value import discover_latest_projection_db

    db = discover_latest_projection_db(prefix="finvalue_")
    if db is None:
        return None
    csv = Path(db).parent / "value_names.csv"
    return csv if csv.exists() else None


def _resolve(requested: str, *catalogs: Mapping[str, Any]) -> str | None:
    """A full id when it is in a catalog; a bare ticker matched on the first catalog that has it (US listings first)."""
    req = requested.strip()
    if ":" in req:
        return req if any(req in c for c in catalogs) else None
    for cat in catalogs:
        hits = [s for s in cat if s.split(":", 1)[-1] == req]
        us = [s for s in hits if s.split(":", 1)[0] in US_EXCHANGES]
        pick = us or hits
        if len(pick) == 1:
            return pick[0]
    return None


def name_inputs(value: Mapping[str, Any], recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Year-1 EPS, the boom pace and the hurdle for one value row, or the reason they are missing."""
    close, e0, e1 = _f(value.get("close")), _f(value.get("eps_ttm")), _f(value.get("eps1"))
    g_rev = _f(value.get("g1_pct"))
    g_rev = g_rev / 100.0 if g_rev is not None else None
    out: dict[str, Any] = {"close": close, "eps_ttm": e0, "eps1_source": "street_eps"}
    if "currency_mismatch" in str(value.get("flags") or ""):
        return {**out, "invalid_reason": "currency_mismatch"}
    if e1 is None and e0 is not None and e0 > 0 and g_rev is not None:
        e1, out["eps1_source"] = e0 * (1.0 + g_rev), "ttm_eps_x_revenue_growth"
    if not close or close <= 0 or e1 is None or e1 <= 0:
        return {**out, "eps1": e1, "invalid_reason": "no_positive_eps1"}
    g_eps = e1 / e0 - 1.0 if e0 and e0 > 0 else None
    paces = [g for g in (g_eps, g_rev) if g is not None]
    hurdle = _f(value.get("hurdle_pct"))
    rate = hurdle / 100.0 if hurdle is not None else float(recipe.get("fallback_rate", 0.12))
    pe_ind = _f(value.get("pe_ind_med"))
    if pe_ind is not None:
        pe_ind = _clip(pe_ind, float(recipe.get("industry_pe_floor", 6.0)), float(recipe.get("industry_pe_cap", 35.0)))
    return {**out, "eps1": e1, "g_eps1_pct": g_eps * 100.0 if g_eps is not None else None,
            "g_rev1_pct": g_rev * 100.0 if g_rev is not None else None, "boom_g": max(paces) if paces else 0.0,
            "rate": rate, "pe_ind": pe_ind, "invalid_reason": ""}


def _cell(value: Any) -> Any:
    """A CSV cell as a number when it is one, else the text (empty -> None), so the tables keep numeric columns."""
    num = _f(value)
    if num is not None:
        return num
    return value if value not in (None, "") else None


def _pe_options(recipe: Mapping[str, Any], pe_ind: float | None) -> list[tuple[str, float]]:
    out = []
    for p in recipe.get("exit_pe") or [15]:
        if p == "industry":
            if pe_ind is not None:
                out.append(("ind", pe_ind))
        else:
            out.append((f"{float(p):g}", float(p)))
    return out


# --- build -----------------------------------------------------------------------------------------------------


DET_FIELDS = ("book_usd", "wt_nav", "pnl_usd", "dd", "det", "r_3m", "r_12m", "eps_rev_3m", "rev_rev_3m", "rev_yoy", "eps_nfy_g",
              "opm", "opm_q_ago", "fcf_margin", "pe_fwd", "street_px")
VALUE_FIELDS = ("industry", "pe_fwd_now", "pe_ind_med", "m1_pct", "margin_ind_med_pct", "fv_base_px", "fv_upside_pct",
                "irr_exp_pct", "priced_in_cagr_pct")


def build_cycle_scenarios(pack_dir: str | Path, *, recipe: Mapping[str, Any] | None = None, ids: Sequence[str] = (),
                          value_csv: str | Path | None = None, shocks: str | Path | None = None) -> dict[str, Any]:
    recipe = recipe or load_recipe()
    pack = Path(pack_dir)
    vcsv = Path(value_csv) if value_csv else _latest_value_csv()
    if vcsv is None or not vcsv.exists():
        raise SystemExit("no forward value run (finvalue_*/value_names.csv): run python src/run_financial_projection.py")
    values = {str(r.get("symbol")): r for r in rows_from_csv(vcsv)}
    det_csv = pack / "deterioration" / "names.csv"
    det = {str(r.get("symbol")): r for r in rows_from_csv(det_csv)} if det_csv.exists() else {}
    book = [s for s, r in det.items() if str(r.get("in_book") or "").lower() in ("true", "1", "yes")]
    pack_json = pack / "briefing_pack.json"
    capital = (json.loads(pack_json.read_text(encoding="utf-8")).get("capital") or {}) if pack_json.exists() else {}
    nav = _f(capital.get("nav_usd"))

    symbols, missing = list(book), []
    for raw in ids:
        sym = _resolve(raw, det, values)
        if sym is None:
            missing.append(raw)
        elif sym not in symbols:
            symbols.append(sym)

    ks = [int(k) for k in recipe.get("boom_years") or [0, 1, 2, 3]]
    drops = [float(d) for d in recipe.get("drops") or [0.25, 0.5, 0.75]]
    primary = float(recipe.get("primary_pe", 15))
    names: list[dict[str, Any]] = []
    grid: list[dict[str, Any]] = []
    for sym in symbols:
        v, dr = values.get(sym) or {}, det.get(sym) or {}
        row: dict[str, Any] = {"symbol": sym, "in_book": sym in book}
        row.update({f: _cell(dr.get(f)) for f in DET_FIELDS})
        row.update({f: _cell(v.get(f)) for f in VALUE_FIELDS})
        m1, mind = _f(v.get("m1_pct")), _f(v.get("margin_ind_med_pct"))
        row["margin_x_ind"] = _r(m1 / mind, 2) if m1 is not None and mind and mind > 0 else None
        if not v:
            row["invalid_reason"] = "no_value_row"
            names.append(row)
            continue
        inp = name_inputs(v, recipe)
        row.update({"close": inp.get("close"), "eps_ttm": inp.get("eps_ttm"), "eps1": _r(inp.get("eps1"), 3),
                    "eps1_source": inp.get("eps1_source"), "invalid_reason": inp["invalid_reason"]})
        if inp["invalid_reason"]:
            names.append(row)
            continue
        close, e1, bg, rate = inp["close"], inp["eps1"], inp["boom_g"], inp["rate"]
        row.update({"g_eps1_pct": _r(inp["g_eps1_pct"]), "g_rev1_pct": _r(inp["g_rev1_pct"]), "boom_g_pct": _r(bg * 100.0),
                    "hurdle_pct": _r(rate * 100.0), "pe_y1": _r(close / e1), "pe_exit_ind": _r(inp["pe_ind"]),
                    "primary_pe": primary})
        for k in ks:
            path0 = eps_path(e1, bg, k=k, d=0.0, recipe=recipe)
            row[f"peak_eps_k{k}"] = _r(path0["peak"], 3)
            row[f"pe_on_peak_k{k}"] = _r(close / path0["peak"]) if path0["peak"] > 0 else None
            for tag, pe in _pe_options(recipe, inp["pe_ind"]):
                d_in, state = priced_in_drop(close, e1, bg, rate, k=k, pe=pe, recipe=recipe)
                suffix = "" if pe == primary and tag != "ind" else f"_pe{tag}"
                row[f"drop_in_k{k}{suffix}"] = _r(d_in * 100.0) if d_in is not None else None
                row[f"drop_state_k{k}{suffix}"] = state
                for d in drops:
                    s = scenario_value(close, e1, bg, rate, k=k, d=d, pe=pe, recipe=recipe)
                    grid.append({"symbol": sym, "k": k, "drop_pct": _r(d * 100.0, 0), "exit_pe_tag": tag, "exit_pe": _r(pe),
                                 "peak_year": s["peak_year"], "peak_eps": _r(s["peak_eps"], 3), "trough_eps": _r(s["trough_eps"], 3),
                                 "norm_eps": _r(s["norm_eps"], 3), "cum_eps": _r(s["cum_eps"], 3),
                                 "cum_eps_vs_close_pct": _r(s["cum_eps"] / close * 100.0), "pe_on_peak": _r(s["pe_on_peak"]),
                                 "pe_on_norm": _r(s["pe_on_norm"]), "pe_need": _r(s["pe_need"]), "fair_value": _r(s["fair_value"]),
                                 "upside_pct": _r(s["upside_pct"]), "irr_pct": _r(s["irr_pct"]), "close": close,
                                 "eps_path": "/".join(f"{e:.2f}" for e in s["eps"])})
                    if pe == primary and tag != "ind":
                        row[f"fv_k{k}_d{_d_key(d)}"] = _r(s["fair_value"])
                        row[f"up_k{k}_d{_d_key(d)}"] = _r(s["upside_pct"])
                        row[f"pe_need_k{k}_d{_d_key(d)}"] = _r(s["pe_need"])
        for d in drops:
            need = None
            for k in ks:
                if scenario_value(close, e1, bg, rate, k=k, d=d, pe=primary, recipe=recipe)["fair_value"] >= close:
                    need = k
                    break
            row[f"k_needed_d{_d_key(d)}"] = need
        names.append(row)

    stress, missing_shock = [], []
    if shocks:
        stress, missing_shock = _stress(Path(shocks), det, book, nav)
    spec = {"pack": str(pack), "value_csv": str(vcsv), "nav_usd": nav, "n_names": len(names),
            "n_valid": sum(1 for r in names if not r.get("invalid_reason")), "n_grid": len(grid), "missing_ids": missing,
            "invalid": {r["symbol"]: r["invalid_reason"] for r in names if r.get("invalid_reason")},
            "shocks": str(shocks) if shocks else None, "missing_shock": missing_shock,
            "recipe": {k: v for k, v in recipe.items() if not k.startswith("_")}}
    return {"spec": spec, "names": names, "grid": grid, "stress": stress}


def _stress(path: Path, det: Mapping[str, Mapping[str, Any]], book: Sequence[str], nav: float | None
            ) -> tuple[list[dict[str, Any]], list[str]]:
    """Book dollars and NAV percent for each line under each shock column (percent price moves)."""
    rows = rows_from_csv(path)
    if not rows:
        return [], []
    scen_cols = [c for c in rows[0].keys() if c not in ("symbol", "note", "role")]
    by_sym: dict[str, Mapping[str, Any]] = {}
    for r in rows:
        sym = _resolve(str(r.get("symbol") or ""), {s: 1 for s in book})
        if sym:
            by_sym[sym] = r
    out, missing = [], [s for s in book if s not in by_sym]
    for scen in scen_cols:
        tot_usd, tot_book = 0.0, 0.0
        for sym in book:
            usd_line = _f(det[sym].get("book_usd")) or 0.0
            shock = _f((by_sym.get(sym) or {}).get(scen)) or 0.0
            usd = usd_line * shock / 100.0
            tot_usd, tot_book = tot_usd + usd, tot_book + usd_line
            out.append({"scenario": scen, "symbol": sym, "role": (by_sym.get(sym) or {}).get("role"), "book_usd": _r(usd_line, 0),
                        "shock_pct": _r(shock, 1), "usd": _r(usd, 0), "nav_pct": _r(usd / nav * 100.0) if nav else None})
        out.append({"scenario": scen, "symbol": None, "role": "BOOK total", "book_usd": _r(tot_book, 0),
                    "shock_pct": _r(tot_usd / tot_book * 100.0, 1) if tot_book else None, "usd": _r(tot_usd, 0),
                    "nav_pct": _r(tot_usd / nav * 100.0) if nav else None})
    return out, missing


def write_cycle_scenarios(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    spec = payload["spec"]
    rec = spec["recipe"]
    notes = [
        f"names: year-1 EPS = street eps1 (else TTM x revenue growth); k boom years {rec.get('boom_years')} at the year-1 pace "
        f"halved yearly (between {rec.get('boom_floor')} and {rec.get('boom_cap')}); then a drop d {rec.get('drops')} of the peak for "
        f"{rec.get('bust_years')} year(s), a {rec.get('recover_years')}-year climb to peak x (1 - d x {rec.get('norm_share_of_drop')}); owner cash "
        f"{rec.get('cash_conversion')} x EPS; exit = normalized EPS x P/E at year {rec.get('horizon_years')}; discounted at the value run's hurdle.",
        f"drop_in_k<k> (primary P/E {rec.get('primary_pe')}; _pe<tag> other exits): the peak-to-trough EPS drop the close already pays for after k "
        "boom years. needs_more = the price needs more than k boom years even with no bust; covers_max = it survives the largest drop.",
        "pe_need_k<k>_d<d>: the exit P/E on the normalized EPS at year H that the close requires after k boom years and drop d "
        "(today's price less the discounted owner cash, carried to year H, over the normalized EPS); compare it with what cyclicals "
        "and the industry are paid through a cycle.",
        "k_needed_d<d>: the fewest boom years in the grid at which the close is covered at drop d (empty = more than the grid).",
        "up_k<k>_d<d> / fv_k<k>_d<d>: value at the primary exit P/E vs the close. grid: every (k, d, exit P/E) with the EPS path, "
        "cum_eps_vs_close_pct (five years of EPS against the price), pe_on_peak / pe_on_norm and the IRR.",
        "stress: per-line price shocks (percent, from the shocks CSV) in Book dollars and NAV percent; role BOOK total (empty symbol) = the total per scenario.",
        "Scenarios, not forecasts: no ranking, no composite, no cutoff, no course.",
    ]
    tables = {"names": payload.get("names") or [], "grid": payload.get("grid") or []}
    if payload.get("stress"):
        tables["stress"] = payload["stress"]
    return write_run_export(out_dir, tool="generic_utils.cycle_scenarios", tables=tables,
                            sources={"pack": spec["pack"], "value_csv": spec["value_csv"], "shocks": spec["shocks"]},
                            notes=notes, extra={"spec": spec}, duckdb_name="cycle_scenarios.duckdb")


def cycle_scenarios_local_main() -> None:
    from generic_utils.brief_history import latest_pack_dir

    pack = latest_pack_dir(REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs")
    if pack is None:
        raise SystemExit("No briefing_pack_* folder found")
    print(write_cycle_scenarios(build_cycle_scenarios(pack), pack / "cycle_scenarios"))


if __name__ == "__main__":
    cycle_scenarios_local_main()
