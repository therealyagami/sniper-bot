import asyncio
import pandas as pd
import numpy as np
import requests
import streamlit as st
from deriv_api import DerivAPI
from datetime import datetime

# --- CONFIGURATION ---
import os

# --- CONFIGURATION ---
APP_ID = 1089
SYMBOL = 'R_75'

# Try to get the secret from the Cloud, otherwise use a placeholder
try:
    DISCORD_WEBHOOK_URL = st.secrets["discord_webhook"]
except:
    # This keeps it working on your laptop for testing
    DISCORD_WEBHOOK_URL = "PASTE_YOUR_NEW_WEBHOOK_HERE_FOR_LOCAL_TESTING"

# STRATEGY: Diamond Settings (Row 1)
Z_TRIGGER = -2.0
ER_FILTER = 0.4
SL_MULT = 3.0
TP_MULT = 9.0

# --- LOGGING ENGINE ---
def log_to_file(msg):
    """Writes a message to a permanent text file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("bot_history.log", "a") as f:
        f.write(f"[{timestamp}] {msg}\n")

# --- MATH ENGINE ---
class SyntheticMathEngine:
    def __init__(self, price_data):
        self.data = pd.Series(price_data)

    def get_atr(self, window=14):
        if len(self.data) < window: return 0
        tr = np.abs(self.data - self.data.shift(1))
        return tr.rolling(window=window).mean().iloc[-1]

    def get_volatility_z_score(self, window=20, lookback=100):
        if len(self.data) < lookback: return 0
        returns = self.data.pct_change().dropna()
        rolling_vol = returns.rolling(window=window).std()
        
        current_vol = rolling_vol.iloc[-1]
        mean_vol = rolling_vol.rolling(window=lookback).mean().iloc[-1]
        std_vol = rolling_vol.rolling(window=lookback).std().iloc[-1]
        
        if std_vol == 0 or np.isnan(std_vol): return 0
        return (current_vol - mean_vol) / std_vol

    def get_efficiency_ratio(self, length=10):
        if len(self.data) < length: return 0
        net_change = np.abs(self.data.iloc[-1] - self.data.iloc[-length])
        sum_path = np.abs(self.data - self.data.shift(1)).rolling(window=length).sum().iloc[-1]
        if sum_path == 0: return 0
        return net_change / sum_path

    def analyze_market_state(self):
        z_score = self.get_volatility_z_score()
        er = self.get_efficiency_ratio()
        atr = self.get_atr()
        price = self.data.iloc[-1]
        
        state = {
            "status": "WAIT",
            "message": f"Scanning... Z: {z_score:.2f} | ER: {er:.2f}",
            "color": "gray",
            "signal": False
        }

        if z_score < Z_TRIGGER:
            if er > ER_FILTER:
                state["status"] = "🚨 SNIPER ENTRY"
                state["message"] = f"EXTREME SQUEEZE (Z: {z_score:.2f})"
                state["color"] = "red"
                state["signal"] = True
                
                # Calc Levels
                state["buy_entry"] = price + (atr * 0.5)
                state["buy_sl"] = state["buy_entry"] - (atr * SL_MULT)
                state["buy_tp"] = state["buy_entry"] + (atr * TP_MULT)
                
                state["sell_entry"] = price - (atr * 0.5)
                state["sell_sl"] = state["sell_entry"] + (atr * SL_MULT)
                state["sell_tp"] = state["sell_entry"] - (atr * TP_MULT)
                
            else:
                state["status"] = "⚠️ PRE-SQUEEZE"
                state["message"] = f"Vol low ({z_score:.2f}), waiting for Trend ({er:.2f})"
                state["color"] = "orange"

        return state, z_score, er, price

# --- UTILS ---
async def fetch_data():
    api = DerivAPI(app_id=APP_ID)
    try:
        ticks = await api.ticks_history({'ticks_history': SYMBOL, 'count': 3000, 'end': 'latest', 'style': 'ticks'})
        if 'history' in ticks: return [float(t) for t in ticks['history']['prices']]
        return []
    except: return []
    finally: await api.clear()

def send_discord_alert(state, symbol):
    if "http" not in DISCORD_WEBHOOK_URL: return
    
    msg = {
        "content": f"🚨 **SIGNAL: {symbol}**",
        "embeds": [{
            "title": "Breakout Detected",
            "color": 16711680,
            "fields": [
                {"name": "Buy Stop", "value": f"{state['buy_entry']:.2f}", "inline": True},
                {"name": "SL / TP", "value": f"{state['buy_sl']:.2f} / {state['buy_tp']:.2f}", "inline": True},
                {"name": "---", "value": "---", "inline": False},
                {"name": "Sell Stop", "value": f"{state['sell_entry']:.2f}", "inline": True},
                {"name": "SL / TP", "value": f"{state['sell_sl']:.2f} / {state['sell_tp']:.2f}", "inline": True}
            ],
            "footer": {"text": f"Z-Score: {state['message']}"}
        }]
    }
    try: requests.post(DISCORD_WEBHOOK_URL, json=msg)
    except: pass

# --- DASHBOARD ---
st.set_page_config(page_title=f"Bot {SYMBOL}", layout="wide")
st.title(f"💎 {SYMBOL} Diamond Logger")

m1, m2, m3, m4 = st.columns(4)
with m1: st.metric("Settings", "Diamond (Row 1)")
with m2: st.metric("Risk", "1:3 Ratio")
with m3: st.metric("Stop Loss", "3.0 ATR")
with m4: st.metric("Status", "Logging Active")

if st.button("Start Scanner"):
    st.write("--- Scanner Active ---")
    log_to_file("Bot Started. Scanner Active.") # Log start
    
    board = st.empty()
    last_signal_time = 0
    
    while True:
        prices = asyncio.run(fetch_data())
        
        with board.container():
            if len(prices) > 100:
                engine = SyntheticMathEngine(prices)
                state, z, er, p = engine.analyze_market_state()
                
                # STATUS
                st.markdown(f"## STATUS: :{state['color']}[{state['status']}]")
                
                # METRICS
                c1, c2, c3 = st.columns(3)
                c1.metric("Z-Score", f"{z:.2f}", delta="Trigger -2.0")
                c2.metric("Efficiency", f"{er:.2f}", delta="Filter 0.4")
                c3.metric("Price", f"{p:.2f}")
                st.divider()
                
                # SIGNAL LOGIC
                if state["signal"]:
                    st.error("⚡ **OPPORTUNITY DETECTED**")
                    
                    b_col, s_col = st.columns(2)
                    with b_col:
                        st.info("🔵 **BUY STOP**")
                        st.code(f"Entry: {state['buy_entry']:.2f}\nSL:    {state['buy_sl']:.2f}\nTP:    {state['buy_tp']:.2f}")
                    with s_col:
                        st.warning("🔴 **SELL STOP**")
                        st.code(f"Entry: {state['sell_entry']:.2f}\nSL:    {state['sell_sl']:.2f}\nTP:    {state['sell_tp']:.2f}")
                    
                    import time
                    if time.time() - last_signal_time > 300: 
                        # 1. Send Discord
                        send_discord_alert(state, SYMBOL)
                        
                        # 2. Log to File
                        log_msg = f"SIGNAL FIRED | Price: {p} | Buy: {state['buy_entry']:.2f} | Sell: {state['sell_entry']:.2f} | Z: {z:.2f}"
                        log_to_file(log_msg)
                        
                        st.toast("Signal Logged to bot_history.log")
                        last_signal_time = time.time()
                
                elif state["status"] == "⚠️ PRE-SQUEEZE":
                    st.warning(f"**Watchlist:** {state['message']}")
                else:
                    st.info("Scanning...")

                st.line_chart(prices[-100:])
            else:
                st.warning("Fetching Data...")
        
        asyncio.run(asyncio.sleep(3))