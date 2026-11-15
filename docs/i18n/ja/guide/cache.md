---
source: f23a479950d38c899e79ae34c9fa1854c7b6fd34
---

# キャッシュと権限

![箱に骨を落とし入れる Bostie](../assets/mascot/bostie-cache.webp){ align=right width="180" loading=lazy }

`bot.cache` は、ゲートウェイが Bot に伝えた内容を保持し、ハンドラーがリクエストなしで情報を参照できるようにします。キャッシュはイベントから埋められ、あなたが行う REST 呼び出しから埋められることはありません。

```python
--8<-- "docs/snippets/cache/cache_lookups.py"
```

## 保持するもの { #what-it-keeps }

| 保持するもの | 既定 | オプション |
|---|---|---|
| サーバーと、そのロール、絵文字、スタンプ | はい | |
| チャンネルとアクティブなスレッド | はい | |
| ボイス状態 | はい | |
| イベント、ステージ、サウンドボードのサウンド | はい | |
| Bot のユーザーと、各サーバーでの Bot 自身のメンバー | はい | |
| すべてのメンバー | いいえ | `members=True`。`Intents.GUILD_MEMBERS` が必要 |
| オフラインでないメンバーのプレゼンス | いいえ | `presences=True`。`Intents.GUILD_PRESENCES` が必要 |
| 最新のメッセージ | いいえ | `max_messages=N` |
| 最新の DM チャンネル | いいえ | `max_dm_channels=N` |

`Bot(cache=False)` は何も保持せず、その場合ほかのオプションは `ValueError` を送出します。インテントが足りないオプションは、`Bot` の作成時に `MissingIntent` を送出します。チャンネルは Discord が送ったとおりに保存されますが、`CHANNEL_UPDATE` が最新に保たないフィールド（`last_message_id`、`message_count`、`member_count`、`last_pin_timestamp`）は `None` になります。

`members=True` の場合、Bot の接続時に Discord は大きなサーバーのメンバーを一部しか送りません。そこで bostie は残りをシャードごとに 1 サーバーずつ要求し、シャードはそのサーバーがすべてそろってはじめて準備完了とみなされます。`chunk_members=False` を指定するとこの処理を省略し、メンバーが参加したり、変更されたり、発言したりするにつれてキャッシュが埋まっていきます。レート制限されたリクエストは、Discord が指定した待ち時間の後にリトライされます。

## 参照 { #lookups }

| メソッド | 戻り値 |
|---|---|
| `guild(id)`、`guilds()` | 1 つの `Guild`、またはそのすべて |
| `channel(id)`、`guild_channels(guild_id)` | `Channel`、またはサーバーのチャンネルとアクティブなスレッド |
| `role(id)`、`guild_roles(guild_id)` | `Role`、またはサーバーのロール |
| `voice_state(guild_id, user_id)`、`guild_voice_states(guild_id)` | ユーザーがボイスのどこにいるか。ユーザーがどのボイスチャンネルにもいなければ `None` |
| `scheduled_event(id)`、`guild_scheduled_events(guild_id)` | `ScheduledEvent`、またはサーバーのイベント |
| `stage_instance(id)`、`guild_stage_instances(guild_id)` | 開催中の `StageInstance`、またはサーバーのステージ |
| `soundboard_sound(sound_id)`、`guild_soundboard_sounds(guild_id)` | `SoundboardSound`、またはサーバーのサウンド |
| `me(guild_id)` | サーバーでの Bot 自身の `Member` |
| `member(guild_id, user_id)`、`guild_members(guild_id)` | メンバー。`members=True` でなければ Bot 自身のみ |
| `presence(guild_id, user_id)`、`guild_presences(guild_id)` | プレゼンス（`presences=True` の場合）。オフラインなら `None` |
| `user(id)` | Bot のユーザー、またはキャッシュされたメンバーや DM チャンネルのユーザー |
| `dm_channel(user_id)` | Bot とあるユーザーとの DM チャンネル（保持されていれば） |
| `message(id)` | 最新の `max_messages` 件のメッセージのうちの 1 件 |
| `permissions(channel_id, member=None)` | チャンネルでのメンバーの権限。`member` を省略すると Bot の権限 |
| `guild_permissions(guild_id, member=None)` | 同じものをサーバー全体について |

