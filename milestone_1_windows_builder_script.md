<#
.SYNOPSIS
Milestone 1 One-Click Builder for daytradingbot (Windows PowerShell)

.DESCRIPTION
Creates the complete Milestone 1 folder structure and source files inside
C:\\dev\\daytradingbot (or a custom -TargetDir), sets up a Python virtual
environment (.venv), installs dependencies, runs pytest, and runs the
deterministic \$250 Replay Demo.

```
NOTE: This builds PAPER / REPLAY mode only. Live trading is hard-disabled.

```

#>

param(
\[string\]\$TargetDir = "C:\\dev\\daytradingbot"
)

\$ErrorActionPreference = "Stop"

# Helper: Write UTF-8 text WITHOUT a Byte Order Mark (BOM).

# Windows PowerShell 5.1 adds a BOM with Set-Content -Encoding UTF8, which can

# break Python TOML parsers and Git configs. This helper avoids that trap.

function Write-Utf8NoBom {
param(
\[Parameter(Mandatory = \$true)\]\[string\]$RelativePath,
\[Parameter(Mandatory = \$true)\]\[string\]$Content
)
\$fullPath = Join-Path $TargetDir$RelativePath
$parent = Split-Path -Parent$fullPath
if (-not (Test-Path \$parent)) {
New-Item -ItemType Directory -Force -Path \$parent | Out-Null
}
\$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
# Normalize line endings to LF for clean Git diffs across Windows/Linux
\$normalized =$Content -replace "`r`n", "\`n"
\[System.IO.File\]::WriteAllText(\$fullPath, $normalized, $utf8NoBom)
Write-Host "  \[Wrote\] \$RelativePath" -ForegroundColor DarkGray
}

try {
Write-Host "" -ForegroundColor Cyan
Write-Host "  daytradingbot - Milestone 1 Builder (\$250 Paper/Replay Core Engine)   " -ForegroundColor Cyan
Write-Host "" -ForegroundColor Cyan

```
# 1. Verify target repository directory
if (-not (Test-Path $TargetDir)) {
    Write-Host "Target directory '$TargetDir' does not exist. Creating it..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
}
Set-Location $TargetDir
Write-Host "[1/6] Working directory: $(Get-Location)" -ForegroundColor Green

# 2. Verify Python 3.10+ is installed
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) {
    throw "Python was not found on PATH. Install Python 3.12 via 'winget install Python.Python.3.12' and restart PowerShell."
}
$pyVersionRaw = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$pyMajor, $pyMinor = $pyVersionRaw.Trim().Split(".") | ForEach-Object { [int]$_ }
if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 10)) {
    throw "Python 3.10+ is required, but found Python $pyVersionRaw."
}
Write-Host "[2/6] Verified Python $pyVersionRaw" -ForegroundColor Green

# 3. Create package directories and empty __init__.py files
Write-Host "[3/6] Creating Milestone 1 folders and files..." -ForegroundColor Green
$dirs = @(
    "config",
    "data",
    "src\daytradingbot\core",
    "src\daytradingbot\strategy",
    "src\daytradingbot\risk",
    "src\daytradingbot\execution",
    "src\daytradingbot\monitor",
    "tests"
)
foreach ($d in $dirs) {
    New-Item -ItemType Directory -Force -Path (Join-Path $TargetDir $d) | Out-Null
}

$inits = @(
    "src\daytradingbot\__init__.py",
    "src\daytradingbot\core\__init__.py",
    "src\daytradingbot\strategy\__init__.py",
    "src\daytradingbot\risk\__init__.py",
    "src\daytradingbot\execution\__init__.py",
    "src\daytradingbot\monitor\__init__.py"
)
foreach ($initFile in $inits) {
    Write-Utf8NoBom -RelativePath $initFile -Content ""
}

# --- File 1: .gitignore ---
Write-Utf8NoBom -RelativePath ".gitignore" -Content @'

```

.venv/
.env
data/*.db
data/*.db-wal
data/\*.db-shm
**pycache**/
\*.pyc
.pytest_cache/
\*.egg-info/
build_milestone1.ps1
'@

```
# --- File 2: .env.example ---
Write-Utf8NoBom -RelativePath ".env.example" -Content @'

```

# Milestone 1 runs locally with \$0 API keys required.

# Milestone 2+ (Alpaca Paper) & Milestone 4+ (OpenRouter):

ALPACA_API_KEY=replace_me_in_milestone_2
ALPACA_SECRET_KEY=replace_me_in_milestone_2
OPENROUTER_API_KEY=replace_me_in_milestone_4
'@

```
# --- File 3: pyproject.toml ---
Write-Utf8NoBom -RelativePath "pyproject.toml" -Content @'

```

\[build-system\]
requires = \["setuptools>=68.0"\]
build-backend = "setuptools.build_meta"

\[project\]
name = "daytradingbot"
version = "0.1.0"
description = "Bounded automated paper-trading engine (Milestone 1: Core & Replay)"
requires-python = ">=3.10"
dependencies = \[
"pydantic>=2.10,<3.0",
"tomli>=2.0; python_version < '3.11'",
\]

\[project.optional-dependencies\]
dev = \[
"pytest>=8.3,<10.0",
\]

\[tool.setuptools.packages.find\]
where = \["src"\]

\[tool.pytest.ini_options\]
testpaths = \["tests"\]
'@

```
# --- File 4: config/settings.example.toml ---
Write-Utf8NoBom -RelativePath "config\settings.example.toml" -Content @'

```

mode = "REPLAY"  # Allowed in v1: "REPLAY" or "PAPER". "LIVE" is hard-blocked in code.
starting_cash_usd = "250.00"
db_path = "data/replay_demo.db"

\[risk\]
max_position_usd = "50.00"
max_risk_per_trade_usd = "3.75"
max_total_exposure_usd = "150.00"
max_concurrent_positions = 2
daily_loss_limit_usd = "10.00"
max_spread_bps = "25"
min_24h_volume_usd = "5000000"
max_quote_age_seconds = 5
max_monthly_api_spend_usd = "2.00"

\[fees\]
crypto_taker_fee_rate = "0.0025"       # 0.25% Alpaca entry-tier taker fee
stock_sec_fee_rate = "0.0000206"       # SEC Section 31 fee on sells
stock_finra_cat_per_share = "0.000003" # FINRA CAT fee per share
base_slippage_bps = "3"                # 0.03% base execution slippage
impact_factor_bps = "5000"             # Scales price impact with notional / 24h volume

\[strategy\]
lookback_bars = 3
min_breakout_pct = "0.003"             # 0.30% breakout above recent bar high
order_notional_usd = "50.00"
stop_loss_pct = "0.025"                # 2.5% stop loss
take_profit_pct = "0.045"              # 4.5% take profit
max_hold_seconds = 3600                # 1-hour time stop
'@

```
# --- File 5: src/daytradingbot/core/models.py ---
Write-Utf8NoBom -RelativePath "src\daytradingbot\core\models.py" -Content @'

```

from **future** import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional

from pydantic import BaseModel, Field

AssetClass = Literal\["crypto", "stock"\]
OrderSide = Literal\["BUY", "SELL"\]
RunMode = Literal\["REPLAY", "PAPER", "LIVE"\]

def q_usd(value: Decimal) -> Decimal:
"""Round USD values consistently to 4 decimal places."""
return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

def q_qty(value: Decimal) -> Decimal:
"""Round share/coin quantities to 8 decimal places."""
return value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)

