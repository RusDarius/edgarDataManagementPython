# === AI GENERATED CODE START (GitHub Copilot - Claude Sonnet 5) ===
# Generated on: 2026-08-22
# Purpose: Storage layer for portfolio-level cash flows (contributions/withdrawals/
#   income) and NAV history snapshots, used for flow-aware historical performance
#   tracking. Sits alongside portfolio_management_operations.py and reuses the same
#   portfolio_registry table so it integrates with the existing holdings system.
from __future__ import annotations

from datetime import datetime
from typing import Any

from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection

# Flow types that represent money crossing the portfolio boundary and therefore
# affect Modified Dietz return calculations.
EXTERNAL_FLOW_TYPES = ("CONTRIBUTION", "WITHDRAWAL")

# Income types are informational only (money that stays inside the portfolio,
# e.g. a dividend that is reinvested or a realized gain left in cash) and do NOT
# distort the return calculation unless separately withdrawn.
INCOME_EVENT_TYPES = ("DIVIDEND", "REALIZED_GAIN", "INTEREST", "OTHER_INCOME")

PERFORMANCE_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS portfolio_cash_flows (
        cash_flow_id INT PRIMARY KEY AUTO_INCREMENT,
        portfolio_id INT NOT NULL,
        flow_type VARCHAR(24) NOT NULL,
        amount DOUBLE NOT NULL,
        currency VARCHAR(8) DEFAULT 'USD',
        fx_rate_to_portfolio DOUBLE NULL,
        benchmark_symbol VARCHAR(32) NULL,
        benchmark_price DOUBLE NULL,
        related_symbol VARCHAR(32) NULL,
        note VARCHAR(1024) NULL,
        executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_portfolio_cash_flows_portfolio
            FOREIGN KEY (portfolio_id) REFERENCES portfolio_registry(portfolio_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_nav_snapshots (
        nav_snapshot_id INT PRIMARY KEY AUTO_INCREMENT,
        portfolio_id INT NOT NULL,
        snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        total_market_value DOUBLE NOT NULL,
        cash_balance DOUBLE NOT NULL DEFAULT 0,
        total_nav DOUBLE NOT NULL,
        benchmark_symbol VARCHAR(32) NULL,
        benchmark_price DOUBLE NULL,
        note VARCHAR(1024) NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_portfolio_nav_snapshots_portfolio
            FOREIGN KEY (portfolio_id) REFERENCES portfolio_registry(portfolio_id)
    )
    """,
]

PERFORMANCE_SCHEMA_INDEXES = [
    ("idx_portfolio_cash_flows_portfolio", "portfolio_cash_flows", "portfolio_id"),
    (
        "idx_portfolio_cash_flows_executed_at",
        "portfolio_cash_flows",
        "executed_at",
    ),
    (
        "idx_portfolio_nav_snapshots_portfolio",
        "portfolio_nav_snapshots",
        "portfolio_id",
    ),
    (
        "idx_portfolio_nav_snapshots_snapshot_at",
        "portfolio_nav_snapshots",
        "snapshot_at",
    ),
]


def _index_exists(cursor, table_name: str, index_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND index_name = %s
        """,
        (table_name, index_name),
    )
    return cursor.fetchone() is not None


def _ensure_index(cursor, index_name: str, table_name: str, column_name: str) -> None:
    if _index_exists(cursor, table_name, index_name):
        return
    cursor.execute(f"CREATE INDEX {index_name} ON {table_name}({column_name})")


def ensure_portfolio_performance_schema() -> None:
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            for statement in PERFORMANCE_SCHEMA_STATEMENTS:
                cursor.execute(statement)
            for index_name, table_name, column_name in PERFORMANCE_SCHEMA_INDEXES:
                _ensure_index(cursor, index_name, table_name, column_name)
        conn.commit()
    finally:
        conn.close()


def insert_cash_flow(
    portfolio_id: int,
    flow_type: str,
    amount: float,
    currency: str = "USD",
    fx_rate_to_portfolio: float | None = None,
    benchmark_symbol: str | None = None,
    benchmark_price: float | None = None,
    related_symbol: str | None = None,
    note: str | None = None,
    executed_at: datetime | None = None,
) -> dict[str, Any]:
    normalized_flow_type = flow_type.strip().upper()
    if normalized_flow_type not in EXTERNAL_FLOW_TYPES + INCOME_EVENT_TYPES:
        raise ValueError(
            f"flow_type must be one of {EXTERNAL_FLOW_TYPES + INCOME_EVENT_TYPES}, got {flow_type!r}"
        )
    if amount == 0:
        raise ValueError("amount must be non-zero")

    ensure_portfolio_performance_schema()

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                """
                INSERT INTO portfolio_cash_flows (
                    portfolio_id,
                    flow_type,
                    amount,
                    currency,
                    fx_rate_to_portfolio,
                    benchmark_symbol,
                    benchmark_price,
                    related_symbol,
                    note,
                    executed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP))
                """,
                (
                    portfolio_id,
                    normalized_flow_type,
                    amount,
                    currency.strip().upper() if currency else "USD",
                    fx_rate_to_portfolio,
                    benchmark_symbol.strip().upper() if benchmark_symbol else None,
                    benchmark_price,
                    related_symbol.strip().upper() if related_symbol else None,
                    note,
                    executed_at,
                ),
            )
            cash_flow_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    return get_cash_flow(cash_flow_id)


