# Regime playbook

What has worked in which market, how to tell which market we are in, and what to change when it turns.
Numbers: signal lab 2026-10-27 (US $1B+, 140 day files 2026-03-29 to 2026-10-27) unless marked OUTSIDE.

## 1. Read the regime first (deterministic, then OUTSIDE)

| Read | Where | Up regime | Down regime |
|---|---|---|---|
| Universe median trailing 1-month return | signal lab `market.mkt_regime`; pack `regime.us_2b.m1` | > 0 | <= 0 |
| Breadth | pack `regime.us_2b.pct_up`, `industries_5d` | most industries up on the week | leadership narrow or rotating |
| Factor month | signal lab `factor_month` | momentum / street upside paying | value / quality / low volatility paying |
| Rates, oil, inflation (OUTSIDE) | news, dated | easing or on hold | hikes priced, oil spiking, CPI rising |

The point-in-time split used by the lab is the universe median 1-month return at the scan (`mkt_regime`):
87 up days and 46 down days in the 7-day sample.

Month by month (universe median trailing 1M; median forward 7d / 28d excess is zero by construction):
Mar -6.9 (sell-off), Apr +4.6, May +1.6, Jun +1.8, Jul +3.0, Aug +1.8, Sep -3.4, Oct -2.5.

## 2. Current regime (2026-10-27)

- Data: two down months running (Sep -3.4, Oct -2.5); chip sell-off of about 16% between 6 and 16 Oct,
  then a chip-led bounce on 26-27 Oct.
- OUTSIDE (2026-10-27): the Fed hiked in September to 3.75-4.00%; October hike odds about 11%, December
  about 82% (CME FedWatch via Reuters); September CPI 3.7%; Brent about $110; the Anthropic IPO filing and a
  $63B Broadcom financing revived the AI trade; decision Wed 28 Oct 2pm ET, then GOOG / MSFT after the close.
- Label: **late-cycle, rate-up, oil-high, AI-capex-bid, down-trend with a bounce**. Defensive quality and
  cash-generating value lead; crowded momentum and high-target stories lag.

### Update 2026-10-28: a turn-up signal, unconfirmed

- Data: the universe median trailing month (`mkt_regime`) turned positive on 23 Oct (+0.32) and is +1.39 on
  28 Oct (US $2B: 1M +2.0%, 5D +1.5%, breadth 48% up on the day). Condition 1 of section 5's turn-up is met.
- Not yet: December hike still about 77% (CME FedWatch via CNBC, 28 Oct), 10-year about 5.0%, Brent about
  $103 (OUTSIDE). Breadth has not widened (under 55% up for a week).
- Alpha month read (week_plays `month_ind`, 28 Oct): leaders are marine shipping, P&C and multi-line
  insurance, integrated oil and refiners, royalty trusts, investment managers, memory / storage; laggards
  are electric utilities (+8.4% last month, median pred_28d -1.85%), gas distributors, biotech, REITs.
  The defensive tilt is moving from rate-sensitive yield to cash-generating value.
- Action: keep the section 3 reading order; re-run the lab with `--first-day 2026-10-23` after ten sessions
  and read whether `pt_up`, `bo` and `r_12_1` turn positive before lifting them.

### Update 2026-10-29: up month confirmed on breadth, factor tape still defensive

- Data: universe trailing month +3.4 (`mkt_regime`), US $2B 1M +3.1%, 5D +2.4%, 75% up on the day. Semiconductors
  5D +7.9% but 1M -10.2%. Condition 1 of the turn-up holds and breadth widened for a day; one day is not a week.
- OUTSIDE: the Fed held at 3.75-4.00% on 28 Oct (10-2, two dissents for a hike); one more 2026 hike still priced.
  Condition 3 of the turn-up (December hike priced out) is not met.
- Factor month (7-day IC, October): beta -0.19, volatility -0.16, street upside -0.14, 12-month momentum -0.09;
  FCF margin +0.13, p_dvc +0.10. Low-beta quality still leads; momentum has not recovered.
- Drawdowns in an up month: the deterioration study shows drawdowns of 35%+ with no letter beyond price
  bouncing (+6.7 to +6.9 over 28 days in up months); with 3+ letters they kept falling in down months
  (`deterioration_playbook.md`). Read the letters before buying any bounce.
- Action: keep the section 3 reading order; new money to low-volatility quality until beta stops losing.

### Update 2026-10-30: the AI-capex trade repriced on cash flow inside an up month

- Data: US $2B day -1.5%, 33% up; trailing month still +3.0 (up regime holds); 54.8% of the floor above the 200-day.
  Trailing 7-day IC: beta -0.133, street upside -0.113, volatility -0.109; forward EPS yield +0.122, FCF margin +0.100.
