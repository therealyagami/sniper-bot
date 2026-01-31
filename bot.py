import asyncio
import pandas as pd
import numpy as np
import requests
import streamlit as st
from deriv_api import DerivAPI
from datetime import datetime
import time

# --- CONFIGURATION (THE CHAMPION SETTINGS) ---
APP_ID = 1089
SYMBOL = 'R_75'       # The Asset
RISK_STAKE = 10.0     # Fixed $10 Stake (Adjust based on your balance)
LEVERAGE = 100        # Multiplier Leverage

# LOGIC: DIAMOND SQUEEZE + RSI MOMENTUM
Z_TRIGGER = -2.0      # Volatility Squeeze
ER_FILTER = 0.4       # Trend Efficiency
RSI_BUY_MIN = 55.0    # Momentum Floor (Buy)
RSI_SELL_MAX = 45.0   # Momentum Ceiling (Sell)

# TARGETS (3:9 Risk/Reward)
SL_ATR_MULT = 3.0     
TP_ATR_MULT = 9.0     

# SECRETS MANAGEMENT
try:
    API_TOKEN = st.secrets["deriv_token"]
    DISCORD_URL = st.secrets["discord_webhook"]
except:
    st.error("❌ CRITICAL: Secrets missing! Add 'deriv_token' and 'discord_webhook'.")
    st.stop()

# --- NOTIFICATIONS ---
def send_discord(msg, color=3066993):
    if "http" not in DISCORD_URL: return
    try:
        requests.post(DISCORD_URL, json={
            "embeds": [{"description": msg, "color": color, "timestamp": datetime.now().isoformat()}]
        })
    except: pass

def log_trade(msg):
    # Logs to a permanent text file on the server
    try:
        with open("trade_log.txt", "a") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except: pass

# --- QUANT ENGINE ---
class QuantEngine:
    def __init__(self, data):
        self.df = pd.DataFrame(data, columns=['close'])
    
    def analyze(self):
        # 1. ATR (Volatility)
        self.df['tr'] = self.df['close'].diff().abs()
        atr = self.df['tr'].rolling(14).mean().iloc[-1]
        
        # 2. RSI (Momentum)
        delta = self.df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]

        # 3. Z-Score (Squeeze)
        rets = self.df['close'].pct_change().dropna()
        rol_std = rets.rolling(20).std()
        mean_vol = rol_std.rolling(100).mean().iloc[-1]
        std_vol = rol_std.rolling(100).std().iloc[-1]
        z = (rol_std.iloc[-1] - mean_vol) / std_vol if std_vol != 0 else 0
        
        # 4. Efficiency Ratio (Trend Quality)
        price = self.df['close']
        net = abs(price.iloc[-1] - price.iloc[-10])
        path = price.diff().abs().rolling(10).sum().iloc[-1]
        er = net / path if path > 0 else 0
        
        return z, er, atr, current_rsi, price.iloc[-1]

# --- EXECUTION ENGINE (GHOST SNIPER) ---
async def execute_trade(api, direction, tp_amount):
    """Executes a Market Multiplier Order with auto-SL and custom TP"""
    contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"
    try:
        await api.authorize(API_TOKEN)
        
        # 1. Get Contract Proposal
        proposal = await api.proposal({
            "proposal": 1,
            "amount": RISK_STAKE,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "symbol": SYMBOL,
            "multiplier": LEVERAGE
        })
        prop_id = proposal['proposal']['id']
        
        # 2. Execute Market Order
        buy_order = await api.buy({"buy": prop_id, "price": RISK_STAKE + 20}) # +20 buffer
        contract_id = buy_order['buy']['contract_id']
        
        # 3. Apply Take Profit (Stop Loss is auto-set to Stake)
        await api.contract_update_history(contract_id, {
            "contract_id": contract_id,
            "limit_order": {
                "take_profit": round(tp_amount, 2)
            }
        })
        
        # 4. Notify
        msg = f"⚡ **OPENED {direction}**\nStake: ${RISK_STAKE}\nTarget Profit: ${round(tp_amount, 2)}"
        send_discord(msg, 5763719 if direction == "BUY" else 15548997)
        log_trade(f"EXECUTION: {direction} | ID: {contract_id}")
        return True
        
    except Exception as e:
        send_discord(f"❌ EXECUTION FAILED: {str(e)}", 10038562)
        log_trade(f"ERROR: {str(e)}")
        return False

