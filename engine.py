import random

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

def generate_history(start_price, length=1000):
    history = []
    p = start_price
    for _ in range(length):
        p += p * random.uniform(-0.002, 0.002)
        history.append(p)
    return history

def sma(data, window):
    """Скользящая средняя (возвращает список той же длины, с None в начале)."""
    if len(data) < window:
        return [None] * len(data)
    result = [None] * (window - 1)
    for i in range(window - 1, len(data)):
        result.append(sum(data[i-window+1:i+1]) / window)
    return result

def backtest_pair(start_price):
    history = generate_history(start_price, 1000)
    fast = sma(history, 10)
    slow = sma(history, 30)
    
    trades = []
    position = None
    
    for i in range(1, len(history)):
        # Пропускаем, пока не набралось достаточно данных для средних
        if fast[i] is None or slow[i] is None or fast[i-1] is None or slow[i-1] is None:
            continue
            
        current_price = history[i]
        
        if position:
            if position["type"] == "BUY":
                if current_price <= position["sl"]:
                    trades.append({"result": "loss", "pnl": -1})
                    position = None
                elif current_price >= position["tp"]:
                    trades.append({"result": "win", "pnl": 2})
                    position = None
            elif position["type"] == "SELL":
                if current_price >= position["sl"]:
                    trades.append({"result": "loss", "pnl": -1})
                    position = None
                elif current_price <= position["tp"]:
                    trades.append({"result": "win", "pnl": 2})
                    position = None

        if not position:
            # Пересечение снизу вверх -> BUY
            if fast[i] > slow[i] and fast[i-1] <= slow[i-1]:
                position = {"type": "BUY", "sl": current_price * 0.995, "tp": current_price * 1.010}
            # Пересечение сверху вниз -> SELL
            elif fast[i] < slow[i] and fast[i-1] >= slow[i-1]:
                position = {"type": "SELL", "sl": current_price * 1.005, "tp": current_price * 0.990}
                
    if position:
        trades.append({"result": "timeout", "pnl": 0})
        
    return trades

def run_full_backtest():
    total_pnl = 0
    total_trades = 0
    wins = 0
    
    for symbol in PAIRS:
        trades = backtest_pair(PRICES[symbol])
        for t in trades:
            total_trades += 1
            total_pnl += t["pnl"]
            if t["result"] == "win": wins += 1
            
    winrate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    return {
        "pnl": round(total_pnl, 2),
        "trades": total_trades,
        "winrate": round(winrate, 1)
    }
