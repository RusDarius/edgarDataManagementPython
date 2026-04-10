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
| `range_compression` | derived as `(High.3M - Low.3M) / close` | robust normalized, then inverted | 0.00 (extended) | tighter 3-month range = more coiled energy |
| `volatility_contraction` | derived as `Volatility.D / Volatility.M` | robust normalized, then inverted | 0.00 (extended) | daily vol lower than monthly = contraction |
| `volume_trend` | derived as `average_volume_10d_calc / average_volume_30d_calc` | robust normalized | 0.00 (extended) | rising short-term vs medium-term volume = accumulation |
| `intraday_momentum` | raw `change_from_open` | robust normalized | 0.00 (extended) | stronger open-to-current momentum |

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
| `stoch_rsi_centered` | derived as `Stoch.RSI.K - 50` | robust normalized | 0.00 (extended) | stronger short-term momentum impulse |
| `CCI20` | raw | robust normalized | 0.00 (extended) | stronger directional thrust |
| `stoch_rsi_crossover` | derived as `Stoch.RSI.K - Stoch.RSI.D` | robust normalized | 0.00 (extended) | bullish Stoch RSI crossover signal |

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
| `close_vs_sma10` | derived | `+1` if `close >= SMA10`, else `-1` | 0.00 (extended) | above shortest-term average |
| `close_vs_sma20` | derived | `+1` if `close >= SMA20`, else `-1` | 0.00 (extended) | above 20-day average |
| `close_vs_sma30` | derived | `+1` if `close >= SMA30`, else `-1` | 0.00 (extended) | above 30-day average |
| `close_vs_ema10` | derived | `+1` if `close >= EMA10`, else `-1` | 0.00 (extended) | above 10-day EMA |
| `close_vs_ema20` | derived | `+1` if `close >= EMA20`, else `-1` | 0.00 (extended) | above 20-day EMA |
| `close_vs_ema30` | derived | `+1` if `close >= EMA30`, else `-1` | 0.00 (extended) | above 30-day EMA |
| `short_trend_emergence` | derived average of six short-term MA comparisons | average of `+1/-1` values | 0.00 (extended) | more short-term MAs crossed above |
| `pivot_distance` | derived as `(close - Pivot.M.Classic.Middle) / close` | robust normalized | 0.00 (extended) | stock above monthly pivot = support confirmation |

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
| `eps_forward_growth` | derived as `(forecast_next_fq - actual_fq) / abs(actual_fq)` | robust normalized | 0.00 (extended) | analysts expect EPS improvement next quarter |
| `revenue_per_employee` | derived as `total_revenue / number_of_employees` | robust normalized | 0.00 (extended) | higher operational efficiency per head |
| `earnings_per_share_diluted_yoy_growth_ttm` | raw | robust normalized | 0.00 (extended) | stronger diluted EPS growth trajectory |
| `gross_profit_margin_fy` | raw | robust normalized | 0.00 (extended) | better annual gross profit margin stability |

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

These weights are the starting map for profiles that inherit specific horizons. The `scale` component was added alongside the original seven and carries small weight by default.

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety | Scale |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.16 | 0.12 | 0.20 | 0.20 | 0.12 | 0.06 | 0.08 | 0.06 |
| `weeks` | 0.12 | 0.06 | 0.20 | 0.22 | 0.14 | 0.08 | 0.12 | 0.06 |
| `months` | 0.06 | 0.03 | 0.14 | 0.22 | 0.24 | 0.14 | 0.12 | 0.05 |
| `years` | 0.02 | 0.02 | 0.04 | 0.12 | 0.32 | 0.24 | 0.20 | 0.04 |

## Profile Design Framework

The 8 profiles are organized into three investment ideas:

### Idea 1 — Trend Following (where the market is moving)
- **breakout_long**: confirmed upside continuation — volume-driven tactical momentum
- **early_momentum_inflection**: catches nascent moves before crowd confirmation — coiled-energy detection

### Idea 2 — Real Quality Identification (ahead of time)
- **quality_value_compounder**: durable quality identification — persistent profitability and capital discipline
- **sector_relative_outperformer**: best-in-class operators by relative strength — efficiency and accumulation
- **forward_edge_active**: forward-looking "what is getting better?" — QoQ improvement and emerging trend

### Idea 3 — Overlooked Fundamentals (unrewarded quality and hedging)
- **asymmetric_value**: quality NOT rewarded by the market — inverted momentum bias to surface mispricing
- **value_recovery**: turnaround with sequential improvement — QoQ dominates YoY
- **fragility_short**: structurally fragile names for short/hedge baskets — all biases inverted to amplify negatives

### Removed profiles
- **balanced**: removed — its role is now covered by `quality_value_compounder` which provides better differentiation. An alias maps `balanced` → `quality_value_compounder` for backward compatibility.
- **backtest_period_ladder**: removed — its retrospective tracking role is now covered by `sector_relative_outperformer`. An alias maps `backtest_period_ladder` → `sector_relative_outperformer` for backward compatibility.

## Profile Overrides

Only the values listed below differ from the default behavior. Any omitted signal weight remains `1.00`. Any omitted directional bias remains `positive=1.00`, `negative=1.00`. Any omitted missing-component fallback means the component is skipped if absent.

### 1. `breakout_long`

Idea: **Trend Following** — confirmed upside continuation

Intent:

- reward names already in motion with volume confirmation
- reward participation and event pressure
- downplay deep valuation and long-duration fundamental purity
- quality weight at 0.00 on days — pure tactical momentum

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.30 | 0.18 | 0.30 | 0.16 | 0.00 | 0.00 | 0.06 |
| `weeks` | 0.20 | 0.10 | 0.28 | 0.28 | 0.02 | 0.00 | 0.12 |
| `months` | 0.10 | 0.06 | 0.22 | 0.30 | 0.10 | 0.04 | 0.18 |
| `years` | 0.03 | 0.03 | 0.12 | 0.22 | 0.18 | 0.12 | 0.30 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `attention` | `relative_volume_10d_calc=1.50`, `float_turnover=1.35`, `dollar_turnover_intensity=1.25`, `Value.Traded=1.20`, `AvgValue.Traded_10d=1.15`, `volume_trend=1.40`, `intraday_momentum=1.25`, `bb_squeeze=0.60` |
| `event` | `premarket_change=1.20`, `postmarket_change=1.20`, `gap=1.20`, `gap_severity=1.35`, `event_intensity=1.30` |
| `momentum` | `change=1.20`, `Perf.5D=1.40`, `Perf.W=1.30`, `Perf.1M=1.15`, `Perf.3M=1.00`, `Perf.YTD=0.60`, `Perf.Y=0.20`, `ROC=1.15`, `Mom=1.15`, `macd_spread=1.20`, `Recommend.MA=1.15`, `rsi7_centered=1.15`, `adx_directional_spread=1.50`, `aroon_spread=1.45` |
| `trend` | `trend_alignment=1.35`, `close_vs_sma50=1.20`, `close_vs_sma200=0.70`, `close_vs_ema50=1.20`, `close_vs_ema200=0.75`, `close_vs_vwap=1.25`, `close_vs_vwma=1.25`, `close_vs_sma10=1.30`, `close_vs_ema10=1.30` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 1.35 | 0.70 |
| `event` | 1.25 | 0.75 |
| `momentum` | 1.40 | 0.65 |
| `trend` | 1.30 | 0.75 |
| `quality` | 1.00 | 0.80 |
| `valuation` | 0.90 | 0.75 |
| `safety` | 1.00 | 0.80 |

Interpretation:

