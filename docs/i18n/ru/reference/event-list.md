---
source: 74e134769abeab53fb9b321be4b35f2e68a57db8
description: "Все события шлюза, вебхуков и бота, которые доставляет bostie, по темам: имя диспетчеризации, интенты, без которых Discord их не пришлёт, и поля."
---

# Список событий

Все классы событий находятся в `bostie.events`. Слушайте их через `@bot.listen`, как объясняет [руководство по событиям](../guide/events.md). Столбец интентов называет интенты, при которых Discord присылает событие; достаточно любого из них. Пустая ячейка означает, что Discord присылает событие без интента или только в ответ на запрос. У каждого события шлюза также есть `shard_id`, `raw_json` и `raw`. В [справочнике по API](api/events.md) есть каждое поле с его типом.

## Сессия { #session }

| Событие | Диспетчеризация | Интенты | Поля |
|---|---|---|---|
| `Ready` | `READY` | | `user`, `guilds` (недоступны до своего `GuildCreate`), `application`, `session_id`, `shard` |
| `Resumed` | `RESUMED` | | |
| `UserUpdate` | `USER_UPDATE` | | `user` — пользователь бота; `old` |
| `GatewayRateLimited` | `RATE_LIMITED` | | `opcode`, `retry_after`, `meta` |

## Серверы { #guilds }

| Событие | Диспетчеризация | Интенты | Поля |
|---|---|---|---|
| `GuildCreate` | `GUILD_CREATE` | `GUILDS` | `guild`, `joined`, `channels`, `threads`, `voice_states`, `stage_instances`, `guild_scheduled_events`, `soundboard_sounds`, `members`, `presences` |
| `GuildUpdate` | `GUILD_UPDATE` | `GUILDS` | `guild`, `old` |
| `GuildDelete` | `GUILD_DELETE` | `GUILDS` | `guild_id`, `unavailable`, `guild` (из кэша) |
| `RoleCreate` | `GUILD_ROLE_CREATE` | `GUILDS` | `guild_id`, `role` |
| `RoleUpdate` | `GUILD_ROLE_UPDATE` | `GUILDS` | `guild_id`, `role`, `old` |
| `RoleDelete` | `GUILD_ROLE_DELETE` | `GUILDS` | `guild_id`, `role_id`, `role` (из кэша) |
| `EmojisUpdate` | `GUILD_EMOJIS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`, `emojis`, `old` |
| `StickersUpdate` | `GUILD_STICKERS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`, `stickers`, `old` |
| `SoundboardSoundCreate` | `GUILD_SOUNDBOARD_SOUND_CREATE` | `GUILD_EXPRESSIONS` | `sound` |
| `SoundboardSoundUpdate` | `GUILD_SOUNDBOARD_SOUND_UPDATE` | `GUILD_EXPRESSIONS` | `sound`, `old` |
| `SoundboardSoundDelete` | `GUILD_SOUNDBOARD_SOUND_DELETE` | `GUILD_EXPRESSIONS` | `sound_id`, `guild_id`, `sound` |
| `SoundboardSoundsUpdate` | `GUILD_SOUNDBOARD_SOUNDS_UPDATE` | `GUILD_EXPRESSIONS` | `soundboard_sounds`, `guild_id`, `old` |
| `SoundboardSounds` | `SOUNDBOARD_SOUNDS` | | `soundboard_sounds`, `guild_id`; ответ на `bot.request_soundboard_sounds` |
| `AuditLogEntryCreate` | `GUILD_AUDIT_LOG_ENTRY_CREATE` | `GUILD_MODERATION` | `guild_id`, `entry` |
| `BanAdd` | `GUILD_BAN_ADD` | `GUILD_MODERATION` | `guild_id`, `user` |
| `BanRemove` | `GUILD_BAN_REMOVE` | `GUILD_MODERATION` | `guild_id`, `user` |
| `GuildIntegrationsUpdate` | `GUILD_INTEGRATIONS_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id` |
| `IntegrationCreate` | `INTEGRATION_CREATE` | `GUILD_INTEGRATIONS` | `guild_id`, `integration` |
| `IntegrationUpdate` | `INTEGRATION_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id`, `integration` |
| `IntegrationDelete` | `INTEGRATION_DELETE` | `GUILD_INTEGRATIONS` | `id`, `guild_id`, `application_id` |
| `WebhooksUpdate` | `WEBHOOKS_UPDATE` | `GUILD_WEBHOOKS` | `guild_id`, `channel_id` |
| `CommandPermissionsUpdate` | `APPLICATION_COMMAND_PERMISSIONS_UPDATE` | | `permissions` |

