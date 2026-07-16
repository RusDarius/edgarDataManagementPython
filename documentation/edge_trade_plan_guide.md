# Edge Trade Plan Guide

## Purpose

`edge_trade_plan` is an additive decision layer on top of the unified edge
highlights store. It does not alter upstream edge, upside, valuation, or safety
scores. It answers two narrower questions:

1. Does the latest all-fields snapshot support entering a currently ranked name?
2. Did simple point-in-time timing and exit rules improve outcomes for the same
   candidate cohort historically?

The integrated edge workflow writes the folder automatically under:

```text
<parent run>/aggregate/edge_trade_plan/
```

## Outputs

| Artifact | Purpose |
|---|---|
| `edge_trade_plan_ranked.csv` | Full unified rows augmented with current checks, state, and trade-plan rank |
| `edge_trade_plan_top<N>.csv` | Concise `ENTER_STARTER` and `ARMED` review list |
| `edge_trade_plan_backtest_summary.csv` | Comparison of baseline and timing/exit variants |
| `edge_trade_plan_backtest_trades.csv` | Entry/exit detail for every non-overlapping simulated trade |
| `edge_trade_plan.duckdb` | Queryable copies of all three datasets |
| `edge_trade_plan_methodology.md` | Run-specific methodology, results, limits, and state counts |
| `edge_trade_plan_manifest.json` | Input paths, variants, counts, and provenance |

DuckDB tables:

- `edge_trade_plan`
- `edge_trade_plan_backtest_summary`
- `edge_trade_plan_backtest_trades`

## Current Decision States

### `ENTER_STARTER`

At least one timing variant matches, every timing check passes, at least three
quality checks pass, historical validation is `supported` or `strong`, and the
safety bucket is `balanced` or `safer`.

This is permission to investigate a starter position, not an instruction to buy.
Earnings, liquidity, spread, portfolio concentration, and current news remain
execution-time checks.

### `ARMED`

A timing variant matches, but the evidence or safety requirements for
`ENTER_STARTER` are incomplete. Do not silently treat `ARMED` as an entry.

### `WATCH`

At least three timing checks pass, but no complete variant matches. This state is
useful for names approaching confirmation or a controlled pullback region.

### `REJECT`

The timing evidence is weak, or a speculative name has no matching timing setup.

## Quality Checks

| Field | Pass condition |
|---|---|
| `check_evidence_depth` | At least five historical setup occurrences |
| `check_historical_support` | Historical bucket is `supported` or `strong` |
| `check_lane_lower_bound_positive` | Five-day lane bootstrap CI lower bound is above zero |
| `check_safety_not_speculative` | Safety bucket is `balanced` or `safer` |
| `check_valuation_not_negative` | Headline forward valuation upside is non-negative |

## Timing Checks

| Field | Pass condition |
|---|---|
| `check_timing_data_complete` | 5D/1M performance, ATRP, relative volume, and recommendation are present |
| `check_trend_intact` | `Perf.1M >= -2%` |
| `check_extension_controlled` | `Perf.5D / ATRP` is between -1 and +2 |
| `check_participation_tradeable` | Relative volume is between 0.6 and 3.0 |
| `check_recommendation_non_negative` | `Recommend.All >= 0` |

The latest all-fields database supplies current values. Historical timing values
come from the point-in-time symbol-day feature snapshot.

## Backtest Variants

### `baseline_5d`

No timing filter. Enter the cohort when eligible and hold five trading
observations. This is the benchmark the other variants must improve.

### `momentum_confirmed_5d`

- `Perf.5D > 0`
- `Perf.1M > 0`
- relative volume between 1.0 and 3.0
- `Recommend.All >= 0`
- `Perf.5D / ATRP <= 2`
- maximum hold: 5 observations
- target: +10% at daily close
- stop: -7% at daily close

### `controlled_pullback_10d`

- `Perf.1M > 0`
- `Perf.5D / ATRP` between -1.0 and +0.75
- relative volume between 0.6 and 1.8
- `Recommend.All >= 0`
- maximum hold: 10 observations
- target: +8% at daily close
- stop: -5% at daily close

