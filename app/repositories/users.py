from __future__ import annotations

from typing import Any, Optional

from asyncpg import Pool, Record

from app.models import UserRecord


class UserRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @staticmethod
    def _to_model(record: Record) -> UserRecord:
        return UserRecord(
            id=record["id"],
            group_chat_id=record["group_chat_id"],
            telegram_id=record["telegram_id"],
            username=record["username"],
            full_name=record["full_name"],
            phone=record["phone"],
            age=record["age"],
            profession=record["profession"] or record["field"],
            experience=record["experience"],
            language=record["language"],
            purpose=record["purpose"],
            status=record["status"],
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )

    async def get_by_group_and_telegram_id(self, group_chat_id: int, telegram_id: int) -> Optional[UserRecord]:
        query = "SELECT * FROM users WHERE group_chat_id = $1 AND telegram_id = $2;"
        record = await self._pool.fetchrow(query, group_chat_id, telegram_id)
        if not record:
            return None
        return self._to_model(record)

    async def upsert_user(self, payload: dict[str, Any]) -> UserRecord:
        query = """
        INSERT INTO users (
            group_chat_id, telegram_id, username, full_name, phone, age, field, profession, experience, language, purpose, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'active')
        ON CONFLICT (group_chat_id, telegram_id)
        DO UPDATE SET
            username = EXCLUDED.username,
            full_name = EXCLUDED.full_name,
            phone = EXCLUDED.phone,
            age = EXCLUDED.age,
            field = EXCLUDED.field,
            profession = EXCLUDED.profession,
            experience = EXCLUDED.experience,
            language = EXCLUDED.language,
            purpose = EXCLUDED.purpose,
            status = 'active',
            updated_at = NOW()
        RETURNING *;
        """
        record = await self._pool.fetchrow(
            query,
            payload["group_chat_id"],
            payload["telegram_id"],
            payload["username"],
            payload["full_name"],
            payload["phone"],
            payload["age"],
            payload["profession"],
            payload["profession"],
            payload["experience"],
            payload["language"],
            payload.get("purpose"),
        )
        if not record:
            raise RuntimeError("Failed to upsert user")
        return self._to_model(record)

    async def update_status(self, group_chat_id: int, telegram_id: int, status: str) -> Optional[UserRecord]:
        query = """
        UPDATE users
        SET status = $3, updated_at = NOW()
        WHERE group_chat_id = $1 AND telegram_id = $2
        RETURNING *;
        """
        record = await self._pool.fetchrow(query, group_chat_id, telegram_id, status)
        if not record:
            return None
        return self._to_model(record)

    async def update_username(self, group_chat_id: int, telegram_id: int, username: str | None) -> None:
        query = "UPDATE users SET username = $3, updated_at = NOW() WHERE group_chat_id = $1 AND telegram_id = $2;"
        await self._pool.execute(query, group_chat_id, telegram_id, username)

    async def list_telegram_ids_by_status(self, status: str, group_chat_id: int | None = None) -> list[int]:
        if group_chat_id is None:
            rows = await self._pool.fetch(
                "SELECT DISTINCT telegram_id FROM users WHERE status=$1 ORDER BY telegram_id ASC;",
                status,
            )
        else:
            rows = await self._pool.fetch(
                "SELECT telegram_id FROM users WHERE status=$1 AND group_chat_id=$2 ORDER BY created_at ASC;",
                status,
                group_chat_id,
            )
        return [int(r["telegram_id"]) for r in rows]

    async def list_group_ids_for_user(self, telegram_id: int) -> list[int]:
        rows = await self._pool.fetch(
            "SELECT group_chat_id FROM users WHERE telegram_id=$1 GROUP BY group_chat_id ORDER BY MAX(updated_at) DESC;",
            telegram_id,
        )
        return [int(r["group_chat_id"]) for r in rows]

    async def list_group_user_pairs_by_status(self, status: str) -> list[tuple[int, int]]:
        rows = await self._pool.fetch(
            "SELECT group_chat_id, telegram_id FROM users WHERE status=$1 ORDER BY group_chat_id ASC, created_at ASC;",
            status,
        )
        return [(int(r["group_chat_id"]), int(r["telegram_id"])) for r in rows]

    async def delete_all_by_telegram_id(self, telegram_id: int) -> int:
        result = await self._pool.execute(
            "DELETE FROM users WHERE telegram_id=$1;",
            telegram_id,
        )
        try:
            return int(result.split()[-1])
        except Exception:
            return 0
