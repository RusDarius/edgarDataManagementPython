# === AI GENERATED CODE START (Cursor Grok 4.5) ===
# Generated on: 2026-07-30
# Model: Cursor Grok 4.5
# Purpose: XTB-style CFD position sizing / deployment planner (long & short)
#          covering margin, exposure, spread, swap, SL/TP, and account buffers.

"""
XTB CFD position deployment planner
===================================

Models how xStation market-order tickets behave for stock / index / FX CFDs and
turns risk inputs into a concrete trade plan (volume, margin lock, SL/TP in all
ticket units, cost-aware break-even, and account-health checks).

Why this exists
---------------
On XTB, P&L is marked on **full notional exposure**, while capital locked is only
the **margin** (= notional / leverage). Ignoring that asymmetry, overnight swaps,
spread entry drag, or free-margin buffers is how accounts get margin-called even
when "risk %" looked fine.

Core XTB mechanics encoded here
-------------------------------
1. **Contract / notional value**
   ``notional = volume * entry_price * contract_size``
   For most XTB **stock CFDs**, ``contract_size = 1`` (1 volume unit ≈ 1 share).
   Always confirm on the instrument specification / ticket (Contract value).

2. **Margin (collateral, not a fee)**
   ``required_margin = notional / leverage``
   Equivalent: ``margin_rate = 1 / leverage`` (e.g. leverage 4 → 25% margin;
   ESMA retail share CFDs are often capped near 1:5 / 20%, but **use the ticket** —
   screenshots for NBIS.US show ~1:4).

3. **Direction / fill side**
   - **Sell (short)**: opens at **bid**; closing requires buying (ask or stop fill).
   - **Buy (long)**: opens at **ask**; closing requires selling (bid or stop fill).
   You start underwater by roughly the **spread** immediately.

4. **Spread cost**
   ``spread_price = ask - bid``
   ``spread_cash = spread_price * volume * contract_size``
   Ticket also shows spread in **pips** where ``1 pip = pip_size``
   (USD-priced shares commonly ``pip_size = 0.01`` → 0.81 price = 81 pips).

5. **Overnight financing (swap)**
   Charged/credited around end-of-day rollover for open positions.
   Rates are **instrument- and direction-specific** and change; pass
   ``swap_avg`` as your working daily estimate (from the ticket "Daily Swap"
   row for your direction, scaled to **per 1 volume unit**).
   Sign convention in this module: **negative = cost**, **positive = credit**.
   Triple-swap days (often Wed for FX, sometimes Fri for equities to cover
   weekend) can be modelled explicitly or folded into ``swap_avg``.

6. **Stop loss / take profit (xStation fields)**
   The ticket binds four linked views of the same level:
   - Price
   - pips (distance / ``pip_size``)
   - cash P&L at that price (before swaps/commission; we also report net)
   - % price move vs entry

7. **Account health**
   ``margin_level % = equity / used_margin * 100``
   Brokers warn near a margin-call threshold and auto-liquidate near **stop-out**
   (often ~50% for retail — **confirm your entity**). This planner projects
   post-trade used margin, free margin, and margin level **at open** and at
   **SL mark** (worst planned case before gap risk).

Disclaimer
----------
Educational sizing aid only. XTB quotes, swaps, margin rates, volume steps,
stop execution, and corporate-action handling can differ by entity, platform
(xStation vs MT4), and moment. Always reconcile against the live ticket /
specification tables before sending an order. CFDs can lose more than the
margin posted (subject to negative-balance protection rules on your account).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from math import floor, isfinite
from typing import Any, Literal, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class CfdDirection(str, Enum):
    """Trade direction matching XTB Sell / Buy buttons."""

    SHORT = "short"  # Sell CFD
    LONG = "long"  # Buy CFD

    @classmethod
    def parse(cls, value: str | "CfdDirection") -> "CfdDirection":
        if isinstance(value, CfdDirection):
            return value
        key = str(value).strip().lower()
        aliases = {
            "short": cls.SHORT,
            "sell": cls.SHORT,
            "s": cls.SHORT,
            "long": cls.LONG,
            "buy": cls.LONG,
            "b": cls.LONG,
        }
        if key not in aliases:
            raise ValueError(
                f"direction must be one of {sorted(set(aliases))}, got {value!r}"
            )
        return aliases[key]


SizingMode = Literal[
    "fixed_volume",
    "risk_fraction",
    "risk_cash",
    "margin_budget",
]


# ---------------------------------------------------------------------------
# Helpers (pips / rounding / validation)
# ---------------------------------------------------------------------------


def pips_from_price(price_distance: float, pip_size: float) -> float:
    """Convert a raw price distance into XTB-style pips."""
    if pip_size <= 0:
        raise ValueError("pip_size must be > 0")
    return price_distance / pip_size


def price_from_pips(pips: float, pip_size: float) -> float:
    """Convert pips into a raw price distance."""
    if pip_size <= 0:
        raise ValueError("pip_size must be > 0")
    return pips * pip_size


def _require_finite(name: str, value: float) -> float:
    if not isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return value


def _round_down_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    # Avoid float drift for common steps like 0.01 / 1.0
    units = floor((value + 1e-12) / step)
    return max(units * step, 0.0)


def _round_money(value: float, decimals: int = 2) -> float:
    return round(value, decimals)


def _round_volume(value: float, decimals: int = 4) -> float:
    return round(value, decimals)


# ---------------------------------------------------------------------------
# Request / result models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CfdPositionRequest:
    """
    Inputs for :func:`plan_cfd_position`.

    Required economic inputs
    ------------------------
    direction:
        ``\"short\"`` / ``\"sell\"`` or ``\"long\"`` / ``\"buy\"``.
    leverage:
        Broker leverage for the instrument (e.g. ``4`` for 1:4). Prefer the
        implied ratio from the ticket: ``contract_value / margin``.
    duration_days:
        Planned holding horizon in calendar days. Used for swap accrual
        estimates and cost-aware break-even / net R:R.
    swap_avg:
        **Average daily overnight financing per 1.0 volume unit**, in
        ``instrument_currency`` (or already converted to account currency if
        you pass FX separately). Sign: **negative = charge**, **positive =
        credit**. Derive from the ticket as::

            swap_avg = ticket_daily_swap_for_direction / ticket_volume

        Example (NBIS.US Sell, volume 30, Daily Swap Sell = -0.14)::

            swap_avg = -0.14 / 30  # ≈ -0.004667 per unit per night

        Because XTB can reprice swaps, keep refreshing this average.

    Prices / spread
    ---------------
    Provide either:
      - ``bid`` + ``ask`` (preferred; mirrors the Sell/Buy buttons), or
      - ``entry_price`` + ``spread_price`` (or ``spread_pips``).

    Stops
    -----
    Provide stop-loss / take-profit as **any one** of:
      - absolute ``stop_loss_price`` / ``take_profit_price``, or
      - distance in price / pips / cash / % of entry
        (``stop_loss_*`` / ``take_profit_*`` fields).

    Sizing
    ------
    ``sizing_mode``:
      - ``fixed_volume``: use ``volume``
      - ``risk_fraction``: size so planned SL loss ≈ ``account_equity * risk_fraction``
      - ``risk_cash``: size so planned SL loss ≈ ``risk_cash``
      - ``margin_budget``: size so required margin ≤ ``max_margin``

    Account / safety
    ----------------
    ``account_equity``, ``used_margin_existing``, margin-call / stop-out %,
    and ``max_margin_usage_fraction`` guardrails are highly recommended for
    live deployment decisions.
    """

    # --- core trade intent ---
    direction: str | CfdDirection
    leverage: float
    duration_days: float
    swap_avg: float

    # --- quotes (instrument currency) ---
    bid: Optional[float] = None
    ask: Optional[float] = None
    entry_price: Optional[float] = None
    spread_price: Optional[float] = None
    spread_pips: Optional[float] = None

    # --- instrument meta ---
    symbol: str = ""
    instrument_currency: str = "USD"
    contract_size: float = 1.0
    pip_size: float = 0.01
    min_volume: float = 1.0
    volume_step: float = 1.0
    max_volume: Optional[float] = None

    # --- sizing ---
    sizing_mode: SizingMode = "risk_fraction"
    volume: Optional[float] = None
    account_equity: Optional[float] = None
    risk_fraction: float = 0.01
    risk_cash: Optional[float] = None
    max_margin: Optional[float] = None
    used_margin_existing: float = 0.0
    free_cash_buffer: float = 0.0

    # --- SL / TP (supply any representation; price wins if set) ---
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    stop_loss_price_distance: Optional[float] = None
    take_profit_price_distance: Optional[float] = None
    stop_loss_pips: Optional[float] = None
    take_profit_pips: Optional[float] = None
    stop_loss_cash: Optional[float] = None
    take_profit_cash: Optional[float] = None
    stop_loss_pct: Optional[float] = None  # % of entry price, e.g. 2.5 for 2.5%
    take_profit_pct: Optional[float] = None

    # --- costs beyond swap ---
    commission_rate: float = 0.0  # fraction of notional per side (open+close both applied)
    commission_flat_per_side: float = 0.0
    fx_to_account: float = 1.0  # multiply instrument-currency cashflows → account ccy

    # --- overnight model ---
    overnight_nights: Optional[float] = None
    # If True, estimate nights from duration with weekend + one triple-swap day/week.
    apply_triple_swap_model: bool = False
    triple_swap_multiplier: float = 3.0
    trading_nights_per_week: float = 5.0
    # Extra stress on swap_avg for planning (1.0 = as given; 1.2 = +20% cost stress)
    swap_stress_multiplier: float = 1.0

    # --- account protection thresholds (confirm for your XTB entity) ---
    margin_call_level_pct: float = 100.0
    stop_out_level_pct: float = 50.0
    # Soft cap: refuse / warn if this trade alone would use more than this
    # fraction of equity as margin (common heuristic 0.10–0.25).
    max_margin_usage_fraction: float = 0.25
    # Require free margin after open ≥ this multiple of required margin.
    min_free_margin_multiple: float = 1.0
    # Extra adverse slippage (price units) assumed on SL fill for risk sizing.
    stop_slippage_price: float = 0.0

    # --- misc ---
    notes: str = ""
    include_spread_in_risk: bool = True
    include_expected_swap_in_risk: bool = True
    include_commission_in_risk: bool = True


@dataclass(frozen=True)
class CfdLevelBreakdown:
    """One protective/target level expressed in all XTB ticket units."""

    price: float
    pips: float
    cash_gross: float
    cash_net_of_entry_costs: float
    pct_of_entry: float
    pct_of_margin: float
    pct_of_equity: Optional[float]


@dataclass(frozen=True)
class CfdPositionPlan:
    """Full deployment plan returned by :func:`plan_cfd_position`."""

    ok: bool
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    symbol: str
    direction: str
    sizing_mode: str

    # fill / exposure
    entry_price: float
    bid: float
    ask: float
    spread_price: float
    spread_pips: float
    volume: float
    contract_size: float
    notional: float
    leverage: float
    margin_rate: float
    required_margin: float

    # account projection (account currency after fx_to_account)
    account_equity: Optional[float]
    used_margin_after_open: Optional[float]
    free_margin_after_open: Optional[float]
    margin_level_after_open_pct: Optional[float]
    margin_level_at_stop_pct: Optional[float]

    # costs
    spread_cash: float
    commission_round_trip: float
    swap_avg_per_unit: float
    swap_per_night_position: float
    overnight_nights: float
    expected_swap_total: float
    total_holding_cost_estimate: float
    break_even_price: float
    break_even_pips: float
    break_even_move_pct: float

    # risk / reward
    stop_loss: Optional[CfdLevelBreakdown]
    take_profit: Optional[CfdLevelBreakdown]
    risk_cash_planned: Optional[float]
    reward_cash_planned: Optional[float]
    reward_risk_gross: Optional[float]
    reward_risk_net: Optional[float]
    risk_fraction_of_equity: Optional[float]
    risk_fraction_of_margin: Optional[float]

    # ticket-oriented quick fields
    ticket: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


# ---------------------------------------------------------------------------
# Internal resolution helpers
# ---------------------------------------------------------------------------


def _resolve_quotes(req: CfdPositionRequest) -> tuple[float, float, float, float]:
    """Return bid, ask, entry_price, spread_price."""
    pip_size = _require_finite("pip_size", req.pip_size)
    if pip_size <= 0:
        raise ValueError("pip_size must be > 0")

    bid = req.bid
    ask = req.ask
    spread_price = req.spread_price

    if spread_price is None and req.spread_pips is not None:
        spread_price = price_from_pips(_require_finite("spread_pips", req.spread_pips), pip_size)

    if bid is not None and ask is not None:
        bid = _require_finite("bid", bid)
        ask = _require_finite("ask", ask)
        if ask < bid:
            raise ValueError(f"ask ({ask}) must be >= bid ({bid})")
        if spread_price is None:
            spread_price = ask - bid
        elif abs((ask - bid) - spread_price) > max(1e-9, 1e-6 * max(ask, 1.0)):
            # Prefer explicit bid/ask; keep warning upstream via caller if needed.
            spread_price = ask - bid
    elif req.entry_price is not None:
        entry = _require_finite("entry_price", req.entry_price)
        if spread_price is None:
            spread_price = 0.0
        else:
            spread_price = _require_finite("spread_price", spread_price)
            if spread_price < 0:
                raise ValueError("spread_price must be >= 0")
        direction = CfdDirection.parse(req.direction)
        # Reconstruct a synthetic book around the intended entry side.
        if direction is CfdDirection.SHORT:
            bid = entry
            ask = entry + spread_price
        else:
            ask = entry
            bid = entry - spread_price
    else:
        raise ValueError(
            "Provide bid+ask, or entry_price with spread_price/spread_pips "
            "(mirrors XTB Sell/Buy buttons)."
        )

    assert bid is not None and ask is not None and spread_price is not None
    direction = CfdDirection.parse(req.direction)
    entry_price = bid if direction is CfdDirection.SHORT else ask
    return bid, ask, entry_price, float(spread_price)


def _estimate_overnight_nights(req: CfdPositionRequest) -> float:
    if req.overnight_nights is not None:
        nights = _require_finite("overnight_nights", req.overnight_nights)
        if nights < 0:
            raise ValueError("overnight_nights must be >= 0")
        return nights

    duration = _require_finite("duration_days", req.duration_days)
    if duration < 0:
        raise ValueError("duration_days must be >= 0")

    if not req.apply_triple_swap_model:
        # Conservative planning default: one financing event per calendar day held.
        # Intraday (duration < 1 and you exit before rollover) → use overnight_nights=0.
        return float(duration)

    # Approximate: for each full week, trading_nights_per_week financing events,
    # one of which is charged at triple_swap_multiplier (weekend coverage).
    weeks, rem_days = divmod(duration, 7.0)
    nights_per_week_effective = (
        (req.trading_nights_per_week - 1.0) * 1.0
        + 1.0 * req.triple_swap_multiplier
    )
    # Remaining partial week: assume rem_days calendar days ≈ that many 1x nights,
    # without forcing an extra triple (user can override overnight_nights).
    return float(weeks * nights_per_week_effective + rem_days)


def _distance_from_level_inputs(
    *,
    entry_price: float,
    direction: CfdDirection,
    volume: float,
    contract_size: float,
    pip_size: float,
    absolute_price: Optional[float],
    price_distance: Optional[float],
    pips: Optional[float],
    cash: Optional[float],
    pct: Optional[float],
    side: Literal["stop", "take"],
) -> Optional[float]:
    """
    Resolve a protective/target level into a **positive price distance** from entry.

    For stops, distance is adverse; for takes, distance is favourable.
    """
    exposures_per_price = volume * contract_size
    if absolute_price is not None:
        level = _require_finite(f"{side}_price", absolute_price)
        raw = level - entry_price
        if direction is CfdDirection.SHORT:
            # Short SL above entry (raw > 0); TP below entry (raw < 0).
            dist = raw if side == "stop" else -raw
        else:
            # Long SL below entry (raw < 0); TP above entry (raw > 0).
            dist = -raw if side == "stop" else raw
        if dist <= 0:
            raise ValueError(
                f"{side} price {level} is on the wrong side of entry {entry_price} "
                f"for direction={direction.value}"
            )
        return dist

    if price_distance is not None:
        dist = _require_finite(f"{side}_price_distance", price_distance)
        if dist <= 0:
            raise ValueError(f"{side}_price_distance must be > 0")
        return dist

    if pips is not None:
        pip_dist = _require_finite(f"{side}_pips", pips)
        if pip_dist <= 0:
            raise ValueError(f"{side}_pips must be > 0")
        return price_from_pips(pip_dist, pip_size)

    if pct is not None:
        pct_v = _require_finite(f"{side}_pct", pct)
        if pct_v <= 0:
            raise ValueError(f"{side}_pct must be > 0 (percent of entry, e.g. 2.5)")
        return entry_price * (pct_v / 100.0)

    if cash is not None:
        cash_v = abs(_require_finite(f"{side}_cash", cash))
        if cash_v <= 0:
            raise ValueError(f"{side}_cash must be != 0")
        if exposures_per_price <= 0:
            raise ValueError("volume/contract_size must be > 0 to invert cash levels")
        return cash_v / exposures_per_price

    return None


def _level_price(
    entry_price: float,
    direction: CfdDirection,
    distance: float,
    side: Literal["stop", "take"],
) -> float:
    if direction is CfdDirection.SHORT:
        return entry_price + distance if side == "stop" else entry_price - distance
    return entry_price - distance if side == "stop" else entry_price + distance


def _build_level(
    *,
    entry_price: float,
    direction: CfdDirection,
    distance: float,
    volume: float,
    contract_size: float,
    pip_size: float,
    required_margin: float,
    account_equity: Optional[float],
    entry_cost_cash: float,
    side: Literal["stop", "take"],
) -> CfdLevelBreakdown:
    price = _level_price(entry_price, direction, distance, side)
    gross = distance * volume * contract_size
    # At SL, gross is a loss; entry costs (spread already embedded in fill side,
    # plus commission/swap budget) deepen the net loss. At TP, entry costs reduce net win.
    if side == "stop":
        net = -(gross) - entry_cost_cash
        cash_gross = -gross
    else:
        net = gross - entry_cost_cash
        cash_gross = gross

    pct_entry = (distance / entry_price) * 100.0 if entry_price else 0.0
    pct_margin = (abs(net) / required_margin) * 100.0 if required_margin else 0.0
    pct_equity = (
        (abs(net) / account_equity) * 100.0 if account_equity not in (None, 0) else None
    )
    return CfdLevelBreakdown(
        price=_round_money(price, 4),
        pips=_round_money(pips_from_price(distance, pip_size), 2),
        cash_gross=_round_money(cash_gross),
        cash_net_of_entry_costs=_round_money(net),
        pct_of_entry=_round_money(pct_entry, 4),
        pct_of_margin=_round_money(pct_margin, 4),
        pct_of_equity=None if pct_equity is None else _round_money(pct_equity, 4),
    )


def _round_trip_commission(notional: float, req: CfdPositionRequest) -> float:
    rate = max(_require_finite("commission_rate", req.commission_rate), 0.0)
    flat = max(
        _require_finite("commission_flat_per_side", req.commission_flat_per_side), 0.0
    )
    # Open + close
    return (2.0 * rate * notional) + (2.0 * flat)


def _adverse_pnl_cash(
    *,
    direction: CfdDirection,
    entry_price: float,
    mark_price: float,
    volume: float,
    contract_size: float,
) -> float:
    """Cash P&L (instrument ccy) at mark, before costs. Negative = loss."""
    if direction is CfdDirection.SHORT:
        return (entry_price - mark_price) * volume * contract_size
    return (mark_price - entry_price) * volume * contract_size


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------


# === AI GENERATED FUNCTION (Cursor Grok 4.5) - 2026-07-30 ===
def plan_cfd_position(request: CfdPositionRequest | Mapping[str, Any]) -> CfdPositionPlan:
    """
    Plan an XTB-style CFD long/short deployment from risk and ticket parameters.

    AI Generated Function: CFD position sizing + cost-aware SL/TP / margin plan
    Generated by Cursor Grok 4.5 on 2026-07-30

    Parameters
    ----------
    request:
        :class:`CfdPositionRequest` or an equivalent mapping. See that dataclass
        for the full parameter reference. Minimum useful set for a short::

            plan_cfd_position(CfdPositionRequest(
                direction=\"short\",
                leverage=4,
                duration_days=5,
                swap_avg=-0.004667,          # ticket Sell swap / volume
                bid=187.21,
                ask=188.02,
                account_equity=10_000,
                risk_fraction=0.01,          # risk ~1% equity to SL
                stop_loss_pct=3.0,           # 3% adverse price move
                take_profit_pct=6.0,         # 6% favourable (gross ~2R before costs)
                symbol=\"NBIS.US\",
            ))

    Returns
    -------
    CfdPositionPlan
        Structured plan with ``ok`` / ``warnings`` / ``errors``, ticket-ready
        volume, margin, SL/TP in price·pips·cash·%, break-even after costs,
        and projected margin level at open and at stop.

    Notes on sensitivity
    --------------------
    - **Leverage amplifies equity drawdown vs price move**: a 5% adverse move
      on 1:4 leverage is ~20% of posted margin (before costs).
    - **Spread is immediate negative expectancy**; shorts open at bid, so TP must
      clear spread + swaps + commission, not just the chart mid move.
    - **Swaps accrue on notional**, not on margin. Long stock CFDs often pay
      more than shorts (as in the NBIS ticket: Buy ≈ -1.27 vs Sell ≈ -0.14 at
      vol 30) — horizon matters.
    - **Stops do not remove gap risk** (earnings, halt, overnight gap through SL).
      ``stop_slippage_price`` stresses SL distance for sizing.
    - **Margin level** can collapse from floating loss even if SL is \"far\" if
      other positions already consume free margin — pass ``used_margin_existing``.
    - Re-check live **Daily Swap**, **Margin**, and **Spread** on the ticket
      immediately before order entry; feed refreshed ``swap_avg`` / quotes.

    Usage (fixed volume evaluation)
    -------------------------------
    ::

        plan = plan_cfd_position(dict(
            direction=\"sell\",
            leverage=4,
            duration_days=3,
            swap_avg=-0.14 / 30,
            bid=187.21,
            ask=188.02,
            sizing_mode=\"fixed_volume\",
            volume=30,
            stop_loss_pips=500,
            take_profit_pips=800,
            account_equity=8000,
        ))
        print(plan.ticket)
    """

    req = (
        request
        if isinstance(request, CfdPositionRequest)
        else CfdPositionRequest(**dict(request))
    )

    warnings: list[str] = []
    errors: list[str] = []

    try:
        direction = CfdDirection.parse(req.direction)
        leverage = _require_finite("leverage", req.leverage)
        if leverage <= 0:
            raise ValueError("leverage must be > 0")
        contract_size = _require_finite("contract_size", req.contract_size)
        if contract_size <= 0:
            raise ValueError("contract_size must be > 0")
        pip_size = _require_finite("pip_size", req.pip_size)
        if pip_size <= 0:
            raise ValueError("pip_size must be > 0")
        fx = _require_finite("fx_to_account", req.fx_to_account)
        if fx <= 0:
            raise ValueError("fx_to_account must be > 0")

        bid, ask, entry_price, spread_price = _resolve_quotes(req)
        overnight_nights = _estimate_overnight_nights(req)
        swap_avg = _require_finite("swap_avg", req.swap_avg) * _require_finite(
            "swap_stress_multiplier", req.swap_stress_multiplier
        )

        margin_rate = 1.0 / leverage

        # --- provisional distances (volume-independent forms first) ---
        # Cash-based SL/TP need volume; resolved in a second pass when needed.
        # Cash-based SL/TP need volume; resolve those after sizing.
        cash_only_stop = (
            req.stop_loss_cash is not None
            and req.stop_loss_price is None
            and req.stop_loss_price_distance is None
            and req.stop_loss_pips is None
            and req.stop_loss_pct is None
        )
        cash_only_take = (
            req.take_profit_cash is not None
            and req.take_profit_price is None
            and req.take_profit_price_distance is None
            and req.take_profit_pips is None
            and req.take_profit_pct is None
        )

        stop_dist = None
        if not cash_only_stop:
            stop_dist = _distance_from_level_inputs(
                entry_price=entry_price,
                direction=direction,
                volume=1.0,
                contract_size=contract_size,
                pip_size=pip_size,
                absolute_price=req.stop_loss_price,
                price_distance=req.stop_loss_price_distance,
                pips=req.stop_loss_pips,
                cash=None,
                pct=req.stop_loss_pct,
                side="stop",
            )

        take_dist = None
        if not cash_only_take:
            take_dist = _distance_from_level_inputs(
                entry_price=entry_price,
                direction=direction,
                volume=1.0,
                contract_size=contract_size,
                pip_size=pip_size,
                absolute_price=req.take_profit_price,
                price_distance=req.take_profit_price_distance,
                pips=req.take_profit_pips,
                cash=None,
                pct=req.take_profit_pct,
                side="take",
            )

        stop_slip = max(_require_finite("stop_slippage_price", req.stop_slippage_price), 0.0)
        effective_stop_dist = None if stop_dist is None else stop_dist + stop_slip

        def costs_for_volume(vol: float) -> tuple[float, float, float, float, float]:
            notional_i = vol * entry_price * contract_size
            spread_cash_i = spread_price * vol * contract_size
            commission_i = _round_trip_commission(notional_i, req)
            swap_night_i = swap_avg * vol
            swap_total_i = swap_night_i * overnight_nights
            # Holding cost used against profits / added to risk: charges are negative swaps.
            # Convert \"cost\" to a positive drain when swap_total is negative.
            swap_cost_i = -swap_total_i  # positive number means money leaving account
            entry_cost_for_risk = 0.0
            if req.include_spread_in_risk:
                # Bid/ask entry already embeds half-spread vs mid; still, closing
                # through spread / immediate MTM is modelled as full quoted spread
                # cash impact unless the user disables this.
                entry_cost_for_risk += spread_cash_i
            if req.include_commission_in_risk:
                entry_cost_for_risk += commission_i
            if req.include_expected_swap_in_risk:
                entry_cost_for_risk += max(swap_cost_i, 0.0)
            return (
                notional_i,
                spread_cash_i,
                commission_i,
                swap_night_i,
                entry_cost_for_risk,
            )

        def risk_cash_at(vol: float, dist: float) -> float:
            _, _, _, _, entry_cost = costs_for_volume(vol)
            return dist * vol * contract_size + entry_cost

        # --- resolve volume ---
        mode: SizingMode = req.sizing_mode  # type: ignore[assignment]
        volume: float

        if mode == "fixed_volume":
            if req.volume is None:
                raise ValueError("sizing_mode='fixed_volume' requires volume")
            volume = _require_finite("volume", req.volume)

        elif mode in ("risk_fraction", "risk_cash"):
            if mode == "risk_fraction":
                if req.account_equity is None:
                    raise ValueError("risk_fraction sizing requires account_equity")
                equity = _require_finite("account_equity", req.account_equity)
                if equity <= 0:
                    raise ValueError("account_equity must be > 0")
                rf = _require_finite("risk_fraction", req.risk_fraction)
                if rf <= 0 or rf > 1:
                    raise ValueError("risk_fraction must be in (0, 1]")
                target_risk = equity * rf
            else:
                if req.risk_cash is None:
                    raise ValueError("risk_cash sizing requires risk_cash")
                target_risk = abs(_require_finite("risk_cash", req.risk_cash))
                if target_risk <= 0:
                    raise ValueError("risk_cash must be > 0")

            if cash_only_stop:
                # stop_loss_cash is the gross adverse cash at SL for the eventual volume;
                # invert after we know cost structure iteratively.
                raise ValueError(
                    "stop_loss_cash cannot be used alone for risk sizing; "
                    "provide stop_loss_price / pips / pct / price_distance"
                )
            if effective_stop_dist is None:
                raise ValueError(
                    "Risk sizing requires a stop-loss distance "
                    "(stop_loss_price, stop_loss_pips, stop_loss_pct, or "
                    "stop_loss_price_distance)."
                )

            # Solve volume from:
            #   target_risk = dist*vol*contract_size + optional costs(vol)
            # Costs linear in volume ⇒ closed form.
            dist = effective_stop_dist
            unit_gross = dist * contract_size
            unit_spread = spread_price * contract_size if req.include_spread_in_risk else 0.0
            unit_comm = (
                (2.0 * max(req.commission_rate, 0.0) * entry_price * contract_size)
                if req.include_commission_in_risk
                else 0.0
            )
            # flat commission is NOT linear — apply after first solve if present
            unit_swap_cost = 0.0
            if req.include_expected_swap_in_risk:
                unit_swap_cost = max(-(swap_avg * overnight_nights), 0.0)

            denom = unit_gross + unit_spread + unit_comm + unit_swap_cost
            if denom <= 0:
                raise ValueError("Invalid stop/cost combination; denom <= 0")
            volume = target_risk / denom

            if req.include_commission_in_risk and req.commission_flat_per_side:
                # Adjust for flat open+close: target = linear*vol + 2*flat
                flat_rt = 2.0 * req.commission_flat_per_side
                volume = max(target_risk - flat_rt, 0.0) / denom

        elif mode == "margin_budget":
            budget = req.max_margin
            if budget is None:
                if req.account_equity is None:
                    raise ValueError(
                        "margin_budget sizing requires max_margin or account_equity"
                    )
                equity = _require_finite("account_equity", req.account_equity)
                budget = equity * _require_finite(
                    "max_margin_usage_fraction", req.max_margin_usage_fraction
                )
            budget = _require_finite("max_margin", float(budget))
            if budget <= 0:
                raise ValueError("max_margin must be > 0")
            # margin = vol * entry * contract_size / leverage
            volume = (budget * leverage) / (entry_price * contract_size)

        else:
            raise ValueError(f"Unknown sizing_mode: {mode!r}")

        # clamp / step
        if volume < 0:
            volume = 0.0
        volume = _round_down_to_step(volume, req.volume_step)
        if volume < req.min_volume:
            warnings.append(
                f"Computed volume {volume} < min_volume {req.min_volume}; "
                f"clamping to min_volume (risk will exceed target if risk-sized)."
            )
            volume = req.min_volume
        if req.max_volume is not None and volume > req.max_volume:
            warnings.append(
                f"Computed volume {volume} > max_volume {req.max_volume}; clamping."
            )
            volume = req.max_volume
        volume = _round_volume(volume)

        if volume <= 0:
            raise ValueError("Resolved volume is 0 — check risk/stop/margin inputs")

        # Resolve cash-only distances now that volume is known
        if cash_only_stop:
            stop_dist = _distance_from_level_inputs(
                entry_price=entry_price,
                direction=direction,
                volume=volume,
                contract_size=contract_size,
                pip_size=pip_size,
                absolute_price=None,
                price_distance=None,
                pips=None,
                cash=req.stop_loss_cash,
                pct=None,
                side="stop",
            )
            effective_stop_dist = None if stop_dist is None else stop_dist + stop_slip
        if cash_only_take:
            take_dist = _distance_from_level_inputs(
                entry_price=entry_price,
                direction=direction,
                volume=volume,
                contract_size=contract_size,
                pip_size=pip_size,
                absolute_price=None,
                price_distance=None,
                pips=None,
                cash=req.take_profit_cash,
                pct=None,
                side="take",
            )

        notional, spread_cash, commission_rt, swap_night, entry_cost_risk = costs_for_volume(
            volume
        )
        required_margin = notional / leverage
        swap_total = swap_night * overnight_nights
        swap_cost = -swap_total
        holding_cost = (
            (spread_cash if req.include_spread_in_risk else 0.0)
            + (commission_rt if req.include_commission_in_risk else 0.0)
            + max(swap_cost, 0.0)
        )

        # Break-even: price move (favourable) needed to offset entry_cost_risk
        # Note: if spread is included in risk costs AND entry already uses bid/ask,
        # this is slightly conservative (spread counted once as cash hurdle).
        be_distance = entry_cost_risk / (volume * contract_size) if volume else 0.0
        if direction is CfdDirection.SHORT:
            break_even_price = entry_price - be_distance
        else:
            break_even_price = entry_price + be_distance

        stop_level = None
        take_level = None
        if effective_stop_dist is not None:
            stop_level = _build_level(
                entry_price=entry_price,
                direction=direction,
                distance=effective_stop_dist,
                volume=volume,
                contract_size=contract_size,
                pip_size=pip_size,
                required_margin=required_margin,
                account_equity=req.account_equity,
                entry_cost_cash=entry_cost_risk,
                side="stop",
            )
        elif stop_dist is not None:
            stop_level = _build_level(
                entry_price=entry_price,
                direction=direction,
                distance=stop_dist,
                volume=volume,
                contract_size=contract_size,
                pip_size=pip_size,
                required_margin=required_margin,
                account_equity=req.account_equity,
                entry_cost_cash=entry_cost_risk,
                side="stop",
            )

        if take_dist is not None:
            take_level = _build_level(
                entry_price=entry_price,
                direction=direction,
                distance=take_dist,
                volume=volume,
                contract_size=contract_size,
                pip_size=pip_size,
                required_margin=required_margin,
                account_equity=req.account_equity,
                entry_cost_cash=entry_cost_risk,
                side="take",
            )

        risk_cash_planned = None if stop_level is None else abs(stop_level.cash_net_of_entry_costs)
        reward_cash_planned = (
            None if take_level is None else take_level.cash_net_of_entry_costs
        )
        rr_gross = None
        rr_net = None
        if stop_level is not None and take_level is not None:
            gross_risk = abs(stop_level.cash_gross)
            gross_reward = abs(take_level.cash_gross)
            rr_gross = None if gross_risk == 0 else gross_reward / gross_risk
            net_risk = abs(stop_level.cash_net_of_entry_costs)
            net_reward = take_level.cash_net_of_entry_costs
            rr_net = None if net_risk == 0 else net_reward / net_risk
            if net_reward <= 0:
                warnings.append(
                    "Net reward at TP <= 0 after spread/commission/swap — "
                    "TP too tight for the planned holding period."
                )

        # --- account projections ---
        equity = req.account_equity
        used_after = None
        free_after = None
        ml_open = None
        ml_stop = None
        if equity is not None:
            equity = _require_finite("account_equity", equity)
            used_existing = max(
                _require_finite("used_margin_existing", req.used_margin_existing), 0.0
            )
            used_after = used_existing + required_margin * fx
            free_after = equity - used_after - max(req.free_cash_buffer, 0.0)
            ml_open = None if used_after == 0 else (equity / used_after) * 100.0

            if stop_level is not None:
                # Equity after floating loss at SL (instrument PnL converted).
                equity_at_stop = equity + stop_level.cash_net_of_entry_costs * fx
                ml_stop = (
                    None
                    if used_after == 0
                    else (equity_at_stop / used_after) * 100.0
                )
                if ml_stop is not None and ml_stop <= req.stop_out_level_pct:
                    warnings.append(
                        f"Projected margin level at SL ({ml_stop:.1f}%) <= "
                        f"stop-out threshold ({req.stop_out_level_pct}%). "
                        "Reduce volume or widen free margin."
                    )
                elif ml_stop is not None and ml_stop <= req.margin_call_level_pct:
                    warnings.append(
                        f"Projected margin level at SL ({ml_stop:.1f}%) <= "
                        f"margin-call threshold ({req.margin_call_level_pct}%)."
                    )

            if free_after is not None and free_after < required_margin * fx * req.min_free_margin_multiple:
                warnings.append(
                    "Free margin after open is below min_free_margin_multiple * "
                    "required_margin — little buffer for adverse movement."
                )

            margin_usage = (required_margin * fx) / equity if equity else 0.0
            if margin_usage > req.max_margin_usage_fraction:
                warnings.append(
                    f"Trade margin uses {margin_usage:.1%} of equity, above "
                    f"max_margin_usage_fraction={req.max_margin_usage_fraction:.1%}."
                )

            if ml_open is not None and ml_open < 200.0:
                warnings.append(
                    f"Margin level after open is {ml_open:.1f}% "
                    "(many desks prefer staying well above 200–300%)."
                )

        # Direction-specific education warnings
        if direction is CfdDirection.SHORT and swap_avg > 0:
            warnings.append(
                "swap_avg > 0 on a short means an expected overnight credit; "
                "verify against the live Sell Daily Swap row."
            )
        if direction is CfdDirection.LONG and swap_avg < 0 and overnight_nights >= 3:
            warnings.append(
                "Long stock CFDs often carry heavier negative swaps; "
                f"{overnight_nights:.1f} nights * swap may dominate edge."
            )
        if spread_price > entry_price * 0.01:
            warnings.append(
                "Spread > 1% of price — unusual for liquid names; "
                "check session hours / liquidity (your NBIS examples showed "
                "wide 80–120 pip spreads in volatile prints)."
            )
        if req.duration_days >= 1 and overnight_nights == 0:
            warnings.append(
                "duration_days >= 1 but overnight_nights resolved to 0 — "
                "swap estimate will understate holding cost."
            )

        # Convert headline cash metrics to account currency where relevant
        def acc(x: float) -> float:
            return _round_money(x * fx)

        ticket = {
            "symbol": req.symbol,
            "direction": direction.value,
            "side_button": "Sell" if direction is CfdDirection.SHORT else "Buy",
            "volume": volume,
            "entry_price": _round_money(entry_price, 4),
            "contract_value": acc(notional),
            "margin": acc(required_margin),
            "leverage": f"1:{_round_money(leverage, 4)}",
            "spread_cash": acc(spread_cash),
            "spread_pips": _round_money(pips_from_price(spread_price, pip_size), 2),
            "commission_round_trip": acc(commission_rt),
            "daily_swap_position": acc(swap_night),
            "expected_swap_over_horizon": acc(swap_total),
            "currency": req.instrument_currency,
            "stop_loss": None
            if stop_level is None
            else {
                "price": stop_level.price,
                "pips": stop_level.pips,
                "cash": acc(stop_level.cash_net_of_entry_costs),
                "pct_price": stop_level.pct_of_entry,
            },
            "take_profit": None
            if take_level is None
            else {
                "price": take_level.price,
                "pips": take_level.pips,
                "cash": acc(take_level.cash_net_of_entry_costs),
                "pct_price": take_level.pct_of_entry,
            },
            "break_even_price": _round_money(break_even_price, 4),
            "notes": req.notes,
        }

        risk_frac_eq = None
        risk_frac_margin = None
        if risk_cash_planned is not None:
            if equity not in (None, 0):
                risk_frac_eq = risk_cash_planned * fx / equity
            if required_margin:
                risk_frac_margin = risk_cash_planned / required_margin

        ok = len(errors) == 0 and volume > 0
        # Treat critical margin insufficiency as hard error when equity known
        if equity is not None and free_after is not None and free_after < 0:
            errors.append(
                "Insufficient free margin to open this position with current equity / "
                "used_margin_existing / free_cash_buffer."
            )
            ok = False

        return CfdPositionPlan(
            ok=ok,
            warnings=tuple(warnings),
            errors=tuple(errors),
            symbol=req.symbol,
            direction=direction.value,
            sizing_mode=mode,
            entry_price=_round_money(entry_price, 4),
            bid=_round_money(bid, 4),
            ask=_round_money(ask, 4),
            spread_price=_round_money(spread_price, 4),
            spread_pips=_round_money(pips_from_price(spread_price, pip_size), 2),
            volume=volume,
            contract_size=contract_size,
            notional=acc(notional),
            leverage=leverage,
            margin_rate=_round_money(margin_rate, 6),
            required_margin=acc(required_margin),
            account_equity=None if equity is None else _round_money(equity),
            used_margin_after_open=None if used_after is None else _round_money(used_after),
            free_margin_after_open=None if free_after is None else _round_money(free_after),
            margin_level_after_open_pct=(
                None if ml_open is None else _round_money(ml_open, 2)
            ),
            margin_level_at_stop_pct=(
                None if ml_stop is None else _round_money(ml_stop, 2)
            ),
            spread_cash=acc(spread_cash),
            commission_round_trip=acc(commission_rt),
            swap_avg_per_unit=_round_money(swap_avg, 8),
            swap_per_night_position=acc(swap_night),
            overnight_nights=_round_money(overnight_nights, 4),
            expected_swap_total=acc(swap_total),
            total_holding_cost_estimate=acc(holding_cost),
            break_even_price=_round_money(break_even_price, 4),
            break_even_pips=_round_money(pips_from_price(be_distance, pip_size), 2),
            break_even_move_pct=_round_money(
                (be_distance / entry_price) * 100.0 if entry_price else 0.0, 4
            ),
            stop_loss=stop_level,
            take_profit=take_level,
            risk_cash_planned=None if risk_cash_planned is None else acc(risk_cash_planned),
            reward_cash_planned=(
                None if reward_cash_planned is None else acc(reward_cash_planned)
            ),
            reward_risk_gross=None if rr_gross is None else _round_money(rr_gross, 4),
            reward_risk_net=None if rr_net is None else _round_money(rr_net, 4),
            risk_fraction_of_equity=(
                None if risk_frac_eq is None else _round_money(risk_frac_eq, 6)
            ),
            risk_fraction_of_margin=(
                None if risk_frac_margin is None else _round_money(risk_frac_margin, 6)
            ),
            ticket=ticket,
            diagnostics={
                "include_spread_in_risk": req.include_spread_in_risk,
                "include_expected_swap_in_risk": req.include_expected_swap_in_risk,
                "include_commission_in_risk": req.include_commission_in_risk,
                "apply_triple_swap_model": req.apply_triple_swap_model,
                "stop_slippage_price": stop_slip,
                "fx_to_account": fx,
                "margin_call_level_pct": req.margin_call_level_pct,
                "stop_out_level_pct": req.stop_out_level_pct,
                "raw_notional_instrument_ccy": _round_money(notional),
                "raw_margin_instrument_ccy": _round_money(required_margin),
            },
        )

    except Exception as exc:  # noqa: BLE001 - surface as structured plan errors
        errors.append(str(exc))
        empty_ticket: dict[str, Any] = {}
        return CfdPositionPlan(
            ok=False,
            warnings=tuple(warnings),
            errors=tuple(errors),
            symbol=getattr(req, "symbol", ""),
            direction=str(getattr(req, "direction", "")),
            sizing_mode=str(getattr(req, "sizing_mode", "")),
            entry_price=0.0,
            bid=0.0,
            ask=0.0,
            spread_price=0.0,
            spread_pips=0.0,
            volume=0.0,
            contract_size=getattr(req, "contract_size", 1.0),
            notional=0.0,
            leverage=float(getattr(req, "leverage", 0.0) or 0.0),
            margin_rate=0.0,
            required_margin=0.0,
            account_equity=getattr(req, "account_equity", None),
            used_margin_after_open=None,
            free_margin_after_open=None,
            margin_level_after_open_pct=None,
            margin_level_at_stop_pct=None,
            spread_cash=0.0,
            commission_round_trip=0.0,
            swap_avg_per_unit=float(getattr(req, "swap_avg", 0.0) or 0.0),
            swap_per_night_position=0.0,
            overnight_nights=0.0,
            expected_swap_total=0.0,
            total_holding_cost_estimate=0.0,
            break_even_price=0.0,
            break_even_pips=0.0,
            break_even_move_pct=0.0,
            stop_loss=None,
            take_profit=None,
            risk_cash_planned=None,
            reward_cash_planned=None,
            reward_risk_gross=None,
            reward_risk_net=None,
            risk_fraction_of_equity=None,
            risk_fraction_of_margin=None,
            ticket=empty_ticket,
            diagnostics={"exception_type": type(exc).__name__},
        )


def summarize_plan(plan: CfdPositionPlan) -> str:
    """Human-readable multi-line summary for logs / notebooks."""
    lines = [
        f"[{'OK' if plan.ok else 'BLOCKED'}] {plan.symbol or '(no symbol)'} "
        f"{plan.direction.upper()} vol={plan.volume}",
        f"  entry={plan.entry_price}  bid/ask={plan.bid}/{plan.ask}  "
        f"spread={plan.spread_pips} pips ({plan.spread_cash} ccy)",
        f"  notional={plan.notional}  margin={plan.required_margin}  "
        f"leverage=1:{plan.leverage}",
        f"  nights={plan.overnight_nights}  swap/night={plan.swap_per_night_position}  "
        f"swap_horizon={plan.expected_swap_total}",
        f"  break-even={plan.break_even_price} ({plan.break_even_pips} pips, "
        f"{plan.break_even_move_pct}%)",
    ]
    if plan.stop_loss:
        sl = plan.stop_loss
        lines.append(
            f"  SL price={sl.price}  pips={sl.pips}  cash~={sl.cash_net_of_entry_costs}  "
            f"%price={sl.pct_of_entry}"
        )
    if plan.take_profit:
        tp = plan.take_profit
        lines.append(
            f"  TP price={tp.price}  pips={tp.pips}  cash~={tp.cash_net_of_entry_costs}  "
            f"%price={tp.pct_of_entry}"
        )
    if plan.reward_risk_net is not None:
        lines.append(
            f"  R:R gross={plan.reward_risk_gross}  R:R net={plan.reward_risk_net}"
        )
    if plan.margin_level_after_open_pct is not None:
        lines.append(
            f"  margin_level open={plan.margin_level_after_open_pct}%  "
            f"at_SL={plan.margin_level_at_stop_pct}%"
        )
    for w in plan.warnings:
        lines.append(f"  WARN: {w}")
    for e in plan.errors:
        lines.append(f"  ERR: {e}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience: mirror an xStation ticket snapshot (e.g. from screenshots)
# ---------------------------------------------------------------------------


def swap_avg_from_ticket(daily_swap_for_direction: float, ticket_volume: float) -> float:
    """
    Convert the xStation \"Daily Swap\" row into ``swap_avg`` (per 1.0 volume).

    Example from NBIS.US Sell ticket: daily_swap=-0.14, volume=30
    → ``swap_avg_from_ticket(-0.14, 30)``.
    """
    ticket_volume = _require_finite("ticket_volume", ticket_volume)
    if ticket_volume == 0:
        raise ValueError("ticket_volume must be != 0")
    return _require_finite("daily_swap_for_direction", daily_swap_for_direction) / ticket_volume


def leverage_from_ticket(contract_value: float, margin: float) -> float:
    """Infer leverage from ticket Contract value / Margin (e.g. 5616.30 / 1404.08 ≈ 4)."""
    margin = _require_finite("margin", margin)
    if margin == 0:
        raise ValueError("margin must be != 0")
    return _require_finite("contract_value", contract_value) / margin


if __name__ == "__main__":
    # Worked example aligned with the NBIS.US Sell ticket screenshot.
    demo = plan_cfd_position(
        CfdPositionRequest(
            symbol="NBIS.US",
            direction="short",
            leverage=leverage_from_ticket(5616.30, 1404.08),
            duration_days=5,
            swap_avg=swap_avg_from_ticket(-0.14, 30),
            bid=187.21,
            ask=188.02,
            sizing_mode="fixed_volume",
            volume=30,
            account_equity=8_000,
            used_margin_existing=0.0,
            stop_loss_pct=4.0,
            take_profit_pct=8.0,
            pip_size=0.01,
            notes="Demo: reconcile against live xStation ticket before ordering.",
        )
    )
    print(summarize_plan(demo))

# === AI GENERATED CODE END ===