class MarketEvent(BaseModel):
event_id: str
symbol: str
asset_class: AssetClass
event_time: datetime
received_time: datetime
bid: Decimal
ask: Decimal
bar_open: Decimal
bar_high: Decimal
bar_low: Decimal
bar_close: Decimal
volume_24h_usd: Decimal
source_status: Literal\["ok", "stale", "disconnected"\] = "ok"

class TradeProposal(BaseModel):
event_id: str
symbol: str
asset_class: AssetClass
action: Literal\["BUY", "NO_TRADE"\]
strategy_name: str
reason: str
ref_price: Decimal = Decimal("0")
notional_usd: Decimal = Decimal("0")
stop_loss_price: Decimal = Decimal("0")
take_profit_price: Decimal = Decimal("0")
max_hold_seconds: int = 0

class RiskVerdict(BaseModel):
allowed: bool
reasons: list\[str\] = Field(default_factory=list)
spread_bps: Decimal = Decimal("0")
estimated_risk_usd: Decimal = Decimal("0")

class OrderRecord(BaseModel):
idempotency_key: str
order_id: str
symbol: str
asset_class: AssetClass
side: OrderSide
requested_notional_usd: Decimal
requested_qty: Decimal = Decimal("0")
status: Literal\["PENDING_SUBMIT", "FILLED", "REJECTED", "AMBIGUOUS"\]
mode: RunMode
created_at: datetime
updated_at: datetime
error: Optional\[str\] = None
stop_loss_price: Decimal = Decimal("0")
take_profit_price: Decimal = Decimal("0")
max_hold_until: Optional\[datetime\] = None
position_id: Optional\[str\] = None
exit_reason: Optional\[str\] = None

class FillRecord(BaseModel):
fill_id: str
order_id: str
idempotency_key: str
symbol: str
side: OrderSide
qty: Decimal
fill_price: Decimal
gross_notional_usd: Decimal
fee_usd: Decimal
slippage_bps: Decimal
impact_bps: Decimal
mode: RunMode
filled_at: datetime

class PositionRecord(BaseModel):
position_id: str
symbol: str
asset_class: AssetClass
status: Literal\["OPEN", "CLOSED"\]
qty: Decimal
entry_price: Decimal
entry_notional_usd: Decimal
entry_fee_usd: Decimal
stop_loss_price: Decimal
take_profit_price: Decimal
max_hold_until: datetime
opened_at: datetime
closed_at: Optional\[datetime\] = None
exit_price: Optional\[Decimal\] = None
exit_fee_usd: Optional\[Decimal\] = None
realized_pnl_usd: Optional\[Decimal\] = None
exit_reason: Optional\[str\] = None
feed_health: Literal\["OK", "STALE_OR_DISCONNECTED"\] = "OK"

class EquitySnapshot(BaseModel):
ts: datetime
mode: RunMode
cash_usd: Decimal
open_market_value_usd: Decimal
total_equity_usd: Decimal
peak_equity_usd: Decimal
drawdown_pct: Decimal
cumulative_fees_usd: Decimal
monthly_api_spend_usd: Decimal
net_equity_after_api_usd: Decimal
'@

```
# --- File 6: src/daytradingbot/core/config.py ---
Write-Utf8NoBom -RelativePath "src\daytradingbot\core\config.py" -Content @'

```

from **future** import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

try:
import tomllib
except ModuleNotFoundError:  # Python 3.10 fallback
import tomli as tomllib  # type: ignore\[no-redef\]

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
lookback_bars: int = 3
min_breakout_pct: Decimal = Decimal("0.003")
order_notional_usd: Decimal = Decimal("50.00")
stop_loss_pct: Decimal = Decimal("0.025")
take_profit_pct: Decimal = Decimal("0.045")
max_hold_seconds: int = 3600

