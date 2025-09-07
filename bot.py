# bot_watch_signals.py
# Bot de sinais estilo "startsignals" para Gate.io (15m)
# /startsignals liga monitoramento, /stopsignals desliga
# /add, /remove, /watchlist, /setdev, /debug
# Estratégia didática: SMA20 + ATR14; desvio vs média define LONG/SHORT.

import os, math, time, requests, pandas as pd
from dataclasses import dataclass
from typing import Dict, Optional
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ===================== CONFIG =====================
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
INTERVAL         = os.getenv("INTERVAL", "15m")      # timeframe Gate.io
DEFAULT_DEV      = float(os.getenv("DEV", "0.004"))  # limiar padrão 0,4% (=0.004)
POLLING_SECONDS  = int(os.getenv("POLLING", "60"))   # frequência do monitor (s)

DEFAULT_PAIRS = [p.strip() for p in os.getenv(
    "PAIRS",
    "BTC_USDT,ETH_USDT,SOL_USDT,BNB_USDT,XRP_USDT,ADA_USDT,DOGE_USDT,TRX_USDT,AVAX_USDT,"
    "MATIC_USDT,DOT_USDT,LTC_USDT,SHIB_USDT,UNI_USDT,LINK_USDT,XLM_USDT,ATOM_USDT,ETC_USDT,APT_USDT,NEAR_USDT"
).split(",") if p.strip()]

CANDLE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
# ==================================================

@dataclass
class Position:
    direction: str
    price: float
    stop: float
    tp1: float
    tp2: float
    opened_ts: float

# -------- utils --------
def norm_pair(s: str) -> Optional[str]:
    """Normaliza entradas comuns: ADA, ADA/USDT, ada_usdt -> ADA_USDT"""
    if not s:
        return None
    s = s.upper().replace(" ", "").replace("-", "_")
    s = s.replace("/", "_").replace("__", "_")
    if "_USDT" not in s:
        # se o usuário mandar só 'ADA', assume _USDT
        if len(s) <= 6:
            s = f"{s}_USDT"
    parts = s.split("_")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return s

def fetch_klines(pair: str, interval: str = INTERVAL, limit: int = 120) -> Optional[pd.DataFrame]:
    try:
        r = requests.get(CANDLE_URL, params={
            "currency_pair": pair, "interval": interval, "limit": str(limit)
        }, timeout=20)
        r.raise_for_status()
        data = r.json()
        cols = ["ts","volume","close","high","low","open"]
        df = pd.DataFrame(data, columns=cols)
        for c in ["close","high","low","open","volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.sort_values("ts").reset_index(drop=True)
    except Exception:
        return None

def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()

def simple_atr(df: pd.DataFrame, n: int = 14) -> float:
    h,l,c = df["high"].values, df["low"].values, df["close"].values
    trs=[]
    for i in range(1,len(df)):
        trs.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    if not trs:
        return float("nan")
    return float(pd.Series(trs).rolling(n).mean().iloc[-1])

def format_signal(direction: str, pair: str, last: float, stop: float, tp1: float, tp2: float) -> str:
    head = "📉 Entrada encontrada" if direction == "SHORT" else "📈 Entrada encontrada"
    return (
        f"{head}\n"
        f"✅ {direction} {pair.replace('_','/')} 15m\n"
        f"Preço: {last:.6f}\n"
        f"Stop:  {stop:.6f}\n"
        f"TP1:  {tp1:.6f} | TP2: {tp2:.6f}"
    )

def try_build_signal(pair: str, deviation_thr: float) -> Optional[Position]:
    df = fetch_klines(pair, limit=120)
    if df is None or len(df) < 30:
        return None
    last = float(df["close"].iloc[-1])
    ma20 = float(sma(df["close"], 20).iloc[-1])
    atr14 = simple_atr(df, 14)
    if any(map(math.isnan, [ma20, atr14])) or atr14 <= 0:
        return None
    dev = (last - ma20) / ma20
    if dev >= deviation_thr:   # SHORT
        return Position("SHORT", last, last + 0.5*atr14, last - 0.5*atr14, last - 1.0*atr14, time.time())
    if dev <= -deviation_thr:  # LONG
        return Position("LONG",  last, last - 0.5*atr14, last + 0.5*atr14, last + 1.0*atr14, time.time())
    return None
# -----------------------

# -------- commands --------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot de sinais Gate.io (15m)\n"
        "/startsignals — iniciar\n/stopsignals — parar\n"
        "/add PAR (ex: /add ETH_USDT)\n/remove PAR\n/watchlist\n"
        "/setdev 0.002 (ajusta limiar por chat para 0,2%)\n"
        "/debug BTC_USDT (diagnóstico)"
    )

async def startsignals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cd = context.chat_data
    cd.setdefault("watchlist", set(DEFAULT_PAIRS[:10]))  # 10 pares iniciais
    cd["active"] = True
    cd.setdefault("positions", {})
    wl = ", ".join([p.replace("_","/") + " 15m" for p in sorted(cd["watchlist"])])
    await update.message.reply_text("🟢 Sinais iniciados.\nWatchlist carregada: " + wl)

