"""Merge move-prediction scoring with configured current holdings.

Reads tickers from a holdings config, joins against the latest (or specified)
move-prediction DuckDB run, and writes per-run CSV exports under
``logs/tradingview_analysis/holdings_scoring_analysis/``.

Future scans (module2, price-driven, etc.) plug in via ``SCAN_EXPORTERS``.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from data_analysis_scripts._shared_analysis_utils import get_symbol_name
from data_loaders.fx_rate_resolver import (
    FxRateQuote,
    convert_amount,
    normalize_currency_code,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    DEFAULT_PERFORMANCE_TRACKING_PERIODS,
    LOG_DIR,
    PERFORMANCE_TRACKING_FIELD_ORDER,
    _build_duckdb_run_id,
    _collect_code_version_metadata,
    _slugify,
)
from db.trading_view_move_prediction_duckdb import (
    describe_move_prediction_duckdb,
    query_move_prediction_duckdb,
)

HOLDINGS_SCORING_LOG_DIR = LOG_DIR.parent / "holdings_scoring_analysis"
DEFAULT_DUCKDB_RUNS_ROOT = LOG_DIR / "duckdb_runs"
HORIZON_NAMES = ("days", "weeks", "months", "years")
MARK_TO_MARKET_FIELDS = (
    "close_quote",
    "close_quote_currency",
    "close",
    "close_usd",
    "close_source",
    "current_value",
    "current_value_currency",
    "current_value_usd",
    "unrealized_pnl",
    "unrealized_pnl_usd",
    "unrealized_return_pct",
)

EXCHANGE_QUOTE_CURRENCY: dict[str, str] = {
    "AMEX": "USD",
    "ARCA": "USD",
    "BATS": "USD",
    "NASDAQ": "USD",
    "NYSE": "USD",
    "OTC": "USD",
    "OMXCOP": "DKK",
    "OMXCSE": "DKK",
    "OMXHEX": "EUR",
    "OMXSTO": "SEK",
    "LSE": "GBP",
    "SIX": "CHF",
    "XETR": "EUR",
    "FWB": "EUR",
    "GETTEX": "EUR",
    "EURONEXT": "EUR",
    "NGM": "SEK",
    "HKEX": "HKD",
    "SEHK": "HKD",
}

COUNTRY_QUOTE_CURRENCY: dict[str, str] = {
    "CHINA": "USD",
    "DENMARK": "DKK",
    "FRANCE": "EUR",
    "GERMANY": "EUR",
    "SWEDEN": "SEK",
    "NORWAY": "NOK",
    "UNITED KINGDOM": "GBP",
    "UNITED STATES": "USD",
    "SWITZERLAND": "CHF",
}


@dataclass
class MarkToMarketContext:
    portfolio_currency: str = "USD"
    as_of_date: date | None = None
    resolve_fx_rates: bool = True
    fx_warnings: list[str] = field(default_factory=list)
    rate_cache: dict[tuple[str, str, str | None], FxRateQuote] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class CloseQuote:
    price: float | None
    quote_currency: str | None = None
    source: str = ""


RAW_PERF_FIELDS = (
    "change",
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.6M",
    "Perf.YTD",
    "Perf.Y",
    "Perf.5Y",
)

RAW_SYMBOL_JOIN = """
    (
        r.symbol = target.symbol
        OR r.symbol LIKE '%:' || target.symbol
        OR target.symbol LIKE '%:' || r.symbol
    )
