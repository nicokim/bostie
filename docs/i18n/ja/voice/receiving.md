---
source: 0d8bbdce2b1aa0ef1ee6009ade35eb117f7d7909
description: "Discord のボイスを話者ごとに PCM、Opus、RTP パケットで受信し、発話を検出して区切り、通話を中継し、ユーザーごとに Ogg や WAV ファイルへ録音します。"
---

# 受信と録音

![届く音波に向けてマイクを構える Bostie](../assets/mascot/bostie-receiving.webp){ align=right width="180" loading=lazy }

`vc.receive()` はレシーバーを開きます。レシーバーは、Bot が聞いている音声を順に返す、容量に上限のある非同期イテレーターです。話者ごとの音声は個別に届き、Rust で復号、並べ替え、デコードされます。1 つの接続で複数のレシーバーを開くことができ、それぞれが独自の形式とフィルターを持ちます。

```python
--8<-- "docs/snippets/voice/listening.py"
```

## 形式 { #formats }

`vc.receive(format)` は、`voice.ReceiveFormat` に対応する `voice.Receiver` を返します。

| `vc.receive(format)` | 返す項目 | 用途 |
|---|---|---|
| `voice.PCM(rate=48000, channels=2, sample="s16le")` | その形式でデコードされたサンプルの `Frame`。サンプルレートは `voice.DECODE_RATES` のいずれか（8、12、16、24、48 kHz） | 聞き取り、音声認識、音量レベルの計測 |
| `voice.Opus()` | `data` が 1 つの Opus パケットである `Frame` | デコードせずに録音または転送する |
| `voice.Packets()` | ジッターバッファーを通る前の、復号済みで到着順の `Packet` | 独自のジッターバッファー、RTP ヘッダー拡張 |

`Frame` は `user_id`、`ssrc`、RTP の `timestamp`、レシーバーの 48 kHz クロック上の `offset` と `samples`、`received_at`、`data`、`lost`（失われたパケットを隠すために Opus が補完した音声なら真）、そして PCM の場合はフレームの RMS を dBFS で表す `level` を持ちます。クロックはレシーバーを開いた時点から始まるので、異なるユーザーのフレームは `offset` で揃えられます。`Packet` は `user_id`、`ssrc`、`sequence`、`timestamp`、`received_at`、`opus` ペイロード、RTP ヘッダーの `extensions` を持ちます。

## 聞く相手の選択 { #choosing-whom-to-hear }

- `users=[...]` は指定したユーザーだけを残し、`exclude=[...]` は一部のユーザーを除外します。
- `include_self=True` は Bot 自身の音声を加えます。
- `mix=True`（PCM と Opus）は全員の音声をミキシングした 1 つのストリームを届け、`user_id` は `None` になります。
- `markers=True` は `SpeechStart`、`SpeechStop`、`UserLeave` のイベントを、同じクロック上の `offset` 付きでフレームの間に挿入します。これにより、受け取る側は時間を計らなくても、発話がどこで始まりどこで終わるかがわかります。

## 設定 { #settings }

`vc.receive` は次のキーワード引数を受け取ります。`Packets()` にはジッターバッファーがないため、`jitter` は受け取りません。

| 設定 | 既定値 | 意味 |
|---|---|---|
| `buffer` | `Defaults.receive_buffer`、2.0 | 処理の遅い受け取り側のためにレシーバーが保持する項目の秒数。0.02〜60 |
| `overflow` | `Defaults.overflow`、`"drop_oldest"` | バッファーが満杯のときの動作。最も古い項目を破棄するか、`"drop_newest"` で最も新しい項目を破棄する |
| `jitter` | `Defaults.jitter`、0.1 | 遅れて届くパケットを待ってから再生を続けるまでの秒数。0〜1。PCM と Opus のみ |
| `stall` | `Defaults.receive_stall`、`"wait"` | 接続が再接続するときの動作。`"wait"` はレシーバーを開いたままにし、`"end"` は終了する |

## 読み取り { #reading }

`async for item in rx` は項目を待ち、レシーバーが終了すると止まります。`async with rx:` はブロックの終わりでレシーバーを閉じます。

