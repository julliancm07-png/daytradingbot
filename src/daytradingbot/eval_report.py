from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from daytradingbot.core.config import load_settings
from daytradingbot.core.ledger import Ledger
from daytradingbot.eval.backtest import (
    WindowMetrics,
    fetch_alpaca_historical_bars,
    generate_synthetic_bars,
    run_is_oos_evaluation,
)


def _print_window(m: WindowMetrics) -> None:
    print(f"  [{m.label}] Bars: {m.bars_count} ({m.start_ts[:16]} -> {m.end_ts[:16]})")
    print(
        f"    Start=${m.starting_cash_usd:.2f} | End=${m.ending_equity_usd:.4f} | "
        f"NetPnL=${m.net_pnl_usd:+.4f} ({m.net_return_pct:+.2f}%)"
    )
    print(
        f"    Fees=${m.total_fees_usd:.4f} | ClosedTrades={m.closed_trades} "
        f"(OpenAtEnd={m.open_trades_at_end}) | Exits: TP={m.tp_exits}, SL={m.sl_exits}, "
        f"MR={m.mr_exits}, TIME={m.time_stop_exits}"
    )
    print(
        f"    WinRate={m.win_rate_pct:.1f}% | PF={m.profit_factor:.2f} | "
        f"MaxDrawdown={m.max_drawdown_pct:.2f}% | Buy&Hold Benchmark={m.buy_and_hold_return_pct:+.2f}%"
    )


def _print_verdict(oos_m: WindowMetrics, label: str = "OUT-OF-SAMPLE ONLY") -> None:
    print(f"\nEDGE VERDICT ({label}):")
    if oos_m.closed_trades < 5:
        print(
            "  [INCONCLUSIVE] Fewer than 5 Out-of-Sample closed trades — sample size is too small "
            "to claim an edge. Do NOT trade live."
        )
    elif oos_m.net_pnl_usd <= Decimal("0"):
        print(
            "  [NO EDGE DEMONSTRATED] Out-of-Sample net PnL after fees/spread is <= $0. "
            "Keep in paper/research mode; do NOT deploy live capital."
        )
    else:
        print(
            "  [PROMISING OOS — NOT YET PROVEN] Positive OOS net return after modeled costs. "
            "Next step: validate over 30+ forward paper trades before considering live mode."
        )


