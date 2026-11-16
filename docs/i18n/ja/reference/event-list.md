---
source: 74e134769abeab53fb9b321be4b35f2e68a57db8
description: "bostie が届けるゲートウェイ、ウェブフック、Bot のすべてのイベントを、トピック別にディスパッチ名、必要なインテント、フィールドとともに一覧にします。"
---

# イベント一覧

すべてのイベントクラスは `bostie.events` にあります。[イベントのガイド](../guide/events.md)で説明しているとおり、`@bot.listen` で受け取ります。インテントの列は、Discord がそのイベントを送るために必要なインテントを示し、どれか 1 つがあれば十分です。空欄は、Discord がインテントなしで送るか、リクエストへの応答としてのみ送ることを意味します。すべてのゲートウェイイベントは `shard_id`、`raw_json`、`raw` も持ちます。各フィールドとその型は [API リファレンス](api/events.md)に載っています。

## セッション { #session }

| イベント | ディスパッチ | インテント | フィールド |
|---|---|---|---|
| `Ready` | `READY` | | `user`、`guilds`（各サーバーの `GuildCreate` が届くまでは利用不可）、`application`、`session_id`、`shard` |
| `Resumed` | `RESUMED` | | |
| `UserUpdate` | `USER_UPDATE` | | `user`（Bot 自身のもの）、`old` |
| `GatewayRateLimited` | `RATE_LIMITED` | | `opcode`、`retry_after`、`meta` |

## サーバー { #guilds }

| イベント | ディスパッチ | インテント | フィールド |
|---|---|---|---|
| `GuildCreate` | `GUILD_CREATE` | `GUILDS` | `guild`、`joined`、`channels`、`threads`、`voice_states`、`stage_instances`、`guild_scheduled_events`、`soundboard_sounds`、`members`、`presences` |
| `GuildUpdate` | `GUILD_UPDATE` | `GUILDS` | `guild`、`old` |
| `GuildDelete` | `GUILD_DELETE` | `GUILDS` | `guild_id`、`unavailable`、`guild`（キャッシュ済み） |
| `RoleCreate` | `GUILD_ROLE_CREATE` | `GUILDS` | `guild_id`、`role` |
| `RoleUpdate` | `GUILD_ROLE_UPDATE` | `GUILDS` | `guild_id`、`role`、`old` |
| `RoleDelete` | `GUILD_ROLE_DELETE` | `GUILDS` | `guild_id`、`role_id`、`role`（キャッシュ済み） |
| `EmojisUpdate` | `GUILD_EMOJIS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`、`emojis`、`old` |
| `StickersUpdate` | `GUILD_STICKERS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`、`stickers`、`old` |
| `SoundboardSoundCreate` | `GUILD_SOUNDBOARD_SOUND_CREATE` | `GUILD_EXPRESSIONS` | `sound` |
| `SoundboardSoundUpdate` | `GUILD_SOUNDBOARD_SOUND_UPDATE` | `GUILD_EXPRESSIONS` | `sound`、`old` |
| `SoundboardSoundDelete` | `GUILD_SOUNDBOARD_SOUND_DELETE` | `GUILD_EXPRESSIONS` | `sound_id`、`guild_id`、`sound` |
| `SoundboardSoundsUpdate` | `GUILD_SOUNDBOARD_SOUNDS_UPDATE` | `GUILD_EXPRESSIONS` | `soundboard_sounds`、`guild_id`、`old` |
| `SoundboardSounds` | `SOUNDBOARD_SOUNDS` | | `soundboard_sounds`、`guild_id`（`bot.request_soundboard_sounds` への応答） |
| `AuditLogEntryCreate` | `GUILD_AUDIT_LOG_ENTRY_CREATE` | `GUILD_MODERATION` | `guild_id`、`entry` |
| `BanAdd` | `GUILD_BAN_ADD` | `GUILD_MODERATION` | `guild_id`、`user` |
| `BanRemove` | `GUILD_BAN_REMOVE` | `GUILD_MODERATION` | `guild_id`、`user` |
| `GuildIntegrationsUpdate` | `GUILD_INTEGRATIONS_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id` |
| `IntegrationCreate` | `INTEGRATION_CREATE` | `GUILD_INTEGRATIONS` | `guild_id`、`integration` |
| `IntegrationUpdate` | `INTEGRATION_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id`、`integration` |
| `IntegrationDelete` | `INTEGRATION_DELETE` | `GUILD_INTEGRATIONS` | `id`、`guild_id`、`application_id` |
| `WebhooksUpdate` | `WEBHOOKS_UPDATE` | `GUILD_WEBHOOKS` | `guild_id`、`channel_id` |
| `CommandPermissionsUpdate` | `APPLICATION_COMMAND_PERMISSIONS_UPDATE` | | `permissions` |

