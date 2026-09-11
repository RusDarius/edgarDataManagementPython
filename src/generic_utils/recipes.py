"""Named structural SQL for TradingView DuckDB scans.

Recipes know table and column *names* so agents do not regenerate
`SELECT score FROM profile_horizon_scores WHERE horizon_name='weeks'`.
They do not encode unpaid / paid / satellite eligibility.
"""

from __future__ import annotations

from typing import Any

RECIPES: dict[str, dict[str, Any]] = {
    "pred.runs": {
        "sql": """
            SELECT run_id, CAST(created_at_utc AS VARCHAR) AS created_at_utc
            FROM run_metadata
            ORDER BY created_at_utc DESC NULLS LAST, run_id DESC
            LIMIT ?
        """,
        "params": ["limit"],
        "defaults": {"limit": 8},
        "notes": "Newest move-prediction runs in this DuckDB.",
    },
    "pred.raw_identity": {
        "sql": """
            SELECT symbol, name, exchange, country, industry, sector,
                   close, change, "Perf.5D", "Perf.1M", RSI
            FROM raw_scan_rows
            WHERE run_id = ?
        """,
        "params": ["run_id"],
        "notes": "Tape identity. Never SELECT * on raw_scan_rows (200+ cols).",
    },
    "pred.tape_returns": {
        "sql": """
            SELECT symbol, name, exchange, industry,
                   close, change AS day, "Perf.5D" AS d5, "Perf.W" AS w,
                   "Perf.1M" AS m1, "Perf.3M" AS m3, RSI AS rsi,
                   relative_volume_10d_calc AS relvol
            FROM raw_scan_rows
            WHERE run_id = ?
        """,
        "params": ["run_id"],
        "notes": "Day/week/5D/1M/3M returns from pred tape. Prefer all-fields named if a Perf.* column is missing.",
    },
    "pred.profile_weeks": {
        "sql": """
            SELECT symbol, profile_name, score, manager_action_signal, risk_adjusted_score
            FROM profile_horizon_scores
            WHERE run_id = ?
              AND horizon_name = 'weeks'
        """,
        "params": ["run_id"],
        "notes": "Weeks-horizon profile scores. Pivot in Python if you need bo/cont/fwd columns.",
    },
    "pred.profile_weeks_pivot": {
        "sql": """
            SELECT symbol,
                   max(CASE WHEN profile_name = 'breakout_long_v1' THEN score END) AS bo,
                   max(CASE WHEN profile_name = 'quality_continuation_v1' THEN score END) AS cont,
                   max(CASE WHEN profile_name = 'forward_edge_active_v2' THEN score END) AS fwd,
                   max(CASE WHEN profile_name = 'early_momentum_inflection_v1' THEN score END) AS early,
                   max(CASE WHEN profile_name = 'sustained_momentum_safety_v1' THEN score END) AS sms,
                   max(CASE WHEN profile_name = 'fragility_short' THEN score END) AS frag,
                   max(CASE WHEN profile_name = 'mean_reversion_exhaustion_v1' THEN score END) AS exh
            FROM profile_horizon_scores
            WHERE run_id = ?
              AND horizon_name = 'weeks'
            GROUP BY 1
        """,
        "params": ["run_id"],
        "notes": "The bo/cont/fwd join the briefing pack uses. Compare is weeks profile deltas, not leftover.",
    },
    "pred.conviction": {
        "sql": """
            SELECT symbol, conviction_score, rank_overall, manager_action_signal,
                   weeks_ras, months_ras, entry_readiness, exclusion_reason, sleeve
            FROM conviction_rankings
            WHERE run_id = ?
        """,
        "params": ["run_id"],
        "notes": "Mix column is manager_action_signal. Rank column is rank_overall.",
    },
    "pred.consensus_weeks": {
        "sql": """
            SELECT ticker, manager_action_signal,
                   consensus_weeks_score, consensus_weeks_risk_adjusted_score
            FROM consensus_rows
            WHERE run_id = ?
        """,
        "params": ["run_id"],
        "notes": "consensus_rows uses ticker, not symbol.",
    },
    "pred.regime": {
        "sql": """
            SELECT symbol, regime_fit_score, active_mgmt_tier
            FROM regime_context_scores
            WHERE run_id = ?
        """,
        "params": ["run_id"],
        "notes": "Column is active_mgmt_tier, not regime_fit_tier.",
    },
}


def list_recipes() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "params": spec.get("params") or [],
            "defaults": spec.get("defaults") or {},
            "notes": spec.get("notes"),
        }
        for name, spec in RECIPES.items()
    ]


def render_recipe(name: str, **values: Any) -> tuple[str, list[Any]]:
    spec = RECIPES.get(name)
    if spec is None:
        known = ", ".join(sorted(RECIPES))
        raise KeyError(f"Unknown recipe {name!r}. Known: {known}")
    params_spec = list(spec.get("params") or [])
    defaults = dict(spec.get("defaults") or {})
    bound: list[Any] = []
    for key in params_spec:
        if key in values and values[key] is not None:
            bound.append(values[key])
        elif key in defaults:
            bound.append(defaults[key])
        else:
            raise ValueError(f"Recipe {name!r} requires parameter {key!r}")
    return str(spec["sql"]), bound
