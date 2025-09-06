async def scanner_loop(app, chat_id: int):
    while True:
        cfg = STATE.get(chat_id)
        if not cfg:
            return
        tokens, network, threshold = cfg["tokens"], cfg["network"], cfg["threshold"]
        try:
            rows = summarize_spreads(network, tokens)

            # Mensagem principal
            lines = [f"📊 *Top spreads — {network}* (limite {threshold:.2f}%)"]

            for addr, pmin, pmax, spread, dexes in rows[:10]:
                mark = "✅" if spread >= threshold else "➖"
                lines.append(
                    f"{mark} `{addr}`\n"
                    f"  • min ${pmin:.4f} | max ${pmax:.4f} | *{spread:.2f}%*\n"
                    f"  • DEXs: {', '.join(dexes[:5])}"
                )

            await app.bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")

        except Exception as e:
            await app.bot.send_message(chat_id, f"⚠️ Erro: {e}")

        await asyncio.sleep(INTERVAL_SEC)
