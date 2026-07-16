import duckdb
import pandas as pd
from pathlib import Path

base = Path(
    r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis"
    r"/edge_research_tools/runs/edge_latest_500m_full_parent_20260713_1503_utc_ce6f36f2/aggregate"
)
u = pd.read_csv(base / "edge_unified_highlights/edge_unified_highlights.csv")
tp = pd.read_csv(base / "edge_trade_plan/edge_trade_plan_top20.csv")
p = r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=29/parquet/earnings_priority_consensus_rows.parquet"

# map bare_ticker to symbol for merge
u["ticker_key"] = u["symbol"].str.split(":", n=1).str[-1]

ep = duckdb.sql(
    f"""
    SELECT ticker, earnings_release_next_date, days_until_earnings,
           consensus_weeks_score, consensus_days_score, bucket, industry, company
    FROM read_parquet('{p}')
    WHERE sort_flavour = 'score'
      AND run_id LIKE '%08199ba2%'
      AND days_until_earnings <= 7
      AND consensus_weeks_score >= 0.25
    ORDER BY consensus_weeks_score DESC
    """
).df()

m = ep.merge(u, left_on="ticker", right_on="bare_ticker", how="left")
on = m[m["unified_edge_highlight_rank"].notna()].sort_values("consensus_weeks_score", ascending=False)
print("EARNINGS this week on edge radar:")
cols = [
    "symbol", "company", "earnings_release_next_date", "days_until_earnings",
    "consensus_weeks_score", "unified_edge_highlight_rank", "tradeable_safety_rank",
    "upside_prediction_rank", "safety_companion_score", "safety_bucket",
    "forward_valuation_upside_pct",
]
print(on.head(15)[cols].round(4).to_string(index=False))

safe = m[
    (m["consensus_weeks_score"] >= 0.30)
    & (m["safety_companion_score"].fillna(0) >= 0.55)
].sort_values("consensus_weeks_score", ascending=False)
print("\nEARNINGS safer bullish:")
print(safe.head(12)[cols].round(4).to_string(index=False))

print("\nTRADE PLAN TOP 20:")
tcols = [
    "trade_plan_rank", "entry_state", "symbol", "company_name",
    "trade_plan_score", "trade_plan_quality_score", "trade_plan_timing_score",
    "unified_edge_highlight_rank", "tradeable_safety_rank", "safety_bucket",
    "forward_valuation_upside_pct", "next_earnings_at",
]
print(tp[tcols].to_string(index=False))
