# Main DB Population Flow (CIK Mapping + Financial Concepts)

This is the minimal flow to run from `src/main.py` to populate the core tables for CIK mapping and EDGAR financial concepts.

## What each method does

1. `run_sec_cik_ticker_mapping_workflow()`
   - Fetches SEC company ticker mapping JSON from SEC API.
   - Upserts data into table: `sec_cik_tickers_mapping`.
   - Also ensures/updates `SecondaryTickers` in that table.

2. `batchLoadSecDataParallel(batch_tags)`
   - Reads bulk SEC files (`sub.txt`, `num.txt`, `tag.txt`) per batch folder.
   - Loads facts into table: `edgar_financial_data_concepts`.
   - Uses `sec_cik_tickers_mapping` to map `Cik -> Ticker`.

3. `batchLoadSecTagDataParallel(batch_tags)`
   - Reads tag metadata from each batch `tag.txt`.
   - Upserts into table: `edgar_tag_info`.

4. `batchLoadMissingSecDataUsingArelle(batch_size=100, min_year=2013)`
   - Processes missing filings for CIK/ticker rows not yet checked.
   - Reads pending rows from table: `cik_ticker_checked` (`Checked = false`).
   - Attempts extraction and insert for missing filing facts (Arelle-based flow).
   - Marks processed rows as checked in `cik_ticker_checked`.

## Recommended run order in `main()`

Use this sequence for initial population:

1. `run_sec_cik_ticker_mapping_workflow()`
2. `batchLoadSecDataParallel(batch_tags)`
3. `batchLoadSecTagDataParallel(batch_tags)`

Optional follow-up for missing filings:

4. `insert_all_cik_ticker_checked_from_mapping(default_checked=False)`
5. `batchLoadMissingSecDataUsingArelle(batch_size=500, min_year=2012)`

## Example `main()` snippet

```python
batch_tags = [
    "2012q1", "2012q2", "2012q3", "2012q4",
    "2011q1", "2011q2", "2011q3", "2011q4",
    "2010q1", "2010q2", "2010q3", "2010q4",
]

run_sec_cik_ticker_mapping_workflow()
batchLoadSecDataParallel(batch_tags)
batchLoadSecTagDataParallel(batch_tags)

# Optional missing-filings enrichment
insert_all_cik_ticker_checked_from_mapping(default_checked=False)
batchLoadMissingSecDataUsingArelle(batch_size=500, min_year=2012)
```

## Notes for future use

- `batchLoadSecDataParallel` and `batchLoadSecTagDataParallel` now accept `batch_tags` as input.
- `insert_all_cik_ticker_checked_from_mapping()` populates `cik_ticker_checked` from `sec_cik_tickers_mapping` (required before running `batchLoadMissingSecDataUsingArelle` if `cik_ticker_checked` is empty).
- `process_submission_files_by_cik()` is currently called in `main.py`, but it is not part of the core initial table-population flow above. It is used to correct wrong periods for edgar_financial_data_concepts

- `process_submission_files_by_cik_get_JSON`()  # Process SEC submission JSON files and print results
- this augments the data from the sec_cik_tickers_mapping table with SIC and Exchanges values extracted from the filings, using the new batch update function with chunking for efficiency
- `get_distinct_exchanges_from_csv`()  # Get distinct exchanges from indname.csv and print them
