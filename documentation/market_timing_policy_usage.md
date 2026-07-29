# Market Timing Policy: Usage

## Default Run

The normal workflow is one command:

```powershell
$env:PYTHONPATH='src;.'
python src/run_market_timing_policy.py
```

This uses `latest` analysis mode. No premarket, after-open, or midday label is
required.

The runner automatically selects:

1. The latest edge-research parent run and its foundation snapshot.
2. The latest ISO year/week move-prediction DuckDB, then the newest prediction
   `run_id` inside that database.
3. The newest backwards-prediction analysis when one exists.
4. The latest dated all-fields directory, then the newest all-fields `run_id`
   inside that day's DuckDB.
5. Up to the three newest all-fields scans from that same latest day.

Latest all-fields selection uses the `DD_MM_YYYY` directory date, not file
modification time. Latest prediction selection uses ISO year and week before
file modification time. This prevents an older artifact that was copied or
touched later from becoming the default input.

## What the Scan Does

The engine is a **timing and action-policy layer**, not another broad stock
discovery model. It answers:

- Is a name already identified by edge research in a potentially actionable
   timing state?
- Which setup is active now?
- Should the volatile-bounce sleeve enter small, probe, watch, or avoid a
   day-after chase?
- What evidence supports the decision, and what existing conviction/safety
   outputs disagree with it?

It does not claim to predict the exact session of every large move. It uses the
latest available snapshots to recognize setup conditions and authorize limited
risk under explicit rules.

### Candidate Coverage

The default input universe is the union of edge upside-prediction ranks 1-50
and edge screen ranks 1-100. Overlap is stored once. Each DuckDB row includes
`candidate_source` with one of `upside`, `screen`, or `both`, plus the two source
flags. These are coverage limits, not entry rules.

The original 30/50 limits produced 76 data rows (77 CSV lines including the
header). A Jul 21 comparison showed that 50/100 produced 142 names and recovered
26 additional active setups, including AMKR, FORM, NEXA, RMBS, KLAC, QCOM,
NVTS, OUST, IFX, and VRT. Expanding to the complete 842-name source union
created 214 additional actions and was too noisy for a default. The 50/100
boundary is the smallest tested expansion that materially improves coverage
without turning the timing book into another broad scanner.

### Data Enrichment

Every candidate is enriched with:

- Latest all-fields gap, current daily change, 5D/1M performance, ADRP, ATRP,
   industry, and market capitalization.
- Industry breadth: percent of industry names with market cap at least $500M
   that are up at least 3% in the latest scan.
- Up to three same-day scans for change, gap, and breadth deltas.
- Prior daily all-fields snapshots for consecutive-down-day state, prior-day
   change, and rolling drawdown.
- Latest prediction sleeve, conviction, rank, entry readiness, size tier, and
   tape state.
- Nearest weekly backwards-analysis direction and score delta.
- Historical edge validation fields when present in unified edge output.

Prediction, backwards, safety, and calibration fields are evidence. They do
not silently veto or boost the timing score.

### Setup Detection

| Setup | Required state |
|---|---|
| `drawdown_bounce` | Candidate coverage, high ADR, at least 10% drawdown, and at least two consecutive down snapshots |
| `gap_breadth_thrust` | Candidate coverage, gap at least 3%, and industry breadth at least 60% |
| `pullback_swing` | Candidate coverage, high ADR, at least two down snapshots, 5D performance at or below zero, and positive 1M performance |
| `breakout_continuation` | Positive 1M performance plus a qualifying gap, or current move at least 3% with breadth confirmation |

High ADR means ADRP percentile at least 70 or ATRP at least 4. The score uses
TradingView's exported daily `change` for sequence rules. Archived
close-to-close changes are not substituted because they do not always reproduce
that field.

### Timing Score

The auditable score is clamped to 0-100:

| Component | Points |
|---|---:|
| In candidate coverage | +25 |
| Upside-watchlist member | +10 |
| High ADR | +10 |
| Deep drawdown | +10 |
| At least two down snapshots | +15 |
| Gap confirmation | +15 |
| Industry breadth confirmation | +15 |
| At least two setup families agree | +10 |
| Prior day at least +5% without expanding breadth | -35 |

The prior-day rule is also an action override: it returns `AVOID_CHASE` unless
same-day breadth is explicitly expanding. This prevents the timing score from
turning a high-volatility watchlist into a continuation-chasing book.

### Action Policy

| Action | Meaning |
|---|---|
| `ENTER_SMALL` | Active setup and score at least 70; authorize 0.50 risk units, or 0.75 when multiple setups agree |
| `ENTER_PROBE` | Active setup and score at least 55; authorize 0.25 risk units |
| `WATCH` | Candidate remains relevant, but no setup currently authorizes risk |
| `AVOID_CHASE` | Prior-day thrust block; authorize zero risk |
| `NOT_IN_TIMING_UNIVERSE` | Available to the core for audit rows, but normally absent because the loader stores the selected candidate union |

