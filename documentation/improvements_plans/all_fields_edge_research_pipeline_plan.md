# All-Fields Edge Research Pipeline Plan

## Goal

Build a new all-fields-only edge research pipeline that can scan reusable daily TradingView all-fields datasets, create time-respecting symbol-day research tables, and test interpretable entry and exit templates with explicit risk metrics.

This pipeline should not depend on the existing scoring models as its core signal source. It can compare against them later, but the main objective is to discover whether the all-fields daily dataset itself contains repeatable, risk-aware setups.

## Package Boundary

- Create a new sibling package under `src/` named `edge_research_tools`.
- Keep `src/run_edge_research_tools.py` as the only manual execution entrypoint.
- Keep the entrypoint thin: resolve config, locate reusable sources, dispatch to package runners, and print output paths.
- Keep all real logic inside `src/edge_research_tools/`.

## Design Principles

1. One row in the core research table should mean one symbol on one measurement day.
2. Every label must be future-only relative to that measurement day.
3. Every setup must be interpretable and decomposable into modular rules.
4. Entry research and exit research should be separate layers.
5. Validation should focus on expectancy and drawdown behavior, not only raw return.
6. Reuse existing repository tools wherever possible instead of duplicating loaders or root discovery logic.

## Phase 1: Foundation

1. Create `src/edge_research_tools/` with a minimal reusable structure.
2. Add a source-discovery layer that can locate:
   - daily all-fields DuckDB exports
   - scan-period tracking runs
   - taxonomy artifacts
   - candidate edge-research reuse artifacts
3. Add a run-context layer for output-root creation under:
   - `logs/tradingview_analysis/edge_research_tools/runs/<run_id>/`
4. Implement an inventory runner first so the package is immediately usable and validates root scanning before deeper feature work begins.

## Phase 2: Symbol-Day Research Dataset

1. Build a symbol-day feature snapshot layer from the daily all-fields DuckDB exports.
2. Reuse the existing daily database discovery helpers rather than hardcoding file locations.
3. Preserve source provenance on each row:
   - day label
   - run id
   - source database path
   - symbol
   - bare ticker
4. Add configurable universe filters:
   - min market cap
   - min liquidity
   - stock-only
   - primary-only
   - sector / industry restriction
   - exchange inclusion
5. Compute same-day relative state metrics:
   - percentile rank
   - universe z-score
   - same-day medians / averages where relevant

## Phase 3: Feature Engineering Sleeves

Start with explicit sleeves instead of searching all fields blindly.

1. Volatility / expansion sleeve
   - ATRP
   - ADRP
   - realized volatility fields
   - compression-to-expansion transitions
2. Liquidity / activity sleeve
   - volume
   - average volume
   - relative volume
   - value traded style fields
3. Trend / momentum sleeve
   - trailing performance windows
   - moving-average recommendation state
   - multi-window percentile shifts
4. Mean-reversion / exhaustion sleeve
   - oversold / overbought state fields
   - drawdown-like state proxies
   - reversal transition conditions
5. Quality / operating-strength overlay sleeve
   - only as a context overlay, not a standalone execution trigger
   - helps decide whether a high-volatility setup is acceptable or too fragile

For each sleeve, test both:

- level features
- delta features
- percentile shifts
- z-score shifts
- multi-day transitions

## Phase 4: Forward Labels

The new pipeline should not rely on hindsight ladder labels as its core research target.

Start with forward labels that are actually usable for execution research:

1. next 3-day close return
2. next 5-day close return
3. next 10-day close return
4. next 20-day close return
5. maximum favorable excursion before day N
6. maximum adverse excursion before day N
7. hit +X% before -Y%
8. hit stop before target
9. days to target
10. days to stop

These labels should be parameterized so the same feature snapshot table can be reused with multiple risk templates.

## Phase 5: Setup Research Engine

Build an interpretable setup engine on top of the symbol-day dataset.

Each setup should be a named template composed from a small number of explicit conditions.

Examples:

1. Volatility expansion after compression
2. High relative volume plus trend improvement
3. Extreme ADRP state with controlled drawdown context
4. Mean-reversion exhaustion followed by improving liquidity
5. Single-name recurrent setup templates

Each setup should support separate exit research:

1. fixed target / fixed stop
2. time stop
3. trailing stop
4. deterioration exit from state variables
5. hybrid target + time stop

## Phase 6: Validation

Every setup report should include:

1. sample count
2. coverage rate
3. median return
4. win rate
5. target-before-stop rate
6. stop-before-target rate
7. median MAE
8. median MFE
9. time-in-trade distribution
10. stability across walk-forward slices
11. stability across regime / sector / liquidity buckets

Promotion criteria should prioritize stable expectancy and tolerable risk behavior, not only the highest raw return.

## Phase 7: Single-Name Research Lane

Add a dedicated single-name lane using the same feature and label store.

Purpose:

1. identify recurring pre-move states for one symbol
2. compare which sleeves matter for that symbol
3. measure which hold durations and exits fit that symbol best
4. build name-specific but still explainable playbooks

This lane should reuse the same daily feature logic as the cross-sectional engine so results stay comparable.

## Proposed Package Layout

```text
src/
  edge_research_tools/
    __init__.py
    config.py
    source_inventory.py
    dataset_builder.py
    labeling.py
    sleeves.py
    setup_engine.py
    validation.py
    single_name.py
    reporting.py
  run_edge_research_tools.py
```

Suggested implementation order:

1. `config.py`
2. `source_inventory.py`
3. `dataset_builder.py`
4. `labeling.py`
5. `sleeves.py`
6. `setup_engine.py`
7. `validation.py`
8. `single_name.py`
9. `reporting.py`

## Reuse Targets

Primary reuse anchors:

1. `src/data_analysis_scripts/trading_view_all_fields_metric_pattern_analyzer.py`
2. `src/data_analysis_scripts/trading_view_ticker_field_pattern_scan.py`
3. `src/data_analysis_scripts/trading_view_all_fields_upside_edge_research.py`
4. `src/data_analysis_scripts/trading_view_field_taxonomy_builder.py`
5. `src/db/trading_view_all_fields_duckdb.py`

The new package should scan for these sources and reuse them regardless of where the data folders live, as long as a valid root is provided.

## First Deliverables

1. Source inventory runner
2. Symbol-day dataset builder for a short date range
3. Forward-label builder for one small set of horizons
4. One sleeve end-to-end research pass
5. One single-name exploratory report

## Verification Checklist

1. Confirm the new package can discover daily all-fields DuckDB files from a configurable root.
2. Confirm the new package can discover scan-period tracking runs and taxonomy artifacts.
3. Confirm symbol-day rows are deduplicated consistently.
4. Confirm forward labels only use future data.
5. Confirm one sleeve can be evaluated on a walk-forward split.
6. Confirm one single-name report matches raw daily DuckDB values for a few spot-check rows.

## Scope Boundaries

Included:

1. all-fields daily data
2. taxonomy-based filtering
3. symbol-day feature engineering
4. forward labels
5. setup research
6. exit research
7. single-name studies

Excluded from first core implementation:

1. move-prediction scoring models
2. backwards profile databases
3. profile-rank execution logic
4. portfolio allocation logic
5. fully automated production trade execution

## Immediate Next Step

Start with Phase 1 only:

1. create the package
2. implement source inventory
3. validate root scanning and output layout
4. then move to the symbol-day dataset builder
