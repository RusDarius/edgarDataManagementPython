# Edge Research Tools Active Management Playbook

This playbook is dedicated to the edge research module under `src/edge_research_tools/`.
It explains what the scans mean, how to interpret ranking context, how to extend breadth, and how to move from data analysis into active execution decisions.

## 1) What this module is doing

The module is a state-detection engine on top of all-fields daily exports.

It asks:
- Which names are in a high-volatility/high-liquidity state today?
- Which names show that state repeatedly (durable behavior, not one-off)?
- Which industries or sectors historically had positive forward outcomes when that state appeared?

It does **not** claim permanent intrinsic quality. It ranks **current statistical setup state** and historical forward behavior under that state.

## 1A) Field Transparency: What This Module Actually Uses Today

The current edge-research module is intentionally narrow. It is a **state engine**, not a full fundamental quality model.

### Base snapshot identity / price context
- `symbol`
- `bare_ticker`
- `company_name`
- `exchange`
- `country`
- `sector`
- `industry`
- `close_price`
- `market_cap_basic`

### Core volatility / liquidity / momentum fields used in the edge module

These are the fields currently used to build the sleeve tables and current-state ranking context.

Volatility sleeve:
- `ADRP`
- `ATRP`
- `Volatility.M`
- `Volatility.W`
- `Volatility.D`
- `beta_1_year`

Liquidity sleeve:
- `relative_volume_10d_calc`
- `Value.Traded`
- `volume`
- `average_volume_30d_calc`
- `AvgValue.Traded_30d`

Momentum-context sleeve:
- `Recommend.All`
- `Perf.5D`
- `Perf.1M`
- `Perf.3M`

### Derived / ranking fields built from those raw fields

For each selected field, the module computes:
- `raw_value`
- directional universe percentile
- directional sector percentile
- directional industry percentile
- z-scores by universe / sector / industry

For each sleeve, the module computes:
- average directional universe percentile
- average directional sector percentile
- average directional industry percentile

### Fields used directly in `screen` / `highlights`

Current-state scoring uses:
- `ADRP` directional percentile
- `relative_volume_10d_calc` directional percentile
- `Value.Traded` directional percentile
- `volatility_core` sleeve directional percentile
- `liquidity_core` sleeve directional percentile
- `momentum_context` sleeve directional percentile

Informational columns are also surfaced:
- `ADRP` raw
- `ATRP` raw
- `Volatility.M` raw
- `relative_volume_10d_calc` raw
- `volume` raw

### Forward-label outputs used for edge validation
- `forward_return_3d_pct`
- `forward_return_5d_pct`
- `forward_return_10d_pct`
- `forward_return_20d_pct`
- `max_favorable_excursion_<N>d_pct`
- `max_adverse_excursion_<N>d_pct`
- `target_before_stop_<N>d_flag`

### What is **not** currently used inside this module

The current edge-research module does **not** directly score on:
- EV-based valuation multiples
- free cash flow yield / EV cash generation
- net debt / debt maturity structure
- `current_ratio`, `cash_ratio`, `short_term_cash_coverage`
- `altman_z_score_ttm`, `zmijewski_score_ttm`
- `return_on_invested_capital`
- `earnings_yield`, `shareholder_yield`

Those fields exist elsewhere in the broader codebase, but they are **not yet in the current edge-research snapshot scoring path**.

That distinction matters: this module currently tells you whether a name is in an actionable trading state with historical edge, not whether it is a fortress balance-sheet compounder.

## 2) Core outputs and what each one tells you

### A. Screen output (`screen` command)

Output: `edge_screen_ranked.csv`

What it tells you:
- Today-state ranking of symbols by setup quality.
- Whether each symbol currently passes one or more setup variants.
- Historical forward behavior for the same symbol when it was previously in setup.

Ranking context:
- `screen_rank` is sorted by `composite_score` descending.
- Tie-break favors higher `adrp_directional_universe_percentile`.

`composite_score` definition:
- Mean of 6 directional universe percentiles:
  - ADRP percentile
  - relative volume percentile
  - value traded percentile
  - volatility_core sleeve percentile
  - liquidity_core sleeve percentile
  - momentum_context sleeve percentile

Inclusion logic:
- Included if `composite_score >= min_composite`, **or** it passes at least one setup variant flag.

Important interpretation:
- High `composite_score` = strong current state quality.
- High `median_fwd_5d_in_setup` + high `win_rate_5d_in_setup` = historical confirmation for this symbol under similar states.
- Low historical counts with high score = promising but less statistically validated.

### B. Persistence output (`scan-persistence` command)

Output: `edge_persistence_ranked.csv`

What it tells you:
- Which symbols repeatedly enter setup conditions over the entire dataset window.

Ranking context:
- `persistence_rank` sorted by `any_setup_rate` descending.
- Tie-break favors higher `avg_adrp_pct`.

Key durability signal:
- `any_setup_rate = any_setup_days / total_dataset_days`.

Important interpretation:
- High persistence = setup is recurring and structurally frequent.
- Low persistence but high current screen rank = more tactical/opportunistic than durable.

### C. Edge summary output (`scan-edge` command)

Output: `edge_summary_industry.csv` (or sector/exchange/country)

What it tells you:
- Where edge is concentrated by group and setup variant.
- Group-level performance distribution for forward returns, win rate, target-before-stop, MFE/MAE.

Ranking context:
- Ordered by `setup_name`, then `median_fwd_<ranking_horizon>d` descending, then occurrence count.

Important interpretation:
- This is your lane-selection stage.
- Favor groups with both positive median returns and enough sample size.

### D. Daily highlights output (`highlights` command)

Outputs:
- `edge_lane_leaders.csv`
- `edge_name_shortlist.csv`
- `edge_name_top30.csv`
- `edge_name_top<N>_confidence.csv`
- optional companion overlay from integrated suite: `edge_tradeable_safety_overlay.csv`
- optional companion valuation lens from integrated suite: `edge_forward_upside_valuation_ranked.csv`

What it tells you:
- strongest lanes for the chosen setup variant in the chosen scope
- names that pass lane + state + durability filters
- a top-30 daily big-mover list for active management
- a configurable top-confidence list for quick review when you need speed
- when run through the integrated suite, a non-excluding safety/value overlay for those same tradeable names
- when run through the integrated suite, a forward-upside valuation ranking that adapts method mix by company style and caps active valuation methods at four

Ranking context:
- `big_mover_score` emphasizes today-state quality and expansion conditions
- `confidence_score` emphasizes recurrence, historical confirmation, and rolling stability
- `stability_score` measures whether the setup has appeared consistently and whether recent composite scores are stable rather than erratic

## 3) Setup variants and thresholds

The module uses three variants (from `setup_engine.py`):

1. `adrp_relvol_core`
- ADRP >= 0.80
- Relative volume >= 0.70
- Value traded >= 0.55
- Liquidity core >= 0.60
- Momentum context >= 0.45
- Volatility core >= 0.65

2. `adrp_relvol_momentum`
- ADRP >= 0.75
- Relative volume >= 0.70
- Value traded >= 0.50
- Liquidity core >= 0.58
- Momentum context >= 0.55
- Volatility core >= 0.60

3. `volatility_liquidity_balanced`
- ADRP >= 0.70
- Relative volume >= 0.60
- Value traded >= 0.50
- Liquidity core >= 0.68
- Momentum context >= 0.50
- Volatility core >= 0.75

Interpretation rule:
- Passing more than one variant usually indicates stronger setup quality consistency across multiple lenses.

## 3A) Why a Separate Safety Scan Is Better Than Forcing Quality Into This Composite

You asked whether quality / fortress-style safety should become part of the same composite.

The better answer is usually **no**.

Reason:
- the edge module is built around volatility, liquidity, and participation state
- a deep quality / balance-sheet composite has a different purpose
- mixing them directly often creates a noisy score where tactical expansion and slow-moving solvency fight each other

A better design is a **companion safety scan** with two explicit components:

1. `balance_sheet_safety_score`
- net cash to market cap
- short-term cash coverage
- current ratio
- cash ratio
- debt to equity
- net debt
- Altman Z
- Zmijewski

2. `cash_generation_value_score`
- free cash flow margin
- earnings yield
- shareholder yield
- cash generation relative to EV or market cap
- peer-relative percentiles inside industry

That gives you:
- edge module = state / timing / participation
- safety module = solvency / cash durability / valuation support

Then you can intersect them when you want high-quality tactical longs rather than trying to make one score solve two different problems.

## 4) How to build edge names (recommended funnel)

Use this exact sequence:

### Step 1: Select lanes (group-level edge)
From `scan-edge`:
- Keep groups with sufficient sample and positive edge.

Practical lane filter:
- `sample_count_5d >= 20`
- `win_rate_5d >= 0.55`
- `median_fwd_5d >= 1.0`

