# TradingView Move Prediction Profile Weighting Reference

## Why This Reference Was Added

The preset profiles were producing too much leaderboard overlap in the March 29, 2026 run located under `logs/tradingview_analysis/prediction_analysis/AllInUniverse_min1bil/29_03_2026`.

Observed overlap from that output set:

- `balanced` vs `quality_value_compounder` top 10 overlap was `10/10` on the `days` horizon.
- `balanced` vs `breakout_long` top 10 overlap was `8/10` on `days`, `7/10` on `weeks`, `8/10` on `months`, and `9/10` on `years`.
- `quality_value_compounder` vs `value_recovery` top 10 overlap was `10/10` on `days`, `9/10` on `months`, and `8/10` on `years`.

That level of agreement is too high for profiles that are supposed to represent meaningfully different opportunity lenses.

The root causes were:

1. `quality_value_compounder` only overrode `months` and `years`, so its `days` and `weeks` behavior was still very close to the default balanced model.
2. Horizon scores were previously renormalized only across present components, so missing `quality`, `valuation`, or `safety` data could let a name rank highly in a fundamental profile anyway.
3. `breakout_long` was more aggressive than balanced, but not aggressive enough to consistently separate tactical names from broad multi-factor leaders.

This reference documents the revised scoring model and the updated profile behavior.

## Score Flow

The move-prediction model works in six layers.

### 1. Raw values and derived values

Each row begins with TradingView scan fields plus internally derived metrics.

### 2. Robust normalization

For most numeric fields, the model computes a scan-relative robust signal:

```text
robust_signal = clamp((value - median) / (MAD * 1.4826), -3.0, 3.0)
```

If `MAD` is `0` or missing, the fallback is a simpler sign comparison versus the median:

- above median -> `+1.0`
- below median -> `-1.0`
- equal median -> `0.0`

### 3. Component score construction

Signals are grouped into seven components:

- `attention`
- `event`
- `momentum`
- `trend`
- `quality`
- `valuation`
- `safety`

Each component starts as a weighted average of its included signals.

```text
component_score = sum(signal_i * signal_weight_i) / sum(signal_weight_i)
```

Unless a profile overrides a signal weight, the default signal weight is `1.00`. Extended signals (added March 30, 2026) default to `0.00` so they only activate when a profile explicitly sets a nonzero weight for them.

### 4. Directional bias

After the component average is computed, the profile can bias positive and negative readings differently.

```text
if component_score > 0:
    biased_component = clamp(component_score * positive_multiplier, -3.0, 3.0)
elif component_score < 0:
    biased_component = clamp(component_score * negative_multiplier, -3.0, 3.0)
else:
    biased_component = 0.0
```

This is how one profile can reward upside momentum while another punishes downside safety failure more aggressively.

### 5. Horizon score construction

Each horizon score is a weighted average of the seven components.

```text
horizon_score = sum(component_value_j * component_weight_j) / sum(used_component_weights)
```

Where `used_component_weights` includes:

- actual component scores when available
- profile-specific missing-component fallback scores when a component is missing and the profile defines a fallback

If a component is missing and no fallback is defined, that component is skipped.

### 6. Coverage and confidence

Coverage is:

```text
coverage = used_component_weight / total_possible_component_weight
```

Confidence is:

```text
confidence = clamp(
    (28 + 18 * abs(horizon_score) + 40 * coverage - event_penalty)
    * confidence_multiplier
    + confidence_offset,
    5,
    99,
)
```

Event penalty depends on how close the next earnings date is.

## Component And Field Mapping

The tables below document how each scored field contributes.

### Attention Component

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `relative_volume_10d_calc` | raw | robust normalized | 1.00 | more attention |
| `float_turnover` | derived as `volume / float_shares_outstanding` | robust normalized | 1.00 | more attention |
| `dollar_turnover_intensity` | derived as `AvgValue.Traded_10d / market_cap_basic` | robust normalized | 1.00 | more attention |
| `Value.Traded` | raw | robust normalized | 1.00 | more attention |
| `AvgValue.Traded_10d` | raw | robust normalized | 1.00 | more attention |
| `bb_squeeze` | derived as `(BB.upper - BB.lower) / close` | robust normalized, then inverted | 0.00 (extended) | tighter bands = more coiled energy |

### Event Component

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `premarket_change` | raw | robust normalized | 1.00 | more upside event pressure |
| `postmarket_change` | raw | robust normalized | 1.00 | more upside event pressure |
| `gap` | raw | robust normalized | 1.00 | more upside gap pressure |
| `gap_severity` | derived as `gap / ATRP` | robust normalized | 1.00 | more abnormal gap pressure |
| `event_intensity` | derived as `premarket_volume / average_volume_10d_calc` | robust normalized | 1.00 | stronger event participation |

