from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from asyncpg import Pool, Record


@dataclass(slots=True)
class BotTextRow:
    id: UUID
    lang: str
    key: str
    text: str
    version: int
    is_active: bool


class BotTextsRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @staticmethod
    def _row(r: Record) -> BotTextRow:
        return BotTextRow(
            id=r["id"],
            lang=r["lang"],
            key=r["key"],
            text=r["text"],
            version=r["version"],
            is_active=r["is_active"],
        )

    async def get_active(self, lang: str, key: str) -> Optional[BotTextRow]:
        r = await self._pool.fetchrow(
            """
            SELECT id, lang, key, text, version, is_active
            FROM bot_texts
            WHERE is_active=TRUE AND lang=$1 AND key=$2
            LIMIT 1;
            """,
            lang,
            key,
        )
        return self._row(r) if r else None

    async def ensure_schema(self) -> None:
        # Keep this minimal; no extensions required.
        await self._pool.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_texts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                lang VARCHAR(10) NOT NULL CHECK (lang IN ('uz', 'ru', 'en')),
                key TEXT NOT NULL,
                text TEXT NOT NULL,
                version INT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_by_admin_id UUID,
                UNIQUE (lang, key, version)
            );
            CREATE INDEX IF NOT EXISTS idx_bot_texts_lang_key ON bot_texts (lang, key);
            CREATE INDEX IF NOT EXISTS idx_bot_texts_active ON bot_texts (is_active);
            """
        )

    async def seed_defaults_if_missing(self, defaults: dict[str, dict[str, str]]) -> None:
        # Insert v1 active records only if no active text exists for (lang,key).
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for lang, items in defaults.items():
                    for key, text in items.items():
                        exists = await conn.fetchval(
                            "SELECT 1 FROM bot_texts WHERE is_active=TRUE AND lang=$1 AND key=$2;",
                            lang,
                            key,
                        )
                        if exists:
                            continue
                        await conn.execute(
                            """
                            INSERT INTO bot_texts (lang, key, text, version, is_active)
                            VALUES ($1, $2, $3, 1, TRUE);
                            """,
                            lang,
                            key,
                            text,
                        )

    async def ensure_active_text(self, lang: str, key: str, text: str) -> BotTextRow:
        # Creates and activates a new version if active text differs.
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                active = await conn.fetchrow(
                    """
                    SELECT id, lang, key, text, version, is_active
                    FROM bot_texts
                    WHERE is_active=TRUE AND lang=$1 AND key=$2
                    LIMIT 1;
                    """,
                    lang,
                    key,
                )
                if active and (active["text"] or "") == text:
                    return self._row(active)

                next_version = await conn.fetchval(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM bot_texts WHERE lang=$1 AND key=$2;",
                    lang,
                    key,
                )
                await conn.execute("UPDATE bot_texts SET is_active=FALSE WHERE lang=$1 AND key=$2;", lang, key)
                r = await conn.fetchrow(
                    """
                    INSERT INTO bot_texts (lang, key, text, version, is_active)
                    VALUES ($1, $2, $3, $4, TRUE)
                    RETURNING id, lang, key, text, version, is_active;
                    """,
                    lang,
                    key,
                    text,
                    int(next_version or 1),
                )
                if not r:
                    raise RuntimeError("Failed to ensure active text")
                return self._row(r)
