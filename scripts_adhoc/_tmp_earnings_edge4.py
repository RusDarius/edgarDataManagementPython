import duckdb
import pandas as pd
from pathlib import Path

p = r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=29/parquet/earnings_priority_consensus_rows.parquet"
base = Path(
    r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis"
    r"/edge_research_tools/runs/edge_latest_500m_full_parent_20260713_1503_utc_ce6f36f2/aggregate"
)
u = pd.read_csv(base / "edge_unified_highlights/edge_unified_highlights.csv")

ep = duckdb.sql(
    f"""
    SELECT ticker AS symbol, company, earnings_release_next_date, days_until_earnings,
           consensus_weeks_score, consensus_days_score, bucket, industry
    FROM read_parquet('{p}')
    WHERE run_id = 'move_prediction_20260713_1333_utc_08199ba2'
      AND sort_flavour = 'default'
  """
).df()

m = ep.merge(u, on="symbol", how="inner", suffixes=("", "_u"))
print("total earnings-edge overlap", len(m))

imminent = m[(m["days_until_earnings"] <= 14) & (m["consensus_weeks_score"] >= 0.25)].sort_values(
    ["consensus_weeks_score", "tradeable_safety_rank"], ascending=[False, True]
)
cols = [
    "symbol", "company", "earnings_release_next_date", "days_until_earnings",
    "consensus_weeks_score", "consensus_days_score",
    "unified_edge_highlight_rank", "tradeable_safety_rank", "upside_prediction_rank",
    "safety_companion_score", "safety_bucket", "forward_valuation_upside_pct",
    "historical_validation_bucket",
]
print("\nIMMINENT EARNINGS + EDGE (top 20):")
print(imminent.head(20)[cols].round(4).to_string(index=False))

safe = imminent[
    (imminent["consensus_weeks_score"] >= 0.35)
    & (imminent["safety_companion_score"] >= 0.55)
    & (imminent["safety_bucket"].isin(["safer", "balanced", "aggressive"]))
].sort_values("consensus_weeks_score", ascending=False)
print("\nSAFER IMMINENT CATALYSTS:")
print(safe.head(12)[cols].round(4).to_string(index=False))

# high conviction: weeks>=0.5, tradeable top 100
hi = imminent[
    (imminent["consensus_weeks_score"] >= 0.45)
    & (imminent["tradeable_safety_rank"] <= 150)
].sort_values(["consensus_weeks_score", "tradeable_safety_rank"], ascending=[False, True])
print("\nHIGH CONVICTION CATALYSTS:")
print(hi.head(15)[cols].round(4).to_string(index=False))
