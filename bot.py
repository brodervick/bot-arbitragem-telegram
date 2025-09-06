# bot_watch_signals.py
# Bot de sinais estilo "startsignals" para Gate.io (15m)
# - /startsignals liga o monitoramento periódico e mostra a watchlist
# - /stopsignals desliga
# - /add <PAR>  /remove <PAR>  /watchlist
# Estratégia didática: SMA20 + ATR14; desvio vs média define LONG/SHORT.

import os, math, time, requests, pandas as pd
from dataclasses import dataclass
from typing import Dict, Optional
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
INTERVAL = os.getenv("INTERVAL", "15m")          # timeframe Gate.io
DEV = float(os.getenv("DEV", "0.004"))           # 0.4% (0.004) de limiar
POLLING_SECONDS = int(os.getenv("POLLING", "60"))# frequência do monitor em s

DEFAULT_PAIRS = [p.strip() for p in os.getenv(
    "PAIRS",
    "BTC_USDT,ETH_USDT,SOL_USDT,BNB_USDT,XRP_USDT,ADA_USDT,DOGE_USDT,TRX_USDT,AVAX_USDT,"
    "MATIC_USDT,DOT_USDT,LTC_USDT,SHIB_USDT,UNI_USDT,LINK_USDT,XLM_USDT,ATOM_USDT,ETC_USDT,APT_USDT,NEAR_USDT"
).split(",") if p.strip()]

CANDLE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"

@dataclass
class Position:
    direction: str
    price: float
    stop: float
    tp1: float
    tp2: float
    opened_ts: float

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

def try_build_signal(pair: str, deviation_thr: float = DEV) -> Optional[Position]:
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

async def startsignals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cd = context.chat_data
    cd.setdefault("watchlist", set(DEFAULT_PAIRS[:10]))  # começa com 10 do print
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
    pair = context.args[0].upper()
    context.chat_data.setdefault("watchlist", set(DEFAULT_PAIRS[:10])).add(pair)
    await update.message.reply_text(f"✅ Adicionado: {pair.replace('_','/')} 15m")

async def remove_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use: /remove BTC_USDT")
        return
    pair = context.args[0].upper()
    wl = context.chat_data.get("watchlist", set())
    if pair in wl:
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

async def check_loop(context: ContextTypes.DEFAULT_TYPE):
    """Job que verifica sinais e STOPs para cada chat ativo."""
    for chat_id, cd in list(context.application.chat_data.items()):
        if not cd.get("active"):
            continue
        wl = cd.get("watchlist", set())
        positions: Dict[str, Position] = cd.setdefault("positions", {})

        # tentar gerar novos sinais
        for pair in list(wl):
            if pair not in positions:
                pos = try_build_signal(pair, DEV)
                if pos:
                    positions[pair] = pos
                    await context.bot.send_message(
                        chat_id,
                        text=format_signal(pos.direction, pair, pos.price, pos.stop, pos.tp1, pos.tp2)
                    )

        # checar STOP dos sinais ativos
        for pair, pos in list(positions.items()):
            df = fetch_klines(pair, limit=5)
            if df is None or df.empty:
                continue
            last = float(df["close"].iloc[-1])
            stopped = (last <= pos.stop) if pos.direction == "LONG" else (last >= pos.stop)
            if stopped:
                await context.bot.send_message(chat_id, text=f"🛑 STOP — {pair.replace('_','/')} 15m @ {pos.stop:.6f}")
                positions.pop(pair, None)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot de sinais Gate.io (15m)\n"
        "/startsignals — iniciar\n/stopsignals — parar\n"
        "/add PAR (ex: /add ETH_USDT)\n/remove PAR\n/watchlist"
    )

def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit("Defina TELEGRAM_TOKEN no ambiente.")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("startsignals", startsignals))
    app.add_handler(CommandHandler("stopsignals", stopsignals))
    app.add_handler(CommandHandler("add", add_pair))
    app.add_handler(CommandHandler("remove", remove_pair))
    app.add_handler(CommandHandler("watchlist", watchlist_cmd))

    # >>>>>>> JobQueue (requer python-telegram-bot[job-queue]) <<<<<<<
    app.job_queue.run_repeating(check_loop, interval=POLLING_SECONDS, first=2)

    print("✅ Bot watch-signals iniciado. Envie /startsignals no Telegram.")
    app.run_polling()

if __name__ == "__main__":
    main()
