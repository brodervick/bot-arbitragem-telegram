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

# Lista de 50 tokens ERC-20 (Ethereum)
DEFAULT_TOKENS = [
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", # USDC
    "0xdAC17F958D2ee523a2206206994597C13D831ec7", # USDT
    "0x6B175474E89094C44Da98b954EedeAC495271d0F", # DAI
    "0x853d955aCEf822Db058eb8505911ED77F175b99e", # FRAX
    "0x8E870D67F660D95d5be530380D0eC0bd388289E1", # USDP
    "0x0000000000085d4780B73119b644AE5ecd22b376", # TUSD
    "0x5f98805a4e8be255a32880fdec7f6728c6568ba0", # LUSD
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", # WETH
    "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", # WBTC
    "0x514910771AF9Ca656af840dff83E8264EcF986CA", # LINK
    "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", # UNI
    "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9", # AAVE
    "0x9f8F72aA9304c8B593d555F12eF6589cC3A579A2", # MKR
    "0xC011A72400E58ecD99AeD7FE5Ab07AbaA4E5dd42", # SNX
    "0xc00e94Cb662C3520282E6f5717214004A7f26888", # COMP
    "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32", # LDO
    "0xD533a949740bb3306d119CC777fa900bA034cd52", # CRV
    "0x6B3595068778DD592e39A122f4f5a5CF09C90fE2", # SUSHI
    "0x0bc529c00C6401aEF6D220BE8C6Ea1667F6Ad93e", # YFI
    "0x0D8775F648430679A709E98d2b0Cb6250d2887EF", # BAT
    "0xE41d2489571d322189246DaFA5ebDe1F4699F498", # ZRX
    "0xc944E90C64B2c07662A292be6244BDf05Cda44a7", # GRT
    "0x7D1AfA7B718fb893dB30A3aBc0Cfc608AaCfeBB0", # MATIC (ERC-20)
    "0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72", # ENS
    "0x111111111117dC0aa78b770fA6A738034120C302", # 1INCH
    "0xba100000625a3754423978a60c9317c58a424e3D", # BAL
    "0x3845badAde8e6DFF049820680d1F14bd3903a5d0", # SAND
    "0x0F5D2fB29fb7d3CFeE444a200298f468908cC942", # MANA
    "0x3506424F91fD33084466F402dA0aFfAc3Cf8AaeD", # CHZ
    "0x4d224452801ACEd8B2F0aebE155379bb5D594381", # APE
    "0x15D4c048F83bd7e37d49EA4C83a07267Ec4203da", # GALA
    "0x18aAA7115705e8be94bFFebDE57Af9BFc265B998", # AUDIO
    "0x912CE59144191C1204E64559FE8253a0e49E6548", # ARB
    "0xbbbbca6a901c926f240b89eacb641d8aec7aeafd", # LRC
    "0x4691937a7508860F876c9c0a2a617e7d9e945D4B", # WOO
    "0x808507121B80c02388fAd14726482e061B8da827", # PENDLE
    "0xd33526068D116cE69F19A9ee46F0bd304F21A51f", # RPL
    "0xdefa4e8a7bcba345f687a2f1456f5edd9ce97202", # KNC
    "0x1F573D6Fb3F13d689FF844B4CE37794d79a7FF1C", # BNT
    "0xfF20817765cB7f73d4bde2e66e067E58D11095C2", # AMP
    "0x967da4048cD07aB37855c090aAF366e4ce1b9F48", # OCEAN
    "0xB62132e35a6c13ee1EE0f84dC5d40bad8d815206", # NEXO
    "0x4e15361fd6b4bb609fa63c81a2be19d873717870", # FTM (ERC-20)
    "0xA0b73E1Ff0B80914AB6fe0444E65848C4C34450b", # CRO (ERC-20)
    "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE", # SHIB
    "0x6982508145454Ce325dDbE47a25d4ec3d2311933", # PEPE
    "0x2b591e99afE9f32eAA6214f7B7629768c40Eeb39", # HEX
    "0x92D6C1e31e14520e676a687F0a93788B716BEff5", # DYDX
    "0xD41fDB03Ba84762dD66a0af1a6C8540FF1ba5dfb", # SFP
    "0xd26114cd6EE289AccF82350c8d8487fedB8A0C07", # OMG
    "0x4fabb145d64652a948d72533023f6e7a623c7c53", # BUSD
    "0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84"  # stETH
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