This is a pullback-zone proxy, not a true reclaim signal. A later version should
add prior-day price/average relationships before using the word "reclaim".

### `risk_controlled_10d`

- `ATRP <= 6`
- `Perf.1M >= -2%`
- `Perf.5D / ATRP` between -0.5 and +1.5
- relative volume between 0.7 and 2.0
- `Recommend.All >= 0`
- maximum hold: 10 observations
- target: +6% at daily close
- stop: -4% at daily close

## Backtest Interpretation

The simulation uses point-in-time entry fields and subsequent daily closes.
Trades do not overlap for the same symbol and variant. This reduces repeated
signals from being counted as independent simultaneous trades.

The first implementation is deliberately a **current-cohort timing diagnostic**:
it evaluates historical timing for symbols in today's unified shortlist. It does
not reconstruct historical unified ranks. Consequently:

- use it to reject weak timing-rule ideas quickly;
- do not use it to claim full out-of-sample strategy performance;
- do not optimize thresholds repeatedly against this one cohort;
- require improvement over baseline in median return and MAE, not average return
  alone;
- inspect trade count, symbol breadth, and stop/target rates;
- treat daily-close stops as slower than executable intraday stops, especially
  through gaps.

The simulation excludes bid/ask spread, commissions, slippage, taxes, FX,
borrow constraints, and news/earnings gaps.

## Latest July 10 Result

For the attached July 10, 2026 run, the unfiltered five-day baseline had a
positive median return. All three initial timing variants produced lower median
returns; the two tighter 10-day stop variants produced materially negative
medians. Therefore these variants remain research checks and are not validated
automatic entry rules.

The strict current-state result contained no `ENTER_STARTER` rows. This is a
useful output: the screen did not weaken evidence and safety requirements merely
to populate a recommendation list.

## Starter Queries

Current actionable review list:

```sql
SELECT trade_plan_rank,
       entry_state,
       symbol,
       matched_entry_variants,
       trade_plan_score,
       historical_validation_bucket,
       safety_bucket,
       extension_atr_units,
       relative_volume_10d_calc,
       forward_valuation_upside_pct
FROM edge_trade_plan
WHERE entry_state IN ('ENTER_STARTER', 'ARMED')
ORDER BY trade_plan_rank;
```

Only fully gated starter candidates:

```sql
SELECT *
FROM edge_trade_plan
WHERE entry_state = 'ENTER_STARTER'
ORDER BY trade_plan_rank;
```

Compare timing variants:

```sql
SELECT variant,
       trade_count,
       symbol_count,
       win_rate,
       avg_return_pct,
       median_return_pct,
       median_mfe_pct,
       median_mae_pct,
       target_exit_rate,
       stop_exit_rate
FROM edge_trade_plan_backtest_summary
ORDER BY variant;
```

Inspect large stop slippage at daily close:

```sql
SELECT variant,
       symbol,
       entry_date,
       exit_date,
       return_pct,
       mae_pct
FROM edge_trade_plan_backtest_trades
WHERE exit_reason = 'stop_exit'
ORDER BY return_pct
LIMIT 100;
```

## Historical Candidate Backtest

Point-in-time historical candidate reconstruction now lives in the separate
`src/run_edge_historical_backtest.py` runner. It selects either two anchors per
week or every Nth available trading observation, persists config-hashed candidate
snapshots in DuckDB, refreshes outcomes incrementally, and tests fixed 3/5/10/20
observation holds plus fresh-entry and rank-deterioration exits.

The default output is:

```text
logs/tradingview_analysis/edge_research_tools/historical_backtests/
```

Historical setup statistics use only outcomes whose complete forward window was
observable by the anchor. Current-anchor forward labels are stored as outcomes
and never enter the historical score. Safety and valuation overlays remain
excluded because point-in-time historical copies are not guaranteed.

## Daily Integrated Progression

The integrated `run_edge_research_tools.py` workflow now refreshes historical
progression after unified highlights and before `edge_trade_plan`. The ordering
is deliberate:

1. build the current unified shortlist;
2. append the latest trading-date anchor to the shared dense historical store;
3. create an augmented unified CSV/DuckDB with progression fields;
4. pass that augmented unified CSV into `edge_trade_plan`.