キャッシュが保持していないものに対しては、参照は `None` または空のリストを返します。`bot.cache.clear()` はキャッシュを空にします。更新イベントと削除イベントは、[イベント](events.md#old-values-and-the-cache)で説明しているとおり、変更前のキャッシュされたオブジェクトを持っています。

## キャッシュになければ REST { #the-cache-else-rest }

```python
--8<-- "docs/snippets/cache/lookups_with_rest.py"
```

`bot.lookup` は、キャッシュにあればキャッシュからオブジェクトを返し、なければ REST に問い合わせます。`user(user_id)`、`member(guild_id, user_id)`、`channel(channel_id)`、`guild(guild_id)`、`role(guild_id, role_id)`、`message(channel_id, message_id)` があります。REST が返したものは保存されません。キャッシュはあくまでゲートウェイだけに従うので、同じ欠けているオブジェクトをもう一度参照すると、再び問い合わせが行われます。存在しない ID に対する `NotFound` のような REST のエラーは、そのまま送出されます。

## イベント、ステージ、サウンド { #scheduled-events-stages-and-sounds }

```python
--8<-- "docs/snippets/cache/guild_events.py"
```

これらは各サーバーと一緒に届き、作成・更新・削除のイベントに従って更新されます。そのためには、イベントには `Intents.GUILD_SCHEDULED_EVENTS` が、サウンドボードのサウンドには `Intents.GUILD_EXPRESSIONS` が必要です。どちらも `Intents.DEFAULT` に含まれています。`bot.request_soundboard_sounds` への応答は、サーバーのサウンドを置き換えます。これらの更新イベントは `old` を持ち、`SoundboardSoundDelete` は削除された `sound` を持ちます。

## 起動 { #startup }

シャードは、`READY` に挙げられたすべてのサーバーが `GuildCreate` として届いた時点、または `guild_ready_timeout` 秒（既定値は 2）の間どのサーバーも届かなかった時点で準備完了になります。`Started` はすべてのシャードを待ちます。`joined=True` を持つ `GuildCreate` は、Bot が追加されたばかりのサーバーです。それ以外は、利用可能になったサーバーです。

`large_threshold`（50 から 250、既定値は 50）は、Discord がオフラインのメンバーを含めずにサーバーを送るようになるメンバー数のしきい値です。

## 権限 { #permissions }

`cache.permissions(channel_id, member)` は Discord のルールを適用します。サーバーオーナーと管理者はすべての権限を持ちます。それ以外の場合は、`@everyone` ロール、次にメンバーのロール、次に `@everyone`、ロール、メンバーに対するチャンネルの上書きを順に適用します。タイムアウト中のメンバーに残るのは「チャンネルを見る」と「メッセージ履歴を読む」だけです。「チャンネルを見る」がなければ何も残りません。「メッセージを送信」がなければ、それを必要とする権限が取り除かれます。ボイスチャンネルで「接続」がなければ、ボイスの権限が取り除かれます。スレッドは親チャンネルの上書きを使います。キャッシュにチャンネル、サーバー、またはスレッドの親がない場合は `None` を返します。

同じ計算はキャッシュなしでも使えます。`bostie.permissions.compute_permissions(guild, member, channel=None, *, parent=None)` で、`parent` はスレッドの親チャンネルです。このモジュールには、Discord のドキュメントに基づく `Permissions` の集合もあります。`ALL`、`NONE`、`TEXT`、`VOICE`、`STAGE`（チャンネルの種類ごとに適用される権限）、`ELEVATED`（サーバーが要求している場合に、モデレーターのアカウントで 2段階認証を必要とする権限）、`TIMED_OUT`（タイムアウト中のメンバーに残る権限）です。

インタラクションの中ではキャッシュは必要ありません。`ctx.interaction.app_permissions` にはチャンネルでの Bot の権限が、`ctx.member.permissions` にはユーザーの権限が入っています。

## ロールの階層 { #role-hierarchy }

メンバーがキック、BAN、タイムアウト、編集できるのは、最上位のロールが自分の最上位のロールより低いメンバーだけです。また、Discord はメンバーの名前を、色の付いたロールのうち最上位のものの色で表示します。`bostie.permissions` には、キャッシュ、インタラクション、REST のいずれかから得た `Guild` とメンバーを受け取る、3 つのヘルパーがあります。

| ヘルパー | 戻り値 |
|---|---|
| `top_role(guild, member)` | メンバーの最上位の `Role`。`@everyone` しか持たない場合は `None` |
| `display_colors(guild, member)` | 色を持つロールのうち最上位のものの `RoleColors`、または `None` |
| `can_act_on(guild, actor, target)` | `actor` が `target`（メンバーまたはロール）より上位かどうか。オーナーは全員より上位で、オーナーより上位の人はいません。それ以外の場合は、Discord がロール管理で行うのと同じように、actor の最上位のロールを、target の最上位のロール、または target のロールそのものと位置で比較します |

同じ位置にあるロールは、Discord の並べ方と同じく、ID 順（古いロールが先）で順位が決まります。

```python
--8<-- "docs/snippets/cache/role_hierarchy.py"
```