def get_cash_flow(cash_flow_id: int) -> dict[str, Any] | None:
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT * FROM portfolio_cash_flows WHERE cash_flow_id = %s",
                (cash_flow_id,),
            )
            return cursor.fetchone()
    finally:
        conn.close()


def get_cash_flows(
    portfolio_id: int,
    flow_types: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    ensure_portfolio_performance_schema()

    conditions = ["portfolio_id = %s"]
    params: list[Any] = [portfolio_id]

    if flow_types:
        normalized_types = [flow_type.strip().upper() for flow_type in flow_types]
        placeholders = ", ".join(["%s"] * len(normalized_types))
        conditions.append(f"flow_type IN ({placeholders})")
        params.extend(normalized_types)
    if start is not None:
        conditions.append("executed_at >= %s")
        params.append(start)
    if end is not None:
        conditions.append("executed_at <= %s")
        params.append(end)

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM portfolio_cash_flows
                WHERE {" AND ".join(conditions)}
                ORDER BY executed_at ASC, cash_flow_id ASC
                """,
                tuple(params),
            )
            return cursor.fetchall()
    finally:
        conn.close()


def insert_nav_snapshot(
    portfolio_id: int,
    total_market_value: float,
    cash_balance: float = 0.0,
    benchmark_symbol: str | None = None,
    benchmark_price: float | None = None,
    note: str | None = None,
    snapshot_at: datetime | None = None,
) -> dict[str, Any]:
    ensure_portfolio_performance_schema()

    total_nav = total_market_value + cash_balance

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                """
                INSERT INTO portfolio_nav_snapshots (
                    portfolio_id,
                    snapshot_at,
                    total_market_value,
                    cash_balance,
                    total_nav,
                    benchmark_symbol,
                    benchmark_price,
                    note
                ) VALUES (%s, COALESCE(%s, CURRENT_TIMESTAMP), %s, %s, %s, %s, %s, %s)
                """,
                (
                    portfolio_id,
                    snapshot_at,
                    total_market_value,
                    cash_balance,
                    total_nav,
                    benchmark_symbol.strip().upper() if benchmark_symbol else None,
                    benchmark_price,
                    note,
                ),
            )
            nav_snapshot_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()

    return get_nav_snapshot(nav_snapshot_id)


def get_nav_snapshot(nav_snapshot_id: int) -> dict[str, Any] | None:
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT * FROM portfolio_nav_snapshots WHERE nav_snapshot_id = %s",
                (nav_snapshot_id,),
            )
            return cursor.fetchone()
    finally:
        conn.close()


def get_nav_snapshots(
    portfolio_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    ensure_portfolio_performance_schema()

    conditions = ["portfolio_id = %s"]
    params: list[Any] = [portfolio_id]
    if start is not None:
        conditions.append("snapshot_at >= %s")
        params.append(start)
    if end is not None:
        conditions.append("snapshot_at <= %s")
        params.append(end)

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM portfolio_nav_snapshots
                WHERE {" AND ".join(conditions)}
                ORDER BY snapshot_at ASC, nav_snapshot_id ASC
                """,
                tuple(params),
            )
            return cursor.fetchall()
    finally:
        conn.close()


def get_latest_nav_snapshot(portfolio_id: int) -> dict[str, Any] | None:
    ensure_portfolio_performance_schema()

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM portfolio_nav_snapshots
                WHERE portfolio_id = %s
                ORDER BY snapshot_at DESC, nav_snapshot_id DESC
                LIMIT 1
                """,
                (portfolio_id,),
            )
            return cursor.fetchone()
    finally:
        conn.close()


# === AI GENERATED CODE END ===
