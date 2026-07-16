"""Adhoc: synthesize edge scan recommendations from ce6f36f2 run."""
from __future__ import annotations

import re

import duckdb
import pandas as pd
from pathlib import Path

BASE = Path(
    r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis"
    r"/edge_research_tools/runs/edge_latest_500m_full_parent_20260713_1503_utc_ce6f36f2/aggregate"
)
EP_CONS = Path(
    r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis"
    r"/prediction_analysis/duckdb_runs/iso_year=2026/week=29/parquet"
    r"/earnings_priority_consensus_rows.parquet"
)
EP_PROF = Path(
    r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis"
    r"/prediction_analysis/duckdb_runs/iso_year=2026/week=29/parquet"
    r"/earnings_priority_profile_rows.parquet"
)


def _rank_pctile(rank: pd.Series) -> pd.Series:
  """Lower rank number = better -> higher percentile."""
  return 1.0 - rank.rank(pct=True, ascending=True, method="average")


def _score_pctile(score: pd.Series) -> pd.Series:
  return score.rank(pct=True, ascending=True, method="average")


def main() -> None:
  unified = pd.read_csv(BASE / "edge_unified_highlights/edge_unified_highlights.csv")
  trade = pd.read_csv(BASE / "tradeable_safety_lens/edge_tradeable_safety_overlay.csv")
  blind_top = pd.read_csv(BASE / "blindspot_lane/edge_blindspot_symbol_top40.csv")
  trade_plan = pd.read_csv(BASE / "edge_trade_plan/edge_trade_plan_ranked.csv")

  df = unified.copy()
  n = len(df)

  df["upside_pctile"] = _rank_pctile(df["upside_prediction_rank"].fillna(n))
  df["fwd_pctile"] = _rank_pctile(df["forward_upside_rank"].fillna(n))
  df["tradeable_pctile"] = _rank_pctile(df["tradeable_safety_rank"].fillna(n))
  df["unified_pctile"] = _rank_pctile(df["unified_edge_highlight_rank"].fillna(n))
  df["safety_pctile"] = _score_pctile(df["safety_companion_score"].fillna(0))
  df["mover_pctile"] = _score_pctile(df["big_mover_score"].fillna(0))

  hist_bonus = df["historical_validation_bucket"].map(
    {"supported": 0.06, "mixed": 0.0, "thin": -0.06, "weak": -0.12}
  ).fillna(-0.04)
  safety_bonus = df["safety_bucket"].map(
    {"safer": 0.05, "balanced": 0.02, "aggressive": 0.0, "speculative": -0.04, "distressed": -0.15}
  ).fillna(-0.02)

  df["risk_weighted_upside"] = (
    0.22 * df["upside_pctile"]
    + 0.20 * df["fwd_pctile"]
    + 0.18 * df["tradeable_pctile"]
    + 0.15 * df["unified_pctile"]
    + 0.12 * df["safety_pctile"]
    + 0.08 * df["mover_pctile"]
    + hist_bonus
    + safety_bonus
  )

  show = [
    "symbol", "company_name", "industry", "risk_weighted_upside",
    "unified_edge_highlight_rank", "tradeable_safety_rank", "upside_prediction_rank",
    "forward_upside_rank", "forward_valuation_upside_pct", "safety_companion_score",
    "safety_bucket", "historical_validation_bucket", "unified_best_outlook_name",
  ]

  print("=" * 100)
  print("SCAN: edge_latest_500m_full_parent_20260713_1503_utc_ce6f36f2 | scan_day=2026-07-10")
  print("=" * 100)

  print("\n## TIER A — Risk-weighted upside (top 15)")
  print(df.sort_values("risk_weighted_upside", ascending=False).head(15)[show].round(4).to_string(index=False))

  # Safety-first: tradeable top 20 merged with upside
  tm = trade.merge(
    unified[["symbol", "upside_prediction_rank", "forward_valuation_upside_pct", "unified_edge_highlight_rank"]],
    on="symbol", how="left", suffixes=("", "_u"),
  )
  tm["safe_upside"] = (
    0.45 * _rank_pctile(tm["tradeable_safety_rank"])
    + 0.30 * _rank_pctile(tm["upside_prediction_rank"].fillna(n))
    + 0.25 * _score_pctile(tm["safety_companion_score"].fillna(0))
  )
  print("\n## TIER B — Safety-first with upside (top 12)")
  bshow = [
    "symbol", "company_name", "safe_upside", "tradeable_safety_rank",
    "tradeable_safety_blend_score", "upside_prediction_rank", "forward_valuation_upside_pct",
    "safety_companion_score", "safety_bucket", "historical_validation_bucket",
    "unified_edge_highlight_rank",
  ]
  print(tm.sort_values("safe_upside", ascending=False).head(12)[bshow].round(4).to_string(index=False))

  # Pure momentum / valuation upside (accept more risk)
  print("\n## TIER C — Max upside / valuation (unified top 12, flag thin hist)")
  print(df.sort_values("unified_edge_highlight_rank").head(12)[show].round(4).to_string(index=False))

  # Blindspot
  blind_m = blind_top.merge(
    unified[[
      "symbol", "safety_companion_score", "safety_bucket",
      "upside_prediction_rank", "tradeable_safety_rank", "forward_valuation_upside_pct",
      "unified_edge_highlight_rank",
    ]],
    on="symbol", how="left",
  )
  blind_m["blindspot_safe"] = (
    0.45 * _score_pctile(blind_m["quiet_mover_score"].fillna(0))
    + 0.30 * _score_pctile(blind_m["safety_companion_score"].fillna(0))
    + 0.25 * _score_pctile(blind_m["blindspot_hist_win_rate"].fillna(0))
  )
  print("\n## BLINDSPOT — Quiet movers + safety cross (top 12)")
  bcols = [
    "symbol", "company_name", "industry", "blindspot_safe", "quiet_mover_score",
    "trend_persistence_score", "in_shortlist_flag", "currently_quant_hot_flag",
    "safety_companion_score", "safety_bucket", "unified_edge_highlight_rank",
    "blindspot_hist_median_fwd_pct", "blindspot_hist_win_rate", "blindspot_hist_occurrence_count",
  ]
  print(blind_m.sort_values("blindspot_safe", ascending=False).head(12)[bcols].round(4).to_string(index=False))

  # Industry rotation blindspot
  ind_rot = pd.read_csv(BASE / "blindspot_lane/edge_blindspot_industry_rotation.csv")
  print("\n## BLINDSPOT — Top rotating industries")
  icols = [c for c in ind_rot.columns if "industry" in c.lower() or "rotation" in c.lower() or "quiet" in c.lower()][:8]
  sort_i = "industry_rotation_score" if "industry_rotation_score" in ind_rot.columns else ind_rot.columns[1]
  print(ind_rot.sort_values(sort_i, ascending=False).head(8)[icols].to_string(index=False))

  # Trade plan
  tp_rank = "edge_trade_plan_rank" if "edge_trade_plan_rank" in trade_plan.columns else None
  if tp_rank:
    tpcols = [c for c in trade_plan.columns if c in ("symbol", "company_name", tp_rank) or "entry" in c.lower() or "action" in c.lower() or "timing" in c.lower() or "state" in c.lower()]
    tpcols = list(dict.fromkeys(tpcols))[:12]
    tp_top = trade_plan.sort_values(tp_rank).head(12)
    print("\n## TRADE PLAN top 12")
    print(tp_top[tpcols].to_string(index=False))

  # Earnings priority via duckdb
  if EP_CONS.exists():
    ep_cons = duckdb.sql(f"SELECT * FROM read_parquet('{EP_CONS.as_posix()}')").df()
    ep_prof = duckdb.sql(f"SELECT * FROM read_parquet('{EP_PROF.as_posix()}')").df() if EP_PROF.exists() else pd.DataFrame()
    print(f"\n## EARNINGS consensus rows: {len(ep_cons)}")
    print("cols:", list(ep_cons.columns)[:20])

    sym = "symbol" if "symbol" in ep_cons.columns else "ticker"
    # find weeks score column
    wk_col = next((c for c in ep_cons.columns if "wks" in c.lower() and "score" in c.lower()), None)
    days_col = next((c for c in ep_cons.columns if "days" in c.lower() and "score" in c.lower()), None)
    earn_col = next((c for c in ep_cons.columns if "earn" in c.lower() and "date" in c.lower()), None)

    ep_u = ep_cons.merge(unified, on=sym, how="inner", suffixes=("_ep", ""))
    print(f"Earnings names overlapping unified shortlist: {len(ep_u)}")

    score_col = wk_col or days_col
    if score_col and len(ep_u):
      ep_u["earn_catalyst"] = (
        0.35 * _score_pctile(ep_u[score_col].fillna(-1))
        + 0.25 * _rank_pctile(ep_u["unified_edge_highlight_rank"].fillna(n))
        + 0.20 * _rank_pctile(ep_u["tradeable_safety_rank"].fillna(n))
        + 0.20 * _score_pctile(ep_u["safety_companion_score"].fillna(0))
      )
      ec = [
        sym, earn_col, score_col, "earn_catalyst",
        "unified_edge_highlight_rank", "tradeable_safety_rank", "upside_prediction_rank",
        "forward_valuation_upside_pct", "safety_companion_score", "safety_bucket",
        "company_name", "industry",
      ]
      ec = [c for c in ec if c in ep_u.columns]
      print("\n## EARNINGS CATALYST + EDGE (unified overlap, top 15 by catalyst score)")
      print(ep_u.sort_values("earn_catalyst", ascending=False).head(15)[ec].round(4).to_string(index=False))

    # imminent earnings (<=7 days) with positive weeks score
    if earn_col and score_col:
      ep_all = ep_cons.copy()
      ep_all[earn_col] = pd.to_datetime(ep_all[earn_col], errors="coerce")
      imminent = ep_all[ep_all[earn_col] <= pd.Timestamp("2026-07-20")]
      bullish = imminent[imminent[score_col] >= 0.25].sort_values(score_col, ascending=False)
      bull_m = bullish.merge(
        unified[[
          sym, "safety_companion_score", "safety_bucket", "unified_edge_highlight_rank",
          "tradeable_safety_rank", "upside_prediction_rank", "forward_valuation_upside_pct",
        ]].rename(columns={sym: sym}),
        on=sym, how="left",
      )
      print(f"\n## EARNINGS this week (<=Jul 20) with Wks score >= 0.25: {len(bullish)} names")
      print(f"    ...with unified shortlist overlap: {bull_m['unified_edge_highlight_rank'].notna().sum()}")
      show_e = [sym, earn_col, score_col, "safety_companion_score", "safety_bucket",
                "unified_edge_highlight_rank", "tradeable_safety_rank", "forward_valuation_upside_pct"]
      show_e = [c for c in show_e if c in bull_m.columns]
      on_radar = bull_m[bull_m["unified_edge_highlight_rank"].notna()].sort_values(score_col, ascending=False)
      if len(on_radar):
        print("\n## EARNINGS this week ON edge radar (top 12)")
        print(on_radar.head(12)[show_e].round(4).to_string(index=False))
      safe_bull = bull_m[
        (bull_m[score_col] >= 0.30)
        & (bull_m["safety_companion_score"].fillna(0) >= 0.55)
      ].sort_values(score_col, ascending=False)
      if len(safe_bull):
        print("\n## EARNINGS this week — safer + bullish consensus (top 10)")
        print(safe_bull.head(10)[show_e].round(4).to_string(index=False))


if __name__ == "__main__":
  main()
