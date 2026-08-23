# Portfolio Performance Tracking

Flow-aware historical performance tracking for a portfolio: handles money added
at different times (initial funding + later top-ups), realized gains and
dividends, and benchmarks your percentage performance against an index/ETF
using a matched cash-flow simulation. This is a companion to
[`portofolio_integration_analysis`](../portofolio_integration_analysis/), not a
replacement for it:

| | `portofolio_integration_analysis` (existing) | `portfolio_performance_tracking` (this folder) |
|---|---|---|
| Question answered | "What do I hold right now and what's it worth?" | "How has my money actually performed over time?" |
| Shape | Live position snapshot (shares, cost basis, unrealized PnL) | Time series (NAV snapshots + cash flows -> % return curve) |
| Handles irregular funding dates | No | Yes (Modified Dietz) |
| Benchmark comparison | No | Yes (shadow benchmark portfolio) |

Both share the same `portfolio_registry` row (same `portfolio_id`/`portfolio_name`),
so you use `PortfolioTracker` for day-to-day holdings management and
`PortfolioPerformanceTracker` to log the history that builds the performance curve.

Same relationship applies to `run_holdings_scoring_analysis`: keep using it
as-is for point-in-time scoring/shortlisting of current holdings. This module
is for the orthogonal concern of running, historical, flow-adjusted return
tracking.

## Where data lives

- **Database (source of truth):** MySQL, `finance_manager_iteration_1`
  (`src/db/connection_credentials.py`), tables `portfolio_cash_flows` and
  `portfolio_nav_snapshots` -- see [DB schema](#db-schema) below. Every
  `record_*`/`take_nav_snapshot*`/`record_fund_value` call writes here; nothing
  in this module is in-memory only.
- **Exported reports (derived, regenerable at any time):** calling
  `export_performance_history(tracker)` writes to
  `logs/portfolio_performance_tracking/<slugified_portfolio_name>/<DD_MM_YYYY>/`:
  `performance_history_<HHMMSS>.log` (human-readable), plus
  `performance_history_<HHMMSS>.csv`, `benchmark_comparison_<HHMMSS>.csv`, and
  `active_capital_breakdown_<HHMMSS>.csv`. These files are just a formatted
  view of the DB rows -- deleting them loses nothing; re-run the export to
  regenerate.

## Two modes: Holdings-integrated vs. Simple

Recording every buy/sell/price update name-by-name is accurate but tedious.
Both modes below use the **exact same tables and return math** -- the only
difference is where NAV comes from.

| | Holdings-integrated mode | Simple mode |
|---|---|---|
| Setup | `PortfolioPerformanceTracker(portfolio_name=...)` | `PortfolioPerformanceTracker.create_simple_fund(fund_name=...)` |
| Track individual symbols? | Yes, via `tracker.holdings_tracker` (`PortfolioTracker`) | No |
| NAV snapshot method | `take_nav_snapshot_from_holdings()` (auto-sums live position market value) or `take_nav_snapshot(total_market_value, cash_balance)` | `record_fund_value(total_value)` -- one number, no market/cash split |
| Effort per update | Update price on every held symbol, then snapshot | Just write down what the whole fund is worth today |
| Best for | You actively manage individual positions and want per-symbol PnL too | You just want "money in vs. what it's worth now" without bookkeeping every trade |

Contributions, withdrawals, dividends, realized gains, benchmark comparison,
and the active-capital breakdown work identically in both modes -- they only
depend on `portfolio_cash_flows` + `portfolio_nav_snapshots`, never on
per-symbol data. Switching modes later (e.g. starting simple, then adopting
holdings-integrated tracking) is safe -- both just keep appending snapshots
to the same portfolio.

## Core concepts

**Cash flows** (`portfolio_cash_flows` table, via `cash_flow_ledger.py`):
- `CONTRIBUTION` / `WITHDRAWAL` -- money crossing the portfolio boundary. These
  are the only flows that affect the return calculation (Modified Dietz).
- `DIVIDEND` / `REALIZED_GAIN` -- informational only. This money is already
  reflected in NAV growth (it stays in the portfolio as cash or gets reinvested),
  so recording it does **not** change the computed return. If a dividend or a
  realized gain is paid **out** to you, also record a `WITHDRAWAL` for that amount.

**NAV snapshots** (`portfolio_nav_snapshots` table, via `nav_history.py`):
- A point-in-time reading of `total_market_value + cash_balance = total_nav`.
- You control the cadence -- daily, weekly, or whenever you refresh holdings.
  More snapshots = a smoother return curve; two snapshots is the minimum to
  get any return number at all.
- `take_nav_snapshot_from_holdings()` auto-fills `total_market_value` from the
  linked `PortfolioTracker`'s current priced positions (unpriced positions are
  excluded, same semantics as `PortfolioSummaryMetrics.total_market_value`).
  You still supply `cash_balance` yourself since uninvested cash isn't tracked
  by the holdings tracker.

