import json
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="FX Trading Bot")

PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "EURCHF",
    "GBPJPY", "GBPCHF",
    "AUDJPY", "AUDCAD", "AUDNZD",
    "CADJPY", "CHFJPY", "NZDJPY",
    "EURAUD", "EURCAD",
]

PRICES = {
    "EURUSD": 1.0850, "GBPUSD": 1.2700, "USDJPY": 155.00, "USDCHF": 0.8900,
    "AUDUSD": 0.6600, "USDCAD": 1.3600, "NZDUSD": 0.6100,
    "EURGBP": 0.8550, "EURJPY": 168.00, "EURCHF": 0.9650,
    "GBPJPY": 196.50, "GBPCHF": 1.1300,
    "AUDJPY": 102.50, "AUDCAD": 0.9000, "AUDNZD": 1.0800,
    "CADJPY": 114.00, "CHFJPY": 174.00, "NZDJPY": 94.50,
    "EURAUD": 1.6450, "EURCAD": 1.4750,
}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FX Trading Bot</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        background: #1e1e2e; color: #e0e0e0; padding: 12px; min-height: 100vh;
    }
    h1 { font-size: 18px; margin-bottom: 4px; color: #a0a0c0; }
    .subtitle { font-size: 12px; color: #888; margin-bottom: 16px; }
    .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 16px; }
    .stat { background: #252537; border-radius: 8px; padding: 10px; text-align: center; }
    .stat-label { font-size: 10px; color: #888; text-transform: uppercase; }
    .stat-value { font-size: 16px; font-weight: bold; margin-top: 4px; }
    .controls { display: flex; gap: 8px; margin-bottom: 16px; }
    button {
        flex: 1; background: #3b3b5c; border: 1px solid #4a4a6e; border-radius: 6px;
        padding: 10px; color: #e0e0e0; font-size: 13px; font-weight: 600;
    }
    button:active { background: #4a4a6e; }
    .table-wrap { background: #252537; border-radius: 8px; overflow: hidden; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    thead { background: #2d2d44; }
    th {
        padding: 8px 6px; text-align: center; color: #c0c0e0;
        font-weight: 600; font-size: 11px; text-transform: uppercase;
        border-bottom: 1px solid #333;
    }
    td { padding: 10px 6px; text-align: center; border-bottom: 1px solid #2a2a3d; }
    tbody tr:nth-child(even) { background: #2a2a3d; }
    tbody tr:last-child td { border-bottom: none; }
    .sym { font-weight: bold; }
    .buy { color: #22c55e; font-weight: bold; }
    .sell { color: #ef4444; font-weight: bold; }
    .none { color: #666; }
    .price { font-variant-numeric: tabular-nums; }
    .footer { text-align: center; color: #555; font-size: 10px; margin-top: 16px; }
</style>
</head>
<body>
    <h1>FX Trading Bot</h1>
    <div class="subtitle">20 валютных пар · Демо-данные</div>

    <div class="stats">
        <div class="stat"><div class="stat-label">Пар</div><div class="stat-value" id="stat-pairs">20</div></div>
        <div class="stat"><div class="stat-label">Тиков</div><div class="stat-value" id="stat-ticks">0</div></div>
        <div class="stat"><div class="stat-label">Статус</div><div class="stat-value" id="stat-status">OK</div></div>
    </div>

    <div class="controls">
        <button onclick="togglePause()" id="pause-btn">⏸ Пауза</button>
        <button onclick="resetPrices()">↻ Сброс</button>
    </div>

    <div class="table-wrap">
        <table>
            <thead><tr><th>Пара</th><th>Bid</th><th>Ask</th><th>Сигнал</th></tr></thead>
            <tbody id="tbody"></tbody>
        </table>
    </div>

    <div class="footer">Этап 1 · обновление каждые 1с</div>

<script>
const PAIRS = __PAIRS__;
const initialPrices = __PRICES__;
let prices = Object.assign({}, initialPrices);
let ticks = 0;
let paused = false;
const tbody = document.getElementById("tbody");

function fmt(v) { return v.toFixed(v > 100 ? 3 : 5); }

function render() {
    tbody.innerHTML = "";
    PAIRS.forEach(sym => {
        const tr = document.createElement("tr");
        const price = prices[sym];
        const bid = price;
        const ask = price + 0.0001;
        const r = Math.random();
        const signal = r < 0.15 ? "BUY" : r < 0.30 ? "SELL" : "—";
        const cls = signal === "BUY" ? "buy" : signal === "SELL" ? "sell" : "none";
        tr.innerHTML = `<td class="sym">${sym}</td><td class="price">${fmt(bid)}</td><td class="price">${fmt(ask)}</td><td class="${cls}">${signal}</td>`;
        tbody.appendChild(tr);
    });
    document.getElementById("stat-ticks").textContent = ticks;
}

function tick() {
    if (paused) return;
    PAIRS.forEach(sym => {
        const p = prices[sym];
        const delta = (Math.random() - 0.5) * p * 0.0004;
        prices[sym] = Math.max(0.0001, p + delta);
    });
    ticks++;
    render();
}

function togglePause() {
    paused = !paused;
    document.getElementById("pause-btn").textContent = paused ? "▶ Продолжить" : "⏸ Пауза";
}

function resetPrices() {
    prices = Object.assign({}, initialPrices);
    ticks = 0;
    render();
}

render();
setInterval(tick, 1000);
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def index():
    html = HTML_TEMPLATE.replace("__PAIRS__", json.dumps(PAIRS)).replace("__PRICES__", json.dumps(PRICES))
    return HTMLResponse(html)

@app.get("/health")
async def health():
    return {"status": "ok"}
