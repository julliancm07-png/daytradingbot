from __future__ import annotations

import argparse
from decimal import Decimal

from dotenv import load_dotenv

from daytradingbot.core.config import load_settings
from daytradingbot.eval.backtest import (
    HistoricalBar,
    fetch_alpaca_historical_bars,
    generate_synthetic_bars,
)
from daytradingbot.eval.portfolio import (
    FillMode,
    PortfolioWindowMetrics,
    run_portfolio_is_oos,
)


def _print_portfolio_window(m: PortfolioWindowMetrics) -> None:
    wu_note = f" | Pre-OOS Indicator Warmup={m.warmup_bars_per_symbol} bars (0 warmup trades)" if m.warmup_bars_per_symbol else ""
    print(f"  [{m.label} | fill={m.fill_mode}] Dates: {m.timestamps_count} ({m.start_ts[:10]} -> {m.end_ts[:10]}){wu_note}")
    print(
        f"    Start=${m.starting_cash_usd:.2f} | End=${m.ending_equity_usd:.4f} | "
        f"NetPnL=${m.net_pnl_usd:+.4f} ({m.net_return_pct:+.2f}%)"
    )
    print(
        f"    Fees=${m.total_fees_usd:.4f} | ClosedTrades={m.closed_trades} "
        f"(OpenAtEnd={m.open_trades_at_end}) | Slot/Cash Vetoes={m.slot_vetoes}"
    )
    print(
        f"    Exits: TP={m.tp_exits}, SL={m.sl_exits}, MR={m.mr_exits}, TIME={m.time_stop_exits} | "
        f"WinRate={m.win_rate_pct:.1f}% | PF={m.profit_factor:.2f}"
    )
    print(
        f"    MaxDrawdown={m.max_drawdown_pct:.2f}% | Equal-Weight Basket B&H={m.equal_weight_bh_return_pct:+.2f}%"
    )
    parts = [
        f"{c.symbol}: {c.realized_pnl_usd:+.2f} ({c.closed_trades}t, B&H {c.buy_and_hold_pct:+.1f}%)"
        for c in m.per_symbol
    ]
    print("    Per-Symbol Closed PnL: " + " | ".join(parts))


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Run shared-$250 multi-symbol portfolio IS/OOS evaluation with same-close vs next-open stress testing."
    )
    parser.add_argument("--config", default="config/settings.etf_swing.toml")
    parser.add_argument(
        "--symbols",
        default="SPY,IWM,XLE,XLF,GLD,TLT",
        help="Comma-separated list of symbols to run on one shared $250 ledger.",
    )
    parser.add_argument("--asset-class", choices=["stock", "crypto"], default="stock")
    parser.add_argument("--days", type=int, default=1800)
    parser.add_argument("--timeframe", default="1Day")
    parser.add_argument("--max-positions", type=int, default=None, help="Override max_concurrent_positions.")
    parser.add_argument("--position-usd", type=str, default=None, help="Override notional per position.")
    parser.add_argument(
        "--fill-at",
        choices=["same-close", "next-open"],
        default="same-close",
        help="Execution timing model.",
    )
    parser.add_argument(
        "--compare-fills",
        action="store_true",
        help="Run BOTH same-close and next-open side-by-side on identical bars to test overnight gap sensitivity.",
    )
    parser.add_argument(
        "--offline-demo",
        action="store_true",
        help="Run deterministic synthetic bars instead of fetching Alpaca history.",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)
    risk_updates: dict[str, object] = {}
    strat_updates: dict[str, object] = {}
    if args.max_positions is not None:
        risk_updates["max_concurrent_positions"] = args.max_positions
    if args.position_usd is not None:
        val = Decimal(args.position_usd)
        risk_updates["max_position_usd"] = val
        strat_updates["order_notional_usd"] = val
    if risk_updates:
        settings = settings.model_copy(update={"risk": settings.risk.model_copy(update=risk_updates)})
    if strat_updates:
        settings = settings.model_copy(update={"strategy": settings.strategy.model_copy(update=strat_updates)})

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    spread_bps = Decimal("5") if args.asset_class == "crypto" else Decimal("2")

    bars_by_symbol: dict[str, list[HistoricalBar]] = {}
    if args.offline_demo:
        source_label = "SYNTHETIC OFFLINE DEMO BARS (NOT REAL MARKET DATA)"
        for sym in symbols:
            bars_by_symbol[sym] = generate_synthetic_bars(sym, args.asset_class, count=260)
    else:
        source_label = f"ALPACA HISTORICAL {args.timeframe} BARS ({args.days}d, PAGINATED)"
        for sym in symbols:
            bars_by_symbol[sym] = fetch_alpaca_historical_bars(
                symbol=sym,
                asset_class=args.asset_class,
                days=args.days,
                timeframe=args.timeframe,
            )

    modes: list[FillMode] = ["same-close", "next-open"] if args.compare_fills else [args.fill_at]

    print("=" * 80)
    print(f"SHARED-$250 PORTFOLIO EVALUATION — {', '.join(symbols)} ({args.asset_class.upper()})")
    print(f"Data Source: {source_label} | Spread: {spread_bps} bps + Slippage/Fees")
    print(
        f"Rules: {settings.strategy.strategy_type} | RSI({settings.strategy.rsi_period})<={settings.strategy.rsi_entry_below} | "
        f"SMA={settings.strategy.trend_ma_bars} | Slots={settings.risk.max_concurrent_positions} x ${settings.strategy.order_notional_usd} "
        f"(MaxExp ${settings.risk.max_total_exposure_usd}) | SL={settings.strategy.stop_loss_pct * 100:.1f}% | TP={settings.strategy.take_profit_pct * 100:.1f}%"
    )
    print("=" * 80)

    results: dict[FillMode, tuple[PortfolioWindowMetrics, PortfolioWindowMetrics]] = {}
    for fm in modes:
        is_m, oos_m = run_portfolio_is_oos(
            bars_by_symbol=bars_by_symbol,
            settings=settings,
            in_sample_ratio=0.60,
            assumed_spread_bps=spread_bps,
            fill_mode=fm,
        )
        results[fm] = (is_m, oos_m)
        print(f"\n--- EXECUTION MODEL: {fm.upper()} ---")
        _print_portfolio_window(is_m)
        print()
        _print_portfolio_window(oos_m)

    print("\nHONEST PORTFOLIO VERDICT (OUT-OF-SAMPLE ONLY):")
    if args.compare_fills:
        sc_oos = results["same-close"][1]
        no_oos = results["next-open"][1]
        print(
            f"  Same-Close OOS: ${sc_oos.net_pnl_usd:+.4f} ({sc_oos.net_return_pct:+.2f}%, PF={sc_oos.profit_factor:.2f}, DD={sc_oos.max_drawdown_pct:.2f}%)"
        )
        print(
            f"  Next-Open  OOS: ${no_oos.net_pnl_usd:+.4f} ({no_oos.net_return_pct:+.2f}%, PF={no_oos.profit_factor:.2f}, DD={no_oos.max_drawdown_pct:.2f}%)"
        )
        if sc_oos.net_pnl_usd > Decimal("0") and no_oos.net_pnl_usd > Decimal("0"):
            print(
                "  [SURVIVES NEXT-OPEN STRESS TEST] Positive OOS net PnL under BOTH close and next-open fills. "
                "Still NOT proven for live money until validated in live forward paper mode."
            )
        elif sc_oos.net_pnl_usd > Decimal("0") and no_oos.net_pnl_usd <= Decimal("0"):
            print(
                "  [TIMING-SENSITIVE EDGE] Positive at same-close ONLY; fades at next-open. "
                "Requires accurate 3:55 PM ET close execution (cannot rely on next-morning market opens)."
            )
        else:
            print(
                "  [NO PORTFOLIO EDGE DEMONSTRATED] Shared-$250 OOS net PnL is <= $0. Keep researching in paper mode."
            )
    else:
        oos_m = results[modes[0]][1]
        if oos_m.closed_trades < 10:
            print("  [INCONCLUSIVE] Too few OOS closed trades (<10). Do NOT trade live.")
        elif oos_m.net_pnl_usd <= Decimal("0"):
            print("  [NO PORTFOLIO EDGE DEMONSTRATED] OOS net PnL <= $0. Keep in paper/research mode.")
        else:
            print("  [PROMISING OOS — NOT YET PROVEN] Positive shared-$250 OOS net PnL after costs.")

    print("=" * 80)


if __name__ == "__main__":
    main()
