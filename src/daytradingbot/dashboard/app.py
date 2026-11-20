from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

from daytradingbot.core.config import AppSettings, load_settings
from daytradingbot.core.ledger import Ledger
from daytradingbot.core.models import WorkerHealthRecord, q_usd
from daytradingbot.execution.paper_broker import PaperBroker
from daytradingbot.ingest.alpaca_feed import AlpacaMarketDataClient, FeedBatchResult
from daytradingbot.monitor.coordinator import TradingCoordinator
from daytradingbot.strategy.openrouter_analyst import OpenRouterStrategyAnalyst


def build_state_payload(settings: AppSettings, ledger: Ledger) -> dict[str, Any]:
    """Assemble full dashboard state directly from SQLite WAL ledger."""
    now_utc = datetime.now(timezone.utc)
    cash = ledger.get_cash_usd()
    starting_cash = ledger.get_starting_cash_usd()
    cum_fees = ledger.get_cumulative_fees_usd()
    api_spend = ledger.get_monthly_api_spend_usd()
    paused = ledger.is_paused()

    quotes = ledger.get_latest_quotes()
    quote_map = {str(q["symbol"]): q for q in quotes}

    open_positions = ledger.get_open_positions()
    open_pos_views: list[dict[str, Any]] = []
    open_market_val = Decimal("0")
    unrealized_pnl_total = Decimal("0")

    for pos in open_positions:
        q = quote_map.get(pos.symbol)
        mark = Decimal(str(q["bid"])) if q and Decimal(str(q["bid"])) > Decimal("0") else pos.entry_price
        mv = q_usd(pos.qty * mark)
        open_market_val += mv
        unrl = q_usd(mv - (pos.entry_notional_usd + pos.entry_fee_usd))
        unrealized_pnl_total += unrl
        open_pos_views.append(
            {
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "asset_class": pos.asset_class,
                "qty": str(pos.qty),
                "entry_price": str(pos.entry_price),
                "mark_price": str(mark),
                "stop_loss_price": str(pos.stop_loss_price),
                "take_profit_price": str(pos.take_profit_price),
                "unrealized_pnl_usd": str(unrl),
                "feed_health": pos.feed_health,
                "opened_at": pos.opened_at.isoformat(),
            }
        )

    closed_positions = [p for p in ledger.get_all_positions() if p.status == "CLOSED"]
    realized_pnl_total = sum(
        (p.realized_pnl_usd for p in closed_positions if p.realized_pnl_usd is not None),
        Decimal("0"),
    )

    total_equity = q_usd(cash + open_market_val)
    peak_equity = max(ledger.get_peak_equity_usd(), total_equity)
    drawdown_pct = (
        q_usd(((peak_equity - total_equity) / peak_equity) * Decimal("100"))
        if peak_equity > Decimal("0")
        else Decimal("0.0000")
    )
    net_equity_after_api = q_usd(total_equity - api_spend)

    orders = [o.model_dump(mode="json") for o in reversed(ledger.get_all_orders()[-30:])]
    closed_views = [p.model_dump(mode="json") for p in reversed(closed_positions[-30:])]

    return {
        "server_time_utc": now_utc.isoformat(),
        "mode": settings.mode,
        "live_execution_disabled": True,
        "paused": paused,
        "summary": {
            "starting_cash_usd": str(starting_cash),
            "cash_usd": str(cash),
            "open_market_value_usd": str(open_market_val),
            "total_equity_usd": str(total_equity),
            "peak_equity_usd": str(peak_equity),
            "drawdown_pct": str(drawdown_pct),
            "realized_pnl_usd": str(q_usd(realized_pnl_total)),
            "unrealized_pnl_usd": str(q_usd(unrealized_pnl_total)),
            "cumulative_fees_usd": str(cum_fees),
            "monthly_api_spend_usd": str(api_spend),
            "monthly_api_cap_usd": str(settings.risk.max_monthly_api_spend_usd),
            "net_equity_after_api_usd": str(net_equity_after_api),
            "daily_loss_limit_usd": str(settings.risk.daily_loss_limit_usd),
            "max_position_usd": str(settings.risk.max_position_usd),
            "max_total_exposure_usd": str(settings.risk.max_total_exposure_usd),
            "open_position_count": len(open_pos_views),
            "max_concurrent_positions": settings.risk.max_concurrent_positions,
        },
        "workers": ledger.get_worker_health(),
        "quotes": quotes,
        "open_positions": open_pos_views,
        "closed_positions": closed_views,
        "orders": orders,
        "audit_log": ledger.get_recent_audit_log(limit=35),
        "equity_history": ledger.get_equity_history(limit=80),
    }