- `rx.get_nowait()` は次の項目を返します。キューに何もない場合は `voice.Empty` を、レシーバーが終了している場合は `voice.ReceiverClosed` を送出します。
- `rx.queued` は、キュー内で最も古い項目が待っている時間、つまり受け取り側の遅れです。
- `rx.clear()` はキュー内のすべての項目を破棄し、ライブの音声から続けます。
- `await rx.mark()` は、呼び出し前に受信した音声がキューに入るか破棄された時点で `True` を返し、1.2 秒にジッターの 2 倍を足した時間が経過すると `False` を返します。1 つの待機をキャンセルしても、他の待機のためにマークは残ります。
- `rx.stats()` は `ReceiverStats` を返します。届けた項目数 `delivered`、あふれて破棄された項目数 `dropped`、途中で失われたパケット数 `lost` を持ちます。
- `rx.close()` は受信を止めます。`rx.closed_reason` は `voice.ClosedReason` で、レシーバーが終了した理由を示します。値は `CLOSED`、`DISCONNECTED`、`MOVED`（Bot がチャンネルを移動した）、`RECONNECTING`（`stall="end"` の場合）のいずれかです。

モデレーターが Bot をスピーカーミュートすると、Discord は Bot に音声を送らなくなります。Bot がスピーカーミュートされている間にレシーバーを開いたとき、またレシーバーが開いている間に Bot がスピーカーミュートされたとき、bostie は警告をログに出力します。

## 中継 { #relaying }

レシーバーとシンクを直接つなぐと、ある通話の音声を別の通話に運べます。

```python
--8<-- "docs/snippets/voice/relay.py"
```

ミキシングされたフレームは `user_id`、`ssrc`、`timestamp` を持たず、ミックスにはマーカーを含められません。失われた Opus フレームは、渡すパケットがないため `data` が空です。`relay_speaker` のように、Opus のシンクに書き込む前にスキップしてください。どちらの端も既定では 48 kHz ステレオ 16 ビットの PCM なので、PCM のレシーバーは既定のシンクにそのまま書き込めます。

## 発話 { #speech }

`speech_activity` がオンの間、すべての接続で発話検出が Rust 上で動作します。ユーザーは最初の音声で話し始めたとみなされ、`speech_hangover` 秒（既定では 0.2 秒）の無音の後に話し終えたとみなされます。`vc.speaking` は現在話しているユーザーを保持し、`SpeechStart` と `SpeechStop` のイベントがリスナーにそれを通知し、`markers=True` で開いたレシーバーはそれをストリームに含めます。`speech_activity` がオフで、開いているレシーバーもない場合、bostie は受信した音声の復号さえ行いません。

`voice.utterances(rx, min_silence=0.8, max_length=30.0)` は、PCM レシーバーのフレームを `Utterance` にまとめます。`Utterance` は 1 人のユーザーの発話の始まりから終わりまでで、レシーバーの形式の `data`、その `offset`、`duration`、失われた秒数 `lost` を持ちます。発話は、`min_silence` 秒のあいだ話し声がないか、`max_length` 秒に達すると終わります。`markers=True` で開いた `mix` なしの PCM レシーバーが必要です。あるいは dBFS 単位の `level` を指定すると、マーカーの代わりにその値で何を発話とみなすかを決めます。

## 録音 { #recording }

```python
--8<-- "docs/snippets/voice/call_recording.py"
```

`voice.Recorder(vc, path)` は各ユーザーをそれぞれのファイルに録音します。`mix=True` の場合は通話全体を 1 つのファイルに録音します。パスは `{guild_id}` と `{user_id}` を含むテンプレートです。ユーザーごとに 1 ファイルにする場合は必須で、`mix=True` の場合は拒否されます。拡張子でコンテナが決まります。

- `.ogg` または `.opus` は Ogg Opus で書き込みます。ユーザーごとの録音では、受信した Opus パケットをデコードせずにそのまま書き込みます。
- `.wav` はレコーダーの `format`（既定では 48 kHz ステレオ）で 16 ビット PCM を書き込みます。サンプルレートは `voice.DECODE_RATES` のいずれかです。

既定の `align=True` では無音で隙間を埋めるので、どのファイルも録音の開始時点から始まり、ファイル同士の時間が揃います。録音する相手はレシーバーと同じく `users`、`exclude`、`include_self` で選びます。ディスクが詰まることがあるため、`buffer`（30 秒）と `jitter`（0.3 秒）はレシーバーの既定値より大きくなっています。録音は、`max_duration` 秒が経過したとき、ファイルが `max_size` バイトに達したとき、または接続が終了したときに自動的に終わります。

`await recorder.stop()` は、すでに聞き取った音声を書き込んだ後で録音を終了し、ファイルをユーザー ID から `Path` への辞書として返します。ミキシングしたファイルのキーは `None` です。`await recorder.wait()` は録音が自然に終わるのを待ちます。`recorder.files` はそれまでに書き込んだファイルを一覧にし、`recorder.stats()` はレシーバーのカウンターを返します。その `dropped` は、ディスクの書き込みが `buffer` より長く遅れたことを意味します。

スニペットのように、通話にいる人たちに Bot が録音していることを伝えてください。
