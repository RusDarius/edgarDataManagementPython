# Data Analysis Scripts Guide

This guide explains the scripts in `src/data_analysis_scripts` in a simple way, while still keeping the data meaning and practical use clear.

The folder contains two kinds of scripts:

1. Analysis scripts that study company or market data and write reports.
2. One support script that enriches your TradingView field dictionary.

Most of the TradingView-based scripts do not fetch data by themselves. They expect `scan_data`, which usually comes from `ApiTradingViewClient` in `src/data_loaders/api_tradingview_client.py`.

## Quick Mental Model

- `analisys_by_industry.py`: annual EDGAR revenue trend and revenue share.
- `trading_view_priceperf_analysis.py`: which stocks have performed best or worst across time horizons.
- `trading_view_activity_float_attention.py`: where market attention, participation, and squeeze-style pressure are concentrated.
- `trading_view_move_prediction_analysis.py`: heuristic directional ranking for days, weeks, and months.
- `trading_view_safety_check_v1.py`: balance-sheet and financial-strength screen.
- `trading_view_valuation_analysis.py`: valuation multiples by industry and valuation deviation.
- `enrich_trading_view_fields.py`: enriches the TradingView field CSV with plain-English explanations.

## Before You Run Anything

### 1. Python environment

Install the project dependencies first:

```bash
pip install -r requirements.txt
```

### 2. Data sources used by these scripts

There are two main sources:

- MySQL EDGAR data for `analisys_by_industry.py`
- TradingView screener API data for the TradingView scripts

### 3. Common objects you will use

Most TradingView analysis calls follow this shape:

```python
from constants.trading_view_constants import PREFERRED_MARKETS, TRADING_VIEW_INDUSTRIES
from data_loaders.api_tradingview_client import ApiTradingViewClient

client = ApiTradingViewClient(user_agent="your-email-or-user-agent")
scan_data = client.scan_global_market_activity_float_attention(
    min_market_cap_usd=1_000_000_000,
    markets=PREFERRED_MARKETS,
).get("data", [])
```

### 4. Common argument meanings

These parameters appear in several scripts:

| Argument | Meaning | How to use it |
| --- | --- | --- |
| `scan_data` | A list of mapped TradingView rows. Each row is one ticker with named fields. | Usually pass `client.some_scan(...).get("data", [])`. |
| `industries` | Optional list of TradingView industry labels. | Use constants from `TRADING_VIEW_INDUSTRIES` where possible. |
| `min_market_cap_usd` | Lower market-cap cutoff in USD. | Use this to avoid microcaps or illiquid names. |
| `max_market_cap_usd` | Upper market-cap cutoff in USD. | Use this when you only want small- or mid-cap names. |
| `markets` | TradingView market codes like `america`, `japan`, `uk`. | Use `PREFERRED_MARKETS` for a cleaner global universe. |
| `ticker_filter` | A text match filter for a specific ticker. | Useful for one-company checks in safety and move prediction scans. |

## 1. `analisys_by_industry.py`

## What it does

This script analyzes annual revenue by ticker using the EDGAR facts stored in your MySQL database. It then lets you compare companies by year and calculate each company's share of combined revenue.

Despite the filename, this is really a revenue trend and revenue-share analysis across a ticker list.

## Main functions

- `analyze_revenues_by_industry(tickers, start_year=2020)`
- `process_revenues_for_tickers(tickers, start_year=2020)`

## What values it takes

| Input | Type | Meaning |
| --- | --- | --- |
| `tickers` | `list[str]` | Stock tickers to analyze, such as `['LMT', 'NOC', 'RTX']`. |
| `start_year` | `int` | First fiscal year to include. Default is `2020`. |

## What data it uses

It queries:

- `edgar_financial_data_concepts`
- `sec_cik_tickers_mapping`

It only uses annual-style filings and annualized rows:

- `Qtrs = 4`
- filing types `10-K`, `20-F`, `40-F`
- no segment rows

It uses a fallback stack of revenue concepts:

1. `RevenueFromContractWithCustomerExcludingAssessedTax`
2. `SalesRevenueNet`
3. `Revenues`
4. `Revenue`
5. `RevenueFromContractWithCustomerIncludingAssessedTax`
6. `RevenueFromRenderingOfServices`

It also normalizes common USD units:

- `USD`
- thousands-style USD units
- millions-style USD units
- billions-style USD units

## What it looks for

- Which annual revenue value is the best representative value for each ticker-year.
- Whether a company has revenue data at all for the requested period.
- How much of a group's total revenue each company contributes each year.

## What it tells you

This script is useful when you want to answer questions like:

