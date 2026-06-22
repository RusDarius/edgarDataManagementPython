"""Temporary: compare two move-prediction runs."""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

DB = Path(
    r"d:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis"
    r"\prediction_analysis\duckdb_runs\iso_year=2026\week=26\move_prediction_2026_W26.duckdb"
)
R1 = "move_prediction_20260622_1545_utc_f58d4cb3"
R2 = "move_prediction_20260622_1557_utc_60f4c5bd"

con = duckdb.connect(str(DB), read_only=True)
cols = [r[0] for r in con.execute("DESCRIBE consensus_rows").fetchall()]
print("COLUMNS:", cols)
print("RUN COUNTS:", con.execute("SELECT run_id, COUNT(1) FROM consensus_rows GROUP BY 1").fetchall())

# consensus weeks scores delta
scan_cols = [r[0] for r in con.execute("DESCRIBE raw_scan_rows").fetchall()]
print("SCAN COLS sample:", [c for c in scan_cols if 'ticker' in c.lower() or c in ('symbol','industry','run_id')])

rows = con.execute(
    """
    WITH a AS (
        SELECT c.ticker, r.industry, c.consensus_weeks_score, c.consensus_weeks_risk_adjusted_score,
               c.manager_action_signal, TRY_CAST(r."Perf.W" AS DOUBLE) perf_w,
               TRY_CAST(r."Perf.1M" AS DOUBLE) perf_1m
        FROM consensus_rows c
        JOIN raw_scan_rows r ON c.run_id = r.run_id
            AND c.ticker = regexp_extract(r.symbol, '[^:]+$', 1)
        WHERE c.run_id = ?
    ),
    b AS (
        SELECT ticker, consensus_weeks_score AS weeks_score_b,
               consensus_weeks_risk_adjusted_score AS weeks_radj_b,
               manager_action_signal AS action_b
        FROM consensus_rows
        WHERE run_id = ?
    )
    SELECT
        a.ticker,
        a.industry,
        ROUND(a.consensus_weeks_score, 3),
        ROUND(b.weeks_score_b, 3),
        ROUND(b.weeks_score_b - a.consensus_weeks_score, 3),
        ROUND(a.consensus_weeks_risk_adjusted_score, 3),
        ROUND(b.weeks_radj_b, 3),
        a.manager_action_signal,
        b.action_b,
        ROUND(a.perf_w, 2),
        ROUND(a.perf_1m, 2)
    FROM a
    JOIN b USING (ticker)
    ORDER BY 5 DESC
    """,
    [R1, R2],
).fetchall()
headers = [
    "ticker", "industry", "score_r1", "score_r2", "score_delta",
    "radj_r1", "radj_r2", "action_r1", "action_r2", "perf_w", "perf_1m",
]

def print_table(title, data, n=25):
    print(f"\n=== {title} ===")
    print("\t".join(headers))
    for row in data[:n]:
        print("\t".join(str(x) for x in row))

deltas = con.execute(
    """
    SELECT ticker,
           ROUND(c1.consensus_weeks_score,4) s1,
           ROUND(c2.consensus_weeks_score,4) s2,
           ROUND(c2.consensus_weeks_score - c1.consensus_weeks_score,4) d,
           c2.manager_action_signal
    FROM consensus_rows c1
    JOIN consensus_rows c2 USING (ticker)
    WHERE c1.run_id=? AND c2.run_id=?
    ORDER BY ABS(c2.consensus_weeks_score - c1.consensus_weeks_score) DESC
    LIMIT 30
    """,
    [R1, R2],
).fetchall()
print("\n=== LARGEST |WEEKS SCORE| MOVES ===")
for row in deltas:
    print(row)