- OUTSIDE (30 Oct): Nasdaq -2.5% near a three-month low, VIX 20.0, 10-year 4.95% (flight to safety), WTI $104,
  December hike about 85% priced; NASDAQ:INTC -29% on its guide, NASDAQ:AMZN trailing FCF -$42B on capex, NYSE:RDDT
  -28%. Q3 earnings still about +31% (LSEG via Reuters): a financing and valuation scare, not an earnings recession.
- Label: **up month, AI-capex sell-off, rate-up, oil-high**. Next tests: 3 Nov midterms and NASDAQ:AMD, 4 Nov
  NASDAQ:QCOM / NASDAQ:ARM, 6 Nov jobs.
- Action: no new money into the AI cluster; band-only adds on no-letter drawdowns; one low-beta starter per Daily.
- 3:20pm ET (OUTSIDE): S&P 500 7,544 (-2.3%, 3.5% under the 6 Oct record 7,818.93), Nasdaq -3.5% (8.4% under its record), VIX 22
  (about 6.4% one-month one-sigma: a full 10% correction is a one-sigma month). Downside scan (`tv_scan_cli.py downside`): the store's
  cap-weighted floor is 4.4% under its 13 Aug high, the AI cluster 12.3% under its 6 Oct high (the June-July leg bottomed at -11.8%), and
  the Book carries 185% of NAV in beta-weighted exposure; in the current leg it fell about 2.5x the market (1.35x what beta implied).
  The store has no 10% index fall in it: read its tails as a floor. Rule added: no add with beta above 2 before the event that can reprice it.
  Go more defensive (section 5) if the trailing month turns negative or beta's trailing IC falls under -0.15.

### Update 2026-11-02: relief rally into the midterm week

- Data: US $2B +1.2% at the mark, 73% up; the universe's trailing month +4.4 (up regime). The store's cap-weighted floor is 2.9% under its
  high, the AI cluster 10.6% under its 6 Oct high. GMV value-led profiles (RERATE / VALUE_TURN) led later closes (t 5.5 / 5.4).
- OUTSIDE (2 Nov): S&P 500 +0.6 to +1% after Friday's -1.8% (Nasdaq -5.3% in October); WTI -7% to ~$97 on US-Iran talks; 10-year 4.88%;
  VIX 18.9; December hike 69-85% priced; midterms Tue with a Democratic sweep ~71% (Kalshi). Since 1974 the S&P 500 averaged +5.7% in the
  three months after a midterm (11 of 13 up). AI darlings and defense are seen as most exposed to the vote.
- Action: low-beta value starters before the events; the AI cluster's adds after NASDAQ:AMD / NASDAQ:QCOM, in bounce bands. Lean back on two
  of: the guides hold with no new letter, the cluster back above its 50-days, beta's trailing IC above -0.05.

### Update 2026-11-12: rates through 5%, a governance shock at the AI bellwether

- Data: cap-weighted flat (-0.12%) on 28% breadth and 22% up-volume; 21 sessions -1.03%, 3.1% under the store high; 56% of names over
  their 200-day. The AI cluster is 11.6% under its high after the 5 Nov crash low (S&P 500 -4.4% that day, OUTSIDE) and a five-session
  rebound. The lane book's October-proof lanes (cheap and turning, washed-out quality) still lead; AI lanes carry 20-55% odds of -10%.
- OUTSIDE (12 Nov): 10-year 5.11%; core CPI +0.3% m/m for the third month and a December hike about fully priced (FOMC 8-9 Dec); oil up
  on Iran headlines; NVIDIA's CEO resigned three sessions before its 17 Nov print; the Anthropic IPO prices 18 Nov.
- Action: limits in the measured non-AI lanes; one energy line with negative store beta as the hedge for the rates / oil leg; cash at or
  above 20% of NAV into the FOMC. AI stage B on 2 of 4 triggers (follow-through day on volume, no new cluster low for 5 sessions,
  NASDAQ:NVDA holds its band after the print, the cap month positive); stage C also needs the CEO seat settled.

### Update 2026-11-13: AI sold on good news, the rest held

- Data: cap-weighted -0.72% at the scan, median name +0.06%, 52% up; the AI cluster -2.0% (13.5% under its high, over its 5 Nov low).
  Beat-and-raise prints in AI hardware (NASDAQ:CSCO, NASDAQ:AMAT) were sold 5%+; energy, telecom, defense, beverages led the week.
