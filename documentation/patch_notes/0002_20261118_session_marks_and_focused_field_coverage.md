# Patch 0002: premarket / after-hours prices on the right base, and the fields the daily scan stopped fetching

| | |
|---|---|
| Date | 2026-11-18 |
| Commit | `bb7e9f3`; base `fd2525e` |
| Change set | Scanned before the note: 24 files, +658 / -40 (3 added, 21 modified, none deleted). The commit: 30 files, +1090 / -67, including this note, its README row and the three generated files |
| Scan | `logs/patch_notes/20261118_staged/inventory.md` |
| Tests | 924 passed, 8 skipped; the two older test files unchanged (baseline B6) |

## Summary

The pack, the premarket book and the post-earn page now give the premarket and after-hours price on the close each
percent is measured against. Before this patch every scan taken after the cash open counted the day's move twice in
the premarket price. Book, names, tape and movers rows carry the premarket and after-hours change, the share of a
normal day's volume traded there, and the close against VWAP; the brief's and the board's Book tabs and the movers day
tables show them. The focused intraday export, the only daily producer since the full export stopped running daily,
now fetches the ten fields the suites read beyond its catalog: price / book and the analyst rating return to the panel
store and the pack, and the catalyst outlook gets its ex-dividend and IPO-lockup events back. Three income / defensive
scoring profiles read a dividend field that TradingView no longer fills; they now read the live one.

## What changed for the operator

### Premarket and after-hours price (generic_utils.session_marks)

- Question: where does a name trade before the open or after the close, and is the move real?
- TradingView's `premarket_change` is against the last cash close before that premarket. During the premarket that is
  the row's `close`; after the open, `close` is the live price and the base is `close / (1 + change / 100)`.
  `postmarket_change` is against the cash close of the session it follows. Checked on 29 full-catalog runs (Sep to Nov
  2026): exact both ways.
- `pre_px` / `post_px`: `premarket_close` / `postmarket_close` when the scan carries them (now always, see below), else
  the percent on the right base, chosen by the scan time on a New York clock (`us_phase`: pre 04:00-09:30, cash
  09:30-16:00, post 16:00-20:00, closed). Without a scan time a derived price stays empty rather than guessed.
- `pre_vol_x` / `post_vol_x`: shares traded in that session over the 10-day average daily volume (0.05 = 5% of a normal
  day). A large move on a small ratio is a thin print. `vs_vwap`: close against the session VWAP, percent.
- Where: pack `names`, `book`, `us2b_tape`, movers tables (`compile` whitelists); `PACK/premarket_book` and
  `tv_scan_cli.py session` (post-earn); `earnings_week` and the outlook / board (percent and volume ratios, no price).
  Pages: brief Book tab, board Book tab, movers Day tables (`pre_chg`, `pre_vol_x`, `post_chg`, `post_vol_x`).
- Fill by phase (US $2B+, runs 6-18 Nov): premarket fields fill through the premarket (about 40-60% early, about 70%
  by the open) and stay all day; after-hours fields are filled only after the close and overnight, empty from the next
  premarket through the cash session.
- Limits: US market holidays are not in the clock; a premarket read taken before 04:00 New York or on a weekend is the
  last session's (`us_phase` = closed).

### Fields the focused export now fetches

- Since 7 Sep 2026 the daily producer is `export_focused_tradingview_fields_duckdb` (243 fields); the full export ran
  on some days only and last on 6 Nov. Every reader takes the day file's newest run, so anything outside the focused
  catalog was empty on those days.
- `FOCUSED_REQUIRED_FIELDS` (in `trading_view_export_all_tdfields.py`, merged into every focused request, 253 columns,
  still one request): `premarket_close`, `postmarket_close`, `price_book_ratio`, `recommendation_mark`,
  `recommendation_total`, `ex_dividend_date_upcoming`, `dividend_amount_upcoming`, `ipo_offer_date`,
  `price_52_week_high_date`, `total_shares_outstanding`. The catalog CSV in `savedData/` is gitignored, so the code
  list is the versioned record; the CSV was extended the same way on this machine.
- What comes back, from the next producer run:

| Field | Reader | Empty since |
|---|---|---|
| `pb` | panel store (signal lab, alpha model, lane book, analogs), pack P/B for the book family | 7 Sep (panel: 49 days, 107 days filled before); every focused-only run (pack) |
| `rec_analyst` | panel store, catalyst outlook `rating`, name dossier | 7 Sep |
| ex-dividend dates (270-406 events on full-run days), IPO lockups (9), analyst count | catalyst outlook events | 9 Nov every day; most earlier days without a full run |
| premarket / after-hours close | pack, post-earn page | every focused run |