"""


@dataclass(frozen=True)
class CashPositionConfig:
    value: float
    currency: str
    value_in_portfolio_currency: float | None = None
    fx_rate_to_portfolio: float | None = None
    fx_rate_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "currency": self.currency,
            "value_in_portfolio_currency": self.value_in_portfolio_currency,
            "fx_rate_to_portfolio": self.fx_rate_to_portfolio,
            "fx_rate_date": self.fx_rate_date,
        }


@dataclass(frozen=True)
class HoldingConfig:
    ticker: str
    symbol: str | None = None
    sleeve: str | None = None
    invested_sum: float | None = None
    average_price: float | None = None
    invested_currency: str = "USD"
    price_currency: str = "USD"
    quote_currency: str | None = None
    currency: str = "USD"
    notes: str | None = None
    invested_sum_usd: float | None = None
    invested_sum_in_price_currency: float | None = None
    fx_rate_to_usd: float | None = None
    fx_rate_price_to_usd: float | None = None
    fx_rate_date: str | None = None
    implied_shares: float | None = None
    portfolio_weight_pct: float | None = None

    def __post_init__(self) -> None:
        legacy_currency = normalize_currency_code(self.currency)
        invested_currency = normalize_currency_code(self.invested_currency)
        price_currency = normalize_currency_code(self.price_currency)
        if (
            invested_currency == "USD"
            and price_currency == "USD"
            and legacy_currency != "USD"
        ):
            object.__setattr__(self, "invested_currency", legacy_currency)
            object.__setattr__(self, "price_currency", legacy_currency)
        object.__setattr__(self, "currency", price_currency)


@dataclass(frozen=True)
class RawScanSymbolEntry:
    symbol: str
    company: str
    bare_ticker: str


@dataclass(frozen=True)
class MatchedHolding:
    holding: HoldingConfig
    db_symbol: str
    scan_symbol: str | None = None
    company: str | None = None

    @property
    def lookup_key(self) -> str:
        return self.scan_symbol or self.db_symbol


@dataclass(frozen=True)
class MovePredictionSource:
    database_path: Path
    run_id: str
    created_at_utc: datetime | str | None
    suite_name: str | None
    profile_names: list[str]


@dataclass
class ScanExportResult:
    scan_key: str
    source_run_id: str
    source_database_path: str
    exported_files: dict[str, str] = field(default_factory=dict)
    matched_count: int = 0
    unmatched_tickers: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


ScanExporter = Callable[
    [
        MovePredictionSource,
        list[HoldingConfig],
        list[MatchedHolding],
        list[str],
        Path,
        list[dict[str, Any]] | None,
        MarkToMarketContext | None,
    ],
    ScanExportResult,
]


def _build_holdings_run_id(
    run_label: str | None = None,
    reference_time: datetime | None = None,
) -> str:
    base_run_id = _build_duckdb_run_id(run_label, reference_time)
    if run_label and _slugify(run_label):
        return base_run_id
    return base_run_id.replace("move_prediction_", "holdings_scoring_", 1)


def normalize_holding_ticker(ticker: str) -> str:
    return str(ticker or "").strip().upper()


def _split_exchange_symbol(value: str) -> tuple[str | None, str]:
    normalized = normalize_holding_ticker(value)
    if not normalized:
        return None, ""
    if ":" in normalized:
        exchange, ticker = normalized.split(":", 1)
        return exchange or None, ticker
    return None, normalized


def holding_ticker_keys(ticker: str) -> set[str]:
    normalized = normalize_holding_ticker(ticker)
    if not normalized:
        return set()
    keys = {normalized}
    if ":" in normalized:
        keys.add(normalized.split(":", 1)[-1])
    return keys


def holding_matches_db_symbol(config_ticker: str, db_symbol: str) -> bool:
    return bool(holding_ticker_keys(config_ticker) & holding_ticker_keys(db_symbol))


def holding_matches_db_symbol_strict(config_symbol: str, db_symbol: str) -> bool:
    """Match when ticker suffix agrees and exchange prefixes are compatible."""
    config_norm = normalize_holding_ticker(config_symbol)
    db_norm = normalize_holding_ticker(db_symbol)
    if config_norm == db_norm:
        return True
    config_exchange, config_ticker = _split_exchange_symbol(config_norm)
    db_exchange, db_ticker = _split_exchange_symbol(db_norm)
    if not config_ticker or config_ticker != db_ticker:
        return False
    if config_exchange and db_exchange:
        return config_exchange == db_exchange
    return False


def holding_config_match_keys(holding: HoldingConfig) -> tuple[str, bool]:
    """Return the match key and whether exchange-aware strict matching applies."""
    if holding.symbol:
        symbol = normalize_holding_ticker(holding.symbol)
        exchange, _ = _split_exchange_symbol(symbol)
        return symbol, bool(exchange)
    return normalize_holding_ticker(holding.ticker), False


def holding_matches_db_symbol_for_holding(
    holding: HoldingConfig,
    db_symbol: str,
) -> bool:
    match_key, strict = holding_config_match_keys(holding)
    if strict:
        return holding_matches_db_symbol_strict(match_key, db_symbol)
    return holding_matches_db_symbol(match_key, db_symbol)


def holding_config_label(holding: HoldingConfig) -> str:
    symbol = str(holding.symbol or "").strip()
    ticker = str(holding.ticker or "").strip()
    if symbol and symbol.upper() != ticker.upper():
        return f"{ticker} [{symbol}]" if ticker else symbol
    return ticker or symbol


def _pick_catalog_entry(
    holding: HoldingConfig,
    candidates: Sequence[RawScanSymbolEntry],
) -> RawScanSymbolEntry | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    notes = (holding.notes or "").strip().lower()
    if notes:
        for entry in candidates:
            company_lower = entry.company.lower()
            if notes in company_lower or company_lower in notes:
                return entry
    return None


def _catalog_entry_for_holding(
    holding: HoldingConfig,
    catalog: Sequence[RawScanSymbolEntry],
) -> RawScanSymbolEntry | None:
    if not catalog:
        return None
    match_key, strict = holding_config_match_keys(holding)
    if strict:
        return next(
            (
                entry
                for entry in catalog
                if holding_matches_db_symbol_strict(match_key, entry.symbol)
            ),
            None,
        )
    candidates = [
        entry
        for entry in catalog
        if holding_matches_db_symbol(match_key, entry.symbol)
        or holding_matches_db_symbol(match_key, entry.bare_ticker)
    ]
    return _pick_catalog_entry(holding, candidates)


def _row_company(row: Mapping[str, Any]) -> str:
    return str(row.get("company") or row.get("Company") or "").strip()


def _resolve_row_to_match(
    row: Mapping[str, Any],
    matched: Sequence[MatchedHolding],
) -> MatchedHolding | None:
    symbol = str(row.get("symbol") or "")
    company = _row_company(row)
    for match in matched:
        if match.db_symbol != symbol:
            continue
        if match.company and company and match.company != company:
            continue
        if match.company and not company:
            continue
        return match
    return next((match for match in matched if match.db_symbol == symbol), None)


def _resolve_holding_currency_fields(
    item: dict[str, Any],
    *,
    portfolio_currency: str,
) -> tuple[str, str, str | None]:
    """Resolve invested/price/quote currencies from config with legacy ``currency`` support."""
    legacy_currency = item.get("currency")
    invested_currency = normalize_currency_code(
        item.get("invested_currency") or legacy_currency,
        default=portfolio_currency,
    )
    price_currency = normalize_currency_code(
        item.get("price_currency") or legacy_currency,
        default=portfolio_currency,
    )
    quote_currency_raw = item.get("quote_currency")
    quote_currency = (
        normalize_currency_code(quote_currency_raw)
        if quote_currency_raw
        else None
    )
    return invested_currency, price_currency, quote_currency


def _parse_cash_position(
    raw: Any,
    *,
    portfolio_currency: str,
    config_path: Path,
) -> CashPositionConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"cash_position must be an object in {config_path}")
    value = raw.get("value")
    if value is None:
        return None
    try:
        parsed_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"cash_position.value must be numeric in {config_path}"
        ) from exc
    if parsed_value < 0:
        raise ValueError(f"cash_position.value must be >= 0 in {config_path}")
    currency = normalize_currency_code(
        raw.get("currency"),
        default=portfolio_currency,
    )
    return CashPositionConfig(value=parsed_value, currency=currency)


def load_holdings_config(
    config_path: str | Path,
) -> tuple[dict[str, Any], list[HoldingConfig], CashPositionConfig | None]:
    resolved_path = Path(config_path)
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    schema_version = payload.get("schema_version")
    if schema_version != "holdings_scoring_v1":
        raise ValueError(
            f"Unsupported holdings config schema: {schema_version!r} "
            f"(expected holdings_scoring_v1) in {resolved_path}"
        )
    portfolio_currency = normalize_currency_code(
        payload.get("portfolio_currency"),
        default="USD",
    )
    raw_holdings = payload.get("holdings")
    if not isinstance(raw_holdings, list) or not raw_holdings:
        raise ValueError(f"holdings config must contain a non-empty holdings list: {resolved_path}")

    holdings: list[HoldingConfig] = []
    for index, item in enumerate(raw_holdings):
        if not isinstance(item, dict):
            raise ValueError(f"holdings[{index}] must be an object in {resolved_path}")
        ticker = str(item.get("ticker") or "").strip()
        if not ticker:
            raise ValueError(f"holdings[{index}].ticker is required in {resolved_path}")
        symbol_raw = item.get("symbol")
        symbol = str(symbol_raw).strip() if symbol_raw else None
        if symbol == "":
            symbol = None
        invested_sum = item.get("invested_sum")
        average_price = item.get("average_price")
        invested_currency, price_currency, quote_currency = _resolve_holding_currency_fields(
            item,
            portfolio_currency=portfolio_currency,
        )
        holdings.append(
            HoldingConfig(
                ticker=ticker,
                symbol=symbol,
                sleeve=str(item["sleeve"]).strip() if item.get("sleeve") else None,
                invested_sum=float(invested_sum) if invested_sum is not None else None,
                average_price=float(average_price) if average_price is not None else None,
                invested_currency=invested_currency,
                price_currency=price_currency,
                quote_currency=quote_currency,
                currency=price_currency,
                notes=str(item["notes"]).strip() if item.get("notes") else None,
            )
        )
    cash_position = _parse_cash_position(
        payload.get("cash_position"),
        portfolio_currency=portfolio_currency,
        config_path=resolved_path,
    )
    payload["_resolved_portfolio_currency"] = portfolio_currency
    payload["_resolved_cash_position"] = (
        cash_position.to_dict() if cash_position is not None else None
    )
    return payload, holdings, cash_position


def _parse_rate_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _convert_holding_amount(
    amount: float,
    from_currency: str,
    to_currency: str,
    *,
    ticker: str,
    as_of_date: date | None,
    rate_cache: dict[tuple[str, str, str | None], FxRateQuote],
    allow_external_fx: bool,
    fx_warnings: list[str],
    label: str,
) -> tuple[float | None, float | None, str | None]:
    """Convert ``amount`` and return (converted_amount, fx_rate_to_target, rate_date)."""
    source = normalize_currency_code(from_currency)
    target = normalize_currency_code(to_currency)
    if source == target:
        rate_date = _parse_rate_date(as_of_date or datetime.now(timezone.utc).date())
        return amount, 1.0, rate_date

    if not allow_external_fx:
        fx_warnings.append(
            f"{ticker}: skipped {label} FX conversion ({source}->{target}) "
            "because external FX is disabled"
        )
        return None, None, None

    try:
        converted_amount, quote = convert_amount(
            amount,
            source,
            target,
            as_of_date=as_of_date,
            rate_cache=rate_cache,
        )
        return converted_amount, quote.rate, quote.rate_date
    except Exception as exc:
        fx_warnings.append(
            f"{ticker}: {label} FX conversion {source}->{target} failed: {exc}"
        )
        return None, None, None


def enrich_holdings_positions(
    holdings: Sequence[HoldingConfig],
    *,
    portfolio_currency: str = "USD",
    as_of_date: date | None = None,
    rate_cache: dict[tuple[str, str, str | None], FxRateQuote] | None = None,
    allow_external_fx: bool = True,
) -> tuple[list[HoldingConfig], list[str]]:
    """Convert position amounts to portfolio currency and compute weights."""
    resolved_portfolio_currency = normalize_currency_code(portfolio_currency)
    fx_warnings: list[str] = []
    cache = rate_cache if rate_cache is not None else {}
    enriched: list[HoldingConfig] = []

    for holding in holdings:
        invested_sum_usd: float | None = None
        invested_sum_in_price_currency: float | None = None
        fx_rate_to_usd: float | None = None
        fx_rate_price_to_usd: float | None = None
        fx_rate_date: str | None = None

        if holding.invested_sum is not None:
            invested_sum_usd, fx_rate_to_usd, fx_rate_date = _convert_holding_amount(
                holding.invested_sum,
                holding.invested_currency,
                resolved_portfolio_currency,
                ticker=holding.ticker,
                as_of_date=as_of_date,
                rate_cache=cache,
                allow_external_fx=allow_external_fx,
                fx_warnings=fx_warnings,
                label="invested_sum",
            )

        if holding.invested_sum is not None:
            if holding.invested_currency == holding.price_currency:
                invested_sum_in_price_currency = holding.invested_sum
            else:
                invested_sum_in_price_currency, _, _ = _convert_holding_amount(
                    holding.invested_sum,
                    holding.invested_currency,
                    holding.price_currency,
                    ticker=holding.ticker,
                    as_of_date=as_of_date,
                    rate_cache=cache,
                    allow_external_fx=allow_external_fx,
                    fx_warnings=fx_warnings,
                    label="invested_sum_to_price_currency",
                )

        if holding.price_currency == resolved_portfolio_currency:
            fx_rate_price_to_usd = 1.0
        else:
            _, fx_rate_price_to_usd, price_rate_date = _convert_holding_amount(
                1.0,
                holding.price_currency,
                resolved_portfolio_currency,
                ticker=holding.ticker,
                as_of_date=as_of_date,
                rate_cache=cache,
                allow_external_fx=allow_external_fx,
                fx_warnings=fx_warnings,
                label="price_currency",
            )
            if fx_rate_date is None and price_rate_date is not None:
                fx_rate_date = price_rate_date
        if (
            fx_rate_price_to_usd is None
            and holding.invested_currency == holding.price_currency
        ):
            fx_rate_price_to_usd = fx_rate_to_usd

        implied_shares = None
        if (
            invested_sum_in_price_currency is not None
            and holding.average_price
            and holding.average_price != 0
        ):
            implied_shares = invested_sum_in_price_currency / holding.average_price

        enriched.append(
            HoldingConfig(
                ticker=holding.ticker,
                symbol=holding.symbol,
                sleeve=holding.sleeve,
                invested_sum=holding.invested_sum,
                average_price=holding.average_price,
                invested_currency=holding.invested_currency,
                price_currency=holding.price_currency,
                quote_currency=holding.quote_currency,
                currency=holding.price_currency,
                notes=holding.notes,
                invested_sum_usd=invested_sum_usd,
                invested_sum_in_price_currency=invested_sum_in_price_currency,
                fx_rate_to_usd=fx_rate_to_usd,
                fx_rate_price_to_usd=fx_rate_price_to_usd,
                fx_rate_date=fx_rate_date,
                implied_shares=implied_shares,
            )
        )

    total_invested = sum(
        holding.invested_sum_usd
        for holding in enriched
        if holding.invested_sum_usd is not None
    )
    weighted_holdings: list[HoldingConfig] = []
    for holding in enriched:
        portfolio_weight_pct = None
        if holding.invested_sum_usd is not None and total_invested > 0:
            portfolio_weight_pct = holding.invested_sum_usd / total_invested * 100.0
        weighted_holdings.append(
            HoldingConfig(
                ticker=holding.ticker,
                symbol=holding.symbol,
                sleeve=holding.sleeve,
                invested_sum=holding.invested_sum,
                average_price=holding.average_price,
                invested_currency=holding.invested_currency,
                price_currency=holding.price_currency,
                quote_currency=holding.quote_currency,
                currency=holding.price_currency,
                notes=holding.notes,
                invested_sum_usd=holding.invested_sum_usd,
                invested_sum_in_price_currency=holding.invested_sum_in_price_currency,
                fx_rate_to_usd=holding.fx_rate_to_usd,
                fx_rate_price_to_usd=holding.fx_rate_price_to_usd,
                fx_rate_date=holding.fx_rate_date,
                implied_shares=holding.implied_shares,
                portfolio_weight_pct=portfolio_weight_pct,
            )
        )

    return weighted_holdings, fx_warnings


def enrich_cash_position(
    cash_position: CashPositionConfig | None,
    *,
    portfolio_currency: str = "USD",
    as_of_date: date | None = None,
    rate_cache: dict[tuple[str, str, str | None], FxRateQuote] | None = None,
    allow_external_fx: bool = True,
    fx_warnings: list[str] | None = None,
) -> CashPositionConfig | None:
    """Convert cash to portfolio currency when FX resolution is enabled."""
    if cash_position is None:
        return None
    resolved_portfolio_currency = normalize_currency_code(portfolio_currency)
    warnings = fx_warnings if fx_warnings is not None else []
    cache = rate_cache if rate_cache is not None else {}
    if cash_position.currency == resolved_portfolio_currency:
        return CashPositionConfig(
            value=cash_position.value,
            currency=cash_position.currency,
            value_in_portfolio_currency=cash_position.value,
            fx_rate_to_portfolio=1.0,
        )
    if not allow_external_fx:
        return cash_position
    converted_value, fx_rate, fx_rate_date = _convert_holding_amount(
        cash_position.value,
        cash_position.currency,
        resolved_portfolio_currency,
        ticker="CASH",
        as_of_date=as_of_date,
        rate_cache=cache,
        allow_external_fx=allow_external_fx,
        fx_warnings=warnings,
        label="cash_position",
    )
    return CashPositionConfig(
        value=cash_position.value,
        currency=cash_position.currency,
        value_in_portfolio_currency=converted_value,
        fx_rate_to_portfolio=fx_rate,
        fx_rate_date=fx_rate_date,
    )


def _parse_profile_names(profile_names_json: str | None) -> list[str]:
    if not profile_names_json:
        return []
    try:
        parsed = json.loads(profile_names_json)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [str(name) for name in parsed]
    return []


def _iter_weekly_prediction_databases(duckdb_runs_root: Path) -> list[Path]:
    if not duckdb_runs_root.exists():
        return []
    return sorted(duckdb_runs_root.rglob("move_prediction_*.duckdb"))


def _resolve_run_metadata_row(
    database_path: Path,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    if run_id:
        rows = query_move_prediction_duckdb(
            database_path,
            """
            SELECT run_id, created_at_utc, suite_name, profile_names_json
            FROM run_metadata
            WHERE run_id = ?
            LIMIT 1
            """,
            parameters=[run_id],
        )
        return rows[0] if rows else None

    rows = query_move_prediction_duckdb(
        database_path,
        """
        SELECT run_id, created_at_utc, suite_name, profile_names_json
        FROM run_metadata
        ORDER BY created_at_utc DESC
        LIMIT 1
        """,
    )
    return rows[0] if rows else None


def resolve_latest_move_prediction_source(
    *,
    duckdb_runs_root: str | Path | None = None,
    database_path: str | Path | None = None,
    run_id: str | None = None,
) -> MovePredictionSource:
    """Resolve the newest move-prediction run across weekly DuckDB stores."""
    if database_path is not None:
        resolved_db = Path(database_path)
        if not resolved_db.exists():
            raise FileNotFoundError(f"DuckDB database not found: {resolved_db}")
        row = _resolve_run_metadata_row(resolved_db, run_id=run_id)
        if row is None:
            raise ValueError(
                f"No matching run found in DuckDB database: {resolved_db} "
                f"(run_id={run_id!r})"
            )
        return MovePredictionSource(
            database_path=resolved_db,
            run_id=str(row["run_id"]),
            created_at_utc=row.get("created_at_utc"),
            suite_name=str(row["suite_name"]) if row.get("suite_name") else None,
            profile_names=_parse_profile_names(row.get("profile_names_json")),
        )

    root = Path(duckdb_runs_root) if duckdb_runs_root is not None else DEFAULT_DUCKDB_RUNS_ROOT
    best_row: dict[str, Any] | None = None
    best_db: Path | None = None

    for candidate_db in _iter_weekly_prediction_databases(root):
        row = _resolve_run_metadata_row(candidate_db, run_id=run_id)
        if row is None:
            continue
        created_at = row.get("created_at_utc")
        if best_row is None:
            best_row = row
            best_db = candidate_db
            continue
        if created_at is not None and (
            best_row.get("created_at_utc") is None
            or created_at > best_row.get("created_at_utc")
        ):
            best_row = row
            best_db = candidate_db

    if best_row is None or best_db is None:
        raise ValueError(
            f"No move-prediction runs found under DuckDB root: {root} "
            f"(run_id={run_id!r})"
        )

    return MovePredictionSource(
        database_path=best_db,
        run_id=str(best_row["run_id"]),
        created_at_utc=best_row.get("created_at_utc"),
        suite_name=str(best_row["suite_name"]) if best_row.get("suite_name") else None,
        profile_names=_parse_profile_names(best_row.get("profile_names_json")),
    )


def _table_exists(database_path: Path, table_name: str) -> bool:
    tables = describe_move_prediction_duckdb(database_path)
    return any(str(row.get("table_name")) == table_name for row in tables)


def _fetch_distinct_symbols(
    database_path: Path,
    run_id: str,
    table_name: str,
) -> list[str]:
    rows = query_move_prediction_duckdb(
        database_path,
        f"""
        SELECT DISTINCT symbol
        FROM {table_name}
        WHERE run_id = ?
        ORDER BY symbol
        """,
        parameters=[run_id],
    )
    return [str(row["symbol"]) for row in rows if row.get("symbol")]


def _fetch_raw_scan_symbol_catalog(
    database_path: Path,
    run_id: str,
) -> list[RawScanSymbolEntry]:
    if not _table_exists(database_path, "raw_scan_rows"):
        return []
    rows = query_move_prediction_duckdb(
        database_path,
        """
        SELECT DISTINCT symbol, company
        FROM raw_scan_rows
        WHERE run_id = ?
          AND symbol IS NOT NULL
          AND symbol != ''
        ORDER BY symbol
        """,
        parameters=[run_id],
    )
    catalog: list[RawScanSymbolEntry] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        _, bare_ticker = _split_exchange_symbol(symbol)
        catalog.append(
            RawScanSymbolEntry(
                symbol=symbol,
                company=_row_company(row),
                bare_ticker=bare_ticker,
            )
        )
    return catalog


def match_holdings_to_db_symbols(
    holdings: Sequence[HoldingConfig],
    db_symbols: Sequence[str],
    *,
    raw_catalog: Sequence[RawScanSymbolEntry] | None = None,
) -> tuple[list[MatchedHolding], list[str]]:
    matched: list[MatchedHolding] = []
    unmatched: list[str] = []
    used_lookup_keys: set[str] = set()
    catalog = list(raw_catalog or [])

    for holding in holdings:
        catalog_entry = _catalog_entry_for_holding(holding, catalog)
        if catalog_entry is not None:
            lookup_key = catalog_entry.symbol
            if lookup_key in used_lookup_keys:
                unmatched.append(holding_config_label(holding))
                continue
            used_lookup_keys.add(lookup_key)
            matched.append(
                MatchedHolding(
                    holding=holding,
                    db_symbol=catalog_entry.bare_ticker,
                    scan_symbol=catalog_entry.symbol,
                    company=catalog_entry.company or None,
                )
            )
            continue

        match_symbol = next(
            (
                db_symbol
                for db_symbol in db_symbols
                if db_symbol not in used_lookup_keys
                and holding_matches_db_symbol_for_holding(holding, db_symbol)
            ),
            None,
        )
        if match_symbol is None:
            unmatched.append(holding_config_label(holding))
            continue
        used_lookup_keys.add(match_symbol)
        matched.append(MatchedHolding(holding=holding, db_symbol=match_symbol))

    return matched, unmatched


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return path

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    return path


def _build_symbol_values_clause(matched: Sequence[MatchedHolding]) -> tuple[str, list[Any]]:
    placeholders = ", ".join("?" for _ in matched)
    parameters = [match.db_symbol for match in matched]
    return placeholders, parameters


def _build_symbol_match_clause(matched: Sequence[MatchedHolding]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    for match in matched:
        if match.company:
            clauses.append("(symbol = ? AND company = ?)")
            parameters.extend([match.db_symbol, match.company])
        else:
            clauses.append("symbol = ?")
            parameters.append(match.db_symbol)
    return " OR ".join(clauses), parameters


def _holding_position_fields(holding: HoldingConfig) -> dict[str, Any]:
    return {
        "sleeve": holding.sleeve or "",
        "invested_sum": holding.invested_sum,
        "invested_currency": holding.invested_currency,
        "average_price": holding.average_price,
        "price_currency": holding.price_currency,
        "quote_currency": holding.quote_currency or "",
        "currency": holding.price_currency,
        "invested_sum_usd": holding.invested_sum_usd,
        "invested_sum_in_price_currency": holding.invested_sum_in_price_currency,
        "fx_rate_to_usd": holding.fx_rate_to_usd,
        "fx_rate_price_to_usd": holding.fx_rate_price_to_usd,
        "fx_rate_date": holding.fx_rate_date,
        "implied_shares": holding.implied_shares,
        "portfolio_weight_pct": holding.portfolio_weight_pct,
        "notes": holding.notes or "",
        "config_symbol": holding.symbol or "",
    }


def _holding_metadata_row(match: MatchedHolding) -> dict[str, Any]:
    return {
        "config_ticker": match.holding.ticker,
        "config_symbol": match.holding.symbol or "",
        "matched_symbol": match.scan_symbol or match.db_symbol,
        "db_symbol": match.db_symbol,
        "matched_company": match.company or "",
        **_holding_position_fields(match.holding),
    }


def _coerce_positive_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _resolve_close_quote_currency(
    *,
    currency: str | None = None,
    exchange: str | None = None,
    country: str | None = None,
    quote_currency_override: str | None = None,
) -> str | None:
    """Infer the listing quote currency for a scan close price."""
    if quote_currency_override:
        return normalize_currency_code(quote_currency_override)
    if currency:
        return normalize_currency_code(currency)

    exchange_key = str(exchange or "").strip().upper()
    if exchange_key:
        if exchange_key in EXCHANGE_QUOTE_CURRENCY:
            return EXCHANGE_QUOTE_CURRENCY[exchange_key]
        for prefix, quote_currency in EXCHANGE_QUOTE_CURRENCY.items():
            if exchange_key.startswith(f"{prefix}:") or exchange_key.startswith(prefix):
                return quote_currency

    country_key = str(country or "").strip().upper()
    if country_key in COUNTRY_QUOTE_CURRENCY:
        return COUNTRY_QUOTE_CURRENCY[country_key]
    return None


def _convert_value_between_currencies(
    amount: float,
    from_currency: str,
    to_currency: str,
    *,
    context: MarkToMarketContext | None = None,
    warning_ticker: str = "",
) -> float | None:
    source = normalize_currency_code(from_currency)
    target = normalize_currency_code(to_currency)
    if source == target:
        return amount

    if context is None or not context.resolve_fx_rates:
        if context is not None:
            context.fx_warnings.append(
                f"{warning_ticker}: skipped close FX conversion "
                f"({source}->{target}) because external FX is disabled"
            )
        return None

    try:
        converted_amount, _quote = convert_amount(
            amount,
            source,
            target,
            as_of_date=context.as_of_date,
            rate_cache=context.rate_cache,
        )
        return converted_amount
    except Exception as exc:
        if context is not None:
            context.fx_warnings.append(
                f"{warning_ticker}: close FX conversion {source}->{target} failed: {exc}"
            )
        return None


def _build_mark_to_market_fields(
    holding: HoldingConfig,
    close: Any,
    *,
    close_quote_currency: str | None = None,
    close_source: str = "raw_scan_rows.close",
    mtm_context: MarkToMarketContext | None = None,
) -> dict[str, Any]:
    """Compute mark-to-market value from run close and configured position size.

    ``close`` is the listing quote from scan data. When its currency differs from
    ``holding.price_currency``, it is converted before comparing to ``average_price``.
    USD outputs use ``fx_rate_price_to_usd`` / ``fx_rate_to_usd`` as appropriate.
    """
    close_quote = _coerce_positive_float(close)
    price_currency = normalize_currency_code(holding.price_currency)
    invested_currency = normalize_currency_code(holding.invested_currency)
    resolved_quote_currency = _resolve_close_quote_currency(
        currency=close_quote_currency,
        quote_currency_override=holding.quote_currency,
    ) or price_currency
    close_price: float | None = None
    close_usd: float | None = None
    current_value: float | None = None
    current_value_usd: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_usd: float | None = None
    unrealized_return_pct: float | None = None
    portfolio_currency = normalize_currency_code(
        mtm_context.portfolio_currency if mtm_context else "USD"
    )

    if close_quote is not None:
        close_price = _convert_value_between_currencies(
            close_quote,
            resolved_quote_currency,
            price_currency,
            context=mtm_context,
            warning_ticker=holding.ticker,
        )
        if close_price is None and resolved_quote_currency == price_currency:
            close_price = close_quote

        if close_price is not None:
            if price_currency == portfolio_currency:
                close_usd = close_price
            elif holding.fx_rate_price_to_usd is not None:
                close_usd = close_price * holding.fx_rate_price_to_usd
            else:
                close_usd = _convert_value_between_currencies(
                    close_price,
                    price_currency,
                    portfolio_currency,
                    context=mtm_context,
                    warning_ticker=holding.ticker,
                )

        if close_price is not None:
            invested_in_price_currency = holding.invested_sum_in_price_currency
            if holding.implied_shares is not None:
                current_value = holding.implied_shares * close_price
            elif (
                invested_in_price_currency is not None
                and holding.average_price is not None
                and holding.average_price != 0
            ):
                current_value = invested_in_price_currency * (
                    close_price / holding.average_price
                )

            if current_value is not None:
                cost_basis_in_price_currency = holding.invested_sum_in_price_currency
                if (
                    cost_basis_in_price_currency is None
                    and holding.implied_shares is not None
                    and holding.average_price is not None
                ):
                    cost_basis_in_price_currency = (
                        holding.implied_shares * holding.average_price
                    )
                elif (
                    cost_basis_in_price_currency is None
                    and holding.invested_sum is not None
                    and invested_currency == price_currency
                ):
                    cost_basis_in_price_currency = holding.invested_sum

                if cost_basis_in_price_currency is not None:
                    unrealized_pnl = current_value - cost_basis_in_price_currency

                if price_currency == portfolio_currency:
                    current_value_usd = current_value
                elif holding.fx_rate_price_to_usd is not None:
                    current_value_usd = current_value * holding.fx_rate_price_to_usd
                elif mtm_context is not None and mtm_context.resolve_fx_rates:
                    current_value_usd = _convert_value_between_currencies(
                        current_value,
                        price_currency,
                        portfolio_currency,
                        context=mtm_context,
                        warning_ticker=holding.ticker,
                    )

                if (
                    current_value_usd is not None
                    and holding.invested_sum_usd is not None
                    and holding.invested_sum_usd != 0
                ):
                    unrealized_pnl_usd = current_value_usd - holding.invested_sum_usd
                    unrealized_return_pct = (
                        unrealized_pnl_usd / holding.invested_sum_usd * 100.0
                    )
                elif (
                    unrealized_pnl is not None
                    and cost_basis_in_price_currency is not None
                    and cost_basis_in_price_currency != 0
                ):
                    unrealized_return_pct = (
                        unrealized_pnl / cost_basis_in_price_currency * 100.0
                    )
                    if (
                        unrealized_pnl_usd is None
                        and holding.fx_rate_to_usd is not None
                        and invested_currency == price_currency
                    ):
                        unrealized_pnl_usd = unrealized_pnl * holding.fx_rate_to_usd

    return {
        "close_quote": close_quote,
        "close_quote_currency": resolved_quote_currency if close_quote is not None else "",
        "close": close_price,
        "close_usd": close_usd,
        "close_source": close_source if close_quote is not None else "",
        "current_value": current_value,
        "current_value_currency": price_currency if current_value is not None else "",
        "current_value_usd": current_value_usd,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_usd": unrealized_pnl_usd,
        "unrealized_return_pct": unrealized_return_pct,
    }


def _fetch_symbol_close_quotes(
    source: MovePredictionSource,
    matched: Sequence[MatchedHolding],
    raw_perf_by_symbol: dict[str, dict[str, Any]],
) -> dict[str, CloseQuote]:
    """Resolve scan-time close prices, preferring ``raw_scan_rows.close``."""
    close_by_symbol: dict[str, CloseQuote] = {}
    missing: list[MatchedHolding] = []

    holding_by_lookup = {match.lookup_key: match.holding for match in matched}

    for match in matched:
        raw_row = raw_perf_by_symbol.get(match.lookup_key, {})
        close_price = _coerce_positive_float(raw_row.get("close"))
        if close_price is not None:
            close_by_symbol[match.lookup_key] = CloseQuote(
                price=close_price,
                quote_currency=_resolve_close_quote_currency(
                    currency=raw_row.get("currency"),
                    exchange=raw_row.get("exchange"),
                    country=raw_row.get("country"),
                    quote_currency_override=holding_by_lookup[match.lookup_key].quote_currency,
                ),
                source="raw_scan_rows.close",
            )
        else:
            missing.append(match)

    if missing and _table_exists(source.database_path, "profile_horizon_scores"):
        match_clause, match_params = _build_symbol_match_clause(missing)
        rows = query_move_prediction_duckdb(
            source.database_path,
            f"""
            SELECT symbol, company, MAX(close) AS close
            FROM profile_horizon_scores
            WHERE run_id = ?
              AND ({match_clause})
              AND close IS NOT NULL
            GROUP BY symbol, company
            """,
            parameters=[source.run_id, *match_params],
        )
        for row in rows:
            owning_match = _resolve_row_to_match(row, missing)
            if owning_match is None:
                continue
            close_price = _coerce_positive_float(row.get("close"))
            if close_price is None or owning_match.lookup_key in close_by_symbol:
                continue
            holding = holding_by_lookup.get(owning_match.lookup_key)
            raw_row = raw_perf_by_symbol.get(owning_match.lookup_key, {})
            close_by_symbol[owning_match.lookup_key] = CloseQuote(
                price=close_price,
                quote_currency=_resolve_close_quote_currency(
                    currency=raw_row.get("currency"),
                    exchange=raw_row.get("exchange"),
                    country=raw_row.get("country"),
                    quote_currency_override=holding.quote_currency if holding else None,
                ),
                source="profile_horizon_scores.close",
            )

    for match in matched:
        close_by_symbol.setdefault(match.lookup_key, CloseQuote(price=None, source=""))
    return close_by_symbol


def _order_summary_row(
    row: dict[str, Any],
    perf_fields: Sequence[str],
) -> dict[str, Any]:
    """Keep summary CSV columns grouped: identity, position, mark-to-market, then scores."""
    leading_keys = [
        "config_ticker",
        "config_symbol",
        "matched_symbol",
        "company",
        "sector",
        "industry",
        "sleeve",
        "notes",
        "invested_sum",
        "invested_currency",
        "average_price",
        "price_currency",
        "quote_currency",
        "currency",
        "implied_shares",
        "invested_sum_in_price_currency",
        "invested_sum_usd",
        "fx_rate_to_usd",
        "fx_rate_price_to_usd",
        "fx_rate_date",
        "portfolio_weight_pct",
        *MARK_TO_MARKET_FIELDS,
        "source_run_id",
        "suite_name",
        "market_cap_basic",
        "conviction_score",
        "conviction_rank_overall",
        "conviction_sleeve",
        "conviction_entry_readiness",
        "conviction_breakout_tier",
    ]
    consensus_keys = [
        f"consensus_{horizon}_{suffix}"
        for horizon in HORIZON_NAMES
        for suffix in ("score", "direction", "ras", "agreement", "manager_action")
    ]
    ordered_keys = [
        *leading_keys,
        *consensus_keys,
        *perf_fields,
    ]
    seen: set[str] = set()
    final_row: dict[str, Any] = {}
    for key in ordered_keys:
        if key in row:
            final_row[key] = row[key]
            seen.add(key)
    for key, value in row.items():
        if key not in seen:
            final_row[key] = value
    return final_row


def _fetch_consensus_rows(
    source: MovePredictionSource,
    matched: Sequence[MatchedHolding],
) -> dict[str, dict[str, dict[str, Any]]]:
    if not matched:
        return {}
    match_clause, match_params = _build_symbol_match_clause(matched)
    rows = query_move_prediction_duckdb(
        source.database_path,
        f"""
        SELECT symbol, company, horizon_name, score, direction, confidence,
            agreement_ratio, opinions, risk_adjusted_score, risk_tier,
            manager_action_signal
        FROM consensus_horizon_scores
        WHERE run_id = ?
          AND ({match_clause})
        """,
        parameters=[source.run_id, *match_params],
    )
    by_symbol: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        owning_match = _resolve_row_to_match(row, matched)
        if owning_match is None:
            continue
        horizon = str(row["horizon_name"])
        by_symbol.setdefault(owning_match.lookup_key, {})[horizon] = row
    return by_symbol


def _fetch_conviction_rows(
    source: MovePredictionSource,
    matched: Sequence[MatchedHolding],
) -> dict[str, dict[str, Any]]:
    if not matched or not _table_exists(source.database_path, "conviction_rankings"):
        return {}
    match_clause, match_params = _build_symbol_match_clause(matched)
    rows = query_move_prediction_duckdb(
        source.database_path,
        f"""
        SELECT symbol, company, sector, industry, sleeve, conviction_score,
            rank_overall, rank_in_sleeve, display_flag, manager_action_signal,
            days_score, days_ras, weeks_score, weeks_ras, months_score, months_ras,
            years_score, years_ras, agreement_ratio, opinions, bullish_profile_count,
            breakout_conviction_tier, entry_readiness, size_tier, tape_pass,
            earnings_days_until, exclusion_reason
        FROM conviction_rankings
        WHERE run_id = ?
          AND ({match_clause})
        """,
        parameters=[source.run_id, *match_params],
    )
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        owning_match = _resolve_row_to_match(row, matched)
        if owning_match is None:
            continue
        by_symbol[owning_match.lookup_key] = row
    return by_symbol


def _available_raw_scan_columns(database_path: Path) -> set[str]:
    rows = query_move_prediction_duckdb(
        database_path,
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'main'
          AND table_name = 'raw_scan_rows'
        """,
    )
    return {str(row["column_name"]) for row in rows}


