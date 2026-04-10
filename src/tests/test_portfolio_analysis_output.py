from datetime import datetime

from pathlib import Path
from tempfile import TemporaryDirectory

from portofolio_integration_analysis.portfolio_analysis_output import (
    write_portfolio_analysis_files,
)
from portofolio_integration_analysis.portfolio_metrics import (
    PortfolioPositionRecord,
    build_portfolio_summary,
)


def test_write_portfolio_analysis_files_creates_log_and_csv():
    positions = [
        PortfolioPositionRecord(
            portfolio_item_id=1,
            portfolio_id=4,
            portfolio_name="Output Test",
            company_internal_id=101,
            symbol="AAA",
            company_name="Alpha Inc.",
            shares_held=10,
            average_entry_price=100.0,
            cost_basis_total=1000.0,
            realized_pnl=20.0,
            last_price=110.0,
            last_perf_w=3.0,
            last_perf_1m=8.0,
            last_perf_y=25.0,
            opened_at=datetime(2025, 1, 1),
        )
    ]

    summary, metrics = build_portfolio_summary(positions, as_of=datetime(2026, 1, 1))

    with TemporaryDirectory() as temp_dir:
        paths = write_portfolio_analysis_files(
            portfolio_name="Output Test",
            summary=summary,
            position_metrics=metrics,
            output_dir=temp_dir,
        )

        log_text = Path(paths["log"]).read_text(encoding="utf-8")
        csv_text = Path(paths["csv"]).read_text(encoding="utf-8-sig")

        assert Path(paths["log"]).exists()
        assert Path(paths["csv"]).exists()
        assert "Portfolio Analysis | Output Test" in log_text
        assert "AAA" in log_text
        assert "AAA" in csv_text
        assert "symbol,company_name" in csv_text
        assert "holding_days" in csv_text
        assert "Annualized total return" in log_text
