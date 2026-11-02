# Signal lab and alpha model: follow-ups (from the 2026-10-27 first iteration)

State on 2026-10-27: panel store 140 day files (29 Mar to 27 Oct), signal lab and alpha model running on it,
print pointers on every tape row. Alpha model state: 1d WEAK, 7d WEAK (OOS IC +0.025), 28d WEAK
(+0.054, about +0.13 since August), 56d FAILING (one independent period). Nothing below is done yet.

## More data (each makes the evidence stronger without more agent tokens)

1. **Keep the store growing.** The refresh producer `panel_store` appends each day file. The 28d model needs
   roughly three to four more months before its t can reach 2 at the current IC; re-read `state` monthly.
2. **Re-add three dropped export fields.** `revenue_surprise_percent_fq`, `recommendation_mark` (analyst
   rating) and `price_book_ratio` left the focused all-fields export on 2026-10-14. Revenue surprise is
   derived for now; the analyst rating and P/B have no substitute. Change the focused field list used by
   `export_focused_tradingview_fields_duckdb` (producer change: confirm first).
3. **Backfill profile scores before 25 Jun.** The strongest features (p_def, p_dvc, p_recov, p_fwd) start on
   2026-06-25 because the March-June prediction runs are consensus-only backfills. A prediction-suite pass on
   those day files would add three months to the features that matter most. Multi-hour producer: ask first.
4. **Ingest pack-derived fields per day** (`left`, `rr`, `mix`, `mtp`, GMV lenses, edge scores) into the
   store, so the lab can test the pack's own orders the way it tests raw fields. 28 pack days exist now.
5. **A Europe store.** Non-US names (OMXCOP:NOVO_B, SIX:LOGN, XETR:VUAA) are outside the US floor, so they have
   no forecast. A second store recipe (European markets, quote-currency targets as in earnings_week) closes it.
6. **Consistent scan time.** Day files hold runs from 14:00 to 20:00 UTC and the store takes the newest. A
   `run_pick` knob (newest / nearest to a UTC hour) would make 1d labels cleaner.

## Model work (generic, in alpha_model / signal_lab)

7. **Cluster correlated screener scores.** p_def, p_dvc, p_qvc and p_asym rank names alike (see
   `feature_corr`); the IC weights count that cluster several times. Weight per cluster, then per feature.
8. **Promote the variant sweep** (scratch script on 2026-10-27) into `tv_scan_cli.py alpha-sweep`, writing
   one row per variant and horizon (all / since a date / spread), so the default is re-checked from data.
9. **Regime-conditional weights.** The lab has `ic_up_mkt` / `ic_down_mkt`; a variant that keeps two weight
   sets and picks by the point-in-time regime is a small step.
10. **A print model.** The event study has real structure (sold into + previous print down: +3.1% hold-through,
    t 4.5; B/B drift +1.1%). A forecast per upcoming print (hold-through over 7 / 28 days) from run-up,
    previous reaction, revisions and surprise history would feed earnings_week directly.
11. **Scan entries at the next file.** `scan_study` enters at the pack's own scan close; add a next-file entry
    variant so a list's return excludes the rest of the scan day.
12. **Movers classes.** BOUNCE / CONTINUE_DOWN exist in packs from 22 Oct only (3 days). Re-read
    `scan_summary` (`movers_class`, `movers_tail`) in three weeks: the tails kept falling for two weeks in
    Sep-Oct, so a BOUNCE call has to beat that drift.

## Token use

13. Agents read `PACK/signal_lab/highlights.log` and `PACK/alpha_model/highlights.log` (about 3 KB each)
    instead of tables; `alpha-model --ids A,B` gives one line per name. Add those two calls to the DAILY and
    EARNINGS WEEK prompts once the model state is OK, so no SQL is needed for a model view.

## Still open from earlier

14. Pack `extract.py` price target for non-US names (`price_target_average` is USD; OMXCOP:NOVO_B
    `street_left` -82% should be about +18%). earnings_week already uses `price_target_1y`. Changes pack
    `street_left` for non-US names: confirm first.
