---
source: f49f915994d33a7383708875aaeb52d2b113c2b7
---

# Lista de eventos

Todas as classes de evento ficam em `bostie.events`. Escute uma com `@bot.listen`, como o [guia de eventos](../guide/events.md) explica. A coluna de intents nomeia as intents de que o Discord precisa para enviar o evento, bastando qualquer uma delas; uma célula vazia significa que o Discord o envia sem intent, ou só como resposta a uma requisição. Todo evento do gateway também tem `shard_id`, `raw_json` e `raw`. A [referência da API](api/events.md) tem cada campo com o seu tipo.

## Sessão { #session }

| Evento | Dispatch | Intents | Campos |
|---|---|---|---|
| `Ready` | `READY` | | `user`, `guilds` (indisponíveis até o seu `GuildCreate`), `application`, `session_id`, `shard` |
| `Resumed` | `RESUMED` | | |
| `UserUpdate` | `USER_UPDATE` | | `user`, o do bot; `old` |
| `GatewayRateLimited` | `RATE_LIMITED` | | `opcode`, `retry_after`, `meta` |

## Servidores { #guilds }

| Evento | Dispatch | Intents | Campos |
|---|---|---|---|
| `GuildCreate` | `GUILD_CREATE` | `GUILDS` | `guild`, `joined`, `channels`, `threads`, `voice_states`, `stage_instances`, `guild_scheduled_events`, `soundboard_sounds`, `members`, `presences` |
| `GuildUpdate` | `GUILD_UPDATE` | `GUILDS` | `guild`, `old` |
| `GuildDelete` | `GUILD_DELETE` | `GUILDS` | `guild_id`, `unavailable`, `guild` (do cache) |
| `RoleCreate` | `GUILD_ROLE_CREATE` | `GUILDS` | `guild_id`, `role` |
| `RoleUpdate` | `GUILD_ROLE_UPDATE` | `GUILDS` | `guild_id`, `role`, `old` |
| `RoleDelete` | `GUILD_ROLE_DELETE` | `GUILDS` | `guild_id`, `role_id`, `role` (do cache) |
| `EmojisUpdate` | `GUILD_EMOJIS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`, `emojis`, `old` |
| `StickersUpdate` | `GUILD_STICKERS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`, `stickers`, `old` |
| `SoundboardSoundCreate` | `GUILD_SOUNDBOARD_SOUND_CREATE` | `GUILD_EXPRESSIONS` | `sound` |
| `SoundboardSoundUpdate` | `GUILD_SOUNDBOARD_SOUND_UPDATE` | `GUILD_EXPRESSIONS` | `sound`, `old` |
| `SoundboardSoundDelete` | `GUILD_SOUNDBOARD_SOUND_DELETE` | `GUILD_EXPRESSIONS` | `sound_id`, `guild_id`, `sound` |
| `SoundboardSoundsUpdate` | `GUILD_SOUNDBOARD_SOUNDS_UPDATE` | `GUILD_EXPRESSIONS` | `soundboard_sounds`, `guild_id`, `old` |
| `SoundboardSounds` | `SOUNDBOARD_SOUNDS` | | `soundboard_sounds`, `guild_id`; a resposta a `bot.request_soundboard_sounds` |
| `AuditLogEntryCreate` | `GUILD_AUDIT_LOG_ENTRY_CREATE` | `GUILD_MODERATION` | `guild_id`, `entry` |
| `BanAdd` | `GUILD_BAN_ADD` | `GUILD_MODERATION` | `guild_id`, `user` |
| `BanRemove` | `GUILD_BAN_REMOVE` | `GUILD_MODERATION` | `guild_id`, `user` |
| `GuildIntegrationsUpdate` | `GUILD_INTEGRATIONS_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id` |
| `IntegrationCreate` | `INTEGRATION_CREATE` | `GUILD_INTEGRATIONS` | `guild_id`, `integration` |
| `IntegrationUpdate` | `INTEGRATION_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id`, `integration` |
| `IntegrationDelete` | `INTEGRATION_DELETE` | `GUILD_INTEGRATIONS` | `id`, `guild_id`, `application_id` |
| `WebhooksUpdate` | `WEBHOOKS_UPDATE` | `GUILD_WEBHOOKS` | `guild_id`, `channel_id` |
| `CommandPermissionsUpdate` | `APPLICATION_COMMAND_PERMISSIONS_UPDATE` | | `permissions` |

## Membros e presenças { #members-and-presences }

