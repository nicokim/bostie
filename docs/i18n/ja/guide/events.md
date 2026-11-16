---
source: 0402229d2b242ba10aed929b9dedd0087f57b406
description: "型付きリスナーで Discord のゲートウェイイベントを受け取り、待機やストリームで扱い、生のイベントや独自のイベントを使います。それぞれに必要なインテントも解説します。"
---

# イベント

![鳴っているベルにあいさつする Bostie](../assets/mascot/bostie-events.webp){ align=right width="180" loading=lazy }

Discord は、ゲートウェイイベントを通じて、何が起きたかを Bot に伝えます。メッセージが送信された、メンバーが参加した、ロールが変更された、といったことです。リスナーは、イベントを 1 つ受け取る非同期関数です。

```python
--8<-- "docs/snippets/events/listeners.py"
```

`@bot.listen` は最初のパラメーターの型ヒントを読み取り、そのイベントクラス、またはユニオンに含まれる各クラスに対して関数を登録します。型ヒントのない関数や、より広い型ヒントを持つ関数には、`@bot.listen(MessageUpdate)` のようにクラスを直接指定します。クラスは `bostie.events` にあり、[イベント一覧](../reference/event-list.md)にすべてのクラスとそのインテント、フィールドが載っています。[ルーター](routers.md)にも同じデコレーターがあります。

各リスナーは独自のタスクとして実行されるので、遅いリスナーが他のリスナーや次のイベントを待たせることはありません。タスクはすぐに開始されます。つまり、リスナーのコードのうち最初の `await` までは、次のイベントが処理される前に実行されます。例外は `Starting` と `Stopped` のリスナーで、`start()` と `close()` がこれらを 1 つずつ順に await します。`bot.remove_listener(function)` は、関数が待ち受けているすべてのイベントからその関数の登録を解除します。

## 待ち受けるものだけ { #only-what-you-listen-to }

bostie は Rust コアに対し、Bot の中の何かが使っているディスパッチ名だけを転送するよう求めます。対象は、あなたのリスナー、キャッシュ、コマンドとコンポーネントのルーティング、そして待機中の処理です。それ以外のイベントはすべて Python に届く前に破棄されるので、何も待ち受けなければ何のコストもかかりません。型付きのイベントは、ディスパッチごとに一度だけ、しかも必要とされるときにだけデコードされます。

## インテント { #intents }

Discord はほとんどのイベントを、そのインテントを要求した Bot にしか送りません。`Bot(intents=...)` の既定値は `Intents.DEFAULT` で、3 つの特権インテントを除くすべてのインテントです。Bot のどのインテントでも届かないイベントに対してリスナーを登録すると、bostie は追加すべきインテントの名前を挙げた警告をログに出力し、特権インテントは Developer Portal でも有効にする必要があることを知らせます。詳しくは[インテント](gateway.md#intents)で説明しています。

## 古い値とキャッシュ { #old-values-and-the-cache }

更新イベントは、変更前に[キャッシュ](cache.md)が保持していたオブジェクトを `old` として持ち、削除イベントは削除されたオブジェクトを持ちます。`RoleUpdate.old`、`ScheduledEventUpdate.old`、`MessageDelete.message`、`MemberRemove.member` などです。キャッシュがそのオブジェクトを保持していなかった場合は `None` になります。メッセージは `Bot(max_messages=...)` を指定したときだけ、メンバーは `members=True` のときだけ、プレゼンスは `presences=True` のときだけキャッシュされます。

## イベントを待つ { #waiting-for-an-event }

```python
--8<-- "docs/snippets/events/waiting.py"
```

`await bot.wait_for(Event, check=None)` は、そのクラスのイベントのうち `check` が真を返す次のイベントを返します。`async with bot.stream(Event, check=None) as events:` は、ブロックの実行中に一致したすべてのイベントを `async for` 用にキューに入れます。ループ本体の実行中に届いたイベントも失われません。どちらにも独自のタイムアウトはないので、`asyncio.timeout()` で上限を設けてください。`check` が送出した例外は、待機の側から送出されます。`bot.wait_for_component` と `bot.wait_for_modal` は、[コンポーネントの使用](components.md#waiting-for-a-click-inside-a-handler)と[モーダルの送信](modals.md#waiting-for-the-submit-inside-a-handler)に対して同じことを行います。

## 生のイベントと、bostie がモデル化していないイベント { #raw-events-and-events-bostie-does-not-model }

```python
--8<-- "docs/snippets/events/custom_events.py"
```

`@bot.listen_raw("NAME", ...)` は、指定した各ディスパッチ名について、モデル化されているかどうかにかかわらず `RawEvent` を届けます。`RawEvent` を型ヒントに持つリスナーは、すべてのディスパッチを受け取ります。`RawEvent` は、ディスパッチの `name`、シーケンス番号 `seq`、そしてペイロードを `raw`（毎回新しい `dict`）または `raw_json`（バイト列）として持ちます。型付きのイベントもすべて `raw` と `raw_json` を持っています。

Discord が、ドキュメントで必須とされているフィールドがないなど、クラスに合わないペイロードを送ってきた場合、bostie はイベント名ごとに一度だけ警告をログに出力します。型付きのリスナーはそのイベントをスキップしますが、`RawEvent` のリスナーには引き続き届きます。

## 独自のイベント { #events-of-your-own }

`kw_only=True` を指定して `BotEvent` をサブクラス化し、そのインスタンスを `bot.dispatch(event)` で送ります。そのリスナー、待機、ストリームは、ほかのイベントと同じようにそれを受け取ります。`NAME` を持たないサブクラスには、モジュール名とクラス名に基づいた名前が付きます。イベントクラスはフリーズされた `msgspec` の構造体なので、フィールドはアノテーションとして宣言します。

## ライフサイクルとシャードのイベント { #lifecycle-and-shard-events }

bostie は Bot 自身についての独自のイベントを送ります。[ライフサイクル](running.md#the-lifecycle)の前後には `Starting`、`Started`、`Stopping`、`Stopped`、各[シャード](sharding.md)については `ShardReady`、`ShardDisconnect`、`ShardStop`、`ShardBackpressure`、失敗については `ListenerError` と `TaskError` です。ボイス接続は、同じ `@bot.listen` を通じて[ボイスのイベント](../voice/index.md#events)を送ります。

## リスナーが例外を送出したとき { #when-a-listener-raises }

リスナー内の例外は、`listener`、`event`、`error` を持つ `ListenerError` イベントとして送られます。`ListenerError` のリスナーがなければ、bostie はその例外をトレースバック付きでログに出力します。リスナーがある場合、ログをどう出力するかはあなた次第です。`ListenerError` のリスナー内で起きた例外は、ログに出力されるだけです。
