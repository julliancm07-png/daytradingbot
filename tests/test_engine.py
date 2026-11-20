from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from daytradingbot.core.config import AppSettings
from daytradingbot.core.ledger import Ledger
from daytradingbot.core.models import MarketEvent, TradeProposal
from daytradingbot.execution.paper_broker import PaperBroker
from daytradingbot.monitor.coordinator import TradingCoordinator
from daytradingbot.risk.controller import PortfolioState, RiskController

T0 = datetime(2026, 11, 19, 15, 0, 0, tzinfo=timezone.utc)


def make_event(
    event_id: str,
    symbol: str = "BTC/USD",
    asset_class: str = "crypto",
    minute_offset: int = 0,
    bid: str = "100.00",
    ask: str = "100.05",
    high: str = "100.10",
    close: str = "100.02",
    vol: str = "20000000",
    lag_s: float = 0.2,
    source_status: str = "ok",
) -> MarketEvent:
    ts = T0 + timedelta(minutes=minute_offset)
    return MarketEvent(
        event_id=event_id,
        symbol=symbol,
        asset_class=asset_class,  # type: ignore[arg-type]
        event_time=ts,
        received_time=ts + timedelta(seconds=lag_s),
        bid=Decimal(bid),
        ask=Decimal(ask),
        bar_open=Decimal(bid),
        bar_high=Decimal(high),
        bar_low=Decimal(bid),
        bar_close=Decimal(close),
        volume_24h_usd=Decimal(vol),
        source_status=source_status,  # type: ignore[arg-type]
    )


def warmup_symbol(coord: TradingCoordinator, symbol: str = "BTC/USD", asset_class: str = "crypto") -> None:
    for i in range(1, 4):
        res = coord.process_event(
            make_event(f"{symbol}-w{i}", symbol=symbol, asset_class=asset_class, minute_offset=i)
        )
        assert res["status"] == "NO_TRADE"


def test_entry_take_profit_and_exact_accounting(tmp_path: Path) -> None:
    settings = AppSettings(db_path=str(tmp_path / "test.db"))
    ledger = Ledger(settings.db_path, settings.starting_cash_usd)
    broker = PaperBroker(settings.fees)
    coord = TradingCoordinator(settings, ledger, broker)

    warmup_symbol(coord)
    entry = coord.process_event(
        make_event("b1", minute_offset=4, bid="101.00", ask="101.05", high="101.10", close="101.02")
    )
    assert entry["status"] == "FILLED_BUY"
    assert len(ledger.get_open_positions()) == 1
    assert ledger.get_cash_usd() < Decimal("200.0000")

    exit_res = coord.process_event(
        make_event("x1", minute_offset=10, bid="106.00", ask="106.05", high="106.10", close="106.02")
    )
    assert exit_res["status"] == "FILLED_SELL"
    assert len(ledger.get_open_positions()) == 0

    pos = ledger.get_all_positions()[0]
    assert pos.status == "CLOSED"
    assert pos.exit_reason == "TAKE_PROFIT"
    assert pos.realized_pnl_usd is not None and pos.realized_pnl_usd > Decimal("0")
    assert ledger.get_cash_usd() == settings.starting_cash_usd + pos.realized_pnl_usd


def test_stock_regulatory_fees_and_stop_loss(tmp_path: Path) -> None:
    settings = AppSettings(db_path=str(tmp_path / "stock.db"))
    ledger = Ledger(settings.db_path, settings.starting_cash_usd)
    broker = PaperBroker(settings.fees)
    coord = TradingCoordinator(settings, ledger, broker)

    warmup_symbol(coord, symbol="AAPL", asset_class="stock")
    entry = coord.process_event(
        make_event(
            "s-buy",
            symbol="AAPL",
            asset_class="stock",
            minute_offset=4,
            bid="101.00",
            ask="101.02",
            high="101.10",
            close="101.01",
        )
    )
    assert entry["status"] == "FILLED_BUY"
    assert entry["fill"].fee_usd == Decimal("0.0000")

    exit_res = coord.process_event(
        make_event(
            "s-sl",
            symbol="AAPL",
            asset_class="stock",
            minute_offset=6,
            bid="98.00",
            ask="98.02",
            high="98.10",
            close="98.01",
        )
    )
    assert exit_res["status"] == "FILLED_SELL"
    assert exit_res["fill"].fee_usd > Decimal("0.0000")
    pos = ledger.get_all_positions()[0]
    assert pos.exit_reason == "STOP_LOSS"
    assert pos.realized_pnl_usd is not None and pos.realized_pnl_usd < Decimal("0")


