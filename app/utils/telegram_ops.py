from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, TypeVar

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

T = TypeVar("T")

# Background tasklarni kuzatish uchun set
_background_tasks: set[asyncio.Task] = set()


async def with_retry(operation: Callable[[], Awaitable[T]], attempts: int = 3) -> T | None:
    for index in range(attempts):
        try:
            return await operation()
        except TelegramRetryAfter as exc:
            if index == attempts - 1:
                return None
            await asyncio.sleep(exc.retry_after + 0.2)
    return None


async def safe_delete_message(bot: Bot, chat_id: int, message_id: int) -> None:
    async def _delete() -> None:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)

    try:
        await with_retry(_delete)
    except (TelegramBadRequest, TelegramForbiddenError):
        return


def delete_message_later(bot: Bot, chat_id: int, message_id: int, delay_seconds: int = 60) -> None:
    """Xabarni keyinroq o'chirish. Task avtomatik ravishda kuzatiladi va tozalanadi."""
    async def _job() -> None:
        await asyncio.sleep(delay_seconds)
        await safe_delete_message(bot, chat_id, message_id)

    task = asyncio.create_task(_job())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def reply_with_retry(message: Message, text: str, **kwargs: object) -> Message | None:
    return await with_retry(lambda: message.reply(text, **kwargs))


def utc_timestamp() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def touch_state(state: FSMContext) -> None:
    await state.update_data(last_activity=utc_timestamp())
