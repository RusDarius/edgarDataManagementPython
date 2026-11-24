# Patch 0003: decision journal, rotation pairs, peer-value fit quality, and pruning for the day files and flow runs

| | |
|---|---|
| Date | 2026-11-24 |
| Commit | pending; base `bb7e9f3` |
| Change set | Working tree vs HEAD before the note: 44 files, +5,900 / -54 (15 added, 29 modified, none deleted), including patch 0002's commit row and the regenerated files |
| Scan | `logs/patch_notes/20261124_1026_working-tree-vs-head/inventory.md` (first pass `20261124_1018_...`: one lint finding, fixed) |
| Tests | 954 passed, 8 skipped without the two baseline B6 files; B6 `test_scan_period_close_forward_predictor_tracking` still fails (DuckDB `AVG` binder error, unchanged) |

## Summary

Every session now leaves a small record, the decision journal. It holds the regime, a snapshot of every covered name, each call when it changes (with its reason) and dated OUTSIDE notes. `journal review` measures what each call and each covered name did next, from 1 day to 6 months, as excess over the median store name. The panel store gains 91-day and 182-day forward labels for every reader.

`rotation-pairs` shows which baskets move against each other once the market's step is taken out, and what followed past extremes of each pair. It appears on the lane book's new Pairs tab. The lane book also gains Top tier and Breakeven tabs, the deterioration page a Recovery clock tab, the print brief a Holders & flow tab, and the movers Plan tab two judgment tables.

Peer value now gives a gap for every name with a premium, including the 112 that had none: thin industries borrow their sector's slopes. Fits that do not beat the plain peer median out of sample are no longer used, and growth and quality can no longer lower a multiple.

Two maintenance tools cap stored runs at three per day:
- the all-fields day files, at most 2 of the 3 full-catalog (applied to every day before 24 Nov: 31 days, 68 runs, 58.8 → 28.9 GB);
- the edge, market-timing and financial-projection run folders (applied: 19 runs, 1.12 GB).

## What changed for the operator

### Decision journal (generic_utils.journal, `run_journal.py`, skill `journal`)

- **Question.** How did past calls and covered names play, what was decided on a name and why, and what did names in the same state do before?
- **Commands.**
  - `python src/run_journal.py` records the newest pack with the day's judgment, reviews it and renders `journal-YYYYMMDD`.
  - Flags: `--backfill` (first time), `--history IDS`, `--note IDS --text ...`.
  - The CLI is `tv_scan_cli.py journal record|note|backfill|review|history|stats|compact`.
  - `priors` records the judgment automatically (`--no-journal` turns that off).
  - The optional refresh suite `journal` runs `record --review` after board, sentiment and lane_book.
- **Store.** `logs/tradingview_analysis/journal/journal.duckdb` (recipe `config/generic_utils/journal.json`) has five tables:
  - `sessions`: one per pack, with the regime from `PACK/sentiment`, live patterns and alpha states;
  - `snaps`: one row per covered name per day, from the board's `coverage.board_where` plus named ids and the Book, falling back to pack names on older packs;
  - `calls`: written only when the course, grade or capital-plan row changes;
  - `texts`: each reason stored once;
  - `notes`: deduplicated by hash.
- **Review.** `review` builds a temporary `outcomes` table on the panel store:
  - raw return, excess over the median store name (`fx`) and signed excess (`sfx` = sign × fx, sign from `course_classes`);
  - path low and high;
  - kill / band / target touches on closes.
  - It writes `PACK/journal`: `scorecard`, `by_grade`, `studies`, `similar_now`, `timeline`, `latest`, `notes`, `sessions`, `storage`.
- **Size.** On 24 Nov the file is 9.8 MB: 121 sessions, 17,980 snaps (2 Sep to 23 Nov, 50 days), 1,576 calls, 1,555 texts, 80 notes. There are no bare ids in calls or notes. The refresh step takes 2.5–2.9 s.
- **Limits.**
  - Entry is the scan price, which can be intraday.
  - Overlapping windows on one name repeat, so read `n_names` / `n_days` beside `n`.
  - Labels at 91 and 182 days exist only for store days up to 24 Aug and 22 May.

