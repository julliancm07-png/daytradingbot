from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Optional

from daytradingbot.core.models import (
    EquitySnapshot,
    FillRecord,
    MarketEvent,
    OrderRecord,
    PositionRecord,
    q_qty,
    q_usd,
)


class Ledger:
    """SQLite WAL durable store for events, orders, fills, positions, and equity."""

    def __init__(self, db_path: str | Path, starting_cash_usd: Decimal) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema(starting_cash_usd)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self, starting_cash_usd: Decimal) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS market_events (
                    event_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    received_time TEXT NOT NULL,
                    bar_high TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS symbol_state (
                    symbol TEXT PRIMARY KEY,
                    last_event_time TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    ref_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    idempotency_key TEXT PRIMARY KEY,
                    order_id TEXT UNIQUE NOT NULL,
                    symbol TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    side TEXT NOT NULL,
                    requested_notional_usd TEXT NOT NULL,
                    requested_qty TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT,
                    stop_loss_price TEXT NOT NULL,
                    take_profit_price TEXT NOT NULL,
                    max_hold_until TEXT,
                    position_id TEXT,
                    exit_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS fills (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT UNIQUE NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty TEXT NOT NULL,
                    fill_price TEXT NOT NULL,
                    gross_notional_usd TEXT NOT NULL,
                    fee_usd TEXT NOT NULL,
                    slippage_bps TEXT NOT NULL,
                    impact_bps TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    filled_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    status TEXT NOT NULL,
                    qty TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    entry_notional_usd TEXT NOT NULL,
                    entry_fee_usd TEXT NOT NULL,
                    stop_loss_price TEXT NOT NULL,
                    take_profit_price TEXT NOT NULL,
                    max_hold_until TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    exit_price TEXT,
                    exit_fee_usd TEXT,
                    realized_pnl_usd TEXT,
                    exit_reason TEXT,
                    feed_health TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_pnl (
                    trade_date TEXT PRIMARY KEY,
                    realized_pnl_usd TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    cash_usd TEXT NOT NULL,
                    open_market_value_usd TEXT NOT NULL,
                    total_equity_usd TEXT NOT NULL,
                    peak_equity_usd TEXT NOT NULL,
                    drawdown_pct TEXT NOT NULL,
                    cumulative_fees_usd TEXT NOT NULL,
                    monthly_api_spend_usd TEXT NOT NULL,
                    net_equity_after_api_usd TEXT NOT NULL
                );
                """
            )
            defaults = {
                "starting_cash_usd": str(q_usd(starting_cash_usd)),
                "cash_usd": str(q_usd(starting_cash_usd)),
                "peak_equity_usd": str(q_usd(starting_cash_usd)),
                "cumulative_fees_usd": "0.0000",
                "monthly_api_spend_usd": "0.0000",
                "paused": "0",
            }
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (k, v)
                )

    def _get_meta(self, conn: sqlite3.Connection, key: str) -> str:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise KeyError(f"Missing meta key: {key}")
        return str(row["value"])

    def _set_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def get_cash_usd(self) -> Decimal:
        with self._conn() as conn:
            return Decimal(self._get_meta(conn, "cash_usd"))

    def get_peak_equity_usd(self) -> Decimal:
        with self._conn() as conn:
            return Decimal(self._get_meta(conn, "peak_equity_usd"))

    def get_cumulative_fees_usd(self) -> Decimal:
        with self._conn() as conn:
            return Decimal(self._get_meta(conn, "cumulative_fees_usd"))

    def get_monthly_api_spend_usd(self) -> Decimal:
        with self._conn() as conn:
            return Decimal(self._get_meta(conn, "monthly_api_spend_usd"))

    def add_api_spend_usd(self, amount_usd: Decimal) -> Decimal:
        with self._conn() as conn:
            new_total = q_usd(
                Decimal(self._get_meta(conn, "monthly_api_spend_usd")) + amount_usd
            )
            self._set_meta(conn, "monthly_api_spend_usd", str(new_total))
            return new_total

    def is_paused(self) -> bool:
        with self._conn() as conn:
            return self._get_meta(conn, "paused") == "1"

    def set_paused(self, paused: bool) -> None:
        with self._conn() as conn:
            self._set_meta(conn, "paused", "1" if paused else "0")

    def has_event(self, event_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM market_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return row is not None

    def get_last_event_time(self, symbol: str) -> Optional[datetime]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT last_event_time FROM symbol_state WHERE symbol = ?", (symbol,)
            ).fetchone()
            return datetime.fromisoformat(row["last_event_time"]) if row else None

    def record_market_event(self, event: MarketEvent) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO market_events (event_id, symbol, event_time, received_time, bar_high, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.symbol,
                    event.event_time.isoformat(),
                    event.received_time.isoformat(),
                    str(event.bar_high),
                    event.model_dump_json(),
                ),
            )
            conn.execute(
                "INSERT INTO symbol_state (symbol, last_event_time) VALUES (?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET last_event_time = excluded.last_event_time",
                (event.symbol, event.event_time.isoformat()),
            )

    def get_recent_bar_highs(self, limit_per_symbol: int) -> dict[str, list[Decimal]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT symbol, bar_high FROM market_events ORDER BY event_time ASC"
            ).fetchall()
        grouped: dict[str, list[Decimal]] = {}
        for row in rows:
            sym = str(row["symbol"])
            grouped.setdefault(sym, []).append(Decimal(row["bar_high"]))
        return {sym: highs[-limit_per_symbol:] for sym, highs in grouped.items()}

    def log_audit(
        self,
        ts: datetime,
        stage: str,
        symbol: str,
        ref_id: str,
        status: str,
        details: dict[str, Any],
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (ts, stage, symbol, ref_id, status, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ts.isoformat(),
                    stage,
                    symbol,
                    ref_id,
                    status,
                    json.dumps(details, default=str),
                ),
            )

    def get_audit_log(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()
        return [
            {
                "id": r["id"],
                "ts": r["ts"],
                "stage": r["stage"],
                "symbol": r["symbol"],
                "ref_id": r["ref_id"],
                "status": r["status"],
                "details": json.loads(r["details_json"]),
            }
            for r in rows
        ]

    def get_order_by_key(self, idempotency_key: str) -> Optional[OrderRecord]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            return self._row_to_order(row) if row else None

    def get_unresolved_orders(self) -> list[OrderRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status IN ('PENDING_SUBMIT', 'AMBIGUOUS') "
                "ORDER BY created_at ASC"
            ).fetchall()
            return [self._row_to_order(r) for r in rows]

    def get_all_orders(self) -> list[OrderRecord]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM orders ORDER BY created_at ASC").fetchall()
            return [self._row_to_order(r) for r in rows]

    def save_order_if_absent(self, order: OrderRecord) -> tuple[OrderRecord, bool]:
        """Returns (order, created). Prevents duplicate orders for the same idempotency key."""
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT * FROM orders WHERE idempotency_key = ?", (order.idempotency_key,)
            ).fetchone()
            if existing is not None:
                return self._row_to_order(existing), False
            conn.execute(
                """
                INSERT INTO orders (
                    idempotency_key, order_id, symbol, asset_class, side,
                    requested_notional_usd, requested_qty, status, mode,
                    created_at, updated_at, error, stop_loss_price, take_profit_price,
                    max_hold_until, position_id, exit_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.idempotency_key,
                    order.order_id,
                    order.symbol,
                    order.asset_class,
                    order.side,
                    str(order.requested_notional_usd),
                    str(order.requested_qty),
                    order.status,
                    order.mode,
                    order.created_at.isoformat(),
                    order.updated_at.isoformat(),
                    order.error,
                    str(order.stop_loss_price),
                    str(order.take_profit_price),
                    order.max_hold_until.isoformat() if order.max_hold_until else None,
                    order.position_id,
                    order.exit_reason,
                ),
            )
            return order, True

    def update_order_status(
        self,
        idempotency_key: str,
        status: str,
        updated_at: datetime,
        error: Optional[str] = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE orders SET status = ?, updated_at = ?, error = ? WHERE idempotency_key = ?",
                (status, updated_at.isoformat(), error, idempotency_key),
            )

    def apply_fill(self, order: OrderRecord, fill: FillRecord) -> Optional[PositionRecord]:
        """Atomically record a fill, update cash/fees, and open or close a position (idempotent)."""
        with self._conn() as conn:
            already = conn.execute(
                "SELECT 1 FROM fills WHERE order_id = ?", (fill.order_id,)
            ).fetchone()
            if already is not None:
                return None

            conn.execute(
                """
                INSERT INTO fills (
                    fill_id, order_id, idempotency_key, symbol, side, qty,
                    fill_price, gross_notional_usd, fee_usd, slippage_bps,
                    impact_bps, mode, filled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.fill_id,
                    fill.order_id,
                    fill.idempotency_key,
                    fill.symbol,
                    fill.side,
                    str(fill.qty),
                    str(fill.fill_price),
                    str(fill.gross_notional_usd),
                    str(fill.fee_usd),
                    str(fill.slippage_bps),
                    str(fill.impact_bps),
                    fill.mode,
                    fill.filled_at.isoformat(),
                ),
            )
            conn.execute(
                "UPDATE orders SET status = 'FILLED', updated_at = ?, error = NULL WHERE idempotency_key = ?",
                (fill.filled_at.isoformat(), order.idempotency_key),
            )

            cash = Decimal(self._get_meta(conn, "cash_usd"))
            cum_fees = q_usd(Decimal(self._get_meta(conn, "cumulative_fees_usd")) + fill.fee_usd)
            self._set_meta(conn, "cumulative_fees_usd", str(cum_fees))

            if fill.side == "BUY":
                new_cash = q_usd(cash - fill.gross_notional_usd - fill.fee_usd)
                self._set_meta(conn, "cash_usd", str(new_cash))
                pos_id = f"POS-{fill.fill_id}"
                assert order.max_hold_until is not None
                pos = PositionRecord(
                    position_id=pos_id,
                    symbol=order.symbol,
                    asset_class=order.asset_class,
                    status="OPEN",
                    qty=q_qty(fill.qty),
                    entry_price=fill.fill_price,
                    entry_notional_usd=fill.gross_notional_usd,
                    entry_fee_usd=fill.fee_usd,
                    stop_loss_price=order.stop_loss_price,
                    take_profit_price=order.take_profit_price,
                    max_hold_until=order.max_hold_until,
                    opened_at=fill.filled_at,
                )
                conn.execute(
                    """
                    INSERT INTO positions (
                        position_id, symbol, asset_class, status, qty,
                        entry_price, entry_notional_usd, entry_fee_usd,
                        stop_loss_price, take_profit_price, max_hold_until,
                        opened_at, feed_health
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pos.position_id,
                        pos.symbol,
                        pos.asset_class,
                        pos.status,
                        str(pos.qty),
                        str(pos.entry_price),
                        str(pos.entry_notional_usd),
                        str(pos.entry_fee_usd),
                        str(pos.stop_loss_price),
                        str(pos.take_profit_price),
                        pos.max_hold_until.isoformat(),
                        pos.opened_at.isoformat(),
                        pos.feed_health,
                    ),
                )
                return pos

            assert order.position_id is not None
            row = conn.execute(
                "SELECT * FROM positions WHERE position_id = ?", (order.position_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Position not found for sell fill: {order.position_id}")
            pos = self._row_to_position(row)
            proceeds = q_usd(fill.gross_notional_usd - fill.fee_usd)
            cost_basis = q_usd(pos.entry_notional_usd + pos.entry_fee_usd)
            realized_pnl = q_usd(proceeds - cost_basis)

            new_cash = q_usd(cash + proceeds)
            self._set_meta(conn, "cash_usd", str(new_cash))

            conn.execute(
                """
                UPDATE positions
                SET status = 'CLOSED', closed_at = ?, exit_price = ?,
                    exit_fee_usd = ?, realized_pnl_usd = ?, exit_reason = ?
                WHERE position_id = ?
                """,
                (
                    fill.filled_at.isoformat(),
                    str(fill.fill_price),
                    str(fill.fee_usd),
                    str(realized_pnl),
                    order.exit_reason or "EXIT",
                    pos.position_id,
                ),
            )

            trade_date = fill.filled_at.date().isoformat()
            prev_day = conn.execute(
                "SELECT realized_pnl_usd FROM daily_pnl WHERE trade_date = ?", (trade_date,)
            ).fetchone()
            day_total = q_usd(
                (Decimal(prev_day["realized_pnl_usd"]) if prev_day else Decimal("0")) + realized_pnl
            )
            conn.execute(
                "INSERT INTO daily_pnl (trade_date, realized_pnl_usd) VALUES (?, ?) "
                "ON CONFLICT(trade_date) DO UPDATE SET realized_pnl_usd = excluded.realized_pnl_usd",
                (trade_date, str(day_total)),
            )

            return pos.model_copy(
                update={
                    "status": "CLOSED",
                    "closed_at": fill.filled_at,
                    "exit_price": fill.fill_price,
                    "exit_fee_usd": fill.fee_usd,
                    "realized_pnl_usd": realized_pnl,
                    "exit_reason": order.exit_reason or "EXIT",
                }
            )

    def get_open_positions(self) -> list[PositionRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM positions WHERE status = 'OPEN' ORDER BY opened_at ASC"
            ).fetchall()
            return [self._row_to_position(r) for r in rows]

    def get_all_positions(self) -> list[PositionRecord]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM positions ORDER BY opened_at ASC").fetchall()
            return [self._row_to_position(r) for r in rows]

    def set_position_feed_health(self, position_id: str, health: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE positions SET feed_health = ? WHERE position_id = ?",
                (health, position_id),
            )

    def get_daily_realized_pnl(self, trade_date: str) -> Decimal:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT realized_pnl_usd FROM daily_pnl WHERE trade_date = ?", (trade_date,)
            ).fetchone()
            return Decimal(row["realized_pnl_usd"]) if row else Decimal("0")

    def get_all_fills(self) -> list[FillRecord]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM fills ORDER BY filled_at ASC").fetchall()
        return [
            FillRecord(
                fill_id=r["fill_id"],
                order_id=r["order_id"],
                idempotency_key=r["idempotency_key"],
                symbol=r["symbol"],
                side=r["side"],
                qty=Decimal(r["qty"]),
                fill_price=Decimal(r["fill_price"]),
                gross_notional_usd=Decimal(r["gross_notional_usd"]),
                fee_usd=Decimal(r["fee_usd"]),
                slippage_bps=Decimal(r["slippage_bps"]),
                impact_bps=Decimal(r["impact_bps"]),
                mode=r["mode"],
                filled_at=datetime.fromisoformat(r["filled_at"]),
            )
            for r in rows
        ]

    def record_equity_snapshot(self, snap: EquitySnapshot) -> None:
        with self._conn() as conn:
            if snap.total_equity_usd > Decimal(self._get_meta(conn, "peak_equity_usd")):
                self._set_meta(conn, "peak_equity_usd", str(snap.total_equity_usd))
            conn.execute(
                """
                INSERT INTO equity_snapshots (
                    ts, mode, cash_usd, open_market_value_usd, total_equity_usd,
                    peak_equity_usd, drawdown_pct, cumulative_fees_usd,
                    monthly_api_spend_usd, net_equity_after_api_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap.ts.isoformat(),
                    snap.mode,
                    str(snap.cash_usd),
                    str(snap.open_market_value_usd),
                    str(snap.total_equity_usd),
                    str(snap.peak_equity_usd),
                    str(snap.drawdown_pct),
                    str(snap.cumulative_fees_usd),
                    str(snap.monthly_api_spend_usd),
                    str(snap.net_equity_after_api_usd),
                ),
            )

    @staticmethod
    def _row_to_order(r: sqlite3.Row) -> OrderRecord:
        return OrderRecord(
            idempotency_key=r["idempotency_key"],
            order_id=r["order_id"],
            symbol=r["symbol"],
            asset_class=r["asset_class"],
            side=r["side"],
            requested_notional_usd=Decimal(r["requested_notional_usd"]),
            requested_qty=Decimal(r["requested_qty"]),
            status=r["status"],
            mode=r["mode"],
            created_at=datetime.fromisoformat(r["created_at"]),
            updated_at=datetime.fromisoformat(r["updated_at"]),
            error=r["error"],
            stop_loss_price=Decimal(r["stop_loss_price"]),
            take_profit_price=Decimal(r["take_profit_price"]),
            max_hold_until=datetime.fromisoformat(r["max_hold_until"]) if r["max_hold_until"] else None,
            position_id=r["position_id"],
            exit_reason=r["exit_reason"],
        )

    @staticmethod
    def _row_to_position(r: sqlite3.Row) -> PositionRecord:
        return PositionRecord(
            position_id=r["position_id"],
            symbol=r["symbol"],
            asset_class=r["asset_class"],
            status=r["status"],
            qty=Decimal(r["qty"]),
            entry_price=Decimal(r["entry_price"]),
            entry_notional_usd=Decimal(r["entry_notional_usd"]),
            entry_fee_usd=Decimal(r["entry_fee_usd"]),
            stop_loss_price=Decimal(r["stop_loss_price"]),
            take_profit_price=Decimal(r["take_profit_price"]),
            max_hold_until=datetime.fromisoformat(r["max_hold_until"]),
            opened_at=datetime.fromisoformat(r["opened_at"]),
            closed_at=datetime.fromisoformat(r["closed_at"]) if r["closed_at"] else None,
            exit_price=Decimal(r["exit_price"]) if r["exit_price"] else None,
            exit_fee_usd=Decimal(r["exit_fee_usd"]) if r["exit_fee_usd"] else None,
            realized_pnl_usd=Decimal(r["realized_pnl_usd"]) if r["realized_pnl_usd"] else None,
            exit_reason=r["exit_reason"],
            feed_health=r["feed_health"],
        )
