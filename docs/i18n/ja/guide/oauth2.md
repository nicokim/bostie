---
source: 6f520ae7da3868093d6ae15e172deaedb506e1fc
---

# OAuth2 とリンクされたロール

![輝く鍵を掲げる Bostie](../assets/mascot/bostie-oauth2.webp){ align=right width="180" loading=lazy }

OAuth2 を使うと、ユーザーは自分のアカウントへのアクセスをアプリに許可できます。対象は、プロフィール、参加しているサーバー、連携しているアカウント、そのユーザーをサーバーに追加する権利、リンクされたロールのためのロール接続です。bostie は認可 URL を作成し、ユーザーが許可したトークンで API を呼び出します。

```python
--8<-- "docs/snippets/oauth2/oauth_flow.py"
```

## 認可 URL { #authorization-urls }

`oauth2.authorize_url(client_id, scopes=[...], redirect_uri=..., state=...)` は、ユーザーに `scopes` の許可を求め、`code` を付けて `redirect_uri` にリダイレクトして戻る URL を返します。スコープは `OAuth2Scope` のメンバーで、`identify` に対応する `OAuth2Scope.IDENTIFY` のように、Discord が文書化しているスコープごとに 1 つずつあります。bostie が名前を付ける前に Discord が追加したスコープには、ただの文字列も使えます。リダイレクトに送ったときと同じ `state` が含まれていることを確認してください。そうすれば、別のサイトが自分のコードでユーザーをログインさせることはできません。ほかにも次の引数を受け取ります。

- `code_challenge`。PKCE の検証子から求めた S256 のチャレンジで、`exchange_code` が後でその検証子を `code_verifier` として送ります。スニペットでは検証子を 1 つ計算しています。
- `prompt="none"`。すでにスコープを許可したユーザーには同意画面を飛ばします。`"consent"` を指定すると再び表示します。
- `integration_type`。アプリをサーバーにインストールするか、ユーザーにインストールするかを指定します。

bostie が作成するのは、認可コードグラントの URL だけです。インプリシットグラントはトークンをリダイレクト URL に含め、リフレッシュトークンも発行しないため、Discord はこれを推奨しておらず、代わりに PKCE 付きのコードグラントを勧めています。

`oauth2.invite_url(client_id, permissions=..., scopes=[...])` はアプリのインストールリンクを返します。`integration_type`、あらかじめサーバーを選択しておく `guild_id`、`disable_guild_select` も受け取ります。省いた項目は、Developer Portal にあるアプリのインストール設定から取られます。

## トークン { #tokens }

トークンなしの `BearerClient()` から使えるのは `rest.oauth2` だけです。

| メソッド | 動作 |
|---|---|
| `exchange_code(client_id=, client_secret=, code=, redirect_uri=, code_verifier=None)` | リダイレクトで受け取ったコードを `AccessToken` に交換します |
| `refresh(client_id=, client_secret=, refresh_token=)` | 古いトークンが失効する前に新しいトークンを取得します |
| `revoke(client_id=, client_secret=, token=)` | トークンと、そのトークンから発行されたすべてのトークンの認可を終了します |
| `exchange_client_credentials(client_id=, client_secret=, scopes=[...])` | アプリのオーナー（またはそのチーム）のトークンを取得します |

`AccessToken` は `access_token`、`token_type`、秒単位の `expires_in`、`scope`（許可されたスコープを空白で区切ったもの）、`refresh_token` を持ちます。`bot` スコープを含むコードグラントでは、Bot が参加した `guild` も返されます。`webhook.incoming` を含む場合は、作成された `webhook` も返されます。トークンの `repr` は秘密情報を隠すので、ログに出しても漏れることはありません。

## ユーザーとして API を呼び出す { #calling-the-api-as-the-user }

`async with BearerClient(access_token) as user:` からは、トークンのスコープが許す範囲にアクセスできます。

| `user.rest.` | メソッド | スコープ |
|---|---|---|
| `users` | `fetch_current` | `identify` |
| `users` | `list_connections` | `connections` |
| `users` | `fetch_role_connection`、`update_role_connection`、`delete_role_connection` | `role_connections.write` |
| `guilds` | `list` | `guilds` |
| `members` | `fetch_current(guild_id)` | `guilds.members.read` |
| `commands` | `fetch_permissions`、`list_permissions`、`edit_permissions` | 編集には `applications.commands.permissions.update` |
| `oauth2` | `fetch_authorization`。トークンが何を誰に許可しているかを返します | 任意 |
| `lobbies` | [Social SDK のロビー](platform.md#social-sdk)のユーザー側の操作 | `sdk.social_layer` |

`guilds.join` スコープがあれば、Bot は自分が参加しているサーバーにそのユーザーを追加できます（`await bot.rest.members.add(guild_id, user_id, access_token=token.access_token)`）。これは新しい `Member` を返し、ユーザーがすでにメンバーだった場合は `None` を返します。

## リンクされたロール { #linked-roles }

リンクされたロールは、アプリとの接続について「レベル 10 以上」のような条件を満たしたユーザーに付与されるサーバーのロールです。アプリは報告する値を一度宣言し、その後、各ユーザーの値をそのユーザーのトークンで報告します。

1. `bot.rest.application.update_role_connection_metadata(application_id, [...])` で、最大 5 件のメタデータを登録します。それぞれ `type`、`key`、`name`、`description` を持つ `ApplicationRoleConnectionMetadata` です。スニペットの `register_metadata` は、`RESTClient` を使ってこれを行います。
2. Developer Portal で、アプリの **Linked Roles Verification URL** に、`role_connections.write` スコープで OAuth2 フローを開始するページを設定します。
3. 交換の後、`user.rest.users.update_role_connection(application_id, platform_name=..., platform_username=..., metadata={"level": "12"})` を呼び出します。Discord の API が受け取るとおり、メタデータの値は文字列です。

[リンクされたロールのサンプル](../examples.md#linked-roles)は、完全な認証ページです。`fmt.guild_navigation("linked-roles", role_id)` は、チャットでサーバーのリンクされたロールへのリンクを作ります。
