# TradingView Activity / Float / Attention Field Guide

## Purpose
This document explains the TradingView fields used in the activity / float / attention scan and how to interpret both:
- the raw field value itself
- the field's deviation from the scan average or median

This is intended to support the fields used in the activity scan workflow and the generated universe-statistics and per-ticker deviation outputs.

## How To Read Deviations
For any numeric field, there are two common comparisons:

- Deviation from mean:
  `field_dev_mean = company_value - universe_mean`
- Deviation from median:
  `field_dev_median = company_value - universe_median`

Practical rule:
- Use median deviation as the primary reference for highly skewed fields such as volume, traded value, market-cap-adjacent turnover, and volatility spikes.
- Use mean deviation as a secondary reference when the field is closer to a symmetric distribution.
- For very skewed fields, percentile ranking or robust z-score is often better than plain mean deviation.

## Interpretation Framework
Use the following decision logic when reading a field:

1. Is the raw value high or low in an absolute sense?
2. Is it high or low relative to the current universe?
3. Is that direction good, bad, or simply unusual for the use case?
4. Does the deviation confirm the narrative from other fields?

Example:
- `relative_volume_10d_calc` high on its own means elevated trading participation.
- `relative_volume_10d_calc__dev_median` strongly positive means that participation is not just elevated, but abnormally elevated versus peers in the same scan.

## Participation, Float, And Trading Value

| Field | Meaning | Practical use of raw value | Practical use of deviation from average / median |
| --- | --- | --- | --- |
| `float_shares_outstanding` | Tradable share float, excluding closely held or restricted shares where supported by source methodology. | Smaller float can make price moves more explosive because less stock is available to absorb demand. Larger float usually dampens squeeze-like moves. | A large negative deviation vs median can flag tighter float structure than the rest of the universe. Combined with high volume or high relative volume, it suggests stronger squeeze or crowding potential. |
| `volume` | Current session share volume. | High raw volume means heavy trading interest. On its own, it matters most when compared to float or recent average volume. | Positive deviation vs mean/median shows current participation is elevated vs the scan universe. More useful when paired with `relative_volume_10d_calc` because raw volume alone favors larger companies. |
| `average_volume_10d_calc` | Average daily volume over the last 10 trading days. | Short-horizon liquidity baseline. Useful for detecting whether current activity is abnormal relative to the recent regime. | If current `volume` is high but `average_volume_10d_calc` deviation is also high, the name may simply always trade heavily. If `volume` is high and 10D average is not, the move is more event-like. |
| `average_volume_30d_calc` | Average daily volume over 30 trading days. | Medium-short liquidity baseline. Better than 10D when the last 10 days are distorted by a recent event. | A company with high current volume but low 30D average deviation may be experiencing a real participation break from normal behavior. |
| `average_volume_60d_calc` | Average daily volume over 60 trading days. | Broader baseline for liquidity and participation persistence. | Positive deviation suggests structurally active names. Negative deviation with very high current volume can indicate a recent temporary spike. |
| `average_volume_90d_calc` | Average daily volume over 90 trading days. | Longer baseline for whether a stock is usually liquid or usually ignored. | Strong positive deviation means the stock is structurally liquid relative to peers. High current volume on top of low 90D deviation often signals abnormal attention. |
| `relative_volume_10d_calc` | Current volume divided by recent average volume, typically 10D. | One of the best direct measures of abnormal participation. Values materially above 1 indicate above-normal trading activity. | Positive deviation vs median is a strong attention signal. If this field is an outlier, it often matters more than raw volume because it normalizes for company size and typical trading level. |
| `Value.Traded` | Current session traded notional value, usually price times volume. | Better than share volume for comparing across low-price and high-price stocks. High value traded means meaningful capital is moving. | Positive deviation shows unusual dollar participation. This is often more institutionally relevant than share volume deviation. |
| `AvgValue.Traded_10d` | Average traded notional value over 10 days. | Short-term capital-flow baseline. Useful for judging whether a stock can absorb large orders. | High current `Value.Traded` plus low `AvgValue.Traded_10d` deviation suggests new attention. High in both suggests an already liquid institutional name. |
| `AvgValue.Traded_30d` | Average traded notional value over 30 days. | More stable capital-flow baseline than 10D. | Compare against current `Value.Traded` to identify recent acceleration in capital commitment. |
| `AvgValue.Traded_60d` | Average traded notional value over 60 days. | Longer baseline for structural liquidity. | Helps distinguish persistent liquidity from a short-lived burst of attention. |
| `AvgValue.Traded_90d` | Average traded notional value over 90 days. | Long-horizon liquidity anchor for execution feasibility and institutional relevance. | Strong negative deviation combined with a sudden high current value traded can identify names that are newly entering the market's focus. |