### Momentum Component

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `change` | raw | robust normalized | 1.00 | stronger near-term momentum |
| `Perf.5D` | raw | robust normalized | 1.00 | stronger 5-day momentum |
| `Perf.W` | raw | robust normalized | 1.00 | stronger 1-week momentum |
| `Perf.1M` | raw | robust normalized | 1.00 | stronger 1-month momentum |
| `Perf.3M` | raw | robust normalized | 1.00 | stronger 3-month momentum |
| `Perf.YTD` | raw | robust normalized | 1.00 | stronger YTD momentum |
| `Perf.Y` | raw | robust normalized | 1.00 | stronger 1-year momentum |
| `ROC` | raw | robust normalized | 1.00 | stronger rate-of-change momentum |
| `Mom` | raw | robust normalized | 1.00 | stronger momentum |
| `macd_spread` | derived as `MACD.macd - MACD.signal` | robust normalized | 1.00 | stronger MACD confirmation |
| `Recommend.All` | raw | robust normalized | 1.00 | stronger broad model sentiment |
| `Recommend.MA` | raw | robust normalized | 1.00 | stronger moving-average sentiment |
| `Recommend.Other` | raw | robust normalized | 1.00 | stronger non-MA sentiment |
| `rsi_centered` | derived as `RSI - 50` | robust normalized | 1.00 | stronger RSI leadership |
| `rsi7_centered` | derived as `RSI7 - 50` | robust normalized | 1.00 | stronger fast RSI leadership |
| `Perf.6M` | raw | robust normalized | 0.00 (extended) | stronger 6-month momentum |
| `aroon_spread` | derived as `Aroon.Up - Aroon.Down` | robust normalized | 0.00 (extended) | new uptrend forming |
| `adx_directional_spread` | derived as `ADX+DI - ADX-DI` | robust normalized | 0.00 (extended) | bullish directional strength emerging |

### Trend Component

Trend uses direct binary alignment fields, not robust normalization.

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `close_vs_sma50` | derived | `+1` if `close >= SMA50`, else `-1` | 1.00 | stronger trend |
| `close_vs_sma200` | derived | `+1` if `close >= SMA200`, else `-1` | 1.00 | stronger long trend |
| `close_vs_ema50` | derived | `+1` if `close >= EMA50`, else `-1` | 1.00 | stronger trend |
| `close_vs_ema200` | derived | `+1` if `close >= EMA200`, else `-1` | 1.00 | stronger long trend |
| `close_vs_vwap` | derived | `+1` if `close >= VWAP`, else `-1` | 1.00 | stronger tactical trend |
| `close_vs_vwma` | derived | `+1` if `close >= VWMA`, else `-1` | 1.00 | stronger tactical volume-weighted trend |
| `trend_alignment` | derived average of all trend comparisons | average of available `+1/-1` values | 1.00 | stronger broad alignment |
| `bb_position` | derived as `(close - BB.lower) / (BB.upper - BB.lower)` | robust normalized | 0.00 (extended) | closer to upper Bollinger Band |

### Quality Component

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `total_revenue_yoy_growth_ttm` | raw | robust normalized | 1.00 | better growth |
| `total_revenue_qoq_growth_fq` | raw | robust normalized | 1.00 | better short growth |
| `ebitda_yoy_growth_ttm` | raw | robust normalized | 1.00 | better EBITDA growth |
| `ebitda_qoq_growth_fq` | raw | robust normalized | 1.00 | better short EBITDA growth |
| `net_income_yoy_growth_ttm` | raw | robust normalized | 1.00 | better earnings growth |
| `net_income_qoq_growth_fq` | raw | robust normalized | 1.00 | better short earnings growth |
| `free_cash_flow_yoy_growth_ttm` | raw | robust normalized | 1.00 | better FCF growth |
| `free_cash_flow_qoq_growth_fq` | raw | robust normalized | 1.00 | better short FCF growth |
| `gross_margin` | raw | robust normalized | 1.00 | better economics |
| `operating_margin` | raw | robust normalized | 1.00 | better operating quality |
| `after_tax_margin` | raw | robust normalized | 1.00 | better earnings quality |
| `return_on_assets` | raw | robust normalized | 1.00 | better asset efficiency |
| `return_on_equity` | raw | robust normalized | 1.00 | better equity efficiency |
| `return_on_invested_capital` | raw | robust normalized | 1.00 | better capital discipline |
| `free_cash_flow_margin_ttm` | raw | robust normalized | 0.00 (extended) | better cash conversion |
| `buyback_yield` | raw | robust normalized | 0.00 (extended) | stronger shareholder return via buybacks |
| `dividends_yield_current` | raw | robust normalized | 0.00 (extended) | stronger income return |

### Valuation Component

Valuation uses `invert=True` and `positive_only=True` for all included fields. That means the model only scores positive multiples and treats lower multiples as better.

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `price_earnings_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `price_earnings_growth_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `price_sales_current` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `price_book_fq` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `price_free_cash_flow_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `price_to_cash_f_operating_activities_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `enterprise_value_to_revenue_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `enterprise_value_to_ebit_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `enterprise_value_ebitda_ttm` | raw | robust normalized, then inverted, positive-only | 1.00 | lower multiple is better |
| `earnings_yield` | raw | robust normalized | 0.00 (extended) | higher earnings yield = cheaper stock |
| `distance_from_52w_high` | derived as `(close - price_52_week_high) / abs(price_52_week_high)` | robust normalized, then inverted | 0.00 (extended) | further below 52-week peak = deeper discount |
| `range_position_52w` | derived as `(close - price_52_week_low) / (price_52_week_high - price_52_week_low)` | robust normalized, then inverted | 0.00 (extended) | lower in 52-week range = more upside room |

### Safety Component

`debt_to_equity`, `debt_to_revenue_ttm`, `net_debt`, and `beta_1_year` are inverted so lower values help safety.

