# Position Sizing Distribution Framework For TradingView Model Outputs

## Goal

This guide gives you a starting framework for turning the outputs of your move-prediction, attention, price-performance, and valuation models into actual position sizes.

The focus is not just on picking the best names. The focus is on capital distribution:

- how much capital should stay free
- how much should be committed by holding horizon
- how much should go into one position
- how much should be added on confirmation
- how to avoid capital getting trapped in the wrong time bucket

This is a framework, not a claim that any sizing method will maximize gains by itself. The practical objective is better upside capture with fewer large mistakes and less dead capital.

## Core Principle

Do not size directly from conviction alone.

Size in this order:

1. Decide how total capital is distributed across time horizons.
2. Decide the maximum position size allowed inside each horizon bucket.
3. Decide whether the candidate passes your risk and liquidity gates.
4. Use model alignment to choose where inside the allowed size range the position should land.
5. Add only when the move confirms and the thesis stays in the correct horizon bucket.

That order matters because a very attractive short-term setup should not consume the same amount of capital as a multi-year compounder, and a multi-year idea should not block tactical capital.

## The Models And Their Jobs

Use each model for a different role.

### 1. Move prediction model

Use it as the primary directional ranking engine.

It is best for:

- deciding direction
- deciding expected horizon fit
- comparing names inside the same scan universe
- deciding whether the setup is attention-led, trend-led, quality-led, or safety-led

### 2. Attention / float / activity model

Use it to judge whether the move is being powered by real participation or by unusual float dynamics.

It is best for:

- abnormal participation
- event pressure
- squeeze risk or squeeze opportunity
- deciding whether a name belongs in a tactical bucket rather than a long-duration bucket

### 3. Price-performance model

Use it to identify whether the name already has trend leadership or laggard behavior.

It is best for:

- trend persistence
- timing long continuation versus trying to catch reversals
- separating durable leaders from short-term bursts

### 4. Valuation model

Use it to decide how much patience a position deserves.

It is best for:

- deciding whether a weak short-term tape might still deserve medium- or long-horizon capital
- deciding whether a strong short-term move is already too expensive for larger size

### 5. Safety model

Use it mainly as a gate, not as the main size driver.

If safety is poor, your maximum size should drop even if the move model looks strong.

## Step 1: Split Capital By Time Horizon

Start by dividing the account into horizon buckets. This solves the "capital blocked in the wrong place" problem better than trying to fix everything with stop losses.

### Starting allocation model

For a general active workflow, use this starting split:

| Bucket | Holding horizon | Role | % of total capital |
| --- | --- | --- | ---: |
| Cash reserve | Always available | Optionality, adds, new setups, drawdown control | 20% |
| Tactical | 1 to 2 trading days | Event moves, squeeze moves, breakouts, failed breaks | 15% |
| Swing | 3 days to 3 weeks | Trend continuation, event follow-through, rerates | 20% |
| Intermediate | 1 to 6 months | valuation plus improving quality, medium-term themes | 20% |
| Long duration | 6 months to 2 years | quality plus value plus safety | 15% |
| Very long duration | 2 years plus | highest-conviction compounders only | 10% |

This does three useful things:

- keeps 20% free so you can add to winners or respond to new opportunity
- stops short-term ideas from eating your long-term capital
- stops slow long-term ideas from clogging tactical capital

## Step 2: Build A Model Alignment Score

Do not let one model decide size alone. Use a weighted alignment score.

### A. Tactical horizon score

For 1 to 2 day trades:

```text
Tactical score =
0.45 * move_prediction_days
+ 0.30 * attention_score
+ 0.20 * price_performance_short
+ 0.05 * valuation_score
```

Interpretation:

- move model leads
- attention matters a lot because abnormal participation drives short moves
- price performance confirms tape strength
- valuation matters the least because it usually does not control a 1 to 2 day move

### B. Swing horizon score

For 3 days to 3 weeks:

```text
Swing score =
0.40 * move_prediction_weeks
+ 0.25 * attention_score
+ 0.20 * price_performance_medium
+ 0.15 * valuation_score
```

### C. Intermediate horizon score

For 1 to 6 months:

```text
Intermediate score =
0.35 * move_prediction_months
+ 0.15 * attention_score
+ 0.20 * price_performance_1m_to_1y
+ 0.30 * valuation_score
```

### D. Long-duration score

For 6 months to years:

```text
Long-duration score =
0.30 * move_prediction_years
+ 0.05 * attention_score
+ 0.20 * price_performance_1y
+ 0.45 * valuation_score
```

### Safety gate

Before position sizing, apply a safety gate:

- strong safety: no haircut
- mixed safety: reduce final size by 15%
- weak safety: reduce final size by 35% to 50%
- very weak safety: tactical trade only, or no trade

That keeps low-quality balance sheets from getting medium- and long-horizon size just because the tape is hot.

## Step 3: Convert Alignment Into Target Size

Use the model alignment score to choose the position size inside the horizon bucket.

### Starting conviction bands

Normalize your alignment score to a 0 to 100 scale if possible. Then use this table:

