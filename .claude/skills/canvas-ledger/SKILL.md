---
name: canvas-ledger
description: How to write an operator page ("canvas") — daily-focus, movers, book-risk, playbook, horizon-plan, capture, wisdom, analysis-style, post-earn and the rest — as a JSON spec rendered to self-contained HTML by tv_scan_cli.py page, with exactly one Ledger tab that explains every abbreviation and every coded value. Use whenever a paste says canvas output, when writing or editing any operator page, or when the user mentions rng, rr, leftover, mesh, glossary, abbreviations, or ledger.
---

# Operator pages and the one Ledger

An operator page ("canvas" in the prompts) must be readable without the chat. Cursor `.canvas.tsx` pages are retired; old ones stay in `~/.cursor/projects/d-FinanceProjects-edgarDataManagementPython/canvases/`.

## How a page is made (templates first)

Daily, movers, playbook and GMV pages come from templates (`config/generic_utils/pages/daily_focus.json`, `movers.json`, `playbook.json`, `gmv.json`). The template owns the layout, the SQL and the columns. You write one judgment file per day, `logs/canvases/YYYYMMDD/judgment.json`, and render into the run folder:

```
python src/generic_utils/tv_scan_cli.py page-run --day YYYYMMDD --mode full          # IDE paste only; a runner prompt already gives RUN_DIR
python src/generic_utils/tv_scan_cli.py page --template daily_focus --day YYYYMMDD --judgment logs/canvases/YYYYMMDD/judgment.json --out-dir RUN_DIR --strict
python src/generic_utils/tv_scan_cli.py page --template daily_focus --day YYYYMMDD --manual --out-dir RUN_DIR   # data-only page, no judgment
```

