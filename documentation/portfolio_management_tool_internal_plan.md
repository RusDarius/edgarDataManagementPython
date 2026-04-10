# Portfolio Management Tool Internal Plan

## Goal

Create a persistent portfolio management layer that can track holdings, realized and unrealized performance, planning fields, and reusable analysis outputs across the TradingView- and EDGAR-based workflows already in this repository.

## Persisted Data Model

### 1. Portfolio registry

Store one row per portfolio with:

- `portfolio_id`
- `portfolio_name`
- `description`
- `currency`
- `benchmark_symbol`
- `notes`
- `created_at`
- `updated_at`

Purpose: clean portfolio identity, reporting scope, benchmark linkage, and future scheduling.

### 2. Persistent position state

Use `portfolio_managements_data_v1` as the durable per-entry state table.

Core tracking fields:

- `portfolio_item_id`
- `portfolio_id`
- `company_internal_id`
- `status` (`ACTIVE`, `CLOSED`, later `WATCHLIST` if needed)
- `shares_held`
- `average_entry_price`
- `cost_basis_total`
- `realized_pnl`
- `fees_total`

Snapshot and mark-to-market fields:

- `last_price`
- `last_price_at`
- `last_market_cap_basic`
- `last_change_pct`
- `last_perf_w`
- `last_perf_1m`
- `last_perf_y`

Portfolio planning fields:

- `target_weight_pct`
- `risk_limit_pct`
- `target_price`
- `stop_loss_price`
- `thesis`
- `notes`
- `opened_at`
- `closed_at`
- `created_at`
- `updated_at`

Purpose: one durable row per portfolio-position combination that remains stable even as transactions accumulate.

### 3. Transaction log

Use `portfolio_transactions` for the immutable audit trail.

Required fields:

- `portfolio_transaction_id`
- `portfolio_id`
- `portfolio_item_id`
- `company_internal_id`
- `transaction_type`
- `shares_delta`
- `price`
- `fees`
- `cash_flow`
- `note`
- `external_reference`
- `executed_at`
- `created_at`

Purpose: reproducibility, reconciliation, realized PnL computation, and later tax-lot expansion.

## Required Portfolio Metrics

### Position metrics

Each row exported to CSV should carry:

- shares held
- average entry price
- cost basis total
- current market value when price is available
- unrealized PnL
- unrealized return percent
- realized PnL
- total PnL
- total return percent
- weight by market value
- weight by cost basis
- latest daily move percent
- latest 1W / 1M / 1Y performance snapshots
- exchange / market / currency metadata

### Portfolio summary metrics

Each log should at minimum include:

- position count
- priced position count
- price coverage ratio
- total cost basis
- total market value
- total unrealized PnL
- total realized PnL
- total combined PnL
- total return percent
- weighted daily move percent
- weighted 1W / 1M / 1Y performance
- largest position weight
- HHI concentration score

### Near-term useful additions

These are not yet fully implemented in persistence but should be supported next:

- exposure by market
- exposure by exchange
- exposure by currency
- exposure by market-cap bucket
- realized win rate
- average winner / average loser
- turnover percent
- days held per position
- target weight gap vs actual weight
- stop-distance risk per position

## Integration Points With Existing Analysis Tools

### TradingView move prediction analysis

Use results to update:

- `last_price`
- performance snapshots
- optional ranking metadata in a later enrichment table

Potential derived portfolio overlays:

- weighted momentum score
- weighted quality score
- weighted valuation score
- weighted safety score
- concentration in high-scoring names

### TradingView targets analysis

Use target outputs to compare current holdings against:

- base target upside
- bear/base/bull spread width
- overvaluation or undervaluation flags
- mismatch between position size and target attractiveness

Future output idea:

- portfolio aggregate upside/downside map based on position weights times target spreads.

### TradingView safety scan

Use to flag:

- leverage deterioration
- balance-sheet risk drift
- names requiring stop tightening or position-size reduction

### Valuation analysis

Use to identify:

- positions that remain justified despite premium multiples
- positions that should be trimmed due to multiple expansion without matching operating improvement

### Sherwood / news-driven workflows

Use to annotate:

- catalyst-linked transactions
- news event tags on position reviews
- event-risk watchlists per holding

## Output Plan

### Log output location

Write human-readable portfolio status logs under:

`logs/portfolio_analysis/<portfolio_slug>/<dd_mm_yyyy>/portfolio_status_<hhmmss>.log`

### CSV output location

Write per-entry metric rows under the same folder:

`logs/portfolio_analysis/<portfolio_slug>/<dd_mm_yyyy>/portfolio_positions_metrics_<hhmmss>.csv`

### Log sections

Recommended structure:

1. summary block
2. pricing coverage block
3. positions table
4. concentration and risk notes
5. optional analysis-tool overlays when available

## Operating Principles

- Keep SQL isolated under `src/db/`.
- Keep arithmetic and aggregation pure under `src/portofolio_integration_analysis/portfolio_metrics.py`.
- Keep orchestration under `src/portofolio_integration_analysis/portfolio_tracker.py`.
- Keep file output under `src/portofolio_integration_analysis/portfolio_analysis_output.py`.
- Avoid embedding analysis math directly into DB update functions.

## Next Practical Extensions

1. Add exposure aggregation by market / exchange / currency.
2. Add optional benchmark-relative return tracking at the portfolio level.
3. Add persistent enrichment snapshots for move-prediction and targets analysis results.
4. Add review workflow fields such as conviction tier, thesis status, and next review date.
5. Add tax-lot support if FIFO / average-cost distinctions become important.