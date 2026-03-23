# TradingView Stock Screen Payload Principles

## Purpose
This document compiles verified TradingView screener payload combinations for building stock safety screens, activity and price-move screens, and peer comparison packs.

The field names used below are limited to fields confirmed in `savedData/stock_fields.csv`.

## Correctness Notes
- The payload structures below follow the query shape already used in this repository.
- The safest use is to retrieve the fields shown here and apply thresholds, ranks, and composite scores in Python after download.
- Your current repo examples clearly use these filter operations: `equal`, `has`, `has_none_of`, `in_range`.
- I am not assuming additional numeric filter operators here because they are not yet demonstrated in the repo code.
- The standalone field `name` is not present in `stock_fields.csv`. If you want `name` in the output, derive it from `ticker-view` in the mapped response or from your custom response mapping.

## Important Data Gaps In Current CSV Inventory
These are useful for real screening, but they are not confirmed in the current TradingView field inventory you shared:
- Short interest percent of float
- Days to cover
- Borrow fee / stock loan fee
- Insider buying and selling
- Analyst estimate revisions
- Options open interest and skew
- Direct interest coverage fields based on interest expense

Because of that, activity and squeeze-style inference can only be partial with the current field set.

## Reusable Base Universe Block
Use this as the common payload shell and replace only `columns`, `range`, and `sort` per use case.

```json
{
  "filter": [
    {
      "left": "is_primary",
      "operation": "equal",
      "right": true
    }
  ],
  "ignore_unknown_fields": false,
  "options": {
    "lang": "en"
  },
  "range": [
    0,
    300
  ],
  "symbols": {},
  "markets": [
    "america"
  ],
  "filter2": {
    "operator": "and",
    "operands": [
      {
        "operation": {
          "operator": "or",
          "operands": [
            {
              "operation": {
                "operator": "and",
                "operands": [
                  {
                    "expression": {
                      "left": "type",
                      "operation": "equal",
                      "right": "stock"
                    }
                  },
                  {
                    "expression": {
                      "left": "typespecs",
                      "operation": "has",
                      "right": ["common"]
                    }
                  }
                ]
              }
            },
            {
              "operation": {
                "operator": "and",
                "operands": [
                  {
                    "expression": {
                      "left": "type",
                      "operation": "equal",
                      "right": "stock"
                    }
                  },
                  {
                    "expression": {
                      "left": "typespecs",
                      "operation": "has",
                      "right": ["preferred"]
                    }
                  }
                ]
              }
            },
            {
              "operation": {
                "operator": "and",
                "operands": [
                  {
                    "expression": {
                      "left": "type",
                      "operation": "equal",
                      "right": "dr"
                    }
                  }
                ]
              }
            },
            {
              "operation": {
                "operator": "and",
                "operands": [
                  {
                    "expression": {
                      "left": "type",
                      "operation": "equal",
                      "right": "fund"
                    }
                  },
                  {
                    "expression": {
                      "left": "typespecs",
                      "operation": "has_none_of",
                      "right": ["etf"]
                    }
                  }
                ]
              }
            }
          ]
        }
      },
      {
        "expression": {
          "left": "typespecs",
          "operation": "has_none_of",
          "right": ["pre-ipo"]
        }
      }
    ]
  }
}
```

## Screen 1: Robust Shape / Safety Core Pack

### What It Does
This payload is for evaluating whether the business looks financially robust or fragile based on liquidity, leverage, margins, cash generation, and distress proxies.

Use it to rank companies inside an industry by:
- Liquidity strength
- Solvency and balance-sheet stress
- Profitability quality
- Cash flow quality
- Asset efficiency
- Distress risk