### Rotation pairs (generic_utils.rotation_pairs, lane-book skill)

- **Question.** What moves against what once the market's step is out, where does each pair sit today, and what followed past extremes?
- **Command.** `tv_scan_cli.py rotation-pairs --pack PACK --line` writes `PACK/rotation_pairs`: `baskets`, `pairs`, `ind_pairs`, `series` and `highlights.log`. Recipe `config/generic_utils/rotation_pairs.json`.
- **Flow.** It runs as an optional refresh suite (2.4–2.5 s). The lane book's Pairs tab requires its DuckDB.
- **Limits.**
  - The store covers 158 steps (29 Mar to 23 Nov).
  - Tails are in-sample percentiles, and `nind` (independent starts) is often 1–3.
  - A missing basket step counts as 0 and is reported in `missing_steps`.

### Peer value: fit quality (generic_utils.peer_value; recipe keys `ridge_per_name`, `expect_positive`, `min_loo_r2`)

- **Why.** NYSE:HPE and NYSE:DELL had a `peer_prem_pct` but an empty `peer_gap_pct` on the 23 Nov brief. Their data was complete. Two things were wrong with the fit:
  - Their industries had fewer than `min_fit_n` names with a positive yield.
  - The sector fallback group held only the leftover names, with n = 1.
- **Sector fallback.** For each lens, the level and median come from the industry. When the industry has fewer than `min_fit_n` names with a positive yield, the slopes come from every sector name with one, shifted to the industry's level.
  - Sector groups are rebuilt from every sector name.
  - Per name: `<lens>_fit_on` (group / sector) and `<lens>_fit_n`.
- **Shrinkage.** The penalty is `ridge + ridge_per_name × n` (0.5 per name).
- **Sign constraints.** A driver in `expect_positive` (rev_nfy_g, eps_nfy_g, opm, roe, fcf_margin) whose coefficient comes out negative is dropped, and the fit is rerun (`signed_ridge_fit`; the group row lists `sign_dropped`).
- **Quality gate.** `fit_loo_r2` is the hat-matrix leave-one-out R² against the median of the other peers. A fit at or under `min_loo_r2` (0.0) is not used: `fit_used` = 0, `just_pct` = 0 and `gap_pct` = the plain discount.
- **Where it shows.**
  - The board joins `peer_fit_on`, `peer_fit_loo_r2` and `peer_fit_used` (Peers column group).
  - The brief's Earnings table shows `peer_gap_pct`, `peer_fit_on` and `peer_fit_loo_r2`.
  - The Ledger has `fit_on`, `fit_loo_r2` and `fit_used`.
  - The forward-value skill says how to read them.
- **Numbers that change.** Every `just_pct`, `gap_pct` and `px_fit`. On the 23 Nov pack (see Review R1, R2):
  - NYSE:DELL: prem +3.3, just −28.5, gap −30.8 (sector fit, n 165, LOO 0.035);
  - NYSE:HPE: gap −5.8;
  - NYSE:KR: gap −11.1.
  - 1,924 of the board's 1,924 names with a premium now have a gap; 90 are sector-fitted; 606 have `fit_used` = 0.

### Page tabs (templates and Ledger)

- **lane_book.**
  - Top tier: the judgment's `top_picks` plus `PACK/analysis_scans/top_tier.csv`, five 0/1 facets with `n_facets` as a count.
  - Breakeven: the recovery ladder, the recovery clock, and the stake in each top-tier name that earns $1,000 at its month target.
  - Pairs: from rotation_pairs.
- **deterioration.** Recovery clock tab (requires `PACK/recovery_clock`). The recovery clock recipe adds two studies and live cells: drawdown × count × 12-month run, and drawdown × count × 1-month direction (`crun`, `cdir`).
- **print_brief.** Holders & flow tab: the judgment's `holders` plus `PACK/analysis_scans/reporter_flow.csv` (optional).
- **movers Plan tab.** Opportunity cost (`plan_4`) and decision calendar (`plan_5`), both judgment tables and both optional.
- **journal page.** Six tabs: Scorecard, Lists and regimes, Similar now, Calls, Notes, Sessions and storage. It is in `templates_all`, so on demand.
- **Ledger.** About 70 new keys, for the journal, recovery clock, lane book top tier / breakeven and the peer fit.

