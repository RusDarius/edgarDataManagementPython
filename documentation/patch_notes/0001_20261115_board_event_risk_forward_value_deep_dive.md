# Patch 0001: the board and three daily pages, event risk as a core warning, forward value, deep-dive agent

| | |
|---|---|
| Date | 2026-11-15 (work of 3 to 15 Nov 2026) |
| Commit | pending; base `4896018` |
| Change set | 102 files at the first review (+17,552 / -138; 50 added, 52 modified). The review added and edited files (section "Files the review added or changed"), which were then staged with the rest: about 114 files, +21,500 / -138. Nothing deleted |
| Scan | `logs/patch_notes/20261115_staged/inventory.md` (the first 102 files, as reviewed); `logs/patch_notes/20261116_worktree/inventory.md` (everything, with the feature and removal sections) |
| Tests | 917 passed, 8 skipped (everything, 16 Nov); 3 failures and 4 errors in two older test files, unchanged from the baseline (B6) |

## Summary

The Daily now produces three pages instead of thirteen (a quick **brief**, the **board** with plan and grids, the
**movers** tape); the other pages render on demand. Ten suites join the refresh: an opportunity board that puts every
suite's read on one row per name, event risk (dated events against each line's crowd, with a WARN level that every
other tool now shows), peer value, the beat bar, winners anatomy, the recovery clock, cycle scenarios, the lane book
and market sentiment. Forward value gives each name a fair value today, an IRR against a cost-of-equity hurdle and the
growth its price needs. Ids that TradingView drops or renames are resolved in one place, and scans keep US share
classes and ADRs. A deep-dive agent researches one name, a list or a question from a per-name dossier. Patch notes
start with this file.

The review found two errors in the new forward value model (percent fields read 100 times too large under 2.5 points;
dividends never read) that reach the pack's `fv_*` columns. Both are fixed in the working tree with tests; the stored
value run of 12 Nov and the packs built on it carry the old numbers until the value run is repeated.

## What changed for the operator

### Daily output: one fire, three pages

- `config/generic_utils/daily_refresh.json` `templates` is now `brief`, `board`, `movers`. `templates_all` lists all
  18 pages. The baseline rendered all 13 of its templates every time.
- Deep pages on demand: `tv_scan_cli.py page-all --set all`, or `--templates outlook,print_brief` (print night),
  `deterioration,pathway` (a drawdown), `alpha_model,signal_lab` (a model review).
- `brief-YYYYMMDD`: Highlights, Moves, Book update with cash, Earnings this week with the bar each print faces,
  Projections, a Sentiment tab. `board-YYYYMMDD`: Plan, Catalyst book, Timing, Earnings bar, Safe park, Value, All
  names, Book. Each read lives on one page only.
- `python src/run_operator_day.py --agent --mode baseline`: a session-aware start of day (prompt
  `src/research_prompt/agent_runs/daily_baseline.txt`) that fills the three default pages; `full` still fills every page.
- Grids: screens now overlap. A click adds a screen and keeps rows that pass every active one; a screen in the same
  `group` (or on the same column) replaces the one it clashes with; Ctrl+click overlaps anyway; Shift+click isolates;
  each button shows the rows a click would leave.

### Opportunity board

- Question: which names have several independent reads pointing the same way, without a composite?
- Run: `tv_scan_cli.py board --pack PACK --ids A,B --line` (refresh suite `board`, last in the chain).
- Reads `PACK/outlook` names and joins peer value, deterioration, the beat bar, forward value, anatomy letters, store
  beta, event risk. Adds only positions: `band_state` (close against the bounce band), `to_band_high_pct`,
  `kill_dist_pct`, the beat-bar cell split per axis. Writes `PACK/board/board.duckdb` (`names`, `groups`).
- Page `board`: the Plan and Catalyst book tabs come from the judgment (`pages.board`), the grids from the data.
  On-demand pages built on it: `focus_book` (highest-margin book for two weeks and the month) and `lane_book`.

### Event risk and the core warning field

- Question: which Book lines can a dated event (a vote, a policy step, a macro print) take down hard, how many
  dollars is that, and has an event already repriced the line's crowd?
- Run: `tv_scan_cli.py event-risk --pack PACK --ids A,B --line` (`--as-of D` replays the store point-in-time). Skill
  `event-risk`.
- Each US $2B+ name, Book line and named id gets a crowd (its 15 most-correlated names on the store), the crowd's
  moves and z, a stress (the worse of the crowd's worst 5-step move times down-beta and the line's own worst 5 steps;
  dollars and % NAV on held lines), the calendar events that touch it, letters `EPRUSBTVD$` and a level WARN_STRONG /
  WARN / WATCH from fixed rules. A level is a review trigger, never a size.
- **Core field:** the suite runs right after `movers` and `book_risk`. Deterioration, earnings week, outlook, pathway
  and the board join `warn_level`, `er_letters`, `stress_5s_usd`, `ev_next`, `ev_recent_z`. `example_entry.py stance
  SYM --line` ends a flagged name with `warn=WARN_STRONG RBTV$ $-1616 (-1.13% NAV) evz -4.09`; an unflagged name
  costs no bytes. UPDATE flags a held line's level change (`warn A->B`).
