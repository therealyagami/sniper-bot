import asyncio
import pandas as pd
import numpy as np
import requests
import streamlit as st
from deriv_api import DerivAPI
from datetime import datetime
import time

# --- CONFIGURATION ---
APP_ID = 1089
SYMBOL = 'R_75'       
RISK_STAKE = 10.0     
LEVERAGE = 100        

# STRATEGY
Z_TRIGGER = -2.0
ER_FILTER = 0.4
RSI_BUY_MIN = 55.0    
RSI_SELL_MAX = 45.0   
SL_ATR_MULT = 3.0     
TP_ATR_MULT = 9.0     

# --- SECRET HANDLING (CRASH PROOF) ---
MODE = "PAPER" # Default
API_TOKEN = None

try:
    DISCORD_URL = st.secrets["discord_webhook"]
except:
    DISCORD_URL = "PASTE_LOCAL_WEBHOOK_HERE"

try:
    API_TOKEN = st.secrets["deriv_token"]
    MODE = "LIVE"
except:
    MODE = "PAPER" # Fallback if no token found

# --- NOTIFICATIONS ---
def send_discord(msg, color):
    if "http" not in DISCORD_URL: return
    try:
        requests.post(DISCORD_URL, json={
            "embeds": [{"description": msg, "color": color, "timestamp": datetime.now().isoformat()}]
        })
    except: pass

def log_trade(msg):
    try:
        with open("paper_trading_log.txt", "a") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except: pass

# --- MATH ENGINE ---
class QuantEngine:
    def __init__(self, data):
        self.df = pd.DataFrame(data, columns=['close'])
    
    def analyze(self):
        self.df['tr'] = self.df['close'].diff().abs()
        atr = self.df['tr'].rolling(14).mean().iloc[-1]
        
        delta = self.df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        rets = self.df['close'].pct_change().dropna()
        rol_std = rets.rolling(20).std()
        mean_vol = rol_std.rolling(100).mean().iloc[-1]
        std_vol = rol_std.rolling(100).std().iloc[-1]
        z = (rol_std.iloc[-1] - mean_vol) / std_vol if std_vol != 0 else 0
        
        price = self.df['close']
        net = abs(price.iloc[-1] - price.iloc[-10])
        path = price.diff().abs().rolling(10).sum().iloc[-1]
        er = net / path if path > 0 else 0
        
        return z, er, atr, rsi.iloc[-1], price.iloc[-1]

# --- EXECUTION ENGINE (DUAL MODE) ---
async def execute_trade(api, direction, tp_amount):
    if MODE == "PAPER":
        # SIMULATION
        msg = f"📝 **PAPER TRADE: {direction}**\nStake: ${RISK_STAKE}\nTarget: ${round(tp_amount, 2)}"
        send_discord(msg, 16776960) # Yellow for Paper
        log_trade(f"PAPER EXECUTION: {direction}")
        return True
    
    elif MODE == "LIVE":
        # REAL EXECUTION (Requires Token)
        contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"
        try:
            await api.authorize(API_TOKEN)
            proposal = await api.proposal({
                "proposal": 1, "amount": RISK_STAKE, "basis": "stake",
                "contract_type": contract_type, "currency": "USD",
                "symbol": SYMBOL, "multiplier": LEVERAGE
            })
            prop_id = proposal['proposal']['id']
            buy_order = await api.buy({"buy": prop_id, "price": RISK_STAKE + 20})
            contract_id = buy_order['buy']['contract_id']
            await api.contract_update_history(contract_id, {
                "contract_id": contract_id,
                "limit_order": {"take_profit": round(tp_amount, 2)}
            })
            send_discord(f"⚡ **LIVE EXECUTION: {direction}**", 5763719)
            return True
        except Exception as e:
            send_discord(f"❌ LIVE FAIL: {str(e)}", 10038562)
            return False

# --- MAIN LOOP ---
st.set_page_config(page_title="R_75 Sniper", layout="wide")
st.title(f"🎯 R_75 Momentum Sniper ({MODE} MODE)")

if "ghost_order" not in st.session_state: st.session_state.ghost_order = None
c1, c2, c3, c4 = st.columns(4)

async def main_loop():
    api = DerivAPI(app_id=APP_ID)
    send_discord(f"🟢 **SYSTEM ONLINE**\nMode: {MODE} TRADING", 3066993)
    
    while True:
        try:
            ticks = await api.ticks_history({'ticks_history': SYMBOL, 'count': 2000, 'end': 'latest', 'style': 'candles', 'granularity': 60})
            if 'candles' in ticks:
                closes = [float(t['close']) for t in ticks['candles']]
                engine = QuantEngine(closes)
                z, er, atr, rsi, price = engine.analyze()
                
                with c1: st.metric("Z-Score", f"{z:.2f}")
                with c2: st.metric("RSI", f"{rsi:.1f}")
                with c3: st.metric("State", "HUNTING" if not st.session_state.ghost_order else "GHOST ACTIVE")
                
                if st.session_state.ghost_order is None:
                    if z < Z_TRIGGER and er > ER_FILTER:
                        st.session_state.ghost_order = {
                            "buy_lvl": price + (atr * 0.5), "sell_lvl": price - (atr * 0.5),
                            "atr": atr, "created": time.time()
                        }
                        send_discord(f"👀 **SQUEEZE FOUND**\nWaiting for Momentum...", 16776960)
                else:
                    order = st.session_state.ghost_order
                    if time.time() - order['created'] > 900:
                        st.session_state.ghost_order = None; continue
                    
                    if price >= order['buy_lvl']:
                        if rsi > RSI_BUY_MIN:
                            await execute_trade(api, "BUY", RISK_STAKE * (TP_ATR_MULT/SL_ATR_MULT))
                            st.session_state.ghost_order = None
                    elif price <= order['sell_lvl']:
                        if rsi < RSI_SELL_MAX:
                            await execute_trade(api, "SELL", RISK_STAKE * (TP_ATR_MULT/SL_ATR_MULT))
                            st.session_state.ghost_order = None
                            
        except Exception as e: print(e)
        await asyncio.sleep(2)

if __name__ == "__main__": asyncio.run(main_loop())