`short_term_cash_coverage` is positive-only, so the model only uses it when the ratio is above zero.

| Signal field | Source | Transform | Default weight | Higher value effect |
| --- | --- | --- | ---: | --- |
| `current_ratio` | raw | robust normalized | 1.00 | better liquidity |
| `quick_ratio` | raw | robust normalized | 1.00 | better liquidity |
| `cash_ratio` | raw | robust normalized | 1.00 | better liquidity |
| `short_term_cash_coverage` | derived as `cash_n_short_term_invest / short_term_debt` using FY first then FQ fallback | robust normalized, positive-only | 1.00 | better short-term solvency |
| `altman_z_score_ttm` | raw | robust normalized | 1.00 | better distress profile |
| `debt_to_equity` | raw | robust normalized, then inverted | 1.00 | lower leverage is better |
| `debt_to_revenue_ttm` | raw | robust normalized, then inverted | 1.00 | lower leverage burden is better |
| `net_debt` | raw | robust normalized, then inverted | 1.00 | lower net debt is better |
| `beta_1_year` | raw | robust normalized, then inverted | 1.00 | lower volatility is safer |

## Default Horizon Weights

These weights are used by the `balanced` profile and as the starting map for all other profiles.

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.24 | 0.20 | 0.24 | 0.18 | 0.06 | 0.03 | 0.05 |
| `weeks` | 0.16 | 0.10 | 0.25 | 0.22 | 0.10 | 0.05 | 0.12 |
| `months` | 0.08 | 0.05 | 0.18 | 0.22 | 0.22 | 0.13 | 0.12 |
| `years` | 0.02 | 0.03 | 0.05 | 0.12 | 0.32 | 0.24 | 0.22 |

## Profile Overrides

Only the values listed below differ from the default `balanced` behavior. Any omitted signal weight remains `1.00`. Any omitted directional bias remains `positive=1.00`, `negative=1.00`. Any omitted missing-component fallback means the component is skipped if absent.

### 1. `balanced`

Intent:

- broad multi-factor baseline
- no signal overrides
- no directional overrides
- no missing-component fallback overrides

### 2. `breakout_long`

Intent:

- reward names already in motion
- reward participation and event pressure
- downplay deep valuation and long-duration fundamental purity

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.34 | 0.24 | 0.28 | 0.10 | 0.01 | 0.00 | 0.03 |
| `weeks` | 0.24 | 0.14 | 0.30 | 0.20 | 0.03 | 0.01 | 0.08 |
| `months` | 0.14 | 0.10 | 0.24 | 0.23 | 0.10 | 0.04 | 0.15 |
| `years` | 0.04 | 0.05 | 0.15 | 0.20 | 0.18 | 0.10 | 0.28 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `attention` | `relative_volume_10d_calc=1.45`, `float_turnover=1.30`, `dollar_turnover_intensity=1.20`, `Value.Traded=1.15`, `AvgValue.Traded_10d=1.10` |
| `event` | `premarket_change=1.15`, `postmarket_change=1.15`, `gap=1.15`, `gap_severity=1.30`, `event_intensity=1.25` |
| `momentum` | `change=1.15`, `Perf.5D=1.35`, `Perf.W=1.25`, `Perf.1M=1.15`, `Perf.3M=1.05`, `Perf.YTD=0.75`, `Perf.Y=0.40`, `ROC=1.10`, `Mom=1.10`, `macd_spread=1.15`, `Recommend.MA=1.10`, `rsi7_centered=1.10` |
| `trend` | `trend_alignment=1.30`, `close_vs_sma50=1.15`, `close_vs_sma200=0.80`, `close_vs_ema50=1.15`, `close_vs_ema200=0.85`, `close_vs_vwap=1.20`, `close_vs_vwma=1.20` |

Interpretation:

- fast price action matters more than deep history
- shorter-term trend markers matter more than 200-day references
- long-duration momentum fields are intentionally muted so this profile stays tactical

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 1.25 | 0.80 |
| `event` | 1.20 | 0.80 |
| `momentum` | 1.28 | 0.78 |
| `trend` | 1.18 | 0.85 |
| `quality` | 1.00 | 0.85 |
| `valuation` | 0.95 | 0.80 |
| `safety` | 1.00 | 0.82 |

Interpretation:

