from collections.abc import Mapping, Sequence
from functools import partial
from typing import TypedDict, Unpack

from bostie.enums import (
    ApplicationEventWebhookStatus,
    ApplicationFlags,
    ApplicationIntegrationType,
)
from bostie.file import File
from bostie.models import (
    ActivityInstance,
    ApplicationIntegrationTypeConfiguration,
    ApplicationRoleConnectionMetadata,
    InstallParams,
    decode,
)
from bostie.models.application import Application as ApplicationModel
from bostie.models.base import decoder
from bostie.rest.base import Group, wire

METADATA = decoder(tuple[ApplicationRoleConnectionMetadata, ...]).decode


class ApplicationEdit(TypedDict, total=False):
    """`flags` takes only the limited intent flags."""

    custom_install_url: str
    description: str
    role_connections_verification_url: str
    install_params: InstallParams
    integration_types_config: Mapping[
        ApplicationIntegrationType, ApplicationIntegrationTypeConfiguration
    ]
    flags: ApplicationFlags
    icon: File | None
    cover_image: File | None
    interactions_endpoint_url: str
    tags: Sequence[str]
    event_webhooks_url: str
    event_webhooks_status: ApplicationEventWebhookStatus
    event_webhooks_types: Sequence[str]


class Application(Group):
    async def edit(self, **options: Unpack[ApplicationEdit]) -> ApplicationModel:
        return await self._http._decoded(
            partial(decode, ApplicationModel),
            "PATCH",
            "/applications/@me",
            json=wire(options, ApplicationEdit),
        )

    async def fetch(self) -> ApplicationModel:
        return await self._http._decoded(
            partial(decode, ApplicationModel), "GET", "/applications/@me"
        )

    async def fetch_activity_instance(
        self, application_id: int, instance_id: str, /
    ) -> ActivityInstance:
        return await self._http._decoded(
            partial(decode, ActivityInstance),
            "GET",
            "/applications/{application_id}/activity-instances/{instance_id}",
            application_id=application_id,
            instance_id=instance_id,
        )

    async def list_role_connection_metadata(
        self, application_id: int, /
    ) -> tuple[ApplicationRoleConnectionMetadata, ...]:
        return await self._http._decoded(
            METADATA,
            "GET",
            "/applications/{application_id}/role-connections/metadata",
            application_id=application_id,
        )

    async def update_role_connection_metadata(
        self,
        application_id: int,
        /,
        records: Sequence[ApplicationRoleConnectionMetadata],
    ) -> tuple[ApplicationRoleConnectionMetadata, ...]:
        """Replace the app's role connection metadata, at most 5 records."""
        return await self._http._decoded(
            METADATA,
            "PUT",
            "/applications/{application_id}/role-connections/metadata",
            json=records,
            application_id=application_id,
        )
