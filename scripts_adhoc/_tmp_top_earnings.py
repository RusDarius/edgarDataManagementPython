import duckdb
import pandas as pd
from pathlib import Path

p = r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=29/parquet/earnings_priority_consensus_rows.parquet"
symbols = [
    "OMXCOP:ZEAL", "NASDAQ:PGY", "NYSE:LAC", "NASDAQ:CCC", "NYSE:RDDT",
    "NASDAQ:ALAB", "NASDAQ:INTC", "NASDAQ:MU", "NASDAQ:PLTR", "NASDAQ:SOFI",
    "NYSE:INSP", "NASDAQ:DAVE", "NASDAQ:META", "NASDAQ:NVDA", "NASDAQ:SLDE",
    "NYSE:NEXA", "NASDAQ:FSLR", "NYSE:THC", "NASDAQ:PEGA",
]
syms = ",".join(repr(s) for s in symbols)
df = duckdb.sql(
    f"""
    SELECT ticker AS symbol, company, earnings_release_next_date, days_until_earnings,
           consensus_weeks_score, consensus_days_score, bucket
    FROM read_parquet('{p}')
    WHERE run_id='move_prediction_20260713_1333_utc_08199ba2' AND sort_flavour='default'
      AND ticker IN ({syms})
    ORDER BY days_until_earnings
  """
).df()
print(df.to_string(index=False))
