from __future__ import annotations

from typing import Any

from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection


COMPANY_LOOKUP_FIELDS = """
    internal_id,
    symbol,
    name,
    exchange,
    description,
    type,
    typespecs,
    market_cap_basic,
    fundamental_currency_code,
    market,
    kind,
    change_pct,
    perf_w,
    perf_1m,
    perf_y,
    perf_ytd,
    perf_6m,
    perf_5y,
    updated_at
"""


def get_trading_view_company_by_internal_id(internal_id: int) -> dict[str, Any] | None:
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                f"""
                SELECT {COMPANY_LOOKUP_FIELDS}
                FROM trading_view_company_data_map
                WHERE internal_id = %s
                """,
                (internal_id,),
            )
            return cursor.fetchone()
    finally:
        conn.close()


def get_trading_view_company_by_symbol(symbol: str) -> dict[str, Any] | None:
    normalized_symbol = symbol.strip()
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                f"""
                SELECT {COMPANY_LOOKUP_FIELDS}
                FROM trading_view_company_data_map
                WHERE symbol = %s
                   OR symbol = SUBSTRING_INDEX(%s, ':', -1)
                ORDER BY
                    CASE
                        WHEN symbol = %s THEN 0
                        WHEN symbol = SUBSTRING_INDEX(%s, ':', -1) THEN 1
                        ELSE 2
                    END,
                    market_cap_basic DESC,
                    symbol ASC
                LIMIT 1
                """,
                (
                    normalized_symbol,
                    normalized_symbol,
                    normalized_symbol,
                    normalized_symbol,
                ),
            )
            return cursor.fetchone()
    finally:
        conn.close()


def get_trading_view_companies_by_internal_ids(
    internal_ids: list[int],
) -> list[dict[str, Any]]:
    if not internal_ids:
        return []

    placeholders = ", ".join(["%s"] * len(internal_ids))
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                f"""
                SELECT {COMPANY_LOOKUP_FIELDS}
                FROM trading_view_company_data_map
                WHERE internal_id IN ({placeholders})
                ORDER BY symbol
                """,
                tuple(internal_ids),
            )
            return cursor.fetchall()
    finally:
        conn.close()


def search_trading_view_companies(query: str, limit: int = 25) -> list[dict[str, Any]]:
    normalized_limit = max(1, min(int(limit), 200))
    search_term = f"%{query.strip()}%"

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                f"""
                SELECT {COMPANY_LOOKUP_FIELDS}
                FROM trading_view_company_data_map
                WHERE symbol LIKE %s OR name LIKE %s
                ORDER BY market_cap_basic DESC, symbol ASC
                LIMIT %s
                """,
                (search_term, search_term, normalized_limit),
            )
            return cursor.fetchall()
    finally:
        conn.close()
