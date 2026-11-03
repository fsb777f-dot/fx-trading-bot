import random
import math

PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "EURCHF", "GBPJPY", "GBPCHF", "AUDJPY", "AUDCAD",
    "AUDNZD", "CADJPY", "CHFJPY", "NZDJPY", "EURAUD", "EURCAD",
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

# ========== ИНДИКАТОРЫ ==========

def sma(data, window):
    result = [None] * len(data)
    if len(data) < window: return result
    for i in range(window - 1, len(data)):
        result[i] = sum(data[i-window+1:i+1]) / window
    return result

def ema(data, window):
    result = [None] * len(data)
    if len(data) < window: return result
    k = 2 / (window + 1)
    result[window - 1] = sum(data[:window]) / window
    for i in range(window, len(data)):
        result[i] = data[i] * k + result[i-1] * (1 - k)
    return result

def rsi(data, window=14):
    result = [None] * len(data)
    if len(data) < window + 1: return result
    gains = sum(max(data[i] - data[i-1], 0) for i in range(1, window+1))
    losses = sum(max(data[i-1] - data[i], 0) for i in range(1, window+1))
    ag, al = gains / window, losses / window
    result[window] = 100 if al == 0 else 100 - 100 / (1 + ag/al)
    for i in range(window + 1, len(data)):
        d = data[i] - data[i-1]
        ag = (ag * (window-1) + max(d, 0)) / window
        al = (al * (window-1) + max(-d, 0)) / window
        result[i] = 100 if al == 0 else 100 - 100 / (1 + ag/al)
    return result

def stoch(data, window=14):
    result = [None] * len(data)
    if len(data) < window: return result
    for i in range(window - 1, len(data)):
        w = data[i-window+1:i+1]
        h, l = max(w), min(w)
        result[i] = 100 if h == l else (data[i] - l) / (h - l) * 100
    return result

def bollinger(data, window=20, ns=2):
    mid = sma(data, window)
    upper = [None] * len(data)
    lower = [None] * len(data)
    for i in range(window - 1, len(data)):
        w = data[i-window+1:i+1]
        m = mid[i]
        std = (sum((x - m)**2 for x in w) / window) ** 0.5
        upper[i] = m + ns * std
        lower[i] = m - ns * std
    return mid, upper, lower

def donchian(data, window):
    high = [None] * len(data)
    low = [None] * len(data)
    for i in range(window - 1, len(data)):
        w = data[i-window+1:i+1]
        high[i] = max(w)
        low[i] = min(w)
    return high, low

def prepare_indicators(history):
    sma10 = sma(history, 10)
    sma20 = sma(history, 20)
    sma30 = sma(history, 30)
    sma50 = sma(history, 50)
    ema9 = ema(history, 9)
    ema12 = ema(history, 12)
    ema20 = ema(history, 20)
    ema21 = ema(history, 21)
    ema26 = ema(history, 26)
    ema50 = ema(history, 50)
    rsi14 = rsi(history, 14)
    stoch14 = stoch(history, 14)
    bb_mid, bb_up, bb_low = bollinger(history, 20, 2)
    d20h, d20l = donchian(history, 20)
    d50h, d50l = donchian(history, 50)
    macd_line = [None] * len(history)
    for i in range(len(history)):
        if ema12[i] is not None and ema26[i] is not None:
            macd_line[i] = ema12[i] - ema26[i]
    macd_sig = ema([x if x is not None else 0 for x in macd_line], 9)
    return {
        "sma10": sma10, "sma20": sma20, "sma30": sma30, "sma50": sma50,
        "ema9": ema9, "ema12": ema12, "ema20": ema20, "ema21": ema21,
        "ema26": ema26, "ema50": ema50,
        "rsi14": rsi14, "stoch14": stoch14,
        "bb_mid": bb_mid, "bb_up": bb_up, "bb_low": bb_low,
        "d20h": d20h, "d20l": d20l, "d50h": d50h, "d50l": d50l,
        "macd": macd_line, "macd_sig": macd_sig,
    }

# ========== СИГНАЛЫ 20 СТРАТЕГИЙ ==========

def _cross(ind, i, k1, k2):
    a, b = ind[k1], ind[k2]
    if i < 1 or a[i] is None or b[i] is None or a[i-1] is None or b[i-1] is None:
        return None
    if a[i] > b[i] and a[i-1] <= b[i-1]: return "BUY"
    if a[i] < b[i] and a[i-1] >= b[i-1]: return "SELL"
    return None

def _cross_val(ind, i, k, thresh):
    a = ind[k]
    if i < 1 or a[i] is None or a[i-1] is None: return None
    if a[i-1] < thresh and a[i] >= thresh: return "BUY"
    if a[i-1] > thresh and a[i] <= thresh: return "SELL"
    return None