# --- MAIN LOOP ---
st.set_page_config(page_title="R_75 Sniper", layout="wide")
st.title(f"🎯 R_75 Momentum Sniper (Winner: RSI Filter)")

# Persistent State
if "ghost_order" not in st.session_state:
    st.session_state.ghost_order = None # Stores the pending breakout levels

# Dashboard Metrics
c1, c2, c3, c4 = st.columns(4)
stats = st.empty()

async def main_loop():
    api = DerivAPI(app_id=APP_ID)
    
    # Startup Alert
    send_discord("🟢 **SYSTEM ONLINE**\nStrategy: R_75 Diamond + RSI Filter", 3066993)
    
    while True:
        try:
            # 1. Get Live Data
            ticks = await api.ticks_history({'ticks_history': SYMBOL, 'count': 2000, 'end': 'latest', 'style': 'candles', 'granularity': 60})
            if 'candles' in ticks:
                closes = [float(t['close']) for t in ticks['candles']]
                engine = QuantEngine(closes)
                z, er, atr, rsi, price = engine.analyze()
                
                # UI Update
                with c1: st.metric("Z-Score (<-2.0)", f"{z:.2f}", delta_color="inverse")
                with c2: st.metric("RSI (55/45)", f"{rsi:.1f}")
                with c3: st.metric("Mode", "HUNTING" if not st.session_state.ghost_order else "GHOST ACTIVE")
                with c4: st.metric("Price", f"{price:.2f}")

                # ---------------- STRATEGY LOGIC ----------------
                
                # PHASE 1: SEARCH FOR SQUEEZE (If no ghost exists)
                if st.session_state.ghost_order is None:
                    if z < Z_TRIGGER and er > ER_FILTER:
                        # Squeeze Found! Define the Breakout Levels (Ghost Order)
                        st.session_state.ghost_order = {
                            "buy_lvl": price + (atr * 0.5),
                            "sell_lvl": price - (atr * 0.5),
                            "atr": atr,
                            "created": time.time()
                        }
                        log_msg = f"SQUEEZE DETECTED | Z: {z:.2f} | RSI: {rsi:.1f}"
                        log_trade(log_msg)
                        send_discord(f"👀 **SQUEEZE FOUND**\nWaiting for Momentum Breakout...", 16776960)

                # PHASE 2: MONITOR FOR BREAKOUT + MOMENTUM
                else:
                    order = st.session_state.ghost_order
                    
                    # Expiry Rule: If no breakout in 15 mins, cancel.
                    if time.time() - order['created'] > 900:
                        st.session_state.ghost_order = None
                        log_trade("GHOST EXPIRED (No Breakout)")
                        continue
                    
                    # TRIGGER CHECKS
                    
                    # Check BUY Breakout
                    if price >= order['buy_lvl']:
                        if rsi > RSI_BUY_MIN: # MOMENTUM CONFIRMED
                            reward = RISK_STAKE * (TP_ATR_MULT / SL_ATR_MULT)
                            await execute_trade(api, "BUY", reward)
                            st.session_state.ghost_order = None # Reset
                        else:
                            pass # Breakout but weak momentum (Fakeout)

                    # Check SELL Breakout
                    elif price <= order['sell_lvl']:
                        if rsi < RSI_SELL_MAX: # MOMENTUM CONFIRMED
                            reward = RISK_STAKE * (TP_ATR_MULT / SL_ATR_MULT)
                            await execute_trade(api, "SELL", reward)
                            st.session_state.ghost_order = None # Reset
                        else:
                            pass # Breakout but weak momentum (Fakeout)

        except Exception as e:
            print(f"Loop Error: {e}")
            
        await asyncio.sleep(2) # Scan every 2 seconds

if __name__ == "__main__":
    asyncio.run(main_loop())