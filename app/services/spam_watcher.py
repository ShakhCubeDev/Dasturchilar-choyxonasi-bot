from __future__ import annotations

import asyncio

from app.handlers.spam import process_expired_spam_polls
from app.services.context import AppContext


async def spam_poll_watcher(ctx: AppContext, bot) -> None:
    while True:
        try:
            await process_expired_spam_polls(ctx, bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            ctx.logger.exception("spam_poll_watcher_error")
        await asyncio.sleep(5)
