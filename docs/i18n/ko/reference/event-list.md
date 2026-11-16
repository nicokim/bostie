---
source: 74e134769abeab53fb9b321be4b35f2e68a57db8
description: "bostie가 전달하는 모든 게이트웨이, 웹후크, 봇 이벤트를 주제별로 모아 디스패치 이름, 필요한 인텐트, 필드를 정리합니다."
---

# 이벤트 목록

모든 이벤트 클래스는 `bostie.events`에 있습니다. [이벤트 가이드](../guide/events.md)에서 설명하듯이 `@bot.listen`으로 수신합니다. 인텐트 열에는 Discord가 그 이벤트를 보내는 데 필요한 인텐트가 있으며, 그중 하나만 있어도 됩니다. 빈 칸은 Discord가 인텐트 없이 보내거나, 요청에 대한 응답으로만 보낸다는 뜻입니다. 모든 게이트웨이 이벤트에는 `shard_id`, `raw_json`, `raw`도 있습니다. 모든 필드와 그 타입은 [API 레퍼런스](api/events.md)에 있습니다.

## 세션 { #session }

| 이벤트 | 디스패치 | 인텐트 | 필드 |
|---|---|---|---|
| `Ready` | `READY` | | `user`, `guilds`(각 서버의 `GuildCreate`가 올 때까지 사용할 수 없는 상태), `application`, `session_id`, `shard` |
| `Resumed` | `RESUMED` | | |
| `UserUpdate` | `USER_UPDATE` | | 봇 자신의 `user`, `old` |
| `GatewayRateLimited` | `RATE_LIMITED` | | `opcode`, `retry_after`, `meta` |

## 서버 { #guilds }

| 이벤트 | 디스패치 | 인텐트 | 필드 |
|---|---|---|---|
| `GuildCreate` | `GUILD_CREATE` | `GUILDS` | `guild`, `joined`, `channels`, `threads`, `voice_states`, `stage_instances`, `guild_scheduled_events`, `soundboard_sounds`, `members`, `presences` |
| `GuildUpdate` | `GUILD_UPDATE` | `GUILDS` | `guild`, `old` |
| `GuildDelete` | `GUILD_DELETE` | `GUILDS` | `guild_id`, `unavailable`, `guild`(캐시된 값) |
| `RoleCreate` | `GUILD_ROLE_CREATE` | `GUILDS` | `guild_id`, `role` |
| `RoleUpdate` | `GUILD_ROLE_UPDATE` | `GUILDS` | `guild_id`, `role`, `old` |
| `RoleDelete` | `GUILD_ROLE_DELETE` | `GUILDS` | `guild_id`, `role_id`, `role`(캐시된 값) |
| `EmojisUpdate` | `GUILD_EMOJIS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`, `emojis`, `old` |
| `StickersUpdate` | `GUILD_STICKERS_UPDATE` | `GUILD_EXPRESSIONS` | `guild_id`, `stickers`, `old` |
| `SoundboardSoundCreate` | `GUILD_SOUNDBOARD_SOUND_CREATE` | `GUILD_EXPRESSIONS` | `sound` |
| `SoundboardSoundUpdate` | `GUILD_SOUNDBOARD_SOUND_UPDATE` | `GUILD_EXPRESSIONS` | `sound`, `old` |
| `SoundboardSoundDelete` | `GUILD_SOUNDBOARD_SOUND_DELETE` | `GUILD_EXPRESSIONS` | `sound_id`, `guild_id`, `sound` |
| `SoundboardSoundsUpdate` | `GUILD_SOUNDBOARD_SOUNDS_UPDATE` | `GUILD_EXPRESSIONS` | `soundboard_sounds`, `guild_id`, `old` |
| `SoundboardSounds` | `SOUNDBOARD_SOUNDS` | | `soundboard_sounds`, `guild_id`. `bot.request_soundboard_sounds`에 대한 응답 |
| `AuditLogEntryCreate` | `GUILD_AUDIT_LOG_ENTRY_CREATE` | `GUILD_MODERATION` | `guild_id`, `entry` |
| `BanAdd` | `GUILD_BAN_ADD` | `GUILD_MODERATION` | `guild_id`, `user` |
| `BanRemove` | `GUILD_BAN_REMOVE` | `GUILD_MODERATION` | `guild_id`, `user` |
| `GuildIntegrationsUpdate` | `GUILD_INTEGRATIONS_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id` |
| `IntegrationCreate` | `INTEGRATION_CREATE` | `GUILD_INTEGRATIONS` | `guild_id`, `integration` |
| `IntegrationUpdate` | `INTEGRATION_UPDATE` | `GUILD_INTEGRATIONS` | `guild_id`, `integration` |
| `IntegrationDelete` | `INTEGRATION_DELETE` | `GUILD_INTEGRATIONS` | `id`, `guild_id`, `application_id` |
| `WebhooksUpdate` | `WEBHOOKS_UPDATE` | `GUILD_WEBHOOKS` | `guild_id`, `channel_id` |
| `CommandPermissionsUpdate` | `APPLICATION_COMMAND_PERMISSIONS_UPDATE` | | `permissions` |

