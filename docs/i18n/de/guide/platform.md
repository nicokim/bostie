---
source: 03a349670a189a38d3b3491aba4b54e6af1ec4e9
---

# Aktivitäten, Monetarisierung und das Social SDK

Discord-Apps sind mehr als Bots auf Servern. Diese Seite behandelt Apps, die für Benutzer installiert sind, Aktivitäten, den Verkauf von SKUs und die Serverseite des Social SDK.

```python
--8<-- "docs/snippets/apps/premium.py"
```

## Für Benutzer installierte Apps { #apps-installed-to-users }

Eine App lässt sich nicht nur auf einem Server, sondern auch im Konto eines Benutzers installieren, und ihre Befehle funktionieren dann überall, wo dieser Benutzer ist, auch in DMs mit Freunden und auf Servern ohne den Bot. Schalte User Install auf der Seite **Installation** im Developer Portal ein und lass Befehle es dann mit `integration_types` und `contexts` nutzen, wie es das Snippet für den ganzen Bot tut ([Befehle registrieren](registration.md#where-commands-can-be-used)).

In einem solchen Kontext ist der Bot nicht im Kanal: Es gibt keinen Cache-Eintrag, kein Gateway-Event, und die REST-API lehnt dort die meisten Aufrufe ab. `ctx.interaction.authorizing_integration_owners` sagt, welche Installationen die Interaktion ermöglicht haben, `ctx.interaction.context`, wo sie passiert ist, und `ctx.interaction.app_permissions`, was die App tun darf. Antworten funktionieren wie gewohnt, und Discord erlaubt 5 Folgenachrichten für eine App, die nur für den Benutzer installiert ist; bostie warnt bei der sechsten.

## Aktivitäten { #activities }

Eine Aktivität ist eine Web-App, die innerhalb von Discord läuft. bostie deckt die Bot-Seite ab:

- `@bot.entry_point(name)` registriert den Einstiegspunkt-Befehl, den der App Launcher anzeigt, wie [Benutzer-, Nachrichten- und Einstiegspunkt-Befehle](context-menus.md#the-entry-point-command) erklärt.
- `await ctx.launch_activity()` öffnet die Aktivität aus einem Befehl oder einer Komponente und gibt die ID ihrer Instanz zurück.
- `await bot.rest.application.fetch_activity_instance(application_id, instance_id)` gibt eine `ActivityInstance` zurück: wo sie läuft und die IDs der Benutzer darin.

Das [Aktivitäts-Beispiel](../examples.md#activity) registriert einen Einstiegspunkt und einen Slash-Befehl, die sie beide starten.

## Monetarisierung { #monetization }

Eine App mit aktivierter Monetarisierung verkauft SKUs: Abonnements, dauerhafte Artikel und Verbrauchsartikel. Die Käufe des Benutzers sind Entitlements.

- `ctx.interaction.entitlements` listet die Entitlements des Benutzers und des Servers einer Interaktion, sodass ein Befehl ohne Anfrage auf einen Kauf prüfen kann.
- `ui.premium_button(sku_id)` zeigt einen Button, der den Kauf einer SKU öffnet; er erreicht den Bot nie. `fmt.store(application_id, sku_id=None)` verlinkt die Shop-Seite der App oder eine einzelne SKU, die Discord als Karte anzeigt.
- `bot.rest.monetization` listet die SKUs der App (`list_skus`), durchläuft Entitlements mit Filtern nach Benutzer, Server und SKU (`list_entitlements`), holt ein einzelnes und markiert einen Verbrauchsartikel als verbraucht (`consume_entitlement`). Außerdem listet und holt es die Abonnements einer SKU.
- `create_test_entitlement(application_id, sku_id=..., owner_id=..., owner_type=1)` gewährt einem Server (1) oder einem Benutzer (2) ein kostenloses Test-Entitlement, und `delete_test_entitlement` entfernt es.
- Die Events `EntitlementCreate`, `EntitlementUpdate` und `EntitlementDelete` sowie `SubscriptionCreate`, `SubscriptionUpdate` und `SubscriptionDelete` melden Änderungen über das Gateway; die Entitlement-Events kommen auch als [Webhook-Events](http-interactions.md#webhook-events) an. Ein Test-Entitlement kommt als `PartialEntitlement` an, ohne die Daten eines echten.

## Social SDK { #social-sdk }

Das Discord Social SDK läuft innerhalb von Spielen. Seine Serverseite ist REST:

- `bot.rest.lobbies` erstellt Lobbys, bearbeitet ihre Metadaten und Mitglieder (`add_member`, `bulk_update_members` für bis zu 25 auf einmal, `remove_member`), löscht sie, lädt einen Benutzer in den verknüpften Kanal der Lobby ein und setzt Moderations-Metadaten an Lobby-Nachrichten.
- `user.rest.lobbies` an einem [`BearerClient`](oauth2.md#calling-the-api-as-the-user) mit dem Token eines Spielers erstellt eine Lobby oder tritt ihr per Geheimnis bei (`create_or_join`), verlässt sie (`leave`), verknüpft einen Discord-Kanal und hebt die Verknüpfung auf (`link_channel`, `unlink_channel`), sendet und listet Nachrichten (`send_message`, `list_messages`) und lädt den Spieler selbst in den verknüpften Kanal ein (`create_invite_for_self`).
- `bot.rest.partner_sdk` tauscht Tokens: ein vorläufiges Token für einen Spieler aus dem eigenen Kontosystem des Spiels, ein Token gegen das Token eines externen Identitätsanbieters, das Token eines untergeordneten Spiels gegen das eines Publishers; außerdem trennt es zusammengeführte vorläufige Konten wieder und setzt Moderations-Metadaten an Direktnachrichten.
- `bot.rest.identities` listet, holt und aktualisiert die Anwendungsidentitäten und Profile von Benutzern, die ein externes Konto verknüpft haben.

Lobby-Nachrichten und Direktnachrichten im Spiel kommen als [Webhook-Events](http-interactions.md#webhook-events) `LobbyMessage*` und `GameDirectMessage*` an.
