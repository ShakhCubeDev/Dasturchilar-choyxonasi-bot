from __future__ import annotations

from app.config import Settings


MODE_DCH = "dch"
MODE_OTHER = "other_groups"


def group_mode(chat_id: int, settings: Settings) -> str:
    if chat_id == settings.special_group_id:
        return MODE_DCH
    return MODE_OTHER


def mode_title(mode: str) -> str:
    if mode == MODE_DCH:
        return "DCH"
    return "Boshqa guruhlar"
