from bostie.cache import Cache
from bostie.http import HTTPClient
from bostie.models import AllowedMentions
from bostie.rest.application import Application
from bostie.rest.audit_log import AuditLog
from bostie.rest.auto_moderation import AutoModeration
from bostie.rest.bans import Bans
from bostie.rest.channels import Channels
from bostie.rest.commands import Commands
from bostie.rest.emojis import Emojis
from bostie.rest.gateway import Gateway
from bostie.rest.guilds import Guilds
from bostie.rest.integrations import Integrations
from bostie.rest.invites import Invites
from bostie.rest.members import Members
from bostie.rest.messages import Messages
from bostie.rest.monetization import Monetization
from bostie.rest.onboarding import Onboarding
from bostie.rest.polls import Polls
from bostie.rest.reactions import Reactions
from bostie.rest.roles import Roles
from bostie.rest.scheduled_events import ScheduledEvents
from bostie.rest.soundboard import Soundboard
from bostie.rest.stage_instances import StageInstances
from bostie.rest.stickers import Stickers
from bostie.rest.templates import Templates
from bostie.rest.threads import Threads
from bostie.rest.users import Users
from bostie.rest.voice import Voice
from bostie.rest.webhooks import Webhooks


class REST:
    """`bot.rest`: each attribute is a group of Discord's REST operations."""

    def __init__(
        self, http: HTTPClient, allowed_mentions: AllowedMentions, cache: Cache
    ) -> None:
        shared = (http, allowed_mentions, cache)
        self.application = Application(*shared)
        self.audit_log = AuditLog(*shared)
        self.auto_moderation = AutoModeration(*shared)
        self.bans = Bans(*shared)
        self.channels = Channels(*shared)
        self.commands = Commands(*shared)
        self.emojis = Emojis(*shared)
        self.gateway = Gateway(*shared)
        self.guilds = Guilds(*shared)
        self.integrations = Integrations(*shared)
        self.invites = Invites(*shared)
        self.members = Members(*shared)
        self.messages = Messages(*shared)
        self.monetization = Monetization(*shared)
        self.onboarding = Onboarding(*shared)
        self.polls = Polls(*shared)
        self.reactions = Reactions(*shared)
        self.roles = Roles(*shared)
        self.scheduled_events = ScheduledEvents(*shared)
        self.soundboard = Soundboard(*shared)
        self.stage_instances = StageInstances(*shared)
        self.stickers = Stickers(*shared)
        self.templates = Templates(*shared)
        self.threads = Threads(*shared)
        self.users = Users(*shared)
        self.voice = Voice(*shared)
        self.webhooks = Webhooks(*shared)