class AppSettings(BaseModel):
mode: Literal\["REPLAY", "PAPER", "LIVE"\] = "REPLAY"
starting_cash_usd: Decimal = Decimal("250.00")
db_path: str = "data/replay_demo.db"
risk: RiskSettings = RiskSettings()
fees: FeeSettings = FeeSettings()
strategy: StrategySettings = StrategySettings()

def load_settings(path: str | Path) -> AppSettings:
raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
return AppSettings.model_validate(raw)
'@

```
# --- File 7: src/daytradingbot/core/ledger.py ---
Write-Utf8NoBom -RelativePath "src\daytradingbot\core\ledger.py" -Content @'

```

from **future** import annotations

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

```
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
        new_total = q_usd(Decimal(self._get_meta(conn, "monthly_api_spend_usd")) + amount_usd)
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
            (ts.isoformat(), stage, symbol, ref_id, status, json.dumps(details, default=str)),
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
        day_total = q_usd((Decimal(prev_day["realized_pnl_usd"]) if prev_day else Decimal("0")) + realized_pnl)
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

```

'@

```
# --- File 8: src/daytradingbot/strategy/baseline.py ---
Write-Utf8NoBom -RelativePath "src\daytradingbot\strategy\baseline.py" -Content @'

```

from **future** import annotations

from collections import deque
from decimal import Decimal

from daytradingbot.core.config import StrategySettings
from daytradingbot.core.models import MarketEvent, TradeProposal, q_usd

class RulesBaselineStrategy:
"""Simple, deterministic breakout baseline used to benchmark future LLM advisors."""

```
name = "rules_breakout_v1"

def __init__(self, cfg: StrategySettings) -> None:
    self.cfg = cfg
    self._bar_highs: dict[str, deque[Decimal]] = {}

def hydrate(self, history: dict[str, list[Decimal]]) -> None:
    for sym, highs in history.items():
        self._bar_highs[sym] = deque(
            highs[-self.cfg.lookback_bars :], maxlen=self.cfg.lookback_bars
        )

def observe(self, event: MarketEvent) -> None:
    buf = self._bar_highs.setdefault(
        event.symbol, deque(maxlen=self.cfg.lookback_bars)
    )
    buf.append(event.bar_high)

def evaluate(self, event: MarketEvent) -> TradeProposal:
    buf = self._bar_highs.setdefault(
        event.symbol, deque(maxlen=self.cfg.lookback_bars)
    )
    if len(buf) < self.cfg.lookback_bars:
        buf.append(event.bar_high)
        return TradeProposal(
            event_id=event.event_id,
            symbol=event.symbol,
            asset_class=event.asset_class,
            action="NO_TRADE",
            strategy_name=self.name,
            reason=f"WARMUP_{len(buf)}_OF_{self.cfg.lookback_bars}",
        )

    prior_high = max(buf)
    threshold = prior_high * (Decimal("1") + self.cfg.min_breakout_pct)
    # Append AFTER comparing against prior bars so there is zero look-ahead bias.
    buf.append(event.bar_high)

    if event.bar_close >= threshold and event.ask > Decimal("0"):
        ref = event.ask
        sl = q_usd(ref * (Decimal("1") - self.cfg.stop_loss_pct))
        tp = q_usd(ref * (Decimal("1") + self.cfg.take_profit_pct))
        return TradeProposal(
            event_id=event.event_id,
            symbol=event.symbol,
            asset_class=event.asset_class,
            action="BUY",
            strategy_name=self.name,
            reason=f"BREAKOUT_CLOSE_{event.bar_close}_ABOVE_{q_usd(threshold)}",
            ref_price=ref,
            notional_usd=self.cfg.order_notional_usd,
            stop_loss_price=sl,
            take_profit_price=tp,
            max_hold_seconds=self.cfg.max_hold_seconds,
        )

    return TradeProposal(
        event_id=event.event_id,
        symbol=event.symbol,
        asset_class=event.asset_class,
        action="NO_TRADE",
        strategy_name=self.name,
        reason="NO_BREAKOUT_SIGNAL",
    )

```

'@

```
# --- File 9: src/daytradingbot/risk/controller.py ---
Write-Utf8NoBom -RelativePath "src\daytradingbot\risk\controller.py" -Content @'

```

from **future** import annotations

from dataclasses import dataclass
from decimal import Decimal

from daytradingbot.core.config import AppSettings
from daytradingbot.core.models import MarketEvent, PositionRecord, RiskVerdict, TradeProposal, q_usd

@dataclass
class PortfolioState:
cash_usd: Decimal
open_positions: list\[PositionRecord\]
daily_realized_pnl_usd: Decimal
monthly_api_spend_usd: Decimal
paused: bool

class RiskController:
"""Non-overridable deterministic risk gate. Fails closed on missing/stale data."""