def _price_cross(h, ind, i, k):
    a = ind[k]
    if i < 1 or a[i] is None or a[i-1] is None: return None
    if h[i] > a[i] and h[i-1] <= a[i-1]: return "BUY"
    if h[i] < a[i] and h[i-1] >= a[i-1]: return "SELL"
    return None

def sig_sma10_30(h, ind, i): return _cross(ind, i, "sma10", "sma30")
def sig_sma20_50(h, ind, i): return _cross(ind, i, "sma20", "sma50")
def sig_ema9_21(h, ind, i): return _cross(ind, i, "ema9", "ema21")
def sig_ema12_26(h, ind, i): return _cross(ind, i, "ema12", "ema26")

def sig_rsi_rev(h, ind, i):
    s = ind["rsi14"]
    if i < 1 or s[i] is None or s[i-1] is None: return None
    if s[i-1] < 30 and s[i] >= 30: return "BUY"
    if s[i-1] > 70 and s[i] <= 70: return "SELL"
    return None

def sig_rsi_50(h, ind, i): return _cross_val(ind, i, "rsi14", 50)

def sig_bb_rev(h, ind, i):
    if i < 1 or ind["bb_up"][i] is None: return None
    if h[i] < ind["bb_low"][i]: return "BUY"
    if h[i] > ind["bb_up"][i]: return "SELL"
    return None

def sig_bb_break(h, ind, i):
    if i < 1 or ind["bb_up"][i] is None or ind["bb_up"][i-1] is None: return None
    if h[i] > ind["bb_up"][i] and h[i-1] <= ind["bb_up"][i-1]: return "BUY"
    if h[i] < ind["bb_low"][i] and h[i-1] >= ind["bb_low"][i-1]: return "SELL"
    return None

def sig_macd_cross(h, ind, i): return _cross(ind, i, "macd", "macd_sig")
def sig_macd_zero(h, ind, i): return _cross_val(ind, i, "macd", 0)

def sig_stoch_rev(h, ind, i):
    s = ind["stoch14"]
    if i < 1 or s[i] is None or s[i-1] is None: return None
    if s[i-1] < 20 and s[i] >= 20: return "BUY"
    if s[i-1] > 80 and s[i] <= 80: return "SELL"
    return None

def sig_stoch_50(h, ind, i): return _cross_val(ind, i, "stoch14", 50)

def sig_triple_ema(h, ind, i):
    if i < 1: return None
    a, b, c = ind["ema9"], ind["ema21"], ind["ema50"]
    if a[i] is None or b[i] is None or c[i] is None or a[i-1] is None or b[i-1] is None: return None
    if a[i] > b[i] and b[i] > c[i] and a[i-1] <= b[i-1]: return "BUY"
    if a[i] < b[i] and b[i] < c[i] and a[i-1] >= b[i-1]: return "SELL"
    return None

def sig_donchian20(h, ind, i):
    if i < 1 or ind["d20h"][i-1] is None: return None
    if h[i] > ind["d20h"][i-1]: return "BUY"
    if h[i] < ind["d20l"][i-1]: return "SELL"
    return None

def sig_donchian50(h, ind, i):
    if i < 1 or ind["d50h"][i-1] is None: return None
    if h[i] > ind["d50h"][i-1]: return "BUY"
    if h[i] < ind["d50l"][i-1]: return "SELL"
    return None

def sig_mom10(h, ind, i):
    if i < 11: return None
    mom = h[i] - h[i-10]
    mom_prev = h[i-1] - h[i-11]
    if mom > 0 and mom_prev <= 0: return "BUY"
    if mom < 0 and mom_prev >= 0: return "SELL"
    return None

def sig_sma_slope(h, ind, i):
    s = ind["sma20"]
    if i < 2 or s[i] is None or s[i-1] is None or s[i-2] is None: return None
    if s[i] > s[i-1] and s[i-1] <= s[i-2]: return "BUY"
    if s[i] < s[i-1] and s[i-1] >= s[i-2]: return "SELL"
    return None

def sig_ema_slope(h, ind, i):
    s = ind["ema20"]
    if i < 2 or s[i] is None or s[i-1] is None or s[i-2] is None: return None
    if s[i] > s[i-1] and s[i-1] <= s[i-2]: return "BUY"
    if s[i] < s[i-1] and s[i-1] >= s[i-2]: return "SELL"
    return None

def sig_price_sma50(h, ind, i): return _price_cross(h, ind, i, "sma50")
def sig_price_ema20(h, ind, i): return _price_cross(h, ind, i, "ema20")

