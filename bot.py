import os
import math
import asyncio
import logging
import requests
from typing import Dict, List, Tuple, Optional

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ============================== CONFIG GERAL ==============================
TOKEN = os.getenv("TELEGRAM_TOKEN")
THRESHOLD_DEFAULT = float(os.getenv("LIMITE", "0.50"))   # % arbitragem (0.5 padrão)
INTERVAL_SEC = int(os.getenv("INTERVALO_SEC", "90"))     # loop arbitragem
SIGNAL_SEC = int(os.getenv("SIGNAL_SEC", "60"))          # loop sinais

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("arb-signal-bot")

# ============================== ARBITRAGEM (GeckoTerminal) ==============================
NETWORK_SLUGS = {
    "ethereum": "eth", "eth": "eth",
    "polygon": "polygon_pos", "matic": "polygon_pos", "polygon_pos": "polygon_pos",
    "arbitrum": "arbitrum",
    "base": "base",
    "optimism": "optimism",
    "bsc": "bsc",
}
def gt_slug(net: str) -> str:
    return NETWORK_SLUGS.get(net.lower(), net.lower())

GT_BASE = "https://api.geckoterminal.com/api/v2"

# --- Tokens por rede (resumo útil para arbitragem) ---
POLYGON_TOKENS: List[str] = [
    # stable
    "0xC2132D05D31c914a87C6611C10748AEb04B58e8F","0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359","0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
    "0xf2f77fe7b8e66571e0fca7104c4d670bf1c8d722","0x45c32fA6DF82ead1e2EF74d17b76547EDdFaFF89",
    "0xE111178A87A3BFF0C8d18DECBa5798827539Ae99","0xE4DfF5eFb8Cdd80Aee7c4A4A5eDd65E32f90F476",
    # blue chips
    "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619","0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",
    "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270","0xD6DF932A45C0f255f85145f286eA0b292B21C90B",
    "0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39","0xb33EaAd8d922B1083446DC23f610c2567fB5180f",
    "0x172370d5Cd63279eFa6d502DAB29171933a610AF","0x5559edb74751a0ede9dea4dc23aee72cca6be3d5",
    # DeFi & outros
    "0x831753DD7087CaC61aB5644b308642cc1c33Dc13","0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a",
    "0x9A71012B13CA4d3D0Cdc72A177DF3ef03b0E76A3","0x0a3f6849f78076aefaDf113F5BED87720274dDC0",
    "0x2a3bFF78B79A009976EeA096A51A948a3dD76Ee0","0x1e5f20c77b6e9a43dd985ccfb67a3a124d6ed5d5",
    "0x9b83B1f49382bA2f8A2eB2A6BBb911cd3C4c1F9A","0x9A02d6274D3514b0BD36D0b9D4aCf56cCB7cC4f7",
    "0x62f594339830b90ae4c084ae7d223ffafd9658a7","0x8Dff5E27EA6b7AC08EbFdf9e9e3C8eBA8fF4B6e2",
    "0x0bA7d2e0fC1dE6fDd9C73e29eF6A4CAd69f93A1c","0x7c9f4C87d911613Fe9ca58b579f737911AAD2D43",
    "0x2A88B032E57B48F8dF3f2B3a6109bFfd9FAdb907","0x3a58dA1D0d6eD66c36190E5b44A1e6C12316C03D",
]
ETHEREUM_TOKENS: List[str] = [
    "0xdAC17F958D2ee523a2206206994597C13D831ec7","0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "0x6B175474E89094C44Da98b954EedeAC495271d0F","0x853d955aCEf822Db058eb8505911ED77F175b99e",
    "0x0000000000085d4780B73119b644AE5ecd22b376","0x5f98805A4E8be255a32880FDeC7F6728C6568bA0",
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2","0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
    "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9","0x514910771AF9Ca656af840dff83E8264EcF986CA",
    "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984","0xD533a949740bb3306d119CC777fa900bA034cd52",
    "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32","0xba100000625a3754423978a60c9317c58a424e3D",
    "0x6B3595068778DD592e39A122f4f5a5CF09C90fE2","0xC011a73ee8576Fb46F5E1c5751cA3B9Fe0af2a6F",
    "0x0F5D2fB29fb7d3CFeE444A200298f468908cC942","0x3845badAde8e6dFF049820680d1F14bD3903a5d0",
]
TOKEN_LISTS: Dict[str, List[str]] = {"polygon": POLYGON_TOKENS, "ethereum": ETHEREUM_TOKENS}

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

