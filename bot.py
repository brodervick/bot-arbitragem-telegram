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
INTERVAL = "15m"                         # timeframe fixo
FETCH_LIMIT = 300                        # qntd de candles p/ indicadores
LOOP_SLEEP_SEC = 60                      # checar a cada 60s
DEFAULT_PAIRS = ["BTC_USDT","ETH_USDT","LTC_USDT","TRX_USDT","AVAX_USDT"]

STATE = {
    "running": False,
    "pairs": set(os.getenv("PAIRS", ",".join(DEFAULT_PAIRS)).split(",")),
    "dev": float(os.getenv("DEV", "0.002")),       # 0.2% por padrão
    "use_rsi": True,
    "rsi_low": 30,
    "rsi_high": 70,
    "use_ema": True,
    "ema_len": 50,
}

STATS = {"wins": 0, "losses": 0, "last_signals": {}}  # simples placeholder

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
    # formatação simples; Gate tem ticks diferentes por par
    if x >= 100:
        return f"{x:.3f}"
    if x >= 1:
        return f"{x:.6f}"
    return f"{x:.8f}"

# ───────────────────── API Gate.io (candles) ─────────────────────
GATE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"

async def fetch_candles(session: aiohttp.ClientSession, pair: str, interval: str) -> Optional[pd.DataFrame]:
    params = {"currency_pair": pair, "interval": interval, "limit": str(FETCH_LIMIT)}
    try:
        async with session.get(GATE_URL, params=params, timeout=20) as r:
            if r.status != 200:
                log.warning("HTTP %s ao buscar %s", r.status, pair)
                return None
            data = await r.json()
            # Gate retorna lista de listas: [t, v, c, h, l, o] em ordem DECRESCENTE de tempo
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
    except Exception as e:
        log.exception("Erro fetch_candles %s: %s", pair, e)
        return None

# ──────────────── Lógica de Sinal (mean-reversion + filtros) ────────────────
def evaluate_signal(df: pd.DataFrame, dev: float, use_rsi: bool, rsi_low: int, rsi_high: int,
                    use_ema: bool, ema_len: int) -> Tuple[Optional[str], Dict]:
    """
    Retorna ("LONG"/"SHORT"/None, info_dict)
    Trabalha na ÚLTIMA VELA FECHADA.
    """
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

    # Desvio versus SMA
    deviation = pct(last, sma_v)

    # Filtros
    rsi_ok_long = (rsi_v <= rsi_low)
    rsi_ok_short = (rsi_v >= rsi_high)

    trend_ok_long = (last > ema_v)
    trend_ok_short = (last < ema_v)

    info = {
        "last": last, "sma20": sma_v, "ema": ema_v, "rsi": rsi_v,
        "atr": atr_v, "deviation": deviation
    }

    signal = None
    if abs(deviation) >= dev:
        # Mean-reversion: se preço << SMA => LONG; se preço >> SMA => SHORT
        if deviation <= -dev:
            # preço abaixo da SMA => LONG
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

    # Alvos e stop por ATR (conservador)
    if np.isfinite(atr_v) and atr_v > 0:
        if signal == "LONG":
            entry = last
            tp1 = entry + 0.5 * atr_v
            tp2 = entry + 1.0 * atr_v
            stop = entry - 1.0 * atr_v
        elif signal == "SHORT":
            entry = last
            tp1 = entry - 0.5 * atr_v
            tp2 = entry - 1.0 * atr_v
            stop = entry + 1.0 * atr_v
        else:
            entry = tp1 = tp2 = stop = None
    else:
        entry = tp1 = tp2 = stop = None

    info.update({"entry": entry, "tp1": tp1, "tp2": tp2, "stop": stop})
    return signal, info

# ────────────────────── Mensagens & Helpers ──────────────────────
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

# ─────────────────────────── Loop de Sinais ───────────────────────────
async def signals_loop(app):
    log.info("Loop de sinais iniciado.")
    async with aiohttp.ClientSession() as session:
        last_bar_time: Dict[str, datetime] = {}

        while STATE["running"]:
            try:
                for pair in list(STATE["pairs"]):
                    df = await fetch_candles(session, pair, INTERVAL)
                    if df is None or df.empty:
                        continue

                    # Trabalhar com a ÚLTIMA vela FECHADA
                    ts_last_closed = df["ts"].iloc[-1]

                    # Evitar repetir alerta na mesma vela
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
                            except Exception as e:
                                log.warning("Falha ao enviar para %s: %s", chat_id, e)

                    # marca a vela processada
                    last_bar_time[pair] = ts_last_closed

            except Exception as e:
                log.exception("Erro no loop principal: %s", e)

            await asyncio.sleep(LOOP_SLEEP_SEC)

    log.info("Loop de sinais finalizado.")

