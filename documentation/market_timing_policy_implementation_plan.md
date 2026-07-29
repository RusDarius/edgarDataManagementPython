# Market Timing and Policy Engine: Implementation Plan

## Objective

Build a separate decision process for **WHEN** to act and **HOW** to express risk.
The existing edge and prediction suites remain the primary source for **WHO** is
interesting. The timing engine must not reinterpret an upside rank as an entry
signal, and existing safety or conviction rejections remain visible context
rather than automatic vetoes for this small speculative sleeve.

The default operating model is one latest-data run with no required session
label. Three optional snapshot labels remain available when the source data is
known to represent those phases:

1. `premarket`
2. `after_open` (target: 30-60 minutes after the open)
3. `midday`

There is no continuous intraday monitor and no mandatory three-run cadence.
See `documentation/market_timing_policy_usage.md` for the operator workflow.

## Implemented First Slice

The first executable slice consists of:

- `src/edge_research_tools/market_timing_policy.py`: pure setup scoring and
  action policy.
- `src/run_market_timing_policy.py`: latest-by-default artifact discovery,
  source integration, daily path derivation, output persistence, and CLI.
- `src/tests/test_market_timing_policy.py`: focused policy and methodology
  regressions.

Run it with:

```powershell
$env:PYTHONPATH='src;.'
python src/run_market_timing_policy.py
```

The default uses the latest edge parent, prediction run, backwards analysis,
and all-fields day. Up to three scans from that same latest all-fields day add
optional trend evidence. `--checkpoint premarket`, `--checkpoint after_open`,
and `--checkpoint midday` are explicit overrides, not required inputs.

Explicit `--edge-parent-dir`, `--all-fields-database`,
`--prediction-database`, and `--backwards-database` arguments override latest
discovery for replay and audit.

Each run writes under
`logs/tradingview_analysis/market_timing_policy/runs/<run_id>/`:

- `timing_checkpoint_scores.csv`
- `action_policy_recommendations.csv`
- `timing_rule_calibration.csv`
- `market_timing_policy.duckdb`
- `market_timing_policy_manifest.json`

The DuckDB contains equivalent `timing_checkpoint_scores`,
`action_policy_recommendations`, and `timing_rule_calibration` tables.

## Current Data Flow

| Need | Latest-by-default source | Current use |
|---|---|---|
| WHO and upside rank | Edge `edge_upside_prediction_ranked.csv` | Upside top-50 watchlist |
| Coverage glue | Edge `edge_screen_ranked.csv` | Adds screen top-100 names such as WDC, KLAC, and QCOM |
| Historical edge context | Unified edge highlights | Evidence fields only |
| Gap/current move/ADRP/ATRP | Latest dated all-fields DuckDB and newest run ID | Current timing features |
| Same-day trend | Up to three newest runs from the latest all-fields day | Optional change/gap/breadth deltas |
| Industry breadth | Latest all-fields run | Percent of industry names up at least 3%, market cap at least $500M |
| Daily path | Prior dated all-fields DuckDBs | Exported `change` for sequence rules; `close` for drawdown |
| Conviction and existing veto state | Latest move-prediction DuckDB | Visible context only |
| Score evolution | Latest backwards-analysis DuckDB | Nearest weekly-anchor evidence only |
| Rule outcomes | Dated all-fields history | Separate next-day calibration table |

Using TradingView's exported daily `change` is intentional. Close-to-close
changes in archived snapshots do not always reproduce that field and caused
false prior-day and two-down-day signals during implementation.

## Setup and Policy Contract

Version 1 evaluates four setup families:

| Setup | Minimum intent |
|---|---|
| Drawdown bounce | WHO candidate, high ADR, at least 10% below recent high, two down snapshots |
| Gap and breadth thrust | WHO candidate, gap at least 3%, industry breadth at least 60% up more than 3% |
| Pullback swing | WHO candidate, high ADR, two down snapshots, non-positive 5D return, positive 1M trend |
| Breakout continuation | Positive 1M trend plus current gap or same-day breadth confirmation |

The anti-continuation rule overrides every setup: after a prior daily gain of
at least 5%, return `AVOID_CHASE` unless breadth is explicitly marked as still
expanding. This implements the July finding without making it a universal
claim; its evidence remains visible in calibration.

Policy actions are:

- `ENTER_SMALL`: 0.50 normalized risk units; 0.75 when multiple setups agree.
- `ENTER_PROBE`: 0.25 normalized risk units.
- `WATCH`: no authorized risk yet.
- `AVOID_CHASE`: explicit day-after-thrust block.
- `NOT_IN_TIMING_UNIVERSE`: no qualifying WHO or coverage membership.

Drawdown-bounce and gap-thrust trades are capped at two days with a 0.75 ATR
invalidation distance. Pullback and guarded continuation trades are capped at
five days with a 1.0 ATR invalidation distance. These are normalized controls,
not account-specific position sizes.

## Next Implementation Phases

### Phase 2: Optional Checkpoint State and Freshness

When multiple daily runs exist, persist transitions rather than treating each
run as isolated. Add `first_seen_checkpoint`, `previous_action`, `action_transition`,
`gap_retention_pct`, and `breadth_delta_since_prior_checkpoint`. Refuse to label
a run `after_open` or `midday` when source timestamps are stale for that phase.

Acceptance checks:

- A premarket `WATCH` can promote to `ENTER_PROBE` after open.
- An after-open entry can demote to `WATCH` at midday when gap retention fails.
- Re-running the same source snapshot is idempotent and visibly marked stale.