### Verified Core Fields
- Liquidity: `current_ratio`, `current_ratio_current`, `current_ratio_fq`, `current_ratio_fy`, `quick_ratio`, `cash_ratio`, `cash_n_equivalents_fq`, `cash_n_short_term_invest_fq`, `total_current_assets`
- Solvency: `total_debt`, `net_debt`, `debt_to_equity`, `debt_to_asset_fq`, `debt_to_asset_fy`, `debt_to_assets`, `debt_to_revenue_ttm`, `total_liabilities_fq`, `total_liabilities_fy`, `altman_z_score_ttm`, `altman_z_score_fy`
- Profitability: `gross_margin`, `gross_profit_margin_fy`, `operating_margin`, `oper_income_margin_fy`, `after_tax_margin`, `return_on_assets`, `return_on_equity`, `return_on_invested_capital`
- Cash flow and discipline: `cash_f_operating_activities_ttm`, `cash_f_investing_activities_ttm`, `cash_f_financing_activities_ttm`, `capital_expenditures_ttm`, `free_cash_flow_margin_ttm`, `free_cash_flow_yoy_growth_ttm`, `cash_dividend_coverage_ratio_ttm`
- Trend support: `total_revenue_yoy_growth_ttm`, `net_income_yoy_growth_ttm`, `asset_turnover_current`, `asset_turnover_fy`
- Structural context: `market_cap_basic`, `sector`, `industry`, `country`, `exchange`, `type`, `typespecs`, `fundamental_currency_code`

### Payload Fragment
Plug this into the base universe block.

```json
{
  "columns": [
    "ticker-view",
    "close",
    "type",
    "typespecs",
    "exchange",
    "country",
    "sector",
    "industry",
    "market",
    "market_cap_basic",
    "fundamental_currency_code",
    "current_ratio",
    "current_ratio_current",
    "current_ratio_fq",
    "current_ratio_fy",
    "quick_ratio",
    "cash_ratio",
    "cash_n_equivalents_fq",
    "cash_n_short_term_invest_fq",
    "total_current_assets",
    "total_liabilities_fq",
    "total_liabilities_fy",
    "total_debt",
    "net_debt",
    "debt_to_equity",
    "debt_to_asset_fq",
    "debt_to_asset_fy",
    "debt_to_assets",
    "debt_to_revenue_ttm",
    "altman_z_score_ttm",
    "altman_z_score_fy",
    "gross_margin",
    "gross_profit_margin_fy",
    "operating_margin",
    "oper_income_margin_fy",
    "after_tax_margin",
    "return_on_assets",
    "return_on_equity",
    "return_on_invested_capital",
    "cash_f_operating_activities_ttm",
    "cash_f_investing_activities_ttm",
    "cash_f_financing_activities_ttm",
    "capital_expenditures_ttm",
    "free_cash_flow_margin_ttm",
    "free_cash_flow_yoy_growth_ttm",
    "cash_dividend_coverage_ratio_ttm",
    "total_revenue_yoy_growth_ttm",
    "net_income_yoy_growth_ttm",
    "asset_turnover_current",
    "asset_turnover_fy",
    "price_earnings_ttm",
    "price_free_cash_flow_ttm",
    "price_book_fq",
    "price_revenue_ttm",
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_to_ebit_ttm",
    "enterprise_value_ebitda_ttm"
  ],
  "range": [0, 500],
  "sort": {
    "sortBy": "market_cap_basic",
    "sortOrder": "desc"
  }
}
```

### Derived Safety Checks To Compute After Download
- Net debt to market cap = `net_debt / market_cap_basic`
- Cash coverage of liabilities = `cash_n_short_term_invest_fq / total_liabilities_fq`
- Liability load vs scale = `total_liabilities_fq / market_cap_basic`
- Capex burden = `capital_expenditures_ttm / cash_f_operating_activities_ttm`
- Operating cash backing = `cash_f_operating_activities_ttm / total_debt`
- Margin stack quality check: gross margin, operating margin, net margin alignment
- Return quality check: `return_on_invested_capital` relative to `return_on_assets` and `return_on_equity`

### Practical Interpretation
- Safer companies usually combine strong liquidity ratios, positive cash operating flow, moderate debt load, positive margins, and decent Altman Z.
- Riskier companies usually combine weak liquidity, high debt to assets or debt to revenue, low or negative margins, and poor cash generation.

