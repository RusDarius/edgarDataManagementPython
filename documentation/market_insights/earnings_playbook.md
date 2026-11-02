# Earnings playbook (what prints did, 2026-04 to 2026-10)

Source: signal lab `print_events` / `print_cells`, 2026-10-27: 4,840 prints on the US $1B floor with a
measured reaction; the surprise-mix rows and "The reaction night" are remeasured 2026-10-28 post-close
(4,994 prints) after the stale-revenue fix (`earnings_week.revenue_posted`). `react_fx` = reaction minus the universe median that day; `drift_fx_<h>` runs from the
reaction close; `hold_fx_<h>` from the close before the print (the pre-print entry). t treats prints as
independent (they cluster in season, so t runs optimistic). Columns live on every tape row as the print
pointers (`last_surprise`, `rev_surprise`, `surprise_mix`, `dsp`, `react_last`, `since_print`...).

## Before the print (pre-print entry, hold 28 days)

| Set-up | n | hold_fx_28d | t | Rule |
|---|---|---|---|---|
| Sold into the print (runup < 0) and the previous print sold off | 517 | +3.11% (median +2.13%, 59% positive) | 4.5 | **Best pre-print set-up.** Buy a limit inside the buy-in-red band. |
| Sold into, previous print up | 558 | +1.33% | 2.1 | Second best. |
| Ran into, previous print down | 759 | +1.09% | 2.1 | OK. |
| Ran into, previous print up | 727 | -0.35% | -0.6 | **Avoid** (sell-the-news risk for a held line). |
| Run-up bottom quintile (< -3.8%) | 941 | +1.74% | 3.4 | Sold-into names hold best. |
| Run-up middle quintile (-0.2% to +2.8%) | 941 | -0.55% | -1.4 | Flat into the print: no edge. |
| Next-FY EPS revisions top quintile (> +6.6% in 3 months) | 438 | -0.51% | -0.7 | **Rising estimates are priced**: no hold-through edge. |
| Revisions bottom quintile (< -1.8%) | 437 | +1.96% | 2.7 | Low bar beats. |
| Street upside top quintile (> +38%) | 958 | +1.41% | 2.4 | Wide street gap helps. |
| Old BUY_PRE rule (sold into + revisions up + last print up) | 359 | +0.94% vs +0.90% others | - | **No edge: retired as a rule.** |

Slot: BMO prints reacted +0.36% on average, AMC +0.14% (n 2,366 / 2,474).

## After the print (drift from the reaction close)

| Read | n | drift_fx_28d | t | Rule |
|---|---|---|---|---|
| B/B (EPS and revenue beat) | 2,872 | +1.00% | 4.35 | Drift continues: post-print adds on B/B are supported. |
| B/M (EPS beat, revenue miss) | 591 | -0.32% | -0.69 | No drift, and it reacts -0.97% vs the market: revenue decides. |
| M/B | 707 | +0.31% | 0.58 | No drift. |
| M/M | 459 | +0.01% | 0.02 | No drift (reaction -4.34%: the night already priced it). |
| Revenue not posted yet (`rev_posted` false) | - | - | - | **No mix read.** TradingView rolls the estimate before it posts the actual (a day late on some BMO prints); use its own `revenue_surprise_percent_fq` or wait. Before 2026-10-28 about 700 prints sat in B/M by this artefact. |
| EPS surprise quintile 4 (+9.6% to +24.2%) | 945 | +1.35% | 3.5 | Strong beats drift; the top quintile (> +24%, one-offs) less (+0.76%). |
| Revenue surprise +0.2% to +13.2% (Q3-Q4) | 1,854 | +0.98% / +1.24% | 2.3 / 2.8 | A clean revenue beat drifts. |
| Reaction bottom quintile (< -5.1% vs market) | 957 | +1.37% | 3.0 | **Big sell-offs recover.** |
| Reaction top quintile (> +5.4%) | 958 | +1.06% | 2.4 | Big pops keep going. |
| Middle reactions | 2,873 | about 0 | - | Nothing to do. |
| Beat, sold 5%+ vs market | 602 | +1.26% | 2.3 | Buy-the-dip after a good print works. |
| Miss, sold 5%+ vs market | 349 | +1.64% | 2.0 | Even misses that sold off hard recovered. |
| Miss, bought 5%+ | 149 | +0.60% | 0.4 | Thin; no edge. |

