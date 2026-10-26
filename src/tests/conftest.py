"""Shared pytest setup for src/tests.

- Output isolation: code that falls back to repo-level output folders (plots/, ./parquet,
  logs/canvases) is pointed at a per-session temp dir, so a test run leaves no files in the repo.
- `network` marker: tests that download live SEC/EDGAR filings. Skipped by default
  (they took ~10 minutes of a 11-minute run and re-pull the same filings every time).
  Run them with `--run-network` or RUN_NETWORK_TESTS=1.
"""

from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--run-network", action="store_true", default=False,
                     help="Also run tests marked `network` (live SEC/EDGAR downloads).")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "network: downloads live data (SEC/EDGAR); skipped unless --run-network")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-network") or os.environ.get("RUN_NETWORK_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="live network test; run with --run-network")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _isolate_default_output_roots(tmp_path_factory: pytest.TempPathFactory):
    """Point repo-level fallback output folders at a session temp dir (restored afterwards)."""
    root = tmp_path_factory.mktemp("repo_outputs")
    overrides = {"TV_PLOTS_ROOT": str(root / "plots"), "TV_DEFAULT_PARQUET_DIR": str(root / "parquet"),
                 "TV_CANVAS_ROOT": str(root / "canvases")}
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    yield root
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
