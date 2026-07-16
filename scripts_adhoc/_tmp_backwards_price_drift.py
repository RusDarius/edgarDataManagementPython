import duckdb

con = duckdb.connect(
    r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/prediction_analysis/duckdb_runs/backwards_prediction_analysis/runs/backwards_prediction_analysis_20260712_2003_utc_ec9aadd2/backwards_prediction_analysis.duckdb",
    read_only=True,
)

syms = [
    "NYSE:AMP", "NASDAQ:CALM", "NYSE:RJF", "NASDAQ:KARO", "NYSE:STT", "NYSE:UNH",
    "NASDAQ:GOOG", "NASDAQ:TXN", "NYSE:ELV", "NYSE:PXED", "OMXSTO:HACK", "NASDAQ:ATRC",
    "NASDAQ:NTCT", "NYSE:BAC", "NYSE:JPM", "NYSE:BNY", "NASDAQ:KARO", "OMXHEX:EVLI",
    "NYSE:SCHW", "NASDAQ:INCY", "NYSE:INSP", "NASDAQ:ABUS",
]
sym_sql = ",".join(f"'{s}'" for s in syms)

rows = con.execute(
    f"""
    WITH recent AS (
      SELECT anchor_name
      FROM backwards_analysis_anchors
      WHERE backwards_analysis_id='backwards_prediction_analysis_20260712_2003_utc_ec9aadd2'
      ORDER BY offset_days_from_current
      LIMIT 2
    )
    SELECT symbol, company, profile_name, anchor_name,
      round(close_delta_pct,2) px_pct_since_anchor,
      round(current_close,2) px_now,
      round(anchor_close,2) px_anchor,
      current_direction
    FROM backwards_profile_horizon_deltas
    WHERE backwards_analysis_id='backwards_prediction_analysis_20260712_2003_utc_ec9aadd2'
      AND symbol IN ({sym_sql})
      AND horizon_name='weeks'
      AND profile_name='breakout_long_v1'
      AND anchor_name IN (SELECT anchor_name FROM recent)
    ORDER BY symbol, anchor_name
    """
).fetchdf()

# pivot-ish print
for sym in sorted(rows["symbol"].unique()):
    sub = rows[rows["symbol"] == sym]
    company = sub.iloc[0]["company"]
    parts = []
    for _, r in sub.iterrows():
        parts.append(f"{r['anchor_name']}: {r['px_pct_since_anchor']:+.1f}% (dir={r['current_direction']})")
    print(f"{sym} | {company[:35]:35s} | " + " | ".join(parts))