- Read `PACK/event_risk/highlights.log` (about 5 KB), never `names.csv` (about 780 KB).
- **Renamed column:** `letters` is `er_letters` since 4 Nov. Readers fall back to `letters` on older packs.

### Forward value, cycle scenarios, peer value

- **Forward value** (`python src/run_financial_projection.py --mode value`; the script's default run writes it too):
  a `finvalue_*` run values every all-fields name over at most five years. Year 1 is the street's revenue and EPS;
  growth and margins decay toward the industry; a year-5 exit P/E (EV / sales for loss makers); dividends; discounted
  at a beta-based cost of equity. The pack joins `fv_bear_px` / `fv_base_px` / `fv_bull_px` / `fv_exp_px`,
  `fv_upside_pct`, `irr_exp_pct` against `hurdle_pct`, `priced_in_cagr_pct`, `growth_gap_pp`, `fv_lens`, `fv_flags`.
  **Changed default:** when a value run exists, `pe_fwd` / `fp_pe_y1` / `fp_pe_y5` come from its street-anchored
  path (`pe_fwd_source = fp_street`) instead of the growth-lane path. Guide: `documentation/financial_projection_usage.md`
  section V. It is not in the refresh graph (finding R3).
- **Cycle scenarios** (`tv_scan_cli.py cycle --pack PACK --ids A,B --line`, suite `cycle` on the AI themes): what a
  price assumes about a boom ending. `drop_in_k<k>` = the EPS bust the price already pays for after k more boom years;
  `pe_need_k<k>_d<d>` = the exit multiple it needs. Reads the newest value run.
- **Peer value** (`tv_scan_cli.py peer-value --pack PACK --ids A,B --line`, suite after `movers`): each name's
  multiple against its industry: `prem_pct` to the peer median, `just_pct` = the premium its growth and quality earn in
  the industry's ridge fit, `gap_pct` = fitted against actual (positive = cheaper than peers with the same profile),
  the five nearest peers, what each industry pays per driver, and whether the gap paid on the store. Joined by
  deterioration, pathway, earnings week and the board as `peer_*`.

### Earnings: the beat bar, print-night tails

- **Beat bar** (`tv_scan_cli.py beat-bar --pack PACK --line`): for every reporter of this week and next, what a beat
  paid and a miss cost on past prints set up the same way (terciles of run-up, 3-month tape, 3-month EPS revision),
  the surprise the set-up needed, the name's own beat record. Never a gate.
- **Print night** gains tail measures (`u10` / `d10` = odds of a reaction beyond +10 / -10 points against the market;
  `*_hi` the highest odds among a name's cells and `*_hi_ptr` its pointer) and three size pointers (`own_abs`,
  `mcap`, `rev_yoy`) measured on the tails only. The outlook page's Tonight tab shows them.

### What the store says about movers and recoveries

- **Winners anatomy** (`tv_scan_cli.py anatomy --pack PACK --line`; `--test 'r_1m:lo & pt_up:hi & dte<=28'`): what the
  top and bottom 5% of later movers looked like before the move: single quintile legs, pairs of extreme legs, a
  hold-out on the last window, the catalyst mix, named patterns by month, and today's names with pattern letters
  (`pat_up`, `pat_dn`). Read the month table before a pattern: most up patterns lost in October 2026.
- **Recovery clock** (`tv_scan_cli.py recovery-clock --pack PACK --line`, on demand and in the refresh): every $2B+
  name started weekly in its deterioration state; Kaplan-Meier odds and median days to each rise, half-way and the
  high, the path low first; each Book line's odds to its cost, 50-day and half-way.

### Lane book

- Question: where can the free cash go this month, on lanes with a measured record?
- Run: `tv_scan_cli.py lane-book --pack PACK --ids A,B --line` (suite after `board`); page on demand
  (`page --template lane_book`). Skill `lane-book`.
- A lane is one SQL condition on the store. The same condition gives its record (raw and excess at 7 / 14 / 28 days,
  odds of +10% / -10%, block t, halves, months, AI cluster against the rest) and today's names with the board's band,
  kill and targets. Also value quintile by catalyst timing, industry rotation quadrants, the dated timeline by week,
  the cash above the floor. In-sample, one regime.

### Market sentiment

- Run: `tv_scan_cli.py sentiment --pack PACK --line` (suite `sentiment`); `sentiment-add --kind K --value V --date D
  --source S --link L` appends an OUTSIDE reading. `run_market_timing_policy.py` writes the same read into its run
  folder by default (`--no-sentiment`, `--sentiment-only`).
- One row per store file on the US floor (cap-weighted move, breadth, up-volume share, volume pace scaled for the
  session seen at scan time, distribution and accumulation days, stretch, appetite spreads, print reactions, revision
  breadth, score medians, the MTP ENTER share), each reading measured against the cap index's next 1 / 5 / 10 / 21
  sessions; named volume and breadth patterns. Families count arrows, never a sum. With months of store most
  readings carry no arrow yet.

### Ids that go missing or old

- Cause: TradingView moved Alphabet's primary listing from NASDAQ:GOOG to NASDAQ:GOOGL on 2 Nov 2026; primary-only
  scans lost the held class and the Book total dropped the line.
- Scans (all-fields and move-prediction) now keep US share classes and ADRs (`with_us_secondary_listings`).
- One resolver (`generic_utils.symbol_resolver`): alias file (`config/generic_utils/symbol_aliases.json`, written by
  hand) -> the caller's run -> the newest all-fields run -> the panel store's last row (with `age_days`) -> reported.
  Never a sibling class or another exchange.
- The holdings scorer keeps an unmatched held line with `close_source = symbol_resolver.*` and `match_note =
  not_in_source_run`; compile writes `book_missing` and a pack note for any config line still off the Book.
- `tv_scan_cli.py symbol-check [--ids ...] [--flagged]` lists ids that look old with candidates.

### Deep-dive agent

- Question: a full read on one name, a suite, or a question over them, with outside context checked against the data.
- Run: `deep_dive_analysis_agent("NYSE:DOW")` in `src/main.py`, or `python src/run_deep_dive.py --ids A,B [--theme T]
  [--question Q] [--agent | --print-prompt]`; data only: `tv_scan_cli.py dossier` and `dossier-page --dir DIR
  --strict`. Skill `deep-dive-agent`, prompt `src/research_prompt/agent_runs/deep_dive.txt`.
- The dossier (`generic_utils.name_dossier`, about 3 seconds) gathers per name: the board row and every suite join,
  the store's daily progression, movement anatomy, prints, analog forward paths (nearest US $2B+ name-days on
  day-ranked features, raw and excess, all floor against same industry), evidence directions per horizon (counts),
  live band and kill (the bounce band when the entry book sits above the close), peers, dated events, a research plan.
  The agent writes `dossier.judgment.json`: verdict, band, kill, targets, a SHORT / MID / LONG trajectory classed
  DATA_BACKED / MIXED / NARRATIVE, suite order, the question read.
