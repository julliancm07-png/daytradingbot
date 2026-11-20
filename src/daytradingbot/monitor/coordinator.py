from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from daytradingbot.core.config import AppSettings
from daytradingbot.core.ledger import Ledger
from daytradingbot.core.models import (
    EquitySnapshot,
    MarketEvent,
    OrderRecord,
    PositionRecord,
    WorkerHealthRecord,
    q_usd,
)
from daytradingbot.execution.paper_broker import BrokerTimeoutError, PaperBroker
from daytradingbot.risk.controller import PortfolioState, RiskController
from daytradingbot.strategy.factory import create_strategy
from daytradingbot.strategy.openrouter_analyst import OpenRouterStrategyAnalyst


class TradingCoordinator:
    """Orchestrates event dedup, strategy, optional bounded LLM review, risk gate, and exits."""

    def __init__(
        self,
        settings: AppSettings,
        ledger: Ledger,
        broker: PaperBroker,
        strategy: Optional[Any] = None,
        analyst: Optional[OpenRouterStrategyAnalyst] = None,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.broker = broker
        self.strategy = strategy or create_strategy(settings.strategy)
        self.analyst = analyst
        self.risk = RiskController(settings)
        self._last_bid: dict[str, Decimal] = {}
        self.startup_recover()

    def startup_recover(self) -> list[str]:
        """Rebuild strategy lookback and reconcile any ambiguous orders left by a crash."""
        self.strategy.hydrate(
            self.ledger.get_recent_bar_highs(self.settings.strategy.lookback_bars)
        )
        for q in self.ledger.get_latest_quotes():
            bid = Decimal(str(q["bid"]))
            if bid > Decimal("0"):
                self._last_bid[str(q["symbol"])] = bid
        return self.reconcile_ambiguous_orders()

    def reconcile_ambiguous_orders(self) -> list[str]:
        reconciled: list[str] = []
        for order in self.ledger.get_unresolved_orders():
            remote = self.broker.fetch_order_status(order.idempotency_key)
            if remote.status == "FILLED" and remote.fill is not None:
                self.ledger.apply_fill(order, remote.fill)
                self.ledger.log_audit(
                    ts=remote.fill.filled_at,
                    stage="RECONCILE",
                    symbol=order.symbol,
                    ref_id=order.idempotency_key,
                    status="RECONCILED_FILLED",
                    details={"order_id": order.order_id, "fill_id": remote.fill.fill_id},
                )
                reconciled.append(f"{order.order_id}:FILLED")
            else:
                self.ledger.update_order_status(
                    order.idempotency_key,
                    status="REJECTED",
                    updated_at=order.updated_at,
                    error=remote.error or "RECONCILED_NOT_FILLED",
                )
                self.ledger.log_audit(
                    ts=order.updated_at,
                    stage="RECONCILE",
                    symbol=order.symbol,
                    ref_id=order.idempotency_key,
                    status="RECONCILED_REJECTED",
                    details={"order_id": order.order_id, "error": remote.error},
                )
                reconciled.append(f"{order.order_id}:REJECTED")
        return reconciled

    def process_event(self, event: MarketEvent) -> dict[str, Any]:
        # Always surface latest quote telemetry to the dashboard
        self.ledger.upsert_latest_quote(event)

        # 1. Durable deduplication
        if self.ledger.has_event(event.event_id):
            self.ledger.log_audit(
                event.received_time, "INGEST", event.symbol, event.event_id, "IGNORED_DUPLICATE", {}
            )
            return {"status": "IGNORED_DUPLICATE", "event_id": event.event_id}

        # 2. Out-of-order guard
        last_ts = self.ledger.get_last_event_time(event.symbol)
        if last_ts is not None and event.event_time <= last_ts:
            self.ledger.log_audit(
                event.received_time,
                "INGEST",
                event.symbol,
                event.event_id,
                "IGNORED_OUT_OF_ORDER",
                {"last_event_time": last_ts.isoformat(), "event_time": event.event_time.isoformat()},
            )
            return {"status": "IGNORED_OUT_OF_ORDER", "event_id": event.event_id}

        self.ledger.record_market_event(event)
        if event.bid > Decimal("0") and event.source_status == "ok":
            self._last_bid[event.symbol] = event.bid

        # 3. Monitor any open position for this symbol first (exits stay active even if paused)
        open_positions = self.ledger.get_open_positions()
        existing = next((p for p in open_positions if p.symbol == event.symbol), None)
        if existing is not None:
            self.strategy.observe(event)
            result = self._handle_open_position(existing, event)
            self._snapshot_equity(event.received_time)
            return result

        # 4. Rules-only strategy proposal
        proposal = self.strategy.evaluate(event)
        if proposal.reason != "WAITING_NEXT_BAR":
            self.ledger.log_audit(
                event.received_time,
                "STRATEGY",
                event.symbol,
                event.event_id,
                proposal.action,
                {"reason": proposal.reason, "strategy": proposal.strategy_name},
            )
        if proposal.action == "NO_TRADE":
            self._snapshot_equity(event.received_time)
            return {"status": "NO_TRADE", "reason": proposal.reason}

        # 4b. Optional bounded OpenRouter LLM review (runs ONLY on candidate BUY signals)
        if self.analyst is not None and self.settings.llm.enabled:
            current_spend = self.ledger.get_monthly_api_spend_usd()
            review = self.analyst.review(proposal, event, current_spend)
            if review.cost_usd > Decimal("0"):
                self.ledger.add_api_spend_usd(review.cost_usd)

            self._record_analyst_health(event.received_time, review)

            if review.status == "OK" and review.decision is not None:
                mode_tag = self.settings.llm.mode.upper()
                self.ledger.log_audit(
                    event.received_time,
                    "LLM_ANALYST",
                    event.symbol,
                    event.event_id,
                    f"{mode_tag}_{review.decision.verdict}",
                    {
                        "model": review.model,
                        "confidence": review.decision.confidence,
                        "reason": review.decision.rationale,
                        "cost_usd": str(review.cost_usd),
                        "latency_ms": review.latency_ms,
                    },
                )
                if self.settings.llm.mode == "filter":
                    if review.decision.verdict == "VETO":
                        self._snapshot_equity(event.received_time)
                        return {
                            "status": "LLM_VETOED",
                            "reason": review.decision.rationale,
                        }
                    proposal = self.analyst.apply_bounded_tightening(proposal, review.decision)
            else:
                self.ledger.log_audit(
                    event.received_time,
                    "LLM_ANALYST",
                    event.symbol,
                    event.event_id,
                    review.status,
                    {"model": review.model, "error": review.error},
                )
                if (
                    self.settings.llm.mode == "filter"
                    and self.settings.llm.fallback_on_error == "NO_TRADE"
                ):
                    self._snapshot_equity(event.received_time)
                    return {
                        "status": "LLM_FALLBACK_NO_TRADE",
                        "reason": review.error or review.status,
                    }

        # 5. Non-overridable risk controller (always enforces hard caps after strategy/LLM)
        portfolio = PortfolioState(
            cash_usd=self.ledger.get_cash_usd(),
            open_positions=open_positions,
            daily_realized_pnl_usd=self.ledger.get_daily_realized_pnl(
                event.event_time.date().isoformat()
            ),
            monthly_api_spend_usd=self.ledger.get_monthly_api_spend_usd(),
            paused=self.ledger.is_paused(),
        )
        verdict = self.risk.evaluate(proposal, event, portfolio)
        self.ledger.log_audit(
            event.received_time,
            "RISK",
            event.symbol,
            event.event_id,
            "ALLOW" if verdict.allowed else "VETO",
            verdict.model_dump(mode="json"),
        )
        if not verdict.allowed:
            self._snapshot_equity(event.received_time)
            return {"status": "VETOED", "reasons": verdict.reasons}

        # 6. Idempotent order submission
        order = OrderRecord(
            idempotency_key=f"ENTRY:{event.event_id}:{event.symbol}",
            order_id=f"ORD-BUY-{event.event_id}",
            symbol=event.symbol,
            asset_class=event.asset_class,
            side="BUY",
            requested_notional_usd=proposal.notional_usd,
            status="PENDING_SUBMIT",
            mode=self.settings.mode,
            created_at=event.received_time,
            updated_at=event.received_time,
            stop_loss_price=proposal.stop_loss_price,
            take_profit_price=proposal.take_profit_price,
            max_hold_until=event.event_time + timedelta(seconds=proposal.max_hold_seconds),
        )
        exec_result = self._execute_order(order, event)
        self._snapshot_equity(event.received_time)
        return exec_result

    def _record_analyst_health(self, ts: datetime, review: Any) -> None:
        status_map = {
            "OK": "OK",
            "SKIPPED_DISABLED": "IDLE",
            "SKIPPED_NO_KEY": "IDLE",
            "SPEND_CAP_BLOCKED": "PAUSED",
            "ERROR": "DEGRADED",
        }
        msg = (
            f"{self.settings.llm.mode} mode ({review.model}) | cost=${review.cost_usd}"
            if review.status == "OK"
            else (review.error or review.status)
        )
        self.ledger.upsert_worker_health(
            WorkerHealthRecord(
                worker_name="openrouter_analyst",
                status=status_map.get(review.status, "DEGRADED"),  # type: ignore[arg-type]
                last_heartbeat=ts,
                last_latency_ms=review.latency_ms,
                consecutive_errors=1 if review.status == "ERROR" else 0,
                message=msg,
            )
        )

    def _handle_open_position(self, pos: PositionRecord, event: MarketEvent) -> dict[str, Any]:
        quote_age = (event.received_time - event.event_time).total_seconds()
        if (
            event.source_status != "ok"
            or quote_age > self.settings.risk.max_quote_age_seconds
            or event.bid <= Decimal("0")
        ):
            # Honest outage behavior: never fabricate a stop fill on missing/stale data.
            self.ledger.set_position_feed_health(pos.position_id, "STALE_OR_DISCONNECTED")
            self.ledger.log_audit(
                event.received_time,
                "MONITOR",
                pos.symbol,
                pos.position_id,
                "OUTAGE_EXIT_UNVERIFIED",
                {"source_status": event.source_status, "quote_age_s": quote_age},
            )
            return {"status": "POSITION_FEED_STALE", "position_id": pos.position_id}

        self.ledger.set_position_feed_health(pos.position_id, "OK")
        exit_reason: Optional[str] = None
        if event.bid <= pos.stop_loss_price:
            exit_reason = "STOP_LOSS"
        elif event.bid >= pos.take_profit_price:
            exit_reason = "TAKE_PROFIT"
        elif event.event_time >= pos.max_hold_until:
            exit_reason = "TIME_STOP"
        elif hasattr(self.strategy, "should_exit"):
            exit_reason = self.strategy.should_exit(event, pos)

        if exit_reason is None:
            return {"status": "HOLDING", "position_id": pos.position_id, "bid": str(event.bid)}

        order = OrderRecord(
            idempotency_key=f"EXIT:{pos.position_id}:{exit_reason}",
            order_id=f"ORD-SELL-{pos.position_id}",
            symbol=pos.symbol,
            asset_class=pos.asset_class,
            side="SELL",
            requested_notional_usd=q_usd(pos.qty * event.bid),
            requested_qty=pos.qty,
            status="PENDING_SUBMIT",
            mode=self.settings.mode,
            created_at=event.received_time,
            updated_at=event.received_time,
            position_id=pos.position_id,
            exit_reason=exit_reason,
        )
        return self._execute_order(order, event)

    def _execute_order(self, order: OrderRecord, event: MarketEvent) -> dict[str, Any]:
        stored, created = self.ledger.save_order_if_absent(order)
        if not created:
            if stored.status in ("PENDING_SUBMIT", "AMBIGUOUS"):
                reconciled = self.reconcile_ambiguous_orders()
                return {"status": "RECONCILED_EXISTING", "details": reconciled}
            return {"status": f"EXISTING_ORDER_{stored.status}", "order_id": stored.order_id}

        try:
            res = self.broker.submit_order(stored, event)
        except BrokerTimeoutError as exc:
            self.ledger.update_order_status(
                stored.idempotency_key, "AMBIGUOUS", event.received_time, error=str(exc)
            )
            self.ledger.log_audit(
                event.received_time,
                "EXECUTION",
                stored.symbol,
                stored.order_id,
                "AMBIGUOUS_TIMEOUT",
                {"error": str(exc)},
            )
            return {"status": "AMBIGUOUS", "order_id": stored.order_id}

        if res.status == "FILLED" and res.fill is not None:
            pos = self.ledger.apply_fill(stored, res.fill)
            self.ledger.log_audit(
                res.fill.filled_at,
                "EXECUTION",
                stored.symbol,
                stored.order_id,
                f"FILLED_{stored.side}",
                {
                    "fill_price": str(res.fill.fill_price),
                    "qty": str(res.fill.qty),
                    "fee_usd": str(res.fill.fee_usd),
                    "mode": res.fill.mode,
                },
            )
            return {
                "status": f"FILLED_{stored.side}",
                "order_id": stored.order_id,
                "fill": res.fill,
                "position": pos,
            }

        self.ledger.update_order_status(
            stored.idempotency_key, "REJECTED", event.received_time, error=res.error
        )
        self.ledger.log_audit(
            event.received_time,
            "EXECUTION",
            stored.symbol,
            stored.order_id,
            "REJECTED",
            {"error": res.error},
        )
        return {"status": "REJECTED", "order_id": stored.order_id, "error": res.error}

    def _snapshot_equity(self, ts: datetime) -> EquitySnapshot:
        cash = self.ledger.get_cash_usd()
        open_positions = self.ledger.get_open_positions()
        open_val = Decimal("0")
        for p in open_positions:
            mark = self._last_bid.get(p.symbol, p.entry_price)
            open_val += q_usd(p.qty * mark)

        total_equity = q_usd(cash + open_val)
        peak = max(self.ledger.get_peak_equity_usd(), total_equity)
        drawdown_pct = (
            q_usd(((peak - total_equity) / peak) * Decimal("100"))
            if peak > Decimal("0")
            else Decimal("0.0000")
        )
        cum_fees = self.ledger.get_cumulative_fees_usd()
        api_spend = self.ledger.get_monthly_api_spend_usd()
        snap = EquitySnapshot(
            ts=ts,
            mode=self.settings.mode,
            cash_usd=cash,
            open_market_value_usd=open_val,
            total_equity_usd=total_equity,
            peak_equity_usd=peak,
            drawdown_pct=drawdown_pct,
            cumulative_fees_usd=cum_fees,
            monthly_api_spend_usd=api_spend,
            net_equity_after_api_usd=q_usd(total_equity - api_spend),
        )
        self.ledger.record_equity_snapshot(snap)
        return snap