Drift by print month (28d): Apr +1.03, May -0.79, Jun -3.17 (n 98), Jul +0.59, Aug +1.12, Sep +3.43
(n 111). Drift is regime-dependent: May / June drifted negative into a soft tape.

## The reaction night (what a hold-through takes overnight)

Every pre-print set-up is close to a coin flip on the reaction day: react_fx hit 48-53%, q10 -7.6% to
-9.4%, q90 +8.6% to +10.4% (best cell: hit 52.9%, q10 -8.2%). Only the surprise sorts the night, and it is
unknown before the print. The set-ups earn their edge over the 7-28 days after it.

| Set-up | Reaction vs market | n | drift_fx_28d from the reaction close | t |
|---|---|---|---|---|
| Sold into, previous print down | sold 5%+ | 94 | +3.31% | 2.21 |
| Sold into, previous print down | inside the band | 330 | +3.63% | 4.92 |
| Sold into, previous print down | bought 5%+ | 115 | +1.06% | 0.87 |
| Sold into, previous print up | sold 5%+ | 121 | +2.77% | 2.35 |
| Ran into, previous print down | sold 5%+ | 139 | +2.36% | 1.99 |
| Ran into, previous print up | sold 5%+ | 146 | -0.17% | -0.15 |
| Gapped down 5%+, sold after the open | (any set-up) | 402 | +2.39% | 3.01 |
| Gapped down 5%+, bought after the open | (any set-up) | 457 | +0.63% | 0.93 |
| After hours below -3.7% (AMC, post-close scans) | next close vs the after-hours read: +2.24 pp | 31 | -4.49% | -1.52 |

Reading: a 5%+ sell-off on the reaction does not cancel the set-up's drift, except after a run-up into the
print that followed an up print. Big pops (bought 5%+) have little left (+0.2% to +1.3%, t < 1): trim into
them rather than add.

## Every field, one at a time (print night, 2026-10-29)

`tv_scan_cli.py print-night` cuts 23 pre-print fields and 5 print fields into quintiles on the lab's 5,204 prints
(30 Mar to 29 Oct) and keeps an arrow only where the lift vs every other print has |t| >= 2, n >= 40, the same
sign in both halves and the hit rate moving the same way: 70 of 407 cells. Base: reaction vs market +0.33
(hit 49.9%, q10 -9.1, q90 +10.0, n 5,150); hold 7d +0.20 (n 4,739); hold 28d +0.62 (hit 48.4%, n 4,424).

| Read | Cell | Lift (pp) | t | n |
|---|---|---|---|---|
| Reaction, any pre-print field | only middle quintiles (RSI, 50-day, beta, 1M revisions, target drift) | under +/-1 | 2.1-2.6 | ~1,000 |
| Hold 7d up | run-up bottom quintile (< -3.7%) | +1.37 | 3.6 | 933 |
| Hold 7d up | weekly move-prediction consensus bottom quintile | +1.20 | 3.2 | 953 |
| Hold 7d down | coming quarter's EPS step top quintile (> +22.8%) | -1.04 | -2.7 | 789 |
| Hold 28d up | trailing month top quintile (> +12.3%) | +2.87 | 4.5 | 977 |
| Hold 28d up | p_dvc top quintile | +2.62 | 3.7 | 439 |
| Hold 28d up | the industry's prints paid this season (peer_rx > +2.5) | +1.98 | 2.9 | 746 |
| Hold 28d up | run-up bottom quintile / trailing quarter bottom quintile | +1.47 / +1.66 | 2.6 / 2.6 | 889 / 830 |
| Hold 28d down | realized volatility bottom quintile | -2.13 | -5.3 | 694 |
| Hold 28d down | EPS revisions top quintile (> +6.6%, priced) | -1.84 | -2.3 | 379 |
| Hold 28d down | run-up middle quintile (flat into the print) | -1.54 | -3.2 | 848 |
| After: reaction | B/B / M/M mix | +3.43 / -5.09 | 13.7 / -11.2 | 3,038 / 478 |
| After: close vs after hours | after hours below -3.5% (AMC) | +1.68 | 2.0 | 50 |
| After: drift 28d | reaction bottom quintile (< -5.0%) | +1.14 | 2.3 | 916 |