## Screen 1B: Deterioration / Hidden Fragility Pack

### What It Does
This is the inverse of the safety pack. It is for finding companies that still trade actively but may be deteriorating underneath.

### Payload Fragment
```json
{
  "columns": [
    "ticker-view",
    "close",
    "sector",
    "industry",
    "market_cap_basic",
    "altman_z_score_ttm",
    "debt_to_equity",
    "debt_to_assets",
    "debt_to_revenue_ttm",
    "current_ratio_fq",
    "quick_ratio",
    "cash_ratio",
    "net_debt",
    "total_liabilities_fq",
    "operating_margin",
    "after_tax_margin",
    "free_cash_flow_margin_ttm",
    "cash_f_operating_activities_ttm",
    "capital_expenditures_ttm",
    "total_revenue_yoy_growth_ttm",
    "net_income_yoy_growth_ttm",
    "free_cash_flow_yoy_growth_ttm",
    "price_earnings_ttm",
    "price_free_cash_flow_ttm",
    "Perf.Y",
    "Perf.YTD",
    "Perf.1M"
  ],
  "range": [0, 300],
  "sort": {
    "sortBy": "altman_z_score_ttm",
    "sortOrder": "asc"
  }
}
```

### What To Flag After Download
- Low Altman Z combined with high debt ratios
- Revenue holding up while net income and free cash flow growth weaken
- Positive price performance with worsening safety metrics
- Expensive valuation despite deteriorating margin and cash flow profile

## Screen 2: Activity / Float / Attention Pack

### What It Does
This payload is for share-trading activity and attention intensity. It helps identify names where participation, turnover, volatility, and abnormal trading conditions are rising.

This does not prove direction by itself. It is for detecting where price movement potential is higher because market attention is high.

### Verified Core Fields
- Liquidity and turnover: `volume`, `average_volume_10d_calc`, `average_volume_30d_calc`, `average_volume_60d_calc`, `average_volume_90d_calc`, `relative_volume_10d_calc`, `Value.Traded`, `AvgValue.Traded_10d`, `AvgValue.Traded_30d`, `AvgValue.Traded_60d`, `AvgValue.Traded_90d`, `float_shares_outstanding`
- Volatility and range: `ADR`, `ATR`, `ATRP`, `Volatility.D`, `Volatility.W`, `Volatility.M`, `beta_1_year`, `beta_3_year`, `beta_5_year`
- Immediate repricing: `change`, `gap`, `premarket_gap`, `premarket_change`, `premarket_volume`, `postmarket_change`, `postmarket_volume`
- Momentum and state: `Perf.W`, `Perf.1M`, `Perf.3M`, `Perf.YTD`, `Perf.Y`, `Perf.3Y`, `Perf.5D`, `RSI`, `RSI7`, `MACD.macd`, `MACD.signal`, `Mom`, `ROC`, `Recommend.All`, `Recommend.MA`, `Recommend.Other`, `VWAP`, `VWMA`, `SMA50`, `SMA200`, `EMA50`, `EMA200`
- Event timing: `earnings_release_date`, `earnings_release_next_date`

