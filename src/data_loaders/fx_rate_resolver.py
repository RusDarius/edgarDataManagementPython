"""Resolve FX rates for holdings position normalization.

Uses the free Frankfurter API (ECB reference rates, no API key):
https://api.frankfurter.app/docs/

Rates are typically available for the latest ECB business day when
``as_of_date`` is omitted or is today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import requests

FRANKFURTER_API_BASE = "https://api.frankfurter.app"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class FxRateQuote:
    from_currency: str
    to_currency: str
    rate: float
    rate_date: str
    provider: str = "frankfurter"


def normalize_currency_code(currency: str | None, *, default: str = "USD") -> str:
    normalized = str(currency or default).strip().upper()
    if not normalized:
        return default.upper()
    return normalized


def _parse_rate_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _request_frankfurter_rate(
    from_currency: str,
    to_currency: str,
    *,
    as_of_date: date | None = None,
) -> FxRateQuote:
    if as_of_date is None:
        url = f"{FRANKFURTER_API_BASE}/latest"
        params = {"from": from_currency, "to": to_currency}
    else:
        url = f"{FRANKFURTER_API_BASE}/{as_of_date.isoformat()}"
        params = {"from": from_currency, "to": to_currency}

    response = requests.get(url, params=params, timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    rates = payload.get("rates") or {}
    if to_currency not in rates:
        raise ValueError(
            f"Frankfurter response missing {to_currency} rate for {from_currency}: {payload}"
        )
    return FxRateQuote(
        from_currency=from_currency,
        to_currency=to_currency,
        rate=float(rates[to_currency]),
        rate_date=_parse_rate_date(payload.get("date")),
        provider="frankfurter",
    )


def get_fx_rate(
    from_currency: str,
    to_currency: str = "USD",
    *,
    as_of_date: date | None = None,
    rate_cache: dict[tuple[str, str, str | None], FxRateQuote] | None = None,
) -> FxRateQuote:
    """Return ``to_currency`` per 1 unit of ``from_currency``."""
    source = normalize_currency_code(from_currency)
    target = normalize_currency_code(to_currency)
    if source == target:
        rate_day = _parse_rate_date(as_of_date or datetime.now(timezone.utc).date())
        return FxRateQuote(
            from_currency=source,
            to_currency=target,
            rate=1.0,
            rate_date=rate_day,
            provider="identity",
        )

    cache_key = (source, target, as_of_date.isoformat() if as_of_date else None)
    if rate_cache is not None and cache_key in rate_cache:
        return rate_cache[cache_key]

    quote = _request_frankfurter_rate(source, target, as_of_date=as_of_date)
    if rate_cache is not None:
        rate_cache[cache_key] = quote
    return quote


def convert_amount(
    amount: float,
    from_currency: str,
    to_currency: str = "USD",
    *,
    as_of_date: date | None = None,
    rate_cache: dict[tuple[str, str, str | None], FxRateQuote] | None = None,
) -> tuple[float, FxRateQuote]:
    quote = get_fx_rate(
        from_currency,
        to_currency,
        as_of_date=as_of_date,
        rate_cache=rate_cache,
    )
    return amount * quote.rate, quote