## メンバーとプレゼンス { #members-and-presences }

| イベント | ディスパッチ | インテント | フィールド |
|---|---|---|---|
| `MemberJoin` | `GUILD_MEMBER_ADD` | `GUILD_MEMBERS` | `guild_id`、`member` |
| `MemberUpdate` | `GUILD_MEMBER_UPDATE` | `GUILD_MEMBERS` | `guild_id`、`user`、`roles`、`nick`、`joined_at`、`communication_disabled_until` などのメンバーのフィールド、`old` |
| `MemberRemove` | `GUILD_MEMBER_REMOVE` | `GUILD_MEMBERS` | `guild_id`、`user`、`member`（キャッシュ済み） |
| `MembersChunk` | `GUILD_MEMBERS_CHUNK` | | `guild_id`、`members`、`presences`、`not_found`、`chunk_index`、`chunk_count`、`nonce`（`bot.request_members` への応答） |
| `PresenceUpdate` | `PRESENCE_UPDATE` | `GUILD_PRESENCES` | `guild_id`、`presence`、`old` |

## チャンネルとスレッド { #channels-and-threads }

| イベント | ディスパッチ | インテント | フィールド |
|---|---|---|---|
| `ChannelCreate` | `CHANNEL_CREATE` | `GUILDS` | `channel` |
| `ChannelUpdate` | `CHANNEL_UPDATE` | `GUILDS` | `channel`、`old` |
| `ChannelDelete` | `CHANNEL_DELETE` | `GUILDS` | `channel` |
| `ChannelPinsUpdate` | `CHANNEL_PINS_UPDATE` | `GUILDS`、`DIRECT_MESSAGES` | `guild_id`、`channel_id`、`last_pin_timestamp` |
| `ThreadCreate` | `THREAD_CREATE` | `GUILDS` | `channel`、`newly_created`、`member` |
| `ThreadUpdate` | `THREAD_UPDATE` | `GUILDS` | `channel`、`old` |
| `ThreadDelete` | `THREAD_DELETE` | `GUILDS` | `id`、`guild_id`、`parent_id`、`type`、`thread`（キャッシュ済み） |
| `ThreadListSync` | `THREAD_LIST_SYNC` | `GUILDS` | `guild_id`、`channel_ids`、`threads`、`members` |
| `ThreadMemberUpdate` | `THREAD_MEMBER_UPDATE` | `GUILDS` | `guild_id`、`thread_member`（Bot 自身のもの） |
| `ThreadMembersUpdate` | `THREAD_MEMBERS_UPDATE` | `GUILDS`、`GUILD_MEMBERS` | `id`、`guild_id`、`member_count`、`added_members`、`removed_member_ids` |
| `StageInstanceCreate` | `STAGE_INSTANCE_CREATE` | `GUILDS` | `stage_instance` |
| `StageInstanceUpdate` | `STAGE_INSTANCE_UPDATE` | `GUILDS` | `stage_instance`、`old` |
| `StageInstanceDelete` | `STAGE_INSTANCE_DELETE` | `GUILDS` | `stage_instance` |
| `InviteCreate` | `INVITE_CREATE` | `GUILD_INVITES` | `code`、`channel_id`、`guild_id`、`inviter`、`max_age`、`max_uses`、`expires_at` など |
| `InviteDelete` | `INVITE_DELETE` | `GUILD_INVITES` | `channel_id`、`guild_id`、`code` |

## メッセージ、リアクション、投票 { #messages-reactions-and-polls }

