from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import httpx

from daytradingbot.core.config import AppSettings
from daytradingbot.core.ledger import Ledger
from daytradingbot.core.models import AssetClass, MarketEvent, q_usd
from daytradingbot.execution.paper_broker import PaperBroker
from daytradingbot.ingest.alpaca_feed import ALPACA_DATA_BASE, parse_iso_utc
from daytradingbot.monitor.coordinator import TradingCoordinator


@dataclass
class HistoricalBar:
    symbol: str
    asset_class: AssetClass
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_usd: Decimal


@dataclass
class WindowMetrics:
    label: str
    bars_count: int
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
    win_rate_pct: Decimal
    profit_factor: Decimal
    max_drawdown_pct: Decimal
    buy_and_hold_return_pct: Decimal


def fetch_alpaca_historical_bars(
    symbol: str,
    asset_class: AssetClass,
    days: int = 14,
    timeframe: str = "15Min",
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    http_client: Optional[httpx.Client] = None,
    max_pages: int = 25,
) -> list[HistoricalBar]:
    """Fetch full historical OHLCV bars from Alpaca Data API, following next_page_token."""
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    key = api_key if api_key is not None else os.getenv("ALPACA_API_KEY", "")
    secret = secret_key if secret_key is not None else os.getenv("ALPACA_SECRET_KEY", "")
    headers: dict[str, str] = {"accept": "application/json"}
    if key and secret and not key.startswith("replace_me"):
        headers["APCA-API-KEY-ID"] = key.strip()
        headers["APCA-API-SECRET-KEY"] = secret.strip()

    if asset_class == "crypto":
        url = "/v1beta3/crypto/us/bars"
        base_params: dict[str, str] = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "limit": "1000",
        }
    else:
        url = "/v2/stocks/bars"
        base_params = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "feed": "iex",
            "limit": "1000",
        }

    bars_by_ts: dict[datetime, HistoricalBar] = {}
    owns_client = http_client is None
    client = http_client or httpx.Client(base_url=ALPACA_DATA_BASE, timeout=15.0)
    try:
        page_token: Optional[str] = None
        for _ in range(max_pages):
            params = dict(base_params)
            if page_token:
                params["page_token"] = page_token
            resp = client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
            raw_bars = payload.get("bars", {}).get(symbol, [])
            for b in raw_bars:
                ts = parse_iso_utc(str(b["t"]))
                c = Decimal(str(b["c"]))
                v = Decimal(str(b.get("v", "0") or "0"))
                est_24h_vol = max(q_usd(v * c * Decimal("96")), Decimal("10000000"))
                bars_by_ts[ts] = HistoricalBar(
                    symbol=symbol,
                    asset_class=asset_class,
                    ts=ts,
                    open=Decimal(str(b["o"])),
                    high=Decimal(str(b["h"])),
                    low=Decimal(str(b["l"])),
                    close=c,
                    volume_usd=est_24h_vol,
                )
            page_token = payload.get("next_page_token")
            if not page_token:
                break
    finally:
        if owns_client:
            client.close()

    return [bars_by_ts[k] for k in sorted(bars_by_ts.keys())]


def generate_synthetic_bars(
    symbol: str = "BTC/USD",
    asset_class: AssetClass = "crypto",
    count: int = 240,
) -> list[HistoricalBar]:
    """Deterministic synthetic OHLCV bars for offline demo & CI tests (NOT real market data)."""
    t0 = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
    out: list[HistoricalBar] = []
    price = 90000.0 if asset_class == "crypto" else 500.0
    for i in range(count):
        wave = math.sin(i / 8.0) * 0.008 + math.sin(i / 21.0) * 0.012
        drift = 0.00015
        o = price
        c = o * (1.0 + drift + (wave * 0.15))
        h = max(o, c) * (1.0 + 0.0015)
        low = min(o, c) * (1.0 - 0.0015)
        price = c
        out.append(
            HistoricalBar(
                symbol=symbol,
                asset_class=asset_class,
                ts=t0 + timedelta(minutes=15 * i),
                open=q_usd(Decimal(str(o))),
                high=q_usd(Decimal(str(h))),
                low=q_usd(Decimal(str(low))),
                close=q_usd(Decimal(str(c))),
                volume_usd=Decimal("50000000"),
            )
        )
    return out


