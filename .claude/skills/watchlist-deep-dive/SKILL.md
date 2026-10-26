---
name: watchlist-deep-dive
description: Custom operator page on named themes or tickers - discount to peers and own history, buy-in-red band, kill zone, add-on-reclaim and trim zone per name, grouped by theme, with GMV lenses. Use when the user names stocks or a theme (AI buildout, cyber, hyperscalers) and asks where the discount is, what margin of safety, investment band, kill, trim or add to use. Not the Daily budget and not a 0-100.
---

# Watchlist deep dive

**For a named set: how cheap is it on forward earnings, where do I buy on a red day, where does that entry die, where do I add on confirmation, and where do I trim?**

## Commands

```powershell
python src/generic_utils/tv_scan_cli.py page-run --day YYYYMMDD --mode ide --origin "deep dive"     # prints RUN_DIR
python src/generic_utils/tv_scan_cli.py deep-dive --day YYYYMMDD --theme ai_core,ai_peers,cyber --ids EXTRA1,EXTRA2 --out-dir RUN_DIR
# after writing RUN_DIR/judgment.json (courses + pages.deepdive blocks):
python src/generic_utils/tv_scan_cli.py deep-dive --day YYYYMMDD --theme ai_core,ai_peers,cyber --levels-dir RUN_DIR/name_levels --judgment RUN_DIR/judgment.json --out-dir RUN_DIR --strict
```

`deep-dive` runs `name-levels` (tape row -> entry book -> GMV lens -> day history -> safety band) into `RUN_DIR/name_levels`, builds a template with one tab per theme (levels, margin of safety, 7-month path) plus Plan, Lenses and Sources, writes it as `RUN_DIR/deep_dive_template.json`, and renders it. `--levels-dir` reuses a folder instead of rerunning it. Themes are `config/generic_utils/themes.json` (add a theme there instead of retyping ids). Theme candidates by industry: `tv_scan_cli.py theme-slice --theme ai_buildout --line`.

## Judgment (run-local, not the live day judgment)

Write `RUN_DIR/judgment.json` with `courses` (Course / Grade / Horizon / Reason for every name) and `pages.deepdive.blocks`: `stats` (list of {value, label, tone}), `bias`, `contrast`, `timing` (callouts {title, text, tone}), `rules` (list of strings), `group_1`, `group_2`, ... (text per theme tab, in theme order), `lens_text`, `sources` (strings). The live day judgment and priors stay untouched; the Daily owns the NEW / ADD budget.

## How to read it

1. `mos_peer_pct` is the discount to the industry median forward PE on today's next-FY EPS; `mos_own_med_pct` to the name's own median. `pe_nfy_seat` 0 means the cheapest multiple of the window. A cyclical at a window-low multiple on estimates that tripled (`eps_fc_pct`) is a cycle question, not a margin of safety by itself.
2. `derate_pct` against `eps_fc_pct` separates a price that fell behind its estimates (de-rating) from a multiple that expanded (re-rating).
3. Buy-in-red: a full $5,000 starter inside `bounce_low`-`bounce_high` on a red day, never at the mark; `bounce_kill` ends that entry. The confirmation entry is `add_trigger_low`-`add_trigger_high` (the 50-day reclaim). A held line keeps the Daily's own kill.
4. Trim into `trim_zone`; `trim_zone_state` above_street means a held line is already there.
5. Raw history is split-adjusted (`splits`); a raw price span that shows a crash with a continuous market cap is a split.
6. Outside news (dates, prints, narrative) is tagged OUTSIDE and moves timing or Grade only.

## Generic vs domain

Levels, anchors, the split guard and the page generator are generic (`generic_utils/name_levels.py`, `safety_band.py`, `deep_dive.py`). Courses, grades and the size advice ($5,000 starter, about $1,000 add) are judgment.