**Performance calculation** (`performance_calculator.py`):
- Uses the **Modified Dietz method** for each sub-period between two
  consecutive NAV snapshots, weighting each flow by how much of the period it
  was invested for. Sub-period returns are then geometrically chained
  (compounded) into one cumulative % series. This is the standard practical
  approximation of a true time-weighted return when you only have periodic NAV
  readings rather than a daily valuation feed.
- This is precisely what solves the "flow structure" problem: a contribution
  added last week doesn't get credited/blamed for gains/losses from months ago,
  and a big top-up right before a rally doesn't inflate your real return.

**Benchmark comparison** (`benchmark_comparator.py`):
- Builds a "shadow portfolio" that receives the *same* contributions/withdrawals
  at the *same* times, but buys/sells a benchmark symbol (e.g. `SPY`) instead.
  Comparing the shadow portfolio's value to your actual NAV directly answers
  "would I have been better off just buying the benchmark with this exact
  money, on this exact schedule?" -- this avoids having to reconcile two
  different Modified Dietz calculations.
- Requires a `benchmark_price` (the benchmark's close) attached to each
  `CONTRIBUTION`/`WITHDRAWAL` flow and to each NAV snapshot you want compared.
  Use `PortfolioPerformanceTracker.resolve_benchmark_price(scan_data, "SPY")`
  to pull it out of a TradingView scan you already fetched elsewhere (most
  scans include a `close` field) instead of making an extra API call.
  Flows/snapshots without a price are simply skipped for the benchmark leg
  only -- your own portfolio return is never affected by missing benchmark data.
- The shadow value is run through the **same** `build_cumulative_performance_series`
  (Modified Dietz) logic as the real portfolio, fed the identical flows, so both
  cumulative-return numbers are computed with identical methodology and
  `alpha_pct = portfolio_cumulative_return_pct - shadow_benchmark_cumulative_return_pct`
  is a fair, apples-to-apples comparison.

**Active capital breakdown** (simple-mode-friendly reporting, `cash_flow_ledger.get_active_capital_breakdown` /
`PortfolioPerformanceTracker.get_active_capital_breakdown`):
- One row per contribution/withdrawal showing `days_active` (how long that
  money has been sitting in the fund) and `running_active_capital` (net
  external capital deployed so far, in chronological order). This directly
  answers "since when did my money go in" without needing any NAV history --
  useful even before you've logged a single fund-value snapshot.

## DB schema

Two new MySQL tables (`src/db/portfolio_performance_tracking_operations.py`),
both keyed to `portfolio_registry.portfolio_id` (the same registry row used by
`portofolio_integration_analysis.PortfolioTracker`). Neither table is touched
by the existing holdings code (`portfolio_managements_data_v1` /
`portfolio_transactions`), so adopting this module cannot corrupt existing data.

**`portfolio_cash_flows`** -- one row per money-in/money-out/income event:

| Column | Notes |
|---|---|
| `cash_flow_id` | PK, auto-increment |
| `portfolio_id` | FK -> `portfolio_registry` |
| `flow_type` | `CONTRIBUTION` \| `WITHDRAWAL` \| `DIVIDEND` \| `REALIZED_GAIN` \| `INTEREST` \| `OTHER_INCOME` |
| `amount` | Signed: positive = money in, negative = money out. `WITHDRAWAL` is stored negative automatically by `record_withdrawal`. |
| `currency` | Default `USD` |
| `fx_rate_to_portfolio` | Only required when `currency` != portfolio currency |
| `benchmark_symbol` / `benchmark_price` | Optional; needed on `CONTRIBUTION`/`WITHDRAWAL` rows for the shadow-benchmark simulation |
| `related_symbol` | Optional tag for `DIVIDEND`/`REALIZED_GAIN` (which holding it came from) |
| `note`, `executed_at`, `created_at` | Free text + the date the flow actually happened vs. when it was logged |

**`portfolio_nav_snapshots`** -- one row per point-in-time fund value reading:

| Column | Notes |
|---|---|
| `nav_snapshot_id` | PK, auto-increment |
| `portfolio_id` | FK -> `portfolio_registry` |
| `snapshot_at` | The date/time this reading represents |
| `total_market_value` | Sum of priced positions (0 in simple mode) |
| `cash_balance` | Uninvested cash (0 in simple mode -- `total_market_value` carries the whole number) |
| `total_nav` | `total_market_value + cash_balance`, computed automatically on insert |
| `benchmark_symbol` / `benchmark_price` | Optional; needed here too for the shadow-benchmark simulation |
| `note`, `created_at` | |

## Methods that alter the portfolio

**Cash-flow ledger** (`cash_flow_ledger.py`, also exposed as `PortfolioPerformanceTracker` methods):

| Method | Effect |
|---|---|
| `record_contribution(amount, ...)` | Log money added from outside the fund. Affects Modified Dietz. |
| `record_withdrawal(amount, ...)` | Log money removed from the fund (stored as a negative amount). Affects Modified Dietz. |
| `record_dividend(amount, related_symbol=None, ...)` | Log dividend income that stays in the fund. Informational only -- does not affect return. |
| `record_realized_gain(amount, related_symbol=None, ...)` | Log a realized capital gain/loss (signed) that stays in the fund. Informational only. |

**NAV / fund value** (`nav_history.py`, also exposed as `PortfolioPerformanceTracker` methods):

| Method | Effect |
|---|---|
| `take_nav_snapshot(total_market_value, cash_balance=0.0, ...)` | Holdings-integrated mode: log an explicit market-value/cash split. |
| `take_nav_snapshot_from_holdings(cash_balance=0.0, ...)` | Holdings-integrated mode: auto-computes `total_market_value` from live priced positions on `tracker.holdings_tracker`. |
| `record_fund_value(total_value, ...)` | Simple mode: log "the whole fund is worth X today" as one number (no split). |

**Deletion** (permanent):

| Method | Effect |
|---|---|
| `PortfolioPerformanceTracker.delete_fund(fund_name)` | Delete the exact-name fund plus its holdings, transactions, cash flows, and NAV snapshots. Returns `True` if deleted, `False` if not found. |
| `PortfolioPerformanceTracker.delete_simple_fund(fund_name)` | Explicit simple-mode alias for `delete_fund`. |

Deletion is database deletion, not archiving. Use the exact portfolio/fund name
and call it only when all associated history may be removed.

**Reading results** (no DB writes):

| Method | Returns |
|---|---|
| `get_performance_history()` | `list[PerformancePoint]` -- the Modified-Dietz-linked cumulative % return curve |
| `compare_to_benchmark()` | `list[BenchmarkComparisonPoint]` -- portfolio vs. shadow benchmark + alpha |
| `get_active_capital_breakdown()` | Per-flow age (`days_active`) + running net active capital |
| `get_latest_performance_summary()` | One dict with the latest cumulative/annualized return + lifetime cash-flow totals |
| `cash_flow_summary()` | Lifetime totals: contributions, withdrawals, dividends, realized gains |

**Holdings-integrated mode only** (delegates to `tracker.holdings_tracker`, the
existing `portofolio_integration_analysis.PortfolioTracker` -- see that
module's own docs for full details):

| Method | Effect |
|---|---|
| `tracker.holdings_tracker.add_holding_by_amount(...)` / `add_holding(...)` | Buy a symbol; updates cost basis/shares on that position. Does NOT itself log a `CONTRIBUTION` -- call `record_contribution` separately for the cash that funded the buy. |
| `tracker.holdings_tracker.sell_holding(...)` | Sell shares; realized PnL is computed automatically on the position. Does NOT itself log a `REALIZED_GAIN`/`WITHDRAWAL` -- call those separately if you want them reflected in this module's ledger/return calc. |
| `tracker.holdings_tracker.update_market_snapshot(internal_id, last_price=...)` | Refresh a position's current price before taking an NAV snapshot. |

## IMPORTANT — things to know before using this

1. **Two NAV snapshots minimum.** No snapshots = no history. One snapshot =
   no return yet (nothing to compare against). Take a snapshot right when you
   start using this (even if NAV = your very first contribution) and again
   whenever you want an updated read.
2. **Don't double count.** `DIVIDEND`/`REALIZED_GAIN` events are bookkeeping
   only. Never also log them as a `CONTRIBUTION` -- the cash is already inside
   your NAV. Only log a flow as `CONTRIBUTION`/`WITHDRAWAL` when money actually
   entered or left the portfolio from/to the outside world (bank transfer in,
   cash withdrawn out).
3. **`executed_at` matters a lot.** Modified Dietz weights each flow by how
   long it sat inside the period. Backdate `executed_at` to the actual transfer
   date, not "whenever you got around to logging it," or the return will be
   distorted.
4. **Benchmark comparison is opt-in and precision-dependent.** Without
   `benchmark_price` on flows/snapshots you still get your own flow-adjusted
   return, just no benchmark line. Partial benchmark pricing (some flows priced,
   some not) will silently under-simulate the shadow portfolio for the unpriced
   flows -- try to price every external flow if you want the comparison to be
   trustworthy.
5. **Currency is assumed portfolio-native unless FX is supplied**, same
   convention as `PortfolioTracker.add_holding_by_amount` (`fx_rate_to_portfolio`
   required when `currency` differs from the portfolio's own currency).
6. **This module never touches `portfolio_managements_data_v1` or
   `portfolio_transactions`** (the existing per-position holdings tables). It
   only adds two new tables (`portfolio_cash_flows`, `portfolio_nav_snapshots`)
   keyed by the same `portfolio_id`, so it's safe to adopt without touching
   existing holdings/scoring flows.
7. **Snapshot cadence vs. accuracy trade-off.** Modified Dietz linking is an
   approximation of true time-weighted return; the fewer/further-apart your NAV
   snapshots are, the more any large flow near a big market move will blur the
   sub-period return. For serious accuracy, snapshot at least around each time
   you add/remove funds (right before and right after), not just on a fixed
   weekly cadence.
8. **Buying/selling a symbol does not auto-log a cash flow.** In
   holdings-integrated mode, `tracker.holdings_tracker.add_holding_by_amount(...)`
   only changes that position's shares/cost-basis -- it does not call
   `record_contribution` for you. Always pair "money entered the fund" with an
   explicit `record_contribution`/`record_withdrawal` call, otherwise the
   Modified Dietz calculation won't know about it.

## Usage

### Holdings-integrated mode

```python
from portfolio_performance_tracking.portfolio_performance_tracker import (
    PortfolioPerformanceTracker,
)
from portfolio_performance_tracking.performance_report_output import (
    export_performance_history,
)

tracker = PortfolioPerformanceTracker(portfolio_name="TV Date Aware Demo")

# 1. Log the initial funding (money you started with).
tracker.record_contribution(
    amount=25_000.00,
    benchmark_symbol="SPY",
    benchmark_price=592.10,   # SPY close on the contribution date
    note="Initial funding",
    executed_at=datetime(2025, 10, 1),
)

# 2. Snapshot NAV right after funding (before anything is invested, cash_balance
#    covers the un-deployed cash so NAV still matches the contribution).
tracker.take_nav_snapshot(
    total_market_value=0.0,
    cash_balance=25_000.00,
    benchmark_symbol="SPY",
    benchmark_price=592.10,
    snapshot_at=datetime(2025, 10, 1),
)

# ... buy positions via tracker.holdings_tracker (the existing PortfolioTracker) ...

# 3. A later top-up.
tracker.record_contribution(
    amount=5_000.00,
    benchmark_symbol="SPY",
    benchmark_price=601.40,
    note="December top-up",
    executed_at=datetime(2025, 12, 18),
)

# 4. A dividend that stays invested (informational only, does not skew return).
tracker.record_dividend(amount=42.10, related_symbol="MSFT", note="Q4 dividend")

# 5. Periodic NAV snapshot, auto-derived from current holdings market value.
tracker.take_nav_snapshot_from_holdings(
    cash_balance=120.35,
    benchmark_symbol="SPY",
    benchmark_price=615.20,
)

# 6. Read the results / export a report.
summary = tracker.get_latest_performance_summary()
print(summary["cumulative_return_pct"], summary["annualized_return_pct"])

history = tracker.get_performance_history()          # list[PerformancePoint]
comparison = tracker.compare_to_benchmark()           # list[BenchmarkComparisonPoint]

paths = export_performance_history(tracker, benchmark_symbol="SPY")
print(paths["log"])              # human-readable report
print(paths["performance_csv"])  # NAV / return curve
print(paths["benchmark_csv"])    # portfolio vs shadow-benchmark curve + alpha
```

### Simple mode (no symbols, just fund-level flows + value updates)

```python
tracker = PortfolioPerformanceTracker.create_simple_fund(
    fund_name="My Simple Fund", currency="USD", benchmark_symbol="SPY"
)

tracker.record_contribution(
    amount=20_000.00, benchmark_symbol="SPY", benchmark_price=592.10,
    note="Initial funding", executed_at=datetime(2025, 10, 1),
)
tracker.record_fund_value(
    total_value=20_000.00, benchmark_symbol="SPY", benchmark_price=592.10,
    as_of=datetime(2025, 10, 1),
)

# ... time passes, the fund grows/shrinks ...
tracker.record_fund_value(
    total_value=21_800.00, benchmark_symbol="SPY", benchmark_price=601.40,
    as_of=datetime(2026, 1, 1),
)

# A later top-up.
tracker.record_contribution(
    amount=5_000.00, benchmark_symbol="SPY", benchmark_price=605.00,
    note="Top-up", executed_at=datetime(2026, 2, 1),
)
tracker.record_fund_value(
    total_value=27_100.00, benchmark_symbol="SPY", benchmark_price=610.00,
    as_of=datetime(2026, 3, 1),
)

summary = tracker.get_latest_performance_summary()
for row in tracker.get_active_capital_breakdown():
    print(row["executed_at"].date(), row["flow_type"], row["amount"], f"{row['days_active']:.0f} days active")
```

### Pulling a benchmark price from a scan you already fetched

```python
scan_data = TRADINGVIEW_API_CLIENT.scan_global_market_move_prediction(
    min_market_cap_usd=1_000_000_000,
).get("data", [])

spy_price = PortfolioPerformanceTracker.resolve_benchmark_price(scan_data, "SPY")
tracker.take_nav_snapshot_from_holdings(cash_balance=0.0, benchmark_symbol="SPY", benchmark_price=spy_price)
```

## Files

- `db/portfolio_performance_tracking_operations.py` -- MySQL schema + CRUD for
  `portfolio_cash_flows` and `portfolio_nav_snapshots` (in `src/db/`, alongside
  the existing `portfolio_management_operations.py`).
- `cash_flow_ledger.py` -- record/query contributions, withdrawals, dividends,
  realized gains.
- `nav_history.py` -- record/query NAV snapshots.
- `performance_calculator.py` -- Modified Dietz sub-period return + chaining
  into a cumulative % series, plus annualization.
- `benchmark_comparator.py` -- shadow benchmark portfolio simulation + helper
  to pull a benchmark price out of an existing TradingView scan payload.
- `portfolio_performance_tracker.py` -- `PortfolioPerformanceTracker`, the
  main entry point wiring everything above to a `PortfolioTracker`-backed
  portfolio.
- `performance_report_output.py` -- writes the log + CSV report under
  `logs/portfolio_performance_tracking/<portfolio>/<DD_MM_YYYY>/`.

## Verified end-to-end

`scripts_adhoc/portfolio_performance_two_year_simulation.py` runs both modes
against the real DB over a simulated 2-year window (contributions, a top-up,
a dividend, a partial sale + realized gain, a withdrawal, and a benchmark
comparison vs. SPY), then exports the full report. Re-running it is safe -- it
resets its own two demo portfolios first. Regression tests for the pure return
math (Modified Dietz chaining, shadow-benchmark consistency, active-capital
timeline) live in `src/tests/test_portfolio_performance_tracking.py`.

