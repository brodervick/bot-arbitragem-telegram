import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

import aiohttp
import numpy as np
import pandas as pd
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)

# ───────────────────────────── Log ─────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("gateio-signals")

# ─────────────────────────── Config Padrão ───────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
INTERVAL = "15m"                         
FETCH_LIMIT = 300                        
LOOP_SLEEP_SEC = 60                      

# Lista com 50 pares populares da Gate.io
DEFAULT_PAIRS = [
    "BTC_USDT","ETH_USDT","BNB_USDT","XRP_USDT","ADA_USDT",
    "DOGE_USDT","SOL_USDT","DOT_USDT","MATIC_USDT","LTC_USDT",
    "TRX_USDT","AVAX_USDT","SHIB_USDT","UNI_USDT","LINK_USDT",
    "ATOM_USDT","XLM_USDT","ETC_USDT","NEAR_USDT","APT_USDT",
    "FIL_USDT","VET_USDT","ICP_USDT","SAND_USDT","AXS_USDT",
    "AAVE_USDT","MANA_USDT","EOS_USDT","THETA_USDT","XTZ_USDT",
    "GRT_USDT","RUNE_USDT","KAVA_USDT","FLOW_USDT","ZEC_USDT",
    "XMR_USDT","QNT_USDT","CRV_USDT","COMP_USDT","1INCH_USDT",
    "ALGO_USDT","CHZ_USDT","ENJ_USDT","CAKE_USDT","FTM_USDT",
    "DASH_USDT","WAVES_USDT","ZIL_USDT","LRC_USDT","BAT_USDT"
]

STATE = {
    "running": False,
    "pairs": set(os.getenv("PAIRS", ",".join(DEFAULT_PAIRS)).split(",")),
    "dev": float(os.getenv("DEV", "0.002")),       # 0.2%
    "use_rsi": True,
    "rsi_low": 30,
    "rsi_high": 70,
    "use_ema": True,
    "ema_len": 50,
}

STATS = {"wins": 0, "losses": 0, "last_signals": {}}

# ───────────────────────── Utilidades ─────────────────────────
def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean()
    rs = up / (down.replace(0, np.nan))
    return 100 - (100 / (1 + rs))

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def pct(a, b) -> float:
    return (a - b) / b if b else 0.0

def fmt_price(x: float) -> str:
    if x >= 100:
        return f"{x:.3f}"
    if x >= 1:
        return f"{x:.6f}"
    return f"{x:.8f}"

# ───────────────────── API Gate.io ─────────────────────
GATE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"

async def fetch_candles(session: aiohttp.ClientSession, pair: str, interval: str) -> Optional[pd.DataFrame]:
    params = {"currency_pair": pair, "interval": interval, "limit": str(FETCH_LIMIT)}
    try:
        async with session.get(GATE_URL, params=params, timeout=20) as r:
            if r.status != 200:
                return None
            data = await r.json()
            rows = []
            for item in data:
                ts = int(item[0])
                rows.append({
                    "ts": datetime.fromtimestamp(ts, tz=timezone.utc),
                    "volume": float(item[1]),
                    "close": float(item[2]),
                    "high": float(item[3]),
                    "low": float(item[4]),
                    "open": float(item[5]),
                })
            df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
            return df
    except:
        return None

