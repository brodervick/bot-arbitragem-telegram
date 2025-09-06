import os
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import yfinance as yf
import pandas as pd
import pandas_ta as ta

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("entrybot")

TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))  # 0 = sem restrição

STATE_FILE = "state.json"

# ---------------------- util: persistência --------------------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

state = load_state()
# estrutura:
# state[user_id] = {
#   "pair": "EURUSD",
#   "tf": "5m",
#   "fast": 10, "slow": 30, "rsi": 14, "rsi_buy_max": 70, "rsi_sell_min": 30,
#   "tp_pips": 10, "sl_pips": 25,
#   "enabled": False,
#   "last_signal_id": ""  # anti-duplicação
# }

# ---------------------- mapeamento de símbolos ----------------------
# yfinance formatos: Forex "EURUSD=X", ouro "XAUUSD=X", prata "XAGUSD=X"
def yfsym(pair: str) -> str:
    pair = pair.upper().replace("/", "")
    if pair in ("XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"):
        return f"{pair}=X"
    if pair.endswith("USD") or pair.startswith("USD") or len(pair) == 6:
        return f"{pair}=X"
    # fallback
    return f"{pair}=X"

# ---------------------- dados & sinais ------------------------------
def fetch_ohlc(symbol: str, tf: str, lookback="2d") -> pd.DataFrame:
    # tf exemplos: "1m","5m","15m","1h"
    df = yf.download(tickers=symbol, interval=tf, period=lookback, progress=False)
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    df = df.rename(columns=str.title)
    df.dropna(inplace=True)
    return df

def compute_signals(df: pd.DataFrame, fast=10, slow=30, rsi_len=14,
                    rsi_buy_max=70, rsi_sell_min=30):
    if df.empty:
        return None
    # indicadores
    df["EMA_fast"] = ta.ema(df["Close"], length=fast)
    df["EMA_slow"] = ta.ema(df["Close"], length=slow)
    df["RSI"] = ta.rsi(df["Close"], length=rsi_len)

    df.dropna(inplace=True)
    if len(df) < 2:
        return None

    c0 = df.iloc[-1]
    c1 = df.iloc[-2]

    cross_up   = (c1.EMA_fast < c1.EMA_slow) and (c0.EMA_fast > c0.EMA_slow)
    cross_down = (c1.EMA_fast > c1.EMA_slow) and (c0.EMA_fast < c0.EMA_slow)

    long_sig  = cross_up   and (c0.RSI <= rsi_buy_max)
    short_sig = cross_down and (c0.RSI >= rsi_sell_min)

    return {
        "time": df.index[-1].to_pydatetime(),
        "price": float(c0.Close),
        "rsi": float(c0.RSI),
        "fast": float(c0.EMA_fast),
        "slow": float(c0.EMA_slow),
        "long": long_sig,
        "short": short_sig
    }

def pip_size(pair: str) -> float:
    pair = pair.upper()
    # pares de 5 casas costumam usar 0.00010 por pip; XAUUSD geralmente 0.10
    if pair.startswith("XAU") or pair.startswith("XAG"):
        return 0.10
    return 0.00010

def build_message(pair, tf, sig, tp_pips, sl_pips):
    direction = "✅ LONG (COMPRA)" if sig["long"] else "✅ SHORT (VENDA)"
    ps = pip_size(pair)
    price = sig["price"]
    if sig["long"]:
        tp = price + tp_pips * ps
        sl = price - sl_pips * ps
    else:
        tp = price - tp_pips * ps
        sl = price + sl_pips * ps

    return (
        f"📈 *Entrada encontrada* — *{pair}* `{tf}`\n"
        f"{direction}\n"
        f"Preço: `{price:.5f}`\n"
        f"SL:  `{sl:.5f}`  |  TP: `{tp:.5f}`\n"
        f"RSI: `{sig['rsi']:.1f}`  |  EMA(fast/slow): `{sig['fast']:.5f}` / `{sig['slow']:.5f}`\n"
        f"Hora: `{sig['time'].strftime('%Y-%m-%d %H:%M:%S')}`"
    )

# ---------------------- bot helpers --------------------------------
async def auth_ok(update: Update) -> bool:
    if ALLOWED_USER_ID and update.effective_user and update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Acesso não autorizado.")
        return False
    return True

def ensure_user(user_id: int):
    if str(user_id) not in state:
        state[str(user_id)] = {
            "pair": "EURUSD",
            "tf": "5m",
            "fast": 10, "slow": 30, "rsi": 14, "rsi_buy_max": 70, "rsi_sell_min": 30,
            "tp_pips": 8, "sl_pips": 25,
            "enabled": False,
            "last_signal_id": ""
        }
        save_state()
    return state[str(user_id)]

# ---------------------- comandos -----------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await auth_ok(update): return
    cfg = ensure_user(update.effective_user.id)
    await update.message.reply_text(
        "🤖 Bot de entradas (EMA cruzada + RSI)\n\n"
        "Comandos:\n"
        "/on — liga os sinais\n"
        "/off — desliga os sinais\n"
        "/setpair EURUSD — define o par\n"
        "/settf 5m — timeframe (1m,5m,15m,1h)\n"
        "/setparams 10 30 14 70 30 — ema_fast ema_slow rsi_len rsi_buyMax rsi_sellMin\n"
        "/risk 8 25 — TP/SL em pips (ex.: 8 e 25)\n"
        "/status — ver configurações atuais\n"
    )