- Is a company gaining or losing revenue share inside a peer group?
- Is industry leadership stable or changing?
- Are smaller players closing the gap or losing relevance?

This is a good first-layer screen, but it does not tell you whether the revenue is profitable, cash-generative, or cheap.

## Output

- `analyze_revenues_by_industry(...)` returns a dictionary keyed by ticker.
- `process_revenues_for_tickers(...)` writes a log file to:
  - `logs/revenues_for_tickers_v1.log`
- It also prints yearly revenue share summaries to the console.

## Example

```python
from data_analysis_scripts.analisys_by_industry import process_revenues_for_tickers

process_revenues_for_tickers(
    tickers=["LMT", "NOC", "RTX", "GD"],
    start_year=2018,
)
```

## When to use it

Use this when your main question is demand scale and long-term business footprint.

Do not use it alone when your real question is valuation, balance-sheet safety, or short-term price movement.

## 2. `trading_view_priceperf_analysis.py`

## What it does

This script ranks stocks by price performance across many time horizons.

It generates two report styles:

1. Sorted by market cap, then broken out by performance period.
2. Sorted by performance value itself, highest to lowest.

## Main functions

- `analyze_price_performance_by_market_cap(...)`
- `analyze_price_performance_by_metric(...)`
- `analyze_global_price_performance(...)`

In practice, `analyze_global_price_performance(...)` is the normal entry point.

## What values it takes

| Input | Type | Meaning |
| --- | --- | --- |
| `scan_data` | `list[dict]` | TradingView rows from `scan_world_market_all_priceperf_metrics(...)`. |
| `min_market_cap_usd` | `float | None` | Minimum market cap filter. |
| `max_market_cap_usd` | `float | None` | Maximum market cap filter. |
| `performance_field` | `str | None` | Optional pre-filter field, like `Perf.Y` or `Perf.1M`. |
| `min_performance_pct` | `float | dict | None` | Minimum required return. Can be one number or a dictionary by field. |
| `max_performance_pct` | `float | dict | None` | Maximum allowed return. Can be one number or a dictionary by field. |

## What data it uses

The script reads performance fields such as:

- `Perf.All`
- `Perf.10Y`
- `Perf.5Y`
- `Perf.3Y`
- `Perf.1Y`
- `Perf.6M`
- `Perf.YTD`
- `Perf.1M`
- `Perf.W`
- `Perf.5D`
- `change`

It also uses `market_cap_basic` so large and small names can be compared in context.

## What those fields mean

- `Perf.Y`: trailing 1-year return
- `Perf.6M`: trailing 6-month return
- `Perf.1M`: trailing 1-month return
- `Perf.W`: trailing 1-week return
- `Perf.5D`: recent 5-day return
- `change`: current daily move

Longer windows help you find durable leaders and laggards.
Shorter windows help you find current acceleration or breakdown.

## What it looks for

- Long-term winners versus short-term winners
- Big-cap leaders versus small-cap leaders
- Whether strength is broad across timeframes or only recent
- Whether weakness is structural or only short-term

## What it tells you

This script answers questions like:

- Which names have the strongest sustained trend?
- Which names are suddenly breaking out in the short term?
- Are the best performers concentrated in large caps or smaller names?

This is a ranking tool. It tells you what price has done, not why it happened.

## Output

It writes two log files in `logs/tradingview_analysis` with an hour-stamped suffix:

- `tradingview_price_performance_by_market_cap_YYYYMMDD_HH.log`
- `tradingview_price_performance_ranked_by_metric_YYYYMMDD_HH.log`

## Example

```python
from constants.trading_view_constants import PREFERRED_MARKETS
from data_analysis_scripts.trading_view_priceperf_analysis import analyze_global_price_performance
from data_loaders.api_tradingview_client import ApiTradingViewClient

client = ApiTradingViewClient(user_agent="your-user-agent")
scan_data = client.scan_world_market_all_priceperf_metrics(
    min_market_cap_usd=1_000_000_000,
    markets=PREFERRED_MARKETS,
).get("data", [])

analyze_global_price_performance(
    scan_data=scan_data,
    min_market_cap_usd=1_000_000_000,
    min_performance_pct={
        "Perf.1Y": 10.0,
        "Perf.6M": 5.0,
        "Perf.1M": 2.0,
    },
)
```

## Best use case

Use this as a first pass when you want to separate leaders from laggards before doing deeper work on attention, safety, or valuation.

## 3. `trading_view_activity_float_attention.py`

## What it does

This script studies where the market is paying attention right now.

It does not just look at price movement. It looks at participation, float structure, traded value, event-style gaps, and momentum/trend alignment.

It can analyze:

