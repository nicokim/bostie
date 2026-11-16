---
source: 0c49168c9e0add898da9cb6bf25e6b0a9fb68ecd
description: "ユーザーインストール型の Discord アプリ、アクティビティの起動、エンタイトルメントとサブスクリプションでの SKU 販売、REST での Social SDK ロビー管理。"
---

# アクティビティ、収益化、Social SDK

Discord のアプリは、サーバー内の Bot にとどまりません。このページでは、ユーザーにインストールされるアプリ、アクティビティ、SKU の販売、Social SDK のサーバー側を扱います。

```python
--8<-- "docs/snippets/apps/premium.py"
```

## ユーザーにインストールされるアプリ { #apps-installed-to-users }

アプリはサーバーだけでなくユーザーのアカウントにもインストールでき、その場合、コマンドはそのユーザーがいる場所ならどこでも動作します。フレンドとの DM や、Bot がいないサーバーも含まれます。Developer Portal の **Installation** ページでユーザーインストールを有効にし、スニペットが Bot 全体に対して行っているように、`integration_types` と `contexts` でコマンドがそれを使えるようにします（[コマンドの登録](registration.md#where-commands-can-be-used)を参照）。

このようなコンテキストでは、Bot はチャンネルにいません。キャッシュのエントリーもゲートウェイイベントもなく、REST API もそこではほとんどの呼び出しを拒否します。`ctx.interaction.authorizing_integration_owners` はどのインストールによってインタラクションが可能になったかを、`ctx.interaction.context` はインタラクションがどこで起きたかを、`ctx.interaction.app_permissions` はアプリに何が許されているかを示します。応答はいつもどおり動作します。ユーザーにのみインストールされたアプリでは、Discord が許可するフォローアップは 5 件までで、bostie は 6 件目で警告します。

## アクティビティ { #activities }

アクティビティは Discord の中で動く Web アプリです。bostie は Bot 側を扱います。

- `@bot.entry_point(name)` は、アプリランチャーに表示されるエントリーポイントコマンドを登録します。詳しくは[ユーザーコマンド、メッセージコマンド、エントリーポイントコマンド](context-menus.md#the-entry-point-command)で説明しています。
- `await ctx.launch_activity()` は、コマンドやコンポーネントからアクティビティを開き、そのインスタンスの ID を返します。
- `await bot.rest.application.fetch_activity_instance(application_id, instance_id)` は `ActivityInstance` を返します。アクティビティが動いている場所と、参加しているユーザーの ID がわかります。

[アクティビティのサンプル](../examples.md#activity)は、エントリーポイントと、どちらもアクティビティを起動するスラッシュコマンドを登録します。

## 収益化 { #monetization }

収益化を有効にしたアプリは SKU（サブスクリプション、永続アイテム、消費型アイテム）を販売します。ユーザーの購入はエンタイトルメントです。

- `ctx.interaction.entitlements` は、インタラクションのユーザーとサーバーのエンタイトルメントを列挙します。そのため、コマンドはリクエストを送らずに購入を確認できます。
- `ui.premium_button(sku_id)` は、SKU の購入画面を開くボタンを表示します。このボタンが Bot に届くことはありません。`fmt.store(application_id, sku_id=None)` は、アプリのストアページ、または 1 つの SKU へのリンクを作ります。Discord はこれをカードとして表示します。
- `bot.rest.monetization` は、アプリの SKU を列挙し（`list_skus`）、ユーザー、サーバー、SKU で絞り込んでエンタイトルメントをたどり（`list_entitlements`）、1 件を取得し、消費型アイテムを使用済みにします（`consume_entitlement`）。SKU のサブスクリプションの一覧表示と取得もできます。
- `create_test_entitlement(application_id, sku_id=..., owner_id=..., owner_type=1)` は、サーバー（1）またはユーザー（2）に無料のテスト用エンタイトルメントを付与し、`delete_test_entitlement` はそれを削除します。
- `EntitlementCreate`、`EntitlementUpdate`、`EntitlementDelete` イベントと、`SubscriptionCreate`、`SubscriptionUpdate`、`SubscriptionDelete` イベントは、ゲートウェイ経由で変更を知らせます。エンタイトルメントのイベントは[ウェブフックイベント](http-interactions.md#webhook-events)としても届きます。テスト用エンタイトルメントは、本物のエンタイトルメントにある日付を持たない `PartialEntitlement` として届きます。

## Social SDK { #social-sdk }

Discord Social SDK はゲームの中で動作します。そのサーバー側は REST です。

- `bot.rest.lobbies` は、ロビーを作成し、そのメタデータとメンバーを編集し（`add_member`、一度に最大 25 人を更新する `bulk_update_members`、`remove_member`）、ロビーを削除し、ユーザーをロビーのリンク先チャンネルに招待し、ロビーのメッセージにモデレーション用のメタデータを設定します。
- プレイヤーのトークンを持つ [`BearerClient`](oauth2.md#calling-the-api-as-the-user) の `user.rest.lobbies` は、シークレットでロビーを作成または参加し（`create_or_join`）、ロビーから退出し（`leave`）、Discord のチャンネルをリンクおよびリンク解除し（`link_channel`、`unlink_channel`）、メッセージを送信・一覧表示し（`send_message`、`list_messages`）、プレイヤー自身をリンク先チャンネルに招待します（`create_invite_for_self`）。
- `bot.rest.partner_sdk` はトークンを交換します。ゲーム独自のアカウントシステムのプレイヤー向けの仮トークン、外部 ID プロバイダーのトークンと引き換えのトークン、パブリッシャーのトークンと引き換えの子ゲームのトークンです。仮アカウントの統合解除や、ダイレクトメッセージへのモデレーション用メタデータの設定も行います。
- `bot.rest.identities` は、外部アカウントを連携したユーザーのアプリケーション ID 情報とプロフィールを一覧表示、取得、更新します。

ロビーのメッセージとゲーム内のダイレクトメッセージは、`LobbyMessage*` と `GameDirectMessage*` の[ウェブフックイベント](http-interactions.md#webhook-events)として届きます。
