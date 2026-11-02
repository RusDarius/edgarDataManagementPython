# Findings log (newest first)

Each entry: date, data window, what was measured, the numbers with t and sample size, and what changed in
the playbooks. Source tables: `PACK/signal_lab/signal_lab.duckdb`, `PACK/alpha_model/alpha_model.duckdb`.

---

## 2026-10-30: the AI-capex sell-off, the states by regime, and what averaging down buys

Data: pack `briefing_pack_20261030_1604_utc_b0098e99` (intraday 11:59 ET), panel store 2026-03-29..10-30
(254,091 study rows), deterioration cells, the new recovery ladder (`tv_scan_cli.py recovery`).

- Tape: US $2B day -1.5%, 33% up; the universe's trailing month still +3.0 (up regime). Book equity -$7.0k (-5.1%):
  NYSE:RDDT -28.1%, NASDAQ:QCOM -11.2%, NASDAQ:RMBS -7.0%, NASDAQ:WDC -6.4%, NASDAQ:MU -5.4%. OUTSIDE: Nasdaq -2.5%,
  VIX 20.0, 10-year 4.95%, NASDAQ:INTC -29% on its guide.
- Factor tape (trailing 7-day IC): beta -0.133, street upside -0.113, volatility -0.109; forward EPS yield +0.122,
  FCF margin +0.100, p_dvc +0.098, closeness to the high +0.085. The Book: beta 85th percentile, street upside 82nd.
- Attribution: 1M gap -16.0% (-$14.4k) = industry -10.9% ($9.8k) + stock -5.1% ($4.6k); 3M -11.5% ($10.4k).
- States by market regime (28-day excess, drawdown x letter count x market): dd 50+ | 0 +6.9 up / +5.1 down
  (n 691 / 227; odds of -10% more 14% / 30%); dd 35-50 | 1 -0.3 up / -1.5 down (56d -0.4 / -5.8, odds of -10%
  more within 56d 37% / 43%); dd 20-35 | 2 +1.8 up / -1.2 down; dd 20-35 | 0 +1.6 / +2.3; crowded winner
  dd 20-35 | 0 | 12M 100+ -2.4 (n 1,267, q10 -24%, 34% odds of -10% more).
- Recovery ladder: at the bounce band a $2.5k add lowers a line's average 2-5% (NASDAQ:MU 1.8%, NYSE:RDDT 12.5%).
  After it, break-even sits +7.6% (NASDAQ:RMBS), +9.9% (NASDAQ:STRL), +12.0% (NASDAQ:WDC) above the buy, under
  their 50-days; NYSE:RDDT stays +46% even after $5k, NASDAQ:FSLR +17.7%, OMXCOP:NOVO_B +16.2%.
- Play review (29 Oct calls): the two DERISK_INTO_PRINT were right and not taken (NYSE:RDDT -28.1%, NASDAQ:FSLR
  -3.1% next session); NASDAQ:QCOM HOLD closed under its kill (-8.5%).

Changed: the Daily reads the recovery ladder before any add (`recovery`, refresh suite after deterioration);
`deterioration_playbook.md` rule 5 now names the average-under-the-50-day test.

## 2026-10-29 (print night): every pre-print field against what prints did next

New study `tv_scan_cli.py print-night` (PACK/print_night) on pack `briefing_pack_20261029_1411_utc_fa5d9ba1`: 23 pre-print
pointers and 5 print pointers, quintiles on 5,204 lab prints (2026-03-30..10-29), lift vs every other print, Welch t,
halves split at the median print date, hit-rate rule. 70 of 407 cells carry an arrow. Page: the outlook's Tonight tabs
(`logs/canvases/20261029/1423_full/outlook-20261029.html`).

- Reaction session vs the market, before the print: 5 arrows, all middle quintiles, |lift| 0.61-0.92 pp (t 2.1-2.6).
  Nothing known before the print sorts the reaction (base hit 49.9%, q10 -9.1, q90 +10.0).
- Hold 28d from the pre-print close: up r_1m Q5 +2.87 (t 4.47, n 977), p_dvc Q5 +2.62 (3.66, 439), peer_rx Q5 +1.98
  (2.85, 746), rev_rev_3m Q1 +1.98 (2.55, 431), vs50 Q5 +1.91 (3.08), rsi Q5 +1.81 (3.03), runup Q1 +1.47 (2.59, 889);
  down rvol_20 Q1 -2.13 (-5.33, 694), peer_rx Q2 -2.18 (-3.88), eps_rev_3m Q5 -1.84 (-2.29, 379), runup Q3 -1.54 (-3.19).
  AMC slot +1.02 vs BMO (t 2.3).