# ============================== EXCHANGES (ccxt) — Gate.io por padrão ==============================
import ccxt

EXCHANGE_NAME = os.getenv("EXCHANGE", "gateio").lower()

def build_exchanges(name: str):
    name = name.lower()
    if name == "gateio":
        spot = ccxt.gateio()
        fut  = ccxt.gateio({'options': {'defaultType': 'swap'}})  # perp USDT
    elif name == "binance":
        spot = ccxt.binance()
        fut  = ccxt.binanceusdm()
    else:
        spot = getattr(ccxt, name)()
        fut  = spot
    return spot, fut

EXCHANGE, EXCHANGE_FUT = build_exchanges(EXCHANGE_NAME)

TF_MAP = {"1m":"1m","3m":"3m","5m":"5m","15m":"15m","30m":"30m","1h":"1h","4h":"4h","1d":"1d"}

def fetch_ohlcv(sym: str, tf: str, limit: int = 300, futures: bool = False):
    ex = EXCHANGE_FUT if futures else EXCHANGE
    tf = TF_MAP.get(tf, "15m")
    try:
        return ex.fetch_ohlcv(sym, timeframe=tf, limit=limit)
    except Exception:
        # Gate perp padrão "BTC/USDT:USDT"
        if EXCHANGE_NAME == "gateio" and not futures:
            if ":" not in sym and sym.endswith("USDT"):
                alt = sym.replace("USDT", "USDT:USDT")
                return EXCHANGE_FUT.fetch_ohlcv(alt, timeframe=tf, limit=limit)
        if EXCHANGE_NAME == "gateio" and futures and ":" not in sym and sym.endswith("USDT"):
            alt = sym.replace("USDT", "USDT:USDT")
            return EXCHANGE_FUT.fetch_ohlcv(alt, timeframe=tf, limit=limit)
        raise

def last_closed(ohlcv: List[List[float]]) -> int:
    return ohlcv[-2][0] if len(ohlcv) >= 2 else ohlcv[-1][0]

def funding_rate(symbol: str) -> Optional[float]:
    """Tenta funding na exchange de futuros (Gate usa ex: BTC/USDT:USDT)."""
    try:
        sym = symbol
        if EXCHANGE_NAME == "gateio" and ":" not in sym and sym.endswith("USDT"):
            sym = sym.replace("USDT", "USDT:USDT")
        if getattr(EXCHANGE_FUT, "has", {}).get("fetchFundingRate"):
            fr = EXCHANGE_FUT.fetch_funding_rate(sym)
            return float(fr.get("fundingRate")) if fr else None
    except Exception as e:
        log.warning(f"Funding rate falhou {symbol}: {e}")
    return None

# ============================== INDICADORES ==============================
def ema(vals: List[float], n: int) -> List[float]:
    k = 2/(n+1)
    e = []
    for i, v in enumerate(vals):
        if i == 0: e.append(v)
        else: e.append(v*k + e[-1]*(1-k))
    return e