def test_risk_vetoes_fail_closed() -> None:
    settings = AppSettings(mode="LIVE")
    rc = RiskController(settings)
    ev = make_event("r1", bid="100.00", ask="101.00", vol="1000", lag_s=12.0, source_status="disconnected")
    proposal = TradeProposal(
        event_id="r1",
        symbol="BTC/USD",
        asset_class="crypto",
        action="BUY",
        strategy_name="test",
        reason="test",
        ref_price=Decimal("101.00"),
        notional_usd=Decimal("90.00"),
        stop_loss_price=Decimal("80.00"),
        take_profit_price=Decimal("110.00"),
        max_hold_seconds=3600,
    )
    portfolio = PortfolioState(
        cash_usd=Decimal("250.00"),
        open_positions=[],
        daily_realized_pnl_usd=Decimal("-12.00"),
        monthly_api_spend_usd=Decimal("2.50"),
        paused=True,
    )
    verdict = rc.evaluate(proposal, ev, portfolio)
    assert not verdict.allowed
    joined = " ".join(verdict.reasons)
    for expected in [
        "LIVE_MODE_TECHNICALLY_DISABLED",
        "EMERGENCY_PAUSED",
        "FEED_STATUS_DISCONNECTED",
        "STALE_MARKET_DATA",
        "SPREAD_TOO_WIDE",
        "INSUFFICIENT_LIQUIDITY",
        "DAILY_LOSS_LIMIT_HIT",
        "MONTHLY_API_SPEND_CAP_HIT",
        "MAX_POSITION_SIZE_EXCEEDED",
        "MAX_RISK_PER_TRADE_EXCEEDED",
    ]:
        assert expected in joined


def test_duplicate_and_out_of_order_events(tmp_path: Path) -> None:
    settings = AppSettings(db_path=str(tmp_path / "dedup.db"))
    ledger = Ledger(settings.db_path, settings.starting_cash_usd)
    broker = PaperBroker(settings.fees)
    coord = TradingCoordinator(settings, ledger, broker)

    warmup_symbol(coord)
    ev = make_event("dup-1", minute_offset=4, bid="101.00", ask="101.05", high="101.10", close="101.02")
    assert coord.process_event(ev)["status"] == "FILLED_BUY"
    assert coord.process_event(ev)["status"] == "IGNORED_DUPLICATE"
    assert broker.submit_call_count == 1

    older = make_event("old-1", minute_offset=2, bid="90.00", ask="90.05", high="90.10", close="90.02")
    assert coord.process_event(older)["status"] == "IGNORED_OUT_OF_ORDER"


def test_stale_feed_does_not_fabricate_exit_fill(tmp_path: Path) -> None:
    settings = AppSettings(db_path=str(tmp_path / "outage.db"))
    ledger = Ledger(settings.db_path, settings.starting_cash_usd)
    broker = PaperBroker(settings.fees)
    coord = TradingCoordinator(settings, ledger, broker)

    warmup_symbol(coord)
    assert (
        coord.process_event(
            make_event("b1", minute_offset=4, bid="101.00", ask="101.05", high="101.10", close="101.02")
        )["status"]
        == "FILLED_BUY"
    )

    outage = coord.process_event(
        make_event(
            "out1",
            minute_offset=5,
            bid="90.00",
            ask="90.05",
            high="90.10",
            close="90.02",
            lag_s=20.0,
            source_status="disconnected",
        )
    )
    assert outage["status"] == "POSITION_FEED_STALE"
    open_pos = ledger.get_open_positions()
    assert len(open_pos) == 1
    assert open_pos[0].feed_health == "STALE_OR_DISCONNECTED"


def test_broker_rejection_leaves_no_ghost_position(tmp_path: Path) -> None:
    settings = AppSettings(db_path=str(tmp_path / "rej.db"))
    ledger = Ledger(settings.db_path, settings.starting_cash_usd)
    broker = PaperBroker(settings.fees)
    broker.simulate_reject_next = "SIMULATED_API_503"
    coord = TradingCoordinator(settings, ledger, broker)

    warmup_symbol(coord)
    res = coord.process_event(
        make_event("b1", minute_offset=4, bid="101.00", ask="101.05", high="101.10", close="101.02")
    )
    assert res["status"] == "REJECTED"
    assert ledger.get_open_positions() == []
    assert ledger.get_cash_usd() == settings.starting_cash_usd


def test_ambiguous_order_and_crash_restart_recovery(tmp_path: Path) -> None:
    db = tmp_path / "crash.db"
    settings = AppSettings(db_path=str(db))
    ledger1 = Ledger(db, settings.starting_cash_usd)
    broker = PaperBroker(settings.fees)
    broker.simulate_ambiguous_filled_next = True
    coord1 = TradingCoordinator(settings, ledger1, broker)

    warmup_symbol(coord1)
    res = coord1.process_event(
        make_event("amb-1", minute_offset=4, bid="101.00", ask="101.05", high="101.10", close="101.02")
    )
    assert res["status"] == "AMBIGUOUS"
    assert len(ledger1.get_open_positions()) == 0
    assert broker.submit_call_count == 1

    # Simulate process crash & restart with brand-new instances
    ledger2 = Ledger(db, settings.starting_cash_usd)
    coord2 = TradingCoordinator(settings, ledger2, broker)

    assert broker.submit_call_count == 1
    assert len(ledger2.get_open_positions()) == 1
    assert ledger2.get_cash_usd() < Decimal("200.0000")

    dup = coord2.process_event(
        make_event("amb-1", minute_offset=4, bid="101.00", ask="101.05", high="101.10", close="101.02")
    )
    assert dup["status"] == "IGNORED_DUPLICATE"
