from pathlib import Path

from financial_projection.config import DEFAULT_ALL_FIELDS_ROOT
from financial_projection.load_all_fields import resolve_all_fields_day_database
from financial_projection.sources import (
    normalize_symbols,
    parse_symbols_csv,
    resolve_symbols_argument,
)


def test_parse_and_normalize_symbols() -> None:
    assert parse_symbols_csv("NASDAQ:AAPL, NYSE:CRM,,NASDAQ:AAPL") == [
        "NASDAQ:AAPL",
        "NYSE:CRM",
    ]
    assert normalize_symbols([" NASDAQ:MSFT ", "", "NASDAQ:MSFT"]) == ["NASDAQ:MSFT"]


def test_resolve_symbols_merges_sources(tmp_path: Path) -> None:
    # Without a real prediction DB, only CSV/explicit merge is tested here.
    resolved = resolve_symbols_argument(
        symbols=["NASDAQ:AAPL"],
        symbols_csv="NYSE:CRM,NASDAQ:AAPL",
    )
    assert resolved == ["NASDAQ:AAPL", "NYSE:CRM"]


def test_resolve_all_fields_day_database_01_07_2026() -> None:
    path = resolve_all_fields_day_database("01_07_2026")
    assert path.name == "tradingview_all_fields_01_07_2026.duckdb"
    assert path.parent.name == "01_07_2026"
    assert path.exists()
    assert path.is_relative_to(DEFAULT_ALL_FIELDS_ROOT.resolve())