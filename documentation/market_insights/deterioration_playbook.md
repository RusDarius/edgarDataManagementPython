# Deterioration playbook (drawdowns: hold, cut, or double down)

How to read a held line that has fallen: is it only price, or is the business deteriorating too, and what did
that state do next? Numbers: `tv_scan_cli.py deterioration` on pack `briefing_pack_20261029_1411_utc_fa5d9ba1`
(panel store 2026-03-29 to 2026-10-29, US $2B+, 252k name-days; excess = return minus that day's universe
median, winsorized). Page: `logs/canvases/20261029/1423_full/deterioration-20261029.html`.

## 1. The state

| Read | Field | Rule (config/generic_utils/deterioration.json) |
|---|---|---|
| Drawdown | `dd` from `hi_prox` (close vs the 52-week high) | dd 0-10 / 10-20 / 20-35 / 35-50 / 50+ |
| E | `eps_rev_3m` | next-FY EPS estimate cut by more than 1% over 3 months |
| R | `rev_rev_3m` | next-FY revenue estimate cut by more than 1% over 3 months |
| C | `fcf_margin` | free cash flow negative (ttm) |
| S | `last_surprise` | last reported EPS under its estimate |
| Splits (not letters) | `r_12m`, `mkt_regime`, `vs200`, `pt_rev_1m`, `r_1m` | 12-month run, the market's trailing month, the 200-day trend, street target revisions, 1-month direction |

Trend and target cuts are splits because they follow the price: in the 50%+ bucket, names under the 200-day
did better (+2.1) than those above it (-2.0), and names whose targets were cut did better (+2.7) than those
whose targets held (-0.3). Counting them as deterioration double-counts the drawdown.

## 2. What followed (28-day excess unless marked; t on non-overlapping days)

| State | n (name-days) | 28d mean | hit | q10 | further -10% | 56d mean | half the gap in 56d |
|---|---|---|---|---|---|---|---|
| dd 20-35, no letter | 15,831 | +1.89 | 0.56 | -12.4 | 18% | +2.91 | 12% |
| dd 20-35, 3+ letters | 1,695 | -0.89 | 0.46 | -14.3 | 24% | -1.79 | 8% |
| dd 35-50, no letter | 5,903 | +6.49 | 0.65 | -11.5 | 17% | +8.48 | 9% |
| dd 35-50, 3+ letters | 1,597 | -0.21 | 0.45 | -13.0 | 27% | +0.47 | 3% |
| dd 50+, no letter | 1,295 | +6.51 | 0.65 | -13.2 | 18% | +9.05 | 5% |
| dd 50+, 3+ letters | 1,358 | -0.26 | 0.45 | -16.3 | 32% | -2.02 | 1% |

Letter counts need every letter known, so these cells start in July: about three independent 28-day periods.
The non-overlapping t of the no-letter cells is 2.2 / 2.9 / 6.2 (dd 20-35 / 35-50 / 50+) from the store's first
day and 1.3 / 1.6 / 3.5 from mid-June: with three periods it swings with the start day. Direction, not proof.

By market regime (`mkt`, universe median trailing month at the scan): with 3+ letters, mkt down -4.2 (dd 20-35),
-4.6 (dd 35-50), -6.5 (dd 50+) with 37-52% odds of a further -10%; mkt up +0.2 / +1.9 / +2.0. No letter stayed
positive in both (+1.6 to +6.9).

One letter at a time (full sample; C has the longest history): C on vs off, 28 days: dd 20-35 -1.22 vs +1.35
(off t 2.9); dd 35-50 -0.82 vs +2.27; dd 50+ +0.21 vs +3.25; 56 days -1.9 to -2.9 on vs +2.5 to +6.1 off, with
39-47% odds of a further -10% when on. E on vs off (from July): dd 35-50 -0.02 vs +4.52, dd 50+ +2.29 vs +5.07.
S (last miss) vs beat: dd 35-50 -1.08 vs +2.28.

Crowded winners (12-month +100% or more): dd 20-35 with no letter -2.15 (hit 0.42, q10 -23.6, 34% odds of
-10% more, 56d -0.63); with one letter -6.31 (50% odds over 56 days). At 35-50% under the high with no letter
they rebounded (+9.7). The unwind of a crowded winner runs further than a plain drawdown before it turns.

Depth alone: the left tail fattens with depth (q10 -11.3 at dd 0-10, -20.7 at dd 50+) and half the gap is
rarely regained inside two months (38% at dd 0-10, 16% at 20-35, 7% at 35-50, 2% at 50+).

## 3. How to use

1. A drawdown with no letter is price: hold it. Cutting it has, on this data, sold into the states that
   recovered (+1.9 to +6.5 over 28 days against +0.8 for the alpha model's top decile).
2. A new letter on a held line is the review trigger, not the price. Two or more letters plus a close under
   the bounce kill is a cut (a prior ADD / NEW still needs thesis_kill).
3. In a down month the letters cost most (-4 to -6.5 over 28 days): that is when the cut rule must be run daily.
4. Do not add to a crowded winner 10-35% under its high, even with clean fundamentals.
5. A double-down needs a no-letter state, a print cell that drifts (week_plays), a buy-in-red band and a kill;
   never on the reaction day (earnings_playbook rule 4). Read the recovery ladder first
   (`tv_scan_cli.py recovery --pack PACK --line`, PACK/recovery_ladder): add only where the new break-even
   (`be_vs_sma50_pct` < 0) sits under the 50-day. On 30 Oct a $2.5k add at the band cut most averages by only 2-5%;
   where the gap to cost is large (NYSE:RDDT +46% after $5k) an add buys exposure, not recovery.
6. Recovery is slow: hold for the drift, not for the old high. Recovery levels in order: the 50-day
   (`first_target`), half-way (`half_px`), the high.
7. Size the cluster, not the line: tails of correlated lines arrive on the same day.

## 4. The Book on 29 Oct 2026 (worked example)

The Book had 44% of NAV in one AI-capex trade (memory, chips, power, build-out, a hyperscaler). October paid
low beta (7-day IC -0.19) and low volatility (-0.16) and punished street upside (-0.14) and 12-month momentum
(-0.09); the Book was top 15% on quality (p_def / p_dvc) and top 20% on volatility. Eight of twelve US lines
had no letter (price-only damage: NASDAQ:STRL, NASDAQ:WDC, NYSE:RDDT, NASDAQ:RMBS, NASDAQ:MU, NYSE:GEV,
NASDAQ:PGY, NYSE:UNH); letters on NASDAQ:FSLR (E plus the revenue cut), NASDAQ:QCOM (E, S), NASDAQ:BIDU (E, S,
FCF unknown; EPS estimate -22%), NASDAQ:GOOG (S, with C likely as capex passes $300B in 2027). Calls:
exit NASDAQ:BIDU, derisk the two prints, no add to NASDAQ:MU (crowded winner), add to NASDAQ:WDC only in its
band, NASDAQ:GOOG only after the second close, new money into low-volatility quality (NYSE:AMP).

## 5. Open questions

- Up-month vs down-month cells have one or two independent periods each: re-measure after the next down month.
- Margin and growth changes (`opm_chg_q`, `rev_yoy_chg_q`) are live-only; add them as panel features to test them.
- Europe and ETFs are outside the US store (OMXCOP:NOVO_B, XETR:VUAA have no cell).
