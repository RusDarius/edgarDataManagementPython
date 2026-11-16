# Interpretation guide: reading the evidence and adjusting the analysis

## 1. Reading a number

| Number | Means | Trust it when | Do not |
|---|---|---|---|
| IC (`ic_mean`) | Daily rank correlation of a feature with later excess return. 0.03 is useful, 0.10 is strong for 2,000 names. | `ic_t_indep` >= 2, both halves the same sign, `ic_ind` close to `ic_mean` | Read a 56d IC as proven (one to three independent periods). |
| `ic_t_nw` vs `ic_t_indep` | Two t's; NW runs optimistic when the lag nears the sample. | Use the smaller (the verdict does). | Quote NW alone. |
| `ic_ind` | Same IC inside industries. | Close to `ic_mean`: stock-picking signal. | Treat a big `ic_mean` with small `ic_ind` as stock-level: it is sector rotation. |
| `ic_up_mkt` / `ic_down_mkt` | IC split by the trailing 1-month market. | Signs agree. | Use a REGIME feature without checking today's regime. |
| verdict | STRONG / CONSISTENT / REGIME / WEAK (statistical label). | STRONG over CONSISTENT. | Treat as a gate. About 5% of tests clear t 2 by chance (about 18 of 390). |
| `scan_summary.fx_mean` | A pack list's equal-weight excess over the US $2B tape. | n_days >= 20 for 7d. | Over-read 28d rows (9 pack days). |
| `pred_<h>`, `dec_<h>` | Model forecast, % excess vs the market; decile within today's names. | `state` = OK and `beats_baselines`. | Add to rr / bo / left, size from it, or buy the top decile blind. |
| `deciles` | What each forecast decile returned out of sample. | Monotonic; bottom decile clearly negative. | Expect the top decile's mean every time (hit rate 53%). |
| bands `b<h>_q20/q50/q80` | Flat-market price range for the horizon. | As a limit / trim reference beside entry-book bands. | Ignore the market move (shift the band by it). |
| print pointers | Last print's surprise and reaction on every row. | Read with `earnings_playbook.md`. | Rank on `last_surprise` above +100% (one-offs, e.g. NASDAQ:GOOG +216%). |

## 2. Daily checklist (two minutes, before naming names)

1. Regime: pack breadth plus `regime_playbook.md` section 1. Up or down month? Rates and oil (OUTSIDE)?
2. Model `state` (alpha page Health tab): any horizon OK? If none, the model is a second read only.
3. Lab digest (`PACK/signal_lab/highlights.log`): did the STRONG list change since the last snapshot in
   `findings_log.md`? A feature that flipped sign is the first thing to adjust.
4. Pack lists (`scan_summary`): which of our own lists paid lately (trim / exit worked; ADD and the short
   book did not in Sep-Oct). Lower the Grade on calls from lists that have been losing.

## 3. When to adjust the analysis

| Signal | Adjust |
|---|---|
| A STRONG feature's `ic_recent` turns negative for 20+ days | Drop it from the reading order; re-run `signal-lab --first-day <turn>`. |
| `mkt_regime` crosses zero | Switch the regime column in `regime_playbook.md` section 3; expect momentum / street upside to swap sign. |
| Model `state` goes DEGRADED or FAILING | Stop quoting `pred` for that horizon; check `weights.flips_60d` for the feature that turned. |
| A pack list loses for 3 weeks (`scan_days`) | Lower the Grade of that list's calls; look for the rule behind it (e.g. BOUNCE fighting a continuation drift). |
| A print set-up stops paying in a season | Update `earnings_playbook.md` with the new snapshot; keep the old line struck through with its date. |
| A new field or set-up idea | Add it as a SQL feature in `config/generic_utils/panel_store.json` or a list in `signal_lab.json`; test before using. |

## 3b. Scoring yesterday and reading the month (play-review, month_ind)

- `play-review` (PACK/play_review) scores the newest prior judgment against today's pack. Read `ret_fx`
  per course for direction, and `open_state` / `min_state` / `close_state` for the levels: a list that is
  right on direction but never fills (ABOVE_BAND all day) says the band is too deep, not that the thesis
  is wrong. One session is noise for the courses; repeat the read on day 5 and day 20.
- The day files hold no session low or high: a fill is measured on opens and closes. A limit that would
  have filled on an intraday dip is not visible here.
- `month_ind` (PACK/week_plays) is the alpha model's 28-day forecast rolled up per industry. Use it as a
  regime read (which kinds of businesses the quality / value weights favour now), not a buy list, and only
  while the 28d state is WEAK or better.

## 3c. Peer value: a premium earned or not (peer-value)

- `prem_pct` is the plain premium to the industry median; `just_pct` is the premium the name's growth and quality earn in the
  industry's own fit; `gap_pct` is the difference (positive = cheaper than peers with the same profile).
- Four reads: discount + gap near 0 = the profile explains the discount (do not buy it for cheapness); discount + gap > 0 = cheap for
  what it is; premium + gap > 0 = rich to the median but still under what it earns; gap < 0 = paying more than the profile delivers.
- Check `groups.pay_*` before calling a growth premium deserved: an industry that pays nothing for growth will not reward it.
- Cyclicals at peak margins and misclassified names fit badly; groups under `min_fit_n` give only the premium.
- Support, not a sort key or a size (forward-value skill, Peer value; evidence in `peer_value.evidence`).

## 4. Known biases in this evidence

- One market, seven months: AI build-out into an October chip sell-off, rate hikes, oil above $100.
- Screener profile scores start 2026-06-25 (earlier prediction runs are consensus-only backfills).
- Intraday scans (14:00-20:00 UTC): labels run scan-to-scan, not close-to-close.
- No transaction costs, no borrow costs for shorts.
- The default model variant was picked from 9 tried on the same window.

## 5. How the agent should cite evidence

"p_def 7d IC +0.060 (t_indep 3.0, 14 independent periods, STRONG, signal lab 2026-10-27)" not "p_def
works". Tag OUTSIDE context with its date and link. Evidence changes Grade, timing and reading order only.