### All-fields day-file prune (`data_analysis_scripts/trading_view_all_fields_prune_runs.py` + `_validate.py`)

- **Rule.** At most `keep` (3) runs per day, of which at most `max_full` (2) are full-catalog runs. The newest run and the newest full run are always kept. With `strategy=spread` (the default), the last focused run of each other US session phase is kept.
- **Safety.**
  - It skips today, locked days and days with a `.wal`.
  - It rewrites into a temp copy, checks row counts, re-exports `parquet/` and removes the dropped `runs/<id>/`.
  - It logs to `_prune_log.jsonl`.
  - The validator checks dropped runs are gone, kept runs are byte-identical and the manifest matches, and can roll back.
- **Flags.** `--estimate` / `--measure` write nothing; `--keep-backup` / `--drop-backups` / `--rollback` handle backups.
- **Applied in this session.** 30 days and 59 runs were removed, 57.7 → 28.7 GB, with 0 validation failures. A second pass on 24 Nov pruned 23 Nov (12 focused runs → 3: last pre-market, last cash, newest; 1.06 → 0.22 GB, 40/40 checks).
  - The backups (25 + 1, 55 GB) were dropped on the operator's go-ahead once validation passed.
  - Afterwards no day before today is over 3 runs or 2 full runs; the folder is 374 GB (from 426 GB).
  - The empty `06_04_2026` day folder (0 rows in every table, an empty run folder from a failed June backfill) was deleted.
  - Six 23 Nov briefing packs name a focused run that is now gone in `sources.all_fields_run_id`. Each pack keeps its own copy of the data. Only an explicit re-read of that run id (`compare --run-a`, `session --prior-run`) would miss it. This is true for packs on every pruned day.
  - `main.py` carries the call commented out.

### Flow-run prune (`data_analysis_scripts/flow_runs_prune.py`)

- **Rule.** Max `keep` (3) run folders per scan day for `edge` (`edge_research_tools/runs/edge_*`, day from the manifest `scan_day`), `mtp` and `finproj` (`financial_projection/<DD_MM_YYYY>/<kind>_<stamp>Z`).
- **Always kept.**
  - The runs each reader picks: backtests timeline per scan day, newest edge by mtime, newest finproj per prefix.
  - Runs the newest briefing pack names in `sources`.
  - The newest run per kind.
  - Runs younger than an hour.
- **Safety.**
  - A dry run unless `--apply`.
  - `--keep-backup` / `--drop-backups` handle backups.
  - The validator checks that kept files are unchanged by size and mtime, and that readers resolve the same runs.
  - Logs go to `logs/tradingview_analysis/_flow_runs_prune_log.jsonl`.
- **Applied on 24 Nov.** 19 runs, 1.12 GB: edge 5 (0.70 GB), mtp 12 (0.13 GB), finproj 2 (0.29 GB).
  - It ran with `--keep-backup`; each flow passed 5/5 checks, and then the backups were dropped.
  - A dry run afterwards finds 0 days over 3.
  - Two unreadable edge runs from 27 Jul (`edge_latest_500m_full_parent_20260727_1501_utc_*`) are left alone.

### Operator records in this change set (not reviewed as code)

- `config/holdings_scoring/current_holdings.json` (the Book update).
- `config/generic_utils/sentiment_outside.json`: OUTSIDE readings dated 4, 11, 19, 20 and 23 Nov.

## Feature changes