```
def __init__(self, settings: AppSettings) -> None:
    self.settings = settings

def evaluate(
    self,
    proposal: TradeProposal,
    event: MarketEvent,
    portfolio: PortfolioState,
) -> RiskVerdict:
    reasons: list[str] = []
    r = self.settings.risk
    f = self.settings.fees

    if self.settings.mode == "LIVE":
        reasons.append("LIVE_MODE_TECHNICALLY_DISABLED")

    if portfolio.paused:
        reasons.append("EMERGENCY_PAUSED")

    if event.source_status != "ok":
        reasons.append(f"FEED_STATUS_{event.source_status.upper()}")

    quote_age = (event.received_time - event.event_time).total_seconds()
    if quote_age < 0 or quote_age > r.max_quote_age_seconds:
        reasons.append(f"STALE_MARKET_DATA_{quote_age:.1f}S")

    spread_bps = Decimal("0")
    if event.bid <= Decimal("0") or event.ask <= Decimal("0") or event.ask < event.bid:
        reasons.append("INVALID_QUOTE_PRICES")
    else:
        mid = (event.bid + event.ask) / Decimal("2")
        spread_bps = q_usd(((event.ask - event.bid) / mid) * Decimal("10000"))
        if spread_bps > r.max_spread_bps:
            reasons.append(f"SPREAD_TOO_WIDE_{spread_bps}_BPS")

    if event.volume_24h_usd < r.min_24h_volume_usd:
        reasons.append("INSUFFICIENT_LIQUIDITY")

    if portfolio.daily_realized_pnl_usd <= -r.daily_loss_limit_usd:
        reasons.append("DAILY_LOSS_LIMIT_HIT")

    if portfolio.monthly_api_spend_usd >= r.max_monthly_api_spend_usd:
        reasons.append("MONTHLY_API_SPEND_CAP_HIT")

    if len(portfolio.open_positions) >= r.max_concurrent_positions:
        reasons.append("MAX_CONCURRENT_POSITIONS_REACHED")

    if any(p.symbol == proposal.symbol for p in portfolio.open_positions):
        reasons.append("ALREADY_IN_SYMBOL")

    if proposal.notional_usd <= Decimal("0") or proposal.notional_usd > r.max_position_usd:
        reasons.append("MAX_POSITION_SIZE_EXCEEDED")

    fee_rate = f.crypto_taker_fee_rate if proposal.asset_class == "crypto" else Decimal("0")
    est_entry_fee = q_usd(proposal.notional_usd * fee_rate)
    if proposal.notional_usd + est_entry_fee > portfolio.cash_usd:
        reasons.append("INSUFFICIENT_CASH")

    current_exposure = sum(
        (p.entry_notional_usd for p in portfolio.open_positions), Decimal("0")
    )
    if current_exposure + proposal.notional_usd > r.max_total_exposure_usd:
        reasons.append("MAX_TOTAL_EXPOSURE_EXCEEDED")

    est_risk_usd = Decimal("0")
    if proposal.ref_price <= Decimal("0") or proposal.stop_loss_price <= Decimal("0"):
        reasons.append("MISSING_STOP_LOSS")
    elif proposal.stop_loss_price >= proposal.ref_price:
        reasons.append("INVALID_STOP_LOSS_ABOVE_ENTRY")
    else:
        stop_pct = (proposal.ref_price - proposal.stop_loss_price) / proposal.ref_price
        roundtrip_cost_pct = (Decimal("2") * fee_rate) + (
            Decimal("2") * f.base_slippage_bps / Decimal("10000")
        )
        est_risk_usd = q_usd(proposal.notional_usd * (stop_pct + roundtrip_cost_pct))
        if est_risk_usd > r.max_risk_per_trade_usd:
            reasons.append(f"MAX_RISK_PER_TRADE_EXCEEDED_{est_risk_usd}")

    return RiskVerdict(
        allowed=len(reasons) == 0,
        reasons=reasons,
        spread_bps=spread_bps,
        estimated_risk_usd=est_risk_usd,
    )

```

'@

```
# --- File 10: src/daytradingbot/execution/paper_broker.py ---
Write-Utf8NoBom -RelativePath "src\daytradingbot\execution\paper_broker.py" -Content @'

```

from **future** import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from daytradingbot.core.config import FeeSettings
from daytradingbot.core.models import FillRecord, MarketEvent, OrderRecord, q_qty, q_usd

class BrokerTimeoutError(RuntimeError):
"""Raised when an order submission times out and execution status is ambiguous."""

@dataclass
class BrokerSubmitResult:
status: str  # "FILLED" or "REJECTED"
fill: Optional\[FillRecord\] = None
error: Optional\[str\] = None

class PaperBroker:
"""Simulates realistic paper fills; live order submission is intentionally absent."""