def rsi(vals: List[float], n: int = 14) -> List[float]:
    gains, losses = [0], [0]
    for i in range(1, len(vals)):
        ch = vals[i] - vals[i-1]
        gains.append(max(ch, 0)); losses.append(abs(min(ch, 0)))
    avg_g = sum(gains[:n])/n if len(vals)>n else 0
    avg_l = sum(losses[:n])/n if len(vals)>n else 0
    rsis = [50]*len(vals)
    for i in range(n, len(vals)):
        avg_g = (avg_g*(n-1)+gains[i])/n
        avg_l = (avg_l*(n-1)+losses[i])/n
        rs = (avg_g/avg_l) if avg_l != 0 else 999
        rsis[i] = 100 - (100/(1+rs))
    return rsis

def bbands(vals: List[float], n: int = 20, k: float = 2.0):
    if len(vals) < n: return [None]*len(vals), [None]*len(vals), [None]*len(vals)
    sma, stds = [], []
    for i in range(len(vals)):
        if i+1 < n: sma.append(None); stds.append(None)
        else:
            w = vals[i-n+1:i+1]; m = sum(w)/n
            var = sum((x-m)**2 for x in w)/n
            sma.append(m); stds.append(var**0.5)
    upper = [ (sma[i] + k*stds[i]) if sma[i] is not None else None for i in range(len(vals))]
    lower = [ (sma[i] - k*stds[i]) if sma[i] is not None else None for i in range(len(vals))]
    return upper, sma, lower

def atr(ohl: List[List[float]], n: int = 14) -> List[float]:
    if len(ohl) < n+1: return [0]*len(ohl)
    trs = [0]
    for i in range(1, len(ohl)):
        h,l,c1 = ohl[i][2], ohl[i][3], ohl[i-1][4]
        trs.append(max(h-l, abs(h-c1), abs(l-c1)))
    a = []
    cur = sum(trs[1:n+1])/n
    for i in range(len(ohl)):
        if i < n: a.append(0)
        elif i == n: a.append(cur)
        else:
            cur = (cur*(n-1)+trs[i])/n
            a.append(cur)
    return a

# ============================== SINAL (entrada/saída) ==============================
def build_signal(symbol: str, tf: str, use_bias: bool = True):
    """Retorna dict com 'side', 'entry', 'stop', 'tp1', 'tp2', 'text' ou None se neutro."""
    fut = False
    try:
        ohl = fetch_ohlcv(symbol, tf, futures=fut)
    except Exception:
        fut = True
        ohl = fetch_ohlcv(symbol, tf, futures=fut)

    closes = [c[4] for c in ohl]
    highs  = [c[2] for c in ohl]
    lows   = [c[3] for c in ohl]

    ema50  = ema(closes, 50)
    ema200 = ema(closes, 200)
    rs     = rsi(closes, 14)
    bb_u, bb_m, bb_l = bbands(closes, 20, 2.0)
    a      = atr(ohl, 14)

    i = len(closes) - 2  # vela fechada
    if i < 200 or bb_m[i] is None or a[i] == 0:
        return None

    trend_up = ema50[i] > ema200[i]
    trend_dn = ema50[i] < ema200[i]
    rsi_up   = rs[i-1] <= 50 and rs[i] > 50
    rsi_dn   = rs[i-1] >= 50 and rs[i] < 50
    brk_up   = closes[i] > bb_m[i] and closes[i] > highs[i-1]
    brk_dn   = closes[i] < bb_m[i] and closes[i] < lows[i-1]

    price = closes[i]
    side  = None
    if trend_up and (rsi_up or brk_up): side = "LONG"
    if trend_dn and (rsi_dn or brk_dn): side = "SHORT"

    bias_txt = ""
    if side and use_bias and fut:
        fr = funding_rate(symbol)
        if fr is not None:
            bias_txt = f" | funding {fr:.4%}"
            # funding muito positivo → contrarian (desfavorece LONG)
            if fr > 0.01 and side == "LONG": side = None
            if fr < -0.01 and side == "SHORT": side = None

    if not side:
        return None

    at = a[i]
    if side == "LONG":
        entry = price
        stop  = max(lows[i-1], entry - 1.2*at)
        tp1   = entry + 1.0*at
        tp2   = entry + 2.0*at
    else:
        entry = price
        stop  = min(highs[i-1], entry + 1.2*at)
        tp1   = entry - 1.0*at
        tp2   = entry - 2.0*at

    text = (f"✅ {side} {symbol} {tf}\n"
            f"Preço: {entry:.6f}\n"
            f"Stop:  {stop:.6f}\n"
            f"TP1:   {tp1:.6f} | TP2: {tp2:.6f}{bias_txt}")

    return {
        "side": side, "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
        "futures": fut, "tf": tf, "text": text
    }