- positive tactical evidence is amplified
- negative tactical evidence is not allowed to dominate as much as in balanced
- valuation and safety are deliberately softened because breakout names are often expensive and not always low-volatility

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.70`, `event=-0.45`, `momentum=-0.65`, `trend=-0.40` |
| `weeks` | `attention=-0.45`, `momentum=-0.50`, `trend=-0.35` |
| `months` | `momentum=-0.35`, `trend=-0.30` |

Interpretation:

- a breakout profile should not treat missing tactical confirmation as neutral

### 3. `quality_value_compounder`

Intent:

- reward durable profitability, efficiency, and balance-sheet quality
- reward reasonable valuation
- stop tactical excitement from dominating the leaderboard
- explicitly penalize names with sparse fundamental support

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.03 | 0.01 | 0.06 | 0.18 | 0.34 | 0.16 | 0.22 |
| `weeks` | 0.03 | 0.02 | 0.07 | 0.16 | 0.32 | 0.18 | 0.22 |
| `months` | 0.02 | 0.02 | 0.08 | 0.16 | 0.31 | 0.18 | 0.23 |
| `years` | 0.00 | 0.01 | 0.02 | 0.08 | 0.41 | 0.25 | 0.23 |

This is the main fix for the earlier similarity problem. The profile now defines its own `days` and `weeks` behavior instead of inheriting the balanced tactical map.

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `momentum` | `change=0.15`, `Perf.5D=0.15`, `Perf.W=0.20`, `Perf.1M=0.40`, `Perf.3M=1.05`, `Perf.YTD=1.05`, `Perf.Y=1.20`, `ROC=0.40`, `Mom=0.45`, `macd_spread=0.35`, `Recommend.All=0.70`, `Recommend.MA=0.55`, `Recommend.Other=0.65`, `rsi_centered=0.20`, `rsi7_centered=0.10` |
| `trend` | `close_vs_sma50=0.85`, `close_vs_sma200=1.35`, `close_vs_ema50=0.85`, `close_vs_ema200=1.35`, `close_vs_vwap=0.25`, `close_vs_vwma=0.40`, `trend_alignment=1.20` |
| `quality` | `total_revenue_yoy_growth_ttm=1.10`, `total_revenue_qoq_growth_fq=0.85`, `ebitda_yoy_growth_ttm=1.15`, `ebitda_qoq_growth_fq=0.90`, `net_income_yoy_growth_ttm=1.05`, `net_income_qoq_growth_fq=0.85`, `free_cash_flow_yoy_growth_ttm=1.20`, `free_cash_flow_qoq_growth_fq=1.00`, `gross_margin=1.10`, `operating_margin=1.20`, `after_tax_margin=1.10`, `return_on_assets=1.05`, `return_on_equity=1.10`, `return_on_invested_capital=1.30` |
| `valuation` | `price_earnings_ttm=1.10`, `price_earnings_growth_ttm=1.15`, `price_sales_current=1.05`, `price_book_fq=0.90`, `price_free_cash_flow_ttm=1.20`, `price_to_cash_f_operating_activities_ttm=1.10`, `enterprise_value_to_revenue_ttm=1.05`, `enterprise_value_to_ebit_ttm=0.95`, `enterprise_value_ebitda_ttm=1.15` |
| `safety` | `current_ratio=1.05`, `quick_ratio=1.05`, `cash_ratio=1.00`, `short_term_cash_coverage=1.15`, `altman_z_score_ttm=1.20`, `debt_to_equity=1.20`, `debt_to_revenue_ttm=1.15`, `net_debt=1.10`, `beta_1_year=0.75` |

Interpretation:

- short-term tape fields are heavily muted
- 200-day structure matters more than intraday or fast tactical trend markers
- return-on-capital, operating margin, and cash generation matter more than short bursts of price strength

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `momentum` | 1.00 | 0.85 |
| `trend` | 1.05 | 1.10 |
| `quality` | 1.20 | 1.25 |
| `valuation` | 1.18 | 1.15 |
| `safety` | 1.15 | 1.25 |

Interpretation:

- positive quality and valuation help more than in balanced
- negative quality and safety hurt more than in balanced
- momentum weakness is partially tolerated because great compounders do not always have the hottest tape

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `quality=-0.90`, `valuation=-0.75`, `safety=-0.70` |
| `weeks` | `quality=-1.00`, `valuation=-0.80`, `safety=-0.80` |
| `months` | `quality=-1.10`, `valuation=-0.90`, `safety=-0.85` |
| `years` | `quality=-1.20`, `valuation=-1.00`, `safety=-0.95` |

Interpretation:

- sparse fundamentals now materially hurt this profile
- this specifically addresses the prior problem where a name with missing quality and valuation could still rank highly on trend or event strength alone

### 4. `value_recovery`

Intent:

- reward cheap names with improving fundamentals and solvency
- tolerate imperfect short-term momentum more than balanced or breakout
- distinguish rerating candidates from already-crowded momentum winners

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.05 | 0.05 | 0.04 | 0.12 | 0.22 | 0.28 | 0.24 |
| `weeks` | 0.06 | 0.05 | 0.08 | 0.16 | 0.20 | 0.24 | 0.21 |
| `months` | 0.03 | 0.03 | 0.06 | 0.16 | 0.25 | 0.27 | 0.20 |
| `years` | 0.00 | 0.01 | 0.01 | 0.08 | 0.28 | 0.35 | 0.27 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `momentum` | `change=0.40`, `Perf.5D=0.30`, `Perf.W=0.35`, `Perf.1M=0.55`, `Perf.3M=1.10`, `Perf.YTD=1.05`, `Perf.Y=0.90`, `ROC=0.55`, `Mom=0.60`, `macd_spread=0.50`, `Recommend.All=0.70`, `Recommend.MA=0.60`, `Recommend.Other=0.70`, `rsi_centered=0.25`, `rsi7_centered=0.15` |
| `valuation` | `price_earnings_ttm=1.10`, `price_earnings_growth_ttm=1.20`, `price_book_fq=1.20`, `price_sales_current=1.10`, `price_free_cash_flow_ttm=1.05`, `enterprise_value_to_revenue_ttm=1.10`, `enterprise_value_ebitda_ttm=1.15` |
| `quality` | `free_cash_flow_yoy_growth_ttm=1.20`, `free_cash_flow_qoq_growth_fq=1.05`, `net_income_yoy_growth_ttm=1.10`, `net_income_qoq_growth_fq=1.05`, `ebitda_yoy_growth_ttm=1.05`, `operating_margin=1.10`, `after_tax_margin=1.05` |
| `safety` | `short_term_cash_coverage=1.10`, `altman_z_score_ttm=1.20`, `debt_to_equity=1.15`, `debt_to_revenue_ttm=1.10`, `net_debt=1.15` |
| `trend` | `close_vs_sma50=0.90`, `close_vs_sma200=1.15`, `close_vs_ema50=0.90`, `close_vs_ema200=1.15`, `close_vs_vwap=0.60`, `close_vs_vwma=0.75`, `trend_alignment=1.10` |

Interpretation:

- very short-term momentum is muted, but medium-term recovery trend is still allowed to matter
- cheapness, cash flow improvement, and balance-sheet repair matter most

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `momentum` | 0.90 | 0.65 |
| `trend` | 1.05 | 0.90 |
| `valuation` | 1.25 | 1.10 |
| `quality` | 1.12 | 0.95 |
| `safety` | 1.12 | 1.05 |

Interpretation:

- this profile deliberately dampens negative momentum punishment
- cheap, improving names should not be pushed too far down just because the tape still looks ugly

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `valuation=-0.75`, `quality=-0.45`, `safety=-0.55` |
| `weeks` | `valuation=-0.85`, `quality=-0.55`, `safety=-0.65` |
| `months` | `valuation=-0.95`, `quality=-0.65`, `safety=-0.75` |
| `years` | `valuation=-1.05`, `quality=-0.75`, `safety=-0.85` |

Interpretation:

- you cannot earn a strong recovery score without actual evidence of cheapness and stabilization

### 5. `fragility_short`

Intent:

- penalize downside momentum, downside event pressure, and weak safety harder than balanced
- make the short profile less likely to surface the same bullish tactical leaders simply because a few components are missing

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.18 | 0.28 | 0.28 | 0.18 | 0.02 | 0.01 | 0.05 |
| `weeks` | 0.10 | 0.18 | 0.28 | 0.24 | 0.05 | 0.01 | 0.14 |
| `months` | 0.04 | 0.08 | 0.22 | 0.24 | 0.12 | 0.04 | 0.26 |
| `years` | 0.00 | 0.03 | 0.08 | 0.15 | 0.16 | 0.05 | 0.53 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `event` | `premarket_change=1.15`, `postmarket_change=1.15`, `gap=1.15`, `gap_severity=1.25`, `event_intensity=1.10` |
| `momentum` | `change=1.15`, `Perf.5D=1.20`, `Perf.W=1.20`, `Perf.1M=1.10`, `Perf.3M=1.05`, `ROC=1.10` |
| `trend` | `close_vs_sma50=1.10`, `close_vs_sma200=1.15`, `close_vs_ema50=1.10`, `close_vs_ema200=1.15`, `close_vs_vwap=1.10`, `close_vs_vwma=1.10`, `trend_alignment=1.20` |
| `safety` | `current_ratio=1.05`, `short_term_cash_coverage=1.10`, `debt_to_equity=1.25`, `debt_to_revenue_ttm=1.20`, `net_debt=1.15`, `altman_z_score_ttm=1.25`, `beta_1_year=1.05` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `event` | 0.90 | 1.20 |
| `momentum` | 0.85 | 1.20 |
| `trend` | 0.90 | 1.25 |
| `quality` | 0.90 | 1.15 |
| `safety` | 0.85 | 1.35 |

Interpretation:

- downside evidence is amplified more than upside evidence
- safety breakdown is the most aggressive short-side lever, especially on long horizons

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `event=-0.40`, `momentum=-0.45`, `trend=-0.35` |
| `weeks` | `momentum=-0.50`, `trend=-0.40`, `safety=-0.45` |
| `months` | `trend=-0.45`, `safety=-0.60` |
| `years` | `safety=-0.80`, `quality=-0.35` |

Interpretation:

- when fragility evidence is missing, the profile no longer assumes neutrality by default

### 6. `backtest_period_ladder`

Intent:

- align horizons to trailing performance windows for retrospective tracking
- mute circular use of the same `Perf.*` fields being used both for scoring and for subsequent validation

This profile remains unchanged in principle. Its special behavior is:

- `Perf.W`, `Perf.1M`, `Perf.YTD`, and `Perf.Y` are set to `0.00` inside the momentum component
- performance tracking maps are:
  - `days -> Perf.W`
  - `weeks -> Perf.1M`
  - `months -> Perf.YTD`
  - `years -> Perf.Y` and `Perf.5Y`

### 7. `asymmetric_value`

Intent:

- surface deep value discounts with asymmetric upside potential
- combine traditional multiples with forward-looking signals: earnings yield, FCF margin, shareholder returns
- use price-position metrics (distance from 52-week high, range position in 52-week window) to identify over-discounted names
- maintain a strong safety floor across all horizons to avoid value traps
- tolerate weak recent momentum — cheap-but-unloved names should not be penalized for lack of short-term price action

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.02 | 0.02 | 0.06 | 0.15 | 0.20 | 0.35 | 0.20 |
| `weeks` | 0.03 | 0.03 | 0.08 | 0.14 | 0.22 | 0.30 | 0.20 |
| `months` | 0.02 | 0.02 | 0.06 | 0.12 | 0.26 | 0.32 | 0.20 |
| `years` | 0.00 | 0.01 | 0.02 | 0.07 | 0.28 | 0.38 | 0.24 |

Valuation dominates every horizon. Quality and safety together account for 40-52% of each horizon, guaranteeing that cheap names without fundamental support are filtered out.

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `momentum` | `change=0.25`, `Perf.5D=0.30`, `Perf.W=0.35`, `Perf.1M=0.55`, `Perf.3M=1.15`, `Perf.6M=1.10`, `Perf.YTD=1.05`, `Perf.Y=0.80`, `ROC=0.50`, `Mom=0.55`, `macd_spread=0.45`, `Recommend.All=0.65`, `Recommend.MA=0.55`, `Recommend.Other=0.60`, `rsi_centered=0.20`, `rsi7_centered=0.10` |
| `trend` | `close_vs_sma50=0.80`, `close_vs_sma200=1.30`, `close_vs_ema50=0.80`, `close_vs_ema200=1.30`, `close_vs_vwap=0.50`, `close_vs_vwma=0.60`, `trend_alignment=1.15` |
| `quality` | `total_revenue_yoy_growth_ttm=1.05`, `total_revenue_qoq_growth_fq=0.90`, `ebitda_yoy_growth_ttm=1.10`, `ebitda_qoq_growth_fq=0.95`, `net_income_yoy_growth_ttm=1.05`, `net_income_qoq_growth_fq=0.90`, `free_cash_flow_yoy_growth_ttm=1.25`, `free_cash_flow_qoq_growth_fq=1.05`, `gross_margin=1.05`, `operating_margin=1.15`, `after_tax_margin=1.05`, `return_on_assets=1.00`, `return_on_equity=1.05`, `return_on_invested_capital=1.30`, `free_cash_flow_margin_ttm=1.30`, `buyback_yield=1.10`, `dividends_yield_current=0.90` |
| `valuation` | `price_earnings_ttm=1.10`, `price_earnings_growth_ttm=1.15`, `price_sales_current=1.00`, `price_book_fq=1.10`, `price_free_cash_flow_ttm=1.20`, `price_to_cash_f_operating_activities_ttm=1.05`, `enterprise_value_to_revenue_ttm=1.00`, `enterprise_value_to_ebit_ttm=0.95`, `enterprise_value_ebitda_ttm=1.10`, `earnings_yield=1.30`, `distance_from_52w_high=1.40`, `range_position_52w=1.25` |
| `safety` | `current_ratio=1.05`, `quick_ratio=1.05`, `cash_ratio=1.00`, `short_term_cash_coverage=1.15`, `altman_z_score_ttm=1.25`, `debt_to_equity=1.20`, `debt_to_revenue_ttm=1.15`, `net_debt=1.15`, `beta_1_year=0.80` |

Interpretation:

- extended valuation signals are the highest-weighted new additions: `distance_from_52w_high=1.40` (how far below peak — bigger discount = stronger signal), `earnings_yield=1.30` (how cheap on an earnings basis), `range_position_52w=1.25` (how low in the 52-week price range)
- quality is enriched with `free_cash_flow_margin_ttm=1.30` and `buyback_yield=1.10` for forward-looking cash generation and capital return
- short-term momentum is aggressively damped below 1.0; medium-term recovery signals (`Perf.3M`, `Perf.6M`) carry more weight
- trend emphasizes 200-day structure over intraday levels

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `momentum` | 0.85 | 0.60 |
| `trend` | 1.00 | 0.85 |
| `quality` | 1.15 | 1.20 |
| `valuation` | 1.30 | 1.00 |
| `safety` | 1.10 | 1.30 |

Interpretation:

- positive valuation evidence (deep value) is amplified to 1.30x so genuinely cheap names stand out
- negative safety is amplified to 1.30x so balance-sheet deterioration kills the score — this is the main value-trap guard
- momentum in either direction is deliberately suppressed; the profile is patient about price action

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `valuation=-0.80`, `quality=-0.55`, `safety=-0.60` |
| `weeks` | `valuation=-0.90`, `quality=-0.65`, `safety=-0.70` |
| `months` | `valuation=-1.00`, `quality=-0.75`, `safety=-0.80` |
| `years` | `valuation=-1.10`, `quality=-0.85`, `safety=-0.90` |

Interpretation:

- you cannot earn an asymmetric-value score without actual evidence of cheapness, quality, and safety
- the penalties are comparable to `value_recovery` but slightly heavier on the safety side

### 8. `early_momentum_inflection`

Intent:

- catch names at the very beginning of a directional move before the crowd notices
- use Aroon spread for new-trend detection: when Aroon.Up rises above Aroon.Down, a new uptrend is forming
- use ADX directional spread for emerging directional strength: when ADX+DI exceeds ADX-DI, bullish momentum is building
- use Bollinger Band squeeze for coiled-energy setups: tight bands signal compressed volatility about to expand
- de-emphasize established long-duration momentum so names already well into a run do not dominate

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.22 | 0.14 | 0.32 | 0.22 | 0.03 | 0.02 | 0.05 |
| `weeks` | 0.16 | 0.08 | 0.30 | 0.28 | 0.06 | 0.03 | 0.09 |
| `months` | 0.08 | 0.04 | 0.24 | 0.26 | 0.14 | 0.08 | 0.16 |
| `years` | 0.02 | 0.02 | 0.10 | 0.18 | 0.24 | 0.18 | 0.26 |

Momentum + trend together account for 54-60% on short horizons. Fundamentals ramp up on longer horizons so the profile does not chase pure technicals indefinitely.

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `attention` | `relative_volume_10d_calc=1.30`, `float_turnover=1.25`, `dollar_turnover_intensity=1.15`, `Value.Traded=1.10`, `AvgValue.Traded_10d=1.00`, `bb_squeeze=1.30` |
| `event` | `premarket_change=1.10`, `postmarket_change=1.10`, `gap=1.10`, `gap_severity=1.20`, `event_intensity=1.15` |
| `momentum` | `change=1.00`, `Perf.5D=1.30`, `Perf.W=1.15`, `Perf.1M=0.85`, `Perf.3M=0.60`, `Perf.6M=0.40`, `Perf.YTD=0.40`, `Perf.Y=0.25`, `ROC=1.20`, `Mom=1.15`, `macd_spread=1.25`, `Recommend.All=0.85`, `Recommend.MA=1.00`, `Recommend.Other=0.90`, `rsi_centered=0.80`, `rsi7_centered=1.05`, `aroon_spread=1.45`, `adx_directional_spread=1.35` |
| `trend` | `close_vs_sma50=1.10`, `close_vs_sma200=0.85`, `close_vs_ema50=1.10`, `close_vs_ema200=0.85`, `close_vs_vwap=1.25`, `close_vs_vwma=1.20`, `trend_alignment=1.10`, `bb_position=1.20` |

Interpretation:

- `aroon_spread=1.45` is the single highest-weighted momentum signal: when Aroon Up exceeds Aroon Down, the indicator is specifically designed to detect new trend formation within a lookback window
- `adx_directional_spread=1.35` catches bullish directional divergence: a positive spread means buying pressure is building relative to selling pressure, even if ADX itself is still low (indicating a new trend, not an established one)
- `bb_squeeze=1.30` in attention detects compressed volatility (inverted band width; tighter bands = higher score) — this surfaces names where price has been consolidating and an expansion is likely
- `bb_position=1.20` in trend measures where price sits within its Bollinger Bands — a high reading means price is pressing toward the upper band
- established momentum signals (`Perf.Y=0.25`, `Perf.YTD=0.40`, `Perf.6M=0.40`) are heavily muted so names already well into a trend do not crowd out fresh inflection candidates
- short-term inflection signals (`Perf.5D=1.30`, `ROC=1.20`, `macd_spread=1.25`) carry the weight because they measure the most recent acceleration
- tactical trend markers (`close_vs_vwap=1.25`, `close_vs_vwma=1.20`) are elevated over 200-day markers because the profile cares about where price is now relative to recent volume distribution, not secular trend

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 1.20 | 0.80 |
| `event` | 1.10 | 0.90 |
| `momentum` | 1.30 | 0.75 |
| `trend` | 1.20 | 0.85 |
| `quality` | 1.00 | 0.85 |
| `safety` | 1.00 | 0.85 |

Interpretation:

- positive momentum evidence is amplified to 1.30x — the profile strongly rewards emergent upside direction
- negative momentum is suppressed to 0.75x — a name pulling back from a recent high is not as interesting as one starting to move
- attention and trend are also asymmetrically biased toward positive readings

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.55`, `momentum=-0.60`, `trend=-0.40` |
| `weeks` | `momentum=-0.45`, `trend=-0.35` |
| `months` | `momentum=-0.30` |