### Step 2: Select current candidates (today-state)
From `screen`:
- Keep names in selected lanes.
- Prefer top `composite_score` and at least one setup variant flag.

### Step 3: Keep durable confirmations
From `scan-persistence` + screen history:
- `any_setup_rate` above minimal floor (e.g., 0.03 to 0.08 depending strictness)
- `hist_occurrence_count_in_setup` above floor (e.g., >= 2 or >= 5)
- `median_fwd_5d_in_setup >= 0`
- `win_rate_5d_in_setup` acceptable for your risk style

### Step 4: Final ranking for execution list
Use a blended score, for example:

`edge_score = 0.60 * composite_score + 0.40 * (any_setup_rate / 0.22)`

You can change weights:
- More tactical: increase composite weight.
- More durable: increase persistence weight.

### Step 5: Daily highlights run for active management

Once the snapshot database is refreshed for the day, run:

`python -m run_edge_research_tools highlights --snapshot-db ".../symbol_day_feature_snapshot.duckdb"`

What it produces:
- lane leaders
- full shortlist
- top 30 big movers
- top confidence list sized by your configured `top10_count`

This is the intended once-per-market-day extraction method.

If you are running the grouped full suite, read those same outputs under the parent run directory:
- `aggregate/highlights/edge_lane_leaders.csv`
- `aggregate/highlights/edge_name_shortlist.csv`
- `aggregate/highlights/edge_name_top30.csv`
- `aggregate/highlights/edge_name_top20_confidence.csv` for the current configured focus count
- `aggregate/safety_highlights/edge_safety_name_top20_focus.csv` for the current configured focus count
- `aggregate/tradeable_safety_lens/edge_tradeable_safety_overlay.csv`
- `aggregate/tradeable_safety_lens/edge_tradeable_safety_top20.csv`
- `aggregate/forward_upside_valuation_lens/edge_forward_upside_valuation_ranked.csv`
- `aggregate/forward_upside_valuation_lens/edge_forward_upside_valuation_top20.csv` for the current configured focus count
- `aggregate/edge_unified_highlights/edge_unified_highlights.duckdb` query-ready merge of all outlooks (integrated suite only)
- `aggregate/edge_unified_highlights/edge_unified_highlights.csv` CSV mirror of the same rows

## 5) Interpreting a grouped suite run

Current grouped parent run example:
- `logs/tradingview_analysis/edge_research_tools/runs/edge_latest_500m_full_parent_20260628_1800_utc_f6d84ec6/`

Read it in this order:
- `parent_run_manifest.json`: authoritative suite scope, date window, requested market-cap floor, preferred-markets universe, and snapshot path.
- `aggregate/parent_run_manifest.json`: aggregate-suite settings and child output folders.
- `aggregate/screen/edge_screen_ranked.csv`: current-state expansion candidates.
- `aggregate/scan_persistence/edge_persistence_ranked.csv`: durable repeat-setup names.
- `aggregate/scan_edge/edge_summary_industry.csv`: best historical lanes by group.
- `aggregate/highlights/edge_name_top20_confidence.csv`: fastest daily action list for the current configured focus count.
- `aggregate/highlights/edge_name_top30.csv`: broader tactical expansion list.
- `aggregate/safety_highlights/edge_safety_name_top20_focus.csv`: quality / resilience companion list for the current configured focus count.
- `aggregate/tradeable_safety_lens/edge_tradeable_safety_overlay.csv`: the clean join between upside tradeables and safety/value scores.
- `aggregate/tradeable_safety_lens/edge_tradeable_safety_top20.csv`: combined upside-plus-safety review file for the current configured focus count.
- `aggregate/forward_upside_valuation_lens/edge_forward_upside_valuation_ranked.csv`: full shortlist ranked by adaptive forward valuation upside.
- `aggregate/forward_upside_valuation_lens/edge_forward_upside_valuation_top20.csv`: fastest forward-valuation focus file for the current configured count.

Interpretation:
- For this specific run, the snapshot was sourced from `logs/tradingview_analysis/edge_research_tools/foundations/edge_feature_snapshot_20260628_1042_utc_c3c0217c/`, which means the foundation-vs-analysis split behaved as intended.
- The suite used the preferred-markets universe with a compatible 500M minimum market-cap snapshot, so the run is valid for the intended active-opportunity scope.
- `edge_screen_ranked.csv` is the widest current-state list and should be used for idea discovery, not final conviction.
- `edge_persistence_ranked.csv` tells you which setups recur structurally, which is useful for separating tactical spikes from repeatable behavior.
- `edge_summary_industry.csv` is your lane filter; it tells you where setup edge is concentrated before you rank names.
- `edge_name_top20_confidence.csv` is the primary execution review file for this currently configured workflow.
- `edge_safety_name_top20_focus.csv` is not a replacement for the edge list; it is the companion file when you want higher-balance-sheet-quality tactical longs.
- `edge_tradeable_safety_overlay.csv` is the preferred non-excluding risk lens because it keeps the full highlights shortlist and simply adds safety/value columns, threshold flags, and combined ranks.
- `edge_tradeable_safety_top20.csv` is the fastest combined file when you want upside candidates sorted with an extra safety check.
- `edge_forward_upside_valuation_ranked.csv` is the full adaptive valuation lens output; each symbol uses up to four valuation mechanisms chosen by company style and data availability.
- `edge_forward_upside_valuation_top20.csv` is the fastest forward-looking valuation shortlist when you want explicit fair-value-like upside context.
- `aggregate/edge_unified_highlights/edge_unified_highlights.duckdb` is the single query surface when you want edge + probabilistic bootstrap + upside + valuation + safety in one place.
- There is no longer a core `edge_name_top10.csv` or `edge_screen_persistence_intersection.csv` artifact in the grouped suite. Use `edge_name_top<N>_confidence.csv`, `edge_name_top30.csv`, the safety companion outputs, the tradeable safety lens, and the unified DuckDB store instead.
- This audited run was created before the child-artifact path-rewrite fix. The actual live files are the ones under `aggregate/*`; if a child report or manifest still prints `_staging/...`, treat that embedded path as stale text rather than as the real location.

## 6) Active-management usage: when to act now vs wait

### Act now (higher-priority candidates)
Typical profile:
- Top screen ranks
- Positive and strong historical in-setup 5d stats
- Adequate persistence
- In one of the best lanes

### Watchlist / staged entry
Typical profile:
- High current score but lower persistence or limited history
- Lane is strong but name-specific history is mixed
- Or high big-mover score but weaker confidence score

### De-prioritize for now
Typical profile:
- Strong current score in a weak lane
- Or strong lane but symbol has weak historical in-setup outcomes
- Or high volatility expansion but poor stability / poor historical confirmation

## 7) Breadth controls (how to extend coverage)

### Broader symbol coverage
- Increase `screen --top-n`
- Lower `screen --min-composite`
- Increase `scan-persistence --top-n`
- Reduce `scan-persistence --min-setup-days`
- Increase `highlights --shortlist-top-n`
- Use highlights shortlist breadth mode (`min_shortlist_count`, default 1000 in local full-suite workflow) so integrated unified outputs can score/rank a wider candidate set while preserving strict-pass priority.

### Broader grouping analysis
- Run `scan-edge` with `--group-by industry`, `sector`, `exchange`, `country`
- Run `highlights --group-by sector` when you want broader lanes than industry

### Region filters for selection outputs
- `highlights --us-only`
- `highlights --countries US,CA`
- `highlights --exchanges NASDAQ,NYSE`
- `highlights --markets america,canada,uk`

### Broader universe construction
When running `suite` or `snapshot`:
- Lower/remove `--min-market-cap-usd`
- Use `--include-non-primary` if you want broader symbol classes

### More outcome detail
- Keep horizons wide in suite (`3 5 10 20`)
- Evaluate tradeoff between short-term consistency and longer-term payoff tails

## 8) Execution-oriented daily/weekly tutorial

### Daily process (active manager loop)
1. Run `screen` on latest snapshot day.
2. Pull top candidates and check variant flags + composite score.
3. Join with persistence ranks and historical in-setup metrics.
4. Produce execution watchlist tiers (A/B/C) with your risk sizing framework.

### Faster daily process (recommended)
1. Refresh suite or latest snapshot database.
2. Run `highlights`.
3. Review `edge_name_top20_confidence.csv` first for the current configured focus count.
4. Review `edge_name_top30.csv` second for broader expansion candidates.
5. Review `edge_tradeable_safety_top20.csv` when you want the same upside set reordered by a safety/value-aware blend.
6. Use `edge_tradeable_safety_overlay.csv` to inspect safer vs riskier names without excluding tactical setups.
7. Review `edge_forward_upside_valuation_top20.csv` when you want forward-looking valuation upside estimates beside tactical edge.
8. Use `edge_lane_leaders.csv` to see whether the day is concentrated in a few strong lanes or spread out.

