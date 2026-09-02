"""Deterministic focus pool screening over TradingView data.

Combines the daily move-prediction run (scores + raw scan), the all-fields
snapshot, and the latest edge-research unified highlights into one tunable
value / fundamental / technical / momentum pool with lane flags and a
DuckDB focus map.

Entry point: ``python src/focus_pool_screening/example_entry.py``
"""

from .config import DEFAULT_CONFIG_PATH, FocusPoolConfig, load_focus_pool_config
from .engine import run_focus_pool_screening

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "FocusPoolConfig",
    "load_focus_pool_config",
    "run_focus_pool_screening",
]
