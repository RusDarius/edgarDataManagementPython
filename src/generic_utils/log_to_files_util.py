from pathlib import Path
import csv
import functools
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Union

_LOCAL = threading.local()


def log_to_file(
    file_location: Union[str, Path], text: str, add_newline: bool = True
) -> None:
    """
    Append text to a log file.

    - If the file exists, content is appended.
    - If the file does not exist, it is created.
    - Inside ``buffered_logs()`` the line is held in memory and written with the
      rest of that file's lines when the block exits (same final content).

    Args:
            file_location: Full path to the log file.
            text: Text content to write.
            add_newline: When True, appends a trailing newline after text.
    """
    message = f"{text}\n" if add_newline else text
    buffers = getattr(_LOCAL, "buffers", None)
    if buffers is not None:
        buffers.setdefault(Path(file_location), []).append(message)
        return

    path = Path(file_location)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8", buffering=8192) as log_file:
        log_file.write(message)


@contextmanager
def buffered_logs() -> Iterator[None]:
    """Collect ``log_to_file`` appends in memory and write each file once on exit.

    Lines keep their call order per file, so the file ends up byte-identical to
    unbuffered appends. Use it only around code that appends (a reset before the
    block is fine; reading or truncating the same file inside the block is not).
    Nested blocks share the outer buffer. Lines are flushed even if the block raises.
    Buffers are per thread.
    """
    if getattr(_LOCAL, "buffers", None) is not None:
        yield
        return
    _LOCAL.buffers = {}
    try:
        yield
    finally:
        buffers, _LOCAL.buffers = _LOCAL.buffers, None
        for path, parts in buffers.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as log_file:
                log_file.write("".join(parts))


def buffer_log_writes(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: run ``fn`` inside ``buffered_logs()`` (one write per log file per call)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with buffered_logs():
            return fn(*args, **kwargs)

    return wrapper


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
