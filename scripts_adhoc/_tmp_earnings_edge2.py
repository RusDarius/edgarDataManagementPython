import duckdb
import pandas as pd
from pathlib import Path

base = Path(
    r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis"
    r"/edge_research_tools/runs/edge_latest_500m_full_parent_20260713_1503_utc_ce6f36f2/aggregate"
)
u = pd.read_csv(base / "edge_unified_highlights/edge_unified_highlights.csv")
p = r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=29/parquet/earnings_priority_consensus_rows.parquet"

ep = duckdb.sql(
    f"""
    SELECT ticker, company, earnings_release_next_date, days_until_earnings,
           consensus_weeks_score, consensus_days_score, bucket, industry
    FROM read_parquet('{p}')
    WHERE sort_flavour = 'score'
      AND run_id LIKE '%08199ba2%'
    """
).df()
print("ep rows", len(ep), "sample ticker", ep["ticker"].head(10).tolist())

# try symbol suffix match
u["bare"] = u["symbol"].str.split(":", n=1).str[-1]
merged = ep.merge(u, left_on="ticker", right_on="bare", how="inner")
print("merged", len(merged))
imminent = merged[merged["days_until_earnings"] <= 7].sort_values("consensus_weeks_score", ascending=False)
print("\nImminent earnings on unified shortlist (top 20 by weeks score):")
cols = [
    "symbol", "company_x", "earnings_release_next_date", "days_until_earnings",
    "consensus_weeks_score", "unified_edge_highlight_rank", "tradeable_safety_rank",
    "upside_prediction_rank", "safety_companion_score", "safety_bucket",
    "forward_valuation_upside_pct",
]
print(imminent.head(20)[cols].round(4).to_string(index=False))

bull = imminent[imminent["consensus_weeks_score"] >= 0.20].sort_values(
    ["consensus_weeks_score", "tradeable_safety_rank"], ascending=[False, True]
)
print("\nBullish imminent (weeks>=0.20):")
print(bull.head(15)[cols].round(4).to_string(index=False))

# also check names from regime context that user cared about
watch = [
    "INSP", "FBRX", "DAVE", "MNPR", "CNMD", "META", "ANET", "RDDT", "WDFC",
    "NUTX", "SLDE", "ABUS", "EVER", "NVDA", "PLTR", "MU", "INCY", "ZEAL",
]
w = ep[ep["ticker"].isin(watch)].sort_values("days_until_earnings")
print("\nWatchlist earnings:")
print(w.to_string(index=False))
