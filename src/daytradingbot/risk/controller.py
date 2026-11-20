from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from daytradingbot.core.config import AppSettings
from daytradingbot.core.models import MarketEvent, PositionRecord, RiskVerdict, TradeProposal, q_usd


@dataclass
class PortfolioState:
    cash_usd: Decimal
    open_positions: list[PositionRecord]
    daily_realized_pnl_usd: Decimal
    monthly_api_spend_usd: Decimal
    paused: bool


class RiskController:
    """Non-overridable deterministic risk gate. Fails closed on missing/stale data."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def evaluate(
        self,
        proposal: TradeProposal,
        event: MarketEvent,
        portfolio: PortfolioState,
    ) -> RiskVerdict:
        reasons: list[str] = []
        r = self.settings.risk
        f = self.settings.fees

        if self.settings.mode == "LIVE":
            reasons.append("LIVE_MODE_TECHNICALLY_DISABLED")

        if portfolio.paused:
            reasons.append("EMERGENCY_PAUSED")

        if event.source_status != "ok":
            reasons.append(f"FEED_STATUS_{event.source_status.upper()}")

        quote_age = (event.received_time - event.event_time).total_seconds()
        if quote_age < 0 or quote_age > r.max_quote_age_seconds:
            reasons.append(f"STALE_MARKET_DATA_{quote_age:.1f}S")

        spread_bps = Decimal("0")
        if event.bid <= Decimal("0") or event.ask <= Decimal("0") or event.ask < event.bid:
            reasons.append("INVALID_QUOTE_PRICES")
        else:
            mid = (event.bid + event.ask) / Decimal("2")
            spread_bps = q_usd(((event.ask - event.bid) / mid) * Decimal("10000"))
            if spread_bps > r.max_spread_bps:
                reasons.append(f"SPREAD_TOO_WIDE_{spread_bps}_BPS")

        if event.volume_24h_usd < r.min_24h_volume_usd:
            reasons.append("INSUFFICIENT_LIQUIDITY")

        if portfolio.daily_realized_pnl_usd <= -r.daily_loss_limit_usd:
            reasons.append("DAILY_LOSS_LIMIT_HIT")

        if portfolio.monthly_api_spend_usd >= r.max_monthly_api_spend_usd:
            reasons.append("MONTHLY_API_SPEND_CAP_HIT")

        if len(portfolio.open_positions) >= r.max_concurrent_positions:
            reasons.append("MAX_CONCURRENT_POSITIONS_REACHED")

        if any(p.symbol == proposal.symbol for p in portfolio.open_positions):
            reasons.append("ALREADY_IN_SYMBOL")

        if proposal.notional_usd <= Decimal("0") or proposal.notional_usd > r.max_position_usd:
            reasons.append("MAX_POSITION_SIZE_EXCEEDED")

        fee_rate = f.crypto_taker_fee_rate if proposal.asset_class == "crypto" else Decimal("0")
        est_entry_fee = q_usd(proposal.notional_usd * fee_rate)
        if proposal.notional_usd + est_entry_fee > portfolio.cash_usd:
            reasons.append("INSUFFICIENT_CASH")

        current_exposure = sum(
            (p.entry_notional_usd for p in portfolio.open_positions), Decimal("0")
        )
        if current_exposure + proposal.notional_usd > r.max_total_exposure_usd:
            reasons.append("MAX_TOTAL_EXPOSURE_EXCEEDED")

        est_risk_usd = Decimal("0")
        if proposal.ref_price <= Decimal("0") or proposal.stop_loss_price <= Decimal("0"):
            reasons.append("MISSING_STOP_LOSS")
        elif proposal.stop_loss_price >= proposal.ref_price:
            reasons.append("INVALID_STOP_LOSS_ABOVE_ENTRY")
        else:
            stop_pct = (proposal.ref_price - proposal.stop_loss_price) / proposal.ref_price
            roundtrip_cost_pct = (Decimal("2") * fee_rate) + (
                Decimal("2") * f.base_slippage_bps / Decimal("10000")
            )
            est_risk_usd = q_usd(proposal.notional_usd * (stop_pct + roundtrip_cost_pct))
            if est_risk_usd > r.max_risk_per_trade_usd:
                reasons.append(f"MAX_RISK_PER_TRADE_EXCEEDED_{est_risk_usd}")

        return RiskVerdict(
            allowed=len(reasons) == 0,
            reasons=reasons,
            spread_bps=spread_bps,
            estimated_risk_usd=est_risk_usd,
        )
