from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


class RiskSettings(BaseModel):
    max_position_usd: Decimal = Decimal("50.00")
    max_risk_per_trade_usd: Decimal = Decimal("3.75")
    max_total_exposure_usd: Decimal = Decimal("150.00")
    max_concurrent_positions: int = 2
    daily_loss_limit_usd: Decimal = Decimal("10.00")
    max_spread_bps: Decimal = Decimal("25")
    min_24h_volume_usd: Decimal = Decimal("5000000")
    max_quote_age_seconds: int = 5
    max_monthly_api_spend_usd: Decimal = Decimal("2.00")


class FeeSettings(BaseModel):
    crypto_taker_fee_rate: Decimal = Decimal("0.0025")
    stock_sec_fee_rate: Decimal = Decimal("0.0000206")
    stock_finra_cat_per_share: Decimal = Decimal("0.000003")
    base_slippage_bps: Decimal = Decimal("3")
    impact_factor_bps: Decimal = Decimal("5000")


class StrategySettings(BaseModel):
    strategy_type: Literal["breakout", "mean_reversion"] = "breakout"
    lookback_bars: int = 3
    min_breakout_pct: Decimal = Decimal("0.003")
    order_notional_usd: Decimal = Decimal("50.00")
    stop_loss_pct: Decimal = Decimal("0.025")
    take_profit_pct: Decimal = Decimal("0.045")
    max_hold_seconds: int = 3600
    trend_ma_bars: int = 0
    # Mean-reversion parameters (used when strategy_type == "mean_reversion")
    rsi_period: int = 7
    rsi_entry_below: Decimal = Decimal("30")
    rsi_exit_above: Decimal = Decimal("60")
    mean_exit_sma_bars: int = 10


class FeedSettings(BaseModel):
    crypto_symbols: list[str] = Field(default_factory=lambda: ["BTC/USD", "ETH/USD", "SOL/USD"])
    stock_symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "AAPL"])
    crypto_loc: str = "us"
    stock_feed: str = "iex"
    poll_interval_seconds: float = 3.0
    request_timeout_seconds: float = 8.0


class DashboardSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    sse_interval_seconds: float = 2.0


class LLMSettings(BaseModel):
    enabled: bool = False
    mode: Literal["shadow", "filter"] = "shadow"
    model: str = "google/gemini-2.5-flash-lite"
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 6.0
    max_tokens: int = 220
    fallback_on_error: Literal["NO_TRADE", "BASELINE"] = "NO_TRADE"
    estimated_cost_per_1k_input_usd: Decimal = Decimal("0.0001")
    estimated_cost_per_1k_output_usd: Decimal = Decimal("0.0004")


class AppSettings(BaseModel):
    mode: Literal["REPLAY", "PAPER", "LIVE"] = "REPLAY"
    starting_cash_usd: Decimal = Decimal("250.00")
    db_path: str = "data/replay_demo.db"
    risk: RiskSettings = RiskSettings()
    fees: FeeSettings = FeeSettings()
    strategy: StrategySettings = StrategySettings()
    feed: FeedSettings = FeedSettings()
    dashboard: DashboardSettings = DashboardSettings()
    llm: LLMSettings = LLMSettings()


def load_settings(path: str | Path) -> AppSettings:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return AppSettings.model_validate(raw)
