"""Alpha model — walk-forward, self-correcting excess-return forecast with bands (generic_utils.alpha_model).

How to run (from the repo root; PYTHONPATH=src;. is set by .claude/settings.json):

    python src/run_alpha_model.py                          # update the panel store, fit walk-forward, forecast today -> PACK/alpha_model
    python src/run_alpha_model.py --ids RMBS,PWR,MU        # + one line per named id (forecasts, deciles, bands, drivers)
    python src/run_alpha_model.py --page                   # + data-only page into logs/canvases/<day>/<HHMM>_ide/
    python src/run_alpha_model.py --horizons 7d,28d        # fewer horizons
    python src/run_alpha_model.py --pack none --no-log     # stamped run folder, no live-log entry

The daily refresh runs it as the optional suite ``alpha_model`` (after earnings_week, so the calendar
joins) and page-all renders the ``alpha_model`` template. Every run appends today's forecasts to
logs/tradingview_analysis/alpha_model/live_log.duckdb and scores the logged ones whose labels exist.

Method in short (module docstring has the full story): each feature's daily rank IC against the later
excess return; a forecast day weights features by their trailing IC from days whose labels were known by
then, decayed by age and shrunk by their own t; the composite rank times its own slope gives pred_<h> in
percent excess. Out-of-sample record, walk-forward baselines, calibration deciles and the state
(OK / WEAK / DEGRADED / FAILING / NO_TRACK) come with every run.

Knobs: config/generic_utils/alpha_model.json (features, fit, baselines, bands, joins) on top of
config/generic_utils/panel_store.json. Skill: .claude/skills/alpha-model/SKILL.md.
Template: config/generic_utils/pages/alpha_model.json.
"""

from __future__ import annotations

import argparse
from typing import Any, Sequence

from generic_utils.tv_scan_cli import main as tv_main
from run_signal_lab import render_page


def run_alpha_model(*, pack: str = "latest", ids: str | None = None, page: bool = False,
                    extra: Sequence[str] = ()) -> dict[str, Any]:
    argv = ["alpha-model", "--pack", pack, "--line", *(["--ids", ids] if ids else []), *extra]
    result = tv_main(argv)
    if page and pack != "none":
        result["page_run"] = render_page("alpha_model", pack=pack, origin="run_alpha_model")
        print(f"page run {result['page_run']}")
    return result


def run_alpha_model_local_main() -> dict[str, Any]:
    """No-arg defaults: update the store, forecast beside the newest pack, log today, print the digest."""
    return run_alpha_model(pack="latest", page=False)


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Alpha model: walk-forward excess-return forecast with bands.")
    parser.add_argument("--pack", default="latest", help="Pack folder, 'latest', or 'none' for a stamped run folder.")
    parser.add_argument("--ids", default=None, help="Comma ids for one line each (bare tickers resolve).")
    parser.add_argument("--horizons", default=None, help="Comma horizon names (default the recipe's).")
    parser.add_argument("--no-update", action="store_true", help="Read the panel store as it is.")
    parser.add_argument("--no-log", action="store_true", help="Do not append today's forecasts to the live log.")
    parser.add_argument("--page", action="store_true", help="Also render the data-only page.")
    args = parser.parse_args(argv)
    extra = [*(["--horizons", args.horizons] if args.horizons else []), *(["--no-update"] if args.no_update else []),
             *(["--no-log"] if args.no_log else [])]
    return run_alpha_model(pack=args.pack, ids=args.ids, page=args.page, extra=extra)


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 1:
        run_alpha_model_local_main()
    else:
        main()