def _resolve_raw_perf_fields(database_path: Path) -> list[str]:
    available_columns = _available_raw_scan_columns(database_path)
    return [field_name for field_name in RAW_PERF_FIELDS if field_name in available_columns]


def _fetch_raw_perf_rows(
    source: MovePredictionSource,
    matched: Sequence[MatchedHolding],
) -> dict[str, dict[str, Any]]:
    if not matched:
        return {}
    perf_fields = _resolve_raw_perf_fields(source.database_path)
    available_columns = _available_raw_scan_columns(source.database_path)
    quote_meta_columns = [
        column_name
        for column_name in ("exchange", "country", "currency")
        if column_name in available_columns
    ]
    quoted_meta_fields = ", ".join(
        f'r."{column_name}"' for column_name in quote_meta_columns
    )
    meta_select = f"{quoted_meta_fields}," if quoted_meta_fields else ""
    symbol_rows = [
        {
            "symbol": match.scan_symbol or match.db_symbol,
            "config_ticker": match.holding.ticker,
            "lookup_key": match.lookup_key,
        }
        for match in matched
    ]
    values_sql = ", ".join("(?, ?, ?)" for _ in symbol_rows)
    flat_params: list[Any] = []
    for row in symbol_rows:
        flat_params.extend([row["symbol"], row["config_ticker"], row["lookup_key"]])

    quoted_perf_fields = ", ".join(
        f'r."{field_name}"' for field_name in perf_fields
    )
    perf_select = (
        f"{quoted_perf_fields}" if quoted_perf_fields else "NULL AS _no_perf_fields"
    )
    rows = query_move_prediction_duckdb(
        source.database_path,
        f"""
        WITH target(symbol, config_ticker, lookup_key) AS (
            VALUES {values_sql}
        )
        SELECT target.lookup_key,
            target.config_ticker,
            target.symbol AS matched_symbol,
            r.symbol AS raw_symbol,
            COALESCE(r.company, r."Company") AS company,
            r.sector,
            r.industry,
            r.market_cap_basic,
            {meta_select}
            r.close,
            {perf_select}
        FROM target
        LEFT JOIN raw_scan_rows r
            ON r.run_id = ?
            AND r.symbol = target.symbol
        """,
        parameters=[*flat_params, source.run_id],
    )
    return {str(row["lookup_key"]): row for row in rows if row.get("lookup_key")}


