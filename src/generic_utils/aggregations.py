"""Zero-cutoff aggregations any prompt can reuse on list[dict] rows.

No industry skip-list, no leftover floor, no US-listed filter. The caller
always names the field, the group, the bin edges, and the min-n.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Sequence

from generic_utils.ranking import to_float


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _median(values: Iterable[float | None]) -> float | None:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    mid = len(clean) // 2
    if len(clean) % 2:
        return clean[mid]
    return (clean[mid - 1] + clean[mid]) / 2.0


def _round(value: float | None, digits: int | None) -> float | None:
    if value is None:
        return None
    if digits is None:
        return value
    return round(value, digits)


def numeric_summary(
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
    *,
    digits: int | None = 2,
) -> dict[str, dict[str, Any]]:
    """Per-field n / mean / median / min / max / pct_positive / missing."""
    out: dict[str, dict[str, Any]] = {}
    n_rows = len(rows)
    for field in fields:
        values = [to_float(row.get(field)) for row in rows]
        clean = [v for v in values if v is not None]
        positive = [v for v in clean if v > 0]
        out[field] = {
            "n": len(clean),
            "missing": n_rows - len(clean),
            "mean": _round(_mean(clean), digits) if clean else None,
            "median": _round(_median(clean), digits) if clean else None,
            "min": _round(min(clean), digits) if clean else None,
            "max": _round(max(clean), digits) if clean else None,
            "pct_positive": round(100.0 * len(positive) / len(clean), 1)
            if clean
            else None,
        }
    return out


def group_stats(
    rows: Sequence[Mapping[str, Any]],
    *,
    group_field: str,
    metrics: Sequence[str],
    min_n: int = 1,
    pct_positive_field: str | None = None,
    pct_positive_denom: str = "clean",
    digits: int | None = 2,
) -> list[dict[str, Any]]:
    """Mean of `metrics` per distinct `group_field` value.

    `pct_positive_field` (often `day` or `d5`) adds `pct_positive` when set.
    `pct_positive_denom` is `"clean"` (non-null values) or `"n"` (group size,
    so missing counts as not-up). Groups smaller than `min_n` are omitted.
    Sort is by the first metric, highest-first. No default industry cap.
    """
    if pct_positive_denom not in {"clean", "n"}:
        raise ValueError("pct_positive_denom must be 'clean' or 'n'")
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(row.get(group_field) or "")
        if not key:
            continue
        groups[key].append(row)

    out: list[dict[str, Any]] = []
    for key, members in groups.items():
        n = len(members)
        if n < min_n:
            continue
        rec: dict[str, Any] = {group_field: key, "n": n}
        for metric in metrics:
            rec[metric] = _round(
                _mean(to_float(m.get(metric)) for m in members), digits
            )
        if pct_positive_field:
            values = [to_float(m.get(pct_positive_field)) for m in members]
            hits = sum(1 for v in values if v is not None and v > 0)
            if pct_positive_denom == "n":
                denom = n
            else:
                denom = sum(1 for v in values if v is not None)
            rec["pct_positive"] = (
                round(100.0 * hits / denom, 1) if denom else None
            )
        out.append(rec)
    sort_field = metrics[0] if metrics else "n"
    out.sort(key=lambda r: (r.get(sort_field) is None, -(r.get(sort_field) or 0)))
    return out


def histogram(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
    bins: Sequence[float],
) -> list[dict[str, Any]]:
    """Count rows into caller-supplied bin edges. Edges are left-inclusive, last right-inclusive."""
    edges = [float(b) for b in bins]
    if len(edges) < 2:
        raise ValueError("histogram needs at least two bin edges")
    buckets = [
        {"lo": edges[i], "hi": edges[i + 1], "n": 0, "label": f"{edges[i]:g}-{edges[i + 1]:g}"}
        for i in range(len(edges) - 1)
    ]
    missing = 0
    below = 0
    above = 0
    last = len(buckets) - 1
    for row in rows:
        value = to_float(row.get(field))
        if value is None:
            missing += 1
            continue
        if value < edges[0]:
            below += 1
            continue
        if value > edges[-1]:
            above += 1
            continue
        placed = False
        for i, bucket in enumerate(buckets):
            hi = bucket["hi"]
            if value < hi or (i == last and value <= hi):
                bucket["n"] += 1
                placed = True
                break
        if not placed:
            above += 1
    payload: list[dict[str, Any]] = list(buckets)
    if below:
        payload.insert(0, {"lo": None, "hi": edges[0], "n": below, "label": f"<{edges[0]:g}"})
    if above:
        payload.append({"lo": edges[-1], "hi": None, "n": above, "label": f">{edges[-1]:g}"})
    if missing:
        payload.append({"lo": None, "hi": None, "n": missing, "label": "missing"})
    return payload


def value_counts(
    rows: Sequence[Mapping[str, Any]],
    field: str,
    *,
    top: int = 40,
) -> list[dict[str, Any]]:
    """Top-N distinct values of `field` with counts. Missing counted as empty string."""
    counter: Counter[str] = Counter()
    for row in rows:
        counter[str(row.get(field) or "")] += 1
    ranked = counter.most_common(top)
    return [{"value": value or None, "n": n} for value, n in ranked]


def overlap(
    ids_a: Sequence[str],
    ids_b: Sequence[str],
    *,
    list_limit: int = 40,
) -> dict[str, Any]:
    """Set overlap of two id lists. Listed members are capped; counts are exact."""
    set_a = {str(x) for x in ids_a if x}
    set_b = {str(x) for x in ids_b if x}
    both = sorted(set_a & set_b)
    a_only = sorted(set_a - set_b)
    b_only = sorted(set_b - set_a)
    return {
        "n_a": len(set_a),
        "n_b": len(set_b),
        "n_both": len(both),
        "n_a_only": len(a_only),
        "n_b_only": len(b_only),
        "both": both[:list_limit],
        "a_only": a_only[:list_limit],
        "b_only": b_only[:list_limit],
        "list_limit": list_limit,
    }


def ids_of(rows: Sequence[Mapping[str, Any]], id_field: str = "symbol") -> list[str]:
    return [str(row.get(id_field) or "") for row in rows if row.get(id_field)]


def weighted_group_stats(
    rows: Sequence[Mapping[str, Any]],
    *,
    group_field: str,
    weight_field: str,
    metrics: Sequence[str] = (),
    min_n: int = 1,
    digits: int | None = 2,
) -> list[dict[str, Any]]:
    """Sum of `weight_field` plus weighted means of `metrics` per group.

    Rows with missing/non-positive weight are skipped for the weight sum
    and for weighted means. No eligibility cutoff lives here.
    """
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(row.get(group_field) or "")
        if not key:
            continue
        groups[key].append(row)

    out: list[dict[str, Any]] = []
    for key, members in groups.items():
        n = len(members)
        if n < min_n:
            continue
        weights = [to_float(m.get(weight_field)) for m in members]
        usable = [(w, m) for w, m in zip(weights, members) if w is not None and w > 0]
        weight_sum = sum(w for w, _ in usable)
        rec: dict[str, Any] = {
            group_field: key,
            "n": n,
            "weight": _round(weight_sum, digits) if usable else None,
        }
        for metric in metrics:
            if not usable or weight_sum <= 0:
                rec[metric] = None
                continue
            numer = 0.0
            denom = 0.0
            for weight, member in usable:
                value = to_float(member.get(metric))
                if value is None:
                    continue
                numer += weight * value
                denom += weight
            rec[metric] = _round(numer / denom, digits) if denom else None
        out.append(rec)
    out.sort(
        key=lambda r: (r.get("weight") is None, -(r.get("weight") or 0)),
    )
    return out
