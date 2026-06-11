"""Restore frozen baseline PRESET profile blocks from git HEAD."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "data_analysis_scripts" / "trading_view_move_prediction_analysis.py"

PROFILE_NAMES = [
    "breakout_long",
    "early_momentum_inflection",
    "forward_edge_active",
    "durable_value_compounder",
    "sector_relative_outperformer",
    "value_recovery",
]


def extract_profile_block(text: str, name: str) -> str | None:
    pattern = rf'    "{name}": ScoringProfile\((.*?)\n    \),\n'
    match = re.search(pattern, text, re.S)
    return match.group(0) if match else None


def main() -> None:
    head_text = subprocess.check_output(
        ["git", "show", "HEAD:src/data_analysis_scripts/trading_view_move_prediction_analysis.py"],
        text=True,
        encoding="utf-8",
    )
    current_text = TARGET.read_text(encoding="utf-8")
    updated_text = current_text

    for profile_name in PROFILE_NAMES:
        baseline_block = extract_profile_block(head_text, profile_name)
        current_block = extract_profile_block(updated_text, profile_name)
        if baseline_block is None:
            raise RuntimeError(f"Baseline block not found for {profile_name}")
        if current_block is None:
            raise RuntimeError(f"Current block not found for {profile_name}")
        if baseline_block == current_block:
            print(f"unchanged: {profile_name}")
            continue
        updated_text = updated_text.replace(current_block, baseline_block, 1)
        print(f"restored: {profile_name}")

    TARGET.write_text(updated_text, encoding="utf-8")


if __name__ == "__main__":
    main()