def _build_summary_rows(
    source: MovePredictionSource,
    matched: Sequence[MatchedHolding],
    consensus_by_symbol: dict[str, dict[str, dict[str, Any]]],
    conviction_by_symbol: dict[str, dict[str, Any]],
    raw_perf_by_symbol: dict[str, dict[str, Any]],
    close_by_symbol: dict[str, CloseQuote],
    perf_fields: Sequence[str] | None = None,
    mtm_context: MarkToMarketContext | None = None,
) -> list[dict[str, Any]]:
    summary_rows: list[dict[str, Any]] = []
    resolved_perf_fields = list(perf_fields or RAW_PERF_FIELDS)
    for match in matched:
        row = _holding_metadata_row(match)
        row["source_run_id"] = source.run_id
        row["suite_name"] = source.suite_name or ""

        consensus = consensus_by_symbol.get(match.lookup_key, {})
        for horizon_name in HORIZON_NAMES:
            horizon = consensus.get(horizon_name, {})
            row[f"consensus_{horizon_name}_score"] = horizon.get("score")
            row[f"consensus_{horizon_name}_direction"] = horizon.get("direction")
            row[f"consensus_{horizon_name}_ras"] = horizon.get("risk_adjusted_score")
            row[f"consensus_{horizon_name}_agreement"] = horizon.get("agreement_ratio")
            row[f"consensus_{horizon_name}_manager_action"] = horizon.get(
                "manager_action_signal"
            )

        conviction = conviction_by_symbol.get(match.lookup_key, {})
        row["conviction_score"] = conviction.get("conviction_score")
        row["conviction_rank_overall"] = conviction.get("rank_overall")
        row["conviction_sleeve"] = conviction.get("sleeve")
        row["conviction_entry_readiness"] = conviction.get("entry_readiness")
        row["conviction_breakout_tier"] = conviction.get("breakout_conviction_tier")

        raw_perf = raw_perf_by_symbol.get(match.lookup_key, {})
        row["company"] = (
            raw_perf.get("company")
            or conviction.get("company")
            or conviction.get("Company")
            or match.company
        )
        row["sector"] = raw_perf.get("sector") or conviction.get("sector")
        row["industry"] = raw_perf.get("industry") or conviction.get("industry")
        row["market_cap_basic"] = raw_perf.get("market_cap_basic")
        close_quote = close_by_symbol.get(match.lookup_key, CloseQuote(price=None))
        row.update(
            _build_mark_to_market_fields(
                match.holding,
                close_quote.price,
                close_quote_currency=close_quote.quote_currency,
                close_source=close_quote.source or "raw_scan_rows.close",
                mtm_context=mtm_context,
            )
        )
        for perf_field in resolved_perf_fields:
            if perf_field != "close":
                row[perf_field] = raw_perf.get(perf_field)

        summary_rows.append(_order_summary_row(row, resolved_perf_fields))
    return summary_rows


