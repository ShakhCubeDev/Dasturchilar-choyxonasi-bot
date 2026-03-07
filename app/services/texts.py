from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.repositories.texts import BotTextsRepository
from app.text_defaults import TEXTS as DEFAULT_TEXTS


@dataclass
class TextService:
    repo: BotTextsRepository
    ttl_seconds: int = 30

    def __post_init__(self) -> None:
        self._cache: dict[tuple[str, str], tuple[float, str]] = {}

    async def t(self, lang: str, key: str, **kwargs: Any) -> str:
        language = lang if lang in {"uz", "ru", "en"} else "uz"
        now = time.monotonic()
        cache_key = (language, key)
        cached = self._cache.get(cache_key)
        if cached and (now - cached[0]) < self.ttl_seconds:
            text = cached[1]
        else:
            row = await self.repo.get_active(language, key)
            if row:
                text = row.text
            else:
                text = DEFAULT_TEXTS.get(language, DEFAULT_TEXTS["uz"]).get(key, DEFAULT_TEXTS["uz"].get(key, key))
            self._cache[cache_key] = (now, text)

        if kwargs:
            try:
                return text.format(**kwargs)
            except Exception:
                return text
        return text

