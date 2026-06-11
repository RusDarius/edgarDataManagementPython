"""Versioned JSON profile configs for move-prediction scoring.

Baseline profiles remain frozen in ``PRESET_SCORING_PROFILES`` (Python).
New or revised lenses are **appended** as JSON files (e.g. ``breakout_long_v1.json``)
so historical DuckDB runs stay comparable.

Layout::

    config/move_prediction_profiles/
      suites/baseline.json
      suites/active_manager_v1.json
      profiles/breakout_long_v1.json
      ...
"""

from __future__ import annotations

import json
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from data_analysis_scripts.trading_view_move_prediction_analysis import (
    CONSENSUS_PROFILE_WEIGHTS,
    DEFAULT_MOVE_PREDICTION_PROFILE_SUITE,
    DirectionalBias,
    ScoringProfile,
)

PROFILE_CONFIG_FILE_SCHEMA_VERSION = "move_prediction_profile_file_v1"
PROFILE_SUITE_SCHEMA_VERSION = "move_prediction_profile_suite_v1"

DEFAULT_PROFILE_CONFIG_ROOT = (
    Path(__file__).resolve().parents[2] / "config" / "move_prediction_profiles"
)


def _default_profiles_dir() -> Path:
    return DEFAULT_PROFILE_CONFIG_ROOT / "profiles"


def _default_suites_dir() -> Path:
    return DEFAULT_PROFILE_CONFIG_ROOT / "suites"


def scoring_profile_to_dict(profile: ScoringProfile) -> dict[str, Any]:
    payload = asdict(profile)
    payload["component_directional_bias"] = {
        component_name: {
            "positive_multiplier": bias.positive_multiplier,
            "negative_multiplier": bias.negative_multiplier,
        }
        for component_name, bias in profile.component_directional_bias.items()
    }
    return payload


def scoring_profile_from_dict(profile_data: Mapping[str, Any]) -> ScoringProfile:
    bias_raw = profile_data.get("component_directional_bias", {})
    component_directional_bias = {
        str(component_name): DirectionalBias(
            positive_multiplier=float(values.get("positive_multiplier", 1.0)),
            negative_multiplier=float(values.get("negative_multiplier", 1.0)),
        )
        for component_name, values in bias_raw.items()
    }
    return ScoringProfile(
        name=str(profile_data["name"]),
        description=str(profile_data.get("description", "")),
        horizon_weights={
            str(horizon): {str(k): float(v) for k, v in weights.items()}
            for horizon, weights in profile_data.get("horizon_weights", {}).items()
        },
        component_signal_weights={
            str(component): {str(k): float(v) for k, v in signals.items()}
            for component, signals in profile_data.get(
                "component_signal_weights", {}
            ).items()
        },
        component_directional_bias=component_directional_bias,
        missing_component_scores_by_horizon={
            str(horizon): {str(k): float(v) for k, v in scores.items()}
            for horizon, scores in profile_data.get(
                "missing_component_scores_by_horizon", {}
            ).items()
        },
        performance_tracking_periods={
            str(horizon): list(periods)
            for horizon, periods in profile_data.get(
                "performance_tracking_periods", {}
            ).items()
        },
        intro_metric_notes=[
            str(note) for note in profile_data.get("intro_metric_notes", [])
        ],
        confidence_multiplier=float(profile_data.get("confidence_multiplier", 1.0)),
        confidence_offset=float(profile_data.get("confidence_offset", 0.0)),
    )


def load_profile_config_file(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    schema_version = payload.get("schema_version")
    if schema_version != PROFILE_CONFIG_FILE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported profile config schema '{schema_version}' in {config_path}. "
            f"Expected '{PROFILE_CONFIG_FILE_SCHEMA_VERSION}'."
        )
    profile_id = str(payload.get("profile_id") or payload.get("profile", {}).get("name"))
    if not profile_id:
        raise ValueError(f"Profile config missing profile_id: {config_path}")
    profile = scoring_profile_from_dict(payload["profile"])
    if profile.name != profile_id:
        profile = ScoringProfile(
            name=profile_id,
            description=profile.description,
            horizon_weights=profile.horizon_weights,
            component_signal_weights=profile.component_signal_weights,
            component_directional_bias=profile.component_directional_bias,
            missing_component_scores_by_horizon=profile.missing_component_scores_by_horizon,
            performance_tracking_periods=profile.performance_tracking_periods,
            intro_metric_notes=profile.intro_metric_notes,
            confidence_multiplier=profile.confidence_multiplier,
            confidence_offset=profile.confidence_offset,
        )
    return {
        "schema_version": schema_version,
        "profile_id": profile_id,
        "base_profile_id": payload.get("base_profile_id"),
        "version": payload.get("version"),
        "effective_from": payload.get("effective_from"),
        "changelog": payload.get("changelog"),
        "source_path": str(config_path),
        "profile": profile,
    }