### Weekly process
1. Refresh `scan-edge` by industry/sector.
2. Refresh `scan-persistence` with broad top-n.
3. Rebuild lane filter and shortlist.
4. Compare week-over-week lane drift and shortlist churn.

### Practical command set (PowerShell)

`$env:PYTHONPATH='src;.'`

`python -m run_edge_research_tools screen --snapshot-db "logs/tradingview_analysis/edge_research_tools/foundations/edge_feature_snapshot_20260628_1042_utc_c3c0217c/symbol_day_feature_snapshot.duckdb" --top-n 300 --min-composite 0.45 --ranking-horizon 5`

`python -m run_edge_research_tools scan-persistence --snapshot-db "logs/tradingview_analysis/edge_research_tools/foundations/edge_feature_snapshot_20260628_1042_utc_c3c0217c/symbol_day_feature_snapshot.duckdb" --min-setup-days 1 --top-n 20000 --ranking-horizon 5`

`python -m run_edge_research_tools scan-edge --snapshot-db "logs/tradingview_analysis/edge_research_tools/foundations/edge_feature_snapshot_20260628_1042_utc_c3c0217c/symbol_day_feature_snapshot.duckdb" --group-by industry --ranking-horizon 5 --min-occurrence-count 5`

`python -m run_edge_research_tools highlights --snapshot-db "logs/tradingview_analysis/edge_research_tools/foundations/edge_feature_snapshot_20260628_1042_utc_c3c0217c/symbol_day_feature_snapshot.duckdb" --ranking-horizon 5 --shortlist-top-n 30 --top10-count 10`

Preferred-markets example:

`python -m run_edge_research_tools highlights --snapshot-db "logs/tradingview_analysis/edge_research_tools/foundations/edge_feature_snapshot_20260628_1042_utc_c3c0217c/symbol_day_feature_snapshot.duckdb" --markets america,canada,uk --ranking-horizon 5 --shortlist-top-n 30 --top10-count 10`

US-only example:

`python -m run_edge_research_tools highlights --snapshot-db "logs/tradingview_analysis/edge_research_tools/foundations/edge_feature_snapshot_20260628_1042_utc_c3c0217c/symbol_day_feature_snapshot.duckdb" --us-only`

## 9) What this does and does not say

What it says well:
- Statistical setup quality now.
- Historical behavior under similar setup states.
- Where edge concentration appears by lane.

What it does not say by itself:
- Fundamental fair value truth.
- Event-specific microstructure outcomes.
- Live intraday entry quality.

Forward-upside lens caveat:
- `forward_upside_valuation_lens` adds valuation-style estimates and scenario ranges, but these are model-based projections (not guarantees), and fallback mode can be active when valuation coverage is sparse.

Use this as the research + ranking engine, then combine with your execution and risk framework.

## 10) Common mistakes and fixes

1. Using too small `scan-persistence --top-n`
- You may miss joins when merging with broad screen output.
- Fix: use a large top-n (for example 5,000 to 20,000) during analysis generation.

2. Overfitting to tiny lanes
- High median return with very small sample can be unstable.
- Fix: apply minimum sample floors before trusting lane edge.

3. Treating one-day screen as durable edge
- A high screen rank can be tactical noise.
- Fix: always check persistence + historical in-setup stats.

4. Ignoring drawdown context
- Positive median return with severe MAE can still be poor for execution.
- Fix: inspect MAE and target-before-stop rates for horizon-specific suitability.

5. Forcing defensive-quality logic into the volatility/liquidity composite
- This can dilute both tactical and quality signals.
- Fix: keep a separate safety companion score and intersect it with the edge shortlist.

## 11) Next extensions (recommended)

- Completed in this iteration:
- grouped parent-run packaging for multi-step suites
- foundation snapshots now written under `foundations/` instead of mixed into `runs/`
- preferred-markets suite support using canonical TradingView market codes
- built-in `highlights` command for daily lane leaders, shortlist, top 30, and top 10 confidence
- non-excluding `tradeable_safety_lens` companion output that joins highlights tradeables to the safety/value score universe
- non-excluding `forward_upside_valuation_lens` companion output that applies adaptive forward valuation (max four methods per company) with fallback to edge-native upside ranking
- `edge_unified_highlights` DuckDB store that merges shortlist symbols with all outlook scores, probabilistic bootstrap context, safety detail, and best-across-outlooks ranking
- optional region filters (`--us-only`, `--countries`, `--exchanges`) on highlights output
- optional market filters (`--markets`) on screen, persistence, edge summary, highlights, and safety outputs
- direct region filters on `screen`, `scan-persistence`, and `scan-edge`
- explicit date-window controls (`--start-date`, `--end-date`) on persistence and edge summary scans
- per-name rolling `stability_score` based on recent setup frequency and composite-score dispersion
- bootstrap confidence intervals for lane medians in `scan-edge` and lane leaders in `highlights`
- dedicated `safety-highlights` companion command with
  - `balance_sheet_safety_score`
  - `cash_generation_value_score`
  - indicator columns for solvency, liquidity, debt, and cash-generation checks

- Good next additions:
- confidence overlap views between edge state and safety state
- configurable hard-gate presets (strict / balanced / tactical) for safety indicators
- automatic intersection report: edge top 30 x safety top 30 with combined conviction bands

## 12) Unified Edge Highlights DuckDB (`edge_unified_highlights`)

This section is the decision-making query guide for:

`aggregate/edge_unified_highlights/edge_unified_highlights.duckdb`

It is built automatically at the end of the **integrated extension** suite (and the full historic aggregate workflow). It is **not** built by standalone `highlights` or `safety-highlights` runs alone.

Companion files in the same folder:
- `edge_unified_highlights.csv` — same rows as the main table (Excel-friendly)
- `edge_unified_highlights_manifest.json` — run paths and horizon
- `edge_unified_highlights_report.md` — short usage summary

### 12A) Which stocks are in this database — and which are not

**A symbol gets a row in `symbol_unified_highlights` only if it is in `edge_name_shortlist.csv`.** The unified DB is **not** the full screen universe, **not** the full safety universe, and **not** the full persistence universe.

#### Layer 1 — Foundation snapshot universe (upstream gate)

Before highlights runs, names must exist in `symbol_day_feature_snapshot.duckdb` for the run window. In the default full aggregate suite this typically means:
- preferred markets filter (for example `america`, `canada`, `uk`)
- minimum market cap on the snapshot (for example `500_000_000` USD on the latest full suite)
- valid symbol-day rows with forward labels for the ranking horizon

Names outside the snapshot never reach any downstream artifact.

#### Layer 2 — Screen / current-state gate (day of scan)

On the highlights scan date, a symbol must satisfy at least one of:
- `composite_score_day >= screen_min_composite` (default `0.45`)
- **or** `in_any_setup = 1` (passes at least one volatility/liquidity setup variant that day)

Default setup variant for lane selection: `adrp_relvol_core`, grouped by `industry`.

#### Layer 3 — Lane gate (group must be a leader)

The symbol's lane group (`lane_group_value`, usually `industry`) must appear in `edge_lane_leaders.csv`.

Lane leaders must pass lane-level floors (defaults):
- `lane_min_sample_count >= 20` at ranking horizon
- `lane_min_win_rate >= 0.55`
- `lane_min_median_fwd >= 1.0` (% forward return)

If no lane passes all floors, highlights falls back to the top raw lanes — but the symbol still must belong to one of those lane groups.

#### Layer 4 — Symbol shortlist gate (strict first, then expanded breadth if needed)

Even with a strong lane, strict membership prefers symbols that pass all of these (defaults):
- `hist_occurrence_count_in_setup >= 2`
- `win_rate_5d_in_setup >= 0.45` (or your configured ranking horizon)
- `median_fwd_5d_in_setup >= 0.0`
- `any_setup_rate >= 0.03`

When strict-pass names are below `min_shortlist_count` (new highlights breadth control, default 1000 in local full suite), highlights adds expanded momentum candidates from the same date to reach the floor, and tags them with:
- `strict_filter_pass` (`1` strict, `0` expanded)
- `strict_filter_failed_tags` (which strict durability checks failed)
- `expanded_capture_score` (momentum-biased expansion rank)
- `shortlist_source_tier` (`strict` or `expanded`)

The unified DB contains **exactly `edge_name_shortlist.csv`** (one row per symbol), re-ranked by `unified_edge_highlight_score`, so increasing `min_shortlist_count` directly increases unified modeling reach.

#### What is explicitly **not** in the unified DB

| Outside the DB | Why |
|---|---|
| `edge_screen_ranked.csv` names that failed shortlist filters | Screen is wider than shortlist |
| `edge_persistence_ranked.csv` names not on shortlist | Persistence is universe-wide recurrence, not today's tradeable shortlist |
| `edge_safety_scored.csv` names not on shortlist | Safety scans the filtered snapshot universe (~thousands), unified only keeps shortlist overlap |
| Top-30 / top-confidence-only names missing from shortlist | Top lists are subsets of shortlist; unified has the **full** shortlist |
| Names outside region/market filters used in the parent run | Filtered upstream in snapshot/highlights |

