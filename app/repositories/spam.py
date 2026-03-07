from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from asyncpg import Pool, Record

from app.models import SpamPollRecord, SpamSettingsRecord


class SpamRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @staticmethod
    def _to_poll(record: Record) -> SpamPollRecord:
        return SpamPollRecord(
            id=int(record["id"]),
            mode=record["mode"],
            group_chat_id=int(record["group_chat_id"]),
            target_telegram_id=int(record["target_telegram_id"]),
            initiator_telegram_id=int(record["initiator_telegram_id"]),
            message_id=int(record["message_id"]) if record["message_id"] is not None else None,
            yes_votes=int(record["yes_votes"]),
            no_votes=int(record["no_votes"]),
            threshold=int(record["threshold"]),
            expires_at=record["expires_at"],
            status=record["status"],
            decision=record["decision"],
            created_at=record["created_at"],
            closed_at=record["closed_at"],
        )

    @staticmethod
    def _to_settings(record: Record) -> SpamSettingsRecord:
        return SpamSettingsRecord(
            vote_threshold=int(record["vote_threshold"]),
            timeout_seconds=int(record["timeout_seconds"]),
            global_enabled=bool(record["global_enabled"]),
            updated_at=record["updated_at"],
        )

    async def get_settings(self, mode: str) -> SpamSettingsRecord:
        row = await self._pool.fetchrow(
            """
            SELECT vote_threshold, timeout_seconds, global_enabled, updated_at
            FROM spam_mode_settings
            WHERE mode = $1;
            """,
            mode,
        )
        if not row:
            raise RuntimeError("spam_mode_settings row missing")
        return self._to_settings(row)

    async def set_threshold(self, mode: str, value: int) -> None:
        await self._pool.execute(
            "UPDATE spam_mode_settings SET vote_threshold=$2, updated_at=NOW() WHERE mode=$1;",
            mode,
            value,
        )

    async def set_timeout_seconds(self, mode: str, value: int) -> None:
        await self._pool.execute(
            "UPDATE spam_mode_settings SET timeout_seconds=$2, updated_at=NOW() WHERE mode=$1;",
            mode,
            value,
        )

    async def set_global_enabled(self, mode: str, enabled: bool) -> None:
        await self._pool.execute(
            "UPDATE spam_mode_settings SET global_enabled=$2, updated_at=NOW() WHERE mode=$1;",
            mode,
            enabled,
        )

    async def is_globally_banned(self, mode: str, telegram_id: int) -> bool:
        v = await self._pool.fetchval(
            "SELECT 1 FROM global_spam_users_mode WHERE mode=$1 AND telegram_id=$2;",
            mode,
            telegram_id,
        )
        return bool(v)

    async def is_globally_banned_any(self, telegram_id: int) -> bool:
        v = await self._pool.fetchval(
            "SELECT 1 FROM global_spam_users_mode WHERE telegram_id=$1 LIMIT 1;",
            telegram_id,
        )
        return bool(v)

    async def add_global_spam(
        self,
        mode: str,
        telegram_id: int,
        source_group_id: int,
        source_poll_id: int | None,
        reason: str,
        target_username: str | None = None,
        source_group_title: str | None = None,
        source_group_username: str | None = None,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO global_spam_users_mode (
                mode, telegram_id, target_username, source_group_id, source_group_title, source_group_username, source_poll_id, reason
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (mode, telegram_id) DO UPDATE SET
                target_username=EXCLUDED.target_username,
                source_group_id=EXCLUDED.source_group_id,
                source_group_title=EXCLUDED.source_group_title,
                source_group_username=EXCLUDED.source_group_username,
                source_poll_id=EXCLUDED.source_poll_id,
                reason=EXCLUDED.reason,
                created_at=NOW();
            """,
            mode,
            telegram_id,
            target_username,
            source_group_id,
            source_group_title,
            source_group_username,
            source_poll_id,
            reason,
        )

    async def remove_global_spam(self, mode: str, telegram_id: int) -> bool:
        result = await self._pool.execute(
            "DELETE FROM global_spam_users_mode WHERE mode=$1 AND telegram_id=$2;",
            mode,
            telegram_id,
        )
        return result.endswith("1")

    async def remove_global_spam_any(self, telegram_id: int) -> bool:
        result = await self._pool.execute(
            "DELETE FROM global_spam_users_mode WHERE telegram_id=$1;",
            telegram_id,
        )
        return not result.endswith("0")

    async def count_global_spam(self, mode: str) -> int:
        value = await self._pool.fetchval(
            "SELECT COUNT(*) FROM global_spam_users_mode WHERE mode=$1;",
            mode,
        )
        return int(value or 0)

    async def count_global_spam_all(self) -> int:
        value = await self._pool.fetchval(
            "SELECT COUNT(DISTINCT telegram_id) FROM global_spam_users_mode;"
        )
        return int(value or 0)

    async def list_global_spam(self, mode: str, limit: int = 50) -> list[dict[str, object]]:
        rows = await self._pool.fetch(
            """
            SELECT
                telegram_id,
                target_username,
                source_group_id,
                source_group_title,
                source_group_username,
                source_poll_id,
                reason,
                created_at
            FROM global_spam_users_mode
            WHERE mode=$1
            ORDER BY created_at DESC
            LIMIT $2;
            """,
            mode,
            limit,
        )
        result: list[dict[str, object]] = []
        for r in rows:
            result.append(
                {
                    "telegram_id": int(r["telegram_id"]),
                    "target_username": str(r["target_username"]) if r["target_username"] is not None else None,
                    "source_group_id": int(r["source_group_id"]) if r["source_group_id"] is not None else None,
                    "source_group_title": str(r["source_group_title"]) if r["source_group_title"] is not None else None,
                    "source_group_username": str(r["source_group_username"]) if r["source_group_username"] is not None else None,
                    "source_poll_id": int(r["source_poll_id"]) if r["source_poll_id"] is not None else None,
                    "reason": str(r["reason"] or ""),
                    "created_at": r["created_at"],
                }
            )
        return result

    async def list_global_spam_all(self, limit: int = 50) -> list[dict[str, object]]:
        rows = await self._pool.fetch(
            """
            WITH ranked AS (
                SELECT
                    telegram_id,
                    target_username,
                    source_group_id,
                    source_group_title,
                    source_group_username,
                    source_poll_id,
                    reason,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY telegram_id ORDER BY created_at DESC) AS rn
                FROM global_spam_users_mode
            )
            SELECT
                telegram_id,
                target_username,
                source_group_id,
                source_group_title,
                source_group_username,
                source_poll_id,
                reason,
                created_at
            FROM ranked
            WHERE rn = 1
            ORDER BY created_at DESC
            LIMIT $1;
            """,
            limit,
        )
        result: list[dict[str, object]] = []
        for r in rows:
            result.append(
                {
                    "telegram_id": int(r["telegram_id"]),
                    "target_username": str(r["target_username"]) if r["target_username"] is not None else None,
                    "source_group_id": int(r["source_group_id"]) if r["source_group_id"] is not None else None,
                    "source_group_title": str(r["source_group_title"]) if r["source_group_title"] is not None else None,
                    "source_group_username": str(r["source_group_username"]) if r["source_group_username"] is not None else None,
                    "source_poll_id": int(r["source_poll_id"]) if r["source_poll_id"] is not None else None,
                    "reason": str(r["reason"] or ""),
                    "created_at": r["created_at"],
                }
            )
        return result

    async def get_open_poll(self, mode: str, group_chat_id: int, target_telegram_id: int) -> Optional[SpamPollRecord]:
        row = await self._pool.fetchrow(
            """
            SELECT *
            FROM spam_polls
            WHERE mode=$1 AND group_chat_id=$2 AND target_telegram_id=$3 AND status='open'
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            mode,
            group_chat_id,
            target_telegram_id,
        )
        return self._to_poll(row) if row else None

    async def create_poll(
        self,
        mode: str,
        group_chat_id: int,
        target_telegram_id: int,
        initiator_telegram_id: int,
        threshold: int,
        timeout_seconds: int,
    ) -> SpamPollRecord:
        row = await self._pool.fetchrow(
            """
            INSERT INTO spam_polls (
                mode, group_chat_id, target_telegram_id, initiator_telegram_id, threshold, expires_at
            )
            VALUES ($1, $2, $3, $4, $5, NOW() + ($6 * INTERVAL '1 second'))
            RETURNING *;
            """,
            mode,
            group_chat_id,
            target_telegram_id,
            initiator_telegram_id,
            threshold,
            timeout_seconds,
        )
        if not row:
            raise RuntimeError("failed to create spam poll")
        return self._to_poll(row)

    async def set_poll_message_id(self, poll_id: int, message_id: int) -> None:
        await self._pool.execute(
            "UPDATE spam_polls SET message_id=$2 WHERE id=$1;",
            poll_id,
            message_id,
        )

    async def get_poll(self, poll_id: int) -> Optional[SpamPollRecord]:
        row = await self._pool.fetchrow("SELECT * FROM spam_polls WHERE id=$1;", poll_id)
        return self._to_poll(row) if row else None

    async def register_vote(self, poll_id: int, voter_id: int, vote_yes: bool) -> tuple[bool, str, SpamPollRecord | None]:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT * FROM spam_polls WHERE id=$1 FOR UPDATE;", poll_id)
                if not row:
                    return (False, "poll_not_found", None)
                poll = self._to_poll(row)
                if poll.status != "open":
                    return (False, "poll_closed", poll)
                if poll.target_telegram_id == voter_id:
                    return (False, "self_vote", poll)

                inserted = await conn.execute(
                    """
                    INSERT INTO spam_poll_votes (poll_id, voter_id, vote_yes)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (poll_id, voter_id) DO NOTHING;
                    """,
                    poll_id,
                    voter_id,
                    vote_yes,
                )
                if inserted.endswith("0"):
                    return (False, "already_voted", poll)

                counts = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(CASE WHEN vote_yes THEN 1 ELSE 0 END), 0) AS yes_votes,
                        COALESCE(SUM(CASE WHEN NOT vote_yes THEN 1 ELSE 0 END), 0) AS no_votes
                    FROM spam_poll_votes
                    WHERE poll_id=$1;
                    """,
                    poll_id,
                )
                yes_votes = int(counts["yes_votes"]) if counts else 0
                no_votes = int(counts["no_votes"]) if counts else 0
                updated = await conn.fetchrow(
                    """
                    UPDATE spam_polls
                    SET yes_votes=$2, no_votes=$3
                    WHERE id=$1
                    RETURNING *;
                    """,
                    poll_id,
                    yes_votes,
                    no_votes,
                )
                if not updated:
                    return (False, "poll_not_found", None)
                return (True, "ok", self._to_poll(updated))

    async def close_poll(self, poll_id: int, status: str, decision: str) -> Optional[SpamPollRecord]:
        row = await self._pool.fetchrow(
            """
            UPDATE spam_polls
            SET status=$2, decision=$3, closed_at=NOW()
            WHERE id=$1 AND status='open'
            RETURNING *;
            """,
            poll_id,
            status,
            decision,
        )
        return self._to_poll(row) if row else None

    async def list_expired_open_polls(self, limit: int = 100) -> list[SpamPollRecord]:
        rows = await self._pool.fetch(
            """
            SELECT *
            FROM spam_polls
            WHERE status='open' AND expires_at <= NOW()
            ORDER BY expires_at ASC
            LIMIT $1;
            """,
            limit,
        )
        return [self._to_poll(r) for r in rows]

    async def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)