async def _poll_loop(
    settings: AppSettings,
    ledger: Ledger,
    coordinator: TradingCoordinator,
    client: AlpacaMarketDataClient,
    stop_event: asyncio.Event,
) -> None:
    err_counts: dict[str, int] = {"alpaca_crypto": 0, "alpaca_stocks": 0}
    while not stop_event.is_set():
        batches: list[FeedBatchResult] = await asyncio.gather(
            client.fetch_crypto_snapshots(),
            client.fetch_stock_snapshots(),
        )
        max_backoff = 0.0
        now_utc = datetime.now(timezone.utc)

        for batch in batches:
            if batch.status in ("DISCONNECTED", "DEGRADED"):
                err_counts[batch.source] = err_counts.get(batch.source, 0) + 1
            else:
                err_counts[batch.source] = 0

            ledger.upsert_worker_health(
                WorkerHealthRecord(
                    worker_name=batch.source,
                    status=batch.status,  # type: ignore[arg-type]
                    last_heartbeat=now_utc,
                    last_latency_ms=batch.latency_ms,
                    rate_limit_remaining=batch.rate_limit_remaining,
                    consecutive_errors=err_counts[batch.source],
                    message=batch.message,
                )
            )
            if batch.retry_after_seconds > max_backoff:
                max_backoff = batch.retry_after_seconds

            for ev in batch.events:
                # Run sync coordinator (including any OpenRouter review) off the event loop
                await asyncio.to_thread(coordinator.process_event, ev)

        ledger.upsert_worker_health(
            WorkerHealthRecord(
                worker_name="coordinator",
                status="PAUSED" if ledger.is_paused() else "OK",
                last_heartbeat=now_utc,
                last_latency_ms=0.0,
                consecutive_errors=0,
                message=(
                    "New entries blocked (exits still active)"
                    if ledger.is_paused()
                    else "Forward paper loop healthy"
                ),
            )
        )

        wait_s = max(settings.feed.poll_interval_seconds, max_backoff)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_s)
        except asyncio.TimeoutError:
            pass


