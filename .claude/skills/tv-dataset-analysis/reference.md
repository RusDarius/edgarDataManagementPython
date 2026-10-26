# TV dataset analysis — procedure inventory

Catalog of fetch / aggregate / prune procedures to reuse instead of
regenerating SQL or `logs/_tmp_*.py`. Judgment (ADD vs WAIT vs PASS,
High/Med/Low) stays in daily-operator-scan.

Typical agent mistakes this layer replaces: guessing table names,
`SELECT *` / wrong mix column, hand-rolled top-N + industry cap,
reimplementing leftover/range/vs50, industry breadth, and histograms.

## Layers

```
generic_utils.ranking / aggregations / predicates / derive / recipes / focus
        ^
        |  (zero cutoff; caller supplies field / where / bins / cap)
        |
operator_briefing.sleeves / extract / compile / stance
        ^
        |  (ONE scan's eligibility + continuity + suggested_conviction)
        |
daily-operator-scan canvas  (operator High/Med/Low)
```

## Procedures

| Procedure | Generic home | Domain consumer |
|---|---|---|
| Lock newest pred / all-fields / edge / MTP / holdings / priors | `inspect` / `discovery.lock_sources` | compile |
| Table + column inventory | `tv_scan_cli.py inventory` | inspect |
| All-fields whitelist (never SELECT *) | `extract.AF_CANDIDATES` (tape + `pe`/`pe_fwd`/`peg`/`evebitda`/`pb`/`evfcf`/`evrev`) | compile names |
| Weeks profile pivot bo/cont/fwd | recipe `pred.profile_weeks_pivot` | extract_profile_weeks, compare |
| Conviction / mix / rank_overall | recipe `pred.conviction` | extract_conviction |
| Regime fit | recipe `pred.regime` | extract_regime |
| Leftover / 52w range / vs50 / rr | `derive` | sleeves + extract |
| Street PT $ / pack leftover $ | `derive.implied_price` (`street_px`, `target_px`) | compile names + movers keep |
| Pct vs industry median (EV/Rev, PE, leftover) | `derive.attach_vs_group` | movers Tape Peer input |
| Primary value / peer / tech | `setup.attach_setup` | Daily Value, movers Val/Peer/Tech, RISK Setup |
| Street vs pack vs model forward | `forward_value.attach_forward_value` / `tv_scan_cli.py forward` | Value dump, FORWARD suite |
| Manual all-suite DuckDB dumps | `src/run_operator_suites.py` | wisdom / --list (cover 1000, no industry cap) |
| Universe mean day/d5/m1/rsi + pct up | `aggregations.numeric_summary` | `extract.universe_stats` |
| Industry 5D leaders/laggards | `aggregations.group_stats` | `extract.industry_breadth` |
| Both-tail movers (day/w/5D/1M) | `ranking.horizon_movers` / `movers --recipe` (25 up / 25 punished, `vs_group`; `drop_duplicate_suffixes`) | daily-focus Tape Val/Peer/Proj/Tech + down_class |
| Book NAV / loss ladders | `risk.attach_book_risk` / `tv_scan_cli.py risk --run latest` (`book_risk.md` in `--out-dir`) | Book wt_nav, wipe, m15_nav, trim cash; canvas `book-risk-YYYYMMDD` |
| DuckDB + overview.log | `run_export.write_run_export` | `briefing_pack.duckdb` |
| Top-N leftover | `ranking.top_n` | unpaid sort then cap |
| Cap K per industry, overflow visible | `ranking.group_capped_top_n` | `sleeves.dedupe_by_industry` |
| Caller filter then rank then cap | `focus.build_focus` | FOCUS CSV |
| Named-column fetch (never SELECT *) | `series.fetch_named` / `named` CLI | lookup fallback |
| Oldest→latest field series | `series.field_history` / `history` CLI | EV/Rev, price, score paths |
| First/last/Δ per id | `series.series_span` / `span` CLI | rerating vs price |
| Weighted group (capital) | `aggregations.weighted_group_stats` | Book industry exposure |
| Join two CSVs on ticker | `series.join_rows` / `join` CLI | tape onto Book |
| Refresh leftover from a later close | `derive.apply_derived(..., refresh=True)` | Friday PT × today close |
| Named-ticker dossier | lookup CLI | scan-evidence |
| Run-to-run Δbo/Δcont/Δfwd | compare CLI | scan-evidence |
| MTP action counts | `aggregations.value_counts` | compile `_mtp_climate` (also bounce/avoid lists) |
| Sleeve overlap / confirmation count | `aggregations.overlap` + pack `sleeve_tags` | compile `_attach_sleeve_tags` |
| Earnings DTE buckets | `aggregations.histogram` with caller edges `[-1,8,22,61]` | compile `_earnings_lanes` |

`extract.universe_stats` / `industry_breadth` keep the US $2B listing filter
(domain). Mean / pct-up / group-by math is generic. Industry `pct_up` uses
group size (`pct_positive_denom="n"`); universe `pct_up` uses non-missing day.

Headline FOCUS = `radar_curated_50` (Build-50). First 25 = `radar_curated_25`.
Appendix = `radar_upside_100`.
Layer map: `src/generic_utils/specs/scoring_layer_order.md`.
`pack-focus` / `summary` / `group` commands are in SKILL.md.

## Adding a new procedure later

If a later prompt invents SQL or a `_tmp_*.py` for a third time:

1. Formula (leftover, range, vs-ref) → `derive.py`.
2. Reduce / group / count / weighted group → `aggregations.py`.
3. Stable SELECT against a known table → `recipes.py`.
4. Filter + rank + cap → `focus.py` with caller `--where`.
5. Dated DuckDB walk / named-column history / span → `series.py`.
6. "What is a good stock" → `operator_briefing.sleeves`, not here.