async def stopsignals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["active"] = False
    await update.message.reply_text("🔴 Sinais parados. Use /startsignals para ligar novamente.")

async def add_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use: /add BTC_USDT")
        return
    raw = "".join(context.args)   # junta se o usuário digitou com espaço
    pair = norm_pair(raw)
    if not pair:
        await update.message.reply_text("Par inválido. Ex.: /add ETH_USDT")
        return
    context.chat_data.setdefault("watchlist", set()).add(pair)
    await update.message.reply_text(f"✅ Adicionado: {pair.replace('_','/')} 15m")

async def remove_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use: /remove BTC_USDT")
        return
    pair = norm_pair("".join(context.args))
    wl = context.chat_data.get("watchlist", set())
    if pair and pair in wl:
        wl.remove(pair)
        await update.message.reply_text(f"🗑️ Removido: {pair.replace('_','/')} 15m")
    else:
        await update.message.reply_text("Par não está na watchlist.")

async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wl = context.chat_data.get("watchlist", set())
    if not wl:
        await update.message.reply_text("Watchlist vazia. Use /add PAR.")
    else:
        await update.message.reply_text("👀 Watchlist: " + ", ".join(sorted(wl)))

async def setdev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use: /setdev 0.002 (0,2%) ou /setdev 0.2%")
        return
    x = context.args[0].replace("%", "")
    try:
        val = float(x)
        if val >= 1:   # se mandou 0.2 vira 0.002
            val = val / 100.0
        context.chat_data["dev"] = val
        await update.message.reply_text(f"🔧 Limiar ajustado para {val*100:.2f}%")
    except Exception:
        await update.message.reply_text("Valor inválido. Ex.: /setdev 0.0015 (0,15%)")

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pair = norm_pair("".join(context.args)) if context.args else None
    if not pair:
        await update.message.reply_text("Use: /debug BTC_USDT")
        return
    df = fetch_klines(pair, limit=120)
    if df is None or len(df) < 30:
        await update.message.reply_text(f"Sem dados para {pair}")
        return
    last = float(df["close"].iloc[-1])
    ma20 = float(sma(df["close"], 20).iloc[-1])
    atr14 = simple_atr(df, 14)
    dev = (last - ma20)/ma20 if ma20 else float("nan")
    thr = context.chat_data.get("dev", DEFAULT_DEV)
    cond = "SHORT" if dev >= thr else ("LONG" if dev <= -thr else "NEUTRO")
    await update.message.reply_text(
        f"🔧 DEBUG {pair.replace('_','/')} 15m\n"
        f"Preço: {last:.6f}\nSMA20: {ma20:.6f}\nATR14: {atr14:.6f}\n"
        f"Desvio: {dev*100:.3f}% • Limiar: {thr*100:.2f}%\n"
        f"Condição: {cond}"
    )
# -------------------------

# ------- job periódico -------
async def check_loop(context: ContextTypes.DEFAULT_TYPE):
    """Verifica sinais e STOPs para cada chat ativo."""
    for chat_id, cd in list(context.application.chat_data.items()):
        if not cd.get("active"):
            continue
        wl = cd.get("watchlist", set())
        positions: Dict[str, Position] = cd.setdefault("positions", {})
        thr = cd.get("dev", DEFAULT_DEV)

        # 1) tentar gerar novos sinais
        for pair in list(wl):
            if pair not in positions:
                pos = try_build_signal(pair, thr)
                if pos:
                    positions[pair] = pos
                    await context.bot.send_message(
                        chat_id,
                        text=format_signal(pos.direction, pair, pos.price, pos.stop, pos.tp1, pos.tp2)
                    )

        # 2) checar STOP nos sinais ativos
        for pair, pos in list(positions.items()):
            df = fetch_klines(pair, limit=5)
            if df is None or df.empty:
                continue
            last = float(df["close"].iloc[-1])
            stopped = (last <= pos.stop) if pos.direction == "LONG" else (last >= pos.stop)
            if stopped:
                await context.bot.send_message(
                    chat_id,
                    text=f"🛑 STOP — {pair.replace('_','/')} 15m @ {pos.stop:.6f}"
                )
                positions.pop(pair, None)
# ------------------------------

def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit("Defina TELEGRAM_TOKEN no ambiente.")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("startsignals", startsignals))
    app.add_handler(CommandHandler("stopsignals", stopsignals))
    app.add_handler(CommandHandler("add", add_pair))
    app.add_handler(CommandHandler("remove", remove_pair))
    app.add_handler(CommandHandler("watchlist", watchlist_cmd))
    app.add_handler(CommandHandler("setdev", setdev))
    app.add_handler(CommandHandler("debug", debug))

    # JobQueue (requer python-telegram-bot[job-queue])
    app.job_queue.run_repeating(check_loop, interval=POLLING_SECONDS, first=2)

    print("✅ Bot watch-signals iniciado. Envie /startsignals no Telegram.")
    app.run_polling()

if __name__ == "__main__":
    main()
