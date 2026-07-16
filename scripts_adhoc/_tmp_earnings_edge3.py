import duckdb
import pandas as pd
from pathlib import Path

p = r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=29/parquet/earnings_priority_consensus_rows.parquet"
base = Path(
    r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis"
    r"/edge_research_tools/runs/edge_latest_500m_full_parent_20260713_1503_utc_ce6f36f2/aggregate"
)
u = pd.read_csv(base / "edge_unified_highlights/edge_unified_highlights.csv")
u["bare"] = u["symbol"].str.split(":", n=1).str[-1]

meta = duckdb.sql(
    f"SELECT run_id, sort_flavour, count(*) AS c FROM read_parquet('{p}') GROUP BY 1,2 ORDER BY c DESC"
).df()
print("META:\n", meta.to_string())

ep = duckdb.sql(
    f"""
    SELECT ticker, company, earnings_release_next_date, days_until_earnings,
           consensus_weeks_score, consensus_days_score, bucket, industry
    FROM read_parquet('{p}')
    WHERE run_id = 'move_prediction_20260713_1333_utc_08199ba2'
      AND sort_flavour = 'default'
      AND days_until_earnings BETWEEN 0 AND 14
      AND consensus_weeks_score >= 0.30
    ORDER BY consensus_weeks_score DESC
  """
).df()
print("\nBullish earnings next 2 weeks:", len(ep))
m = ep.merge(u, left_on="ticker", right_on="bare", how="left")
on = m[m["unified_edge_highlight_rank"].notna()].sort_values("consensus_weeks_score", ascending=False)
print("overlap count", len(on))
cols = [
    "symbol", "company", "earnings_release_next_date", "days_until_earnings",
    "consensus_weeks_score", "unified_edge_highlight_rank", "tradeable_safety_rank",
    "upside_prediction_rank", "safety_companion_score", "safety_bucket",
    "forward_valuation_upside_pct",
]
print("\nON EDGE RADAR:\n", on.head(20)[cols].round(4).to_string(index=False))

safe = m[
    (m["consensus_weeks_score"] >= 0.35)
    & (m["safety_companion_score"].fillna(0) >= 0.55)
].sort_values("consensus_weeks_score", ascending=False)
print("\nSAFER BULLISH (all, top 15):\n", safe.head(15)[
    ["ticker", "company", "earnings_release_next_date", "consensus_weeks_score",
     "safety_companion_score", "safety_bucket", "unified_edge_highlight_rank"]
].round(4).to_string(index=False))

# cross names from regime context
watch = ["INSP","DAVE","META","RDDT","NVDA","PLTR","MU","SLDE","ABUS","NUTX","WDFC","CNMD","FBRX","ZEAL","PGR","ERIC_A","AKSO","FAST","PXED"]
w = duckdb.sql(
    f"""
    SELECT ticker, company, earnings_release_next_date, days_until_earnings,
           consensus_weeks_score, consensus_days_score, bucket
    FROM read_parquet('{p}')
    WHERE run_id = 'move_prediction_20260713_1333_utc_08199ba2'
      AND sort_flavour = 'default'
      AND ticker IN ({','.join(repr(x) for x in watch)})
    ORDER BY days_until_earnings
  """
).df()
print("\nSAMPLE TICKERS:\n", duckdb.sql(
    f"SELECT ticker, company, consensus_weeks_score, days_until_earnings FROM read_parquet('{p}') "
    f"WHERE run_id='move_prediction_20260713_1333_utc_08199ba2' AND sort_flavour='default' "
    f"ORDER BY consensus_weeks_score DESC LIMIT 10"
).df().to_string(index=False))

# try EXCHANGE: prefix merge
u["sym2"] = u["symbol"]
m2 = ep.merge(u, left_on="ticker", right_on="sym2", how="inner")
print("prefix merge", len(m2))
if len(m2):
    print(m2[["symbol","company","consensus_weeks_score","unified_edge_highlight_rank"]].head(10).to_string())
