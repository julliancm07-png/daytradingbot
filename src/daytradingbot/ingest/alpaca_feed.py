from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx

from daytradingbot.core.config import FeedSettings
from daytradingbot.core.models import MarketEvent, q_usd

ALPACA_DATA_BASE = "https://data.alpaca.markets"
_FRAC_RE = re.compile(r"(\.\d+)")


def parse_iso_utc(raw: str) -> datetime:
    """Parse Alpaca ISO-8601 timestamps (including nanosecond precision) safely to UTC."""
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    def _trim_frac(match: re.Match[str]) -> str:
        frac = match.group(1)[1:]
        return "." + (frac[:6].ljust(6, "0"))

    s = _FRAC_RE.sub(_trim_frac, s, count=1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_valid_key(value: Optional[str]) -> bool:
    if not value:
        return False
    lower = value.strip().lower()
    return bool(lower) and not lower.startswith("replace_me")


def _compute_volume_24h_usd(snap: dict[str, Any]) -> Decimal:
    candidates: list[Decimal] = []
    for key in ("dailyBar", "prevDailyBar"):
        bar = snap.get(key)
        if isinstance(bar, dict):
            vol = Decimal(str(bar.get("v", "0") or "0"))
            px = Decimal(str(bar.get("vw") or bar.get("c") or "0"))
            if vol > Decimal("0") and px > Decimal("0"):
                candidates.append(q_usd(vol * px))
    return max(candidates) if candidates else Decimal("0")


@dataclass
class FeedBatchResult:
    source: str
    events: list[MarketEvent] = field(default_factory=list)
    status: str = "OK"  # OK, DEGRADED, DISCONNECTED, IDLE
    latency_ms: float = 0.0
    rate_limit_remaining: Optional[int] = None
    retry_after_seconds: float = 0.0
    message: str = ""


class AlpacaMarketDataClient:
    """Async REST snapshot client for Alpaca 24/7 Crypto & US Stocks (IEX)."""

    def __init__(
        self,
        feed_settings: FeedSettings,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.cfg = feed_settings
        self.api_key = api_key if api_key is not None else os.getenv("ALPACA_API_KEY", "")
        self.secret_key = secret_key if secret_key is not None else os.getenv("ALPACA_SECRET_KEY", "")
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=ALPACA_DATA_BASE,
            timeout=self.cfg.request_timeout_seconds,
            headers={"accept": "application/json"},
        )

    @property
    def has_api_keys(self) -> bool:
        return _is_valid_key(self.api_key) and _is_valid_key(self.secret_key)

    def _auth_headers(self) -> dict[str, str]:
        if self.has_api_keys:
            return {
                "APCA-API-KEY-ID": self.api_key.strip(),
                "APCA-API-SECRET-KEY": self.secret_key.strip(),
            }
        return {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_crypto_snapshots(self) -> FeedBatchResult:
        if not self.cfg.crypto_symbols:
            return FeedBatchResult(source="alpaca_crypto", status="IDLE", message="No crypto symbols configured")

        symbols_param = ",".join(self.cfg.crypto_symbols)
        url = f"/v1beta3/crypto/{self.cfg.crypto_loc}/snapshots"
        return await self._fetch_and_parse(
            source="alpaca_crypto",
            url=url,
            params={"symbols": symbols_param},
            asset_class="crypto",
            unwrap_snapshots_key=True,
        )

    async def fetch_stock_snapshots(self) -> FeedBatchResult:
        if not self.cfg.stock_symbols:
            return FeedBatchResult(source="alpaca_stocks", status="IDLE", message="No stock symbols configured")
        if not self.has_api_keys:
            return FeedBatchResult(
                source="alpaca_stocks",
                status="IDLE",
                message="Add free ALPACA_API_KEY & ALPACA_SECRET_KEY to .env to enable US stock quotes",
            )

        symbols_param = ",".join(self.cfg.stock_symbols)
        return await self._fetch_and_parse(
            source="alpaca_stocks",
            url="/v2/stocks/snapshots",
            params={"symbols": symbols_param, "feed": self.cfg.stock_feed},
            asset_class="stock",
            unwrap_snapshots_key=False,
        )

    async def _fetch_and_parse(
        self,
        source: str,
        url: str,
        params: dict[str, str],
        asset_class: str,
        unwrap_snapshots_key: bool,
    ) -> FeedBatchResult:
        t0 = time.perf_counter()
        try:
            resp = await self._client.get(url, params=params, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            return FeedBatchResult(
                source=source,
                status="DISCONNECTED",
                latency_ms=latency_ms,
                message=f"Network error: {exc}",
            )

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        rl_raw = resp.headers.get("X-RateLimit-Remaining")
        rl_rem = int(rl_raw) if rl_raw and rl_raw.isdigit() else None

        if resp.status_code == 429:
            return FeedBatchResult(
                source=source,
                status="DEGRADED",
                latency_ms=latency_ms,
                rate_limit_remaining=0,
                retry_after_seconds=10.0,
                message="HTTP 429 rate limit hit; backing off 10s",
            )

        if resp.status_code >= 400:
            return FeedBatchResult(
                source=source,
                status="DISCONNECTED",
                latency_ms=latency_ms,
                rate_limit_remaining=rl_rem,
                message=f"HTTP {resp.status_code}: {resp.text[:160]}",
            )

        payload = resp.json()
        raw_map = payload.get("snapshots", {}) if unwrap_snapshots_key else payload
        if not isinstance(raw_map, dict):
            return FeedBatchResult(
                source=source,
                status="DEGRADED",
                latency_ms=latency_ms,
                rate_limit_remaining=rl_rem,
                message="Unexpected snapshot JSON structure",
            )

        received_at = datetime.now(timezone.utc)
        events: list[MarketEvent] = []
        for symbol, snap in raw_map.items():
            if not isinstance(snap, dict):
                continue
            ev = self._parse_single_snapshot(symbol, asset_class, snap, received_at)
            if ev is not None:
                events.append(ev)

        return FeedBatchResult(
            source=source,
            events=events,
            status="OK" if events else "DEGRADED",
            latency_ms=latency_ms,
            rate_limit_remaining=rl_rem,
            message=f"Fetched {len(events)} snapshots in {latency_ms}ms",
        )

    def _parse_single_snapshot(
        self,
        symbol: str,
        asset_class: str,
        snap: dict[str, Any],
        received_at: datetime,
    ) -> Optional[MarketEvent]:
        quote = snap.get("latestQuote")
        bar = snap.get("minuteBar") or snap.get("dailyBar")
        if not isinstance(quote, dict) or not isinstance(bar, dict):
            return None

        bid = Decimal(str(quote.get("bp", "0") or "0"))
        ask = Decimal(str(quote.get("ap", "0") or "0"))
        quote_t_raw = quote.get("t")
        bar_t_raw = bar.get("t")
        if not quote_t_raw or not bar_t_raw:
            return None

        quote_time = parse_iso_utc(str(quote_t_raw))
        bar_time = parse_iso_utc(str(bar_t_raw))
        bar_open = Decimal(str(bar.get("o", "0") or "0"))
        bar_high = Decimal(str(bar.get("h", "0") or "0"))
        bar_low = Decimal(str(bar.get("l", "0") or "0"))
        bar_close = Decimal(str(bar.get("c", "0") or "0"))
        vol_24h = _compute_volume_24h_usd(snap)

        # Stable deterministic event_id derived from symbol + quote timestamp + bar timestamp
        event_id = f"ALP-{symbol.replace('/', '_')}-{quote_time.strftime('%Y%m%dT%H%M%S%f')}-{bar_time.strftime('%H%M')}"
        return MarketEvent(
            event_id=event_id,
            symbol=symbol,
            asset_class=asset_class,  # type: ignore[arg-type]
            event_time=quote_time,
            received_time=received_at,
            bid=bid,
            ask=ask,
            bar_open=bar_open,
            bar_high=bar_high,
            bar_low=bar_low,
            bar_close=bar_close,
            volume_24h_usd=vol_24h,
            source_status="ok",
            bar_time=bar_time,
        )