### Phase 3: Point-in-Time Setup Calibration

Backtest each setup exactly as computed at each checkpoint. Keep separate
cohorts by setup, checkpoint, market regime, industry/thematic group, ADR band,
and drawdown band. Report count, win rate, mean/median return, MFE, MAE, target
before stop, and turnover for 1D, 2D, and 5D horizons.

Do not blend these statistics into the live score until minimum sample and
walk-forward stability gates pass. The current 50/100 coverage-union
calibration is a smoke test, not confirmation of the five-name July result.

Acceptance checks:

- No row uses data published after its simulated checkpoint.
- July 2026 can be replayed with the same source run IDs.
- Results compare the timing sleeve against conviction longs and buy/hold as
  separate books.

### Phase 4: Policy Outcome Engine

Add executable entry references, gap-retention invalidation, ATR stops, maximum
hold exits, day-after-thrust blocks, and transaction-cost assumptions. Track
authorized actions separately from hypothetical fills so signal quality is not
confused with execution quality.

Acceptance checks:

- Every entry has an entry reference, invalidation, size cap, and time exit.
- Every exit has one deterministic reason.
- No existing `size_tier=reject` or `tape_pass=false` field silently cancels a
  timing-sleeve action.

### Phase 5: Operator Report and Optional Scheduling

Add a compact report showing promotions, demotions, new coverage names, stale
data, setup evidence, and existing-book contradictions. Optional schedules may
run premarket, after open, and midday, but the default latest-data command must
remain fully usable on its own. Retain manifests for replay.

## Prioritized Data Gaps

### 1. Checkpoint-accurate snapshots

**Gap:** A latest all-fields database may have been captured at a different
time than the requested checkpoint.

**Fill from:** TradingView all-fields exports run separately premarket,
30-60 minutes after open, and midday. Store exchange-local capture time,
`created_at_utc`, market session, and source field update timestamps.

**Why first:** Without freshness enforcement, a correct formula can still make
a temporally false decision.

### 2. Gap retention and open-to-checkpoint path

**Gap:** Gap size alone cannot distinguish a held gap from a gap that has
already failed.

**Fill from:** Existing all-fields `open`, `close`, `high`, `low`, `change`,
premarket fields, and bar update timestamps at each scheduled snapshot. Derive
open-to-current return, distance from session high, gap retained, and first-hour
range position.

### 3. Historical breadth percentile and breadth change

**Gap:** The current 60% rule is an absolute cross-section threshold. The
intended signal is also relative to that industry's own recent breadth regime.

**Fill from:** Daily all-fields archive. Persist industry breadth each day and
derive percentile versus the prior 20 sessions, breadth acceleration, median
industry return, and count coverage.

### 4. Thematic breadth taxonomy

**Gap:** TradingView puts ALAB/MU in `Semiconductors`, WDC/SNDK in `Computer
Peripherals`, and NBIS in `Packaged Software`. A real AI compute/storage thrust
therefore fragments across industries; broad sector breadth is too blunt.

**Fill from:** A maintained theme map built from TradingView industry/sector,
company descriptions, ETF holdings, and manually reviewed groups such as AI
compute, memory/storage, data-center networking, and power/cooling. Version the
mapping and prevent hindsight edits in backtests.

### 5. True sticky-WHO history

**Gap:** Version 1 unions current upside top-50 and current screen top-100. It
does not yet include every symbol that appeared in upside during the prior 30
days.

**Fill from:** Edge historical progression DuckDB and prior edge parent
artifacts. Derive days in top-10/top-30, best rank, current rank, rank slope,
days since last inclusion, and coverage source.

### 6. Archive completeness and corporate-action controls

**Gap:** Dated snapshot availability is not a formal trading calendar, and
archived close changes can disagree with exported daily `change`.

**Fill from:** A US/global exchange calendar, per-day source manifest, adjusted
OHLC where available, split/dividend events, and explicit missing-session flags.
Continue using exported `change` for current sequence rules until adjusted
prices are validated.

### 7. Execution outcomes

**Gap:** Daily snapshots cannot estimate fill quality, first-hour adverse move,
or whether a stop and target were both touched in an unknown order.

**Fill from:** Scheduled open/after-open/midday snapshots as the minimum viable
path. For higher fidelity later, obtain 5- or 15-minute OHLCV from a licensed
market-data source. Continuous monitoring is not required to store bars for
research.

### 8. Catalyst and event context

**Gap:** Breadth thrusts can be caused by earnings, guidance, macro releases,
or peer news; policy should distinguish systematic washout bounces from
event-specific gaps.

**Fill from:** Existing earnings dates plus SEC filings, company releases,
licensed news/event feeds, and macro calendars. Store publication time so the
feature remains point-in-time.

### 9. Portfolio context

**Gap:** Normalized risk units do not know existing exposure or correlated
positions.

**Fill from:** Current holdings, gross/net exposure, sector/theme exposure,
single-name limits, and realized volatility. Add this only after the standalone
timing sleeve has stable signal KPIs.

## Guardrails

- Never use upside rank alone as an entry.
- Never promote a day-after 5% winner without explicit breadth expansion.
- Never use backwards-scan deltas as if they were known before the current run.
- Never let historical calibration leak into live scoring without a versioned
  rule and walk-forward validation.
- Keep timing-sleeve KPIs separate from conviction/survival-book KPIs.
- Surface missing or stale fields; do not convert them into silent passes.