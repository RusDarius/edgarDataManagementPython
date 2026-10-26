---
name: brief-history
description: Compact scorecard of prior operator briefs versus later pack closes. Use when the user asks how earlier recommendations held up, what was wrong or right, how to adapt, or for a brief-history digest before Daily, Movers, or Playbook. Not a second ranking and not a 0-100.
---

# Brief history

Prior JSON notes are long. This skill reads the digest, not the folder.

The scorecard joins `logs/AI_ANALYSIS_UTILS/priors/*_operator_actions.json` to each cited briefing pack close and to the latest pack close. The return is the first session that has a price through the latest later close. The label uses the action on that first priced session.

Labels are bands in `config/generic_utils/brief_history.json`. They are not eligibility cutoffs and not a 0–100. ADD versus WAIT stays on the daily canvas. A starter is still sized with about $5,000 in mind. An add to a held line can be about $1,000. That advice is not a band.

## Command

```powershell
$env:PYTHONPATH='src;.'
python src/generic_utils/tv_scan_cli.py briefs
```

Folder: `logs/tradingview_analysis/operator_briefing/runs/brief_history_<stamp>/`
`digest.md`, `brief_history.duckdb`, `queries.txt`, `overview.log`.

Read `digest.md`. It lists sessions, counts, adapt lines, and at most 40 names. It does not copy notes.

One name, including the short note head only when `--note-chars` is set:

```sql
SELECT * FROM events WHERE symbol = 'NASDAQ:MU'
```

Full rollup is table `names`. Do not open every prior file. Open one prior only when a BAD or MISSED name needs the original reason on the canvas.

## How to use the labels

| Label | Meaning |
|---|---|
| GOOD | The call cleared its band. ADD/HOLD rose. TRIM/EXIT or a short fell. A PASS did not run. |
| BAD | The call missed its band. A fade, an early cut, a hold that broke, or a short that rose. |
| MISSED | A PASS later rose through the miss band. |
| FLAT | Inside the band. Not a lesson. |
| OPEN | No later close, or an action this scorecard does not label. |

Adapt lines are counts plus a few tickers. They change the grade on the next starter. They do not reorder Build-50, the mover 25s, or Best 25.

Re-run after a new briefing pack so the latest mark moves. The top-100 rank review is a separate command, `python src/run_decision_review.py`. Do not write `logs/_tmp_*.py`.

Ids: `names` / `events` carry `symbol` as `EXCHANGE:TICKER` (bare legacy priors keys are qualified from that day's pack, then the latest; a ticker listed on two exchanges stays bare). `ticker` is the bare helper column.