| feature | change | parts | what a user of it must know |
|---|---|---|---|
| `journal` | added | module, recipe, page, tests, CLI `journal`, refresh suite `journal`, skill `journal`, CLAUDE.md | One DuckDB of sessions / snaps / changed calls / notes; `review` measures 1d–182d excess; `priors` records automatically |
| `run_journal` | added | runner | Record + review + page in one command; `--backfill` the first time |
| `rotation_pairs` | added | module, recipe, tests, CLI `rotation-pairs`, refresh suite, lane-book skill | Baskets and pairs on the store with the market's step out; read `nind` before a tail mean |
| `peer_value` | updated (changed numbers) | module, recipe, tests | Sector-slope fallback, shrinkage, sign constraints, LOO gate; new `fit_on` / `fit_n` / `fit_loo_r2` / `fit_used`; every `gap_pct` / `just_pct` / `px_fit` moves |
| `opportunity_board`, `(page) brief` | updated | recipe, page | `peer_fit_on`, `peer_fit_loo_r2`, `peer_fit_used` joined; the brief's Earnings table shows the fit beside `peer_gap_pct` |
| `panel_store` | updated | recipe | Labels `fwd_91d`, `fwd_182d` (63 / 126 files) |
| `recovery_clock` | updated | recipe | Studies and live cells `crun` (drawdown × count × 12m run), `cdir` (× 1m direction) |
| `(page) lane_book` | updated | page | Top tier, Breakeven and Pairs tabs; the scan-fed tables are optional |
| `(page) deterioration` | updated | page | Recovery clock tab |
| `(page) print_brief` | updated | page | Holders & flow tab |
| `(page) movers` | updated | page | Plan tab: opportunity cost and decision calendar (`plan_4`, `plan_5`) |
| `(page) journal` | added | page | On demand (`templates_all`) |
| `page_update` | updated | module | UPDATE replays recorded scans whose `after` names a refresh suite (`replay_steps`); before, the DAG rejected the unknown step |
| `catalyst_outlook` | updated | module | `pack_list_tags` (list form); `pack_lists` unchanged |
| `symbol_identity` | updated | module | `qualify_text` never reads a one-letter word as a ticker ("A beat", letters E S), even when NYSE:A or NYSE:C is judged |
| `run_daily_refresh` | updated | recipe | Optional suites `rotation_pairs`, `journal`; `journal` in `templates_all` |
| All-fields day-file prune | added | 2 scripts, 2 test files, `main.py` (commented) | Max 3 runs per day, max 2 full; validated, with backups; applied to 30 days |
| Flow-run prune | added | script, test file | Max 3 runs per scan day for edge / mtp / finproj; reader picks protected; applied 24 Nov (1.12 GB) |
| Ledger | updated | `canvas_ledger.json` | About 70 keys (journal, recovery clock, lane book, peer fit) |

**Deprecated or removed in this patch: nothing.** No `lifecycle.json` entry.

## Technical changes

| area | added | modified | deleted | lines |
|---|---|---|---|---|
| generic_utils | 2 | 4 | 0 | +1,344 / -16 |
| data_analysis_scripts | 3 | 0 | 0 | +1,360 / -0 |
| page templates | 1 | 5 | 0 | +1,002 / -4 |
| Ledger | 0 | 1 | 0 | +717 / -3 |
| tests | 5 | 3 | 0 | +682 / -0 |
| recipes | 2 | 6 | 0 | +440 / -5 |
| CLI (tv_scan_cli) | 0 | 1 | 0 | +187 / -0 |
| runners | 1 | 0 | 0 | +70 / -0 |
| skills | 1 | 2 | 0 | +58 / -1 |
| patch notes | 0 | 4 | 0 | +28 / -21 |
| Book config, main.py, CLAUDE.md | 0 | 3 | 0 | +12 / -4 |

- **`peer_value.py`**
  - New `signed_ridge_fit(y, x, lam, positive)` returns `a`, `b`, `fitted`, `r2`, `loo_r2` and `dropped`.
  - The `score_day` lens loop now pools by sector, caches fits per `(fit_on, lens)` and shifts sector fits to the industry level.
  - The primary-lens copy keys add `fit_on`, `fit_n`, `fit_loo_r2` and `fit_used`.
  - `write_peer_value` notes are updated.
