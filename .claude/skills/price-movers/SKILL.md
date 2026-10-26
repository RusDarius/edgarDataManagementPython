---
name: price-movers
description: Classify whether day / week / 5D / 1M (or 3M) price movers are supported by leftover, weeks scores, mix, MTP, industry participation, valuation, and peer-relative discount. Use when the user asks for movers, groovers, tape leaders/laggards, or the MOVERS copy-paste suite. Do not use for leftover radar, Top 100 unpaid, shorts overlay, the DAILY leftover briefing, or week/month IC plans (backtest-horizon).
---

# Price movers (supported tape)

**Canvas = operator HTML page** (JSON spec in `logs/canvases/` rendered by `tv_scan_cli.py page`, see `.claude/skills/canvas-ledger/SKILL.md`). Not a Cursor `.canvas.tsx`.

**Did price already move, and is that move supported?** DAILY leftover radar answers **can this still pay**.

`duckdb output` (the default paste) stops at `movers.duckdb`. Write `movers-YYYYMMDD` only when the paste says `canvas output`. Shown tape rows add `opm`, `pe`, `pe_fwd`, and `dte` next to Val / Peer / Proj / Tech. Peer is the label plus `val_vs_ind` (percent versus the industry median) and `val_vs_avg` (percent versus the industry average). Those are different columns. Tech is `ABOVE`/`BELOW` plus `vs50`, and `UNUSED`/`MID`/`PAID` plus `rng`. Field relatives such as `pe_vs_ind` and `pe_vs_avg` stay on the row. The canvas table keeps them; zoom handles the width.

On DAILY (full): one **condensed Tape** section on `daily-focus-YYYYMMDD` (day / week / 5D / 1M clusters). Not four Daily tabs. Full ranked 25s are **not** Daily work.

Tape-only / --- MOVERS (day / 5D / 1M) ---: **one** canvas `movers-YYYYMMDD` (`logs/canvases/`, `tv_scan_cli.py page`) covering **all** of those horizons. Do **not** split `movers-day-YYYYMMDD`, `movers-5d-YYYYMMDD`, `movers-1m-YYYYMMDD`. Do not attach `src/research_prompt/daily_generic.txt` on this suite.

A third tape horizon is another JSON recipe (`config/generic_utils/movers_3m.json`), not another skill. 3M is `movers-3m-YYYYMMDD` — still one 3M canvas, not a Daily tab.

Do not write `logs/_tmp_*.py`. Do not invent a 0–100 "supported score".

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/operator_briefing/example_entry.py inspect
# After compile, prefer pack.movers / briefing_pack.duckdb table movers_tails.
python src/generic_utils/tv_scan_cli.py movers --recipe config/generic_utils/movers_day.json --csv us2b_tape.csv --out-dir RUN/movers_day
python src/generic_utils/tv_scan_cli.py export --out-dir RUN/section --table radar=radar.csv --source-note "pack RUN"
```

`--out-dir` writes CSV + `movers.duckdb` + `overview.log` (tool, UTC time, git, sources, row counts). Quote DuckDB paths that contain `=`.

Recipes pin **top 25 up by that horizon’s return** (best → least, no industry cap) and a **bottom 25 punished pool**. Downside is not leftover-first: classify the pool into **BOUNCE** (leftover / unused range / cheap vs industry / live weeks or MTP can justify a bounce) vs **CONTINUE_DOWN** (likely to keep falling). Omit MIXED or note briefly. Override with `--leaders` / `--laggards`. Bare `--top N` (no recipe size) is the old per-tail size. Generic CLI with no recipe still defaults to cover 50 at 75/25.

Each tail row keeps **Val** (`val_label` `val` from `generic_utils.setup` — not always EV/Rev), **Peer** (`peer` plus `val_vs_ind` and `val_ind_med`; DISCOUNT is ≤ −5% versus the US $2B industry median on that field, PREMIUM is ≥ 5%, INLINE is between), **Proj** (left, bo, cont, fwd, street_px / target_px), **Tech** (`tech_sma` with `vs50`, `tech_rng` with `rng`, RSI, ADX). Peer set is the US $2B industry, not the 25-name list. No 0–100 composite. Do not quote EV/Rev to manufacture a discount. A label without its percent is incomplete on the canvas and in the DuckDB selection list.

Manual vs-group without movers:

```powershell
python src/generic_utils/tv_scan_cli.py derive --csv us2b_tape.csv --vs-by ind --vs-fields evrev,pe,left --vs-min-n 6 --out tape_vs.csv
```

3M: `--recipe config/generic_utils/movers_3m.json` (same 25 up / 25 punished). Recipes drop `TICKER23` listing leaks when the stem ticker is in the universe (`--keep-duplicate-suffixes` to keep). Leftover SQL column is `"left"` (DuckDB reserved); prefer `--csv` / `named`. Laggards carry pack/CLI field `down_class` (`sleeves.classify_punished_tape`). Do not put those cutoffs in `ranking.py`.

## Work order

1. inspect; compile if sources differ. Read pack `movers` before re-fetching tape.
2. **Up:** rank the **top 25** by that horizon’s return (best → least). Start from pack/CLI `up_class` (`sleeves.classify_up_tape`) and override only with a reason. Classify IS_SUPPORTED / CHASE / UNSUPPORTED / MIXED using leftover, weeks (bo/cont/fwd), mix, MTP, **and** Val / Peer / Proj / Tech. Show the ranked 25, not only clusters. **Down:** start from the bottom 25 in pack.movers / movers_tails (`down_class`). Publish **BOUNCE** (strongest leftover / unused range / cheap vs industry that can justify a bounce) **and** **CONTINUE_DOWN** (structure + leftover say it keeps falling). Skip MIXED unless a Book name. CAN_PAY = DAILY unpaid unused-range that has not ripped — pointer, not a second NEW. Canvas Tape columns **Val / Peer / Proj / Tech**. DuckDB holds the 25+25 per horizon.
3. Book: polar still applies. Groover Book may TRIM if CHASE; ADD only if leftover/bo unpaid. A groover at a peer **premium** (`peer` PREMIUM / positive `val_vs_ind`) with leftover gone is CHASE, not IS_SUPPORTED. A brief-history BAD or MISSED label can change the grade on a Book name. It does not reorder the top 25.
4. Focus band on Plan rows you would trade (Book names on the tape, and any supported name with a course). Quote `entry_low` to `entry_high`, `trim_px` / `trim_state`, and `kill_px` from `tv_scan_cli.py entry-book` on that short list. Do not attach a band to every tail row. A starter stays about $5,000 in mind. An add to a held line can be about $1,000.
5. Canvas:
   - **DAILY (full):** update Daily **Tape** only (clusters). One Ledger on Daily.
   - **MOVERS (day / week / 5D / 1M):** write **one** `movers-YYYYMMDD` page from `--template movers` and the day's judgment file (courses + `pages.movers.blocks`) — Plan / Tape / Ledger. Tape tables use `movers_tails` / `movers.duckdb` as the `source`. Tape holds every requested horizon (horizon filter on that Tape menu, or stacked H2s). Not four canvases. If Daily exists this session, Daily Tape stays condensed and points at this file. One Ledger. Add Val/Peer/Proj/Tech abbreviations in the same edit.
   - **MOVERS (3M):** `movers-3m-YYYYMMDD` only.
6. Generic-vs-domain note.

Standing spec for tape-only / 3M: `src/research_prompt/movers_generic.txt` (do not @ on DAILY (full)).
