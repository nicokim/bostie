---
source: 4108c4a61370aa8ef893c1d65371c3d654e43c09
description: "Discord のボイスチャンネルとステージチャンネルに参加します。接続設定、DAVE のエンドツーエンド暗号化、ボイスイベント、メディアの停滞、外部ボイスエンジンを扱います。"
---

# ボイス接続

![ヘッドホンで音を聴く Bostie](../assets/mascot/bostie-listen.webp){ align=right width="180" loading=lazy }

bostie のボイスエンジンは Rust コアの中で動作します。ボイス WebSocket、UDP トランスポート、20 ms の送信クロック、デコードとリサンプリング、ミキサー、Opus のエンコードとデコード、トランスポート暗号化、DAVE によるエンドツーエンド暗号化はすべてそこで処理されます。Python はエンジンに参加、再生、受信を指示するだけです。Python が各シンクのバッファーを満たしておく必要がある点を除けば、タイミングがイベントループに左右されることはありません。

ボイスには `Intents.GUILD_VOICE_STATES` が必要で、これは `Intents.DEFAULT` に含まれています。Bot が持てるボイス接続は、サーバーごとに 1 つまでです。

```python
--8<-- "docs/snippets/voice/voice_connection.py"
```

## 接続 { #connecting }

`await ctx.voice.connect()` は、コマンドを実行したユーザーがいるボイスチャンネルまたはステージチャンネルに参加し、音声を双方向に流せるようになった時点で戻ります。ユーザーのチャンネルはキャッシュから探し、サーバーがキャッシュされていなければ Discord に問い合わせます。ユーザーがそのサーバーのどのボイスチャンネルにもいない場合や、インタラクションが DM から来た場合は `NotInVoice` を送出します。`ctx.voice.current` は、そのサーバーにおける Bot の接続、または `None` です。

ハンドラーの外では、`await bot.voice.connect(channel_id)` で ID を指定してチャンネルに参加します。Bot がすでに接続しているサーバーでは、接続を移動し、指定したオプションだけを適用します。`bot.voice.get(guild_id)` と `bot.voice.connections` は開いている接続を返し、`await bot.voice.close()` は `bot.close()` と同じように、すべての接続から退出します。

`timeout` 秒以内に準備が整わない参加は、理由 `TIMEOUT` の `VoiceConnectError` を送出します。満員のチャンネルや、Bot に接続権限のないチャンネルへの参加には、Discord は応答を返しません。その他の失敗では、理由、クローズコード、Discord のメッセージを持つ `VoiceConnectError` が送出されます。

## 設定 { #settings }

`Bot(voice=voice.Defaults(...))` はすべての接続の初期設定を決め、`connect()` は 1 回の呼び出しに対する接続設定を受け取ります。

