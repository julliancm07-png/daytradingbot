from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, Optional

from daytradingbot.core.config import AppSettings
from daytradingbot.core.ledger import Ledger
from daytradingbot.core.models import (
    EquitySnapshot,
    MarketEvent,
    OrderRecord,
    PositionRecord,
    TradeProposal,
    q_usd,
)
from daytradingbot.eval.backtest import HistoricalBar
from daytradingbot.execution.paper_broker import PaperBroker
from daytradingbot.monitor.coordinator import TradingCoordinator
from daytradingbot.risk.controller import PortfolioState, RiskController
from daytradingbot.strategy.factory import create_strategy

FillMode = Literal["same-close", "next-open"]

_SLOT_VETO_REASONS = {
    "MAX_CONCURRENT_POSITIONS_REACHED",
    "MAX_TOTAL_EXPOSURE_EXCEEDED",
    "INSUFFICIENT_CASH",
}


@dataclass
class SymbolContribution:
    symbol: str
    closed_trades: int
    wins: int
    realized_pnl_usd: Decimal
    fees_usd: Decimal
    buy_and_hold_pct: Decimal


@dataclass
class PortfolioWindowMetrics:
    label: str
    fill_mode: FillMode
    symbols: list[str]
    timestamps_count: int
    warmup_bars_per_symbol: int
    start_ts: str
    end_ts: str
    starting_cash_usd: Decimal
    ending_equity_usd: Decimal
    net_pnl_usd: Decimal
    net_return_pct: Decimal
    total_fees_usd: Decimal
    closed_trades: int
    open_trades_at_end: int
    tp_exits: int
    sl_exits: int
    mr_exits: int
    time_stop_exits: int
    slot_vetoes: int
    win_rate_pct: Decimal
    profit_factor: Decimal
    max_drawdown_pct: Decimal
    equal_weight_bh_return_pct: Decimal
    per_symbol: list[SymbolContribution] = field(default_factory=list)


def _bar_to_event(
    event_id: str,
    bar: HistoricalBar,
    price_anchor: Decimal,
    half_spread: Decimal,
    lag_ms: int = 250,
) -> MarketEvent:
    bid = q_usd(price_anchor * (Decimal("1") - half_spread))
    ask = q_usd(price_anchor * (Decimal("1") + half_spread))
    return MarketEvent(
        event_id=event_id,
        symbol=bar.symbol,
        asset_class=bar.asset_class,
        event_time=bar.ts,
        received_time=bar.ts + timedelta(milliseconds=lag_ms),
        bid=bid,
        ask=ask,
        bar_open=bar.open,
        bar_high=bar.high,
        bar_low=bar.low,
        bar_close=bar.close,
        volume_24h_usd=bar.volume_usd,
        source_status="ok",
        bar_time=bar.ts,
    )


def _snapshot_portfolio(
    ledger: Ledger,
    mode: Literal["REPLAY", "PAPER", "LIVE"],
    ts: datetime,
    last_bid: dict[str, Decimal],
) -> EquitySnapshot:
    cash = ledger.get_cash_usd()
    open_positions = ledger.get_open_positions()
    open_val = Decimal("0")
    for p in open_positions:
        mark = last_bid.get(p.symbol, p.entry_price)
        open_val += q_usd(p.qty * mark)

    total_equity = q_usd(cash + open_val)
    peak = max(ledger.get_peak_equity_usd(), total_equity)
    dd = (
        q_usd(((peak - total_equity) / peak) * Decimal("100"))
        if peak > Decimal("0")
        else Decimal("0.0000")
    )
    cum_fees = ledger.get_cumulative_fees_usd()
    api_spend = ledger.get_monthly_api_spend_usd()
    snap = EquitySnapshot(
        ts=ts,
        mode=mode,
        cash_usd=cash,
        open_market_value_usd=open_val,
        total_equity_usd=total_equity,
        peak_equity_usd=peak,
        drawdown_pct=dd,
        cumulative_fees_usd=cum_fees,
        monthly_api_spend_usd=api_spend,
        net_equity_after_api_usd=q_usd(total_equity - api_spend),
    )
    ledger.record_equity_snapshot(snap)
    return snap