1. Raw ticker-level rows
2. Industry-grouped aggregate rows built from the raw scan

## Main functions

- `analyze_activity_float_attention_scan(...)`
- `analyze_activity_float_attention_scan_grouped_industries(...)`
- `run_activity_float_attention_scan(...)`
- `run_activity_float_attention_scan_grouped_industries(...)`

## What values it takes

| Input | Type | Meaning |
| --- | --- | --- |
| `scan_data` | `list[dict[str, Any]]` | TradingView rows from `scan_global_market_activity_float_attention(...)`. |
| `industries` | `list[str] | None` | Optional industry filter label list. |
| `min_market_cap_usd` | `float | None` | Lower market-cap cutoff. |
| `max_market_cap_usd` | `float | None` | Upper market-cap cutoff. |

## What data it uses

The analysis is built around four groups of fields:

### Participation and float

- `volume`
- `average_volume_10d_calc`, `average_volume_30d_calc`, `average_volume_60d_calc`, `average_volume_90d_calc`
- `relative_volume_10d_calc`
- `Value.Traded`
- `AvgValue.Traded_10d`, `AvgValue.Traded_30d`, `AvgValue.Traded_60d`, `AvgValue.Traded_90d`
- `float_shares_outstanding`

Meaning:

- High `relative_volume_10d_calc` means trading is unusually active versus its own recent baseline.
- Lower `float_shares_outstanding` means the stock can move more violently if demand arrives.
- High `Value.Traded` means real capital, not just share count, is moving.

### Event pressure

- `change`
- `gap`
- `premarket_gap`
- `premarket_change`
- `premarket_volume`
- `postmarket_change`
- `postmarket_volume`

Meaning:

- Gaps and off-hours moves often mean news, earnings, or a catalyst is driving repricing.
- Premarket or postmarket volume tells you whether the move has real participation behind it.

### Momentum

- `Perf.5D`, `Perf.W`, `Perf.1M`, `Perf.3M`, `Perf.YTD`, `Perf.Y`
- `RSI`, `RSI7`
- `MACD.macd`, `MACD.signal`
- `Mom`, `ROC`
- `Recommend.All`, `Recommend.MA`, `Recommend.Other`

Meaning:

- These help separate one-day noise from a real ongoing move.

### Trend anchors

- `VWAP`, `VWMA`
- `SMA50`, `SMA200`
- `EMA50`, `EMA200`

Meaning:

- If price is above major averages, the tape is usually healthier.
- If attention is high but price is under key averages, the move may be lower quality or more event-driven than trend-driven.

## What it looks for

- Abnormal participation
- Tight-float names under pressure
- Event-driven repricing
- Momentum with confirmation
- Trend support behind the move

The script compares each name with the current scan universe, so it is more relative than absolute.

## What it tells you

This script helps answer:

- Which names are really in play right now?
- Is volume high because the company is always liquid, or because something new is happening?
- Are buyers actually in control, or is it just noisy activity?
- Which industries are attracting the most aggregate attention?

## Output

Ticker-level version writes:

- `logs/tradingview_analysis/tradingview_activity_float_attention__{industries}__min_{x}__max_{y}.log`
- matching CSV with the same name stem

Grouped-industry version writes:

- `logs/tradingview_analysis/tradingview_activity_float_attention_grouped_industries__{industries}__min_{x}__max_{y}.log`
- matching CSV with the same name stem

The CSV includes raw values plus deviations from the scan mean and median for the included numeric fields.

## Example

```python
from constants.trading_view_constants import PREFERRED_MARKETS
from data_analysis_scripts.trading_view_activity_float_attention import run_activity_float_attention_scan
from data_loaders.api_tradingview_client import ApiTradingViewClient

client = ApiTradingViewClient(user_agent="your-user-agent")
scan_data = client.scan_global_market_activity_float_attention(
    min_market_cap_usd=1_000_000_000,
    markets=PREFERRED_MARKETS,
).get("data", [])

run_activity_float_attention_scan(
    scan_data=scan_data,
    min_market_cap_usd=1_000_000_000,
)
```

## Best use case

Use this when you want to find current action, crowding, abnormal participation, and possible squeeze or catalyst conditions.

## 4. `trading_view_move_prediction_analysis.py`

## What it does

This script creates a heuristic directional model for each stock in the scan.

Important: this is not a trained machine learning model. The script itself says it is a heuristic ranking model.

It combines seven component groups:

1. Attention
2. Event
3. Momentum
4. Trend
5. Quality
6. Valuation
7. Safety

It then builds directional views for three horizons:

- `days`
- `weeks`
- `months`

## Main functions