```
def __init__(self, fee_settings: FeeSettings) -> None:
    self.fees = fee_settings
    self._remote_state: dict[str, BrokerSubmitResult] = {}
    self.submit_call_count: int = 0
    self.simulate_reject_next: Optional[str] = None
    self.simulate_ambiguous_filled_next: bool = False

def submit_order(self, order: OrderRecord, event: MarketEvent) -> BrokerSubmitResult:
    if order.mode == "LIVE":
        raise PermissionError("Live execution is technically disabled in PaperBroker.")

    self.submit_call_count += 1

    # Remote broker idempotency check
    if order.idempotency_key in self._remote_state:
        return self._remote_state[order.idempotency_key]

    if self.simulate_reject_next:
        err = self.simulate_reject_next
        self.simulate_reject_next = None
        res = BrokerSubmitResult(status="REJECTED", error=err)
        self._remote_state[order.idempotency_key] = res
        return res

    fill = self._build_simulated_fill(order, event)
    res = BrokerSubmitResult(status="FILLED", fill=fill)
    self._remote_state[order.idempotency_key] = res

    if self.simulate_ambiguous_filled_next:
        # Simulates a real-world network drop AFTER the broker already filled the order.
        self.simulate_ambiguous_filled_next = False
        raise BrokerTimeoutError("Simulated network timeout after remote fill")

    return res

def fetch_order_status(self, idempotency_key: str) -> BrokerSubmitResult:
    if idempotency_key in self._remote_state:
        return self._remote_state[idempotency_key]
    return BrokerSubmitResult(status="REJECTED", error="NOT_FOUND_AT_BROKER")

def _build_simulated_fill(self, order: OrderRecord, event: MarketEvent) -> FillRecord:
    notional_est = (
        order.requested_notional_usd
        if order.side == "BUY"
        else q_usd(order.requested_qty * event.bid)
    )
    vol = max(event.volume_24h_usd, Decimal("1"))
    impact_bps = q_usd((notional_est / vol) * self.fees.impact_factor_bps)
    slippage_bps = self.fees.base_slippage_bps
    total_bps = slippage_bps + impact_bps

    if order.side == "BUY":
        fill_price = q_usd(event.ask * (Decimal("1") + total_bps / Decimal("10000")))
        qty = q_qty(order.requested_notional_usd / fill_price)
    else:
        fill_price = q_usd(event.bid * (Decimal("1") - total_bps / Decimal("10000")))
        qty = q_qty(order.requested_qty)

    gross_notional = q_usd(qty * fill_price)
    fee_usd = self._calculate_fee(order.asset_class, order.side, qty, gross_notional)

    return FillRecord(
        fill_id=f"FILL-{order.order_id}",
        order_id=order.order_id,
        idempotency_key=order.idempotency_key,
        symbol=order.symbol,
        side=order.side,
        qty=qty,
        fill_price=fill_price,
        gross_notional_usd=gross_notional,
        fee_usd=fee_usd,
        slippage_bps=slippage_bps,
        impact_bps=impact_bps,
        mode=order.mode,
        filled_at=event.received_time,
    )

def _calculate_fee(
    self, asset_class: str, side: str, qty: Decimal, gross_notional: Decimal
) -> Decimal:
    if asset_class == "crypto":
        return q_usd(gross_notional * self.fees.crypto_taker_fee_rate)
    # US Stocks: $0 commission; SEC + FINRA CAT regulatory fees apply on sells
    if side == "SELL":
        sec_fee = gross_notional * self.fees.stock_sec_fee_rate
        cat_fee = qty * self.fees.stock_finra_cat_per_share
        return q_usd(sec_fee + cat_fee)
    return Decimal("0.0000")

```

'@

```
# --- File 11: src/daytradingbot/monitor/coordinator.py ---
Write-Utf8NoBom -RelativePath "src\daytradingbot\monitor\coordinator.py" -Content @'

```

from **future** import annotations

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
q_usd,
)
from daytradingbot.execution.paper_broker import BrokerTimeoutError, PaperBroker
from daytradingbot.risk.controller import PortfolioState, RiskController
from daytradingbot.strategy.baseline import RulesBaselineStrategy

class TradingCoordinator:
"""Orchestrates event dedup, strategy, risk gate, idempotent orders, and exits."""

```
def __init__(
    self,
    settings: AppSettings,
    ledger: Ledger,
    broker: PaperBroker,
    strategy: Optional[RulesBaselineStrategy] = None,
) -> None:
    self.settings = settings
    self.ledger = ledger
    self.broker = broker
    self.strategy = strategy or RulesBaselineStrategy(settings.strategy)
    self.risk = RiskController(settings)
    self._last_bid: dict[str, Decimal] = {}
    self.startup_recover()

def startup_recover(self) -> list[str]:
    """Rebuild strategy lookback and reconcile any ambiguous orders left by a crash."""
    self.strategy.hydrate(
        self.ledger.get_recent_bar_highs(self.settings.strategy.lookback_bars)
    )
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

    # 5. Non-overridable risk controller
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

```

'@

```
# --- File 12: src/daytradingbot/replay_demo.py ---
Write-Utf8NoBom -RelativePath "src\daytradingbot\replay_demo.py" -Content @'

```

from **future** import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from daytradingbot.core.config import load_settings
from daytradingbot.core.ledger import Ledger
from daytradingbot.core.models import MarketEvent
from daytradingbot.execution.paper_broker import PaperBroker
from daytradingbot.monitor.coordinator import TradingCoordinator

def \_ev(
event_id: str,
symbol: str,
asset_class: str,
t: datetime,
bid: str,
ask: str,
high: str,
close: str,
vol: str = "50000000",
lag_s: float = 0.2,
source_status: str = "ok",
) -> MarketEvent:
return MarketEvent(
event_id=event_id,
symbol=symbol,
asset_class=asset_class,  # type: ignore\[arg-type\]
event_time=t,
received_time=t + timedelta(seconds=lag_s),
bid=Decimal(bid),
ask=Decimal(ask),
bar_open=Decimal(bid),
bar_high=Decimal(high),
bar_low=Decimal(bid),
bar_close=Decimal(close),
volume_24h_usd=Decimal(vol),
source_status=source_status,  # type: ignore\[arg-type\]
)

def run_replay_demo() -> None:
cfg_path = Path("config/settings.example.toml")
settings = load_settings(cfg_path)
db_file = Path(settings.db_path)
for suffix in ("", "-wal", "-shm"):
candidate = Path(f"{db_file}{suffix}")
if candidate.exists():
candidate.unlink()