The shared store uses every available trading date, the same preferred-market
scope as the daily edge run, a stable top-2000 candidate depth, and a 0.45
minimum composite threshold. With 70 dates this is roughly 140,000 maximum
candidate rows. Existing anchors are skipped, while outcomes are refreshed when
new forward observations mature.

Per-run outputs live under:

```text
<aggregate run>/historical_edge_progression/
```

| Artifact | Purpose |
|---|---|
| `edge_unified_with_history.csv` | Current unified shortlist plus historical progression fields |
| `edge_unified_with_history.duckdb` | Sidecar with `symbol_unified_highlights`, `historical_edge_progression`, and run metadata |
| `edge_historical_progression.csv` | Compact progression-only daily consultation view |
| `edge_historical_progression_report.md` | State counts, anchor coverage, parameters, and shared-store path |
| `historical_store/` | Small run-specific backtest summaries; full candidate history remains in shared DuckDB |

The base unified DuckDB is not modified. This keeps daily runs reliable when the
base file is open in DBeaver. Parent manifests and printed output point to the
augmented sidecar, and the trade plan consumes the augmented CSV automatically.
Use the per-run sidecar for daily DBeaver consultation. Close the shared
`daily_historical_edge_progression.duckdb` before launching a scan because
DuckDB requires an exclusive writer lock on Windows.

### Unified Progression Fields

- `historical_edge_current_rank`, `historical_edge_previous_rank`
- `historical_edge_rank_improvement_1d`, `historical_edge_rank_improvement_5d`
- `historical_edge_current_score`, `historical_edge_score_change_1d`
- `historical_edge_best_rank`, `historical_edge_median_rank`
- `historical_edge_anchors_seen`, `historical_edge_total_anchors`
- `historical_edge_inclusion_rate`, first/last-seen dates
- mature five-day sample count, median return, win rate, and setup rate
- `historical_edge_rank_trajectory_last_10`
- `historical_edge_fresh_top20_entry_flag`, `historical_edge_rank_exit_flag`
- `historical_edge_daily_state`
- `historical_edge_config_hash`, `historical_edge_database_path`

Daily states are `ENTER_FRESH` (top 20 after absence/below 100), `HOLD` (top
100), `WATCH` (101-200), `EXIT` (present before but absent from the latest
anchor), and `OUTSIDE` (below the action bands or no stored rank).

Daily action query:

```sql
SELECT unified_edge_highlight_rank,
     symbol,
     historical_edge_daily_state,
     historical_edge_current_rank,
     historical_edge_previous_rank,
     historical_edge_rank_improvement_1d,
     historical_edge_current_score,
     historical_edge_hist_median_5d_pct,
     historical_edge_hist_win_rate_5d,
     historical_edge_rank_trajectory_last_10
FROM symbol_unified_highlights
WHERE historical_edge_daily_state IN ('ENTER_FRESH', 'HOLD', 'WATCH', 'EXIT')
ORDER BY CASE historical_edge_daily_state
       WHEN 'ENTER_FRESH' THEN 1
       WHEN 'HOLD' THEN 2
       WHEN 'WATCH' THEN 3
       ELSE 4
     END,
     historical_edge_current_rank NULLS LAST;
```

The shared config hash changes when market scope, spacing, score version,
candidate depth, or thresholds change. Histories with different parameters
coexist instead of being blended.

The historical state and current trade-plan state remain separate by design. A
name can be a fresh historical rank entry but fail current timing, safety, or
valuation gates. Consult both in the trade-plan database:

```sql
SELECT trade_plan_rank,
     symbol,
     historical_edge_daily_state,
     historical_edge_current_rank,
     historical_edge_previous_rank,
     entry_state,
     matched_entry_variants,
     trade_plan_score
FROM edge_trade_plan
WHERE historical_edge_daily_state IN ('ENTER_FRESH', 'HOLD', 'WATCH', 'EXIT')
ORDER BY CASE historical_edge_daily_state
       WHEN 'ENTER_FRESH' THEN 1
       WHEN 'HOLD' THEN 2
       WHEN 'WATCH' THEN 3
       ELSE 4
     END,
     trade_plan_rank;
```