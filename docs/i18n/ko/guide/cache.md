---
source: f23a479950d38c899e79ae34c9fa1854c7b6fd34
---

# 캐시와 권한

![상자에 뼈다귀를 넣는 Bostie](../assets/mascot/bostie-cache.webp){ align=right width="180" loading=lazy }

`bot.cache`는 게이트웨이가 봇에게 알려 준 내용을 보관하므로, 핸들러는 요청 없이 필요한 것을 찾아볼 수 있습니다. 캐시는 이벤트로만 채워지며, 직접 호출한 REST 결과로는 채워지지 않습니다.

```python
--8<-- "docs/snippets/cache/cache_lookups.py"
```

## 보관하는 것 { #what-it-keeps }

| 보관 대상 | 기본 | 옵션 |
|---|---|---|
| 서버와 그 역할, 이모지, 스티커 | 예 | |
| 채널과 활성 스레드 | 예 | |
| 음성 상태 | 예 | |
| 예약 이벤트, 스테이지 인스턴스, 사운드보드 사운드 | 예 | |
| 봇의 사용자와 각 서버에서의 봇 자신의 멤버 | 예 | |
| 모든 멤버 | 아니요 | `members=True`, `Intents.GUILD_MEMBERS` 필요 |
| 오프라인이 아닌 멤버의 프레즌스 | 아니요 | `presences=True`, `Intents.GUILD_PRESENCES` 필요 |
| 최근 메시지 | 아니요 | `max_messages=N` |
| 최근 개인 메시지(DM) 채널 | 아니요 | `max_dm_channels=N` |

`Bot(cache=False)`는 아무것도 보관하지 않으며, 이때 나머지 옵션은 `ValueError`를 일으킵니다. 필요한 인텐트가 없는 옵션은 `Bot`을 만들 때 `MissingIntent`를 일으킵니다. 채널은 Discord가 보낸 그대로 저장되지만, `CHANNEL_UPDATE`가 최신 상태로 유지하지 않는 필드(`last_message_id`, `message_count`, `member_count`, `last_pin_timestamp`)는 `None`입니다.

`members=True`이면 봇이 연결될 때 Discord는 큰 서버의 멤버 중 일부만 보냅니다. 그러면 bostie가 나머지를 샤드마다 서버 하나씩 요청하며, 샤드는 자기 서버가 모두 채워진 뒤에야 준비된 것으로 간주됩니다. `chunk_members=False`로 이 과정을 건너뛰면, 멤버가 들어오거나 바뀌거나 말할 때 캐시가 채워집니다. 레이트 리밋에 걸린 요청은 Discord가 알려 준 지연 시간 뒤에 재시도합니다.

## 조회 { #lookups }

| 메서드 | 반환값 |
|---|---|
| `guild(id)`, `guilds()` | `Guild` 하나 또는 전체 |
| `channel(id)`, `guild_channels(guild_id)` | `Channel` 하나, 또는 서버의 채널과 활성 스레드 |
| `role(id)`, `guild_roles(guild_id)` | `Role` 하나, 또는 서버의 역할 |
| `voice_state(guild_id, user_id)`, `guild_voice_states(guild_id)` | 사용자가 어느 음성 채널에 있는지. 사용자가 음성 채널에 없으면 `None` |
| `scheduled_event(id)`, `guild_scheduled_events(guild_id)` | `ScheduledEvent` 하나, 또는 서버의 예약 이벤트 |
| `stage_instance(id)`, `guild_stage_instances(guild_id)` | 진행 중인 `StageInstance` 하나, 또는 서버의 스테이지 인스턴스 |
| `soundboard_sound(sound_id)`, `guild_soundboard_sounds(guild_id)` | `SoundboardSound` 하나, 또는 서버의 사운드 |
| `me(guild_id)` | 서버에서의 봇 자신의 `Member` |
| `member(guild_id, user_id)`, `guild_members(guild_id)` | 멤버. `members=True`가 아니면 봇 자신만 |
| `presence(guild_id, user_id)`, `guild_presences(guild_id)` | `presences=True`일 때의 프레즌스. 오프라인이면 `None` |
| `user(id)` | 봇의 사용자, 또는 캐시된 멤버나 개인 메시지 채널의 사용자 |
| `dm_channel(user_id)` | 보관 중이라면 봇과 사용자 사이의 개인 메시지 채널 |
| `message(id)` | 최근 `max_messages`개 메시지 중 하나 |
| `permissions(channel_id, member=None)` | 채널에서 멤버가 가진 권한. `member`를 생략하면 봇의 권한 |
| `guild_permissions(guild_id, member=None)` | 같은 계산을 서버 전체 기준으로 |

