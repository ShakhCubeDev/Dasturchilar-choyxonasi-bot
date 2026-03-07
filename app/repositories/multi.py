from __future__ import annotations

from typing import Any, Optional

from app.models import GroupRecord, UserRecord
from app.repositories.groups import GroupRepository
from app.repositories.join_gates import JoinGateRepository
from app.repositories.users import UserRepository


class MultiUserRepository:
    def __init__(
        self,
        primary: UserRepository,
        special: UserRepository | None,
        special_group_id: int,
    ) -> None:
        self._primary = primary
        self._special = special
        self._special_group_id = special_group_id

    def _repo(self, group_chat_id: int) -> UserRepository:
        if self._special is not None and group_chat_id == self._special_group_id:
            return self._special
        return self._primary

    async def get_by_group_and_telegram_id(self, group_chat_id: int, telegram_id: int) -> Optional[UserRecord]:
        return await self._repo(group_chat_id).get_by_group_and_telegram_id(group_chat_id, telegram_id)

    async def upsert_user(self, payload: dict[str, Any]) -> UserRecord:
        gid = int(payload["group_chat_id"])
        return await self._repo(gid).upsert_user(payload)

    async def update_status(self, group_chat_id: int, telegram_id: int, status: str) -> Optional[UserRecord]:
        return await self._repo(group_chat_id).update_status(group_chat_id, telegram_id, status)

    async def update_username(self, group_chat_id: int, telegram_id: int, username: str | None) -> None:
        await self._repo(group_chat_id).update_username(group_chat_id, telegram_id, username)

    async def list_telegram_ids_by_status(self, status: str, group_chat_id: int | None = None) -> list[int]:
        if group_chat_id is not None:
            return await self._repo(group_chat_id).list_telegram_ids_by_status(status, group_chat_id)
        ids = await self._primary.list_telegram_ids_by_status(status)
        if self._special is not None:
            ids.extend(await self._special.list_telegram_ids_by_status(status))
        return sorted(set(ids))

    async def list_group_ids_for_user(self, telegram_id: int) -> list[int]:
        ids = await self._primary.list_group_ids_for_user(telegram_id)
        if self._special is not None:
            ids.extend(await self._special.list_group_ids_for_user(telegram_id))
        seen: set[int] = set()
        result: list[int] = []
        for gid in ids:
            if gid not in seen:
                seen.add(gid)
                result.append(gid)
        return result

    async def list_group_user_pairs_by_status(self, status: str) -> list[tuple[int, int]]:
        pairs = await self._primary.list_group_user_pairs_by_status(status)
        if self._special is not None:
            pairs.extend(await self._special.list_group_user_pairs_by_status(status))
        return pairs

    async def delete_all_by_telegram_id(self, telegram_id: int) -> int:
        total = await self._primary.delete_all_by_telegram_id(telegram_id)
        if self._special is not None:
            total += await self._special.delete_all_by_telegram_id(telegram_id)
        return total


class MultiJoinGateRepository:
    def __init__(
        self,
        primary: JoinGateRepository,
        special: JoinGateRepository | None,
        special_group_id: int,
    ) -> None:
        self._primary = primary
        self._special = special
        self._special_group_id = special_group_id

    def _repo(self, group_chat_id: int) -> JoinGateRepository:
        if self._special is not None and group_chat_id == self._special_group_id:
            return self._special
        return self._primary

    async def mark(self, group_chat_id: int, telegram_id: int) -> None:
        await self._repo(group_chat_id).mark(group_chat_id, telegram_id)

    async def is_gated(self, group_chat_id: int, telegram_id: int) -> bool:
        return await self._repo(group_chat_id).is_gated(group_chat_id, telegram_id)

    async def unmark(self, group_chat_id: int, telegram_id: int) -> None:
        await self._repo(group_chat_id).unmark(group_chat_id, telegram_id)

    async def list_group_ids_for_user(self, telegram_id: int) -> list[int]:
        ids = await self._primary.list_group_ids_for_user(telegram_id)
        if self._special is not None:
            ids.extend(await self._special.list_group_ids_for_user(telegram_id))
        seen: set[int] = set()
        result: list[int] = []
        for gid in ids:
            if gid not in seen:
                seen.add(gid)
                result.append(gid)
        return result

    async def delete_all_for_user(self, telegram_id: int) -> int:
        total = await self._primary.delete_all_for_user(telegram_id)
        if self._special is not None:
            total += await self._special.delete_all_for_user(telegram_id)
        return total


class MultiGroupRepository:
    def __init__(
        self,
        primary: GroupRepository,
        special: GroupRepository | None,
        special_group_id: int,
    ) -> None:
        self._primary = primary
        self._special = special
        self._special_group_id = special_group_id

    def _repo(self, chat_id: int) -> GroupRepository:
        if self._special is not None and chat_id == self._special_group_id:
            return self._special
        return self._primary

    async def upsert_group(self, chat_id: int, title: str, owner_telegram_id: int, bot_is_admin: bool) -> GroupRecord:
        return await self._repo(chat_id).upsert_group(chat_id, title, owner_telegram_id, bot_is_admin)

    async def upsert_group_with_registration(
        self,
        chat_id: int,
        title: str,
        owner_telegram_id: int,
        bot_is_admin: bool,
        registration_enabled: bool | None,
    ) -> GroupRecord:
        return await self._repo(chat_id).upsert_group(
            chat_id,
            title,
            owner_telegram_id,
            bot_is_admin,
            registration_enabled=registration_enabled,
        )

    async def set_bot_admin(self, chat_id: int, is_admin: bool) -> None:
        await self._repo(chat_id).set_bot_admin(chat_id, is_admin)

    async def get_by_chat_id(self, chat_id: int) -> Optional[GroupRecord]:
        rec = await self._primary.get_by_chat_id(chat_id)
        if rec:
            return rec
        if self._special is not None:
            return await self._special.get_by_chat_id(chat_id)
        return None

    async def set_registration_enabled(self, chat_id: int, enabled: bool) -> None:
        await self._repo(chat_id).set_registration_enabled(chat_id, enabled)

    async def list_owned_groups(self, owner_telegram_id: int) -> list[GroupRecord]:
        items = await self._primary.list_owned_groups(owner_telegram_id)
        if self._special is not None:
            items.extend(await self._special.list_owned_groups(owner_telegram_id))
        items.sort(key=lambda x: x.updated_at, reverse=True)
        return items