## Volatility And Risk Structure

| Field | Meaning | Practical use of raw value | Practical use of deviation from average / median |
| --- | --- | --- | --- |
| `ADR` | Average daily range, often high-low range averaged over a lookback. | Measures typical daily movement amplitude. Higher raw values indicate larger day-to-day trading ranges. | Strong positive deviation means the name is moving more widely than peers. Useful for identifying unstable names or potential breakout candidates. |
| `ATR` | Average true range, incorporating gaps as well as intraday range. | A practical measure of actual trading range and stop placement distance. | Positive deviation indicates broader actual price movement than peers. More robust than simple range when gaps matter. |
| `ATRP` | ATR expressed as a percent of price. | Better cross-sectional comparison than raw ATR because it normalizes for stock price level. | Strong positive deviation highlights names with extreme percentage volatility. This is more actionable than raw ATR when comparing low-price and high-price names together. |
| `Volatility.D` | Daily volatility estimate. | Captures day-level instability. Useful for sizing and risk control. | Positive deviation indicates the stock is unusually unstable even among active names. |
| `Volatility.W` | Weekly volatility estimate. | Better for swing-trading horizon than daily volatility. | Positive deviation suggests larger short-term multi-day swings than peers. |
| `Volatility.M` | Monthly volatility estimate. | Useful for broader tactical risk framing. | High deviation indicates persistent rather than one-day-only instability. |
| `beta_1_year` | 1-year beta vs benchmark. | Measures benchmark sensitivity over a short historical window. Greater than 1 implies amplified market moves. | Positive deviation means the stock is more market-sensitive than peers. This matters when separating stock-specific attention from broad risk-on behavior. |
| `beta_3_year` | 3-year beta vs benchmark. | Longer-horizon market sensitivity. More stable than 1-year beta. | Strong deviation can indicate persistent cyclicality or defensive behavior relative to peers. |
| `beta_5_year` | 5-year beta vs benchmark. | Long-horizon systematic risk profile. | Use deviation to identify names that are structurally more aggressive or defensive than the current scan cohort. |

## Price Change, Gaps, And Session Event Signals

| Field | Meaning | Practical use of raw value | Practical use of deviation from average / median |
| --- | --- | --- | --- |
| `change` | Current regular-session price change, usually percent. | Direct measure of current move magnitude. | Large positive or negative deviation means the stock is moving far more than peers today. This helps identify the true leaders and laggards of the session. |
| `gap` | Open vs prior close gap, usually percent. | Shows whether the day started with a discontinuous repricing. | Strong deviation suggests an event-driven opening, especially when paired with `ATRP`, `premarket_volume`, or `postmarket_change`. |
| `premarket_gap` | Premarket price gap vs prior close. | Measures off-session repricing before the main market opens. | Large deviation flags names where information was priced before the bell. Useful for event and catalyst scans. |
| `premarket_change` | Premarket price change. | Indicates direction and scale of off-session momentum. | Positive deviation can confirm that attention is already building before the regular session begins. |
| `premarket_volume` | Premarket share volume. | Shows how much actual participation exists behind the premarket move. | High positive deviation is much more meaningful than price change alone because it confirms real trading interest, not just thin prints. |
| `postmarket_change` | After-hours price change. | Useful for earnings reactions, guidance shocks, and late news repricing. | Large deviation highlights names whose narrative changed after the close. |
| `postmarket_volume` | After-hours share volume. | Confirms whether after-hours moves are meaningful or thin. | Strong positive deviation supports the idea that after-hours repricing is real and not just illiquid noise. |

## Performance And Multi-Horizon Momentum

