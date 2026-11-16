---
source: 0d5db0165c75f4671bf7c09cfef1394b9bfa9188
description: "Discord なしで Bot をテストします。ローカルのフェイクのゲートウェイ、REST API、ボイスサーバーで動かし、インタラクションやイベントを送り、送信内容を確認します。"
---

# Bot のテスト

![チェックマークが現れるまで虫眼鏡で探す Bostie](../assets/mascot/bostie-detective.webp){ align=right width="180" loading=lazy }

`bostie.testing` は localhost 上でフェイクの Discord を動かします。中身はゲートウェイ、Bot がよく使うルートを備えた REST API、そして必要に応じてボイスサーバーです。実際の Bot がこれに接続し、テストは Discord 側を操作します。つまり、イベントやインタラクションを送り、Bot が送り返したものを確認します。`testing` extra が必要で（[インストール](../install.md)）、bostie 自身のテストスイートもこの上で動いています。

```python
--8<-- "docs/snippets/testing/test_greeter.py"
```

`run(scenario)` は `FakeDiscord` を起動し、それに対して `async def scenario(fake)` を 10 秒以内に実行してから停止します。通常の関数なので、pytest にプラグインは必要ありません。`async with running(bot, fake):` はフェイク上で Bot を起動し、準備ができるまで待ち、最後に Bot を閉じます。Bot の接続先をフェイクにするには `Bot(api_url=fake.api_url)` を使うか、Bot がほかの場所で組み立てられる場合は `BOSTIE_API_URL` を設定します。

## Discord 側を操作する { #driving-discords-side }

| 呼び出し | 動作 |
|---|---|
| `await fake.interaction(type, data)` | `INTERACTION_CREATE` を送り、Bot の最初の応答を `Recorded` リクエストとして返します |
| `await fake.dispatch(name, d, shard=0)` | 任意のゲートウェイイベントを送り、そのシーケンス番号を返します |
| `fake.expect_callback(payload)` | 自分でディスパッチしたインタラクションについて、その最初の応答を待つ future を返します |
| `await fake.send_op(op, d)` | 7（reconnect）や 9（invalid session）のような、ディスパッチ以外のオペコードを送ります |
| `await fake.close_gateway(code)`、`fake.drop_gateway()` | クローズコード付きで接続を閉じるか、コードなしで接続を切ります |
| `fake.hold_acks()` | 接続が死んだときのように、ハートビートへの応答を止めます |
| `await fake.next_identify()`、`await fake.next_resume()` | Bot の次の IDENTIFY または RESUME を待ちます |

インタラクションの種類は Discord の番号で、コマンドは 2、コンポーネントは 3、オートコンプリートは 4、モーダルの送信は 5 です。

4007 のように新しいセッションが始まるクローズの後、Bot は `HTTPSettings.identify_spacing` 秒待ってから再び Identify を行います。既定値は Discord が求めるとおり 5 秒です。テストでは `Bot(http_settings=HTTPSettings(identify_spacing=0))` でこの待ち時間を省けます。

`bostie.testing.payloads` は、Discord のドキュメントにあるサンプルから、新しい ID 付きで Discord のペイロードを組み立てます。

- コマンドとオートコンプリートには `command(name, *options)`、`option(name, type, value, focused=False)`、`subcommand(...)`、`group(...)`、`resolved()`。
- ユーザーコマンドとメッセージコマンドには `context_menu(type, name, target_id, resolved)`。
- コンポーネントとモーダルには `button(custom_id)`、`select(custom_id, *values)`、`modal(custom_id)`。
- イベントには `message(...)`、`guild(...)`、`member(...)`、`user(...)`、`role(...)`、`channel(...)`、`voice_channel(...)`、`stage_channel(...)`、`voice_state(...)`。どれもキーワード引数でフィールドを上書きできます。
- bostie が同梱しているドキュメント記載のペイロードには `fixture(name)`、新しい ID には `snowflake()`。

## Bot が送ったものを確認する { #checking-what-the-bot-sent }

