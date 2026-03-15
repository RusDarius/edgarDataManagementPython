import csv
import os
from typing import List, Set


def _default_indname_csv_path() -> str:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(project_root, "savedData", "indname.csv")


def _extract_exchange(exchange_ticker: str) -> str:
    # Expected shape: "EXCHANGE:TICKER" (e.g., "NYSE:ABC").
    exchange, _, _ = exchange_ticker.partition(":")
    return exchange.strip()


def _read_distinct_exchanges_with_encoding(source_path: str, encoding: str) -> Set[str]:
    exchanges: Set[str] = set()
    with open(source_path, "r", encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_value = (row.get("Exchange:Ticker") or "").strip()
            if not raw_value:
                continue
            exchange = _extract_exchange(raw_value)
            if exchange:
                exchanges.add(exchange)
    return exchanges


def get_distinct_exchanges_from_csv(csv_path: str | None = None) -> List[str]:
    """Return distinct exchanges from the CSV Exchange:Ticker column."""
    source_path = csv_path or _default_indname_csv_path()
    encodings_to_try = ("utf-8-sig", "cp1252", "latin-1")
    last_error: UnicodeDecodeError | None = None

    for encoding in encodings_to_try:
        try:
            exchanges = _read_distinct_exchanges_with_encoding(source_path, encoding)
            return sorted(exchanges)
        except UnicodeDecodeError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error

    return []