- most aggressive upside momentum bias in the system (+1.40x/−0.65x)
- ADX directional spread (1.50) and Aroon spread (1.45) catch emerging directional strength
- quality=0.00 at days means pure tactical scoring
- bb_squeeze dampened to 0.60 — breakouts should already be moving, not coiling

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.85`, `event=-0.55`, `momentum=-0.80`, `trend=-0.50` |
| `weeks` | `attention=-0.60`, `momentum=-0.65`, `trend=-0.45` |
| `months` | `momentum=-0.45`, `trend=-0.40` |

### 2. `early_momentum_inflection`

Idea: **Trend Following** — catches nascent moves before crowd confirmation

Intent:

- catch names at the very beginning of a directional move before the crowd notices
- detect coiled-energy setups via Bollinger Band squeeze and range compression
- use Aroon spread (highest in system at 1.65) for new-trend detection
- de-emphasize established long-duration momentum so names already well into a run do not dominate
- established momentum nearly invisible (Perf.Y=0.10)

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.26 | 0.10 | 0.30 | 0.26 | 0.02 | 0.01 | 0.05 |
| `weeks` | 0.20 | 0.06 | 0.28 | 0.32 | 0.04 | 0.02 | 0.08 |
| `months` | 0.12 | 0.04 | 0.22 | 0.28 | 0.12 | 0.06 | 0.16 |
| `years` | 0.02 | 0.02 | 0.08 | 0.18 | 0.24 | 0.18 | 0.28 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `attention` | `bb_squeeze=1.55`, `range_compression=1.50`, `volatility_contraction=1.45`, `volume_trend=1.40`, `relative_volume_10d_calc=1.20`, `intraday_momentum=1.10` |
| `event` | `premarket_change=1.15`, `gap_severity=1.25`, `event_intensity=1.20` |
| `momentum` | `aroon_spread=1.65`, `adx_directional_spread=1.55`, `stoch_rsi_crossover=1.40`, `Perf.5D=1.30`, `macd_spread=1.35`, `ROC=1.25`, `Perf.Y=0.10`, `Perf.YTD=0.15`, `Perf.6M=0.30` |
| `trend` | `short_trend_emergence=1.55`, `close_vs_sma10=1.40`, `close_vs_ema10=1.40`, `bb_position=1.30`, `close_vs_vwap=1.25`, `close_vs_vwma=1.20`, `trend_alignment=0.75`, `close_vs_sma200=0.70`, `close_vs_ema200=0.70` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 1.30 | 0.72 |
| `event` | 1.12 | 0.88 |
| `momentum` | 1.40 | 0.65 |
| `trend` | 1.30 | 0.75 |
| `quality` | 1.00 | 0.82 |
| `safety` | 1.00 | 0.82 |

Interpretation:

- `aroon_spread=1.65` is the highest single momentum signal in the system — when Aroon Up exceeds Aroon Down, a new trend is forming
- coiled-energy signals (`bb_squeeze=1.55`, `range_compression=1.50`) are the highest in the system
- `short_trend_emergence=1.55` detects when multiple short-term MAs are crossing bullish
- established momentum heavily muted (Perf.Y=0.10) so names already far into a run do not crowd out inflection candidates

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.75`, `momentum=-0.80`, `trend=-0.55` |
| `weeks` | `attention=-0.50`, `momentum=-0.60`, `trend=-0.45` |
| `months` | `momentum=-0.40`, `trend=-0.30` |

### 3. `quality_value_compounder`

Idea: **Real Quality** — durable quality identification

Intent:

- reward durable profitability, efficiency, and balance-sheet quality
- quality dominates all horizons (35–42%)
- stop tactical excitement from dominating the leaderboard
- explicitly penalize names with sparse fundamental support (heaviest missing-component penalties)
- attention inverted (0.70x/1.50x) — low-volume names face strongest anti-concentration drag
- activate all extended quality signals (ROIC=1.40, FCF margin, buyback yield, revenue per employee)

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.04 | 0.01 | 0.04 | 0.16 | 0.35 | 0.16 | 0.24 |
| `weeks` | 0.03 | 0.01 | 0.05 | 0.14 | 0.34 | 0.18 | 0.25 |
| `months` | 0.02 | 0.01 | 0.04 | 0.12 | 0.36 | 0.20 | 0.25 |
| `years` | 0.01 | 0.00 | 0.01 | 0.06 | 0.42 | 0.24 | 0.26 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `momentum` | `change=0.10`, `Perf.5D=0.10`, `Perf.W=0.15`, `Perf.1M=0.30`, `Perf.3M=1.00`, `Perf.YTD=1.05`, `Perf.Y=1.25`, `ROC=0.30`, `Mom=0.35`, `macd_spread=0.25`, `rsi_centered=0.15`, `rsi7_centered=0.05` |
| `trend` | `close_vs_sma200=1.40`, `close_vs_ema200=1.40`, `trend_alignment=1.25`, `close_vs_sma50=0.80`, `close_vs_ema50=0.80`, `close_vs_vwap=0.20`, `close_vs_vwma=0.30` |
| `quality` | `return_on_invested_capital=1.40`, `operating_margin=1.30`, `free_cash_flow_margin_ttm=1.25`, `free_cash_flow_yoy_growth_ttm=1.25`, `ebitda_yoy_growth_ttm=1.20`, `return_on_equity=1.15`, `total_revenue_yoy_growth_ttm=1.15`, `earnings_per_share_diluted_yoy_growth_ttm=1.15`, `after_tax_margin=1.10`, `gross_margin=1.10`, `gross_profit_margin_fy=1.10`, `revenue_per_employee=1.10`, `return_on_assets=1.05`, `buyback_yield=0.90` |
| `valuation` | `price_free_cash_flow_ttm=1.25`, `price_earnings_growth_ttm=1.20`, `enterprise_value_ebitda_ttm=1.15`, `price_earnings_ttm=1.10`, `price_to_cash_f_operating_activities_ttm=1.10` |
| `safety` | `altman_z_score_ttm=1.25`, `debt_to_equity=1.25`, `debt_to_revenue_ttm=1.20`, `short_term_cash_coverage=1.15`, `net_debt=1.15`, `beta_1_year=0.70` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 0.70 | 1.50 |
| `momentum` | 1.00 | 0.90 |
| `trend` | 1.05 | 1.15 |
| `quality` | 1.25 | 1.35 |
| `valuation` | 1.20 | 1.20 |
| `safety` | 1.18 | 1.30 |

Interpretation:

