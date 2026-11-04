import os
import json
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import engine

app = FastAPI(title="FX Trading Bot")
DB_PATH = Path(__file__).with_name("trades.db")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
COOLDOWN_SECONDS = 300
last_signal_at = {}

def cloud_db_enabled():
    return bool(SUPABASE_URL and SUPABASE_KEY)

def cloud_request(method, table="trades", params=None, payload=None, prefer=None):
    if not cloud_db_enabled():
        raise RuntimeError("Облачная база не настроена: добавь SUPABASE_URL и SUPABASE_SECRET_KEY в Render")
    headers = {"apikey": SUPABASE_KEY, "Authorization": "Bearer " + SUPABASE_KEY, "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    response = httpx.request(method, SUPABASE_URL + "/rest/v1/" + table, headers=headers, params=params, json=payload, timeout=20)
    if response.is_error:
        raise RuntimeError("Supabase: " + response.text[:500])
    if response.text.strip():
        return response.json()
    return []

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT NOT NULL, strategy TEXT NOT NULL,
        side TEXT NOT NULL, entry REAL NOT NULL, stop_loss REAL NOT NULL, take_profit REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN', exit_price REAL, result TEXT, pnl REAL,
        opened_at TEXT NOT NULL, closed_at TEXT)""")
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
    try:
        if cloud_db_enabled():
            return cloud_request("GET", params={"select": "*", "order": "id.desc", "limit": "500"})
        conn = db()
        rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 500").fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as exc:
        raise HTTPException(503, "Не удалось загрузить журнал: " + str(exc))

@app.delete("/api/journal/{trade_id}")
async def delete_trade(trade_id: int):
    try:
        if cloud_db_enabled():
            cloud_request("DELETE", params={"id": "eq." + str(trade_id)}, prefer="return=minimal")
        else:
            conn = db()
            conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
            conn.commit()
            conn.close()
        return {"deleted": trade_id}
    except Exception as exc:
        raise HTTPException(503, "Не удалось удалить сделку: " + str(exc))

@app.delete("/api/journal")
async def clear_journal():
    try:
        if cloud_db_enabled():
            cloud_request("DELETE", params={"id": "gt.0"}, prefer="return=minimal")
        else:
            conn = db()
            conn.execute("DELETE FROM trades")
            conn.commit()
            conn.close()
        return {"cleared": True}
    except Exception as exc:
        raise HTTPException(503, "Не удалось очистить журнал: " + str(exc))

@app.post("/api/demo/scan")
async def demo_scan(req: ScanRequest):
    if req.strategy not in engine.STRATEGIES:
        raise HTTPException(400, "Неизвестная стратегия")
    now = time.time()
    opened = []
    fn = engine.STRATEGIES[req.strategy]
    conn = None if cloud_db_enabled() else db()
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
        sl = price * (0.9985 if signal == "BUY" else 1.0015)
        tp = price * (1.003 if signal == "BUY" else 0.997)
        stamp = datetime.now(timezone.utc).isoformat()
        values = {"pair": pair, "strategy": req.strategy, "side": signal, "entry": price, "stop_loss": sl, "take_profit": tp, "opened_at": stamp}
        if cloud_db_enabled():
            saved = cloud_request("POST", payload=values, prefer="return=representation")
            trade_id = saved[0]["id"] if saved else None
        else:
            cur = conn.execute("INSERT INTO trades(pair,strategy,side,entry,stop_loss,take_profit,opened_at) VALUES(?,?,?,?,?,?,?)", (pair, req.strategy, signal, price, sl, tp, stamp))
            trade_id = cur.lastrowid
        opened.append({"id": trade_id, "pair": pair, "side": signal})
        last_signal_at[pair] = now
    if conn:
        conn.commit()
        conn.close()
    return {"opened": opened, "mode": "SIMULATION", "journal_storage": "CLOUD" if cloud_db_enabled() else "LOCAL_TEMPORARY"}

class TickRequest(BaseModel):
    prices: dict[str, float]

@app.post("/api/demo/tick")
async def demo_tick(req: TickRequest):
    if cloud_db_enabled():
        rows = cloud_request("GET", params={"select": "*", "status": "eq.OPEN"})
    else:
        conn = db()
        rows = [dict(x) for x in conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()]
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
            values = {"status": "CLOSED", "exit_price": price, "result": hit, "pnl": round(pnl, 4), "closed_at": stamp}
            if cloud_db_enabled():
                cloud_request("PATCH", params={"id": "eq." + str(row["id"])}, payload=values, prefer="return=minimal")
            else:
                conn.execute("UPDATE trades SET status='CLOSED',exit_price=?,result=?,pnl=?,closed_at=? WHERE id=?", (price, hit, round(pnl, 4), stamp, row["id"]))
            closed.append({"id": row["id"], "result": hit, "pnl": round(pnl, 4)})
    if not cloud_db_enabled():
        conn.commit()
        conn.close()
    return {"closed": closed}

@app.get("/health")
async def health():
    return {"status": "ok", "journal_storage": "CLOUD" if cloud_db_enabled() else "LOCAL_TEMPORARY"}

@app.get("/api/config")
async def config():
    return {"journal_storage": "CLOUD" if cloud_db_enabled() else "LOCAL_TEMPORARY", "cloud_database_ready": cloud_db_enabled()}