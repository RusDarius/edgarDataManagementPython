# Portfolio Management Flow Guide

## Goal

This flow wires TradingView company loading, dated portfolio entries, market snapshot refresh, and holding-period performance reporting into one clean path.

## What Changed

- `load_tradingview_company_data` now exists as a database script and loads the mapped rows returned by `ApiTradingViewClient.scan_world_market_all_priceperf_metrics` into `trading_view_company_data_map`.
- Portfolio positions now preserve the effective `opened_at` date from the first dated buy you submit.
- Portfolio transactions now store the original input shape for money-sized entries:
  - `quantity_value`
  - `quantity_unit`
  - `amount_currency`
  - `fx_rate_to_portfolio`
- Position metrics now expose holding-period fields:
  - `holding_days`
  - `annualized_return_pct`
- Portfolio summaries now expose period-aware aggregates:
  - `average_holding_days`
  - `weighted_holding_days`
  - `oldest_position_days`
  - `annualized_total_return_pct`

## Loader Flow

Use the TradingView loader first so the symbol universe is present in `trading_view_company_data_map` and each company has an `internal_id`.

```python
from database_scripts.insert_trading_view_company_data import load_tradingview_company_data

load_result = load_tradingview_company_data(
    api_client=TRADINGVIEW_API_CLIENT,
    markets=PREFERRED_MARKETS,
)
print(load_result)
```

This uses `scan_world_market_all_priceperf_metrics`, normalizes the mapped rows, and upserts them into the company map table.

## Dated Entry API

For share-based entries, the old method still works:

```python
tracker.add_holding(
    internal_id=1,
    shares=10,
    price=120.00,
    executed_at=datetime(2025, 12, 1, 14, 30),
)
```

For money-sized entries with a holding start date, use:

```python
tracker.add_holding_by_amount(
    internal_id=1,
    avg_price=120.00,
    date_open=datetime(2025, 12, 1, 14, 30),
    amount_money=6_000.00,
    unit="USD",
    fx_rate_to_portfolio=None,
)
```

Semantics:

- `avg_price` and `amount_money` are assumed to be expressed in the same input unit.
- `unit` is the currency code for the money input.
- If `unit` differs from the portfolio currency, pass `fx_rate_to_portfolio`.
- Shares are derived as `amount_money / avg_price`.
- The stored position cost basis is kept in the portfolio currency.

## FX Placeholder Design

There is no external FX endpoint wired yet.

Current behavior:

- If `unit == portfolio currency`, the FX rate is treated as `1.0`.
- If `unit != portfolio currency`, `fx_rate_to_portfolio` is required.

This is the extension point for a later FX resolver or API-backed conversion layer. The clean future replacement is:

```python
resolved_fx_rate = fx_service.get_rate(
    from_currency=unit,
    to_currency=tracker.portfolio_currency,
    at_datetime=date_open,
)
```

## Measuring Performance Over Time

After positions are opened, refresh market data and compute metrics.

```python
scan_data = TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    min_market_cap_usd=1_000_000_000,
    markets=PREFERRED_MARKETS,
).get("data", [])

tracker.sync_market_snapshots(scan_data, symbol_key="ticker-view")
summary, metrics = tracker.get_summary()
tracker.print_summary()
export_paths = export_portfolio_analysis(tracker)
```

The snapshot sync now tolerates both `ticker-view` and raw TradingView `symbol` formats like `NASDAQ:AAPL`.

## Example Flow In `main.py`

`main.py` now includes `run_portfolio_bootstrap_example()`.

It performs these steps:

1. Load TradingView company data into the DB.
2. Create a demo portfolio.
3. Resolve internal IDs by symbol.
4. Add dated, money-sized sample positions.
5. Refresh latest market snapshots from the move-prediction scan.
6. Print portfolio metrics.
7. Export the analysis log and CSV.

The function is intentionally not executed by default because it writes to your DB and calls external APIs.

## Output

Portfolio analysis exports now include holding-period fields in both the log and CSV.

- Log: annualized total return, average holding days, weighted holding days, oldest position days
- CSV: `opened_at`, `holding_days`, `annualized_return_pct`, `entry_currency`, `entry_fx_to_portfolio`

## Recommended Next Step

When you wire an FX endpoint later, keep the API boundary inside `PortfolioTracker.add_holding_by_amount` or a dedicated portfolio FX service so the DB layer remains currency-agnostic except for persisted values.