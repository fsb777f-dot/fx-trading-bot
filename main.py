import random
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import engine

app = FastAPI(title="FX Trading Bot")

# Текущие цены (в будущем будут обновляться с Capital.com)
current_prices = dict(engine.PRICES)

@app.get("/", response_class=HTMLResponse)
async def index():
    """Отдает HTML файл."""
    html_path = Path(__file__).parent / "ui.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

@app.get("/api/prices")
async def get_prices():
    """Симуляция живых цен для интерфейса."""
    for symbol in engine.PAIRS:
        p = current_prices[symbol]
        # Двигаем цену на сотые доли процента
        current_prices[symbol] = max(0.0001, p + (random.random() - 0.5) * p * 0.0004)
    return current_prices

@app.get("/api/backtest")
async def run_backtest():
    """Запускает логику из engine.py."""
    return engine.run_full_backtest()

@app.get("/health")
async def health():
    return {"status": "ok"}
