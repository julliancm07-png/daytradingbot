from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from daytradingbot.core.config import AppSettings, RiskSettings, StrategySettings
from daytradingbot.eval.backtest import HistoricalBar, generate_synthetic_bars
from daytradingbot.eval.portfolio import run_portfolio_is_oos, run_portfolio_window

T0 = datetime(2026, 1, 1, 5, 0, 0, tzinfo=timezone.utc)


def _make_bars(symbol: str, prices: list[tuple[str, str]]) -> list[HistoricalBar]:
    """Build daily HistoricalBars from (open, close) tuples."""
    out: list[HistoricalBar] = []
    for i, (o_str, c_str) in enumerate(prices):
        o = Decimal(o_str)
        c = Decimal(c_str)
        out.append(
            HistoricalBar(
                symbol=symbol,
                asset_class="stock",
                ts=T0 + timedelta(days=i),
                open=o,
                high=max(o, c),
                low=min(o, c),
                close=c,
                volume_usd=Decimal("50000000"),
            )
        )
    return out


def test_shared_portfolio_enforces_max_concurrent_positions_across_symbols() -> None:
    # Both AAA and BBB dip sharply on day 3 -> both emit RSI(2)<=15 BUY signals on the exact same bar
    seq = [("100", "100"), ("100", "100"), ("100", "100"), ("99", "98"), ("98", "101")]
    bars = {
        "AAA": _make_bars("AAA", seq),
        "BBB": _make_bars("BBB", seq),
    }
    settings = AppSettings(
        risk=RiskSettings(max_concurrent_positions=1, max_position_usd=Decimal("50.00")),
        strategy=StrategySettings(
            strategy_type="mean_reversion",
            rsi_period=2,
            rsi_entry_below=Decimal("15"),
            rsi_exit_above=Decimal("65"),
            mean_exit_sma_bars=3,
            trend_ma_bars=0,
            order_notional_usd=Decimal("50.00"),
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
            max_hold_seconds=240 * 3600,
        ),
    )
    m = run_portfolio_window("SLOT_TEST", bars, settings, assumed_spread_bps=Decimal("2"), fill_mode="same-close")
    assert m.closed_trades == 1
    assert m.slot_vetoes >= 1  # BBB blocked because AAA already occupied the 1 allowed slot


def test_next_open_fill_uses_next_day_open_with_zero_lookahead() -> None:
    # Day 0-2 flat at 100. Day 3 dips to close=95 (triggers RSI(2)=0 BUY signal).
    # Day 4 gaps UP at open=99 and closes=101 (exits).
    # Day 5 opens=101, closes=101 (next-open exit executes at 101).
    seq = [
        ("100", "100"),
        ("100", "100"),
        ("100", "100"),
        ("99", "95"),
        ("99", "101"),
        ("101", "101"),
    ]
    bars = {"SPY": _make_bars("SPY", seq)}
    settings = AppSettings(
        strategy=StrategySettings(
            strategy_type="mean_reversion",
            rsi_period=2,
            rsi_entry_below=Decimal("15"),
            rsi_exit_above=Decimal("65"),
            mean_exit_sma_bars=3,
            trend_ma_bars=0,
            order_notional_usd=Decimal("50.00"),
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
            max_hold_seconds=240 * 3600,
        )
    )
    sc = run_portfolio_window("SC", bars, settings, assumed_spread_bps=Decimal("2"), fill_mode="same-close")
    no = run_portfolio_window("NO", bars, settings, assumed_spread_bps=Decimal("2"), fill_mode="next-open")

    # Same-close buys at ~95 and exits at ~101 (+$3+ profit).
    # Next-open buys at ~99 (missing the overnight gap!) and exits at ~101 (much smaller profit).
    assert sc.closed_trades == 1
    assert no.closed_trades == 1
    assert sc.net_pnl_usd > no.net_pnl_usd + Decimal("1.50")


def test_portfolio_is_oos_split_warms_up_oos_without_trading_in_warmup() -> None:
    bars = {
        "SPY": generate_synthetic_bars("SPY", "stock", count=100),
        "IWM": generate_synthetic_bars("IWM", "stock", count=100),
    }
    settings = AppSettings(
        strategy=StrategySettings(
            strategy_type="mean_reversion",
            rsi_period=2,
            rsi_entry_below=Decimal("25"),
            rsi_exit_above=Decimal("65"),
            mean_exit_sma_bars=5,
            trend_ma_bars=20,
            order_notional_usd=Decimal("50.00"),
            stop_loss_pct=Decimal("0.03"),
            take_profit_pct=Decimal("0.05"),
            max_hold_seconds=240 * 3600,
        )
    )
    is_m, oos_m = run_portfolio_is_oos(bars, settings, in_sample_ratio=0.60, fill_mode="next-open")
    assert is_m.end_ts < oos_m.start_ts
    assert oos_m.starting_cash_usd == Decimal("250.00")
    assert oos_m.warmup_bars_per_symbol == 20


def test_portfolio_metrics_reconcile_per_symbol_pnl() -> None:
    seq = [
        ("100", "100"),
        ("100", "100"),
        ("100", "100"),
        ("99", "96"),
        ("97", "101"),
        ("101", "101"),
    ]
    bars = {
        "SPY": _make_bars("SPY", seq),
        "GLD": _make_bars("GLD", seq),
    }
    settings = AppSettings(
        risk=RiskSettings(max_concurrent_positions=2, max_position_usd=Decimal("50.00")),
        strategy=StrategySettings(
            strategy_type="mean_reversion",
            rsi_period=2,
            rsi_entry_below=Decimal("15"),
            rsi_exit_above=Decimal("65"),
            mean_exit_sma_bars=3,
            trend_ma_bars=0,
            order_notional_usd=Decimal("50.00"),
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
            max_hold_seconds=240 * 3600,
        ),
    )
    m = run_portfolio_window("RECON", bars, settings, assumed_spread_bps=Decimal("2"), fill_mode="same-close")
    assert m.closed_trades == 2
    assert m.open_trades_at_end == 0
    per_sym_sum = sum((c.realized_pnl_usd for c in m.per_symbol), Decimal("0"))
    assert per_sym_sum == m.net_pnl_usd
