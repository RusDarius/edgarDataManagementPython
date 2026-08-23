from __future__ import annotations

from datetime import datetime
from typing import Any

from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection

PORTFOLIO_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS portfolio_registry (
        portfolio_id INT PRIMARY KEY AUTO_INCREMENT,
        portfolio_name VARCHAR(128) NOT NULL,
        description VARCHAR(512),
        currency VARCHAR(8) DEFAULT 'USD',
        benchmark_symbol VARCHAR(32),
        notes VARCHAR(1024),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_portfolio_name (portfolio_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_managements_data_v1 (
        portfolio_item_id INT PRIMARY KEY AUTO_INCREMENT,
        portfolio_id INT NOT NULL,
        company_internal_id INT NOT NULL,
        status VARCHAR(24) NOT NULL DEFAULT 'ACTIVE',
        shares_held DOUBLE NOT NULL DEFAULT 0,
        average_entry_price DOUBLE NOT NULL DEFAULT 0,
        cost_basis_total DOUBLE NOT NULL DEFAULT 0,
        realized_pnl DOUBLE NOT NULL DEFAULT 0,
        fees_total DOUBLE NOT NULL DEFAULT 0,
        last_price DOUBLE NULL,
        last_price_at TIMESTAMP NULL DEFAULT NULL,
        last_market_cap_basic BIGINT NULL,
        last_change_pct DOUBLE NULL,
        last_perf_w DOUBLE NULL,
        last_perf_1m DOUBLE NULL,
        last_perf_y DOUBLE NULL,
        entry_currency VARCHAR(8) NULL,
        entry_fx_to_portfolio DOUBLE NULL,
        target_weight_pct DOUBLE NULL,
        risk_limit_pct DOUBLE NULL,
        target_price DOUBLE NULL,
        stop_loss_price DOUBLE NULL,
        thesis VARCHAR(2048) NULL,
        notes VARCHAR(1024) NULL,
        opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP NULL DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_portfolio_company (portfolio_id, company_internal_id),
        CONSTRAINT fk_portfolio_managements_data_v1_portfolio
            FOREIGN KEY (portfolio_id) REFERENCES portfolio_registry(portfolio_id),
        CONSTRAINT fk_portfolio_managements_data_v1_company
            FOREIGN KEY (company_internal_id) REFERENCES trading_view_company_data_map(internal_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_transactions (
        portfolio_transaction_id INT PRIMARY KEY AUTO_INCREMENT,
        portfolio_id INT NOT NULL,
        portfolio_item_id INT NULL,
        company_internal_id INT NOT NULL,
        transaction_type VARCHAR(32) NOT NULL,
        shares_delta DOUBLE NOT NULL DEFAULT 0,
        quantity_value DOUBLE NULL,
        quantity_unit VARCHAR(16) NULL,
        price DOUBLE NULL,
        fees DOUBLE NOT NULL DEFAULT 0,
        cash_flow DOUBLE NULL,
        amount_currency VARCHAR(8) NULL,
        fx_rate_to_portfolio DOUBLE NULL,
        note VARCHAR(1024),
        external_reference VARCHAR(128),
        executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_portfolio_transactions_portfolio
            FOREIGN KEY (portfolio_id) REFERENCES portfolio_registry(portfolio_id),
        CONSTRAINT fk_portfolio_transactions_item
            FOREIGN KEY (portfolio_item_id) REFERENCES portfolio_managements_data_v1(portfolio_item_id),
        CONSTRAINT fk_portfolio_transactions_company
            FOREIGN KEY (company_internal_id) REFERENCES trading_view_company_data_map(internal_id)
    )
    """,
]

PORTFOLIO_SCHEMA_INDEXES = [
    (
        "idx_portfolio_managements_data_v1_portfolio",
        "portfolio_managements_data_v1",
        "portfolio_id",
    ),
    (
        "idx_portfolio_managements_data_v1_company",
        "portfolio_managements_data_v1",
        "company_internal_id",
    ),
    (
        "idx_portfolio_managements_data_v1_status",
        "portfolio_managements_data_v1",
        "status",
    ),
    ("idx_portfolio_transactions_portfolio", "portfolio_transactions", "portfolio_id"),
    (
        "idx_portfolio_transactions_company",
        "portfolio_transactions",
        "company_internal_id",
    ),
    ("idx_portfolio_transactions_executed_at", "portfolio_transactions", "executed_at"),
]

PORTFOLIO_SCHEMA_MIGRATIONS = {
    "portfolio_managements_data_v1": [
        ("entry_currency", "VARCHAR(8) NULL"),
        ("entry_fx_to_portfolio", "DOUBLE NULL"),
    ],
    "portfolio_transactions": [
        ("quantity_value", "DOUBLE NULL"),
        ("quantity_unit", "VARCHAR(16) NULL"),
        ("amount_currency", "VARCHAR(8) NULL"),
        ("fx_rate_to_portfolio", "DOUBLE NULL"),
    ],
}


def _normalize_timestamp(value: datetime | None) -> datetime | None:
    return value


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    )
    return cursor.fetchone() is not None


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


def _ensure_column(
    cursor,
    table_name: str,
    column_name: str,
    definition_sql: str,
) -> None:
    if _column_exists(cursor, table_name, column_name):
        return
    cursor.execute(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition_sql}"
    )


def _ensure_index(
    cursor,
    index_name: str,
    table_name: str,
    column_name: str,
) -> None:
    if _index_exists(cursor, table_name, index_name):
        return
    cursor.execute(f"CREATE INDEX {index_name} ON {table_name}({column_name})")


def ensure_portfolio_management_schema() -> None:
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            for statement in PORTFOLIO_SCHEMA_STATEMENTS:
                cursor.execute(statement)
            for index_name, table_name, column_name in PORTFOLIO_SCHEMA_INDEXES:
                _ensure_index(cursor, index_name, table_name, column_name)
            for table_name, columns in PORTFOLIO_SCHEMA_MIGRATIONS.items():
                for column_name, definition_sql in columns:
                    _ensure_column(cursor, table_name, column_name, definition_sql)
        conn.commit()
    finally:
        conn.close()


def create_or_get_portfolio(
    portfolio_name: str,
    description: str | None = None,
    currency: str = "USD",
    benchmark_symbol: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    ensure_portfolio_management_schema()

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO portfolio_registry (
                    portfolio_name,
                    description,
                    currency,
                    benchmark_symbol,
                    notes
                ) VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    description = COALESCE(VALUES(description), description),
                    currency = COALESCE(VALUES(currency), currency),
                    benchmark_symbol = COALESCE(VALUES(benchmark_symbol), benchmark_symbol),
                    notes = COALESCE(VALUES(notes), notes)
                """,
                (portfolio_name, description, currency, benchmark_symbol, notes),
            )
        conn.commit()
    finally:
        conn.close()

    return get_portfolio_by_name(portfolio_name)


def get_portfolio_by_name(portfolio_name: str) -> dict[str, Any] | None:
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM portfolio_registry
                WHERE portfolio_name = %s
                """,
                (portfolio_name,),
            )
            return cursor.fetchone()
    finally:
        conn.close()


def delete_portfolio_by_name(portfolio_name: str) -> bool:
    """Delete one portfolio and all holdings/performance records by exact name.

    Child rows are deleted explicitly because the existing foreign keys do not
    use ``ON DELETE CASCADE``. The performance schema is ensured first so this
    operation also works after upgrading an older database installation.
    """
    normalized_name = portfolio_name.strip()
    if not normalized_name:
        raise ValueError("portfolio_name must be non-empty")

    ensure_portfolio_management_schema()
    from db.portfolio_performance_tracking_operations import (
        ensure_portfolio_performance_schema,
    )

    ensure_portfolio_performance_schema()

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT portfolio_id FROM portfolio_registry WHERE portfolio_name = %s",
                (normalized_name,),
            )
            portfolio_row = cursor.fetchone()
            if portfolio_row is None:
                return False

            portfolio_id = int(portfolio_row["portfolio_id"])
            for table_name in (
                "portfolio_transactions",
                "portfolio_cash_flows",
                "portfolio_nav_snapshots",
                "portfolio_managements_data_v1",
            ):
                cursor.execute(
                    f"DELETE FROM {table_name} WHERE portfolio_id = %s",
                    (portfolio_id,),
                )
            cursor.execute(
                "DELETE FROM portfolio_registry WHERE portfolio_id = %s",
                (portfolio_id,),
            )
        conn.commit()
    finally:
        conn.close()

    return True


def get_portfolio_by_id(portfolio_id: int) -> dict[str, Any] | None:
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM portfolio_registry
                WHERE portfolio_id = %s
                """,
                (portfolio_id,),
            )
            return cursor.fetchone()
    finally:
        conn.close()


def list_portfolios() -> list[dict[str, Any]]:
    ensure_portfolio_management_schema()

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("""
                SELECT
                    portfolio_registry.*,
                    COUNT(portfolio_managements_data_v1.portfolio_item_id) AS positions_count,
                    SUM(CASE WHEN portfolio_managements_data_v1.status = 'ACTIVE' THEN 1 ELSE 0 END) AS active_positions_count
                FROM portfolio_registry
                LEFT JOIN portfolio_managements_data_v1
                    ON portfolio_managements_data_v1.portfolio_id = portfolio_registry.portfolio_id
                GROUP BY portfolio_registry.portfolio_id
                ORDER BY portfolio_registry.updated_at DESC, portfolio_registry.portfolio_name ASC
                """)
            return cursor.fetchall()
    finally:
        conn.close()


def _fetch_portfolio_item_for_update(
    cursor,
    portfolio_id: int,
    company_internal_id: int,
) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT *
        FROM portfolio_managements_data_v1
        WHERE portfolio_id = %s AND company_internal_id = %s
        FOR UPDATE
        """,
        (portfolio_id, company_internal_id),
    )
    return cursor.fetchone()


def _record_transaction(
    cursor,
    portfolio_id: int,
    portfolio_item_id: int | None,
    company_internal_id: int,
    transaction_type: str,
    shares_delta: float,
    quantity_value: float | None,
    quantity_unit: str | None,
    price: float | None,
    fees: float,
    cash_flow: float | None,
    amount_currency: str | None,
    fx_rate_to_portfolio: float | None,
    note: str | None,
    external_reference: str | None,
    executed_at: datetime | None,
) -> None:
    cursor.execute(
        """
        INSERT INTO portfolio_transactions (
            portfolio_id,
            portfolio_item_id,
            company_internal_id,
            transaction_type,
            shares_delta,
            quantity_value,
            quantity_unit,
            price,
            fees,
            cash_flow,
            amount_currency,
            fx_rate_to_portfolio,
            note,
            external_reference,
            executed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            portfolio_id,
            portfolio_item_id,
            company_internal_id,
            transaction_type,
            shares_delta,
            quantity_value,
            quantity_unit,
            price,
            fees,
            cash_flow,
            amount_currency,
            fx_rate_to_portfolio,
            note,
            external_reference,
            _normalize_timestamp(executed_at),
        ),
    )


def apply_buy_transaction(
    portfolio_id: int,
    company_internal_id: int,
    shares: float,
    price: float,
    fees: float = 0.0,
    note: str | None = None,
    executed_at: datetime | None = None,
    external_reference: str | None = None,
    market_snapshot: dict[str, Any] | None = None,
    quantity_value: float | None = None,
    quantity_unit: str | None = None,
    amount_currency: str | None = None,
    fx_rate_to_portfolio: float | None = None,
) -> dict[str, Any]:
    if shares <= 0:
        raise ValueError("shares must be positive for a buy transaction")
    if price <= 0:
        raise ValueError("price must be positive for a buy transaction")

    ensure_portfolio_management_schema()

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            position_row = _fetch_portfolio_item_for_update(
                cursor, portfolio_id, company_internal_id
            )

            shares_cost = shares * price
            if position_row is None:
                cost_basis_total = shares_cost + fees
                average_entry_price = cost_basis_total / shares
                cursor.execute(
                    """
                    INSERT INTO portfolio_managements_data_v1 (
                        portfolio_id,
                        company_internal_id,
                        status,
                        shares_held,
                        average_entry_price,
                        cost_basis_total,
                        fees_total,
                        last_price,
                        last_price_at,
                        last_market_cap_basic,
                        last_change_pct,
                        last_perf_w,
                        last_perf_1m,
                        last_perf_y,
                        entry_currency,
                        entry_fx_to_portfolio,
                        opened_at,
                        notes
                    ) VALUES (%s, %s, 'ACTIVE', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP), %s)
                    """,
                    (
                        portfolio_id,
                        company_internal_id,
                        shares,
                        average_entry_price,
                        cost_basis_total,
                        fees,
                        price,
                        _normalize_timestamp(executed_at),
                        (market_snapshot or {}).get("market_cap_basic"),
                        (market_snapshot or {}).get("change_pct"),
                        (market_snapshot or {}).get("perf_w"),
                        (market_snapshot or {}).get("perf_1m"),
                        (market_snapshot or {}).get("perf_y"),
                        amount_currency,
                        fx_rate_to_portfolio,
                        _normalize_timestamp(executed_at),
                        note,
                    ),
                )
                portfolio_item_id = cursor.lastrowid
            else:
                shares_held = float(position_row["shares_held"])
                cost_basis_total = (
                    float(position_row["cost_basis_total"]) + shares_cost + fees
                )
                new_shares_held = shares_held + shares
                average_entry_price = cost_basis_total / new_shares_held
                cursor.execute(
                    """
                    UPDATE portfolio_managements_data_v1
                    SET
                        status = 'ACTIVE',
                        shares_held = %s,
                        average_entry_price = %s,
                        cost_basis_total = %s,
                        fees_total = fees_total + %s,
                        last_price = %s,
                        last_price_at = %s,
                        last_market_cap_basic = COALESCE(%s, last_market_cap_basic),
                        last_change_pct = COALESCE(%s, last_change_pct),
                        last_perf_w = COALESCE(%s, last_perf_w),
                        last_perf_1m = COALESCE(%s, last_perf_1m),
                        last_perf_y = COALESCE(%s, last_perf_y),
                        entry_currency = COALESCE(entry_currency, %s),
                        entry_fx_to_portfolio = COALESCE(entry_fx_to_portfolio, %s),
                        opened_at = CASE
                            WHEN opened_at IS NULL THEN COALESCE(%s, CURRENT_TIMESTAMP)
                            WHEN %s IS NOT NULL AND %s < opened_at THEN %s
                            ELSE opened_at
                        END,
                        closed_at = NULL,
                        notes = COALESCE(%s, notes)
                    WHERE portfolio_item_id = %s
                    """,
                    (
                        new_shares_held,
                        average_entry_price,
                        cost_basis_total,
                        fees,
                        price,
                        _normalize_timestamp(executed_at),
                        (market_snapshot or {}).get("market_cap_basic"),
                        (market_snapshot or {}).get("change_pct"),
                        (market_snapshot or {}).get("perf_w"),
                        (market_snapshot or {}).get("perf_1m"),
                        (market_snapshot or {}).get("perf_y"),
                        amount_currency,
                        fx_rate_to_portfolio,
                        _normalize_timestamp(executed_at),
                        _normalize_timestamp(executed_at),
                        _normalize_timestamp(executed_at),
                        _normalize_timestamp(executed_at),
                        note,
                        position_row["portfolio_item_id"],
                    ),
                )
                portfolio_item_id = int(position_row["portfolio_item_id"])

            _record_transaction(
                cursor,
                portfolio_id=portfolio_id,
                portfolio_item_id=portfolio_item_id,
                company_internal_id=company_internal_id,
                transaction_type="BUY",
                shares_delta=shares,
                quantity_value=quantity_value if quantity_value is not None else shares,
                quantity_unit=quantity_unit or "SHARES",
                price=price,
                fees=fees,
                cash_flow=-(shares_cost + fees),
                amount_currency=amount_currency,
                fx_rate_to_portfolio=fx_rate_to_portfolio,
                note=note,
                external_reference=external_reference,
                executed_at=executed_at,
            )
        conn.commit()
    finally:
        conn.close()

    return get_portfolio_position(portfolio_id, company_internal_id)


def apply_sell_transaction(
    portfolio_id: int,
    company_internal_id: int,
    shares: float,
    price: float,
    fees: float = 0.0,
    note: str | None = None,
    executed_at: datetime | None = None,
    external_reference: str | None = None,
    market_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if shares <= 0:
        raise ValueError("shares must be positive for a sell transaction")
    if price <= 0:
        raise ValueError("price must be positive for a sell transaction")

    ensure_portfolio_management_schema()

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            position_row = _fetch_portfolio_item_for_update(
                cursor, portfolio_id, company_internal_id
            )
            if position_row is None:
                raise ValueError("cannot sell a position that does not exist")

            current_shares = float(position_row["shares_held"])
            if shares > current_shares:
                raise ValueError("cannot sell more shares than currently held")

            current_cost_basis_total = float(position_row["cost_basis_total"])
            allocated_cost_basis = current_cost_basis_total * (shares / current_shares)
            proceeds_after_fees = (shares * price) - fees
            realized_delta = proceeds_after_fees - allocated_cost_basis
            remaining_shares = current_shares - shares
            remaining_cost_basis = max(
                current_cost_basis_total - allocated_cost_basis, 0.0
            )
            average_entry_price = (
                remaining_cost_basis / remaining_shares if remaining_shares > 0 else 0.0
            )
            new_status = "CLOSED" if remaining_shares == 0 else "ACTIVE"

            cursor.execute(
                """
                UPDATE portfolio_managements_data_v1
                SET
                    status = %s,
                    shares_held = %s,
                    average_entry_price = %s,
                    cost_basis_total = %s,
                    realized_pnl = realized_pnl + %s,
                    fees_total = fees_total + %s,
                    last_price = %s,
                    last_price_at = %s,
                    last_market_cap_basic = COALESCE(%s, last_market_cap_basic),
                    last_change_pct = COALESCE(%s, last_change_pct),
                    last_perf_w = COALESCE(%s, last_perf_w),
                    last_perf_1m = COALESCE(%s, last_perf_1m),
                    last_perf_y = COALESCE(%s, last_perf_y),
                    closed_at = CASE WHEN %s = 'CLOSED' THEN COALESCE(%s, CURRENT_TIMESTAMP) ELSE NULL END,
                    notes = COALESCE(%s, notes)
                WHERE portfolio_item_id = %s
                """,
                (
                    new_status,
                    remaining_shares,
                    average_entry_price,
                    remaining_cost_basis,
                    realized_delta,
                    fees,
                    price,
                    _normalize_timestamp(executed_at),
                    (market_snapshot or {}).get("market_cap_basic"),
                    (market_snapshot or {}).get("change_pct"),
                    (market_snapshot or {}).get("perf_w"),
                    (market_snapshot or {}).get("perf_1m"),
                    (market_snapshot or {}).get("perf_y"),
                    new_status,
                    _normalize_timestamp(executed_at),
                    note,
                    position_row["portfolio_item_id"],
                ),
            )

            _record_transaction(
                cursor,
                portfolio_id=portfolio_id,
                portfolio_item_id=int(position_row["portfolio_item_id"]),
                company_internal_id=company_internal_id,
                transaction_type="SELL",
                shares_delta=-shares,
                quantity_value=shares,
                quantity_unit="SHARES",
                price=price,
                fees=fees,
                cash_flow=proceeds_after_fees,
                amount_currency=None,
                fx_rate_to_portfolio=None,
                note=note,
                external_reference=external_reference,
                executed_at=executed_at,
            )
        conn.commit()
    finally:
        conn.close()

    return get_portfolio_position(portfolio_id, company_internal_id)


def update_position_market_snapshot(
    portfolio_id: int,
    company_internal_id: int,
    last_price: float | None = None,
    last_price_at: datetime | None = None,
    last_market_cap_basic: float | None = None,
    last_change_pct: float | None = None,
    last_perf_w: float | None = None,
    last_perf_1m: float | None = None,
    last_perf_y: float | None = None,
    note: str | None = None,
    create_transaction: bool = False,
) -> dict[str, Any]:
    ensure_portfolio_management_schema()

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            position_row = _fetch_portfolio_item_for_update(
                cursor, portfolio_id, company_internal_id
            )
            if position_row is None:
                raise ValueError("cannot update market snapshot for a missing position")

            cursor.execute(
                """
                UPDATE portfolio_managements_data_v1
                SET
                    last_price = COALESCE(%s, last_price),
                    last_price_at = COALESCE(%s, last_price_at),
                    last_market_cap_basic = COALESCE(%s, last_market_cap_basic),
                    last_change_pct = COALESCE(%s, last_change_pct),
                    last_perf_w = COALESCE(%s, last_perf_w),
                    last_perf_1m = COALESCE(%s, last_perf_1m),
                    last_perf_y = COALESCE(%s, last_perf_y),
                    notes = COALESCE(%s, notes)
                WHERE portfolio_item_id = %s
                """,
                (
                    last_price,
                    _normalize_timestamp(last_price_at),
                    last_market_cap_basic,
                    last_change_pct,
                    last_perf_w,
                    last_perf_1m,
                    last_perf_y,
                    note,
                    position_row["portfolio_item_id"],
                ),
            )

            if create_transaction:
                _record_transaction(
                    cursor,
                    portfolio_id=portfolio_id,
                    portfolio_item_id=int(position_row["portfolio_item_id"]),
                    company_internal_id=company_internal_id,
                    transaction_type="MARKET_SNAPSHOT",
                    shares_delta=0,
                    quantity_value=None,
                    quantity_unit=None,
                    price=last_price,
                    fees=0.0,
                    cash_flow=None,
                    amount_currency=None,
                    fx_rate_to_portfolio=None,
                    note=note,
                    external_reference=None,
                    executed_at=last_price_at,
                )
        conn.commit()
    finally:
        conn.close()

    return get_portfolio_position(portfolio_id, company_internal_id)


def update_position_plan(
    portfolio_id: int,
    company_internal_id: int,
    target_weight_pct: float | None = None,
    risk_limit_pct: float | None = None,
    target_price: float | None = None,
    stop_loss_price: float | None = None,
    thesis: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    ensure_portfolio_management_schema()

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            position_row = _fetch_portfolio_item_for_update(
                cursor, portfolio_id, company_internal_id
            )
            if position_row is None:
                raise ValueError("cannot update plan fields for a missing position")

            cursor.execute(
                """
                UPDATE portfolio_managements_data_v1
                SET
                    target_weight_pct = COALESCE(%s, target_weight_pct),
                    risk_limit_pct = COALESCE(%s, risk_limit_pct),
                    target_price = COALESCE(%s, target_price),
                    stop_loss_price = COALESCE(%s, stop_loss_price),
                    thesis = COALESCE(%s, thesis),
                    notes = COALESCE(%s, notes)
                WHERE portfolio_item_id = %s
                """,
                (
                    target_weight_pct,
                    risk_limit_pct,
                    target_price,
                    stop_loss_price,
                    thesis,
                    note,
                    position_row["portfolio_item_id"],
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return get_portfolio_position(portfolio_id, company_internal_id)


def get_portfolio_position(
    portfolio_id: int,
    company_internal_id: int,
) -> dict[str, Any] | None:
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                """
                SELECT
                    portfolio_managements_data_v1.portfolio_item_id,
                    portfolio_managements_data_v1.portfolio_id,
                    portfolio_registry.portfolio_name,
                    portfolio_managements_data_v1.company_internal_id,
                    trading_view_company_data_map.symbol,
                    trading_view_company_data_map.name AS company_name,
                    portfolio_managements_data_v1.status,
                    portfolio_managements_data_v1.shares_held,
                    portfolio_managements_data_v1.average_entry_price,
                    portfolio_managements_data_v1.cost_basis_total,
                    portfolio_managements_data_v1.realized_pnl,
                    portfolio_managements_data_v1.fees_total,
                    portfolio_managements_data_v1.last_price,
                    portfolio_managements_data_v1.last_price_at,
                    COALESCE(
                        portfolio_managements_data_v1.last_market_cap_basic,
                        trading_view_company_data_map.market_cap_basic
                    ) AS last_market_cap_basic,
                    COALESCE(
                        portfolio_managements_data_v1.last_change_pct,
                        trading_view_company_data_map.change_pct
                    ) AS last_change_pct,
                    COALESCE(
                        portfolio_managements_data_v1.last_perf_w,
                        trading_view_company_data_map.perf_w
                    ) AS last_perf_w,
                    COALESCE(
                        portfolio_managements_data_v1.last_perf_1m,
                        trading_view_company_data_map.perf_1m
                    ) AS last_perf_1m,
                    COALESCE(
                        portfolio_managements_data_v1.last_perf_y,
                        trading_view_company_data_map.perf_y
                    ) AS last_perf_y,
                    portfolio_managements_data_v1.entry_currency,
                    portfolio_managements_data_v1.entry_fx_to_portfolio,
                    trading_view_company_data_map.exchange,
                    trading_view_company_data_map.market,
                    COALESCE(
                        portfolio_registry.currency,
                        trading_view_company_data_map.fundamental_currency_code
                    ) AS currency,
                    portfolio_managements_data_v1.target_weight_pct,
                    portfolio_managements_data_v1.risk_limit_pct,
                    portfolio_managements_data_v1.target_price,
                    portfolio_managements_data_v1.stop_loss_price,
                    portfolio_managements_data_v1.thesis,
                    portfolio_managements_data_v1.notes,
                    portfolio_managements_data_v1.opened_at,
                    portfolio_managements_data_v1.closed_at,
                    portfolio_managements_data_v1.updated_at
                FROM portfolio_managements_data_v1
                INNER JOIN portfolio_registry
                    ON portfolio_registry.portfolio_id = portfolio_managements_data_v1.portfolio_id
                INNER JOIN trading_view_company_data_map
                    ON trading_view_company_data_map.internal_id = portfolio_managements_data_v1.company_internal_id
                WHERE portfolio_managements_data_v1.portfolio_id = %s
                  AND portfolio_managements_data_v1.company_internal_id = %s
                """,
                (portfolio_id, company_internal_id),
            )
            return cursor.fetchone()
    finally:
        conn.close()


def get_portfolio_positions(
    portfolio_id: int,
    include_closed: bool = False,
) -> list[dict[str, Any]]:
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            query = """
                SELECT
                    portfolio_managements_data_v1.portfolio_item_id,
                    portfolio_managements_data_v1.portfolio_id,
                    portfolio_registry.portfolio_name,
                    portfolio_managements_data_v1.company_internal_id,
                    trading_view_company_data_map.symbol,
                    trading_view_company_data_map.name AS company_name,
                    portfolio_managements_data_v1.status,
                    portfolio_managements_data_v1.shares_held,
                    portfolio_managements_data_v1.average_entry_price,
                    portfolio_managements_data_v1.cost_basis_total,
                    portfolio_managements_data_v1.realized_pnl,
                    portfolio_managements_data_v1.fees_total,
                    portfolio_managements_data_v1.last_price,
                    portfolio_managements_data_v1.last_price_at,
                    COALESCE(
                        portfolio_managements_data_v1.last_market_cap_basic,
                        trading_view_company_data_map.market_cap_basic
                    ) AS last_market_cap_basic,
                    COALESCE(
                        portfolio_managements_data_v1.last_change_pct,
                        trading_view_company_data_map.change_pct
                    ) AS last_change_pct,
                    COALESCE(
                        portfolio_managements_data_v1.last_perf_w,
                        trading_view_company_data_map.perf_w
                    ) AS last_perf_w,
                    COALESCE(
                        portfolio_managements_data_v1.last_perf_1m,
                        trading_view_company_data_map.perf_1m
                    ) AS last_perf_1m,
                    COALESCE(
                        portfolio_managements_data_v1.last_perf_y,
                        trading_view_company_data_map.perf_y
                    ) AS last_perf_y,
                    portfolio_managements_data_v1.entry_currency,
                    portfolio_managements_data_v1.entry_fx_to_portfolio,
                    trading_view_company_data_map.exchange,
                    trading_view_company_data_map.market,
                    COALESCE(
                        portfolio_registry.currency,
                        trading_view_company_data_map.fundamental_currency_code
                    ) AS currency,
                    portfolio_managements_data_v1.target_weight_pct,
                    portfolio_managements_data_v1.risk_limit_pct,
                    portfolio_managements_data_v1.target_price,
                    portfolio_managements_data_v1.stop_loss_price,
                    portfolio_managements_data_v1.thesis,
                    portfolio_managements_data_v1.notes,
                    portfolio_managements_data_v1.opened_at,
                    portfolio_managements_data_v1.closed_at,
                    portfolio_managements_data_v1.updated_at
                FROM portfolio_managements_data_v1
                INNER JOIN portfolio_registry
                    ON portfolio_registry.portfolio_id = portfolio_managements_data_v1.portfolio_id
                INNER JOIN trading_view_company_data_map
                    ON trading_view_company_data_map.internal_id = portfolio_managements_data_v1.company_internal_id
                WHERE portfolio_managements_data_v1.portfolio_id = %s
            """
            params: list[Any] = [portfolio_id]
            if not include_closed:
                query += " AND portfolio_managements_data_v1.status <> 'CLOSED'"
            query += """
                ORDER BY
                    CASE WHEN portfolio_managements_data_v1.status = 'ACTIVE' THEN 0 ELSE 1 END,
                    COALESCE(
                        portfolio_managements_data_v1.last_price * portfolio_managements_data_v1.shares_held,
                        portfolio_managements_data_v1.cost_basis_total
                    ) DESC,
                    trading_view_company_data_map.symbol ASC
            """
            cursor.execute(query, tuple(params))
            return cursor.fetchall()
    finally:
        conn.close()


def get_portfolio_transactions(
    portfolio_id: int,
    company_internal_id: int | None = None,
) -> list[dict[str, Any]]:
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            query = """
                SELECT *
                FROM portfolio_transactions
                WHERE portfolio_id = %s
            """
            params: list[Any] = [portfolio_id]
            if company_internal_id is not None:
                query += " AND company_internal_id = %s"
                params.append(company_internal_id)
            query += " ORDER BY executed_at DESC, portfolio_transaction_id DESC"
            cursor.execute(query, tuple(params))
            return cursor.fetchall()
    finally:
        conn.close()