Interpretation:

- the profile requires actual inflection evidence on short horizons
- longer horizons are more tolerant because fundamental support becomes more important there

## How To Read The Profiles Now

### If you want tactical upside continuation

Use `breakout_long`.

You should expect the leaders to have:

- abnormal activity
- strong gap or event pressure
- strong short-term momentum
- clean tactical trend confirmation

You should not expect it to prefer the cheapest or safest names.

### If you want durable quality leadership

Use `quality_value_compounder`.

You should expect the leaders to have:

- stronger profitability and returns on capital
- better balance-sheet quality
- more acceptable valuation
- less dependence on one-day event pressure

Names with missing fundamentals should rank worse than they did before.

### If you want rerating or recovery ideas

Use `value_recovery`.

You should expect the leaders to have:

- cheaper valuation
- improving safety and cash generation
- medium-term trend improvement
- imperfect but not disastrous short-term momentum

### If you want short-fragility candidates

Use `fragility_short`.

You should expect the leaders to have:

- worsening safety
- worsening trend and momentum
- downside event pressure
- weak quality support

### If you want asymmetric value with forward-looking conviction

Use `asymmetric_value`.

You should expect the leaders to have:

- deep value discount: cheap on multiples, high earnings yield, far below 52-week highs, low in 52-week range
- robust fundamental floor: strong FCF margins, capital discipline, reasonable safety
- tolerance for weak recent momentum: the profile deliberately dampens short-term price noise so cheap-but-unloved names surface
- protection against value traps: safety carries meaningful weight across all horizons and missing safety evidence is penalized

