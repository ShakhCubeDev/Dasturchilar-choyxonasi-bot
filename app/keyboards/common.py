from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

LANGUAGE_ITEMS = [
    ("uz", "Uzbek"),
    ("ru", "Русский"),
    ("en", "English"),
]

EXPERIENCE_ITEMS = [
    ("1y", "1 yil", "1 год", "1 year"),
    ("2_3y", "2-3 yil", "2-3 года", "2-3 years"),
    ("4_7y", "4-7 yil", "4-7 лет", "4-7 years"),
    ("8_plus", "8+ yil", "8+ лет", "8+ years"),
]


def language_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=title, callback_data=f"lang:{code}")] for code, title in LANGUAGE_ITEMS]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def contact_keyboard(lang: str) -> ReplyKeyboardMarkup:
    if lang == "ru":
        title = "Отправить контакт"
    elif lang == "en":
        title = "Send contact"
    else:
        title = "Kontakt yuborish"
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=title, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def remove_reply_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def experience_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = []
    for key, uz, ru, en in EXPERIENCE_ITEMS:
        text = {"uz": uz, "ru": ru, "en": en}.get(lang, uz)
        rows.append([InlineKeyboardButton(text=text, callback_data=f"exp:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard(lang: str) -> InlineKeyboardMarkup:
    confirm = {"uz": "Tasdiqlash", "ru": "Подтвердить", "en": "Confirm"}.get(lang, "Tasdiqlash")
    reset = {"uz": "Qayta toldirish", "ru": "Заполнить заново", "en": "Fill again"}.get(lang, "Qayta toldirish")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=confirm, callback_data="confirm:yes")],
            [InlineKeyboardButton(text=reset, callback_data="confirm:reset")],
        ]
    )


def registration_deeplink_keyboard(bot_username: str, group_chat_id: int, lang: str = "uz") -> InlineKeyboardMarkup:
    url = f"https://t.me/{bot_username}?start=reg_{group_chat_id}"
    title = {
        "uz": "Ro'yxatdan o'tish",
        "ru": "Зарегистрироваться",
        "en": "Register",
    }.get(lang, "Ro'yxatdan o'tish")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=title, url=url)]])


def add_bot_to_group_keyboard(bot_username: str, lang: str = "uz") -> InlineKeyboardMarkup:
    url = f"https://t.me/{bot_username}?startgroup=start&admin=restrict_members+delete_messages+invite_users"
    title = {
        "uz": "Botni guruhga qo'shish",
        "ru": "Добавить бота в группу",
        "en": "Add Bot To Group",
    }.get(lang, "Botni guruhga qo'shish")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=title, url=url)]])


def group_select_keyboard(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = []
    for chat_id, title in items[:20]:
        rows.append([InlineKeyboardButton(text=title[:48], callback_data=f"group:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_reject_keyboard(group_chat_id: int, telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Malumotlar notogri", callback_data=f"reject:{group_chat_id}:{telegram_id}")]
        ]
    )


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Barchani unblock qilish", callback_data="admin:unblock_all")],
            [InlineKeyboardButton(text="Barchaga habar jonatish", callback_data="admin:broadcast")],
        ]
    )


def admin_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Yuborish", callback_data="admin:broadcast_confirm")],
            [InlineKeyboardButton(text="Bekor qilish", callback_data="admin:broadcast_cancel")],
        ]
    )


def spam_vote_keyboard(poll_id: int, yes_votes: int, no_votes: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Ha ({yes_votes})", callback_data=f"spamvote:{poll_id}:yes")],
            [InlineKeyboardButton(text=f"Yoq ({no_votes})", callback_data=f"spamvote:{poll_id}:no")],
        ]
    )


def admin_reply_main_keyboard(is_dev_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Spam Boshqaruv"), KeyboardButton(text="Broadcast")],
        [KeyboardButton(text="Barchani Unblock"), KeyboardButton(text="Set Active")],
        [KeyboardButton(text="Holat")],
    ]
    if is_dev_admin:
        rows.append([KeyboardButton(text="Dev Admin"), KeyboardButton(text="Userni To'liq O'chirish")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def admin_reply_spam_modes_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="DCH"), KeyboardButton(text="Boshqa guruhlar")],
            [KeyboardButton(text="Orqaga")],
        ],
        resize_keyboard=True,
    )


def admin_reply_spam_actions_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Threshold O'zgartirish"), KeyboardButton(text="Timeout O'zgartirish")],
            [KeyboardButton(text="Global On/Off"), KeyboardButton(text="Spamdan Chiqarish")],
            [KeyboardButton(text="Spam Ro'yxati"), KeyboardButton(text="Holatni Ko'rish")],
            [KeyboardButton(text="Orqaga")],
        ],
        resize_keyboard=True,
    )


def admin_reply_on_off_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Yoqish"), KeyboardButton(text="O'chirish")],
            [KeyboardButton(text="Bekor qilish")],
        ],
        resize_keyboard=True,
    )


def admin_reply_confirm_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Yuborish"), KeyboardButton(text="Bekor qilish")],
        ],
        resize_keyboard=True,
    )


def admin_reply_cancel_back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Bekor qilish"), KeyboardButton(text="Orqaga")],
        ],
        resize_keyboard=True,
    )


def registration_toggle_keyboard(group_chat_id: int, lang: str = "uz") -> InlineKeyboardMarkup:
    on_text = {
        "uz": "Ro'yxatdan o'tishni YOQISH",
        "ru": "ВКЛЮЧИТЬ регистрацию",
        "en": "ENABLE registration",
    }.get(lang, "Ro'yxatdan o'tishni YOQISH")
    off_text = {
        "uz": "Ro'yxatdan o'tishni O'CHIRISH",
        "ru": "ВЫКЛЮЧИТЬ регистрацию",
        "en": "DISABLE registration",
    }.get(lang, "Ro'yxatdan o'tishni O'CHIRISH")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=on_text, callback_data=f"regctl:{group_chat_id}:on"),
                InlineKeyboardButton(text=off_text, callback_data=f"regctl:{group_chat_id}:off"),
            ]
        ]
    )