- `analyze_move_prediction_scan(...)`
- `run_move_prediction_scan(...)`
- `run_move_prediction_profile_suite(...)`
- `list_move_prediction_scoring_profiles()`

## What values it takes

| Input | Type | Meaning |
| --- | --- | --- |
| `scan_data` | `list[dict[str, Any]]` | TradingView rows from `scan_global_market_move_prediction(...)`. |
| `industries` | `list[str] | None` | Optional industry filter. |
| `min_market_cap_usd` | `float | None` | Lower market-cap cutoff. |
| `max_market_cap_usd` | `float | None` | Upper market-cap cutoff. |

## What data it uses

It mixes fields from several families:

- Activity and attention fields such as `relative_volume_10d_calc`, `Value.Traded`
- Event fields such as `gap`, `premarket_change`, `postmarket_change`
- Momentum fields such as `Perf.*`, `ROC`, `Mom`, `RSI`, `MACD`
- Trend fields using `SMA50`, `SMA200`, `EMA50`, `EMA200`, `VWAP`, `VWMA`
- Quality fields such as revenue growth, EBITDA growth, margin fields, ROA, ROE, ROIC
- Valuation fields such as `price_earnings_ttm`, `price_sales_current`, `enterprise_value_ebitda_ttm`
- Safety fields such as `current_ratio`, `quick_ratio`, `cash_ratio`, `debt_to_equity`, `altman_z_score_ttm`

It also builds derived metrics such as:

- `float_turnover`
- `dollar_turnover_intensity`
- `gap_severity`
- `event_intensity`
- `macd_spread`
- `trend_alignment`

## What the component groups mean

| Component | What it means |
| --- | --- |
| Attention | How unusually active the name is relative to float and traded value. |
| Event | Whether a catalyst or gap-like repricing is happening. |
| Momentum | Whether price strength or weakness is carrying across timeframes. |
| Trend | Whether price is aligned with major technical anchors. |
| Quality | Whether the business is showing growth and profitability strength. |
| Valuation | Whether the stock looks cheap or expensive relative to the scan. Lower multiples help here. |
| Safety | Whether balance-sheet and risk structure are supportive or fragile. |

## How horizon weighting works

- `days`: attention, event pressure, and momentum matter more
- `weeks`: momentum, trend, and safety matter more
- `months`: quality, trend, valuation, and safety matter more

The script also reduces confidence if earnings are very close, because near-term event risk makes the signal less stable.

## What it looks for

- Short-term continuation setups
- Trend continuation
- Event-driven upside or downside
- Fundamental rerating
- Trend breakdowns
- Balance-sheet-risk unwinds

## What it tells you

For each name, it gives:

- a score
- a direction label like `Up`, `Strong Up`, `Down`, `Strong Down`, or `Neutral`
- a confidence estimate
- a setup label describing the kind of move the model thinks is most likely

It also shows consensus groups such as:

- bullish across all horizons
- bearish across all horizons
- short-term upside but long-term drag
- short-term weakness but long-term recovery

It also now adds blind-spot sections that try to surface ideas the standard top-rank list can miss, such as:

- underfollowed quality
- crowded fragility
- event dislocation
- recovery candidates

## Detailed score flow

The current scoring mechanism works in five layers.

### 1. Raw field collection

Each row starts with raw TradingView fields and a few derived fields.

Examples:

- raw fields: `relative_volume_10d_calc`, `gap`, `Perf.1M`, `gross_margin`, `price_earnings_ttm`, `current_ratio`
- derived fields: `float_turnover`, `dollar_turnover_intensity`, `gap_severity`, `event_intensity`, `macd_spread`, `trend_alignment`

### 2. Robust normalization

Each numeric field is compared against the current scan universe using:

- median
- median absolute deviation, or MAD

That means the model does not rely on hard-coded global thresholds first. It asks whether a field is unusually high or low versus the current returned universe.

In simple terms:

- positive signal: stronger than the current universe median
- negative signal: weaker than the current universe median
- larger magnitude: more unusual relative position

If MAD is missing or zero, the code falls back to a simpler sign comparison versus the median.

### 3. Component score construction

Signals are grouped into seven component buckets:

- `attention`
- `event`
- `momentum`
- `trend`
- `quality`
- `valuation`
- `safety`

Each component is a weighted average of its underlying signals.

Examples:

- `attention` uses abnormal participation and turnover fields
- `event` uses gaps and off-hours reaction fields
- `momentum` uses `Perf.*`, `ROC`, `Mom`, `MACD`, and RSI-centered signals
- `trend` uses price position relative to `SMA`, `EMA`, `VWAP`, and `VWMA`
- `quality` uses growth, margin, and return-on-capital style fields
- `valuation` uses inverted positive valuation multiples, so cheaper usually scores better
- `safety` uses liquidity, leverage, distress, and balance-sheet stability fields

