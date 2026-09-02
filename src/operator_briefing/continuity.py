"""Session continuity / polar-flip gate for the operator briefing pack.

A polar flip is yesterday ADD/NEW vs today EXIT/SHORT. Default is HOLD /
NO-ADD until a thesis-kill is cited. Mix avoid_value_trap is not an EXIT.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

ADD_LIKE = frozenset({"ADD", "NEW", "HOLD_WITH_ADD"})
EXIT_LIKE = frozenset({"EXIT", "SHORT", "HEDGE", "HEDGE_OR_SHORT"})
NO_ADD_LIKE = frozenset({"HOLD_NO_ADD", "HOLD", "WAIT", "WATCH", "TRIM", "DERISK_INTO_PRINT", "DERISK"})


def load_prior_actions(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    return payload


def _ticker_key(symbol: str) -> str:
    text = str(symbol or "").strip()
    if ":" in text:
        return text.split(":")[-1]
    return text


def prior_action_for(priors: Mapping[str, Any], symbol: str) -> dict[str, Any] | None:
    actions = priors.get("actions") or {}
    if not isinstance(actions, dict):
        return None
    ticker = _ticker_key(symbol)
    row = actions.get(ticker) or actions.get(symbol)
    if isinstance(row, dict):
        return row
    return None


def is_polar_flip(prior_action: str | None, naive_action: str | None) -> bool:
    prior = str(prior_action or "").strip().upper()
    naive = str(naive_action or "").strip().upper()
    return prior in ADD_LIKE and naive in EXIT_LIKE


def map_mix_to_naive_book_action(
    mix: str | None,
    *,
    mtp: str | None = None,
    leftover: float | None = None,
    rsi: float | None = None,
    rng: float | None = None,
) -> str:
    """Naive map from manager_action_signal. Continuity may override."""
    signal = str(mix or "").strip().lower()
    timing = str(mtp or "").strip().upper()
    if timing in {"AVOID_CHASE", "AVOID"}:
        return "HOLD_NO_ADD"
    if signal.startswith("add_long") or signal.startswith("accumulate"):
        if leftover is not None and leftover <= 0 and rsi is not None and rsi >= 63:
            return "HOLD_NO_ADD"
        return "ADD"
    if signal in {"hedge_or_short", "fragility_short"}:
        return "EXIT"
    if signal.startswith("trim"):
        return "TRIM"
    if signal in {"avoid_value_trap", "avoid_reversal_trap"}:
        return "PASS_OR_HOLD_NO_ADD"
    if signal.startswith("hold_quality"):
        return "HOLD"
    return "HOLD"


def continuity_action(
    *,
    prior: Mapping[str, Any] | None,
    naive: str,
    close: float | None,
    vs_cost_pct: float | None,
    weeks_ras: float | None,
    months_ras: float | None,
    lost_sma50: bool,
    leftover: float | None,
    rsi: float | None,
    rng: float | None,
) -> dict[str, Any]:
    """Return published action + polar flag + reason. Does not invent thesis-kills
    beyond the numeric rules in daily_generic.
    """
    prior_action = str((prior or {}).get("action") or "").upper()
    invalidation = (prior or {}).get("invalidation")
    naive_norm = naive.upper()
    if naive_norm == "PASS_OR_HOLD_NO_ADD":
        naive_norm = "EXIT" if prior_action in ADD_LIKE else "HOLD_NO_ADD"

    polar = is_polar_flip(prior_action, naive_norm)
    kill_reasons: list[str] = []
    if invalidation is not None and close is not None:
        try:
            if close < float(invalidation):
                kill_reasons.append(f"close {close} through invalidation {invalidation}")
        except (TypeError, ValueError):
            pass
    if weeks_ras is not None and months_ras is not None and weeks_ras <= 0 and months_ras <= 0:
        kill_reasons.append(f"weeks RAS {weeks_ras:.2f} and months RAS {months_ras:.2f} both <= 0")
    if vs_cost_pct is not None and vs_cost_pct <= -15 and (weeks_ras is None or weeks_ras < 0.15) and lost_sma50:
        kill_reasons.append(
            f"vs-cost {vs_cost_pct:.1f}% and lost SMA50 with weeks RAS {weeks_ras}"
        )
    if leftover is not None and leftover <= 0 and rng is not None and rng >= 85 and rsi is not None and rsi >= 70:
        kill_reasons.append(f"leftover gone rng {rng:.0f} RSI {rsi:.0f}")

    if polar and not kill_reasons:
        return {
            "action": "HOLD_NO_ADD",
            "polar": True,
            "polar_blocked": True,
            "prior_action": prior_action or None,
            "naive_action": naive,
            "reason": "Polar ADD/NEW -> EXIT blocked. Mix trap / naive EXIT is not a thesis-kill.",
            "invalidation": invalidation,
        }

    published = naive_norm
    if prior_action == "EXIT" and published in {"HOLD", "ADD", "HOLD_NO_ADD"}:
        repaired = (
            weeks_ras is not None
            and weeks_ras >= 0.25
            and not lost_sma50
        )
        if not repaired:
            published = "EXIT"
    if prior_action in ADD_LIKE and published == "ADD":
        published = "ADD"
    if prior_action == "HOLD_NO_ADD" and published == "ADD":
        published = "HOLD_NO_ADD"

    return {
        "action": published,
        "polar": polar,
        "polar_blocked": False,
        "prior_action": prior_action or None,
        "naive_action": naive,
        "reason": "; ".join(kill_reasons) if kill_reasons else None,
        "invalidation": invalidation,
        "thesis_kill": bool(kill_reasons) if polar else False,
    }
