from __future__ import annotations

from typing import Any

from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection
from data_loaders.api_tradingview_client import ApiTradingViewClient


TRADING_VIEW_COMPANY_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS trading_view_company_data_map (
        internal_id INT PRIMARY KEY AUTO_INCREMENT,
        symbol VARCHAR(50) NOT NULL,
        name VARCHAR(256),
        exchange VARCHAR(32),
        description VARCHAR(256),
        type VARCHAR(32),
        typespecs VARCHAR(128),
        market_cap_basic BIGINT,
        fundamental_currency_code VARCHAR(8),
        market VARCHAR(32),
        kind VARCHAR(32),
        change_pct DOUBLE,
        perf_5y DOUBLE,
        perf_6m DOUBLE,
        perf_all DOUBLE,
        perf_1m DOUBLE,
        perf_w DOUBLE,
        perf_y DOUBLE,
        perf_ytd DOUBLE,
        perf_10y DOUBLE,
        perf_3y DOUBLE,
        perf_5d DOUBLE,
        logoid VARCHAR(255),
        logo_style VARCHAR(32),
        kind_delay INT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_symbol (symbol)
    )
    """,
]

TRADING_VIEW_COMPANY_INDEXES = [
    ("idx_symbol", "trading_view_company_data_map", "symbol"),
    ("idx_name", "trading_view_company_data_map", "name"),
    ("idx_market", "trading_view_company_data_map", "market"),
    ("idx_exchange", "trading_view_company_data_map", "exchange"),
]

TRADING_VIEW_COMPANY_SCHEMA_MIGRATIONS = [
    ("change_pct", "DOUBLE NULL"),
    ("perf_5y", "DOUBLE NULL"),
    ("perf_6m", "DOUBLE NULL"),
    ("perf_all", "DOUBLE NULL"),
    ("perf_1m", "DOUBLE NULL"),
    ("perf_w", "DOUBLE NULL"),
    ("perf_y", "DOUBLE NULL"),
    ("perf_ytd", "DOUBLE NULL"),
    ("perf_10y", "DOUBLE NULL"),
    ("perf_3y", "DOUBLE NULL"),
    ("perf_5d", "DOUBLE NULL"),
    ("logoid", "VARCHAR(64) NULL"),
    ("logo_style", "VARCHAR(32) NULL"),
    ("kind_delay", "INT NULL"),
    ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
]

TRADING_VIEW_COMPANY_VARCHAR_REQUIREMENTS = [
    ("name", 256, "VARCHAR(256) NULL"),
    ("logoid", 255, "VARCHAR(255) NULL"),
]


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


def _get_character_maximum_length(
    cursor,
    table_name: str,
    column_name: str,
) -> int | None:
    cursor.execute(
        """
        SELECT character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    )
    result = cursor.fetchone()
    if result is None:
        return None
    return result[0]


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


def _ensure_index(
    cursor,
    index_name: str,
    table_name: str,
    column_name: str,
) -> None:
    if _index_exists(cursor, table_name, index_name):
        return
    cursor.execute(f"CREATE INDEX {index_name} ON {table_name}({column_name})")


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


def _ensure_minimum_varchar_length(
    cursor,
    table_name: str,
    column_name: str,
    minimum_length: int,
    definition_sql: str,
) -> None:
    current_length = _get_character_maximum_length(cursor, table_name, column_name)
    if current_length is None:
        _ensure_column(cursor, table_name, column_name, definition_sql)
        return
    if current_length >= minimum_length:
        return
    cursor.execute(
        f"ALTER TABLE {table_name} MODIFY COLUMN {column_name} {definition_sql}"
    )


def ensure_tradingview_company_data_schema() -> None:
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            for statement in TRADING_VIEW_COMPANY_SCHEMA_STATEMENTS:
                cursor.execute(statement)
            for column_name, definition_sql in TRADING_VIEW_COMPANY_SCHEMA_MIGRATIONS:
                _ensure_column(
                    cursor,
                    "trading_view_company_data_map",
                    column_name,
                    definition_sql,
                )
            for (
                column_name,
                minimum_length,
                definition_sql,
            ) in TRADING_VIEW_COMPANY_VARCHAR_REQUIREMENTS:
                _ensure_minimum_varchar_length(
                    cursor,
                    "trading_view_company_data_map",
                    column_name,
                    minimum_length,
                    definition_sql,
                )
            for index_name, table_name, column_name in TRADING_VIEW_COMPANY_INDEXES:
                _ensure_index(cursor, index_name, table_name, column_name)
        conn.commit()
    finally:
        conn.close()


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def _normalize_company_entry(row: dict[str, Any]) -> dict[str, Any] | None:
    ticker_view = row.get("ticker-view")
    ticker_view_metadata = ticker_view if isinstance(ticker_view, dict) else {}

    symbol = row.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        fallback_ticker = ticker_view_metadata.get("name") or row.get("name")
        fallback_exchange = row.get("exchange") or ticker_view_metadata.get("exchange")
        if fallback_exchange and fallback_ticker:
            symbol = f"{fallback_exchange}:{fallback_ticker}"
        else:
            symbol = fallback_ticker

    if not isinstance(symbol, str) or not symbol.strip():
        return None

    normalized_typespecs = row.get("typespecs")
    if normalized_typespecs is None:
        normalized_typespecs = ticker_view_metadata.get("typespecs")

    if isinstance(normalized_typespecs, list):
        normalized_typespecs = ",".join(normalized_typespecs)

    logoid = row.get("logoid") or ticker_view_metadata.get("logoid")
    logo_style = row.get("logo_style")
    if logo_style is None:
        logo_payload = ticker_view_metadata.get("logo")
        if isinstance(logo_payload, dict):
            logo_style = logo_payload.get("style")

    display_name = (
        row.get("description")
        or ticker_view_metadata.get("description")
        or row.get("name")
        or ticker_view_metadata.get("name")
    )

    return {
        "symbol": symbol.strip(),
        "name": display_name,
        "exchange": row.get("exchange") or ticker_view_metadata.get("exchange"),
        "description": row.get("description")
        or ticker_view_metadata.get("description"),
        "type": row.get("type") or ticker_view_metadata.get("type"),
        "typespecs": normalized_typespecs,
        "market_cap_basic": _coerce_int(row.get("market_cap_basic")),
        "fundamental_currency_code": row.get("fundamental_currency_code"),
        "market": row.get("market"),
        "kind": row.get("kind") or ticker_view_metadata.get("kind"),
        "change_pct": _coerce_float(row.get("change")),
        "perf_5y": _coerce_float(row.get("Perf.5Y")),
        "perf_6m": _coerce_float(row.get("Perf.6M")),
        "perf_all": _coerce_float(row.get("Perf.All")),
        "perf_1m": _coerce_float(row.get("Perf.1M")),
        "perf_w": _coerce_float(row.get("Perf.W")),
        "perf_y": _coerce_float(row.get("Perf.Y")),
        "perf_ytd": _coerce_float(row.get("Perf.YTD")),
        "perf_10y": _coerce_float(row.get("Perf.10Y")),
        "perf_3y": _coerce_float(row.get("Perf.3Y")),
        "perf_5d": _coerce_float(row.get("Perf.5D")),
        "logoid": logoid,
        "logo_style": logo_style,
        "kind_delay": _coerce_int(
            row.get("kind_delay")
            or row.get("kind-delay")
            or ticker_view_metadata.get("kind_delay")
            or ticker_view_metadata.get("kind-delay")
        ),
    }


