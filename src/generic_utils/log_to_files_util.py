from pathlib import Path
import csv
from typing import Union


def log_to_file(
    file_location: Union[str, Path], text: str, add_newline: bool = True
) -> None:
    """
    Append text to a log file.

    - If the file exists, content is appended.
    - If the file does not exist, it is created.

    Args:
            file_location: Full path to the log file.
            text: Text content to write.
            add_newline: When True, appends a trailing newline after text.
    """
    path = Path(file_location)
    path.parent.mkdir(parents=True, exist_ok=True)

    message = f"{text}\n" if add_newline else text

    with path.open("a", encoding="utf-8", buffering=8192) as log_file:
        log_file.write(message)


def log_rows_to_csv(
    file_location: Union[str, Path],
    headers: list[str],
    rows: list[list[str]],
) -> None:
    """
    Write tabular data to a CSV file, replacing any existing file content.

    Args:
            file_location: Full path to the CSV file.
            headers: Column names for the CSV header row.
            rows: Row values. Each row must have the same number of items as headers.
    """
    path = Path(file_location)
    path.parent.mkdir(parents=True, exist_ok=True)

    header_count = len(headers)
    for index, row in enumerate(rows, start=1):
        if len(row) != header_count:
            raise ValueError(
                f"CSV row {index} has {len(row)} values but expected {header_count}."
            )

    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)
        writer.writerows(rows)