### 4. Optional directional bias

This is the new adjustable layer.

Each component score can now be biased differently for positive and negative readings.

Example:

- if `momentum` is positive and the profile wants breakout continuation, positive momentum can be multiplied by more than `1.0`
- if `safety` is negative and the profile wants downside-fragility shorts, negative safety can be multiplied by more than `1.0`

This is how the same underlying signals can be tilted toward different opportunity types without rewriting the whole model.

### 5. Horizon synthesis

Component scores are then combined into horizon-level scores.

The module now supports:

- `days`
- `weeks`
- `months`
- `years`

The default logic is:

- `days`: attention, event, and momentum matter most
- `weeks`: momentum, trend, and safety matter more
- `months`: quality, trend, valuation, and safety matter more
- `years`: quality, valuation, and safety matter most

The model then produces:

- `score`
- `direction`
- `confidence`
- `coverage`
- `setup`

Confidence is reduced when earnings are close, because event risk makes the signal less stable.

## How scoring can be adjusted intentionally

The module now supports scoring profiles.

There are three main ways to bias the score:

### 1. Change horizon weights

This changes which component buckets matter most for a given horizon.

Use this when your strategy changes by holding period.

Examples:

- for tactical breakout trading, increase `attention`, `event`, `momentum`, and `trend` in `days` and `weeks`
- for long-duration investing, increase `quality`, `valuation`, and `safety` in `months` and `years`

### 2. Change signal weights inside a component

This changes what a component cares about most.

Examples:

- in `attention`, overweight `relative_volume_10d_calc` and `float_turnover` if you care more about crowding and abnormal participation
- in `quality`, overweight `free_cash_flow_yoy_growth_ttm` and `return_on_invested_capital` if you care more about disciplined long-term compounders
- in `safety`, overweight `debt_to_equity` and `altman_z_score_ttm` if you want fragility screens to react faster

### 3. Change positive-vs-negative directional multipliers

This changes whether the model should react more strongly to bullish or bearish evidence.

Examples:

- for upside continuation, amplify positive `attention`, `momentum`, and `trend`
- for short-fragility hunting, amplify negative `safety`, `trend`, and `momentum`
- for recovery hunting, dampen negative `momentum` a bit so cheap improving names are not punished too hard for still-looking ugly short term

## Included scoring profiles

The file now includes these presets:

| Profile | Main intent |
| --- | --- |
| `balanced` | The original default logic, now made explicit. |
| `breakout_long` | Tactical upside continuation and high-participation names. |
| `quality_value_compounder` | Longer-duration quality plus reasonable-price longs. |
| `value_recovery` | Improving recovery or rerating candidates that may not have strong short-term momentum yet. |
| `fragility_short` | Balance-sheet and trend-fragility short ideas. |

## New usage examples

### Run one custom profile

```python
from constants.trading_view_constants import PREFERRED_MARKETS
from data_analysis_scripts.trading_view_move_prediction_analysis import run_move_prediction_scan
from data_loaders.api_tradingview_client import ApiTradingViewClient

client = ApiTradingViewClient(user_agent="your-user-agent")
scan_data = client.scan_global_market_move_prediction(
    min_market_cap_usd=1_000_000_000,
    markets=PREFERRED_MARKETS,
).get("data", [])

run_move_prediction_scan(
    scan_data=scan_data,
    min_market_cap_usd=1_000_000_000,
    scoring_profile="breakout_long",
    include_blind_spot_sections=True,
)
```

### Run several opportunity lenses at once

```python
from constants.trading_view_constants import PREFERRED_MARKETS
from data_analysis_scripts.trading_view_move_prediction_analysis import run_move_prediction_profile_suite
from data_loaders.api_tradingview_client import ApiTradingViewClient

client = ApiTradingViewClient(user_agent="your-user-agent")
scan_data = client.scan_global_market_move_prediction(
    min_market_cap_usd=1_000_000_000,
    markets=PREFERRED_MARKETS,
).get("data", [])

logs_by_profile = run_move_prediction_profile_suite(
    scan_data=scan_data,
    min_market_cap_usd=1_000_000_000,
    profile_names=[
        "balanced",
        "breakout_long",
        "quality_value_compounder",
        "value_recovery",
        "fragility_short",
    ],
)
```

### Inspect available presets

```python
from data_analysis_scripts.trading_view_move_prediction_analysis import list_move_prediction_scoring_profiles

profiles = list_move_prediction_scoring_profiles()
```

