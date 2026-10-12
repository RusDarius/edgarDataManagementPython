from typing import Any

from db import trading_view_company_data_map_operations as operations


class _FakeCursor:
    def __init__(self) -> None:
        self.sql = ""
        self.params: tuple[Any, ...] = ()

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.sql = sql
        self.params = params

    def fetchone(self) -> dict[str, Any]:
        return {"symbol": "MMS", "exchange": "NYSE"}


class _FakeConnection:
    def __init__(self) -> None:
        self.last_cursor = _FakeCursor()
        self.closed = False

    def cursor(self, **_: Any) -> _FakeCursor:
        return self.last_cursor

    def close(self) -> None:
        self.closed = True


def test_qualified_company_lookup_filters_symbol_and_exchange(monkeypatch: Any) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(
        operations,
        "get_mysql_connection",
        lambda **_: connection,
    )

    row = operations.get_trading_view_company_by_symbol("NYSE:MMS")

    assert row == {"symbol": "MMS", "exchange": "NYSE"}
    assert "UPPER(COALESCE(exchange, '')) = %s" in connection.last_cursor.sql
    assert connection.last_cursor.params == (
        "NYSE:MMS",
        "MMS",
        "NYSE",
        "NYSE:MMS",
    )
    assert connection.closed


def test_bare_company_lookup_remains_intentionally_exchange_agnostic(
    monkeypatch: Any,
) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(
        operations,
        "get_mysql_connection",
        lambda **_: connection,
    )

    operations.get_trading_view_company_by_symbol("MMS")

    assert "SUBSTRING_INDEX(symbol, ':', -1)" in connection.last_cursor.sql
    assert connection.last_cursor.params == ("MMS", "MMS", "MMS")
