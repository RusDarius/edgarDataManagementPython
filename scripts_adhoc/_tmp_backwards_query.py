import duckdb

con = duckdb.connect(
    r"d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/prediction_analysis/duckdb_runs/backwards_prediction_analysis/runs/backwards_prediction_analysis_20260712_2003_utc_ec9aadd2/backwards_prediction_analysis.duckdb",
    read_only=True,
)
anchors = con.execute(
    """
    SELECT anchor_name, offset_days_from_current
    FROM backwards_analysis_anchors
    WHERE backwards_analysis_id='backwards_prediction_analysis_20260712_2003_utc_ec9aadd2'
    ORDER BY offset_days_from_current
    LIMIT 5
    """
).fetchall()
print("RECENT ANCHORS:", anchors)

print("AMP count", con.execute("SELECT count(1) FROM backwards_profile_horizon_deltas WHERE symbol='NYSE:AMP'").fetchone())
print(con.execute("SELECT symbol, profile_name, anchor_name, horizon_name, score_delta, close_delta_pct FROM backwards_profile_horizon_deltas WHERE symbol='NYSE:AMP' AND horizon_name='weeks' ORDER BY anchor_name LIMIT 8").fetchall())
