from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SleeveDefinition:
    name: str
    semantic_buckets: tuple[str, ...]
    preferred_fields: tuple[str, ...]
    max_fields: int


@dataclass(frozen=True)
class SleeveFieldSpec:
    sleeve_name: str
    field_name: str
    display_name: str
    semantic_bucket: str
    indicator_family: str
    relevance_tier: str
    relevance_direction: str
    composite_relevance_score: float | None
    predictor_fill_rate: float | None
    passes_stability_gate: bool


DEFAULT_SLEEVE_DEFINITIONS: tuple[SleeveDefinition, ...] = (
    SleeveDefinition(
        name="volatility_core",
        semantic_buckets=("volatility_risk",),
        preferred_fields=(
            "ADRP",
            "ATRP",
            "Volatility.M",
            "Volatility.W",
            "Volatility.D",
            "beta_1_year",
        ),
        max_fields=6,
    ),
    SleeveDefinition(
        name="liquidity_core",
        semantic_buckets=("volume_liquidity",),
        preferred_fields=(
            "relative_volume_10d_calc",
            "Value.Traded",
            "volume",
            "average_volume_30d_calc",
            "AvgValue.Traded_30d",
        ),
        max_fields=5,
    ),
    SleeveDefinition(
        name="momentum_context",
        semantic_buckets=("technical_pattern", "momentum_performance"),
        preferred_fields=("Recommend.All", "Perf.5D", "Perf.1M", "Perf.3M"),
        max_fields=4,
    ),
)

_RELEVANCE_TIER_RANK = {
    "promote": 3,
    "watch": 2,
    "neutral": 1,
    "demote": 0,
}


def _bool_from_csv(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return normalized in {"1", "true", "yes", "y"}


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _load_predictor_taxonomy_rows(taxonomy_root: str | Path) -> list[dict[str, str]]:
    predictor_csv = (
        Path(taxonomy_root) / "variants" / "by_usage" / "predictor_eligible.csv"
    )
    if not predictor_csv.exists():
        return []
    with predictor_csv.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def resolve_sleeve_field_specs(
    taxonomy_root: str | Path,
    *,
    sleeve_definitions: tuple[SleeveDefinition, ...] = DEFAULT_SLEEVE_DEFINITIONS,
) -> list[SleeveFieldSpec]:
    rows = _load_predictor_taxonomy_rows(taxonomy_root)
    if not rows:
        return []

    resolved_specs: list[SleeveFieldSpec] = []
    for sleeve in sleeve_definitions:
        preferred_rank = {
            field_name: index
            for index, field_name in enumerate(sleeve.preferred_fields)
        }
        candidates = []
        for row in rows:
            field_name = (row.get("field_name") or "").strip()
            semantic_bucket = (row.get("semantic_bucket") or "").strip()
            timeframe_class = (row.get("timeframe_class") or "").strip().lower()
            if not field_name:
                continue
            if (
                field_name not in preferred_rank
                and semantic_bucket not in sleeve.semantic_buckets
            ):
                continue
            if timeframe_class not in {"", "daily"}:
                continue
            if not _bool_from_csv(row.get("is_quantifiable")):
                continue
            if not _bool_from_csv(row.get("is_predictor_candidate")):
                continue
            candidates.append(row)

        candidates.sort(
            key=lambda row: (
                preferred_rank.get((row.get("field_name") or "").strip(), 999),
                -int(_bool_from_csv(row.get("passes_stability_gate"))),
                -_RELEVANCE_TIER_RANK.get(
                    (row.get("relevance_tier") or "").strip().lower(),
                    1,
                ),
                -(_float_or_none(row.get("composite_relevance_score")) or 0.0),
                -(_float_or_none(row.get("predictor_fill_rate")) or 0.0),
                (row.get("field_name") or "").strip(),
            )
        )

        seen_field_names: set[str] = set()
        for row in candidates:
            field_name = (row.get("field_name") or "").strip()
            if field_name in seen_field_names:
                continue
            seen_field_names.add(field_name)
            resolved_specs.append(
                SleeveFieldSpec(
                    sleeve_name=sleeve.name,
                    field_name=field_name,
                    display_name=(row.get("display_name") or field_name).strip()
                    or field_name,
                    semantic_bucket=(row.get("semantic_bucket") or "").strip(),
                    indicator_family=(row.get("indicator_family") or "").strip(),
                    relevance_tier=(row.get("relevance_tier") or "neutral").strip()
                    or "neutral",
                    relevance_direction=(
                        row.get("relevance_direction") or "neutral"
                    ).strip()
                    or "neutral",
                    composite_relevance_score=_float_or_none(
                        row.get("composite_relevance_score")
                    ),
                    predictor_fill_rate=_float_or_none(row.get("predictor_fill_rate")),
                    passes_stability_gate=_bool_from_csv(
                        row.get("passes_stability_gate")
                    ),
                )
            )
            if len(seen_field_names) >= max(1, sleeve.max_fields):
                break
    return resolved_specs