def run_backtest_window(
    label: str,
    bars: list[HistoricalBar],
    settings: AppSettings,
    assumed_spread_bps: Decimal = Decimal("5"),
) -> WindowMetrics:
    """
    Runs a strictly chronological historical window through the exact PaperBroker
    and RiskController. Historical bars lack L1 quotes, so we explicitly model a
    symmetric bid/ask spread of `assumed_spread_bps` around each bar close.
    """
    if len(bars) < 2:
        raise ValueError("Need at least 2 historical bars to evaluate a window.")

    half_spread = (assumed_spread_bps / Decimal("2")) / Decimal("10000")
    with tempfile.TemporaryDirectory() as tmp:
        eval_settings = settings.model_copy(
            update={
                "mode": "REPLAY",
                "db_path": str(Path(tmp) / f"{label.lower()}.db"),
            }
        )
        ledger = Ledger(eval_settings.db_path, eval_settings.starting_cash_usd)
        broker = PaperBroker(eval_settings.fees)
        coord = TradingCoordinator(eval_settings, ledger, broker)

        max_dd = Decimal("0.0000")
        for idx, b in enumerate(bars):
            bid = q_usd(b.close * (Decimal("1") - half_spread))
            ask = q_usd(b.close * (Decimal("1") + half_spread))
            ev = MarketEvent(
                event_id=f"BT-{label}-{idx}-{b.symbol.replace('/', '_')}",
                symbol=b.symbol,
                asset_class=b.asset_class,
                event_time=b.ts,
                received_time=b.ts + timedelta(milliseconds=250),
                bid=bid,
                ask=ask,
                bar_open=b.open,
                bar_high=b.high,
                bar_low=b.low,
                bar_close=b.close,
                volume_24h_usd=b.volume_usd,
                source_status="ok",
                bar_time=b.ts,
            )
            coord.process_event(ev)
            snap = ledger.get_latest_equity_snapshot()
            if snap is not None:
                dd = Decimal(str(snap["drawdown_pct"]))
                if dd > max_dd:
                    max_dd = dd

        latest_snap = ledger.get_latest_equity_snapshot()
        ending_equity = (
            Decimal(str(latest_snap["total_equity_usd"]))
            if latest_snap is not None
            else eval_settings.starting_cash_usd
        )
        start_cash = eval_settings.starting_cash_usd
        net_pnl = q_usd(ending_equity - start_cash)
        net_ret_pct = q_usd((net_pnl / start_cash) * Decimal("100"))

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

        bh_ret = q_usd(((bars[-1].close - bars[0].close) / bars[0].close) * Decimal("100"))
        return WindowMetrics(
            label=label,
            bars_count=len(bars),
            start_ts=bars[0].ts.isoformat(),
            end_ts=bars[-1].ts.isoformat(),
            starting_cash_usd=start_cash,
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
            win_rate_pct=win_rate,
            profit_factor=pf,
            max_drawdown_pct=max_dd,
            buy_and_hold_return_pct=bh_ret,
        )


def run_is_oos_evaluation(
    bars: list[HistoricalBar],
    settings: AppSettings,
    in_sample_ratio: float = 0.60,
    assumed_spread_bps: Decimal = Decimal("5"),
) -> tuple[WindowMetrics, WindowMetrics]:
    """Chronological In-Sample / Out-of-Sample split (strictly non-overlapping)."""
    if len(bars) < 20:
        raise ValueError("Need at least 20 bars for an In-Sample / Out-of-Sample split.")
    split_idx = int(len(bars) * in_sample_ratio)
    is_bars = bars[:split_idx]
    oos_bars = bars[split_idx:]
    assert is_bars[-1].ts < oos_bars[0].ts, "Chronological split violated!"
    is_metrics = run_backtest_window("IN_SAMPLE", is_bars, settings, assumed_spread_bps)
    oos_metrics = run_backtest_window("OUT_OF_SAMPLE", oos_bars, settings, assumed_spread_bps)
    return is_metrics, oos_metrics
