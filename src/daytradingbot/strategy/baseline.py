from __future__ import annotations

from collections import deque
from datetime import datetime
from decimal import Decimal

from daytradingbot.core.config import StrategySettings
from daytradingbot.core.models import MarketEvent, TradeProposal, q_usd


class RulesBaselineStrategy:
    """Deterministic breakout baseline with optional zero-lookahead SMA trend filter."""

    name = "rules_breakout_v1"

    def __init__(self, cfg: StrategySettings) -> None:
        self.cfg = cfg
        self._bar_highs: dict[str, deque[Decimal]] = {}
        self._bar_closes: dict[str, deque[Decimal]] = {}
        self._last_bar_time: dict[str, datetime] = {}

    def hydrate(self, history: dict[str, list[Decimal]]) -> None:
        for sym, highs in history.items():
            self._bar_highs[sym] = deque(
                highs[-self.cfg.lookback_bars :], maxlen=self.cfg.lookback_bars
            )

    def observe(self, event: MarketEvent) -> None:
        if event.bar_time is not None:
            if self._last_bar_time.get(event.symbol) == event.bar_time:
                return
            self._last_bar_time[event.symbol] = event.bar_time
        buf = self._bar_highs.setdefault(
            event.symbol, deque(maxlen=self.cfg.lookback_bars)
        )
        buf.append(event.bar_high)
        if self.cfg.trend_ma_bars > 0:
            cbuf = self._bar_closes.setdefault(
                event.symbol, deque(maxlen=self.cfg.trend_ma_bars)
            )
            cbuf.append(event.bar_close)

    def evaluate(self, event: MarketEvent) -> TradeProposal:
        # When live snapshots poll every few seconds, only evaluate entries on a new bar_time.
        if event.bar_time is not None:
            if self._last_bar_time.get(event.symbol) == event.bar_time:
                return TradeProposal(
                    event_id=event.event_id,
                    symbol=event.symbol,
                    asset_class=event.asset_class,
                    action="NO_TRADE",
                    strategy_name=self.name,
                    reason="WAITING_NEXT_BAR",
                )
            self._last_bar_time[event.symbol] = event.bar_time

        buf = self._bar_highs.setdefault(
            event.symbol, deque(maxlen=self.cfg.lookback_bars)
        )
        cbuf = (
            self._bar_closes.setdefault(
                event.symbol, deque(maxlen=self.cfg.trend_ma_bars)
            )
            if self.cfg.trend_ma_bars > 0
            else None
        )

        needs_high_warmup = len(buf) < self.cfg.lookback_bars
        needs_ma_warmup = cbuf is not None and len(cbuf) < self.cfg.trend_ma_bars
        if needs_high_warmup or needs_ma_warmup:
            buf.append(event.bar_high)
            if cbuf is not None:
                cbuf.append(event.bar_close)
            target = max(self.cfg.lookback_bars, self.cfg.trend_ma_bars)
            have = min(len(buf), len(cbuf)) if cbuf is not None else len(buf)
            return TradeProposal(
                event_id=event.event_id,
                symbol=event.symbol,
                asset_class=event.asset_class,
                action="NO_TRADE",
                strategy_name=self.name,
                reason=f"WARMUP_{have}_OF_{target}",
            )

        prior_high = max(buf)
        prior_ma = (
            (sum(cbuf, Decimal("0")) / Decimal(len(cbuf)))
            if cbuf is not None
            else None
        )
        threshold = prior_high * (Decimal("1") + self.cfg.min_breakout_pct)

        # Append AFTER comparing against prior bars so there is zero look-ahead bias.
        buf.append(event.bar_high)
        if cbuf is not None:
            cbuf.append(event.bar_close)

        if prior_ma is not None and event.bar_close <= prior_ma:
            return TradeProposal(
                event_id=event.event_id,
                symbol=event.symbol,
                asset_class=event.asset_class,
                action="NO_TRADE",
                strategy_name=self.name,
                reason=f"BELOW_TREND_SMA_{q_usd(prior_ma)}",
            )

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