### Payload Fragment
```json
{
  "columns": [
    "ticker-view",
    "close",
    "exchange",
    "country",
    "sector",
    "industry",
    "market_cap_basic",
    "float_shares_outstanding",
    "volume",
    "average_volume_10d_calc",
    "average_volume_30d_calc",
    "average_volume_60d_calc",
    "average_volume_90d_calc",
    "relative_volume_10d_calc",
    "Value.Traded",
    "AvgValue.Traded_10d",
    "AvgValue.Traded_30d",
    "AvgValue.Traded_60d",
    "AvgValue.Traded_90d",
    "ADR",
    "ATR",
    "ATRP",
    "Volatility.D",
    "Volatility.W",
    "Volatility.M",
    "beta_1_year",
    "beta_3_year",
    "beta_5_year",
    "change",
    "gap",
    "premarket_gap",
    "premarket_change",
    "premarket_volume",
    "postmarket_change",
    "postmarket_volume",
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.YTD",
    "Perf.Y",
    "RSI",
    "RSI7",
    "MACD.macd",
    "MACD.signal",
    "Mom",
    "ROC",
    "Recommend.All",
    "Recommend.MA",
    "Recommend.Other",
    "VWAP",
    "VWMA",
    "SMA50",
    "SMA200",
    "EMA50",
    "EMA200",
    "earnings_release_date",
    "earnings_release_next_date"
  ],
  "range": [0, 500],
  "sort": {
    "sortBy": "relative_volume_10d_calc",
    "sortOrder": "desc"
  }
}
```

### Derived Activity Checks To Compute After Download
- Float turnover proxy = `volume / float_shares_outstanding`
- Dollar-turnover intensity = `AvgValue.Traded_10d / market_cap_basic`
- Event intensity = `premarket_volume / average_volume_10d_calc`
- Gap severity = `gap / ATRP`
- Momentum with participation = `Perf.1M` plus `relative_volume_10d_calc`
- Trend confirmation = price relative to `SMA50`, `SMA200`, `EMA50`, `EMA200`

### What This Pack Is Good For
- Detecting breakout candidates with real participation
- Detecting event-driven names before or after earnings
- Detecting high-attention names where price discovery is accelerating
- Detecting unstable names where volatility is too high for sizing comfort

## Screen 2B: Gap / Event Repricing Pack

### What It Does
This payload is narrower. It is for names reacting strongly to earnings, guidance, macro news, or sentiment shocks.

### Payload Fragment
```json
{
  "columns": [
    "ticker-view",
    "close",
    "sector",
    "industry",
    "market_cap_basic",
    "volume",
    "relative_volume_10d_calc",
    "premarket_gap",
    "premarket_change",
    "premarket_volume",
    "postmarket_change",
    "postmarket_volume",
    "gap",
    "change",
    "ATR",
    "ATRP",
    "Volatility.D",
    "Volatility.W",
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "RSI",
    "MACD.macd",
    "MACD.signal",
    "earnings_release_date",
    "earnings_release_next_date"
  ],
  "range": [0, 250],
  "sort": {
    "sortBy": "premarket_gap",
    "sortOrder": "desc"
  }
}
```

### Practical Use
- Sort descending for upside surprise monitoring
- Re-run with ascending sort for downside shock monitoring
- Compare gap size to ATR and relative volume before acting

## Screen 3: Industry Comparison Pack

### What It Does
This is the best peer-basis panel. It mixes safety, valuation, momentum, and activity so you can compare companies inside one industry or across adjacent industries.

### Payload Fragment
```json
{
  "columns": [
    "ticker-view",
    "exchange",
    "country",
    "sector",
    "industry",
    "market",
    "market_cap_basic",
    "close",
    "fundamental_currency_code",
    "total_revenue_yoy_growth_ttm",
    "net_income_yoy_growth_ttm",
    "free_cash_flow_yoy_growth_ttm",
    "gross_margin",
    "operating_margin",
    "after_tax_margin",
    "return_on_assets",
    "return_on_equity",
    "return_on_invested_capital",
    "current_ratio_fq",
    "quick_ratio",
    "cash_ratio",
    "debt_to_equity",
    "debt_to_assets",
    "net_debt",
    "altman_z_score_ttm",
    "price_earnings_ttm",
    "price_book_fq",
    "price_free_cash_flow_ttm",
    "price_revenue_ttm",
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_to_ebit_ttm",
    "enterprise_value_ebitda_ttm",
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.6M",
    "Perf.YTD",
    "Perf.Y",
    "Perf.3Y",
    "relative_volume_10d_calc",
    "AvgValue.Traded_30d",
    "float_shares_outstanding",
    "Volatility.M",
    "beta_1_year"
  ],
  "range": [0, 500],
  "sort": {
    "sortBy": "market_cap_basic",
    "sortOrder": "desc"
  }
}
```