조회 메서드는 캐시에 없는 대상에 대해 `None`이나 빈 리스트를 반환합니다. `bot.cache.clear()`는 캐시를 비웁니다. 업데이트 이벤트와 삭제 이벤트는 [이벤트](events.md#old-values-and-the-cache)에서 설명하듯 변경 전에 캐시되어 있던 객체를 담습니다.

## 캐시에 없으면 REST로 { #the-cache-else-rest }

```python
--8<-- "docs/snippets/cache/lookups_with_rest.py"
```

`bot.lookup`은 캐시에 객체가 있으면 캐시에서 반환하고, 없으면 REST로 요청합니다. `user(user_id)`, `member(guild_id, user_id)`, `channel(channel_id)`, `guild(guild_id)`, `role(guild_id, role_id)`, `message(channel_id, message_id)`가 있습니다. REST가 반환한 객체는 저장되지 않습니다. 캐시는 계속 게이트웨이만 따르므로, 캐시에 없는 같은 객체를 다시 조회하면 다시 요청합니다. 존재하지 않는 ID에 대한 `NotFound` 같은 REST 에러는 그대로 전달됩니다.

## 예약 이벤트, 스테이지, 사운드 { #scheduled-events-stages-and-sounds }

```python
--8<-- "docs/snippets/cache/guild_events.py"
```

이들은 각 서버와 함께 도착하고, 생성, 업데이트, 삭제 이벤트를 따라 갱신됩니다. 예약 이벤트에는 `Intents.GUILD_SCHEDULED_EVENTS`가, 사운드보드 사운드에는 `Intents.GUILD_EXPRESSIONS`가 필요하며, 둘 다 `Intents.DEFAULT`에 들어 있습니다. `bot.request_soundboard_sounds`에 대한 응답은 서버의 사운드를 교체합니다. 이들의 업데이트 이벤트는 `old`를, `SoundboardSoundDelete`는 삭제된 `sound`를 담습니다.

## 시작 { #startup }

샤드는 `READY`에 나열된 모든 서버가 `GuildCreate`로 도착하거나, `guild_ready_timeout`초(기본값 2초) 동안 새 서버가 도착하지 않으면 준비된 것으로 간주됩니다. `Started`는 모든 샤드를 기다립니다. `joined=True`인 `GuildCreate`는 봇이 방금 추가된 서버이고, 나머지는 사용할 수 있게 된 서버입니다.

`large_threshold`는 50에서 250 사이의 값으로 기본값은 50이며, 멤버 수가 이 값을 넘는 서버는 Discord가 오프라인 멤버를 빼고 보냅니다.

## 권한 { #permissions }

`cache.permissions(channel_id, member)`는 Discord의 규칙을 적용합니다. 서버 소유자와 관리자는 모든 권한을 가집니다. 그 외에는 `@everyone` 역할, 멤버의 역할, 그리고 `@everyone`, 역할, 멤버에 대한 채널의 권한 덮어쓰기 순서로 적용합니다. 타임아웃된 멤버는 채널 보기(View Channel)와 메시지 기록 보기(Read Message History)만 유지합니다. 채널 보기 권한이 없으면 아무 권한도 없습니다. 메시지 보내기(Send Messages) 권한이 없으면 이 권한이 필요한 권한도 빠집니다. 연결(Connect) 권한이 없는 음성 채널에서는 음성 권한이 빠집니다. 스레드는 부모 채널의 권한 덮어쓰기를 사용합니다. 캐시에 채널, 서버, 또는 스레드의 부모 채널이 없으면 `None`을 반환합니다.

같은 계산은 캐시 없이도 할 수 있습니다. `bostie.permissions.compute_permissions(guild, member, channel=None, *, parent=None)`를 사용하며, `parent`는 스레드의 부모 채널입니다. 이 모듈에는 Discord 문서에서 가져온 `Permissions` 집합도 있습니다. `ALL`, `NONE`, `TEXT`, `VOICE`, `STAGE`(채널 종류마다 적용되는 권한), `ELEVATED`(서버가 요구할 때 관리자 계정에 2단계 인증이 필요한 권한), `TIMED_OUT`(타임아웃된 멤버가 유지하는 권한)입니다.

인터랙션 안에서는 캐시가 필요 없습니다. `ctx.interaction.app_permissions`에는 채널에서 봇이 가진 권한이, `ctx.member.permissions`에는 사용자의 권한이 들어 있습니다.

## 역할 계층 { #role-hierarchy }

멤버는 자신의 가장 높은 역할보다 가장 높은 역할이 낮은 멤버만 추방, 차단, 타임아웃하거나 수정할 수 있으며, Discord는 멤버의 이름을 색이 있는 역할 중 가장 높은 역할의 색으로 표시합니다. `bostie.permissions`에는 이를 위한 헬퍼 세 개가 있으며, 캐시, 인터랙션, REST에서 얻은 `Guild`와 멤버를 받습니다.

| 헬퍼 | 반환값 |
|---|---|
| `top_role(guild, member)` | 멤버의 가장 높은 `Role`. `@everyone`만 있으면 `None` |
| `display_colors(guild, member)` | 색이 있는 역할 중 가장 높은 역할의 `RoleColors`, 또는 `None` |
| `can_act_on(guild, actor, target)` | `actor`가 멤버나 역할인 `target`보다 높은지 여부. 소유자는 모두보다 높고, 누구도 소유자보다 높을 수 없으며, 그 외에는 Discord가 역할 관리에서 하듯 actor의 가장 높은 역할을 target의 가장 높은 역할, 또는 역할 자체와 위치로 비교합니다 |

위치가 같은 역할은 Discord가 정렬하는 방식대로 ID로, 즉 오래된 역할이 먼저 오도록 순위를 매깁니다.

```python
--8<-- "docs/snippets/cache/role_hierarchy.py"
```