#### Partial vs full field coverage inside an included row

| Field family | Coverage rule |
|---|---|
| Edge setup / hist / ranks | Always present (shortlist-native) |
| `safety_detail_*` | Present when symbol exists in `edge_safety_scored.csv` (usually yes for liquid names in snapshot) |
| `tradeable_safety_*` | Present when integrated suite ran tradeable safety lens |
| `forward_*` valuation | Present when integrated suite ran forward lens; may be `fallback` mode with sparse valuation % |
| `lane_leader_*` bootstrap | Present when industry/lane group matched `lane_bootstrap_leaders` |
| `edge_group_*` bootstrap | Present when `scan_edge` industry summary exists for that group |

Check run settings any time:

```sql
SELECT key, value
FROM unified_run_manifest
ORDER BY key;
```

### 12B) DuckDB tables

| Table | Rows | Use |
|---|---|---|
| `symbol_unified_highlights` | one per shortlist symbol | **Primary decision table** — all merged scores and outlook columns |
| `lane_bootstrap_leaders` | one per qualifying lane group | Industry/sector lane probabilistic edge context |
| `edge_group_bootstrap_summary` | one per group × setup variant | Grouped `scan-edge` bootstrap CIs |
| `unified_run_manifest` | key/value metadata | Horizon, source CSV paths, outlook list |

#### Key columns on `symbol_unified_highlights`

**Symbol identity (use for precise lookups)**
- `symbol` — primary key, `EXCHANGE:TICKER` format (e.g. `NYSE:FDX`, `NASDAQ:RMBS`, `LSE:ABC`)
- `bare_ticker` — ticker only (`FDX`, `RMBS`); safe only when unambiguous in the run
- `exchange` — exchange code (`NYSE`, `NASDAQ`, `AMEX`, `OTC`, `LSE`, …)

> **Recommended:** filter on `symbol = 'NYSE:FDX'` for precision. Use `bare_ticker` only when you have confirmed a single match.

**Unified ranking**
- `unified_edge_highlight_rank` — primary sort (best overall)
- `unified_edge_highlight_score` — `72%` cross-outlook consensus + `28%` probabilistic lane-fit
- `unified_best_outlook_name` — strongest single outlook (`upside_prediction`, `forward_valuation`, `edge_big_mover`, etc.)
- `unified_best_outlook_score` — normalized 0–1 score for that outlook
- `unified_consensus_score` — weighted average across outlooks
- `unified_probabil_edge_fit_score` — bootstrap CI + lane/hist probabilistic fit
- `unified_best_across_outlooks_flag` — `1` for top-30 unified highlights
- `outlook_*_score` — per-outlook normalized scores

**Edge setup (tactical state)**
- `big_mover_score`, `big_mover_rank`, `confidence_score`, `confidence_rank`
- `composite_score`, `stability_score`, `current_lane_flag`
- `adrp_pct_today`, `relvol_pct_today`, `value_traded_pct_today`, sleeve percentiles

**Historical tape (in-setup behavior)**
- `median_fwd_5d_in_setup`, `win_rate_5d_in_setup`, `target_rate_5d_in_setup`
- `median_mfe_5d_in_setup`, `median_mae_5d_in_setup`, `hist_occurrence_count_in_setup`
- `lane_median_fwd_5d`, `lane_win_rate_5d`, `lane_context_score`

**Upside prediction lens**
- `upside_prediction_score`, `upside_prediction_rank`
- `upside_hist_signal_blend`, `upside_target_before_stop_rate`

**Forward valuation lens**
- `forward_valuation_upside_pct` — headline fair-value upside %
- `forward_upside_mode` — `valuation` or `fallback`
- `forward_selected_lenses`, `forward_company_style`
- `forward_near_term_base_upside_pct`, `forward_medium_term_base_upside_pct`, `forward_long_term_base_upside_pct`

**Safety / tradeable**
- `tradeable_safety_blend_score`, `tradeable_safety_rank`, `safety_bucket`
- `safety_companion_score`, `balance_sheet_safety_score`, `safety_shortlist_flag`
- `safety_detail_*` — full safety scored metrics and indicator flags

**Probabilistic bootstrap (lane attached to symbol)**
- `lane_leader_median_fwd_5d_ci_low`, `lane_leader_median_fwd_5d_ci_high`, `lane_leader_median_fwd_5d_ci_width`
- `lane_leader_median_fwd_5d_bootstrap_sample_count`

**Probabilistic bootstrap (group edge summary)**
- `edge_group_median_fwd_5d_ci_low`, `edge_group_median_fwd_5d_ci_high`, `edge_group_median_fwd_5d_bootstrap_sample_count`

> **Note:** Examples below use the default `5d` ranking horizon. If your manifest shows a different `ranking_horizon`, replace `5d` with `3d`, `10d`, or `20d` in column names.

### 12C) How to open the database

DBeaver, DuckDB CLI, or Python:

```powershell
duckdb "D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\edge_research_tools\runs\<parent_run>\aggregate\edge_unified_highlights\edge_unified_highlights.duckdb"
```

If you see a file-lock error, close other connections (DBeaver) or query the CSV mirror instead.

### 12D) Query cookbook by scanning scenario

Each block is a focused scan you can run as-is. Adjust `LIMIT`, thresholds, and `WHERE` clauses to match your risk style.

---

#### Scenario 1 — Daily action list (start here every morning)

**Goal:** Highest overall conviction names across all outlooks.

```sql
SELECT
    unified_edge_highlight_rank,
    symbol,
    bare_ticker,
    company_name,
    industry,
    unified_edge_highlight_score,
    unified_best_outlook_name,
    big_mover_rank,
    confidence_rank,
    upside_prediction_rank,
    forward_upside_rank,
    tradeable_safety_rank,
    forward_valuation_upside_pct,
    safety_bucket
FROM symbol_unified_highlights
ORDER BY unified_edge_highlight_rank
LIMIT 20;
```

---

#### Scenario 2 — Tactical expansion scan (trade today on setup state)

**Goal:** Strong current volatility/liquidity expansion even if other outlooks are mixed.

```sql
SELECT
    big_mover_rank,
    symbol,
    bare_ticker,
    composite_score,
    adrp_pct_today,
    relvol_pct_today,
    value_traded_pct_today,
    current_lane_flag,
    big_mover_score,
    confidence_score,
    unified_edge_highlight_rank
FROM symbol_unified_highlights
WHERE current_lane_flag = 1
   OR composite_score >= 0.60
ORDER BY big_mover_score DESC, composite_score DESC
LIMIT 30;
```

---

#### Scenario 3 — Durable / high-confidence scan (confirmation-first)

**Goal:** Names with recurring setup behavior and stable composite history.

```sql
SELECT
    confidence_rank,
    symbol,
    bare_ticker,
    confidence_score,
    stability_score,
    any_setup_rate,
    hist_occurrence_count_in_setup,
    win_rate_5d_in_setup,
    median_fwd_5d_in_setup,
    big_mover_score,
    unified_edge_highlight_rank
FROM symbol_unified_highlights
WHERE confidence_score >= 0.55
  AND stability_score >= 0.20
  AND hist_occurrence_count_in_setup >= 3
ORDER BY confidence_score DESC, stability_score DESC
LIMIT 25;
```

---

#### Scenario 4 — Historical upside / tape scan (empirical forward behavior)

**Goal:** Prioritize names whose **in-setup history** shows strong median forward returns and win rate.

```sql
SELECT
    upside_prediction_rank,
    symbol,
    bare_ticker,
    upside_prediction_score,
    upside_hist_signal_blend,
    median_fwd_5d_in_setup,
    win_rate_5d_in_setup,
    target_rate_5d_in_setup,
    upside_target_before_stop_rate,
    median_mfe_5d_in_setup,
    median_mae_5d_in_setup,
    hist_occurrence_count_in_setup
FROM symbol_unified_highlights
WHERE win_rate_5d_in_setup >= 0.55
  AND median_fwd_5d_in_setup >= 2.0
  AND hist_occurrence_count_in_setup >= 3
ORDER BY upside_prediction_score DESC, median_fwd_5d_in_setup DESC
LIMIT 25;
```

---

#### Scenario 5 — Forward valuation scan (fair-value upside)

**Goal:** Names where the valuation lens sees meaningful upside to blended fair value.

**Highlights mover / liquidity fields already in this table** (no extra join needed for edge highlights):

| What you want | Columns in `symbol_unified_highlights` |
|---|---|
| Mover rank / score | `big_mover_rank`, `big_mover_score`, `outlook_edge_big_mover_score` |
| Upside prediction rank / score | `upside_prediction_rank`, `upside_prediction_score`, `outlook_upside_prediction_score` |
| Liquidity participation | `relvol_pct_today`, `liq_core_pct_today`, `value_traded_pct_today` |
| Volatility expansion | `adrp_pct_today`, `vol_core_pct_today` |
| Combined setup quality | `composite_score`, `current_lane_flag`, `confidence_rank` |

