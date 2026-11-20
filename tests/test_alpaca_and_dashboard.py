from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daytradingbot.core.config import AppSettings, FeedSettings
from daytradingbot.core.ledger import Ledger
from daytradingbot.core.models import MarketEvent
from daytradingbot.dashboard.app import create_app
from daytradingbot.execution.paper_broker import PaperBroker
from daytradingbot.ingest.alpaca_feed import AlpacaMarketDataClient, parse_iso_utc
from daytradingbot.monitor.coordinator import TradingCoordinator


def test_parse_nanosecond_iso_timestamp() -> None:
    dt = parse_iso_utc("2026-11-19T23:10:05.507608832Z")
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2026 and dt.microsecond == 507608


def test_alpaca_crypto_snapshot_parsing() -> None:
    sample_payload = {
        "snapshots": {
            "BTC/USD": {
                "dailyBar": {"c": 91000, "h": 92000, "l": 90000, "o": 90500, "t": "2026-11-19T05:00:00Z", "v": 10, "vw": 91000},
                "prevDailyBar": {"c": 90500, "h": 91500, "l": 89500, "o": 90000, "t": "2026-11-18T05:00:00Z", "v": 250, "vw": 90200},
                "latestQuote": {"ap": 91010, "as": 0.5, "bp": 91000, "bs": 0.5, "t": "2026-11-19T23:10:00.123456789Z"},
                "minuteBar": {"c": 91005, "h": 91020, "l": 90990, "o": 91000, "t": "2026-11-19T23:09:00Z", "v": 1.2, "vw": 91005},
            }
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1beta3/crypto/us/snapshots"
        return httpx.Response(200, json=sample_payload, headers={"X-RateLimit-Remaining": "195"})

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(base_url="https://data.alpaca.markets", transport=transport) as hc:
            client = AlpacaMarketDataClient(FeedSettings(crypto_symbols=["BTC/USD"]), http_client=hc)
            return await client.fetch_crypto_snapshots()

    batch = asyncio.run(_run())
    assert batch.status == "OK"
    assert batch.rate_limit_remaining == 195
    assert len(batch.events) == 1
    ev = batch.events[0]
    assert ev.symbol == "BTC/USD"
    assert ev.bid == Decimal("91000")
    assert ev.ask == Decimal("91010")
    # Uses max(dailyBar, prevDailyBar) so midnight UTC rollovers don't look illiquid
    assert ev.volume_24h_usd == Decimal("22550000.0000")
    assert ev.bar_time == datetime(2026, 11, 19, 23, 9, 0, tzinfo=timezone.utc)


def test_alpaca_rate_limit_429_triggers_degraded_backoff() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limit exceeded")

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(base_url="https://data.alpaca.markets", transport=transport) as hc:
            client = AlpacaMarketDataClient(FeedSettings(crypto_symbols=["BTC/USD"]), http_client=hc)
            return await client.fetch_crypto_snapshots()

    batch = asyncio.run(_run())
    assert batch.status == "DEGRADED"
    assert batch.retry_after_seconds >= 10.0
    assert batch.events == []


def test_intra_minute_quote_waits_for_new_bar_on_entry_but_exits_immediately(tmp_path: Path) -> None:
    settings = AppSettings(mode="PAPER", db_path=str(tmp_path / "bars.db"))
    ledger = Ledger(settings.db_path, settings.starting_cash_usd)
    broker = PaperBroker(settings.fees)
    coord = TradingCoordinator(settings, ledger, broker)

    t0 = datetime(2026, 11, 19, 23, 0, 0, tzinfo=timezone.utc)

    def ev(eid: str, q_sec: int, bar_min: int, bid: str, ask: str, high: str, close: str) -> MarketEvent:
        qt = t0 + timedelta(seconds=q_sec)
        bt = t0 + timedelta(minutes=bar_min)
        return MarketEvent(
            event_id=eid,
            symbol="BTC/USD",
            asset_class="crypto",
            event_time=qt,
            received_time=qt + timedelta(milliseconds=200),
            bid=Decimal(bid),
            ask=Decimal(ask),
            bar_open=Decimal(bid),
            bar_high=Decimal(high),
            bar_low=Decimal(bid),
            bar_close=Decimal(close),
            volume_24h_usd=Decimal("50000000"),
            bar_time=bt,
        )

    # 3 distinct minute bars for warmup
    for m in (1, 2, 3):
        assert coord.process_event(ev(f"w{m}", m * 60, m, "100", "100.05", "100.10", "100.02"))["status"] == "NO_TRADE"

    # Second quote inside minute 3 must NOT advance strategy bar buffer
    same_bar = coord.process_event(ev("w3b", 183, 3, "102", "102.05", "102.10", "102.02"))
    assert same_bar["status"] == "NO_TRADE"
    assert same_bar["reason"] == "WAITING_NEXT_BAR"

    # New minute bar 4 triggers breakout entry
    entry = coord.process_event(ev("b4", 240, 4, "101.00", "101.05", "101.10", "101.02"))
    assert entry["status"] == "FILLED_BUY"

    # Intra-minute quote 3 seconds later inside the SAME minute bar 4 hits take profit and exits immediately
    fast_exit = coord.process_event(ev("x4", 243, 4, "106.00", "106.05", "106.10", "106.02"))
    assert fast_exit["status"] == "FILLED_SELL"
    assert len(ledger.get_open_positions()) == 0


def test_dashboard_endpoints_and_emergency_pause(tmp_path: Path) -> None:
    cfg_file = tmp_path / "test_settings.toml"
    db_file = tmp_path / "dash.db"
    cfg_file.write_text(
        f'mode = "PAPER"\nstarting_cash_usd = "250.00"\ndb_path = "{db_file.as_posix()}"\n',
        encoding="utf-8",
    )
    app = create_app(cfg_file, enable_live_poller=False)
    with TestClient(app) as client:
        html_resp = client.get("/")
        assert html_resp.status_code == 200
        assert "daytradingbot" in html_resp.text

        st1 = client.get("/api/state").json()
        assert st1["mode"] == "PAPER"
        assert st1["live_execution_disabled"] is True
        assert st1["paused"] is False
        assert st1["summary"]["total_equity_usd"] == "250.0000"

        assert client.post("/api/pause").json()["paused"] is True
        assert client.get("/api/state").json()["paused"] is True
        assert client.post("/api/resume").json()["paused"] is False
