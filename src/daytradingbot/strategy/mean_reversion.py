from __future__ import annotations

from collections import deque
from datetime import datetime
from decimal import Decimal
from typing import Optional

from daytradingbot.core.config import StrategySettings
from daytradingbot.core.models import MarketEvent, PositionRecord, TradeProposal, q_usd


def compute_rsi(closes: list[Decimal], period: int) -> Optional[Decimal]:
    """Compute simple average-gain/loss RSI strictly from completed bar closes (zero look-ahead)."""
    if period <= 0 or len(closes) < period + 1:
        return None
    window = closes[-(period + 1) :]
    gains = Decimal("0")
    losses = Decimal("0")
    for idx in range(1, len(window)):
        delta = window[idx] - window[idx - 1]
        if delta > Decimal("0"):
            gains += delta
        elif delta < Decimal("0"):
            losses += -delta

    avg_gain = gains / Decimal(period)
    avg_loss = losses / Decimal(period)
    if avg_loss == Decimal("0"):
        return Decimal("50.0000") if avg_gain == Decimal("0") else Decimal("100.0000")
    rs = avg_gain / avg_loss
    rsi = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
    return q_usd(rsi)


class MeanReversionStrategy:
    """
    Deterministic RSI dip mean-reversion strategy for liquid US equities/ETFs.
    - Enters on short-term oversold pullback (RSI <= rsi_entry_below).
    - Optional slower SMA trend filter (`trend_ma_bars`) blocks falling-knife crashes.
    - Exits when price snaps back to short SMA or RSI recovers, while hard SL/TP/TIME
      stops remain enforced by TradingCoordinator.
    """

    name = "rules_rsi_mean_reversion_v1"

    def __init__(self, cfg: StrategySettings) -> None:
        self.cfg = cfg
        self._buf_len = max(cfg.rsi_period + 1, cfg.trend_ma_bars, cfg.mean_exit_sma_bars, 2) + 2
        self._bar_closes: dict[str, deque[Decimal]] = {}
        self._last_bar_time: dict[str, datetime] = {}

    def hydrate(self, history: dict[str, list[Decimal]]) -> None:
        # Mean-reversion relies on bar closes (not highs); warms up cleanly from live/replay bars.
        _ = history

    def _record_bar(self, event: MarketEvent) -> bool:
        """Append bar_close once per new bar_time. Returns True if a new bar was recorded."""
        if event.bar_time is not None:
            if self._last_bar_time.get(event.symbol) == event.bar_time:
                return False
            self._last_bar_time[event.symbol] = event.bar_time
        buf = self._bar_closes.setdefault(event.symbol, deque(maxlen=self._buf_len))
        buf.append(event.bar_close)
        return True

    def observe(self, event: MarketEvent) -> None:
        self._record_bar(event)

    def evaluate(self, event: MarketEvent) -> TradeProposal:
        if not self._record_bar(event):
            return TradeProposal(
                event_id=event.event_id,
                symbol=event.symbol,
                asset_class=event.asset_class,
                action="NO_TRADE",
                strategy_name=self.name,
                reason="WAITING_NEXT_BAR",
            )

        closes = list(self._bar_closes[event.symbol])
        needed = max(self.cfg.rsi_period + 1, self.cfg.trend_ma_bars)
        if len(closes) < needed:
            return TradeProposal(
                event_id=event.event_id,
                symbol=event.symbol,
                asset_class=event.asset_class,
                action="NO_TRADE",
                strategy_name=self.name,
                reason=f"WARMUP_{len(closes)}_OF_{needed}",
            )

        if self.cfg.trend_ma_bars > 0:
            trend_slice = closes[-self.cfg.trend_ma_bars :]
            trend_sma = q_usd(sum(trend_slice, Decimal("0")) / Decimal(len(trend_slice)))
            if event.bar_close <= trend_sma:
                return TradeProposal(
                    event_id=event.event_id,
                    symbol=event.symbol,
                    asset_class=event.asset_class,
                    action="NO_TRADE",
                    strategy_name=self.name,
                    reason=f"BELOW_TREND_SMA_{trend_sma}",
                )

        rsi = compute_rsi(closes, self.cfg.rsi_period)
        if rsi is None:
            return TradeProposal(
                event_id=event.event_id,
                symbol=event.symbol,
                asset_class=event.asset_class,
                action="NO_TRADE",
                strategy_name=self.name,
                reason="RSI_UNAVAILABLE",
            )

        if rsi <= self.cfg.rsi_entry_below and event.ask > Decimal("0"):
            ref = event.ask
            sl = q_usd(ref * (Decimal("1") - self.cfg.stop_loss_pct))
            tp = q_usd(ref * (Decimal("1") + self.cfg.take_profit_pct))
            return TradeProposal(
                event_id=event.event_id,
                symbol=event.symbol,
                asset_class=event.asset_class,
                action="BUY",
                strategy_name=self.name,
                reason=f"OVERSOLD_RSI_{rsi}_LE_{self.cfg.rsi_entry_below}",
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
            reason=f"NO_DIP_RSI_{rsi}",
        )

    def should_exit(self, event: MarketEvent, pos: PositionRecord) -> Optional[str]:
        """Exit when oversold dip snaps back to short-term mean or RSI recovers."""
        _ = pos
        buf = self._bar_closes.get(event.symbol)
        if not buf:
            return None
        closes = list(buf)

        if self.cfg.mean_exit_sma_bars > 0 and len(closes) >= self.cfg.mean_exit_sma_bars:
            exit_slice = closes[-self.cfg.mean_exit_sma_bars :]
            mean_sma = q_usd(sum(exit_slice, Decimal("0")) / Decimal(len(exit_slice)))
            if event.bid >= mean_sma:
                return "MEAN_REVERT"

        rsi = compute_rsi(closes, self.cfg.rsi_period)
        if rsi is not None and rsi >= self.cfg.rsi_exit_above:
            return "MEAN_REVERT"

        return None