> This is the **edge highlights** mover stack (volatility/liquidity state + historical tape), not the separate `run_full_analysis_suite_duckdb` move-prediction profile suite from `main.py`. To blend that external DuckDB, see **Scenario 5C** below.

**5A — Valuation only (original)**

```sql
SELECT
    forward_upside_rank,
    symbol,
    bare_ticker,
    forward_upside_mode,
    forward_valuation_upside_pct,
    forward_rank_horizon_valuation_upside_pct,
    forward_valuation_bear_upside_pct,
    forward_valuation_bull_upside_pct,
    forward_company_style,
    forward_selected_lenses,
    forward_long_term_base_upside_pct,
    upside_prediction_score,
    safety_companion_score
FROM symbol_unified_highlights
WHERE forward_upside_mode = 'valuation'
  AND TRY_CAST(forward_valuation_upside_pct AS DOUBLE) >= 15
ORDER BY TRY_CAST(forward_valuation_upside_pct AS DOUBLE) DESC
LIMIT 25;
```

**5B — Valuation + mover + liquidity + safety (recommended for actionable names)**

Shows fair-value upside, tape expansion/liquidity, **and** safety companion ranks. The original 5B did **not** filter on safety — this version adds safety columns, optional floors, and includes safety in the blended rank.

| Safety field | Meaning |
|---|---|
| `tradeable_safety_rank` | Upside core + safety blend rank (best overall tradeable+safety sort) |
| `safety_rank_within_tradeables` | Safest names **inside the shortlist** (lower = safer) |
| `safety_rank_global` | Rank in full safety scored universe |
| `safety_companion_score` | Blended balance-sheet + cash-generation score (0–1) |
| `tradeable_safety_blend_score` | 65% tradeable core + 35% safety companion |
| `safety_bucket` | `safer`, `balanced`, `aggressive`, `speculative`, `unscored` |

```sql
SELECT
    forward_upside_rank,
    big_mover_rank,
    upside_prediction_rank,
    tradeable_safety_rank,
    safety_rank_within_tradeables,
    symbol,
    bare_ticker,
    industry,
    -- valuation
    forward_upside_mode,
    TRY_CAST(forward_valuation_upside_pct AS DOUBLE) AS valuation_upside_pct,
    forward_company_style,
    forward_selected_lenses,
    -- highlights mover / prediction
    big_mover_score,
    upside_prediction_score,
    confidence_score,
    composite_score,
    current_lane_flag,
    -- liquidity / participation percentiles (0-1)
    relvol_pct_today,
    liq_core_pct_today,
    value_traded_pct_today,
    adrp_pct_today,
    vol_core_pct_today,
    -- safety companion
    safety_bucket,
    safety_companion_score,
    balance_sheet_safety_score,
    cash_generation_value_score,
    tradeable_safety_blend_score,
    indicator_pass_count,
    safety_shortlist_flag,
    -- blended rank: valuation, mover, liquidity, then safety
    ROW_NUMBER() OVER (
        ORDER BY
            TRY_CAST(forward_valuation_upside_pct AS DOUBLE) DESC,
            big_mover_score DESC,
            relvol_pct_today DESC,
            liq_core_pct_today DESC,
            tradeable_safety_blend_score DESC,
            safety_companion_score DESC
    ) AS valuation_mover_liquidity_safety_rank
FROM symbol_unified_highlights
WHERE forward_upside_mode = 'valuation'
  AND TRY_CAST(forward_valuation_upside_pct AS DOUBLE) >= 15
  AND big_mover_score >= 0.55
  AND relvol_pct_today >= 0.50
  AND liq_core_pct_today >= 0.45
  -- optional safety floors (tune or comment out for tactical-only)
  AND safety_data_available = 1
  AND safety_companion_score >= 0.50
  AND safety_bucket NOT IN ('speculative', 'unscored')
ORDER BY valuation_mover_liquidity_safety_rank
LIMIT 25;
```

**5B-strict** — same scan but require safer balance-sheet profile:

```sql
-- Same SELECT as 5B; stricter WHERE:
WHERE forward_upside_mode = 'valuation'
  AND TRY_CAST(forward_valuation_upside_pct AS DOUBLE) >= 15
  AND big_mover_score >= 0.55
  AND relvol_pct_today >= 0.50
  AND safety_companion_score >= 0.65
  AND indicator_pass_count >= 5
  AND safety_bucket IN ('safer', 'balanced')
ORDER BY valuation_mover_liquidity_safety_rank
LIMIT 25;
```

**5B-lite — Single composite score (valuation × mover × liquidity × safety)**

```sql
WITH scored AS (
    SELECT
        *,
        (
            0.40 * LEAST(TRY_CAST(forward_valuation_upside_pct AS DOUBLE) / 80.0, 1.0)
          + 0.25 * big_mover_score
          + 0.10 * LEAST(GREATEST(relvol_pct_today, 0), 1)
          + 0.07 * LEAST(GREATEST(liq_core_pct_today, 0), 1)
          + 0.10 * tradeable_safety_blend_score
          + 0.08 * safety_companion_score
        ) AS valuation_mover_liquidity_safety_score
    FROM symbol_unified_highlights
    WHERE forward_upside_mode = 'valuation'
      AND TRY_CAST(forward_valuation_upside_pct AS DOUBLE) >= 10
)
SELECT
    ROW_NUMBER() OVER (ORDER BY valuation_mover_liquidity_safety_score DESC) AS combo_rank,
    symbol,
    bare_ticker,
    ROUND(valuation_mover_liquidity_safety_score, 4) AS combo_score,
    forward_valuation_upside_pct,
    big_mover_rank,
    big_mover_score,
    upside_prediction_rank,
    tradeable_safety_rank,
    safety_rank_within_tradeables,
    safety_bucket,
    safety_companion_score,
    tradeable_safety_blend_score,
    relvol_pct_today,
    liq_core_pct_today,
    forward_selected_lenses
FROM scored
ORDER BY combo_rank
LIMIT 25;
```

**5B-outlook — Using unified outlook scores** (normalized 0–1 columns, includes safety outlooks):

```sql
SELECT
    symbol,
    bare_ticker,
    forward_valuation_upside_pct,
    outlook_forward_valuation_score,
    outlook_edge_big_mover_score,
    outlook_upside_prediction_score,
    outlook_tradeable_safety_score,
    outlook_safety_companion_score,
    big_mover_rank,
    upside_prediction_rank,
    tradeable_safety_rank,
    safety_rank_within_tradeables,
    safety_bucket,
    relvol_pct_today,
    liq_core_pct_today,
    (
        0.38 * outlook_forward_valuation_score
      + 0.25 * outlook_edge_big_mover_score
      + 0.12 * outlook_upside_prediction_score
      + 0.10 * outlook_tradeable_safety_score
      + 0.08 * outlook_safety_companion_score
      + 0.07 * LEAST(GREATEST(relvol_pct_today, 0), 1)
    ) AS blended_outlook_score
FROM symbol_unified_highlights
WHERE forward_upside_mode = 'valuation'
ORDER BY blended_outlook_score DESC
LIMIT 25;
```

Fallback names (valuation data sparse — rely on tape scores):

```sql
SELECT symbol, bare_ticker, forward_upside_mode, forward_upside_fallback_reason,
       forward_supporting_upside_prediction_score, upside_prediction_score,
       big_mover_score, big_mover_rank, relvol_pct_today, liq_core_pct_today
FROM symbol_unified_highlights
WHERE forward_upside_mode = 'fallback'
ORDER BY forward_supporting_upside_prediction_score DESC, big_mover_score DESC
LIMIT 20;
```

**5C — Optional: join external move-prediction DuckDB** (from `run_full_analysis_suite_duckdb` in `main.py`)

Only if you run that scan separately and want `consensus_horizon_scores` beside edge unified rows:

```sql
ATTACH 'D:/path/to/move_prediction_run/move_prediction.duckdb' AS mp (READ_ONLY);

SELECT
    u.symbol,
    u.bare_ticker,
    u.forward_valuation_upside_pct,
    u.big_mover_score,
    u.relvol_pct_today,
    mp.consensus_horizon_scores.score AS mp_consensus_score,
    mp.consensus_horizon_scores.direction AS mp_direction,
    mp.consensus_horizon_scores.confidence AS mp_confidence,
    mp.consensus_horizon_scores.risk_adjusted_score AS mp_risk_adj_score,
    mp.consensus_horizon_scores.horizon_name
FROM symbol_unified_highlights AS u
LEFT JOIN mp.consensus_horizon_scores
    ON u.symbol = mp.consensus_horizon_scores.symbol
   AND mp.consensus_horizon_scores.horizon_name = 'near_term'   -- or your horizon
WHERE u.forward_upside_mode = 'valuation'
  AND TRY_CAST(u.forward_valuation_upside_pct AS DOUBLE) >= 15
ORDER BY
    TRY_CAST(u.forward_valuation_upside_pct AS DOUBLE) DESC,
    mp.consensus_horizon_scores.score DESC NULLS LAST
LIMIT 25;
```