- Readers fall back as well: the pack's `pb` takes `price_book_fq` last (94% of US $2B+ on 18 Nov); a panel field may
  name a list of columns, read in order, and `pb` is `[price_book_ratio, price_book_fq]`.
- Not backfilled: the panel's `pb` for 7 Sep-18 Nov can be refilled from `price_book_fq` by re-ingesting those days
  (not run: it rewrites part of the store every study reads). `rec_analyst` was never fetched in that period and
  cannot be refilled.

### Scoring profiles (ranking change)

- `dividend_yield_recent` is empty at TradingView: 0 of 5,109 rows in the W47 move-prediction scan, 0 of 2,012 US
  $2B+ on the 6 Nov full run. `dividends_yield_current` is filled for 5,101.
- `defensive_fortress_v1`, `defensive_fortress_v2` (move prediction) and `defensive_income_v1` (module 2) weighted only
  the dead field, so they ran with no dividend input (missing signals are skipped and the weights renormalised). The
  weight now sits on `dividends_yield_current`, same value. **Their scores and ranks change from the next prediction
  run**; their config hashes change with them.
- `income_compounder_v1` and `durable_value_compounder_v1` already weight the live field beside the dead one: unchanged.

### Operator records in this commit (not reviewed as code)

- `config/holdings_scoring/current_holdings.json`: the operator's Book update (the file is the record).
- `config/generic_utils/sentiment_outside.json`: OUTSIDE readings for 16-17 Nov (10-year yield, VIX and others).
- `documentation/market_insights/findings_log.md`: entries for 16 and 17 Nov.

## Feature changes

| Feature | Change | Parts | What a user of it must know |
|---|---|---|---|
| `session_marks` | added | module, tests | One place for the premarket / after-hours price, session volume and VWAP on a row |
| `session_tape` (premarket book, post-earn) | updated | module, tests | `pre_px` right after the open; `pre_close` / `post_close` filled on focused runs; new `pre_vol_x`, `post_vol_x`, `vs_vwap`, `us_phase`; `spec.current_scan_at`. A derived price needs the scan time |
| `extract`, `compile` (pack) | updated | modules | Book / names / tape / movers carry `us_phase`, `pre_chg`, `pre_px`, `pre_vol_x`, `post_chg`, `post_px`, `post_vol_x`, `vs_vwap`; `pb` falls back to `price_book_fq` |
| `earnings_week` | updated | module | `pre_vol_x`, `post_vol_x` beside `pre_chg` / `post_chg` on the calendar |
| `catalyst_outlook`, `opportunity_board` | updated | recipes | The four session columns after `day` |
| `(page) brief`, `(page) board`, `(page) movers` | updated | page templates | Book tabs and movers Day tables show the session columns (dropped on older packs) |
| `panel_store` | updated | module, recipe | A field can list fallback columns (`numeric_pick`); `pb` = price_book_ratio, else price_book_fq |
| Focused all-fields export | updated (changed default) | `trading_view_export_all_tdfields.py` | Ten more fields per request (`FOCUSED_REQUIRED_FIELDS`); `extra_fields` parameter on the full export |
| Move-prediction profiles `defensive_fortress_v1` / `_v2`, module-2 `defensive_income_v1` | updated (changed scoring) | profile JSON | Dividend weight on the live field; ranks change |
| Ledger | updated | `canvas_ledger.json`, `run_export` notes | New keys `pre_chg`, `pre_px`, `pre_vol_x`, `post_vol_x`, `vs_vwap`, `us_phase`; `post_chg` corrected (it is empty during the session, not "the evening before") |

**Deprecated or removed in this patch: nothing.** `implied_premarket_price` stays (documented: right only on the
correct base); row-level callers use `session_marks`.

## Technical changes

| Area | Added | Modified | Lines |
|---|---|---|---|
| generic_utils (`session_marks`, `session_tape`, `earnings_week`, `panel_store`, `run_export`) | 1 | 4 | +210 / -20 |
| tests | 2 | 1 | +151 / -1 |
| recipes (outlook, board, panel store, sentiment readings) | 0 | 4 | +108 / -1 |
| documentation (findings log) | 0 | 1 | +61 |
| Ledger | 0 | 1 | +50 / -2 |
| data_analysis_scripts (export) | 0 | 1 | +29 / -3 |
| operator_briefing (`extract`, `compile`) | 0 | 2 | +26 / -1 |
| Book config | 0 | 1 | +15 / -4 |
| page templates | 0 | 3 | +5 / -5 |
| scoring profiles | 0 | 3 | +3 / -3 |
| **Total** | **3** | **21** | **+658 / -40** |

- `session_marks.py`: `us_phase` (zoneinfo America/New_York, so daylight saving is handled), `prior_close`,
  `session_marks`, `marks_from_raw`, `run_scan_time`, `SESSION_FIELDS`, `MARK_COLUMNS`.
