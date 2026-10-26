---
name: analysis-style
description: >-
  Active-management analysis style that ranks asymmetric payoff and conviction
  from pack derivatives, raw fields, and all-fields history. Use when the user
  asks for analysis style, analysis_style, asymmetric rank, quality
  opportunities, wisdom thinking, or best trades across week, month, and long
  horizon. Not a replacement for Daily, Movers, or Book risk.
---

# Analysis style (active management)

**Canvas = operator HTML page** (JSON spec in `logs/canvases/` rendered by `tv_scan_cli.py page`, see `.claude/skills/canvas-ledger/SKILL.md`). Not a Cursor `.canvas.tsx`.

**Which names have asymmetric payoff or real conviction, once the algos have spoken?** Daily answers leftover. Movers answers whether tape is supported. Risk answers what a drop costs. Wisdom dumps the tables. This style ranks a shortlist from those derivatives, the raw fields, and the historical all-fields tape.

Do not invent a 0–100. Do not add `rr` and `bo` into one number. Do not write `logs/_tmp_*.py`. Do not publish a second NEW/ADD budget. Do not steal Daily, Movers, or Risk tabs.

## What the rank is allowed to use

| Layer | Source | Role in the order |
|---|---|---|
| Derived | Pack `left`, `rr`, `rng`, `bo`/`cont`/`fwd`, `peer`, `val_field`, `mix`, MTP, `down_class` | Payoff sort is `rr`. Conviction sort is `bo`. Peer is a quality flag. |
| Raw, now | Wisdom / all-fields: `close`, `price_target_average`, `RSI`, `ATRP`, and that row's `val_field` | Confirms the pack. Do not recompute leftover. |
| History | `tv_scan_cli.py history` + `span` on `trading_view_all_fields_data` | Path class: OPENING, DYING, CHEAPER, THIN_HIST. |
| Outside news | Web search, dated, `OUTSIDE` | Timing and grade only. Never a sort key. |
| Backtest IC | Wisdom `datasets/family_metrics` or `run_operator_suites.py sources` | Support only. Rank `spearman_ic` with `rank_cli`. Not a sort key and not a leftover gate. |

