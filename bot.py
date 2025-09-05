import os
import asyncio
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)
import aiohttp

# ───────────────────────────── Config & Estado ─────────────────────────────
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Par padrão e threshold (em %) para alertar
STATE = {
    "pair": os.getenv("PAIR", "BTCUSDT"),
    "threshold": float(os.getenv("THRESHOLD", "0.50")),   # 0.50% por padrão
    "fee_per_side_pct": float(os.getenv("FEE_PER_SIDE_PCT", "0.10")),  # 0.10%/lado
    "auto": True,                # loop automático ligado
    "chat_id": None,             # preenchido no /start
    "interval_sec": int(os.getenv("INTERVAL_SEC", "20")),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("arb-bot")

# ─────────────────────────── Helpers de Mercado ─────────────────────────────
BYBIT_OB_URL   = "https://api.bybit.com/v5/market/orderbook"
BITGET_OB_URL  = "https://api.bitget.com/api/spot/v1/market/depth"

async def bybit_best(session: aiohttp.ClientSession, symbol: str):
    """Retorna (best_bid, best_ask) da Bybit Spot."""
    params = {"category": "spot", "symbol": symbol, "limit": 1}
    async with session.get(BYBIT_OB_URL, params=params, timeout=10) as r:
        js = await r.json()
        a = js.get("result", {}).get("a", [])
        b = js.get("result", {}).get("b", [])
        # Formato: [["preco","qtd"], ...]
        best_ask = float(a[0][0]) if a else None
        best_bid = float(b[0][0]) if b else None
        return best_bid, best_ask

async def bitget_best(session: aiohttp.ClientSession, symbol: str):
    """Retorna (best_bid, best_ask) da Bitget Spot."""
    params = {"symbol": symbol, "limit": 1}
    async with session.get(BITGET_OB_URL, params=params, timeout=10) as r:
        js = await r.json()
        data = js.get("data", {})
        bids = data.get("bids") or []
        asks = data.get("asks") or []
        # Formato: [["preco","qtd"], ...]
        best_bid = float(bids[0][0]) if bids else None
        best_ask = float(asks[0][0]) if asks else None
        return best_bid, best_ask

async def get_quotes(symbol: str):
    """Coleta melhores bids/asks em ambas exchanges."""
    async with aiohttp.ClientSession() as session:
        bybit_bid, bybit_ask   = await bybit_best(session, symbol)
        bitget_bid, bitget_ask = await bitget_best(session, symbol)
    return {
        "bybit":  {"bid": bybit_bid,  "ask": bybit_ask},
        "bitget": {"bid": bitget_bid, "ask": bitget_ask},
    }

def calc_spread(q):
    """
    Calcula o melhor cenário de arbitragem executável:
    comprar no ask mais barato e vender no bid mais caro.
    Retorna um dicionário com cenário e spread líquido.
    """
    fee_side = STATE["fee_per_side_pct"] / 100.0
    combos = []

    # Comprar Bybit (ask) -> Vender Bitget (bid)
    if q["bybit"]["ask"] and q["bitget"]["bid"]:
        buy = q["bybit"]["ask"]
        sell = q["bitget"]["bid"]
        gross = (sell - buy) / buy
        net = gross - (fee_side * 2)
        combos.append(("BYBIT→BITGET", buy, sell, gross, net))

    # Comprar Bitget (ask) -> Vender Bybit (bid)
    if q["bitget"]["ask"] and q["bybit"]["bid"]:
        buy = q["bitget"]["ask"]
        sell = q["bybit"]["bid"]
        gross = (sell - buy) / buy
        net = gross - (fee_side * 2)
        combos.append(("BITGET→BYBIT", buy, sell, gross, net))

    if not combos:
        return None

    best = max(combos, key=lambda x: x[4])  # pelo spread líquido
    return {
        "route": best[0],
        "buy": best[1],
        "sell": best[2],
        "gross_pct": best[3] * 100,
        "net_pct": best[4] * 100,
    }

def fmt_quotes(q):
    def f(x):
        if x is None: return "—"
        return f"{x:.2f}"
    return (
        f"Bybit  bid {f(q['bybit']['bid'])} / ask {f(q['bybit']['ask'])}\n"
        f"Bitget bid {f(q['bitget']['bid'])} / ask {f(q['bitget']['ask'])}"
    )

# ──────────────────────────────── Comandos ─────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE["chat_id"] = update.effective_chat.id
    msg = (
        "✅ Bot de Arbitragem online!\n\n"
        f"Par: *{STATE['pair']}*\n"
        f"Alerta a partir de: *{STATE['threshold']:.2f}%* (líquido)\n"
        f"Taxa por lado considerada: *{STATE['fee_per_side_pct']:.3f}%*\n"
        f"Auto-scan: *{'ligado' if STATE['auto'] else 'desligado'}* "
        f"cada *{STATE['interval_sec']}s*\n\n"
        "Comandos:\n"
        "/status – mostra preços e melhor rota agora\n"
        "/setpair BTCUSDT – troca o par\n"
        "/setthreshold 0.6 – muda % de alerta\n"
        "/setfee 0.10 – define taxa por lado\n"
        "/toggleauto – liga/desliga varredura automática\n"
        "/runonce – checa uma vez agora"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = STATE["pair"]
    q = await get_quotes(symbol)
    best = calc_spread(q)
    text = f"📊 *{symbol}*\n{fmt_quotes(q)}\n\n"
    if best:
        text += (
            f"Melhor rota: *{best['route']}*\n"
            f"Spread bruto: *{best['gross_pct']:.3f}%*\n"
            f"Spread líquido (c/ taxas): *{best['net_pct']:.3f}%*"
        )
    else:
        text += "Não consegui calcular o spread agora."
    await update.message.reply_text(text, parse_mode="Markdown")

async def setpair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /setpair BTCUSDT")
        return
    STATE["pair"] = context.args[0].upper()
    await update.message.reply_text(f"Par alterado para *{STATE['pair']}*.", parse_mode="Markdown")

async def setthreshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        v = float(context.args[0])
        STATE["threshold"] = v
        await update.message.reply_text(f"Threshold alterado para *{v:.2f}%*.", parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("Uso: /setthreshold 0.6  (em %)")

async def setfee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        v = float(context.args[0])
        STATE["fee_per_side_pct"] = v
        await update.message.reply_text(f"Taxa por lado definida em *{v:.3f}%*.", parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("Uso: /setfee 0.10  (em %)")

async def toggleauto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE["auto"] = not STATE["auto"]
    await update.message.reply_text(f"Auto-scan: *{'ligado' if STATE['auto'] else 'desligado'}*.", parse_mode="Markdown")

async def runonce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await do_scan_once(force_send=True)

# ────────────────────────── Loop de Varredura ──────────────────────────────
async def do_scan_once(force_send=False):
    """Faz 1 varredura e envia alerta se net spread >= threshold."""
    symbol = STATE["pair"]
    try:
        q = await get_quotes(symbol)
        best = calc_spread(q)
        if not best:
            return

        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        txt = (
            f"⏱ {now}\n"
            f"*{symbol}*\n{fmt_quotes(q)}\n\n"
            f"Melhor rota: *{best['route']}*\n"
            f"Spread líquido: *{best['net_pct']:.3f}%* "
            f"(threshold {STATE['threshold']:.2f}%)"
        )

        if STATE["chat_id"] and (force_send or best["net_pct"] >= STATE["threshold"]):
            from telegram import constants
            await application.bot.send_message(
                chat_id=STATE["chat_id"], text=txt, parse_mode=constants.ParseMode.MARKDOWN
            )
    except Exception as e:
        logger.exception("Erro no scan: %s", e)

async def scanner_task():
    # loop contínuo
    while True:
        if STATE["auto"] and STATE["chat_id"]:
            await do_scan_once(force_send=False)
        await asyncio.sleep(STATE["interval_sec"])

# ─────────────────────────────── Main ──────────────────────────────────────
async def on_start(app):
    # inicia a tarefa de varredura em background
    app.create_task(scanner_task())
    logger.info("Scanner iniciado.")

def build_app():
    app = ApplicationBuilder().token(TOKEN).post_init(on_start).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("setpair", setpair))
    app.add_handler(CommandHandler("setthreshold", setthreshold))
    app.add_handler(CommandHandler("setfee", setfee))
    app.add_handler(CommandHandler("toggleauto", toggleauto))
    app.add_handler(CommandHandler("runonce", runonce))
    return app

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN não encontrado nas variáveis de ambiente.")
    application = build_app()
    application.run_polling(drop_pending_updates=True)
