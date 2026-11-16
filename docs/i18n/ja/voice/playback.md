---
source: d85e30b6bb45eff282f70f86375dc49e252a5387
description: "Discord のボイスでファイル、URL、HLS を再生。トラック、音量、イコライザーと速度のフィルター、途切れずクロスフェードするキュー、Opus エンコーダー設定を解説。"
---

# 音声の再生

![音楽に合わせてうなずく Bostie](../assets/mascot/bostie-playback.webp){ align=right width="180" loading=lazy }

`await vc.play(source)` は[ボイス接続](index.md)でソースを再生し、すぐに `Track` を返します。複数のトラックは同時に再生されてミキシングされます。`Queue` を使うと、トラックを 1 つずつ順番に再生します。

```python
--8<-- "docs/snippets/voice/tracks.py"
```

## ソース { #sources }

ソースは再生する音声です。各ソースは 1 回しか使えません。`play` がソースを開き、終了したソースを再び開くと `StateError` が送出されます。同じ音声を 2 回再生するには、`source.again()` で同じ音声を持つ新しいソースを取得します。

| ソース | 再生するもの | シーク |
|---|---|---|
| `voice.File(path)` | ローカルファイル | 可能 |
| `voice.Bytes(data, hint=None)` | メモリ上のファイルの内容。`hint` は `"ogg"` のような拡張子 | 可能 |
| `voice.Url(url, headers=None)` | ストリーミングで取得する HTTP または HTTPS のメディアの直接 URL、または HLS プレイリスト | サーバーが Range リクエストを受け付ける場合は可能。HLS は不可 |
| `voice.FFmpeg(args, executable="ffmpeg")` | ffmpeg コマンドの出力。bostie がデコードできない形式に使う | 不可 |
| `voice.Stream(items, format)` | `format` 形式の PCM チャンクまたは Opus パケットを返す非同期イテラブル | 不可 |

`File`、`Bytes`、`Url` は Rust でデコードされます。対応形式は Ogg（Opus、Vorbis）、WebM と Matroska、MP4 と M4A（AAC）、MP3、FLAC、WAV です。URL はメディアそのものを指している必要があり、メディアを埋め込んだ Web ページを指していてはいけません。サーバーとの接続が切れると、`Url` は 0.5 秒、1 秒、2 秒の間隔をおいて最大 3 回まで再接続し、サーバーが Range リクエストを受け付ける場合は切れた位置から続きを再生します。HLS のマスタープレイリストはその音声レンディションを再生し、ライブプレイリストは末尾の 3 セグメント手前から再生を始めます。暗号化されたプレイリストとバイト範囲指定のセグメントは `SourceError` で拒否されます。`FFmpeg` は渡された入力引数でプログラムを実行し、その出力から 48 kHz ステレオ 16 ビットの PCM を読み取ります。プログラムが `PATH` 上にない場合や、エラーで終了した場合は `SourceError` を送出します。開けない、デコードできない、シークできないソースは `SourceError` を送出するか、そのトラックを理由 `ERROR` で終了させます。

`open()` によって `opened` が設定された後、ソースは `format`、`duration`（秒。不明な場合は `None`）、`seekable`、`tags` を持ちます。`tags` は、ファイルに記録された `title`、`artist`、`album` を持つ `voice.Tags` です。

```python
--8<-- "docs/snippets/voice/other_sources.py"
```

ほとんどの Ogg Opus ファイルや WebM ファイルのように、20 ms のパケットに分かれた 48 kHz ステレオの Opus 音声は、フィルターなし・音量 1 で単独再生している間は、デコードも再エンコードもせずにそのまま送信されます。`track.passthrough` で、そうなっているかどうかがわかります。

## トラック { #tracks }

`vc.play(source, volume=1.0, filters=Filters(), meta=None)` は、開始時の音量とフィルター、そして `meta` を受け取ります。`meta` には、リクエストしたユーザーなど、トラックと一緒に保持したい任意の値を渡せます。トラックの[シンク](sinks.md)に使う `buffer` と `stall` も受け取ります。

| `Track` | 動作 |
|---|---|
| `await track.wait()` | 終了を待って `TrackEnd` を返す。待機をキャンセルしてもトラックは止まらない |
| `await track.started()` | 再生が始まるまで待つ。先に終了した場合は `False` |
| `track.ended` | `TrackEnd`。再生中は `None` |
| `track.position` | これまでに再生したソースの秒数 |
| `track.pause()`、`track.resume()`、`track.paused` | 音声の供給を止める、再開する。すでにバッファーにある音声は再生される |
| `await track.seek(seconds)` | 指定した位置から再生を続ける。ソースがシークできない場合は `SourceError` |
| `track.volume`、`track.filters` | 再生中に変更すると即座に反映される |
| `track.stop()` | トラックを終了する |
| `track.source`、`track.meta`、`track.sink` | 再生しているソース、指定した値、書き込み先のシンク |

