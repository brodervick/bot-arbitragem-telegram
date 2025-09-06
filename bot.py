import os
import math
import asyncio
import logging
import requests
from typing import Dict, List, Tuple

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ───────────────────────────── Config ─────────────────────────────
TOKEN = os.getenv("TELEGRAM_TOKEN")
THRESHOLD_DEFAULT = float(os.getenv("LIMITE", "0.50"))     # padrão 0.5%
INTERVAL_SEC = int(os.getenv("INTERVALO_SEC", "90"))       # segundos

# Lista de tokens essenciais para arbitragem em Polygon
DEFAULT_TOKENS: List[str] = [
    # Stablecoins
    "0xC2132D05D31c914a87C6611C10748AEb04B58e8F",  # USDT
    "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",  # USDC (nativo Circle)
    "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e (bridged)
    "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",  # DAI
    "0xf2f77fe7b8e66571e0fca7104c4d670bf1c8d722",  # BRLA

    # Blue chips
    "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",  # WETH
    "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",  # WBTC
    "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",  # WMATIC
]

# ───────────────────────────── API GeckoTerminal ─────────────────────────────
GT_BASE = "https://api.geckoterminal.com/api/v2"
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("scanner-bot")

STATE = {}

def gt_token_top_pools(network: str, token: str, page: int = 1) -> Dict:
    url = f"{GT_BASE}/networks/{network}/tokens/{token}/pools?page={page}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def calc_pool_price_usd(pool_obj) -> Tuple[str, float, str]:
    attrs = pool_obj.get("attributes", {})
    price = attrs.get("price_in_usd")
    dex = attrs.get("dex", "DEX")
    if not price:
        return ("pool", float("nan"), dex)
    return (attrs.get("name", "pool"), float(price), dex)

def summarize_spreads(network: str, tokens: List[str]):
    rows = []
    for addr in tokens:
        try:
            j = gt_token_top_pools(network, addr)
            data = j.get("data", [])
            prices, dexes = [], []
            for p in data:
                _, px, dex = calc_pool_price_usd(p)
                if px and px > 0 and math.isfinite(px):
                    prices.append(px)
                    dexes.append(dex)
            if len(prices) >= 2:
                pmin, pmax = min(prices), max(prices)
                spread = (pmax - pmin) / pmax * 100
                rows.append((addr, pmin, pmax, spread, sorted(set(dexes))))
        except Exception as e:
            log.warning(f"Falha token {addr}: {e}")
    rows.sort(key=lambda r: r[3], reverse=True)
    return rows

# ───────────────────────────── Scanner Loop ─────────────────────────────
async def scanner_loop(app, chat_id: int):
    while True:
        cfg = STATE.get(chat_id)
        if not cfg:
            return
        tokens, network, threshold = cfg["tokens"], cfg["network"], cfg["threshold"]
        try:
            rows = summarize_spreads(network, tokens)
            hits = [r for r in rows if r[3] >= threshold]
            if hits:
                lines = [f"🔎 *Top spreads ≥ {threshold:.2f}%* — _{network}_"]
                for addr, pmin, pmax, spread, dexes in hits[:10]:
                    lines.append(
                        f"`{addr}`\n"
                        f"  • min ${pmin:.4f} | max ${pmax:.4f} | *{spread:.2f}%*\n"
                        f"  • DEXs: {', '.join(dexes[:5])}"
                    )
                await app.bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await app.bot.send_message(chat_id, f"⚠️ Erro: {e}")
        await asyncio.sleep(INTERVAL_SEC)

# ───────────────────────────── Comandos ─────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    network_env = os.getenv("REDE", "polygon").strip().lower()
    STATE[chat] = {
        "tokens": DEFAULT_TOKENS,
        "network": network_env,
        "threshold": THRESHOLD_DEFAULT,
        "task": None
    }
    await update.message.reply_text("🤖 Bot pronto! Use /startscan para iniciar.")

async def cmd_startscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    cfg = STATE.get(chat)
    if not cfg:
        return
    if cfg["task"] and not cfg["task"].done():
        cfg["task"].cancel()
    cfg["task"] = asyncio.create_task(scanner_loop(context.application, chat))
    await update.message.reply_text("🟢 Scanner iniciado.")

async def cmd_stopscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    cfg = STATE.get(chat)
    if cfg and cfg["task"]:
        cfg["task"].cancel()
        cfg["task"] = None
    await update.message.reply_text("🔴 Scanner parado.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    cfg = STATE.get(chat)
    if not cfg:
        await update.message.reply_text("Use /start primeiro.")
        return
    await update.message.reply_text(
        f"Rede: {cfg['network']}\nTokens: {len(cfg['tokens'])}\nLimite: {cfg['threshold']}%"
    )

async def cmd_setnetwork(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Uso: /setnetwork <ethereum|polygon|arbitrum|base|...>")
        return
    net = context.args[0].strip().lower()
    if chat not in STATE:
        STATE[chat] = {"tokens": DEFAULT_TOKENS, "network": net, "threshold": THRESHOLD_DEFAULT, "task": None}
    else:
        STATE[chat]["network"] = net
    await update.message.reply_text(f"✅ Rede ajustada para: {net}")

async def cmd_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Uso: /threshold <valor_em_%>")
        return
    try:
        val = float(context.args[0])
        if chat not in STATE:
            STATE[chat] = {"tokens": DEFAULT_TOKENS, "network": "polygon", "threshold": val, "task": None}
        else:
            STATE[chat]["threshold"] = val
        await update.message.reply_text(f"✅ Threshold ajustado para {val}%")
    except ValueError:
        await update.message.reply_text("Valor inválido. Exemplo: /threshold 0.5")

# ───────────────────────────── Main ─────────────────────────────
def main():
    if not TOKEN:
        raise RuntimeError("Defina TELEGRAM_TOKEN no Railway!")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("startscan", cmd_startscan))
    app.add_handler(CommandHandler("stopscan", cmd_stopscan))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("setnetwork", cmd_setnetwork))
    app.add_handler(CommandHandler("threshold", cmd_threshold))
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
