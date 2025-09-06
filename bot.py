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
NETWORK = os.getenv("REDE", "ethereum")                 # antes era NETWORK
THRESHOLD = float(os.getenv("LIMITE", "0.10"))          # antes era THRESHOLD
INTERVAL_SEC = int(os.getenv("INTERVALO_SEC", "90"))    # mantido igual

# Lista de tokens Polygon (chain_id 137)
DEFAULT_TOKENS = [
    # Stablecoins
    "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e (bridged)
    "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",  # USDC (nativo Circle)
    "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",  # DAI
    "0xC2132D05D31c914a87C6611C10748AEb04B58e8F",  # USDT
    "0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89",  # FRAX
    "0xE111178A87A3BFF0C8d18DECBa5798827539Ae99",  # LUSD
    "0xE4DfF5eFb8Cdd80Aee7c4A4A5eDd65E32f90F476",  # TUSD

    # Blue chips
    "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",  # WETH
    "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",  # WBTC
    "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",  # WMATIC
    "0x172370d5Cd63279eFa6d502DAB29171933a610AF",  # CRV
    "0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39",  # LINK
    "0x7ce9E3a3D969a1dDd9bb36fF02cD866B8333bEf2",  # UNI
    "0xD6DF932A45C0f255f85145f286eA0b292B21C90B",  # AAVE
    "0x5559edb74751a0ede9dea4dc23aee72cca6be3d5",  # LDO

    # DeFi / DEX
    "0x831753DD7087CaC61aB5644b308642cc1c33Dc13",  # QUICK
    "0x9A71012B13CA4d3D0Cdc72A177DF3ef03b0E76A3",  # BAL
    "0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a",  # SUSHI
    "0x1e5f20c77b6e9a43dd985ccfb67a3a124d6ed5d5",  # WOO
    "0x2a3bFF78B79A009976EeA096A51A948a3dD76Ee0",  # DFYN
    "0x0a3f6849f78076aefaDf113F5BED87720274dDC0",  # SNX

    # Gaming / NFT / outros
    "0x9C9e5fD8bbc25984B178FdCE6117Defa39d2db39",  # BNB (bridged)
    "0x62f594339830b90ae4c084ae7d223ffafd9658a7",  # GNS
    "0x8Dff5E27EA6b7AC08EbFdf9e9e3C8eBA8fF4B6e2",  # MATICX
    "0x9A02d6274D3514b0BD36D0b9D4aCf56cCB7cC4f7",  # SAND
    "0x9b83B1f49382bA2f8A2eB2A6BBb911cd3C4c1F9A",  # MANA

    # Novos/bridged/LS
    "0x0bA7d2e0fC1dE6fDd9C73e29eF6A4CAd69f93A1c",  # jEUR
    "0x7c9f4C87d911613Fe9ca58b579f737911AAD2D43",  # axlUSDC
    "0x2A88B032E57B48F8dF3f2B3a6109bFfd9FAdb907",  # stMATIC
    "0x3a58dA1D0d6eD66c36190E5b44A1e6C12316C03D",  # TETU",  # TETU
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

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    STATE[chat] = {"tokens": DEFAULT_TOKENS, "network": NETWORK, "threshold": THRESHOLD, "task": None}
    await update.message.reply_text("Bot pronto! Use /startscan para iniciar.")

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
    )

def main():
    if not TOKEN:
        raise RuntimeError("Defina TELEGRAM_TOKEN no ambiente Railway!")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("startscan", cmd_startscan))
    app.add_handler(CommandHandler("stopscan", cmd_stopscan))
    app.add_handler(CommandHandler("status", cmd_status))
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
