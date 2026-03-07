from __future__ import annotations

from asyncpg import Pool


class JoinGateRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def mark(self, group_chat_id: int, telegram_id: int) -> None:
        await self._pool.execute(
            """
            INSERT INTO join_gates (group_chat_id, telegram_id)
            VALUES ($1, $2)
            ON CONFLICT (group_chat_id, telegram_id) DO NOTHING;
            """,
            group_chat_id,
            telegram_id,
        )

    async def is_gated(self, group_chat_id: int, telegram_id: int) -> bool:
        val = await self._pool.fetchval(
            "SELECT 1 FROM join_gates WHERE group_chat_id=$1 AND telegram_id=$2;",
            group_chat_id,
            telegram_id,
        )
        return bool(val)

    async def unmark(self, group_chat_id: int, telegram_id: int) -> None:
        await self._pool.execute(
            "DELETE FROM join_gates WHERE group_chat_id=$1 AND telegram_id=$2;",
            group_chat_id,
            telegram_id,
        )

    async def list_group_ids_for_user(self, telegram_id: int) -> list[int]:
        rows = await self._pool.fetch(
            "SELECT group_chat_id FROM join_gates WHERE telegram_id=$1 ORDER BY created_at DESC;",
            telegram_id,
        )
        return [int(row["group_chat_id"]) for row in rows]

    async def delete_all_for_user(self, telegram_id: int) -> int:
        result = await self._pool.execute(
            "DELETE FROM join_gates WHERE telegram_id=$1;",
            telegram_id,
        )
        try:
            return int(result.split()[-1])
        except Exception:
            return 0