Reading: nothing known before the print calls the reaction session itself; the tape fields show a U-shape on the
holds (both the sold-into and the strongest-month names beat the flat middle), and quiet, low-volatility names and
priced revisions lag. A pre-print entry is a hold-through bet sized by the overnight tail; the reaction is read after
the print. Page: the outlook's Tonight tabs (screens and a Guide tab); per-name counts in `PACK/print_night`.

## How to use it on a print day

0. Tonight's reporters: the outlook page's Tonight tab. Start with the screens Hold 28d leans up / 7d leans up /
   Leans down / Book lines printing; count families, not arrows; a down arrow in Estimates or History, or a wide
   own move (`own_move_starter_usd`), sends the name to the post-print digest instead of a pre-print ticket.

1. Pre-print: rank the week's names by the set-up first (sold into + previous print down), then check
   revisions are not in the top quintile (priced) and street upside is wide. Enter as a limit between the
   alpha 7d q20 and the top of the buy-in-red band (not the band middle), with a kill on a close; size from
   the Daily's event budget. Why (play-review 2026-10-28): on 27 Oct the band middles sat 4-8% under the
   mark, 17 of 19 week-list names never traded into them, and the two that did (NYSE:XYL, NASDAQ:LAUR)
   kept falling (-6.7%, -4.7% vs the tape): fills deep in the band were adverse selection before a print.
2. Post-print, next session: read `surprise_mix` and `react_fx`.
   B/B with any reaction: hold / add on a dip. B/M, M/B: no drift edge, no add.
   Reaction < -5% vs market: a recovery candidate for 1-4 weeks (both beats and misses). Use the full
   bounce band here, and when the day's tape class is CONTINUE_DOWN wait for a close inside the band
   (punished day tails fell -2.9% over 7 days in the Sep-Oct sample).
3. Book lines into a print that ran into it after an up print: that is the -0.35% cell; DERISK fits.
4. Size into the print, not the kill. A hold-through or BUY_PRE takes the cell's q10 overnight (-8% to
   -9%): `rx_tail_usd` (week plays) is the line times that tail. DERISK to the dollars you accept there.
5. The kill does not fire on the reaction close or the after-hours print for the three set-ups whose
   sell-offs drift (sold into either way; ran into after a down print). Re-arm it at the reaction
   session's low, judged from the second close, unless the print breaks the thesis (`thesis_kill`). For
   ran into + previous print up, the kill fires as written. Why (2026-10-28): NASDAQ:GOOG went -9.4% after
   hours through its 303.2 kill in the best cell, whose 5%+ sell-offs drifted +3.31% (t 2.2) afterwards.
6. Decide at the cash close, not after hours or in the premarket: deep after-hours drops closed 2.2 points
   better on average (thin, n 31), and gap-downs that kept selling after the open drifted +2.39% (t 3.0).
7. On the print night read `rev_posted` before the mix: false means the revenue actual is not in the
   scan yet, so B/M or M/M can be an artefact. EPS on names with large equity stakes (NASDAQ:GOOG: +216.7%
   last quarter, -29.7% now) carries investment marks; read the operating lines (OUTSIDE).

## Caveats

- One regime (rate-up, AI capex, two down months at the end). Re-read `print_cells` each season.
- Cells overlap (a name is in several); t is optimistic; no transaction costs.
