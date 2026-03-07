from __future__ import annotations

from typing import Optional

from asyncpg import Pool, Record

from app.models import GroupRecord


class GroupRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @staticmethod
    def _to_model(record: Record) -> GroupRecord:
        return GroupRecord(
            id=record["id"],
            chat_id=record["chat_id"],
            title=record["title"],
            owner_telegram_id=record["owner_telegram_id"],
            bot_is_admin=record["bot_is_admin"],
            registration_enabled=record["registration_enabled"],
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )

    async def upsert_group(
        self,
        chat_id: int,
        title: str,
        owner_telegram_id: int,
        bot_is_admin: bool,
        registration_enabled: bool | None = None,
    ) -> GroupRecord:
        query = """
        INSERT INTO groups (chat_id, title, owner_telegram_id, bot_is_admin, registration_enabled, updated_at)
        VALUES ($1, $2, $3, $4, COALESCE($5, TRUE), NOW())
        ON CONFLICT (chat_id)
        DO UPDATE SET
            title = EXCLUDED.title,
            owner_telegram_id = CASE
                WHEN groups.bot_is_admin = FALSE AND EXCLUDED.bot_is_admin = TRUE THEN EXCLUDED.owner_telegram_id
                ELSE groups.owner_telegram_id
            END,
            bot_is_admin = EXCLUDED.bot_is_admin,
            updated_at = NOW()
        RETURNING *;
        """
        record = await self._pool.fetchrow(query, chat_id, title, owner_telegram_id, bot_is_admin, registration_enabled)
        if not record:
            raise RuntimeError("Failed to upsert group")
        return self._to_model(record)

    async def set_bot_admin(self, chat_id: int, is_admin: bool) -> None:
        await self._pool.execute(
            "UPDATE groups SET bot_is_admin=$2, updated_at=NOW() WHERE chat_id=$1;",
            chat_id,
            is_admin,
        )

    async def get_by_chat_id(self, chat_id: int) -> Optional[GroupRecord]:
        record = await self._pool.fetchrow("SELECT * FROM groups WHERE chat_id=$1;", chat_id)
        return self._to_model(record) if record else None

    async def set_registration_enabled(self, chat_id: int, enabled: bool) -> None:
        await self._pool.execute(
            "UPDATE groups SET registration_enabled=$2, updated_at=NOW() WHERE chat_id=$1;",
            chat_id,
            enabled,
        )

    async def list_owned_groups(self, owner_telegram_id: int) -> list[GroupRecord]:
        rows = await self._pool.fetch(
            "SELECT * FROM groups WHERE owner_telegram_id=$1 ORDER BY updated_at DESC;",
            owner_telegram_id,
        )
        return [self._to_model(row) for row in rows]

    async def list_all_groups(self, limit: int = 500) -> list[GroupRecord]:
        rows = await self._pool.fetch(
            "SELECT * FROM groups ORDER BY updated_at DESC LIMIT $1;",
            limit,
        )
        return [self._to_model(row) for row in rows]