def upsert_tradingview_company_entries(entries: list[dict[str, Any]]) -> int:
    normalized_entries = [
        normalized for row in entries if (normalized := _normalize_company_entry(row))
    ]
    if not normalized_entries:
        return 0

    ensure_tradingview_company_data_schema()

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO trading_view_company_data_map (
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
                    perf_5y,
                    perf_6m,
                    perf_all,
                    perf_1m,
                    perf_w,
                    perf_y,
                    perf_ytd,
                    perf_10y,
                    perf_3y,
                    perf_5d,
                    logoid,
                    logo_style,
                    kind_delay
                ) VALUES (
                    %(symbol)s,
                    %(name)s,
                    %(exchange)s,
                    %(description)s,
                    %(type)s,
                    %(typespecs)s,
                    %(market_cap_basic)s,
                    %(fundamental_currency_code)s,
                    %(market)s,
                    %(kind)s,
                    %(change_pct)s,
                    %(perf_5y)s,
                    %(perf_6m)s,
                    %(perf_all)s,
                    %(perf_1m)s,
                    %(perf_w)s,
                    %(perf_y)s,
                    %(perf_ytd)s,
                    %(perf_10y)s,
                    %(perf_3y)s,
                    %(perf_5d)s,
                    %(logoid)s,
                    %(logo_style)s,
                    %(kind_delay)s
                )
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    exchange = COALESCE(VALUES(exchange), exchange),
                    description = COALESCE(VALUES(description), description),
                    type = COALESCE(VALUES(type), type),
                    typespecs = COALESCE(VALUES(typespecs), typespecs),
                    market_cap_basic = COALESCE(VALUES(market_cap_basic), market_cap_basic),
                    fundamental_currency_code = COALESCE(VALUES(fundamental_currency_code), fundamental_currency_code),
                    market = COALESCE(VALUES(market), market),
                    kind = COALESCE(VALUES(kind), kind),
                    change_pct = COALESCE(VALUES(change_pct), change_pct),
                    perf_5y = COALESCE(VALUES(perf_5y), perf_5y),
                    perf_6m = COALESCE(VALUES(perf_6m), perf_6m),
                    perf_all = COALESCE(VALUES(perf_all), perf_all),
                    perf_1m = COALESCE(VALUES(perf_1m), perf_1m),
                    perf_w = COALESCE(VALUES(perf_w), perf_w),
                    perf_y = COALESCE(VALUES(perf_y), perf_y),
                    perf_ytd = COALESCE(VALUES(perf_ytd), perf_ytd),
                    perf_10y = COALESCE(VALUES(perf_10y), perf_10y),
                    perf_3y = COALESCE(VALUES(perf_3y), perf_3y),
                    perf_5d = COALESCE(VALUES(perf_5d), perf_5d),
                    logoid = COALESCE(VALUES(logoid), logoid),
                    logo_style = COALESCE(VALUES(logo_style), logo_style),
                    kind_delay = COALESCE(VALUES(kind_delay), kind_delay)
                """,
                normalized_entries,
            )
            affected_rows = cursor.rowcount
        conn.commit()
        return affected_rows
    finally:
        conn.close()


def load_tradingview_company_data(
    api_client: ApiTradingViewClient,
    timeout: int = 30,
    markets: list[str] | None = None,
) -> dict[str, Any]:
    response_payload = api_client.scan_world_market_all_priceperf_metrics(
        timeout=timeout,
        markets=markets,
        include_mapped_rows=True,
    )
    rows = response_payload.get("data", [])
    upserted_rows = upsert_tradingview_company_entries(rows)
    return {
        "fetched_rows": len(rows),
        "upserted_rows": upserted_rows,
        "markets": list(markets) if markets is not None else None,
    }
