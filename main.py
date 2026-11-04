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

# ========== CAPITAL.COM DEMO (READ-ONLY CONNECTION CHECK) ==========
CAPITAL_DEMO_BASE = "https://demo-api-capital.backend-capital.com"

def capital_demo_credentials():
    return {
        "api_key": os.getenv("DEMO_CAPITAL_API_KEY", "").strip(),
        "identifier": os.getenv("DEMO_CAPITAL_IDENTIFIER", "").strip(),
        "password": os.getenv("DEMO_CAPITAL_API_PASSWORD", ""),
    }

@app.get("/api/capital/status")
async def capital_demo_status():
    credentials = capital_demo_credentials()
    missing = [name for name, value in [
        ("DEMO_CAPITAL_API_KEY", credentials["api_key"]),
        ("DEMO_CAPITAL_IDENTIFIER", credentials["identifier"]),
        ("DEMO_CAPITAL_API_PASSWORD", credentials["password"]),
    ] if not value]
    if missing:
        return {
            "connected": False,
            "mode": "DEMO",
            "message": "Не все переменные Capital.com Demo добавлены в Render.",
            "missing": missing,
        }

    try:
        response = httpx.post(
            CAPITAL_DEMO_BASE + "/api/v1/session",
            headers={"X-CAP-API-KEY": credentials["api_key"]},
            json={
                "identifier": credentials["identifier"],
                "password": credentials["password"],
                "encryptedPassword": False,
            },
            timeout=20,
        )
        if response.status_code in (401, 403):
            return {
                "connected": False,
                "mode": "DEMO",
                "message": "Capital.com отклонил вход. Проверь Demo API key, логин и API password в Render.",
            }
        if response.is_error:
            return {
                "connected": False,
                "mode": "DEMO",
                "message": "Capital.com вернул ошибку авторизации.",
                "http_status": response.status_code,
            }

        cst = response.headers.get("CST")
        security_token = response.headers.get("X-SECURITY-TOKEN")
        if not cst or not security_token:
            return {
                "connected": False,
                "mode": "DEMO",
                "message": "Вход выполнен без ожидаемых токенов сессии. Проверь ответ Capital.com.",
            }

        details = httpx.get(
            CAPITAL_DEMO_BASE + "/api/v1/session",
            headers={"CST": cst, "X-SECURITY-TOKEN": security_token},
            timeout=20,
        )
        if details.is_error:
            return {
                "connected": False,
                "mode": "DEMO",
                "message": "Сессия создана, но не удалось прочитать сведения аккаунта.",
                "http_status": details.status_code,
            }

        account = details.json()
        return {
            "connected": True,
            "mode": "DEMO",
            "message": "Подключение к Capital.com Demo успешно.",
            "currency": account.get("currency"),
            "timezone_offset": account.get("timezoneOffset"),
        }
    except httpx.TimeoutException:
        return {
            "connected": False,
            "mode": "DEMO",
            "message": "Capital.com не ответил вовремя. Попробуй проверить подключение позже.",
        }
    except Exception:
        return {
            "connected": False,
            "mode": "DEMO",
            "message": "Не удалось проверить подключение к Capital.com. Подробности скрыты для защиты секретов.",
        }


# ========== CAPITAL.COM DEMO MARKET DATA (READ ONLY) ==========
CAPITAL_PAIR_LIST = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "EURCHF", "GBPJPY", "GBPCHF", "AUDJPY", "AUDCAD",
    "AUDNZD", "CADJPY", "CHFJPY", "NZDJPY", "EURAUD", "EURCAD",
]
capital_market_cache = {}

def capital_login_tokens():
    credentials = capital_demo_credentials()
    if not all(credentials.values()):
        raise RuntimeError("Не заданы все переменные Capital.com Demo в Render.")
    response = httpx.post(
        CAPITAL_DEMO_BASE + "/api/v1/session",
        headers={"X-CAP-API-KEY": credentials["api_key"]},
        json={
            "identifier": credentials["identifier"],
            "password": credentials["password"],
            "encryptedPassword": False,
        },
        timeout=20,
    )
    if response.is_error:
        raise RuntimeError("Capital.com не подтвердил Demo-сессию (HTTP " + str(response.status_code) + ").")
    cst = response.headers.get("CST")
    security_token = response.headers.get("X-SECURITY-TOKEN")
    if not cst or not security_token:
        raise RuntimeError("Capital.com не вернул токены Demo-сессии.")
    return {"CST": cst, "X-SECURITY-TOKEN": security_token}