```
ledger = Ledger(db_file, settings.starting_cash_usd)
broker = PaperBroker(settings.fees)
coord = TradingCoordinator(settings, ledger, broker)

t0 = datetime(2026, 11, 19, 14, 30, 0, tzinfo=timezone.utc)
events = [
    # 3 warm-up bars for BTC/USD
    _ev("e1", "BTC/USD", "crypto", t0 + timedelta(minutes=1), "90000", "90020", "90100", "90010"),
    _ev("e2", "BTC/USD", "crypto", t0 + timedelta(minutes=2), "90050", "90070", "90150", "90060"),
    _ev("e3", "BTC/USD", "crypto", t0 + timedelta(minutes=3), "90080", "90100", "90200", "90090"),
    # Valid breakout -> BUY $50 BTC/USD
    _ev("e4", "BTC/USD", "crypto", t0 + timedelta(minutes=4), "90500", "90520", "90550", "90510"),
    # Duplicate of e4 -> IGNORED_DUPLICATE
    _ev("e4", "BTC/USD", "crypto", t0 + timedelta(minutes=4), "90500", "90520", "90550", "90510"),
    # Out-of-order older timestamp -> IGNORED_OUT_OF_ORDER
    _ev("e5", "BTC/USD", "crypto", t0 + timedelta(minutes=2), "90050", "90070", "90150", "90060"),
    # ETH/USD warm-up + wide spread breakout -> VETO (SPREAD_TOO_WIDE)
    _ev("e6", "ETH/USD", "crypto", t0 + timedelta(minutes=1), "3000", "3001", "3005", "3000"),
    _ev("e7", "ETH/USD", "crypto", t0 + timedelta(minutes=2), "3001", "3002", "3006", "3001"),
    _ev("e8", "ETH/USD", "crypto", t0 + timedelta(minutes=3), "3002", "3003", "3007", "3002"),
    _ev("e9", "ETH/USD", "crypto", t0 + timedelta(minutes=4), "3020", "3040", "3040", "3030"),
    # SOL/USD warm-up + stale quote breakout -> VETO (STALE_MARKET_DATA)
    _ev("e10", "SOL/USD", "crypto", t0 + timedelta(minutes=1), "200.00", "200.10", "200.20", "200.05"),
    _ev("e11", "SOL/USD", "crypto", t0 + timedelta(minutes=2), "200.10", "200.20", "200.30", "200.15"),
    _ev("e12", "SOL/USD", "crypto", t0 + timedelta(minutes=3), "200.15", "200.25", "200.40", "200.20"),
    _ev("e13", "SOL/USD", "crypto", t0 + timedelta(minutes=4), "202.00", "202.10", "202.20", "202.05", lag_s=14.0),
    # BTC/USD hits Take Profit (+4.5% above ~90520 -> ~94593) -> SELL & realize PnL
    _ev("e14", "BTC/USD", "crypto", t0 + timedelta(minutes=10), "94700", "94720", "94750", "94710"),
]

print("=" * 72)
print(f"MODE: {settings.mode} / DEMO (SIMULATED PAPER FILLS — NOT LIVE TRADES)")
print(f"Starting Virtual Balance: ${settings.starting_cash_usd}")
print("=" * 72)

for ev in events:
    res = coord.process_event(ev)
    detail = res.get("reasons") or res.get("reason", "")
    suffix = f" | {detail}" if detail else ""
    print(f"[{ev.event_id:>3}] {ev.symbol:<7} -> {res['status']}{suffix}")

# Demonstrate Emergency Pause blocking a fresh stock breakout
ledger.set_paused(True)
stock_events = [
    _ev("s1", "AAPL", "stock", t0 + timedelta(minutes=1), "230.00", "230.05", "230.10", "230.02"),
    _ev("s2", "AAPL", "stock", t0 + timedelta(minutes=2), "230.05", "230.10", "230.15", "230.08"),
    _ev("s3", "AAPL", "stock", t0 + timedelta(minutes=3), "230.10", "230.15", "230.20", "230.12"),
    _ev("s4", "AAPL", "stock", t0 + timedelta(minutes=4), "231.50", "231.55", "231.60", "231.52"),
]
for ev in stock_events:
    res = coord.process_event(ev)
    if ev.event_id == "s4":
        print(f"[{ev.event_id:>3}] {ev.symbol:<7} -> {res['status']} | {res.get('reasons')}")

print("-" * 72)
for pos in ledger.get_all_positions():
    print(
        f"Position {pos.position_id}: {pos.symbol} [{pos.status}] "
        f"Entry=${pos.entry_price} Exit=${pos.exit_price} "
        f"EntryFee=${pos.entry_fee_usd} ExitFee=${pos.exit_fee_usd} "
        f"NetPnL=${pos.realized_pnl_usd} ({pos.exit_reason})"
    )
print(f"Final Cash (Reconciled): ${ledger.get_cash_usd()}")
print(f"Cumulative Fees Paid:    ${ledger.get_cumulative_fees_usd()}")
print(f"SQLite Ledger Saved At:  {db_file.resolve()}")
print("=" * 72)

```

if **name** == "**main**":
run_replay_demo()
'@

```
# --- File 13: tests/test_engine.py ---
Write-Utf8NoBom -RelativePath "tests\test_engine.py" -Content @'

```

from **future** import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from daytradingbot.core.config import AppSettings
from daytradingbot.core.ledger import Ledger
from daytradingbot.core.models import MarketEvent, TradeProposal
from daytradingbot.execution.paper_broker import PaperBroker
from daytradingbot.monitor.coordinator import TradingCoordinator
from daytradingbot.risk.controller import PortfolioState, RiskController

T0 = datetime(2026, 11, 19, 15, 0, 0, tzinfo=timezone.utc)

