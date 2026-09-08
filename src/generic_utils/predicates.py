"""Caller-supplied row predicates. No baked RSI / leftover / mcap cutoffs.

A where-string is comma-separated clauses:
    left>=25,rsi<=68,mcap>=2000000000,ind!=Biotechnology

Missing numeric values fail the clause (they do not pass). The spec of
parsed clauses is always echoed so a later agent can audit what ran.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float

_CLAUSE = re.compile(
    r"^(?P<field>[A-Za-z0-9_.]+)\s*(?P<op>>=|<=|!=|==|>|<|~=)\s*(?P<value>.+)$"
)
_OPS = frozenset({">=", "<=", "!=", "==", ">", "<", "~="})


def parse_where(where: str | Sequence[str] | None) -> list[dict[str, Any]]:
    if where is None or where == "":
        return []
    raw_parts = where.split(",") if isinstance(where, str) else list(where)
    clauses: list[dict[str, Any]] = []
    for part in raw_parts:
        text = part.strip()
        if not text:
            continue
        match = _CLAUSE.match(text)
        if not match:
            raise ValueError(f"Cannot parse where clause: {text!r}")
        field = match.group("field")
        op = match.group("op")
        if op not in _OPS:
            raise ValueError(f"Unsupported operator {op!r} in {text!r}")
        raw_value = match.group("value").strip().strip("'\"")
        number = to_float(raw_value)
        clauses.append(
            {
                "field": field,
                "op": op,
                "value": number if number is not None else raw_value,
                "numeric": number is not None,
            }
        )
    return clauses


def _match(row: Mapping[str, Any], clause: Mapping[str, Any]) -> bool:
    field = str(clause["field"])
    op = str(clause["op"])
    expected = clause["value"]
    observed = row.get(field)
    if clause.get("numeric"):
        left = to_float(observed)
        if left is None:
            return False
        right = float(expected)
        if op == ">=":
            return left >= right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == "<":
            return left < right
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        return False
    left_s = str(observed or "")
    right_s = str(expected)
    if op == "==":
        return left_s == right_s
    if op == "!=":
        return left_s != right_s
    if op == "~=":
        return right_s.lower() in left_s.lower()
    return False


def filter_rows(
    rows: Sequence[Mapping[str, Any]],
    where: str | Sequence[str] | None,
) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
    """Return (kept, clauses). Empty where keeps every row."""
    clauses = parse_where(where)
    if not clauses:
        return list(rows), clauses
    kept = [row for row in rows if all(_match(row, clause) for clause in clauses)]
    return kept, clauses
