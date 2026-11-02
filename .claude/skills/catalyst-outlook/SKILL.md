---
name: catalyst-outlook
description: Catalyst outlook - every US $2B+ name with its catalysts over the next six weeks (earnings with slot, ex-dividend, IPO lockups, dated OUTSIDE events such as the Fed, CPI, index reviews, PDUFAs, conferences) and the day's suites side by side, in a fixed column order (technical, setup, earnings surprise, value, progression, pack lists); plus the print-night deep dive on tonight's reporters (measured pointers up / down per horizon, the overnight tail, the post-print digest). Use when the user asks for earnings and other catalysts for today / this week / next week, a deep dive on the names reporting tonight, a pre-earnings decision matrix, how to digest a print, build-up trades into later events, overlooked names, positioning for the next 4-6 weeks, a catalyst calendar, or pastes CATALYST OUTLOOK / PRINT NIGHT. Not the Daily budget, not a 0-100.
---

# Catalyst outlook

**What is coming in the next six weeks, which names it touches, and what each name looks like in one row: tape, setup, last print, value, estimate and model progression.**

## Commands

```powershell
python src/generic_utils/tv_scan_cli.py outlook --pack PACK --ids NVDA,ZM --line     # rows for named ids + summary (PACK/outlook)
python src/generic_utils/tv_scan_cli.py page --template outlook --day YYYYMMDD --judgment J --out-dir RUN_DIR --strict
```

The daily refresh runs `outlook` after week_plays (optional suite) and `page-all` renders the template. Code `generic_utils/catalyst_outlook.py`, recipe `config/generic_utils/catalyst_outlook.json` (universe floor, horizon_days 42, lockup_days 180, levels_window_days, joins, and **`column_groups`: the one owner of the column order** for the tables and the page), OUTSIDE dates `config/generic_utils/catalyst_calendar.json`.

`PACK/outlook/outlook.duckdb` (grep its `queries.txt`):

- `names`: one row per name on the floor (plus named ids and the Book), columns in group order. Earnings dates come from the day file for every name (not only W0 / W1); ex-dividend, IPO date, analyst count and 52-week-high date from the newest full-scan run (intraday runs leave them empty; `spec.catalyst_run` names it). `pack_lists` empty = the name is on no pack list.
- `events`: one row per catalyst inside the horizon (earnings, ex_dividend, ipo_lockup, and each OUTSIDE calendar row with status and source).
- `days`: one row per session: counts, the largest prints, Book prints, other events.
- `groups`: the column groups.

## Print night (tonight's reporters, on demand)

```powershell
python src/generic_utils/tv_scan_cli.py print-night --pack PACK --line                 # every name whose reaction lands on the next session
python src/generic_utils/tv_scan_cli.py print-night --pack PACK --react-on today --line # the morning after: digest the prints reacting today
python src/generic_utils/tv_scan_cli.py print-night --pack PACK --sessions 5 --quiet    # the rest of the week
```

The refresh runs it after week_plays (optional suite `print_night`); the outlook template shows its tabs (Tonight, Tonight pointers,
Tonight digest, Pointer evidence) only when `PACK/print_night/print_night.duckdb` exists (tab-level `requires`). Code
`generic_utils/print_night.py`, recipe `config/generic_utils/print_night.json` (pointers, families, measures, t_min, min_n, halves / hit rules).

