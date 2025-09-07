# bot.py
# Bot de sinais para Gate.io (15m) no Telegram
# Estratégia: mean-reversion SMA20 + filtros RSI/EMA
# Alertas: Entrada, TP1, TP2, STOP

import os, math, time, threading, requests, pandas as pd, logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from dataclasses import dataclass
from typing import Dict, Optional
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ───────────────────────────── Logs ─────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("gateio-signals")

# ───────────────────────────── CONFIG ───────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
INTERVAL         = "15m"
DEFAULT_DEV      = float(os.getenv("DEV", "0.004"))      # 0.4% desvio vs SMA20
POLLING_SECONDS  = int(os.getenv("POLLING", "60"))       # frequência do job
PORT             = int(os.getenv("PORT", "8080"))        # usado no Railway (Web)

DEFAULT_PAIRS = [p.strip() for p in os.getenv(
    "PAIRS",
    "BTC_USDT,ETH_USDT,SOL_USDT,BNB_USDT,XRP_USDT,ADA_USDT,DOGE_USDT,TRX_USDT,AVAX_USDT,"
    "MATIC_USDT,DOT_USDT,LTC_USDT,SHIB_USDT,UNI_USDT,LINK_USDT,XLM_USDT,ATOM_USDT,ETC_USDT,APT_USDT,NEAR_USDT"
).split(",") if p.strip()]

CANDLE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"

# filtros
RSI_ENABLED_DEFAULT = True
RSI_LONG_MAX_DEFAULT = 35.0
RSI_SHORT_MIN_DEFAULT = 65.0
EMA_ENABLED_DEFAULT = True
EMA_PERIOD_DEFAULT = 50

# ──────────────────────────── TYPES ─────────────────────────────
@dataclass
class Position:
    direction: str
    price: float
    stop: float
    tp1: float
    tp2: float
    opened_ts: float

# ──────────────────────────── HTTP KEEPALIVE ────────────────────
class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

