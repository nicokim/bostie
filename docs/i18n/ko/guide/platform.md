---
source: 03a349670a189a38d3b3491aba4b54e6af1ec4e9
---

# 활동, 수익화, Social SDK

Discord 앱은 서버 안의 봇에 그치지 않습니다. 이 페이지에서는 사용자에게 설치하는 앱, 활동(Activity), SKU 판매, Social SDK의 서버 측을 다룹니다.

```python
--8<-- "docs/snippets/apps/premium.py"
```

## 사용자에게 설치하는 앱 { #apps-installed-to-users }

앱은 서버뿐 아니라 사용자 계정에도 설치할 수 있으며, 그러면 그 앱의 명령어는 친구와의 개인 메시지(DM)나 봇이 없는 서버를 포함해 사용자가 있는 어디서든 동작합니다. Developer Portal의 **Installation** 페이지에서 사용자 설치를 켠 다음, 위 예제가 봇 전체에 하는 것처럼 `integration_types`와 `contexts`로 명령어가 사용자 설치를 쓰도록 합니다([명령어 등록](registration.md#where-commands-can-be-used)).

이런 컨텍스트에서는 봇이 채널에 없습니다. 캐시 항목도 게이트웨이 이벤트도 없고, REST API도 그곳에서는 대부분의 호출을 거부합니다. `ctx.interaction.authorizing_integration_owners`는 어떤 설치 덕분에 인터랙션이 일어날 수 있었는지, `ctx.interaction.context`는 인터랙션이 어디서 일어났는지, `ctx.interaction.app_permissions`는 앱이 무엇을 할 수 있는지 알려 줍니다. 응답은 평소처럼 동작하며, 사용자에게만 설치된 앱에는 Discord가 후속 메시지를 5개까지 허용합니다. bostie는 여섯 번째에서 경고합니다.

## 활동 { #activities }

활동(Activity)은 Discord 안에서 실행되는 웹 앱입니다. bostie는 봇 측을 담당합니다.

- `@bot.entry_point(name)`은 앱 실행기(App Launcher)에 표시되는 진입점 명령어를 등록합니다. 자세한 내용은 [사용자, 메시지, 진입점 명령어](context-menus.md#the-entry-point-command)에서 설명합니다.
- `await ctx.launch_activity()`는 명령어나 컴포넌트에서 활동을 열고, 그 인스턴스의 ID를 반환합니다.
- `await bot.rest.application.fetch_activity_instance(application_id, instance_id)`는 활동이 실행되는 위치와 참여 중인 사용자의 ID를 담은 `ActivityInstance`를 반환합니다.

[활동 예제](../examples.md#activity)는 둘 다 활동을 실행하는 진입점 명령어와 슬래시 명령어를 등록합니다.

## 수익화 { #monetization }

수익화를 켠 앱은 구독, 영구 아이템, 소모성 아이템 같은 SKU를 판매합니다. 사용자의 구매 내역은 엔타이틀먼트입니다.

- `ctx.interaction.entitlements`는 인터랙션의 사용자와 서버가 가진 엔타이틀먼트를 나열하므로, 명령어는 요청 없이도 구매 여부를 확인할 수 있습니다.
- `ui.premium_button(sku_id)`는 SKU 구매 화면을 여는 버튼을 표시하며, 이 버튼은 봇에 전달되지 않습니다. `fmt.store(application_id, sku_id=None)`은 앱의 상점 페이지나 SKU 하나로 연결되는 링크를 만들고, Discord는 이를 카드로 표시합니다.
- `bot.rest.monetization`은 앱의 SKU를 나열하고(`list_skus`), 사용자, 서버, SKU로 필터링해 엔타이틀먼트를 순회하며(`list_entitlements`), 하나를 가져오고, 소모성 아이템을 사용 처리합니다(`consume_entitlement`). SKU의 구독을 나열하고 가져오는 기능도 있습니다.
- `create_test_entitlement(application_id, sku_id=..., owner_id=..., owner_type=1)`은 서버(1)나 사용자(2)에게 무료 테스트 엔타이틀먼트를 부여하고, `delete_test_entitlement`는 이를 제거합니다.
- `EntitlementCreate`, `EntitlementUpdate`, `EntitlementDelete` 이벤트와 `SubscriptionCreate`, `SubscriptionUpdate`, `SubscriptionDelete` 이벤트는 게이트웨이로 변경 사항을 알려 줍니다. 엔타이틀먼트 이벤트는 [웹후크 이벤트](http-interactions.md#webhook-events)로도 전달됩니다. 테스트 엔타이틀먼트는 실제 엔타이틀먼트의 날짜 정보가 없는 `PartialEntitlement`로 전달됩니다.

## Social SDK { #social-sdk }

Discord Social SDK는 게임 안에서 실행됩니다. 그 서버 측은 REST입니다.

- `bot.rest.lobbies`는 로비를 만들고, 로비의 메타데이터와 멤버를 수정하며(`add_member`, 한 번에 최대 25명까지 처리하는 `bulk_update_members`, `remove_member`), 로비를 삭제하고, 사용자를 로비에 연결된 채널로 초대하며, 로비 메시지에 모더레이션 메타데이터를 설정합니다.
- 플레이어의 토큰을 가진 [`BearerClient`](oauth2.md#calling-the-api-as-the-user)의 `user.rest.lobbies`는 시크릿으로 로비를 만들거나 참가하고(`create_or_join`), 로비에서 나가고(`leave`), Discord 채널을 연결하거나 연결을 해제하며(`link_channel`, `unlink_channel`), 메시지를 보내고 나열하고(`send_message`, `list_messages`), 플레이어 자신을 연결된 채널에 초대합니다(`create_invite_for_self`).
- `bot.rest.partner_sdk`는 토큰을 교환합니다. 게임 자체 계정 시스템의 플레이어용 임시 토큰, 외부 ID 제공자의 토큰과 바꾸는 토큰, 퍼블리셔의 토큰과 바꾸는 하위 게임의 토큰을 다룹니다. 임시 계정의 병합을 해제하고 개인 메시지에 모더레이션 메타데이터를 설정하는 기능도 있습니다.
- `bot.rest.identities`는 외부 계정을 연결한 사용자의 애플리케이션 ID 정보와 프로필을 나열하고, 가져오고, 수정합니다.

로비 메시지와 게임 내 개인 메시지는 `LobbyMessage*`와 `GameDirectMessage*` [웹후크 이벤트](http-interactions.md#webhook-events)로 전달됩니다.