A **pointer** is one field known at the time, cut into quintiles on the lab's past prints (`PACK/signal_lab/print_events.csv`, store
features at each print's pre-print file); slot and mix use categories. Per cell and measure: mean, hit, n, `lift` vs every other print,
Welch `lift_t`, the lift in each half, `hit_lift`. `dir` up / down only when |t| >= t_min, n >= min_n, both halves agree and the hit rate
moves the same way. Tables: `names` (per reporter: `<m>_up` / `<m>_dn` counts for rx reaction, h7 / h28 hold, ac after-hours to close,
d28 drift; `_fam_up` / `_fam_dn` families; `_up_list` / `_dn_list`; the set-up cell's tail in starter dollars; own past move; peers this
season; alpha decile with its measured mean; levels), `matrix` (`x_<pointer>` cells `▲+1.4` / `▼-2.1` / bare lift), `pointers` (long form),
`cells` (every measured cell), `base` (all prints), `history` (the reporters' own past prints).

Reading (the page's Guide tab has the steps and every screen):

1. Fields known before a print have barely called the reaction session (29 Oct: only middle-quintile cells, lifts under 1 pp). A pre-print
   entry is a bet on the hold-through (h7 / h28), where the pointers carry signal; size it by `tail_starter_usd` / `own_move_starter_usd`.
2. Count families, not arrows: Tape pointers (run-up, month, RSI, 50-day) point together.
3. A down arrow in Estimates or History, or a wide own move, sends the name to the digest: buy the reaction in the bounce band after the print.
4. Book lines printing: hold vs DERISK from the down list and the tail; the polar rule and sticky DERISK still bind.
5. The digest (after the post-close scan or `--react-on today`): the print's own pointers on the reaction, the after-hours-to-close move
   and the 28-day drift, beside the week-plays post cells. Decide at the cash close.

**Print brief page** (one card per reporter, the written read):

```powershell
python src/generic_utils/tv_scan_cli.py print-brief --pack PACK --ids RDDT,INTC --line   # headlines (and every text) for named cards
python src/generic_utils/tv_scan_cli.py page --template print_brief --day YYYYMMDD --judgment J --out-dir RUN_DIR --strict
```

`generic_utils/print_brief.py` (recipe `config/generic_utils/print_brief.json`: starter, peer window, the set-up and slot notes) runs after
print_night and outlook (refresh suite `print_brief`). `PACK/print_brief/print_brief.duckdb`: `cards` (card_fields plus `headline`,
`setup_text`, `value_text`, `quarter_text`, `peers_text`, `pointers_text`, `levels_text`, `history_text`, `print_text`, `watch_items`) and
`industries`. Every number in a text is a pack value or a measured cell. The page (tabs Focus, Leans up, Leans down, Large caps, Printed,
All reporters, Industries) renders cards with the renderer's `cards` block; Course / Reason join from `courses`.

Judgment `pages.print_brief`: `title`, `ids` (the Focus cards in order: the Book's prints first), `blocks.stats`, `blocks.read` (callout),
`blocks.sources` (list; markdown links render), and `notes` `{id: [lines]}`: the verbal layer the data cannot see (guidance, consensus by
source, report-date conflicts, the mission for the print), every line tagged OUTSIDE with its date and source. Web reads are costly: write
notes for the Book's prints and the few names that could take capital, not every reporter.

Judgment: `pages.outlook.blocks.tonight_read` (callout), `blocks.tonight` `[[Symbol, Slot, Course, Entry, Kill, Size, Pointers, Why]]`,
`blocks.digest_read` (callout, after the print). Event tickets come out of the Daily's event budget; otherwise WATCH with the post-print band.

## Reading

1. Today and this week: the earnings-week / week-plays rules hold (best pre-print cell at the band top, B/B and beat-sold dips after the print in the full band). The outlook adds the calendar around them.
2. Next week: rank the best-cell names by the alpha decile, check revisions are not in the top quintile, and keep them WATCH until that week's Daily spends its event budget.
3. Weeks 3-6 (`earn_week` W2-W6): build-up candidates are bought before the event inside the band, not chased after it. Their pre-print cell is not formed yet; re-read it on the week of the print.
4. Overlooked = strong on model / print / value but on no pack list, or thin coverage (`analysts`). A reading, not a score.
5. Lockups (`ipo_lockup`) are offer date + 180 days, estimated: supply, never a buy signal by themselves.
6. OUTSIDE rows change timing and Grade only. Add a new dated event to `catalyst_calendar.json` (date, kind, event, status, source, symbols, read) instead of writing it only into a page.

## Judgment (`pages.outlook`)

`ids` (the Shortlist data tab, reading order), `blocks.stats`, `blocks.read`, `blocks.top20` `[[#, Symbol, Catalyst, When, Course, Entry, Kill, Evidence, Why]]` (the final reading order for this week and next: pre-print best cell, post-print beat/miss-sold, sticky DERISKs, build-ups; not a budget), `blocks.shortlist` `[[Symbol, Catalyst, When, Course, Entry, Kill, Upside, Evidence, Why]]`, `blocks.positioning` `[[Symbol, Theme, Course, Entry, Kill, Target, Catalyst path, Why]]`, `blocks.outside` `[[Date, Catalyst, Names, Read, Source]]`, `blocks.rules`, and one text per tab (`calendar_0`, `week0_0`, `week1_0`, `buildup_0`, `other_0`, `overlooked_0`, `positioning_0`, `book_0`), `blocks.sources`. Courses for every ruled name. One budget per Daily: next week's events and the build queue are WATCH / `NEW (queue)` lines, never a second budget.

## Generic vs domain

Universe, catalyst dates, joins and column order are generic (no cutoffs beyond the floor). The windows on the page (decile 8+, $3B+, W2-W6), the courses and the sizing are domain.
