import os, math, asyncio, logging, requests
from typing import Dict, List, Tuple
from datetime import datetime

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)

# ───────────────────────────── Config por ENV ─────────────────────────────
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Rede padrão do GeckoTerminal (mude via /setnetwork)
NETWORK = os.getenv("NETWORK", "ethereum")

# Lista padrão de tokens (endereços) para monitorar (mude via /settokens)
DEFAULT_TOKENS = [
    # exemplos na Ethereum:
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
    "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
    "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  # WBTC
    "0x6982508145454Ce325dDbE47a25d4ec3d2311933",  # PEPE
    "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE",  # SHIB
    "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",  # UNI
]

# limites / frequência
THRESHOLD = float(os.getenv("THRESHOLD", "0.80"))  # alerta se spread >= %
INTERVAL_SEC = int(os.getenv("INTERVAL_SEC", "90"))  # intervalo de varredura

# 0x (proxy de agregador tipo "MetaMask price", sem a taxa 0,875% do MM)
ZX_PRICE = "https://api.0x.org/swap/price"
GT_BASE = "https://api.geckoterminal.com/api/v2"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("scanner-bot")

# ───────────────────────────── Estado do Bot ─────────────────────────────
# para cada chat, guardamos parâmetros e a task assíncrona do scanner
STATE = {}  # chat_id -> {"tokens": [...], "network": str, "threshold": float, "task": asyncio.Task | None}

# ───────────────────────────── Helpers API ─────────────────────────────
def gt_token_top_pools(network: str, token: str, page: int = 1) -> Dict:
    url = f"{GT_BASE}/networks/{network}/tokens/{token}/pools?page={page}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def calc_pool_price_usd(pool_obj) -> Tuple[str, float, str]:
    attrs = pool_obj.get("attributes", {})
    price = attrs.get("price_in_usd")
    name = attrs.get("name", "pool")
    dex = attrs.get("dex", "DEX")
    if price is None:
        return (name, float("nan"), dex)
    return (name, float(price), dex)

def format_usd(x): return f"${x:,.4f}"

def summarize_spreads(network: str, tokens: List[str]):
    """retorna lista de tuples (token_addr, pmin, pmax, spread%, dex_list)"""
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

# ───────────────────────────── Scanner Task ─────────────────────────────
async def scanner_loop(app, chat_id: int):
    """task que roda em loop e envia alertas para um chat"""
    while True:
        config = STATE.get(chat_id)
        if not config:
            return
        tokens = config["tokens"]
        network = config["network"]
        threshold = config["threshold"]

        try:
            rows = summarize_spreads(network, tokens)
            # filtra por threshold
            hits = [r for r in rows if r[3] >= threshold]
            if hits:
                lines = [f"🔎 *Top spreads ≥ {threshold:.2f}%* — _{network}_  ({datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')})"]
                for addr, pmin, pmax, spread, dexes in hits[:10]:
                    lines.append(
                        f"`{addr}`\n"
                        f"  • mín {format_usd(pmin)} | máx {format_usd(pmax)} | *{spread:.2f}%*\n"
                        f"  • DEXs: {', '.join(dexes[:6])}"
                    )
                msg = "\n".join(lines)
                await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            else:
                await app.bot.send_message(chat_id=chat_id,
                    text=f"✔️ Nenhum spread ≥ {threshold:.2f}% agora em _{network}_.",
                    parse_mode="Markdown"
                )
        except Exception as e:
            log.exception("Erro no scanner")
            try:
                await app.bot.send_message(chat_id=chat_id, text=f"⚠️ Erro no scanner: {e}")
            except:
                pass

        await asyncio.sleep(INTERVAL_SEC)

