"""Deterministic scoring primitives for focus pool screening.

Every function in this module is pure and deterministic: identical inputs
produce identical outputs regardless of input row ordering. Ties in
percentile ranking receive the average rank; final row ordering elsewhere
must break ties by symbol.
"""

from __future__ import annotations

import math
from typing import Any, Hashable, Mapping, Sequence

DIRECTION_HIGHER_BETTER = "higher_better"
DIRECTION_LOWER_BETTER = "lower_better"
DIRECTION_BAND = "band"
SUPPORTED_DIRECTIONS = (DIRECTION_HIGHER_BETTER, DIRECTION_LOWER_BETTER, DIRECTION_BAND)


def coerce_float(value: Any) -> float | None:
    """Best-effort numeric coercion; returns None for anything non-numeric."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(",", "")
        if not text or text.lower() in {"nan", "none", "null", "n/a", "-", "--"}:
            return None
        if text.endswith("%"):
            text = text[:-1].strip()
        try:
            number = float(text)
        except ValueError:
            return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def apply_valid_range(
    value: float | None, valid_range: Sequence[float | None] | None
) -> float | None:
    """Values outside the valid range are treated as missing (not clipped)."""
    if value is None or valid_range is None:
        return value
    low, high = valid_range[0], valid_range[1]
    if low is not None and value < low:
        return None
    if high is not None and value > high:
        return None
    return value


def clip_value(value: float | None, clip: Sequence[float | None] | None) -> float | None:
    """Winsorize a value into [low, high]; None bounds are open-ended."""
    if value is None or clip is None:
        return value
    low, high = clip[0], clip[1]
    if low is not None and value < low:
        return low
    if high is not None and value > high:
        return high
    return value


def percentile_scores(
    observations: Mapping[Hashable, float | None],
    *,
    direction: str = DIRECTION_HIGHER_BETTER,
) -> dict[Hashable, float | None]:
    """Rank-based 0-100 scores. None observations stay None.

    Ties share the average rank. With a single observation the score is 50.
    """
    if direction not in (DIRECTION_HIGHER_BETTER, DIRECTION_LOWER_BETTER):
        raise ValueError(f"percentile direction must be higher/lower_better, got {direction!r}")
    valid = [(key, val) for key, val in observations.items() if val is not None]
    result: dict[Hashable, float | None] = {key: None for key in observations}
    n = len(valid)
    if n == 0:
        return result
    if n == 1:
        result[valid[0][0]] = 50.0
        return result

    ordered = sorted(valid, key=lambda item: (item[1], str(item[0])))
    ranks: dict[Hashable, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        average_rank = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[ordered[k][0]] = average_rank
        i = j + 1

    for key, _ in valid:
        pct = ranks[key] / (n - 1) * 100.0
        result[key] = pct if direction == DIRECTION_HIGHER_BETTER else 100.0 - pct
    return result


def band_score(
    value: float | None,
    low: float,
    high: float,
    decay_span: float | None = None,
) -> float | None:
    """100 inside [low, high]; linear decay to 0 at decay_span outside the band."""
    if value is None:
        return None
    if low > high:
        raise ValueError(f"band low {low} must be <= high {high}")
    if low <= value <= high:
        return 100.0
    width = high - low
    span = decay_span if decay_span is not None and decay_span > 0 else (width if width > 0 else 1.0)
    distance = (low - value) if value < low else (value - high)
    return max(0.0, 100.0 * (1.0 - distance / span))


def score_field_value(
    value: float | None,
    *,
    direction: str,
    band: Sequence[float] | None = None,
    decay_span: float | None = None,
    percentile_lookup: Mapping[Hashable, float | None] | None = None,
    lookup_key: Hashable | None = None,
) -> float | None:
    """Score one value. Band fields score directly; ranked fields read the
    precomputed percentile lookup keyed by ``lookup_key``."""
    if direction == DIRECTION_BAND:
        if band is None:
            raise ValueError("band direction requires band=[low, high]")
        return band_score(value, band[0], band[1], decay_span)
    if percentile_lookup is None:
        raise ValueError("ranked directions require a percentile lookup")
    return percentile_lookup.get(lookup_key)


def weighted_average(
    components: Sequence[tuple[float, float | None]],
) -> tuple[float | None, float]:
    """(score, coverage): weighted mean over available scores.

    Coverage is the share of total configured weight that had a score.
    """
    total_weight = sum(weight for weight, _ in components if weight > 0)
    available = [(w, s) for w, s in components if s is not None and w > 0]
    available_weight = sum(w for w, _ in available)
    if total_weight <= 0 or available_weight <= 0:
        return None, 0.0
    score = sum(w * s for w, s in available) / available_weight
    return score, available_weight / total_weight


def evaluate_condition(node: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    """Evaluate a lane condition tree against a flat row mapping.

    Leaf: {"field": <row key>, "op": one of >=,<=,>,<,==,!=,between, "value": number|[lo,hi]}
    Group: {"any": [nodes]} or {"all": [nodes]}.
    A missing/None row value evaluates to False for leaf nodes.
    """
    if "any" in node:
        children = node["any"]
        return any(evaluate_condition(child, row) for child in children)
    if "all" in node:
        children = node["all"]
        return all(evaluate_condition(child, row) for child in children)

    field = str(node.get("field") or "")
    op = str(node.get("op") or "").strip()
    expected = node.get("value")
    actual = row.get(field)
    if actual is None:
        return False
    if op == "between":
        if not isinstance(expected, (list, tuple)) or len(expected) != 2:
            raise ValueError(f"between op needs [low, high], got {expected!r}")
        return expected[0] <= actual <= expected[1]
    if op == ">=":
        return actual >= expected
    if op == "<=":
        return actual <= expected
    if op == ">":
        return actual > expected
    if op == "<":
        return actual < expected
    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    raise ValueError(f"Unsupported lane condition op: {op!r}")
