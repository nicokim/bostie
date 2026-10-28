# Voice

## Voice server changes

Discord sometimes moves a call to another voice server, for example when someone changes the channel's region or when a server goes away. Everyone in the call reconnects at the same moment. bostie follows on its own: the connection goes `RECONNECTING`, comes back `READY` on the new server, and the audio held in its sinks plays from there.

A Discord client that is still connecting to the new server when the bot gets there may never play the bot. The bot is connected and sends audio, but that listener hears nothing until the bot connects again. Waiting a moment before connecting to the new server avoids this:

```python
vc = await bot.voice.connect(channel_id, server_change_delay=0.5)
```

or, for every connection:

```python
bot = Bot(voice=voice.Defaults(server_change_delay=0.5))
```

`server_change_delay` is in seconds. `0.5` is enough for the clients we have seen this on. The default, `0`, connects at once, which is fastest when nobody is affected.

The wait applies only to a server change. A first connect and a move to another channel connect at once, and the wait does not count against `timeout`.