| イベント | ディスパッチ | インテント | フィールド |
|---|---|---|---|
| `MessageCreate` | `MESSAGE_CREATE` | `GUILD_MESSAGES`、`DIRECT_MESSAGES` | `message` |
| `MessageUpdate` | `MESSAGE_UPDATE` | `GUILD_MESSAGES`、`DIRECT_MESSAGES` | `message`、`old` |
| `MessageDelete` | `MESSAGE_DELETE` | `GUILD_MESSAGES`、`DIRECT_MESSAGES` | `id`、`channel_id`、`guild_id`、`message`（キャッシュ済み） |
| `MessageDeleteBulk` | `MESSAGE_DELETE_BULK` | `GUILD_MESSAGES` | `ids`、`channel_id`、`guild_id`、`messages`（キャッシュ済み） |
| `TypingStart` | `TYPING_START` | `GUILD_MESSAGE_TYPING`、`DIRECT_MESSAGE_TYPING` | `channel_id`、`guild_id`、`user_id`、`timestamp`、`member` |
| `ReactionAdd` | `MESSAGE_REACTION_ADD` | `GUILD_MESSAGE_REACTIONS`、`DIRECT_MESSAGE_REACTIONS` | `user_id`、`channel_id`、`message_id`、`guild_id`、`emoji`、`burst`、`type`、`member`、`message_author_id`、`burst_colors` |
| `ReactionRemove` | `MESSAGE_REACTION_REMOVE` | `GUILD_MESSAGE_REACTIONS`、`DIRECT_MESSAGE_REACTIONS` | `user_id`、`channel_id`、`message_id`、`guild_id`、`emoji`、`burst`、`type` |
| `ReactionRemoveAll` | `MESSAGE_REACTION_REMOVE_ALL` | `GUILD_MESSAGE_REACTIONS`、`DIRECT_MESSAGE_REACTIONS` | `channel_id`、`message_id`、`guild_id` |
| `ReactionRemoveEmoji` | `MESSAGE_REACTION_REMOVE_EMOJI` | `GUILD_MESSAGE_REACTIONS`、`DIRECT_MESSAGE_REACTIONS` | `channel_id`、`message_id`、`guild_id`、`emoji` |
| `PollVoteAdd` | `MESSAGE_POLL_VOTE_ADD` | `GUILD_MESSAGE_POLLS`、`DIRECT_MESSAGE_POLLS` | `user_id`、`channel_id`、`message_id`、`guild_id`、`answer_id` |
| `PollVoteRemove` | `MESSAGE_POLL_VOTE_REMOVE` | `GUILD_MESSAGE_POLLS`、`DIRECT_MESSAGE_POLLS` | `user_id`、`channel_id`、`message_id`、`guild_id`、`answer_id` |

メッセージの内容と `AutoModerationActionExecution` の `content` には `MESSAGE_CONTENT` インテントが必要です。ただし、DM と、Bot をメンションしたメッセージは例外です。

## ボイス { #voice }

| イベント | ディスパッチ | インテント | フィールド |
|---|---|---|---|
| `VoiceStateUpdate` | `VOICE_STATE_UPDATE` | `GUILD_VOICE_STATES` | `voice_state`、`old` |
| `VoiceServerUpdate` | `VOICE_SERVER_UPDATE` | | `token`、`guild_id`、`endpoint` |
| `VoiceChannelEffectSend` | `VOICE_CHANNEL_EFFECT_SEND` | `GUILD_VOICE_STATES` | `channel_id`、`guild_id`、`user_id`、`emoji`、`animation_type`、`animation_id`、`sound_id`、`sound_volume` |
| `VoiceChannelStatusUpdate` | `VOICE_CHANNEL_STATUS_UPDATE` | `GUILDS` | `id`、`guild_id`、`status` |
| `VoiceChannelStartTimeUpdate` | `VOICE_CHANNEL_START_TIME_UPDATE` | `GUILDS` | `id`、`guild_id`、`voice_start_time` |
| `ChannelInfo` | `CHANNEL_INFO` | | `guild_id`、`channels`（`bot.request_channel_info` への応答） |