`TrackEnd` は `track`、`reason`（`FINISHED`、`error` を伴う `ERROR`、`STOPPED`、または接続が終了したときの `CLEANUP`）、`may_start_next` を持ちます。`may_start_next` は `FINISHED` または `ERROR` の後で真になり、独自のプレイヤーが次のトラックに進むべきかどうかを示します。`vc.stop()` はすべてのトラックを終了します。

## 音量とフィルター { #volume-and-filters }

`volume` はサンプルに掛ける倍率です。0.5 で半分の大きさになり、1.0 では変化しません。`voice.Filters` は、音声の再生中に Rust が適用するエフェクトを設定します。

| `Filters` のフィールド | 既定値 | 範囲 |
|---|---|---|
| `equalizer` | `{}` | 最大 16 バンド。20〜20 000 Hz の中心周波数を dB 単位のゲインに対応付ける。各バンドの幅は 1 オクターブ |
| `bass` | 0.0 | 100 Hz 未満のゲイン（dB） |
| `speed` | 1.0 | 0.5〜2.0。音程は保たれる |
| `pitch` | 1.0 | 0.5〜2.0。テンポは保たれる |

ゲインの範囲は -24〜24 dB で、範囲外の値は `ValueError` を送出します。

## キュー { #queues }

```python
--8<-- "docs/snippets/voice/queue_player.py"
```

`voice.Queue(vc)` はトラックを隙間なく続けて再生します。`crossfade` 秒だけ重ねて再生することもできます。`volume`、`filters`、`crossfade`、`history`（既定値は接続の設定）と、シンクの `buffer` と `stall` を受け取ります。

- `queue.add(source, meta=None, index=None)` はソースを末尾に、または次に再生するトラックの中の `index` の位置に追加し、その `Track` を返します。
- `queue.now`、`queue.upcoming`、`queue.history` は、現在のトラック、次に再生するトラック、そして再生済みのトラック（最大 `history` 件）です。
- `queue.skip()` は現在のトラックを終了します。`queue.back()` は履歴の最後のトラックをもう一度再生し、その後で現在のトラックを最初から再生します。
- `queue.move(track, index)`、`queue.remove(track)`、`queue.shuffle()`、`queue.clear()` は、次に再生するトラックを並べ替えます。
- `queue.loop = "track"` で現在のトラックを、`"queue"` でキュー全体を繰り返し、`None` で繰り返しを止めます。型は `voice.Loop` です。繰り返しには `source.again()` が使われます。
- `queue.pause()`、`queue.resume()`、`queue.paused`、`queue.volume`、`queue.filters` は、再生中のトラックに作用します。
- `queue.stop()` はすべてのトラックを終了します。その後に追加したトラックは再び再生されます。
- `await queue.join()` は、キューにも再生中にも何もなくなるまで待ちます。

終了した接続のキューは `closed` となり、それ以上トラックを受け付けません。スニペットのように、再接続した後で新しいキューを作成してください。

## サウンドボードのサウンド { #soundboard-sounds }

`await bot.rest.soundboard.send(vc.channel_id, sound_id=...)` は、Bot が接続しているチャンネルでサウンドボードのサウンドを再生します。サウンドは Bot が送る音声とは別に、Discord が通話に直接再生します。別のサーバーのサウンドには `source_guild_id` を渡します。`bot.rest.soundboard.list_default()` は Discord 標準のサウンドを、`list(guild_id)` はサーバーのサウンドを一覧にし、`bot.request_soundboard_sounds(guild_id)` はゲートウェイにそれらを要求します。

## 複数のサーバーで同じ音声を流す { #the-same-audio-in-several-guilds }

`await voice.broadcast(source, connections)` は 1 つのソースを複数の接続で再生し、デコードとエンコードは 1 回だけ行います。終了した接続は対象から外れ、後から参加した接続は追加されません。`encoder` と `stall` を受け取り、`stall` の既定値は `"drop"` です。これにより、停滞した接続が他の接続の足を引っ張りません。

## エンコーダー { #encoder }

`voice.EncoderSettings` は接続の Opus エンコーダーを設定します。`Defaults(encoder=...)`、`connect(encoder=...)`、または接続中の `vc.encoder = ...` で指定します。

| フィールド | 既定値 | 意味 |
|---|---|---|
| `bitrate` | `None` | ビット毎秒。500〜512 000。`None` はチャンネルのビットレートに従い、なければ 64 000 |
| `application` | `"audio"` | 音楽には `"audio"`、音声通話には `"voip"`、遅延を最小にするには `"lowdelay"` |
| `fec` | `True` | 失われたパケットを補う前方誤り訂正 |
| `packet_loss` | 0.1 | `fec` が想定するパケット損失率。0〜1 |
| `complexity` | 10 | 0〜10。低いほど CPU 使用量が減る |
| `bandwidth` | `"auto"` | 最も高い音声帯域。`"narrow"`、`"medium"`、`"wide"`、`"superwide"`、`"full"` のいずれか |
| `signal_type` | `"auto"` | `"voice"` または `"music"` 向けに調整する |

`vc.speaking_flags` は Bot が通知する発話フラグを設定します。既定値は `voice.SpeakingFlags.MICROPHONE` で、共有音声には `SOUNDSHARE`、優先スピーカーには `PRIORITY` を使います。
