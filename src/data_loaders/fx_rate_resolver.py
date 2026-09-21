"""Resolve FX rates for holdings position normalization.

Uses the free Frankfurter API (no API key):
https://api.frankfurter.dev/

v1 keeps the ECB-style ``{rates: {USD: ...}}`` payload. The old
``api.frankfurter.app`` host 301s to ``.dev`` and can hang on the redirect.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import requests

FRANKFURTER_API_BASE = "https://api.frankfurter.dev/v1"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0

_LOOKUP_FAILURES: dict[tuple[str, str, str | None], BaseException] = {}


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


def clear_fx_lookup_failures() -> None:
    """Drop cached FX lookup failures (used by tests and long-lived processes)."""
    _LOOKUP_FAILURES.clear()


def _parse_rate_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _quote_from_payload(
    payload: Mapping[str, Any],
    from_currency: str,
    to_currency: str,
) -> FxRateQuote:
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


def _frankfurter_candidates(
    from_currency: str,
    to_currency: str,
    *,
    as_of_date: date | None = None,
) -> list[tuple[str, dict[str, str]]]:
    params = {"from": from_currency, "to": to_currency}
    if as_of_date is None:
        return [(f"{FRANKFURTER_API_BASE}/latest", params)]
    return [
        (f"{FRANKFURTER_API_BASE}/{as_of_date.isoformat()}", params),
        (f"{FRANKFURTER_API_BASE}/latest", params),
    ]


def _request_frankfurter_rate(
    from_currency: str,
    to_currency: str,
    *,
    as_of_date: date | None = None,
) -> FxRateQuote:
    last_error: BaseException | None = None
    for url, params in _frankfurter_candidates(
        from_currency,
        to_currency,
        as_of_date=as_of_date,
    ):
        for attempt in range(1, DEFAULT_MAX_RETRIES + 1):
            try:
                response = requests.get(
                    url,
                    params=params,
                    timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                return _quote_from_payload(response.json(), from_currency, to_currency)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt < DEFAULT_MAX_RETRIES:
                    time.sleep(DEFAULT_RETRY_BACKOFF_SECONDS * attempt)
                    continue
            except Exception as exc:
                last_error = exc
                break
    if last_error is None:
        raise RuntimeError("Frankfurter FX lookup failed without an error")
    raise last_error


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
    cached_failure = _LOOKUP_FAILURES.get(cache_key)
    if cached_failure is not None:
        raise cached_failure

    try:
        quote = _request_frankfurter_rate(source, target, as_of_date=as_of_date)
    except Exception as exc:
        _LOOKUP_FAILURES[cache_key] = exc
        raise
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
