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
THRESHOLD_DEFAULT = float(os.getenv("LIMITE", "0.50"))   # % (padrão 0.5)
INTERVAL_SEC = int(os.getenv("INTERVALO_SEC", "90"))     # segundos

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("scanner-bot")

# ── Slugs de rede do GeckoTerminal
NETWORK_SLUGS = {
    "ethereum": "eth", "eth": "eth",
    "polygon": "polygon_pos", "matic": "polygon_pos", "polygon_pos": "polygon_pos",
    "arbitrum": "arbitrum",
    "base": "base",
    "optimism": "optimism",
    "bsc": "bsc",
    # adicione mais se quiser…
}
def gt_slug(net: str) -> str:
    return NETWORK_SLUGS.get(net.lower(), net.lower())

# ── Tokens por rede
POLYGON_TOKENS: List[str] = [
    # Stablecoins
    "0xC2132D05D31c914a87C6611C10748AEb04B58e8F",  # USDT
    "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e
    "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",  # USDC (Circle)
    "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",  # DAI
    "0xf2f77fe7b8e66571e0fca7104c4d670bf1c8d722",  # BRLA
    "0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89",  # FRAX
    "0xE111178A87A3BFF0C8d18DECBa5798827539Ae99",  # LUSD
    "0xE4DfF5eFb8Cdd80Aee7c4A4A5eDd65E32f90F476",  # TUSD
    # Blue chips
    "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",  # WETH
    "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",  # WBTC
    "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",  # WMATIC
    "0xD6DF932A45C0f255f85145f286eA0b292B21C90B",  # AAVE
    "0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39",  # LINK
    "0xb33EaAd8d922B1083446DC23f610c2567fB5180f",  # UNI
    "0x172370d5Cd63279eFa6d502DAB29171933a610AF",  # CRV
    "0x5559edb74751a0ede9dea4dc23aee72cca6be3d5",  # LDO
    # DeFi / DEX
    "0x831753DD7087CaC61aB5644b308642cc1c33Dc13",  # QUICK
    "0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a",  # SUSHI
    "0x9A71012B13CA4d3D0Cdc72A177DF3ef03b0E76A3",  # BAL
    "0x0a3f6849f78076aefaDf113F5BED87720274dDC0",  # SNX
    "0x2a3bFF78B79A009976EeA096A51A948a3dD76Ee0",  # DFYN
    "0x1e5f20c77b6e9a43dd985ccfb67a3a124d6ed5d5",  # WOO
    # Gaming / Outros
    "0x9b83B1f49382bA2f8A2eB2A6BBb911cd3C4c1F9A",  # MANA
    "0x9A02d6274D3514b0BD36D0b9D4aCf56cCB7cC4f7",  # SAND
    "0x62f594339830b90ae4c084ae7d223ffafd9658a7",  # GNS
    "0x8Dff5E27EA6b7AC08EbFdf9e9e3C8eBA8fF4B6e2",  # MATICX
    "0x0bA7d2e0fC1dE6fDd9C73e29eF6A4CAd69f93A1c",  # jEUR
    "0x7c9f4C87d911613Fe9ca58b579f737911AAD2D43",  # axlUSDC
    "0x2A88B032E57B48F8dF3f2B3a6109bFfd9FAdb907",  # stMATIC
    "0x3a58dA1D0d6eD66c36190E5b44A1e6C12316C03D",  # TETU
]

ETHEREUM_TOKENS: List[str] = [
    # Stablecoins / majors
    "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
    "0x6B175474E89094C44Da98b954EedeAC495271d0F",  # DAI
    "0x853d955aCEf822Db058eb8505911ED77F175b99e",  # FRAX
    "0x0000000000085d4780B73119b644AE5ecd22b376",  # TUSD
    "0x5f98805A4E8be255a32880FDeC7F6728C6568bA0",  # LUSD
    # Blue chips
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
    "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  # WBTC
    # DeFi / DEX
    "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",  # AAVE
    "0x514910771AF9Ca656af840dff83E8264EcF986CA",  # LINK
    "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",  # UNI
    "0xD533a949740bb3306d119CC777fa900bA034cd52",  # CRV
    "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32",  # LDO
    "0xba100000625a3754423978a60c9317c58a424e3D",  # BAL
    "0x6B3595068778DD592e39A122f4f5a5CF09C90fE2",  # SUSHI
    "0xC011a73ee8576Fb46F5E1c5751cA3B9Fe0af2a6F",  # SNX
    # Gaming / Outros
    "0x0F5D2fB29fb7d3CFeE444A200298f468908cC942",  # MANA
    "0x3845badAde8e6dFF049820680d1F14bD3903a5d0",  # SAND
]