## 멤버와 프레즌스 { #members-and-presences }

| 이벤트 | 디스패치 | 인텐트 | 필드 |
|---|---|---|---|
| `MemberJoin` | `GUILD_MEMBER_ADD` | `GUILD_MEMBERS` | `guild_id`, `member` |
| `MemberUpdate` | `GUILD_MEMBER_UPDATE` | `GUILD_MEMBERS` | `guild_id`, `user`, `roles`, `nick`, `joined_at`, `communication_disabled_until` 및 그 밖의 멤버 필드, `old` |
| `MemberRemove` | `GUILD_MEMBER_REMOVE` | `GUILD_MEMBERS` | `guild_id`, `user`, `member`(캐시된 값) |
| `MembersChunk` | `GUILD_MEMBERS_CHUNK` | | `guild_id`, `members`, `presences`, `not_found`, `chunk_index`, `chunk_count`, `nonce`. `bot.request_members`에 대한 응답 |
| `PresenceUpdate` | `PRESENCE_UPDATE` | `GUILD_PRESENCES` | `guild_id`, `presence`, `old` |

## 채널과 스레드 { #channels-and-threads }

| 이벤트 | 디스패치 | 인텐트 | 필드 |
|---|---|---|---|
| `ChannelCreate` | `CHANNEL_CREATE` | `GUILDS` | `channel` |
| `ChannelUpdate` | `CHANNEL_UPDATE` | `GUILDS` | `channel`, `old` |
| `ChannelDelete` | `CHANNEL_DELETE` | `GUILDS` | `channel` |
| `ChannelPinsUpdate` | `CHANNEL_PINS_UPDATE` | `GUILDS`, `DIRECT_MESSAGES` | `guild_id`, `channel_id`, `last_pin_timestamp` |
| `ThreadCreate` | `THREAD_CREATE` | `GUILDS` | `channel`, `newly_created`, `member` |
| `ThreadUpdate` | `THREAD_UPDATE` | `GUILDS` | `channel`, `old` |
| `ThreadDelete` | `THREAD_DELETE` | `GUILDS` | `id`, `guild_id`, `parent_id`, `type`, `thread`(캐시된 값) |
| `ThreadListSync` | `THREAD_LIST_SYNC` | `GUILDS` | `guild_id`, `channel_ids`, `threads`, `members` |
| `ThreadMemberUpdate` | `THREAD_MEMBER_UPDATE` | `GUILDS` | `guild_id`, 봇 자신의 `thread_member` |
| `ThreadMembersUpdate` | `THREAD_MEMBERS_UPDATE` | `GUILDS`, `GUILD_MEMBERS` | `id`, `guild_id`, `member_count`, `added_members`, `removed_member_ids` |
| `StageInstanceCreate` | `STAGE_INSTANCE_CREATE` | `GUILDS` | `stage_instance` |
| `StageInstanceUpdate` | `STAGE_INSTANCE_UPDATE` | `GUILDS` | `stage_instance`, `old` |
| `StageInstanceDelete` | `STAGE_INSTANCE_DELETE` | `GUILDS` | `stage_instance` |
| `InviteCreate` | `INVITE_CREATE` | `GUILD_INVITES` | `code`, `channel_id`, `guild_id`, `inviter`, `max_age`, `max_uses`, `expires_at` 등 |
| `InviteDelete` | `INVITE_DELETE` | `GUILD_INVITES` | `channel_id`, `guild_id`, `code` |

