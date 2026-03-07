from __future__ import annotations


def preferred_user_lang(language_code: str | None) -> str:
    code = (language_code or "").strip().lower()
    if code.startswith("ru"):
        return "ru"
    if code.startswith("en"):
        return "en"
    return "uz"
