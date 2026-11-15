---
source: f49f915994d33a7383708875aaeb52d2b113c2b7
---

# 事件列表

所有事件类都位于 `bostie.events` 中。用 `@bot.listen` 监听其中某个事件，[事件指南](../guide/events.md)中有说明。Intent 一列列出 Discord 发送该事件所需的 Intent，满足其中任意一个即可；空白单元格表示 Discord 无需 Intent 即会发送，或者只作为对某个请求的应答发送。每个网关事件还带有 `shard_id`、`raw_json` 和 `raw`。[API 参考](api/events.md)列出了每个字段及其类型。

## 会话 { #session }

| 事件 | 分发名 | Intent | 字段 |
|---|---|---|---|
| `Ready` | `READY` | | `user`、`guilds`（在各自的 `GuildCreate` 到达之前不可用）、`application`、`session_id`、`shard` |
| `Resumed` | `RESUMED` | | |
| `UserUpdate` | `USER_UPDATE` | | `user`（机器人自己的）；`old` |
| `GatewayRateLimited` | `RATE_LIMITED` | | `opcode`、`retry_after`、`meta` |

## 服务器 { #guilds }

| 事件 | 分发名 | Intent | 字段 |
|---|---|---|---|
| `GuildCreate` | `GUILD_CREATE` | `GUILDS` | `guild`、`joined`、`channels`、`threads`、`voice_states`、`stage_instances`、`guild_scheduled_events`、`soundboard_sounds`、`members`、`presences` |
| `GuildUpdate` | `GUILD_UPDATE` | `GUILDS` | `guild`、`old` |
| `GuildDelete` | `GUILD_DELETE` | `GUILDS` | `guild_id`、`unavailable`、`guild`（来自缓存） |
| `RoleCreate` | `GUILD_ROLE_CREATE` | `GUILDS` | `guild_id`、`role` |
| `RoleUpdate` | `GUILD_ROLE_UPDATE` | `GUILDS` | `guild_id`、`role`、`old` |
| `RoleDelete` | `GUILD_ROLE_DELETE` | `GUILDS` | `guild_id`、`role_id`、`role`（来自缓存） |
| `EmojisUpdate` | `GUILD_EMOJIS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`、`emojis`、`old` |
| `StickersUpdate` | `GUILD_STICKERS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`、`stickers`、`old` |
| `SoundboardSoundCreate` | `GUILD_SOUNDBOARD_SOUND_CREATE` | `GUILD_EXPRESSIONS` | `sound` |
| `SoundboardSoundUpdate` | `GUILD_SOUNDBOARD_SOUND_UPDATE` | `GUILD_EXPRESSIONS` | `sound`、`old` |
| `SoundboardSoundDelete` | `GUILD_SOUNDBOARD_SOUND_DELETE` | `GUILD_EXPRESSIONS` | `sound_id`、`guild_id`、`sound` |
| `SoundboardSoundsUpdate` | `GUILD_SOUNDBOARD_SOUNDS_UPDATE` | `GUILD_EXPRESSIONS` | `soundboard_sounds`、`guild_id`、`old` |
| `SoundboardSounds` | `SOUNDBOARD_SOUNDS` | | `soundboard_sounds`、`guild_id`；对 `bot.request_soundboard_sounds` 的应答 |
| `AuditLogEntryCreate` | `GUILD_AUDIT_LOG_ENTRY_CREATE` | `GUILD_MODERATION` | `guild_id`、`entry` |
| `BanAdd` | `GUILD_BAN_ADD` | `GUILD_MODERATION` | `guild_id`、`user` |
| `BanRemove` | `GUILD_BAN_REMOVE` | `GUILD_MODERATION` | `guild_id`、`user` |
| `GuildIntegrationsUpdate` | `GUILD_INTEGRATIONS_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id` |
| `IntegrationCreate` | `INTEGRATION_CREATE` | `GUILD_INTEGRATIONS` | `guild_id`、`integration` |
| `IntegrationUpdate` | `INTEGRATION_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id`、`integration` |
| `IntegrationDelete` | `INTEGRATION_DELETE` | `GUILD_INTEGRATIONS` | `id`、`guild_id`、`application_id` |
| `WebhooksUpdate` | `WEBHOOKS_UPDATE` | `GUILD_WEBHOOKS` | `guild_id`、`channel_id` |
| `CommandPermissionsUpdate` | `APPLICATION_COMMAND_PERMISSIONS_UPDATE` | | `permissions` |

## 成员与在线状态 { #members-and-presences }