| `voice.Defaults` のフィールド | 既定値 | 意味 |
|---|---|---|
| `self_mute`、`self_deaf` | `False` | Bot 自身のミュートとスピーカーミュート |
| `timeout` | 10.0 | 参加、移動、編集を待つ秒数 |
| `server_change_delay` | 0.0 | Discord に従って新しいボイスサーバーへ移る前に待つ秒数 |
| `encoder` | `EncoderSettings()` | Opus エンコーダー。[音声の再生](playback.md#encoder)を参照 |
| `speaking_flags` | `MICROPHONE` | Bot が再生するときに通知する `voice.SpeakingFlags` |
| `stage` | `"listen"` | ステージ上での役割。`"listen"`、発言をリクエストする `"request"`、または `"speak"` |
| `speech_hangover` | 0.2 | 話者が話し終えたとみなすまでの無音の秒数。0.02〜10 |
| `speech_activity` | `True` | 発話の開始と終了のイベントを発生させるかどうか |
| `stall_after` | 2.0 | メディアの流れが止まってから `MediaStalled` が報告するまでの秒数。0.02 以上 |
| `sink_buffer`、`stall` | 0.2, `"hold"` | すべての[シンク](sinks.md)の既定値 |
| `receive_buffer`、`jitter`、`overflow`、`receive_stall` | 2.0, 0.1, `"drop_oldest"`、`"wait"` | すべての[レシーバー](receiving.md)の既定値 |
| `crossfade`、`history` | 0.0, 0 | すべての[キュー](playback.md#queues)の既定値 |

`connect()` は `self_mute`、`self_deaf`、`timeout`、`server_change_delay`、`encoder`、`speaking_flags`、`stage`、`speech_hangover`、`speech_activity`、`stall_after` をキーワード引数として受け取ります。省略した引数や `None` を渡した引数は既定値のままです。`connect_external()` は最初の 3 つを、`move()` は `timeout` を、`edit()` は `self_mute`、`self_deaf`、`timeout` を受け取ります。不正な値は、`Defaults` を作成した時点で `ValueError` を送出します。文字列の設定にはリテラル型 `voice.StageRole`、`voice.SinkStall`、`voice.ReceiveStall`、`voice.Overflow`、`voice.Sample`、`voice.OpusApplication`、`voice.OpusBandwidth`、`voice.OpusSignal` があります。`ctx.voice` は `voice.ContextVoice`、`bot.voice` は `voice.VoiceManager` です。

## 接続オブジェクト { #the-connection }

| `VoiceConnection` | 値 |
|---|---|
| `guild_id`、`channel_id` | 接続している場所 |
| `state` | `voice.State`。`CONNECTING`、`READY`、`RESUMING`、`RECONNECTING`、`DISCONNECTED` のいずれか |
| `members` | 通話にいる他のユーザーの ID |
| `speaking` | 現在話しているユーザーと、話し始めた時点の `time.monotonic()` の対応 |
| `ssrcs` | 通話内の各 SSRC とそのユーザー ID の対応 |
| `self_mute`、`self_deaf` | Discord が適用した、Bot 自身のミュートとスピーカーミュート |
| `mute`、`deaf`、`suppress` | モデレーターが Bot をミュートまたはスピーカーミュートしたかどうか、あるいはこのステージで Bot が発言できないかどうか |
| `latency` | WebSocket と UDP の往復時間（秒）を持つ `Latency`。計測されるまでは `None` |
| `dave` | 通話のエンドツーエンド暗号化。後述 |
| `sinks` | 開いているシンク。古い順 |
| `encoder`、`speaking_flags`、`speech_hangover`、`speech_activity`、`stall_after` | 接続中に変更可能 |

- `await vc.move(channel_id)` はサーバー内の別のボイスチャンネルまたはステージチャンネルに移動し、移動先でメディアが流れ始めた時点で戻ります。
- `await vc.edit(self_mute=..., self_deaf=...)` は Bot 自身のミュートまたはスピーカーミュートを変更し（指定しなかったほうはそのまま）、Discord が適用した時点で戻ります。
- `await vc.stage("request")` はステージで発言をリクエストし、`"speak"` は（権限があれば）発言し、`"listen"` はオーディエンスに戻ります。
- `await vc.wait_until(voice.State.READY)` は特定の状態になるまで待ち、先に接続が終了した場合は `VoiceNotConnected` を送出します。
- `vc.reconnect()` はボイス WebSocket を切断し、新しい WebSocket で再開します。
- `vc.stop()` はすべてのシンクを閉じるので、何も再生されなくなります。
- `await vc.disconnect()` はチャンネルから退出し、接続が終了した時点（最大 2 秒後）で戻ります。

終了した接続に対する呼び出しは `VoiceNotConnected` を送出します。

接続は自己修復します。切断された WebSocket は再開し、失われたセッションは再接続します。モデレーターが Bot を移動した場合は新しいチャンネルに追従し、`Moved` イベントを送ります。モデレーターが Bot を切断したりチャンネルを削除したりすると、接続は理由 `REMOVED` で終了します。サーバーミュートされている間やステージで発言を抑制されている間に Bot が再生したとき、またスピーカーミュート中にレシーバーが待機しているとき、bostie は警告を出します。

## エンドツーエンド暗号化 { #end-to-end-encryption }

bostie は、Discord のボイス用エンドツーエンド暗号化である DAVE に、設定なしで対応しています。通話の暗号化グループに参加し、送信する音声を暗号化し、受信した音声を復号します。`vc.dave` はその状態を `voice.Dave` として報告します。

- `protocol_version` は使用中の DAVE プロトコルで、使っていなければ 0 です。
- `epoch` はグループのメンバーが変わった回数を数えます。
- `privacy_code` は、Discord クライアントがその通話について表示するコードです。
- `await vc.dave.verification_code(user_id)` は、他のユーザーと通話の外で照合するためのコードを返します。これにより、間に第三者がいないことを確認できます。

`DaveTransition` イベントは変更のたびに報告されます。DAVE を必須とするチャンネルに、DAVE に対応していないクライアントから接続すると、理由 `DAVE_REQUIRED` で切断されます。DAVE の処理に失敗すると `DaveError` が送出されるか、接続が `DAVE_FAILED` で終了します。

## イベント { #events }

ボイス接続のイベントは `@bot.listen` を通じて届きます。どれも `guild_id` を持つ `voice.VoiceEvent` です。`DISCONNECTED` への `StateChange` は `voice.DisconnectReason` を持ちます。値は `REQUESTED`（`disconnect()` が実行された）、`REMOVED`（モデレーターが Bot を切断した、またはチャンネルを削除した）、`CALL_TERMINATED`、`RATE_LIMITED`、`TIMEOUT`、`SESSION_INVALID`、`AUTHENTICATION_FAILED`、`DAVE_REQUIRED`、`DAVE_FAILED`、`PROTOCOL_ERROR`、`CLOSED`（Bot が終了した）のいずれかです。

| イベント | フィールド | 送られるタイミング |
|---|---|---|
| `StateChange` | `old`、`new`、`reason`、`code`、`detail` | 接続の状態が変わったとき |
| `Moved` | `old_channel_id`、`new_channel_id`、`by_us` | `move()` またはモデレーターによって Bot が移動したとき |
| `DaveTransition` | `protocol_version`、`epoch`、`transition_id` | エンドツーエンド暗号化が変わったとき |
| `MediaStalled`、`MediaResumed` | `direction`（`voice.Direction.SEND` または `RECEIVE`）, `duration` | 送信または受信が止まったとき、または再開したとき |
| `UserJoin`、`UserLeave` | `user_id` | ユーザーが通話に参加または退出したとき |
| `SpeakingUpdate` | `user_id`、`ssrc`、`flags` | ユーザーが自分の SSRC と発話フラグを通知したとき |
| `SpeechStart`、`SpeechStop` | `user_id`、`ssrc`、`at`、終了時は `duration` も | ユーザーが話し始めたとき、または話し終えたとき |
| `RtcpPacket` | `packet_type`、`count`、`ssrc`、`user_id`、`reports`、`data` | ボイスサーバーが RTCP パケットを送ったとき。`reports` 内の各 `ReceptionReport` は、1 つのストリームの `fraction_lost`、`lost`、`highest_sequence`、`jitter`、`last_sender_report`、`delay` を持ちます |

これらは `bostie.voice` にあります。`SpeechStart` と `SpeechStop` は、`speech_activity` がオンの間だけ発生します。

## メディアの停滞 { #stalled-media }

```python
--8<-- "docs/snippets/voice/media_stalls.py"
```

`direction` が `SEND` の `MediaStalled` は、再接続中や DAVE の切り替え中などに、シンクで待機している音声が `stall_after` 秒のあいだ送り出せないときに発生します。`RECEIVE` の場合は、開いているレシーバーにパケットが届いているのに、その間どれも開くことも再生することもできない状態です。メディアが再び流れると `MediaResumed` が続き、その `duration` は停滞が始まった時点から数えます。`stall_after` は、`voice.Defaults`、`connect(stall_after=...)`、`vc.stall_after` で別の値を指定しない限り 2 秒です。

## 空のチャンネルからの退出 { #leaving-an-empty-channel }

Bot は切断するまでチャンネルに残ります。最後のユーザーが退出したときに一緒に退出するには、次のようにします。

```python
--8<-- "docs/snippets/voice/leave_when_alone.py"
```

## エラー { #errors }

ボイスのエラーは `VoiceError` です。`VoiceConnectError`（準備が整う前に参加が終了した。`reason`、`code`、`detail` を持つ）、`NotInVoice`、`VoiceNotConnected`、`AlreadyConnected`、`VoiceQueueFull`（接続を待っているコマンドが多すぎる）、`DaveError`、そして音声関連の `SourceError`、`SinkClosed`、`BufferFull`、`Empty`、`ReceiverClosed` があります。音声関連のエラーは次のページ以降で説明します。

## 外部のボイスエンジン { #external-voice-engines }

```python
--8<-- "docs/snippets/voice/external_engine.py"
```

`await bot.voice.connect_external(guild_id, channel_id)` は、別の場所で動くボイスエンジンのためにチャンネルに参加し、エンジンが接続に使う `state`（`VoiceStateUpdate`）と `server`（`VoiceServerUpdate`）を持つ `ExternalVoice` を返します。以降、bostie はそのサーバーで音声を送信しません。`async with external.updates() as updates:` はそのサーバーの以降のボイスサーバーと Bot 自身のボイス状態を届け、`await external.disconnect()` はチャンネルから退出します。bostie が独自の接続を動かしているサーバーでは `AlreadyConnected` を送出します。引数として `self_mute`、`self_deaf`、`timeout` を受け取ります。

## バージョン { #versions }

`voice.versions()` は、bostie、libopus、DAVE ライブラリのバージョンと、ビルドに組み込まれた最も新しい DAVE プロトコルのバージョンを返します。