# Pequena store de chats (memória volátil)
class AppWithChats:
    def __init__(self, app):
        self._app = app
        self.chat_ids: set[int] = set()

    @property
    def bot(self):
        return self._app.bot

# ──────────────────────────── Handlers ────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app = context.application.app_with_chats  # type: ignore
    app.chat_ids.add(update.effective_chat.id)
    text = (
        "🤖 Bot de sinais 15m (Gate.io)\n"
        "/startsignals • /stopsignals\n"
        "/add PAR • /remove PAR • /watchlist\n"
        "/setdev 0.002 • /togglersi on/off • /setrsi 30 70\n"
        "/toggleema on/off • /setema 50 • /debug BTC_USDT"
    )
    await update.message.reply_text(text)

async def startsignals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if STATE["running"]:
        await update.message.reply_text("Já estou rodando os sinais.")
        return
    STATE["running"] = True
    # garante que o chat atual receba alertas
    context.application.app_with_chats.chat_ids.add(update.effective_chat.id)  # type: ignore
    asyncio.create_task(signals_loop(context.application.app_with_chats))       # type: ignore
    await update.message.reply_text(
        "🟢 Sinais iniciados.\n"
        f"Watchlist: {', '.join(sorted(STATE['pairs']))} {INTERVAL}"
    )

async def stopsignals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE["running"] = False
    await update.message.reply_text("🔴 Sinais parados.")

async def add_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use: /add BTC_USDT")
        return
    pair = context.args[0].upper()
    STATE["pairs"].add(pair)
    await update.message.reply_text(f"Par adicionado: {pair}")

async def remove_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use: /remove BTC_USDT")
        return
    pair = context.args[0].upper()
    if pair in STATE["pairs"]:
        STATE["pairs"].remove(pair)
        await update.message.reply_text(f"Par removido: {pair}")
    else:
        await update.message.reply_text("Par não estava na lista.")

async def watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👀 Watchlist: " + ", ".join(sorted(STATE["pairs"])))

async def setdev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(f"Desvio atual: {STATE['dev']:.4f} ({STATE['dev']*100:.2f}%)")
        return
    try:
        val = float(context.args[0])
        if val <= 0 or val > 0.05:
            await update.message.reply_text("Use um valor entre 0.0005 e 0.05")
            return
        STATE["dev"] = val
        await update.message.reply_text(f"Novo desvio: {val:.4f} ({val*100:.2f}%)")
    except:
        await update.message.reply_text("Ex.: /setdev 0.002")

async def togglersi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    onoff = (context.args[0].lower() if context.args else "")
    if onoff in ("on", "off"):
        STATE["use_rsi"] = (onoff == "on")
    else:
        STATE["use_rsi"] = not STATE["use_rsi"]
    await update.message.reply_text(f"Filtro RSI: {'on' if STATE['use_rsi'] else 'off'}")

async def setrsi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text(f"Atual: {STATE['rsi_low']}-{STATE['rsi_high']} | Ex.: /setrsi 30 70")
        return
    try:
        low = int(context.args[0]); high = int(context.args[1])
        if not (0 <= low < high <= 100):
            raise ValueError
        STATE["rsi_low"], STATE["rsi_high"] = low, high
        await update.message.reply_text(f"RSI bounds: {low}-{high}")
    except:
        await update.message.reply_text("Ex.: /setrsi 25 75")

async def toggleema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    onoff = (context.args[0].lower() if context.args else "")
    if onoff in ("on", "off"):
        STATE["use_ema"] = (onoff == "on")
    else:
        STATE["use_ema"] = not STATE["use_ema"]
    await update.message.reply_text(f"Filtro EMA: {'on' if STATE['use_ema'] else 'off'}")

async def setema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(f"EMA atual: {STATE['ema_len']}")
        return
    try:
        n = int(context.args[0])
        if n < 10 or n > 200:
            raise ValueError
        STATE["ema_len"] = n
        await update.message.reply_text(f"Nova EMA: {n}")
    except:
        await update.message.reply_text("Ex.: /setema 50")

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Ex.: /debug BTC_USDT")
        return
    pair = context.args[0].upper()
    info = STATS["last_signals"].get(pair)
    await update.message.reply_text(format_debug_msg(pair, info))

# ──────────────────────────── Bootstrap ────────────────────────────
def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit("Defina TELEGRAM_TOKEN no ambiente.")

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    # wrapper p/ guardar chat_ids
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

    log.info("Bot up. Interval: %s | Pairs: %s", INTERVAL, ",".join(sorted(STATE["pairs"])))
    application.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