Bot 自身のボイス接続のイベントは、[ボイスのガイド](../voice/index.md#events)に一覧があります。

## インタラクション、モデレーション、イベント { #interactions-moderation-and-scheduled-events }

| イベント | ディスパッチ | インテント | フィールド |
|---|---|---|---|
| `InteractionCreate` | `INTERACTION_CREATE` | | `interaction` |
| `AutoModerationRuleCreate` | `AUTO_MODERATION_RULE_CREATE` | `AUTO_MODERATION_CONFIGURATION` | `rule` |
| `AutoModerationRuleUpdate` | `AUTO_MODERATION_RULE_UPDATE` | `AUTO_MODERATION_CONFIGURATION` | `rule` |
| `AutoModerationRuleDelete` | `AUTO_MODERATION_RULE_DELETE` | `AUTO_MODERATION_CONFIGURATION` | `rule` |
| `AutoModerationActionExecution` | `AUTO_MODERATION_ACTION_EXECUTION` | `AUTO_MODERATION_EXECUTION` | `guild_id`、`action`、`rule_id`、`rule_trigger_type`、`user_id`、`channel_id`、`message_id`、`content`、`matched_keyword`、`matched_content` |
| `ScheduledEventCreate` | `GUILD_SCHEDULED_EVENT_CREATE` | `GUILD_SCHEDULED_EVENTS` | `scheduled_event` |
| `ScheduledEventUpdate` | `GUILD_SCHEDULED_EVENT_UPDATE` | `GUILD_SCHEDULED_EVENTS` | `scheduled_event`、`old` |
| `ScheduledEventDelete` | `GUILD_SCHEDULED_EVENT_DELETE` | `GUILD_SCHEDULED_EVENTS` | `scheduled_event` |
| `ScheduledEventUserAdd` | `GUILD_SCHEDULED_EVENT_USER_ADD` | `GUILD_SCHEDULED_EVENTS` | `guild_scheduled_event_id`、`user_id`、`guild_id` |
| `ScheduledEventUserRemove` | `GUILD_SCHEDULED_EVENT_USER_REMOVE` | `GUILD_SCHEDULED_EVENTS` | `guild_scheduled_event_id`、`user_id`、`guild_id` |

## 収益化 { #monetization }

| イベント | ディスパッチ | インテント | フィールド |
|---|---|---|---|
| `EntitlementCreate` | `ENTITLEMENT_CREATE` | | `entitlement`（テスト用のエンタイトルメントでは `PartialEntitlement`） |
| `EntitlementUpdate` | `ENTITLEMENT_UPDATE` | | `entitlement`。サブスクリプションの場合は終了時のみ送信されます |
| `EntitlementDelete` | `ENTITLEMENT_DELETE` | | `entitlement` |
| `SubscriptionCreate` | `SUBSCRIPTION_CREATE` | | `subscription` |
| `SubscriptionUpdate` | `SUBSCRIPTION_UPDATE` | | `subscription` |
| `SubscriptionDelete` | `SUBSCRIPTION_DELETE` | | `subscription` |

## ウェブフックイベント { #webhook-events }

Discord はこれらのイベントを、アプリの Webhook Events URL にだけ送ります。この URL は [`ASGIApp`](../guide/http-interactions.md) で受け付けられます。上のエンタイトルメントのイベントもそこに届きます。

| イベント | 種類 | フィールド |
|---|---|---|
| `ApplicationAuthorized` | `APPLICATION_AUTHORIZED` | `integration_type`、`user`、`scopes`、`guild` |
| `ApplicationDeauthorized` | `APPLICATION_DEAUTHORIZED` | `user` |
| `QuestUserEnrollment` | `QUEST_USER_ENROLLMENT` | |
| `LobbyMessageCreate` | `LOBBY_MESSAGE_CREATE` | `message` |
| `LobbyMessageUpdate` | `LOBBY_MESSAGE_UPDATE` | `message` |
| `LobbyMessageDelete` | `LOBBY_MESSAGE_DELETE` | `id`、`lobby_id` |
| `GameDirectMessageCreate` | `GAME_DIRECT_MESSAGE_CREATE` | `message` |
| `GameDirectMessageUpdate` | `GAME_DIRECT_MESSAGE_UPDATE` | `message` |
| `GameDirectMessageDelete` | `GAME_DIRECT_MESSAGE_DELETE` | `message` |

## すべてのディスパッチ { #any-dispatch }

| イベント | ディスパッチ | インテント | フィールド |
|---|---|---|---|
| `RawEvent` | すべて | | `name`、`seq`、`raw`、`raw_json`。モデル化されているかどうかにかかわらず、すべてのディスパッチで届きます |

## Bot のイベント { #events-of-the-bot }

これらは Discord ではなく bostie から届くイベントで、名前も bostie 独自のものです。

| イベント | 名前 | フィールド | 送信されるタイミング |
|---|---|---|---|
| `Starting` | `bot.starting` | | `start()` が同期と接続を始める直前。REST は使えます |
| `Started` | `bot.started` | | すべてのシャードの準備ができたとき。`start()` ごとに 1 回 |
| `Stopping` | `bot.stopping` | | `Started` の後に `close()` が始まったとき。ゲートウェイはまだ開いています |
| `Stopped` | `bot.stopped` | | `close()` が完了したとき。ゲートウェイも REST も使えなくなっています |
| `ShardReady` | `shard.ready` | `shard_id` | シャードの新しいセッションの準備ができ、そのサーバーが届いたとき |
| `ShardDisconnect` | `shard.disconnect` | `shard_id`、`code`、`reason`、`resuming` | シャードの接続が切れたとき |
| `ShardStop` | `shard.stop` | `shard_id`、`code`、`reason` | シャードが完全に停止したとき |
| `ShardBackpressure` | `shard.backpressure` | `shard_id`、`queued_bytes` | イベントループが追いつかないため、シャードが読み込みを一時停止したとき |
| `ListenerError` | `bot.listener_error` | `listener`、`event`、`error` | リスナーが例外を送出したとき |
| `TaskError` | `bot.task_error` | `task`、`error` | バックグラウンドタスクが例外を送出したとき |
