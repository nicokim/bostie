---
source: 74e134769abeab53fb9b321be4b35f2e68a57db8
description: "Jedes Gateway-, Webhook- und Bot-Event von bostie, nach Thema gruppiert, mit Dispatch-Namen, den Intents, die Discord zum Senden braucht, und seinen Feldern."
---

# Event-Liste

Jede Event-Klasse liegt in `bostie.events`. Du hörst mit `@bot.listen` auf eine, wie die [Anleitung zu Events](../guide/events.md) erklärt. Die Spalte Intents nennt die Intents, die Discord braucht, um das Event zu senden, wobei jeder einzelne davon genügt; eine leere Zelle bedeutet, dass Discord es ohne Intent sendet oder nur als Antwort auf eine Anfrage. Jedes Gateway-Event hat außerdem `shard_id`, `raw_json` und `raw`. Die [API-Referenz](api/events.md) enthält jedes Feld mit seinem Typ.

## Sitzung { #session }

| Event | Dispatch | Intents | Felder |
|---|---|---|---|
| `Ready` | `READY` | | `user`, `guilds` (nicht verfügbar bis zu ihrem `GuildCreate`), `application`, `session_id`, `shard` |
| `Resumed` | `RESUMED` | | |
| `UserUpdate` | `USER_UPDATE` | | `user`, der des Bots; `old` |
| `GatewayRateLimited` | `RATE_LIMITED` | | `opcode`, `retry_after`, `meta` |

## Server { #guilds }

| Event | Dispatch | Intents | Felder |
|---|---|---|---|
| `GuildCreate` | `GUILD_CREATE` | `GUILDS` | `guild`, `joined`, `channels`, `threads`, `voice_states`, `stage_instances`, `guild_scheduled_events`, `soundboard_sounds`, `members`, `presences` |
| `GuildUpdate` | `GUILD_UPDATE` | `GUILDS` | `guild`, `old` |
| `GuildDelete` | `GUILD_DELETE` | `GUILDS` | `guild_id`, `unavailable`, `guild` (gecacht) |
| `RoleCreate` | `GUILD_ROLE_CREATE` | `GUILDS` | `guild_id`, `role` |
| `RoleUpdate` | `GUILD_ROLE_UPDATE` | `GUILDS` | `guild_id`, `role`, `old` |
| `RoleDelete` | `GUILD_ROLE_DELETE` | `GUILDS` | `guild_id`, `role_id`, `role` (gecacht) |
| `EmojisUpdate` | `GUILD_EMOJIS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`, `emojis`, `old` |
| `StickersUpdate` | `GUILD_STICKERS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`, `stickers`, `old` |
| `SoundboardSoundCreate` | `GUILD_SOUNDBOARD_SOUND_CREATE` | `GUILD_EXPRESSIONS` | `sound` |
| `SoundboardSoundUpdate` | `GUILD_SOUNDBOARD_SOUND_UPDATE` | `GUILD_EXPRESSIONS` | `sound`, `old` |
| `SoundboardSoundDelete` | `GUILD_SOUNDBOARD_SOUND_DELETE` | `GUILD_EXPRESSIONS` | `sound_id`, `guild_id`, `sound` |
| `SoundboardSoundsUpdate` | `GUILD_SOUNDBOARD_SOUNDS_UPDATE` | `GUILD_EXPRESSIONS` | `soundboard_sounds`, `guild_id`, `old` |
| `SoundboardSounds` | `SOUNDBOARD_SOUNDS` | | `soundboard_sounds`, `guild_id`; die Antwort auf `bot.request_soundboard_sounds` |
| `AuditLogEntryCreate` | `GUILD_AUDIT_LOG_ENTRY_CREATE` | `GUILD_MODERATION` | `guild_id`, `entry` |
| `BanAdd` | `GUILD_BAN_ADD` | `GUILD_MODERATION` | `guild_id`, `user` |
| `BanRemove` | `GUILD_BAN_REMOVE` | `GUILD_MODERATION` | `guild_id`, `user` |
| `GuildIntegrationsUpdate` | `GUILD_INTEGRATIONS_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id` |
| `IntegrationCreate` | `INTEGRATION_CREATE` | `GUILD_INTEGRATIONS` | `guild_id`, `integration` |
| `IntegrationUpdate` | `INTEGRATION_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id`, `integration` |
| `IntegrationDelete` | `INTEGRATION_DELETE` | `GUILD_INTEGRATIONS` | `id`, `guild_id`, `application_id` |
| `WebhooksUpdate` | `WEBHOOKS_UPDATE` | `GUILD_WEBHOOKS` | `guild_id`, `channel_id` |
| `CommandPermissionsUpdate` | `APPLICATION_COMMAND_PERMISSIONS_UPDATE` | | `permissions` |

## Mitglieder und Präsenzen { #members-and-presences }