def _fetch_profile_horizon_rows(
    source: MovePredictionSource,
    matched: Sequence[MatchedHolding],
) -> list[dict[str, Any]]:
    if not matched:
        return []
    match_clause, match_params = _build_symbol_match_clause(matched)
    rows = query_move_prediction_duckdb(
        source.database_path,
        f"""
        SELECT symbol, company, profile_name, horizon_name, score, direction,
            confidence, coverage, setup, risk_adjusted_score, risk_tier,
            manager_action_signal
        FROM profile_horizon_scores
        WHERE run_id = ?
          AND ({match_clause})
        ORDER BY symbol, profile_name, horizon_name
        """,
        parameters=[source.run_id, *match_params],
    )
    export_rows: list[dict[str, Any]] = []
    for row in rows:
        owning_match = _resolve_row_to_match(row, matched)
        export_row = {
            "config_ticker": owning_match.holding.ticker if owning_match else "",
            "matched_symbol": row.get("symbol"),
            **{key: row.get(key) for key in row if key != "symbol"},
        }
        if owning_match:
            export_row.update(_holding_position_fields(owning_match.holding))
        export_rows.append(export_row)
    return export_rows


def _fetch_performance_tracking_rows(
    source: MovePredictionSource,
    matched: Sequence[MatchedHolding],
) -> list[dict[str, Any]]:
    if not matched or not _table_exists(source.database_path, "profile_performance_tracking"):
        return []
    match_clause, match_params = _build_symbol_match_clause(matched)
    rows = query_move_prediction_duckdb(
        source.database_path,
        f"""
        SELECT symbol, company, profile_name, horizon_name, performance_field,
            performance_label, score, performance_value, direction,
            manager_action_signal
        FROM profile_performance_tracking
        WHERE run_id = ?
          AND ({match_clause})
        ORDER BY symbol, profile_name, horizon_name, performance_field
        """,
        parameters=[source.run_id, *match_params],
    )
    export_rows: list[dict[str, Any]] = []
    for row in rows:
        owning_match = _resolve_row_to_match(row, matched)
        export_rows.append(
            {
                "config_ticker": owning_match.holding.ticker if owning_match else "",
                "matched_symbol": row.get("symbol"),
                **{key: row.get(key) for key in row if key != "symbol"},
            }
        )
    return export_rows


def _fetch_conviction_export_rows(
    source: MovePredictionSource,
    matched: Sequence[MatchedHolding],
) -> list[dict[str, Any]]:
    conviction_by_symbol = _fetch_conviction_rows(source, matched)
    export_rows: list[dict[str, Any]] = []
    for match in matched:
        conviction = conviction_by_symbol.get(match.lookup_key, {})
        if not conviction:
            continue
        export_rows.append(
            {
                **_holding_metadata_row(match),
                **{key: value for key, value in conviction.items() if key != "symbol"},
            }
        )
    return export_rows


def _fetch_raw_perf_export_rows(
    source: MovePredictionSource,
    matched: Sequence[MatchedHolding],
    close_by_symbol: dict[str, CloseQuote],
    mtm_context: MarkToMarketContext | None = None,
) -> list[dict[str, Any]]:
    raw_perf_by_symbol = _fetch_raw_perf_rows(source, matched)
    export_rows: list[dict[str, Any]] = []
    for match in matched:
        raw_perf = raw_perf_by_symbol.get(match.lookup_key, {})
        close_quote = close_by_symbol.get(match.lookup_key, CloseQuote(price=None))
        export_row = {
            **_holding_metadata_row(match),
            **raw_perf,
            **_build_mark_to_market_fields(
                match.holding,
                close_quote.price,
                close_quote_currency=close_quote.quote_currency,
                close_source=close_quote.source or "raw_scan_rows.close",
                mtm_context=mtm_context,
            ),
        }
        export_rows.append(export_row)
    return export_rows


def _empty_mark_to_market_fields() -> dict[str, Any]:
    return {field_name: None for field_name in MARK_TO_MARKET_FIELDS} | {
        "close_source": "",
        "close_quote_currency": "",
        "current_value_currency": "",
    }


def _build_unmatched_rows(unmatched_tickers: Sequence[str], holdings: Sequence[HoldingConfig]) -> list[dict[str, Any]]:
    holding_by_label = {holding_config_label(holding): holding for holding in holdings}
    rows: list[dict[str, Any]] = []
    for label in unmatched_tickers:
        holding = holding_by_label.get(label)
        position_fields = (
            _holding_position_fields(holding)
            if holding
            else {
                "sleeve": "",
                "invested_sum": None,
                "invested_currency": "",
                "average_price": None,
                "price_currency": "",
                "quote_currency": "",
                "currency": "",
                "invested_sum_in_price_currency": None,
                "invested_sum_usd": None,
                "fx_rate_to_usd": None,
                "fx_rate_price_to_usd": None,
                "fx_rate_date": None,
                "implied_shares": None,
                "portfolio_weight_pct": None,
                "notes": "",
                "config_symbol": "",
            }
        )
        rows.append(
            {
                "config_ticker": holding.ticker if holding else label,
                **position_fields,
                **_empty_mark_to_market_fields(),
                "reason": "no_symbol_match_in_source_run",
            }
        )
    return rows


def _scan_row_matches_holding(row: dict[str, Any], holding: HoldingConfig) -> bool:
    candidates = [
        row.get("symbol"),
        get_symbol_name(row),
    ]
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        candidates.append(ticker_view.get("name"))
    for candidate in candidates:
        candidate_text = str(candidate or "").strip()
        if candidate_text and holding_matches_db_symbol_for_holding(holding, candidate_text):
            return True
    return False


def _build_api_fallback_raw_perf_rows(
    holdings: Sequence[HoldingConfig],
    scan_data: Sequence[dict[str, Any]],
    mtm_context: MarkToMarketContext | None = None,
) -> list[dict[str, Any]]:
    export_rows: list[dict[str, Any]] = []
    for holding in holdings:
        matched_row = next(
            (row for row in scan_data if _scan_row_matches_holding(row, holding)),
            None,
        )
        if matched_row is None:
            continue
        export_row = {
            "config_ticker": holding.ticker,
            "config_symbol": holding.symbol or "",
            "matched_symbol": get_symbol_name(matched_row),
            **_holding_position_fields(holding),
            "company": matched_row.get("name") or matched_row.get("description"),
            "sector": matched_row.get("sector"),
            "industry": matched_row.get("industry"),
            "market_cap_basic": matched_row.get("market_cap_basic"),
            **_build_mark_to_market_fields(
                holding,
                matched_row.get("close"),
                close_quote_currency=_resolve_close_quote_currency(
                    currency=matched_row.get("currency"),
                    exchange=matched_row.get("exchange"),
                    country=matched_row.get("country"),
                    quote_currency_override=holding.quote_currency,
                ),
                close_source="api_scan_data.close",
                mtm_context=mtm_context,
            ),
        }
        for perf_field in RAW_PERF_FIELDS:
            if perf_field != "close":
                export_row[perf_field] = matched_row.get(perf_field)
        export_rows.append(export_row)
    return export_rows


def export_move_prediction_v1_scan(
    source: MovePredictionSource,
    holdings: Sequence[HoldingConfig],
    matched: Sequence[MatchedHolding],
    unmatched_tickers: Sequence[str],
    output_scan_dir: Path,
    scan_data: list[dict[str, Any]] | None = None,
    mtm_context: MarkToMarketContext | None = None,
) -> ScanExportResult:
    output_scan_dir.mkdir(parents=True, exist_ok=True)
    exported_files: dict[str, str] = {}
    counts: dict[str, int] = {}

    consensus_by_symbol = _fetch_consensus_rows(source, matched)
    conviction_by_symbol = _fetch_conviction_rows(source, matched)
    raw_perf_by_symbol = _fetch_raw_perf_rows(source, matched)
    perf_fields = _resolve_raw_perf_fields(source.database_path)
    close_by_symbol = _fetch_symbol_close_quotes(source, matched, raw_perf_by_symbol)

    summary_rows = _build_summary_rows(
        source,
        matched,
        consensus_by_symbol,
        conviction_by_symbol,
        raw_perf_by_symbol,
        close_by_symbol,
        perf_fields=perf_fields,
        mtm_context=mtm_context,
    )
    profile_rows = _fetch_profile_horizon_rows(source, matched)
    performance_rows = _fetch_performance_tracking_rows(source, matched)
    conviction_rows = _fetch_conviction_export_rows(source, matched)
    raw_perf_rows = _fetch_raw_perf_export_rows(
        source,
        matched,
        close_by_symbol,
        mtm_context=mtm_context,
    )
    unmatched_rows = _build_unmatched_rows(unmatched_tickers, holdings)

    api_rows: list[dict[str, Any]] = []
    if scan_data:
        api_rows = _build_api_fallback_raw_perf_rows(holdings, scan_data, mtm_context)
        if api_rows and not raw_perf_rows:
            raw_perf_rows = api_rows
        holding_by_ticker = {holding.ticker: holding for holding in holdings}
        for summary_row in summary_rows:
            if summary_row.get("close") is not None:
                continue
            api_match = next(
                (
                    row
                    for row in api_rows
                    if row.get("config_ticker") == summary_row.get("config_ticker")
                ),
                None,
            )
            if api_match is None:
                continue
            holding = holding_by_ticker.get(str(summary_row.get("config_ticker") or ""))
            if holding is None:
                continue
            matched_row = next(
                (row for row in scan_data if _scan_row_matches_holding(row, holding)),
                None,
            )
            summary_row.update(
                _build_mark_to_market_fields(
                    holding,
                    api_match.get("close_quote") or api_match.get("close"),
                    close_quote_currency=api_match.get("close_quote_currency")
                    or (
                        _resolve_close_quote_currency(
                            currency=matched_row.get("currency") if matched_row else None,
                            exchange=matched_row.get("exchange") if matched_row else None,
                            country=matched_row.get("country") if matched_row else None,
                        )
                        if matched_row is not None
                        else None
                    ),
                    close_source="api_scan_data.close",
                    mtm_context=mtm_context,
                )
            )
            for perf_field in perf_fields or RAW_PERF_FIELDS:
                if perf_field != "close" and summary_row.get(perf_field) is None:
                    summary_row[perf_field] = api_match.get(perf_field)

    file_map = {
        "summary": output_scan_dir / "holdings__summary.csv",
        "profile_horizons": output_scan_dir / "holdings__profile_horizons.csv",
        "performance_tracking": output_scan_dir / "holdings__performance_tracking.csv",
        "conviction": output_scan_dir / "holdings__conviction.csv",
        "raw_perf": output_scan_dir / "holdings__raw_perf.csv",
        "unmatched": output_scan_dir / "holdings__unmatched.csv",
    }
    row_map = {
        "summary": summary_rows,
        "profile_horizons": profile_rows,
        "performance_tracking": performance_rows,
        "conviction": conviction_rows,
        "raw_perf": raw_perf_rows,
        "unmatched": unmatched_rows,
    }
    for key, path in file_map.items():
        rows = row_map[key]
        exported_files[key] = str(_write_csv(path, rows))
        counts[key] = len(rows)

    return ScanExportResult(
        scan_key="move_prediction_v1",
        source_run_id=source.run_id,
        source_database_path=str(source.database_path),
        exported_files=exported_files,
        matched_count=len(matched),
        unmatched_tickers=list(unmatched_tickers),
        counts=counts,
    )