| 事件 | 分发名 | Intent | 字段 |
|---|---|---|---|
| `MemberJoin` | `GUILD_MEMBER_ADD` | `GUILD_MEMBERS` | `guild_id`、`member` |
| `MemberUpdate` | `GUILD_MEMBER_UPDATE` | `GUILD_MEMBERS` | `guild_id`、`user`、`roles`、`nick`、`joined_at`、`communication_disabled_until` 以及其他成员字段；`old` |
| `MemberRemove` | `GUILD_MEMBER_REMOVE` | `GUILD_MEMBERS` | `guild_id`、`user`、`member`（来自缓存） |
| `MembersChunk` | `GUILD_MEMBERS_CHUNK` | | `guild_id`、`members`、`presences`、`not_found`、`chunk_index`、`chunk_count`、`nonce`；对 `bot.request_members` 的应答 |
| `PresenceUpdate` | `PRESENCE_UPDATE` | `GUILD_PRESENCES` | `guild_id`、`presence`、`old` |

## 频道与子区 { #channels-and-threads }

| 事件 | 分发名 | Intent | 字段 |
|---|---|---|---|
| `ChannelCreate` | `CHANNEL_CREATE` | `GUILDS` | `channel` |
| `ChannelUpdate` | `CHANNEL_UPDATE` | `GUILDS` | `channel`、`old` |
| `ChannelDelete` | `CHANNEL_DELETE` | `GUILDS` | `channel` |
| `ChannelPinsUpdate` | `CHANNEL_PINS_UPDATE` | `GUILDS`、`DIRECT_MESSAGES` | `guild_id`、`channel_id`、`last_pin_timestamp` |
| `ThreadCreate` | `THREAD_CREATE` | `GUILDS` | `channel`、`newly_created`、`member` |
| `ThreadUpdate` | `THREAD_UPDATE` | `GUILDS` | `channel`、`old` |
| `ThreadDelete` | `THREAD_DELETE` | `GUILDS` | `id`、`guild_id`、`parent_id`、`type`、`thread`（来自缓存） |
| `ThreadListSync` | `THREAD_LIST_SYNC` | `GUILDS` | `guild_id`、`channel_ids`、`threads`、`members` |
| `ThreadMemberUpdate` | `THREAD_MEMBER_UPDATE` | `GUILDS` | `guild_id`、`thread_member`（机器人自己的） |
| `ThreadMembersUpdate` | `THREAD_MEMBERS_UPDATE` | `GUILDS`、`GUILD_MEMBERS` | `id`、`guild_id`、`member_count`、`added_members`、`removed_member_ids` |
| `StageInstanceCreate` | `STAGE_INSTANCE_CREATE` | `GUILDS` | `stage_instance` |
| `StageInstanceUpdate` | `STAGE_INSTANCE_UPDATE` | `GUILDS` | `stage_instance`、`old` |
| `StageInstanceDelete` | `STAGE_INSTANCE_DELETE` | `GUILDS` | `stage_instance` |
| `InviteCreate` | `INVITE_CREATE` | `GUILD_INVITES` | `code`、`channel_id`、`guild_id`、`inviter`、`max_age`、`max_uses`、`expires_at` 等 |
| `InviteDelete` | `INVITE_DELETE` | `GUILD_INVITES` | `channel_id`、`guild_id`、`code` |

## 消息、反应与投票 { #messages-reactions-and-polls }

| 事件 | 分发名 | Intent | 字段 |
|---|---|---|---|
| `MessageCreate` | `MESSAGE_CREATE` | `GUILD_MESSAGES`、`DIRECT_MESSAGES` | `message` |
| `MessageUpdate` | `MESSAGE_UPDATE` | `GUILD_MESSAGES`、`DIRECT_MESSAGES` | `message`、`old` |
| `MessageDelete` | `MESSAGE_DELETE` | `GUILD_MESSAGES`、`DIRECT_MESSAGES` | `id`、`channel_id`、`guild_id`、`message`（来自缓存） |
| `MessageDeleteBulk` | `MESSAGE_DELETE_BULK` | `GUILD_MESSAGES` | `ids`、`channel_id`、`guild_id`、`messages`（来自缓存） |
| `TypingStart` | `TYPING_START` | `GUILD_MESSAGE_TYPING`、`DIRECT_MESSAGE_TYPING` | `channel_id`、`guild_id`、`user_id`、`timestamp`、`member` |
| `ReactionAdd` | `MESSAGE_REACTION_ADD` | `GUILD_MESSAGE_REACTIONS`、`DIRECT_MESSAGE_REACTIONS` | `user_id`、`channel_id`、`message_id`、`guild_id`、`emoji`、`burst`、`type`、`member`、`message_author_id`、`burst_colors` |
| `ReactionRemove` | `MESSAGE_REACTION_REMOVE` | `GUILD_MESSAGE_REACTIONS`、`DIRECT_MESSAGE_REACTIONS` | `user_id`、`channel_id`、`message_id`、`guild_id`、`emoji`、`burst`、`type` |
| `ReactionRemoveAll` | `MESSAGE_REACTION_REMOVE_ALL` | `GUILD_MESSAGE_REACTIONS`、`DIRECT_MESSAGE_REACTIONS` | `channel_id`、`message_id`、`guild_id` |
| `ReactionRemoveEmoji` | `MESSAGE_REACTION_REMOVE_EMOJI` | `GUILD_MESSAGE_REACTIONS`、`DIRECT_MESSAGE_REACTIONS` | `channel_id`、`message_id`、`guild_id`、`emoji` |
| `PollVoteAdd` | `MESSAGE_POLL_VOTE_ADD` | `GUILD_MESSAGE_POLLS`、`DIRECT_MESSAGE_POLLS` | `user_id`、`channel_id`、`message_id`、`guild_id`、`answer_id` |
| `PollVoteRemove` | `MESSAGE_POLL_VOTE_REMOVE` | `GUILD_MESSAGE_POLLS`、`DIRECT_MESSAGE_POLLS` | `user_id`、`channel_id`、`message_id`、`guild_id`、`answer_id` |