def create_app(
    settings_path: str | Path = "config/settings.paper.toml",
    enable_live_poller: bool = True,
) -> FastAPI:
    settings = load_settings(settings_path)
    ledger = Ledger(settings.db_path, settings.starting_cash_usd)
    broker = PaperBroker(settings.fees)
    analyst = OpenRouterStrategyAnalyst(
        settings.llm, max_monthly_api_spend_usd=settings.risk.max_monthly_api_spend_usd
    )
    coordinator = TradingCoordinator(settings, ledger, broker, analyst=analyst)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        stop_event = asyncio.Event()
        task: Optional[asyncio.Task[None]] = None
        client: Optional[AlpacaMarketDataClient] = None
        now_utc = datetime.now(timezone.utc)

        if settings.llm.enabled and analyst.has_api_key:
            llm_status = "OK"
            llm_msg = f"Ready in {settings.llm.mode} mode ({settings.llm.model}); triggers only on BUY candidates"
        elif settings.llm.enabled:
            llm_status = "IDLE"
            llm_msg = "Enabled in config, waiting for OPENROUTER_API_KEY in .env"
        else:
            llm_status = "IDLE"
            llm_msg = "Disabled (Rules-Only Baseline active; $0 API cost)"

        ledger.upsert_worker_health(
            WorkerHealthRecord(
                worker_name="openrouter_analyst",
                status=llm_status,  # type: ignore[arg-type]
                last_heartbeat=now_utc,
                message=llm_msg,
            )
        )

        if enable_live_poller and settings.mode == "PAPER":
            client = AlpacaMarketDataClient(settings.feed)
            task = asyncio.create_task(
                _poll_loop(settings, ledger, coordinator, client, stop_event)
            )
        else:
            ledger.upsert_worker_health(
                WorkerHealthRecord(
                    worker_name="coordinator",
                    status="IDLE",
                    last_heartbeat=now_utc,
                    message=f"Viewing stored ledger ({settings.mode} mode; live poller off)",
                )
            )
        try:
            yield
        finally:
            stop_event.set()
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if client is not None:
                await client.aclose()
            analyst.close()

    app = FastAPI(title="daytradingbot Dashboard", lifespan=lifespan)
    app.state.settings = settings
    app.state.ledger = ledger
    app.state.coordinator = coordinator

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/api/state")
    async def api_state() -> dict[str, Any]:
        return build_state_payload(settings, ledger)

    @app.post("/api/pause")
    async def api_pause() -> dict[str, Any]:
        ledger.set_paused(True)
        ledger.log_audit(
            datetime.now(timezone.utc),
            "CONTROL",
            "SYSTEM",
            "DASHBOARD",
            "EMERGENCY_PAUSED",
            {"policy": "New entries blocked; open position exit monitoring remains active"},
        )
        return {"paused": True}

    @app.post("/api/resume")
    async def api_resume() -> dict[str, Any]:
        ledger.set_paused(False)
        ledger.log_audit(
            datetime.now(timezone.utc),
            "CONTROL",
            "SYSTEM",
            "DASHBOARD",
            "RESUMED",
            {"policy": "Bounded paper entries re-enabled"},
        )
        return {"paused": False}

    @app.get("/api/stream")
    async def api_stream() -> StreamingResponse:
        async def event_gen() -> AsyncIterator[str]:
            while True:
                data = build_state_payload(settings, ledger)
                yield f"data: {json.dumps(data)}\n\n"
                await asyncio.sleep(settings.dashboard.sse_interval_seconds)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    return app


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>daytradingbot — Paper Operations Dashboard</title>
<style>
  :root {
    --bg: #0b0f17;
    --panel: #131a26;
    --panel-border: #233046;
    --text: #e6edf7;
    --muted: #8b9bb4;
    --green: #22c55e;
    --red: #ef4444;
    --amber: #f59e0b;
    --blue: #38bdf8;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.4;
  }
  .topbar {
    position: sticky;
    top: 0;
    z-index: 10;
    background: rgba(11, 15, 23, 0.94);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--panel-border);
    padding: 12px 16px;
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    justify-content: space-between;
  }
  .brand { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .brand h1 { margin: 0; font-size: 1.05rem; letter-spacing: 0.02em; }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .badge-paper { background: rgba(56, 189, 248, 0.16); color: var(--blue); border: 1px solid rgba(56, 189, 248, 0.4); }
  .badge-replay { background: rgba(245, 158, 11, 0.16); color: var(--amber); border: 1px solid rgba(245, 158, 11, 0.4); }
  .badge-ok { background: rgba(34, 197, 94, 0.15); color: var(--green); }
  .badge-warn { background: rgba(245, 158, 11, 0.15); color: var(--amber); }
  .badge-err { background: rgba(239, 68, 68, 0.15); color: var(--red); }
  .badge-idle { background: rgba(139, 155, 180, 0.15); color: var(--muted); }
  .controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  button {
    border: none;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 700;
    font-size: 0.82rem;
    cursor: pointer;
    transition: opacity 0.15s;
  }
  button:hover { opacity: 0.9; }
  .btn-pause { background: var(--red); color: #fff; }
  .btn-resume { background: var(--green); color: #052e16; }
  .container { max-width: 1320px; margin: 0 auto; padding: 16px; display: grid; gap: 16px; }
  .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: 12px; }
  .card {
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 12px;
    padding: 14px;
  }
  .kpi-label { font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .kpi-val { font-size: 1.35rem; font-weight: 700; font-family: var(--mono); margin-top: 4px; }
  .kpi-sub { font-size: 0.76rem; color: var(--muted); margin-top: 4px; }
  .pos { color: var(--green); }
  .neg { color: var(--red); }
  .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; }
  h2 { margin: 0 0 10px 0; font-size: 0.95rem; color: #cbd5e1; display: flex; justify-content: space-between; align-items: center; }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid rgba(35, 48, 70, 0.7); white-space: nowrap; }
  th { color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; }
  td.mono { font-family: var(--mono); }
  .muted { color: var(--muted); font-size: 0.8rem; }
  .chart-box { width: 100%; height: 150px; background: #0d131f; border-radius: 8px; padding: 8px; }
  .notice {
    font-size: 0.8rem;
    color: var(--muted);
    background: rgba(56, 189, 248, 0.07);
    border: 1px solid rgba(56, 189, 248, 0.2);
    border-radius: 8px;
    padding: 8px 12px;
  }
  .toast { font-size: 0.78rem; color: var(--blue); min-height: 1.1em; }
</style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <h1>daytradingbot</h1>
      <span id="modeBadge" class="badge badge-paper">LOADING...</span>
      <span id="connBadge" class="badge badge-idle">CONNECTING</span>
    </div>
    <div class="controls">
      <span id="lastUpdate" class="muted">Last sync: --</span>
      <button id="pauseBtn" class="btn-pause" onclick="togglePause()">Emergency Pause</button>
    </div>
  </header>

  <main class="container">
    <div class="notice">
      <strong>Safety Boundary:</strong> Live real-money order submission is technically disabled.
      Quotes and paper fills shown here are simulated against live or replayed market data and are <strong>not confirmed live trades</strong>.
      Emergency Pause blocks all new entries immediately while keeping exit monitoring active for open positions.
      <div id="toast" class="toast"></div>
    </div>

    <section class="kpi-grid">
      <div class="card">
        <div class="kpi-label">Total Paper Equity</div>
        <div id="kpiEquity" class="kpi-val">$0.00</div>
        <div id="kpiCash" class="kpi-sub">Cash: $0.00</div>
      </div>
      <div class="card">
        <div class="kpi-label">Realized / Unrealized PnL</div>
        <div id="kpiPnl" class="kpi-val">$0.00</div>
        <div id="kpiUnrl" class="kpi-sub">Unrealized: $0.00</div>
      </div>
      <div class="card">
        <div class="kpi-label">Drawdown (From Peak)</div>
        <div id="kpiDd" class="kpi-val">0.00%</div>
        <div id="kpiPeak" class="kpi-sub">Peak: $0.00</div>
      </div>
      <div class="card">
        <div class="kpi-label">Trading Fees &amp; API Costs</div>
        <div id="kpiFees" class="kpi-val">$0.00</div>
        <div id="kpiApi" class="kpi-sub">API Spend: $0.00 / $2.00</div>
      </div>
      <div class="card">
        <div class="kpi-label">Open Exposure</div>
        <div id="kpiExposure" class="kpi-val">$0.00</div>
        <div id="kpiPosCount" class="kpi-sub">0 / 2 Positions</div>
      </div>
    </section>

    <section class="grid-2">
      <div class="card">
        <h2>Equity Curve <span id="netAfterApi" class="muted">Net after API: --</span></h2>
        <div id="equityChart" class="chart-box"></div>
      </div>
      <div class="card">
        <h2>Worker &amp; Data-Source Health</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Worker</th><th>Status</th><th>Latency</th><th>Rate Left</th><th>Details</th></tr></thead>
            <tbody id="workerBody"><tr><td colspan="5" class="muted">Waiting for heartbeats...</td></tr></tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="grid-2">
      <div class="card">
        <h2>Watched Symbols (Live Quotes)</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Symbol</th><th>Bid / Ask</th><th>Spread</th><th>Age</th><th>24h Vol</th></tr></thead>
            <tbody id="quoteBody"><tr><td colspan="5" class="muted">No quotes received yet...</td></tr></tbody>
          </table>
        </div>
      </div>
      <div class="card">
        <h2>Open Positions</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Symbol</th><th>Entry</th><th>Mark</th><th>SL / TP</th><th>Unrl PnL</th><th>Feed</th></tr></thead>
            <tbody id="openPosBody"><tr><td colspan="6" class="muted">No open positions</td></tr></tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="grid-2">
      <div class="card">
        <h2>Recent Candidates, Decisions &amp; Veto Reasons</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Time (UTC)</th><th>Stage</th><th>Symbol</th><th>Status</th><th>Reason / Details</th></tr></thead>
            <tbody id="auditBody"><tr><td colspan="5" class="muted">No events yet</td></tr></tbody>
          </table>
        </div>
      </div>
      <div class="card">
        <h2>Trade History &amp; Order Lifecycle</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Symbol</th><th>Status</th><th>Entry / Exit</th><th>Fees</th><th>Net PnL</th><th>Exit Reason</th></tr></thead>
            <tbody id="closedBody"><tr><td colspan="6" class="muted">No closed trades yet</td></tr></tbody>
          </table>
        </div>
      </div>
    </section>
  </main>

<script>
  let isPaused = false;

  function fmtUsd(val) {
    const n = Number(val || 0);
    return (n < 0 ? "-$" : "$") + Math.abs(n).toFixed(2);
  }
  function fmtUsd4(val) {
    const n = Number(val || 0);
    return (n < 0 ? "-$" : "$") + Math.abs(n).toFixed(4);
  }
  function shortTime(iso) {
    if (!iso) return "--";
    return iso.replace("T", " ").slice(11, 19);
  }
  function badgeClass(status) {
    const s = String(status || "").toUpperCase();
    if (["OK", "ALLOW", "FILLED", "FILLED_BUY", "FILLED_SELL", "RESUMED", "SHADOW_APPROVE", "FILTER_APPROVE"].includes(s)) return "badge-ok";
    if (["DEGRADED", "PAUSED", "IDLE", "NO_TRADE", "WAITING_NEXT_BAR", "SHADOW_VETO"].includes(s)) return "badge-warn";
    return "badge-err";
  }

  function renderChart(points) {
    const box = document.getElementById("equityChart");
    if (!points || points.length < 2) {
      box.innerHTML = '<div class="muted" style="padding:48px 0;text-align:center;">Collecting equity snapshots...</div>';
      return;
    }
    const vals = points.map(p => Number(p.total_equity_usd));
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const span = max - min || 1;
    const w = 520, h = 130, pad = 10;
    const coords = vals.map((v, i) => {
      const x = pad + (i / (vals.length - 1)) * (w - pad * 2);
      const y = h - pad - ((v - min) / span) * (h - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    const color = vals[vals.length - 1] >= vals[0] ? "#22c55e" : "#ef4444";
    box.innerHTML = `
      <svg viewBox="0 0 ${w} ${h}" width="100%" height="100%" preserveAspectRatio="none">
        <polyline fill="none" stroke="${color}" stroke-width="2.5" points="${coords}" />
      </svg>`;
  }

  function renderState(d) {
    isPaused = Boolean(d.paused);
    const modeEl = document.getElementById("modeBadge");
    if (d.mode === "PAPER") {
      modeEl.textContent = "PAPER MODE (SIMULATED FILLS)";
      modeEl.className = "badge badge-paper";
    } else {
      modeEl.textContent = d.mode + " / DEMO (NOT LIVE)";
      modeEl.className = "badge badge-replay";
    }

    const pauseBtn = document.getElementById("pauseBtn");
    if (isPaused) {
      pauseBtn.textContent = "Resume Entries (PAUSED)";
      pauseBtn.className = "btn-resume";
    } else {
      pauseBtn.textContent = "Emergency Pause";
      pauseBtn.className = "btn-pause";
    }

    document.getElementById("lastUpdate").textContent = "Last sync: " + shortTime(d.server_time_utc) + " UTC";

    const s = d.summary;
    document.getElementById("kpiEquity").textContent = fmtUsd(s.total_equity_usd);
    document.getElementById("kpiCash").textContent = "Cash: " + fmtUsd(s.cash_usd) + " (Start " + fmtUsd(s.starting_cash_usd) + ")";

    const pnlEl = document.getElementById("kpiPnl");
    pnlEl.textContent = fmtUsd4(s.realized_pnl_usd);
    pnlEl.className = "kpi-val " + (Number(s.realized_pnl_usd) >= 0 ? "pos" : "neg");
    document.getElementById("kpiUnrl").textContent = "Unrealized: " + fmtUsd4(s.unrealized_pnl_usd);

    document.getElementById("kpiDd").textContent = Number(s.drawdown_pct).toFixed(2) + "%";
    document.getElementById("kpiPeak").textContent = "Peak: " + fmtUsd(s.peak_equity_usd);

    document.getElementById("kpiFees").textContent = fmtUsd4(s.cumulative_fees_usd);
    document.getElementById("kpiApi").textContent = "API Spend: " + fmtUsd(s.monthly_api_spend_usd) + " / " + fmtUsd(s.monthly_api_cap_usd);
    document.getElementById("netAfterApi").textContent = "Net after API: " + fmtUsd(s.net_equity_after_api_usd);

    document.getElementById("kpiExposure").textContent = fmtUsd(s.open_market_value_usd);
    document.getElementById("kpiPosCount").textContent = s.open_position_count + " / " + s.max_concurrent_positions + " Positions (Max " + fmtUsd(s.max_total_exposure_usd) + ")";

    renderChart(d.equity_history);

    const wb = document.getElementById("workerBody");
    wb.innerHTML = d.workers.length ? d.workers.map(w => `
      <tr>
        <td class="mono">${w.worker_name}</td>
        <td><span class="badge ${badgeClass(w.status)}">${w.status}</span></td>
        <td class="mono">${Number(w.last_latency_ms || 0).toFixed(0)}ms</td>
        <td class="mono">${w.rate_limit_remaining ?? "--"}</td>
        <td class="muted">${w.message}</td>
      </tr>`).join("") : '<tr><td colspan="5" class="muted">No worker heartbeats yet</td></tr>';

    const qb = document.getElementById("quoteBody");
    qb.innerHTML = d.quotes.length ? d.quotes.map(q => `
      <tr>
        <td class="mono"><strong>${q.symbol}</strong></td>
        <td class="mono">${Number(q.bid).toFixed(2)} / ${Number(q.ask).toFixed(2)}</td>
        <td class="mono">${Number(q.spread_bps).toFixed(1)} bps</td>
        <td class="mono">${Number(q.quote_age_s).toFixed(1)}s</td>
        <td class="mono">$${(Number(q.volume_24h_usd) / 1e6).toFixed(1)}M</td>
      </tr>`).join("") : '<tr><td colspan="5" class="muted">No live quotes yet</td></tr>';

    const ob = document.getElementById("openPosBody");
    ob.innerHTML = d.open_positions.length ? d.open_positions.map(p => `
      <tr>
        <td class="mono"><strong>${p.symbol}</strong></td>
        <td class="mono">${Number(p.entry_price).toFixed(2)}</td>
        <td class="mono">${Number(p.mark_price).toFixed(2)}</td>
        <td class="mono">${Number(p.stop_loss_price).toFixed(2)} / ${Number(p.take_profit_price).toFixed(2)}</td>
        <td class="mono ${Number(p.unrealized_pnl_usd) >= 0 ? 'pos' : 'neg'}">${fmtUsd4(p.unrealized_pnl_usd)}</td>
        <td><span class="badge ${badgeClass(p.feed_health)}">${p.feed_health}</span></td>
      </tr>`).join("") : '<tr><td colspan="6" class="muted">No open positions</td></tr>';

    const ab = document.getElementById("auditBody");
    ab.innerHTML = d.audit_log.length ? d.audit_log.slice(0, 20).map(a => {
      const det = a.details || {};
      const reason = det.reasons ? det.reasons.join(", ") : (det.reason || det.policy || det.error || (det.fill_price ? "fill=" + det.fill_price : ""));
      return `<tr>
        <td class="mono">${shortTime(a.ts)}</td>
        <td>${a.stage}</td>
        <td class="mono">${a.symbol}</td>
        <td><span class="badge ${badgeClass(a.status)}">${a.status}</span></td>
        <td class="muted">${reason}</td>
      </tr>`;
    }).join("") : '<tr><td colspan="5" class="muted">No audit events yet</td></tr>';

    const cb = document.getElementById("closedBody");
    cb.innerHTML = d.closed_positions.length ? d.closed_positions.map(c => {
      const fees = Number(c.entry_fee_usd || 0) + Number(c.exit_fee_usd || 0);
      return `<tr>
        <td class="mono"><strong>${c.symbol}</strong></td>
        <td><span class="badge badge-ok">${c.status}</span></td>
        <td class="mono">${Number(c.entry_price).toFixed(2)} &rarr; ${Number(c.exit_price).toFixed(2)}</td>
        <td class="mono">${fmtUsd4(fees)}</td>
        <td class="mono ${Number(c.realized_pnl_usd) >= 0 ? 'pos' : 'neg'}">${fmtUsd4(c.realized_pnl_usd)}</td>
        <td class="muted">${c.exit_reason || ""}</td>
      </tr>`;
    }).join("") : '<tr><td colspan="6" class="muted">No closed trades yet</td></tr>';
  }

  async function togglePause() {
    const endpoint = isPaused ? "/api/resume" : "/api/pause";
    const resp = await fetch(endpoint, { method: "POST" });
    if (resp.ok) {
      const res = await resp.json();
      document.getElementById("toast").textContent = res.paused
        ? "Emergency Pause ACTIVE: new entries blocked (open position exits stay active)."
        : "System RESUMED: bounded paper entries enabled.";
      const st = await fetch("/api/state");
      if (st.ok) renderState(await st.json());
    }
  }

  function connectStream() {
    const connBadge = document.getElementById("connBadge");
    const es = new EventSource("/api/stream");
    es.onopen = () => {
      connBadge.textContent = "LIVE 2S STREAM";
      connBadge.className = "badge badge-ok";
    };
    es.onmessage = (ev) => {
      renderState(JSON.parse(ev.data));
    };
    es.onerror = () => {
      connBadge.textContent = "DISCONNECTED (RETRYING)";
      connBadge.className = "badge badge-err";
    };
  }

  fetch("/api/state").then(r => r.json()).then(renderState).catch(() => {});
  connectStream();
</script>
</body>
</html>
"""
