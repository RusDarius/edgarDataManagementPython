from __future__ import annotations

from datetime import datetime
from typing import Any

from db.portfolio_management_operations import (
    apply_buy_transaction,
    apply_sell_transaction,
    create_or_get_portfolio,
    get_portfolio_by_id,
    get_portfolio_by_name,
    get_portfolio_position,
    get_portfolio_positions,
    get_portfolio_transactions,
    list_portfolios,
    update_position_market_snapshot,
    update_position_plan,
)
from db.trading_view_company_data_map_operations import (
    get_trading_view_company_by_internal_id,
    search_trading_view_companies,
)
from portofolio_integration_analysis.portfolio_metrics import (
    PortfolioPositionRecord,
    build_portfolio_summary,
)


class PortfolioTracker:
    def __init__(
        self,
        portfolio_name: str | None = None,
        portfolio_id: int | None = None,
        create_if_missing: bool = True,
        description: str | None = None,
        currency: str = "USD",
        benchmark_symbol: str | None = None,
        notes: str | None = None,
    ) -> None:
        if portfolio_id is None and not portfolio_name:
            raise ValueError("portfolio_name or portfolio_id must be provided")

        portfolio_row = None
        if portfolio_id is not None:
            portfolio_row = get_portfolio_by_id(portfolio_id)
        elif portfolio_name is not None:
            portfolio_row = get_portfolio_by_name(portfolio_name)

        if portfolio_row is None and create_if_missing and portfolio_name is not None:
            portfolio_row = create_or_get_portfolio(
                portfolio_name=portfolio_name,
                description=description,
                currency=currency,
                benchmark_symbol=benchmark_symbol,
                notes=notes,
            )

        if portfolio_row is None:
            raise ValueError("portfolio could not be resolved")

        self._portfolio_row = portfolio_row

    @property
    def portfolio_id(self) -> int:
        return int(self._portfolio_row["portfolio_id"])

    @property
    def portfolio_name(self) -> str:
        return str(self._portfolio_row["portfolio_name"])

    @property
    def portfolio_currency(self) -> str:
        return str(self._portfolio_row.get("currency") or "USD")

    @property
    def portfolio_row(self) -> dict[str, Any]:
        return dict(self._portfolio_row)

    def reload(self) -> dict[str, Any]:
        refreshed = get_portfolio_by_id(self.portfolio_id)
        if refreshed is None:
            raise ValueError("portfolio no longer exists in the database")
        self._portfolio_row = refreshed
        return self.portfolio_row

    @classmethod
    def from_portfolio_id(cls, portfolio_id: int) -> "PortfolioTracker":
        return cls(portfolio_id=portfolio_id, create_if_missing=False)

    @staticmethod
    def list_all() -> list[dict[str, Any]]:
        return list_portfolios()

    @staticmethod
    def search(query: str, limit: int = 25) -> list[dict[str, Any]]:
        return search_trading_view_companies(query=query, limit=limit)

    def _build_market_snapshot(
        self,
        company_internal_id: int,
        price: float | None = None,
        extra_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        company_row = get_trading_view_company_by_internal_id(company_internal_id)
        snapshot = {
            "market_cap_basic": None,
            "change_pct": None,
            "perf_w": None,
            "perf_1m": None,
            "perf_y": None,
        }
        if company_row is not None:
            snapshot.update(
                {
                    "market_cap_basic": company_row.get("market_cap_basic"),
                    "change_pct": company_row.get("change_pct"),
                    "perf_w": company_row.get("perf_w"),
                    "perf_1m": company_row.get("perf_1m"),
                    "perf_y": company_row.get("perf_y"),
                }
            )
        if extra_snapshot:
            snapshot.update(
                {
                    key: value
                    for key, value in extra_snapshot.items()
                    if value is not None
                }
            )
        if price is not None:
            snapshot["last_price"] = price
        return snapshot

    @staticmethod
    def _normalize_money_unit(unit: str | None) -> str:
        normalized_unit = (unit or "USD").strip().upper()
        if not normalized_unit:
            raise ValueError("unit must be a non-empty currency code")
        return normalized_unit

    def _resolve_portfolio_fx_rate(
        self,
        unit: str,
        fx_rate_to_portfolio: float | None,
    ) -> float:
        if unit == self.portfolio_currency.upper():
            return 1.0
        if fx_rate_to_portfolio is None or fx_rate_to_portfolio <= 0:
            raise ValueError(
                "fx_rate_to_portfolio is required when the money unit differs from the portfolio currency"
            )
        return fx_rate_to_portfolio

    def add_holding(
        self,
        internal_id: int,
        shares: float,
        price: float,
        fees: float = 0.0,
        notes: str | None = None,
        executed_at: datetime | None = None,
        market_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if get_trading_view_company_by_internal_id(internal_id) is None:
            raise ValueError(f"company internal_id={internal_id} was not found")

        return apply_buy_transaction(
            portfolio_id=self.portfolio_id,
            company_internal_id=internal_id,
            shares=shares,
            price=price,
            fees=fees,
            note=notes,
            executed_at=executed_at,
            quantity_value=shares,
            quantity_unit="SHARES",
            market_snapshot=self._build_market_snapshot(
                company_internal_id=internal_id,
                price=price,
                extra_snapshot=market_snapshot,
            ),
        )

    def add_holding_by_amount(
        self,
        internal_id: int,
        avg_price: float,
        date_open: datetime,
        amount_money: float,
        unit: str = "USD",
        fees: float = 0.0,
        fx_rate_to_portfolio: float | None = None,
        notes: str | None = None,
        market_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if get_trading_view_company_by_internal_id(internal_id) is None:
            raise ValueError(f"company internal_id={internal_id} was not found")
        if avg_price <= 0:
            raise ValueError("avg_price must be positive")
        if amount_money <= 0:
            raise ValueError("amount_money must be positive")

        normalized_unit = self._normalize_money_unit(unit)
        resolved_fx_rate = self._resolve_portfolio_fx_rate(
            unit=normalized_unit,
            fx_rate_to_portfolio=fx_rate_to_portfolio,
        )
        shares = amount_money / avg_price
        portfolio_price = avg_price * resolved_fx_rate

        return apply_buy_transaction(
            portfolio_id=self.portfolio_id,
            company_internal_id=internal_id,
            shares=shares,
            price=portfolio_price,
            fees=fees,
            note=notes,
            executed_at=date_open,
            quantity_value=amount_money,
            quantity_unit="MONEY",
            amount_currency=normalized_unit,
            fx_rate_to_portfolio=resolved_fx_rate,
            market_snapshot=self._build_market_snapshot(
                company_internal_id=internal_id,
                price=portfolio_price,
                extra_snapshot=market_snapshot,
            ),
        )

    def sell_holding(
        self,
        internal_id: int,
        shares: float,
        price: float,
        fees: float = 0.0,
        notes: str | None = None,
        executed_at: datetime | None = None,
        market_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return apply_sell_transaction(
            portfolio_id=self.portfolio_id,
            company_internal_id=internal_id,
            shares=shares,
            price=price,
            fees=fees,
            note=notes,
            executed_at=executed_at,
            market_snapshot=self._build_market_snapshot(
                company_internal_id=internal_id,
                price=price,
                extra_snapshot=market_snapshot,
            ),
        )

    def update_market_snapshot(
        self,
        internal_id: int,
        last_price: float | None = None,
        last_price_at: datetime | None = None,
        last_market_cap_basic: float | None = None,
        last_change_pct: float | None = None,
        last_perf_w: float | None = None,
        last_perf_1m: float | None = None,
        last_perf_y: float | None = None,
        note: str | None = None,
        create_transaction: bool = False,
    ) -> dict[str, Any]:
        return update_position_market_snapshot(
            portfolio_id=self.portfolio_id,
            company_internal_id=internal_id,
            last_price=last_price,
            last_price_at=last_price_at,
            last_market_cap_basic=last_market_cap_basic,
            last_change_pct=last_change_pct,
            last_perf_w=last_perf_w,
            last_perf_1m=last_perf_1m,
            last_perf_y=last_perf_y,
            note=note,
            create_transaction=create_transaction,
        )

    def update_position_plan(
        self,
        internal_id: int,
        target_weight_pct: float | None = None,
        risk_limit_pct: float | None = None,
        target_price: float | None = None,
        stop_loss_price: float | None = None,
        thesis: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        return update_position_plan(
            portfolio_id=self.portfolio_id,
            company_internal_id=internal_id,
            target_weight_pct=target_weight_pct,
            risk_limit_pct=risk_limit_pct,
            target_price=target_price,
            stop_loss_price=stop_loss_price,
            thesis=thesis,
            note=note,
        )

    def get_position(self, internal_id: int) -> dict[str, Any] | None:
        return get_portfolio_position(self.portfolio_id, internal_id)

    def list_positions(self, include_closed: bool = False) -> list[dict[str, Any]]:
        return get_portfolio_positions(self.portfolio_id, include_closed=include_closed)

    def get_transactions(
        self, company_internal_id: int | None = None
    ) -> list[dict[str, Any]]:
        return get_portfolio_transactions(
            portfolio_id=self.portfolio_id,
            company_internal_id=company_internal_id,
        )

    def _row_to_position_record(self, row: dict[str, Any]) -> PortfolioPositionRecord:
        return PortfolioPositionRecord(
            portfolio_item_id=row.get("portfolio_item_id"),
            portfolio_id=int(row["portfolio_id"]),
            portfolio_name=str(row["portfolio_name"]),
            company_internal_id=int(row["company_internal_id"]),
            symbol=str(row["symbol"]),
            company_name=str(row["company_name"]),
            shares_held=float(row.get("shares_held") or 0.0),
            average_entry_price=float(row.get("average_entry_price") or 0.0),
            cost_basis_total=float(row.get("cost_basis_total") or 0.0),
            realized_pnl=float(row.get("realized_pnl") or 0.0),
            fees_total=float(row.get("fees_total") or 0.0),
            last_price=(
                float(row["last_price"]) if row.get("last_price") is not None else None
            ),
            last_market_cap_basic=(
                float(row["last_market_cap_basic"])
                if row.get("last_market_cap_basic") is not None
                else None
            ),
            last_change_pct=(
                float(row["last_change_pct"])
                if row.get("last_change_pct") is not None
                else None
            ),
            last_perf_w=(
                float(row["last_perf_w"])
                if row.get("last_perf_w") is not None
                else None
            ),
            last_perf_1m=(
                float(row["last_perf_1m"])
                if row.get("last_perf_1m") is not None
                else None
            ),
            last_perf_y=(
                float(row["last_perf_y"])
                if row.get("last_perf_y") is not None
                else None
            ),
            opened_at=row.get("opened_at"),
            closed_at=row.get("closed_at"),
            exchange=row.get("exchange"),
            market=row.get("market"),
            currency=row.get("currency"),
            entry_currency=row.get("entry_currency"),
            entry_fx_to_portfolio=(
                float(row["entry_fx_to_portfolio"])
                if row.get("entry_fx_to_portfolio") is not None
                else None
            ),
            notes=row.get("notes"),
        )

    def get_position_records(
        self,
        include_closed: bool = False,
    ) -> list[PortfolioPositionRecord]:
        return [
            self._row_to_position_record(row)
            for row in self.list_positions(include_closed=include_closed)
        ]

    def get_summary(self, include_closed: bool = False):
        records = self.get_position_records(include_closed=include_closed)
        return build_portfolio_summary(records)

    def sync_market_snapshots(
        self,
        scan_data: list[dict[str, Any]],
        symbol_key: str = "symbol",
    ) -> dict[str, int]:
        positions = self.list_positions(include_closed=False)
        if not positions:
            return {"matched": 0, "updated": 0, "skipped": len(scan_data)}

        positions_by_symbol = {
            str(position["symbol"]).upper(): position for position in positions
        }

        def _candidate_symbols(row: dict[str, Any]) -> list[str]:
            raw_candidates = [
                row.get(symbol_key),
                row.get("ticker-view"),
                row.get("symbol"),
            ]
            normalized_candidates: list[str] = []
            for candidate in raw_candidates:
                candidate_text = str(candidate or "").upper()
                if not candidate_text:
                    continue
                normalized_candidates.append(candidate_text)
                if ":" in candidate_text:
                    normalized_candidates.append(candidate_text.split(":")[-1])
            return normalized_candidates

        matched = 0
        updated = 0
        for row in scan_data:
            matched_symbol = next(
                (
                    symbol
                    for symbol in _candidate_symbols(row)
                    if symbol in positions_by_symbol
                ),
                None,
            )
            if matched_symbol is None:
                continue

            matched += 1
            position = positions_by_symbol[matched_symbol]
            update_position_market_snapshot(
                portfolio_id=self.portfolio_id,
                company_internal_id=int(position["company_internal_id"]),
                last_price=row.get("close"),
                last_market_cap_basic=row.get("market_cap_basic"),
                last_change_pct=row.get("change_pct", row.get("change")),
                last_perf_w=row.get("perf_w", row.get("Perf.W")),
                last_perf_1m=row.get("perf_1m", row.get("Perf.1M")),
                last_perf_y=row.get("perf_y", row.get("Perf.Y")),
            )
            updated += 1

        return {
            "matched": matched,
            "updated": updated,
            "skipped": max(len(scan_data) - matched, 0),
        }

    def print_summary(self, include_closed: bool = False) -> None:
        summary, metrics = self.get_summary(include_closed=include_closed)
        print(f"Portfolio: {self.portfolio_name} (id={self.portfolio_id})")
        if summary is None:
            print("No positions found.")
            return

        print(f"Positions: {summary.positions_count}")
        print(f"Cost basis: {summary.total_cost_basis:,.2f}")
        if summary.average_holding_days is not None:
            print(f"Average holding days: {summary.average_holding_days:,.1f}")
        if summary.total_market_value is not None:
            print(f"Market value: {summary.total_market_value:,.2f}")
        if summary.total_pnl is not None:
            print(
                f"Total PnL: {summary.total_pnl:,.2f} ({summary.total_return_pct:.2f}%)"
            )
        if summary.annualized_total_return_pct is not None:
            print(f"Annualized return: {summary.annualized_total_return_pct:.2f}%")
        print(
            f"Price coverage: {summary.priced_positions_count}/{summary.positions_count}"
        )

        for metric in metrics:
            pnl_text = "N/A" if metric.total_pnl is None else f"{metric.total_pnl:,.2f}"
            mv_text = (
                "N/A" if metric.market_value is None else f"{metric.market_value:,.2f}"
            )
            holding_days_text = (
                "N/A" if metric.holding_days is None else f"{metric.holding_days:,.1f}"
            )
            annualized_text = (
                "N/A"
                if metric.annualized_return_pct is None
                else f"{metric.annualized_return_pct:,.2f}%"
            )
            print(
                f"  {metric.symbol:<10} shares={metric.shares_held:>10.4f} days={holding_days_text:>8} cost={metric.cost_basis_total:>12,.2f} market={mv_text:>12} pnl={pnl_text:>12} ann={annualized_text:>10}"
            )

    def export_analysis(
        self, output_dir: str | None = None, include_closed: bool = False
    ):
        from portofolio_integration_analysis.portfolio_analysis_output import (
            export_portfolio_analysis,
        )

        return export_portfolio_analysis(
            self,
            output_dir=output_dir,
            include_closed=include_closed,
        )