- Hold 7d: up runup Q1 +1.37 (3.62, 933), p_cons_w Q1 +1.20 (3.16), vs50 Q1 +1.17 (3.06); down rsi Q4 -1.05 (-3.02),
  eps_q_step Q5 -1.04 (-2.71).
- After the print: B/B reaction +3.43 (13.66, 3,038), M/M -5.09 (-11.18, 478); EPS surprise Q5 +4.22 (11.12);
  after hours < -3.5% (AMC) reacted -9.97 but closed +1.68 above the after-hours read (2.0, n 50); reaction Q1
  (< -5.0) drift 28d +1.14 (2.32, 916).
- Tonight (29 Oct AMC + 30 Oct BMO, 146 names, 145 pre-print): 23 lean up on the 28-day hold (3+ up, at most 1 down,
  2+ families), 19 on the 7-day (2+ up, none down), 11 on both, 18 lean down (4+ down, at most 1 up). Book: NYSE:RDDT
  h28 5 up / 2 down, h7 4 / 1; NASDAQ:FSLR h28 5 up / 0 down from five families.
- Playbook: earnings_playbook "Every field, one at a time" and How to use step 0.

---

## 2026-10-29 (later): pathway - the Book's tilt and attribution

New study `tv_scan_cli.py pathway` (PACK/pathway) on the same pack; page `logs/canvases/20261029/1423_full/pathway-20261029.html`.

