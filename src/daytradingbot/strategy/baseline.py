from __future__ import annotations

from collections import deque
from decimal import Decimal

from daytradingbot.core.config import StrategySettings
from daytradingbot.core.models import MarketEvent, TradeProposal, q_usd


class RulesBaselineStrategy:
    """Simple, deterministic breakout baseline used to benchmark future LLM advisors."""

    name = "rules_breakout_v1"

    def __init__(self, cfg: StrategySettings) -> None:
        self.cfg = cfg
        self._bar_highs: dict[str, deque[Decimal]] = {}

    def hydrate(self, history: dict[str, list[Decimal]]) -> None:
        for sym, highs in history.items():
            self._bar_highs[sym] = deque(
                highs[-self.cfg.lookback_bars :], maxlen=self.cfg.lookback_bars
            )

    def observe(self, event: MarketEvent) -> None:
        buf = self._bar_highs.setdefault(
            event.symbol, deque(maxlen=self.cfg.lookback_bars)
        )
        buf.append(event.bar_high)

    def evaluate(self, event: MarketEvent) -> TradeProposal:
        buf = self._bar_highs.setdefault(
            event.symbol, deque(maxlen=self.cfg.lookback_bars)
        )
        if len(buf) < self.cfg.lookback_bars:
            buf.append(event.bar_high)
            return TradeProposal(
                event_id=event.event_id,
                symbol=event.symbol,
                asset_class=event.asset_class,
                action="NO_TRADE",
                strategy_name=self.name,
                reason=f"WARMUP_{len(buf)}_OF_{self.cfg.lookback_bars}",
            )

        prior_high = max(buf)
        threshold = prior_high * (Decimal("1") + self.cfg.min_breakout_pct)
        # Append AFTER comparing against prior bars so there is zero look-ahead bias.
        buf.append(event.bar_high)

        if event.bar_close >= threshold and event.ask > Decimal("0"):
            ref = event.ask
            sl = q_usd(ref * (Decimal("1") - self.cfg.stop_loss_pct))
            tp = q_usd(ref * (Decimal("1") + self.cfg.take_profit_pct))
            return TradeProposal(
                event_id=event.event_id,
                symbol=event.symbol,
                asset_class=event.asset_class,
                action="BUY",
                strategy_name=self.name,
                reason=f"BREAKOUT_CLOSE_{event.bar_close}_ABOVE_{q_usd(threshold)}",
                ref_price=ref,
                notional_usd=self.cfg.order_notional_usd,
                stop_loss_price=sl,
                take_profit_price=tp,
                max_hold_seconds=self.cfg.max_hold_seconds,
            )

        return TradeProposal(
            event_id=event.event_id,
            symbol=event.symbol,
            asset_class=event.asset_class,
            action="NO_TRADE",
            strategy_name=self.name,
            reason="NO_BREAKOUT_SIGNAL",
        )