# ============================== STATE & LOOPS ==============================
STATE: Dict[int, dict] = {}   # por chat
# Estrutura:
# STATE[chat] = {
#   "arb": {"network": str, "threshold": float, "tokens": List[str], "task": Task|None},
#   "sig": {
#       "watch": [(symbol, tf)],
#       "bias": bool,
#       "task": Task|None,
#       "last_bar": { "SYM:TF": ts },
#       "active": { "SYM:TF": {side, entry, stop, tp1, tp2, futures} }
#   }
# }

def ensure_state(chat: int):
    if chat not in STATE:
        net = os.getenv("REDE", "polygon").strip().lower()
        tokens = TOKEN_LISTS.get(net, POLYGON_TOKENS)
        STATE[chat] = {
            "arb": {"network": net, "threshold": THRESHOLD_DEFAULT, "tokens": tokens, "task": None},
            "sig": {"watch": [], "bias": True, "task": None, "last_bar": {}, "active": {}},
        }

# --------- loops
async def arb_loop(app, chat_id: int):
    while True:
        cfg = STATE[chat_id]["arb"]
        try:
            rows = summarize_spreads(cfg["network"], cfg["tokens"])
            header = f"📊 Top spreads — {cfg['network']} (limite {cfg['threshold']:.2f}%)"
            if not rows:
                await app.bot.send_message(chat_id, header + "\n(nenhum pool com 2+ preços agora)")
            else:
                lines = [header]
                for addr, pmin, pmax, spread, dexes in rows[:10]:
                    mark = "✅" if spread >= cfg["threshold"] else "➖"
                    dex_list = ", ".join(dexes[:5]) if dexes else "-"
                    lines.append(
                        f"{mark} `{addr}`\n"
                        f"  • min ${pmin:.4f} | max ${pmax:.4f} | *{spread:.2f}%*\n"
                        f"  • DEXs: {dex_list}"
                    )
                await app.bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await app.bot.send_message(chat_id, f"⚠️ Arb erro: {e}")
        await asyncio.sleep(INTERVAL_SEC)