- **`tv_scan_cli.py`**
  - `_cmd_rotation_pairs`, `_cmd_journal`, `_journal_record` (calls go under the pack the judgment was reviewed on) and `_qualify_one`.
  - `_cmd_priors` records the journal unless `--no-journal`.
- **`journal.py`.** The schema grows by `ALTER` when the recipe adds snap columns. Snap and call inserts are chunked, multi-row `VALUES`. `compact` rewrites the file (DuckDB does not shrink in place).
- **`rotation_pairs.py`.** Steps come from `downside_risk._step_cte` (split-guarded, start-file caps). `_excess` was added in review.
- **`page_update.replay_steps`.** It keeps only the `after` names that are other recorded scans.
- **`daily_refresh.json`.** `rotation_pairs` has `after: []`; `journal` runs after board, sentiment and lane_book. Both are optional.

## Review

Measured on the 23 Nov packs (`briefing_pack_20261123_1742_utc_75009ab2`, `_2123_utc_3c955ebb`) and the panel store as of 23 Nov.

| # | severity | where | finding | status |
|---|---|---|---|---|
| R1 | high | `peer_value.score_day` | 112 names with a premium had no gap (NYSE:HPE, NYSE:DELL on the brief). Fits overfit: median in-sample R² 0.47 against leave-one-out 0.04, and 46% of 311 fits did worse out of sample than the peer median. 44% of the growth / quality slopes were wrong-signed | Fixed: sector-slope fallback, `ridge_per_name`, `expect_positive`, `min_loo_r2`. Every gap is now filled, no slope has the wrong sign, and 606 board names fall back to the plain discount |
| R2 | medium | peer value evidence | On forward returns, 28-day `gap_pct` t was 1.24 before. The fallback alone gives 1.14; with shrinkage and sign constraints it is 0.92–0.97. All are under 2, with about 7 independent periods, so the variants cannot be told apart. The LOO test across 311 fits is the stronger evidence | Open. The checks were kept on as asked; `ridge_per_name: 0` and `expect_positive: []` return to the fallback alone |
| R3 | medium | `pages/lane_book.json` Breakeven tab | Two tables read `analysis_scans/top_tier.csv` and `recovery_clock.duckdb`, but the tab requires only the recovery ladder. On a pack without them (the 23 Nov 21:23 update pack) `--strict` fails | Fixed: both tables `optional` |
| R4 | medium | TradingView TTM fields / panel store | `free_cash_flow_ttm` (often `return_on_equity` too) goes blank for 1–3 weeks after a print (MRK, ABT, BLK, MS, C, late Oct to 9 Nov). Store FCF coverage falls from 94% to 85% in earnings season. The `_fy` fields stay filled but are not on the focused list. Banks and semiannual ADRs (HSBC, MUFG) never get TTM FCF | Open (follow-up 1) |
| R5 | medium | move-prediction profiles | `price_earnings_forward_fy` is empty on every row (all-fields full and focused, move-prediction 0 of 15,095 this week). `quality_growth_at_reasonable_price_v1` (1.4) and `durable_value_compounder_v1` (1.25) still weight it; missing signals are skipped and the weights renormalised | Open (follow-up 2) |
| R6 | low | `journal.json` `snap_fields.text.peer` | It reads `peer` from board rows, which have no such column, so it was empty on all 442 snaps on 23 Nov. The pack's DISCOUNT / PREMIUM label is lost; `peer_gap_pct` is stored | Open (follow-up 4) |
| R7 | low | `journal._duck_rows`, `_qualify_one` | Broad `except` (9 in journal, 3 in the CLI). A renamed sentiment or alpha column leaves the regime fields empty without a message. `_qualify_one` returns the bare ticker on any error, so a note can land under a bare id. On 24 Nov there were 0 bare ids in calls / notes | Open |
| R8 | low | `journal.requalify` | `UPDATE calls SET symbol` can break the `(session_id, symbol)` key when one session holds both the bare and the full id; the backfill would then stop at the end | Open, not seen |
| R9 | low | `journal.compact` | Unlinks the file before renaming the copy in, so a failure between the two loses the journal | Open (copy the file first) |
| R10 | low | `rotation_pairs` `ind_pairs.xs_21_*` | `(window_return(...) or 0)` turned a missing return into 0% | Fixed (`_excess`); no row changed on today's store |
| R11 | low | `rotation_pairs` imports | `_lit` and `_step_cte` are underscore helpers of `downside_risk` (baseline B9) | Open |
| R12 | low | `peer_value` runtime | 4.0–5.1 s on the 23 Nov refreshes, 6.8–7.0 s now (LOO and sign refits). The step runs before earnings_week | Accepted |
| R13 | low | prune tools | No line in `CLAUDE.md`, `run_catalog.txt` or a skill; only `main.py` (commented) names the day-file prune | Open (follow-up 5) |
| R14 | low | `flow_runs_prune.py:35` | Unused `timedelta` import (scan lint) | Fixed |
| R15 | note | `patch_scan` feature map | The `journal` subcommand shows under `generic_utils`, and `run_journal` has no test file; same pairing limit as 0002 R9 | Open, tool only |