def make_event(
event_id: str,
symbol: str = "BTC/USD",
asset_class: str = "crypto",
minute_offset: int = 0,
bid: str = "100.00",
ask: str = "100.05",
high: str = "100.10",
close: str = "100.02",
vol: str = "20000000",
lag_s: float = 0.2,
source_status: str = "ok",
) -> MarketEvent:
ts = T0 + timedelta(minutes=minute_offset)
return MarketEvent(
event_id=event_id,
symbol=symbol,
asset_class=asset_class,  # type: ignore\[arg-type\]
event_time=ts,
received_time=ts + timedelta(seconds=lag_s),
bid=Decimal(bid),
ask=Decimal(ask),
bar_open=Decimal(bid),
bar_high=Decimal(high),
bar_low=Decimal(bid),
bar_close=Decimal(close),
volume_24h_usd=Decimal(vol),
source_status=source_status,  # type: ignore\[arg-type\]
)

def warmup_symbol(coord: TradingCoordinator, symbol: str = "BTC/USD", asset_class: str = "crypto") -> None:
for i in range(1, 4):
res = coord.process_event(
make_event(f"{symbol}-w{i}", symbol=symbol, asset_class=asset_class, minute_offset=i)
)
assert res\["status"\] == "NO_TRADE"

def test_entry_take_profit_and_exact_accounting(tmp_path: Path) -> None:
settings = AppSettings(db_path=str(tmp_path / "test.db"))
ledger = Ledger(settings.db_path, settings.starting_cash_usd)
broker = PaperBroker(settings.fees)
coord = TradingCoordinator(settings, ledger, broker)

```
warmup_symbol(coord)
entry = coord.process_event(
    make_event("b1", minute_offset=4, bid="101.00", ask="101.05", high="101.10", close="101.02")
)
assert entry["status"] == "FILLED_BUY"
assert len(ledger.get_open_positions()) == 1
assert ledger.get_cash_usd() < Decimal("200.0000")

exit_res = coord.process_event(
    make_event("x1", minute_offset=10, bid="106.00", ask="106.05", high="106.10", close="106.02")
)
assert exit_res["status"] == "FILLED_SELL"
assert len(ledger.get_open_positions()) == 0

pos = ledger.get_all_positions()[0]
assert pos.status == "CLOSED"
assert pos.exit_reason == "TAKE_PROFIT"
assert pos.realized_pnl_usd is not None and pos.realized_pnl_usd > Decimal("0")
assert ledger.get_cash_usd() == settings.starting_cash_usd + pos.realized_pnl_usd

```

def test_stock_regulatory_fees_and_stop_loss(tmp_path: Path) -> None:
settings = AppSettings(db_path=str(tmp_path / "stock.db"))
ledger = Ledger(settings.db_path, settings.starting_cash_usd)
broker = PaperBroker(settings.fees)
coord = TradingCoordinator(settings, ledger, broker)

```
warmup_symbol(coord, symbol="AAPL", asset_class="stock")
entry = coord.process_event(
    make_event(
        "s-buy",
        symbol="AAPL",
        asset_class="stock",
        minute_offset=4,
        bid="101.00",
        ask="101.02",
        high="101.10",
        close="101.01",
    )
)
assert entry["status"] == "FILLED_BUY"
assert entry["fill"].fee_usd == Decimal("0.0000")

exit_res = coord.process_event(
    make_event(
        "s-sl",
        symbol="AAPL",
        asset_class="stock",
        minute_offset=6,
        bid="98.00",
        ask="98.02",
        high="98.10",
        close="98.01",
    )
)
assert exit_res["status"] == "FILLED_SELL"
assert exit_res["fill"].fee_usd > Decimal("0.0000")
pos = ledger.get_all_positions()[0]
assert pos.exit_reason == "STOP_LOSS"
assert pos.realized_pnl_usd is not None and pos.realized_pnl_usd < Decimal("0")

```

def test_risk_vetoes_fail_closed() -> None:
settings = AppSettings(mode="LIVE")
rc = RiskController(settings)
ev = make_event("r1", bid="100.00", ask="101.00", vol="1000", lag_s=12.0, source_status="disconnected")
proposal = TradeProposal(
event_id="r1",
symbol="BTC/USD",
asset_class="crypto",
action="BUY",
strategy_name="test",
reason="test",
ref_price=Decimal("101.00"),
notional_usd=Decimal("90.00"),
stop_loss_price=Decimal("80.00"),
take_profit_price=Decimal("110.00"),
max_hold_seconds=3600,
)
portfolio = PortfolioState(
cash_usd=Decimal("250.00"),
open_positions=\[\],
daily_realized_pnl_usd=Decimal("-12.00"),
monthly_api_spend_usd=Decimal("2.50"),
paused=True,
)
verdict = rc.evaluate(proposal, ev, portfolio)
assert not verdict.allowed
joined = " ".join(verdict.reasons)
for expected in \[
"LIVE_MODE_TECHNICALLY_DISABLED",
"EMERGENCY_PAUSED",
"FEED_STATUS_DISCONNECTED",
"STALE_MARKET_DATA",
"SPREAD_TOO_WIDE",
"INSUFFICIENT_LIQUIDITY",
"DAILY_LOSS_LIMIT_HIT",
"MONTHLY_API_SPEND_CAP_HIT",
"MAX_POSITION_SIZE_EXCEEDED",
"MAX_RISK_PER_TRADE_EXCEEDED",
\]:
assert expected in joined