- Attribution (US lines, 71% of the Book, today's weights): 1M -9.41 pp vs the market (+3.44): industry -7.48
  (-$7,224), stock -1.92 (-$1,856). 3M -5.00: industry -4.35 (-$4,198), stock -0.65 (-$625).
- Tilt (NAV-weighted percentile; tilt_month = gap x October 7d IC): paid p_dvc 0.80 (+0.030), fcf_margin 0.74
  (+0.030), p_recov 0.75 (+0.024), ey_fwd 0.64 (+0.022), p_qvc 0.77 (+0.020), p_def 0.79 (+0.010); cost beta 0.83
  (-0.062), pt_up 0.78 (-0.038), rvol_20 0.69 (-0.029), hi_prox 0.27 (-0.021). Trailing tape (20 labelled days to
  22 Oct): beta -0.135 (30% positive days), pt_up -0.130 (10%), rvol_20 -0.111 (25%), p_dvc +0.114 (85%), fcf_margin
  +0.112 (90%), ey_fwd +0.132 (90%).
- 1-day IC, beta: +0.43 (26 Oct), +0.25 (27 Oct), -0.14 (28 Oct): the chip bounce did not hold.
- Regime series: above the 200-day 45% (14 Oct) -> 57% (29 Oct); the market's month -3.2 -> +3.4; median distance
  to the 52-week high -19.9% -> -16.5%.
- Rebalance deltas on beta percentile: $5,000 of NYSE:EOG -0.041, NYSE:HIG -0.035, NYSE:AMP -0.018; a third of
  NASDAQ:MU sold -0.009.
- Playbook: new `investing_pathway.md` (diagnosis, stages, triggers).

---

## 2026-10-29: deterioration beyond price, the factor month, and the Book's two-month drought

Pack `briefing_pack_20261029_1411_utc_fa5d9ba1` (10:06 ET scan, 142 store days 2026-03-29..10-29); new study
`tv_scan_cli.py deterioration` (PACK/deterioration, 252,326 name-days, US $2B+, 384 cells); pages
`logs/canvases/20261029/1423_full/`.

- Drawdown x letters beyond price (E, R, C, S; from July, about 3 independent 28-day periods): no letter at
  20-35 / 35-50 / 50+ under the high +1.89 / +6.49 / +6.51 over 28 days (hit 0.56 / 0.65 / 0.65; t_nw 6.2 /
  5.1 / 4.9; non-overlapping t 2.2 / 2.9 / 6.2 on 3 periods, 1.3 / 1.6 / 3.5 from a mid-June start); 3+ letters -0.89 / -0.21 / -0.26 (56d -1.79 / +0.47 / -2.02). With the
  market's trailing month down, 3+ letters -4.18 / -4.64 / -6.52.
- C (negative FCF) is the most consistent single letter (full sample): 28d on vs off -1.22 vs +1.35 (dd 20-35),
  -0.82 vs +2.27 (35-50), +0.21 vs +3.25 (50+). 200-day trend and street target cuts read the wrong way round in
  deep drawdowns: they follow the price and are now splits, not letters.
- Crowded winners (12M +100%): dd 20-35 with no letter -2.15 over 28 days (hit 0.42, q10 -23.6, 34% odds of
  -10% more). Half the gap to the high regained within 56 days: 38% (dd 0-10), 16% (20-35), 7% (35-50), 2% (50+).
- Factor month, 7-day IC (signal lab): October beta -0.185, rvol_20 -0.157, pt_up -0.137, r_12m -0.091,
  fcf_margin +0.127, p_dvc +0.100, hi_prox +0.092. Beta, volatility and momentum also lost in July and August;
  September was the exception (r_12_1 +0.22). The Book is top 15% on p_def / p_dvc and top 20% on volatility.
- Alpha model: 28d WEAK (OOS IC +0.054, t_indep 1.34, 4.3 independent; does not beat its best baseline); 7d
  WEAK; 1d and 56d FAILING. 28d top decile realized +0.76% (hit 0.53).
- Play review (28 -> 29 Oct, one session, tape +1.0%): week list -1.97 vs the tape (19% up), NASDAQ:GOOG -13.6;
  PASS calls NYSE:NOW (-24) and NASDAQ:SFM (-20) avoided; no band above the price filled. The holdings file
  showed the planned derisks not taken and $2,000 each added to NASDAQ:GOOG and NASDAQ:STRL before the prints.
- OUTSIDE: Fed held 3.75-4.00% on 28 Oct (10-2, two hike dissents); Alphabet 2027 capex above $300B.
- Playbooks changed: new `deterioration_playbook.md`; `regime_playbook.md` section 2 update (29 Oct).

---

## 2026-10-28 (post-close): the reaction night, a stale revenue field, and the kill on the reaction close

Pack `briefing_pack_20261028_2138_utc_01ecf61f` on the 17:24 ET scan (`focused_intraday_20261028_2124_utc_8c77d0b9`,
after hours); page `logs/canvases/20261028/2141_update/post-earn-20261028.html`. 4,994 prints, US $1B+,
2026-04 to 2026-10. New studies in `signal_lab.print_cell_studies`: `set-up x reaction`, `gap x session`,
`opening gap quintile (reaction day)`, `after-hours read quintile (AMC)`; tails (`_q10` / `_q90`) on every cell.

### Data defect fixed (it changed today's calls)

- On print day TradingView rolls `revenue_forecast_fq` to the new quarter before it posts `total_revenue_fq`
  (VRT / GEV still unposted at 17:24 ET after a BMO print). `actual / forecast` then set last quarter's revenue
  against this quarter's estimate. `earnings_week.revenue_posted` now compares with the last file before the
  print; reactions take the surprise from the first file that posts it; the live row falls back to
  TradingView's own `revenue_surprise_percent_fq` (now in the focused field catalog).
- Remeasured: B/M went from n 1,297 (react_fx +1.12) to n 591 (react_fx -0.97, drift28 -0.32, t -0.69);
  B/B n 2,872, drift28 +1.00 (t 4.35); M/M react_fx -4.34, drift28 +0.01. About 700 "B/M" prints were B/B or
  revenue-pending.
- Today: NYSE:RNR was B/M (revenue -11% posted, -6.3% TradingView), not "+55%": NEW -> WAIT.
  NASDAQ:FFIV was B/B (+3.8%), not B/M: WAIT -> NEW at its band. NYSE:GRMN is B/M by 0.13%.

### The reaction night

- No pre-print set-up beats the market on the reaction day more than 53% of the time (best cell 52.9%,
  worst 48.4%); tails q10 -7.6% to -9.4%, q90 +8.6% to +10.4%. The best cell's +3.11% is a 28-day hold.
- Set-up x reaction, drift from the reaction close (28d): sold into + previous down | sold 5%+ +3.31% (t 2.21,
  n 94), | inside +3.63% (t 4.92, n 330), | bought 5%+ +1.06% (t 0.87). Sold into + previous up | sold 5%+
  +2.77% (t 2.35). Ran into + previous down | sold 5%+ +2.36% (t 1.99). Ran into + previous up | sold 5%+
  -0.17% (t -0.15): the only set-up where a sell-off does not rebound.
- Gap x session: gapped down 5%+ and sold after the open (n 402, react_fx -14.9%) drifted +2.39% (t 3.01);
  bought after the open +0.63% (t 0.93). Gap bottom quintile: open-to-close +0.48% (t 2.23).
- After-hours read (AMC, post-close scans only, n 159): below -3.7% the next close came back +2.24 pp
  (t 2.38, n 31); above +4.0% it held (-0.36 pp). Thin.

### Tonight (OUTSIDE where tagged)

- 70 US $2B+ AMC prints, mean after hours -0.69%. Software sold: NASDAQ:FTNT -25.0% (EPS beat), NYSE:NOW
  -15.2%, NASDAQ:GOOG -9.4%, NASDAQ:TENB -6.6%, NYSE:TYL -4.7%; NASDAQ:MSFT +2.6%, NASDAQ:KLAC +2.9%.
  Best set-up names: 5 up, 5 down.
- NASDAQ:GOOG (OUTSIDE): revenue $127.6B in line, Cloud +103%, capex $51.4B, FCF -$9.1B, EPS $2.12 with a
  $0.55 equity-stake mark; capex guide $195-205B unchanged. NASDAQ:STRL: EPS +22.3%, revenue pending.

### Changed in the playbooks

- earnings_playbook: B/M / B/B tables remeasured; new section "The reaction night"; a hold-through's kill
  no longer fires on the reaction close or the after-hours print in the three set-ups whose sell-offs drift
  (re-armed at the reaction session's low from the second close, unless thesis_kill); size into a print by
  the cell's q10 (`rx_tail_usd`); decide at the cash close, not in the premarket.
- daily-update: `update.md` flags after-hours moves on tonight's prints (`after_hours` in daily_refresh.json).

---

## 2026-10-28: first scored day (play-review, alpha live track), regime turn signal

Pack `briefing_pack_20261028_1721_utc_6c810f87` (17:10 UTC scan); page
`logs/canvases/20261028/1733_full/week-plays-20261028.html`. New generic: `tv_scan_cli.py play-review`.

- **Yesterday's list, one session (27 Oct 17:08 -> 28 Oct 17:10 UTC, market +0.23%):** the 19 week-plays
  names +0.97% vs the tape (median +0.88%, 74% up). BUY_PRE NASDAQ:STRL +4.0%, NASDAQ:GOOG +0.8%; WAIT
  names that printed ran (NYSE:EVR +11.9% B/B, NYSE:VRT +13.5% B/M); PASS NASDAQ:FFIV -20.1% (B/M guide
  cut); DERISK_INTO_PRINT -1.2% (n 2); STAND_ASIDE -1.1% (n 6); WATCH +0.67% (n 108); PASS +0.09% (n 75).
- **Levels:** 17 of 19 never traded into the published bounce band (4-8% under the mark); the two that
  did (NYSE:XYL, NASDAQ:LAUR) fell -6.7% / -4.7% vs the tape. Changed in `earnings_playbook.md`: pre-print
  limits go between the 7d q20 and the band top; post-print dips keep the full band, filled on a close when
  the day class is CONTINUE_DOWN.
- **Alpha model, first live label:** 1d IC of the 27 Oct forecast -0.114 (n 2,227; decile 10 -0.12%,
  decile 1 +0.75%), a high-volatility-led day. Consistent with the 1d FAILING state (OOS IC -0.001).
  7d labels arrive 4 Nov, 28d 24 Nov. States unchanged: 7d WEAK (+0.023), 28d WEAK (+0.054, recent +0.171).
- **Signal lab (141 days):** no ranking changes. p_def 28d +0.111 (t_indep 3.4, STRONG), p_dvc +0.134;
  B/B drift +1.13% (t 4.25, n 2,235); sold into + previous print down +3.11% (n 547).
- **GMV walk-forward (12 days, 28d):** RERATE IC +0.083 (t 5.4), VALUE_TURN +0.049 (t 4.9); momentum lens
  -0.009, participation -0.021. Same tilt as the lab.
- **Regime:** `mkt_regime` positive since 23 Oct (+1.39 on 28 Oct); see `regime_playbook.md` section 2
  update. The month read (alpha `month_ind`) leads with shipping, insurers, oil / refiners, investment
  managers; utilities moved to the bottom.
- **Under-appreciated prints found (B/B, small reaction, decile 10):** NYSE:RNR (-3.1%), NASDAQ:ACGL,
  NASDAQ:UMBF, NYSE:STNG, NYSE:EQT; beat sold 5%+: NYSE:GRMN (-5.4%, decile 9). Score after 28 days.
- **Check later:** NYSE:RNR / NYSE:AMP (today's NEW / event) and NYSE:GRMN (queue) vs the tape on 4 Nov and
  25 Nov; whether band-top limits fill more often than band middles (`play-review` min_state over a week).

---

## 2026-10-27 (week list): this week's plays placed in the measured cells

`tv_scan_cli.py week-plays` (new generic join, PACK/week_plays) on pack `briefing_pack_20261027_1712_utc_1e675dc8`;
page `logs/canvases/20261027/2121_ide/week-plays-20261027.html`. Score it after the season (print_cells by cell, alpha
`live_track`).

- Best pre-print cell (sold into + previous print down) holds 85 unprinted $2B+ names this week; 13 of them with
  revisions under +6.6% and alpha 28d decile 8+. Reading order used: NASDAQ:STRL, NASDAQ:GOOG (events), NYSE:EVR
  (the only one also in the bottom revision quintile, +1.96%, t 2.7), NYSE:GEV, NYSE:VRT; NASDAQ:SNDK blocked by
  memory concentration (swap for NASDAQ:WDC only).
- Post-print candidates: NYSE:AIT (B/B, beat sold 5%+: +1.26%, t 2.3), NYSE:XYL (miss sold 5%+: +1.64%, t 2.0;
  reaction Q1 +1.37%, t 3.0), NYSE:AWI (second tier). NYSE:BA passed: in the sold-off cell but alpha decile 1.
- Book in cells: NYSE:RDDT sold into + previous down (+3.11%), NASDAQ:FSLR sold into + previous up (+1.33%, t 2.1),
  NASDAQ:QCOM and OMXCOP:NOVO_B ran into + previous down (+1.10%, t 2.1), NASDAQ:PGY ran into + previous up
  (-0.35%, t -0.6: the weakest cell). NYSE:UNH is the weakest alpha read in the book (decile 4) and the funding line.
- Note for next time: run-up Q1 (< -3.8%) alone held +1.74% (t 3.4) and street upside Q5 (> +38%) +1.42% (t 2.4)
  before the print, although street upside fails as a cross-sectional factor in October. Check whether the two
  overlap with the set-up cell before using them as separate support.

---

## 2026-10-27 (17:08 UTC scan): first Daily run on the evidence

Applied in `logs/canvases/20261027/judgment.json` (run `1714_full`); score it in two to four weeks.

- Event slots moved from NYSE:PWR / NASDAQ:SIMO (previous print up, PWR model decile 3) to NASDAQ:STRL and
  NASDAQ:GOOG (sold into + previous print down, deciles 9 / 10), limits inside the buy-in-red bands only.
- NASDAQ:SNDK and NYSE:VRT fit the best cell but wait (memory concentration; above band before a BMO print).
- NYSE:SCCO short stood aside (model decile 10; the pack short book rose in the lab).
- 1M BOUNCE crypto miners passed (model bottom decile); day-punished B/B prints (NYSE:AIT) moved to WATCH as
  post-print recovery candidates.
- Check later: did the sold-into + previous-print-down names beat the others this week (print_cells after
  the season), and did the model's decile 10 Book names (NASDAQ:MU, NASDAQ:WDC, NASDAQ:FSLR, NYSE:RDDT)
  outperform NYSE:UNH (decile 4) over 28 days (alpha `live_track`).

---

## 2026-10-27: first signal lab and alpha model run

Window: 140 all-fields day files 2026-03-29 to 2026-10-27, US $1B+ (313,693 name-days); 28 briefing packs
8 Sep to 27 Oct; 4,840 measured prints. 390 feature x horizon tests (about 18 clear |t| 2 by chance):
5 STRONG, 14 CONSISTENT.

### Features (rank IC vs later excess return)

| Feature | 1d | 7d | 14d | 28d | 56d | Note |
|---|---|---|---|---|---|---|
| p_def (defensive_fortress_v2) | +0.028 C | +0.060 S (t_indep 3.0) | +0.080 S | +0.111 S (3.4) | +0.159 S | from 2026-06-25; up and down markets |
| p_dvc (durable_value_compounder_v1) | +0.041 C | +0.088 C (2.9) | +0.117 C | +0.134 C | +0.179 C | |
| p_recov (value_recovery_v3) | +0.035 C | +0.074 C (2.4) | +0.096 C | +0.104 | +0.140 | |
| p_fwd (forward_edge_active_v2) | | +0.043 | +0.051 | +0.063 | +0.127 | |
| last_surprise (EPS surprise, last quarter) | +0.012 S (3.3) | +0.027 C (2.9) | +0.034 C (2.5) | +0.043 | | stronger in down markets |
| fcf_margin | | +0.042 | +0.056 C | +0.065 | +0.094 | |
| p_bo (breakout_long_v1) | | -0.010 | | -0.025 | | negative in up markets |
| p_conv (conviction_score) | | +0.000 | | +0.004 | | no lead |
| p_cons_w (consensus weeks) | | +0.015 | | +0.034 | | weak |
| r_1w | | -0.012 | | -0.039 | | 28d reversal |
| since_print | | -0.032 | -0.040 C | -0.053 | | ran since the print -> gives back |

S = STRONG, C = CONSISTENT. Monthly 7d ICs and regime splits: `regime_playbook.md` section 3.

### Prints (`earnings_playbook.md` has the full tables)

- B/B drift +1.13% / 28d (t 4.3, n 2,183); B/M +0.18%; M/B +0.27%; M/M +0.09%.
- Sold into + previous print down: hold-through +3.11% (t 4.5, n 517). Ran into + previous print up: -0.35%.
- Revisions top quintile before the print: hold -0.51% (priced). Old BUY_PRE set-up: no edge (+0.94 vs +0.90).
- Reaction < -5.1% vs market: drift +1.37% (t 3.0); beat and sold 5%+: +1.26% (t 2.3).

### Pack lists (excess vs the pack's US $2B tape, 7 days, 21 pack days)

- trim_exit -2.2% (t -3.9): the cuts held. pass -0.9%, short_wait -0.2%: right direction.
- add -3.3% (18 obs), book -0.8%, hold_no_add -1.5%: held and added names lagged.
- short_15 +0.6% (shorts rose), earnings_down -0.75% (worked), earnings_up +0.4%, radar_25 +0.2%, radar_50 -0.5%.
- Movers tails (25 days): 5D laggards -5.3% (t -2.7), week laggards -4.7% (t -2.8) over 7d; 1M leaders
  -6.9% over 28d (t -2.6); 1M laggards +0.9% over 28d (t 2.5). Class history (BOUNCE / CONTINUE_DOWN): 3 days.

### Alpha model (walk-forward, out of sample)

- 1d WEAK (IC +0.002), 7d WEAK (+0.025, t_indep 1.2), 28d WEAK (+0.054, t_indep 1.3; about +0.13 since
  August with a +5% top-bottom decile spread), 56d FAILING (one independent period).
- Ridge (all features together) lost to the best single feature at every horizon; IC-weighted with
  select_t 2 and half-life 60 is the default. 28d deciles: bottom -1.5%, top +0.76% (sharper on laggards).
- Weights in use: p_dvc, p_def, p_qvc, p_asym, fcf_margin / fcf_yield, ey_fwd.

### Changed in the playbooks

- Retired the earnings-week BUY_PRE rule "every past print up"; replaced by "sold into + previous print down,
  revisions not in the top quintile".
- Added: lead the reading order with the value / quality profiles, not `bo`, in the current regime.
- Added: punished 5D / week tails are not bounce trades in a down regime.

### Regime context (OUTSIDE, 2026-10-27)

Fed funds 3.75-4.00% after a September hike; December hike about 82% priced, October about 11% (CME FedWatch
via Reuters / CNBC); September CPI 3.7%; Brent about $110; Anthropic IPO filing and a $63B Broadcom financing
revived the AI trade; Philadelphia semis +2.8% on 26 Oct.

## 2026-10-30 (3:20pm ET): market beta, not the paper loss, is the Book's downside

- `tv_scan_cli.py downside` on pack `briefing_pack_20261030_1921_utc_aba00e04` (store 29 Mar - 30 Oct, 143 files). The Book carries 185% of
  NAV in beta-weighted exposure to the cap-weighted US $2B+ floor (8 of 14 lines above beta 2; NASDAQ:MU alone 22% of the beta dollars).
  About -$2.6k per -1% in the market; a 10% S&P correction from the record (about -7% from the mark) is about -$18.5k (-13% NAV).
- In the AI-cluster leg of 6-30 Oct the Book fell 2.5x the market, about 1.35x what linear beta implied: cluster tails run past beta.
- Every line sliding to its bounce kill = -$10.3k, the same as the Book's store 28-day q05 (-$10.1k). The store holds no 10% index fall
  (cap-weighted max drawdown -4.6%): its tails are a floor for the bad case.
- Plans change beta more than the trims suggest: the morning plan's two high-beta adds (beta 3+) outweighed the trims (185% -> 188%);
  queuing them gave 177%. Read `spec.plans_after` before shipping adds in a sell-off.