def _build_metrics(
    label: str,
    fill_mode: FillMode,
    bars_by_symbol: dict[str, list[HistoricalBar]],
    unique_ts: list[datetime],
    warmup_bars_per_symbol: int,
    ledger: Ledger,
    starting_cash: Decimal,
    max_dd: Decimal,
) -> PortfolioWindowMetrics:
    symbols = sorted(bars_by_symbol.keys())
    latest_snap = ledger.get_latest_equity_snapshot()
    ending_equity = (
        Decimal(str(latest_snap["total_equity_usd"]))
        if latest_snap is not None
        else starting_cash
    )
    net_pnl = q_usd(ending_equity - starting_cash)
    net_ret_pct = q_usd((net_pnl / starting_cash) * Decimal("100"))

    all_pos = ledger.get_all_positions()
    closed = [p for p in all_pos if p.status == "CLOSED" and p.realized_pnl_usd is not None]
    open_end = [p for p in all_pos if p.status == "OPEN"]

    tp_exits = sum(1 for p in closed if p.exit_reason == "TAKE_PROFIT")
    sl_exits = sum(1 for p in closed if p.exit_reason == "STOP_LOSS")
    mr_exits = sum(1 for p in closed if p.exit_reason == "MEAN_REVERT")
    time_stop_exits = sum(1 for p in closed if p.exit_reason == "TIME_STOP")

    wins = [p.realized_pnl_usd for p in closed if p.realized_pnl_usd > Decimal("0")]  # type: ignore[operator]
    losses = [p.realized_pnl_usd for p in closed if p.realized_pnl_usd <= Decimal("0")]  # type: ignore[operator]
    win_rate = (
        q_usd((Decimal(len(wins)) / Decimal(len(closed))) * Decimal("100"))
        if closed
        else Decimal("0.0000")
    )
    gross_win = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    if gross_loss > Decimal("0"):
        pf = q_usd(gross_win / gross_loss)
    elif gross_win > Decimal("0"):
        pf = Decimal("99.0000")
    else:
        pf = Decimal("0.0000")

    # Count how many BUY proposals were blocked due to shared-cash/slot contention
    slot_vetoes = 0
    for entry in ledger.get_audit_log():
        if entry["stage"] == "RISK" and entry["status"] == "VETO":
            reasons = set(entry["details"].get("reasons", []))
            if reasons.intersection(_SLOT_VETO_REASONS):
                slot_vetoes += 1

    # Equal-weight Buy-and-Hold benchmark & per-symbol contribution table
    bh_returns: list[Decimal] = []
    per_symbol: list[SymbolContribution] = []
    for sym in symbols:
        s_bars = bars_by_symbol[sym]
        bh_pct = (
            q_usd(((s_bars[-1].close - s_bars[0].close) / s_bars[0].close) * Decimal("100"))
            if len(s_bars) >= 2 and s_bars[0].close > Decimal("0")
            else Decimal("0.0000")
        )
        bh_returns.append(bh_pct)

        sym_closed = [p for p in closed if p.symbol == sym]
        sym_wins = sum(1 for p in sym_closed if p.realized_pnl_usd is not None and p.realized_pnl_usd > Decimal("0"))
        sym_pnl = q_usd(sum((p.realized_pnl_usd for p in sym_closed if p.realized_pnl_usd is not None), Decimal("0")))
        sym_fees = q_usd(
            sum(
                (p.entry_fee_usd + (p.exit_fee_usd or Decimal("0")) for p in all_pos if p.symbol == sym),
                Decimal("0"),
            )
        )
        per_symbol.append(
            SymbolContribution(
                symbol=sym,
                closed_trades=len(sym_closed),
                wins=sym_wins,
                realized_pnl_usd=sym_pnl,
                fees_usd=sym_fees,
                buy_and_hold_pct=bh_pct,
            )
        )

    eq_bh = (
        q_usd(sum(bh_returns, Decimal("0")) / Decimal(len(bh_returns)))
        if bh_returns
        else Decimal("0.0000")
    )

    return PortfolioWindowMetrics(
        label=label,
        fill_mode=fill_mode,
        symbols=symbols,
        timestamps_count=len(unique_ts),
        warmup_bars_per_symbol=warmup_bars_per_symbol,
        start_ts=unique_ts[0].isoformat(),
        end_ts=unique_ts[-1].isoformat(),
        starting_cash_usd=starting_cash,
        ending_equity_usd=ending_equity,
        net_pnl_usd=net_pnl,
        net_return_pct=net_ret_pct,
        total_fees_usd=ledger.get_cumulative_fees_usd(),
        closed_trades=len(closed),
        open_trades_at_end=len(open_end),
        tp_exits=tp_exits,
        sl_exits=sl_exits,
        mr_exits=mr_exits,
        time_stop_exits=time_stop_exits,
        slot_vetoes=slot_vetoes,
        win_rate_pct=win_rate,
        profit_factor=pf,
        max_drawdown_pct=max_dd,
        equal_weight_bh_return_pct=eq_bh,
        per_symbol=per_symbol,
    )