class ProfileConfigRegistry:
    """Runtime registry of JSON-defined profile versions."""

    def __init__(self) -> None:
        self._profiles: dict[str, dict[str, Any]] = {}

    def register_profile_file(self, path: str | Path) -> str:
        loaded = load_profile_config_file(path)
        profile_id = loaded["profile_id"]
        self._profiles[profile_id] = loaded
        return profile_id

    def register_profiles_dir(self, directory: str | Path | None = None) -> list[str]:
        profiles_dir = Path(directory or _default_profiles_dir())
        if not profiles_dir.exists():
            return []
        registered: list[str] = []
        for config_path in sorted(profiles_dir.glob("*.json")):
            registered.append(self.register_profile_file(config_path))
        return registered

    def get_profile(self, profile_id: str) -> ScoringProfile | None:
        loaded = self._profiles.get(profile_id)
        if loaded is None:
            return None
        return loaded["profile"]

    def get_metadata(self, profile_id: str) -> dict[str, Any] | None:
        loaded = self._profiles.get(profile_id)
        if loaded is None:
            return None
        return {
            key: loaded.get(key)
            for key in (
                "schema_version",
                "profile_id",
                "base_profile_id",
                "version",
                "effective_from",
                "changelog",
                "source_path",
            )
        }

    def list_profile_ids(self) -> list[str]:
        return sorted(self._profiles)


@lru_cache(maxsize=1)
def get_default_profile_registry() -> ProfileConfigRegistry:
    registry = ProfileConfigRegistry()
    registry.register_profiles_dir()
    return registry


def load_profile_suite(path: str | Path) -> dict[str, Any]:
    suite_path = Path(path)
    payload = json.loads(suite_path.read_text(encoding="utf-8"))
    schema_version = payload.get("schema_version")
    if schema_version != PROFILE_SUITE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported suite schema '{schema_version}' in {suite_path}. "
            f"Expected '{PROFILE_SUITE_SCHEMA_VERSION}'."
        )

    registry = ProfileConfigRegistry()
    profiles_dir = suite_path.parent.parent / "profiles"
    profile_file_map = payload.get("profile_files", {})
    profile_names = [str(name) for name in payload.get("profile_names", [])]
    if not profile_names:
        raise ValueError(f"Suite missing profile_names: {suite_path}")

    for profile_name in profile_names:
        if profile_name in profile_file_map:
            registry.register_profile_file(profiles_dir / profile_file_map[profile_name])
        else:
            candidate = profiles_dir / f"{profile_name}.json"
            if candidate.exists():
                registry.register_profile_file(candidate)

    consensus_weights = {
        str(profile_name): float(weight)
        for profile_name, weight in payload.get(
            "consensus_profile_weights", {}
        ).items()
    }
    inverted_profiles = frozenset(
        str(name) for name in payload.get("inverted_consensus_profiles", [])
    )
    long_profiles = frozenset(
        str(name) for name in payload.get("long_consensus_profiles", [])
    )

    return {
        "schema_version": schema_version,
        "suite_id": str(payload.get("suite_id") or suite_path.stem),
        "suite_version": payload.get("suite_version"),
        "description": payload.get("description"),
        "source_path": str(suite_path),
        "profile_names": profile_names,
        "registry": registry,
        "consensus_profile_weights": consensus_weights,
        "inverted_consensus_profiles": inverted_profiles,
        "long_consensus_profiles": long_profiles,
    }


def resolve_profile_suite(
    profile_suite_path: str | Path | None = None,
    profile_names: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve the effective profile list and consensus settings for a run."""
    if profile_suite_path is None:
        resolved_names = list(profile_names or DEFAULT_MOVE_PREDICTION_PROFILE_SUITE)
        return {
            "suite_id": "builtin_baseline",
            "suite_version": None,
            "source_path": None,
            "profile_names": resolved_names,
            "registry": get_default_profile_registry(),
            "consensus_profile_weights": dict(CONSENSUS_PROFILE_WEIGHTS),
            "inverted_consensus_profiles": frozenset({"fragility_short"}),
            "long_consensus_profiles": frozenset(
                name
                for name in resolved_names
                if name != "fragility_short"
            ),
        }

    suite = load_profile_suite(profile_suite_path)
    if profile_names is not None:
        suite["profile_names"] = list(profile_names)
    return suite


def write_profile_config_file(
    profile: ScoringProfile,
    *,
    output_path: str | Path,
    base_profile_id: str | None = None,
    version: str = "1.0.0",
    effective_from: str | None = None,
    changelog: str | None = None,
) -> Path:
    """Serialize a ScoringProfile to a versioned JSON config file."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": PROFILE_CONFIG_FILE_SCHEMA_VERSION,
        "profile_id": profile.name,
        "base_profile_id": base_profile_id,
        "version": version,
        "effective_from": effective_from,
        "changelog": changelog,
        "profile": scoring_profile_to_dict(profile),
    }
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return destination
