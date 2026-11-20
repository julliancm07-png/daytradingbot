from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from daytradingbot.core.config import load_settings
from daytradingbot.dashboard.app import create_app


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Start daytradingbot forward paper engine + 2-second mobile dashboard."
    )
    parser.add_argument(
        "--config",
        default="config/settings.paper.toml",
        help="Path to TOML config file (default: config/settings.paper.toml).",
    )
    parser.add_argument(
        "--replay-view",
        action="store_true",
        help="View the Replay Demo ledger in the dashboard without live polling.",
    )
    args = parser.parse_args()

    cfg_path = Path("config/settings.example.toml" if args.replay_view else args.config)
    settings = load_settings(cfg_path)
    enable_poller = (not args.replay_view) and (settings.mode == "PAPER")

    print("=" * 72)
    print(f"daytradingbot Dashboard | Mode: {settings.mode} (LIVE TRADING DISABLED)")
    print(f"Config: {cfg_path} | Ledger: {settings.db_path}")
    print(f"Open in browser: http://{settings.dashboard.host}:{settings.dashboard.port}")
    print("Press Ctrl+C in this window to stop safely.")
    print("=" * 72)

    app = create_app(cfg_path, enable_live_poller=enable_poller)
    uvicorn.run(
        app,
        host=settings.dashboard.host,
        port=settings.dashboard.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