def run_portfolio_window(
    label: str,
    bars_by_symbol: dict[str, list[HistoricalBar]],
    settings: AppSettings,
    assumed_spread_bps: Decimal = Decimal("2"),
    fill_mode: FillMode = "same-close",
    warmup_bars_by_symbol: Optional[dict[str, list[HistoricalBar]]] = None,
) -> PortfolioWindowMetrics:
    """
    Simulate multiple symbols simultaneously on ONE shared SQLite WAL ledger.
    - `same-close`: signals evaluated and filled at bar[t].close +/- half_spread + slippage.
    - `next-open`: signals evaluated strictly on completed bar[t] (zero look-ahead),
      queued as Market-on-Open orders, and filled at bar[t+1].open +/- half_spread + slippage.
    """
    symbols = sorted(s for s, blist in bars_by_symbol.items() if blist)
    if not symbols:
        raise ValueError("No historical bars provided for portfolio evaluation.")

    # Index bars by timestamp so all symbols advance chronologically together
    by_ts: dict[datetime, list[HistoricalBar]] = {}
    for sym in symbols:
        for b in bars_by_symbol[sym]:
            by_ts.setdefault(b.ts, []).append(b)
    unique_ts = sorted(by_ts.keys())
    if len(unique_ts) < 2:
        raise ValueError("Need at least 2 timestamps for a portfolio window.")

    half_spread = (assumed_spread_bps / Decimal("2")) / Decimal("10000")
    warmup_count = 0
    if warmup_bars_by_symbol:
        warmup_count = max((len(v) for v in warmup_bars_by_symbol.values()), default=0)

    with tempfile.TemporaryDirectory() as tmp:
        eval_settings = settings.model_copy(
            update={
                "mode": "REPLAY",
                "db_path": str(Path(tmp) / f"port_{label.lower()}_{fill_mode}.db"),
            }
        )
        ledger = Ledger(eval_settings.db_path, eval_settings.starting_cash_usd)
        broker = PaperBroker(eval_settings.fees)
        max_dd = Decimal("0.0000")

        if fill_mode == "same-close":
            coord = TradingCoordinator(eval_settings, ledger, broker)
            # Pre-feed strictly past warmup bars to indicators ONLY (zero trades permitted)
            if warmup_bars_by_symbol:
                for sym in symbols:
                    for w_idx, wb in enumerate(warmup_bars_by_symbol.get(sym, [])):
                        wev = _bar_to_event(f"WU-{sym}-{w_idx}", wb, wb.close, half_spread)
                        coord.strategy.observe(wev)

            for ts_idx, ts in enumerate(unique_ts):
                day_bars = sorted(by_ts[ts], key=lambda x: x.symbol)
                for b in day_bars:
                    ev = _bar_to_event(
                        f"PT-{label}-{ts_idx}-{b.symbol.replace('/', '_')}",
                        b,
                        b.close,
                        half_spread,
                    )
                    coord.process_event(ev)
                snap = ledger.get_latest_equity_snapshot()
                if snap is not None:
                    dd = Decimal(str(snap["drawdown_pct"]))
                    if dd > max_dd:
                        max_dd = dd

            return _build_metrics(
                label=label,
                fill_mode=fill_mode,
                bars_by_symbol=bars_by_symbol,
                unique_ts=unique_ts,
                warmup_bars_per_symbol=warmup_count,
                ledger=ledger,
                starting_cash=eval_settings.starting_cash_usd,
                max_dd=max_dd,
            )

        # ------------------------------------------------------------------
        # NEXT-OPEN MODE: Strict two-phase execution (Open fills -> Close signals)
        # ------------------------------------------------------------------
        strategy = create_strategy(eval_settings.strategy)
        risk = RiskController(eval_settings)
        last_bid: dict[str, Decimal] = {}

        if warmup_bars_by_symbol:
            for sym in symbols:
                for w_idx, wb in enumerate(warmup_bars_by_symbol.get(sym, [])):
                    wev = _bar_to_event(f"WU-{sym}-{w_idx}", wb, wb.close, half_spread)
                    strategy.observe(wev)

        pending_exits: dict[str, tuple[PositionRecord, str]] = {}
        pending_entries: dict[str, TradeProposal] = {}

        for ts_idx, ts in enumerate(unique_ts):
            day_bars = sorted(by_ts[ts], key=lambda x: x.symbol)

            # PHASE 1A: Execute queued SELL exits at today's OPEN (before seeing today's Close)
            for b in day_bars:
                if b.symbol not in pending_exits:
                    continue
                pos, exit_reason = pending_exits.pop(b.symbol)
                open_ev = _bar_to_event(
                    f"NO-SELL-{ts_idx}-{b.symbol.replace('/', '_')}",
                    b,
                    b.open,
                    half_spread,
                    lag_ms=100,
                )
                order = OrderRecord(
                    idempotency_key=f"EXIT:{pos.position_id}:{exit_reason}",
                    order_id=f"ORD-SELL-{pos.position_id}",
                    symbol=pos.symbol,
                    asset_class=pos.asset_class,
                    side="SELL",
                    requested_notional_usd=q_usd(pos.qty * open_ev.bid),
                    requested_qty=pos.qty,
                    status="PENDING_SUBMIT",
                    mode="REPLAY",
                    created_at=open_ev.received_time,
                    updated_at=open_ev.received_time,
                    position_id=pos.position_id,
                    exit_reason=exit_reason,
                )
                stored, created = ledger.save_order_if_absent(order)
                if created:
                    res = broker.submit_order(stored, open_ev)
                    if res.status == "FILLED" and res.fill is not None:
                        ledger.apply_fill(stored, res.fill)
                        ledger.log_audit(
                            res.fill.filled_at,
                            "EXECUTION",
                            stored.symbol,
                            stored.order_id,
                            "FILLED_SELL",
                            {"fill_price": str(res.fill.fill_price), "fill_mode": "next-open"},
                        )

            # PHASE 1B: Execute queued BUY entries at today's OPEN (re-checked by RiskController)
            for b in day_bars:
                if b.symbol not in pending_entries:
                    continue
                proposal = pending_entries.pop(b.symbol)
                open_ev = _bar_to_event(
                    f"NO-BUY-{ts_idx}-{b.symbol.replace('/', '_')}",
                    b,
                    b.open,
                    half_spread,
                    lag_ms=100,
                )
                ref = open_ev.ask
                sl = q_usd(ref * (Decimal("1") - eval_settings.strategy.stop_loss_pct))
                tp = q_usd(ref * (Decimal("1") + eval_settings.strategy.take_profit_pct))
                anchored = proposal.model_copy(
                    update={"ref_price": ref, "stop_loss_price": sl, "take_profit_price": tp}
                )
                portfolio_state = PortfolioState(
                    cash_usd=ledger.get_cash_usd(),
                    open_positions=ledger.get_open_positions(),
                    daily_realized_pnl_usd=ledger.get_daily_realized_pnl(b.ts.date().isoformat()),
                    monthly_api_spend_usd=ledger.get_monthly_api_spend_usd(),
                    paused=ledger.is_paused(),
                )
                verdict = risk.evaluate(anchored, open_ev, portfolio_state)
                ledger.log_audit(
                    open_ev.received_time,
                    "RISK",
                    b.symbol,
                    open_ev.event_id,
                    "ALLOW" if verdict.allowed else "VETO",
                    verdict.model_dump(mode="json"),
                )
                if not verdict.allowed:
                    continue

                order = OrderRecord(
                    idempotency_key=f"ENTRY:{open_ev.event_id}:{b.symbol}",
                    order_id=f"ORD-BUY-{open_ev.event_id}",
                    symbol=b.symbol,
                    asset_class=b.asset_class,
                    side="BUY",
                    requested_notional_usd=anchored.notional_usd,
                    status="PENDING_SUBMIT",
                    mode="REPLAY",
                    created_at=open_ev.received_time,
                    updated_at=open_ev.received_time,
                    stop_loss_price=anchored.stop_loss_price,
                    take_profit_price=anchored.take_profit_price,
                    max_hold_until=b.ts + timedelta(seconds=anchored.max_hold_seconds),
                )
                stored, created = ledger.save_order_if_absent(order)
                if created:
                    res = broker.submit_order(stored, open_ev)
                    if res.status == "FILLED" and res.fill is not None:
                        ledger.apply_fill(stored, res.fill)
                        ledger.log_audit(
                            res.fill.filled_at,
                            "EXECUTION",
                            stored.symbol,
                            stored.order_id,
                            "FILLED_BUY",
                            {"fill_price": str(res.fill.fill_price), "fill_mode": "next-open"},
                        )

            # PHASE 2: Evaluate today's COMPLETED bar close -> queue orders for tomorrow's OPEN
            open_map = {p.symbol: p for p in ledger.get_open_positions()}
            for b in day_bars:
                close_ev = _bar_to_event(
                    f"NO-CLOSE-{ts_idx}-{b.symbol.replace('/', '_')}",
                    b,
                    b.close,
                    half_spread,
                    lag_ms=250,
                )
                last_bid[b.symbol] = close_ev.bid

                if b.symbol in open_map:
                    pos = open_map[b.symbol]
                    strategy.observe(close_ev)
                    exit_reason: Optional[str] = None
                    if close_ev.bid <= pos.stop_loss_price:
                        exit_reason = "STOP_LOSS"
                    elif close_ev.bid >= pos.take_profit_price:
                        exit_reason = "TAKE_PROFIT"
                    elif b.ts >= pos.max_hold_until:
                        exit_reason = "TIME_STOP"
                    elif hasattr(strategy, "should_exit"):
                        exit_reason = strategy.should_exit(close_ev, pos)
                    if exit_reason is not None:
                        pending_exits[b.symbol] = (pos, exit_reason)
                else:
                    prop = strategy.evaluate(close_ev)
                    if prop.action == "BUY":
                        pending_entries[b.symbol] = prop

            snap = _snapshot_portfolio(ledger, "REPLAY", ts + timedelta(milliseconds=300), last_bid)
            if snap.drawdown_pct > max_dd:
                max_dd = snap.drawdown_pct

        return _build_metrics(
            label=label,
            fill_mode=fill_mode,
            bars_by_symbol=bars_by_symbol,
            unique_ts=unique_ts,
            warmup_bars_per_symbol=warmup_count,
            ledger=ledger,
            starting_cash=eval_settings.starting_cash_usd,
            max_dd=max_dd,
        )