## Участники и присутствие { #members-and-presences }

| Событие | Диспетчеризация | Интенты | Поля |
|---|---|---|---|
| `MemberJoin` | `GUILD_MEMBER_ADD` | `GUILD_MEMBERS` | `guild_id`, `member` |
| `MemberUpdate` | `GUILD_MEMBER_UPDATE` | `GUILD_MEMBERS` | `guild_id`, `user`, `roles`, `nick`, `joined_at`, `communication_disabled_until` и остальные поля участника; `old` |
| `MemberRemove` | `GUILD_MEMBER_REMOVE` | `GUILD_MEMBERS` | `guild_id`, `user`, `member` (из кэша) |
| `MembersChunk` | `GUILD_MEMBERS_CHUNK` | | `guild_id`, `members`, `presences`, `not_found`, `chunk_index`, `chunk_count`, `nonce`; ответ на `bot.request_members` |
| `PresenceUpdate` | `PRESENCE_UPDATE` | `GUILD_PRESENCES` | `guild_id`, `presence`, `old` |

## Каналы и ветки { #channels-and-threads }

| Событие | Диспетчеризация | Интенты | Поля |
|---|---|---|---|
| `ChannelCreate` | `CHANNEL_CREATE` | `GUILDS` | `channel` |
| `ChannelUpdate` | `CHANNEL_UPDATE` | `GUILDS` | `channel`, `old` |
| `ChannelDelete` | `CHANNEL_DELETE` | `GUILDS` | `channel` |
| `ChannelPinsUpdate` | `CHANNEL_PINS_UPDATE` | `GUILDS`, `DIRECT_MESSAGES` | `guild_id`, `channel_id`, `last_pin_timestamp` |
| `ThreadCreate` | `THREAD_CREATE` | `GUILDS` | `channel`, `newly_created`, `member` |
| `ThreadUpdate` | `THREAD_UPDATE` | `GUILDS` | `channel`, `old` |
| `ThreadDelete` | `THREAD_DELETE` | `GUILDS` | `id`, `guild_id`, `parent_id`, `type`, `thread` (из кэша) |
| `ThreadListSync` | `THREAD_LIST_SYNC` | `GUILDS` | `guild_id`, `channel_ids`, `threads`, `members` |
| `ThreadMemberUpdate` | `THREAD_MEMBER_UPDATE` | `GUILDS` | `guild_id`, `thread_member` — участник ветки бота |
| `ThreadMembersUpdate` | `THREAD_MEMBERS_UPDATE` | `GUILDS`, `GUILD_MEMBERS` | `id`, `guild_id`, `member_count`, `added_members`, `removed_member_ids` |
| `StageInstanceCreate` | `STAGE_INSTANCE_CREATE` | `GUILDS` | `stage_instance` |
| `StageInstanceUpdate` | `STAGE_INSTANCE_UPDATE` | `GUILDS` | `stage_instance`, `old` |
| `StageInstanceDelete` | `STAGE_INSTANCE_DELETE` | `GUILDS` | `stage_instance` |
| `InviteCreate` | `INVITE_CREATE` | `GUILD_INVITES` | `code`, `channel_id`, `guild_id`, `inviter`, `max_age`, `max_uses`, `expires_at` и другие |
| `InviteDelete` | `INVITE_DELETE` | `GUILD_INVITES` | `channel_id`, `guild_id`, `code` |

## Сообщения, реакции и опросы { #messages-reactions-and-polls }

