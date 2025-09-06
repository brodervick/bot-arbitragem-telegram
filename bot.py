# bot_signals.py
# Bot de sinais para vários pares da Gate.io (15m)
# Usa SMA20 + ATR14 para gerar entradas LONG/SHORT

import os
import math
import requests
import pandas as pd
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Token do Telegram (defina como variável de ambiente TELEGRAM_TOKEN no Railway)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Pares padrão (você pode alterar via variável PAIRS no Railway)
DEFAULT_PAIRS = [p.strip() for p in os.getenv(
    "PAIRS",
    "BTC_USDT,ETH_USDT,SOL_USDT,ADA_USDT,DOGE_USDT,LINK_USDT,BNB_USDT,XRP_USDT,MATIC_USDT,DOT_USDT"
).split(",") if p.strip()]

# Gate.io API
CANDLE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"


def fetch_klines(pair: str, interval: str = "900", limit: int = 120):
    """Busca candles 15m (interval=900 segundos) e retorna DataFrame"""
    try:
        r = requests.get(CANDLE_URL, params={
            "currency_pair": pair, "interval": interval, "limit": str(limit)
        }, timeout=20)
        r.raise_for_status()
        data = r.json()
        cols = ["ts", "volume", "close", "high", "low", "open"]
        df = pd.DataFrame(data, columns=cols)
        for c in ["close", "high", "low", "open", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.sort_values("ts").reset_index(drop=True)
        return df
    except Exception:
        return None


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def simple_atr(df: pd.DataFrame, n: int = 14) -> float:
    high, low, close = df["high"].values, df["low"].values, df["close"].values
    trs = []
    for i in range(1, len(df)):
        tr = max(high[i] - low[i],
                 abs(high[i] - close[i-1]),
                 abs(low[i] - close[i-1]))
        trs.append(tr)
    if not trs:
        return float("nan")
    return float(pd.Series(trs).rolling(n).mean().iloc[-1])


def build_signal_for_pair(pair: str, deviation_thr: float = 0.01):
    """Gera sinal LONG/SHORT estilo do print"""
    df = fetch_klines(pair, interval="900", limit=120)
    if df is None or len(df) < 30:
        return None

    last = float(df["close"].iloc[-1])
    ma = float(sma(df["close"], 20).iloc[-1])
    atr = simple_atr(df, 14)
    if math.isnan(atr) or atr <= 0:
        return None

    dev = (last - ma) / ma
    if dev >= deviation_thr:  # preço acima da média -> SHORT
        direction = "SHORT"
        stop = last + atr * 0.5
        tp1 = last - atr * 0.5
        tp2 = last - atr * 1.0
    elif dev <= -deviation_thr:  # preço abaixo -> LONG
        direction = "LONG"
        stop = last - atr * 0.5
        tp1 = last + atr * 0.5
        tp2 = last + atr * 1.0
    else:
        return None

    msg = (
        "📉 Entrada encontrada" if direction == "SHORT" else "📈 Entrada encontrada"
    )
    msg += f"\n✅ {direction} {pair} 15m"
    msg += f"\nPreço: {last:.6f}"
    msg += f"\nStop:  {stop:.6f}"
    msg += f"\nTP1:  {tp1:.6f} | TP2: {tp2:.6f}"
    return msg


def build_signals_text(pairs):
    blocks = []
    for p in pairs:
        sig = build_signal_for_pair(p)
        if sig:
            blocks.append(sig)
    if not blocks:
        return "⏳ Sem sinais agora.\nTente novamente em alguns minutos."
    return "\n\n".join(blocks)


# ---- Telegram Commands ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot de SINAIS Gate.io ativo!\nUse /sinais para gerar entradas.")


async def sinais(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pairs = [p.upper() for p in (context.args if context.args else DEFAULT_PAIRS)]
    text = build_signals_text(pairs)
    await update.message.reply_text(text)


def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit("❌ Defina a variável TELEGRAM_TOKEN no Railway")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sinais", sinais))
    print("✅ Bot de sinais iniciado. Use /start no Telegram.")
    app.run_polling()


if __name__ == "__main__":
    main()
