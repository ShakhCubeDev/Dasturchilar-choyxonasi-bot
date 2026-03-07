from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.utils.telegram_ops import touch_state


class FSMTimeoutMiddleware(BaseMiddleware):
    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        state: FSMContext | None = data.get("state")
        if not state:
            return await handler(event, data)

        state_name = await state.get_state()
        if not state_name:
            return await handler(event, data)

        values = await state.get_data()
        last_activity_raw = values.get("last_activity")
        if last_activity_raw:
            try:
                last_activity = datetime.fromisoformat(last_activity_raw)
                now = datetime.now(tz=timezone.utc)
                if (now - last_activity).total_seconds() > self.timeout_seconds:
                    await state.clear()
                    await self._notify_expired(event)
                    return None
            except ValueError:
                pass

        await touch_state(state)
        return await handler(event, data)

    @staticmethod
    async def _notify_expired(event: TelegramObject) -> None:
        if isinstance(event, Message):
            await event.answer("Sessiya tugadi. /start bosing.")
            return
        if isinstance(event, CallbackQuery):
            if event.message:
                await event.message.answer("Sessiya tugadi. /start bosing.")
            await event.answer()