# ───────────────────────────── Handlers ─────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    STATE.setdefault(chat_id, {
        "tokens": DEFAULT_TOKENS.copy(),
        "network": NETWORK,
        "threshold": THRESHOLD,
        "task": None
    })

    await update.message.reply_text(
        "👋 Pronto para monitorar spreads entre DEXs (inclui Uniswap via GeckoTerminal).\n"
        "Comandos:\n"
        "• /startscan — inicia scanner periódico\n"
        "• /stopscan — para scanner\n"
        "• /status — mostra config atual\n"
        "• /setnetwork <rede> — ex.: ethereum, base, arbitrum, polygon\n"
        "• /settokens <addr1,addr2,...>\n"
        "• /threshold <percentual> — ex.: 0.8\n"
        "• /help — ajuda"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    cfg = STATE.get(chat_id)
    if not cfg:
        await update.message.reply_text("Use /start primeiro.")
        return
    await update.message.reply_text(
        "⚙️ *Config atual:*\n"
        f"• network: `{cfg['network']}`\n"
        f"• threshold: {cfg['threshold']:.2f}%\n"
        f"• tokens ({len(cfg['tokens'])}): {', '.join(cfg['tokens'][:6])}{'...' if len(cfg['tokens'])>6 else ''}\n"
        f"• scanner rodando: {'sim' if cfg['task'] and not cfg['task'].done() else 'não'}",
        parse_mode="Markdown"
    )

async def cmd_setnetwork(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Uso: /setnetwork <ethereum|base|arbitrum|polygon|...>")
        return
    net = context.args[0].strip().lower()
    STATE.setdefault(chat_id, {"tokens": DEFAULT_TOKENS.copy(), "network": NETWORK, "threshold": THRESHOLD, "task": None})
    STATE[chat_id]["network"] = net
    await update.message.reply_text(f"✅ network definida: {net}")

async def cmd_settokens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    txt = " ".join(context.args)
    if not txt:
        await update.message.reply_text("Uso: /settokens <addr1,addr2,...>")
        return
    toks = [t.strip() for t in txt.replace("\n", " ").split(",") if t.strip()]
    if not toks:
        await update.message.reply_text("Nenhum endereço válido encontrado.")
        return
    STATE.setdefault(chat_id, {"tokens": DEFAULT_TOKENS.copy(), "network": NETWORK, "threshold": THRESHOLD, "task": None})
    STATE[chat_id]["tokens"] = toks
    await update.message.reply_text(f"✅ {len(toks)} tokens configurados.")

async def cmd_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Uso: /threshold <percentual>  (ex.: /threshold 0.8)")
        return
    try:
        th = float(context.args[0])
        if th <= 0:
            raise ValueError()
    except:
        await update.message.reply_text("Valor inválido. Ex.: /threshold 0.8")
        return
    STATE.setdefault(chat_id, {"tokens": DEFAULT_TOKENS.copy(), "network": NETWORK, "threshold": THRESHOLD, "task": None})
    STATE[chat_id]["threshold"] = th
    await update.message.reply_text(f"✅ threshold definido: {th:.2f}%")

async def cmd_startscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    cfg = STATE.setdefault(chat_id, {"tokens": DEFAULT_TOKENS.copy(), "network": NETWORK, "threshold": THRESHOLD, "task": None})
    # cancela task antiga (se existir)
    if cfg["task"] and not cfg["task"].done():
        cfg["task"].cancel()
    cfg["task"] = asyncio.create_task(scanner_loop(context.application, chat_id))
    await update.message.reply_text("🟢 Scanner iniciado. Vou enviar alertas periódicos aqui no chat.")

async def cmd_stopscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    cfg = STATE.get(chat_id)
    if not cfg or not cfg["task"]:
        await update.message.reply_text("Scanner não está rodando.")
        return
    try:
        cfg["task"].cancel()
    except:
        pass
    cfg["task"] = None
    await update.message.reply_text("🔴 Scanner parado.")

# ───────────────────────────── Main ─────────────────────────────
def main():
    if not TOKEN:
        raise RuntimeError("Defina TELEGRAM_TOKEN no ambiente.")
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("setnetwork", cmd_setnetwork))
    app.add_handler(CommandHandler("settokens", cmd_settokens))
    app.add_handler(CommandHandler("threshold", cmd_threshold))
    app.add_handler(CommandHandler("startscan", cmd_startscan))
    app.add_handler(CommandHandler("stopscan", cmd_stopscan))

    log.info("Bot online.")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
