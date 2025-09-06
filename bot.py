# bot_signals.py
# Bot de sinais para 20 pares da Gate.io (timeframe 15m)
# Regras: SMA20 + ATR14; LONG se desvio <= -thr; SHORT se desvio >= +thr

import os, math, requests, pandas as pd
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# ---------- Configurações ----------
INTERVAL = os.getenv("INTERVAL", "15m")     # Gate.io aceita '15m' (não '900')
DEFAULT_DEV = float(os.getenv("DEV", "0.005"))  # 0.5% por padrão
DEFAULT_PAIRS = [p.strip() for p in os.getenv(
    "PAIRS",
    "BTC_USDT,ETH_USDT,SOL_USDT,BNB_USDT,XRP_USDT,ADA_USDT,DOGE_USDT,TRX_USDT,AVAX_USDT,"
    "MATIC_USDT,DOT_USDT,LTC_USDT,SHIB_USDT,UNI_USDT,LINK_USDT,XLM_USDT,ATOM_USDT,ETC_USDT,APT_USDT,NEAR_USDT"
).split(",") if p.strip()]

CANDLE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"

# ---------- Utilidades ----------
def fetch_klines(pair: str, interval: str = INTERVAL, limit: int = 200):
    """Candle Gate.io -> DataFrame crescente"""
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
    if not trs: return float("nan")
    return float(pd.Series(trs).rolling(n).mean().iloc[-1])

def format_signal(direction: str, pair: str, last: float, stop: float, tp1: float, tp2: float) -> str:
    head = "📉 Entrada encontrada" if direction == "SHORT" else "📈 Entrada encontrada"
    return (
        f"{head}\n"
        f"✅ {direction} {pair} 15m\n"
        f"Preço: {last:.6f}\n"
        f"Stop:  {stop:.6f}\n"
        f"TP1:  {tp1:.6f} | TP2: {tp2:.6f}"
    )

def build_signal_for_pair(pair: str, deviation_thr: float):
    df = fetch_klines(pair, interval=INTERVAL, limit=120)
    if df is None or len(df) < 30:
        return None

    last = float(df["close"].iloc[-1])
    ma20 = float(sma(df["close"], 20).iloc[-1])
    atr14 = simple_atr(df, 14)
    if math.isnan(atr14) or atr14 <= 0 or math.isnan(ma20):
        return None

    dev = (last - ma20) / ma20
    if dev >= deviation_thr:      # SHORT
        return format_signal("SHORT", pair, last, last + 0.5*atr14, last - 0.5*atr14, last - 1.0*atr14)
    elif dev <= -deviation_thr:   # LONG
        return format_signal("LONG",  pair, last, last - 0.5*atr14, last + 0.5*atr14, last + 1.0*atr14)
    return None

def build_signals_text(pairs, thr):
    blocks=[]
    for p in pairs:
        try:
            sig = build_signal_for_pair(p, thr)
        except Exception:
            sig = None
        if sig: blocks.append(sig)
    if not blocks:
        return f"⏳ Sem sinais agora.\nLimiar usado: {thr*100:.2f}% • Pares: {len(pairs)}\nTente novamente em alguns minutos."
    return "\n\n".join(blocks)

def parse_threshold_and_pairs(args):
    """/sinais [threshold] [pairs…]. Ex: /sinais 0.004 BTC_USDT ETH_USDT"""
    thr = DEFAULT_DEV
    pairs = DEFAULT_PAIRS
    if args:
        # se o primeiro argumento for numérico, trata como threshold
        try:
            cand = float(str(args[0]).replace("%",""))
            thr = cand if cand < 1 else cand/100.0  # aceita 0.5 ou 0.005 ou 0.5%
            args = args[1:]
        except Exception:
            pass
        if args:
            pairs = [a.upper() for a in args]
    return thr, pairs

# ---------- Telegram ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot de SINAIS Gate.io (15m)\n"
        "Comando: /sinais [limiar] [pares...]\n"
        "Ex.: /sinais 0.004 BTC_USDT ETH_USDT  (limiar 0.4%)\n"
        "Sem parâmetros: usa 0.5% e 20 pares padrão."
    )

async def sinais(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thr, pairs = parse_threshold_and_pairs(context.args)
    text = build_signals_text(pairs, thr)
    await update.message.reply_text(text)

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra valores de diagnóstico do primeiro par informado."""
    pair = (context.args[0].upper() if context.args else DEFAULT_PAIRS[0])
    df = fetch_klines(pair, interval=INTERVAL, limit=120)
    if df is None or len(df) < 30:
        await update.message.reply_text(f"Sem dados para {pair}.")
        return
    last = float(df['close'].iloc[-1])
    ma20 = float(sma(df['close'], 20).iloc[-1])
    atr14 = simple_atr(df, 14)
    dev = (last-ma20)/ma20 if ma20 else float('nan')
    await update.message.reply_text(
        f"🔧 DEBUG {pair} 15m\n"
        f"Preço: {last:.6f}\nSMA20: {ma20:.6f}\nATR14: {atr14:.6f}\nDesvio: {dev*100:.3f}%"
    )

def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit("❌ Defina TELEGRAM_TOKEN no ambiente")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sinais", sinais))
    app.add_handler(CommandHandler("debug", debug))
    print("✅ Bot de sinais iniciado. Use /start e /sinais no Telegram.")
    app.run_polling()

if __name__ == "__main__":
    main()
