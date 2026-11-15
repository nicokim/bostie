---
render_macros: true
source: a3a1b83398a63bd2c49a018f148793d0781c22a0
---

<div class="bostie-hero" markdown>
<div class="bostie-hero__copy" markdown>

# bostie

bostie は、ゲートウェイ、HTTP クライアント、ボイスエンジンを Rust で動かす Python 向けの Discord ライブラリです。Bot そのものは、型付きの非同期 Python で書きます。

</div>

![両腕を広げて迎えるボストン・テリアの Bostie](assets/mascot/bostie-welcoming.webp){ .bostie-hero__mascot width="200" }

</div>

!!! warning "プレアルファ"
    bostie の API はまだ変わる可能性があります。スラッシュコマンド、ユーザーコマンド、メッセージコマンド、コンポーネント、モーダル、ゲートウェイイベント、キャッシュ、REST、ウェブフック、HTTP インタラクション、OAuth2、そしてボイスの再生と受信は、現時点ですでに動作します。

```python
--8<-- "docs/snippets/home/readme_example.py"
```

`/play` は、コマンドを実行したユーザーがいるボイスチャンネルに参加し、URL をストリーミングして、停止ボタン付きで応答します。ボタンのカスタム ID にはサーバー ID が含まれており、`@bot.on_component` がそれを `int` に解析し直すので、Bot を再起動した後もボタンは動作し続けます。`bot.run()` はトークンを環境変数 `BOSTIE_TOKEN` から読み込み、コマンドが変更されていれば Discord に送信します。

{% include "callout.md" %}

## 何のために作られたか { #what-it-is-built-for }

### ボイス { #voice }

Discord は 2026 年 3 月 1 日以降、エンドツーエンド暗号化された通話（[DAVE](https://docs.discord.com/developers/topics/voice-connections)）しか受け付けていません。bostie のボイスエンジンは GIL の外側、Rust のスレッド上で動作し、20 ms のクロック、デコード、ミキシング、Opus エンコード、トランスポート暗号化、DAVE を担います。Python 側の仕事は、各シンクのバッファー（既定では 0.2 秒分の音声）を満たしておくことだけです。bostie はファイル、URL、HLS ストリーム、独自の PCM や Opus を再生し、複数のソースをミキシングし、話者ごとに個別に受信し、通話を録音できます。

### Discord の変更に耐える Bot { #bots-that-survive-discords-changes }

未知のフィールド、列挙値、コンポーネントの種類、インタラクションの種類があっても、デコードが失敗することはありません。すべてのイベントと、ペイロード全体からデコードされたすべてのモデルは生の JSON を保持しており、bostie がまだモデル化していないものにも `bot.http` と生のイベントリスナーからアクセスできます。

### Discord のドキュメントどおりの型 { #types-that-match-discords-documentation }

Discord が必ず送るフィールドが `None` になることはなく、部分オブジェクトには専用のクラスがあります。たとえば、コマンドのオプションから解決されたメンバーは `ResolvedMember` であり、`deaf` や `mute` を持ちません。

### シグネチャから作るコマンド { #commands-from-signatures }

オプション、選択肢、範囲、オートコンプリートは型ヒントから作られます。名前、説明、オプションの数、選択肢は、Discord が同期を拒否した時点ではなく、デコレーターの実行時に Discord の上限と照合されます。

### Discord なしでテスト { #tests-without-discord }

`bostie.testing.FakeDiscord` は、実際の Bot をそのまま動かせるローカルのゲートウェイと REST API です。

## 次に読むもの { #where-to-go-next }

- bostie とそのビルド要件を[インストール](install.md)します。
- [クイックスタート](quickstart.md)で最初の Bot を書きます。
- ガイドでは、[スラッシュコマンド](guide/commands.md)から[テスト](guide/testing.md)まで、1 ページにつき 1 つの機能を説明しています。
- [ボイス](voice/index.md)のセクションでは、接続、再生、生の音声、受信を扱います。
- リファレンスには、すべての [`Bot` のオプション](reference/bot-options.md)、すべての[イベント](reference/event-list.md)、そして [API](reference/api/bot.md) が載っています。
