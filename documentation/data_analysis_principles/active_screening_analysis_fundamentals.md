# Active Screening Analysis Fundamentals (Current Stage)

## 1) Current Objective
Build an active portfolio process that uses SEC-reported fundamentals to:
- Compare companies within industries and across time.
- Detect improving fundamentals early.
- Allocate more capital to asymmetric risk/reward setups.

The goal is not only to find growth, but to find growth quality and capital discipline that are not fully priced yet.

## 2) What Your Data Already Supports
From your concept-frequency file (`all_financial_concept_values.csv`), the universe has strong breadth and depth:
- Concepts with fact_count > 50,000: 330
- Max fact_count: 3,601,636
- Median fact_count (within >50k): 123,289

This is enough coverage to build robust multi-factor signals. You do not need to rely on one metric (for example revenue share) in isolation.

## 3) Core Concept Families To Prioritize
At this stage, prioritize high-coverage families that are most explanatory for active allocation:

1. Revenue and demand
- RevenueFromContractWithCustomerExcludingAssessedTax
- Revenues
- SalesRevenueNet
- Revenue
- RevenueFromContractWithCustomerIncludingAssessedTax


2. Profitability quality
- OperatingIncomeLoss
- GrossProfit
- NetIncomeLoss
- ProfitLoss
- IncomeLossFromContinuingOperations...

3. Cash generation
- NetCashProvidedByUsedInOperatingActivities
- NetCashProvidedByUsedInInvestingActivities
- NetCashProvidedByUsedInFinancingActivities

4. Balance-sheet resilience
- Assets, Liabilities, StockholdersEquity
- LongTermDebt, DebtCurrent, InterestExpense
- Goodwill, IntangibleAssets

5. Capital allocation and dilution
- PaymentsForRepurchaseOfCommonStock
- DividendsCommonStockCash / PaymentsOfDividends
- StockIssuedDuringPeriod... concepts
- ShareBasedCompensation
- WeightedAverageNumberOfDilutedSharesOutstanding

## 4) Your Base Signal Should Evolve Like This
You already use industry revenue-share trend (for example Aerospace & Defense over 10 years). Keep that as the anchor and add quality overlays.

Recommended progression:
1. Start with industry revenue share trend.
2. Add profitability trend confirmation.
3. Add cash-flow conversion filter.
4. Add dilution and capital discipline filter.
5. Add valuation overlay before sizing.

This sequence improves false-positive control while preserving upside detection.

## 5) Canonical Metric Construction (Very Important)
Because EDGAR tags vary, define fallback stacks for each metric.

Example: Revenue fallback stack
1. RevenueFromContractWithCustomerExcludingAssessedTax
2. Revenues
3. RevenueFromContractWithCustomerIncludingAssessedTax
4. SalesRevenueNet
5. Revenue

Apply the same approach to:
- Net income
- Operating income
- Shares outstanding
- Capex
- Debt
- Equity

Rule: use the first available concept in a defined fallback order for each ticker-year.

## 6) Metrics To Compute Per Ticker-Year
Use these first before adding advanced variants:

1. Growth and share
- Revenue YoY
- 3y and 5y revenue CAGR
- Industry revenue share
- Change in industry share (1y, 3y)

2. Profitability and quality
- Operating margin = OperatingIncome / Revenue
- Net margin = NetIncome / Revenue
- Cash conversion = CFO / NetIncome
- Accrual proxy = (NetIncome - CFO) / Assets

3. Capital intensity and discipline
- Capex intensity = Capex / Revenue
- SBC burden = ShareBasedCompensation / Revenue
- Net issuance pressure using stock issuance and repurchase concepts

4. Balance-sheet risk
- Leverage proxy = Debt / Equity (or Debt / Assets fallback)
- Interest burden = InterestExpense / OperatingIncome

5. Per-share integrity
- Diluted shares growth trend
- EPS growth versus revenue growth divergence

## 7) Information-Value Scoring of Concepts
Do not pick concepts only by fact_count. Use a concept information score.

For concept i:

- Coverage:
  C_i = log(1 + fact_count_i) / log(1 + fact_count_max)

- Cross-sectional dispersion (useful variation):
  D_i = std(z_i) / (1 + abs(skew(z_i)))

- Predictive relation with forward returns:
  P_i = abs(IC_i)
  where IC is rank-correlation with next-period excess return

- Stability over time:
  S_i = 1 - std(IC_rolling)

Composite:
InfoScore_i = 0.35*C_i + 0.25*D_i + 0.30*P_i + 0.10*S_i

Practical use:
- Keep top concepts by InfoScore within each family.
- Recompute quarterly or semiannually.

## 8) Factor Model For Active Screening
Construct a composite score from industry-relative z-scores:

CompositeAlpha =
- 30% Industry share momentum
- 25% Quality and cash conversion
- 20% Capital discipline / dilution control
- 25% Valuation attractiveness

Notes:
- Standardize within industry and year to avoid sector bias.
- Winsorize extreme values before z-scoring.
- Require minimum data completeness thresholds before assigning full score.

## 9) Capital Allocation Framework (Asymmetric Risk Focus)
Position sizing should depend on expected edge and downside risk, not raw conviction alone.

Baseline sizing logic:
- Higher CompositeAlpha -> larger target weight.
- Scale down weight by downside volatility and drawdown risk.
- Enforce caps: max single-name, max industry, liquidity constraints.

Suggested implementation idea:
TargetWeight_i proportional to Edge_i / DownsideRisk_i
then clipped by portfolio constraints.

Asymmetric setup criteria (high priority names):
- Rising industry share
- Improving cash conversion and margins
- Stable or improving leverage
- Limited dilution
- Valuation still below peer-adjusted quality level

## 10) Process Controls You Should Add Now
1. Data quality flags
- Missing concept fallback depth used
- Non-comparable fiscal periods
- Outlier value checks by concept

2. Comparability rules
- Prefer FY (qtrs=4) for strategic trend layers
- Use trailing windows for timeliness overlays

3. Regime awareness
- Separate factor performance in risk-on vs risk-off periods
- Track factor decay over time

4. Monitoring dashboard outputs
- Top/bottom decile names by composite score
- Score changes month over month
- Attribution: which factor drove each name move

## 11) Immediate Next Build Steps
1. Build canonical concept mapping tables.
2. Create ticker-year metric panel with quality flags.
3. Compute industry-relative z-scores for all core metrics.
4. Build CompositeAlpha and rank names by industry.
5. Backtest rank buckets and turnover-adjusted returns.
6. Add downside-aware position sizing and risk caps.

## 12) Practical Principle For This Stage
Your analysis should move from single-dimension screening to layered evidence:
- Demand trend (share),
- Economic quality (margins/cash conversion),
- Capital behavior (dilution/buybacks/debt),
- Price paid (valuation).

This combination is the most reliable path to improve active capital allocation with asymmetric upside/downside control using your current data foundation.