- `session_tape.py`: `SESSION_COLUMNS` gains the two volume fields, the 10-day average volume and VWAP;
  `join_named_premarket` / `join_post_earn` take `scan_at`; the builders read it from `run_metadata`.
- `extract.py`: the pre / post aliases were selected and then dropped from every row; they now go through
  `session_marks` with the run's `created_at_utc` (`_scan_time`). New aliases `pre_vol`, `post_vol`, `avgvol10`,
  `vwap`.
- `compile.py`: `MARK_COLUMNS` in `TAPE_FIELDS`, `_compact_name` and `_book_rows`.
- `panel_store.py`: `numeric_pick` builds `COALESCE` over the columns a day file has.

## Review

Measured on the 18 Nov 14:45 UTC run (cash session, US $2B+, 1,986 names) with the new code against the old formula.

| # | Severity | Where | Finding | Status |
|---|---|---|---|---|
| R1 | high | `session_tape.implied_premarket_price` (premarket book) | After the open the premarket price used the live price as its base: 132 of 1,433 names off by more than 2%, median 0.66%. NYSE:PRMB 19.55 instead of 22.00 (day -11.1%, premarket -9.3%); NASDAQ:GLBE 32.89 instead of 36.41 | Fixed |
| R2 | high | focused export, panel store, outlook | Fields outside the focused catalog empty on every focused-only day: panel `pb` and `rec_analyst` since 7 Sep; outlook ex-dividend / IPO-lockup events and analyst count on every day without a full run, every day since 9 Nov | Fixed going forward; panel `pb` backfill offered, not run |
| R3 | medium | `extract.extract_all_fields` | `pre_chg`, `pre_gap`, `pre_close`, `post_chg`, `post_close` were selected and never copied to a row: no pack table carried a premarket or after-hours move | Fixed |
| R4 | medium | profiles | Three scoring profiles had no live dividend input (dead `dividend_yield_recent`) | Fixed; ranks change |
| R5 | medium | pack `pb` | The P/B chain had no field the focused run carries; book-family names (banks, insurers) got no quoted multiple and no peer label on those runs | Fixed: `price_book_fq` last (94% filled) |
| R6 | low | volume / VWAP | `premarket_volume`, `postmarket_volume` and `VWAP` were fetched on every run and read by no daily suite. On 18 Nov 7 of the 19 premarket moves of 5% or more traded on under 2% of a normal day (NASDAQ:NEOG -7.3% on 0.01%) | Fixed (`pre_vol_x`, `post_vol_x`, `vs_vwap`) |
| R7 | low | Ledger `post_chg` | Said the field carries "the evening before" during the session; runs 6-18 Nov show it empty from the next premarket to the close | Fixed |
| R8 | low | `session_marks.us_phase` | No US holiday calendar: a holiday weekday reads as a normal session and a derived price can be off | Open; explicit prices (now fetched) are used first, so it only matters on old runs |
| R9 | note | `patch_scan` feature map | The movers page shows under `predicates` in the scan's features table (the module the `movers` subcommand is tied to) | Open, tool only |

Not reviewed: the operator's records (Book config, outside readings, findings log); page renders in a browser (the
tables use `drop_missing_columns`, so older packs render without the new columns).

## Tests and checks

- `python -m pytest src/tests -q` without the two older files: 924 passed, 8 skipped (120 s).
- New: `test_session_marks.py` (clock with daylight saving and weekends, base close by phase, explicit prices,
  volume, VWAP), `test_daily_field_coverage.py` (the focused request carries the required fields once and stays one
  request; panel fallback SQL; a pack row gets the marks and `pb` from `price_book_fq`), and a case in
  `test_session_tape.py` for the after-open base.
- In memory on the newest pack: the outlook carries `pre_chg` / `pre_vol_x` for 1,438 of 1,998 names (after-hours
  empty: the run is mid-session); pack `pb` 94%, `vs_vwap` 100%.
- Scan: no lint or guard finding, nothing removed.
- Not run: a producer, a compile or a page render.

## Follow-ups

1. Optional: re-ingest the panel store for 7 Sep-18 Nov so `pb` reads `price_book_fq` on those days (a level break
   against the ratio is possible: median ratio / fq 1.03, 90th percentile gap 32% on the 6 Nov full run).
2. Carry `post_vol_x` into the panel store so signal lab can measure whether thin after-hours moves hold.
3. A US holiday calendar for `us_phase` (R8).
4. The baseline's B3 percent guesses in `financial_projection` are still open (patch 0001 follow-up 2).
5. Done: commit `bb7e9f3` recorded in the Commit row and the generated files regenerated.