- OUTSIDE (13 Nov): 10-year 5.16%, VIX over 20, October PPI +0.3% m/m; NVIDIA prints 17 Nov under an interim CEO; Anthropic prices 18 Nov.
- Action: unchanged map. Non-AI lanes at limits now; AI stage B decided on the 18 Nov close (NASDAQ:CSCO replaces NYSE:TEL as the lead);
  build-ups filled by 20 Nov; cash back at or above 20% of NAV before the 8-9 Dec FOMC.

## 3. What worked, by regime (7-day IC unless noted)

| Signal | All days | Up months | Down months | October | Read |
|---|---|---|---|---|---|
| p_def (defensive_fortress_v2) | +0.060 STRONG | +0.048 | +0.078 | +0.07 | Works in both, more in down markets (28d: +0.089 up / +0.165 down). The anchor in this regime. |
| p_dvc (durable_value_compounder_v1) | +0.088 | +0.087 | +0.090 | +0.15 | Regime-proof so far. |
| p_recov (value_recovery_v3) | +0.074 | +0.074 | +0.072 | +0.14 | Regime-proof so far. |
| p_bo (breakout_long_v1) | -0.010 | -0.048 | +0.049 | +0.09 | Fails in up markets, helps in bounces. Do not lead with `bo` in an up trend. |
| last_surprise | +0.027 | +0.015 | +0.051 | +0.03 | Stronger when the market is down (28d +0.097 down vs +0.021 up). |
| eps_rev_3m | +0.017 | -0.012 | +0.061 | -0.08 | 28d: +0.150 in down markets, about 0 in up. Revisions matter when the tape is weak. |
| r_12_1 (12-1 momentum) | weak | 28d -0.058 | 28d +0.083 | -0.13 | Swings hard: +0.22 in Sep, -0.13 in Oct. Momentum is a regime bet, not a factor here. |
| pt_up (street upside) | +0.007 | +0.023 | -0.022 | -0.19 | Street upside paid in calm up months and failed in October. Do not rank on it in a sell-off. |
| ey_fwd (forward EPS yield) | weak | 28d +0.056 | 28d +0.025 | +0.20 | Value turned on in October. |
| fcf_margin | +0.042 | +0.052 | +0.022 | +0.17 | Quality: steady, strongest in October. |
| rvol_20 (realized volatility) | weak | about 0 | 28d -0.042 | -0.21 | Low volatility won in October. |
| r_1w (last week) | -0.012 | -0.013 | -0.010 | +0.18 | 28d IC -0.039 (STRONG reversal overall); October's bounce made last week's winners win. |

## 4. Cyclical patterns seen so far

- **Earnings season** (Apr-May, Jul-Aug, Oct-Nov): print reactions average +0.25% on the day with a 28-day
  post-print drift of +0.46% (t 2.5). Drift by print month: Apr +1.0, May -0.8, Jun -3.2, Jul +0.6, Aug +1.1,
  Sep +3.4 (the late-season stragglers in a down tape drifted most). See `earnings_playbook.md`.
- **Movers tails** (25 pack days, 9 Sep - 27 Oct, a down regime): punished 5D / week laggards kept falling
  (-5.3% / -4.7% over 7 days); 1M leaders gave back -6.9% over 28 days; 1M laggards bounced +0.9%. In an up
  regime this may flip (not measured yet: re-check when `mkt_regime` turns positive).
- **Momentum crashes at regime turns**: r_12_1 went from +0.22 (Sep) to -0.13 (Oct). Expect the same at the
  next turn up: last quarter's laggards with rising estimates lead the first weeks.

## 5. When the regime turns: what to change

Turn up (universe trailing 1M back above 0, breadth widening, December hike priced out, oil easing):
1. Re-run the lab with `--first-day` at the turn; expect `pt_up`, `bo`, and momentum to recover and the
   defensive tilt to fade.
2. Lift street-upside and breakout reads back up the reading order; keep p_dvc / p_recov (regime-proof).
3. Treat punished-tail BOUNCE calls as more credible; until then they fought a continuation drift.

Turn further down (December hike confirmed, oil higher, AI financing doubts):
1. Lean harder on p_def, revisions (eps_rev_3m) and last_surprise; avoid high realized volatility.
2. Do not buy 5D / week punished laggards for a bounce inside two weeks; wait for a 1M-laggard set-up.
3. Shorts: the short book rose +0.6% over 7 days in this sample; squeeze risk is high in bounces.

## 6. Open questions

- Up-regime behaviour of movers tails and pack lists (the pack history only covers the down months).
- Whether p_def's edge survives a rate-cut cycle (no such period in the sample).
- Europe (outside the US store) is untested.
