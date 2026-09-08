# Operator scan reference

Use this only when compiling or briefing. The briefing pack already joins these.

## Artifacts

| Source | Where | Lock by |
|---|---|---|
| Move prediction | `logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=*/week=*/move_prediction_*.duckdb` | `run_metadata.created_at_utc` |
| All-fields | `logs/tradingview_analysis/trading_view_all_fields_data/DD_MM_YYYY/tradingview_all_fields_*.duckdb` | day folder then latest `run_id` |
| MTP | `logs/tradingview_analysis/market_timing_policy/runs/market_timing_policy_*` | newest dir |
| Edge leftover | parent `.../edge_research_tools/runs/edge_*` → `aggregate/upside_opportunity_scan/upside_opportunity_candidates.csv` | `upside_opportunity_rank`, `forward_valuation_upside_pct` |
| Holdings scoring | `.../holdings_scoring_analysis/runs/holdings_scoring_*/scans/move_prediction_v1/holdings__summary.csv` | newest dir |
| Book membership | `config/holdings_scoring/current_holdings.json` | cash + invested sums |
| Priors | `logs/AI_ANALYSIS_UTILS/priors/YYYYMMDD_operator_actions.json` | latest scan_day |
| Focus pool | `.../focus_pool_screening/runs/focus_pool_*` | optional; flag stale if day ≠ scan day |

DuckDB paths containing `=` must be quoted. Prefer `query_move_prediction_duckdb` / the briefing pack.

## Prediction tables (not the names agents guess)

- `raw_scan_rows` — tape (`close`, `change`, `Perf.5D`, `Perf.1M`, identity)
- `profile_horizon_scores` — per profile × horizon; `score`, `manager_action_signal`
- `consensus_horizon_scores` / `consensus_rows` — mix; column is **`manager_action_signal`**
- `conviction_rankings` — `conviction_score`, **`rank_overall`**, `weeks_ras`, `exclusion_reason`
- `regime_context_scores` — `regime_fit_score`, `active_mgmt_tier` (no `regime_fit_tier`)

Weeks profiles used in the pack:

- leftover/upside tape: `breakout_long_v1` (bo), `quality_continuation_v1` (cont), `forward_edge_active_v2` (fwd)
- also: `early_momentum_inflection_v1`, `sustained_momentum_safety_v1`, `fragility_short`, `mean_reversion_exhaustion_v1`

## All-fields

Select a whitelist. Never `SELECT *` (3.5k columns). Useful: `RSI`, `SMA20`, `SMA50`, `SMA200`, `price_target_average`, `price_52_week_high` / `price_52_week_low` (fallbacks `High.52Week` / `Low.52Week`), `earnings_release_next_date`, `operating_margin`, `price_earnings_ttm`.

`dte` = days from scan day folder date to next earnings date.

## Leftover

```
left = max( (price_target_average / close - 1) * 100,
            edge.forward_valuation_upside_pct )
```

Do not rank leftover Book names by a conviction_score that `avoid_value_trap` zeroed.

## CLI (replaces logs/_tmp_*.py)

| Command | Use |
|---|---|
| `python src/operator_briefing/example_entry.py compile` | Lock sources, sleeves, continuity, stances |
| `... inspect` | Tables, columns, latest run_ids, pack path |
| `... lookup MU SNDK` | Raw + progression + stance dossier |
| `... compare [--run-a A --run-b B]` | Weeks bo/cont/fwd deltas, top-40 churn, mix flips |
| `... stance MU SNDK` | Course + suggested_conviction + conflicts only |

## Stance / suggested_conviction

Pack keys: `stances`, `suggested_courses` (ranked **per bucket**), `primary_course` (`bias` / `do` / `do_not`).

`suggested_conviction` is support for that course, not mix, not leftover rank, not a probability. Conflicts stay on the row (polar block, MTP bounce vs NEW/short, leftover-91 EXIT is not a short, crowd industry).

## Sleeve filters (already in the pack)

Implemented in `src/operator_briefing/sleeves.py`:

- unpaid: leftover ≥ 25, RSI ≤ 68, close ≥ $5, mcap ≥ $2B (satellite $0.5B only if profile live or opp ≤ 15), skip biotech/precious/pharma/insurance dumps
- forming: unused 52w range or rising bo with leftover still there
- continuation_paid: bo ≥ 1.2 and (RSI ≥ 70 & rng ≥ 85, or leftover ≤ 0 & RSI ≥ 66)
- shorts_limited_upside: leftover ≤ 25 and deteriorating 5D / profiles / vs50

Auto unpaid from raw leftover rank is **invalid input**.

## Curated headline sleeves (industry-capped)

`dedupe_by_industry` (`sleeves.py`) is the domain wrapper around
`generic_utils.ranking.group_capped_top_n`: cap a pre-sorted sleeve at
`RADAR_INDUSTRY_CAP`/`SHORT_INDUSTRY_CAP`/`EARNINGS_INDUSTRY_CAP` (default 5)
names per industry so one crowded industry (e.g. Packaged Software) cannot
fill the whole list. Book names are always exempt. `compile.py` uses it to
build, alongside the original uncapped sleeves:

- `sleeves.radar_curated_25` (+ `sleeves.radar_industry_overflow`) — headline Top 100 view; `radar_upside_100` is the appendix
- `sleeves.short_book_15_curated` (+ `sleeves.short_book_15_overflow`) — headline shorts; `short_book_15` is the appendix
- `earnings_lanes.upside_curated` / `.downside_curated` (+ `.upside_overflow` / `.downside_overflow`) — headline earnings lanes; `.upside` / `.downside` are the appendix

Every compact row in every sleeve/earnings bucket also carries `sleeve_tags`
(list of which sleeves — unpaid/continuation_unpaid/forming/continuation_paid/
shorts_limited_upside — confirm this name) and `sleeve_count`. Use it as
cross-sleeve confirmation depth, not as a score.

## MTP

`action_policy_recommendations.csv` column `action`: ENTER_SMALL, ENTER_PROBE, WATCH, AVOID_CHASE.

Coverage is edge upside 1–50 ∪ screen 1–100. That is why crashed high-ADR names dominate ENTER_SMALL.

## Continuity thesis-kills (any one, with numbers)

1. Close through prior invalidation
2. Weeks RAS and months RAS both ≤ 0
3. Quality ≤ -0.35 and safety ≤ -0.55 and lost SMA50
4. Vs-cost ≤ -15% and weeks RAS < 0.15 and lost SMA50
5. Leftover gone: 52w range ≥ 85 and RSI ≥ 70
6. hedge_or_short / fragility with failed structure — not the mix trap label alone

## Polar vs conservative

- New risk: conservative wins (no DUOL ADD vs MTP WATCH)
- Live leftover ADD from yesterday: HOLD_NO_ADD wins over naive EXIT
- Yesterday EXIT stays EXIT until tape repairs (weeks RAS ≥ 0.25 and SMA50 held)
- Prior DERISK_INTO_PRINT sticks while dte ≤ 7
- Prior TRIM sticks until weeks RAS ≥ 0.25 and SMA50 held
- Prior HOLD_NO_ADD does not silently become HOLD
