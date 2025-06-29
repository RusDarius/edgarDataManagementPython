from data_loaders.sec_api_loader import (
    fetch_and_parse_submission,
    fetch_and_parse_submission_enhanced,
)


def fetch_concepts_for_cik_from_sec():
    # Example test: Fetch and parse FDX 10-Q Q2 2023 for RevenueFromContractWithCustomerExcludingAssessedTax
    # 723125 MU
    # 1048911 FDX
    cik = "723125"  # MU
    form_type = "10-Q"
    fiscal_period = "Q1"
    fiscal_year = 2025
    fact_tag = "RevenueFromContractWithCustomerExcludingAssessedTax"

    print(
        f"Fetching and parsing {fact_tag} from {form_type} {fiscal_period} {fiscal_year} for CIK {cik}"
    )

    # Use enhanced extraction with period validation
    result = fetch_and_parse_submission_enhanced(
        cik, form_type, fiscal_period, fiscal_year, fact_tag
    )

    if result.get("fact_value"):
        print(f"Enhanced method - Found fact value: {result.get('fact_value')}")
        print(f"Unit: {result.get('fact_unit')}")
        print(f"Context: {result.get('fact_context')}")
        print(f"Period start: {result.get('fact_period_start')}")
        print(f"Period end: {result.get('fact_period_end')}")
        print(f"Period validated: {result.get('period_validation')}")
        print(f"All contexts found: {result.get('all_contexts_found', [])}")
        print(f"Decimals: {result.get('fact_decimals')}")

        # Print the actual value if possible
        if (
            result.get("fact_value") is not None
            and result.get("fact_decimals") is not None
        ):
            try:
                value = float(result["fact_value"])
                decimals = int(result["fact_decimals"])
                actual_value = value * (10 ** abs(decimals)) if decimals < 0 else value
                period_status = (
                    "VALIDATED" if result.get("period_validation") else "NOT VALIDATED"
                )
                print(
                    f"Actual value: {actual_value} {result.get('fact_unit')} ({period_status})"
                )
            except Exception as e:
                print(f"Could not compute actual value: {e}")
    elif result.get("error"):
        print(f"Enhanced method error: {result.get('error')}")
    else:
        print("Enhanced method: No value or error found.")

    # Original method for comparison (optional)
    print("\n" + "=" * 50)
    print("COMPARISON WITH ORIGINAL METHOD:")
    original_result = fetch_and_parse_submission(
        cik, form_type, fiscal_period, fiscal_year, fact_tag
    )

    if original_result.get("fact_value"):
        print(f"Original method value: {original_result.get('fact_value')}")
        print(f"Original method context: {original_result.get('fact_context')}")
    else:
        print(
            f"Original method error: {original_result.get('error', 'No value found')}"
        )

    # Optional: save the document for further analysis
    if False and result.get("document_text"):
        import os

        save_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "savedData")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(
            save_dir, f"{cik}_{form_type}_{fiscal_period}_{fiscal_year}.html"
        )
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(result.get("document_text", ""))
        print(f"Saved document to {save_path}")