| Field | Meaning | Practical use of raw value | Practical use of deviation from average / median |
| --- | --- | --- | --- |
| `Perf.5D` | 5-day price performance. | Very short momentum snapshot. Useful for identifying names already moving this week. | Positive deviation flags short-term leadership; negative deviation flags short-term breakdowns. |
| `Perf.W` | 1-week performance. | Similar to 5D but aligned to weekly framing. | Strong deviation can confirm current swing direction relative to the universe. |
| `Perf.1M` | 1-month performance. | Good medium-short momentum anchor. | Positive deviation often matters most when paired with high relative volume, because it suggests momentum is being supported by participation. |
| `Perf.3M` | 3-month performance. | Medium-horizon trend strength. | Strong deviation identifies sustained winners or losers rather than one-week noise. |
| `Perf.YTD` | Year-to-date performance. | Useful for calendar-year leadership and laggard analysis. | Positive deviation shows the stock is outperforming the scan universe on the current year basis. |
| `Perf.Y` | 1-year trailing performance. | Longer trend measure and crowding proxy. | Very high positive deviation may indicate powerful trend persistence or, in some cases, overextension and crowded positioning. |

## Oscillators And Momentum Internals

| Field | Meaning | Practical use of raw value | Practical use of deviation from average / median |
| --- | --- | --- | --- |
| `RSI` | Standard relative strength index, usually 14-period. | Measures momentum stretch. High values can indicate overbought conditions; low values can indicate oversold conditions. | Positive deviation shows stronger near-term momentum than peers. More useful cross-sectionally than using absolute 70/30 thresholds alone. |
| `RSI7` | Shorter-lookback RSI. | Faster and more sensitive than standard RSI. Good for detecting early acceleration or exhaustion. | Large deviation often shows short-term momentum shifts before standard RSI fully responds. |
| `MACD.macd` | MACD line. | Indicates momentum direction and acceleration relative to trend. | Positive deviation suggests stronger bullish momentum impulse vs peers. Negative deviation suggests weaker momentum or bearish impulse. |
| `MACD.signal` | MACD signal line. | Smoother confirmation line for MACD state. | Compare `MACD.macd` vs `MACD.signal`, then compare both vs peers. A high positive deviation in MACD without confirmation in other fields can still be early. |
| `Mom` | Momentum indicator, often price change over a selected lookback. | Raw directional speed indicator. | Positive deviation highlights names with stronger directional thrust than peers. |
| `ROC` | Rate of change. | Percentage speed of price movement over a lookback. | Large positive or negative deviation can be more interpretable than raw values when comparing across many tickers. |

## Analyst And Composite Recommendation Signals

| Field | Meaning | Practical use of raw value | Practical use of deviation from average / median |
| --- | --- | --- | --- |
| `Recommend.All` | Composite recommendation score. | Broad sentiment summary. Useful as a soft filter, not a standalone signal. | Positive deviation can show the stock is more favorably viewed than peers, but this usually lags price and earnings signals. |
| `Recommend.MA` | Recommendation component based on moving-average style logic. | Reflects trend-following technical stance. | Positive deviation can confirm technical leadership. Negative deviation can confirm broader technical weakness. |
| `Recommend.Other` | Recommendation component from non-MA signals. | Useful as a secondary sentiment or model-based confirmation input. | Deviation can help spot names where model sentiment is unusually positive or negative compared with the universe. |

## Price Anchors And Trend Reference Levels

| Field | Meaning | Practical use of raw value | Practical use of deviation from average / median |
| --- | --- | --- | --- |
| `VWAP` | Volume-weighted average price for the session or configured period. | Useful for intraday execution and whether price is trading above or below value. | Deviation vs peers is less important than comparison vs the stock's own `close`, but cross-sectional deviation can still identify names trading at unusually high absolute price anchors. |
| `VWMA` | Volume-weighted moving average. | Trend anchor that emphasizes price levels where more volume occurred. | Compare `close` to `VWMA`; deviation of `VWMA` itself is secondary to the stock-relative relationship. |
| `SMA50` | 50-period simple moving average. | Medium-term trend reference. Price above it usually implies intermediate trend support. | Cross-sectional deviation of `SMA50` itself is less informative than `close - SMA50`, but high positive distance from peers may indicate stronger trend extension. |
| `SMA200` | 200-period simple moving average. | Long-term trend reference and widely watched institutional anchor. | Again, relationship to current price matters more than raw level. Deviation can help identify names whose long-term trend base is materially stronger or weaker than peers. |
| `EMA50` | 50-period exponential moving average. | Faster medium-term trend reference than SMA50. | More responsive trend support/resistance anchor. Deviation is most useful when combined with current price position. |
| `EMA200` | 200-period exponential moving average. | Long-horizon trend anchor with somewhat faster adjustment than SMA200. | Useful for trend-confirmation ranking when combined with current price and other average levels. |