| Alignment score | Meaning | Target size inside that horizon bucket |
| --- | --- | ---: |
| 85 to 100 | top-tier setup | 100% of allowed target |
| 70 to 84 | strong setup | 75% of allowed target |
| 55 to 69 | decent setup | 50% of allowed target |
| below 55 | weak or mixed | 0% to 25% starter only |

## Step 4: Set Maximum Position Size By Horizon

Do not use one max size for every holding period.

### Starting maximums by horizon

| Bucket | Max position as % of total capital | Typical starter |
| --- | ---: | ---: |
| Tactical | 4% | 2.0% |
| Swing | 6% | 3.0% |
| Intermediate | 8% | 4.0% |
| Long duration | 10% | 5.0% |
| Very long duration | 12% | 6.0% |

This means:

- a tactical trade should stay small because it can fail quickly and often depends on unstable flow
- a long-duration winner can earn larger size, but only if valuation, quality, and safety all support patience

## Step 5: Use Float % As A Structure Modifier

`float_shares_percent_current` should not automatically make a position bigger. It should change how you interpret the move and how aggressive you are.

### How to read it

- lower Float % usually means a tighter float structure relative to total shares outstanding
- tighter float plus high attention can create fast upside or violent downside
- high Float % usually means price moves are more supply-rich and less squeeze-sensitive

### Starting Float % modifier

| Float % | Interpretation | Size modifier |
| --- | --- | ---: |
| below 20% | very tight structure, squeeze-capable, unstable | 0.60x |
| 20% to 40% | moderately tight | 0.80x |
| 40% to 70% | normal | 1.00x |
| above 70% | broad float, less squeeze-driven | 1.00x |

Important nuance:

- low Float % does not mean avoid it
- low Float % means treat it as tactical or swing unless fundamentals are unusually strong
- if attention and relative volume are extreme, low Float % is telling you that the move may be structure-driven rather than durable

That is exactly why it belongs in the move-model CSV and log output as a diagnostic field.

## Step 6: Add A Liquidity Modifier

Use participation to decide whether the position can absorb size.

### Starting liquidity modifier

| Condition | Modifier |
| --- | ---: |
| high value traded and normal float | 1.00x |
| strong relative volume but still low traded value | 0.85x |
| low liquidity for your intended size | 0.50x to 0.70x |

Practical rule:

If the stock cannot absorb your planned size cleanly, the right position size is smaller even if the signal is good.

## Step 7: Add In Tranches, Not All At Once

Do not take full size on entry unless the setup is both high-conviction and highly liquid.

### Starting add model

For tactical and swing trades:

- starter: 50% of target size
- first add: 30% of target size after confirmation
- final add: 20% of target size after follow-through

For intermediate and long-duration trades:

- starter: 50% of target size
- first add: 30% of target size after thesis confirmation
- final add: 20% of target size on pullback hold, trend confirmation, or new fundamental evidence

### What counts as confirmation

Use confirmations that match the horizon.

For tactical:

- move-prediction days and weeks scores stay positive
- attention remains elevated
- price holds breakout or gap level

For swing:

- weeks score improves or stays strong
- price-performance leadership persists
- value-traded stays healthy

For intermediate and long duration:

- months or years score remains strong
- valuation still makes sense after the move
- safety is not deteriorating
- the trade thesis is not turning into a different horizon than intended

## Step 8: Use Time Stops So Capital Does Not Get Trapped

This is one of the most important parts.

If the trade is supposed to work in a given time bucket, do not let it sit there indefinitely.

### Starting time-stop rules

| Bucket | If it does not behave by... | Action |
| --- | --- | --- |
| Tactical | end of day 2 | cut or recycle capital |
| Swing | 5 to 10 trading days | reduce or exit if leadership fades |
| Intermediate | 4 to 8 weeks | keep only if months thesis is improving |
| Long duration | 1 quarter | keep only if quality/value thesis is still intact |
| Very long duration | each quarter or earnings cycle | keep only if long thesis survives fundamental review |

Capital blocking happens when you keep tactical money in a slow thesis or long-term money in a broken thesis.

## Position Sizing Formula

Use a formula like this:

```text
Final position % of account =
Horizon max %
* conviction multiplier
* float modifier
* liquidity modifier
* safety modifier
* correlation modifier
```

### Starting multipliers

#### Conviction multiplier

- top-tier setup: 1.00
- strong setup: 0.75
- decent setup: 0.50
- weak setup: 0.25 or zero

#### Float modifier

- below 20% float: 0.60
- 20% to 40%: 0.80
- above 40%: 1.00

#### Liquidity modifier

- strong liquidity: 1.00
- acceptable liquidity: 0.85
- weak liquidity: 0.50 to 0.70

#### Safety modifier

- strong safety: 1.00
- mixed safety: 0.85
- weak safety: 0.50 to 0.65

#### Correlation modifier

If you already hold similar names or the same macro factor:

- low overlap: 1.00
- moderate overlap: 0.85
- high overlap: 0.60

## Example For A 10,000 USD Account

### Step 1: Split capital