消息的内容，以及 `AutoModerationActionExecution` 的 `content`，需要 `MESSAGE_CONTENT` Intent，私信和提及机器人的消息除外。

## 语音 { #voice }

| 事件 | 分发名 | Intent | 字段 |
|---|---|---|---|
| `VoiceStateUpdate` | `VOICE_STATE_UPDATE` | `GUILD_VOICE_STATES` | `voice_state`、`old` |
| `VoiceServerUpdate` | `VOICE_SERVER_UPDATE` | | `token`、`guild_id`、`endpoint` |
| `VoiceChannelEffectSend` | `VOICE_CHANNEL_EFFECT_SEND` | `GUILD_VOICE_STATES` | `channel_id`、`guild_id`、`user_id`、`emoji`、`animation_type`、`animation_id`、`sound_id`、`sound_volume` |
| `VoiceChannelStatusUpdate` | `VOICE_CHANNEL_STATUS_UPDATE` | `GUILDS` | `id`、`guild_id`、`status` |
| `VoiceChannelStartTimeUpdate` | `VOICE_CHANNEL_START_TIME_UPDATE` | `GUILDS` | `id`、`guild_id`、`voice_start_time` |
| `ChannelInfo` | `CHANNEL_INFO` | | `guild_id`、`channels`；对 `bot.request_channel_info` 的应答 |

机器人自身语音连接的事件列在[语音指南](../voice/index.md#events)中。

## 交互、管理与服务器活动 { #interactions-moderation-and-scheduled-events }

| 事件 | 分发名 | Intent | 字段 |
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

## 变现 { #monetization }

| 事件 | 分发名 | Intent | 字段 |
|---|---|---|---|
| `EntitlementCreate` | `ENTITLEMENT_CREATE` | | `entitlement`，测试权益为 `PartialEntitlement` |
| `EntitlementUpdate` | `ENTITLEMENT_UPDATE` | | `entitlement`；对于订阅，仅在其结束时发出 |
| `EntitlementDelete` | `ENTITLEMENT_DELETE` | | `entitlement` |
| `SubscriptionCreate` | `SUBSCRIPTION_CREATE` | | `subscription` |
| `SubscriptionUpdate` | `SUBSCRIPTION_UPDATE` | | `subscription` |
| `SubscriptionDelete` | `SUBSCRIPTION_DELETE` | | `subscription` |

## Webhook 事件 { #webhook-events }

Discord 只把这些事件发送到应用的 Webhook Events URL，由 [`ASGIApp`](../guide/http-interactions.md) 提供服务。上面的权益事件也会发送到那里。

| 事件 | 类型 | 字段 |
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

## 任意分发 { #any-dispatch }

| 事件 | 分发名 | Intent | 字段 |
|---|---|---|---|
| `RawEvent` | 任意 | | `name`、`seq`、`raw`、`raw_json`；适用于任意分发，无论是否已建模 |

## 机器人事件 { #events-of-the-bot }

这些事件来自 bostie，而不是 Discord；它们的名称是 bostie 自己定义的。

| 事件 | 名称 | 字段 | 何时发出 |
|---|---|---|---|
| `Starting` | `bot.starting` | | `start()` 即将同步和连接；REST 可用 |
| `Started` | `bot.started` | | 所有分片都已就绪，每次 `start()` 发出一次 |
| `Stopping` | `bot.stopping` | | `close()` 在 `Started` 之后开始；网关仍然打开 |
| `Stopped` | `bot.stopped` | | `close()` 已完成；网关和 REST 都已不可用 |
| `ShardReady` | `shard.ready` | `shard_id` | 分片的新会话已就绪，且其服务器都已到达 |
| `ShardDisconnect` | `shard.disconnect` | `shard_id`、`code`、`reason`、`resuming` | 分片失去了连接 |
| `ShardStop` | `shard.stop` | `shard_id`、`code`、`reason` | 分片永久停止 |
| `ShardBackpressure` | `shard.backpressure` | `shard_id`、`queued_bytes` | 由于事件循环跟不上，分片暂停了读取 |
| `ListenerError` | `bot.listener_error` | `listener`、`event`、`error` | 某个监听器抛出了异常 |
| `TaskError` | `bot.task_error` | `task`、`error` | 某个后台任务抛出了异常 |