- **attention bias 0.70x/1.50x is the most aggressive anti-concentration lever** — low-volume names have negative attention amplified by 50%
- quality negative at 1.35x — durable profitability failure is heavily punished
- ROIC=1.40 is the highest quality signal weight — capital discipline is the primary quality filter
- short-term momentum nearly zeroed (change=0.10, rsi7=0.05) — noise elimination
- missing quality at years fallback of −1.30 is the heaviest in the system

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.50`, `quality=-1.00`, `valuation=-0.80`, `safety=-0.75` |
| `weeks` | `attention=-0.40`, `quality=-1.10`, `valuation=-0.85`, `safety=-0.85` |
| `months` | `attention=-0.30`, `quality=-1.20`, `valuation=-0.95`, `safety=-0.90` |
| `years` | `attention=-0.20`, `quality=-1.30`, `valuation=-1.05`, `safety=-1.00` |

### 4. `sector_relative_outperformer`

Idea: **Real Quality** — best-in-class operators by relative strength

Intent:

- surface best-in-class operators that outperform through operational excellence
- reward efficiency (revenue_per_employee=1.30), capital returns (buyback yield), and durable profitability
- detect volume accumulation through volume trend (1.50 — highest in system; raw attention damped to 0.65–0.85)
- long-duration momentum emphasized (Perf.Y=1.30) while short-term crushed (change=0.15)
- use monthly pivot distance for trend support confirmation

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.04 | 0.02 | 0.08 | 0.20 | 0.32 | 0.14 | 0.20 |
| `weeks` | 0.03 | 0.02 | 0.08 | 0.18 | 0.32 | 0.17 | 0.20 |
| `months` | 0.02 | 0.01 | 0.06 | 0.14 | 0.35 | 0.22 | 0.20 |
| `years` | 0.01 | 0.01 | 0.03 | 0.07 | 0.40 | 0.26 | 0.22 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `attention` | `volume_trend=1.50`, `relative_volume_10d_calc=0.85`, `float_turnover=0.80`, `dollar_turnover_intensity=0.75`, `Value.Traded=0.70`, `AvgValue.Traded_10d=0.65` |
| `momentum` | `Perf.Y=1.30`, `Perf.6M=1.20`, `Perf.YTD=1.15`, `Perf.3M=1.10`, `Perf.1M=0.50`, `Perf.W=0.25`, `Perf.5D=0.15`, `change=0.15` |
| `trend` | `close_vs_sma200=1.40`, `close_vs_ema200=1.40`, `trend_alignment=1.30`, `pivot_distance=1.15`, `close_vs_sma50=0.85`, `close_vs_ema50=0.85`, `close_vs_vwap=0.30`, `close_vs_vwma=0.40` |
| `quality` | `return_on_invested_capital=1.40`, `eps_forward_growth=1.35`, `revenue_per_employee=1.30`, `free_cash_flow_margin_ttm=1.30`, `operating_margin=1.30`, `free_cash_flow_yoy_growth_ttm=1.25`, `ebitda_yoy_growth_ttm=1.20`, `return_on_equity=1.15`, `earnings_per_share_diluted_yoy_growth_ttm=1.15`, `total_revenue_yoy_growth_ttm=1.15`, `gross_profit_margin_fy=1.10`, `buyback_yield=1.10`, `after_tax_margin=1.10`, `return_on_assets=1.10`, `gross_margin=1.10` |
| `valuation` | `price_earnings_growth_ttm=1.25`, `earnings_yield=1.20`, `price_free_cash_flow_ttm=1.20`, `enterprise_value_ebitda_ttm=1.10`, `price_earnings_ttm=1.05` |
| `safety` | `altman_z_score_ttm=1.20`, `debt_to_equity=1.18`, `debt_to_revenue_ttm=1.12`, `short_term_cash_coverage=1.10`, `net_debt=1.10`, `beta_1_year=0.65` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 0.70 | 1.40 |
| `momentum` | 1.08 | 0.85 |
| `trend` | 1.12 | 1.12 |
| `quality` | 1.30 | 1.35 |
| `valuation` | 1.18 | 1.12 |
| `safety` | 1.15 | 1.28 |

Interpretation:

- quality bias +1.30x/−1.35x — excellence must be genuine; negative quality hurts more
- trend bias symmetric at 1.12x — both directions carry equal structural weight
- volume_trend=1.50 detects sustained institutional accumulation, not one-day spikes
- raw attention signals dampened because the profile cares about accumulation trend, not absolute volume

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.45`, `quality=-0.90`, `valuation=-0.70`, `safety=-0.70` |
| `weeks` | `attention=-0.35`, `quality=-1.00`, `valuation=-0.80`, `safety=-0.80` |
| `months` | `attention=-0.25`, `quality=-1.10`, `valuation=-0.90`, `safety=-0.90` |
| `years` | `quality=-1.20`, `valuation=-1.00`, `safety=-0.95` |

### 5. `forward_edge_active`

Idea: **Real Quality** — forward-looking "what is getting better?"

Intent:

- target risk-adjusted outperformance on the weeks-to-months timescale as an early catcher
- eps_forward_growth=1.75 is the highest single signal weight in the system
- QoQ growth dominates YoY (1.35–1.45 vs 0.90–1.00) — focus on improvement trajectory
- attention most aggressively inverted (0.50x/1.50x) — the most anti-crowd profile
- coiled-energy detection via bb_squeeze=1.60 and range_compression=1.55
- Perf.Y=0.05 — established momentum nearly invisible

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.05 | 0.08 | 0.14 | 0.26 | 0.22 | 0.10 | 0.15 |
| `weeks` | 0.04 | 0.06 | 0.12 | 0.22 | 0.26 | 0.14 | 0.16 |
| `months` | 0.03 | 0.03 | 0.08 | 0.16 | 0.30 | 0.18 | 0.22 |
| `years` | 0.02 | 0.01 | 0.03 | 0.10 | 0.32 | 0.22 | 0.30 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `attention` | `bb_squeeze=1.60`, `range_compression=1.55`, `volatility_contraction=1.50`, `volume_trend=1.50`, `relative_volume_10d_calc=0.70`, `float_turnover=0.60`, `dollar_turnover_intensity=0.55`, `Value.Traded=0.50`, `AvgValue.Traded_10d=0.45` |
| `event` | `eps_surprise_percent_fq=1.60` (highest in system), `gap_severity=1.25`, `event_intensity=1.20` |
| `momentum` | `Perf.Y=0.05`, `Perf.YTD=0.15`, `Perf.6M=0.30`, `aroon_spread=1.40`, `macd_spread=1.30`, `adx_directional_spread=1.30`, `Perf.5D=1.20`, `ROC=1.20` |
| `trend` | `short_trend_emergence=1.65` (highest in system), `close_vs_sma10=1.50`, `close_vs_ema10=1.50`, `close_vs_sma20=1.35`, `close_vs_ema20=1.35`, `bb_position=1.25`, `close_vs_vwap=1.30`, `close_vs_vwma=1.25`, `trend_alignment=0.70`, `close_vs_sma200=0.65`, `close_vs_ema200=0.65` |
| `quality` | `eps_forward_growth=1.75` (HIGHEST IN SYSTEM), `free_cash_flow_qoq_growth_fq=1.45`, `net_income_qoq_growth_fq=1.40`, `ebitda_qoq_growth_fq=1.35`, `total_revenue_qoq_growth_fq=1.35`, `free_cash_flow_margin_ttm=1.35`, `return_on_invested_capital=1.20`, `operating_margin=1.15` (YoY metrics at 0.90–1.00) |
| `valuation` | `price_earnings_growth_ttm=1.30`, `earnings_yield=1.25`, `distance_from_52w_high=1.20`, `price_free_cash_flow_ttm=1.15` |
| `safety` | `altman_z_score_ttm=1.20`, `debt_to_equity=1.15`, `net_debt=1.10` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 0.50 | 1.50 |
| `event` | 1.20 | 0.82 |
| `momentum` | 1.15 | 0.72 |
| `trend` | 1.30 | 0.82 |
| `quality` | 1.22 | 1.35 |
| `valuation` | 1.12 | 0.78 |
| `safety` | 1.10 | 1.40 |

Interpretation:

- attention bias 0.50x/1.50x is the most extreme inversion — crowded trades face maximum drag, undiscovered names get the edge
- quality negative at 1.35x and safety negative at 1.40x — forward quality and solvency must be solid
- trend positive at 1.30x rewards early trend breaks; valuation negative at 0.78x dampens expensive-name penalty (forward edge names may be optically expensive)
- performance tracking periods: days→Perf.W, weeks→Perf.1M, months→Perf.YTD+Perf.1M, years→Perf.Y

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `quality=-0.65`, `safety=-0.65`, `trend=-0.50` |
| `weeks` | `quality=-0.75`, `safety=-0.75`, `trend=-0.45` |
| `months` | `quality=-0.90`, `safety=-0.90`, `valuation=-0.60` |
| `years` | `quality=-1.00`, `safety=-1.00`, `valuation=-0.80` |

### 6. `asymmetric_value`

Idea: **Overlooked Fundamentals** — quality NOT rewarded by the market

Intent:

- surface deep value discounts with asymmetric upside potential
- momentum bias INVERTED (positive=0.75x, negative=0.55x) — poor recent performance HELPS, since it indicates the market hasn't rewarded the quality
- combine traditional multiples with forward-looking signals: distance_from_52w_high=1.50 (highest valuation signal), earnings_yield=1.40
- maintain a strong safety floor across all horizons to avoid value traps
- amplify the penalty for low attention so passive low-volume vehicles face a meaningful drag

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.04 | 0.02 | 0.05 | 0.12 | 0.22 | 0.33 | 0.22 |
| `weeks` | 0.04 | 0.02 | 0.06 | 0.12 | 0.24 | 0.30 | 0.22 |
| `months` | 0.03 | 0.02 | 0.04 | 0.10 | 0.28 | 0.32 | 0.21 |
| `years` | 0.02 | 0.01 | 0.02 | 0.06 | 0.30 | 0.36 | 0.23 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `momentum` | `change=0.20`, `Perf.5D=0.20`, `Perf.W=0.25`, `Perf.1M=0.40`, `Perf.3M=1.00`, `Perf.6M=1.05`, `Perf.YTD=0.90`, `Perf.Y=0.60`, `ROC=0.40`, `Mom=0.45`, `macd_spread=0.35`, `rsi_centered=0.15`, `rsi7_centered=0.05` |
| `trend` | `close_vs_sma200=1.25`, `close_vs_ema200=1.25`, `trend_alignment=1.10`, `close_vs_sma50=0.75`, `close_vs_ema50=0.75`, `close_vs_vwap=0.40`, `close_vs_vwma=0.50` |
| `quality` | `return_on_invested_capital=1.35`, `free_cash_flow_margin_ttm=1.35`, `free_cash_flow_yoy_growth_ttm=1.30`, `operating_margin=1.20`, `buyback_yield=1.15`, `return_on_equity=1.10`, `after_tax_margin=1.05` |
| `valuation` | `distance_from_52w_high=1.50`, `earnings_yield=1.40`, `range_position_52w=1.35`, `price_book_fq=1.15`, `price_free_cash_flow_ttm=1.20`, `price_earnings_growth_ttm=1.15`, `enterprise_value_ebitda_ttm=1.10` |
| `safety` | `altman_z_score_ttm=1.30`, `debt_to_equity=1.25`, `debt_to_revenue_ttm=1.20`, `short_term_cash_coverage=1.15`, `net_debt=1.15`, `beta_1_year=0.75` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 0.75 | 1.40 |
| `momentum` | 0.75 | 0.55 |
| `trend` | 1.00 | 0.80 |
| `quality` | 1.20 | 1.25 |
| `valuation` | 1.35 | 1.00 |
| `safety` | 1.12 | 1.35 |

Interpretation:

- **momentum bias is uniquely inverted** — positive momentum at 0.75x and negative at 0.55x means poor recent performance actually HELPS the score. This is the key mechanism for surfacing "overlooked" quality.
- valuation positive at 1.35x amplifies deep value discount evidence
- safety negative at 1.35x acts as the value-trap guard — balance-sheet deterioration kills the score
- distance_from_52w_high=1.50 is the highest valuation signal — further below peak = stronger signal

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.45`, `valuation=-0.90`, `quality=-0.60`, `safety=-0.65` |
| `weeks` | `attention=-0.35`, `valuation=-1.00`, `quality=-0.70`, `safety=-0.75` |
| `months` | `attention=-0.25`, `valuation=-1.10`, `quality=-0.80`, `safety=-0.85` |
| `years` | `valuation=-1.20`, `quality=-0.90`, `safety=-0.95` |

### 7. `value_recovery`

Idea: **Overlooked Fundamentals** — turnaround with sequential improvement

Intent:

- reward cheap names with improving fundamentals and solvency
- QoQ growth dominates YoY (1.20–1.35 vs 0.85–1.10) to detect sequential turnaround
- eps_forward_growth=1.40 — forward-looking recovery conviction
- stoch_rsi_crossover=0.65 dampened — recovery turn detection, not aggressive
- momentum negative bias at 0.60x — recent weakness is forgiven (turnaround candidates often look ugly)
- tolerate imperfect short-term momentum while requiring valuation and safety evidence

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.06 | 0.04 | 0.04 | 0.12 | 0.24 | 0.26 | 0.24 |
| `weeks` | 0.06 | 0.04 | 0.06 | 0.14 | 0.22 | 0.25 | 0.23 |
| `months` | 0.04 | 0.03 | 0.05 | 0.14 | 0.28 | 0.26 | 0.20 |
| `years` | 0.02 | 0.01 | 0.01 | 0.07 | 0.30 | 0.32 | 0.27 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `momentum` | `change=0.30`, `Perf.5D=0.25`, `Perf.W=0.30`, `Perf.1M=0.45`, `Perf.3M=1.15`, `Perf.YTD=1.00`, `Perf.Y=0.80`, `ROC=0.45`, `Mom=0.50`, `macd_spread=0.40`, `rsi_centered=0.20`, `rsi7_centered=0.10`, `stoch_rsi_crossover=0.65` |
| `trend` | `close_vs_sma200=1.10`, `close_vs_ema200=1.10`, `pivot_distance=1.10`, `trend_alignment=1.05`, `close_vs_sma50=0.85`, `close_vs_ema50=0.85`, `close_vs_vwap=0.50`, `close_vs_vwma=0.60`, `short_trend_emergence=0.75` |
| `quality` | `free_cash_flow_qoq_growth_fq=1.35`, `net_income_qoq_growth_fq=1.30`, `ebitda_qoq_growth_fq=1.25`, `total_revenue_qoq_growth_fq=1.25`, `eps_forward_growth=1.40`, `free_cash_flow_margin_ttm=1.20`, `operating_margin=1.10`, `return_on_invested_capital=1.05` (YoY at 0.85–1.10) |
| `valuation` | `price_earnings_growth_ttm=1.25`, `earnings_yield=1.25`, `price_book_fq=1.25`, `enterprise_value_ebitda_ttm=1.20`, `distance_from_52w_high=1.20`, `price_earnings_ttm=1.10` |
| `safety` | `altman_z_score_ttm=1.25`, `net_debt=1.20`, `debt_to_equity=1.20`, `short_term_cash_coverage=1.15`, `debt_to_revenue_ttm=1.10` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 0.82 | 1.32 |
| `momentum` | 0.85 | 0.60 |
| `trend` | 1.08 | 0.85 |
| `quality` | 1.18 | 1.18 |
| `valuation` | 1.30 | 1.12 |
| `safety` | 1.18 | 1.20 |

Interpretation:

- momentum negative at 0.60x is the most forgiving of all long profiles — turnaround candidates are expected to have ugly recent tape
- quality bias symmetric at 1.18x — improvement is rewarded but decline is penalized equally
- valuation positive at 1.30x amplifies cheap evidence for rerating candidates

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `attention=-0.40`, `valuation=-0.85`, `quality=-0.55`, `safety=-0.65` |
| `weeks` | `attention=-0.30`, `valuation=-0.95`, `quality=-0.65`, `safety=-0.75` |
| `months` | `attention=-0.20`, `valuation=-1.05`, `quality=-0.75`, `safety=-0.85` |
| `years` | `valuation=-1.15`, `quality=-0.85`, `safety=-0.95` |

### 8. `fragility_short`

Idea: **Hedging** — identifies structurally fragile names for short/hedge baskets

Intent:

- penalize downside momentum, downside event pressure, and weak safety harder than any other profile
- all directional biases inverted to amplify negatives — safety bias −1.45x is the most aggressive in the system
- safety weight at 0.57 on years is the highest single-component weight in the entire system
- beta NOT inverted (1.15 weight) — higher beta = more fragile = better short candidate
- reward deterioration evidence via extended quality/safety signals

#### Horizon weights

| Horizon | Attention | Event | Momentum | Trend | Quality | Valuation | Safety |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `days` | 0.16 | 0.30 | 0.28 | 0.18 | 0.02 | 0.01 | 0.05 |
| `weeks` | 0.08 | 0.20 | 0.28 | 0.24 | 0.04 | 0.02 | 0.14 |
| `months` | 0.04 | 0.08 | 0.20 | 0.22 | 0.14 | 0.04 | 0.28 |
| `years` | 0.00 | 0.02 | 0.06 | 0.12 | 0.18 | 0.05 | 0.57 |

#### Signal weight overrides

| Component | Override details |
| --- | --- |
| `event` | `gap_severity=1.35`, `eps_surprise_percent_fq=1.25`, `event_intensity=1.20`, `premarket_change=1.15`, `postmarket_change=1.15` |
| `momentum` | `Perf.5D=1.25`, `Perf.W=1.25`, `change=1.20`, `Perf.1M=1.15`, `adx_directional_spread=1.20`, `ROC=1.15`, `Perf.3M=1.05` |
| `trend` | `trend_alignment=1.25`, `close_vs_sma200=1.20`, `close_vs_ema200=1.20`, `close_vs_sma50=1.12`, `close_vs_ema50=1.12`, `close_vs_vwap=1.10`, `close_vs_vwma=1.10` |
| `quality` | `free_cash_flow_margin_ttm=1.20`, `free_cash_flow_yoy_growth_ttm=1.20`, `operating_margin=1.15`, `eps_forward_growth=1.15`, `return_on_invested_capital=1.10` |
| `safety` | `debt_to_equity=1.35`, `altman_z_score_ttm=1.35`, `debt_to_revenue_ttm=1.30`, `net_debt=1.25`, `beta_1_year=1.15` (NOT inverted — higher beta = more fragile), `short_term_cash_coverage=1.10` |

#### Directional bias overrides

| Component | Positive multiplier | Negative multiplier |
| --- | ---: | ---: |
| `attention` | 1.00 | 1.00 |
| `event` | 0.85 | 1.30 |
| `momentum` | 0.80 | 1.30 |
| `trend` | 0.85 | 1.35 |
| `quality` | 0.85 | 1.25 |
| `valuation` | 0.90 | 1.20 |
| `safety` | 0.80 | 1.45 |

Interpretation:

- ALL biases invert to amplify negatives — events, momentum, trend, quality, valuation, and safety all punish downside more than they reward upside
- safety bias at −1.45x is the most aggressive single directional multiplier in the system
- attention is neutral (1.00/1.00) — fragility can occur in any volume regime
- combined with safety weight=0.57 at years, this creates maximum sensitivity to balance-sheet deterioration

#### Missing-component fallback scores

| Horizon | Fallbacks |
| --- | --- |
| `days` | `event=-0.45`, `momentum=-0.50`, `trend=-0.40` |
| `weeks` | `momentum=-0.55`, `trend=-0.45`, `safety=-0.50` |
| `months` | `trend=-0.50`, `safety=-0.70`, `quality=-0.40` |
| `years` | `safety=-0.90`, `quality=-0.45`, `valuation=-0.30` |

## How To Read The Profiles Now

### If you want tactical upside continuation

Use `breakout_long`.

You should expect the leaders to have:

- abnormal activity with volume trend confirmation (1.40) and intraday momentum (1.25)
- strong gap or event pressure with gap severity amplified (1.35)
- strong short-term momentum — ADX directional spread (1.50) and Aroon spread (1.45) detect directional strength
- clean tactical trend confirmation — short-term MAs favored, 200-day dampened
- quality=0.00 at days — pure tactical scoring with no fundamental noise

You should not expect it to prefer the cheapest or safest names. Momentum bias at +1.40x/−0.65x is the most aggressive upside bet in the system.

### If you want to catch early trend inflections

Use `early_momentum_inflection`.

You should expect the leaders to have:

- coiled-energy setups: bb_squeeze=1.55 and range_compression=1.50 (highest in system)
- emerging directional shift: aroon_spread=1.65 (highest single momentum signal), ADX directional spread=1.55
- fresh volume accumulation with volume_trend=1.40
- muted legacy momentum: Perf.Y=0.10, Perf.YTD=0.15 — already-established runs are nearly invisible
- short_trend_emergence=1.55 detects multiple short-term MAs crossing bullish

This profile catches the first winds of a directional move before it becomes obvious to the broader market.

### If you want durable quality leadership

Use `quality_value_compounder`.

You should expect the leaders to have:

- stronger profitability and returns on capital (ROIC=1.40 is the primary quality filter)
- better balance-sheet quality and operational efficiency
- acceptable valuation with FCF-based measures emphasized (P/FCF=1.25)
- evidence of diluted EPS growth and gross margin stability
- attention inverted at 0.70x/1.50x — the most aggressive anti-concentration lever

Names with missing fundamentals should rank significantly worse (quality fallback at years = −1.30).

### If you want best-in-class sector outperformers

Use `sector_relative_outperformer`.

You should expect the leaders to have:

- superior operational efficiency: revenue_per_employee=1.30, strong gross margins, disciplined capital allocation
- durable profitability: ROIC=1.40, operating_margin=1.30, growing FCF margins
- sustained institutional accumulation: volume_trend=1.50 (highest in system), not one-day spikes
- confirmed trend structure: above key moving averages, trading above monthly pivot point
- long-duration momentum: Perf.Y=1.30, Perf.6M=1.20 while short-term noise crushed (change=0.15)

### If you want forward-looking quality improvements

Use `forward_edge_active`.

You should expect the leaders to have:

- forward EPS improvement: eps_forward_growth=1.75 (highest single signal weight in the entire system)
- sequential QoQ acceleration: FCF QoQ=1.45, net income QoQ=1.40 (QoQ dominates YoY)
- coiled-energy before breakout: bb_squeeze=1.60, range_compression=1.55, short_trend_emergence=1.65 (highest trend signal)
- earnings surprise history: eps_surprise_percent_fq=1.60 (highest in system)
- most anti-crowd bias: attention at 0.50x/1.50x, Perf.Y=0.05

This is the most forward-looking profile — it looks at what is getting better, not what was.

### If you want overlooked quality with asymmetric upside

Use `asymmetric_value`.

You should expect the leaders to have:

- deep value discount: distance_from_52w_high=1.50, earnings_yield=1.40, range_position_52w=1.35
- quality NOT rewarded by the market: momentum bias is INVERTED (+0.75x/−0.55x) so poor performance HELPS
- robust fundamental floor: ROIC=1.35, FCF margin=1.35, buyback_yield=1.15
- strong safety to avoid value traps: safety negative at −1.35x

This profile is built for active decisions where you believe the market over-discounted the name.

### If you want rerating or recovery ideas

Use `value_recovery`.

You should expect the leaders to have:

- cheaper valuation with PEG=1.25, earnings_yield=1.25, price_book=1.25
- sequential improvement: QoQ growth dominates YoY (1.20–1.35 vs 0.85–1.10)
- forward recovery conviction: eps_forward_growth=1.40
- recent weakness forgiven: momentum negative bias at 0.60x (most forgiving long profile)
- balance-sheet repair: Altman Z=1.25, net_debt=1.20, debt_to_equity=1.20

### If you want short-fragility candidates

Use `fragility_short`.

You should expect the leaders to have:

- deteriorating safety: safety weight=0.57 at years (highest single-component weight in system)
- all directional biases inverted to amplify negatives: safety bias −1.45x (most aggressive)
- worsening trend and momentum with downside event pressure
- higher beta: beta NOT inverted at 1.15 — higher volatility = more fragile
- weak quality: FCF margin deterioration, declining EPS outlook

## Consensus Aggregator

The consensus aggregator (`run_consensus_aggregator`) runs all 8 profiles against the same scan data and produces a meta-ranking based on cross-profile agreement.

### How it works

1. Each profile scores every name independently using its own weights, biases, and signals
2. Profile scores are collected per ticker per horizon
3. The consensus score is a weighted average across all profiles for each horizon
4. `fragility_short` scores are sign-inverted before aggregation (a bearish fragility call becomes a negative contribution)
5. Names requiring fewer than 3 profiles with valid opinions are excluded
6. Score variance across profiles penalizes inconsistent cross-profile readings

### Profile weights in consensus

| Profile | Consensus weight | Rationale |
| --- | ---: | --- |
| `quality_value_compounder` | 0.18 | Broadest fundamental coverage |
| `sector_relative_outperformer` | 0.15 | Efficiency and durability focus |
| `forward_edge_active` | 0.15 | Forward-looking quality improvement |
| `asymmetric_value` | 0.12 | Mispricing detection |
| `breakout_long` | 0.10 | Tactical momentum confirmation |
| `early_momentum_inflection` | 0.10 | Early trend detection |
| `value_recovery` | 0.10 | Turnaround and rerating |
| `fragility_short` | 0.10 | Short-side / hedge signal |

### Confidence calculation

Consensus confidence combines:
- Score magnitude (abs value × 15)
- Agreement ratio (fraction of directional profiles that agree × 30)
- Consistency bonus (lower variance across profiles → up to 10 points)
- Opinion coverage (fraction of profiles contributing × 15)
- Base of 25, clamped to [5, 99]

### Output

The aggregator produces:
- A ranked report per horizon showing consensus score, direction, confidence, agreement ratio, and opinions
- A "high conviction" section listing names where 5+ profiles agree on direction with ≥60% agreement
- A CSV file with consensus scores plus all individual profile scores for further analysis

### When to use

Use `run_consensus_aggregator` when you want the highest-conviction names that are supported by multiple independent lenses. Use `run_full_analysis_suite` to run both the individual profile suite and the consensus aggregator together.

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
| `breakout_long` | 1.07 | 0.00 |
| `early_momentum_inflection` | 1.05 | 0.00 |
| `quality_value_compounder` | 1.02 | 0.00 |
| `sector_relative_outperformer` | 1.02 | 0.00 |
| `forward_edge_active` | 1.03 | 0.00 |
| `asymmetric_value` | 1.02 | 0.00 |
| `value_recovery` | 1.00 | 0.00 |
| `fragility_short` | 1.07 | 0.00 |

These are secondary to score construction. The main ranking differentiation comes from:

1. horizon weights
2. signal weights
3. directional bias
4. missing-component fallbacks

## Summary

The revised preset logic organizes 8 profiles into three investment ideas with sharply differentiated scoring.

### Trend Following
- `breakout_long` is purely tactical — quality=0.00 at days, momentum bias +1.40x/−0.65x (most aggressive upside), ADX directional spread=1.50, volume_trend=1.40 for confirmation.
- `early_momentum_inflection` catches nascent moves — aroon_spread=1.65 (highest momentum signal), bb_squeeze=1.55, range_compression=1.50 for coiled energy, Perf.Y=0.10 to eliminate established runs.

### Real Quality Identification
- `quality_value_compounder` is fundamentally strict with quality=42% at years, ROIC=1.40, attention inverted 0.70x/1.50x, and the heaviest missing-quality penalties (−1.30 at years).
- `sector_relative_outperformer` rewards efficiency — revenue_per_employee=1.30, volume_trend=1.50 (highest in system for accumulation), Perf.Y=1.30, quality bias +1.30x/−1.35x.
- `forward_edge_active` is most forward-looking — eps_forward_growth=1.75 (highest single signal), QoQ dominates YoY, short_trend_emergence=1.65, attention most inverted at 0.50x/1.50x.

### Overlooked Fundamentals and Hedging
- `asymmetric_value` has uniquely inverted momentum bias (+0.75x/−0.55x) — poor performance HELPS. distance_from_52w_high=1.50, earnings_yield=1.40, safety negative at −1.35x for value-trap guard.
- `value_recovery` forgives weakness most (momentum negative at 0.60x), QoQ dominates YoY (1.20–1.35 vs 0.85–1.10), eps_forward_growth=1.40.
- `fragility_short` inverts all directional biases to amplify negatives — safety=0.57 at years (highest single weight), safety bias −1.45x (most aggressive), beta NOT inverted.

### Removed profiles
- `balanced` and `backtest_period_ladder` were removed. Backward-compatible aliases map them to `quality_value_compounder` and `sector_relative_outperformer` respectively.

### Consensus aggregator
- `run_consensus_aggregator` runs all 8 profiles and produces a weighted meta-ranking per horizon.
- `fragility_short` scores are sign-inverted before aggregation.
- High-conviction names (5+ profiles agreeing with ≥60% agreement ratio) are highlighted.
- `run_full_analysis_suite` runs individual profiles plus the consensus aggregator together.

## Extended Signal System (March 30, 2026 — expanded April 5, 2026)

Extended signals were added to the component signal map behind a backward-compatible gating system. The April 5 expansion added seven more signals and eight new raw scan fields.

### How it works

Signals in the `EXTENDED_SIGNALS` set default to weight `0.00` when a profile does not explicitly override them. This means:

- existing profiles (`balanced`, `breakout_long`, `fragility_short`, `backtest_period_ladder`) continue to behave identically for non-extended signals
- `quality_value_compounder` and `value_recovery` now also activate relevant extended signals (`free_cash_flow_margin_ttm`, `buyback_yield`, `earnings_yield`, `revenue_per_employee`, `earnings_per_share_diluted_yoy_growth_ttm`, `gross_profit_margin_fy`, `pivot_distance`)
- `asymmetric_value` and `early_momentum_inflection` activate the extended signals they need by setting nonzero weights
- `forward_edge_active` activates volume trend, Stoch RSI crossover, intraday momentum, and forward quality signals
- `sector_relative_outperformer` activates all efficiency, capital return, and accumulation signals
- `breakout_long` now activates volume trend and intraday momentum for tactical confirmation
- any future or custom profile can opt into arbitrary subsets of the extended signals

### Extended signal list

| Signal | Component | Source | Description |
| --- | --- | --- | --- |
| `Perf.6M` | momentum | raw | 6-month price performance |
| `aroon_spread` | momentum | derived (`Aroon.Up - Aroon.Down`) | new trend detection |
| `adx_directional_spread` | momentum | derived (`ADX+DI - ADX-DI`) | directional strength emergence |
| `stoch_rsi_centered` | momentum | derived (`Stoch.RSI.K - 50`) | short-term momentum impulse |
| `CCI20` | momentum | raw | directional thrust |
| `stoch_rsi_crossover` | momentum | derived (`Stoch.RSI.K - Stoch.RSI.D`) | bullish Stoch RSI crossover signal |
| `bb_position` | trend | derived (`(close - BB.lower) / (BB.upper - BB.lower)`) | position within Bollinger Bands |
| `close_vs_sma10` | trend | derived (`+1/-1`) | above/below 10-day SMA |
| `close_vs_sma20` | trend | derived (`+1/-1`) | above/below 20-day SMA |
| `close_vs_sma30` | trend | derived (`+1/-1`) | above/below 30-day SMA |
| `close_vs_ema10` | trend | derived (`+1/-1`) | above/below 10-day EMA |
| `close_vs_ema20` | trend | derived (`+1/-1`) | above/below 20-day EMA |
| `close_vs_ema30` | trend | derived (`+1/-1`) | above/below 30-day EMA |
| `short_trend_emergence` | trend | derived (avg of 6 short-term MAs) | early trend forming |
| `pivot_distance` | trend | derived (`(close - Pivot.M.Classic.Middle) / close`) | stock above monthly pivot = support confirmation |
| `bb_squeeze` | attention | derived (`(BB.upper - BB.lower) / close`, inverted) | band compression = coiled energy |
| `range_compression` | attention | derived (`(High.3M - Low.3M) / close`, inverted) | coiled 3-month range |
| `volatility_contraction` | attention | derived (`Volatility.D / Volatility.M`, inverted) | daily-vs-monthly vol contraction |
| `volume_trend` | attention | derived (`average_volume_10d_calc / average_volume_30d_calc`) | rising short-term volume = accumulation |
| `intraday_momentum` | attention | raw `change_from_open` | open-to-current momentum confirmation |
| `earnings_yield` | valuation | raw | earnings-to-price yield (higher = cheaper) |
| `distance_from_52w_high` | valuation | derived (`(close - 52w_high) / abs(52w_high)`, inverted) | discount from peak |
| `range_position_52w` | valuation | derived (`(close - 52w_low) / (52w_high - 52w_low)`, inverted) | position in 52-week range |
| `free_cash_flow_margin_ttm` | quality | raw | cash generation quality |
| `buyback_yield` | quality | raw | shareholder return via buybacks |
| `dividends_yield_current` | quality | raw | income return |
| `eps_forward_growth` | quality | derived (`(forecast_next_fq - actual_fq) / abs(actual_fq)`) | forward EPS improvement |
| `revenue_per_employee` | quality | derived (`total_revenue / number_of_employees`) | operational efficiency per head |
| `earnings_per_share_diluted_yoy_growth_ttm` | quality | raw | diluted EPS growth trajectory |
| `gross_profit_margin_fy` | quality | raw | annual gross margin stability |
| `eps_surprise_percent_fq` | event | raw | earnings surprise history |

### New raw fields added to scan payload (April 5, 2026)

The following columns were added to the TradingView scan payload (`GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD`):

`average_volume_30d_calc`, `Stoch.RSI.D`, `change_from_open`, `earnings_per_share_diluted_yoy_growth_ttm`, `ebitda`, `net_income`, `Pivot.M.Classic.Middle`, `gross_profit_margin_fy`

These were added for:

- **Volume accumulation detection**: `average_volume_30d_calc` enables the `volume_trend` derived signal (10d/30d ratio). Rising ratio = sustained accumulation vs one-day spikes.
- **Stoch RSI crossover**: `Stoch.RSI.D` enables the `stoch_rsi_crossover` derived signal (K minus D). Positive crossover = bullish momentum inflection.
- **Intraday momentum**: `change_from_open` directly measures open-to-current momentum for real-time confirmation.
- **Diluted EPS growth**: `earnings_per_share_diluted_yoy_growth_ttm` adds diluted (not basic) EPS growth for quality assessment. More conservative than basic EPS.
- **Raw financials**: `ebitda` and `net_income` added for future derived ratios and efficiency calculations.
- **Monthly pivot point**: `Pivot.M.Classic.Middle` enables `pivot_distance` for support/resistance confirmation.
- **Gross margin stability**: `gross_profit_margin_fy` adds annual gross profit margin as a quality floor signal.

## Anti-Concentration Tuning (March 30, 2026)

The March 30 run revealed a concentration bias toward Investment Trusts / Mutual Fund industry names across multiple profiles. These entities score well on valuation (structurally low P/E, P/B, P/S), safety (low debt, low beta), and quality (stable margins) because their financial structure is fundamentally different from operating companies. When the fundamental-heavy profiles gave near-zero weight to attention, this structural advantage went unchecked.

### Root causes identified

1. **Valuation component uses `positive_only=True`**: Investment trusts always have positive multiples (low P/E, low P/B). Many growth/tech companies have negative P/E and are excluded from valuation scoring. This means trusts are always evaluated on valuation while many peers are skipped.

2. **Safety advantage is structural**: Trusts typically have low/zero debt, high cash ratios, and low beta. The inverted safety signals (lower = better for debt, beta) give them consistently strong safety scores.

3. **Near-zero attention weight**: Profiles like `quality_value_compounder` (0.03-0.00), `value_recovery` (0.05-0.00), and `asymmetric_value` (0.02-0.00) gave almost no weight to attention. Low-volume passive vehicles were not penalized for lack of market engagement.

4. **MAD normalization is correct but industry-blind**: The scan-relative robust normalization (MAD-based) is working as designed, but it normalizes across the entire scan universe including structurally different entity types. Trusts with stable financials cluster in favorable territory for fundamental components.

### Changes applied

1. **Raised attention weight** in `quality_value_compounder`, `value_recovery`, and `asymmetric_value` (from 0.00-0.03 to 0.02-0.07). Compensated by reducing quality or valuation weights proportionally. All horizon weight sums still equal 1.00.

2. **Added attention directional bias** (`positive_multiplier < 1.0`, `negative_multiplier > 1.0`): negative attention signals are now amplified by 30-40%, so low-volume names face a real drag. Positive attention is dampened because these profiles do not reward volume leadership — they only care that names are not completely inactive.

3. **Activated extended quality signals** in `quality_value_compounder` (`free_cash_flow_margin_ttm=1.15`, `buyback_yield=0.85`) and `value_recovery` (`free_cash_flow_margin_ttm=1.10`). FCF margin rewards operating businesses with real cash conversion. Buyback yield rewards companies that return capital through share repurchases — most investment trusts do not execute buyback programs.

4. **Activated extended valuation signal** in `value_recovery` (`earnings_yield=1.15`). Earnings yield directly measures how cheap a stock is on an earnings basis, adding another dimension beyond multiple-based inversion.

5. **Added missing-component fallbacks for attention** across `quality_value_compounder`, `value_recovery`, `asymmetric_value`, `balanced`, and `backtest_period_ladder`. Names with missing activity data are now penalized instead of silently skipped during renormalization.

6. **Tightened momentum forgiveness** in `quality_value_compounder` (negative_multiplier changed from 0.85 to 0.95). The previous value was too lenient and allowed low-momentum passive vehicles to score well.

### Expected effect

Investment trusts and closed-end funds will still score well on individual fundamental components (quality, valuation, safety) because the MAD normalization correctly identifies their relative strength. However, the combination of higher attention weight, amplified negative attention bias, and extended quality signals should reduce their overall ranking dominance by:

- introducing a structural drag from low attention scores (amplified by 1.30-1.40x)
- requiring evidence of operating cash generation via FCF margin
- penalizing missing activity data through fallback scores

This is a tuning adjustment, not a hard filter. Trusts with genuinely strong engagement and operating characteristics can still rank well.

## Forward-Edge Active Profile And New Signal Infrastructure (March 31, 2026)

### Problem observed

The CF Industries example (flagged on March 30 leaderboard, dropped significantly on March 31 and no longer anywhere near the list) exposed a systematic bias in `breakout_long`, `quality_value_compounder`, `asymmetric_value`, and `early_momentum_inflection`. The profiles catch names "in the making" or "too late" because:

1. **Trailing momentum dominance**: All profiles heavily weight Perf.W through Perf.Y. These are backward-looking — they measure what already happened, not what is about to happen.
2. **No forward-looking fundamental signals**: The scan already fetches `earnings_per_share_forecast_next_fq` and `earnings_per_share_fq` but these were not used in any component scoring.
3. **No anticipatory technical signals**: Short-term MAs (SMA10/20/30, EMA10/20/30), Stoch.RSI.K, and CCI20 were fetched but not wired into the signal map. No volatility contraction or range compression detection existed.
4. **No mean-reversion or exhaustion detection**: Current momentum signals are all trend-following with no mechanism to detect overextension.

### New signals implemented

Seven new derived metrics and three new raw-field signals were added to the infrastructure. All are registered as extended signals (default weight 0.00) so existing profiles are unaffected unless they explicitly activate them.

#### Trend component additions

| Signal | Source | How computed | Higher value effect |
| --- | --- | --- | --- |
| `close_vs_sma10` | derived | `+1` if `close >= SMA10`, else `-1` | above shortest-term average |
| `close_vs_sma20` | derived | `+1` if `close >= SMA20`, else `-1` | above 20-day average |
| `close_vs_sma30` | derived | `+1` if `close >= SMA30`, else `-1` | above 30-day average |
| `close_vs_ema10` | derived | `+1` if `close >= EMA10`, else `-1` | above 10-day EMA |
| `close_vs_ema20` | derived | `+1` if `close >= EMA20`, else `-1` | above 20-day EMA |
| `close_vs_ema30` | derived | `+1` if `close >= EMA30`, else `-1` | above 30-day EMA |
| `short_trend_emergence` | derived | average of the six short-term MA comparisons above | more short-term MAs crossed above = early trend forming |

#### Attention component additions

| Signal | Source | How computed | Higher value effect |
| --- | --- | --- | --- |
| `range_compression` | derived | `(High.3M - Low.3M) / close`, inverted | tighter 3-month range = more coiled energy |
| `volatility_contraction` | derived | `Volatility.D / Volatility.M`, inverted | daily vol lower than monthly = contraction / coiling |

#### Momentum component additions

| Signal | Source | How computed | Higher value effect |
| --- | --- | --- | --- |
| `stoch_rsi_centered` | derived | `Stoch.RSI.K - 50` | stronger short-term momentum impulse |
| `CCI20` | raw field | robust normalized | stronger directional thrust |

#### Quality component addition

| Signal | Source | How computed | Higher value effect |
| --- | --- | --- | --- |
| `eps_forward_growth` | derived | `(forecast_next_fq - actual_fq) / abs(actual_fq)` | analysts expect EPS improvement next quarter |

#### Event component addition

| Signal | Source | How computed | Higher value effect |
| --- | --- | --- | --- |
| `eps_surprise_percent_fq` | raw field | robust normalized | last quarter beat consensus = positive surprise history |

### Scan payload changes

Added to `GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD`:
- `eps_surprise_percent_fq` — most recent quarter earnings surprise percentage
- `earnings_per_share_forecast_next_fy` — next fiscal year EPS forecast

Changed `ignore_unknown_fields` to `True` to handle fields that may not be available for all global markets.

### `forward_edge_active` profile design

This profile targets risk-adjusted outperformance on the weeks-to-months timescale as an early catcher.

#### Core design principles

1. **Trend leads momentum**: The `trend` component (featuring short-term MA alignment) carries the most weight across days and weeks horizons. A stock crossing above its 10/20/30-day MAs is an earlier signal than waiting for Perf.W or Perf.1M to confirm.

2. **Trailing performance heavily damped**: `Perf.Y` weight is 0.10 (vs 1.00 default), `Perf.YTD` is 0.25, `Perf.6M` is 0.40. This prevents names already well into multi-month runs from dominating.

3. **Forward-looking quality emphasis**: `eps_forward_growth` (1.50 weight) and QoQ growth metrics (1.20-1.25 weight) are prioritized over trailing annual metrics. This shifts the quality lens toward what is changing rather than what was.

4. **Attention inversion**: Positive attention is damped (0.65x multiplier) while negative attention is amplified (1.30x). Low-attention names with early technical confirmation get the forward edge. High-attention names face a natural drag because crowded trades have less forward upside.

5. **Coiled-energy detection**: `bb_squeeze` (1.50), `range_compression` (1.40), and `volatility_contraction` (1.35) in the attention component detect names whose price action is compressing — a precursor to directional moves.

6. **Safety guardrails**: Safety weight increases from 0.12 (days) to 0.26 (years). Missing quality or safety data produces penalty fallback scores (-0.50 to -0.85 depending on horizon). This prevents catching falling knives.

7. **Earnings surprise as event signal**: `eps_surprise_percent_fq` (1.40 weight) in the event component rewards companies that consistently beat consensus. Persistent beaters tend to outperform in subsequent quarters.

#### Horizon weight table

| Component | Days | Weeks | Months | Years |
| --- | ---: | ---: | ---: | ---: |
| attention | 0.08 | 0.06 | 0.04 | 0.02 |
| event | 0.10 | 0.08 | 0.05 | 0.02 |
| momentum | 0.18 | 0.16 | 0.12 | 0.06 |
| trend | 0.26 | 0.24 | 0.20 | 0.14 |
| quality | 0.16 | 0.20 | 0.24 | 0.28 |
| valuation | 0.10 | 0.12 | 0.16 | 0.22 |
| safety | 0.12 | 0.14 | 0.19 | 0.26 |

### Suggestions for further improvement (not yet implemented)

These are data-layer and scan-level enhancements that could further improve forward-edge detection and outperformance identification. They require either new TradingView fields, external data sources, or architectural changes.

**Previously suggested items now implemented (April 5, 2026):**

- ~~Multi-timeframe relative volume~~: Implemented as `volume_trend` (10d/30d avg volume ratio) using `average_volume_30d_calc`. Detects sustained accumulation vs one-day spikes. Activated in `breakout_long`, `early_momentum_inflection`, `forward_edge_active`, and `sector_relative_outperformer`.
- ~~Revenue estimate revisions~~: Partially addressed. `earnings_per_share_diluted_yoy_growth_ttm` now added as a diluted EPS growth signal. `eps_forward_growth` (forecast vs actual EPS) was already implemented. True revenue estimate revision tracking would require historical estimate snapshots not available from TradingView scan.

**Remaining suggested items:**

1. **Analyst price target delta** (`price_target_1y_delta`): Available in TradingView field catalog. Measures percentage upside/downside to consensus analyst target. Could be added to the valuation component as a forward-looking anchor. Currently not fetched in the scan payload.

2. **Insider and institutional flow signals**: Fields like institutional ownership changes and insider buying/selling are not currently available from TradingView scan but could be sourced from SEC Form 4 filings (already available in the EDGAR database). High insider buying + low attention + emerging trend = strongest forward edge.

3. **Industry-relative scoring**: The current model normalizes against the entire scan universe. Computing signals relative to industry peers (e.g., "this stock's momentum rank within its industry") would separate genuine sector-relative leadership from broad market moves. The `sector_relative_outperformer` profile partially addresses this through efficiency metrics (revenue per employee) but does not yet normalize against peer groups.

4. **Earnings calendar proximity weighting**: Stocks approaching earnings within 2-4 weeks often exhibit pre-earnings drift (positive historical surprise -> upward drift). The model currently penalizes confidence near earnings but doesn't reward the drift pattern.

5. **Short interest ratio**: Not available in standard TradingView scan but could be sourced from FINRA/exchange data. High short interest + positive momentum inflection = short squeeze candidate.

6. **Options-implied volatility**: IV percentile or IV rank could replace or supplement Bollinger Band squeeze as a more market-informed volatility contraction measure. Not available from TradingView scan.

**New suggestions (April 5, 2026):**

7. **Donchian Channel breakout detection**: TradingView provides `DonchCh20.Upper` and `DonchCh20.Lower`. A derived signal `donchian_position = (close - DonchCh20.Lower) / (DonchCh20.Upper - DonchCh20.Lower)` would detect breakout proximity. Close to upper channel = trending strongly. This is complementary to Bollinger Bands because Donchian is purely price-based (no volatility normalization).

8. **Parabolic SAR trend confirmation**: `P.SAR` is available. A binary `+1/-1` signal for `close > P.SAR` (bullish) vs `close < P.SAR` (bearish) would add another trend confirmation layer. Useful for `breakout_long` and `early_momentum_inflection`.

9. **Hull Moving Average crossover**: `HullMA9` is available. A signal comparing `close` vs `HullMA9` would provide the fastest-reacting MA crossover signal. Hull MA reduces lag compared to SMA/EMA and could improve early trend detection in `early_momentum_inflection` and `forward_edge_active`.

10. **Ichimoku Cloud positioning**: `Ichimoku.Lead1` and `Ichimoku.Lead2` are available. A composite signal based on whether price is above/below the cloud and whether the cloud is bullish (Lead1 > Lead2) would add a powerful multi-factor trend confirmation. Particularly useful for `sector_relative_outperformer` and `quality_value_compounder` on longer horizons.

11. **Chaikin Money Flow as attention signal**: `ChaikinMoneyFlow` is available across timeframes. It combines price and volume to measure buying/selling pressure. Positive CMF = institutional accumulation. Could complement `volume_trend` in detecting authentic buying interest vs noise.

12. **Bull/Bear Power divergence**: `BBPower` (Bull Bear Power) is available. It measures the difference between the highest price and a 13-period EMA. Rising BBPower with declining price = bullish divergence. This would be a mean-reversion/early-inflection signal for `early_momentum_inflection` and `value_recovery`.

13. **Revenue efficiency composite**: Combine `revenue_per_employee` with a new `ebitda_per_employee` (derived from `ebitda / number_of_employees`) to create a composite operational efficiency score. This would separate companies that generate high revenue per head AND retain most of it as operating profit from distribution/logistics companies that have high revenue per head but thin margins.

14. ~~**Cross-profile consensus layer**~~: **Implemented** as `run_consensus_aggregator`. Runs all 8 profiles against the same scan data, produces weighted consensus scores per horizon, identifies high-conviction names where 5+ profiles agree on direction with ≥60% agreement ratio. Output includes ranked report per horizon plus CSV with all individual profile scores.

15. **Temporal stability scoring**: Track whether a name's component scores are stable across consecutive runs (requires storing historical scores). A name that consistently scores well across multiple days has more reliable support than a one-day spike. This would be a confidence multiplier rather than a ranking signal.