Use the latest `run_id` in the move-prediction DB if multiple runs are stored (filter on `run_id` from `mp.run_metadata`).

---

#### Scenario 6 — Safety-first scan (balance sheet + cash generation)

**Goal:** Shortlist names that are tactically interesting **and** score well on safety companion metrics.

```sql
SELECT
    safety_rank_within_tradeables,
    symbol,
    bare_ticker,
    safety_bucket,
    safety_companion_score,
    balance_sheet_safety_score,
    cash_generation_value_score,
    indicator_pass_count,
    safety_shortlist_flag,
    safety_focus_flag,
    tradeable_safety_blend_score,
    unified_edge_highlight_rank
FROM symbol_unified_highlights
WHERE safety_data_available = 1
  AND safety_companion_score >= 0.65
  AND indicator_pass_count >= 5
ORDER BY safety_companion_score DESC, tradeable_safety_blend_score DESC
LIMIT 25;
```

Aggressive tactical names with explicit safety warning:

```sql
SELECT symbol, bare_ticker, safety_bucket, safety_companion_score,
       big_mover_score, forward_valuation_upside_pct, unified_edge_highlight_rank
FROM symbol_unified_highlights
WHERE safety_bucket IN ('aggressive', 'speculative', 'unscored')
  AND big_mover_score >= 0.70
ORDER BY big_mover_score DESC;
```

---

#### Scenario 7 — Tradeable safety blend scan (upside + safety without excluding setups)

**Goal:** Same shortlist, re-ordered for managers who want `65%` tradeable core + `35%` safety blend.

```sql
SELECT
    tradeable_safety_rank,
    symbol,
    bare_ticker,
    tradeable_safety_blend_score,
    tradeable_core_score,
    safety_companion_score,
    big_mover_score,
    confidence_score,
    safety_rank_within_tradeables,
    risk_rank_within_tradeables,
    unified_edge_highlight_rank
FROM symbol_unified_highlights
ORDER BY tradeable_safety_blend_score DESC, tradeable_core_score DESC
LIMIT 20;
```

---

#### Scenario 8 — Probabilistic lane-fit scan (bootstrap-supported edge)

**Goal:** Names whose **industry lane** has statistically positive bootstrap median forward return at the ranking horizon.

```sql
SELECT
    unified_probabil_edge_fit_score,
    symbol,
    bare_ticker,
    industry,
    lane_leader_median_fwd_5d_ci_low,
    lane_leader_median_fwd_5d_ci_high,
    lane_leader_median_fwd_5d_ci_width,
    lane_leader_median_fwd_5d_bootstrap_sample_count,
    lane_median_fwd_5d,
    lane_win_rate_5d,
    median_fwd_5d_in_setup,
    unified_edge_highlight_rank
FROM symbol_unified_highlights
WHERE TRY_CAST(lane_leader_median_fwd_5d_ci_low AS DOUBLE) > 0
  AND TRY_CAST(lane_leader_median_fwd_5d_bootstrap_sample_count AS BIGINT) >= 30
ORDER BY unified_probabil_edge_fit_score DESC, unified_edge_highlight_rank
LIMIT 25;
```

Tight CI only (higher statistical precision, fewer names):

```sql
SELECT symbol, bare_ticker, industry,
       lane_leader_median_fwd_5d_ci_low,
       lane_leader_median_fwd_5d_ci_high,
       lane_leader_median_fwd_5d_ci_width,
       lane_leader_median_fwd_5d_bootstrap_sample_count
FROM symbol_unified_highlights
WHERE TRY_CAST(lane_leader_median_fwd_5d_ci_width AS DOUBLE) <= 6
  AND TRY_CAST(lane_leader_median_fwd_5d_ci_low AS DOUBLE) > 0
ORDER BY lane_leader_median_fwd_5d_ci_width ASC;
```

---

#### Scenario 9 — Industry / lane context scan (where is edge concentrated?)

**Goal:** Before picking names, confirm the industry's lane and group bootstrap support.

```sql
SELECT
    group_value AS industry,
    occurrence_count,
    sample_count_5d,
    median_fwd_5d,
    win_rate_5d,
    median_fwd_5d_ci_low,
    median_fwd_5d_ci_high,
    median_fwd_5d_ci_width,
    median_fwd_5d_bootstrap_sample_count
FROM lane_bootstrap_leaders
ORDER BY median_fwd_5d DESC NULLS LAST
LIMIT 20;
```

Compare highlights lane vs scan-edge group summary for the same industry:

```sql
SELECT
    s.symbol,
    s.bare_ticker,
    s.lane_group_value AS industry,
    s.lane_leader_median_fwd_5d,
    s.lane_leader_median_fwd_5d_ci_low,
    s.edge_group_median_fwd_5d,
    s.edge_group_median_fwd_5d_ci_low,
    s.unified_edge_highlight_rank
FROM symbol_unified_highlights AS s
WHERE s.lane_group_value = 'Semiconductors'  -- change industry
ORDER BY s.unified_edge_highlight_rank;
```

---

#### Scenario 10 — Cross-outlook agreement scan (multi-lens conviction)

**Goal:** Names strong on **multiple independent** outlooks, not just one lens.

```sql
SELECT
    symbol,
    bare_ticker,
    unified_consensus_score,
    outlook_edge_big_mover_score,
    outlook_edge_confidence_score,
    outlook_upside_prediction_score,
    outlook_forward_valuation_score,
    outlook_tradeable_safety_score,
    outlook_safety_companion_score,
    unified_best_outlook_name
FROM symbol_unified_highlights
WHERE outlook_edge_big_mover_score >= 0.60
  AND outlook_upside_prediction_score >= 0.60
  AND outlook_forward_valuation_score >= 0.50
  AND outlook_safety_companion_score >= 0.55
ORDER BY unified_consensus_score DESC
LIMIT 20;
```

Outlook disagreement (one lens loves it, another hates it — review manually):

```sql
SELECT symbol, bare_ticker, unified_best_outlook_name, unified_best_outlook_score,
       outlook_edge_big_mover_score, outlook_forward_valuation_score,
       outlook_safety_companion_score, safety_bucket
FROM symbol_unified_highlights
WHERE outlook_edge_big_mover_score >= 0.75
  AND outlook_safety_companion_score < 0.45
ORDER BY outlook_edge_big_mover_score DESC;
```

---

#### Scenario 11 — Risk / drawdown scan (execution realism)

**Goal:** Filter out names with historically painful adverse excursion in-setup.

```sql
SELECT
    symbol,
    bare_ticker,
    median_fwd_5d_in_setup,
    median_mae_5d_in_setup,
    median_mfe_5d_in_setup,
    target_rate_5d_in_setup,
    upside_target_before_stop_rate,
    win_rate_5d_in_setup,
    unified_edge_highlight_rank
FROM symbol_unified_highlights
WHERE median_mae_5d_in_setup > -8.0
  AND median_fwd_5d_in_setup > 0
ORDER BY upside_target_before_stop_rate DESC, median_fwd_5d_in_setup DESC
LIMIT 25;
```

---

#### Scenario 12 — Watchlist / staging scan (high potential, not top rank yet)

**Goal:** Interesting secondary names below top unified rank but strong on one dimension.

```sql
SELECT
    symbol,
    bare_ticker,
    unified_edge_highlight_rank,
    big_mover_score,
    confidence_score,
    upside_prediction_score,
    forward_valuation_upside_pct,
    safety_companion_score,
    unified_best_outlook_name
FROM symbol_unified_highlights
WHERE unified_edge_highlight_rank BETWEEN 21 AND 60
  AND (
        big_mover_score >= 0.70
     OR TRY_CAST(forward_valuation_upside_pct AS DOUBLE) >= 25
     OR upside_prediction_score >= 0.65
  )
ORDER BY unified_edge_highlight_rank;
```

---

#### Scenario 13 — Holdings overlap scan (compare to your book)

Replace the ticker list with your positions:

```sql
SELECT
    symbol,
    bare_ticker,
    unified_edge_highlight_rank,
    unified_edge_highlight_score,
    unified_best_outlook_name,
    forward_valuation_upside_pct,
    safety_bucket,
    tradeable_safety_rank
FROM symbol_unified_highlights
WHERE bare_ticker IN ('RMBS', 'CRDO', 'PGY')  -- your tickers
ORDER BY unified_edge_highlight_rank;
```

---

---

#### Scenario 13A — Symbol lookup (`EXCHANGE:TICKER`)

