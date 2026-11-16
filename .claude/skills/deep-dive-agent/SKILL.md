---
name: deep-dive-agent
description: Deep research on one name or a suite of names (or a question over them, e.g. how trading an IPO would work) - the name dossier (every pack suite per name, the panel store's daily progression of price / valuation / estimates / targets / model scores, movement anatomy, print history, analog forward paths raw and in excess, evidence directions per horizon, peers, dated events) plus the agent's outside read (news, guidance, dated catalysts, regulation, competition) checked against the data, turned into a verdict, a band / kill / targets and a trajectory per horizon (SHORT / MID / LONG, each classed DATA_BACKED / MIXED / NARRATIVE). Use when the user asks for a deep dive / deep research / full analysis of a stock or several, a proposed trade to vet with data and online context, the best trades among a list, a trajectory or price path, or a question like "position the Anthropic IPO for the market and the company", or calls deep_dive_analysis_agent. Not the watchlist-deep-dive page (levels per theme), not the Daily budget, not a 0-100.
---

# Deep-dive agent

**Is this a trade, at what price, for which horizon, and what does the outside world add that the data cannot see?**

The Daily and its pages surface many names; this run goes deep on a few. The data step gathers everything the project
measures on each name (fast, deterministic); the agent adds the outside read, proves it against the data and makes the call.

## Commands

```powershell
python src/run_deep_dive.py --ids NYSE:DOW                                   # dossier + data-only page
python src/run_deep_dive.py --ids NYSE:DOW --agent                           # + headless agent: outside read, judgment, page
python src/run_deep_dive.py --ids NASDAQ:AMZN,NASDAQ:GOOG --question "..." --print-prompt   # prompt for the IDE
python src/generic_utils/tv_scan_cli.py dossier --ids NYSE:DOW,NYSE:LYB [--theme T] [--question Q]   # data step only
python src/generic_utils/tv_scan_cli.py dossier-page --dir DIR --strict      # render after writing the judgment
```

Python: `from run_deep_dive import deep_dive_analysis_agent; deep_dive_analysis_agent("NYSE:DOW")` (also in `src/main.py`).
Folder: `logs/tradingview_analysis/name_dossier/<YYYYMMDD_HHMM>_<label>/`. Recipe: `config/generic_utils/name_dossier.json`
(joins, progression fields, analog features / k / horizons, evidence sign rules, research query templates, page Ledger).

## What the dossier holds (read `dossier.md` once, ~6-9 KB per name)

- **Suite table** (request order): close, warn level, next catalyst, band state, distance to target / street / kill, model
  28d, analog 28d excess and the same-industry 56d excess, forward-value upside, IRR minus hurdle, peer gap, and the
  evidence counts per horizon (`short ▲/▼`, `mid ▲/▼`, `long ▲/▼`).
- **Per name:** the pack's widest row (board: tape, levels, catalysts, beat bar, alpha model, value, peers, deterioration,
  event risk) plus forward value bear / base / bull, alpha bands and their out-of-sample state, peer value, cycle, event-risk
  crowd, print night, the stance helper; the store's progression (first / last / range per metric, checkpoints every 10
  files), movement anatomy (largest drawdown and run-up with days, time above the 50 / 200-day, volume spikes, largest moves
  and whether a print caused them), the print history, analogs, trajectory prices, evidence lines, nearest peers, dated
  events and the research plan.
- **Analogs** = the nearest US $2B+ name-days to the name's newest store row (features ranked within each day, distance
  over the ranks), other names only, labels known: forward 7 / 14 / 28 / 56-day raw quantiles, excess over the day's median
  name, hit rate, and `n_dates` (few days = one market episode, weak). Read the **all** and **industry** rows side by side:
  on 8 Nov 2026 NYSE:DOW's all-floor analogs leaned up (+2.6 pp at 56 days) while its chemical peers in the same state lost
  (56-day median -7.6%, 1 of 60 up). The BASE row is every floor name-day: the analogs only matter where they differ from it.
- **Evidence** = one direction per item from fixed sign rules (`evidence.rules`), counted per horizon class. A count, never a
  score; it is what the horizon class is checked against.

## Work order

1. Read `dossier.md`. Nothing else whole (query `dossier.duckdb` with `named --db` for one missing number).
2. Outside read: the research plan per name and the question queries (WebSearch standard; extended for thin or recent
   items). Dated catalysts, guidance, estimate / target moves, competition and pricing, regulation and legal dates, capital
   allocation, macro / policy hitting the industry. Every item OUTSIDE with its link, checked against the dossier.
3. Judgment in `DIR/dossier.judgment.json` (from `dossier.judgment.skeleton.json`): thesis, bull / bear (each cites a number
   or a source), classes per horizon, trajectory rows anchored on the dossier (SHORT: analog 7d / 14d and the alpha 7d band;
   MID: analog 28d / 56d, alpha 28d, street; LONG: forward value bear / base / bull, cycle), levels (band, kill on a close,
   targets per horizon), verdict / grade / horizon / plan / size, catalysts, outside lines. Suite `order` with the one key
   that sets it; `summary`; with a question, `question_read` (text + rows: timeline, mechanics, valuation anchors, listed
   exposure, flows, the trade at each stage, what breaks it).
4. Render: `tv_scan_cli.py dossier-page --dir DIR --strict` (0 warnings). Chat: a short snapshot per name + the HTML path.

## Classes and verdicts

- **DATA_BACKED**: the horizon's up and down counts differ by 2+ and the outside read agrees. **NARRATIVE**: most items n/a,
  or the call rests on outside items alone. Else **MIXED**. A NARRATIVE horizon never carries the size.
- Verdicts: BUY_BAND (buy inside the band), ACCUMULATE (scale in on weakness), WAIT (thesis fine, price or timing not),
  AVOID, TRIM, SHORT_WATCH.
- Rules of engagement: starter about $5,000 (adds about $1,000 or staged tranches); every name a band, kill and trim / target;
  prefer the larger margin of safety; AI / data-center names in their own contagion lane; the most critical read (the gain
  to take, the loss to let go). One budget: verdicts are candidates for the Daily's queue, never a second NEW budget.

## Limits

- The store starts 29 Mar 2026: analogs and progression cover months, not a cycle; `n_dates` says how many days stand
  behind an analog read. 56-day labels end about two months before the newest file.
- Names outside the pack's US $2B+ board get store / all-fields data only (the notes line says so); non-US names have no
  store rows.
- Outside items are context: they can move timing, grade and the trajectory with a stated reason, never a sort key or a size.