| Evento | Dispatch | Intents | Campos |
|---|---|---|---|
| `MemberJoin` | `GUILD_MEMBER_ADD` | `GUILD_MEMBERS` | `guild_id`, `member` |
| `MemberUpdate` | `GUILD_MEMBER_UPDATE` | `GUILD_MEMBERS` | `guild_id`, `user`, `roles`, `nick`, `joined_at`, `communication_disabled_until` e os outros campos de membro; `old` |
| `MemberRemove` | `GUILD_MEMBER_REMOVE` | `GUILD_MEMBERS` | `guild_id`, `user`, `member` (do cache) |
| `MembersChunk` | `GUILD_MEMBERS_CHUNK` | | `guild_id`, `members`, `presences`, `not_found`, `chunk_index`, `chunk_count`, `nonce`; a resposta a `bot.request_members` |
| `PresenceUpdate` | `PRESENCE_UPDATE` | `GUILD_PRESENCES` | `guild_id`, `presence`, `old` |

## Canais e tópicos { #channels-and-threads }

| Evento | Dispatch | Intents | Campos |
|---|---|---|---|
| `ChannelCreate` | `CHANNEL_CREATE` | `GUILDS` | `channel` |
| `ChannelUpdate` | `CHANNEL_UPDATE` | `GUILDS` | `channel`, `old` |
| `ChannelDelete` | `CHANNEL_DELETE` | `GUILDS` | `channel` |
| `ChannelPinsUpdate` | `CHANNEL_PINS_UPDATE` | `GUILDS`, `DIRECT_MESSAGES` | `guild_id`, `channel_id`, `last_pin_timestamp` |
| `ThreadCreate` | `THREAD_CREATE` | `GUILDS` | `channel`, `newly_created`, `member` |
| `ThreadUpdate` | `THREAD_UPDATE` | `GUILDS` | `channel`, `old` |
| `ThreadDelete` | `THREAD_DELETE` | `GUILDS` | `id`, `guild_id`, `parent_id`, `type`, `thread` (do cache) |
| `ThreadListSync` | `THREAD_LIST_SYNC` | `GUILDS` | `guild_id`, `channel_ids`, `threads`, `members` |
| `ThreadMemberUpdate` | `THREAD_MEMBER_UPDATE` | `GUILDS` | `guild_id`, `thread_member`, o do bot |
| `ThreadMembersUpdate` | `THREAD_MEMBERS_UPDATE` | `GUILDS`, `GUILD_MEMBERS` | `id`, `guild_id`, `member_count`, `added_members`, `removed_member_ids` |
| `StageInstanceCreate` | `STAGE_INSTANCE_CREATE` | `GUILDS` | `stage_instance` |
| `StageInstanceUpdate` | `STAGE_INSTANCE_UPDATE` | `GUILDS` | `stage_instance`, `old` |
| `StageInstanceDelete` | `STAGE_INSTANCE_DELETE` | `GUILDS` | `stage_instance` |
| `InviteCreate` | `INVITE_CREATE` | `GUILD_INVITES` | `code`, `channel_id`, `guild_id`, `inviter`, `max_age`, `max_uses`, `expires_at` e mais |
| `InviteDelete` | `INVITE_DELETE` | `GUILD_INVITES` | `channel_id`, `guild_id`, `code` |

## Mensagens, reações e enquetes { #messages-reactions-and-polls }

