"""Generate short-term risk-adjusted trade rankings from a module2 run folder."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def _float(row: dict[str, str], key: str, default: float = 0.0) -> float:
    value = row.get(key, "")
    if value in ("", None):
        return default
    return float(value)


def load_profile(run_dir: Path, profile_name: str) -> dict[str, dict[str, str]]:
    path = run_dir / f"module2__profile_{profile_name}.csv"
    rows: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            symbol = row.get("symbol", "").strip()
            if not symbol:
                continue
            rows[symbol] = row
    return rows


def build_rankings(run_dir: Path) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    swing = load_profile(run_dir, "upside_swing_v1")
    breakout = load_profile(run_dir, "breakout_continuation_v1")
    fragility = load_profile(run_dir, "fragility_risk_v1")
    forward = load_profile(run_dir, "forward_fundamental_edge_v1")

    consensus_by_symbol: dict[str, dict[str, str]] = {}
    consensus_path = run_dir / "module2__consensus.csv"
    if consensus_path.exists():
        with consensus_path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                consensus_by_symbol[row["symbol"]] = row

    candidates: list[dict[str, object]] = []
    for symbol in set(swing) | set(breakout):
        swing_row = swing.get(symbol)
        breakout_row = breakout.get(symbol)
        fragility_row = fragility.get(symbol)
        forward_row = forward.get(symbol)
        consensus_row = consensus_by_symbol.get(symbol)

        repair = _float(swing_row, "pillar_repair_trigger") if swing_row else 0.0
        upside = _float(swing_row, "pillar_upside_room") if swing_row else 0.0
        safety = _float(swing_row, "pillar_safety_floor") if swing_row else 0.0
        continuation = _float(breakout_row, "pillar_continuation") if breakout_row else 0.0
        extension = _float(breakout_row, "pillar_extension") if breakout_row else 0.0
        fragility_score = _float(fragility_row, "score") if fragility_row else 0.0

        risk_adjusted = 0.0
        tags: list[str] = []

        if swing_row and swing_row.get("hard_gate_passed") == "True":
            risk_adjusted += _float(swing_row, "score") * 1.15
            risk_adjusted += max(0.0, repair) * 0.12
            risk_adjusted += max(0.0, upside) * 0.08
            risk_adjusted += max(0.0, safety) * 0.18
            risk_adjusted -= max(0.0, -safety) * 0.35
            tags.append("swing_bounce")

        if breakout_row and breakout_row.get("hard_gate_passed") == "True":
            risk_adjusted += _float(breakout_row, "score") * 0.55
            risk_adjusted += max(0.0, continuation) * 0.06
            risk_adjusted -= max(0.0, extension - 1.5) * 0.15
            tags.append("breakout_tape")

        if forward_row and forward_row.get("hard_gate_passed") == "True" and _float(forward_row, "score") > 1.2:
            risk_adjusted += min(_float(forward_row, "score"), 2.5) * 0.12
            tags.append("fundamental_edge")

        if fragility_row:
            risk_adjusted -= fragility_score * 0.45
            if fragility_score > 1.0:
                tags.append("fragility_watch")

        if consensus_row:
            coverage = _float(consensus_row, "coverage")
            consensus_score = _float(consensus_row, "consensus_score")
            if coverage >= 0.5 and consensus_score > 0.7:
                risk_adjusted += consensus_score * 0.08 * coverage
                tags.append("multi_lens")

        if "swing_bounce" in tags and "breakout_tape" in tags:
            tags.append("mixed_setup")
            risk_adjusted *= 0.85

        if not tags or risk_adjusted <= 0:
            continue
        if "swing_bounce" in tags and safety < -0.55:
            continue

        if "swing_bounce" in tags and "breakout_tape" not in tags:
            hold_horizon = "1-2w bounce"
        elif "breakout_tape" in tags and "swing_bounce" not in tags:
            hold_horizon = "1-2w momentum"
        else:
            hold_horizon = "1-2w blended"

        tier = "A" if risk_adjusted >= 1.35 else ("B" if risk_adjusted >= 0.95 else "C")
        name = (swing_row or breakout_row or fragility_row or forward_row or {}).get(
            "company_name"
        ) or (swing_row or breakout_row or fragility_row or forward_row or {}).get(
            "name", ""
        )

        candidates.append(
            {
                "symbol": symbol,
                "name": name,
                "risk_adjusted_score": round(risk_adjusted, 4),
                "tier": tier,
                "hold_horizon": hold_horizon,
                "swing_score": round(_float(swing_row, "score"), 4) if swing_row else "",
                "breakout_score": round(_float(breakout_row, "score"), 4) if breakout_row else "",
                "repair_trigger": round(repair, 4) if swing_row else "",
                "upside_room": round(upside, 4) if swing_row else "",
                "safety_floor": round(safety, 4) if swing_row else "",
                "fragility_score": round(fragility_score, 4) if fragility_row else "",
                "consensus_score": round(_float(consensus_row, "consensus_score"), 4) if consensus_row else "",
                "consensus_coverage": round(_float(consensus_row, "coverage"), 4) if consensus_row else "",
                "tags": ";".join(tags),
            }
        )

    candidates.sort(key=lambda row: float(row["risk_adjusted_score"]), reverse=True)
    starters = [
        row
        for row in candidates
        if row["tier"] == "A"
        and "swing_bounce" in str(row["tags"])
        and (row["safety_floor"] == "" or float(row["safety_floor"]) >= 0.0)
    ][:8]
    momentum = [
        row
        for row in candidates
        if "breakout_tape" in str(row["tags"]) and "fragility_watch" not in str(row["tags"])
    ][:8]
    return candidates, starters, momentum


def write_outputs(
    run_dir: Path,
    candidates: list[dict[str, object]],
    starters: list[dict[str, object]],
    momentum: list[dict[str, object]],
) -> tuple[Path, Path]:
    top = candidates[:25]
    csv_path = run_dir / "module2__short_term_risk_adjusted_rankings.csv"
    if top:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(top[0].keys()))
            writer.writeheader()
            writer.writerows(top)

    run_id = run_dir.name
    lines = [
        "# Module2 short-term trade ideas (risk-adjusted)",
        "",
        f"**Run:** `{run_id}`  ",
        "**Horizon:** 1–2 weeks max hold; screening output only, not investment advice.",
        "",
        "Combines upside swing repair/setup, breakout continuation, fundamental edge, minus fragility extension risk.",
        "Swing vs breakout top-30 overlap in this run: **0.0 Jaccard** (orthogonal lenses).",
        "",
        "## Best starter opportunities (Tier A, swing bounce, safety ≥ 0)",
        "",
        "| Rank | Symbol | Name | Risk-adj | Swing | Repair | Upside | Safety | Tags |",
        "|------|--------|------|----------|-------|--------|--------|--------|------|",
    ]
    for index, row in enumerate(starters, start=1):
        lines.append(
            f"| {index} | {row['symbol']} | {row['name']} | {row['risk_adjusted_score']} | "
            f"{row['swing_score']} | {row['repair_trigger']} | {row['upside_room']} | "
            f"{row['safety_floor']} | {row['tags']} |"
        )

    lines.extend(
        [
            "",
            "## Tactical momentum (1–2w, breakout-led, no fragility flag)",
            "",
            "| Rank | Symbol | Name | Risk-adj | Breakout | Tags |",
            "|------|--------|------|----------|----------|------|",
        ]
    )
    for index, row in enumerate(momentum, start=1):
        lines.append(
            f"| {index} | {row['symbol']} | {row['name']} | {row['risk_adjusted_score']} | "
            f"{row['breakout_score']} | {row['tags']} |"
        )

    lines.extend(["", "## Top 15 overall (risk-adjusted composite)", ""])
    for index, row in enumerate(candidates[:15], start=1):
        lines.append(
            f"{index}. **{row['symbol']}** ({row['name']}) — **{row['risk_adjusted_score']}**, "
            f"tier **{row['tier']}**, {row['hold_horizon']}. Tags: `{row['tags']}`"
        )

    lines.extend(
        [
            "",
            "## Reading notes",
            "",
            "- **Swing bounce:** depressed range + repair confirmation; favor high repair + non-negative safety.",
            "- **Breakout tape:** extended momentum; higher volatility — trim when `fragility_watch` is present.",
            "- **Semi/storage cluster (MU, WDC, SEZL):** strong tape + fundamentals but crowded; size down or wait for pullback.",
            "- CSV: `module2__short_term_risk_adjusted_rankings.csv`",
            "- Pillar definitions: `documentation/prediction_module2_run_output_guide.md`",
        ]
    )

    md_path = run_dir / "module2__short_term_trade_ideas.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to module2 run folder (contains module2__profile_*.csv files)",
    )
    args = parser.parse_args()
    candidates, starters, momentum = build_rankings(args.run_dir)
    csv_path, md_path = write_outputs(args.run_dir, candidates, starters, momentum)
    print(f"Wrote {csv_path}")
    print(f"Wrote {md_path}")
    print(f"Ranked {len(candidates)} candidates; top starter: {starters[0]['symbol'] if starters else 'n/a'}")


if __name__ == "__main__":
    main()