**Goal:** Query one or more names precisely using `symbol` (`NYSE:FDX`, `NASDAQ:RMBS`, etc.). This is supported in `symbol_unified_highlights` — the `symbol` column is the canonical key.

**Exact match (recommended)**

```sql
SELECT symbol, bare_ticker, exchange, company_name, industry,
       unified_edge_highlight_rank, forward_valuation_upside_pct, safety_bucket
FROM symbol_unified_highlights
WHERE symbol = 'NYSE:FDX';
```

**Parameterized — change one line only**

```sql
WITH params AS (
    SELECT 'NYSE:FDX' AS requested_symbol   -- EXCHANGE:TICKER
)
SELECT h.*
FROM symbol_unified_highlights h
CROSS JOIN params p
WHERE UPPER(TRIM(h.symbol)) = UPPER(TRIM(p.requested_symbol));
```

**Several precise symbols**

```sql
SELECT symbol, bare_ticker, unified_edge_highlight_rank, tradeable_safety_rank,
       forward_valuation_upside_pct, safety_bucket
FROM symbol_unified_highlights
WHERE symbol IN ('NYSE:FDX', 'NASDAQ:RMBS', 'NYSE:ASAN')
ORDER BY unified_edge_highlight_rank;
```

**Check whether a bare ticker is ambiguous before querying**

```sql
SELECT bare_ticker, COUNT(*) AS match_count, LIST(symbol ORDER BY symbol) AS symbols
FROM symbol_unified_highlights
WHERE UPPER(bare_ticker) = 'FDX'
GROUP BY 1;
```

- `match_count = 0` → not on the shortlist for this run
- `match_count = 1` → `bare_ticker` is safe to use
- `match_count > 1` → use `symbol` with exchange prefix

**Find candidates when you only know the ticker**

```sql
SELECT symbol, bare_ticker, exchange, company_name, industry
FROM symbol_unified_highlights
WHERE UPPER(bare_ticker) = 'FDX'
   OR UPPER(symbol) LIKE '%:FDX'
ORDER BY exchange, symbol;
```

**Build `symbol` from separate exchange + ticker inputs**

```sql
WITH params AS (
    SELECT 'NYSE' AS exchange, 'FDX' AS bare_ticker
)
SELECT h.*
FROM symbol_unified_highlights h
CROSS JOIN params p
WHERE UPPER(TRIM(h.symbol)) = UPPER(TRIM(p.exchange)) || ':' || UPPER(TRIM(p.bare_ticker));
```

---

#### Scenario 13B — Single ticker scan (full rank tape)

**Goal:** Every rank (and paired score) available in the unified DB for one symbol, if the name is on the shortlist. Forward valuation % estimates are appended at the end of each row (same values on every lens — pulled once from the symbol row).

**Use `symbol = 'EXCHANGE:TICKER'` for precision** (e.g. `NYSE:FDX`). Use `bare_ticker` only when you have confirmed a single match (see Scenario 13A).

**Wide tape (all ranks on one row)**

```sql
SELECT
    symbol,
    bare_ticker,
    company_name,
    industry,
    current_lane_flag,
    safety_bucket,
    forward_upside_mode,
    forward_company_style,
    -- unified master rank
    unified_edge_highlight_rank,
    unified_edge_highlight_score,
    unified_best_outlook_name,
    unified_best_outlook_score,
    unified_consensus_score,
    unified_probabil_edge_fit_score,
    unified_best_across_outlooks_flag,
    -- edge / tactical ranks
    big_mover_rank,
    big_mover_score,
    confidence_rank,
    confidence_score,
    composite_score,
    stability_score,
    -- upside prediction rank
    upside_prediction_rank,
    upside_prediction_score,
    upside_hist_signal_blend,
    upside_target_before_stop_rate,
    -- forward valuation rank
    forward_upside_rank,
    forward_upside_score,
    TRY_CAST(forward_valuation_upside_pct AS DOUBLE) AS forward_valuation_upside_pct,
    TRY_CAST(forward_rank_horizon_valuation_upside_pct AS DOUBLE) AS forward_rank_horizon_valuation_upside_pct,
    TRY_CAST(forward_near_term_base_upside_pct AS DOUBLE) AS forward_near_term_base_upside_pct,
    TRY_CAST(forward_medium_term_base_upside_pct AS DOUBLE) AS forward_medium_term_base_upside_pct,
    TRY_CAST(forward_long_term_base_upside_pct AS DOUBLE) AS forward_long_term_base_upside_pct,
    TRY_CAST(forward_valuation_bear_upside_pct AS DOUBLE) AS forward_valuation_bear_upside_pct,
    TRY_CAST(forward_valuation_bull_upside_pct AS DOUBLE) AS forward_valuation_bull_upside_pct,
    forward_selected_lenses,
    -- safety / tradeable ranks
    tradeable_safety_rank,
    tradeable_safety_blend_score,
    tradeable_core_score,
    safety_rank_within_tradeables,
    risk_rank_within_tradeables,
    safety_rank_global,
    safety_rank,
    safety_detail_safety_rank,
    safety_companion_score,
    balance_sheet_safety_score,
    cash_generation_value_score,
    indicator_pass_count,
    safety_shortlist_flag,
    safety_data_available,
    -- normalized outlook scores (0-1; derived from ranks where applicable)
    outlook_edge_big_mover_score,
    outlook_edge_confidence_score,
    outlook_upside_prediction_score,
    outlook_forward_valuation_score,
    outlook_tradeable_safety_score,
    outlook_safety_companion_score,
    -- liquidity participation (percentile ranks on tape, not integer ranks)
    relvol_pct_today,
    liq_core_pct_today,
    value_traded_pct_today,
    adrp_pct_today,
    vol_core_pct_today
FROM symbol_unified_highlights
WHERE symbol = 'NYSE:FDX';   -- precise: EXCHANGE:TICKER
```

**Vertical tape — single ticker scan (rank + score per lens)**

Use `TRY_CAST(... AS INTEGER)` on each rank so DuckDB keeps integer ranks and empty CSV cells become `NULL`. Filter with `WHERE rank IS NOT NULL` only — do **not** use `rank <> ''` (that triggers `Could not convert string '' to INT64`).

Set **one** lookup in `params`:
- `requested_bare_ticker` — e.g. `'PATH'` (default below; works when unambiguous)
- `requested_symbol` — e.g. `'NYSE:PATH'` for `EXCHANGE:TICKER` precision (set `requested_bare_ticker` to `NULL`)

> **Tested:** default `bare_ticker = 'PGY'` returns 9 rows on a standard shortlist run. The old `NYSE:FDX` example returned 0 rows because FDX was not on that shortlist.

