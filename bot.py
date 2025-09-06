import os
import math
import asyncio
import logging
import requests
from typing import Dict, List, Tuple
from datetime import datetime

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)

# ───────────────────────────── Configuração ─────────────────────────────
TOKEN = os.getenv("TELEGRAM_TOKEN")
NETWORK = os.getenv("REDE", "ethereum")
THRESHOLD = float(os.getenv("LIMITE", "0.10"))
INTERVAL_SEC = int(os.getenv("INTERVALO_SEC", "90"))

CAPITAL = float(os.getenv("CAPITAL", "102"))  # tua banca em USDT
ALVO_DIARIO = float(os.getenv("ALVO_DIARIO", "10"))

# Tokens padrão (exemplo ETH mainnet)
DEFAULT_TOKENS = [
    "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
    "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  # WBTC
    "0x6B175474E89094C44Da98b954EedeAC495271d0F",  # DAI
]

# ───────────────────────────── API GeckoTerminal ─────────────────────────────
GT_BASE = "https://api.geckoterminal.com/api/v2"
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("scanner-bot")

STATE = {"active": {}}

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
                lines = [f"📊 *Top spreads ≥ {threshold:.2f}%* — _{network}_"]
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

# ───────────────────────────── Comandos Telegram ─────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    STATE[chat] = {
        "tokens": DEFAULT_TOKENS,
        "network": NETWORK,
        "threshold": THRESHOLD,
        "capital": CAPITAL,
        "goal": ALVO_DIARIO,
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
        f"📌 Rede: {cfg['network']}\n"
        f"📊 Tokens: {len(cfg['tokens'])}\n"
        f"📈 Limite: {cfg['threshold']}%\n"
        f"💰 Banca: {cfg['capital']} USDT\n"
        f"🎯 Meta diária: {cfg['goal']} USDT"
    )

async def cmd_setnetwork(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Uso: /setnetwork <ethereum|polygon|arbitrum|base|...>")
        return
    net = context.args[0].strip().lower()
    if chat not in STATE:
        STATE[chat] = {"tokens": DEFAULT_TOKENS, "network": NETWORK, "threshold": THRESHOLD, "task": None}
    STATE[chat]["network"] = net
    await update.message.reply_text(f"✅ Rede ajustada para: {net}")

async def cmd_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Uso: /threshold <valor_em_%>")
        return
    try:
        th = float(context.args[0])
        STATE[chat]["threshold"] = th
        await update.message.reply_text(f"✅ Threshold ajustado para {th:.2f}%")
    except Exception:
        await update.message.reply_text("⚠️ Valor inválido.")

# ───────────────────────────── Main ─────────────────────────────

def main():
    if not TOKEN:
        raise RuntimeError("Defina TELEGRAM_TOKEN no ambiente Railway!")
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