async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await auth_ok(update): return
    cfg = ensure_user(update.effective_user.id)
    cfg["enabled"] = True
    save_state()
    await update.message.reply_text("✅ Sinais *ligados*.")

async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await auth_ok(update): return
    cfg = ensure_user(update.effective_user.id)
    cfg["enabled"] = False
    save_state()
    await update.message.reply_text("⏹️ Sinais *desligados*.")

async def cmd_setpair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await auth_ok(update): return
    if not context.args:
        await update.message.reply_text("Uso: /setpair EURUSD")
        return
    pair = context.args[0].upper().replace("/", "")
    cfg = ensure_user(update.effective_user.id)
    cfg["pair"] = pair
    save_state()
    await update.message.reply_text(f"Par definido: *{pair}*")

async def cmd_settf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await auth_ok(update): return
    if not context.args:
        await update.message.reply_text("Uso: /settf 5m  (1m,5m,15m,1h)")
        return
    tf = context.args[0]
    if tf not in ("1m","5m","15m","30m","1h"):
        await update.message.reply_text("Timeframe inválido. Use: 1m, 5m, 15m, 30m, 1h")
        return
    cfg = ensure_user(update.effective_user.id)
    cfg["tf"] = tf
    save_state()
    await update.message.reply_text(f"Timeframe definido: *{tf}*")

async def cmd_setparams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await auth_ok(update): return
    try:
        fast = int(context.args[0]); slow = int(context.args[1]); rsi = int(context.args[2])
        rb = int(context.args[3]); rs = int(context.args[4])
    except Exception:
        await update.message.reply_text("Uso: /setparams 10 30 14 70 30")
        return
    cfg = ensure_user(update.effective_user.id)
    cfg.update({"fast":fast,"slow":slow,"rsi":rsi,"rsi_buy_max":rb,"rsi_sell_min":rs})
    save_state()
    await update.message.reply_text(f"Parâmetros atualizados ✅")

async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await auth_ok(update): return
    try:
        tp = int(context.args[0]); sl = int(context.args[1])
    except Exception:
        await update.message.reply_text("Uso: /risk 8 25   (TP_pips SL_pips)")
        return
    cfg = ensure_user(update.effective_user.id)
    cfg["tp_pips"] = tp; cfg["sl_pips"] = sl; save_state()
    await update.message.reply_text(f"TP/SL ajustados: TP={tp} pips, SL={sl} pips")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await auth_ok(update): return
    cfg = ensure_user(update.effective_user.id)
    await update.message.reply_text(
        "⚙️ *Status*\n"
        f"Ativo: {'ON' if cfg['enabled'] else 'OFF'}\n"
        f"Par: {cfg['pair']}  | TF: {cfg['tf']}\n"
        f"EMA fast/slow: {cfg['fast']}/{cfg['slow']}  | RSI: {cfg['rsi']} (buy≤{cfg['rsi_buy_max']} sell≥{cfg['rsi_sell_min']})\n"
        f"TP/SL: {cfg['tp_pips']}/{cfg['sl_pips']} pips"
    )

# ---------------------- agendador ----------------------------------
async def scan_user(app_context, chat_id: int, cfg: dict):
    if not cfg.get("enabled"):
        return
    pair = cfg["pair"]; tf = cfg["tf"]
    sym = yfsym(pair)
    df = fetch_ohlc(sym, tf, lookback="5d")
    sig = compute_signals(
        df, fast=cfg["fast"], slow=cfg["slow"], rsi_len=cfg["rsi"],
        rsi_buy_max=cfg["rsi_buy_max"], rsi_sell_min=cfg["rsi_sell_min"]
    )
    if not sig:
        return

    # deduplicar por timestamp + direção
    sig_id = f"{pair}-{tf}-{sig['time'].isoformat()}-{'L' if sig['long'] else 'S' if sig['short'] else 'N'}"
    if sig["long"] or sig["short"]:
        if sig_id != cfg.get("last_signal_id",""):
            msg = build_message(pair, tf, sig, cfg["tp_pips"], cfg["sl_pips"])
            await app_context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            cfg["last_signal_id"] = sig_id
            save_state()

async def scheduler_loop(app):
    sched = AsyncIOScheduler(timezone="UTC")
    # roda a cada 60s para todos os usuários com bot ligado
    async def job():
        for uid, cfg in state.items():
            chat_id = int(uid)
            try:
                await scan_user(app, chat_id, cfg)
            except Exception as e:
                log.exception(f"scan_user error for {uid}: {e}")

    sched.add_job(lambda: asyncio.create_task(job()), "interval", seconds=60, next_run_time=datetime.now(timezone.utc) + timedelta(seconds=5))
    sched.start()

# ---------------------- main --------------------------------------
async def main():
    if not TOKEN:
        raise RuntimeError("Defina TELEGRAM_TOKEN no .env")
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("on", cmd_on))
    app.add_handler(CommandHandler("off", cmd_off))
    app.add_handler(CommandHandler("setpair", cmd_setpair))
    app.add_handler(CommandHandler("settf", cmd_settf))
    app.add_handler(CommandHandler("setparams", cmd_setparams))
    app.add_handler(CommandHandler("risk", cmd_risk))
    app.add_handler(CommandHandler("status", cmd_status))

    # inicia agendador
    asyncio.create_task(scheduler_loop(app))
    await app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    asyncio.run(main())
