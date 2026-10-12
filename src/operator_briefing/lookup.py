"""Name dossiers from the briefing pack: raw + progression + stance.

Replaces one-off _tmp_*_detail / _print / _dossiers scripts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from generic_utils.symbol_identity import bare_symbol, symbol_matches_request

from .discovery import load_briefing_pack
from .stance import suggest_stance


def _ticker(symbol: str) -> str:
    return bare_symbol(symbol)


def build_symbol_index(pack: dict[str, Any]) -> dict[str, str]:
    index: dict[str, str] = {}
    bare_candidates: dict[str, set[str]] = {}

    def _add(symbol_value: Any, ticker_value: Any = None) -> None:
        symbol = str(symbol_value or "").strip()
        if not symbol:
            return
        index[symbol.upper()] = symbol
        ticker = str(ticker_value or _ticker(symbol)).strip().upper()
        if ticker:
            bare_candidates.setdefault(ticker, set()).add(symbol)

    for symbol in (pack.get("names") or {}):
        _add(symbol)
    for row in pack.get("book") or []:
        _add(row.get("symbol"), row.get("ticker"))
    for symbol in (pack.get("stances") or {}):
        _add(symbol)
    for ticker, symbols in bare_candidates.items():
        if len(symbols) == 1:
            index[ticker] = next(iter(symbols))
    return index


def resolve_symbols(tokens: Iterable[str], pack: dict[str, Any]) -> tuple[list[str], list[str]]:
    index = build_symbol_index(pack)
    resolved: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        key = str(token or "").strip().upper()
        if not key:
            continue
        symbol = index.get(key)
        if symbol is None:
            missing.append(str(token).strip())
            continue
        if symbol not in seen:
            seen.add(symbol)
            resolved.append(symbol)
    return resolved, missing


def _book_row(pack: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    for row in pack.get("book") or []:
        if str(row.get("symbol") or "").strip().upper() == symbol.strip().upper():
            return row
    for row in pack.get("book") or []:
        stored = row.get("symbol") or row.get("ticker")
        if symbol_matches_request(stored, symbol):
            return row
    return None


def lookup_symbols(
    tokens: Iterable[str],
    *,
    pack: dict[str, Any] | None = None,
    pack_path: Path | str | None = None,
) -> dict[str, Any]:
    payload = pack or load_briefing_pack(pack_path)
    resolved, missing = resolve_symbols(tokens, payload)
    names = payload.get("names") or {}
    progression = payload.get("progression") or {}
    stances = payload.get("stances") or {}
    dossiers: list[dict[str, Any]] = []
    for symbol in resolved:
        raw = names.get(symbol) or {}
        book = _book_row(payload, symbol)
        prog = progression.get(symbol) or {}
        stance = stances.get(symbol)
        if stance is None and raw:
            stance = suggest_stance(
                raw,
                in_book=book is not None,
                continuity=(book or {}).get("continuity") if book else None,
                delta=prog.get("delta_vs_prior_run") if isinstance(prog, dict) else None,
            )
        dossiers.append(
            {
                "symbol": symbol,
                "ticker": _ticker(symbol),
                "raw": raw,
                "book": book,
                "progression": prog,
                "stance": stance,
            }
        )
    return {
        "pack_id": payload.get("run_id"),
        "pack_path": payload.get("_pack_path") or (payload.get("output") or {}).get("json"),
        "prediction_run_id": (payload.get("sources") or {}).get("prediction_run_id"),
        "all_fields_run_id": (payload.get("sources") or {}).get("all_fields_run_id"),
        "resolved": resolved,
        "missing": missing,
        "missing_note": (
            "Name is not in the pack watch list. Recompile, or pass the ticker after a compile "
            "that includes Book / sleeves / earnings. Do not write a _tmp_ extract."
            if missing
            else None
        ),
        "dossiers": dossiers,
        "primary_course": payload.get("primary_course"),
    }
