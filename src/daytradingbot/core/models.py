from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional

from pydantic import BaseModel, Field

AssetClass = Literal["crypto", "stock"]
OrderSide = Literal["BUY", "SELL"]
RunMode = Literal["REPLAY", "PAPER", "LIVE"]


def q_usd(value: Decimal) -> Decimal:
    """Round USD values consistently to 4 decimal places."""
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def q_qty(value: Decimal) -> Decimal:
    """Round share/coin quantities to 8 decimal places."""
    return value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


class MarketEvent(BaseModel):
    event_id: str
    symbol: str
    asset_class: AssetClass
    event_time: datetime
    received_time: datetime
    bid: Decimal
    ask: Decimal
    bar_open: Decimal
    bar_high: Decimal
    bar_low: Decimal
    bar_close: Decimal
    volume_24h_usd: Decimal
    source_status: Literal["ok", "stale", "disconnected"] = "ok"


class TradeProposal(BaseModel):
    event_id: str
    symbol: str
    asset_class: AssetClass
    action: Literal["BUY", "NO_TRADE"]
    strategy_name: str
    reason: str
    ref_price: Decimal = Decimal("0")
    notional_usd: Decimal = Decimal("0")
    stop_loss_price: Decimal = Decimal("0")
    take_profit_price: Decimal = Decimal("0")
    max_hold_seconds: int = 0


class RiskVerdict(BaseModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    spread_bps: Decimal = Decimal("0")
    estimated_risk_usd: Decimal = Decimal("0")


class OrderRecord(BaseModel):
    idempotency_key: str
    order_id: str
    symbol: str
    asset_class: AssetClass
    side: OrderSide
    requested_notional_usd: Decimal
    requested_qty: Decimal = Decimal("0")
    status: Literal["PENDING_SUBMIT", "FILLED", "REJECTED", "AMBIGUOUS"]
    mode: RunMode
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    stop_loss_price: Decimal = Decimal("0")
    take_profit_price: Decimal = Decimal("0")
    max_hold_until: Optional[datetime] = None
    position_id: Optional[str] = None
    exit_reason: Optional[str] = None


class FillRecord(BaseModel):
    fill_id: str
    order_id: str
    idempotency_key: str
    symbol: str
    side: OrderSide
    qty: Decimal
    fill_price: Decimal
    gross_notional_usd: Decimal
    fee_usd: Decimal
    slippage_bps: Decimal
    impact_bps: Decimal
    mode: RunMode
    filled_at: datetime


class PositionRecord(BaseModel):
    position_id: str
    symbol: str
    asset_class: AssetClass
    status: Literal["OPEN", "CLOSED"]
    qty: Decimal
    entry_price: Decimal
    entry_notional_usd: Decimal
    entry_fee_usd: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    max_hold_until: datetime
    opened_at: datetime
    closed_at: Optional[datetime] = None
    exit_price: Optional[Decimal] = None
    exit_fee_usd: Optional[Decimal] = None
    realized_pnl_usd: Optional[Decimal] = None
    exit_reason: Optional[str] = None
    feed_health: Literal["OK", "STALE_OR_DISCONNECTED"] = "OK"


class EquitySnapshot(BaseModel):
    ts: datetime
    mode: RunMode
    cash_usd: Decimal
    open_market_value_usd: Decimal
    total_equity_usd: Decimal
    peak_equity_usd: Decimal
    drawdown_pct: Decimal
    cumulative_fees_usd: Decimal
    monthly_api_spend_usd: Decimal
    net_equity_after_api_usd: Decimal