`fake.requests` はすべての REST リクエストを `Recorded` として列挙します。`fake.expect_request(method, route_prefix)` は、次に一致するリクエストを待つ future を返します。これは、タスクから送るリクエストのように、Bot が自発的に行うリクエストに使います。`Recorded` は `method`、`path`（API のベースからの相対パス）、`query`、`headers`、`json`、そしてアップロードの場合は `parts` を持ちます。インタラクションへの応答である `Recorded` は、上の例のように、コールバックの本文を `json` に持ちます。`fake.gateway_received` は Bot がゲートウェイで送ったペイロードのキューで、`fake.connections` はゲートウェイ接続です。各接続は `Connection` オブジェクトで、受け取ったペイロードの `received`、`shard`、`close_code` を持ちます。

`fake.cdn_file(path, data)` は、Bot がダウンロードする添付ファイルやアセットのために、返した CDN URL で `data` を配信します。`fake.cdn_requests` はそのダウンロードを記録します。

フェイクは、これらのルートが必要とする状態を保持しており、テストから読み取ったり、あらかじめ投入したりできます。`fake.guilds`、`fake.channels`、`fake.users`、`fake.members`、`fake.messages`、`fake.commands`、`fake.guild_commands` があり、コマンドの同期回数は `fake.put_count` でわかります。`fake.ready_guilds` は、`READY` に載せるサーバー ID を設定します。

## 失敗とタイミング { #failures-and-timing }

- `fake.fail(method, route_prefix, status, json)` は、次に一致するリクエストにエラーで応答します。フェイクが提供していないルートのスタブにもなります。
- `fake.rate_limit(method, route_prefix, scope="user", retry_after=0.1)` は 429 で応答します。
- `fake.answer(method, route_prefix, body, content_type)` は JSON ではない本文で応答します。
- `fake.hold(method, route_prefix)` は、返された `asyncio.Event` をセットするまでリクエストを待たせます。`fake.hold_identify(shard)` と `fake.hold_hello()` は、ゲートウェイのハンドシェイクに対して同じことを行います。
- `await settle()` はイベントループに何度か制御を譲り、直前の操作でスケジュールされたコールバックを実行させます。

Bot がリスナーの中で行うことを待つには、sleep するのではなく、2 つ目のテストのように Bot に[独自のイベントをディスパッチ](events.md#events-of-your-own)させ、それを待ちます。

`FakeDiscord(compress=True)` はゲートウェイを zstd で圧縮します。これには Python 3.14 の `compression.zstd` が必要です。`heartbeat_interval` はフェイクが要求する間隔を設定し、既定値は Discord と同じ 41 250 ms です。

## ボイス { #voice }

`FakeDiscord(voice=True)` は、ボイスチャンネルとステージチャンネルを追加し、ボイス状態の更新に応答し、ボイスゲートウェイと UDP サーバーを `fake.voice` として動かします。そのため、`ctx.voice.connect()` や `vc.play(...)` をフェイクに対して実行できます。DAVE には対応していません。DAVE は bostie の Rust のテストでカバーしています。テストでは次のことができます。

- Bot が送ったものを `await fake.next_voice_state_update()`、`await fake.voice.next_identify()`、`await fake.voice.wait_for_packets(count)` で待ちます。`wait_for_packets` は復号した RTP パケットを `Packet` オブジェクトとして返します。各ボイス接続は `fake.voice.sessions` 内の `VoiceSession` で、`VoiceSocket` の上にあります。`fake.voice.op(op, d)` は最新の接続に任意のボイスオペコードを送ります。
- ほかのユーザーとして振る舞います。`fake.voice.clients_connect(user_ids)`、`fake.voice.client_disconnect(user_id)`、`fake.voice.speaking(user_id, ssrc, flags)`、Bot に Opus パケットを送る `fake.voice.send_audio(...)`、Bot に RTCP パケットを送る `fake.voice.send_rtcp(packet)`、壊れたサーバーのように暗号化せずにバイト列を送る `fake.voice.send_datagram(data)` を使います。
- モデレーターのように Bot を移動または切断します。`fake.move_bot(channel_id)` と `fake.kick_bot()` を使います。
- チャンネルが満員のときの Discord のように、次のボイス状態の更新に応答しないままにします。`fake.hold_voice_state()` を使い、返された `asyncio.Event` がセットされるまで保留します。
- Bot について送るボイス状態の `fake.mute`、`fake.deaf`、`fake.suppress` と、参加時に Discord が送る 2 つのイベントの順序を決める `voice_order` を設定します。
