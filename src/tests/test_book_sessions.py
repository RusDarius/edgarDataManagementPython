from pathlib import Path

from generic_utils.capture import build_holdings_sessions


def _write_run(root: Path, day: str, tickers: list[str]) -> None:
    folder = root / f"holdings_scoring_{day}_1200_utc_abcd" / "scans" / "move_prediction_v1"
    folder.mkdir(parents=True)
    lines = ["symbol,ticker,invested_sum_usd,current_value_usd"]
    for ticker in tickers:
        lines.append(f"NASDAQ:{ticker},{ticker},5000,5200")
    (folder / "holdings__summary.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_six_month_window_keeps_exits_and_current_names(tmp_path: Path) -> None:
    _write_run(tmp_path, "20260413", ["AAA", "BBB"])
    _write_run(tmp_path, "20261013", ["AAA"])
    payload = build_holdings_sessions(lookback_days=200, holdings_runs_root=tmp_path)
    by_ticker = {row["ticker"]: row for row in payload["book_names"]}
    assert payload["n_runs"] == 2
    assert by_ticker["AAA"]["still_held"] is True
    assert by_ticker["AAA"]["n_asofs"] == 2
    assert by_ticker["BBB"]["still_held"] is False
    assert any(row["ticker"] == "BBB" for row in payload["exits"])
