"""JSON profile and suite loading for prediction module2."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

PROFILE_CONFIG_FILE_SCHEMA_VERSION = "prediction_module2_profile_v1"
PROFILE_SUITE_SCHEMA_VERSION = "prediction_module2_suite_v1"

DEFAULT_MODULE2_CONFIG_ROOT = (
    Path(__file__).resolve().parents[2] / "config" / "prediction_module2_profiles"
)


@dataclass(frozen=True)
class RegimeGateRule:
    field: str
    source: str = "derived"
    min_z: float | None = None
    max_z: float | None = None
    weight: float = 1.0


@dataclass(frozen=True)
class AntiSignalRule:
    field: str
    source: str = "derived"
    min_z: float | None = None
    max_z: float | None = None
    penalty: float = 1.0


@dataclass(frozen=True)
class PillarConfig:
    weight: float
    fields: dict[str, float] = field(default_factory=dict)
    derived: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DirectionalBiasConfig:
    positive_multiplier: float = 1.0
    negative_multiplier: float = 1.0


@dataclass(frozen=True)
class Module2Profile:
    profile_id: str
    outlook_family: str
    version: str
    description: str
    regime_gates_hard: tuple[RegimeGateRule, ...]
    regime_gates_soft: tuple[RegimeGateRule, ...]
    anti_signals: dict[str, AntiSignalRule]
    pillars: dict[str, PillarConfig]
    horizon_weights: dict[str, float]
    directional_bias: DirectionalBiasConfig
    intro_metric_notes: tuple[str, ...] = ()
    base_profile_id: str | None = None
    effective_from: str | None = None
    changelog: str | None = None
    source_path: str | None = None


@dataclass(frozen=True)
class Module2ProfileSuite:
    suite_id: str
    suite_version: str
    description: str
    profile_names: tuple[str, ...]
    consensus_profile_weights: dict[str, float]
    consensus_mode: str
    outlook_families: dict[str, tuple[str, ...]]
    inverted_consensus_profiles: frozenset[str]
    long_consensus_profiles: frozenset[str]
    registry: Module2ProfileRegistry
    source_path: str | None = None


def _parse_gate_rules(raw_rules: list[Mapping[str, Any]] | None) -> tuple[RegimeGateRule, ...]:
    rules: list[RegimeGateRule] = []
    for item in raw_rules or []:
        rules.append(
            RegimeGateRule(
                field=str(item["field"]),
                source=str(item.get("source", "derived")),
                min_z=float(item["min_z"]) if item.get("min_z") is not None else None,
                max_z=float(item["max_z"]) if item.get("max_z") is not None else None,
                weight=float(item.get("weight", 1.0)),
            )
        )
    return tuple(rules)


def _parse_anti_signals(
    raw_signals: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, AntiSignalRule]:
    parsed: dict[str, AntiSignalRule] = {}
    for field_name, item in (raw_signals or {}).items():
        parsed[str(field_name)] = AntiSignalRule(
            field=str(field_name),
            source=str(item.get("source", "derived")),
            min_z=float(item["min_z"]) if item.get("min_z") is not None else None,
            max_z=float(item["max_z"]) if item.get("max_z") is not None else None,
            penalty=float(item.get("penalty", 1.0)),
        )
    return parsed


def _parse_pillars(raw_pillars: Mapping[str, Mapping[str, Any]] | None) -> dict[str, PillarConfig]:
    parsed: dict[str, PillarConfig] = {}
    for pillar_name, item in (raw_pillars or {}).items():
        parsed[str(pillar_name)] = PillarConfig(
            weight=float(item.get("weight", 0.0)),
            fields={
                str(field_name): float(weight)
                for field_name, weight in (item.get("fields") or {}).items()
            },
            derived={
                str(field_name): float(weight)
                for field_name, weight in (item.get("derived") or {}).items()
            },
        )
    return parsed


def module2_profile_from_dict(payload: Mapping[str, Any]) -> Module2Profile:
    regime_gates = payload.get("regime_gates") or {}
    directional_bias_raw = payload.get("directional_bias") or {}
    return Module2Profile(
        profile_id=str(payload["profile_id"]),
        outlook_family=str(payload.get("outlook_family", "general")),
        version=str(payload.get("version", "1.0.0")),
        description=str(payload.get("description", "")),
        regime_gates_hard=_parse_gate_rules(regime_gates.get("hard")),
        regime_gates_soft=_parse_gate_rules(regime_gates.get("soft")),
        anti_signals=_parse_anti_signals(payload.get("anti_signals")),
        pillars=_parse_pillars(payload.get("pillars")),
        horizon_weights={
            str(horizon): float(weight)
            for horizon, weight in (payload.get("horizon_weights") or {}).items()
        },
        directional_bias=DirectionalBiasConfig(
            positive_multiplier=float(
                directional_bias_raw.get("positive_multiplier", 1.0)
            ),
            negative_multiplier=float(
                directional_bias_raw.get("negative_multiplier", 1.0)
            ),
        ),
        intro_metric_notes=tuple(str(note) for note in payload.get("intro_metric_notes", [])),
        base_profile_id=(
            str(payload["base_profile_id"]) if payload.get("base_profile_id") else None
        ),
        effective_from=(
            str(payload["effective_from"]) if payload.get("effective_from") else None
        ),
        changelog=str(payload["changelog"]) if payload.get("changelog") else None,
    )


def load_module2_profile_config_file(path: str | Path) -> Module2Profile:
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    schema_version = payload.get("schema_version")
    if schema_version != PROFILE_CONFIG_FILE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported module2 profile schema '{schema_version}' in {config_path}. "
            f"Expected '{PROFILE_CONFIG_FILE_SCHEMA_VERSION}'."
        )
    profile_id = str(payload.get("profile_id") or "")
    if not profile_id:
        raise ValueError(f"Module2 profile config missing profile_id: {config_path}")

    profile_data = dict(payload)
    profile_data["profile_id"] = profile_id
    profile_data["description"] = payload.get("description") or payload.get(
        "profile", {}
    ).get("description", "")
    if "pillars" not in profile_data and "profile" in payload:
        nested = payload["profile"]
        for key in (
            "regime_gates",
            "anti_signals",
            "pillars",
            "horizon_weights",
            "directional_bias",
            "intro_metric_notes",
            "outlook_family",
        ):
            if key in nested and key not in profile_data:
                profile_data[key] = nested[key]

    profile = module2_profile_from_dict(profile_data)
    return Module2Profile(
        profile_id=profile.profile_id,
        outlook_family=profile.outlook_family,
        version=profile.version,
        description=profile.description,
        regime_gates_hard=profile.regime_gates_hard,
        regime_gates_soft=profile.regime_gates_soft,
        anti_signals=profile.anti_signals,
        pillars=profile.pillars,
        horizon_weights=profile.horizon_weights,
        directional_bias=profile.directional_bias,
        intro_metric_notes=profile.intro_metric_notes,
        base_profile_id=payload.get("base_profile_id"),
        effective_from=payload.get("effective_from"),
        changelog=payload.get("changelog"),
        source_path=str(config_path),
    )


class Module2ProfileRegistry:
    def __init__(self) -> None:
        self._profiles: dict[str, Module2Profile] = {}

    def register_profile_file(self, path: str | Path) -> str:
        profile = load_module2_profile_config_file(path)
        self._profiles[profile.profile_id] = profile
        return profile.profile_id

    def register_profiles_dir(self, directory: str | Path | None = None) -> list[str]:
        profiles_dir = Path(directory or (DEFAULT_MODULE2_CONFIG_ROOT / "profiles"))
        if not profiles_dir.exists():
            return []
        registered: list[str] = []
        for config_path in sorted(profiles_dir.glob("*.json")):
            registered.append(self.register_profile_file(config_path))
        return registered

    def get_profile(self, profile_id: str) -> Module2Profile | None:
        return self._profiles.get(profile_id)

    def list_profile_ids(self) -> list[str]:
        return sorted(self._profiles)


@lru_cache(maxsize=1)
def get_default_module2_registry() -> Module2ProfileRegistry:
    registry = Module2ProfileRegistry()
    registry.register_profiles_dir()
    return registry


def load_module2_profile_suite(path: str | Path) -> Module2ProfileSuite:
    suite_path = Path(path)
    payload = json.loads(suite_path.read_text(encoding="utf-8"))
    schema_version = payload.get("schema_version")
    if schema_version != PROFILE_SUITE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported module2 suite schema '{schema_version}' in {suite_path}. "
            f"Expected '{PROFILE_SUITE_SCHEMA_VERSION}'."
        )

    registry = Module2ProfileRegistry()
    profiles_dir = suite_path.parent.parent / "profiles"
    profile_file_map = payload.get("profile_files", {})
    profile_names = tuple(str(name) for name in payload.get("profile_names", []))
    if not profile_names:
        raise ValueError(f"Module2 suite missing profile_names: {suite_path}")

    for profile_name in profile_names:
        if profile_name in profile_file_map:
            registry.register_profile_file(profiles_dir / profile_file_map[profile_name])
        else:
            candidate = profiles_dir / f"{profile_name}.json"
            if candidate.exists():
                registry.register_profile_file(candidate)

    consensus_weights = {
        str(name): float(weight)
        for name, weight in (payload.get("consensus_profile_weights") or {}).items()
    }
    weight_total = sum(consensus_weights.values())
    if consensus_weights and abs(weight_total - 1.0) > 1e-6:
        raise ValueError(
            f"Module2 suite consensus weights must sum to 1.0 in {suite_path}; "
            f"got {weight_total:.6f}"
        )

    outlook_families = {
        str(family): tuple(str(name) for name in names)
        for family, names in (payload.get("outlook_families") or {}).items()
    }

    return Module2ProfileSuite(
        suite_id=str(payload.get("suite_id") or suite_path.stem),
        suite_version=str(payload.get("suite_version", "1.0.0")),
        description=str(payload.get("description", "")),
        profile_names=profile_names,
        consensus_profile_weights=consensus_weights,
        consensus_mode=str(payload.get("consensus_mode", "family_orthogonal")),
        outlook_families=outlook_families,
        inverted_consensus_profiles=frozenset(
            str(name) for name in payload.get("inverted_consensus_profiles", [])
        ),
        long_consensus_profiles=frozenset(
            str(name) for name in payload.get("long_consensus_profiles", profile_names)
        ),
        registry=registry,
        source_path=str(suite_path),
    )


def resolve_module2_profile_suite(
    path: str | Path | None = None,
) -> Module2ProfileSuite:
    if path is None:
        default_suite = DEFAULT_MODULE2_CONFIG_ROOT / "suites" / "active_manager_module2_v1.json"
        if not default_suite.exists():
            raise FileNotFoundError(
                f"Default module2 suite not found: {default_suite}"
            )
        return load_module2_profile_suite(default_suite)
    return load_module2_profile_suite(path)