This profile is built for active decisions where you believe the market over-discounted the name and want to size a position with favorable risk/reward asymmetry.

### If you want early trend inflection detection

Use `early_momentum_inflection`.

You should expect the leaders to have:

- emerging directional shift: high Aroon spread (new uptrend forming), positive ADX directional spread (bullish direction strengthening)
- coiled setup energy: tight Bollinger Band squeeze, rising very short-term momentum
- fresh volume pickup: volume confirmation that participation is increasing on the move
- muted legacy momentum: names already well into a long run are deliberately de-emphasized so inflection candidates rise

This profile is built for catching the first winds of a directional move before it becomes obvious to the broader market.

## Similarity Issue Addressed Explicitly

The earlier similarity problem came from two structural effects, not just from superficial weight choices.

### Effect 1: profile inheritance was too broad

`quality_value_compounder` behaved too much like `balanced` on shorter horizons because it inherited the tactical default map.

That is now fixed by giving it its own `days` and `weeks` horizon weights.

### Effect 2: missing important components did not hurt enough

A profile could say it cared about `quality`, `valuation`, or `safety`, but if those fields were absent the horizon score just renormalized around the remaining components.

That is now fixed by per-profile missing-component fallback scores.

Practical consequence:

- `quality_value_compounder` now has to actually see quality, valuation, and safety support
- `value_recovery` now has to actually see rerating and stabilization evidence
- `breakout_long` now has to actually see tactical confirmation
- `fragility_short` now has to actually see enough fragility evidence to deserve ranking highly

