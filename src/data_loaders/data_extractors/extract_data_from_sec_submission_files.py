import json
from pathlib import Path
from typing import Optional
from db.connection_provider import get_mysql_connection
from db.edgar_financial_data_concepts_operations import (
    get_edgar_financial_data_concepts_adshs_sorted_periodend,
    update_fiscal_period_for_entries,
)
from db.sec_cik_tickers_mapping_operations import read_sec_cik_ticker_for_cik


def process_submission_files_by_cik(
    submissions_dir: Optional[Path] = None,
    cik_filter: Optional[set[str]] = None,
    file_limit: Optional[int] = None,
):
    """
    Iterate SEC submission JSON files from `savedData/submissions_by_cik`
    and run a custom processor for each file.
    json file name format: CIK0000004962.json (CIK zero-padded to 10 digits)

    Args:
        submissions_dir: Optional override path. Defaults to project
            `savedData/submissions_by_cik`.
        cik_filter: Optional set of CIK strings (with or without `CIK` prefix)
            to restrict processed files.
        file_limit: Optional max number of files to process.

    Returns:
        A list of dictionaries with:
        - `file_path`: absolute file path
        - `cik`: extracted CIK (10-digit string)
        - `result`: return value from `processor` when successful
        - `error`: error text when processing failed
    """
    project_root = Path(__file__).resolve().parents[3]
    target_dir = submissions_dir or (project_root / "savedData" / "submissions_by_cik")

    if not target_dir.exists() or not target_dir.is_dir():
        raise FileNotFoundError(f"submissions_by_cik directory not found: {target_dir}")

    normalized_filter: Optional[set[str]] = None
    if cik_filter:
        normalized_filter = {
            str(cik).replace("CIK", "").zfill(10) for cik in cik_filter
        }

    if normalized_filter:
        files_to_process = [
            target_dir / f"CIK{cik_10}.json"
            for cik_10 in sorted(normalized_filter)
            if (target_dir / f"CIK{cik_10}.json").exists()
        ]
    else:
        files_to_process = sorted(target_dir.glob("CIK*.json"))

    processed_count = 0
    missing_fillings_cnt = 0
    connection = get_mysql_connection()

    for file_path in files_to_process:
        cik = file_path.stem.replace("CIK", "").lstrip("0")

        if file_limit is not None and processed_count >= file_limit:
            break

        try:
            with file_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)

            result_from_json_file = process_cik_submissions_file(payload)

            cik_ticker = read_sec_cik_ticker_for_cik(connection, cik)

            result_from_database = (
                get_edgar_financial_data_concepts_adshs_sorted_periodend(
                    cik, cik_ticker
                )
            )

            validate_lists_result = lists_if_in_sync(
                result_from_json_file, result_from_database
            )

            if not validate_lists_result.get("result", False):
                missing_fillings_cnt += 1

            if validate_lists_result.get("adshs_to_fix"):
                print(
                    f"Found adshs with mismatched fiscal periods for CIK {cik} ({cik_ticker}). Updating..."
                )
                for entry in validate_lists_result["adshs_to_fix"]:
                    adsh = entry["adsh"]
                    expected_period = entry["expected_period"]
                    update_fiscal_period_for_entries(
                        cik, cik_ticker, adsh, expected_period
                    )

        except Exception as exc:
            raise exc

        processed_count += 1

    connection.close()

    return missing_fillings_cnt


def process_cik_submissions_file(payload):
    recent = payload.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber") or []
    forms = recent.get("form") or []
    total = len(accessions)

    filtered = []
    for i in range(total):
        accession = accessions[i]
        form = forms[i] if i < len(forms) else None
        if form in ["10-K", "10-Q", "20-F", "40-F", "20-F/A", "40-F/A"]:
            filtered.append(
                {
                    "adsh": accession,
                    "form": form,
                }
            )

    return filtered


def lists_if_in_sync(json_list, db_list):
    """
    Find the largest matching suffix from the end of both lists.
    Then, from index 0 up to the start of that suffix, check if all elements match at each index.
    If all match, return True; otherwise, return the first mismatched adsh from db_list.
    """
    fiscal_periods = ["Q3", "Q2", "Q1", "FY"]

    if not db_list or not json_list:
        return {"result": False}

    # Step 1: Find the largest matching suffix
    min_len = min(len(json_list), len(db_list))
    suffix_len = 0
    for offset in range(1, min_len + 1):
        if json_list[-offset].get("adsh") == db_list[-offset][0]:
            suffix_len += 1
        else:
            break

    # Step 2: Check all elements up to the start of the matching suffix
    start_idx_period = None
    period_count = len(fiscal_periods)
    for idx, db_row in enumerate(db_list):
        db_period_candidate = str(db_row[3] or "").strip().upper()
        if db_period_candidate in fiscal_periods:
            start_idx_period = (
                fiscal_periods.index(db_period_candidate) - idx
            ) % period_count
            break

    if start_idx_period is None:
        start_idx_period = 0

    adshs_to_fix = []

    check_len = min(len(json_list), len(db_list)) - suffix_len
    for i in range(check_len):
        db_period = str(db_list[i][3] or "").strip().upper()
        adsh = db_list[i][0]

        expected_period = fiscal_periods[(start_idx_period + i) % period_count]

        if db_period != expected_period:
            adshs_to_fix.append(
                {
                    "adsh": adsh,
                    "expected_period": expected_period,
                }
            )
        if adsh != json_list[i].get("adsh"):
            return {
                "result": False,
                "db_adsh": db_list[i],
                "adshs_to_fix": adshs_to_fix,
            }

    return {
        "result": True,
        "adshs_to_fix": adshs_to_fix,
    }