def capital_find_market(pair, headers):
    if pair in capital_market_cache:
        return capital_market_cache[pair]
    search_term = pair[:3] + "/" + pair[3:]
    response = httpx.get(
        CAPITAL_DEMO_BASE + "/api/v1/markets",
        headers=headers,
        params={"searchTerm": search_term},
        timeout=20,
    )
    if response.is_error:
        raise RuntimeError("Не удалось найти рынок " + search_term + " (HTTP " + str(response.status_code) + ").")
    markets = response.json().get("markets", [])
    def norm(value):
        return "".join(ch for ch in str(value or "").upper() if ch.isalnum())
    exact = [
        item for item in markets
        if norm(item.get("instrumentName")) == pair
        or norm(item.get("symbol")) == pair
        or norm(item.get("epic")) == pair
    ]
    candidates = exact or [
        item for item in markets
        if pair in norm(item.get("instrumentName"))
        and item.get("instrumentType") == "CURRENCIES"
    ]
    market = next(
        (item for item in candidates if item.get("instrumentType") == "CURRENCIES"),
        candidates[0] if candidates else None,
    )
    if market and market.get("epic"):
        capital_market_cache[pair] = market
        return market
    return None

@app.get("/api/capital/markets")
async def capital_demo_markets():
    try:
        headers = capital_login_tokens()
        found, missing = [], []
        for index, pair in enumerate(CAPITAL_PAIR_LIST):
            if index:
                time.sleep(0.12)
            market = capital_find_market(pair, headers)
            if market:
                found.append({
                    "pair": pair,
                    "epic": market.get("epic"),
                    "name": market.get("instrumentName"),
                    "bid": market.get("bid"),
                    "ask": market.get("offer"),
                    "market_status": market.get("marketStatus"),
                    "streaming_available": market.get("streamingPricesAvailable"),
                })
            else:
                missing.append(pair)
        return {
            "connected": True,
            "mode": "DEMO",
            "found_count": len(found),
            "missing_count": len(missing),
            "markets": found,
            "not_found": missing,
            "source": "Capital.com Demo API",
        }
    except Exception as exc:
        raise HTTPException(503, "Ошибка получения рынков Capital.com Demo: " + str(exc))


