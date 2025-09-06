import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Pegando o token do Telegram (variável de ambiente)
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Endpoint da Gate.io para preço atual
GATE_IO_URL = "https://api.gateio.ws/api/v4/spot/tickers"

# Função para consultar preço
def get_price(symbol="BTC_USDT"):
    try:
        resp = requests.get(GATE_IO_URL, params={"currency_pair": symbol})
        data = resp.json()
        return float(data[0]["last"])
    except Exception as e:
        return f"Erro ao buscar preço: {e}"

# /start - boas-vindas
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Bot de sinais Gate.io ativo!\nUse /sinal BTC_USDT para ver sinal.")

# /sinal - mostra sinal simples
async def sinal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("Digite o par, ex: /sinal BTC_USDT")
        return

    pair = context.args[0].upper()
    price = get_price(pair)

    if isinstance(price, str):  # erro
        await update.message.reply_text(price)
        return

    # Estratégia simples: alerta se preço está alto ou baixo
    mensagem = f"📊 {pair}\n💰 Preço atual: {price:.2f} USDT"

    if price > 70000 and "BTC" in pair:
        mensagem += "\n⚠️ Sinal: POSSÍVEL VENDA"
    elif price < 60000 and "BTC" in pair:
        mensagem += "\n⚠️ Sinal: POSSÍVEL COMPRA"
    else:
        mensagem += "\nℹ️ Sinal neutro"

    await update.message.reply_text(mensagem)

# Main
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sinal", sinal))

    print