| Событие | Диспетчеризация | Интенты | Поля |
|---|---|---|---|
| `MessageCreate` | `MESSAGE_CREATE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `message` |
| `MessageUpdate` | `MESSAGE_UPDATE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `message`, `old` |
| `MessageDelete` | `MESSAGE_DELETE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `id`, `channel_id`, `guild_id`, `message` (из кэша) |
| `MessageDeleteBulk` | `MESSAGE_DELETE_BULK` | `GUILD_MESSAGES` | `ids`, `channel_id`, `guild_id`, `messages` (из кэша) |
| `TypingStart` | `TYPING_START` | `GUILD_MESSAGE_TYPING`, `DIRECT_MESSAGE_TYPING` | `channel_id`, `guild_id`, `user_id`, `timestamp`, `member` |
| `ReactionAdd` | `MESSAGE_REACTION_ADD` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `emoji`, `burst`, `type`, `member`, `message_author_id`, `burst_colors` |
| `ReactionRemove` | `MESSAGE_REACTION_REMOVE` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `emoji`, `burst`, `type` |
| `ReactionRemoveAll` | `MESSAGE_REACTION_REMOVE_ALL` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `channel_id`, `message_id`, `guild_id` |
| `ReactionRemoveEmoji` | `MESSAGE_REACTION_REMOVE_EMOJI` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `channel_id`, `message_id`, `guild_id`, `emoji` |
| `PollVoteAdd` | `MESSAGE_POLL_VOTE_ADD` | `GUILD_MESSAGE_POLLS`, `DIRECT_MESSAGE_POLLS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `answer_id` |
| `PollVoteRemove` | `MESSAGE_POLL_VOTE_REMOVE` | `GUILD_MESSAGE_POLLS`, `DIRECT_MESSAGE_POLLS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `answer_id` |

Для содержимого сообщений и `content` в `AutoModerationActionExecution` нужен интент `MESSAGE_CONTENT`, кроме личных сообщений и сообщений, которые упоминают бота.

## Голос { #voice }

| Событие | Диспетчеризация | Интенты | Поля |
|---|---|---|---|
| `VoiceStateUpdate` | `VOICE_STATE_UPDATE` | `GUILD_VOICE_STATES` | `voice_state`, `old` |
| `VoiceServerUpdate` | `VOICE_SERVER_UPDATE` | | `token`, `guild_id`, `endpoint` |
| `VoiceChannelEffectSend` | `VOICE_CHANNEL_EFFECT_SEND` | `GUILD_VOICE_STATES` | `channel_id`, `guild_id`, `user_id`, `emoji`, `animation_type`, `animation_id`, `sound_id`, `sound_volume` |
| `VoiceChannelStatusUpdate` | `VOICE_CHANNEL_STATUS_UPDATE` | `GUILDS` | `id`, `guild_id`, `status` |
| `VoiceChannelStartTimeUpdate` | `VOICE_CHANNEL_START_TIME_UPDATE` | `GUILDS` | `id`, `guild_id`, `voice_start_time` |
| `ChannelInfo` | `CHANNEL_INFO` | | `guild_id`, `channels`; ответ на `bot.request_channel_info` |

События собственных голосовых подключений бота перечислены в [руководстве по голосу](../voice/index.md#events).

## Взаимодействия, модерация и запланированные события { #interactions-moderation-and-scheduled-events }

| Событие | Диспетчеризация | Интенты | Поля |
|---|---|---|---|
| `InteractionCreate` | `INTERACTION_CREATE` | | `interaction` |
| `AutoModerationRuleCreate` | `AUTO_MODERATION_RULE_CREATE` | `AUTO_MODERATION_CONFIGURATION` | `rule` |
| `AutoModerationRuleUpdate` | `AUTO_MODERATION_RULE_UPDATE` | `AUTO_MODERATION_CONFIGURATION` | `rule` |
| `AutoModerationRuleDelete` | `AUTO_MODERATION_RULE_DELETE` | `AUTO_MODERATION_CONFIGURATION` | `rule` |
| `AutoModerationActionExecution` | `AUTO_MODERATION_ACTION_EXECUTION` | `AUTO_MODERATION_EXECUTION` | `guild_id`, `action`, `rule_id`, `rule_trigger_type`, `user_id`, `channel_id`, `message_id`, `content`, `matched_keyword`, `matched_content` |
| `ScheduledEventCreate` | `GUILD_SCHEDULED_EVENT_CREATE` | `GUILD_SCHEDULED_EVENTS` | `scheduled_event` |
| `ScheduledEventUpdate` | `GUILD_SCHEDULED_EVENT_UPDATE` | `GUILD_SCHEDULED_EVENTS` | `scheduled_event`, `old` |
| `ScheduledEventDelete` | `GUILD_SCHEDULED_EVENT_DELETE` | `GUILD_SCHEDULED_EVENTS` | `scheduled_event` |
| `ScheduledEventUserAdd` | `GUILD_SCHEDULED_EVENT_USER_ADD` | `GUILD_SCHEDULED_EVENTS` | `guild_scheduled_event_id`, `user_id`, `guild_id` |
| `ScheduledEventUserRemove` | `GUILD_SCHEDULED_EVENT_USER_REMOVE` | `GUILD_SCHEDULED_EVENTS` | `guild_scheduled_event_id`, `user_id`, `guild_id` |

## Монетизация { #monetization }

| Событие | Диспетчеризация | Интенты | Поля |
|---|---|---|---|
| `EntitlementCreate` | `ENTITLEMENT_CREATE` | | `entitlement`; для тестового entitlement — `PartialEntitlement` |
| `EntitlementUpdate` | `ENTITLEMENT_UPDATE` | | `entitlement`; для подписки — только когда она заканчивается |
| `EntitlementDelete` | `ENTITLEMENT_DELETE` | | `entitlement` |
| `SubscriptionCreate` | `SUBSCRIPTION_CREATE` | | `subscription` |
| `SubscriptionUpdate` | `SUBSCRIPTION_UPDATE` | | `subscription` |
| `SubscriptionDelete` | `SUBSCRIPTION_DELETE` | | `subscription` |

## События вебхуков { #webhook-events }

Discord присылает их только на Webhook Events URL приложения, который обслуживает [`ASGIApp`](../guide/http-interactions.md). События entitlement, перечисленные выше, тоже приходят туда.

| Событие | Тип | Поля |
|---|---|---|
| `ApplicationAuthorized` | `APPLICATION_AUTHORIZED` | `integration_type`, `user`, `scopes`, `guild` |
| `ApplicationDeauthorized` | `APPLICATION_DEAUTHORIZED` | `user` |
| `QuestUserEnrollment` | `QUEST_USER_ENROLLMENT` | |
| `LobbyMessageCreate` | `LOBBY_MESSAGE_CREATE` | `message` |
| `LobbyMessageUpdate` | `LOBBY_MESSAGE_UPDATE` | `message` |
| `LobbyMessageDelete` | `LOBBY_MESSAGE_DELETE` | `id`, `lobby_id` |
| `GameDirectMessageCreate` | `GAME_DIRECT_MESSAGE_CREATE` | `message` |
| `GameDirectMessageUpdate` | `GAME_DIRECT_MESSAGE_UPDATE` | `message` |
| `GameDirectMessageDelete` | `GAME_DIRECT_MESSAGE_DELETE` | `message` |

## Любая диспетчеризация { #any-dispatch }

| Событие | Диспетчеризация | Интенты | Поля |
|---|---|---|---|
| `RawEvent` | любая | | `name`, `seq`, `raw`, `raw_json`; для любой диспетчеризации, смоделированной или нет |

## События бота { #events-of-the-bot }

Эти события приходят от bostie, а не от Discord; их имена принадлежат самому bostie.

| Событие | Имя | Поля | Когда отправляется |
|---|---|---|---|
| `Starting` | `bot.starting` | | `start()` вот-вот начнёт синхронизацию и подключение; REST работает |
| `Started` | `bot.started` | | все шарды готовы; один раз на `start()` |
| `Stopping` | `bot.stopping` | | `close()` начался после `Started`; шлюз ещё открыт |
| `Stopped` | `bot.stopped` | | `close()` завершился; шлюз и REST больше недоступны |
| `ShardReady` | `shard.ready` | `shard_id` | новая сессия шарда готова и его серверы получены |
| `ShardDisconnect` | `shard.disconnect` | `shard_id`, `code`, `reason`, `resuming` | шард потерял соединение |
| `ShardStop` | `shard.stop` | `shard_id`, `code`, `reason` | шард остановился окончательно |
| `ShardBackpressure` | `shard.backpressure` | `shard_id`, `queued_bytes` | шард приостановил чтение, потому что цикл событий отстаёт |
| `ListenerError` | `bot.listener_error` | `listener`, `event`, `error` | слушатель выбросил исключение |
| `TaskError` | `bot.task_error` | `task`, `error` | фоновая задача выбросила исключение |
