import os
import json
import hmac
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import engine

app = FastAPI(title="FX Trading Bot")
DB_PATH = Path(__file__).with_name("trades.db")
# Accept either the project API URL or a URL that already includes /rest/v1.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
if SUPABASE_URL.endswith("/rest/v1"):
    SUPABASE_URL = SUPABASE_URL[:-8].rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
COOLDOWN_SECONDS = 300
last_signal_at = {}

def cloud_db_enabled():
    return bool(SUPABASE_URL and SUPABASE_KEY)

def cloud_request(method, table="trades", params=None, payload=None, prefer=None):
    if not cloud_db_enabled():
        raise RuntimeError("Облачная база не настроена: добавь SUPABASE_URL и SUPABASE_SECRET_KEY в Render")
    # Supabase sb_secret_* keys are opaque API keys, not JWTs: send only via apikey.
    headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
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
        history = [bar[1] for bar in bars[:-1]]
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


def auto_journal_sync(pair, side, price, candles, candle_time, confirmations):
    """Save one virtual consensus entry and process every unseen M5 candle for SL/TP."""
    conn = None
    try:
        if cloud_db_enabled():
            rows = cloud_request("GET", params={
                "select": "*", "pair": "eq." + pair, "status": "eq.OPEN",
                "order": "id.desc", "limit": "1"
            })
        else:
            conn = db()
            rows = [dict(x) for x in conn.execute(
                "SELECT * FROM trades WHERE pair=? AND status='OPEN' ORDER BY id DESC LIMIT 1",
                (pair,)
            ).fetchall()]

        closed = None
        if rows:
            trade = rows[0]
            entry = float(trade["entry"])
            stop = float(trade["stop_loss"])
            target = float(trade["take_profit"])
            trade_side = str(trade["side"]).upper()

            # AUTO tags store the signal candle timestamp: AUTO|BUY|N|unix_time.
            # This prevents counting price movement that happened before entry.
            entry_candle_time = None
            tag_parts = str(trade.get("strategy") or "").split("|")
            if len(tag_parts) >= 4 and tag_parts[0] == "AUTO":
                try:
                    entry_candle_time = int(tag_parts[-1])
                except (TypeError, ValueError):
                    entry_candle_time = None
            if entry_candle_time is None:
                try:
                    opened = datetime.fromisoformat(str(trade["opened_at"]).replace("Z", "+00:00"))
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                    entry_candle_time = (int(opened.timestamp()) // 300) * 300
                except Exception:
                    entry_candle_time = int(candle_time)

            # Walk forward through every candle after the signal/entry candle.
            # If both levels are touched in one candle, count SL first (conservative).
            hit = None
            exit_price = None
            hit_candle_time = None
            for bar_time, bar_close, bar_high, bar_low in candles:
                if int(bar_time) <= entry_candle_time:
                    continue
                if trade_side == "BUY":
                    if float(bar_low) <= stop:
                        hit, exit_price = "STOP_LOSS", stop
                    elif float(bar_high) >= target:
                        hit, exit_price = "TAKE_PROFIT", target
                else:
                    if float(bar_high) >= stop:
                        hit, exit_price = "STOP_LOSS", stop
                    elif float(bar_low) <= target:
                        hit, exit_price = "TAKE_PROFIT", target
                if hit:
                    hit_candle_time = int(bar_time)
                    break

            # A confirmed opposite consensus closes the old position at the scan price.
            if not hit and side in ("BUY", "SELL") and side != trade_side:
                hit, exit_price = "SIGNAL_REVERSAL", float(price)
                hit_candle_time = int(candle_time)

            if hit:
                pnl = ((float(exit_price) - entry) / entry * 100) * (1 if trade_side == "BUY" else -1)
                stamp = datetime.fromtimestamp(hit_candle_time or int(candle_time), timezone.utc).isoformat()
                values = {
                    "status": "CLOSED", "exit_price": float(exit_price), "result": hit,
                    "pnl": round(pnl, 4), "closed_at": stamp
                }
                if cloud_db_enabled():
                    cloud_request("PATCH", params={"id": "eq." + str(trade["id"])},
                                  payload=values, prefer="return=representation")
                else:
                    conn.execute(
                        "UPDATE trades SET status='CLOSED',exit_price=?,result=?,pnl=?,closed_at=? WHERE id=?",
                        (float(exit_price), hit, round(pnl, 4), stamp, trade["id"])
                    )
                    conn.commit()
                closed = {
                    "id": trade["id"], "result": hit, "exit_price": float(exit_price),
                    "pnl": round(pnl, 4), "closed_at": stamp
                }
            elif side in ("BUY", "SELL"):
                return {
                    "opened": False, "closed": None,
                    "reason": "Сделка ещё открыта: TP/SL не достигнуты"
                }

        if side not in ("BUY", "SELL"):
            return {"opened": False, "closed": closed}

        strategy_tag = "AUTO|" + side + "|" + str(confirmations) + "|" + str(candle_time)
        if cloud_db_enabled():
            duplicate = cloud_request("GET", params={
                "select": "id", "pair": "eq." + pair, "strategy": "eq." + strategy_tag, "limit": "1"
            })
        else:
            check = db()
            duplicate = check.execute(
                "SELECT id FROM trades WHERE pair=? AND strategy=? LIMIT 1",
                (pair, strategy_tag)
            ).fetchall()
            check.close()
        if duplicate:
            return {"opened": False, "closed": closed, "reason": "Сигнал этой свечи уже записан"}

        entry_price = float(price)
        sl = entry_price * (0.9985 if side == "BUY" else 1.0015)
        tp = entry_price * (1.003 if side == "BUY" else 0.997)
        stamp = datetime.now(timezone.utc).isoformat()
        values = {
            "pair": pair, "strategy": strategy_tag, "side": side,
            "entry": entry_price, "stop_loss": sl, "take_profit": tp,
            "opened_at": stamp
        }
        if cloud_db_enabled():
            saved = cloud_request("POST", payload=values, prefer="return=representation")
            trade_id = saved[0]["id"] if saved else None
        else:
            if conn is None:
                conn = db()
            cur = conn.execute(
                "INSERT INTO trades(pair,strategy,side,entry,stop_loss,take_profit,opened_at) VALUES(?,?,?,?,?,?,?)",
                (pair, strategy_tag, side, entry_price, sl, tp, stamp)
            )
            trade_id = cur.lastrowid
            conn.commit()

        return {
            "opened": True, "id": trade_id, "pair": pair, "side": side,
            "entry": entry_price, "stop_loss": sl, "take_profit": tp,
            "confirmations": confirmations, "opened_at": stamp, "closed": closed
        }
    except Exception as exc:
        return {"opened": False, "closed": None, "error": str(exc)[:250]}
    finally:
        if conn:
            conn.close()

@app.get("/api/capital/scan")
async def capital_demo_scan_all(x_auto_scan_key: str = Header(default="")):
    """Read-only scan for the UI; authenticated scheduled calls also update the virtual journal."""
    configured_key = os.getenv("AUTO_SCAN_KEY", "")
    auto_journal_enabled = bool(configured_key) and hmac.compare_digest(x_auto_scan_key, configured_key)
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
                close_obj = item.get("closePrice") or {}
                close = close_obj.get("bid")
                high = (item.get("highPrice") or {}).get("bid", close)
                low = (item.get("lowPrice") or {}).get("bid", close)
                stamp = item.get("snapshotTimeUTC") or item.get("snapshotTime")
                if close is None or not stamp:
                    continue
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                bars.append((int(parsed.timestamp()), float(close), float(high if high is not None else close), float(low if low is not None else close)))
            bars.sort(key=lambda x: x[0])
            if len(bars) < 60:
                results.append({"pair": pair, "error": "Недостаточно свечей", "signals": []})
                continue

            # Calculate only on the latest completed 5-minute candle.
            history = [bar[1] for bar in bars[:-1]]
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
            wait_count = len(signals) - buy_count - sell_count
            active_count = buy_count + sell_count
            dominant_count = max(buy_count, sell_count)
            strength_pct = round(dominant_count / max(len(signals), 1) * 100)
            agreement_pct = round(dominant_count / active_count * 100) if active_count else 0
            if buy_count > sell_count and buy_count >= 4:
                summary = "BUY"
                strength_direction = "BUY"
            elif sell_count > buy_count and sell_count >= 4:
                summary = "SELL"
                strength_direction = "SELL"
            else:
                summary = "WAIT"
                strength_direction = "NONE"
            if strength_direction == "NONE":
                strength_label = "Нет согласия"
            elif strength_pct >= 60:
                strength_label = "Очень сильный"
            elif strength_pct >= 40:
                strength_label = "Сильный"
            elif strength_pct >= 25:
                strength_label = "Средний"
            else:
                strength_label = "Слабый"
            candle_time = bars[-2][0]
            confirmations = buy_count if summary == "BUY" else sell_count if summary == "SELL" else 0
            journal_event = auto_journal_sync(
                pair, summary if summary in ("BUY", "SELL") else None,
                price, bars, candle_time, confirmations
            ) if auto_journal_enabled else {"opened":False, "closed":None, "reason":"Автожурнал запускается по расписанию"}
            results.append({
                "pair": pair,
                "price": price,
                "candle_time": candle_time,
                "candles_used": len(history),
                "buy_count": buy_count,
                "sell_count": sell_count,
                "wait_count": wait_count,
                "active_count": active_count,
                "strength_pct": strength_pct,
                "agreement_pct": agreement_pct,
                "strength_direction": strength_direction,
                "strength_label": strength_label,
                "summary": summary,
                "signals": signals,
                "journal": journal_event,
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
        "auto_journal_enabled": auto_journal_enabled,
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