async def signal_loop(app, chat_id: int):
    while True:
        cfg = STATE[chat_id]["sig"]
        msgs = []

        # verificar entradas novas
        for (sym, tf) in list(cfg["watch"]):
            try:
                # evita repetir no mesmo candle
                try:
                    ohl = fetch_ohlcv(sym, tf, futures=False)
                except Exception:
                    ohl = fetch_ohlcv(sym, tf, futures=True)
                ts = last_closed(ohl)
                key = f"{sym}:{tf}"
                if cfg["last_bar"].get(key) == ts:
                    pass
                else:
                    cfg["last_bar"][key] = ts
                    sig = build_signal(sym, tf, use_bias=cfg["bias"])
                    if sig:
                        cfg["active"][key] = {k: sig[k] for k in ("side","entry","stop","tp1","tp2","futures")}
                        msgs.append("📈 *Entrada encontrada*\n" + sig["text"])
            except Exception as e:
                msgs.append(f"⚠️ {sym} {tf}: {e}")

        # acompanhar saídas (TP/STOP)
        for key, pos in list(cfg["active"].items()):
            sym, tf = key.split(":")
            try:
                ohl = fetch_ohlcv(sym, tf, futures=pos["futures"], limit=2)
                price = ohl[-1][4]  # último close parcial
                side, tp1, tp2, stp = pos["side"], pos["tp1"], pos["tp2"], pos["stop"]
                exit_msg = None

                if side == "LONG":
                    if price >= tp2: exit_msg = f"🥳 *TP2 atingido* — {sym} {tf} @ {price:.6f} (saída total)"
                    elif price >= tp1: exit_msg = f"✅ *TP1 atingido* — {sym} {tf} @ {price:.6f} (realize parcial)"
                    elif price <= stp: exit_msg = f"🛑 *STOP* — {sym} {tf} @ {price:.6f}"
                else:
                    if price <= tp2: exit_msg = f"🥳 *TP2 atingido* — {sym} {tf} @ {price:.6f} (saída total)"
                    elif price <= tp1: exit_msg = f"✅ *TP1 atingido* — {sym} {tf} @ {price:.6f} (realize parcial)"
                    elif price >= stp: exit_msg = f"🛑 *STOP* — {sym} {tf} @ {price:.6f}"

                if exit_msg:
                    msgs.append(exit_msg)
                    # remove posição ao atingir STOP ou TP2; mantém após TP1
                    if "TP2" in exit_msg or "STOP" in exit_msg:
                        del cfg["active"][key]
            except Exception as e:
                msgs.append(f"⚠️ acompanhamento {key}: {e}")

        if msgs:
            await app.bot.send_message(chat_id, "\n".join(msgs), parse_mode="Markdown")
        await asyncio.sleep(SIGNAL_SEC)

# ============================== COMANDOS ==============================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    await update.message.reply_text("🤖 Bot pronto!\n• /startscan (arbitragem)\n• /startsignals (sinais)")

# ---- arbitragem
async def cmd_startscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    cfg = STATE[chat]["arb"]
    if cfg["task"] and not cfg["task"].done():
        cfg["task"].cancel()
    cfg["task"] = asyncio.create_task(arb_loop(context.application, chat))
    await update.message.reply_text("🟢 Scanner de arbitragem iniciado.")

async def cmd_stopscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    cfg = STATE[chat]["arb"]
    if cfg["task"]:
        cfg["task"].cancel(); cfg["task"] = None
    await update.message.reply_text("🔴 Scanner de arbitragem parado.")