TOKEN_LISTS: Dict[str, List[str]] = {
    "polygon": POLYGON_TOKENS,
    "ethereum": ETHEREUM_TOKENS,
}

GT_BASE = "https://api.geckoterminal.com/api/v2"
STATE: Dict[int, dict] = {}

def gt_token_top_pools(network_slug: str, token: str) -> Dict:
    url = f"{GT_BASE}/networks/{network_slug}/tokens/{token}/pools?page=1"
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

def summarize_spreads(network_name: str, tokens: List[str]):
    network_slug = gt_slug(network_name)
    rows = []
    for addr in tokens:
        try:
            j = gt_token_top_pools(network_slug, addr)
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
            log.warning(f"Falha token {addr} ({network_slug}): {e}")
    rows.sort(key=lambda r: r[3], reverse=True)
    return rows

# ───────────────────────────── Scanner (sempre envia) ─────────────────────────────
async def scanner_loop(app, chat_id: int):
    while True:
        cfg = STATE.get(chat_id)
        if not cfg:
            return
        tokens = cfg["tokens"]
        network = cfg["network"]
        threshold = cfg["threshold"]
        try:
            rows = summarize_spreads(network, tokens)
            header = f"📊 Top spreads — {network} (limite {threshold:.2f}%)"
            if not rows:
                await app.bot.send_message(chat_id, header + "\n(nenhum pool com 2+ preços agora)")
            else:
                lines = [header]
                for addr, pmin, pmax, spread, dexes in rows[:10]:
                    mark = "✅" if spread >= threshold else "➖"
                    dex_list = ", ".join(dexes[:5]) if dexes else "-"
                    lines.append(
                        f"{mark} `{addr}`\n"
                        f"  • min ${pmin:.4f} | max ${pmax:.4f} | *{spread:.2f}%*\n"
                        f"  • DEXs: {dex_list}"
                    )
                await app.bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await app.bot.send_message(chat_id, f"⚠️ Erro: {e}")
        await asyncio.sleep(INTERVAL_SEC)

# ───────────────────────────── Comandos ─────────────────────────────
def init_state_for(chat_id: int, network_name: str, threshold: float):
    net = network_name.lower()
    tokens = TOKEN_LISTS.get(net, POLYGON_TOKENS)
    STATE[chat_id] = {"tokens": tokens, "network": net, "threshold": threshold, "task": None}

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    network_env = os.getenv("REDE", "polygon").strip().lower()
    init_state_for(chat, network_env, THRESHOLD_DEFAULT)
    await update.message.reply_text("🤖 Bot pronto! Use /startscan para iniciar.")

async def cmd_startscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    cfg = STATE.get(chat)
    if not cfg:
        network_env = os.getenv("REDE", "polygon").strip().lower()
        init_state_for(chat, network_env, THRESHOLD_DEFAULT)
    cfg = STATE[chat]
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
        await update.message.reply_text("Uso: /setnetwork <ethereum|polygon|arbitrum|base>")
        return
    net = context.args[0].strip().lower()
    init_state_for(chat, net, STATE.get(chat, {}).get("threshold", THRESHOLD_DEFAULT))
    await update.message.reply_text(f"✅ Rede ajustada para: {net} | tokens: {len(STATE[chat]['tokens'])}")

async def cmd_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Uso: /threshold <valor_em_%>")
        return
    try:
        val = float(context.args[0])
        if chat not in STATE:
            init_state_for(chat, os.getenv("REDE", "polygon"), val)
        else:
            STATE[chat]["threshold"] = val
        await update.message.reply_text(f"✅ Threshold ajustado para {val}%")
    except ValueError:
        await update.message.reply_text("Valor inválido. Ex.: /threshold 0.3")

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
