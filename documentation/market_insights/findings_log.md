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

## 2026-11-02: what paid going into the midterm week

- Signal lab (144 days): p_def 28d IC +0.106 (t_indep 3.1, STRONG), p_dvc +0.133 (t 2.7); last_surprise leads 1-7 days (t 3.1 / 2.9).
  Prints: B/B drift +1.0% over 28 days (t 4.4); beat sold 5%+ +1.29% (t 2.4); set-up sold into & previous print down holds +3.11% over
  28 days (n 594), sold into & previous up +1.38% (n 643), ran into & previous up -0.35% (n 894).
- Factor month (pathway, 7d IC this month): forward EPS yield +0.136, FCF margin +0.101, p_dvc +0.078; beta -0.164, volatility -0.145,
  street upside -0.108. The Book sits at the 89th percentile of beta and trails the market by 19.8% over a month (industry part -15.4).
- GMV walk-forward (28d, 13 days): value lens t 2.14; RERATE t 5.46 (92% positive days), VALUE_TURN t 5.35 (100%); participation t -1.95.
- Alpha model: 28d WEAK (IC 0.054, t 1.38, 4.4 independent) but now above its walk-forward baseline; 56d FAILING.
- Deterioration: dd 50+ | 0 +6.43% over 28 days (t 6.18, 18% odds of another -10%); dd 20-35 | 2 +0.84% (t 0.87).
- Play review (30 Oct calls vs 2 Nov, market +1.8%): the untaken NASDAQ:FSLR trim +5.4% excess, NASDAQ:BIDU exit +1.5%; NYSE:HIG ran 1.4%
  above its limit. A limit set under the mark on a low-beta name misses in a relief rally.
- Downside: selling NYSE:UNH (store beta 0.18) raised cash to 17% but left beta at 185% of NAV; today's plan takes it to 176%.


## 2026-11-02 (evening): winners anatomy - what the top 5% looked like before they ran

