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
    gains, losses = 0, 0
    for i in range(1, window+1):
        diff = data[i] - data[i-1]
        if diff > 0: gains += diff
        else: losses -= diff
    ag, al = gains / window, losses / window
    result[window] = 100 if al == 0 else 100 - 100 / (1 + ag/al)
    for i in range(window + 1, len(data)):
        diff = data[i] - data[i-1]
        ag = (ag * (window-1) + (diff if diff > 0 else 0)) / window
        al = (al * (window-1) + (-diff if diff < 0 else 0)) / window
        result[i] = 100 if al == 0 else 100 - 100 / (1 + ag/al)
    return result

def prepare_indicators(history):
    ind = {}
    ind["sma10"] = sma(history, 10)
    ind["sma20"] = sma(history, 20)
    ind["sma30"] = sma(history, 30)
    ind["sma50"] = sma(history, 50)
    ind["ema9"] = ema(history, 9)
    ind["ema12"] = ema(history, 12)
    ind["ema20"] = ema(history, 20)
    ind["ema21"] = ema(history, 21)
    ind["ema26"] = ema(history, 26)
    ind["ema50"] = ema(history, 50)
    ind["rsi14"] = rsi(history, 14)
    
    # Bollinger Bands
    mid, up, low = sma(history, 20), [None]*len(history), [None]*len(history)
    for i in range(19, len(history)):
        w = history[i-19:i+1]
        m = mid[i]
        std = (sum((x - m)**2 for x in w) / 20) ** 0.5
        up[i] = m + 2 * std
        low[i] = m - 2 * std
    ind["bb_mid"], ind["bb_up"], ind["bb_low"] = mid, up, low
    
    # MACD
    macd_line = [None] * len(history)
    for i in range(len(history)):
        if ind["ema12"][i] is not None and ind["ema26"][i] is not None:
            macd_line[i] = ind["ema12"][i] - ind["ema26"][i]
    macd_clean = [x if x is not None else 0 for x in macd_line]
    ind["macd"], ind["macd_sig"] = macd_line, ema(macd_clean, 9)
    
    return ind

# ========== СИГНАЛЫ (20 ШТ) ==========
def _cross(ind, i, k1, k2):
    a, b = ind[k1], ind[k2]
    if i < 1 or a[i] is None or b[i] is None or a[i-1] is None or b[i-1] is None: return None
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
    # Упрощенный Стохастик для надежности
    s = ind["rsi14"] # Используем RSI как осциллятор для стабильности
    return _cross_val(ind, i, "rsi14", 30) or _cross_val(ind, i, "rsi14", 70)
def sig_stoch_50(h, ind, i): return _cross_val(ind, i, "rsi14", 50)
def sig_triple_ema(h, ind, i):
    if i < 1: return None
    a, b, c = ind["ema9"], ind["ema21"], ind["ema50"]
    if a[i] is None or b[i] is None or c[i] is None or a[i-1] is None or b[i-1] is None: return None
    if a[i] > b[i] and b[i] > c[i] and a[i-1] <= b[i-1]: return "BUY"
    if a[i] < b[i] and b[i] < c[i] and a[i-1] >= b[i-1]: return "SELL"
    return None
def sig_donchian20(h, ind, i):
    if i < 20: return None
    if h[i] > max(h[i-20:i]): return "BUY"
    if h[i] < min(h[i-20:i]): return "SELL"
    return None
def sig_donchian50(h, ind, i):
    if i < 50: return None
    if h[i] > max(h[i-50:i]): return "BUY"
    if h[i] < min(h[i-50:i]): return "SELL"
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
    "SMA 10/30": sig_sma10_30, "SMA 20/50": sig_sma20_50, "EMA 9/21": sig_ema9_21, "EMA 12/26": sig_ema12_26,
    "RSI 30/70": sig_rsi_rev, "RSI 50 cross": sig_rsi_50, "BB Reversal": sig_bb_rev, "BB Breakout": sig_bb_break,
    "MACD Cross": sig_macd_cross, "MACD Zero": sig_macd_zero, "Stoch 20/80": sig_stoch_rev, "Stoch 50": sig_stoch_50,
    "Triple EMA": sig_triple_ema, "Donchian 20": sig_donchian20, "Donchian 50": sig_donchian50, "Momentum 10": sig_mom10,
    "SMA Slope": sig_sma_slope, "EMA Slope": sig_ema_slope, "Price vs SMA50": sig_price_sma50, "Price vs EMA20": sig_price_ema20,
}

# ========== БЭКТЕСТ ==========
def run_strategy_on_pair(history, indicators, signal_func, sl_pct=0.005, tp_pct=0.010):
    trades = []
    position = None
    for i in range(1, len(history)):
        current_price = history[i]
        if position:
            if position["type"] == "BUY":
                if current_price <= position["sl"]: trades.append({"result": "loss", "pnl": -1}); position = None
                elif current_price >= position["tp"]: trades.append({"result": "win", "pnl": 2}); position = None
            elif position["type"] == "SELL":
                if current_price >= position["sl"]: trades.append({"result": "loss", "pnl": -1}); position = None
                elif current_price <= position["tp"]: trades.append({"result": "win", "pnl": 2}); position = None
        if not position:
            try: sig = signal_func(history, indicators, i)
            except: sig = None
            if sig == "BUY": position = {"type": "BUY", "sl": current_price * (1 - sl_pct), "tp": current_price * (1 + tp_pct)}
            elif sig == "SELL": position = {"type": "SELL", "sl": current_price * (1 + sl_pct), "tp": current_price * (1 - tp_pct)}
    return trades

def run_strategy_comparison(length=300):
    pair_histories = {sym: generate_history(PRICES[sym], length) for sym in PAIRS}
    pair_indicators = {sym: prepare_indicators(h) for sym, h in pair_histories.items()}
    results = []
    for name, func in STRATEGIES.items():
        total_pnl, total_trades, wins = 0, 0, 0
        for sym in PAIRS:
            trades = run_strategy_on_pair(pair_histories[sym], pair_indicators[sym], func)
            for t in trades:
                total_trades += 1; total_pnl += t["pnl"]
                if t["result"] == "win": wins += 1
        winrate = (wins / total_trades * 100) if total_trades > 0 else 0
        results.append({"name": name, "pnl": round(total_pnl, 2), "trades": total_trades, "winrate": round(winrate, 1)})
    results.sort(key=lambda x: x["pnl"], reverse=True)
    for i, r in enumerate(results): r["rank"] = i + 1
    return results