SCAN_EXPORTERS: dict[str, ScanExporter] = {
    "move_prediction_v1": export_move_prediction_v1_scan,
}


def _build_mark_to_market_overview_lines(
    summary_rows: Sequence[dict[str, Any]],
    portfolio_currency: str,
) -> list[str]:
    priced_rows = [
        row
        for row in summary_rows
        if isinstance(row.get("close"), (int, float))
    ]
    total_current_value_usd = sum(
        float(row.get("current_value_usd"))
        for row in summary_rows
        if isinstance(row.get("current_value_usd"), (int, float))
    )
    total_unrealized_pnl_usd = sum(
        float(row.get("unrealized_pnl_usd"))
        for row in summary_rows
        if isinstance(row.get("unrealized_pnl_usd"), (int, float))
    )
    lines = [
        "Mark-to-market (run close price)",
        f"  price_source: raw_scan_rows.close (fallback profile_horizon_scores.close)",
        f"  holdings_with_close: {len(priced_rows)}/{len(summary_rows)}",
        f"  total_current_value_{portfolio_currency.lower()}: "
        f"{total_current_value_usd:,.2f}"
        if total_current_value_usd > 0
        else f"  total_current_value_{portfolio_currency.lower()}: N/A",
        f"  total_unrealized_pnl_{portfolio_currency.lower()}: "
        f"{total_unrealized_pnl_usd:,.2f}"
        if summary_rows
        and any(
            isinstance(row.get("unrealized_pnl_usd"), (int, float))
            for row in summary_rows
        )
        else f"  total_unrealized_pnl_{portfolio_currency.lower()}: N/A",
        "",
    ]
    for row in summary_rows:
        if row.get("close") is None:
            continue
        ticker = row.get("config_ticker") or row.get("matched_symbol")
        current_value_usd = row.get("current_value_usd")
        unrealized_return_pct = row.get("unrealized_return_pct")
        return_text = (
            f"{float(unrealized_return_pct):+.2f}%"
            if isinstance(unrealized_return_pct, (int, float))
            else "N/A"
        )
        value_text = (
            f"{float(current_value_usd):,.2f}"
            if isinstance(current_value_usd, (int, float))
            else "N/A"
        )
        lines.append(
            f"  {ticker}: close={row.get('close')} "
            f"({row.get('close_quote_currency') or row.get('currency') or 'n/a'}) "
            f"close_usd={row.get('close_usd')} "
            f"current_value_{portfolio_currency.lower()}={value_text} "
            f"return={return_text} "
            f"via={row.get('close_source') or 'n/a'}"
        )
    if len(lines) > 6:
        lines.append("")
    return lines


def _coerce_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_cash_position_summary(
    cash_position: CashPositionConfig | Mapping[str, Any] | None,
    *,
    portfolio_currency: str,
) -> str | None:
    if cash_position is None:
        return None
    if isinstance(cash_position, CashPositionConfig):
        value = cash_position.value
        currency = cash_position.currency
        value_in_portfolio = cash_position.value_in_portfolio_currency
    else:
        value = _coerce_optional_float(cash_position.get("value"))
        if value is None:
            return None
        currency = str(cash_position.get("currency") or portfolio_currency)
        value_in_portfolio = _coerce_optional_float(
            cash_position.get("value_in_portfolio_currency")
        )
    line = f"{value:,.2f} {currency}"
    if (
        value_in_portfolio is not None
        and normalize_currency_code(currency) != normalize_currency_code(portfolio_currency)
    ):
        line += (
            f" ({value_in_portfolio:,.2f} "
            f"{normalize_currency_code(portfolio_currency).lower()})"
        )
    return line


def _format_shortlist_usd(amount: float | None) -> str:
    if amount is None:
        return "n/a"
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.0f}"


def _format_shortlist_score(value: float | None, *, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{decimals}f}"


def _format_shortlist_pct(
    value: float | None,
    *,
    signed: bool = True,
    decimals: int = 1,
) -> str:
    if value is None:
        return "n/a"
    if signed:
        return f"{value:+.{decimals}f}%"
    return f"{value:.{decimals}f}%"


def _truncate_label(text: str | None, max_len: int) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= max_len:
        return cleaned
    if max_len <= 1:
        return cleaned[:max_len]
    return cleaned[: max_len - 1] + "…"


def _load_export_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def _shortlist_sort_key(row: dict[str, Any]) -> tuple[float, str]:
    current_value = _coerce_optional_float(row.get("current_value_usd"))
    invested = _coerce_optional_float(row.get("invested_sum_usd"))
    ticker = str(row.get("config_ticker") or row.get("matched_symbol") or "")
    return (-(current_value or invested or 0.0), ticker)