STRATEGIES = {
    "SMA 10/30": sig_sma10_30,
    "SMA 20/50": sig_sma20_50,
    "EMA 9/21": sig_ema9_21,
    "EMA 12/26": sig_ema12_26,
    "RSI 30/70": sig_rsi_rev,
    "RSI 50 cross": sig_rsi_50,
    "BB Reversal": sig_bb_rev,
    "BB Breakout": sig_bb_break,
    "MACD Cross": sig_macd_cross,
    "MACD Zero": sig_macd_zero,
    "Stoch 20/80": sig_stoch_rev,
    "Stoch 50": sig_stoch_50,
    "Triple EMA": sig_triple_ema,
    "Donchian 20": sig_donchian20,
    "Donchian 50": sig_donchian50,
    "Momentum 10": sig_mom10,
    "SMA Slope": sig_sma_slope,
    "EMA Slope": sig_ema_slope,
    "Price vs SMA50": sig_price_sma50,
    "Price vs EMA20": sig_price_ema20,
}

# ========== БЭКТЕСТ ==========

def run_strategy_on_pair(history, indicators, signal_func, sl_pct=0.005, tp_pct=0.010):
    trades = []
    position = None
    for i in range(1, len(history)):
        current_price = history[i]
        if position:
            if position["type"] == "BUY":
                if current_price <= position["sl"]:
                    trades.append({"result": "loss", "pnl": -1}); position = None
                elif current_price >= position["tp"]:
                    trades.append({"result": "win", "pnl": 2}); position = None
            elif position["type"] == "SELL":
                if current_price >= position["sl"]:
                    trades.append({"result": "loss", "pnl": -1}); position = None
                elif current_price <= position["tp"]:
                    trades.append({"result": "win", "pnl": 2}); position = None
        if not position:
            sig = signal_func(history, indicators, i)
            if sig == "BUY":
                position = {"type": "BUY", "sl": current_price * (1 - sl_pct), "tp": current_price * (1 + tp_pct)}
            elif sig == "SELL":
                position = {"type": "SELL", "sl": current_price * (1 + sl_pct), "tp": current_price * (1 - tp_pct)}
    if position:
        trades.append({"result": "timeout", "pnl": 0})
    return trades

def run_strategy_comparison(length=500):
    """Тестирует все 20 стратегий на одинаковых данных."""
    pair_histories = {sym: generate_history(PRICES[sym], length) for sym in PAIRS}
    pair_indicators = {sym: prepare_indicators(h) for sym, h in pair_histories.items()}
    
    results = []
    for name, func in STRATEGIES.items():
        total_pnl = 0
        total_trades = 0
        wins = 0
        for sym in PAIRS:
            trades = run_strategy_on_pair(pair_histories[sym], pair_indicators[sym], func)
            for t in trades:
                total_trades += 1
                total_pnl += t["pnl"]
                if t["result"] == "win": wins += 1
        winrate = (wins / total_trades * 100) if total_trades > 0 else 0
        results.append({
            "name": name,
            "pnl": round(total_pnl, 2),
            "trades": total_trades,
            "winrate": round(winrate, 1),
        })
    
    results.sort(key=lambda x: x["pnl"], reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1
    return results

# Одиночный бэктест (для кнопки на вкладке "Рынок")
def backtest_pair(start_price):
    history = generate_history(start_price, 1000)
    fast = sma(history, 10)
    slow = sma(history, 30)
    trades = []
    position = None
    for i in range(1, len(history)):
        if fast[i] is None or slow[i] is None or fast[i-1] is None or slow[i-1] is None:
            continue
        current_price = history[i]
        if position:
            if position["type"] == "BUY":
                if current_price <= position["sl"]:
                    trades.append({"result": "loss", "pnl": -1}); position = None
                elif current_price >= position["tp"]:
                    trades.append({"result": "win", "pnl": 2}); position = None
            elif position["type"] == "SELL":
                if current_price >= position["sl"]:
                    trades.append({"result": "loss", "pnl": -1}); position = None
                elif current_price <= position["tp"]:
                    trades.append({"result": "win", "pnl": 2}); position = None
        if not position:
            if fast[i] > slow[i] and fast[i-1] <= slow[i-1]:
                position = {"type": "BUY", "sl": current_price * 0.995, "tp": current_price * 1.010}
            elif fast[i] < slow[i] and fast[i-1] >= slow[i-1]:
                position = {"type": "SELL", "sl": current_price * 1.005, "tp": current_price * 0.990}
    if position:
        trades.append({"result": "timeout", "pnl": 0})
    return trades

def run_full_backtest():
    total_pnl = 0; total_trades = 0; wins = 0
    for symbol in PAIRS:
        for t in backtest_pair(PRICES[symbol]):
            total_trades += 1
            total_pnl += t["pnl"]
            if t["result"] == "win": wins += 1
    winrate = (wins / total_trades * 100) if total_trades > 0 else 0
    return {"pnl": round(total_pnl, 2), "trades": total_trades, "winrate": round(winrate, 1)}
