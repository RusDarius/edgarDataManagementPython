from __future__ import annotations

import csv
import shutil
from pathlib import Path

from data_analysis_scripts.trading_view_field_semantic_classifier import (
    EXACT_RULES,
    clean_display_name,
    explain_field,
    parse_name,
)


CSV_PATH = Path(
    r"d:\FinanceProjects\edgarDataManagementPython\savedData\trading_view_stock_fields.csv"
)
BACKUP_PATH = CSV_PATH.with_name(
    "trading_view_stock_fields.backup_before_enrichment.csv"
)


def enrich_csv(csv_path: Path) -> tuple[int, int]:
    with csv_path.open("r", newline="", encoding="utf-8-sig") as infile:
        reader = csv.DictReader(infile)
        original_fieldnames = reader.fieldnames or []
        rows = list(reader)

    if "explanation" not in original_fieldnames:
        original_fieldnames.append("explanation")
    if "model_use" not in original_fieldnames:
        original_fieldnames.append("model_use")

    exact_count = 0
    inferred_count = 0

    for row in rows:
        name_key = "Name" if "Name" in row else "\ufeffName"
        raw_name = row.get(name_key, "")
        display_name = clean_display_name(row.get("Display name", ""))
        type_name = row.get("Type", "")
        base_name, timeframe, lag = parse_name(raw_name)

        used_exact = base_name in EXACT_RULES
        explanation, model_use = explain_field(
            base_name, display_name, type_name, timeframe, lag
        )
        row["explanation"] = explanation
        row["model_use"] = model_use
        if used_exact:
            exact_count += 1
        else:
            inferred_count += 1

    temp_path = csv_path.with_suffix(".tmp")
    with temp_path.open("w", newline="", encoding="utf-8-sig") as outfile:
        writer = csv.DictWriter(
            outfile, fieldnames=original_fieldnames, quoting=csv.QUOTE_ALL
        )
        writer.writeheader()
        writer.writerows(rows)

    if not BACKUP_PATH.exists():
        shutil.copy2(csv_path, BACKUP_PATH)

    temp_path.replace(csv_path)
    return exact_count, inferred_count


def main() -> None:
    exact_count, inferred_count = enrich_csv(CSV_PATH)
    print(f"Updated: {CSV_PATH}")
    print(f"Backup:  {BACKUP_PATH}")
    print(f"Rows using exact rules: {exact_count}")
    print(f"Rows using inferred rules: {inferred_count}")


if __name__ == "__main__":
    main()
