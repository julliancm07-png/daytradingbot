from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from daytradingbot.core.config import FeeSettings
from daytradingbot.core.models import FillRecord, MarketEvent, OrderRecord, q_qty, q_usd


class BrokerTimeoutError(RuntimeError):
    """Raised when an order submission times out and execution status is ambiguous."""


@dataclass
class BrokerSubmitResult:
    status: str  # "FILLED" or "REJECTED"
    fill: Optional[FillRecord] = None
    error: Optional[str] = None


class PaperBroker:
    """Simulates realistic paper fills; live order submission is intentionally absent."""

    def __init__(self, fee_settings: FeeSettings) -> None:
        self.fees = fee_settings
        self._remote_state: dict[str, BrokerSubmitResult] = {}
        self.submit_call_count: int = 0
        self.simulate_reject_next: Optional[str] = None
        self.simulate_ambiguous_filled_next: bool = False

    def submit_order(self, order: OrderRecord, event: MarketEvent) -> BrokerSubmitResult:
        if order.mode == "LIVE":
            raise PermissionError("Live execution is technically disabled in PaperBroker.")

        self.submit_call_count += 1

        # Remote broker idempotency check
        if order.idempotency_key in self._remote_state:
            return self._remote_state[order.idempotency_key]

        if self.simulate_reject_next:
            err = self.simulate_reject_next
            self.simulate_reject_next = None
            res = BrokerSubmitResult(status="REJECTED", error=err)
            self._remote_state[order.idempotency_key] = res
            return res

        fill = self._build_simulated_fill(order, event)
        res = BrokerSubmitResult(status="FILLED", fill=fill)
        self._remote_state[order.idempotency_key] = res

        if self.simulate_ambiguous_filled_next:
            # Simulates a real-world network drop AFTER the broker already filled the order.
            self.simulate_ambiguous_filled_next = False
            raise BrokerTimeoutError("Simulated network timeout after remote fill")

        return res

    def fetch_order_status(self, idempotency_key: str) -> BrokerSubmitResult:
        if idempotency_key in self._remote_state:
            return self._remote_state[idempotency_key]
        return BrokerSubmitResult(status="REJECTED", error="NOT_FOUND_AT_BROKER")

    def _build_simulated_fill(self, order: OrderRecord, event: MarketEvent) -> FillRecord:
        notional_est = (
            order.requested_notional_usd
            if order.side == "BUY"
            else q_usd(order.requested_qty * event.bid)
        )
        vol = max(event.volume_24h_usd, Decimal("1"))
        impact_bps = q_usd((notional_est / vol) * self.fees.impact_factor_bps)
        slippage_bps = self.fees.base_slippage_bps
        total_bps = slippage_bps + impact_bps

        if order.side == "BUY":
            fill_price = q_usd(event.ask * (Decimal("1") + total_bps / Decimal("10000")))
            qty = q_qty(order.requested_notional_usd / fill_price)
        else:
            fill_price = q_usd(event.bid * (Decimal("1") - total_bps / Decimal("10000")))
            qty = q_qty(order.requested_qty)

        gross_notional = q_usd(qty * fill_price)
        fee_usd = self._calculate_fee(order.asset_class, order.side, qty, gross_notional)

        return FillRecord(
            fill_id=f"FILL-{order.order_id}",
            order_id=order.order_id,
            idempotency_key=order.idempotency_key,
            symbol=order.symbol,
            side=order.side,
            qty=qty,
            fill_price=fill_price,
            gross_notional_usd=gross_notional,
            fee_usd=fee_usd,
            slippage_bps=slippage_bps,
            impact_bps=impact_bps,
            mode=order.mode,
            filled_at=event.received_time,
        )

    def _calculate_fee(
        self, asset_class: str, side: str, qty: Decimal, gross_notional: Decimal
    ) -> Decimal:
        if asset_class == "crypto":
            return q_usd(gross_notional * self.fees.crypto_taker_fee_rate)
        # US Stocks: $0 commission; SEC + FINRA CAT regulatory fees apply on sells
        if side == "SELL":
            sec_fee = gross_notional * self.fees.stock_sec_fee_rate
            cat_fee = qty * self.fees.stock_finra_cat_per_share
            return q_usd(sec_fee + cat_fee)
        return Decimal("0.0000")