def _start_http_server():
    """Sobe um HTTP simples na $PORT para manter o container Railway vivo."""
    try:
        server = HTTPServer(("0.0.0.0", PORT), _Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log.info("HTTP health server iniciado na porta %d", PORT)
    except Exception as e:
        log.warning("Falha ao iniciar HTTP health server: %s", e)

# ──────────────────────────── Utils ─────────────────────────────
def norm_pair(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.upper().replace(" ", "").replace("-", "_").replace("/", "_").replace("__","_")
    if "_USDT" not in s and len(s) <= 6:
        s = f"{s}_USDT"
    parts = s.split("_")
    if len(parts) != 2:
        return None
    return s

def fetch_klines(pair: str, interval: str = INTERVAL, limit: int = 120):
    try:
        r = requests.get(
            CANDLE_URL,
            params={"currency_pair": pair, "interval": interval, "limit": str(limit)},
            timeout=20
        )
        r.raise_for_status()
        data = r.json()
        df = pd.DataFrame(data, columns=["ts","volume","close","high","low","open"])
        for c in ["close","high","low","open","volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.sort_values("ts").reset_index(drop=True)
    except Exception as e:
        log.warning("Erro ao buscar candles %s: %s", pair, e)
        return None

def sma(series: pd.Series, n: int): return series.rolling(n).mean()
def ema(series: pd.Series, n: int): return series.ewm(span=n, adjust=False).mean()

def rsi(series: pd.Series, n: int = 14):
    delta = series.diff()
    up = delta.clip(lower=0).rolling(n).mean()
    down = (-delta.clip(upper=0)).rolling(n).mean()
    rs = up / down
    return 100 - (100 / (1 + rs))

def atr(df: pd.DataFrame, n: int = 14):
    h,l,c = df["high"].values, df["low"].values, df["close"].values
    trs=[]
    for i in range(1,len(df)):
        trs.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    return float(pd.Series(trs).rolling(n).mean().iloc[-1])

def format_signal(direction, pair, last, stop, tp1, tp2):
    head = "📉 Entrada encontrada" if direction == "SHORT" else "📈 Entrada encontrada"
    return (
        f"{head}\n"
        f"✅ {direction} {pair.replace('_','/')} 15m\n"
        f"Preço: {last:.6f}\nStop:  {stop:.6f}\nTP1:   {tp1:.6f} | TP2: {tp2:.6f}"
    )

# ──────────────────────────── Lógica ────────────────────────────
def try_build_signal(pair: str, deviation_thr: float, cd: dict):
    df = fetch_klines(pair)
    if df is None or len(df) < 60:
        return None

    close = df["close"]
    last = float(close.iloc[-1])
    ma20 = float(sma(close, 20).iloc[-1])
    atr14 = atr(df, 14)
    if math.isnan(ma20) or math.isnan(atr14) or atr14 <= 0:
        return None

    # configs de chat
    rsi_enabled   = cd.get("rsi_enabled", RSI_ENABLED_DEFAULT)
    rsi_long_max  = cd.get("rsi_long_max", RSI_LONG_MAX_DEFAULT)
    rsi_short_min = cd.get("rsi_short_min", RSI_SHORT_MIN_DEFAULT)
    ema_enabled   = cd.get("ema_enabled", EMA_ENABLED_DEFAULT)
    ema_period    = int(cd.get("ema_period", EMA_PERIOD_DEFAULT))

    dev = (last - ma20) / ma20
    rsi_val = float(rsi(close, 14).iloc[-1])
    ema_val = float(ema(close, ema_period).iloc[-1])

    if dev >= deviation_thr:  # SHORT
        if rsi_enabled and rsi_val < rsi_short_min:
            return None
        if ema_enabled and last >= ema_val:
            return None
        return Position("SHORT", last, last+0.5*atr14, last-0.5*atr14, last-1.0*atr14, time.time())

    if dev <= -deviation_thr:  # LONG
        if rsi_enabled and rsi_val > rsi_long_max:
            return None
        if ema_enabled and last <= ema_val:
            return None
        return Position("LONG", last, last-0.5*atr14, last+0.5*atr14, last+1.0*atr14, time.time())

    return None

# ──────────────────────────── Commands ──────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot de sinais 15m (Gate.io)\n"
        "/startsignals • /stopsignals\n/add PAR • /remove PAR • /watchlist\n"
        "/setdev 0.002 • /togglersi on/off • /setrsi 30 70\n"
        "/toggleema on/off • /setema 50 • /debug BTC_USDT"
    )

async def startsignals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cd = context.chat_data
    cd.setdefault("watchlist", set(DEFAULT_PAIRS[:10]))
    cd.setdefault("positions", {})
    cd.setdefault("dev", DEFAULT_DEV)
    cd["active"] = True
    wl = ", ".join([p.replace("_","/")+" 15m" for p in sorted(cd["watchlist"])])
    await update.message.reply_text("🟢 Sinais iniciados.\nWatchlist: " + wl)

async def stopsignals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["active"] = False
    await update.message.reply_text("🔴 Sinais parados.")

async def add_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pair = norm_pair("".join(context.args))
    if not pair:
        return await update.message.reply_text("Ex.: /add ETH_USDT")
    context.chat_data.setdefault("watchlist", set()).add(pair)
    await update.message.reply_text(f"✅ Adicionado {pair}")

async def remove_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pair = norm_pair("".join(context.args))
    wl = context.chat_data.get("watchlist", set())
    if pair in wl:
        wl.remove(pair)
        await update.message.reply_text(f"🗑️ Removido {pair}")
    else:
        await update.message.reply_text("Par não está na lista.")

async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wl = context.chat_data.get("watchlist", set())
    if not wl:
        return await update.message.reply_text("Watchlist vazia. Use /add BTC_USDT")
    await update.message.reply_text("👀 Watchlist: " + ", ".join(sorted(wl)))

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pair = norm_pair("".join(context.args))
    if not pair:
        return await update.message.reply_text("Ex.: /debug BTC_USDT")
    df = fetch_klines(pair)
    if df is None:
        return await update.message.reply_text("Sem dados.")
    last = float(df["close"].iloc[-1])
    ma20 = float(sma(df["close"],20).iloc[-1])
    rsi14 = float(rsi(df["close"],14).iloc[-1])
    ema50 = float(ema(df["close"],50).iloc[-1])
    dev   = (last-ma20)/ma20
    thr   = context.chat_data.get("dev",DEFAULT_DEV)
    await update.message.reply_text(
        f"DEBUG {pair}\nPreço:{last:.6f}\nSMA20:{ma20:.6f}\nRSI14:{rsi14:.2f}\nEMA50:{ema50:.6f}\n"
        f"Desvio:{dev*100:.3f}% vs {thr*100:.2f}%"
    )

# extras (ajustes via chat)
async def setdev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(context.args[0])
        context.chat_data["dev"] = val
        await update.message.reply_text(f"✅ Desvio alterado para {val*100:.2f}%")
    except Exception:
        await update.message.reply_text("Uso: /setdev 0.004")

async def togglersi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        onoff = (context.args[0].lower() == "on")
        context.chat_data["rsi_enabled"] = onoff
        await update.message.reply_text(f"RSI: {'ON' if onoff else 'OFF'}")
    except Exception:
        await update.message.reply_text("Uso: /togglersi on|off")

async def setrsi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lo = float(context.args[0]); hi = float(context.args[1])
        context.chat_data["rsi_long_max"]  = lo
        context.chat_data["rsi_short_min"] = hi
        await update.message.reply_text(f"✅ RSI limites: long≤{lo} / short≥{hi}")
    except Exception:
        await update.message.reply_text("Uso: /setrsi 30 70")

async def toggleema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        onoff = (context.args[0].lower() == "on")
        context.chat_data["ema_enabled"] = onoff
        await update.message.reply_text(f"EMA: {'ON' if onoff else 'OFF'}")
    except Exception:
        await update.message.reply_text("Uso: /toggleema on|off")

async def setema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        p = int(context.args[0])
        context.chat_data["ema_period"] = p
        await update.message.reply_text(f"✅ EMA período: {p}")
    except Exception:
        await update.message.reply_text("Uso: /setema 50")

# ──────────────────────────── Loop ──────────────────────────────
async def check_loop(context: ContextTypes.DEFAULT_TYPE):
    for chat_id, cd in list(context.application.chat_data.items()):
        if not cd.get("active"):
            continue
        wl = cd.get("watchlist", set())
        thr = cd.get("dev", DEFAULT_DEV)
        positions: Dict[str, Position] = cd.setdefault("positions", {})

        # novos sinais
        for pair in wl:
            if pair not in positions:
                pos = try_build_signal(pair, thr, cd)
                if pos:
                    positions[pair] = pos
                    await context.bot.send_message(
                        chat_id,
                        text=format_signal(pos.direction, pair, pos.price, pos.stop, pos.tp1, pos.tp2)
                    )

        # monitorar TP/STOP
        for pair, pos in list(positions.items()):
            df = fetch_klines(pair, limit=5)
            if df is None or df.empty:
                continue
            last = float(df["close"].iloc[-1])

            # TP2
            if (last >= pos.tp2 and pos.direction == "LONG") or (last <= pos.tp2 and pos.direction == "SHORT"):
                await context.bot.send_message(chat_id, text=f"🎯 TP2 atingido — {pair} 15m @ {last:.6f}")
                positions.pop(pair, None)
                continue

            # TP1
            if (last >= pos.tp1 and pos.direction == "LONG") or (last <= pos.tp1 and pos.direction == "SHORT"):
                await context.bot.send_message(chat_id, text=f"✅ TP1 atingido — {pair} 15m @ {last:.6f}")

            # STOP
            stopped = (last <= pos.stop if pos.direction == "LONG" else last >= pos.stop)
            if stopped:
                await context.bot.send_message(chat_id, text=f"🛑 STOP — {pair} 15m @ {pos.stop:.6f}")
                positions.pop(pair, None)

# ──────────────────────────── Main ──────────────────────────────
def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit("Defina TELEGRAM_TOKEN")

    _start_http_server()  # mantém o container Railway (Web) vivo

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # caso a instalação não traga a job_queue automaticamente,
    # criamos uma de fallback (requer o extra [job-queue] instalado)
    if app.job_queue is None:
        try:
            from telegram.ext import JobQueue
            jq = JobQueue()
            jq.set_application(app)
            jq.start()
            app.job_queue = jq
            log.info("JobQueue criada manualmente.")
        except Exception as e:
            log.error("JobQueue ausente e não pôde ser criada. "
                      "Instale o pacote com o extra [job-queue]. Erro: %s", e)
            raise

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("startsignals", startsignals))
    app.add_handler(CommandHandler("stopsignals", stopsignals))
    app.add_handler(CommandHandler("add", add_pair))
    app.add_handler(CommandHandler("remove", remove_pair))
    app.add_handler(CommandHandler("watchlist", watchlist_cmd))
    app.add_handler(CommandHandler("debug", debug))
    app.add_handler(CommandHandler("setdev", setdev))
    app.add_handler(CommandHandler("togglersi", togglersi))
    app.add_handler(CommandHandler("setrsi", setrsi))
    app.add_handler(CommandHandler("toggleema", toggleema))
    app.add_handler(CommandHandler("setema", setema))

    app.job_queue.run_repeating(check_loop, interval=POLLING_SECONDS, first=2)

    log.info("✅ Bot iniciado. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