Drawdown-bounce and gap-thrust setups use a two-day maximum hold and 0.75 ATR
invalidation. Pullback and continuation setups use a five-day maximum hold and
1.0 ATR invalidation. Risk units are normalized sleeve units, not account-dollar
position sizes.

## Same-Day Scans

Same-day scan history is optional evidence, not a requirement.

- One scan: run normally from the latest scan. Trend delta fields are blank.
- Two scans: compare the latest scan with the earlier scan.
- Three or more scans: use the newest three and compare latest with oldest of
  those three.

The additional output fields are:

- `same_day_scan_count`
- `same_day_scan_run_ids`
- `same_day_scan_change_delta_pct`
- `same_day_scan_gap_delta_pct`
- `same_day_scan_breadth_delta_pct`

Breadth expansion can relax the existing anti-continuation block after a prior
5% day. Otherwise, same-day deltas are evidence only and do not prevent a run
or create a mandatory entry gate.

To use only the newest scan:

```powershell
python src/run_market_timing_policy.py --same-day-scan-limit 1
```

## Optional Session Labels

Use a checkpoint only when the source scan is known to represent that phase:

```powershell
python src/run_market_timing_policy.py --checkpoint premarket
python src/run_market_timing_policy.py --checkpoint after_open
python src/run_market_timing_policy.py --checkpoint midday
```

`premarket` changes the field selection to premarket gap/change and premarket
industry breadth. `after_open` and `midday` retain the normal current-session
fields while adding an explicit label to the output. The runner does not infer
or guarantee a market phase from UTC time; use `latest` when that distinction
is not important.

## Replay or Override

Every automatically selected source can be replaced explicitly:

```powershell
python src/run_market_timing_policy.py `
  --edge-parent-dir logs/tradingview_analysis/edge_research_tools/runs/<edge_run> `
  --all-fields-database logs/tradingview_analysis/trading_view_all_fields_data/<day>/<database>.duckdb `
  --prediction-database logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=<year>/week=<week>/<database>.duckdb `
  --backwards-database logs/tradingview_analysis/prediction_analysis/duckdb_runs/backwards_prediction_analysis/runs/<run>/backwards_prediction_analysis.duckdb
```

Use overrides for historical replay, debugging, or comparing two source runs.
Omit them for normal latest-data operation.

## Outputs

Each run writes to:

`logs/tradingview_analysis/market_timing_policy/runs/<run_id>/`

Files:

- `action_policy_recommendations.csv`: ranked `ENTER_SMALL`, `ENTER_PROBE`,
  `WATCH`, and `AVOID_CHASE` decisions with normalized risk and invalidation.
- `timing_checkpoint_scores.csv`: raw timing score, setup checks, source values,
  and same-day scan evidence.
- `timing_rule_calibration.csv`: historical rule outcomes for evidence only.
- `catalyst_event_policy_scores.csv`: all prediction-universe catalyst-event gate checks (Book A/Book B/PASS).
- `catalyst_event_action_recommendations.csv`: ranked event-sleeve actions and risk units.
- `market_timing_policy.duckdb`: queryable versions of all five tables.
- `market_timing_policy_manifest.json`: exact input paths and run IDs.

For run clarity, inspect these manifest fields first:

- `edge_parent_run_id`
- `edge_scan_day`
- `prediction_run_id`
- `backwards_analysis_id`
- `all_fields_run_ids_used`
- `same_day_scan_limit_requested`
- `all_fields_latest_created_at_utc`
- `checkpoint`
- `scan_day`

## DuckDB Workflow

The DuckDB is the preferred compressed interface. It avoids loading the wide
CSV files and keeps the action, timing, and calibration tables in one file.
Open the newest run's `market_timing_policy.duckdb` in DBeaver or another
DuckDB client.

Tables:

- `action_policy_recommendations`: ranked operator book.
- `timing_checkpoint_scores`: full signal and check audit.
- `timing_rule_calibration`: historical next-day rule statistics.
- `catalyst_event_policy_scores`: all prediction-universe event-gate score rows.
- `catalyst_event_action_recommendations`: event-sleeve action rows with normalized risk.

### Daily Action Book

```sql
SELECT
   policy_rank, symbol, action, primary_setup, timing_score,
   normalized_risk_units, stop_atr_units, max_hold_days,
   candidate_source, upside_prediction_rank, screen_rank,
   gap_pct, industry_breadth_up_3pct, drawdown_from_high_pct,
   prior_day_pct, existing_conviction_state
FROM action_policy_recommendations
WHERE action IN ('ENTER_SMALL', 'ENTER_PROBE')
ORDER BY policy_rank;
```


### Catalyst Event Sleeve (Book A/Book B)