| Bucket | % | USD |
| --- | ---: | ---: |
| Cash reserve | 20% | 2,000 |
| Tactical | 15% | 1,500 |
| Swing | 20% | 2,000 |
| Intermediate | 20% | 2,000 |
| Long duration | 15% | 1,500 |
| Very long duration | 10% | 1,000 |

You are not forced to fill every bucket at once. The point is to reserve capacity.

### Example A: Tactical breakout / squeeze candidate

Assume:

- move days score is very strong
- attention score is extreme
- price-performance short-term is strong
- valuation is not helpful but not the main driver
- Float % is 18%
- safety is mixed

Use:

- horizon max = 4%
- conviction multiplier = 1.00
- float modifier = 0.60
- liquidity modifier = 0.85
- safety modifier = 0.85
- correlation modifier = 1.00

```text
Final size = 4.0% * 1.00 * 0.60 * 0.85 * 0.85 * 1.00
           = 1.73% of account
```

On a 10,000 USD account:

- target position = about 173 USD
- starter at 50% = about 86 USD
- first add at 30% = about 52 USD
- final add at 20% = about 35 USD

This is intentionally small because the setup is real but unstable. Tight float increases move potential and risk at the same time.

### Example B: Swing leader

Assume:

- move weeks score is strong
- attention is healthy, not extreme
- price-performance is strong on 1M and 3M
- valuation is acceptable
- Float % is 55%
- safety is strong

Use:

- horizon max = 6%
- conviction multiplier = 0.75
- float modifier = 1.00
- liquidity modifier = 1.00
- safety modifier = 1.00
- correlation modifier = 0.85

```text
Final size = 6.0% * 0.75 * 1.00 * 1.00 * 1.00 * 0.85
           = 3.83% of account
```

On a 10,000 USD account:

- target position = about 383 USD
- starter = about 192 USD
- first add = about 115 USD
- final add = about 76 USD

### Example C: Intermediate value recovery

Assume:

- move months score is improving
- attention is only moderate
- price performance is not yet strong
- valuation is very attractive
- safety is acceptable but not great
- Float % is 68%

Use:

- horizon max = 8%
- conviction multiplier = 0.75
- float modifier = 1.00
- liquidity modifier = 0.85
- safety modifier = 0.85
- correlation modifier = 1.00

```text
Final size = 8.0% * 0.75 * 1.00 * 0.85 * 0.85 * 1.00
           = 4.34% of account
```

On a 10,000 USD account:

- target position = about 434 USD
- starter = about 217 USD
- first add = about 130 USD
- final add = about 87 USD

### Example D: Long-duration compounder

Assume:

- move years score is strong
- attention is low, which is fine
- price-performance 1Y is constructive
- valuation is fair to attractive
- safety is strong
- Float % is 82%

Use:

- horizon max = 10%
- conviction multiplier = 0.75
- float modifier = 1.00
- liquidity modifier = 1.00
- safety modifier = 1.00
- correlation modifier = 1.00

```text
Final size = 10.0% * 0.75 * 1.00 * 1.00 * 1.00 * 1.00
           = 7.50% of account
```

On a 10,000 USD account:

- target position = 750 USD
- starter = 375 USD
- first add = 225 USD
- final add = 150 USD

This is where larger size belongs: high-quality, safer, longer-duration ideas that are less dependent on unstable flow.

## A Simple Operating Playbook

If you want one starting process, use this every time:

1. Assign the candidate to one horizon bucket first.
2. Reject the trade if it does not clearly belong to one bucket.
3. Check safety and liquidity gates.
4. Build the weighted alignment score from move, attention, price, and value.
5. Apply Float % and liquidity modifiers.
6. Start with 50% of target size.
7. Add only if the thesis confirms inside the intended horizon.
8. Use time stops to recycle blocked capital.

## What Not To Do

Avoid these mistakes:

- using the same position size for a 2-day squeeze trade and a 2-year compounder
- taking full size at entry in low-float names
- averaging down just because valuation looks better after price falls
- letting tactical positions become long-term holdings by accident
- using valuation to justify oversized positions in weak-balance-sheet names
- using attention alone as a reason to size big

## Best Starting Rules

If you want the shortest usable version of this framework, start here:

1. Keep 20% cash free.
2. Use separate capital buckets by horizon.
3. Cap tactical trades at 4% of account and long-duration ideas at 10% to 12% maximum.
4. Start every position at 50% of target size.
5. Add only on confirmation.
6. Cut or reduce positions that fail their time bucket.
7. Shrink size when Float % is low and the move looks squeeze-driven.
8. Let larger sizes belong to liquid, safer, longer-duration ideas.

## How This Fits Your Current Prediction Model

Your move-prediction model should be the main direction and horizon engine.

Use the other models to answer these questions:

- attention model: is this move real and how much is flow-driven?
- price model: is this already a leader or just noisy?
- valuation model: does this deserve patience and larger capital?
- safety model: should size be cut even if the setup looks attractive?

The new Float % field in the move-model output is useful because it helps separate:

- durable trend participation
- low-float squeeze behavior
- high-volatility attention bursts
- more normal liquid participation

That is exactly the kind of field you want for size adjustment, not just for signal ranking.