def run_portfolio_is_oos(
    bars_by_symbol: dict[str, list[HistoricalBar]],
    settings: AppSettings,
    in_sample_ratio: float = 0.60,
    assumed_spread_bps: Decimal = Decimal("2"),
    fill_mode: FillMode = "same-close",
) -> tuple[PortfolioWindowMetrics, PortfolioWindowMetrics]:
    """
    Chronologically split a multi-symbol basket on a single shared date boundary.
    OOS indicators are warmed up strictly using trailing IS bars (zero trades in warmup,
    zero future bars ever visible).
    """
    all_ts = sorted({b.ts for blist in bars_by_symbol.values() for b in blist})
    if len(all_ts) < 20:
        raise ValueError("Need at least 20 shared timestamps for portfolio IS/OOS split.")

    split_idx = int(len(all_ts) * in_sample_ratio)
    split_ts = all_ts[split_idx]

    warmup_needed = max(
        settings.strategy.trend_ma_bars,
        settings.strategy.rsi_period + 1,
        settings.strategy.mean_exit_sma_bars,
        settings.strategy.lookback_bars,
    )

    is_bars: dict[str, list[HistoricalBar]] = {}
    oos_bars: dict[str, list[HistoricalBar]] = {}
    oos_warmup: dict[str, list[HistoricalBar]] = {}

    for sym, blist in bars_by_symbol.items():
        s_is = [b for b in blist if b.ts < split_ts]
        s_oos = [b for b in blist if b.ts >= split_ts]
        if len(s_is) < 2 or len(s_oos) < 2:
            continue
        assert s_is[-1].ts < s_oos[0].ts, f"Chronological split violated for {sym}"
        is_bars[sym] = s_is
        oos_bars[sym] = s_oos
        oos_warmup[sym] = s_is[-warmup_needed:] if warmup_needed > 0 else []

    is_metrics = run_portfolio_window(
        label="IN_SAMPLE",
        bars_by_symbol=is_bars,
        settings=settings,
        assumed_spread_bps=assumed_spread_bps,
        fill_mode=fill_mode,
        warmup_bars_by_symbol=None,
    )
    oos_metrics = run_portfolio_window(
        label="OUT_OF_SAMPLE",
        bars_by_symbol=oos_bars,
        settings=settings,
        assumed_spread_bps=assumed_spread_bps,
        fill_mode=fill_mode,
        warmup_bars_by_symbol=oos_warmup,
    )
    return is_metrics, oos_metrics
