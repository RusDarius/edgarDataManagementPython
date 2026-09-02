"""Operator briefing pack — deterministic evidence layer for daily canvases.

Compile once per scan, then attach the JSON/MD. Agents reason over the pack;
they do not re-fish DuckDB for leftover dumps. Use inspect / lookup / compare /
stance instead of writing _tmp_ scripts under logs/.
"""

from .compile import compile_briefing_pack, render_markdown
from .compare import compare_prediction_runs, summarize_run_compare
from .discovery import latest_briefing_pack_path, load_briefing_pack, lock_sources
from .inspect_sources import inspect_locked_sources
from .lookup import lookup_symbols
from .stance import leftover_flags, rank_suggested_courses, suggest_stance, summarize_operator_course

__all__ = [
    "compile_briefing_pack",
    "compare_prediction_runs",
    "inspect_locked_sources",
    "latest_briefing_pack_path",
    "load_briefing_pack",
    "lock_sources",
    "lookup_symbols",
    "leftover_flags",
    "rank_suggested_courses",
    "render_markdown",
    "suggest_stance",
    "summarize_operator_course",
    "summarize_run_compare",
]
