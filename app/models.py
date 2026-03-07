from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID


@dataclass(slots=True)
class UserRecord:
    id: UUID
    group_chat_id: int
    telegram_id: int
    username: Optional[str]
    full_name: Optional[str]
    phone: str
    age: int
    profession: str
    experience: str
    language: str
    purpose: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class GroupRecord:
    id: UUID
    chat_id: int
    title: str
    owner_telegram_id: int
    bot_is_admin: bool
    registration_enabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class SpamSettingsRecord:
    vote_threshold: int
    timeout_seconds: int
    global_enabled: bool
    updated_at: datetime


@dataclass(slots=True)
class SpamPollRecord:
    id: int
    mode: str
    group_chat_id: int
    target_telegram_id: int
    initiator_telegram_id: int
    message_id: Optional[int]
    yes_votes: int
    no_votes: int
    threshold: int
    expires_at: datetime
    status: str
    decision: Optional[str]
    created_at: datetime
    closed_at: Optional[datetime]
