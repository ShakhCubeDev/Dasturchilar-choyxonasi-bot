from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from asyncpg import Pool


class DatabaseCleanupService:
    """Database tozalash xizmati - eski yozuvlarni o'chirish."""

    def __init__(self, pool: Pool, logger) -> None:
        self._pool = pool
        self._logger = logger

    async def cleanup_fsm_storage(self, days: int = 7) -> int:
        """Eski FSM storage yozuvlarini o'chirish."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = """
            DELETE FROM fsm_storage 
            WHERE updated_at < $1 
            AND (
                state IS NULL 
                OR state = '' 
                OR state LIKE '%:language' 
                OR state LIKE '%:name'
            )
        """
        result = await self._pool.execute(query, cutoff)
        try:
            count = int(result.split()[-1])
            return count
        except Exception:
            return 0

    async def cleanup_join_gates(self, days: int = 1) -> int:
        """Eski join_gates yozuvlarini o'chirish (user allaqachon ro'yxatdan o'tgan)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        # Faqat eski va aktiv userlarga tegishli bo'lmaganlarni o'chirish
        query = """
            DELETE FROM join_gates 
            WHERE created_at < $1
            AND NOT EXISTS (
                SELECT 1 FROM users 
                WHERE users.group_chat_id = join_gates.group_chat_id 
                AND users.telegram_id = join_gates.telegram_id
                AND users.status = 'active'
            )
        """
        result = await self._pool.execute(query, cutoff)
        try:
            count = int(result.split()[-1])
            return count
        except Exception:
            return 0

    async def cleanup_closed_spam_polls(self, days: int = 30) -> int:
        """Yopilgan eski spam poll larini o'chirish."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = """
            DELETE FROM spam_polls 
            WHERE status != 'open' 
            AND closed_at < $1
        """
        result = await self._pool.execute(query, cutoff)
        try:
            count = int(result.split()[-1])
            return count
        except Exception:
            return 0

    async def cleanup_orphan_votes(self) -> int:
        """Ota poll i yo'q qolgan votelarni o'chirish (CASCADE bo'lmasa)."""
        query = """
            DELETE FROM spam_poll_votes 
            WHERE poll_id NOT IN (
                SELECT id FROM spam_polls
            )
        """
        result = await self._pool.execute(query)
        try:
            count = int(result.split()[-1])
            return count
        except Exception:
            return 0

    async def run_cleanup(self) -> dict[str, int]:
        """Barcha cleanup operatsiyalarini bajarish."""
        results = {}
        
        try:
            results['fsm_storage'] = await self.cleanup_fsm_storage(days=7)
        except Exception as e:
            self._logger.exception("cleanup_fsm_storage failed")
            results['fsm_storage'] = -1
        
        try:
            results['join_gates'] = await self.cleanup_join_gates(days=1)
        except Exception as e:
            self._logger.exception("cleanup_join_gates failed")
            results['join_gates'] = -1
        
        try:
            results['spam_polls'] = await self.cleanup_closed_spam_polls(days=30)
        except Exception as e:
            self._logger.exception("cleanup_closed_spam_polls failed")
            results['spam_polls'] = -1
        
        try:
            results['orphan_votes'] = await self.cleanup_orphan_votes()
        except Exception as e:
            self._logger.exception("cleanup_orphan_votes failed")
            results['orphan_votes'] = -1
        
        return results


async def cleanup_watcher(pool: Pool, logger, interval_hours: int = 24) -> None:
    """Har interval_hours soatda cleanup ishga tushirish."""
    cleanup_service = DatabaseCleanupService(pool, logger)
    error_count = 0
    
    while True:
        try:
            # Birinchi marta kutish (bot ishga tushganda darhol emas)
            await asyncio.sleep(interval_hours * 3600)
            
            logger.info("Starting database cleanup...")
            results = await cleanup_service.run_cleanup()
            
            total = sum(v for v in results.values() if v > 0)
            if total > 0:
                logger.info(
                    "Database cleanup completed: fsm=%s, gates=%s, polls=%s, votes=%s",
                    results.get('fsm_storage', 0),
                    results.get('join_gates', 0),
                    results.get('spam_polls', 0),
                    results.get('orphan_votes', 0)
                )
            
            error_count = 0  # Reset on success
            
        except asyncio.CancelledError:
            raise
        except Exception:
            error_count += 1
            if error_count <= 3:
                logger.exception("cleanup_watcher_error (count=%d)", error_count)
            await asyncio.sleep(min(300 * error_count, 3600))  # 5 min -> 1 hour