| Event | Dispatch | Intents | Felder |
|---|---|---|---|
| `MemberJoin` | `GUILD_MEMBER_ADD` | `GUILD_MEMBERS` | `guild_id`, `member` |
| `MemberUpdate` | `GUILD_MEMBER_UPDATE` | `GUILD_MEMBERS` | `guild_id`, `user`, `roles`, `nick`, `joined_at`, `communication_disabled_until` und die übrigen Mitgliederfelder; `old` |
| `MemberRemove` | `GUILD_MEMBER_REMOVE` | `GUILD_MEMBERS` | `guild_id`, `user`, `member` (gecacht) |
| `MembersChunk` | `GUILD_MEMBERS_CHUNK` | | `guild_id`, `members`, `presences`, `not_found`, `chunk_index`, `chunk_count`, `nonce`; die Antwort auf `bot.request_members` |
| `PresenceUpdate` | `PRESENCE_UPDATE` | `GUILD_PRESENCES` | `guild_id`, `presence`, `old` |

## Kanäle und Threads { #channels-and-threads }

| Event | Dispatch | Intents | Felder |
|---|---|---|---|
| `ChannelCreate` | `CHANNEL_CREATE` | `GUILDS` | `channel` |
| `ChannelUpdate` | `CHANNEL_UPDATE` | `GUILDS` | `channel`, `old` |
| `ChannelDelete` | `CHANNEL_DELETE` | `GUILDS` | `channel` |
| `ChannelPinsUpdate` | `CHANNEL_PINS_UPDATE` | `GUILDS`, `DIRECT_MESSAGES` | `guild_id`, `channel_id`, `last_pin_timestamp` |
| `ThreadCreate` | `THREAD_CREATE` | `GUILDS` | `channel`, `newly_created`, `member` |
| `ThreadUpdate` | `THREAD_UPDATE` | `GUILDS` | `channel`, `old` |
| `ThreadDelete` | `THREAD_DELETE` | `GUILDS` | `id`, `guild_id`, `parent_id`, `type`, `thread` (gecacht) |
| `ThreadListSync` | `THREAD_LIST_SYNC` | `GUILDS` | `guild_id`, `channel_ids`, `threads`, `members` |
| `ThreadMemberUpdate` | `THREAD_MEMBER_UPDATE` | `GUILDS` | `guild_id`, `thread_member`, das des Bots |
| `ThreadMembersUpdate` | `THREAD_MEMBERS_UPDATE` | `GUILDS`, `GUILD_MEMBERS` | `id`, `guild_id`, `member_count`, `added_members`, `removed_member_ids` |
| `StageInstanceCreate` | `STAGE_INSTANCE_CREATE` | `GUILDS` | `stage_instance` |
| `StageInstanceUpdate` | `STAGE_INSTANCE_UPDATE` | `GUILDS` | `stage_instance`, `old` |
| `StageInstanceDelete` | `STAGE_INSTANCE_DELETE` | `GUILDS` | `stage_instance` |
| `InviteCreate` | `INVITE_CREATE` | `GUILD_INVITES` | `code`, `channel_id`, `guild_id`, `inviter`, `max_age`, `max_uses`, `expires_at` und mehr |
| `InviteDelete` | `INVITE_DELETE` | `GUILD_INVITES` | `channel_id`, `guild_id`, `code` |

## Nachrichten, Reaktionen und Umfragen { #messages-reactions-and-polls }