async def cmd_setnetwork(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    if not context.args:
        await update.message.reply_text("Uso: /setnetwork <ethereum|polygon|...>")
        return
    net = context.args[0].strip().lower()
    tokens = TOKEN_LISTS.get(net, POLYGON_TOKENS)
    STATE[chat]["arb"].update({"network": net, "tokens": tokens})
    await update.message.reply_text(f"✅ Rede ajustada para: {net} | tokens: {len(tokens)}")

async def cmd_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    if not context.args:
        await update.message.reply_text("Uso: /threshold <valor_em_%>")
        return
    try:
        val = float(context.args[0])
        STATE[chat]["arb"]["threshold"] = val
        await update.message.reply_text(f"✅ Threshold ajustado para {val}%")
    except ValueError:
        await update.message.reply_text("Valor inválido. Ex.: /threshold 0.3")

# ---- sinais
async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /signal <SYMBOL> <TIMEFRAME> (ex: BTC/USDT 15m)")
        return
    sym = context.args[0].upper()
    tf = context.args[1]
    sig = build_signal(sym, tf, use_bias=STATE[chat]["sig"]["bias"])
    if sig:
        key = f"{sym}:{tf}"
        STATE[chat]["sig"]["active"][key] = {k: sig[k] for k in ("side","entry","stop","tp1","tp2","futures")}
        await update.message.reply_text("📈 *Entrada encontrada*\n" + sig["text"], parse_mode="Markdown")
    else:
        await update.message.reply_text("➖ Sem entrada no momento.")

async def cmd_autosignals_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /autosignals_add <SYMBOL> <TIMEFRAME>")
        return
    sym = context.args[0].upper()
    tf = context.args[1]
    STATE[chat]["sig"]["watch"].append((sym, tf))
    await update.message.reply_text(f"✅ Adicionado: {sym} {tf}")

async def cmd_autosignals_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    items = STATE[chat]["sig"]["watch"]
    if not items:
        await update.message.reply_text("Lista vazia.")
        return
    txt = "\n".join([f"{i}. {s} {t}" for i,(s,t) in enumerate(items)])
    await update.message.reply_text("Pares monitorados:\n" + txt)

async def cmd_autosignals_rm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    if not context.args:
        await update.message.reply_text("Uso: /autosignals_rm <index>")
        return
    try:
        idx = int(context.args[0])
        item = STATE[chat]["sig"]["watch"].pop(idx)
        await update.message.reply_text(f"🗑️ Removido: {item[0]} {item[1]}")
    except Exception:
        await update.message.reply_text("Índice inválido.")

async def cmd_startsignals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    cfg = STATE[chat]["sig"]
    if cfg["task"] and not cfg["task"].done():
        cfg["task"].cancel()
    cfg["task"] = asyncio.create_task(signal_loop(context.application, chat))
    await update.message.reply_text("🟢 Sinais iniciados.")

async def cmd_stopsignals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    cfg = STATE[chat]["sig"]
    if cfg["task"]:
        cfg["task"].cancel(); cfg["task"] = None
    await update.message.reply_text("🔴 Sinais parados.")

async def cmd_setbias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    if not context.args or context.args[0].lower() not in ("on","off"):
        await update.message.reply_text("Uso: /setbias on|off (viés de funding em futuros)")
        return
    STATE[chat]["sig"]["bias"] = (context.args[0].lower() == "on")
    await update.message.reply_text(f"✅ Bias funding: {'on' if STATE[chat]['sig']['bias'] else 'off'}")

async def cmd_setexchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global EXCHANGE_NAME, EXCHANGE, EXCHANGE_FUT
    if not context.args:
        await update.message.reply_text("Uso: /setexchange gateio|binance")
        return
    name = context.args[0].lower()
    EXCHANGE_NAME = name
    EXCHANGE, EXCHANGE_FUT = build_exchanges(name)
    await update.message.reply_text(f"✅ Exchange ajustada para: {name}")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    ensure_state(chat)
    arb = STATE[chat]["arb"]; sig = STATE[chat]["sig"]
    await update.message.reply_text(
        f"Exchange: {EXCHANGE_NAME}\n"
        f"Arb — rede: {arb['network']} | tokens: {len(arb['tokens'])} | limite: {arb['threshold']}%\n"
        f"Sinais — pares: {len(sig['watch'])} | bias funding: {'on' if sig['bias'] else 'off'} | ativos: {len(sig['active'])}"
    )

# ============================== MAIN ==============================
def main():
    if not TOKEN:
        raise RuntimeError("Defina TELEGRAM_TOKEN no Railway!")
    app = ApplicationBuilder().token(TOKEN).build()

    # básicos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))

    # arbitragem
    app.add_handler(CommandHandler("startscan", cmd_startscan))
    app.add_handler(CommandHandler("stopscan", cmd_stopscan))
    app.add_handler(CommandHandler("setnetwork", cmd_setnetwork))
    app.add_handler(CommandHandler("threshold", cmd_threshold))

    # sinais
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("autosignals_add", cmd_autosignals_add))
    app.add_handler(CommandHandler("autosignals_list", cmd_autosignals_list))
    app.add_handler(CommandHandler("autosignals_rm", cmd_autosignals_rm))
    app.add_handler(CommandHandler("startsignals", cmd_startsignals))
    app.add_handler(CommandHandler("stopsignals", cmd_stopsignals))
    app.add_handler(CommandHandler("setbias", cmd_setbias))
    app.add_handler(CommandHandler("setexchange", cmd_setexchange))
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
