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
SYMBOLS = ['R_75', '1HZ25V', '1HZ50V'] # The Survivors

# RISK & STRATEGY
RISK_PCT = 0.05       
Z_TRIGGER = -2.0
ER_FILTER = 0.4
SL_ATR_MULT = 3.0     
TP_ATR_MULT = 9.0     

# SECRET MANAGEMENT
try:
    DISCORD_WEBHOOK_URL = st.secrets["discord_webhook"]
except:
    DISCORD_WEBHOOK_URL = "PASTE_LOCAL_WEBHOOK_FOR_TESTING"

# --- NOTIFICATION ENGINE ---
def send_discord_msg(title, description, color):
    """Generic Discord Embed Sender"""
    if "http" not in DISCORD_WEBHOOK_URL: return
    
    msg = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color, # Decimal color code
            "timestamp": datetime.now().isoformat()
        }]
    }
    try: requests.post(DISCORD_WEBHOOK_URL, json=msg)
    except: pass

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
            
        return {"signal": signal, "z": z, "er": er, "price": price, "atr": atr}

# --- UTILS ---
async def fetch_data(symbol):
    api = DerivAPI(app_id=APP_ID)
    try:
        ticks = await api.ticks_history({'ticks_history': symbol, 'count': 3000, 'end': 'latest', 'style': 'candles', 'granularity': 60})
        if 'candles' in ticks: 
            return [float(t['close']) for t in ticks['candles']]
        return []
    except: return []
    finally: await api.clear()

# --- MAIN APP ---
st.set_page_config(page_title="Quantum HQ", layout="wide")
st.title("🏦 Quantum Hedge Fund: Active Sentinel")

# 1. STARTUP CHECK (Sends Discord msg only on server restart)
if "bot_active" not in st.session_state:
    st.session_state.bot_active = True
    st.session_state.last_trades = {sym: 0 for sym in SYMBOLS}
    
    startup_msg = (
        f"**Assets:** {', '.join(SYMBOLS)}\n"
        f"**Strategy:** Diamond (Z < -2.0)\n"
        f"**Risk:** {RISK_PCT*100}% per trade\n"
        f"**Targets:** SL 3.0 ATR | TP 9.0 ATR"
    )
    send_discord_msg("🟢 SYSTEM ONLINE", startup_msg, 3066993) # Green

# 2. UI SIDEBAR
st.sidebar.header("Fund Controls")
equity = st.sidebar.number_input("Equity ($)", value=1000, step=100)
risk_amt = equity * RISK_PCT
st.sidebar.metric("Risk Amount", f"${risk_amt:.2f}")

# 3. MAIN LOOP (Auto-Runs)
status_cols = st.columns(len(SYMBOLS))
charts = {sym: st.empty() for sym in SYMBOLS}
st.write("---")
st.caption("System is scanning. Do not close this tab if running locally.")

while True:
    for i, sym in enumerate(SYMBOLS):
        prices = asyncio.run(fetch_data(sym))
        
        with status_cols[i]:
            if len(prices) > 100:
                engine = SyntheticMathEngine(prices)
                data = engine.analyze()
                
                # Live Display
                st.metric(f"{sym}", f"{data['price']:.2f}", f"Z: {data['z']:.2f}")
                
                # SIGNAL LOGIC
                if data['signal']:
                    # 5 Minute Cooldown per asset
                    import time
                    if time.time() - st.session_state.last_trades[sym] > 300:
                        
                        atr = data['atr']
                        # Buy Params
                        b_entry = data['price'] + (atr*0.5)
                        b_sl = b_entry - (atr*SL_ATR_MULT)
                        b_tp = b_entry + (atr*TP_ATR_MULT)
                        
                        # Sell Params
                        s_entry = data['price'] - (atr*0.5)
                        s_sl = s_entry + (atr*SL_ATR_MULT)
                        s_tp = s_entry - (atr*TP_ATR_MULT)
                        
                        # DISCORD ALERT
                        fields_desc = (
                            f"**BUY STOP:** {b_entry:.4f}\n"
                            f"SL: {b_sl:.4f} | TP: {b_tp:.4f}\n"
                            f"----------------\n"
                            f"**SELL STOP:** {s_entry:.4f}\n"
                            f"SL: {s_sl:.4f} | TP: {s_tp:.4f}"
                        )
                        send_discord_msg(f"🚨 SIGNAL: {sym}", fields_desc, 15158332) # Red
                        
                        # Log
                        log_to_file(f"{sym} SIGNAL | Z:{data['z']:.2f}")
                        st.toast(f"Signal sent for {sym}")
                        
                        # Update Cooldown
                        st.session_state.last_trades[sym] = time.time()
                
                # Chart
                charts[sym].line_chart(prices[-50:], height=150)
            else:
                st.warning(f"Connecting to {sym}...")
    
    # 5 second heartbeat
    asyncio.run(asyncio.sleep(5))