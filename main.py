import json
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import engine

app = FastAPI(title="FX Trading Bot")
DB_PATH = Path(__file__).with_name("trades.db")
COOLDOWN_SECONDS = 300
last_signal_at = {}

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT NOT NULL,
            strategy TEXT NOT NULL,
            side TEXT NOT NULL,
            entry REAL NOT NULL,
            stop_loss REAL NOT NULL,
            take_profit REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            exit_price REAL,
            result TEXT,
            pnl REAL,
            opened_at TEXT NOT NULL,
            closed_at TEXT
        )
    """)
    conn.commit()
    return conn

class ScanRequest(BaseModel):
    strategy: str
    prices: dict[str, float]

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((Path(__file__).parent / "ui.html").read_text(encoding="utf-8"))

@app.get("/api/strategies")
async def strategies():
    return list(engine.STRATEGIES.keys())

@app.get("/api/strategies/compare")
async def compare_strategies():
    try:
        return engine.run_strategy_comparison(length=300)
    except Exception as exc:
        return {"error": str(exc)}

@app.get("/api/journal")
async def journal():
    conn = db()
    rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 500").fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/api/demo/scan")
async def demo_scan(req: ScanRequest):
    if req.strategy not in engine.STRATEGIES:
        raise HTTPException(400, "Неизвестная стратегия")
    now = time.time()
    conn = db()
    opened = []
    fn = engine.STRATEGIES[req.strategy]
    for pair in engine.PAIRS:
        if now - last_signal_at.get(pair, 0) < COOLDOWN_SECONDS:
            continue
        history = engine.generate_history(engine.PRICES[pair], 300)
        indicators = engine.prepare_indicators(history)
        try:
            signal = fn(history, indicators, len(history) - 1)
        except Exception:
            signal = None
        if signal not in ("BUY", "SELL"):
            continue
        price = float(req.prices.get(pair, engine.PRICES[pair]))
        # Demo-only risk levels: 0.15% stop and 0.30% target (1:2).
        sl = price * (0.9985 if signal == "BUY" else 1.0015)
        tp = price * (1.003 if signal == "BUY" else 0.997)
        stamp = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO trades(pair,strategy,side,entry,stop_loss,take_profit,opened_at) VALUES(?,?,?,?,?,?,?)",
            (pair, req.strategy, signal, price, sl, tp, stamp)
        )
        opened.append({"id": cur.lastrowid, "pair": pair, "side": signal})
        last_signal_at[pair] = now
    conn.commit()
    conn.close()
    return {"opened": opened, "mode": "SIMULATION"}

class TickRequest(BaseModel):
    prices: dict[str, float]

@app.post("/api/demo/tick")
async def demo_tick(req: TickRequest):
    conn = db()
    rows = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
    closed = []
    for row in rows:
        price = req.prices.get(row["pair"])
        if price is None:
            continue
        hit = None
        if row["side"] == "BUY":
            if price <= row["stop_loss"]: hit = "STOP_LOSS"
            elif price >= row["take_profit"]: hit = "TAKE_PROFIT"
        else:
            if price >= row["stop_loss"]: hit = "STOP_LOSS"
            elif price <= row["take_profit"]: hit = "TAKE_PROFIT"
        if hit:
            pnl = ((price-row["entry"])/row["entry"]*100) * (1 if row["side"] == "BUY" else -1)
            stamp = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE trades SET status='CLOSED',exit_price=?,result=?,pnl=?,closed_at=? WHERE id=?",
                (price, hit, round(pnl, 4), stamp, row["id"])
            )
            closed.append({"id": row["id"], "result": hit, "pnl": round(pnl, 4)})
    conn.commit()
    conn.close()
    return {"closed": closed}

@app.get("/health")
async def health():
    return {"status": "ok"}
