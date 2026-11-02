# Investing pathway (from the 29 Oct 2026 drawdown)

The plan for the Book from here, with the evidence behind each step and the triggers that move it on. Numbers:
`tv_scan_cli.py pathway` and `deterioration` on pack `briefing_pack_20261029_1411_utc_fa5d9ba1` (store day
2026-10-29, 1,777 US $2B+ names; signal lab 142 days 2026-03-29..10-29). Page:
`logs/canvases/20261029/1423_full/pathway-20261029.html`. Re-read the triggers every Daily (the refresh rebuilds
PACK/pathway); change a line here only when the evidence changes, and log the reason in `findings_log.md`.

## 1. Diagnosis

| Read | Value (29 Oct) | Meaning |
|---|---|---|
| Book's US lines vs the market, 1M | -9.4 pp: industry -7.5, stock -1.9 | The industries chose the result |
| Same, 3M | -5.0 pp: industry -4.4, stock -0.7 | Picks inside industries close to neutral |
| Quality lean (p_def / p_dvc / FCF margin percentile) | 0.79 / 0.80 / 0.74 | Paid: tilt_month +0.010 / +0.030 / +0.030 |
| Beta percentile | 0.83 | Cost: October 7d IC -0.18, trailing tape -0.13 (30% positive days) |
| Street-upside percentile | 0.78 | Cost: October -0.14, tape -0.13 (10% positive days) |
| Volatility percentile | 0.69 | Cost: October -0.16, tape -0.11 |
| Distance to the 52-week high | 0.27 (far from highs) | Cost: near-high names led (+0.09 October, +0.10 tape) |
| Coverage | 71% of the Book (XETR:VUAA, OMXCOP:NOVO_B outside the US store) | |

Per line: semiconductor lines lost to their industry (-14.9 pp vs the market over 1M) while NASDAQ:QCOM (+6.5) and
NASDAQ:FSLR (+12.5) beat it and NASDAQ:MU was in line (-0.5). The stock-specific losses were prints and narrative:
NASDAQ:STRL -20.3, NASDAQ:GOOG -17.5, NYSE:RDDT -11.2, NASDAQ:BIDU -11.0 against their industries.

Conclusion: the selection method (quality, FCF, cheap on forward earnings) works; the mix (one AI-capex trade,
high beta, high street targets) is what lost. Change where new money goes, not how names are picked.

## 2. Stages

| Stage | When | Action | Moves on when |
|---|---|---|---|
| 0 | 29 Oct | Derisk NASDAQ:FSLR 40% and NYSE:RDDT a third into the prints, exit NASDAQ:BIDU, NYSE:AMP limit 470.2-481.8 | Done (cash $6.8k -> $10.7k, 7.5%) |
| 1 | next 1-2 Dailies | One low-beta quality starter per Daily from the month leaders: NYSE:HIG (128.7-131.5, kill 126.7), then NYSE:EOG after its 5 Nov print (155.6-159.1, kill 152.9) if funded; no new money into semiconductors | Beta percentile at or under ~0.75 |
| 2 | 2-6 weeks | Hold the no-letter drawdowns for the drift; adds only inside bands | A new letter or a kill close on a held line |
| 3 | 4 Nov to mid-Dec | Read NASDAQ:QCOM, OMXCOP:NOVO_B (4 Nov) and NASDAQ:MU (December): cut on 2+ letters under the bounce kill | Letters after each print |
| 4 | when the tape turns | Lift adds in the AI cluster inside bands; let it drift back toward a third of NAV | Two of the three triggers below |

Cash: after NYSE:AMP and NYSE:HIG about $5.7k (4.0%, capital-plan `capital_plan_pathway`). Keep about 3% or more;
the second diversifier needs new funding (a kill-close trim or fresh cash).

Each $5,000 low-beta starter lowers the Book's beta percentile by about 0.035-0.041 (NYSE:EOG -0.041,
NYSE:HIG -0.035, NYSE:AMP -0.018); a third of NASDAQ:MU sold lowers it by only 0.009. The style changes with
where new money goes.

## 3. Triggers

| Read | 29 Oct | Lean back into the AI cluster when | Go more defensive when |
|---|---|---|---|
| Beta, trailing 7d IC (20 labelled days) | -0.135, 30% positive | above 0 | under -0.15 with the market's month down |
| Street upside, trailing 7d IC | -0.130, 10% positive | above 0 | stays under -0.10 |
| Share of the floor above the 200-day | 57% (45% on 14 Oct) | over 60% for a week | back under 50% |
| Market's trailing month (regime) | +3.4 | stays above 0 | 0 or below: run the deterioration cut rule daily |
| December hike (OUTSIDE) | still priced | priced out | confirmed |

The 1-day IC is the early read and is noisy: beta paid +0.43 and +0.25 on 26-27 Oct (the chip bounce) and gave
back -0.14 on 28 Oct. Act on the 20-day tape, not on a bounce.

## 4. How to use

1. Every Daily: the pathway Tilt tab (beta, street upside, volatility) and the triggers; a NEW that moves the
   tilt toward what pays (negative `d_beta_add`, positive `d_p_dvc_add`) is preferred over one that deepens the
   costly lean. The Daily's one budget still binds.
2. Weekly: attribution 1M; the industry part should shrink as the mix changes. If the stock part turns
   negative instead, the selection method needs review, not the mix.
3. Monthly: re-run the signal lab with `--first-day` at the regime turn; update section 1 and 3 here.
4. The pathway never sizes a ticket and never ranks; the band, the kill and the course come from the Daily.

## 5. Open questions

- The attribution uses today's weights for 1M / 3M; a daily-weighted path needs the holdings history (capture-book).
- Europe and ETFs are outside the store: XETR:VUAA (22% of NAV) is the index ballast and is not in the tilt.
- Whether the quality lean keeps paying in an up month with falling rates: the sample has no rate-cut period.
