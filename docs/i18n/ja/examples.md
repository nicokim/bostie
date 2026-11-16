---
source: f54db29cb26a01ab63ca1c726a1815c968b1cf3a
description: "bostie のサンプル Bot 集。スラッシュコマンド、Components V2、モーダル、イベント、音楽キュー、録音、HTTP、ウェブフック、リンクされたロール。"
---

# サンプル

ここにあるのは、リポジトリの `examples/` フォルダのファイルです。テストスイートはこれらを型チェックし、1 つずつ読み込みます。読み込むと Bot が組み立てられ、すべてのコマンド定義がチェックされます。どれも `BOSTIE_TOKEN` を設定して `python examples/commands.py` のように実行できます。

## コマンド { #commands }

型付きのオプション、グループ、オートコンプリート、ユーザーコマンドとメッセージコマンド、チェック、エラーフック、gettext カタログからの翻訳。

```python
--8<-- "examples/commands.py"
```

## コンポーネント { #components }

Components V2 を一通り使う例です。ページ送り、コンテナの色を変えるセレクトメニュー、確認のクリックを待ってから実行するキックを含みます。

```python
--8<-- "examples/components.py"
```

## モーダル { #modals }

ボタンから開いて送信時に読み取るモーダルと、コマンドが送信を待つモーダル。

```python
--8<-- "examples/modals.py"
```

## イベント { #events }

アノテーションによるリスナー、ユニオン、Bot 独自のイベント、`ListenerError` のリスナー、そして `wait_for` で作った数当てゲーム。

```python
--8<-- "examples/events.py"
```

## ライフサイクル { #lifecycle }

`Starting` リスナーで開いて `Stopped` リスナーで閉じるデータベースと、2 つのバックグラウンドタスク。

```python
--8<-- "examples/lifecycle.py"
```

## 音楽 { #music }

スキップ、ループ、退出に対応した、サーバーごとのキュー。

```python
--8<-- "examples/music.py"
```

## 録音 { #recording }

`/stop` まで、話者ごとに 1 つの Ogg Opus ファイルに録音します。

```python
--8<-- "examples/recording.py"
```

## アクティビティ { #activity }

アプリのアクティビティを開くエントリーポイントコマンドとスラッシュコマンド。

```python
--8<-- "examples/activity.py"
```

## HTTP インタラクション { #http-interactions }

`ASGIApp` で HTTP 経由で提供するスラッシュコマンド。

```python
--8<-- "examples/http_interactions.py"
```

## REST スクリプト { #rest-script }

ゲートウェイを使わない REST API。

```python
--8<-- "examples/rest_script.py"
```

## Web アプリ { #web-app }

Web アプリの lifespan の中で起動する Bot と、Bot の REST クライアントを通じて投稿するルート。

```python
--8<-- "examples/web_app.py"
```

## ウェブフック { #webhook }

ウェブフック URL を通じた投稿を、非同期コードと通常のコードの両方から行います。

```python
--8<-- "examples/webhook.py"
```

## リンクされたロール { #linked-roles }

リンクされたロールの認証ページ。OAuth2 のログイン、コードの交換、ユーザーのロール接続を扱います。

```python
--8<-- "examples/linked_roles.py"
```

## モジュール構成の Bot { #modular-bot }

モジュールを名前で読み込み、コマンドから再読み込みする Bot。

```python
--8<-- "examples/modular/bot.py"
```

```python
--8<-- "examples/modular/greetings.py"
```
