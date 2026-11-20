from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from daytradingbot.core.config import AppSettings, StrategySettings
from daytradingbot.core.ledger import Ledger
from daytradingbot.core.models import MarketEvent
from daytradingbot.eval.backtest import generate_synthetic_bars, run_is_oos_evaluation
from daytradingbot.execution.paper_broker import PaperBroker
from daytradingbot.monitor.coordinator import TradingCoordinator
from daytradingbot.strategy.mean_reversion import compute_rsi

T0 = datetime(2026, 11, 20, 14, 0, 0, tzinfo=timezone.utc)


def _stock_ev(eid: str, hour_offset: int, bid: str, ask: str, close: str) -> MarketEvent:
    ts = T0 + timedelta(hours=hour_offset)
    return MarketEvent(
        event_id=eid,
        symbol="SPY",
        asset_class="stock",
        event_time=ts,
        received_time=ts + timedelta(milliseconds=200),
        bid=Decimal(bid),
        ask=Decimal(ask),
        bar_open=Decimal(bid),
        bar_high=Decimal(ask),
        bar_low=Decimal(bid),
        bar_close=Decimal(close),
        volume_24h_usd=Decimal("50000000"),
        bar_time=ts,
    )


def test_compute_rsi_edge_cases_and_accuracy() -> None:
    assert compute_rsi([Decimal("100")] * 4, period=5) is None
    assert compute_rsi([Decimal("100")] * 6, period=5) == Decimal("50.0000")
    assert compute_rsi([Decimal(str(100 + i)) for i in range(6)], period=5) == Decimal("100.0000")
    assert compute_rsi([Decimal(str(100 - i)) for i in range(6)], period=5) == Decimal("0.0000")


def test_mean_reversion_enters_oversold_dip_and_exits_on_reversion(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=str(tmp_path / "mr.db"),
        strategy=StrategySettings(
            strategy_type="mean_reversion",
            rsi_period=5,
            rsi_entry_below=Decimal("30"),
            rsi_exit_above=Decimal("60"),
            mean_exit_sma_bars=5,
            trend_ma_bars=0,
            stop_loss_pct=Decimal("0.020"),
            take_profit_pct=Decimal("0.030"),
            max_hold_seconds=72 * 3600,
        ),
    )
    ledger = Ledger(settings.db_path, settings.starting_cash_usd)
    broker = PaperBroker(settings.fees)
    coord = TradingCoordinator(settings, ledger, broker)

    # 5 flat warmup bars
    for h in range(1, 6):
        res = coord.process_event(_stock_ev(f"w{h}", h, "99.98", "100.02", "100.00"))
        assert res["status"] == "NO_TRADE"

    # Dip to 99.00 -> RSI(5) = 0 <= 30 -> BUY
    entry = coord.process_event(_stock_ev("dip", 6, "98.98", "99.02", "99.00"))
    assert entry["status"] == "FILLED_BUY"
    assert len(ledger.get_open_positions()) == 1

    # Snap back above 5-bar SMA (~99.82) -> MEAN_REVERT exit
    exit_res = coord.process_event(_stock_ev("rev", 7, "100.10", "100.14", "100.12"))
    assert exit_res["status"] == "FILLED_SELL"
    pos = ledger.get_all_positions()[0]
    assert pos.status == "CLOSED"
    assert pos.exit_reason == "MEAN_REVERT"
    assert pos.realized_pnl_usd is not None and pos.realized_pnl_usd > Decimal("0")


def test_mean_reversion_trend_filter_blocks_falling_knife(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=str(tmp_path / "mr_trend.db"),
        strategy=StrategySettings(
            strategy_type="mean_reversion",
            rsi_period=3,
            rsi_entry_below=Decimal("30"),
            trend_ma_bars=5,
            stop_loss_pct=Decimal("0.020"),
            take_profit_pct=Decimal("0.030"),
            max_hold_seconds=72 * 3600,
        ),
    )
    ledger = Ledger(settings.db_path, settings.starting_cash_usd)
    broker = PaperBroker(settings.fees)
    coord = TradingCoordinator(settings, ledger, broker)

    for idx, px in enumerate(["120", "118", "116", "114"], start=1):
        assert coord.process_event(_stock_ev(f"f{idx}", idx, px, px, px))["status"] == "NO_TRADE"

    # Crash to 110: RSI(3) = 0 (oversold) BUT below 5-bar SMA (115.6) -> MUST BLOCK!
    blocked = coord.process_event(_stock_ev("f5", 5, "109.98", "110.02", "110.00"))
    assert blocked["status"] == "NO_TRADE"
    assert blocked["reason"].startswith("BELOW_TREND_SMA_")
    assert broker.submit_call_count == 0


def test_is_oos_evaluation_supports_mean_reversion_strategy() -> None:
    bars = generate_synthetic_bars("SPY", "stock", count=200)
    settings = AppSettings(
        strategy=StrategySettings(
            strategy_type="mean_reversion",
            rsi_period=7,
            rsi_entry_below=Decimal("30"),
            rsi_exit_above=Decimal("60"),
            mean_exit_sma_bars=10,
            trend_ma_bars=0,
            stop_loss_pct=Decimal("0.020"),
            take_profit_pct=Decimal("0.030"),
            max_hold_seconds=72 * 3600,
        )
    )
    is_m, oos_m = run_is_oos_evaluation(bars, settings, in_sample_ratio=0.60, assumed_spread_bps=Decimal("2"))
    assert is_m.end_ts < oos_m.start_ts
    assert is_m.closed_trades >= 1
    assert is_m.mr_exits + is_m.tp_exits + is_m.sl_exits + is_m.time_stop_exits == is_m.closed_trades