def test_duplicate_and_out_of_order_events(tmp_path: Path) -> None:
settings = AppSettings(db_path=str(tmp_path / "dedup.db"))
ledger = Ledger(settings.db_path, settings.starting_cash_usd)
broker = PaperBroker(settings.fees)
coord = TradingCoordinator(settings, ledger, broker)

```
warmup_symbol(coord)
ev = make_event("dup-1", minute_offset=4, bid="101.00", ask="101.05", high="101.10", close="101.02")
assert coord.process_event(ev)["status"] == "FILLED_BUY"
assert coord.process_event(ev)["status"] == "IGNORED_DUPLICATE"
assert broker.submit_call_count == 1

older = make_event("old-1", minute_offset=2, bid="90.00", ask="90.05", high="90.10", close="90.02")
assert coord.process_event(older)["status"] == "IGNORED_OUT_OF_ORDER"

```

def test_stale_feed_does_not_fabricate_exit_fill(tmp_path: Path) -> None:
settings = AppSettings(db_path=str(tmp_path / "outage.db"))
ledger = Ledger(settings.db_path, settings.starting_cash_usd)
broker = PaperBroker(settings.fees)
coord = TradingCoordinator(settings, ledger, broker)

```
warmup_symbol(coord)
assert coord.process_event(
    make_event("b1", minute_offset=4, bid="101.00", ask="101.05", high="101.10", close="101.02")
)["status"] == "FILLED_BUY"

outage = coord.process_event(
    make_event(
        "out1",
        minute_offset=5,
        bid="90.00",
        ask="90.05",
        high="90.10",
        close="90.02",
        lag_s=20.0,
        source_status="disconnected",
    )
)
assert outage["status"] == "POSITION_FEED_STALE"
open_pos = ledger.get_open_positions()
assert len(open_pos) == 1
assert open_pos[0].feed_health == "STALE_OR_DISCONNECTED"

```

def test_broker_rejection_leaves_no_ghost_position(tmp_path: Path) -> None:
settings = AppSettings(db_path=str(tmp_path / "rej.db"))
ledger = Ledger(settings.db_path, settings.starting_cash_usd)
broker = PaperBroker(settings.fees)
broker.simulate_reject_next = "SIMULATED_API_503"
coord = TradingCoordinator(settings, ledger, broker)

```
warmup_symbol(coord)
res = coord.process_event(
    make_event("b1", minute_offset=4, bid="101.00", ask="101.05", high="101.10", close="101.02")
)
assert res["status"] == "REJECTED"
assert ledger.get_open_positions() == []
assert ledger.get_cash_usd() == settings.starting_cash_usd

```

def test_ambiguous_order_and_crash_restart_recovery(tmp_path: Path) -> None:
db = tmp_path / "crash.db"
settings = AppSettings(db_path=str(db))
ledger1 = Ledger(db, settings.starting_cash_usd)
broker = PaperBroker(settings.fees)
broker.simulate_ambiguous_filled_next = True
coord1 = TradingCoordinator(settings, ledger1, broker)

```
warmup_symbol(coord1)
res = coord1.process_event(
    make_event("amb-1", minute_offset=4, bid="101.00", ask="101.05", high="101.10", close="101.02")
)
assert res["status"] == "AMBIGUOUS"
assert len(ledger1.get_open_positions()) == 0
assert broker.submit_call_count == 1

# Simulate process crash & restart with brand-new instances
ledger2 = Ledger(db, settings.starting_cash_usd)
coord2 = TradingCoordinator(settings, ledger2, broker)

assert broker.submit_call_count == 1
assert len(ledger2.get_open_positions()) == 1
assert ledger2.get_cash_usd() < Decimal("200.0000")

dup = coord2.process_event(
    make_event("amb-1", minute_offset=4, bid="101.00", ask="101.05", high="101.10", close="101.02")
)
assert dup["status"] == "IGNORED_DUPLICATE"

```

'@

```
# 4. Create virtual environment if missing and install dependencies
$venvPython = Join-Path $TargetDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[4/6] Creating Python virtual environment (.venv)..." -ForegroundColor Green
    & python -m venv .venv
} else {
    Write-Host "[4/6] Found existing .venv virtual environment." -ForegroundColor Green
}

Write-Host "  Installing daytradingbot + pytest into .venv..." -ForegroundColor DarkGray
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -e ".[dev]" --quiet

# 5. Run automated pytest verification suite
Write-Host "`n[5/6] Running Milestone 1 test suite (pytest -v)..." -ForegroundColor Green
& $venvPython -m pytest -v
if ($LASTEXITCODE -ne 0) {
    throw "pytest failed with exit code $LASTEXITCODE."
}

# 6. Run deterministic $250 Replay Demo
Write-Host "`n[6/6] Running deterministic `$250 Replay Demo..." -ForegroundColor Green
& $venvPython -m daytradingbot.replay_demo
if ($LASTEXITCODE -ne 0) {
    throw "Replay demo failed with exit code $LASTEXITCODE."
}

Write-Host "`nSUCCESS: Milestone 1 built, tested, and verified!" -ForegroundColor Cyan
Write-Host "Next step: Commit & push to GitHub with:" -ForegroundColor Yellow
Write-Host "  git status" -ForegroundColor White
Write-Host "  git add ." -ForegroundColor White
Write-Host "  git commit -m `"Milestone 1: Core engine, risk gate, paper broker, replay demo, and tests`"" -ForegroundColor White
Write-Host "  git push -u origin main" -ForegroundColor White

```

}
catch {
Write-Host "\`nERROR: $($\_.Exception.Message)" -ForegroundColor Red
exit 1
}