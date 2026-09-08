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
| All-fields whitelist (never SELECT *) | `extract.AF_CANDIDATES` | compile names |
| Weeks profile pivot bo/cont/fwd | recipe `pred.profile_weeks_pivot` | extract_profile_weeks, compare |
| Conviction / mix / rank_overall | recipe `pred.conviction` | extract_conviction |
| Regime fit | recipe `pred.regime` | extract_regime |
| Leftover / 52w range / vs50 / rr | `derive` | sleeves + extract |
| Universe mean day/d5/m1/rsi + pct up | `aggregations.numeric_summary` | `extract.universe_stats` |
| Industry 5D leaders/laggards | `aggregations.group_stats` | `extract.industry_breadth` |
| Top-N leftover | `ranking.top_n` | unpaid sort then cap |
| Cap K per industry, overflow visible | `ranking.group_capped_top_n` | `sleeves.dedupe_by_industry` |
| Caller filter then rank then cap | `focus.build_focus` | FOCUS CSV |
| Named-ticker dossier | lookup CLI | scan-evidence |
| Run-to-run Δbo/Δcont/Δfwd | compare CLI | scan-evidence |
| MTP action counts | `aggregations.value_counts` | compile `_mtp_climate` (also bounce/avoid lists) |
| Sleeve overlap / confirmation count | `aggregations.overlap` + pack `sleeve_tags` | compile `_attach_sleeve_tags` |
| Earnings DTE buckets | `aggregations.histogram` with caller edges `[-1,8,22,61]` | compile `_earnings_lanes` |

`extract.universe_stats` / `industry_breadth` keep the US $2B listing filter
(domain). Mean / pct-up / group-by math is generic. Industry `pct_up` uses
group size (`pct_positive_denom="n"`); universe `pct_up` uses non-missing day.

Headline FOCUS = `radar_curated_25`. Appendix = `radar_upside_100`.
`pack-focus` / `summary` / `group` commands are in SKILL.md.

## Adding a new procedure later

If a later prompt invents SQL or a `_tmp_*.py` for a third time:

1. Formula (leftover, range, vs-ref) → `derive.py`.
2. Reduce / group / count → `aggregations.py`.
3. Stable SELECT against a known table → `recipes.py`.
4. Filter + rank + cap → `focus.py` with caller `--where`.
5. "What is a good stock" → `operator_briefing.sleeves`, not here.
