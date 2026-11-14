# Interactions over HTTP

`app = bostie.ASGIApp(bot)` is an ASGI app for the Interactions Endpoint URL and the Webhook Events URL of the Developer Portal. Run it with any ASGI server, such as `uvicorn mybot:app`, instead of `bot.run()`:

- It checks each request's signature with the app's public key, answers Discord's PINGs, and runs commands, components and modals with the same routers and contexts as over the gateway.
- Webhook events, such as `ApplicationAuthorized`, reach `@bot.listen`, `bot.wait_for` and `bot.stream`. A bot on the gateway can serve them through the same app.
- The server's lifespan starts the bot's REST, without a gateway, sends `Starting`, syncs commands and starts the bot's tasks; at shutdown it closes the bot, which sends `Stopped`. Mounted in another framework, call `await app.start()` and `await app.close()` from that framework's lifespan.
- Without a gateway there is no voice, `bot.cache` stays empty, gateway events never arrive, `Started` never fires and `await bot.wait_until_ready()` never returns.