Layout: `logs/canvases/YYYYMMDD/judgment.json` (the day's live judgment) and one folder per run, `logs/canvases/YYYYMMDD/HHMM_<full|update|manual|ide>/` (UTC start: pages, `run.json`, `prompt.txt`, `agent.log`, `judgment.json` as that run left it, `judgment.prev.json`, `update.md`). `logs/canvases/index.html` lists every day → run → page. Without `--out-dir` a render goes to `$CANVAS_RUN_DIR` (set for the headless agent), else the day folder.

`--pack` defaults to the newest pack. Judgment file shape (see `logs/canvases/20261022/judgment.json`):

| Key | What it holds |
|---|---|
| `day`, `pack`, `reviewed_pack` | Day and the pack the judgment was written / last reviewed on |
| `courses` | `{"NYSE:BRBR": {"Course": "NEW", "Grade": "High", "Horizon": "MONTH", "Reason": "..."}}` (one decision per name, joined by every sourced table) |
| `pages.<daily\|movers\|playbook>` | `title`, `subtitle`, `blocks.<id>` (callout `{title,text,tone}`, list / stats items, literal table `{title,rows,tones,note}`, text), `ledger_entries` |
| `capital_plan` | `{"capital_plan": [plan rows], "capital_plan_queue": [...]}`, replayed by `capital-plan` on every update |
| `scans` | Extra deterministic steps you ran (argv with `{PACK}`), replayed on every update |
| `update.banner` | Written by UPDATE mode; replace it after review |

Ids: every name on a page (table cell, callout, list, judgment `courses` key) is `EXCHANGE:TICKER`; tables take `symbol` as the id column, not `ticker`.

Start a day's judgment from `tv_scan_cli.py judgment-skeleton --day YYYYMMDD --carry-from PRIOR_DAY` (the full-mode runner already writes `RUN_DIR/judgment.skeleton.json`): every block id with its shape under `pages.<key>._shapes`, the prior courses carried (`<d Mon> (carried): ...`) and its recorded scans. Render all of the day's templates (daily_focus, movers, playbook, gmv, wisdom) with `tv_scan_cli.py page-all --day YYYYMMDD --judgment ... --out-dir RUN_DIR --strict`: one line per page, plus the block ids still unfilled. Template hooks also include `requires` (a path or a list; a missing one skips the page) and `optional: true` on a table (a missing source is a page note, not a warning).

Block ids come from the template (`"from": "pages.daily.blocks.plan_3"`). A table with `ids_from` keeps only the rows a judgment list names, in that order: the playbook's Best 25 reads `pages.playbook.best_25` and its 25-week table `pages.playbook.span_ids` (Best 25 + Book). Without the list the table falls back to its sort and limit. Grep the template for `"from"` to see which ids a page reads; a missing id drops that block. A table with `extra_from: courses` shows the Course / Grade / Horizon / Reason columns it lists. Pages without a template still use a hand spec (below).

## Hand spec (pages without a template)

1. Write the spec in the run folder, or `logs/canvases/YYYYMMDD/` outside a run: `<name>-YYYYMMDD.json` (scan day in the name, same stems as before: `daily-focus-`, `movers-`, `movers-3m-`, `book-risk-`, `playbook-`, `horizon-plan-`, `capture-`, `wisdom-`, `analysis-style-`, `post-earn-`, `premarket-book-`, `entry-book-`, `growth-radar-` …).
2. Render: `python src/generic_utils/tv_scan_cli.py page --spec <folder>/<name>.json` → `<name>.html` beside it, and `logs/canvases/index.html` (every day → run → page, newest first).
3. Read `warnings` in the printed payload. Fix each one in the same turn (add a page Ledger row, or rename the header to a standing key). `--strict` fails on any warning.
4. Chat: short snapshot plus the HTML path as a markdown link. Do not end a turn with "writing the page next".

Spec shape is the docstring of `src/generic_utils/canvas_page.py`. Blocks: `stats`, `callout`, `h2`, `h3`, `text`, `list`, `table`. Tones: `good`, `bad`, `warning`, `info`, `neutral`.

**Do not retype data rows.** A name table points at the file the CLI already wrote, and the spec only adds judgment:

```json
{"type": "table", "title": "Build-50",
 "source": {"duckdb": "PACK/briefing_pack.duckdb", "sql": "SELECT symbol, ind, \"left\", rr, bo, rsi, rng, vs50, peer, val_vs_ind FROM radar_curated_50"},
 "sort": "-rr", "limit": 50,
 "extra": {"NASDAQ:MU": {"Course": "ADD", "Reason": "live leftover, rng 32"}},
 "extra_columns": ["Course", "Reason"], "tone_field": "Course", "tone_map": {"ADD": "good", "EXIT": "bad", "TRIM": "warning"}}
```

Sources: `{"csv": PATH}`, `{"duckdb": PATH, "sql": SQL}` (quote `"left"`), `{"pack": "briefing_pack.json", "key": "sleeves.radar_curated_50"}`. `where` is the `predicates` where-string. `columns` entries are a field or `[field, header]`. `decimals` defaults to 2 (`-1` keeps raw text). `derive: ["left_carrier"]` adds a column from a generic_utils formula (registry `DERIVED_COLUMNS` in `canvas_page.py`; add new ones there, never as a SQL CASE). Short hand tables (Plan, Book actions) can be literal `headers` + `rows`. A Reason cell is judgment and is written by hand.

## Ledger rules (required)

1. **Exactly one Ledger tab.** The renderer adds it last. A tab labelled Ledger in `tabs` is an error.
2. **Standing meanings live in `config/generic_utils/canvas_ledger.json`** (≈150 entries: leftover/targets, valuation/peer, tape, entry band/kill, book risk, path classes, Mesh, action words). Do not restate them in the spec. `ledger.auto` (default on) pulls the entry for every table header and every coded cell value (ALL_CAPS tokens such as ABOVE, DISCOUNT, OPENING, HOLD_NO_ADD). `ledger.standard` adds named keys that only appear in prose or callouts.
3. **Page-only codes** go in `ledger.entries` (`[{"group": "Page", "rows": [["Δbo", "Change in weeks bo vs the prior pred run."]]}]`). A page entry with the same key overrides the standing one for that page.
4. **A code is not explained until its values are.** A coded column needs the field row (allowed characters, letter order, empty value) and one row per value that can appear, with the rule that turns it on and what absence means. A count or filter built from a code (`nmesh`, `Mesh 3+`) is its own row: the count of letters and the stated threshold. Not a 0–100.
5. No local glossary on other tabs. "See the header", "the letters above", "same as the other canvas" are defects. A header may name the column; the definition lives only on Ledger.
6. **A bin label is not the size of the gap.** Any table that shows `ABOVE`/`BELOW`, `DISCOUNT`/`PREMIUM`/`INLINE`, or `UNUSED`/`MID`/`PAID` also shows the percent (or the 0–100 range position) in the next column: `vs50` beside the 50-day label, `val_vs_ind` (and `val_vs_avg`) beside Peer, `rng` beside the range label. Width is fine.
7. If a new standing abbreviation will recur across pages, add it to `canvas_ledger.json` (key, aliases, group, meaning) instead of repeating it in page entries. Do not invent a second definition of an existing key.

Do not skip Ledger because "the operator already knows rr".

## Mesh (the pattern for coded fields)

`Mesh` is a string of letters `F C P D T` (only the ones that are on, in that order; `-` = none), not a score. Its field row, the five letter rules, and `nmesh` / `Mesh 3+` are in the standing ledger under group `Mesh`. Copy that shape when a page invents a similar column. Do not invent a second on/off rule for those letters.

## Layout

- Ledger groups are the standing groups plus `Page`. No empty groups.
- Chart captions and stat labels still name the metric and units. The Ledger does not replace axis labels.
- The page is for decisions plus the tables the paste asked for. Name tables only when the paste says `data lists` (Wisdom excepted, see forward-value).

Daily operator pages: also read `.claude/skills/daily-operator-scan/SKILL.md`.