| Event | Dispatch | Intents | Felder |
|---|---|---|---|
| `MessageCreate` | `MESSAGE_CREATE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `message` |
| `MessageUpdate` | `MESSAGE_UPDATE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `message`, `old` |
| `MessageDelete` | `MESSAGE_DELETE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `id`, `channel_id`, `guild_id`, `message` (gecacht) |
| `MessageDeleteBulk` | `MESSAGE_DELETE_BULK` | `GUILD_MESSAGES` | `ids`, `channel_id`, `guild_id`, `messages` (gecacht) |
| `TypingStart` | `TYPING_START` | `GUILD_MESSAGE_TYPING`, `DIRECT_MESSAGE_TYPING` | `channel_id`, `guild_id`, `user_id`, `timestamp`, `member` |
| `ReactionAdd` | `MESSAGE_REACTION_ADD` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `emoji`, `burst`, `type`, `member`, `message_author_id`, `burst_colors` |
| `ReactionRemove` | `MESSAGE_REACTION_REMOVE` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `emoji`, `burst`, `type` |
| `ReactionRemoveAll` | `MESSAGE_REACTION_REMOVE_ALL` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `channel_id`, `message_id`, `guild_id` |
| `ReactionRemoveEmoji` | `MESSAGE_REACTION_REMOVE_EMOJI` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `channel_id`, `message_id`, `guild_id`, `emoji` |
| `PollVoteAdd` | `MESSAGE_POLL_VOTE_ADD` | `GUILD_MESSAGE_POLLS`, `DIRECT_MESSAGE_POLLS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `answer_id` |
| `PollVoteRemove` | `MESSAGE_POLL_VOTE_REMOVE` | `GUILD_MESSAGE_POLLS`, `DIRECT_MESSAGE_POLLS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `answer_id` |

Der Inhalt von Nachrichten und das `content` von `AutoModerationActionExecution` brauchen den Intent `MESSAGE_CONTENT`, außer in DMs und bei Nachrichten, die den Bot erwähnen.

## Sprachkanäle { #voice }

| Event | Dispatch | Intents | Felder |
|---|---|---|---|
| `VoiceStateUpdate` | `VOICE_STATE_UPDATE` | `GUILD_VOICE_STATES` | `voice_state`, `old` |
| `VoiceServerUpdate` | `VOICE_SERVER_UPDATE` | | `token`, `guild_id`, `endpoint` |
| `VoiceChannelEffectSend` | `VOICE_CHANNEL_EFFECT_SEND` | `GUILD_VOICE_STATES` | `channel_id`, `guild_id`, `user_id`, `emoji`, `animation_type`, `animation_id`, `sound_id`, `sound_volume` |
| `VoiceChannelStatusUpdate` | `VOICE_CHANNEL_STATUS_UPDATE` | `GUILDS` | `id`, `guild_id`, `status` |
| `VoiceChannelStartTimeUpdate` | `VOICE_CHANNEL_START_TIME_UPDATE` | `GUILDS` | `id`, `guild_id`, `voice_start_time` |
| `ChannelInfo` | `CHANNEL_INFO` | | `guild_id`, `channels`; die Antwort auf `bot.request_channel_info` |

Die Events der eigenen Sprachverbindungen des Bots stehen in der [Anleitung zu Sprachverbindungen](../voice/index.md#events).

## Interaktionen, Moderation und geplante Events { #interactions-moderation-and-scheduled-events }

| Event | Dispatch | Intents | Felder |
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

## Monetarisierung { #monetization }

| Event | Dispatch | Intents | Felder |
|---|---|---|---|
| `EntitlementCreate` | `ENTITLEMENT_CREATE` | | `entitlement`, ein `PartialEntitlement` bei einem Test-Entitlement |
| `EntitlementUpdate` | `ENTITLEMENT_UPDATE` | | `entitlement`; bei einem Abonnement nur, wenn es endet |
| `EntitlementDelete` | `ENTITLEMENT_DELETE` | | `entitlement` |
| `SubscriptionCreate` | `SUBSCRIPTION_CREATE` | | `subscription` |
| `SubscriptionUpdate` | `SUBSCRIPTION_UPDATE` | | `subscription` |
| `SubscriptionDelete` | `SUBSCRIPTION_DELETE` | | `subscription` |

## Webhook-Events { #webhook-events }

Discord sendet diese nur an die Webhook Events URL der App, die [`ASGIApp`](../guide/http-interactions.md) bedient. Die Entitlement-Events oben kommen dort ebenfalls an.

| Event | Typ | Felder |
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

## Beliebiger Dispatch { #any-dispatch }

| Event | Dispatch | Intents | Felder |
|---|---|---|---|
| `RawEvent` | beliebig | | `name`, `seq`, `raw`, `raw_json`; für jeden Dispatch, ob modelliert oder nicht |

## Events des Bots { #events-of-the-bot }

Diese kommen von bostie, nicht von Discord; ihre Namen sind die eigenen von bostie.

| Event | Name | Felder | Gesendet, wenn |
|---|---|---|---|
| `Starting` | `bot.starting` | | `start()` gleich synchronisiert und sich verbindet; REST funktioniert |
| `Started` | `bot.started` | | jeder Shard bereit ist, einmal pro `start()` |
| `Stopping` | `bot.stopping` | | `close()` nach `Started` begonnen hat; das Gateway ist noch offen |
| `Stopped` | `bot.stopped` | | `close()` fertig ist; Gateway und REST sind weg |
| `ShardReady` | `shard.ready` | `shard_id` | die neue Sitzung eines Shards bereit ist und seine Server angekommen sind |
| `ShardDisconnect` | `shard.disconnect` | `shard_id`, `code`, `reason`, `resuming` | ein Shard seine Verbindung verloren hat |
| `ShardStop` | `shard.stop` | `shard_id`, `code`, `reason` | ein Shard endgültig gestoppt hat |
| `ShardBackpressure` | `shard.backpressure` | `shard_id`, `queued_bytes` | ein Shard das Lesen pausiert hat, weil die Event-Loop zurückfällt |
| `ListenerError` | `bot.listener_error` | `listener`, `event`, `error` | ein Listener eine Exception geworfen hat |
| `TaskError` | `bot.task_error` | `task`, `error` | ein Hintergrund-Task eine Exception geworfen hat |
