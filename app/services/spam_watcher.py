from __future__ import annotations

import asyncio

from app.handlers.spam import process_expired_spam_polls
from app.services.context import AppContext


async def spam_poll_watcher(ctx: AppContext, bot) -> None:
    error_count = 0
    max_sleep = 300  # 5 daqiqa maksimum
    base_sleep = 5   # 5 soniya asosiy
    
    while True:
        try:
            processed = await process_expired_spam_polls(ctx, bot)
            # Agar muvaffaqiyatli bo'lsa, xato sanog'ini qayta tiklash
            if error_count > 0:
                ctx.logger.info("spam_poll_watcher recovered after %d errors", error_count)
                error_count = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_count += 1
            # Birinchi 3 xatoni logga yozish, keyin har 10 xatodan birini
            if error_count <= 3 or error_count % 10 == 0:
                ctx.logger.exception("spam_poll_watcher_error (count=%d)", error_count)
            # Exponential backoff: 5, 10, 20, 40... 300 soniyagacha
            sleep_time = min(base_sleep * (2 ** (error_count - 1)), max_sleep)
            await asyncio.sleep(sleep_time)
            continue
        
        await asyncio.sleep(base_sleep)
