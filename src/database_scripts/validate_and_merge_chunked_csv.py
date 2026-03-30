from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import TextIO


TEMP_FILE_SUFFIX = ".partial"
CHUNK_FILE_PATTERN = re.compile(r"_chunk_(\d+)$")


def _set_safe_csv_field_size_limit() -> None:
    field_size_limit = sys.maxsize
    while field_size_limit > 0:
        try:
            csv.field_size_limit(field_size_limit)
            return
        except OverflowError:
            field_size_limit //= 10
    raise OverflowError("Unable to configure csv.field_size_limit for this platform.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate chunked CSV files against the original CSV and optionally "
            "merge them back into a single output file."
        )
    )
    parser.add_argument(
        "original_csv",
        type=Path,
        help="Path to the original source CSV file.",
    )
    parser.add_argument(
        "--merged-output",
        type=Path,
        help="Optional output path for a merged CSV reconstructed from the chunks.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing merged output file.",
    )
    return parser.parse_args()


def _chunk_sort_key(path: Path) -> tuple[int, str]:
    match = CHUNK_FILE_PATTERN.search(path.stem)
    if not match:
        return sys.maxsize, path.name
    return int(match.group(1)), path.name


def _discover_chunk_paths(original_csv: Path) -> list[Path]:
    pattern = f"{original_csv.stem}_chunk_*{original_csv.suffix}"
    return sorted(original_csv.parent.glob(pattern), key=_chunk_sort_key)


def _open_csv_reader(csv_path: Path) -> tuple[TextIO, csv.reader]:
    csv_handle = csv_path.open("r", encoding="utf-8-sig", newline="")
    return csv_handle, csv.reader(csv_handle)


def _read_header(reader: csv.reader, csv_path: Path) -> list[str]:
    header = next(reader, None)
    if not header:
        raise ValueError(f"CSV is empty or missing a header row: {csv_path}")
    return header


def _validate_paths(
    original_csv: Path, merged_output: Path | None, overwrite: bool
) -> None:
    if not original_csv.exists():
        raise FileNotFoundError(f"Original CSV does not exist: {original_csv}")
    if not original_csv.is_file():
        raise ValueError(f"Original CSV path is not a file: {original_csv}")
    if merged_output is not None and merged_output.exists() and not overwrite:
        raise FileExistsError(
            f"Merged output already exists: {merged_output}. Re-run with --overwrite to replace it."
        )


def validate_chunked_csv(original_csv: Path) -> dict[str, int | str]:
    _set_safe_csv_field_size_limit()

    chunk_paths = _discover_chunk_paths(original_csv)
    if not chunk_paths:
        raise FileNotFoundError(
            f"No chunk files found next to original CSV: {original_csv}"
        )

    original_handle = None
    try:
        original_handle, original_reader = _open_csv_reader(original_csv)
        original_header = _read_header(original_reader, original_csv)

        data_row_index = 0
        for chunk_index, chunk_path in enumerate(chunk_paths, start=1):
            chunk_handle = None
            try:
                chunk_handle, chunk_reader = _open_csv_reader(chunk_path)
                chunk_header = _read_header(chunk_reader, chunk_path)
                if chunk_header != original_header:
                    raise ValueError(f"Header mismatch in chunk {chunk_path}.")

                expected_chunk_number = chunk_index
                actual_chunk_number, _ = _chunk_sort_key(chunk_path)
                if actual_chunk_number != expected_chunk_number:
                    raise ValueError(
                        f"Chunk numbering is not contiguous at {chunk_path}. "
                        f"Expected chunk {expected_chunk_number:03d}."
                    )

                for chunk_row in chunk_reader:
                    original_row = next(original_reader, None)
                    data_row_index += 1
                    if original_row is None:
                        raise ValueError(
                            f"Chunks contain more data rows than the original CSV. "
                            f"First extra row appears in {chunk_path} at reconstructed row {data_row_index}."
                        )
                    if chunk_row != original_row:
                        raise ValueError(
                            f"Row mismatch at reconstructed data row {data_row_index} from {chunk_path}."
                        )
            finally:
                if chunk_handle is not None and not chunk_handle.closed:
                    chunk_handle.close()

        remaining_original_row = next(original_reader, None)
        if remaining_original_row is not None:
            raise ValueError(
                "Original CSV contains more data rows than the chunk set reconstructs."
            )
    finally:
        if original_handle is not None and not original_handle.closed:
            original_handle.close()

    return {
        "chunk_count": len(chunk_paths),
        "data_row_count": data_row_index,
        "header_column_count": len(original_header),
        "original_csv": str(original_csv),
    }


def merge_chunked_csv(
    original_csv: Path,
    merged_output: Path,
    overwrite: bool = False,
) -> Path:
    _set_safe_csv_field_size_limit()

    chunk_paths = _discover_chunk_paths(original_csv)
    if not chunk_paths:
        raise FileNotFoundError(
            f"No chunk files found next to original CSV: {original_csv}"
        )

    if merged_output.exists() and not overwrite:
        raise FileExistsError(
            f"Merged output already exists: {merged_output}. Re-run with --overwrite to replace it."
        )

    merged_output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = merged_output.with_name(f"{merged_output.name}{TEMP_FILE_SUFFIX}")
    if temp_output.exists():
        temp_output.unlink()

    output_handle = None
    try:
        output_handle = temp_output.open("w", encoding="utf-8-sig", newline="")
        writer = csv.writer(output_handle)

        wrote_header = False
        for chunk_path in chunk_paths:
            chunk_handle = None
            try:
                chunk_handle, chunk_reader = _open_csv_reader(chunk_path)
                chunk_header = _read_header(chunk_reader, chunk_path)
                if not wrote_header:
                    writer.writerow(chunk_header)
                    wrote_header = True
                for row in chunk_reader:
                    writer.writerow(row)
            finally:
                if chunk_handle is not None and not chunk_handle.closed:
                    chunk_handle.close()

        output_handle.close()
        output_handle = None
        temp_output.replace(merged_output)
        return merged_output
    except Exception:
        if output_handle is not None and not output_handle.closed:
            output_handle.close()
        if temp_output.exists():
            temp_output.unlink()
        raise


def main() -> None:
    args = _parse_args()
    _validate_paths(args.original_csv, args.merged_output, args.overwrite)

    summary = validate_chunked_csv(args.original_csv)
    print(
        "Validation passed: "
        f"{summary['chunk_count']} chunk file(s), "
        f"{summary['data_row_count']} data row(s), "
        f"{summary['header_column_count']} header column(s)."
    )

    if args.merged_output is not None:
        merged_path = merge_chunked_csv(
            original_csv=args.original_csv,
            merged_output=args.merged_output,
            overwrite=args.overwrite,
        )
        print(f"Merged CSV written to: {merged_path}")


if __name__ == "__main__":
    main()