```sql
SELECT
   event_policy_rank,
   symbol,
   event_action,
   event_book,
   event_policy_score,
   normalized_risk_units,
   earnings_days_until,
   entry_state,
   conviction_rank,
   conviction_score,
   upside_prediction_rank,
   expected_move_proxy_pct,
   drawdown_from_high_pct,
   perf_1m
FROM catalyst_event_action_recommendations
WHERE event_action IN ('BOOK_A_HOLD_THROUGH', 'BOOK_A_DERISK_INTO_PRINT', 'BOOK_B_SPEC_EARN')
ORDER BY event_policy_rank;
```

### Why Names Were Included

```sql
SELECT
   candidate_source,
   COUNT(*) AS names,
   COUNT(*) FILTER (WHERE action IN ('ENTER_SMALL', 'ENTER_PROBE')) AS actionable,
   COUNT(*) FILTER (WHERE action = 'WATCH') AS watching,
   COUNT(*) FILTER (WHERE action = 'AVOID_CHASE') AS chase_blocked
FROM action_policy_recommendations
GROUP BY candidate_source
ORDER BY names DESC;
```

### Coverage Boundaries

```sql
SELECT
   COUNT(*) AS names,
   MIN(upside_prediction_rank) AS best_upside_rank,
   MAX(upside_prediction_rank) AS worst_available_upside_rank,
   MIN(screen_rank) AS best_screen_rank,
   MAX(screen_rank) AS worst_available_screen_rank
FROM action_policy_recommendations;
```

Rows selected by one source can still carry a lower rank from the other source.
Use `candidate_source` and the source flags to determine why a row was included.

### Active Setup Summary

```sql
SELECT
   primary_setup,
   action,
   COUNT(*) AS names,
   ROUND(AVG(timing_score), 2) AS avg_timing_score
FROM action_policy_recommendations
GROUP BY primary_setup, action
ORDER BY primary_setup, action;
```

### Drawdown-Bounce Candidates

```sql
SELECT
   policy_rank, symbol, action, timing_score,
   consecutive_down_days, drawdown_from_high_pct,
   adrp_percentile, atrp, perf_5d, perf_1m
FROM action_policy_recommendations
WHERE primary_setup = 'drawdown_bounce'
ORDER BY timing_score DESC, policy_rank;
```

### Gap and Breadth Candidates

```sql
SELECT
   p.policy_rank, p.symbol, p.action, p.primary_setup, p.timing_score,
   p.gap_pct, p.industry_breadth_up_3pct,
   t.gap_confirmed, t.breadth_confirmed,
   p.same_day_scan_breadth_delta_pct
FROM action_policy_recommendations p
JOIN timing_checkpoint_scores t USING (symbol, checkpoint)
WHERE t.gap_confirmed = 1 OR t.breadth_confirmed = 1
ORDER BY p.timing_score DESC, p.gap_pct DESC;
```

### Existing-Book Contradictions

```sql
SELECT
   policy_rank, symbol, action, primary_setup, timing_score,
   existing_safety_state, existing_conviction_state,
   conviction_score, conviction_rank
FROM action_policy_recommendations
WHERE action IN ('ENTER_SMALL', 'ENTER_PROBE')
  AND (
     LOWER(existing_safety_state) = 'speculative'
     OR LOWER(existing_conviction_state) LIKE '%reject%'
     OR LOWER(existing_conviction_state) LIKE '%tape=false%'
  )
ORDER BY policy_rank;
```

### Anti-Chase Book

```sql
SELECT
   policy_rank, symbol, prior_day_pct, timing_score,
   same_day_scan_count, same_day_scan_breadth_delta_pct, matched_setups
FROM action_policy_recommendations
WHERE action = 'AVOID_CHASE'
ORDER BY prior_day_pct DESC;
```

### Same-Day Scan Changes

```sql
SELECT
   symbol, action, same_day_scan_count,
   same_day_scan_change_delta_pct,
   same_day_scan_gap_delta_pct,
   same_day_scan_breadth_delta_pct
FROM action_policy_recommendations
WHERE same_day_scan_count > 1
ORDER BY ABS(TRY_CAST(same_day_scan_change_delta_pct AS DOUBLE)) DESC NULLS LAST;
```

### Focus Symbols

```sql
SELECT *
FROM timing_checkpoint_scores
WHERE split_part(symbol, ':', 2) IN ('ALAB', 'NBIS', 'MU', 'WDC', 'SNDK')
ORDER BY timing_score DESC;
```

### Calibration Evidence

```sql
SELECT
   rule_name, sample_count, next_day_mean_pct,
   next_day_median_pct, next_day_win_rate, scoring_role
FROM timing_rule_calibration
ORDER BY sample_count DESC;
```

Calibration is descriptive and universe-dependent. It is not blended into the
live score.

## Recommended Routine

1. Run the default command after refreshing whichever upstream data is
   available.
2. Read `action_policy_recommendations.csv` from the newest output directory.
3. Check the manifest when an input looks stale or a recommendation is
   surprising.
4. Run again later only when a materially newer all-fields or prediction scan
   exists. Multiple scheduled scans are useful but not mandatory.