### What To Do With It
- Rank within industry by safety metrics
- Rank within industry by valuation cheapness
- Rank within industry by growth quality
- Rank within industry by price strength and participation
- Build an industry-relative composite score rather than raw global ranks

## Screen 4: Opportunity Blend Pack

### What It Does
This is the most practical combined pack for your stated strategy: compile fundamentals with technicals in a real way to get the gist of price movement and opportunity quality.

It is not a pure safety screen and not a pure momentum screen. It is a layered evidence pack.

### Payload Fragment
```json
{
  "columns": [
    "ticker-view",
    "exchange",
    "sector",
    "industry",
    "market_cap_basic",
    "close",
    "total_revenue_yoy_growth_ttm",
    "net_income_yoy_growth_ttm",
    "free_cash_flow_yoy_growth_ttm",
    "gross_margin",
    "operating_margin",
    "after_tax_margin",
    "return_on_invested_capital",
    "free_cash_flow_margin_ttm",
    "current_ratio_fq",
    "cash_ratio",
    "debt_to_equity",
    "debt_to_assets",
    "altman_z_score_ttm",
    "price_earnings_ttm",
    "price_book_fq",
    "price_free_cash_flow_ttm",
    "price_revenue_ttm",
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_to_ebit_ttm",
    "enterprise_value_ebitda_ttm",
    "relative_volume_10d_calc",
    "AvgValue.Traded_30d",
    "Volatility.M",
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.YTD",
    "Perf.Y",
    "RSI",
    "MACD.macd",
    "MACD.signal",
    "Recommend.All",
    "Recommend.MA",
    "Recommend.Other",
    "SMA50",
    "SMA200",
    "EMA50",
    "EMA200",
    "VWAP",
    "VWMA"
  ],
  "range": [0, 400],
  "sort": {
    "sortBy": "market_cap_basic",
    "sortOrder": "desc"
  }
}
```

### What This Payload Does
- Captures business quality
- Captures balance-sheet safety
- Captures valuation paid for that quality
- Captures whether the tape is confirming the thesis
- Supports long ranking, watchlist ranking, and deterioration review

## Suggested Scoring Framework After Retrieval

### Safety Score
Use industry-relative percentile ranks for:
- `current_ratio_fq`
- `quick_ratio`
- `cash_ratio`
- `debt_to_equity`
- `debt_to_assets`
- `altman_z_score_ttm`
- `operating_margin`
- `after_tax_margin`
- `free_cash_flow_margin_ttm`
- `return_on_invested_capital`

### Activity Score
Use industry-relative percentile ranks for:
- `relative_volume_10d_calc`
- `AvgValue.Traded_30d`
- `volume / float_shares_outstanding`
- `ATRP`
- `Volatility.W`
- `gap`
- `premarket_gap`
- `Perf.1M`
- `RSI`
- `MACD.macd`

### Opportunity Score
Blend:
- 40% safety and quality
- 25% valuation
- 20% participation and activity
- 15% price trend confirmation

## Best Next Data Additions Outside Current CSV
If you want materially better movement prediction and better tactical screens, prioritize adding:
- Short interest percent of float
- Days to cover
- Earnings estimate revision fields
- Insider transaction data
- Options activity and implied volatility measures
- Institutional ownership trend
- Dividend suspension or buyback program status flags

## Practical Final Guidance
For your process, the strongest build order is:
1. Run the Safety Core Pack by industry.
2. Run the Activity Pack on the same universe.
3. Join them by ticker.
4. Add industry-relative valuation columns.
5. Score within industry, not globally.
6. Treat high activity without strong fundamentals as tactical only.
7. Treat strong fundamentals without tape confirmation as watchlist or staged entry candidates.

That gets you closer to a real hybrid process where fundamentals explain why a company deserves attention and technical and activity data explain whether the market is starting to act on it.