```sql
WITH params AS (
    SELECT
        CAST(NULL AS VARCHAR) AS requested_symbol,   -- e.g. 'NYSE:PATH'
        'PGY' AS requested_bare_ticker               -- e.g. 'PATH' when symbol is NULL
),
base AS (
    SELECT
        h.*,
        (SELECT COUNT(*)::INTEGER FROM symbol_unified_highlights) AS shortlist_n
    FROM symbol_unified_highlights h
    CROSS JOIN params p
    WHERE (
        NULLIF(TRIM(CAST(p.requested_symbol AS VARCHAR)), '') IS NOT NULL
        AND UPPER(TRIM(CAST(h.symbol AS VARCHAR))) = UPPER(TRIM(CAST(p.requested_symbol AS VARCHAR)))
    ) OR (
        NULLIF(TRIM(CAST(p.requested_symbol AS VARCHAR)), '') IS NULL
        AND NULLIF(TRIM(CAST(p.requested_bare_ticker AS VARCHAR)), '') IS NOT NULL
        AND UPPER(TRIM(CAST(h.bare_ticker AS VARCHAR))) = UPPER(TRIM(CAST(p.requested_bare_ticker AS VARCHAR)))
    )
),
tape AS (
    SELECT 1 AS sort_order, 'unified_highlight' AS lens,
           TRY_CAST(unified_edge_highlight_rank AS INTEGER) AS rank,
           TRY_CAST(unified_edge_highlight_rank AS INTEGER) || '/' || shortlist_n AS rank_label,
           TRY_CAST(unified_edge_highlight_score AS DOUBLE) AS score,
           unified_best_outlook_name AS detail
    FROM base
    UNION ALL SELECT 2, 'big_mover',
           TRY_CAST(big_mover_rank AS INTEGER),
           TRY_CAST(big_mover_rank AS INTEGER) || '/' || shortlist_n,
           TRY_CAST(big_mover_score AS DOUBLE), NULL FROM base
    UNION ALL SELECT 3, 'confidence',
           TRY_CAST(confidence_rank AS INTEGER),
           TRY_CAST(confidence_rank AS INTEGER) || '/' || shortlist_n,
           TRY_CAST(confidence_score AS DOUBLE), NULL FROM base
    UNION ALL SELECT 4, 'upside_prediction',
           TRY_CAST(upside_prediction_rank AS INTEGER),
           TRY_CAST(upside_prediction_rank AS INTEGER) || '/' || shortlist_n,
           TRY_CAST(upside_prediction_score AS DOUBLE), NULL FROM base
    UNION ALL SELECT 5, 'forward_upside',
           TRY_CAST(forward_upside_rank AS INTEGER),
           TRY_CAST(forward_upside_rank AS INTEGER) || '/' || shortlist_n,
           TRY_CAST(forward_upside_score AS DOUBLE),
           forward_upside_mode
               || COALESCE(' | ' || NULLIF(TRIM(CAST(forward_selected_lenses AS VARCHAR)), ''), '')
    FROM base
    UNION ALL SELECT 6, 'tradeable_safety',
           TRY_CAST(tradeable_safety_rank AS INTEGER),
           TRY_CAST(tradeable_safety_rank AS INTEGER) || '/' || shortlist_n,
           TRY_CAST(tradeable_safety_blend_score AS DOUBLE), NULL FROM base
    UNION ALL SELECT 7, 'safety_within_tradeables',
           TRY_CAST(safety_rank_within_tradeables AS INTEGER),
           TRY_CAST(safety_rank_within_tradeables AS INTEGER) || '/' || shortlist_n,
           TRY_CAST(safety_companion_score AS DOUBLE), safety_bucket FROM base
    UNION ALL SELECT 8, 'risk_within_tradeables',
           TRY_CAST(risk_rank_within_tradeables AS INTEGER),
           TRY_CAST(risk_rank_within_tradeables AS INTEGER) || '/' || shortlist_n,
           NULL, 'lower = riskier' FROM base
    UNION ALL SELECT 9, 'safety_global',
           TRY_CAST(safety_rank_global AS INTEGER),
           CAST(TRY_CAST(safety_rank_global AS INTEGER) AS VARCHAR),
           TRY_CAST(safety_companion_score AS DOUBLE), 'full safety universe' FROM base
)
SELECT
    tape.sort_order,
    tape.lens,
    tape.rank,
    tape.rank_label,
    ROUND(tape.score, 4) AS score,
    tape.detail,
    ROUND(TRY_CAST(base.forward_valuation_upside_pct AS DOUBLE), 2) AS valuation_base_upside_pct,
    ROUND(TRY_CAST(base.forward_rank_horizon_valuation_upside_pct AS DOUBLE), 2) AS rank_horizon_upside_pct,
    ROUND(TRY_CAST(base.forward_near_term_base_upside_pct AS DOUBLE), 2) AS near_term_base_upside_pct,
    ROUND(TRY_CAST(base.forward_medium_term_base_upside_pct AS DOUBLE), 2) AS medium_term_base_upside_pct,
    ROUND(TRY_CAST(base.forward_long_term_base_upside_pct AS DOUBLE), 2) AS long_term_base_upside_pct,
    ROUND(TRY_CAST(base.forward_valuation_bear_upside_pct AS DOUBLE), 2) AS valuation_bear_upside_pct,
    ROUND(TRY_CAST(base.forward_valuation_bull_upside_pct AS DOUBLE), 2) AS valuation_bull_upside_pct
FROM tape
CROSS JOIN base
WHERE tape.rank IS NOT NULL
ORDER BY tape.sort_order;
```

**Switch to precise symbol lookup** (UiPath example):

```sql
WITH params AS (
    SELECT 'NYSE:PATH' AS requested_symbol, CAST(NULL AS VARCHAR) AS requested_bare_ticker
),
-- keep base / tape / final SELECT identical to query above
```

**Switch to bare ticker** (your working pattern — equivalent when unambiguous):

```sql
WITH base AS (
    SELECT
        h.*,
        (SELECT COUNT(*)::INTEGER FROM symbol_unified_highlights) AS shortlist_n
    FROM symbol_unified_highlights h
    WHERE bare_ticker = 'PATH'
),
-- keep tape / final SELECT identical to query above
```

**Zero rows?** Run Scenario 13A ambiguity check — wrong exchange (`NASDAQ:PATH` vs `NYSE:PATH`) or ticker not on shortlist.

**Forward % columns (appended at end of each row)**

| Column | Source | Meaning |
|---|---|---|
| `valuation_base_upside_pct` | `forward_valuation_upside_pct` | Headline base-case fair-value upside % (55% long / 25% med / 20% near) |
| `rank_horizon_upside_pct` | `forward_rank_horizon_valuation_upside_pct` | Base-case upside at suite ranking horizon (default `5d` → near-term) |
| `near_term_base_upside_pct` | `forward_near_term_base_upside_pct` | Near-term horizon base-case % |
| `medium_term_base_upside_pct` | `forward_medium_term_base_upside_pct` | Medium-term horizon base-case % |
| `long_term_base_upside_pct` | `forward_long_term_base_upside_pct` | Long-term horizon base-case % |
| `valuation_bear_upside_pct` | `forward_valuation_bear_upside_pct` | Bear-case valuation upside % |
| `valuation_bull_upside_pct` | `forward_valuation_bull_upside_pct` | Bull-case valuation upside % |

Example output for one name:

| lens | rank | rank_label | score | detail | valuation_base_upside_pct | rank_horizon_upside_pct |
|---|---:|---|---:|---|---:|---:|
| unified_highlight | 64 | 64/114 | 0.4943 | safety_companion | 42.5 | 38.2 |
| forward_upside | 106 | 106/114 | 0.3010 | valuation \| trajectory\|… | 42.5 | 38.2 |
| tradeable_safety | 4 | 4/114 | 0.6276 | | 42.5 | 38.2 |

`rank_label` is `rank/shortlist_n` for shortlist-based lenses; `safety_global` uses the raw global rank (different universe).

**Several tickers at once (ranks only)**

```sql
SELECT
    bare_ticker,
    symbol,
    unified_edge_highlight_rank,
    big_mover_rank,
    confidence_rank,
    upside_prediction_rank,
    forward_upside_rank,
    tradeable_safety_rank,
    safety_rank_within_tradeables,
    risk_rank_within_tradeables,
    safety_rank_global,
    safety_bucket,
    forward_valuation_upside_pct,
    forward_rank_horizon_valuation_upside_pct
FROM symbol_unified_highlights
WHERE symbol IN ('NYSE:FDX', 'NASDAQ:RMBS', 'NASDAQ:PGY')
ORDER BY unified_edge_highlight_rank;
```

> If the query returns **zero rows**, the ticker is not on `edge_name_shortlist.csv` for that run (not in the unified DB). Query upstream artifacts (`edge_screen_ranked.csv`, `safety_scored.csv`) or re-run the suite.

---

#### Scenario 14 — Full forensic row (one symbol deep dive)

```sql
SELECT *
FROM symbol_unified_highlights
WHERE symbol = 'NASDAQ:RMBS';
```

For safety indicator flags only:

```sql
SELECT symbol, bare_ticker,
       safety_detail_indicator_pass_count,
       safety_detail_indicator_net_cash,
       safety_detail_indicator_altman_safe,
       safety_detail_indicator_fcf_margin_positive,
       safety_detail_indicator_debt_to_equity_low,
       safety_bucket
FROM symbol_unified_highlights
WHERE symbol = 'NASDAQ:RMBS';   -- use EXCHANGE:TICKER for precision
```

### 12E) Suggested daily workflow using only the unified DB

1. Run **Scenario 1** for the master action list.
2. Run **Scenario 8** to confirm bootstrap lane support on those names.
3. Run **Scenario 6** or **Scenario 7** if you are sizing with safety constraints.
4. Run **Scenario 5** when you need explicit valuation upside % for position sizing narratives.
5. Run **Scenario 10** disagreement query on any name you plan to size aggressively.
6. Use **Scenario 9** when a name's industry looks weak — that is often a reason to downgrade conviction even if the symbol scores highly.

### 12F) Interpreting unified ranks vs other ranks

| Rank column | Optimizes for |
|---|---|
| `unified_edge_highlight_rank` | Best overall across outlooks + probabilistic lane fit |
| `big_mover_rank` | Current tactical expansion state |
| `confidence_rank` | Durability + stability + historical confirmation |
| `upside_prediction_rank` | Historical tape + setup upward-move prediction |
| `forward_upside_rank` | Forward valuation lens (or fallback tape signal) |
| `tradeable_safety_rank` | Tradeable core blended with safety companion |
| `safety_rank_within_tradeables` | Safest names inside the shortlist only |
| `risk_rank_within_tradeables` | Highest-risk names inside the shortlist (lower = riskier) |
| `safety_rank_global` / `safety_detail_safety_rank` | Rank in full safety scored universe |

`unified_best_across_outlooks_flag = 1` marks the top-30 unified highlights. It is broader than `edge_name_top30.csv` (big-mover sort) and different from `edge_name_top<N>_confidence.csv`.
