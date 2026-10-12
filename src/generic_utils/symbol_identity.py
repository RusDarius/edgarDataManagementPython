"""Exchange-aware symbol identity helpers.

Global matching contract:

* ``EXCHANGE:TICKER`` requests match that exact full symbol first.
* If an old source stores only ``TICKER``, that genuinely bare row may be used.
* A qualified request never falls through to a different qualified exchange.
* Bare caller requests may match qualified rows, but only a unique candidate
  should be auto-resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class SymbolIdentity:
    normalized: str
    exchange: str | None
    ticker: str

    @property
    def qualified(self) -> bool:
        return self.exchange is not None


def parse_symbol(value: Any) -> SymbolIdentity:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return SymbolIdentity("", None, "")
    if ":" not in normalized:
        return SymbolIdentity(normalized, None, normalized)
    exchange, ticker = normalized.split(":", 1)
    return SymbolIdentity(
        normalized,
        exchange or None,
        ticker,
    )


def bare_symbol(value: Any) -> str:
    return parse_symbol(value).ticker


def symbol_matches_request(stored: Any, requested: Any) -> bool:
    """Whether one stored id satisfies the global request contract."""
    stored_id = parse_symbol(stored)
    request_id = parse_symbol(requested)
    if not stored_id.normalized or not request_id.normalized:
        return False
    if stored_id.normalized == request_id.normalized:
        return True
    if stored_id.ticker != request_id.ticker:
        return False
    if request_id.qualified:
        return not stored_id.qualified
    return True


@dataclass(frozen=True)
class SymbolCatalog:
    """One pass over a symbol set so later resolves do not rescan it.

    ``by_normalized`` maps the normalized id to the first original key.
    ``ticker_keys`` maps a bare ticker to those original keys, in first-seen order.
    """

    by_normalized: dict[str, str]
    ticker_keys: dict[str, tuple[str, ...]]

    @staticmethod
    def build(symbols: Iterable[Any]) -> "SymbolCatalog":
        by_normalized: dict[str, str] = {}
        buckets: dict[str, list[str]] = {}
        for value in symbols:
            text = str(value or "").strip()
            identity = parse_symbol(text)
            if not identity.normalized or identity.normalized in by_normalized:
                continue
            by_normalized[identity.normalized] = text
            buckets.setdefault(identity.ticker, []).append(text)
        return SymbolCatalog(
            by_normalized,
            {ticker: tuple(keys) for ticker, keys in buckets.items()},
        )

    def resolve(self, requested: Any) -> tuple[str | None, str, tuple[str, ...]]:
        """Same contract as ``resolve_symbol``."""
        request_id = parse_symbol(requested)
        if not request_id.normalized:
            return None, "not-found", ()
        exact = self.by_normalized.get(request_id.normalized)
        if request_id.qualified and exact is not None:
            return exact, "exact", (exact,)
        matches = tuple(
            original
            for original in self.ticker_keys.get(request_id.ticker, ())
            if symbol_matches_request(original, request_id.normalized)
        )
        if len(matches) == 1:
            mode = "legacy-bare" if request_id.qualified else "unique-bare"
            return matches[0], mode, matches
        if not matches:
            return None, "not-found", ()
        return None, "ambiguous", tuple(sorted(matches))


def resolve_symbol(
    available_symbols: Iterable[Any],
    requested: Any,
) -> tuple[str | None, str, tuple[str, ...]]:
    """Resolve a request without silently crossing exchanges.

    Returns ``(symbol, mode, candidates)``. ``mode`` is one of ``exact``,
    ``legacy-bare``, ``unique-bare``, ``not-found``, or ``ambiguous``.

    One call builds a catalog and throws it away. A row loop over a list
    should build ``SymbolCatalog`` once and call ``resolve``. A row loop over
    a mapping should build one catalog and pass it to ``lookup_mapping``.
    A qualified ``EXCHANGE:TICKER`` that is already a key does not scan.
    """
    return SymbolCatalog.build(available_symbols).resolve(requested)


def lookup_mapping(
    index: Mapping[str, Any],
    requested: Any,
    catalog: SymbolCatalog | None = None,
) -> Any:
    """Row for ``requested`` from a symbol-keyed mapping.

    A qualified id that is already a key returns in one dict lookup and does
    not scan the other keys. A bare or legacy id uses ``catalog`` when the
    caller built one for the whole table; otherwise that one call scans the
    keys. Pass the same catalog through a row loop.

    The match contract is the same as ``resolve_symbol``: an
    ``EXCHANGE:TICKER`` request never falls through to a different exchange.
    """
    request_id = parse_symbol(requested)
    if not request_id.normalized:
        return None
    if request_id.qualified and request_id.normalized in index:
        return index[request_id.normalized]
    chosen = catalog if catalog is not None else SymbolCatalog.build(index.keys())
    matched, _, _ = chosen.resolve(requested)
    if matched is None:
        return None
    return index.get(matched)
