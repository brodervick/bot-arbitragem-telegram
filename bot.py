# bot_signals.py
# Bot de sinais para 20 pares da Gate.io (15m)
# Usa SMA20 + ATR14 para gerar entradas LONG/SHORT

import os
import math
import requests
import pandas as pd
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Token do Telegram (defina como variável de ambiente TELEGRAM_TOKEN no Railway)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Pares padrão (20 pares top da Gate.io em USDT)
DEFAULT_PAIRS = [p.strip() for p in os.getenv(
    "PAIRS",
    "BTC_USDT,ETH_USDT,SOL_USDT,BNB_USDT,XRP_USDT,ADA_USDT,DOGE_USDT,TRX_USDT,AVAX_USDT,"
    "MATIC_USDT,DOT_USDT,LTC_USDT,SHIB_USDT,UNI_USDT,LINK_USDT,XLM_USDT,ATOM_USDT,ETC_USDT,APT_USDT,NEAR_USDT"
).split(",") if p.strip()]

# Gate.io API
CANDLE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"


def fetch_klines(pair: str, interval: str = "900", limit: int = 120):
    """Busca candles 15m (interval=900 segundos) e retorna DataFrame"""
    try:
        r = requests.get(CANDLE_URL, params={
            "currency_pair": pair, "interval": interval, "limit": str(limit)
        }, timeout=20)
        r.raise_for_status()
        data = r.json()
        cols = ["ts", "volume", "close", "high", "low", "open"]
        df = pd.DataFrame(data, columns=cols)
        for c in ["close", "high", "low", "open", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.sort_values("ts").reset_index(drop=True)
        return df
    except Exception:
        return None


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def simple_atr(df: pd.DataFrame, n: int = 14) -> float:
    high, low, close = df["high"].values, df["low"].values,