- `tv_scan_cli.py anatomy --pack PACK` (new; recipe `config/generic_utils/anatomy.json`) on pack `briefing_pack_20261102_1642_utc_5762ac21`
  (144 files, 29 Mar - 2 Nov, US $1B+, top / bottom 5% of each day's later return). Page `logs/canvases/20261102/1802_ide/alpha-hunt-20261102.html`.
- Winner mix: about half of every horizon's top-5% movers rode a top-quintile industry over the same window (28d 53% vs 19% base; losers 9%).
  Past industry momentum is not directional (ind_r_1m:hi lifts both tails about 1.4x). A print beat in the window lifts top-5% odds 2.5x at 7d.
- Raw winner lift is mostly volatility (atrp / rvol_20 legs lift both tails 2.5x); order up-patterns by asym_up = lift_up / lift_dn with an
  excess t, not by lift.
- Durable up patterns (positive in every entry month including October): rsi:lo & p_fwd:hi 28d +5.1% (t 3.2, hit 64%, Oct +1.9);
  beat-and-sold (since_print<=10 & last_surprise>0 & r_1w:lo) +2.2% (t 4.7, Oct +1.2); industry turn (ind_r_3m:lo & ind_last_surprise:hi)
  +3.5% (t 3.2, Oct -0.3); r_1m:lo & p_dvc:hi +3.2% (t 2.4, Oct +0.1).
- Hindsight shape of October's winners (r_1m:lo & pt_up:hi & dte<=28) lifts both tails about 2x: +3% a month Mar-Sep, -2.4% to -4.6% in
  October. r_3m:lo & pt_up:hi & eps_rev_3m>=0 was the most one-sided (lift up 2.8 / dn 0.7, 28d +5.3%) until October (-4.8%): regime.
- Down side is the robust result: p_bo:hi & p_cons_w:lo 28d -11.7% (hit 22%), p_bo:hi & p_fwd:lo -10.3%, r_1m:hi & p_fwd:lo negative every
  month (Oct -21.5%). Hold-out: down patterns mined before each window caught losers 2.4-2.6x; up patterns mined before October lagged in it.

## 2026-11-02 (night): beat bar - what a beat paid by positioning (5,360 lab prints, 30 Mar - 2 Nov)

- Base: beat rate 73%; a beat +1.27 pp on the reaction vs the market (hit 55%), a miss -2.31 pp (hit 37%); 45% of beats were sold.
  B/B +1.75, B/M -0.95 (an EPS beat with a revenue miss lost). 28-day hold from the pre-print close: beat +1.85, miss -2.40.
- Low bar pays: runup:T3 & r_3m:T1 & eps_rev_3m:T1 (bounce into the print after a 3-month slide, estimates cut) beat +4.9 pp, hit 83%,
  17% sold, lift t 4.2 (second half only: 2 early beats); runup:T1 & r_3m:T1 & eps_rev_3m:T1 beat +3.1 (t 2.35, both halves +), h28 +5.9.
- High bar: estimates raised (eps_rev_3m:T3) with r_3m T2-T3 sold about half of beats (beat -0.6 to -0.9 pp, t -2.1 to -2.6) and
  needed EPS +10% to +24%. Crowded misses (r_3m:T3) cost -5.5 to -8.2 pp, h28 to -13.
- Pointer cells (halves rule): beats paid more after a prior miss (last_surprise Q1 +1.7, t 3.6), target cut, high pt_up; less after
  EPS revisions up (eps_rev_3m Q4 -1.3, t -3.1), revenue revisions up, target raises.

## 2026-11-02 (late): recovery clock - how long drawdown states took to come back (51,371 weekly starts, 29 Mar - 2 Nov)

- New scan `tv_scan_cli.py recovery-clock` (Kaplan-Meier on the store's closes, censored starts count while seen; a touch, not a hold).
- Crowded winners in a drawdown kept falling: dd 20-35 | 12M 100+ ended 91 days later -11.7% (median, 200 names), excess -5.4 pp,
  path-low median -23%, a further -20% in 57% of starts; yet +13.5% was touched in 59% within 91 days (volatility both ways: sell them
  into the 50-day, do not add). dd 35-50 / 50+ | 12M 100+: -15.0% / -7.3% median.
- Not-crowded deep drawdowns drifted up: dd 35-50 / 50+ | 12M down +7.1% / +5.4% over 91 days (median), excess +7.8 / +6.2 pp;
  no-letter count cells (from July) +11.7 / +13.3 pp mean excess. One letter at 35-50%: -7.9% median, path low q10 -37%.
- Starts in a down market month (median name's trailing month down) paid +9% to +12% over 91 days at 10-50%+ drawdowns vs +1% to +2%
  in up months: averaging down pays in a washout, not on a calm day.
- Half-way back to the high inside 182 days: 55% at dd 20-35, 35% at dd 35-50, 21% at dd 50+ (dd-only cells).
- Caveats: a young, mostly rising store; the letter cells start in July so their 126 / 182-day odds stay empty; starts overlap (read names).

## 2026-11-03: peer value - is the discount to peers earned by growth and quality? (29 store days, 9 Jun - 3 Nov, US $2B+)

- New scan `tv_scan_cli.py peer-value`: per TradingView industry (sector when thin), the log forward P/E / EV/EBITDA / EV/sales fitted
  on z-scored growth and quality (ridge); `gap_pct` = fitted vs actual multiple. Median R2 0.49 (P/E, 74 fitted groups), 0.40
  (EV/EBITDA), 0.65 (EV/sales).
- The adjusted gap beat a plain peer discount: 28d IC 0.024 (t 0.86, hit 56%) vs 0.012 (t 0.30, 44%); 56d 0.043 (t 1.31, 74% of days
  positive) vs 0.044 (t 0.88, 61%); 7d 0.014 (t 1.12) vs 0.011 (t 0.57). 28d quintile excess: gap Q5 +1.86 / Q3 +0.41 / Q1 +0.90;
  plain discount Q5 +1.26 / Q1 +1.15 (flat). Direction only: no t above 2 yet.
- What industries pay for (P/E lens, % multiple per 1 sd): Semiconductors revenue growth +46, ROE -24, EPS revisions -34 (rising
  estimates lower the forward P/E before the price follows: the gap partly reads estimates outrunning price); Internet software revenue
  growth +52, margin -37; Packaged software growth +28; P&C insurance growth +8, margin -28.
- Book today: MU gap +90 (cyclical at peak margins, read `cycle` beside it), PGY +127, FSLR +123 (sits in Semiconductors: wrong peers),
  STRL +35, QCOM +31, RDDT +28 (still under its profile after -57% in 12 months), RMBS -10, GEV -14 (premium 33% vs 14% earned).
  Candidates: HIG +66, AMP +46, PDD +111 (sector peers), DINO -9 (its discount is earned).

## 2026-11-03 (16:12 UTC update): peer gaps move between two fits of one day; low-bar cells for this week's prints

- Peer value re-fitted on the afternoon store day (the 16:12 UTC focused run replaced the morning row): Semiconductors grew from 48 to
  55 fitted names and the P/E fit's weights changed sign (ROE -24 -> +4, operating margin -6 -> -29). Single-name gaps moved by up to
  40 points: NASDAQ:QCOM +31 -> +70, NASDAQ:RDDT +28 -> +63, NASDAQ:RMBS -10 -> -29, NASDAQ:WDC unfitted -> -27. Signs held for every
  Book line that had both. Rule: use the sign that agrees across fits, not the size; a pooled fit over the last few store days is the
  fix to test.
- Beat bar, W0 / W1: the best low-bar cell is run-up T3, 3-month tape T1, EPS revisions T1 (n 102 past prints): beats paid +5.1 on the
  day with 17% sold and held +6.4 over 28 days; misses held +7.3. Run-up T1 / tape T1 / revisions T1 (n 137): beats +3.2 on the day,
  28% sold, held +6.1; misses -1.6. High bar (revisions and tape T3): about half of beats sold and misses held -11.8 over 28 days.
- New `tv_scan_cli.py board` (PACK/board) joins all of it per name; the page's screens are the reading orders.

## 2026-11-03 (16:12 UTC): market sentiment, first measurement

- New `tv_scan_cli.py sentiment` (PACK/sentiment; also inside `run_market_timing_policy.py`): 35 market-level readings on the US $2B+
  floor, 145 store files (29 Mar - 3 Nov). None clears the arrow rule (|t| >= 2 on n_indep = days / h, both halves, today's lean, the
  quintile's lift) at 1 / 5 / 10 / 21 sessions: at 21 sessions the store holds about six independent windows. Closest: volume pace at 5
  sessions (IC +0.35, t 1.9, halves +0.11 / +0.23). Read percentiles and patterns as context until the store grows.
- Patterns (episodes are the sample): distribution clusters (5+ in 25 sessions, 6 episodes) were followed by -0.7 pp at 5 sessions and
  -0.9 pp at 21 vs all days; beats-sold stretches (8 episodes) -0.8 / -1.7; momentum unwinds (3 episodes) -1.9 / -2.0; the AI cluster
  trailing the market by 3 pp+ over five sessions (4 episodes) +2.0 at 5, -0.2 at 21. Live today: ai_lags and accumulation_cluster
  (8 accumulation vs 3 distribution days, 97th percentile) with the cap index at its weakest 21-session return in the store (1st percentile).

## 2026-11-04 (13:01 UTC premarket scan): the midterm sweep sorts groups; the Book leans the way October punished

- Premarket after the Democratic sweep (OUTSIDE, Reuters / CNBC 4 Nov), measured on 70 names with `premarket-book` and a policy recipe:
  data-center builders -3 to -11 (NYSE:FIX -10.6, NASDAQ:IESC -7.1, NASDAQ:STRL -7.0, NYSE:PWR -5.2), ERCOT / data-center power -4 to -6
  (NYSE:VST -6.4, NYSE:GEV -6.3, NASDAQ:TLN -4.8), neoclouds -4 to -7, defense -2 to -4; utility-scale solar +6 to +9 (NASDAQ:ARRY +8.8,
  NASDAQ:NXT +6.2, NASDAQ:FSLR +6.0), hospitals / Medicaid insurers +4 to +9, hyperscalers +1 to +4, homebuilders +4. NASDAQ:SEDG -14.5 on a
  Q4 guide under Q3: inside the policy winners, results still set the sign.
- Data on the policy names: the solar names with letters (ARRY revenue cut, ENPH ERS, RUN cash burn) sit in model deciles 1-4; only
  NASDAQ:NXT is decile 10 with no letter. NYSE:VST carries ERS and its cell (dd 20-35 | 3+) is -0.9 over 28 days, t -3.0. NASDAQ:STRL is
  price-only with the strongest cell in the store (dd 50+ | 0: +6.3, t 6.2). Hospitals: NYSE:UHS forward PE 7.9, peer gap +74, no letter.
- Factor month (signal lab, October): forward earnings yield and FCF yield led (28d IC +0.25 / +0.28), beta / ATR / realized volatility
  lagged (28d IC -0.27 / -0.28 / -0.29). November's first sessions reversed (1d IC of rsi, vs20, r_1m about -0.15). The Book sits at the
  92nd percentile of beta and the 82nd of realized volatility; 1M gap to the market -20.5 pp (industry -12.7, stock picking -7.8).
- Alpha model: 28d WEAK (OOS IC 0.055, t 1.43, beats its best baseline), 7d WEAK, 56d FAILING.
- Data gaps found: the holdings producer's FX call timed out (OMXCOP:NOVO_B valued in DKK as USD until a rerun); NASDAQ:GOOG is held but
  unmatched (the move-prediction scan carries only NASDAQ:GOOGL), so the pack's NAV leaves out about $6.3k.

## 2026-11-04 (23:03 UTC post-close scan): big-move odds on prints; size is measurable, side is not

- New print-night measures (recipe `tail_measures`, `generic_utils/print_night.py`): u10 / d10 = reaction vs the market at +10 / -10 pp
  or beyond (0/1 per print), b10 = +1 / -1 / 0 (up odds minus down odds), with Size pointers (own_abs = the name's mean |past reaction|,
  mcap, rev_yoy) measured on these only, so the rx / h7 / h28 counts are unchanged. On 5,699 lab prints (base u10 10%, d10 9%):
  realized volatility Q5 17% / 16% vs Q1 1% / 3%; own past move Q5 15% / 17%; small cap, fast revenue growth and high street upside
  raise both tails. Side tilts are small: previous print <= -10 vs the market u10 17% / d10 15% (mean reaction +1.3, 28-day hold +3.0,
  both halves); previous print >= +10 u10 12% / d10 16%; next-FY EPS revised up 3 months (top quintile) d10 12% vs u10 9%.
- Out of sample on the 179 after-hours reporters of 4 Nov, scored from the 17:03 pack: u10_hi >= 0.16 moved 6.2% on average (47% 5%+,
  16% 10%+ either way), 0.13-0.16 moved 2.7%, under 0.13 moved 1.2% (none 10%). The b10 up-tilt group (momentum: r_1m / rsi / vs50
  top quintile) had 21% of its names at -10 or worse: the side read failed while momentum reverses.
- NASDAQ:APP (+25.5% AH) and NASDAQ:SEZL (+28.2%) both fit "good print, punished print (-19 / -29), rebound": so did NYSE:HUBS (-9.3),
  NASDAQ:FSLY (-14.8), NYSE:BROS (-17.4). After a +10 reaction that follows a punished print the store's next 7 days averaged -1.75 and
  28 days -0.5 (38 prints); after any +10 reaction the 28-day drift is +1.4 mean / -0.2 median with the halves disagreeing. Prints that
  fell 10+ drifted +1.8 over 28 days (both halves positive).
- Lesson for the Book: a best-cell line into a print is a size decision. Cutting NASDAQ:APP to half instead of out would have kept about
  $950 of the +25% night with half the -10% tail.

## 2026-11-09 (16:24 UTC intraday scan): day 2 of the rally; band discipline vs a gap day

- Session: cap-weighted +2.35%, 79.8% of US $2B+ names up, up-volume 76.9%, 59.5% over the 50-day; 8 accumulation vs 3 distribution
  days in 25. The sentiment suite's live patterns both lagged afterwards in the store: follow-through (n 4) 5s -1.14 / 21s -2.40 pp vs
  all days; accumulation cluster (n 3) -0.55 / -1.22 pp. Only measured arrow: median gap down at 1 session. OUTSIDE: VIX 20.9, 10-year
  4.87%, December hike odds about 78% before the 10 Nov CPI.
- Play review of the 6 Nov judgment (6 Nov -> 9 Nov, market +1.59%): WAIT courses +2.86 vs the market (mean, n 38), WATCH (AI bounce)
  +6.74 (n 4), NEW (queue) -1.56 (n 13). The bands held price discipline but cost the strongest movers: NASDAQ:SANM +11.4% from its
  124-127 pre-print ticket, NASDAQ:AEIS +11.6%, NASDAQ:AMAT +10.1%, NASDAQ:GFS +6.5% all left their bands unfilled on a 2-day rally.
  NASDAQ:MNDY's after-print band (82.81-88.28) sat above the reaction (82.2): set post-print bands from the reaction, not before it.
- Bounce tier table shrank from 60 to 28 names in one session: the band-in-reach family (b) is the one a rally removes first.
- Week plays: the best pre-print cell (sold into, previous print sold off, revisions under the priced quintile) still +2.92 hold-through
  (t 4.2). NASDAQ:MNDY post-print cells: B/B +1.01 (t 4.4), reaction Q1 +1.43 (t 3.1), beat sold 5%+ +1.29 (t 2.4).
- Alpha model unchanged: 28d WEAK (OOS IC 0.054, t 1.42, beats its baseline), 7d WEAK, 1d and 56d FAILING.
- Renderer fix: preferred / unit ids with a slash suffix (AMEX:TXCP/PB) were flagged as bare tickers; `symbol_identity._QUALIFIED_TOKEN`
  now reads the suffix as part of the id.

## 2026-11-11 (17:21 UTC intraday scan): lane book - which kinds of trade paid, and when a discount pays

- New suite `tv_scan_cli.py lane-book` (recipe `config/generic_utils/lane_book.json`): 20 lanes plus a base row, each one SQL condition on
  the panel store, measured at 7 / 14 / 28 days (US $2B+, 29 Mar-11 Nov; 28-day labels to 14 Oct; 89 days for lanes that need 3-month
  revisions). Base: 28-day excess +0.6 (the zero line: excess over a median is skewed positive), odds of +10% 18.7%, of -10% 15.7%.
- Non-AI lanes with block t 2+: cheap to peers (forward earnings yield 2+ points over the industry), estimates up, under the 50-day, week up
  +4.8 (t 3.1, +10% 33%, -10% 7.9%); quality washed out (strong, RSI 35 or under) +2.7 (t 3.7, 8.1%); street deep value +4.6 (t 3.7,
  13.6%); quality repairing from depth +4.7 (t 2.2, 11.3%). The first two were positive in every entry month Jun-Oct, including October
  (base -2.4); the depth lanes lost in October (-1.9 / -3.1). Pre-print: low bar +4.0 (positive every month), build-up +2.9 (-10% 8.5%).
- Not edges: estimates up 5%+ with the month down (+0.7, t -0.3), beat-both gap-ups (+1.3, t -0.5), moving without a print (+1.3, second
  half -3.0). Avoid lanes held: any +7% day -0.8 (32% odds of -10%; October entries -18.8 / 64%), cash burner after a big move -2.1
  (40%), big mover the continuation score likes -2.1 (31%), momentum at the high -4.0 (AI -19.1 / 64%). Estimate cuts in a downtrend were
  not a reliable short (+0.8).
- AI cluster: the same quality-from-depth rule had 25.6% odds of -10% in AI vs 11.3% outside; the AI quality dip lane lost 20.9 in
  October entries with 69% odds of -10%. Only cheap-and-turning held inside the cluster (+2.9, t 2.6, -10% odds 20%).
- Premium vs discount has a clock: for strong businesses the cheapest fifth to peers beat the dearest by +5.6 pp over 28 days 8-21 days
  before a print (+10% odds 34% vs 18%), +4.4 at 0-7 days, +2.1 at 22-45 days, -0.8 in the two weeks after the print. With no print near
  the cheap fifth jumped 10% less often (-4.1 points). For weak businesses a discount mainly cut the left tail.
- Industry rotation did not persist: quadrant -> next 28 days Leading 0.0, Improving -0.05, Weakening +0.4, Lagging +0.8 (hit 48-53%).
- Correction: `since_print` is the close vs the last reaction close (%), not days since the print (`dsp`). The 11 Nov mover study's print
  split and its "beat-both gap-up drifted +0.9 to +1.4" rule, the anatomy B letter (`since_print<=10`) and the quiet_accumulation focus
  scan used it as days. Corrected: a +7% day on a print (dsp 0-2) +1.0 over 28 days (-10% odds 20%) vs no print -1.15 (34%); B letter on
  dsp<=10: 5,697 rows (was 27,689), +1.5 at 28 days, t 1.9. The outlook and the board now carry `dsp`.
- NVIDIA's fiscal Q3 print is official for 17 Nov after the close (NVIDIA IR, 28 Oct); the 18 Nov calendar estimate is marked disputed.

## 2026-11-12 (19:05 UTC intraday scan, 71% of the session): a rates day plus the NVIDIA CEO exit; the lane map holds on one more day of labels

- Tape: cap-weighted -0.12% but the median US $2B+ name -0.85%, 28.4% up, up-volume 21.9% (a narrow, rates-driven day). Store step since
  the 11 Nov scan: cap -0.22%, median -0.44%, AI cluster -0.47%, Book -1.2% (-$1,008). The AI cluster sits 11.6% under its high and 5.5%
  over its 5 Nov low (117.76). Sentiment: no measured arrow at 5-21 sessions; the accumulation-cluster pattern is live (after it -0.47 pp
  over 5 sessions, -1.13 pp over 21; 4 episodes).
- OUTSIDE: NVIDIA's CEO resigned (CFO interim; NASDAQ:NVDA opened 151.0, under the old 158.93 stage-C kill, then 164.65); 10-year 5.11%
  (+11 bp); a December hike about fully priced after the hot October CPI; claims 200k; oil up on Iran headlines; Anthropic IPO dates
  confirmed (prices 18 Nov, trades 19 Nov).
- Lane book, labels to 15 Oct (non-AI, 28 days vs base +0.66): cheap and turning +4.65 (t 2.96, +10% 33%, -10% 7.9%; months +11.1 /
  +8.1 / +2.5 / +1.7 / +3.1 Jun-Oct), street deep value +4.52 (t 3.47), washed-out quality +2.57 (t 2.67; every month positive), quality
  from depth +4.55 (t 2.15; October +0.6). AI split: turning up from depth -2.68 with 41.5% odds of -10%; high-continuation chase -10.3
  (55.5%). The edges shrank from June to October in every long lane.
- Today's live cheap-and-turning names outside AI are mostly energy (NYSE:CRGY, NYSE:PARR, NYSE:DK, NYSE:SM, NYSE:INSW), decile 9-10
  with rising estimates and negative store beta (-0.6 to -1.5): the lane and the Book's missing hedge (rates / oil) point the same way.
- Deterioration on the Book: price-only lines kept positive cells (NASDAQ:MU 35-50% no letter +4.86, t 2.91; NYSE:RDDT 50%+ no letter
  +5.05, t 6.18); NASDAQ:FSLR (one letter) -1.17 (t -1.08) is the only negative cell; NASDAQ:GOOG has two letters (one is the
  investment-mark artefact) and sits 12% over its bounce kill.
- Model and lab: alpha 28d WEAK but ahead of its baseline (recent IC +0.093), 7d DEGRADED, 1d / 56d FAILING; p_dvc leads at 7-56 days
  (28d IC +0.122, t_nw 5.8); GMV value lens IC +0.060 (t 2.38), VALUE_TURN t 5.16, momentum and participation negative.
- Play review (11 Nov calls vs the 12 Nov mark, market -0.44%): nothing in the day plan traded into its band on opens and closes;
  day-plan names beat the tape by +1.0 to +2.0 except NASDAQ:DLO (-1.0); NEW (queue) n 13 +0.12 mean / +0.77 median vs PASS -0.52 and
  WAIT -1.0.
- AI trigger 1 (the 11 Nov follow-through day) did not count: the store's volume pace was 0.79 vs 0.96. 1 of 4 triggers on 12 Nov.

## 2026-11-13 (16:43 UTC intraday scan, 34% of the session): beat-and-raise sold in AI hardware; the rest of the tape held

- Tape: cap-weighted -0.72% at the scan but the median name +0.06% and 52% up (up-volume 36%): the selling sat in the AI cluster
  (-2.0% on the step, 13.5% under its high, still over its 5 Nov low 117.8 at 121.4). NASDAQ:AMD -9.7% after +18.9% in five
  sessions: the AI high-continuation chase lane (-10.33 over 28 days, 55% odds of -10%) unwound on schedule.
- Last night's prints: NASDAQ:CSCO and NASDAQ:AMAT beat on both lines, raised / guided above and were sold 5%+ vs the market. Both
  sit in three measured post-print cells that point up (beat on both lines +1.00 over 28 days, t 4.39; reaction in the bottom
  fifth +1.45, t 3.19; beat sold 5%+ +1.32, t 2.47). NYSE:NU +11.2% and NASDAQ:LFTO +26.3% sit in the weaker beat-bought cell
  (+0.78, t 1.73). NASDAQ:DLO opened inside its post-print band and closed the scan over it (revenue actual not posted, mix unused).
- Lane book on one more day of labels (to 16 Oct): non-AI cheap and turning +4.62 (t 3.00, odds of -10% 7.7%), street deep value
  +4.42 (t 3.23), quality from depth +4.45 (t 2.14), washed-out quality +2.49 (t 2.34); the AI split holds only cheap and turning
  (+2.92, t 2.72). When-grid: strong names' cheapest fifth beat the dearest by +5.31 pp 8-21 days before a print.
- Pre-print cells for W1: NASDAQ:PDD is the only decile-10 name in the best cell ('sold into & previous print down' +2.76, t 4.11)
  with a low beat bar; its report date is not confirmed by the company (OUTSIDE), so the event ticket is Grade Low.
- Play review (12 Nov calls vs the 13 Nov scan, market median +0.06%): nothing on the day plan filled on opens and closes;
  NASDAQ:CSCO went through its old post-print band on the reaction day (-5.4%), NASDAQ:MU closed inside yesterday's band.
- OUTSIDE: 10-year 5.16% (highest since October), VIX over 20, October PPI +0.3% m/m (0.4% expected). AI triggers still 1 of 4.
