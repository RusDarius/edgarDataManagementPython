from __future__ import annotations

from typing import Any, Iterable, Sequence


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def normalize_string_list(values: Iterable[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    normalized: list[str] = []
    for value in values:
        stripped = str(value).strip()
        if stripped:
            normalized.append(stripped.upper())
    return tuple(normalized)


def normalize_market_list(values: Iterable[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    normalized: list[str] = []
    for value in values:
        stripped = str(value).strip().lower()
        if stripped:
            normalized.append(stripped)
    return tuple(normalized)


def expand_us_aliases(countries: Sequence[str], us_only: bool) -> tuple[str, ...]:
    resolved = list(countries)
    if us_only:
        for alias in ("US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"):
            if alias not in resolved:
                resolved.append(alias)
    return tuple(resolved)


def build_region_filter_sql(
    *,
    country_expr: str,
    exchange_expr: str,
    countries: Sequence[str],
    exchanges: Sequence[str],
) -> str:
    filters: list[str] = []
    if countries:
        literals = ", ".join(_q(value.upper()) for value in countries)
        filters.append(f"UPPER(COALESCE({country_expr}, '')) IN ({literals})")
    if exchanges:
        literals = ", ".join(_q(value.upper()) for value in exchanges)
        filters.append(f"UPPER(COALESCE({exchange_expr}, '')) IN ({literals})")
    return " AND ".join(filters) if filters else "1 = 1"


def build_market_filter_sql(*, market_expr: str, markets: Sequence[str]) -> str:
    if not markets:
        return "1 = 1"
    literals = ", ".join(_q(value.lower()) for value in markets)
    return f"LOWER(COALESCE({market_expr}, '')) IN ({literals})"


def table_columns(connection: Any, table_name: str) -> set[str]:
    rows = connection.execute(
        f"PRAGMA table_info({_q(table_name)})"
    ).fetchall()
    return {str(row[1]) for row in rows}


def snapshot_has_market_column(
    connection: Any,
    *,
    table_name: str = "symbol_day_feature_snapshot",
) -> bool:
    return "market" in table_columns(connection, table_name)


def snapshot_market_select_sql(
    alias: str,
    connection: Any,
    *,
    table_name: str = "symbol_day_feature_snapshot",
) -> str:
    if snapshot_has_market_column(connection, table_name=table_name):
        return f"{alias}.market"
    return "CAST(NULL AS VARCHAR) AS market"


def resolve_snapshot_market_filter(
    connection: Any,
    *,
    alias: str,
    markets: Sequence[str],
    table_name: str = "symbol_day_feature_snapshot",
) -> tuple[str, bool]:
    """Return SQL filter and whether a market filter was actually applied."""
    if not markets:
        return "1 = 1", False
    if not snapshot_has_market_column(connection, table_name=table_name):
        return "1 = 1", False
    return (
        build_market_filter_sql(market_expr=f"{alias}.market", markets=markets),
        True,
    )
