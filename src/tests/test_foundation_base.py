from __future__ import annotations

from datetime import datetime
from pathlib import Path

from edge_research_tools.foundation_base import (
    _format_min_cap_suffix,
    _next_day_label,
    _sort_day_labels,
    discover_days_to_append,
    normalize_foundation_snapshot_mode,
    resolve_ongoing_foundation_base_dir,
)


def test_normalize_foundation_snapshot_mode() -> None:
    assert normalize_foundation_snapshot_mode(True) == "rebuild"
    assert normalize_foundation_snapshot_mode(False) == "reuse"
    assert normalize_foundation_snapshot_mode(None) == "reuse"
    assert normalize_foundation_snapshot_mode("extend") == "extend"


def test_resolve_ongoing_foundation_base_dir_uses_cap_suffix(tmp_path: Path) -> None:
    base_dir = resolve_ongoing_foundation_base_dir(
        output_root=tmp_path,
        min_market_cap_usd=500_000_000,
    )
    assert base_dir.name == "edge_ongoing_base_min500m"
    assert base_dir.parent.name == "foundations"


def test_resolve_ongoing_foundation_base_dir_honors_explicit_ref(tmp_path: Path) -> None:
    explicit = tmp_path / "custom_base"
    assert resolve_ongoing_foundation_base_dir(
        foundation_base_ref=explicit,
    ) == explicit.resolve()


def test_day_label_helpers() -> None:
    assert _next_day_label("29_03_2026") == "30_03_2026"
    assert _sort_day_labels(["01_04_2026", "29_03_2026"]) == [
        "29_03_2026",
        "01_04_2026",
    ]


def test_discover_days_to_append_skips_existing_days(tmp_path: Path) -> None:
    all_fields_root = tmp_path / "all_fields"
    all_fields_root.mkdir()
    day_a = all_fields_root / "tradingview_all_fields_29_03_2026.duckdb"
    day_b = all_fields_root / "tradingview_all_fields_30_03_2026.duckdb"
    day_a.write_text("a", encoding="utf-8")
    day_b.write_text("b", encoding="utf-8")

    pending = discover_days_to_append(
        all_fields_root=all_fields_root,
        existing_day_labels={"29_03_2026"},
        after_day_label="29_03_2026",
    )
    assert pending == [day_b]


def test_format_min_cap_suffix() -> None:
    assert _format_min_cap_suffix(500_000_000) == "min500m"
    assert _format_min_cap_suffix(None) == "all"