## 메시지, 반응, 투표 { #messages-reactions-and-polls }

| 이벤트 | 디스패치 | 인텐트 | 필드 |
|---|---|---|---|
| `MessageCreate` | `MESSAGE_CREATE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `message` |
| `MessageUpdate` | `MESSAGE_UPDATE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `message`, `old` |
| `MessageDelete` | `MESSAGE_DELETE` | `GUILD_MESSAGES`, `DIRECT_MESSAGES` | `id`, `channel_id`, `guild_id`, `message`(캐시된 값) |
| `MessageDeleteBulk` | `MESSAGE_DELETE_BULK` | `GUILD_MESSAGES` | `ids`, `channel_id`, `guild_id`, `messages`(캐시된 값) |
| `TypingStart` | `TYPING_START` | `GUILD_MESSAGE_TYPING`, `DIRECT_MESSAGE_TYPING` | `channel_id`, `guild_id`, `user_id`, `timestamp`, `member` |
| `ReactionAdd` | `MESSAGE_REACTION_ADD` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `emoji`, `burst`, `type`, `member`, `message_author_id`, `burst_colors` |
| `ReactionRemove` | `MESSAGE_REACTION_REMOVE` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `emoji`, `burst`, `type` |
| `ReactionRemoveAll` | `MESSAGE_REACTION_REMOVE_ALL` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `channel_id`, `message_id`, `guild_id` |
| `ReactionRemoveEmoji` | `MESSAGE_REACTION_REMOVE_EMOJI` | `GUILD_MESSAGE_REACTIONS`, `DIRECT_MESSAGE_REACTIONS` | `channel_id`, `message_id`, `guild_id`, `emoji` |
| `PollVoteAdd` | `MESSAGE_POLL_VOTE_ADD` | `GUILD_MESSAGE_POLLS`, `DIRECT_MESSAGE_POLLS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `answer_id` |
| `PollVoteRemove` | `MESSAGE_POLL_VOTE_REMOVE` | `GUILD_MESSAGE_POLLS`, `DIRECT_MESSAGE_POLLS` | `user_id`, `channel_id`, `message_id`, `guild_id`, `answer_id` |

메시지 내용과 `AutoModerationActionExecution`의 `content`에는 `MESSAGE_CONTENT` 인텐트가 필요합니다. 단, 개인 메시지(DM)와 봇을 멘션한 메시지는 예외입니다.

## 음성 { #voice }

| 이벤트 | 디스패치 | 인텐트 | 필드 |
|---|---|---|---|
| `VoiceStateUpdate` | `VOICE_STATE_UPDATE` | `GUILD_VOICE_STATES` | `voice_state`, `old` |
| `VoiceServerUpdate` | `VOICE_SERVER_UPDATE` | | `token`, `guild_id`, `endpoint` |
| `VoiceChannelEffectSend` | `VOICE_CHANNEL_EFFECT_SEND` | `GUILD_VOICE_STATES` | `channel_id`, `guild_id`, `user_id`, `emoji`, `animation_type`, `animation_id`, `sound_id`, `sound_volume` |
| `VoiceChannelStatusUpdate` | `VOICE_CHANNEL_STATUS_UPDATE` | `GUILDS` | `id`, `guild_id`, `status` |
| `VoiceChannelStartTimeUpdate` | `VOICE_CHANNEL_START_TIME_UPDATE` | `GUILDS` | `id`, `guild_id`, `voice_start_time` |
| `ChannelInfo` | `CHANNEL_INFO` | | `guild_id`, `channels`. `bot.request_channel_info`에 대한 응답 |

봇 자신의 음성 연결에서 오는 이벤트는 [음성 가이드](../voice/index.md#events)에 정리되어 있습니다.

## 인터랙션, 관리, 예약 이벤트 { #interactions-moderation-and-scheduled-events }

| 이벤트 | 디스패치 | 인텐트 | 필드 |
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

## 수익화 { #monetization }

| 이벤트 | 디스패치 | 인텐트 | 필드 |
|---|---|---|---|
| `EntitlementCreate` | `ENTITLEMENT_CREATE` | | `entitlement`. 테스트 엔타이틀먼트이면 `PartialEntitlement` |
| `EntitlementUpdate` | `ENTITLEMENT_UPDATE` | | `entitlement`. 구독은 끝날 때만 발생 |
| `EntitlementDelete` | `ENTITLEMENT_DELETE` | | `entitlement` |
| `SubscriptionCreate` | `SUBSCRIPTION_CREATE` | | `subscription` |
| `SubscriptionUpdate` | `SUBSCRIPTION_UPDATE` | | `subscription` |
| `SubscriptionDelete` | `SUBSCRIPTION_DELETE` | | `subscription` |

## 웹후크 이벤트 { #webhook-events }

Discord는 이 이벤트들을 앱의 Webhook Events URL로만 보내며, 이 URL은 [`ASGIApp`](../guide/http-interactions.md)이 처리합니다. 위의 엔타이틀먼트 이벤트도 이곳으로 들어옵니다.

| 이벤트 | 타입 | 필드 |
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

## 모든 디스패치 { #any-dispatch }

| 이벤트 | 디스패치 | 인텐트 | 필드 |
|---|---|---|---|
| `RawEvent` | 모두 | | `name`, `seq`, `raw`, `raw_json`. 모델링 여부와 관계없이 모든 디스패치에 발생 |

## 봇 이벤트 { #events-of-the-bot }

이 이벤트들은 Discord가 아니라 bostie가 보내며, 이름도 bostie가 정한 것입니다.

| 이벤트 | 이름 | 필드 | 발생 시점 |
|---|---|---|---|
| `Starting` | `bot.starting` | | `start()`가 동기화하고 연결하기 직전. REST는 동작합니다 |
| `Started` | `bot.started` | | 모든 샤드가 준비되었을 때. `start()`마다 한 번 |
| `Stopping` | `bot.stopping` | | `Started` 뒤에 `close()`가 시작되었을 때. 게이트웨이는 아직 열려 있습니다 |
| `Stopped` | `bot.stopped` | | `close()`가 끝났을 때. 게이트웨이와 REST는 닫혔습니다 |
| `ShardReady` | `shard.ready` | `shard_id` | 샤드의 새 세션이 준비되고 그 서버들이 도착했을 때 |
| `ShardDisconnect` | `shard.disconnect` | `shard_id`, `code`, `reason`, `resuming` | 샤드의 연결이 끊겼을 때 |
| `ShardStop` | `shard.stop` | `shard_id`, `code`, `reason` | 샤드가 완전히 멈췄을 때 |
| `ShardBackpressure` | `shard.backpressure` | `shard_id`, `queued_bytes` | 이벤트 루프가 뒤처져서 샤드가 읽기를 일시 중지했을 때 |
| `ListenerError` | `bot.listener_error` | `listener`, `event`, `error` | 리스너가 예외를 발생시켰을 때 |
| `TaskError` | `bot.task_error` | `task`, `error` | 백그라운드 태스크가 예외를 발생시켰을 때 |