# ──────────────── Estratégia ────────────────
def evaluate_signal(df: pd.DataFrame, dev: float, use_rsi: bool, rsi_low: int, rsi_high: int,
                    use_ema: bool, ema_len: int) -> Tuple[Optional[str], Dict]:
    if df is None or len(df) < 60:
        return None, {"reason": "poucos candles"}

    close = df["close"]
    last = close.iloc[-1]
    sma20 = close.rolling(20).mean()
    emaN = ema(close, ema_len)
    rsi14 = rsi(close, 14)
    atr14 = atr(df, 14)

    sma_v = sma20.iloc[-1]
    ema_v = emaN.iloc[-1]
    rsi_v = rsi14.iloc[-1]
    atr_v = atr14.iloc[-1]

    deviation = pct(last, sma_v)
    rsi_ok_long = (rsi_v <= rsi_low)
    rsi_ok_short = (rsi_v >= rsi_high)
    trend_ok_long = (last > ema_v)
    trend_ok_short = (last < ema_v)

    info = {"last": last, "sma20": sma_v, "ema": ema_v, "rsi": rsi_v, "atr": atr_v, "deviation": deviation}

    signal = None
    if abs(deviation) >= dev:
        if deviation <= -dev:
            if (not use_rsi or rsi_ok_long) and (not use_ema or trend_ok_long):
                signal = "LONG"
            else:
                info["reason"] = "filtros bloquearam LONG"
        elif deviation >= dev:
            if (not use_rsi or rsi_ok_short) and (not use_ema or trend_ok_short):
                signal = "SHORT"
            else:
                info["reason"] = "filtros bloquearam SHORT"
    else:
        info["reason"] = "desvio insuficiente"

    if np.isfinite(atr_v) and atr_v > 0:
        if signal == "LONG":
            entry = last; tp1 = entry + 0.5 * atr_v; tp2 = entry + 1.0 * atr_v; stop = entry - 1.0 * atr_v
        elif signal == "SHORT":
            entry = last; tp1 = entry - 0.5 * atr_v; tp2 = entry - 1.0 * atr_v; stop = entry + 1.0 * atr_v
        else:
            entry = tp1 = tp2 = stop = None
    else:
        entry = tp1 = tp2 = stop = None

    info.update({"entry": entry, "tp1": tp1, "tp2": tp2, "stop": stop})
    return signal, info

# ────────────────────── Mensagens ──────────────────────
def format_signal_msg(pair: str, tf: str, side: str, info: Dict) -> str:
    return (
        f"📈 *Entrada encontrada*\n"
        f"✅ *{side} {pair} {tf}*\n"
        f"Preço: {fmt_price(info['entry'])}\n"
        f"Stop:  {fmt_price(info['stop'])}\n"
        f"TP1:   {fmt_price(info['tp1'])} | TP2: {fmt_price(info['tp2'])}"
    )

def format_debug_msg(pair: str, info: Optional[Dict]) -> str:
    if not info:
        return "Sem dados."
    rows = [
        f"Par: {pair}",
        f"Preço: {fmt_price(info.get('last', float('nan')))}",
        f"SMA20: {fmt_price(info.get('sma20', float('nan')))}",
        f"EMA{STATE['ema_len']}: {fmt_price(info.get('ema', float('nan')))}",
        f"RSI14: {info.get('rsi', float('nan')):.2f}",
        f"ATR14: {fmt_price(info.get('atr', float('nan')))}",
        f"Desvio vs SMA: {info.get('deviation', 0)*100:.3f}%",
    ]
    if "reason" in info:
        rows.append(f"Motivo sem sinal: {info['reason']}")
    return "\n".join(rows)

# ─────────────────────────── Loop ───────────────────────────
async def signals_loop(app):
    async with aiohttp.ClientSession() as session:
        last_bar_time: Dict[str, datetime] = {}
        while STATE["running"]:
            try:
                for pair in list(STATE["pairs"]):
                    df = await fetch_candles(session, pair, INTERVAL)
                    if df is None or df.empty:
                        continue
                    ts_last_closed = df["ts"].iloc[-1]
                    if last_bar_time.get(pair) == ts_last_closed:
                        continue
                    signal, info = evaluate_signal(
                        df,
                        STATE["dev"],
                        STATE["use_rsi"], STATE["rsi_low"], STATE["rsi_high"],
                        STATE["use_ema"], STATE["ema_len"]
                    )
                    STATS["last_signals"][pair] = info
                    if signal:
                        text = format_signal_msg(pair, INTERVAL, signal, info)
                        for chat_id in app.chat_ids:
                            try:
                                await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
                            except:
                                pass
                    last_bar_time[pair] = ts_last_closed
            except Exception as e:
                log.exception("Erro no loop: %s", e)
            await asyncio.sleep(LOOP_SLEEP_SEC)