## Practical interpretation of opportunity bias

If you bias the model one way, you should expect different names to rise.

- More `attention` and `momentum`: names already in play move up the list.
- More `event`: catalyst and gap names move up the list.
- More `trend`: cleaner chart structures move up the list.
- More `quality` and `safety`: weaker but exciting names fall down, steadier businesses move up.
- More `valuation`: expensive leaders fall down, cheaper rerating candidates move up.

This matters because the best active-management idea is often not "the highest score overall". It is often "the highest score for the opportunity type I am actually trying to capture."

## Output

It writes:

- a log file in `logs/tradingview_analysis`
- a CSV file with the same naming stem

The file name pattern is:

- `tradingview_move_prediction__{industries}__min_{x}__max_{y}.log`
- matching CSV

For non-default profiles, the file name also includes a profile suffix so multiple profile runs do not overwrite each other.

## Example

```python
from constants.trading_view_constants import PREFERRED_MARKETS
from data_analysis_scripts.trading_view_move_prediction_analysis import run_move_prediction_scan
from data_loaders.api_tradingview_client import ApiTradingViewClient

client = ApiTradingViewClient(user_agent="your-user-agent")
scan_data = client.scan_global_market_move_prediction(
    min_market_cap_usd=1_000_000_000,
    markets=PREFERRED_MARKETS,
).get("data", [])

run_move_prediction_scan(
    scan_data=scan_data,
    min_market_cap_usd=1_000_000_000,
)
```

## Best use case

Use this when you want one relative ranking that combines tape action, fundamentals, valuation, and risk into one directional view.

Do not treat it as a probability model or backtested forecast engine. It is a structured heuristic.

## 5. `trading_view_safety_check_v1.py`

## What it does

This script evaluates financial safety and balance-sheet quality.

It can work in two ways:

1. Full-universe scan analysis
2. Single-company safety assessment

It compares each company against its industry using percentile-style logic, then labels the company `GOOD`, `MIXED`, or `BAD`.

## Main functions

- `analyze_safety_core_scan(...)`
- `run_safety_core_scan(...)`
- `assess_individual_company_safety(entry)`
- `log_individual_company_safety_assessment(entry, log_file=None)`

## What values it takes

### Full-scan functions

| Input | Type | Meaning |
| --- | --- | --- |
| `scan_data` | `list[dict[str, Any]]` | TradingView rows from `scan_global_market_safety_core(...)`. |
| `industries` | `list[str] | None` | Optional industry filter. |
| `min_market_cap_usd` | `float | None` | Lower market-cap cutoff. |
| `max_market_cap_usd` | `float | None` | Upper market-cap cutoff. |
| `include_all_field_deviation_table` | `bool` | Include a full deviation table per ticker. |
| `include_standout_abnormalities` | `bool` | Add large outlier callouts. |
| `standout_zscore_threshold` | `float` | Threshold for calling a field an abnormal standout. |
| `max_standouts_per_ticker` | `int` | Cap on abnormality lines per ticker. |

### Single-company functions

| Input | Type | Meaning |
| --- | --- | --- |
| `entry` | `dict[str, Any]` | One mapped TradingView row or one raw TradingView row. |

## What data it uses

It studies liquidity, solvency, profitability, cash flow, growth support, and derived leverage measures.

Important fields include:

### Liquidity

- `current_ratio`
- `current_ratio_fq`
- `quick_ratio`
- `cash_ratio`

Meaning:

- Higher values usually mean the company can meet near-term obligations more easily.

### Solvency and distress

- `debt_to_equity`
- `debt_to_assets`
- `debt_to_revenue_ttm`
- `total_debt`
- `net_debt`
- `altman_z_score_ttm`

Meaning:

- More debt usually means more fragility.
- Higher Altman Z usually means lower distress risk.

### Profitability and cash flow

- `gross_margin`
- `operating_margin`
- `after_tax_margin`
- `return_on_assets`
- `return_on_equity`
- `return_on_invested_capital`
- `cash_f_operating_activities_ttm`
- `free_cash_flow_margin_ttm`

Meaning:

- Stronger margins and stronger cash generation usually mean a safer operating profile.

### Trend support and derived safety checks

- `total_revenue_yoy_growth_ttm`
- `net_income_yoy_growth_ttm`
- `net_debt_to_market_cap`
- `cash_coverage_liabilities`
- `liability_load_vs_scale`
- `operating_cash_backing_debt`

Meaning:

- These check whether the company is improving and whether its obligations are reasonable relative to scale and cash generation.

## What it looks for

