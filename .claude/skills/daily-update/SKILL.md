---
name: daily-update
description: UPDATE mode for today's operator pages (daily-focus, movers, playbook) - refresh the data, keep the day's judgment, and review only the names the delta report flags. Use when the user asks to update or refresh today's plan, brief, canvas or pages with the latest data, or pastes DAILY (update). Not a first Daily of the day (that is daily-operator-scan).
---

# Daily update (latest data, same judgment)

**Canvas = operator HTML page** rendered from a template + the day's judgment file (`.claude/skills/canvas-ledger/SKILL.md`). Not a Cursor `.canvas.tsx`.

The first Daily of the day wrote `logs/canvases/YYYYMMDD/judgment.json`. Each update gets its own run folder `logs/canvases/YYYYMMDD/HHMM_update/` (RUN_DIR below; `run_operator_day.py --agent` passes it in the prompt, `run_daily_refresh.py` prints it as `update.run_dir`). This mode keeps it, refreshes the data, and asks you to look only at what moved. Do not rebuild pages, re-rank Build-50, reread the whole pack, or rewrite unflagged reasons.

## Steps

1. `python src/run_daily_refresh.py --update-day YYYYMMDD`
   Producers → compile → suite dumps (parallel graph) → the judgment's capital plans and recorded scans replayed on the new pack → pages re-rendered with the existing judgment and an UPDATE banner → `RUN_DIR/update.md` (+ `update.json`, `judgment.prev.json`).
   Data already fresh: add `--no-producers`. Only re-diff and re-render: `--no-producers --no-compile --no-suites`.
2. Read `RUN_DIR/update.md`. That is the whole input. If a regime check is needed, read only the Regime block at the top of the new `briefing_pack.md` (line limit 40). Per-name detail: `example_entry.py stance SYM --brief`.
3. For each name under **Names to review**, keep or change `courses[symbol]` (Course, Grade, Reason, Horizon) in the judgment file:
   - `THROUGH_KILL`: that entry is finished. A NEW / WAIT becomes PASS, or a new band from entry-book on the new pack. A held line goes through the polar and thesis-kill rules (prior ADD/NEW cannot become EXIT without `thesis_kill`).
   - `band inside->above_band`: not a new fill; WAIT for the band. `->below_band` on a reclaim name: still WAIT.
   - Label flips (`up_class`, `down_class`, `peer`, `mix`, `mtp`, `mesh`): re-grade and cite the new value.
   - `close` / `left` / `rr` / `bo` / `rsi` moves: change the Reason only if the course or grade changes.
   - **List moves** `No course yet`: give a course only if the name would take capital; otherwise leave it blank.
   Budget, polar, MTP-is-climate, ~$5,000 starter / ~$1,000 add all still bind.
4. Edit only page blocks whose numbers moved (`pages.<page>.blocks.<id>`: Plan stats, actions table, callouts). Replace `update.banner` with a one-line reviewed note (tone `info`) and set `reviewed_pack` to `pack`.
5. If a course changed capital, edit `capital_plan.capital_plan` / `capital_plan_queue` rows in the judgment, then `python src/run_daily_refresh.py --no-producers --no-compile --no-suites --update-day YYYYMMDD --run-dir RUN_DIR` (replays capital-plan, re-renders into the same run). Otherwise re-render only:
   `python src/generic_utils/tv_scan_cli.py page --template daily_focus --day YYYYMMDD --judgment logs/canvases/YYYYMMDD/judgment.json --out-dir RUN_DIR --strict` (same for `movers`, `playbook`).
6. Priors: `python src/generic_utils/tv_scan_cli.py priors --day YYYYMMDD` (merges the judgment into the day's file; keys `EXCHANGE:TICKER`).
7. Optional OUTSIDE news: one search on the session or after-hours for flagged names only.
8. Chat: changed names and course flips, cash after (from `capital_plan/summary`), the update.md path, page links. A few lines.

## Do not

- Hand-write page spec JSON or retype table rows (templates own the layout and SQL).
- Read whole skills, `queries.txt`, or the full pack md in this mode.
- Spend a second NEW budget because the update found new names.
