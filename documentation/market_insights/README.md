# Market insights (backtest evidence, regime, interpretation)

Standing notes from the deterministic studies, kept so later analysis and decisions start from what
the data has already shown instead of re-deriving it. Evidence comes from the signal lab and the alpha
model (panel store of the all-fields day files, US $1B+); outside context is tagged OUTSIDE and dated.

| File | Use it when |
|---|---|
| [regime_playbook.md](regime_playbook.md) | Deciding which signals to lean on today: how to read the regime, what worked in each one, what to change when it turns. |
| [interpretation_guide.md](interpretation_guide.md) | Reading a number on a page (IC, t, verdict, print pointers, model deciles and bands) and deciding whether the analysis needs adjusting. |
| [earnings_playbook.md](earnings_playbook.md) | Pre-print entries, post-print drift, buy-the-dip after a print. |
| [investing_pathway.md](investing_pathway.md) | The Book's plan from here: tilt vs what pays, picking vs industry, stages and the regime triggers that move them. |
| [deterioration_playbook.md](deterioration_playbook.md) | A held line has fallen: is it only price or the business too, hold / cut / double down, recovery levels. |
| [findings_log.md](findings_log.md) | Dated evidence snapshots (newest first): the numbers behind the rules above, with sample size and t. |

## Rules for this folder

1. Every number carries its date, horizon, t (`t_indep` where it exists) and sample size. A rule without
   a number is a hypothesis and is marked so.
2. Append a dated section to `findings_log.md` after a re-run instead of overwriting; change a playbook
   line only when the newer evidence disagrees, and say which snapshot changed it.
3. Evidence changes Grade, timing and reading order. It never becomes a filter, a size, a 0-100, or a
   second NEW / ADD budget (CLAUDE.md invariants).
4. Seven months of one market is the current sample (2026-03-29 to 2026-10-27). Prefer rules that held in
   both halves and in up and down months.

## Refresh the evidence

```powershell
python src/generic_utils/tv_scan_cli.py panel-store --quiet
python src/generic_utils/tv_scan_cli.py signal-lab --pack latest --no-update --line
python src/generic_utils/tv_scan_cli.py signal-lab --pack none --first-day 2026-09-01 --line   # recent-regime check
python src/generic_utils/tv_scan_cli.py alpha-model --pack latest --no-update --line
python src/generic_utils/tv_scan_cli.py week-plays --pack latest --quiet   # today's rows placed in those cells (PACK/week_plays)
python src/generic_utils/tv_scan_cli.py play-review --pack latest --line   # the prior judgment's calls and levels vs today (PACK/play_review)
python src/generic_utils/tv_scan_cli.py deterioration --pack latest --line # drawdown x letters beyond price, the Book's state (PACK/deterioration)
python src/generic_utils/tv_scan_cli.py pathway --pack latest --line       # the Book's factor tilt vs what pays, picking vs industry (PACK/pathway)
python src/generic_utils/tv_scan_cli.py print-night --pack latest --line   # tonight's reporters on measured pointers (PACK/print_night)
```

Monthly at least, and after any regime turn listed in `regime_playbook.md`. `week-plays` puts each name
printing this week (and each Book line) in the measured cells, so a playbook rule can be read straight off
the `week_plays` page instead of being re-derived. `play-review` scores the previous day's courses and
published levels against the new pack (the page's Yesterday's calls tab); the refresh runs both.