- strong liquidity
- manageable leverage
- low distress risk
- real cash generation
- solid margins and returns
- balance-sheet support for future weakness

It also applies hard risk flags when conditions are severe, including:

- Altman Z below `1.8`
- current ratio below `1.0`
- debt to equity above `2.0`
- negative operating cash flow

## What it tells you

This script answers:

- Which companies are financially sturdy versus fragile?
- Which industries look strongest on average?
- What specific balance-sheet or cash-flow issues are hurting a name?
- Does a stock have strong enough financial structure for a 1-year, 3-year, or 5-year holding view?

## Output

Full-scan analysis writes a log under `logs/tradingview_analysis` using an industry and market-cap-aware file name.

Single-company analysis writes a file like:

- `logs/tradingview_analysis/tradingview_individual_safety__{ticker}.log`

## Example: full scan

```python
from constants.trading_view_constants import PREFERRED_MARKETS
from data_analysis_scripts.trading_view_safety_check_v1 import run_safety_core_scan
from data_loaders.api_tradingview_client import ApiTradingViewClient

client = ApiTradingViewClient(user_agent="your-user-agent")
scan_data = client.scan_global_market_safety_core(
    min_market_cap_usd=1_000_000_000,
    markets=PREFERRED_MARKETS,
).get("data", [])

run_safety_core_scan(
    scan_data=scan_data,
    min_market_cap_usd=1_000_000_000,
    include_all_field_deviation_table=True,
)
```

## Example: one company

```python
from data_analysis_scripts.trading_view_safety_check_v1 import log_individual_company_safety_assessment
from data_loaders.api_tradingview_client import ApiTradingViewClient

client = ApiTradingViewClient(user_agent="your-user-agent")
entry = client.scan_global_market_safety_core(
    ticker_filter="MSFT",
).get("data", [])[0]

assessment, log_path = log_individual_company_safety_assessment(entry)
```

## Best use case

Use this when you want to protect against fragile balance sheets and weak cash generation before taking a position.

## 6. `trading_view_valuation_analysis.py`

## What it does

This script compares valuation multiples across industries and within industries.

It has three distinct analysis modes:

1. `analyze_global_market_performance_by_industry(scan_data)`
2. `analyze_industry_multiples(scan_data, industry_name, min_market_cap_usd=None)`
3. `analyze_ev_ebitda_deviation(scan_data, industries=None)`

The last function name mentions EV/EBITDA, but the current code analyzes a wider set of valuation multiples, not only EV/EBITDA.

## What values it takes

| Function | Main inputs |
| --- | --- |
| `analyze_global_market_performance_by_industry` | `scan_data` from `scan_global_market_by_industry(...)` |
| `analyze_industry_multiples` | `scan_data`, one `industry_name`, optional market-cap floor |
| `analyze_ev_ebitda_deviation` | `scan_data`, optional `industries` list |

## What data it uses

Important multiple fields:

- `price_earnings_ttm`
- `price_earnings_growth_ttm`
- `price_sales_current`
- `price_book_fq`
- `price_to_cash_f_operating_activities_ttm`
- `price_free_cash_flow_ttm`
- `price_to_cash_ratio`
- `enterprise_value_to_revenue_ttm`
- `enterprise_value_to_ebit_ttm`
- `enterprise_value_ebitda_ttm`

It also uses:

- `market_cap_basic`
- `Perf.1Y.MarketCap`
- `Perf.Y`

## What those fields mean

- Lower positive multiples generally mean cheaper valuation.
- Higher multiples usually mean stronger growth expectations, stronger quality, or overpricing.
- Negative multiples are usually excluded because they distort cross-sectional comparison.

Examples:

- `price_earnings_ttm`: price relative to trailing earnings
- `price_sales_current`: price relative to sales, useful when earnings are weak or negative
- `enterprise_value_ebitda_ttm`: firm value relative to operating cash earnings proxy
- `price_book_fq`: price relative to book value, often more relevant for asset-heavy or financial businesses

## What it looks for

- which industries are cheap or expensive relative to the broader set
- which companies are cheap or expensive relative to their own industry
- which multiples are being distorted by bad or non-comparable data

## What it tells you

This script helps answer:

- Is this stock cheap versus peers?
- Is this entire industry expensive versus the market?
- Which metric is showing the strongest valuation gap?
- Are negative or missing values distorting the comparison?

## Important filtering note

The module contains a hard-coded `MIN_MARKET_CAP_USD = 10_000_000_000` for one part of the global industry analysis logic.

Some log strings in the file mention lower thresholds, but the actual constant currently set in code is `10B` USD. If you rely on that function, follow the code constant rather than the older wording in the log text.

## Output

This module writes different logs depending on the function:

- `logs/tradingview_analysis/tradingview_global_industry_performance.log`
- `logs/tradingview_analysis/tradingview_industry_multiples_{industry}.log`
- `logs/tradingview_analysis/tradingview_ev_ebitda_deviation__{industries}.log`

## Example: industry valuation scan

```python
from constants.trading_view_constants import TRADING_VIEW_INDUSTRIES
from data_analysis_scripts.trading_view_valuation_analysis import analyze_ev_ebitda_deviation
from data_loaders.api_tradingview_client import ApiTradingViewClient

client = ApiTradingViewClient(user_agent="your-user-agent")
industries = [TRADING_VIEW_INDUSTRIES.HOTELS_RESORTS_CRUISE_LINES]
scan_data = client.scan_global_market_by_industry(
    industries=industries,
    min_market_cap_usd=1_000_000_000,
).get("data", [])

analyze_ev_ebitda_deviation(
    scan_data=scan_data,
    industries=industries,
)
```

## Best use case

Use this after quality or safety screening. Valuation is most useful when you already know the business is investable and now need to judge the price being paid.

## 7. `enrich_trading_view_fields.py`

## What it does

This is not a stock-screen analysis script. It is a support utility that enriches your TradingView field reference CSV with plain-English descriptions.

It adds two columns:

- `explanation`
- `model_use`

## Main functions

- `enrich_csv(csv_path)`
- `main()`

## What values it takes

| Input | Type | Meaning |
| --- | --- | --- |
| `csv_path` | `Path` | Path to the TradingView field CSV to enrich. |

The default script paths are:

- source CSV: `savedData/trading_view_stock_fields.csv`
- backup CSV: `savedData/trading_view_stock_fields.backup_before_enrichment.csv`

## What data it uses

It reads field metadata columns such as:

- `Name`
- `Display name`
- `Type`

It then uses a large rule set to generate explanations.

## What it looks for

- exact known field names like `market_cap`, `sector`, `return_on_equity`, `enterprise_value_ebitda_ttm`
- indicator patterns
- timeframe suffixes like `|1W` or `|1M`
- lag notation like `[1]`

## What it tells you

It creates a dictionary-style CSV that explains:

- what each field means
- how to use it in screening or modeling

This is valuable when you are building new TradingView analysis scripts and want consistent field interpretation.

## Output

- Updates `savedData/trading_view_stock_fields.csv`
- Creates backup `savedData/trading_view_stock_fields.backup_before_enrichment.csv` if it does not already exist

## Example

```bash
python -m src.data_analysis_scripts.enrich_trading_view_fields
```

or:

```python
from pathlib import Path
from data_analysis_scripts.enrich_trading_view_fields import enrich_csv

enrich_csv(Path(r"d:\FinanceProjects\edgarDataManagementPython\savedData\trading_view_stock_fields.csv"))
```

## Recommended Workflow

If you want a practical order for using these scripts, this sequence is the most useful:

1. `trading_view_priceperf_analysis.py`
   - Find the strongest and weakest names by timeframe.
2. `trading_view_activity_float_attention.py`
   - Check whether the move has unusual participation and event pressure.
3. `trading_view_move_prediction_analysis.py`
   - Get a combined directional ranking across multiple horizons.
4. `trading_view_safety_check_v1.py`
   - Remove balance-sheet and cash-flow weak names.
5. `trading_view_valuation_analysis.py`
   - Decide whether the remaining candidates are expensive or attractive.
6. `analisys_by_industry.py`
   - For longer-term fundamental work, compare revenue scale and share over time.

## Choosing The Right Script

| If your main question is... | Use this script |
| --- | --- |
| Who has the strongest price trend? | `trading_view_priceperf_analysis.py` |
| Which names are attracting unusual attention right now? | `trading_view_activity_float_attention.py` |
| Which names have the strongest multi-factor directional setup? | `trading_view_move_prediction_analysis.py` |
| Which names are financially sturdy versus fragile? | `trading_view_safety_check_v1.py` |
| Which stocks or industries look cheap or expensive? | `trading_view_valuation_analysis.py` |
| Who is gaining or losing revenue share over time? | `analisys_by_industry.py` |
| What does a TradingView field mean and how should I use it? | `enrich_trading_view_fields.py` |

## Final Interpretation Principle

No single script should be treated as the answer by itself.

The strongest workflow in this repository is layered:

1. price behavior
2. participation and attention
3. directional composite view
4. safety and balance-sheet quality
5. valuation
6. long-term fundamental share and scale

That order helps you move from "what is moving" to "why it may keep moving" to "whether the company is strong enough and cheap enough to matter."