@app.get("/api/capital/signal/{pair}")
async def capital_demo_signal(pair: str, strategy: str = "EMA 9/21"):
    pair = pair.upper()
    if pair not in CAPITAL_PAIR_LIST:
        raise HTTPException(400, "Эта валютная пара не входит в список приложения.")
    if strategy not in engine.STRATEGIES:
        raise HTTPException(400, "Неизвестная стратегия.")
    try:
        headers = capital_login_tokens()
        market = capital_find_market(pair, headers)
        if not market:
            raise HTTPException(404, "Capital.com Demo не нашёл эту валютную пару.")
        response = httpx.get(
            CAPITAL_DEMO_BASE + "/api/v1/prices/" + market["epic"],
            headers=headers,
            params={"resolution": "MINUTE_5", "max": 300},
            timeout=20,
        )
        if response.is_error:
            raise RuntimeError("Capital.com не вернул свечи (HTTP " + str(response.status_code) + ").")
        raw_prices = response.json().get("prices", [])
        bars = []
        for item in raw_prices:
            close = (item.get("closePrice") or {}).get("bid")
            stamp = item.get("snapshotTimeUTC") or item.get("snapshotTime")
            if close is None or not stamp:
                continue
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            bars.append((int(parsed.timestamp()), float(close)))
        bars.sort(key=lambda x: x[0])
        # Use the most recently completed 5-minute candle, not the live forming candle.
        if len(bars) < 60:
            raise HTTPException(503, "Недостаточно истории для расчёта стратегии (нужно не менее 60 свечей).")
        history = [price for _, price in bars[:-1]]
        index = len(history) - 1
        indicators = engine.prepare_indicators(history)
        signal = engine.STRATEGIES[strategy](history, indicators, index) or "WAIT"
        price = history[index]
        sl = tp = None
        if signal == "BUY":
            sl, tp = price * (1 - 0.0015), price * (1 + 0.003)
        elif signal == "SELL":
            sl, tp = price * (1 + 0.0015), price * (1 - 0.003)
        return {
            "connected": True,
            "mode": "DEMO",
            "pair": pair,
            "strategy": strategy,
            "signal": signal,
            "price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "candle_time": bars[-2][0],
            "candles_used": len(history),
            "source": "Capital.com Demo API",
            "orders_enabled": False,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Не удалось рассчитать сигнал " + pair + ": " + str(exc))

@app.get("/api/capital/scan")
async def capital_demo_scan_all():
    """Read-only scan: all 20 currency pairs x all 20 strategies on closed M5 candles."""
    try:
        headers = capital_login_tokens()
    except Exception as exc:
        raise HTTPException(503, "Не удалось подключиться к Capital.com Demo: " + str(exc))

    results = []
    for pair in CAPITAL_PAIR_LIST:
        try:
            market = capital_find_market(pair, headers)
            if not market:
                results.append({"pair": pair, "error": "Рынок не найден", "signals": []})
                continue
            response = httpx.get(
                CAPITAL_DEMO_BASE + "/api/v1/prices/" + market["epic"],
                headers=headers,
                params={"resolution": "MINUTE_5", "max": 300},
                timeout=20,
            )
            if response.is_error:
                results.append({"pair": pair, "error": "Capital.com не вернул свечи (HTTP " + str(response.status_code) + ")", "signals": []})
                continue
            bars = []
            for item in response.json().get("prices", []):
                close = (item.get("closePrice") or {}).get("bid")
                stamp = item.get("snapshotTimeUTC") or item.get("snapshotTime")
                if close is None or not stamp:
                    continue
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                bars.append((int(parsed.timestamp()), float(close)))
            bars.sort(key=lambda x: x[0])
            if len(bars) < 60:
                results.append({"pair": pair, "error": "Недостаточно свечей", "signals": []})
                continue

            # Calculate only on the latest completed 5-minute candle.
            history = [price for _, price in bars[:-1]]
            index = len(history) - 1
            price = history[index]
            indicators = engine.prepare_indicators(history)
            signals = []
            for strategy_name, strategy_fn in engine.STRATEGIES.items():
                try:
                    signal = strategy_fn(history, indicators, index) or "WAIT"
                    if signal not in ("BUY", "SELL", "WAIT"):
                        signal = "WAIT"
                except Exception:
                    signal = "WAIT"
                stop_loss = take_profit = None
                if signal == "BUY":
                    stop_loss = price * (1 - 0.0015)
                    take_profit = price * (1 + 0.003)
                elif signal == "SELL":
                    stop_loss = price * (1 + 0.0015)
                    take_profit = price * (1 - 0.003)
                signals.append({
                    "strategy": strategy_name,
                    "signal": signal,
                    "price": price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                })
            buy_count = sum(1 for item in signals if item["signal"] == "BUY")
            sell_count = sum(1 for item in signals if item["signal"] == "SELL")
            summary = "BUY" if buy_count and not sell_count else "SELL" if sell_count and not buy_count else "WAIT"
            results.append({
                "pair": pair,
                "price": price,
                "candle_time": bars[-2][0],
                "candles_used": len(history),
                "buy_count": buy_count,
                "sell_count": sell_count,
                "summary": summary,
                "signals": signals,
            })
            time.sleep(0.08)
        except Exception:
            results.append({"pair": pair, "error": "Ошибка получения или расчёта данных", "signals": []})

    return {
        "connected": True,
        "mode": "DEMO",
        "source": "Capital.com Demo API",
        "timeframe": "MINUTE_5",
        "refresh_seconds": 300,
        "orders_enabled": False,
        "strategies_checked": len(engine.STRATEGIES),
        "pairs_checked": len(CAPITAL_PAIR_LIST),
        "results": results,
    }


@app.get("/api/capital/candles/{pair}")
async def capital_demo_candles(pair: str, resolution: str = "MINUTE_5", max_bars: int = 300):
    pair = pair.upper()
    if pair not in CAPITAL_PAIR_LIST:
        raise HTTPException(400, "Эта валютная пара не входит в список приложения.")
    if resolution not in ("MINUTE", "MINUTE_5", "MINUTE_15", "MINUTE_30", "HOUR", "HOUR_4"):
        raise HTTPException(400, "Недопустимый таймфрейм.")
    max_bars = max(10, min(int(max_bars), 1000))
    try:
        headers = capital_login_tokens()
        market = capital_find_market(pair, headers)
        if not market:
            raise HTTPException(404, "Capital.com Demo не нашёл эту валютную пару.")
        response = httpx.get(
            CAPITAL_DEMO_BASE + "/api/v1/prices/" + market["epic"],
            headers=headers,
            params={"resolution": resolution, "max": max_bars},
            timeout=20,
        )
        if response.is_error:
            raise RuntimeError("Capital.com не вернул свечи (HTTP " + str(response.status_code) + ").")
        raw_prices = response.json().get("prices", [])
        candles = []
        for item in raw_prices:
            op = item.get("openPrice") or {}
            hi = item.get("highPrice") or {}
            lo = item.get("lowPrice") or {}
            cl = item.get("closePrice") or {}
            values = [op.get("bid"), hi.get("bid"), lo.get("bid"), cl.get("bid")]
            if any(value is None for value in values):
                continue
            stamp = item.get("snapshotTimeUTC") or item.get("snapshotTime")
            try:
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                epoch = int(parsed.timestamp())
            except Exception:
                continue
            candles.append({
                "time": epoch,
                "open": float(values[0]),
                "high": float(values[1]),
                "low": float(values[2]),
                "close": float(values[3]),
            })
        snapshot = market
        return {
            "connected": True,
            "mode": "DEMO",
            "pair": pair,
            "epic": market.get("epic"),
            "bid": snapshot.get("bid"),
            "ask": snapshot.get("offer"),
            "resolution": resolution,
            "count": len(candles),
            "candles": candles,
            "source": "Capital.com Demo API",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Ошибка получения данных " + pair + ": " + str(exc))
