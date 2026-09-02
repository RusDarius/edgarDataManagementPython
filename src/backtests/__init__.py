"""Generic backtests package for signal evidence and overlay diagnostics."""

from .config import BacktestConfig, DEFAULT_CONFIG_PATH, load_backtest_config
from .engine import run_backtests

__all__ = ["BacktestConfig", "DEFAULT_CONFIG_PATH", "load_backtest_config", "run_backtests"]