def _merge_shortlist_scoring_fields(
    summary_row: dict[str, Any],
    conviction_row: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(summary_row)
    if conviction_row:
        for key in (
            "manager_action_signal",
            "days_ras",
            "weeks_ras",
            "months_ras",
            "years_ras",
            "agreement_ratio",
            "exclusion_reason",
            "tape_pass",
            "sleeve",
        ):
            if conviction_row.get(key) not in (None, ""):
                merged[key] = conviction_row.get(key)
        if merged.get("conviction_sleeve") in (None, "") and conviction_row.get("sleeve"):
            merged["conviction_sleeve"] = conviction_row.get("sleeve")
    merged["model_sleeve"] = (
        merged.get("conviction_sleeve")
        or (conviction_row or {}).get("sleeve")
        or ""
    )
    merged["manager_action_signal"] = merged.get("manager_action_signal") or "neutral_watch"
    return merged


def _build_shortlist_portfolio_stats(
    rows: Sequence[dict[str, Any]],
    portfolio_currency: str,
) -> dict[str, Any]:
    total_invested = sum(
        float(row["invested_sum_usd"])
        for row in rows
        if _coerce_optional_float(row.get("invested_sum_usd")) is not None
    )
    total_current = sum(
        float(row["current_value_usd"])
        for row in rows
        if _coerce_optional_float(row.get("current_value_usd")) is not None
    )
    total_pnl = sum(
        float(row["unrealized_pnl_usd"])
        for row in rows
        if _coerce_optional_float(row.get("unrealized_pnl_usd")) is not None
    )
    portfolio_return_pct = (
        (total_pnl / total_invested) * 100.0 if total_invested > 0 else None
    )
    priced_count = sum(
        1 for row in rows if _coerce_optional_float(row.get("current_value_usd")) is not None
    )
    conviction_count = sum(
        1
        for row in rows
        if (_coerce_optional_float(row.get("conviction_score")) or 0.0) > 0
    )
    winners = [
        row
        for row in rows
        if (_coerce_optional_float(row.get("unrealized_return_pct")) or 0.0) > 0
    ]
    losers = [
        row
        for row in rows
        if (_coerce_optional_float(row.get("unrealized_return_pct")) or 0.0) < 0
    ]
    return {
        "portfolio_currency": portfolio_currency,
        "holdings_count": len(rows),
        "priced_count": priced_count,
        "total_invested": total_invested or None,
        "total_current": total_current or None,
        "total_pnl": total_pnl if rows else None,
        "portfolio_return_pct": portfolio_return_pct,
        "conviction_count": conviction_count,
        "winner_count": len(winners),
        "loser_count": len(losers),
    }


def _build_shortlist_position_rows(
    summary_rows: Sequence[dict[str, Any]],
    conviction_by_ticker: Mapping[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    conviction_lookup = conviction_by_ticker or {}
    total_invested = sum(
        float(row["invested_sum_usd"])
        for row in summary_rows
        if _coerce_optional_float(row.get("invested_sum_usd")) is not None
    )
    total_current = sum(
        float(row["current_value_usd"])
        for row in summary_rows
        if _coerce_optional_float(row.get("current_value_usd")) is not None
    )

    position_rows: list[dict[str, Any]] = []
    for summary_row in summary_rows:
        ticker = str(summary_row.get("config_ticker") or summary_row.get("matched_symbol") or "")
        conviction_row = conviction_lookup.get(ticker)
        row = _merge_shortlist_scoring_fields(summary_row, conviction_row)

        invested_usd = _coerce_optional_float(row.get("invested_sum_usd"))
        current_value_usd = _coerce_optional_float(row.get("current_value_usd"))
        row["invested_weight_pct"] = (
            (invested_usd / total_invested) * 100.0
            if invested_usd is not None and total_invested > 0
            else _coerce_optional_float(row.get("portfolio_weight_pct"))
        )
        row["current_weight_pct"] = (
            (current_value_usd / total_current) * 100.0
            if current_value_usd is not None and total_current > 0
            else None
        )
        position_rows.append(row)

    position_rows.sort(key=_shortlist_sort_key)
    return position_rows


def _build_shortlist_lines(
    *,
    run_id: str,
    holdings_payload: dict[str, Any],
    summary_rows: Sequence[dict[str, Any]],
    conviction_by_ticker: Mapping[str, dict[str, Any]] | None = None,
    source: MovePredictionSource | None = None,
    generated_at: datetime | None = None,
) -> list[str]:
    portfolio_currency = str(
        holdings_payload.get("_resolved_portfolio_currency")
        or holdings_payload.get("portfolio_currency")
        or "USD"
    )
    generated = generated_at or datetime.now(timezone.utc)
    position_rows = _build_shortlist_position_rows(summary_rows, conviction_by_ticker)
    stats = _build_shortlist_portfolio_stats(position_rows, portfolio_currency)

    lines = [
        "=" * 96,
        " HOLDINGS SHORTLIST",
        "=" * 96,
        f"Run ID     : {run_id}",
        f"Generated  : {generated.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Book       : {holdings_payload.get('holdings_id', 'default')}",
    ]
    if holdings_payload.get("description"):
        lines.append(f"Notes      : {holdings_payload['description']}")
    cash_summary = _format_cash_position_summary(
        holdings_payload.get("_resolved_cash_position"),
        portfolio_currency=portfolio_currency,
    )
    if cash_summary:
        lines.append(f"Cash       : {cash_summary}")
    if source is not None:
        lines.append(f"Scan       : {source.run_id}")
    lines.append("")

    lines.extend(
        [
            "PORTFOLIO AT A GLANCE",
            "-" * 96,
            f"  Positions        : {stats['holdings_count']} "
            f"({stats['priced_count']} priced from scan close)",
            f"  Total invested   : {_format_shortlist_usd(stats['total_invested'])}",
            f"  Current value    : {_format_shortlist_usd(stats['total_current'])}",
            f"  Unrealized P&L   : {_format_shortlist_usd(stats['total_pnl'])} "
            f"({_format_shortlist_pct(stats['portfolio_return_pct'])})",
            f"  Winners / losers : {stats['winner_count']} / {stats['loser_count']}",
            f"  With conviction  : {stats['conviction_count']} of {stats['holdings_count']}",
            "",
        ]
    )

    header = (
        f"{'#':>2}  {'Ticker':<8} {'Company':<22} "
        f"{'Inv%':>5} {'Val%':>5} {'Invested':>10} {'Value':>10} "
        f"{'P&L':>10} {'vsCost':>7} {'YTD':>7} {'1M':>7} "
        f"{'Conv':>5} {'Signal':<18} {'Mdl':>5}"
    )
    lines.extend(
        [
            "POSITIONS (largest current value first)",
            "-" * 96,
            header,
            "-" * 96,
        ]
    )

    for rank, row in enumerate(position_rows, 1):
        ticker = str(row.get("config_ticker") or row.get("matched_symbol") or "")
        company = _truncate_label(row.get("company"), 22)
        conviction = _coerce_optional_float(row.get("conviction_score"))
        conviction_text = f"{conviction:.2f}" if conviction is not None else "—"
        signal = _truncate_label(
            str(row.get("manager_action_signal") or "neutral_watch"),
            18,
        )
        model_sleeve = _truncate_label(str(row.get("model_sleeve") or "—"), 5)
        lines.append(
            f"{rank:>2}  {ticker:<8} {company:<22} "
            f"{_format_shortlist_pct(row.get('invested_weight_pct'), signed=False, decimals=1):>5} "
            f"{_format_shortlist_pct(row.get('current_weight_pct'), signed=False, decimals=1):>5} "
            f"{_format_shortlist_usd(_coerce_optional_float(row.get('invested_sum_usd'))):>10} "
            f"{_format_shortlist_usd(_coerce_optional_float(row.get('current_value_usd'))):>10} "
            f"{_format_shortlist_usd(_coerce_optional_float(row.get('unrealized_pnl_usd'))):>10} "
            f"{_format_shortlist_pct(_coerce_optional_float(row.get('unrealized_return_pct'))):>7} "
            f"{_format_shortlist_pct(_coerce_optional_float(row.get('Perf.YTD'))):>7} "
            f"{_format_shortlist_pct(_coerce_optional_float(row.get('Perf.1M'))):>7} "
            f"{conviction_text:>5} {signal:<18} {model_sleeve:>5}"
        )

    lines.append("")

    # Scoring highlights
    actionable = [
        row
        for row in position_rows
        if str(row.get("manager_action_signal") or "neutral_watch") != "neutral_watch"
    ]
    conviction_rows = [
        row
        for row in position_rows
        if (_coerce_optional_float(row.get("conviction_score")) or 0.0) > 0
    ]
    conviction_rows.sort(
        key=lambda row: _coerce_optional_float(row.get("conviction_score")) or 0.0,
        reverse=True,
    )

    lines.extend(["SCORING HIGHLIGHTS", "-" * 96])
    if actionable:
        lines.append("  Action signals:")
        for row in actionable:
            ticker = row.get("config_ticker") or row.get("matched_symbol")
            weeks_ras = _coerce_optional_float(row.get("weeks_ras"))
            conviction = _coerce_optional_float(row.get("conviction_score")) or 0.0
            lines.append(
                f"    {ticker}: {row.get('manager_action_signal')} "
                f"(conviction {conviction:.2f}, "
                f"weeks RAS {_format_shortlist_score(weeks_ras)}, "
                f"model {row.get('model_sleeve') or 'n/a'})"
            )
    else:
        lines.append("  Action signals: none (all neutral_watch)")

    if conviction_rows:
        lines.append("  Highest conviction:")
        for row in conviction_rows[:8]:
            ticker = row.get("config_ticker") or row.get("matched_symbol")
            lines.append(
                f"    {ticker}: {_coerce_optional_float(row.get('conviction_score')):.2f} "
                f"| {_format_shortlist_score(_coerce_optional_float(row.get('weeks_ras')))} weeks RAS "
                f"| config sleeve {row.get('sleeve') or 'n/a'} "
                f"| model {row.get('model_sleeve') or 'n/a'}"
            )
    else:
        lines.append("  Highest conviction: none above zero")

    lines.append("")

    def _extreme_line(label: str, rows: Sequence[dict[str, Any]], reverse: bool) -> None:
        ranked = [
            row
            for row in rows
            if _coerce_optional_float(row.get("unrealized_return_pct")) is not None
        ]
        ranked.sort(
            key=lambda row: _coerce_optional_float(row.get("unrealized_return_pct")) or 0.0,
            reverse=reverse,
        )
        lines.append(f"  {label}:")
        if not ranked:
            lines.append("    n/a")
            return
        for row in ranked[:5]:
            ticker = row.get("config_ticker") or row.get("matched_symbol")
            ret = _coerce_optional_float(row.get("unrealized_return_pct"))
            ytd = _coerce_optional_float(row.get("Perf.YTD"))
            lines.append(
                f"    {ticker}: vs cost {_format_shortlist_pct(ret)}, "
                f"YTD {_format_shortlist_pct(ytd)}"
            )

    lines.extend(["PERFORMANCE EXTREMES", "-" * 96])
    _extreme_line("Best vs your cost basis", position_rows, reverse=True)
    _extreme_line("Worst vs your cost basis", position_rows, reverse=False)
    lines.append("")

    ytd_ranked = [
        row for row in position_rows if _coerce_optional_float(row.get("Perf.YTD")) is not None
    ]
    ytd_ranked.sort(
        key=lambda row: _coerce_optional_float(row.get("Perf.YTD")) or 0.0,
        reverse=True,
    )
    lines.extend(["TAPE PERFORMANCE (YTD)", "-" * 96])
    if ytd_ranked:
        lines.append(
            "  Leaders: "
            + ", ".join(
                f"{row.get('config_ticker')} "
                f"{_format_shortlist_pct(_coerce_optional_float(row.get('Perf.YTD')))}"
                for row in ytd_ranked[:5]
            )
        )
        lines.append(
            "  Laggards: "
            + ", ".join(
                f"{row.get('config_ticker')} "
                f"{_format_shortlist_pct(_coerce_optional_float(row.get('Perf.YTD')))}"
                for row in ytd_ranked[-5:][::-1]
            )
        )
    else:
        lines.append("  n/a")
    lines.append("")

    weight_drift = [
        row
        for row in position_rows
        if _coerce_optional_float(row.get("invested_weight_pct")) is not None
        and _coerce_optional_float(row.get("current_weight_pct")) is not None
    ]
    weight_drift.sort(
        key=lambda row: abs(
            (_coerce_optional_float(row.get("current_weight_pct")) or 0.0)
            - (_coerce_optional_float(row.get("invested_weight_pct")) or 0.0)
        ),
        reverse=True,
    )
    lines.extend(["WEIGHT DRIFT (current % minus invested %)", "-" * 96])
    if weight_drift:
        for row in weight_drift[:6]:
            ticker = row.get("config_ticker") or row.get("matched_symbol")
            invested_wt = _coerce_optional_float(row.get("invested_weight_pct"))
            current_wt = _coerce_optional_float(row.get("current_weight_pct"))
            drift = (current_wt or 0.0) - (invested_wt or 0.0)
            lines.append(
                f"  {ticker}: invested {_format_shortlist_pct(invested_wt, signed=False)} "
                f"-> current {_format_shortlist_pct(current_wt, signed=False)} "
                f"({drift:+.1f} pp)"
            )
    else:
        lines.append("  n/a")
    lines.append("")

    lines.extend(
        [
            "LEGEND",
            "-" * 96,
            "  Inv% / Val%  : share of total invested / current portfolio value",
            "  vsCost       : unrealized return on your average price",
            "  YTD / 1M     : TradingView scan performance fields",
            "  Conv         : move-prediction conviction score (0 = filtered out)",
            "  Signal       : manager action from conviction engine",
            "  Mdl          : model-assigned sleeve (long/short), independent of config sleeve",
            "=" * 96,
        ]
    )
    return lines


def _write_shortlist_log(
    path: Path,
    *,
    run_id: str,
    holdings_payload: dict[str, Any],
    summary_rows: Sequence[dict[str, Any]],
    conviction_by_ticker: Mapping[str, dict[str, Any]] | None = None,
    source: MovePredictionSource | None = None,
    generated_at: datetime | None = None,
) -> None:
    lines = _build_shortlist_lines(
        run_id=run_id,
        holdings_payload=holdings_payload,
        summary_rows=summary_rows,
        conviction_by_ticker=conviction_by_ticker,
        source=source,
        generated_at=generated_at,
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_sleeve_rollups(summary_rows: Sequence[dict[str, Any]]) -> list[str]:
    sleeves: dict[str, dict[str, Any]] = {}
    for row in summary_rows:
        sleeve = str(row.get("sleeve") or "").strip() or "(unsleeved)"
        bucket = sleeves.setdefault(
            sleeve,
            {
                "ras_values": [],
                "invested_usd": 0.0,
                "current_value_usd": 0.0,
                "weighted_ras_numerator": 0.0,
            },
        )
        ras = row.get("consensus_weeks_ras")
        invested_usd = row.get("invested_sum_usd")
        current_value_usd = row.get("current_value_usd")
        if isinstance(ras, (int, float)):
            bucket["ras_values"].append(float(ras))
            if isinstance(invested_usd, (int, float)) and invested_usd > 0:
                bucket["weighted_ras_numerator"] += float(ras) * float(invested_usd)
        if isinstance(invested_usd, (int, float)):
            bucket["invested_usd"] += float(invested_usd)
        if isinstance(current_value_usd, (int, float)):
            bucket["current_value_usd"] += float(current_value_usd)

    lines: list[str] = []
    for sleeve, bucket in sorted(sleeves.items()):
        values = bucket["ras_values"]
        avg_ras = sum(values) / len(values) if values else None
        invested_usd = bucket["invested_usd"]
        current_value_usd = bucket["current_value_usd"]
        weighted_ras = (
            bucket["weighted_ras_numerator"] / invested_usd
            if invested_usd > 0 and bucket["weighted_ras_numerator"] > 0
            else None
        )
        parts = [f"count={len(values)}"]
        if invested_usd > 0:
            parts.append(f"invested_usd={invested_usd:,.2f}")
        if current_value_usd > 0:
            parts.append(f"current_value_usd={current_value_usd:,.2f}")
        if avg_ras is not None:
            parts.append(f"avg_weeks_ras={avg_ras:.4f}")
        if weighted_ras is not None:
            parts.append(f"weight_weighted_weeks_ras={weighted_ras:.4f}")
        lines.append(f"  {sleeve}: " + " ".join(parts))
    return lines


def _write_overview_log(
    path: Path,
    *,
    run_id: str,
    holdings_payload: dict[str, Any],
    holdings_count: int,
    source: MovePredictionSource | None,
    scan_results: Sequence[ScanExportResult],
    summary_rows: Sequence[dict[str, Any]],
    exported_manifest_path: str,
    fx_warnings: Sequence[str] | None = None,
) -> None:
    portfolio_currency = holdings_payload.get(
        "_resolved_portfolio_currency",
        holdings_payload.get("portfolio_currency", "USD"),
    )
    total_invested_usd = sum(
        float(row.get("invested_sum_usd"))
        for row in summary_rows
        if isinstance(row.get("invested_sum_usd"), (int, float))
    )
    lines = [
        "Holdings Scoring Analysis",
        f"run_id: {run_id}",
        f"generated_at_utc: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Holdings config",
        f"  holdings_id: {holdings_payload.get('holdings_id', '')}",
        f"  description: {holdings_payload.get('description', '')}",
        f"  portfolio_currency: {portfolio_currency}",
        f"  holdings_count: {holdings_count}",
        f"  total_invested_usd: {total_invested_usd:,.2f}"
        if total_invested_usd > 0
        else "  total_invested_usd: N/A",
    ]
    cash_summary = _format_cash_position_summary(
        holdings_payload.get("_resolved_cash_position"),
        portfolio_currency=str(portfolio_currency),
    )
    if cash_summary:
        lines.append(f"  cash_position: {cash_summary}")
    lines.extend(
        [
        "  fx_provider: frankfurter.app (ECB rates, free, no API key)",
        "",
        ]
    )
    if fx_warnings:
        lines.append("FX warnings")
        for warning in fx_warnings:
            lines.append(f"  - {warning}")
        lines.append("")

    lines.extend(_build_mark_to_market_overview_lines(summary_rows, portfolio_currency))

    if source is not None:
        lines.extend(
            [
                "Move-prediction source",
                f"  database_path: {source.database_path}",
                f"  run_id: {source.run_id}",
                f"  created_at_utc: {source.created_at_utc}",
                f"  suite_name: {source.suite_name or ''}",
                f"  profile_count: {len(source.profile_names)}",
                "",
            ]
        )

    total_matched = sum(result.matched_count for result in scan_results)
    all_unmatched = sorted(
        {ticker for result in scan_results for ticker in result.unmatched_tickers}
    )
    lines.extend(
        [
            "Match stats",
            f"  matched_holdings: {total_matched}",
            f"  unmatched_holdings: {len(all_unmatched)}",
            "",
        ]
    )
    if all_unmatched:
        lines.append("Unmatched tickers:")
        for ticker in all_unmatched:
            lines.append(f"  - {ticker}")
        lines.append("")

    lines.append("Horizon performance tracking periods:")
    for horizon_name, perf_fields in DEFAULT_PERFORMANCE_TRACKING_PERIODS.items():
        lines.append(f"  {horizon_name}: {', '.join(perf_fields)}")
    lines.append("")

    sleeve_lines = _build_sleeve_rollups(summary_rows)
    if sleeve_lines:
        lines.append("Sleeve rollups (consensus weeks RAS):")
        lines.extend(sleeve_lines)
        lines.append("")

    lines.append("Exported scans:")
    for result in scan_results:
        lines.append(f"  {result.scan_key}: matched={result.matched_count}")
        for key, count in sorted(result.counts.items()):
            lines.append(f"    {key}: {count} rows")
        for key, file_path in sorted(result.exported_files.items()):
            lines.append(f"    {key}: {file_path}")
    lines.append("")
    lines.append(f"manifest: {exported_manifest_path}")
    lines.append("shortlist: see holdings_scoring__shortlist.log in this run folder")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_holdings_scoring_analysis(
    holdings_config_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    database_path: str | Path | None = None,
    run_id: str | None = None,
    duckdb_runs_root: str | Path | None = None,
    enabled_scans: Sequence[str] = ("move_prediction_v1",),
    scan_data: list[dict[str, Any]] | None = None,
    run_label: str | None = None,
    reference_time: datetime | None = None,
    fx_as_of_date: date | None = None,
    resolve_fx_rates: bool = True,
) -> dict[str, Any]:
    """Run holdings scoring exports for the configured current holdings."""
    resolved_config_path = Path(holdings_config_path)
    holdings_payload, holdings, cash_position = load_holdings_config(resolved_config_path)
    portfolio_currency = str(
        holdings_payload.get("_resolved_portfolio_currency") or "USD"
    )

    created_at = reference_time or datetime.now(timezone.utc)
    as_of_date = fx_as_of_date or created_at.date()
    fx_warnings: list[str] = []
    holdings, enrich_warnings = enrich_holdings_positions(
        holdings,
        portfolio_currency=portfolio_currency,
        as_of_date=as_of_date,
        allow_external_fx=resolve_fx_rates,
    )
    fx_warnings.extend(enrich_warnings)
    cash_position = enrich_cash_position(
        cash_position,
        portfolio_currency=portfolio_currency,
        as_of_date=as_of_date,
        allow_external_fx=resolve_fx_rates,
        fx_warnings=fx_warnings,
    )
    if cash_position is not None:
        holdings_payload["_resolved_cash_position"] = cash_position.to_dict()
    mtm_context = MarkToMarketContext(
        portfolio_currency=portfolio_currency,
        as_of_date=as_of_date,
        resolve_fx_rates=resolve_fx_rates,
        fx_warnings=fx_warnings,
    )

    unknown_scans = [scan for scan in enabled_scans if scan not in SCAN_EXPORTERS]
    if unknown_scans:
        raise ValueError(
            f"Unknown enabled_scans: {unknown_scans}. Available: {sorted(SCAN_EXPORTERS)}"
        )

    holdings_run_id = _build_holdings_run_id(run_label, created_at)
    resolved_output = Path(output_dir) if output_dir is not None else (
        HOLDINGS_SCORING_LOG_DIR / "runs" / holdings_run_id
    )
    resolved_output.mkdir(parents=True, exist_ok=True)
    scans_root = resolved_output / "scans"

    source: MovePredictionSource | None = None
    matched: list[MatchedHolding] = []
    unmatched: list[str] = [holding.ticker for holding in holdings]

    if "move_prediction_v1" in enabled_scans:
        source = resolve_latest_move_prediction_source(
            duckdb_runs_root=duckdb_runs_root,
            database_path=database_path,
            run_id=run_id,
        )
        db_symbols = _fetch_distinct_symbols(
            source.database_path,
            source.run_id,
            "consensus_horizon_scores",
        )
        raw_catalog = _fetch_raw_scan_symbol_catalog(
            source.database_path,
            source.run_id,
        )
        matched, unmatched = match_holdings_to_db_symbols(
            holdings,
            db_symbols,
            raw_catalog=raw_catalog,
        )

    scan_results: list[ScanExportResult] = []
    for scan_key in enabled_scans:
        exporter = SCAN_EXPORTERS[scan_key]
        if scan_key == "move_prediction_v1":
            if source is None:
                raise RuntimeError("move_prediction_v1 source was not resolved")
            result = exporter(
                source,
                holdings,
                matched,
                unmatched,
                scans_root / scan_key,
                scan_data,
                mtm_context,
            )
        else:
            raise RuntimeError(f"Scan exporter {scan_key!r} is not wired yet")
        scan_results.append(result)

    summary_rows: list[dict[str, Any]] = []
    conviction_rows: list[dict[str, Any]] = []
    move_prediction_result = next(
        (result for result in scan_results if result.scan_key == "move_prediction_v1"),
        None,
    )
    if move_prediction_result:
        if "summary" in move_prediction_result.exported_files:
            summary_rows = _load_export_csv_rows(
                Path(move_prediction_result.exported_files["summary"])
            )
        if "conviction" in move_prediction_result.exported_files:
            conviction_rows = _load_export_csv_rows(
                Path(move_prediction_result.exported_files["conviction"])
            )
    conviction_by_ticker = {
        str(row.get("config_ticker") or ""): row
        for row in conviction_rows
        if row.get("config_ticker")
    }

    manifest = {
        "run_id": holdings_run_id,
        "created_at_utc": created_at.astimezone(timezone.utc).isoformat(),
        "holdings_config_path": str(resolved_config_path),
        "holdings_id": holdings_payload.get("holdings_id"),
        "holdings_count": len(holdings),
        "portfolio_currency": portfolio_currency,
        "cash_position": (
            cash_position.to_dict() if cash_position is not None else None
        ),
        "fx_as_of_date": as_of_date.isoformat(),
        "fx_provider": "frankfurter.app",
        "resolve_fx_rates": resolve_fx_rates,
        "fx_warnings": fx_warnings,
        "enabled_scans": list(enabled_scans),
        "performance_tracking_periods": DEFAULT_PERFORMANCE_TRACKING_PERIODS,
        "performance_tracking_field_order": PERFORMANCE_TRACKING_FIELD_ORDER,
        "code_version": _collect_code_version_metadata(),
        "scans": [
            {
                "scan_key": result.scan_key,
                "source_run_id": result.source_run_id,
                "source_database_path": result.source_database_path,
                "matched_count": result.matched_count,
                "unmatched_tickers": result.unmatched_tickers,
                "counts": result.counts,
                "exported_files": result.exported_files,
            }
            for result in scan_results
        ],
    }
    manifest_path = resolved_output / "holdings_scoring__manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    overview_path = resolved_output / "holdings_scoring__overview.log"
    _write_overview_log(
        overview_path,
        run_id=holdings_run_id,
        holdings_payload=holdings_payload,
        holdings_count=len(holdings),
        source=source,
        scan_results=scan_results,
        summary_rows=summary_rows,
        exported_manifest_path=str(manifest_path),
        fx_warnings=fx_warnings,
    )

    shortlist_path = resolved_output / "holdings_scoring__shortlist.log"
    _write_shortlist_log(
        shortlist_path,
        run_id=holdings_run_id,
        holdings_payload=holdings_payload,
        summary_rows=summary_rows,
        conviction_by_ticker=conviction_by_ticker,
        source=source,
        generated_at=created_at,
    )

    return {
        "run_id": holdings_run_id,
        "output_dir": str(resolved_output),
        "manifest_path": str(manifest_path),
        "overview_log": str(overview_path),
        "shortlist_log": str(shortlist_path),
        "holdings_config_path": str(resolved_config_path),
        "holdings_id": holdings_payload.get("holdings_id"),
        "portfolio_currency": portfolio_currency,
        "cash_position": (
            cash_position.to_dict() if cash_position is not None else None
        ),
        "fx_warnings": fx_warnings,
        "source": {
            "database_path": str(source.database_path),
            "run_id": source.run_id,
            "created_at_utc": source.created_at_utc,
            "suite_name": source.suite_name,
            "profile_names": source.profile_names,
        }
        if source
        else None,
        "matched_count": len(matched),
        "unmatched_tickers": unmatched,
        "scan_results": [asdict(result) for result in scan_results],
    }