## Confidence Controls

Confidence does not change ranking directly, but it changes how much trust the report signals should receive.

Current profile confidence parameters:

| Profile | Confidence multiplier | Confidence offset |
| --- | ---: | ---: |
| `balanced` | 1.00 | 0.00 |
| `breakout_long` | 1.07 | 0.00 |
| `quality_value_compounder` | 1.02 | 0.00 |
| `value_recovery` | 1.00 | 0.00 |
| `fragility_short` | 1.07 | 0.00 |
| `backtest_period_ladder` | 1.03 | 0.00 |
| `asymmetric_value` | 1.02 | 0.00 |
| `early_momentum_inflection` | 1.05 | 0.00 |

These are secondary to score construction. The main ranking differentiation comes from:

1. horizon weights
2. signal weights
3. directional bias
4. missing-component fallbacks

## Summary

The revised preset logic is intentionally less interchangeable.

- `breakout_long` is now more tactical and less valuation-sensitive.
- `quality_value_compounder` is now more fundamentally strict across every horizon.
- `value_recovery` is now more explicitly rerating-focused.
- `fragility_short` is now more explicitly downside-fragility-focused.
- `asymmetric_value` surfaces deep-discount names with forward-looking value conviction and a safety floor against value traps.
- `early_momentum_inflection` catches early directional winds via Aroon, ADX, and Bollinger Band signals before established momentum dominates.
- missing data can no longer disappear as easily inside the profile types that are supposed to care about specific evidence.

