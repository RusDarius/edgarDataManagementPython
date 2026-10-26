---
name: entry-book
description: Turn a catalog or tape CSV into buy-band and thesis-kill prices. Use when the user asks where to buy, where a thesis dies, an entry book, or a recount at a different price. Not a Daily ranking and not a 0-100.
---

# Entry book

**Where is the buy on this row, and where does that entry die?** Daily answers which names can still pay. This file prices the buy and the kill. A later close can open a new band. It does not move the kill on this row.

No ADD/WAIT in the dump. No leftover cutoff. No 0–100.

A new line is sized with about $5,000 in mind. An add to a line already held can be about $1,000. That is advice on the recommendation (`starter_hint_usd`, `add_hint_usd`, `advised_usd`). It is not a filter and not a hard cash target.

## Command

```powershell
$env:PYTHONPATH='src;.'
python src/generic_utils/tv_scan_cli.py entry-book --csv CATALOG.csv --out-dir RUN/entry_book
python src/generic_utils/tv_scan_cli.py entry-book --csv CATALOG.csv --mark-csv LATER.csv --out-dir RUN/entry_book
# any tape name: the pack tape carries atr / atrp / street_px / target_px, so a named --csv subset of it bands and kills like a pack name
python src/generic_utils/tv_scan_cli.py named --csv PACK/movers/us2b_tape.csv --fields symbol,ind,close,vs50,atr,atrp,street_px,target_px,left,street_left,rng,rsi,bo,dte --ids CRC,LHX --out PACK/analysis_scans/candidates.csv
python src/generic_utils/tv_scan_cli.py entry-book --csv PACK/analysis_scans/candidates.csv --out-dir PACK/analysis_scans/entry_book_candidates
```

Recipe: `config/generic_utils/entry_book.json`.

Folder: `entry_book.csv`, `entry_book.duckdb`, `queries.txt`, `overview.log`.

The upside catalog at `briefing_pack_*/upside_catalog/upside_entry_catalog_*.csv` is one input. Any CSV with `close` and `vs50` works. `atrp` or `atrp_last` sets the stop width. `street_px` is the target when it is present. `target_px` stays in its own column.

## What the columns mean

| Column | Meaning |
|---|---|
| entry_low / entry_high | The buy for this row. Above the 50-day, that is the average up to the close. Under it, the band starts at the 50-day and extends half an ATR. |
| sma_role | `stop_below` when the close is above the average. `reclaim` when the average is overhead. Do not buy a reclaim name at the mark. |
| trim_px / trim_state | First trim, not the kill. `trim_at_sma`: `trim_px` is the 50-day and a print there lightens a held line. `through_average`: the close is already under the 50-day, so `trim_px` is empty. `at_average`: the close is on the average. |
| kill_px | Same price as `exit_stop`. A print there ends this entry. `kill_scope` is `this_entry`. |
| exit_target_street / exit_target_pack | Street and pack prices. They stay separate. `exit_target` uses the street price when that column is on the row. |
| rr_at_band | Distance from `entry_high` up to the target, divided by the distance down to `kill_px`. A ratio, not a 0–100. |
| mark_vs_band | `inside`, `above_band`, or `below_band` at the row's own close. |
| cost_kill_px | 15% under cost when a cost column is present. Separate from `kill_px`. |
| close_now / band_state_now | A later close. `through_kill` means this entry is already dead. The band prices do not change. |

## How to use it

1. Run the command on the catalog. Grep `queries.txt` for the columns you quote.
2. Focus mode quotes three prices on the row: invest from `entry_low` to `entry_high`, trim at `trim_px` when `trim_state` is `trim_at_sma`, and kill at `kill_px`. `cost_kill_px` is the book 15% under cost and stays separate. A name under the band waits for the 50-day. A name above the band is not filled on this row. A reclaim name is not bought at the mark.
3. If `band_state_now` is `through_kill`, do not repair the old prices. A new catalog row is a new band.
4. Quote street beside pack. Do not spend a new line on `edge_only`.
5. Judgment (NEW, WAIT, EXIT) stays on the daily canvas. This file does not rank the book.

Holdings membership over about six months, with brief labels beside it, is `tv_scan_cli.py book-sessions --lookback-days 180`. That roll is not the entry band.

Buy-in-red and margin of safety on a named set (discount, bounce, kill, add, trim in one table), one call: `tv_scan_cli.py name-levels --pack PACK --theme ai_core,cyber --ids A,B --quiet` (`generic_utils/name_levels.py`; themes in `config/generic_utils/themes.json`). It writes `names.csv`, `entry_book/`, `gmv_names.csv`, `history.csv` and `safety_band/` under PACK/analysis_scans/name_levels. The last step alone is `tv_scan_cli.py safety-band --csv entry_book.csv --lens-csv gmv_names.csv --history-csv history.csv --out-dir safety_band` (`generic_utils/safety_band.py`, knobs `config/generic_utils/safety_band.json`). It prices EPS x own-low / own-median / peer forward PE, a band under the close anchored on the split-adjusted window low, `bounce_kill`, the reclaim add and the trim zone. A low PE on tripled estimates is a cycle question: read `eps_fc_pct` and `pe_nfy_seat` beside every anchor.

Timing policy uses the same buy band and a different kill width. `tv_scan_cli.py timing-levels` prices `kill_px` at the setup's 0.75 or 1.0 ATR. `thesis_drawdown_px` and `thesis_chase_px` are the prices where that timing thesis changes. The MTP run writes those columns on `action_policy_recommendations`. ENTER_SMALL stays climate. The 1.5 ATR kill on this entry book is a separate price.
