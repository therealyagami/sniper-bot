import asyncio
import pandas as pd
import numpy as np
import requests
import streamlit as st
from deriv_api import DerivAPI
from datetime import datetime
import os

# --- CONFIGURATION ---
APP_ID = 1089
# THE SURVIVORS LIST (Only assets that passed the Stress Test)
SYMBOLS = ['R_75', '1HZ25V', '1HZ50V'] 

# RISK MANAGEMENT
RISK_PCT = 0.05       # 5% Risk per trade
MAX_OPEN_TRADES = 3   # Don't over-leverage

# STRATEGY SETTINGS (Diamond)
Z_TRIGGER = -2.0
ER_FILTER = 0.4
SL_ATR_MULT = 3.0     # Initial Stop
TP_ATR_MULT = 9.0     # Ultimate Target (if trail doesn't catch it)
TRAIL_TRIGGER = 3.0   # Move to BE after 3x ATR profit

# SECRET MANAGEMENT
try:
    DISCORD_WEBHOOK_URL = st.secrets["discord_webhook"]
except:
    DISCORD_WEBHOOK_URL = "PASTE_LOCAL_WEBHOOK_FOR_TESTING"

# --- LOGGING ENGINE ---
def log_to_file(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open("bot_history.log", "a") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except: pass

# --- MATH ENGINE ---
class SyntheticMathEngine:
    def __init__(self, price_data):
        self.data = pd.Series(price_data)

    def get_atr(self, window=14):
        if len(self.data) < window: return 0
        tr = np.abs(self.data - self.data.shift(1))
        return tr.rolling(window=window).mean().iloc[-1]

    def get_z_score(self, window=20, lookback=100):
        if len(self.data) < lookback: return 0
        returns = self.data.pct_change().dropna()
        rol_std = returns.rolling(window=window).std()
        mean_vol = rol_std.rolling(window=lookback).mean().iloc[-1]
        std_vol = rol_std.rolling(window=lookback).std().iloc[-1]
        if std_vol == 0: return 0
        return (rol_std.iloc[-1] - mean_vol) / std_vol

    def get_er(self, length=10):
        if len(self.data) < length: return 0
        net = np.abs(self.data.iloc[-1] - self.data.iloc[-length])
        path = np.abs(self.data - self.data.shift(1)).rolling(window=length).sum().iloc[-1]
        return net / path if path > 0 else 0

    def analyze(self):
        z = self.get_z_score()
        er = self.get_er()
        atr = self.get_atr()
        price = self.data.iloc[-1]
        
        signal = False
        if z < Z_TRIGGER and er > ER_FILTER:
            signal = True
            
        return {
            "signal": signal,
            "z": z,
            "er": er,
            "price": price,
            "atr": atr
        }

# --- UTILS ---
async def fetch_data(symbol):
    api = DerivAPI(app_id=APP_ID)
    try:
        # Note: 1HZ indices need granular tick data, fetching candles is safer
        ticks = await api.ticks_history({'ticks_history': symbol, 'count': 3000, 'end': 'latest', 'style': 'candles', 'granularity': 60})
        if 'candles' in ticks: 
            return [float(t['close']) for t in ticks['candles']]
        return []
    except: return []
    finally: await api.clear()

def send_alert(symbol, data, side, entry, sl, tp, risk_usd):
    if "http" not in DISCORD_WEBHOOK_URL: return
    
    color = 5763719 if side == "BUY" else 15548997
    msg = {
        "content": f"🚨 **HEDGE FUND SIGNAL: {symbol}**",
        "embeds": [{
            "title": f"{side} ENTRY DETECTED",
            "color": color,
            "fields": [
                {"name": "Entry Price", "value": f"{entry:.4f}", "inline": True},
                {"name": "Risk (5%)", "value": f"${risk_usd:.2f}", "inline": True},
                {"name": "Stop Loss", "value": f"{sl:.4f}", "inline": True},
                {"name": "Take Profit", "value": f"{tp:.4f}", "inline": True},
                {"name": "Trailing Logic", "value": "Active > 3 ATR", "inline": False}
            ],
            "footer": {"text": f"Z-Score: {data['z']:.2f} | ER: {data['er']:.2f}"}
        }]
    }
    try: requests.post(DISCORD_WEBHOOK_URL, json=msg)
    except: pass

# --- DASHBOARD ---
st.set_page_config(page_title="Quantum Hedge Fund", layout="wide")
st.title("🏦 Quantum Hedge Fund: Multi-Asset Sentinel")

# Initialize Session State for 'Last Trade Time' per symbol to avoid spam
if "last_trades" not in st.session_state:
    st.session_state.last_trades = {sym: 0 for sym in SYMBOLS}

# Sidebar Configuration
st.sidebar.header("Fund Controls")
equity = st.sidebar.number_input("Current Account Equity ($)", value=1000, step=100)
risk_amt = equity * RISK_PCT
st.sidebar.write(f"**Risk per Trade:** ${risk_amt:.2f}")

# Main Loop
if st.checkbox("Active Trading System", value=True):
    status_cols = st.columns(len(SYMBOLS))
    charts = {sym: st.empty() for sym in SYMBOLS}
    
    while True:
        for i, sym in enumerate(SYMBOLS):
            prices = asyncio.run(fetch_data(sym))
            
            with status_cols[i]:
                if len(prices) > 100:
                    engine = SyntheticMathEngine(prices)
                    data = engine.analyze()
                    
                    # Display Live Stats
                    st.metric(f"{sym}", f"{data['price']:.2f}", f"Z: {data['z']:.2f}")
                    
                    # Signal Logic
                    if data['signal']:
                        # Cooldown Check (5 mins)
                        import time
                        if time.time() - st.session_state.last_trades[sym] > 300:
                            
                            # Calc Levels
                            atr = data['atr']
                            buy_entry = data['price'] + (atr*0.5)
                            buy_sl = buy_entry - (atr*SL_ATR_MULT)
                            buy_tp = buy_entry + (atr*TP_ATR_MULT)
                            
                            sell_entry = data['price'] - (atr*0.5)
                            sell_sl = sell_entry + (atr*SL_ATR_MULT)
                            sell_tp = sell_entry - (atr*TP_ATR_MULT)
                            
                            # Log & Alert
                            log_msg = f"{sym} SIGNAL | Z:{data['z']:.2f} | Buy: {buy_entry} | Sell: {sell_entry}"
                            log_to_file(log_msg)
                            
                            # Send Buy Alert
                            send_alert(sym, data, "BUY", buy_entry, buy_sl, buy_tp, risk_amt)
                            # Send Sell Alert
                            send_alert(sym, data, "SELL", sell_entry, sell_sl, sell_tp, risk_amt)
                            
                            st.toast(f"🚨 Signal Sent for {sym}!")
                            st.session_state.last_trades[sym] = time.time()
                    
                    # Mini Chart
                    charts[sym].line_chart(prices[-50:], height=150)
                else:
                    st.warning(f"Loading {sym}...")
        
        asyncio.run(asyncio.sleep(5)) # Scan cycle