ph = con.execute(
    """
    SELECT run_id, ticker, consensus_weeks_score, consensus_days_score, manager_action_signal
    FROM consensus_rows WHERE ticker IN ('PHIL','NDX1','FDXF','DAVE','AKTS','AUPH','ABUS','CRDO','SNDK','MU','YB','ZEAL','DAR')
    AND run_id IN (?, ?) ORDER BY ticker, run_id
    """,
    [R1, R2],
).fetchall()
print("\n=== KEY TICKERS BOTH RUNS ===")
for row in ph:
    print(row)

sorted_gain = sorted(rows, key=lambda r: r[4], reverse=True)
sorted_loss = sorted(rows, key=lambda r: r[4])
if rows:
    print_table("TOP SCORE GAINERS (12 min window)", sorted_gain)
    print_table("TOP SCORE LOSERS", sorted_loss, 20)
else:
    print("\n(join rows empty — using consensus-only deltas above)")

# stable leaders both runs top 30 weeks
top_r1 = {
    r[0]
    for r in con.execute(
        "SELECT ticker FROM consensus_rows WHERE run_id=? ORDER BY consensus_weeks_score DESC LIMIT 40",
        [R1],
    ).fetchall()
}
top_r2 = {
    r[0]
    for r in con.execute(
        "SELECT ticker FROM consensus_rows WHERE run_id=? ORDER BY consensus_weeks_score DESC LIMIT 40",
        [R2],
    ).fetchall()
}
print("\n=== STABLE TOP (in both top-40 weeks) ===", sorted(top_r1 & top_r2))

new_top = top_r2 - top_r1
dropped = top_r1 - top_r2
print("=== NEW IN TOP-40 (run2 only) ===", sorted(new_top))
print("=== DROPPED FROM TOP-40 ===", sorted(dropped))

# regime context if available
try:
    reg = con.execute(
        """
        SELECT run_id, ticker, regime_fit_score, regime_fit_tier, warning_flag_count
        FROM regime_context_scores
        WHERE run_id IN (?, ?) AND ticker IN (
            SELECT ticker FROM consensus_rows WHERE run_id=? ORDER BY consensus_weeks_score DESC LIMIT 15
        )
        ORDER BY ticker, run_id
        """,
        [R1, R2, R2],
    ).fetchall()
    print("\n=== REGIME TOP LEADERS ===")
    for row in reg:
        print(row)
except Exception as exc:
    print("regime query failed:", exc)

action_rows = con.execute(
    """
    WITH a AS (
        SELECT c.ticker, c.consensus_weeks_score, c.manager_action_signal
        FROM consensus_rows c WHERE c.run_id=?
    ),
    b AS (
        SELECT c.ticker, r.industry, c.consensus_weeks_score, c.consensus_weeks_risk_adjusted_score,
               c.manager_action_signal,
               TRY_CAST(r."Perf.W" AS DOUBLE) perf_w,
               TRY_CAST(r."Perf.1M" AS DOUBLE) perf_1m
        FROM consensus_rows c
        JOIN raw_scan_rows r ON c.run_id = r.run_id
            AND c.ticker = regexp_extract(r.symbol, '[^:]+$', 1)
        WHERE c.run_id=?
    )
    SELECT b.ticker, b.industry,
           ROUND(a.consensus_weeks_score,3), ROUND(b.consensus_weeks_score,3),
           ROUND(b.consensus_weeks_score-a.consensus_weeks_score,3),
           ROUND(b.consensus_weeks_risk_adjusted_score,3),
           b.manager_action_signal,
           ROUND(b.perf_w,2), ROUND(b.perf_1m,2)
    FROM a JOIN b USING(ticker)
    WHERE b.consensus_weeks_score >= 0.75
      AND b.manager_action_signal IN ('add_long_breakout','accumulate_value_catalyst')
      AND b.consensus_weeks_score - a.consensus_weeks_score >= 0.02
    ORDER BY 5 DESC
    LIMIT 20
    """,
    [R1, R2],
).fetchall()
print("\n=== RISING + ACTIONABLE (run2) ===")
for row in action_rows:
    print(row)

# delete temp json write
