"""Signal lab — backward evidence on the all-fields day history (generic_utils.signal_lab).

How to run (from the repo root; PYTHONPATH=src;. is set by .claude/settings.json):

    python src/run_signal_lab.py                         # update the panel store, all three studies -> PACK/signal_lab, digest
    python src/run_signal_lab.py --page                  # + data-only page into logs/canvases/<day>/<HHMM>_ide/
    python src/run_signal_lab.py --studies prints        # one study (factor, prints, scans)
    python src/run_signal_lab.py --pack none             # stamped run folder under logs/tradingview_analysis/signal_lab/runs/
    python src/run_signal_lab.py --first-day 2026-07-01  # a sub-period (regime check)

The daily refresh runs it as the optional suite ``signal_lab`` (after alpha_model, reading the store
the panel_store producer updated) and page-all renders the ``signal_lab`` template.

Data flow: all-fields day files + move-prediction runs -> panel store
(logs/tradingview_analysis/signal_lab/panel_store.duckdb: panel, pred, series, prints, labels, features;
generic_utils.panel_store) -> studies (generic_utils.signal_lab) -> PACK/signal_lab/signal_lab.duckdb
(factor_summary, factor_days, factor_month, feature_corr, print_events, print_cells, scan_summary,
scan_days, scan_obs, scan_packs, market), queries.txt, overview.log, highlights.log.

Knobs: config/generic_utils/panel_store.json (fields, floor, horizons, features as SQL) and
config/generic_utils/signal_lab.json (study floor, verdict t bands, print cells, pack lists).
Skill: .claude/skills/signal-lab/SKILL.md. Template: config/generic_utils/pages/signal_lab.json.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any, Sequence

from generic_utils.tv_scan_cli import main as tv_main


def render_page(template: str, *, pack: str = "latest", origin: str) -> str:
    """Open an ide run folder for today and render the data-only page of ``template`` into it."""
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    run = tv_main(["page-run", "--day", day, "--mode", "ide", "--pack", pack, "--origin", origin])["run_dir"]
    tv_main(["page", "--template", template, "--day", day, "--pack", pack, "--manual", "--out-dir", run, "--quiet"])
    return run


def run_signal_lab(*, pack: str = "latest", studies: str | None = None, page: bool = False,
                   extra: Sequence[str] = ()) -> dict[str, Any]:
    argv = ["signal-lab", "--pack", pack, "--line", *(["--studies", studies] if studies else []), *extra]
    result = tv_main(argv)
    if page and pack != "none":
        result["page_run"] = render_page("signal_lab", pack=pack, origin="run_signal_lab")
        print(f"page run {result['page_run']}")
    return result


def run_signal_lab_local_main() -> dict[str, Any]:
    """No-arg defaults: update the store, every study beside the newest pack, print the digest."""
    return run_signal_lab(pack="latest", page=False)


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Signal lab: feature IC, print event study, pack list returns.")
    parser.add_argument("--pack", default="latest", help="Pack folder, 'latest', or 'none' for a stamped run folder.")
    parser.add_argument("--studies", default=None, help="Comma of factor,prints,scans.")
    parser.add_argument("--first-day", default=None)
    parser.add_argument("--last-day", default=None)
    parser.add_argument("--no-update", action="store_true", help="Read the panel store as it is.")
    parser.add_argument("--page", action="store_true", help="Also render the data-only page.")
    args = parser.parse_args(argv)
    extra = [*(["--first-day", args.first_day] if args.first_day else []),
             *(["--last-day", args.last_day] if args.last_day else []), *(["--no-update"] if args.no_update else [])]
    return run_signal_lab(pack=args.pack, studies=args.studies, page=args.page, extra=extra)


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 1:
        run_signal_lab_local_main()
    else:
        main()