- Output: `logs/tradingview_analysis/name_dossier/<stamp>_<label>/`. Not the watchlist `deep-dive` page. Verdicts are
  candidates for the Daily's one budget.

### Research plan (no code)

- `documentation/improvements_plans/holder_base_and_flow_plan.md`: who holds a stock and how flows could be tracked
  (13F, N-PORT, Forms 3 / 4 / 5, FINRA short interest), in phases. Nothing is implemented.

### Patch notes (this review)

- `python src/generic_utils/patch_scan.py scan | capabilities | history`, skill `patch-notes`, this folder. See
  [README.md](README.md).

## Feature changes

One row per feature a user of the platform would notice. The scan's full list (15 new modules, 18 updated, 2 new pages
without a module) is in the inventory under "Features touched". Statuses that are not added / updated are also in
[lifecycle.json](lifecycle.json); per-feature dates and patch numbers are in [feature_register.md](feature_register.md).

| Feature | Change | Parts | What a user of it must know |
|---|---|---|---|
| `opportunity_board` | added | module, recipe, tests, cli `board`, refresh step `board` (last), page `board` | The day's summary page; the judgment fills `pages.board` on every Daily and UPDATE |
| `event_risk` | added | module, recipe, tests, cli `event-risk`, step `event_risk`, skill `event-risk` | `warn_level` is a review trigger, never a size. Read `highlights.log`, not `names.csv` |
| `peer_value` | added | module, recipe, tests, cli `peer-value`, step `peer_value`, page `peer_value` (on demand) | Read `gap_pct` beside `prem_pct`. Its vs-group columns come from the value run (R1 / R2) |
| `beat_bar` | added | module, recipe, tests, cli `beat-bar`, step `beat_bar` | Never a gate. In-sample cells; prints treated as independent |
| `winners_anatomy` | added | module, recipe `anatomy.json`, tests, cli `anatomy`, step `anatomy` | Read the month table before a pattern. Slow (R5) |
| `recovery_clock` | added | module, recipe, tests, cli `recovery-clock`, step `recovery_clock` | Kaplan-Meier odds per Book line; one regime |
| `cycle_scenarios` | added | module, recipe, tests, cli `cycle`, step `cycle` | Reads the newest value run, whatever its age (R3) |
| `lane_book` | added | module, recipe, tests, cli `lane-book`, step `lane_book`, page `lane_book` (on demand), skill `lane-book` | In-sample, one regime: base row, t, halves and months before a mean |
| `market_sentiment` | added | module, recipe, tests, cli `sentiment`, `sentiment-add`, step `sentiment`, hook in `run_market_timing_policy.py` | Families count arrows, never a sum. OUTSIDE readings are shown, never measured |
| `symbol_resolver` | added | module, tests, cli `symbol-check`, `config/generic_utils/symbol_aliases.json` (by hand) | The one place a missing or old id is resolved; never a sibling class |
| `name_dossier`, `run_deep_dive` | added | module, recipe, tests, cli `dossier`, `dossier-page`, runner, prompt `deep_dive.txt`, skill `deep-dive-agent` | `deep_dive_analysis_agent(...)` in `main.py` starts a headless agent by default (R14); the CLI is data only |
| `valuation`, `value_suite` | added | modules, recipe `value_v1.json`, test for `valuation`, `run_financial_projection.py --mode value` | Not a refresh step (R3). Numbers before the R1 / R2 fix are wrong; rerun the value run |
| `(page) brief` | added | page template, rendered on every refresh | The quick scan; never repeats the board's plan or the movers tape |
| `(page) focus_book` | added | page template, on demand | Needs `pages.focus_book` in the judgment and the lane scans recorded in `scans` |
| `patch_scan` | added | module, recipe, tests, skill `patch-notes`, this folder | `scan` before a commit; `regen` after writing the note |
| 12 daily pages: `daily_focus`, `playbook`, `gmv`, `wisdom`, `earnings_week`, `alpha_model`, `signal_lab`, `week_plays`, `outlook`, `print_brief`, `deterioration`, `pathway` | on_demand | `daily_refresh.json` `templates` (13 pages to 3), `templates_all` (18) | Their suites still run on every refresh; only the render is by name: `page-all --set all` or `--templates a,b`. The full Daily still renders all |
| `event_risk.letters` (column) | renamed | `er_letters` in `PACK/event_risk` `names` / `book` | Since 4 Nov 2026. `warn_lookup` and `page_update.warn_changes` still read `letters` on older packs; new code reads `er_letters` only |
| `forward_fundamentals` | updated (changed default) | module; Ledger text for `pe_fwd_source` | With a value run present, `pe_fwd` / `fp_pe_y1` / `fp_pe_y5` come from its street path (`pe_fwd_source = fp_street`); 14 new pack keys |
| TradingView scans (`api_tradingview_client`, `trading_view_export_all_tdfields`) | updated (changed default) | `with_us_secondary_listings` | US share classes and ADRs stay in the scans and so in the panel store from 3 Nov 2026 (R4) |
| Holdings scorer, `compile` | updated | `_resolver_fallback_rows`, `_book_missing` | A held line the run does not carry stays on the Book (`close_source = symbol_resolver.*`); the pack reports `book_missing` |
| `example_entry` | updated | `_attach_warn` | `stance` / `lookup --line` end a flagged name with `warn=...`; an unflagged name is unchanged |
| `page_update` (UPDATE) | updated | `warn_changes` | `update.md` flags `warn A->B` on a held line, a capital course or a new WARN_STRONG |
| `print_night` | updated | module, recipe, tests | Tail measures `u10` / `d10`, `*_hi`, `*_hi_ptr`; pointers `own_abs`, `mcap`, `rev_yoy` on the tails |
| `catalyst_outlook`, `deterioration`, `earnings_week`, `pathway` | updated | recipes (`joins`), `catalyst_outlook.py`, page `outlook` | New joined columns `warn_level`, `er_letters`, `stress_5s_usd`, `ev_next`, `ev_recent_z`, `peer_*`; their refresh steps now wait on `event_risk` |
| `canvas_page` (every grid) | updated | page script | Screens overlap; `group` replaces a clashing screen; Ctrl+click overlaps, Shift+click isolates |
| `symbol_identity` | updated | `_QUALIFIED_TOKEN` | A slash-suffixed id (AMEX:TXCP/PB) is one id |
| `tv_scan_cli` | updated | 13 subcommands (69 in total); `page-all --set`; `judgment-skeleton` over `templates_all` | No subcommand or flag was removed |
| `run_operator_day` | updated | `--mode baseline` | Baseline and UPDATE fill three pages; `full` fills every page |
| `run_market_timing_policy` | updated | `--no-sentiment`, `--sentiment-only` | Writes `market_sentiment/` into its run folder by default |
| `run_financial_projection` | updated | `--mode value` | The default script run now also writes a `finvalue_*` run |
| Skills | added 4, updated 7 | `deep-dive-agent`, `event-risk`, `lane-book`, `patch-notes`; `canvas-ledger`, `catalyst-outlook`, `daily-operator-scan`, `daily-update`, `deterioration`, `forward-value`, `signal-lab` | Routing table in `CLAUDE.md` |

