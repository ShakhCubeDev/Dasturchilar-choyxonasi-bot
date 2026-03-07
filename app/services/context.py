from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from app.config import Settings
from app.repositories.groups import GroupRepository
from app.repositories.join_gates import JoinGateRepository
from app.repositories.spam import SpamRepository
from app.repositories.users import UserRepository
from app.services.nsfw import OpenNSFWService
from app.services.texts import TextService


@dataclass
class AppContext:
    settings: Settings
    users: UserRepository
    groups: GroupRepository
    gates: JoinGateRepository
    spam: SpamRepository
    texts: TextService
    logger: logging.Logger
    nsfw: OpenNSFWService | None = None
    group_reply_last_sent: dict[int, float] = field(default_factory=dict)

    def can_send_group_warning(self, user_id: int) -> bool:
        now = time.monotonic()
        cooldown = self.settings.group_reply_cooldown_seconds
        last_sent = self.group_reply_last_sent.get(user_id)
        if last_sent and now - last_sent < cooldown:
            return False
        self.group_reply_last_sent[user_id] = now
        return True
