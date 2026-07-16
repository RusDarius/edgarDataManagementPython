"""
CLI for point-in-time opportunity deep-dives across edge + all-fields datasets.

Example:
  PYTHONPATH=src;. python -m tools.run_edge_opportunity_deep_dive \\
    --edge-parent logs/.../edge_latest_500m_full_parent_... \\
    --all-fields logs/.../tradingview_all_fields_....duckdb \\
    --conviction logs/.../move_prediction__conviction_rankings.csv \\
    --symbols OMXCOP:ZEAL,NASDAQ:ABUS \\
    --out logs/.../adhoc_analysis/deep_dive.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.edge_opportunity_deep_dive import DeepDivePaths, merge_name_deep_dive, write_deep_dive_json


def _load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def _unwrap_config(payload: dict | list, key: str | None = None):
    """Accept either a bare map/list or a wrapper config with symbols/thesis/stance."""
    if key is None:
        return payload
    if isinstance(payload, dict) and key in payload and isinstance(payload[key], (dict, list)):
        return payload[key]
    return payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Edge opportunity deep-dive (PIT fundamentals + scores)")
    p.add_argument("--edge-parent", type=Path, required=True)
    p.add_argument("--all-fields", type=Path, required=True)
    p.add_argument("--conviction", type=Path, default=None)
    p.add_argument("--symbols", type=str, default=None, help="Comma-separated symbols")
    p.add_argument("--symbols-file", type=Path, default=None, help="JSON list, lines file, or config JSON")
    p.add_argument("--config", type=Path, default=None, help="Config JSON with symbols/thesis/stance")
    p.add_argument("--thesis-file", type=Path, default=None, help="Optional JSON map symbol->thesis")
    p.add_argument("--stance-file", type=Path, default=None, help="Optional JSON map symbol->stance")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--scan-day", type=str, default=None)
    args = p.parse_args(argv)

    config = _load_json(args.config) if args.config else None
    if config is not None and not isinstance(config, dict):
        raise SystemExit("--config must be a JSON object")

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.config:
        symbols = list(config.get("symbols") or [])
    elif args.symbols_file:
        payload = _load_json(args.symbols_file) if args.symbols_file.suffix.lower() == ".json" else None
        if payload is None:
            text = args.symbols_file.read_text(encoding="utf-8")
            symbols = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
        elif isinstance(payload, list):
            symbols = list(payload)
        elif isinstance(payload, dict) and "symbols" in payload:
            symbols = list(payload["symbols"])
        else:
            raise SystemExit("symbols JSON must be a list or {symbols:[...]}")
    else:
        raise SystemExit("Provide --symbols, --symbols-file, or --config")

    if args.thesis_file:
        thesis = _unwrap_config(_load_json(args.thesis_file), "thesis")
    elif config:
        thesis = config.get("thesis")
    else:
        thesis = None

    if args.stance_file:
        stance = _unwrap_config(_load_json(args.stance_file), "stance")
    elif config:
        stance = config.get("stance")
    else:
        stance = None

    if thesis is not None and not isinstance(thesis, dict):
        raise SystemExit("thesis must be a symbol->text map")
    if stance is not None and not isinstance(stance, dict):
        raise SystemExit("stance must be a symbol->stance map")

    paths = DeepDivePaths(
        edge_parent=args.edge_parent,
        all_fields_db=args.all_fields,
        conviction_csv=args.conviction,
    )
    rows = merge_name_deep_dive(symbols, paths, thesis_overrides=thesis, stance_overrides=stance)
    meta = {
        "scan_day": args.scan_day or (config.get("scan_day") if config else None),
        "edge_parent": str(args.edge_parent),
        "all_fields": str(args.all_fields),
        "conviction": str(args.conviction) if args.conviction else None,
        "n_names": len(rows),
    }
    write_deep_dive_json(rows, args.out, meta=meta)
    print(f"Wrote {args.out} ({len(rows)} names)")
    for r in rows:
        print(
            f"  #{r['allocation_rank']:2d} {r['stance']:20s} {r['symbol']:16s} "
            f"alloc={r['allocation_score']:5.1f} risk={r['risk_tier']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