**Deprecated or removed in this patch: nothing.** The scan found no deleted file, no removed function, CLI subcommand
or flag, no removed recipe key and no removed refresh step. No removal was requested. Two things changed status without
being removed: the twelve pages above (on demand) and the renamed column.

## Technical changes

The staged set by area (the review's own files are listed further down):

| Area | Added | Modified | Lines |
|---|---|---|---|
| generic_utils (without the two below) | 11 | 6 | +6,186 / -9 |
| page templates | 5 | 2 | +3,846 / -1 |
| Ledger | 0 | 1 | +1,922 / -7 (189 entries added, 6 changed) |
| recipes | 12 | 8 | +1,568 / -12 |
| tests | 12 | 3 | +1,226 (62 new test methods) |
| financial_projection | 2 | 1 | +683 |
| documentation | 1 | 6 | +500 / -1 |
| CLI (`tv_scan_cli.py`) | 0 | 1 | +432 / -4 (13 subcommands; 69 in total) |
| prompts | 2 | 5 | +329 / -4 |
| skills | 3 | 7 | +273 / -3 |
| runners, `main.py` | 1 | 4 | +233 / -10 |
| data_loaders, data_analysis_scripts, operator_briefing | 0 | 5 | +178 / -8 |
| `config/financial_projection/value_v1.json` | 1 | 0 | +94 |
| page renderer (`canvas_page.py`) | 0 | 1 | +52 / -18 |
| `CLAUDE.md` | 0 | 1 | +27 / -3 |
| Book config (`current_holdings.json`) | 0 | 1 | +3 / -58 |
| **Total** | **50** | **52** | **+17,552 / -138** |

**New modules.** `financial_projection/valuation.py` (pure math: `value_inputs`, `street_growth`, `year1_eps`,
`industry_medians`, `value_scenario`, `priced_in_cagr`, `value_name`, `value_universe`), `value_suite.py`
(`run_value_projection_suite`); in `generic_utils`: `event_risk.py`, `name_dossier.py`, `winners_anatomy.py`,
`lane_book.py`, `market_sentiment.py`, `recovery_clock.py`, `peer_value.py`, `beat_bar.py`, `cycle_scenarios.py`,
`symbol_resolver.py`, `opportunity_board.py`; `src/run_deep_dive.py`. Each follows the six-part shape of baseline
section 9 (the Wiring table in the scan shows the parts).

**Behaviour changes in existing code.**

| Where | Change |
|---|---|
| `data_loaders/api_tradingview_client.py` `with_us_secondary_listings`, `_run_move_prediction_scan`; `trading_view_export_all_tdfields._build_scan_payload` | The `is_primary` filter becomes "primary, or a US-listed common share or depositary receipt". The shared base payload is unchanged. See R4 |
| `generic_utils/forward_fundamentals.py` | `ProjectionContext.value_names`; `load_projection_context(value_db=...)` discovers the newest `finvalue_*`; `attach_forward_fundamentals` sets `fp_pe_y1` / `fp_pe_y5` from the value run and adds 14 pack keys (`fp_eps_y1`, `fp_eps_y5`, `fv_*`, `irr_exp_pct`, `hurdle_pct`, `excess_irr_pp`, `priced_in_cagr_pct`, `growth_gap_pp`) |
| `data_analysis_scripts/trading_view_holdings_scoring_analysis.py` | `_resolver_fallback_rows`; `_build_unmatched_rows(reasons=...)`; hooked into `export_move_prediction_v1_scan` |
| `operator_briefing/compile.py` | `_book_missing`; the pack gains `book_missing` and a `sources.notes` line |
| `operator_briefing/example_entry.py` | `_attach_warn`; brief lines end with `warn=` |
| `generic_utils/page_update.py` | `warn_changes`; UPDATE reviews a level change on a held line, a capital course or any new WARN_STRONG |
| `generic_utils/catalyst_outlook.py`, recipes `deterioration.json`, `earnings_week.json`, `pathway.json`, `catalyst_outlook.json` | Joins to `event_risk` and `peer_value` |
| `generic_utils/print_night.py` | `tail_flags`, `own_abs_move`; recipe `tail_measures` and three pointers |
| `generic_utils/canvas_page.py` | Screens carry `group`; page script `setScreens`, `nextSet`, `combo`, `preview` |
| `generic_utils/symbol_identity.py` | `_QUALIFIED_TOKEN` accepts a slash suffix (AMEX:TXCP/PB is one id) |
| `generic_utils/tv_scan_cli.py` | `_template_set`; `page-all --set`; `judgment-skeleton` covers `templates_all` |
| `run_operator_day.py` | `--mode baseline`; `write_skeleton(page_set=...)` |
| `run_market_timing_policy.py` | `run_market_sentiment`; `sentiment=True` by default |
| `run_financial_projection.py` | `--mode value`, `run_value_projection_from_latest_all_fields`; the default script run also writes a value run |
| `config/generic_utils/daily_refresh.json` | Suites added: `premarket_book`, `beat_bar`, `anatomy`, `recovery_clock`, `cycle`, `peer_value`, `event_risk`, `board`, `lane_book`, `sentiment`; `earnings_week`, `outlook`, `deterioration` wait on `event_risk` (and `earnings_week` on `peer_value`) |
| `config/generic_utils/themes.json` | Eight themes: `ai_adopters`, `policy_midterm_up`, `policy_midterm_down`, `ai_power_build`, `solar_clean`, `memory_storage`, `discount_turn`, `ai_upside_catalyst` |
| `config/generic_utils/catalyst_calendar.json` | Dated rows with `exposure` maps and `odds_kind` for event risk |
| `config/holdings_scoring/current_holdings.json` | The operator's Book update (the file is the record) |

**Suite timings** (refresh of 13 Nov 2026 16:43 UTC, 4 workers, seconds): `anatomy` 79.0, `alpha_model` 54.5,
`signal_lab` 53.2, `gmv` 39.0, `playbook` 32.1, `earnings_week` 17.8, `lane_book` 15.3, `outlook` 14.6, `week_plays`
14.4, `play_review` 14.4, `recovery_clock` 7.1, `event_risk` 5.2, `peer_value` 4.6, `deterioration` 4.0,
`print_night` 3.5, `beat_bar` 2.8, `sentiment` 1.5, `board` 1.4, `cycle` 0.9. The ten suites this patch adds cost
about 119 seconds of work. The longest chain is `movers` -> `peer_value` -> `earnings_week` -> `alpha_model` ->
`signal_lab` -> `week_plays` -> `outlook` -> `anatomy` -> `board` -> `lane_book`: about 257 seconds, of which this
patch put about 100 on it.

## Review

Read in full: `event_risk.py`, `symbol_resolver.py`, `name_dossier.py`, `opportunity_board.py`, `lane_book.py`,
`winners_anatomy.py`, `recovery_clock.py`, `valuation.py`, `value_suite.py`, `run_deep_dive.py`, and the diff of
every modified Python file; in a second pass on 16 Nov `beat_bar.py` and the rest of `market_sentiment.py` (R19,
R20), and this review's own files (the baseline's B8 was wrong and is corrected). Read in part: `peer_value.py` and
`cycle_scenarios.py` (scoring and build). Not reviewed: the five new page templates and
the Ledger wording (covered only by the strict render in tests and on the day's pages), prompt texts, recipe values,
the page script in a browser.

| # | Severity | Where | Finding | Status |
|---|---|---|---|---|
| R1 | high | `financial_projection/valuation.py` `_pct` | Percent fields were converted with a magnitude guess (over 2.5 = percent points, else already a fraction). TradingView sends percent points, so a 1.7% buyback yield became 170% and a 2% net margin 200%. In the 12 Nov value run 1,617 of 3,930 valid names sat at the -5% share-drift floor and 829 at the +3% cap; 496 had a year-1 margin over 50% (NYSE:KR 199%, NYSE:MCK 127%), and the path then cut those margins by a quarter toward the industry. On the 13 Nov run 46% of buyback yields, 9.6% of net margins, 13.7% of revenue growth figures and 9.3% of 5-year CAGRs are under 2.5 points | Fixed (review edit): the division is unconditional; test `PercentPointTests`. After the fix: 436 at the floor, 340 at the cap, 210 margins over 50% |
| R2 | high | `valuation.value_inputs`, `value_suite.VALUE_EXTRA_FIELDS` | The dividend leg never ran: `dividend_yield_recent` is empty on the all-fields scan (0 of 6,702 names of $2B+ on 13 Nov); payout was above 0 for 0 of 3,930 names. IRR left out the yield (NYSE:KO, NYSE:DOW) | Fixed (review edit): reads `dividends_yield_current` (6,702 filled), falls back to the old field. After: payout above 0 for 2,309 of 3,929 |
| R1/R2 effect | | value run, pack `fv_*`, `irr_exp_pct`, `priced_in_cagr_pct`, `fp_pe_y5`; event-risk letter V; peer value's group columns; cycle `margin_x_ind`; board Value screens; dossier evidence | Recomputed in memory on the 13 Nov universe against the stored 12 Nov run (fair value over close, so the day's price move drops out): of 3,885 names, 50% move more than 10% and 21% more than 20%; among US $2B+ names 921 of 1,885 move more than 10% and 236 change the sign of `fv_upside_pct`. Examples (`fv_exp_px`): NYSE:KR 78.09 -> 115.61, NYSE:MCK 791.66 -> 1,226.30, NYSE:XOM 164.98 -> 193.24, NASDAQ:COST 1,086.38 -> 767.38, NASDAQ:MSFT 423.05 -> 358.32, NASDAQ:AAPL 275.64 -> 247.02, NASDAQ:NVDA 201.01 -> 164.60, NYSE:DOW 33.15 -> 33.33 | Not rerun: the stored run and existing packs keep the old numbers until `python src/run_financial_projection.py --mode value`. Whether the corrected model is well calibrated was not judged |
| R3 | medium | `forward_fundamentals.load_projection_context`, `cycle_scenarios._latest_value_csv`, `daily_refresh.json` | The value run is not a refresh producer. The pack joins the newest `finvalue_*` whatever its age, and `fv_upside_pct` / IRR are the run's own (computed on its close), not the pack's. The 13 Nov pack joined the 12 Nov run. The run is cheap: 0.5 s to load and 3.2 s to value 4,855 names | Open. Add a producer after `allfields`, or recompute upside on the pack close and write the run's age |
| R4 | medium | `api_tradingview_client._run_move_prediction_scan`, the panel store, every store study | Keeping US secondary listings changed the study universe. The store's US $2B+ floor went from 1,762 names on 2 Nov to 2,036 on 3 Nov; on 13 Nov 272 of 2,017 rows are non-primary (126 stocks, 146 receipts: NASDAQ:GOOG beside NASDAQ:GOOGL, NYSE:BRK.B, NYSE:TSM, NASDAQ:ASML, NYSE:HSBC). The `panel` table has no `is_primary` column, so signal lab, the alpha model, sentiment's cap-weighted index and breadth, lane records, analogs, anatomy and peer groups mix two universes across that date, and cap-weighted series count Alphabet twice. Prediction rankings can list two classes of one company | Open. Carry `is_primary` into the store and floor the studies on primary listings (Book lines exempt); keep the secondary rows for marks and lookups |
| R5 | medium | `winners_anatomy.py`, refresh graph | `anatomy` takes 41 to 81 s and `board` waits for it. It reads only the store apart from the candidate join, and a `--no-producers` update recomputes it on an unchanged store (12 Nov 21:24: `anatomy` 81 s, `alpha_model` 96 s, `signal_lab` 51 s) | Open. Cache by the store's last file and the recipe, or split the store study from the pack join |
| R6 | medium | `lane_book.build_frame`, `stats_rows` | `SELECT f.*` copies the store's features three times (`lb0`, `lb1`, `lb`); each `stats_rows` call scans a UNION of every lane and split once for the aggregates and once per horizon for the block t; called for lanes, months, each value field and rotation. 11 to 15 s today, linear in store days and in lanes | Open. Select the columns the lanes and `pct_fields` use; fold the block t into one pass |
| R7 | medium | `canvas_page.py` page script `preview`, `apply` | `preview` runs inside `apply` on every keystroke of a filter (`oninput` is not debounced) and re-reads the text of every row for every screen button and filtered column. On the board grid that is about 2,000 rows by 15 or more screens. Not measured in a browser | Open. Debounce the preview or cache cell text per row |
| R8 | medium | `name_dossier.analogs_for` | The 250 nearest name-days can be the same few stocks on consecutive days; `n_dates` is reported but not the number of distinct names, so the sample reads larger than it is. 56-day analogs come only from the store's first months | Open. Add `n_names`; consider a cap per symbol |
| R9 | low | `lane_book.build_lane_book` | `_f(x) or -99` treated a measured 0.0 excess as missing when picking a name's best lane and in the sort | Fixed (review edit): `_or` |
| R10 | low | `event_risk.py`, `lane_book.py`, `name_dossier.py`, `recovery_clock.py`, `winners_anatomy.py` | Seven lint items (unused locals and one import, f-strings without placeholders); `event_risk.latest_odds` compared `kind` raw on one side and as text on the other | Fixed (review edit) |
| R11 | low | `event_risk.highlights_text`, `write_event_risk`, field names | Recipe values repeated as literals: "worst 5-step move", z 2, beta 1.2, -8%, 1% of NAV in the notes; `stress_5s_*` and `crowd_5s_pct` carry `multi_steps = 5` in their names | Open. Format the notes from the recipe |
| R12 | low | `symbol_resolver.resolve_marks`, `symbol_check` | An all-fields mark reports `age_days = 0` and no date whatever the run's age; `symbol_check` finds the newest all-fields run twice and opens it once per old-looking id | Open. Use the run's `created_at_utc`; pass the run through |
| R13 | low | `name_dossier.build_page_spec`, `progression`, `default_out_dir` | SQL built with `WHERE symbol = '{s}'` beside an unused `_lit`; `SUMMARY_FIELDS` is a code constant, not a recipe key; two `DESCRIBE` per symbol; folders are stamped to the minute, so two runs of one label in a minute share a folder | Open |
| R14 | low | `src/main.py` `deep_dive_analysis_agent` | Defaults to `agent=True` (a headless agent run) while the CLI defaults to data only | Open, as requested; stated so a caller knows |
| R15 | low | `recovery_clock.build_recovery_clock`, `cycle_scenarios.build_cycle_scenarios` | The store path is read from the recipe directly where other modules use `default_store_path`; the cycle build values each (k, d) twice | Open |
| R16 | low | `tv_scan_cli.py`, new modules | Baseline debt grew: `main()` is 961 lines; the new modules add their own `_f` / `_r` / `load_recipe` (now in 33 / 24 / 20 modules) and import underscore helpers across modules (`downside_risk._book_lines`, `_lit`, `_step_cte`; `deterioration._resolve_ids`; `beat_bar` takes `_csv`, `_lift` from `print_night` and `_pctl`, `_quantile_edges`, `_bucket` from `signal_lab`) | Open (B1, B2, B9) |
| R17 | low | docs, tests | No `run_catalog.txt` block for `beat-bar`, `cycle`, `peer-value`, `recovery-clock`, `anatomy`, `board`, `sentiment` (they are in `CLAUDE.md` and the capability index); `value_suite.py` and `run_deep_dive.py` have no test of their own | Open |
| R18 | note | 14 new broad `except Exception` handlers: 6 in existing code (resolver fallback in the holdings scorer, value-run load, `warn_changes`, `_book_missing`, `_attach_warn`, `run_market_sentiment`) and 8 in new modules (`name_dossier` 3, `symbol_resolver` 2, `market_sentiment`, `peer_value`, `recovery_clock` 1 each) | Each turns a missing or locked file into an empty join, which is the design for optional suites, and each also hides a schema error | Open; narrow where the failure mode is known |
| R19 | low | `beat_bar.build_beat_bar`, `grid_cells`, `write_beat_bar` | The measures are a recipe list (`measures`: `react_fx` / `rx`, `hold_fx_7d` / `h7`, `hold_fx_28d` / `h28`) but the code names `react_fx`, `rx` and `h28` as literals for the halves, the bar, the dollar tails and the pointer counts, and the note says "beat = EPS surprise > 0" beside the recipe's `beat_field` / `beat_above`. A recipe that renames a code gets empty columns without an error. A surprise of exactly 0 counts as a miss (stated in the note) | Open. Read the reaction measure and the two pointer codes from the recipe; format the note from it |
| R20 | low | `market_sentiment.add_outside`, `build_market_sentiment` | `sentiment-add --date` is stored unchecked: a date that is not ISO makes a row that `daily` never joins and `event_risk.latest_odds` skips without a message. `spec.partial` is true when the scan time is unknown, not only when the session is partly seen | Open. Reject a date `date.fromisoformat` cannot read; report an unknown scan time as unknown |