Not reviewed:
- the operator's records (Book config, outside readings);
- the Ledger texts one by one (the strict renders below found no missing key);
- page renders in a browser.

## Tests and checks

- `python -m pytest src/tests -q` without the two baseline B6 files: 954 passed, 8 skipped (125 s). With them, B6 `test_scan_period_close_forward_predictor_tracking` fails as before.
- Touched files: `test_peer_value` (6, of which 2 new), `test_journal` (4), `test_rotation_pairs` (2), `test_flow_runs_prune` (4), `test_all_fields_prune_runs` (10), `test_all_fields_prune_validate` (7), `test_daily_refresh` (`replay_steps`), `test_operator_tooling` (one-letter ids), `test_opportunity_board`: 56 passed.
- Strict renders (`--strict --no-index` into the scratchpad) of journal, lane_book, print_brief, deterioration, movers, brief and board on 23 Nov with the day's judgment: 0 warnings. lane_book also renders on 20 Nov.
- pyflakes is clean on the new modules after the R14 fix. The scan found no guard issue and nothing removed.
- Not run: a producer, a compile, or a full refresh. The peer-value and rotation-pairs steps were run into the scratchpad only. The brief and board for 23 Nov 17:57 were re-rendered earlier in this change set with the new peer columns.

## Follow-ups

1. **TTM gap fill in the panel store (R4).** Carry the last TTM `fcf_ttm` / `roe` / `opm` forward through a post-print blank (with an age column), then fall back to the `_fy` fields. Add `free_cash_flow_fy`, `return_on_equity_fy` and `operating_margin_fy` to `FOCUSED_REQUIRED_FIELDS`. Measure FCF coverage across the next earnings season.
2. **Dead forward P/E (R5).** Move the weight in the two profiles to a field that is filled (forward EPS / close, or `earnings_per_share_forecast_next_fy`), and drop it from the backtracking config. Ranks change, so state it the way 0002 did for the dividend field.
3. **Peer joins.** deterioration, earnings_week and pathway read `peer_gap_pct` from the next refresh but do not carry `peer_fit_on` / `peer_fit_loo_r2`. Add them where `peer_gap_pct` is shown.
4. **Journal `peer` snap (R6).** Map it to a board field or drop it. Also merge pack-name fields for board-sourced snaps if more pack-only reads are wanted.
5. **Wording for the prune tools (R13).** Add a CLAUDE.md line and a `run_catalog.txt` entry for `trading_view_all_fields_prune_runs.py` (`--estimate`, `--apply`, `--drop-backups`) and `flow_runs_prune.py` (dry run, `--apply`).
6. **Prunes are manual.** Backups were dropped, the flow prune was applied and `06_04_2026` was deleted (24 Nov). Neither tool is in the refresh graph, so new days pile up runs again (23 Nov had 12). Run both after a busy day, or wire them as an optional post-refresh step; that step must skip today.
7. After the commit, put its hash in the Commit row and run `patch_scan.py regen`.