| Evento | Dispatch | Intents | Campos |
|---|---|---|---|
| `MessageCreate` | `MESSAGE_CREATE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `message` |
| `MessageUpdate` | `MESSAGE_UPDATE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `message`, `old` |
| `MessageDelete` | `MESSAGE_DELETE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `id`, `channel_id`, `guild_id`, `message` (do cache) |
| `MessageDeleteBulk` | `MESSAGE_DELETE_BULK` | `GUILD_MESSAGES` | `ids`, `channel_id`, `guild_id`, `messages` (do cache) |
| `TypingStart` | `TYPING_START` | `GUILD_MESSAGE_TYPING`, `DIRECT_MESSAGE_TYPING` | `channel_id`, `guild_id`, `user_id`, `timestamp`, `member` |
| `ReactionAdd` | `MESSAGE_REACTION_ADD` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `emoji`, `burst`, `type`, `member`, `message_author_id`, `burst_colors` |
| `ReactionRemove` | `MESSAGE_REACTION_REMOVE` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `emoji`, `burst`, `type` |
| `ReactionRemoveAll` | `MESSAGE_REACTION_REMOVE_ALL` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `channel_id`, `message_id`, `guild_id` |
| `ReactionRemoveEmoji` | `MESSAGE_REACTION_REMOVE_EMOJI` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `channel_id`, `message_id`, `guild_id`, `emoji` |
| `PollVoteAdd` | `MESSAGE_POLL_VOTE_ADD` | `GUILD_MESSAGE_POLLS`, `DIRECT_MESSAGE_POLLS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `answer_id` |
| `PollVoteRemove` | `MESSAGE_POLL_VOTE_REMOVE` | `GUILD_MESSAGE_POLLS`, `DIRECT_MESSAGE_POLLS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `answer_id` |

O conteúdo das mensagens, e o `content` de `AutoModerationActionExecution`, precisa da intent `MESSAGE_CONTENT`, exceto em mensagens diretas e em mensagens que mencionam o bot.

## Voz { #voice }

| Evento | Dispatch | Intents | Campos |
|---|---|---|---|
| `VoiceStateUpdate` | `VOICE_STATE_UPDATE` | `GUILD_VOICE_STATES` | `voice_state`, `old` |
| `VoiceServerUpdate` | `VOICE_SERVER_UPDATE` | | `token`, `guild_id`, `endpoint` |
| `VoiceChannelEffectSend` | `VOICE_CHANNEL_EFFECT_SEND` | `GUILD_VOICE_STATES` | `channel_id`, `guild_id`, `user_id`, `emoji`, `animation_type`, `animation_id`, `sound_id`, `sound_volume` |
| `VoiceChannelStatusUpdate` | `VOICE_CHANNEL_STATUS_UPDATE` | `GUILDS` | `id`, `guild_id`, `status` |
| `VoiceChannelStartTimeUpdate` | `VOICE_CHANNEL_START_TIME_UPDATE` | `GUILDS` | `id`, `guild_id`, `voice_start_time` |
| `ChannelInfo` | `CHANNEL_INFO` | | `guild_id`, `channels`; a resposta a `bot.request_channel_info` |

Os eventos das próprias conexões de voz do bot estão listados no [guia de voz](../voice/index.md#events).

## Interações, moderação e eventos agendados { #interactions-moderation-and-scheduled-events }

| Evento | Dispatch | Intents | Campos |
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

## Monetização { #monetization }

| Evento | Dispatch | Intents | Campos |
|---|---|---|---|
| `EntitlementCreate` | `ENTITLEMENT_CREATE` | | `entitlement`, um `PartialEntitlement` para um entitlement de teste |
| `EntitlementUpdate` | `ENTITLEMENT_UPDATE` | | `entitlement`; para uma assinatura, só quando ela termina |
| `EntitlementDelete` | `ENTITLEMENT_DELETE` | | `entitlement` |
| `SubscriptionCreate` | `SUBSCRIPTION_CREATE` | | `subscription` |
| `SubscriptionUpdate` | `SUBSCRIPTION_UPDATE` | | `subscription` |
| `SubscriptionDelete` | `SUBSCRIPTION_DELETE` | | `subscription` |

## Eventos de webhook { #webhook-events }

O Discord só envia estes para a Webhook Events URL do app, que o [`ASGIApp`](../guide/http-interactions.md) serve. Os eventos de entitlement acima também chegam lá.

| Evento | Tipo | Campos |
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

## Qualquer dispatch { #any-dispatch }

| Evento | Dispatch | Intents | Campos |
|---|---|---|---|
| `RawEvent` | qualquer um | | `name`, `seq`, `raw`, `raw_json`; para qualquer dispatch, modelado ou não |

## Eventos do bot { #events-of-the-bot }

Estes vêm do bostie, não do Discord; os nomes deles são do próprio bostie.

| Evento | Nome | Campos | Enviado quando |
|---|---|---|---|
| `Starting` | `bot.starting` | | `start()` está prestes a sincronizar e conectar; o REST funciona |
| `Started` | `bot.started` | | todos os shards estão prontos, uma vez por `start()` |
| `Stopping` | `bot.stopping` | | `close()` começou depois de `Started`; o gateway ainda está aberto |
| `Stopped` | `bot.stopped` | | `close()` terminou; o gateway e o REST deixaram de existir |
| `ShardReady` | `shard.ready` | `shard_id` | a nova sessão de um shard está pronta e os servidores dele chegaram |
| `ShardDisconnect` | `shard.disconnect` | `shard_id`, `code`, `reason`, `resuming` | um shard perdeu a conexão |
| `ShardStop` | `shard.stop` | `shard_id`, `code`, `reason` | um shard parou de vez |
| `ShardBackpressure` | `shard.backpressure` | `shard_id`, `queued_bytes` | um shard parou de ler porque o event loop ficou para trás |
| `ListenerError` | `bot.listener_error` | `listener`, `event`, `error` | um listener levantou uma exceção |
| `TaskError` | `bot.task_error` | `task`, `error` | uma tarefa em segundo plano levantou uma exceção |