## Extended Signal System (March 30, 2026)

Eleven new signals were added to the component signal map but placed behind a backward-compatible gating system.

### How it works

Signals in the `EXTENDED_SIGNALS` set default to weight `0.00` when a profile does not explicitly override them. This means:

- existing profiles (`balanced`, `breakout_long`, `quality_value_compounder`, `value_recovery`, `fragility_short`, `backtest_period_ladder`) continue to behave identically
- new profiles (`asymmetric_value`, `early_momentum_inflection`) activate the extended signals they need by setting nonzero weights
- any future or custom profile can opt into arbitrary subsets of the extended signals

### Extended signal list

| Signal | Component | Source | Description |
| --- | --- | --- | --- |
| `Perf.6M` | momentum | raw | 6-month price performance |
| `aroon_spread` | momentum | derived (`Aroon.Up - Aroon.Down`) | new trend detection |
| `adx_directional_spread` | momentum | derived (`ADX+DI - ADX-DI`) | directional strength emergence |
| `bb_position` | trend | derived (`(close - BB.lower) / (BB.upper - BB.lower)`) | position within Bollinger Bands |
| `bb_squeeze` | attention | derived (`(BB.upper - BB.lower) / close`, inverted) | band compression = coiled energy |
| `earnings_yield` | valuation | raw | earnings-to-price yield (higher = cheaper) |
| `distance_from_52w_high` | valuation | derived (`(close - 52w_high) / abs(52w_high)`, inverted) | discount from peak |
| `range_position_52w` | valuation | derived (`(close - 52w_low) / (52w_high - 52w_low)`, inverted) | position in 52-week range |
| `free_cash_flow_margin_ttm` | quality | raw | cash generation quality |
| `buyback_yield` | quality | raw | shareholder return via buybacks |
| `dividends_yield_current` | quality | raw | income return |

### New raw fields added to scan payload

The following columns were added to the TradingView scan payload (`GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD`):

`price_52_week_high`, `price_52_week_low`, `High.6M`, `Low.6M`, `earnings_yield`, `free_cash_flow_margin_ttm`, `buyback_yield`, `dividends_yield_current`, `Aroon.Up`, `Aroon.Down`, `ADX`, `ADX+DI`, `ADX-DI`, `BB.upper`, `BB.lower`

That combination is what should reduce the excessive profile similarity seen in the March 29, 2026 results.