## Tests and checks

- `python -m pytest src/tests -q` on the working tree (the staged set plus the second pass's edits), without the two
  older files: 917 passed, 8 skipped (111 s). The two files alone: 3 failed, 35 passed, 4 errors, as at the baseline
  (`ProfilePerformanceMetrics` signature; scan-period tracking).
- Touched modules after the review edits: 41 passed (`test_event_risk`, `test_lane_book`, `test_name_dossier`,
  `test_recovery_clock`, `test_winners_anatomy`, `test_financial_projection_valuation`, `test_cycle_scenarios`,
  `test_peer_value`); `test_patch_scan` 11 passed.
- Scan guards: no forbidden path, no `_tmp_` file, no marker block, no `SELECT *` on the wide tables, every Python
  file parses, every JSON file loads. Lint: the seven items of R10 in the first 102 files, fixed since. The scan of
  everything reports no finding.
- Lifecycle sections of the scan: 17 features new (15 modules, 2 pages without a module), 18 updated, none removed;
  nothing deleted, no function, CLI subcommand, flag, recipe key or refresh step removed; one line of lifecycle
  wording added (`documentation/data_analysis_principles/tradingview_stock_screen_payloads.md:42`, the "legacy
  `is_primary` filter", which is the R4 change).
- The first 102 files were never tested without the review edits: the tests ran on the working tree. Without those
  edits the seven lint items and R1 / R2 / R9 are in the code.

### Second pass (16 Nov): the review's own files

| Checked | Result |
|---|---|
| The marker guard of `patch_scan.py` | It looked for the literal `=== AI GENERATED CODE ===`; the blocks in the repo read `# === AI GENERATED CODE START (...) ===` / `END ===`, so the guard could never fire. Now a prefix match, checked against commit `cb814d2`, which added one (1 finding) |
| The Wiring table | Refresh steps whose `argv` sits on several lines were missed (`board`, `anatomy`, `sentiment` showed blank). Whitespace is now collapsed before the search; a test covers it |
| `--range A...B`, the first commit, invalid JSON, a file that does not parse, a table cell with a pipe | Each was wrong or silent; fixed with tests |
| Baseline B8 | Said `scripts/_tmp_compare_runs.py` was still tracked (deleted 2 Sep 2026) and that `scripts_adhoc/` was untracked (six files are tracked). Corrected in the baseline. Found by the feature register's list of removed files |
| Three table rows of the baseline | Broken by a pipe inside a code span; rewritten |
| R18's count | 11 in the first draft; the inventory counts 14 |
| What the first pass could not answer | Whether the change set removed, renamed or retired anything, and when each feature arrived. Added: the Feature changes table above, `lifecycle.json`, `feature_register.md`, the scan's removal and reference check |

## Files the review added or changed (staged with the rest on 16 Nov)

- Added: `src/generic_utils/patch_scan.py`, `config/generic_utils/patch_scan.json`, `src/tests/test_patch_scan.py`,
  `.claude/skills/patch-notes/SKILL.md`, and this folder (`README.md`, `0000_baseline_20261102.md`, this file,
  `capability_index.md`, `commit_history.md`, and in the second pass `feature_register.md`, `lifecycle.json`).
- Edited: `src/financial_projection/valuation.py`, `value_suite.py`, `src/tests/test_financial_projection_valuation.py`
  (R1, R2); `src/generic_utils/lane_book.py` (R9, R10); `event_risk.py`, `name_dossier.py`, `recovery_clock.py`,
  `winners_anatomy.py` (R10); `CLAUDE.md`, `README.md`, `src/research_prompt/run_catalog.txt` (patch notes).

## Follow-ups

1. Rerun the value run after taking R1 / R2 and look at the names whose `fv_upside_pct` changed sign before the next
   Daily leans on a Value screen or a V letter.
2. The same percent guess in `financial_projection/growth_lanes.py:41`, `lanes.py:31`, `model.py:30` and `:134`,
   `peer_scales.py:54` (baseline B3). Those feed `fp_*` and the growth lanes; changing them moves numbers that have
   been in use since August, so it wants its own patch with a before / after.
3. R3: the value run as a refresh producer.
4. R4: `is_primary` in the panel store and a primary-only floor for the studies; decide what 3 to 13 Nov rows do.
5. R5 to R7: the anatomy cache, the lane-book frame, the preview debounce.
6. R8: distinct names behind the analogs.
7. R17: seven run-catalog blocks; a smoke test for the two runners.
8. R19, R20: the beat bar's measure codes from the recipe; a date check in `sentiment-add`.
9. Lifecycle candidates, not decided (nothing here was requested, so none is in `lifecycle.json`): the 17 tracked
   one-off scripts in `scripts/` and `scripts_adhoc/` (baseline B8), and `src/tests/token_estimator.py`, a script
   that is not a test and whose module was deleted in August. To retire one, add a `removal_requested` entry and
   follow the README's lifecycle steps.
10. After the commit: put its hash in this file's Commit row and run `patch_scan.py regen`, so the register's
    `0001*` and "not committed" become the patch number and the commit.
11. Holder base and flows: phase 0 of `holder_base_and_flow_plan.md` has not started.
