import random
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import engine

app = FastAPI(title="FX Trading Bot")
current_prices = dict(engine.PRICES)

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "ui.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

@app.get("/api/strategies/compare")
async def compare_strategies():
    try:
        return engine.run_strategy_comparison(length=300)
    except Exception as e:
        return {"error": str(e)}

@app.get("/health")
async def health():
    return {"status": "ok"}
