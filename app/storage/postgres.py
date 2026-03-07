from __future__ import annotations

import json
from dataclasses import dataclass

from aiogram.fsm.storage.base import BaseStorage, StorageKey
from asyncpg import Pool


@dataclass(slots=True)
class PostgresStorage(BaseStorage):
    pool: Pool

    @staticmethod
    async def ensure_schema(pool: Pool) -> None:
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS fsm_storage (
                key TEXT PRIMARY KEY,
                state TEXT,
                data JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_fsm_storage_updated_at ON fsm_storage (updated_at);
            """
        )

    @staticmethod
    def _k(key: StorageKey) -> str:
        # We include all parts to avoid collisions and keep it stable across restarts.
        return (
            f"bot:{key.bot_id}|chat:{key.chat_id}|user:{key.user_id}"
            f"|thread:{key.thread_id or ''}|biz:{key.business_connection_id or ''}|dest:{key.destiny or ''}"
        )

    async def close(self) -> None:
        # Pool is owned by the app; nothing to close here.
        return None

    async def set_state(self, key: StorageKey, state: str | object | None = None) -> None:
        # aiogram passes State objects; store the actual state string (e.g. "Group:state").
        if state is None:
            state_str = None
        elif isinstance(state, str):
            state_str = state
        else:
            state_attr = getattr(state, "state", None)
            state_str = state_attr if isinstance(state_attr, str) else str(state)
        await self.pool.execute(
            """
            INSERT INTO fsm_storage (key, state, data, updated_at)
            VALUES ($1, $2, COALESCE((SELECT data FROM fsm_storage WHERE key=$1), '{}'::jsonb), NOW())
            ON CONFLICT (key) DO UPDATE SET state=EXCLUDED.state, updated_at=NOW();
            """,
            self._k(key),
            state_str,
        )

    async def get_state(self, key: StorageKey) -> str | None:
        val = await self.pool.fetchval("SELECT state FROM fsm_storage WHERE key=$1;", self._k(key))
        if not val:
            return None
        if not isinstance(val, str):
            return str(val)
        # Backward-compat for accidentally stored State.__str__ format: "<State 'Group:state'>"
        if val.startswith("<State '") and val.endswith("'>"):
            return val[len("<State '") : -2]
        return val

    async def set_data(self, key: StorageKey, data) -> None:
        payload = json.dumps(dict(data))
        await self.pool.execute(
            """
            INSERT INTO fsm_storage (key, state, data, updated_at)
            VALUES ($1, COALESCE((SELECT state FROM fsm_storage WHERE key=$1), NULL), $2::jsonb, NOW())
            ON CONFLICT (key) DO UPDATE SET data=EXCLUDED.data, updated_at=NOW();
            """,
            self._k(key),
            payload,
        )

    async def get_data(self, key: StorageKey) -> dict[str, object]:
        row = await self.pool.fetchrow("SELECT data FROM fsm_storage WHERE key=$1;", self._k(key))
        if not row:
            return {}
        raw = row["data"]
        if raw is None:
            return {}
        # asyncpg usually decodes jsonb to dict, but be defensive across envs.
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            try:
                obj = json.loads(raw)
                return dict(obj) if isinstance(obj, dict) else {}
            except Exception:
                return {}
        return {}
