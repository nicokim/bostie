---
source: 74e134769abeab53fb9b321be4b35f2e68a57db8
description: "Chaque événement de gateway, de webhook et du bot livré par bostie, par thème, avec son nom de dispatch, les intents requis par Discord et ses champs."
---

# Liste des événements

Toutes les classes d'événements se trouvent dans `bostie.events`. Écoutez-en une avec `@bot.listen`, comme l'explique le [guide des événements](../guide/events.md). La colonne des intents nomme les intents dont Discord a besoin pour envoyer l'événement, un seul d'entre eux suffisant ; une cellule vide signifie que Discord l'envoie sans intent, ou seulement en réponse à une requête. Chaque événement de gateway a aussi `shard_id`, `raw_json` et `raw`. La [référence de l'API](api/events.md) donne chaque champ avec son type.

## Session { #session }

| Événement | Dispatch | Intents | Champs |
|---|---|---|---|
| `Ready` | `READY` | | `user`, `guilds` (indisponibles jusqu'à leur `GuildCreate`), `application`, `session_id`, `shard` |
| `Resumed` | `RESUMED` | | |
| `UserUpdate` | `USER_UPDATE` | | `user`, celui du bot ; `old` |
| `GatewayRateLimited` | `RATE_LIMITED` | | `opcode`, `retry_after`, `meta` |

## Serveurs { #guilds }

| Événement | Dispatch | Intents | Champs |
|---|---|---|---|
| `GuildCreate` | `GUILD_CREATE` | `GUILDS` | `guild`, `joined`, `channels`, `threads`, `voice_states`, `stage_instances`, `guild_scheduled_events`, `soundboard_sounds`, `members`, `presences` |
| `GuildUpdate` | `GUILD_UPDATE` | `GUILDS` | `guild`, `old` |
| `GuildDelete` | `GUILD_DELETE` | `GUILDS` | `guild_id`, `unavailable`, `guild` (en cache) |
| `RoleCreate` | `GUILD_ROLE_CREATE` | `GUILDS` | `guild_id`, `role` |
| `RoleUpdate` | `GUILD_ROLE_UPDATE` | `GUILDS` | `guild_id`, `role`, `old` |
| `RoleDelete` | `GUILD_ROLE_DELETE` | `GUILDS` | `guild_id`, `role_id`, `role` (en cache) |
| `EmojisUpdate` | `GUILD_EMOJIS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`, `emojis`, `old` |
| `StickersUpdate` | `GUILD_STICKERS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`, `stickers`, `old` |
| `SoundboardSoundCreate` | `GUILD_SOUNDBOARD_SOUND_CREATE` | `GUILD_EXPRESSIONS` | `sound` |
| `SoundboardSoundUpdate` | `GUILD_SOUNDBOARD_SOUND_UPDATE` | `GUILD_EXPRESSIONS` | `sound`, `old` |
| `SoundboardSoundDelete` | `GUILD_SOUNDBOARD_SOUND_DELETE` | `GUILD_EXPRESSIONS` | `sound_id`, `guild_id`, `sound` |
| `SoundboardSoundsUpdate` | `GUILD_SOUNDBOARD_SOUNDS_UPDATE` | `GUILD_EXPRESSIONS` | `soundboard_sounds`, `guild_id`, `old` |
| `SoundboardSounds` | `SOUNDBOARD_SOUNDS` | | `soundboard_sounds`, `guild_id` ; la réponse à `bot.request_soundboard_sounds` |
| `AuditLogEntryCreate` | `GUILD_AUDIT_LOG_ENTRY_CREATE` | `GUILD_MODERATION` | `guild_id`, `entry` |
| `BanAdd` | `GUILD_BAN_ADD` | `GUILD_MODERATION` | `guild_id`, `user` |
| `BanRemove` | `GUILD_BAN_REMOVE` | `GUILD_MODERATION` | `guild_id`, `user` |
| `GuildIntegrationsUpdate` | `GUILD_INTEGRATIONS_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id` |
| `IntegrationCreate` | `INTEGRATION_CREATE` | `GUILD_INTEGRATIONS` | `guild_id`, `integration` |
| `IntegrationUpdate` | `INTEGRATION_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id`, `integration` |
| `IntegrationDelete` | `INTEGRATION_DELETE` | `GUILD_INTEGRATIONS` | `id`, `guild_id`, `application_id` |
| `WebhooksUpdate` | `WEBHOOKS_UPDATE` | `GUILD_WEBHOOKS` | `guild_id`, `channel_id` |
| `CommandPermissionsUpdate` | `APPLICATION_COMMAND_PERMISSIONS_UPDATE` | | `permissions` |

## Membres et présences { #members-and-presences }

| Événement | Dispatch | Intents | Champs |
|---|---|---|---|
| `MemberJoin` | `GUILD_MEMBER_ADD` | `GUILD_MEMBERS` | `guild_id`, `member` |
| `MemberUpdate` | `GUILD_MEMBER_UPDATE` | `GUILD_MEMBERS` | `guild_id`, `user`, `roles`, `nick`, `joined_at`, `communication_disabled_until` et les autres champs du membre ; `old` |
| `MemberRemove` | `GUILD_MEMBER_REMOVE` | `GUILD_MEMBERS` | `guild_id`, `user`, `member` (en cache) |
| `MembersChunk` | `GUILD_MEMBERS_CHUNK` | | `guild_id`, `members`, `presences`, `not_found`, `chunk_index`, `chunk_count`, `nonce` ; la réponse à `bot.request_members` |
| `PresenceUpdate` | `PRESENCE_UPDATE` | `GUILD_PRESENCES` | `guild_id`, `presence`, `old` |

## Salons et fils { #channels-and-threads }

| Événement | Dispatch | Intents | Champs |
|---|---|---|---|
| `ChannelCreate` | `CHANNEL_CREATE` | `GUILDS` | `channel` |
| `ChannelUpdate` | `CHANNEL_UPDATE` | `GUILDS` | `channel`, `old` |
| `ChannelDelete` | `CHANNEL_DELETE` | `GUILDS` | `channel` |
| `ChannelPinsUpdate` | `CHANNEL_PINS_UPDATE` | `GUILDS`, `DIRECT_MESSAGES` | `guild_id`, `channel_id`, `last_pin_timestamp` |
| `ThreadCreate` | `THREAD_CREATE` | `GUILDS` | `channel`, `newly_created`, `member` |
| `ThreadUpdate` | `THREAD_UPDATE` | `GUILDS` | `channel`, `old` |
| `ThreadDelete` | `THREAD_DELETE` | `GUILDS` | `id`, `guild_id`, `parent_id`, `type`, `thread` (en cache) |
| `ThreadListSync` | `THREAD_LIST_SYNC` | `GUILDS` | `guild_id`, `channel_ids`, `threads`, `members` |
| `ThreadMemberUpdate` | `THREAD_MEMBER_UPDATE` | `GUILDS` | `guild_id`, `thread_member`, celui du bot |
| `ThreadMembersUpdate` | `THREAD_MEMBERS_UPDATE` | `GUILDS`, `GUILD_MEMBERS` | `id`, `guild_id`, `member_count`, `added_members`, `removed_member_ids` |
| `StageInstanceCreate` | `STAGE_INSTANCE_CREATE` | `GUILDS` | `stage_instance` |
| `StageInstanceUpdate` | `STAGE_INSTANCE_UPDATE` | `GUILDS` | `stage_instance`, `old` |
| `StageInstanceDelete` | `STAGE_INSTANCE_DELETE` | `GUILDS` | `stage_instance` |
| `InviteCreate` | `INVITE_CREATE` | `GUILD_INVITES` | `code`, `channel_id`, `guild_id`, `inviter`, `max_age`, `max_uses`, `expires_at` et d'autres |
| `InviteDelete` | `INVITE_DELETE` | `GUILD_INVITES` | `channel_id`, `guild_id`, `code` |

## Messages, réactions et sondages { #messages-reactions-and-polls }

| Événement | Dispatch | Intents | Champs |
|---|---|---|---|
| `MessageCreate` | `MESSAGE_CREATE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `message` |
| `MessageUpdate` | `MESSAGE_UPDATE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `message`, `old` |
| `MessageDelete` | `MESSAGE_DELETE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `id`, `channel_id`, `guild_id`, `message` (en cache) |
| `MessageDeleteBulk` | `MESSAGE_DELETE_BULK` | `GUILD_MESSAGES` | `ids`, `channel_id`, `guild_id`, `messages` (en cache) |
| `TypingStart` | `TYPING_START` | `GUILD_MESSAGE_TYPING`, `DIRECT_MESSAGE_TYPING` | `channel_id`, `guild_id`, `user_id`, `timestamp`, `member` |
| `ReactionAdd` | `MESSAGE_REACTION_ADD` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `emoji`, `burst`, `type`, `member`, `message_author_id`, `burst_colors` |
| `ReactionRemove` | `MESSAGE_REACTION_REMOVE` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `emoji`, `burst`, `type` |
| `ReactionRemoveAll` | `MESSAGE_REACTION_REMOVE_ALL` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `channel_id`, `message_id`, `guild_id` |
| `ReactionRemoveEmoji` | `MESSAGE_REACTION_REMOVE_EMOJI` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `channel_id`, `message_id`, `guild_id`, `emoji` |
| `PollVoteAdd` | `MESSAGE_POLL_VOTE_ADD` | `GUILD_MESSAGE_POLLS`, `DIRECT_MESSAGE_POLLS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `answer_id` |
| `PollVoteRemove` | `MESSAGE_POLL_VOTE_REMOVE` | `GUILD_MESSAGE_POLLS`, `DIRECT_MESSAGE_POLLS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `answer_id` |

Le contenu des messages, ainsi que le `content` de `AutoModerationActionExecution`, nécessite l'intent `MESSAGE_CONTENT`, sauf dans les messages privés et les messages qui mentionnent le bot.

## Voix { #voice }

| Événement | Dispatch | Intents | Champs |
|---|---|---|---|
| `VoiceStateUpdate` | `VOICE_STATE_UPDATE` | `GUILD_VOICE_STATES` | `voice_state`, `old` |
| `VoiceServerUpdate` | `VOICE_SERVER_UPDATE` | | `token`, `guild_id`, `endpoint` |
| `VoiceChannelEffectSend` | `VOICE_CHANNEL_EFFECT_SEND` | `GUILD_VOICE_STATES` | `channel_id`, `guild_id`, `user_id`, `emoji`, `animation_type`, `animation_id`, `sound_id`, `sound_volume` |
| `VoiceChannelStatusUpdate` | `VOICE_CHANNEL_STATUS_UPDATE` | `GUILDS` | `id`, `guild_id`, `status` |
| `VoiceChannelStartTimeUpdate` | `VOICE_CHANNEL_START_TIME_UPDATE` | `GUILDS` | `id`, `guild_id`, `voice_start_time` |
| `ChannelInfo` | `CHANNEL_INFO` | | `guild_id`, `channels` ; la réponse à `bot.request_channel_info` |

Les événements des propres connexions vocales du bot sont listés dans le [guide de la voix](../voice/index.md#events).

## Interactions, modération et événements programmés { #interactions-moderation-and-scheduled-events }

| Événement | Dispatch | Intents | Champs |
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

## Monétisation { #monetization }

| Événement | Dispatch | Intents | Champs |
|---|---|---|---|
| `EntitlementCreate` | `ENTITLEMENT_CREATE` | | `entitlement`, un `PartialEntitlement` pour un entitlement de test |
| `EntitlementUpdate` | `ENTITLEMENT_UPDATE` | | `entitlement` ; pour un abonnement, seulement quand il prend fin |
| `EntitlementDelete` | `ENTITLEMENT_DELETE` | | `entitlement` |
| `SubscriptionCreate` | `SUBSCRIPTION_CREATE` | | `subscription` |
| `SubscriptionUpdate` | `SUBSCRIPTION_UPDATE` | | `subscription` |
| `SubscriptionDelete` | `SUBSCRIPTION_DELETE` | | `subscription` |

## Événements webhook { #webhook-events }

Discord ne les envoie qu'à l'URL Webhook Events de l'application, que sert [`ASGIApp`](../guide/http-interactions.md). Les événements d'entitlement ci-dessus y arrivent aussi.

| Événement | Type | Champs |
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

## N'importe quel dispatch { #any-dispatch }

| Événement | Dispatch | Intents | Champs |
|---|---|---|---|
| `RawEvent` | n'importe lequel | | `name`, `seq`, `raw`, `raw_json` ; pour n'importe quel dispatch, modélisé ou non |

## Événements du bot { #events-of-the-bot }

Ceux-ci viennent de bostie, pas de Discord ; leurs noms sont propres à bostie.

| Événement | Nom | Champs | Envoyé quand |
|---|---|---|---|
| `Starting` | `bot.starting` | | `start()` est sur le point de synchroniser et de se connecter ; REST fonctionne |
| `Started` | `bot.started` | | tous les shards sont prêts, une fois par `start()` |
| `Stopping` | `bot.stopping` | | `close()` a commencé après `Started` ; la gateway est encore ouverte |
| `Stopped` | `bot.stopped` | | `close()` est terminé ; la gateway et REST ne sont plus là |
| `ShardReady` | `shard.ready` | `shard_id` | la nouvelle session d'un shard est prête et ses serveurs sont arrivés |
| `ShardDisconnect` | `shard.disconnect` | `shard_id`, `code`, `reason`, `resuming` | un shard a perdu sa connexion |
| `ShardStop` | `shard.stop` | `shard_id`, `code`, `reason` | un shard s'est arrêté définitivement |
| `ShardBackpressure` | `shard.backpressure` | `shard_id`, `queued_bytes` | un shard a suspendu la lecture parce que la boucle d'événements prend du retard |
| `ListenerError` | `bot.listener_error` | `listener`, `event`, `error` | un écouteur a levé une exception |
| `TaskError` | `bot.task_error` | `task`, `error` | une tâche de fond a levé une exception |