def _summarize_forward_paper_ledger(db_path: Path, starting_cash: Decimal) -> None:
    print("\n" + "-" * 76)
    print("FORWARD PAPER LEDGER SUMMARY (LIVE DATA / SIMULATED FILLS)")
    print("-" * 76)
    if not db_path.exists():
        print(f"  No forward paper ledger found yet at {db_path}.")
        return
    ledger = Ledger(db_path, starting_cash)
    closed = [p for p in ledger.get_all_positions() if p.status == "CLOSED"]
    open_pos = ledger.get_open_positions()
    audit = ledger.get_audit_log()
    shadow_approve = sum(1 for a in audit if a["status"] == "SHADOW_APPROVE")
    shadow_veto = sum(1 for a in audit if a["status"] == "SHADOW_VETO")
    print(
        f"  Cash=${ledger.get_cash_usd():.4f} | OpenPos={len(open_pos)} | ClosedTrades={len(closed)} | "
        f"Fees=${ledger.get_cumulative_fees_usd():.4f} | API Spend=${ledger.get_monthly_api_spend_usd():.4f}"
    )
    print(
        f"  OpenRouter Shadow Reviews Logged: APPROVE={shadow_approve}, VETO={shadow_veto}"
    )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Run In-Sample / Out-of-Sample evaluation for deterministic rules strategies."
    )
    parser.add_argument("--config", default="config/settings.paper.toml")
    parser.add_argument("--symbol", default="BTC/USD")
    parser.add_argument("--asset-class", choices=["crypto", "stock"], default="crypto")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--timeframe", default="15Min")
    parser.add_argument("--strategy", choices=["breakout", "mean_reversion"], default=None)
    parser.add_argument("--trend-ma-bars", type=int, default=None, help="Optional SMA trend filter length.")
    parser.add_argument("--max-hold-hours", type=float, default=None, help="Optional hold-time override in hours.")
    parser.add_argument("--tp-pct", type=str, default=None, help="Optional take-profit fraction (e.g. 0.025).")
    parser.add_argument("--sl-pct", type=str, default=None, help="Optional stop-loss fraction (e.g. 0.020).")
    parser.add_argument("--breakout-pct", type=str, default=None, help="Optional breakout threshold.")
    parser.add_argument("--rsi-period", type=int, default=None, help="RSI lookback period for mean_reversion.")
    parser.add_argument("--rsi-entry", type=str, default=None, help="RSI entry threshold (e.g. 30).")
    parser.add_argument("--rsi-exit", type=str, default=None, help="RSI exit threshold (e.g. 60).")
    parser.add_argument(
        "--compare-trend",
        action="store_true",
        help="Compare raw breakout baseline against a 20-bar SMA trend-filtered variant.",
    )
    parser.add_argument(
        "--compare-strategies",
        action="store_true",
        help="Benchmark Breakout vs. RSI Pullback Mean-Reversion (with & without SMA50 filter) on identical bars.",
    )
    parser.add_argument(
        "--offline-demo",
        action="store_true",
        help="Run deterministic synthetic bars instead of fetching Alpaca history.",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)
    strat_updates: dict[str, object] = {}
    if args.strategy is not None:
        strat_updates["strategy_type"] = args.strategy
    if args.trend_ma_bars is not None:
        strat_updates["trend_ma_bars"] = args.trend_ma_bars
    if args.max_hold_hours is not None:
        strat_updates["max_hold_seconds"] = int(args.max_hold_hours * 3600)
    if args.tp_pct is not None:
        strat_updates["take_profit_pct"] = Decimal(args.tp_pct)
    if args.sl_pct is not None:
        strat_updates["stop_loss_pct"] = Decimal(args.sl_pct)
    if args.breakout_pct is not None:
        strat_updates["min_breakout_pct"] = Decimal(args.breakout_pct)
    if args.rsi_period is not None:
        strat_updates["rsi_period"] = args.rsi_period
    if args.rsi_entry is not None:
        strat_updates["rsi_entry_below"] = Decimal(args.rsi_entry)
    if args.rsi_exit is not None:
        strat_updates["rsi_exit_above"] = Decimal(args.rsi_exit)
    if strat_updates:
        settings = settings.model_copy(
            update={"strategy": settings.strategy.model_copy(update=strat_updates)}
        )

    spread_bps = Decimal("5") if args.asset_class == "crypto" else Decimal("2")

    if args.offline_demo:
        source_label = "SYNTHETIC OFFLINE DEMO BARS (NOT REAL MARKET DATA)"
        bars = generate_synthetic_bars(args.symbol, args.asset_class, count=240)
    else:
        source_label = f"ALPACA HISTORICAL {args.timeframe} BARS ({args.days}d, PAGINATED)"
        bars = fetch_alpaca_historical_bars(
            symbol=args.symbol,
            asset_class=args.asset_class,
            days=args.days,
            timeframe=args.timeframe,
        )

    print("=" * 76)
    print(f"STRATEGY EVALUATION REPORT — {args.symbol} ({args.asset_class.upper()})")
    print(f"Data Source: {source_label} | Assumed Spread: {spread_bps} bps + Slippage/Fees")
    print("=" * 76)

    if args.compare_strategies:
        hold_s = 72 * 3600 if args.asset_class == "stock" else 48 * 3600
        variants = [
            (
                "A) BREAKOUT BASELINE (SMA=20 | Hold=72h/48h | TP=2.5% | SL=1.5%)",
                settings.strategy.model_copy(
                    update={
                        "strategy_type": "breakout",
                        "trend_ma_bars": 20,
                        "max_hold_seconds": hold_s,
                        "take_profit_pct": Decimal("0.025"),
                        "stop_loss_pct": Decimal("0.015"),
                    }
                ),
            ),
            (
                "B) RSI MEAN-REVERSION — UNFILTERED (RSI7<=30 -> SMA10/RSI60 | SL=2.0% | TP=3.0%)",
                settings.strategy.model_copy(
                    update={
                        "strategy_type": "mean_reversion",
                        "trend_ma_bars": 0,
                        "rsi_period": 7,
                        "rsi_entry_below": Decimal("30"),
                        "rsi_exit_above": Decimal("60"),
                        "mean_exit_sma_bars": 10,
                        "max_hold_seconds": hold_s,
                        "take_profit_pct": Decimal("0.030"),
                        "stop_loss_pct": Decimal("0.020"),
                    }
                ),
            ),
            (
                "C) RSI PULLBACK IN UPTREND (Close>SMA50 & RSI7<=30 -> SMA10/RSI60 | SL=2.0% | TP=3.0%)",
                settings.strategy.model_copy(
                    update={
                        "strategy_type": "mean_reversion",
                        "trend_ma_bars": 50,
                        "rsi_period": 7,
                        "rsi_entry_below": Decimal("30"),
                        "rsi_exit_above": Decimal("60"),
                        "mean_exit_sma_bars": 10,
                        "max_hold_seconds": hold_s,
                        "take_profit_pct": Decimal("0.030"),
                        "stop_loss_pct": Decimal("0.020"),
                    }
                ),
            ),
        ]
        best_oos: WindowMetrics | None = None
        best_name = ""
        for title, strat_cfg in variants:
            v_settings = settings.model_copy(update={"strategy": strat_cfg})
            v_is, v_oos = run_is_oos_evaluation(
                bars, v_settings, in_sample_ratio=0.60, assumed_spread_bps=spread_bps
            )
            print(f"\n--- {title} ---")
            _print_window(v_is)
            print()
            _print_window(v_oos)
            if best_oos is None or v_oos.net_pnl_usd > best_oos.net_pnl_usd:
                best_oos = v_oos
                best_name = title.split(")")[0] + ")"
        assert best_oos is not None
        _print_verdict(best_oos, label=f"BEST OOS VARIANT: {best_name}")
    else:
        print(
            f"Strategy: {settings.strategy.strategy_type} | SMA={settings.strategy.trend_ma_bars} | "
            f"Hold={settings.strategy.max_hold_seconds / 3600:.1f}h | "
            f"TP={settings.strategy.take_profit_pct * 100:.2f}% | SL={settings.strategy.stop_loss_pct * 100:.2f}%"
        )
        is_m, oos_m = run_is_oos_evaluation(
            bars, settings, in_sample_ratio=0.60, assumed_spread_bps=spread_bps
        )
        _print_window(is_m)
        print()
        _print_window(oos_m)

        if args.compare_trend:
            cmp_strat = settings.strategy.model_copy(
                update={
                    "trend_ma_bars": 20,
                    "max_hold_seconds": 12 * 3600 if args.asset_class == "stock" else 24 * 3600,
                    "take_profit_pct": Decimal("0.018") if args.asset_class == "stock" else Decimal("0.045"),
                    "stop_loss_pct": Decimal("0.010") if args.asset_class == "stock" else Decimal("0.025"),
                }
            )
            cmp_settings = settings.model_copy(update={"strategy": cmp_strat})
            cmp_is, cmp_oos = run_is_oos_evaluation(
                bars, cmp_settings, in_sample_ratio=0.60, assumed_spread_bps=spread_bps
            )
            print("\n" + "-" * 76)
            print(
                f"TREND-FILTERED VARIANT (SMA=20 | Hold={cmp_strat.max_hold_seconds / 3600:.0f}h | "
                f"TP={cmp_strat.take_profit_pct * 100:.1f}% | SL={cmp_strat.stop_loss_pct * 100:.1f}%)"
            )
            print("-" * 76)
            _print_window(cmp_is)
            print()
            _print_window(cmp_oos)
            oos_m = cmp_oos

        _print_verdict(oos_m)

    print("\nMANDATORY BIAS & REALITY CHECKS:")
    print("  1. Look-ahead bias: Indicators use only completed bars <= t; fills apply ask/bid + slippage.")
    print("  2. Survivorship / selection bias: Evaluate across multiple symbols, never one cherry-picked winner.")
    print("  3. Bar-level fill optimism: Historical bars lack order-book depth; live quotes can gap past stops.")
    print("  4. Operating cost hurdle: On $250, keep monthly API/hosting spend <= $2 (0.8%/mo drag).")

    _summarize_forward_paper_ledger(Path(settings.db_path), settings.starting_cash_usd)
    print("=" * 76)


if __name__ == "__main__":
    main()
