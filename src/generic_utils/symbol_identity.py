"""Exchange-aware symbol identity helpers.

Global matching contract:

* ``EXCHANGE:TICKER`` requests match that exact full symbol first.
* If an old source stores only ``TICKER``, that genuinely bare row may be used.
* A qualified request never falls through to a different qualified exchange.
* Bare caller requests may match qualified rows, but only a unique candidate
  should be auto-resolved.

Global output contract (every scan, table, md page, priors file, chat line):

* A stock is written as ``EXCHANGE:TICKER``. A bare ``ticker`` column may sit
  beside it for convenience; it is never the only id on a row.
* ``qualify`` turns a bare id into ``EXCHANGE:TICKER`` from catalogs the caller
  trusts (the day's pack, then the newest). An id that stays bare (not found or
  listed on two exchanges) is left bare so the caller can count it, never guessed.
* ``display_id`` is the one id an md / log line prints for a row.
"""

from __future__ import annotations

import re
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


def qualify(value: Any, *catalogs: SymbolCatalog | None) -> str:
    """``EXCHANGE:TICKER`` for an output row.

    A qualified value comes back normalized. A bare value takes the first
    catalog (caller's preference order) that lists that ticker exactly once.
    Not found or ambiguous: the bare value is returned unchanged.
    """
    identity = parse_symbol(value)
    if not identity.normalized or identity.qualified:
        return identity.normalized
    for catalog in catalogs:
        if catalog is None:
            continue
        matched, mode, _ = catalog.resolve(identity.normalized)
        if matched is not None and mode == "unique-bare":
            return parse_symbol(matched).normalized
    return identity.normalized


def display_id(row: Mapping[str, Any], *catalogs: SymbolCatalog | None) -> str:
    """The id an md / log line prints: a qualified ``symbol`` first, then any qualified id field, then ``qualify`` of the bare one."""
    fields = ("symbol", "matched_symbol", "config_symbol", "ticker", "config_ticker")
    for field in fields:
        identity = parse_symbol(row.get(field))
        if identity.qualified:
            return identity.normalized
    bare = next((str(row.get(f)) for f in fields if str(row.get(f) or "").strip()), "")
    return qualify(bare, *catalogs)


def qualify_rows(
    rows: Iterable[Mapping[str, Any]],
    *catalogs: SymbolCatalog | None,
    field: str = "symbol",
    ticker_field: str = "ticker",
) -> list[dict[str, Any]]:
    """Copy rows with ``field`` set to ``display_id`` and ``ticker_field`` set to the bare ticker."""
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        rec[field] = display_id(rec, *catalogs)
        rec[ticker_field] = parse_symbol(rec[field]).ticker
        out.append(rec)
    return out


_QUALIFIED_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]*:[A-Z0-9][A-Z0-9._]*")
_BARE_TOKEN = re.compile(r"(?<![A-Za-z0-9_.:])[A-Z][A-Z0-9]{0,5}(?:[._][A-Z0-9]{1,3})?(?![A-Za-z0-9_])")


def bare_tickers_in(text: Any) -> list[str]:
    """Ticker-like tokens in an id cell that are not EXCHANGE:TICKER ("NASDAQ:MU / FLEX" -> ["FLEX"])."""
    return _BARE_TOKEN.findall(_QUALIFIED_TOKEN.sub(" ", str(text or "")))


def ticker_map(symbols: Iterable[Any]) -> dict[str, str]:
    """{bare ticker: EXCHANGE:TICKER} for tickers listed once in ``symbols`` (a ticker on two exchanges is left out)."""
    seen: dict[str, set[str]] = {}
    for value in symbols:
        identity = parse_symbol(value)
        if identity.qualified:
            seen.setdefault(identity.ticker, set()).add(identity.normalized)
    return {ticker: next(iter(ids)) for ticker, ids in seen.items() if len(ids) == 1}


def qualify_text(text: Any, by_ticker: Mapping[str, str]) -> tuple[str, list[str]]:
    """Replace whole-word bare tickers the caller maps (``ticker_map``) with EXCHANGE:TICKER.

    Returns (new text, tickers replaced). Words the map does not hold stay as written,
    so prose like FOMC or PMI is never touched.
    """
    replaced: list[str] = []

    def swap(match: "re.Match[str]") -> str:
        token = match.group(0)
        full = by_ticker.get(token)
        if full is None:
            return token
        replaced.append(token)
        return full

    out = _BARE_TOKEN.sub(swap, str(text or ""))
    return out, replaced