## Earnings Timing And Market Context

| Field | Meaning | Practical use of raw value | Practical use of deviation from average / median |
| --- | --- | --- | --- |
| `earnings_release_date` | Last or current earnings release date field depending on source mapping. | Use to identify whether a recent spike, gap, or volatility burst may be earnings-related. | This is a date field, so direct mean/median deviation is not the right interpretation. Better use days since earnings or days until next earnings. |
| `earnings_release_next_date` | Next expected earnings date. | Essential for event risk management, especially when participation and volatility are elevated. | Again, transform to days-to-event rather than using raw deviation from average. Names with high participation and near earnings dates deserve separate treatment. |
| `market` | TradingView market or exchange-region grouping. | Useful for segmenting by geography, session structure, and liquidity regime. Helps avoid mixing structurally different markets without context. | Since this is categorical, average deviation does not apply. The practical comparison is group membership and market-specific baseline behavior. |

## Practical Combinations That Matter Most

### 1. Attention Spike
Use together:
- `relative_volume_10d_calc`
- `premarket_volume`
- `Value.Traded`
- `gap`
- `premarket_gap`

Interpretation:
- If all are high and positively deviated vs median, the stock is not just active, it is undergoing abnormal repricing with real participation.

### 2. Squeeze Or Tight-Float Alert
Use together:
- `float_shares_outstanding`
- `volume`
- `relative_volume_10d_calc`
- `Perf.5D`
- `Perf.1M`

Interpretation:
- Lower float than peers plus very high current participation and strong recent performance can indicate unstable upside price discovery.

### 3. Real Momentum Confirmation
Use together:
- `Perf.1M`
- `Perf.3M`
- `relative_volume_10d_calc`
- `RSI`
- `MACD.macd`
- `Recommend.MA`

Interpretation:
- Momentum is stronger when performance, participation, and trend-model readings all deviate positively at the same time.

### 4. Event Repricing
Use together:
- `premarket_gap`
- `premarket_change`
- `premarket_volume`
- `postmarket_change`
- `postmarket_volume`
- `earnings_release_next_date`
- `earnings_release_date`

Interpretation:
- Big off-session price changes matter more when accompanied by volume and earnings calendar proximity.

### 5. Trend Confirmation
Use together:
- `close`
- `VWAP`
- `VWMA`
- `SMA50`
- `SMA200`
- `EMA50`
- `EMA200`

Interpretation:
- The strongest technical backdrop is usually price above most or all major trend anchors while participation is also elevated.

## Practical Rules For Mean Vs Median
- Prefer median comparison for:
  - `volume`
  - `Value.Traded`
  - `AvgValue.Traded_*`
  - `premarket_volume`
  - `postmarket_volume`
  - `ADR`, `ATR`, `ATRP`
  - `Perf.Y`
- Mean comparison is acceptable, but still secondary, for more bounded fields such as:
  - `RSI`
  - `RSI7`
  - `Recommend.All`
  - `Recommend.MA`
  - `Recommend.Other`
  - `beta_*`

## Final Working Principle
Do not interpret any one activity field in isolation.

The most reliable practical workflow is:
1. Identify abnormal participation using `relative_volume_10d_calc`, `Value.Traded`, and `premarket_volume`.
2. Check whether price is actually repricing using `change`, `gap`, `premarket_gap`, and `Perf.*` fields.
3. Check whether the move is structurally supported using `VWAP`, `VWMA`, `SMA50`, `SMA200`, `EMA50`, and `EMA200`.
4. Use deviations from median to decide whether the move is merely strong or genuinely unusual relative to the current universe.
