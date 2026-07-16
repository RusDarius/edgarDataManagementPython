"""Risk-focused cross-lens evaluation of edge research parent run."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import duckdb

RUN = Path(
    r"logs/tradingview_analysis/edge_research_tools/runs/"
    r"edge_latest_500m_full_parent_20260713_1746_utc_6d8e320a"
)
AGG = RUN / "aggregate"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def f(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    raw = row.get(key, "")
    if raw in ("", None):
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def sym(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or row.get("ticker") or "").upper()


def main() -> None:
    paths = {
        "trade_plan": AGG / "edge_trade_plan" / "edge_trade_plan_top20.csv",
        "tradeable_safety": AGG / "tradeable_safety_lens" / "edge_tradeable_safety_top20.csv",
        "tradeable_overlay": AGG / "tradeable_safety_lens" / "edge_tradeable_safety_overlay.csv",
        "blindspot_top": AGG / "blindspot_lane" / "edge_blindspot_symbol_top40.csv",
        "blindspot_all": AGG / "blindspot_lane" / "edge_blindspot_symbol_candidates.csv",
        "blindspot_industry": AGG / "blindspot_lane" / "edge_blindspot_industry_rotation.csv",
        "earnings": AGG / "earnings_priority_lens" / "edge_earnings_priority_candidates.csv",
        "upside_opp_top": AGG / "upside_opportunity_scan" / "upside_opportunity_top30.csv",
        "upside_opp_all": AGG / "upside_opportunity_scan" / "upside_opportunity_candidates.csv",
        "upside_pred": AGG / "upside_prediction_lens" / "edge_upside_prediction_top20.csv",
        "fwd_val": AGG / "forward_upside_valuation_lens" / "edge_forward_upside_valuation_top20.csv",
        "unified": AGG / "edge_unified_highlights" / "edge_unified_highlights.csv",
    }

    data = {name: read_csv(path) for name, path in paths.items() if path.exists()}

    # Index by symbol
    idx: dict[str, dict[str, dict[str, Any]]] = {}
    for lens, rows in data.items():
        for row in rows:
            s = sym(row)
            if not s:
                continue
            idx.setdefault(s, {})[lens] = row

    # Risk-first composite scoring
    scored: list[dict[str, Any]] = []
    for symbol, lenses in idx.items():
        u = lenses.get("unified", {})
        ts = lenses.get("tradeable_safety", lenses.get("tradeable_overlay", {}))
        uo = lenses.get("upside_opp_all", lenses.get("upside_opp_top", {}))
        bp = lenses.get("blindspot_top", lenses.get("blindspot_all", {}))
        ep = lenses.get("earnings", {})
        tp = lenses.get("trade_plan", {})
        up = lenses.get("upside_pred", {})
        fv = lenses.get("fwd_val", {})

        safety = f(ts, "safety_companion_score") or f(u, "safety_companion_score")
        tradeable_blend = f(ts, "tradeable_safety_blend_score")
        tradeable_core = f(ts, "tradeable_core_score") or f(u, "tradeable_core_score")
        hist_val = f(uo, "opp_historical_validation_component") or f(up, "historical_validation_score")
        opp_score = f(uo, "upside_opportunity_score")
        binary_risk = f(uo, "opp_binary_risk_score")
        downside_risk = f(uo, "opp_downside_risk_score")
        tier = uo.get("opportunity_tier", "")
        lean = uo.get("directional_lean", "")
        blindspot_flag = str(bp.get("blindspot_flag", "0")) == "1"
        quiet = f(bp, "quiet_mover_score")
        earnings_days = f(ep, "days_to_next_earnings", 999)
        earnings_priority = f(ep, "earnings_priority_score")
        plan_score = f(tp, "trade_plan_score")
        fwd_upside = f(fv, "forward_valuation_upside_pct") or f(u, "forward_valuation_upside_pct")

        # Skip obvious downside-risk flags for long setups
        if lean in {"AVOID_DOWNSIDE_RISK"}:
            continue
        if tier == "CAUTION_DOWNSIDE_RISK" and opp_score < 0.35:
            continue

        risk_penalty = 0.35 * binary_risk + 0.25 * downside_risk
        if lean == "CAUTION_DOWNSIDE":
            risk_penalty += 0.08
        if tier == "CAUTION_DOWNSIDE_RISK":
            risk_penalty += 0.05

        lens_hits = sum(
            1
            for key in (
                "trade_plan",
                "tradeable_safety",
                "upside_pred",
                "fwd_val",
                "upside_opp_top",
                "blindspot_top",
            )
            if key in lenses
        )
        earnings_near = earnings_days <= 14 and earnings_priority > 0

        composite = (
            0.28 * tradeable_blend
            + 0.22 * safety
            + 0.18 * tradeable_core
            + 0.12 * hist_val
            + 0.10 * opp_score
            + 0.05 * quiet
            + 0.05 * (earnings_priority if earnings_near else 0.0)
            + 0.04 * min(fwd_upside / 100.0, 1.0)
            + 0.02 * plan_score
            + 0.04 * (lens_hits / 6.0)
            - risk_penalty
        )

        scored.append(
            {
                "symbol": symbol,
                "composite": composite,
                "safety": safety,
                "tradeable_blend": tradeable_blend,
                "tradeable_core": tradeable_core,
                "opp_score": opp_score,
                "binary_risk": binary_risk,
                "downside_risk": downside_risk,
                "tier": tier,
                "lean": lean,
                "quiet_mover": quiet,
                "blindspot": blindspot_flag,
                "earnings_days": earnings_days if earnings_days < 999 else None,
                "earnings_priority": earnings_priority,
                "fwd_upside_pct": fwd_upside,
                "hist_val": hist_val,
                "plan_score": plan_score,
                "lens_hits": lens_hits,
                "industry": u.get("industry") or bp.get("industry") or ep.get("industry", ""),
                "safety_bucket": ts.get("safety_bucket") or u.get("safety_bucket", ""),
                "in_shortlist": str(u.get("in_shortlist_flag", bp.get("in_shortlist_flag", ""))) == "1",
            }
        )

    scored.sort(key=lambda r: r["composite"], reverse=True)

    print("=== SCAN META ===")
    manifest = json.loads((RUN / "parent_run_manifest.json").read_text(encoding="utf-8"))
    print(f"scan_day={manifest['scan_day']} window={manifest['window_start_date']}->{manifest['end_day_label']}")
    print(f"min_mcap=${manifest['min_market_cap_usd']/1e6:.0f}M")

    uo_report = (AGG / "upside_opportunity_scan" / "upside_opportunity_scan_report.md").read_text(
        encoding="utf-8"
    )
    for line in uo_report.splitlines():
        if line.startswith("- ") and any(x in line for x in ("CAUTION", "MODERATE", "HIGH", "LEAN", "AVOID", "candidate")):
            print(line)

    print("\n=== TOP 15 RISK-ADJUSTED COMPOSITE (multi-lens) ===")
    for i, row in enumerate(scored[:15], 1):
        print(
            f"{i:2d} {row['symbol']:<14} comp={row['composite']:.3f} "
            f"safety={row['safety']:.2f} blend={row['tradeable_blend']:.2f} "
            f"opp={row['opp_score']:.2f} binRisk={row['binary_risk']:.2f} "
            f"downRisk={row['downside_risk']:.2f} lean={row['lean'] or '-'} "
            f"tier={row['tier'] or '-'} lenses={row['lens_hits']} "
            f"blindspot={int(row['blindspot'])} earn_d={row['earnings_days']} "
            f"ind={row['industry'][:28]}"
        )

    # Trade plan top
    print("\n=== EDGE TRADE PLAN TOP 10 ===")
    for i, row in enumerate(data.get("trade_plan", [])[:10], 1):
        print(
            f"{i:2d} {sym(row):<14} plan={f(row,'trade_plan_score'):.3f} "
            f"action={row.get('recommended_action','')} "
            f"safety={f(row,'safety_companion_score'):.2f} "
            f"blend={f(row,'tradeable_safety_blend_score'):.2f} "
            f"hist={f(row,'historical_validation_score'):.2f}"
        )

    # Tradeable safety top
    print("\n=== TRADEABLE SAFETY TOP 10 ===")
    for i, row in enumerate(data.get("tradeable_safety", [])[:10], 1):
        print(
            f"{i:2d} {sym(row):<14} blend={f(row,'tradeable_safety_blend_score'):.3f} "
            f"safety={f(row,'safety_companion_score'):.2f} bucket={row.get('safety_bucket','')} "
            f"core={f(row,'tradeable_core_score'):.2f}"
        )

    # Upside opportunity - best moderate with low binary risk
    print("\n=== UPSIDE OPPORTUNITY: BEST MODERATE + LOW BINARY RISK ===")
    uo_rows = data.get("upside_opp_all", [])
    uo_filtered = [
        r
        for r in uo_rows
        if r.get("opportunity_tier") == "MODERATE_UPSIDE"
        and r.get("directional_lean") in {"LEAN_UP", "NEUTRAL", "STRONG_UP"}
    ]
    uo_filtered.sort(key=lambda r: f(r, "upside_opportunity_score") - 0.5 * f(r, "opp_binary_risk_score"), reverse=True)
    for i, row in enumerate(uo_filtered[:12], 1):
        print(
            f"{i:2d} {sym(row):<14} opp={f(row,'upside_opportunity_score'):.3f} "
            f"mom={f(row,'opp_momentum_component'):.2f} hist={f(row,'opp_historical_validation_component'):.2f} "
            f"val={f(row,'opp_valuation_component'):.2f} safe={f(row,'opp_safety_quality_component'):.2f} "
            f"binRisk={f(row,'opp_binary_risk_score'):.2f} downRisk={f(row,'opp_downside_risk_score'):.2f} "
            f"lean={row.get('directional_lean','')}"
        )

    # Blindspot: flag=1, in shortlist, with safety cross-check
    print("\n=== BLINDSPOT: QUIET MOVERS WITH SAFETY >= 0.5 ===")
    bl_rows = data.get("blindspot_all", [])
    bl_filtered = [
        r
        for r in bl_rows
        if str(r.get("blindspot_flag")) == "1"
        and f(r, "quiet_mover_score") >= 0.55
    ]
    # join safety from unified
    unified_by_sym = {sym(r): r for r in data.get("unified", [])}
    bl_scored = []
    for r in bl_filtered:
        s = sym(r)
        u = unified_by_sym.get(s, {})
        safety = f(u, "safety_companion_score")
        if safety < 0.45 and safety > 0:
            continue
        bl_scored.append((r, safety))
    bl_scored.sort(key=lambda x: f(x[0], "quiet_mover_score"), reverse=True)
    for i, (row, safety) in enumerate(bl_scored[:12], 1):
        print(
            f"{i:2d} {sym(row):<14} quiet={f(row,'quiet_mover_score'):.3f} "
            f"trend_persist={f(row,'trend_persistence_score'):.2f} "
            f"hist_win={f(row,'blindspot_hist_win_rate'):.2f} "
            f"hist_n={int(f(row,'blindspot_hist_occurrence_count'))} "
            f"in_shortlist={row.get('in_shortlist_flag','')} safety={safety:.2f} "
            f"ind={row.get('industry','')[:30]}"
        )

    # Industry rotation top
    print("\n=== BLINDSPOT INDUSTRY ROTATION TOP 8 ===")
    ind_rows = sorted(
        data.get("blindspot_industry", []),
        key=lambda r: f(r, "industry_rotation_score"),
        reverse=True,
    )
    for i, row in enumerate(ind_rows[:8], 1):
        print(
            f"{i} {row.get('industry','')[:40]:<40} rot={f(row,'industry_rotation_score'):.3f} "
            f"delta={f(row,'industry_rotation_delta'):.3f} "
            f"top_quiet={row.get('top_quiet_movers','')[:60]}"
        )

    # Earnings priority: near-term + good safety
    print("\n=== EARNINGS PRIORITY: <=10d + SAFETY + IN SHORTLIST ===")
    ep_rows = data.get("earnings", [])
    ep_filtered = []
    for r in ep_rows:
        days = f(r, "days_to_next_earnings", 999)
        if days > 10:
            continue
        s = sym(r)
        u = unified_by_sym.get(s, {})
        safety = f(u, "safety_companion_score") or f(r, "safety_companion_score")
        if str(u.get("in_shortlist_flag", r.get("in_shortlist_flag", ""))) != "1":
            continue
        ep_filtered.append((r, safety, days))
    ep_filtered.sort(key=lambda x: (-f(x[0], "earnings_priority_score"), -x[1]))
    for i, (row, safety, days) in enumerate(ep_filtered[:12], 1):
        print(
            f"{i:2d} {sym(row):<14} earn_d={int(days)} prio={f(row,'earnings_priority_score'):.3f} "
            f"safety={safety:.2f} blend={f(row,'tradeable_safety_blend_score'):.2f} "
            f"unified_rank={row.get('unified_highlight_rank','')} "
            f"ind={row.get('industry','')[:28]}"
        )

    # Multi-lens convergence (appear in 3+ top lists)
    print("\n=== MULTI-LENS CONVERGENCE (3+ top lists) ===")
    top_sets = {
        "trade_plan": {sym(r) for r in data.get("trade_plan", [])},
        "tradeable_safety": {sym(r) for r in data.get("tradeable_safety", [])},
        "upside_pred": {sym(r) for r in data.get("upside_pred", [])},
        "fwd_val": {sym(r) for r in data.get("fwd_val", [])},
        "upside_opp": {sym(r) for r in data.get("upside_opp_top", [])},
        "blindspot": {sym(r) for r in data.get("blindspot_top", [])},
    }
    all_syms = set().union(*top_sets.values())
    conv = []
    for s in all_syms:
        hits = [k for k, st in top_sets.items() if s in st]
        if len(hits) < 3:
            continue
        u = unified_by_sym.get(s, {})
        uo = next((r for r in data.get("upside_opp_all", []) if sym(r) == s), {})
        conv.append(
            {
                "symbol": s,
                "hits": hits,
                "safety": f(u, "safety_companion_score"),
                "blend": f(u, "tradeable_safety_blend_score"),
                "lean": uo.get("directional_lean", ""),
                "tier": uo.get("opportunity_tier", ""),
                "binary_risk": f(uo, "opp_binary_risk_score"),
            }
        )
    conv.sort(key=lambda r: (len(r["hits"]), r["blend"], r["safety"]), reverse=True)
    for row in conv[:15]:
        print(
            f"  {row['symbol']:<14} lists={','.join(row['hits'])} "
            f"safety={row['safety']:.2f} blend={row['blend']:.2f} "
            f"tier={row['tier']} lean={row['lean']} binRisk={row['binary_risk']:.2f}"
        )

    # DuckDB: downside risk concentration
    print("\n=== UPSIDE OPP DOWNSIDE RISK STATS ===")
    conn = duckdb.connect((AGG / "upside_opportunity_scan" / "upside_opportunity_scan.duckdb").as_posix(), read_only=True)
    stats = conn.execute("""
        SELECT directional_lean, opportunity_tier, COUNT(*) n,
               AVG(opp_binary_risk_score) avg_bin,
               AVG(opp_downside_risk_score) avg_down,
               AVG(upside_opportunity_score) avg_opp
        FROM upside_opportunity_candidates
        GROUP BY 1,2 ORDER BY n DESC
    """).fetchall()
    for row in stats:
        print(row)


if __name__ == "__main__":
    main()