Street leftover, pack leftover, and `fp_*` stay separate columns. Quote `pe_fwd` / `fp_pe_y1` and `fp_pe_y5` (projection forward PE). `pe_fwd_tv` stays empty. Quote `val_field`, not EV/Rev for every name.

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/operator_briefing/example_entry.py inspect
python src/operator_briefing/example_entry.py compile
python src/run_operator_suites.py wisdom --cover 1000
python src/generic_utils/tv_scan_cli.py focus --csv SUITES/pack_focus/us2b_cover.csv --where "left>=25,rng<=55,rsi<=68" --field rr --tie-breaker bo --top 40 --group-field ind --cap 4 --out SUITES/analysis_scans/payoff.csv
python src/generic_utils/tv_scan_cli.py history --root logs/tradingview_analysis/trading_view_all_fields_data --glob "**/tradingview_all_fields_*.duckdb" --table all_fields_rows --id-field symbol --ids ID_LIST --fields close,price_target_average,RSI,ATRP --run-prefix tradingview_all_fields_ --newest 40 --rename price_target_average=pt,RSI=rsi,ATRP=atrp --span-id-field symbol --span-fields close,pt,rsi,atrp --out SUITES/analysis_scans/af_span.csv
```

`--ids` is the payoff shortlist (and Book names you might trade), not the 1000-name cover. `--newest 40` is about forty all-fields days. Echo `n`, `close_pct`, `pt_pct`. Wisdom stays `--cover 1000` with no industry cap and no canvas.

`--where` is comma clauses. Missing numbers fail the clause. The starter where is this version's example. Change it when the tape says so, write the new clause on the Rank tab, and do not copy it into `ranking.py`.

## Work order

1. Source lock. Cite pack run_id, the wisdom `suites_*` folder, and `datasets/` (all-fields, prediction, edge, MTP, backtest family IC, value projection). Backtest IC and `fp_*` stay support. They are not the ASYM or CONVICTION sort.
2. Regime from pack `regime` / `industries_5d` before names.
3. Read pack `suggested_courses`, `book`, `capital`. Polar still binds: ADD/NEW cannot become EXIT unless `thesis_kill`. Mix `avoid_value_trap` on a live-leftover Book line stays HOLD_NO_ADD. MTP is climate.
4. **Payoff rank.** `focus --field rr --tie-breaker bo` with the recorded where and an industry cap. This is the candidate pool.
5. **History.** Span that pool on all-fields (`close`, `pt`, `rsi`, `atrp`). Then, only for names still in the Asym table, a second span of each name's own `val_field` (one field list per native multiple, not EV/Rev for every name).
6. Assign a path class. Build the two orders below. Weeks scan and street-vs-model scan stay as support, not as extra sort keys.
7. Outside news on the published names and on Book `dte` ≤ 21. The brief-history digest is the same kind of support: it can change a grade. It is not a sort key. Read `digest.md`, not the prior notes.
8. Files land in the wisdom `suites_*` folder. Write `analysis-style-YYYYMMDD` only when the paste says `canvas output`. Rank rows keep `opm`, `pe`, `pe_peer`, `pe_vs_ind`, `evebitda`, `evebitda_peer`, `evebitda_vs_ind`, `pe_fwd`, `pe_fwd_peer`, `pe_fwd_vs_ind`, `evebitda_fwd`, `evebitda_fwd_peer`, `evebitda_fwd_vs_ind`, `fp_pe_y1`, `fp_pe_y5`, `fp_rev_cagr_own`, `bo`, `rsi`, `rng`, and `peer` beside `left` and `rr`. Trailing `*_peer` is the TradingView multiple. Forward `*_peer` is the projection and can be off.

## Path class (history, not a score)

Use span `close_pct` and `pt_pct`. Pack `left` must still be live for OPENING.

| Class | Rule |
|---|---|
| OPENING | `n` ≥ 8, `close_pct` ≤ 5, `pt_pct` ≥ −2, pack `left` ≥ 18. Price did not consume the target. |
| DYING | `close_pct` ≥ 8 and `pt_pct` ≤ 2. Price ran, the street target did not. |
| CHEAPER | OPENING is false, and that name's own `val_pct` < −5, with pack `left` ≥ 18 and `rng` < 85. The multiple got cheaper on its own history. |
| THIN_HIST | `n` < 8, or `close_pct` / `pt_pct` missing. |

A name can be CHEAPER and also DYING. DYING wins. Drop it from both orders.

## Two orders

**ASYM** — asymmetric payoff. Keep OPENING and CHEAPER. Drop DYING, THIN_HIST, `rng` ≥ 85, RSI ≥ 70, and `mix` starting with `avoid`. Sort by `rr` descending. Tie-break `bo` descending. Industry cap is the one already on the payoff focus. Publish at most 15. Rank 1 is the first row of that sort.

**CONVICTION** — same survivors, sorted by `bo` descending, tie-break `rr`. Publish at most 10. A high `bo` with a dead path does not enter.

Show `rr`, `left`, `rng`, `peer`, `bo`, `close_pct`, `pt_pct`, `n`, and path class on every ranked row. Do not hide the sort behind a blended figure.

Scan hits already on Build-50 are not new ideas. A survivor absent from Build-50, Top 100, and Book is `BEYOND`. Industry overflow is a sleeve miss.

Weeks scan and model gap stay available, recorded, and unsorted into ASYM:

| Id | Question | Starter |
|---|---|---|
| B weeks | Breakout live, not a chase | `--where "bo>=0.8,rng<=70,rsi<=70" --field bo --top 30 --group-field ind --cap 3` |
| C model | Street vs model disagree | Both pack `left` and `fp_primary_upside_pct` exist and the gap is wide. State the gap. Do not blend them. A terminal many times the street is a data gap. |
| D book | What a drop costs | All Book rows from `book_risk`. No where. |
| E band | Ten names, little downside and the widest upside | `tv_scan_cli.py payoff-focus --csv SHORTLIST.csv --out-dir SUITES/payoff_focus`. Sleeves are discount_momentum and premium_strength. Sort is `band_payoff`, not `rr` and not `bo`. Quote `entry_low` to `entry_high`, `trim_px` / `trim_state`, and `kill_px`. |

## Outside news

Search only after the two orders exist. Cover the session (index, rates, pack leaders and laggards), every ranked name, and Book names with `dte` ≤ 21. Tag `OUTSIDE`. News may move timing or Grade. News may not flip HOLD_NO_ADD, polar EXIT, or a leftover-rich short, set size, or become a sort key. No headline is `no headline`.

## Course

One course per name, same words as Daily. Tags:

| Tag | Meaning |
|---|---|
| WEEK / MONTH / LONG | 0–7d, 8–60d, or a hold that is not a NEW slot. |
| ALGO | Course matches the pack. |
| SUPPORT | Path class or news supports that course. |
| BEYOND | Asym or conviction found it outside the sleeves. Still uses the Daily 0–2 NEW budget. |
| CONFLICT | Course differs. Cite both. Polar and mix gates win. |

Every Plan row still states upside (`left`, `rr`, and `fp_primary_upside_pct` if joined), what is already paid (`rng`, RSI, peer), the cost if wrong (Book `m10_nav` and `wipe_nav`; a new name as starter dollars and −15% of NAV), and one invalidation (SMA50, 1.5×ATR, or c15).

Mark which Daily slots this style would spend. Long Book holds are not extra tickets. Default is no average-down.

Cash raised by a book-risk EXIT or TRIM is spent on this ASYM queue, not on the raw leftover order of an upside catalog. Rank the catalog with `focus --field rr --tie-breaker bo` and the recorded where (`OPENING`, `rng<85`, `rsi<70`, `left>=18`, mix not `avoid_value_trap`, carrier not `edge_only`). Then `entry_levels` from `generic_utils.risk`. A name with `sma_role=reclaim` is a later ticket. A name whose street leftover is a small fraction of pack `left` (`both`, street quote near the price) does not take the first starter ahead of a `street` carrier. Two starters, about $5,000 each. An add to a line already held is about $1,000, toward the 50-day, above `atr_stop`.

## Canvas

Tabs: **Plan / Rank / Scans / News / Long / Ledger**.

- Plan: the few names that take a Daily slot, plus the top of ASYM and CONVICTION that do not.
- Rank: both orders, with path class and the span stats. State the where and the sort.
- Scans: payoff where, history command, weeks, model gap, book, and the ten-name payoff focus. Hit counts.
- News: `OUTSIDE` lines.
- Long: 4–6 holds that passed OPENING or CHEAPER, or a Book name whose path is DYING and therefore is not an add.
- Ledger: ASYM, CONVICTION, OPENING, DYING, CHEAPER, THIN_HIST, plus the usual tags. Once.

Point at `daily-focus-YYYYMMDD`, `movers-YYYYMMDD`, and `book-risk-YYYYMMDD` instead of copying their tables.

## Generic vs domain

`focus`, `history`, and `span` are generic. Which where, which path rule, and the course are domain. After the brief: reused CLI, promoted this run (or none), judgment that stayed here.
