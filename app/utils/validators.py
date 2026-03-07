from __future__ import annotations

import re

# NOTE: Purpose text should allow @mentions; we only treat URLs as spam.
LINK_PATTERN = re.compile(r"(https?://|www\.|t\.me/)", re.IGNORECASE)


def is_valid_name(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) < 2 or len(stripped) > 60:
        return False
    if "http" in stripped.lower() or "t.me" in stripped.lower():
        return False
    for char in stripped:
        if not (char.isalpha() or char in " -'"):
            return False
    return True


def is_spam_text(value: str) -> bool:
    return bool(LINK_PATTERN.search(value))


def clean_text(value: str) -> str:
    return " ".join(value.strip().split())
