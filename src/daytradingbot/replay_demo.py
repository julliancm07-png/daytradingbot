from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from daytradingbot.core.config import load_settings
from daytradingbot.core.ledger import Ledger
from daytradingbot.core.models import MarketEvent
from daytradingbot.execution.paper_broker import PaperBroker
from daytradingbot.monitor.coordinator import TradingCoordinator


def _ev(
    event_id: str,
    symbol: str,
    asset_class: str,
    t: datetime,
    bid: str,
    ask: str,
    high: str,
    close: str,
    vol: str = "50000000",
    lag_s: float = 0.2,
    source_status: str = "ok",
) -> MarketEvent:
    return MarketEvent(
        event_id=event_id,
        symbol=symbol,
        asset_class=asset_class,  # type: ignore[arg-type]
        event_time=t,
        received_time=t + timedelta(seconds=lag_s),
        bid=Decimal(bid),
        ask=Decimal(ask),
        bar_open=Decimal(bid),
        bar_high=Decimal(high),
        bar_low=Decimal(bid),
        bar_close=Decimal(close),
        volume_24h_usd=Decimal(vol),
        source_status=source_status,  # type: ignore[arg-type]
    )


def run_replay_demo() -> None:
    cfg_path = Path("config/settings.example.toml")
    settings = load_settings(cfg_path)
    db_file = Path(settings.db_path)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_file}{suffix}")
        if candidate.exists():
            candidate.unlink()

    ledger = Ledger(db_file, settings.starting_cash_usd)
    broker = PaperBroker(settings.fees)
    coord = TradingCoordinator(settings, ledger, broker)

    t0 = datetime(2026, 11, 19, 14, 30, 0, tzinfo=timezone.utc)
    events = [
        # 3 warm-up bars for BTC/USD
        _ev("e1", "BTC/USD", "crypto", t0 + timedelta(minutes=1), "90000", "90020", "90100", "90010"),
        _ev("e2", "BTC/USD", "crypto", t0 + timedelta(minutes=2), "90050", "90070", "90150", "90060"),
        _ev("e3", "BTC/USD", "crypto", t0 + timedelta(minutes=3), "90080", "90100", "90200", "90090"),
        # Valid breakout -> BUY $50 BTC/USD
        _ev("e4", "BTC/USD", "crypto", t0 + timedelta(minutes=4), "90500", "90520", "90550", "90510"),
        # Duplicate of e4 -> IGNORED_DUPLICATE
        _ev("e4", "BTC/USD", "crypto", t0 + timedelta(minutes=4), "90500", "90520", "90550", "90510"),
        # Out-of-order older timestamp -> IGNORED_OUT_OF_ORDER
        _ev("e5", "BTC/USD", "crypto", t0 + timedelta(minutes=2), "90050", "90070", "90150", "90060"),
        # ETH/USD warm-up + wide spread breakout -> VETO (SPREAD_TOO_WIDE)
        _ev("e6", "ETH/USD", "crypto", t0 + timedelta(minutes=1), "3000", "3001", "3005", "3000"),
        _ev("e7", "ETH/USD", "crypto", t0 + timedelta(minutes=2), "3001", "3002", "3006", "3001"),
        _ev("e8", "ETH/USD", "crypto", t0 + timedelta(minutes=3), "3002", "3003", "3007", "3002"),
        _ev("e9", "ETH/USD", "crypto", t0 + timedelta(minutes=4), "3020", "3040", "3040", "3030"),
        # SOL/USD warm-up + stale quote breakout -> VETO (STALE_MARKET_DATA)
        _ev("e10", "SOL/USD", "crypto", t0 + timedelta(minutes=1), "200.00", "200.10", "200.20", "200.05"),
        _ev("e11", "SOL/USD", "crypto", t0 + timedelta(minutes=2), "200.10", "200.20", "200.30", "200.15"),
        _ev("e12", "SOL/USD", "crypto", t0 + timedelta(minutes=3), "200.15", "200.25", "200.40", "200.20"),
        _ev("e13", "SOL/USD", "crypto", t0 + timedelta(minutes=4), "202.00", "202.10", "202.20", "202.05", lag_s=14.0),
        # BTC/USD hits Take Profit (+4.5% above ~90520 -> ~94593) -> SELL & realize PnL
        _ev("e14", "BTC/USD", "crypto", t0 + timedelta(minutes=10), "94700", "94720", "94750", "94710"),
    ]

    print("=" * 72)
    print(f"MODE: {settings.mode} / DEMO (SIMULATED PAPER FILLS — NOT LIVE TRADES)")
    print(f"Starting Virtual Balance: ${settings.starting_cash_usd}")
    print("=" * 72)

    for ev in events:
        res = coord.process_event(ev)
        detail = res.get("reasons") or res.get("reason", "")
        suffix = f" | {detail}" if detail else ""
        print(f"[{ev.event_id:>3}] {ev.symbol:<7} -> {res['status']}{suffix}")

    # Demonstrate Emergency Pause blocking a fresh stock breakout
    ledger.set_paused(True)
    stock_events = [
        _ev("s1", "AAPL", "stock", t0 + timedelta(minutes=1), "230.00", "230.05", "230.10", "230.02"),
        _ev("s2", "AAPL", "stock", t0 + timedelta(minutes=2), "230.05", "230.10", "230.15", "230.08"),
        _ev("s3", "AAPL", "stock", t0 + timedelta(minutes=3), "230.10", "230.15", "230.20", "230.12"),
        _ev("s4", "AAPL", "stock", t0 + timedelta(minutes=4), "231.50", "231.55", "231.60", "231.52"),
    ]
    for ev in stock_events:
        res = coord.process_event(ev)
        if ev.event_id == "s4":
            print(f"[{ev.event_id:>3}] {ev.symbol:<7} -> {res['status']} | {res.get('reasons')}")

    print("-" * 72)
    for pos in ledger.get_all_positions():
        print(
            f"Position {pos.position_id}: {pos.symbol} [{pos.status}] "
            f"Entry=${pos.entry_price} Exit=${pos.exit_price} "
            f"EntryFee=${pos.entry_fee_usd} ExitFee=${pos.exit_fee_usd} "
            f"NetPnL=${pos.realized_pnl_usd} ({pos.exit_reason})"
        )
    print(f"Final Cash (Reconciled): ${ledger.get_cash_usd()}")
    print(f"Cumulative Fees Paid:    ${ledger.get_cumulative_fees_usd()}")
    print(f"SQLite Ledger Saved At:  {db_file.resolve()}")
    print("=" * 72)


if __name__ == "__main__":
    run_replay_demo()
