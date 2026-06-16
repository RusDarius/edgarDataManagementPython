"""JSON config loader for move-prediction conviction mode."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from functools import lru_cache

CONVICTION_CONFIG_SCHEMA_VERSION = "move_prediction_conviction_v1"

DEFAULT_CONVICTION_CONFIG_ROOT = (
    Path(__file__).resolve().parents[2] / "config" / "move_prediction_conviction"
)

DEFAULT_CONVICTION_CONFIG_PATH = (
    DEFAULT_CONVICTION_CONFIG_ROOT / "active_manager_v1.json"
)


def _config_hash(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def resolve_conviction_mode_config(
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Load and validate a conviction-mode JSON config."""
    config_path = Path(path) if path is not None else DEFAULT_CONVICTION_CONFIG_PATH
    return _resolve_conviction_mode_config_cached(str(config_path.resolve()))


@lru_cache(maxsize=8)
def _resolve_conviction_mode_config_cached(resolved_path: str) -> dict[str, Any]:
    config_path = Path(resolved_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Conviction config not found: {config_path}")

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    schema_version = payload.get("schema_version")
    if schema_version != CONVICTION_CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported conviction config schema '{schema_version}' in {config_path}. "
            f"Expected '{CONVICTION_CONFIG_SCHEMA_VERSION}'."
        )

    config_id = str(payload.get("config_id") or config_path.stem)
    resolved: dict[str, Any] = dict(payload)
    resolved["config_id"] = config_id
    resolved["source_path"] = resolved_path
    resolved["config_hash"] = _config_hash(
        {key: value for key, value in payload.items() if key != "config_hash"}
    )
    return resolved