# ──────────────────────────── Telegram Handlers ────────────────────────────
class AppWithChats:
    def __init__(self, app):
        self._app = app
        self.chat_ids: set[int] = set()
    @property
    def bot(self): return self._app.bot

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.application.app_with_chats.chat_ids.add(update.effective_chat.id)  # type: ignore
    await update.message.reply_text(
        "🤖 Bot de sinais 15m (Gate.io)\n"
        "/startsignals • /stopsignals\n"
        "/add PAR • /remove PAR • /watchlist\n"
        "/setdev 0.002 • /togglersi on/off • /setrsi 30 70\n"
        "/toggleema on/off • /setema 50 • /debug BTC_USDT"
    )

async def startsignals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if STATE["running"]:
        await update.message.reply_text("Já estou rodando os sinais.")
        return
    STATE["running"] = True
    context.application.app_with_chats.chat_ids.add(update.effective_chat.id)  # type: ignore
    asyncio.create_task(signals_loop(context.application.app_with_chats))       # type: ignore
    await update.message.reply_text("🟢 Sinais iniciados.\nWatchlist: " + ", ".join(sorted(STATE["pairs"])))

async def stopsignals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE["running"] = False
    await update.message.reply_text("🔴 Sinais parados.")

async def add_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("Use: /add BTC_USDT")
    pair = context.args[0].upper()
    STATE["pairs"].add(pair)
    await update.message.reply_text(f"Par adicionado: {pair}")

async def remove_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("Use: /remove BTC_USDT")
    pair = context.args[0].upper()
    if pair in STATE["pairs"]:
        STATE["pairs"].remove(pair); await update.message.reply_text(f"Par removido: {pair}")
    else:
        await update.message.reply_text("Par não estava na lista.")

async def watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👀 Watchlist: " + ", ".join(sorted(STATE["pairs"])))

async def setdev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(f"Desvio atual: {STATE['dev']:.4f}")
    try:
        val = float(context.args[0])
        STATE["dev"] = val
        await update.message.reply_text(f"Novo desvio: {val:.4f}")
    except: await update.message.reply_text("Ex.: /setdev 0.002")

async def togglersi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE["use_rsi"] = not STATE["use_rsi"]
    await update.message.reply_text(f"Filtro RSI: {'on' if STATE['use_rsi'] else 'off'}")

async def setrsi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args)!=2:
        return await update.message.reply_text(f"Atual: {STATE['rsi_low']}-{STATE['rsi_high']}")
    low, high = int(context.args[0]), int(context.args[1])
    STATE["rsi_low"], STATE["rsi_high"] = low, high
    await update.message.reply_text(f"RSI bounds: {low}-{high}")

async def toggleema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE["use_ema"] = not STATE["use_ema"]
    await update.message.reply_text(f"Filtro EMA: {'on' if STATE['use_ema'] else 'off'}")

async def setema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text(f"EMA atual: {STATE['ema_len']}")
    n = int(context.args[0]); STATE["ema_len"] = n
    await update.message.reply_text(f"Nova EMA: {n}")

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("Ex.: /debug BTC_USDT")
    pair = context.args[0].upper()
    info = STATS["last_signals"].get(pair)
    await update.message.reply_text(format_debug_msg(pair, info))

# ──────────────────────────── Main ────────────────────────────
def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit("Defina TELEGRAM_TOKEN no ambiente.")
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.app_with_chats = AppWithChats(application)  # type: ignore

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("startsignals", startsignals))
    application.add_handler(CommandHandler("stopsignals", stopsignals))
    application.add_handler(CommandHandler("add", add_pair))
    application.add_handler(CommandHandler("remove", remove_pair))
    application.add_handler(CommandHandler("watchlist", watchlist))
    application.add_handler(CommandHandler("setdev", setdev))
    application.add_handler(CommandHandler("togglersi", togglersi))
    application.add_handler(CommandHandler("setrsi", setrsi))
    application.add_handler(CommandHandler("toggleema", toggleema))
    application.add_handler(CommandHandler("setema", setema))
    application.add_handler(CommandHandler("debug", debug))

    log.info("Bot up. Pairs: %s", ",".join(sorted(STATE["pairs"])))
    application.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
