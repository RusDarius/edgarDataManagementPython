"""Operator briefing pack — deterministic evidence layer for daily canvases.

Compile once per scan, then attach the JSON/MD. Agents reason over the pack;
they do not re-fish DuckDB for leftover dumps.
"""

from .compile import compile_briefing_pack, render_markdown
from .discovery import lock_sources

__all__ = ["compile_briefing_pack", "lock_sources", "render_markdown"]
