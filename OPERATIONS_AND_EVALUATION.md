# daytradingbot — Operations, OpenRouter Setup & Evaluation Guide

> **Safety Boundary:** Live real-money order execution is technically disabled in v1.
> All trades recorded by this repository are **simulated paper fills** (`REPLAY` or `PAPER`).

## 1. Daily Windows Commands (PowerShell)

Always start in your repo folder:
```powershell
cd C:\dev\daytradingbot
```

| Action | Command |
| :--- | :--- |
| **Run all 18 automated tests** | `.\.venv\Scripts\python.exe -m pytest -v` |
| **Start Live Forward Paper + Dashboard** | `.\.venv\Scripts\python.exe -m daytradingbot.run_paper` |
| **View Dashboard in Browser** | Open `http://127.0.0.1:8000` |
| **Emergency Pause (blocks new entries, keeps exits active)** | Click **Emergency Pause** in the dashboard |
| **Stop cleanly** | Press `Ctrl + C` in the PowerShell window |
| **Crash / Restart Recovery** | Just re-run `run_paper` — SQLite WAL automatically restores positions, cash, lookback history, and reconciles ambiguous orders |
| **Run Offline Replay Demo** | `.\.venv\Scripts\python.exe -m daytradingbot.replay_demo` |
| **Run Real-Data Out-of-Sample Evaluation (BTC)** | `.\.venv\Scripts\python.exe -m daytradingbot.eval_report --symbol BTC/USD --asset-class crypto --days 14` |
| **Run Real-Data Out-of-Sample Evaluation (SPY)** | `.\.venv\Scripts\python.exe -m daytradingbot.eval_report --symbol SPY --asset-class stock --days 21` |

## 2. Enabling Your Bounded OpenRouter Analyst (Optional)

1. On **openrouter.ai**, create an API key and set a **hard monthly credit limit of `$2.00`** directly on the key.
2. Put your key into `.env` (never commit `.env`):
   ```dotenv
   OPENROUTER_API_KEY=sk-or-v1-your-key-here
   ```
3. In `config/settings.paper.toml`, change `enabled = false` to `enabled = true` under `[llm]`.
4. Leave `mode = "shadow"` for your first 20–30 paper trades so you can measure whether the LLM's vetoes actually outperform the **$0/month Rules-Only Baseline** after API spend.

## 3. Checking Your ThinkPad T470 (Proxmox) Before 24/7 Hosting

Only consider moving the paper bot to your ThinkPad T470 Proxmox node after confirming spare capacity inside the Proxmox shell:
```bash
free -h          # Need at least ~1 GB spare RAM for a lightweight Linux LXC container
uptime           # Check 15-min load average (T470 i5-6300U has 2 cores / 4 threads; keep load < 2.0)
df -h /          # Need at least ~2 GB free disk space
```
`daytradingbot` itself uses `<150 MB RAM` and `<5% CPU`, so a minimal Debian/Ubuntu LXC container with `512 MB RAM` and `1 vCPU` is plenty if your T470 has headroom.

## 4. Strict Gate Before Ever Building a Live Execution Adapter

Do **not** write live-money order code until all four conditions hold:
1. **Out-of-Sample Proof**: Positive net PnL after realistic fees, spreads, and slippage across multiple symbols over a non-overlapping Out-of-Sample window.
2. **Forward Paper Proof**: At least 30+ closed forward paper trades with reconciled accounting, zero unhandled crashes, and positive net equity *after* deducting API spend.
3. **Isolated Live Signer**: Live broker keys must live in a separate restricted execution service with its own hard policy checks, completely outside LLM prompts, logs, and dashboard routes.
4. **Explicit Human Arming**: Live mode must require a dedicated config flag, separate live credentials, and manual arming per session.
