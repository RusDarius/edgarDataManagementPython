from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Iterable, TextIO


PATH_EXAMPLE = Path(
    "d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis/trading_view_all_fields_data/29_03_2026/tradingview_global_all_tdfields_29_03_2026.csv"
)
DEFAULT_MAX_ROWS_PER_CHUNK = 10_000
HARD_MAX_ROWS_PER_CHUNK = 500_000
TEMP_FILE_SUFFIX = ".partial"


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
            "Split a large CSV into smaller CSV files in the same folder while "
            "preserving the header row in every chunk."
        )
    )
    parser.add_argument(
        "input_csv",
        type=Path,
        help="Path to the source CSV file to split.",
    )
    parser.add_argument(
        "--max-rows-per-chunk",
        type=int,
        default=DEFAULT_MAX_ROWS_PER_CHUNK,
        help=(
            "Maximum data rows per output file, excluding the header. "
            f"Default: {DEFAULT_MAX_ROWS_PER_CHUNK}. Hard cap: {HARD_MAX_ROWS_PER_CHUNK}."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace any existing chunk files that match the target naming pattern.",
    )
    return parser.parse_args()


def _validate_max_rows_per_chunk(max_rows_per_chunk: int) -> None:
    if max_rows_per_chunk <= 0:
        raise ValueError("max_rows_per_chunk must be greater than zero.")
    if max_rows_per_chunk > HARD_MAX_ROWS_PER_CHUNK:
        raise ValueError(f"max_rows_per_chunk cannot exceed {HARD_MAX_ROWS_PER_CHUNK}.")


def _build_chunk_path(input_csv: Path, chunk_index: int) -> Path:
    return input_csv.with_name(
        f"{input_csv.stem}_chunk_{chunk_index:03d}{input_csv.suffix}"
    )


def _existing_chunk_paths(input_csv: Path) -> list[Path]:
    pattern = f"{input_csv.stem}_chunk_*{input_csv.suffix}"
    return sorted(input_csv.parent.glob(pattern))


def _ensure_output_targets_available(input_csv: Path, overwrite: bool) -> None:
    existing_paths = _existing_chunk_paths(input_csv)
    if existing_paths and not overwrite:
        formatted_paths = "\n".join(str(path) for path in existing_paths[:10])
        raise FileExistsError(
            "Chunk files already exist for this source CSV. "
            "Re-run with --overwrite to replace them. Existing files include:\n"
            f"{formatted_paths}"
        )

    if overwrite:
        for path in existing_paths:
            path.unlink()


def _open_csv_reader(input_csv: Path) -> tuple[TextIO, csv.reader]:
    input_handle = input_csv.open("r", encoding="utf-8-sig", newline="")
    reader = csv.reader(input_handle)
    return input_handle, reader


def _open_chunk_writer(
    chunk_path: Path, header: list[str]
) -> tuple[TextIO, csv.writer, Path]:
    temp_path = chunk_path.with_name(f"{chunk_path.name}{TEMP_FILE_SUFFIX}")
    output_handle = temp_path.open("w", encoding="utf-8-sig", newline="")
    writer = csv.writer(output_handle)
    writer.writerow(header)
    return output_handle, writer, temp_path


def split_csv_by_rows(
    input_csv: Path,
    max_rows_per_chunk: int = DEFAULT_MAX_ROWS_PER_CHUNK,
    overwrite: bool = False,
) -> list[Path]:
    _validate_max_rows_per_chunk(max_rows_per_chunk)

    if not input_csv.exists():
        raise FileNotFoundError(f"Source CSV does not exist: {input_csv}")
    if not input_csv.is_file():
        raise ValueError(f"Source path is not a file: {input_csv}")

    _ensure_output_targets_available(input_csv, overwrite=overwrite)

    _set_safe_csv_field_size_limit()

    created_paths: list[Path] = []
    input_handle = None
    output_handle = None
    current_temp_path: Path | None = None

    try:
        input_handle, reader = _open_csv_reader(input_csv)
        header = next(reader, None)
        if not header:
            raise ValueError(
                f"Source CSV is empty or missing a header row: {input_csv}"
            )

        chunk_index = 0
        rows_written_to_chunk = 0
        writer: csv.writer | None = None
        chunk_path: Path | None = None

        for row in reader:
            if writer is None or rows_written_to_chunk >= max_rows_per_chunk:
                if (
                    output_handle is not None
                    and current_temp_path is not None
                    and chunk_path is not None
                ):
                    output_handle.close()
                    output_handle = None
                    current_temp_path.replace(chunk_path)
                    created_paths.append(chunk_path)
                    current_temp_path = None

                chunk_index += 1
                chunk_path = _build_chunk_path(input_csv, chunk_index)
                output_handle, writer, current_temp_path = _open_chunk_writer(
                    chunk_path, header
                )
                rows_written_to_chunk = 0

            writer.writerow(row)
            rows_written_to_chunk += 1

        if (
            output_handle is not None
            and current_temp_path is not None
            and chunk_path is not None
        ):
            output_handle.close()
            output_handle = None
            current_temp_path.replace(chunk_path)
            created_paths.append(chunk_path)
            current_temp_path = None
    except Exception:
        if output_handle is not None and not output_handle.closed:
            output_handle.close()
        if current_temp_path is not None and current_temp_path.exists():
            current_temp_path.unlink()
        raise
    finally:
        if input_handle is not None and not input_handle.closed:
            input_handle.close()

    return created_paths


def _format_summary(paths: Iterable[Path]) -> str:
    paths = list(paths)
    if not paths:
        return "No chunk files were created."
    return "\n".join(str(path) for path in paths)


def main() -> None:
    args = _parse_args()
    created_paths = split_csv_by_rows(
        input_csv=args.input_csv,
        max_rows_per_chunk=args.max_rows_per_chunk,
        overwrite=args.overwrite,
    )
    print(f"Created {len(created_paths)} chunk file(s):")
    print(_format_summary(created_paths))


if __name__ == "__main__":
    main()
