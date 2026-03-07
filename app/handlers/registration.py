from __future__ import annotations

from html import escape
import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.common import (
    EXPERIENCE_ITEMS,
    add_bot_to_group_keyboard,
    admin_reply_main_keyboard,
    confirm_keyboard,
    contact_keyboard,
    experience_keyboard,
    language_keyboard,
    remove_reply_keyboard,
)
from app.services.context import AppContext
from app.services.modes import MODE_DCH, group_mode
from app.states import RegistrationStates
from app.utils.language import preferred_user_lang
from app.utils.telegram_ops import touch_state, with_retry
from app.utils.validators import clean_text, is_spam_text, is_valid_name

router = Router(name="registration")
logger = logging.getLogger(__name__)

EXPERIENCE_BY_KEY = {key: {"uz": uz, "ru": ru, "en": en} for key, uz, ru, en in EXPERIENCE_ITEMS}


def _get_lang(data: dict[str, Any]) -> str:
    return data.get("language", "uz")


def _is_dch_mode(data: dict[str, Any], ctx: AppContext) -> bool:
    gid = data.get("group_chat_id")
    if gid is None:
        return False
    try:
        return group_mode(int(gid), ctx.settings) == MODE_DCH
    except Exception:
        return False


async def _t_mode(ctx: AppContext, lang: str, key: str, is_dch: bool, **kwargs: Any) -> str:
    if is_dch:
        return await ctx.texts.t(lang, key, **kwargs)
    other_key = f"other_{key}"
    text = await ctx.texts.t(lang, other_key, **kwargs)
    if text == other_key:
        return await ctx.texts.t(lang, key, **kwargs)
    return text


def _support_footer(lang: str) -> str:
    if lang == "ru":
        return "Если возникли проблемы, обратитесь к администратору бота: @CubeDev."
    if lang == "en":
        return "If you have any issues, contact the bot admin: @CubeDev."
    return "Agar muammo bolsa, bot admini @CubeDev ga murojaat qiling."


def _with_support(text: str, lang: str) -> str:
    return text.rstrip() + "\n\n" + _support_footer(lang)


def _owner_group_state_text(lang: str, enabled: bool) -> str:
    if lang == "ru":
        return "включена" if enabled else "выключена"
    if lang == "en":
        return "enabled" if enabled else "disabled"
    return "yoqilgan" if enabled else "o'chirilgan"


def _outside_start_text(lang: str, owned_groups: list[Any]) -> str:
    group_lines = []
    for group in owned_groups[:10]:
        title = escape(group.title)
        group_lines.append(
            {
                "uz": f"• {title} ({group.chat_id}) — ro'yxatdan o'tish: {_owner_group_state_text(lang, group.registration_enabled)}",
                "ru": f"• {title} ({group.chat_id}) — регистрация: {_owner_group_state_text(lang, group.registration_enabled)}",
                "en": f"• {title} ({group.chat_id}) — registration: {_owner_group_state_text(lang, group.registration_enabled)}",
            }[lang]
        )

    texts = {
        "uz": (
            "Assalomu alaykum. Men guruhingiz uchun ro'yxatdan o'tkazish va himoya botiman.\n\n"
            "Meni o'z guruhingizga qo'shib, admin huquqini bering. Shunda siz:\n"
            "• yangi userlarni deep link orqali ro'yxatdan o'tkazasiz\n"
            "• ro'yxatdan o'tmagan userlarni avtomatik cheklaysiz\n"
            "• global spam himoyasidan foydalanasiz\n"
            "• 18+ profil rasmi bo'lgan userlarni avtomatik aniqlaysiz\n"
            "• faqat o'zingiz qo'shgan guruhlar sozlamalarini boshqarasiz"
        ),
        "ru": (
            "Здравствуйте. Я бот для регистрации и защиты вашей группы.\n\n"
            "Добавьте меня в свою группу и выдайте права администратора. Тогда вы сможете:\n"
            "• регистрировать новых пользователей через deep link\n"
            "• автоматически ограничивать незарегистрированных участников\n"
            "• использовать глобальную spam-защиту\n"
            "• автоматически выявлять пользователей с 18+ аватаром\n"
            "• управлять только данными и настройками своих групп"
        ),
        "en": (
            "Hello. I am a registration and protection bot for your group.\n\n"
            "Add me to your group and grant admin rights. Then you will be able to:\n"
            "• register new users via deep links\n"
            "• automatically restrict unregistered members\n"
            "• use global spam protection\n"
            "• automatically detect users with 18+ profile photos\n"
            "• manage only the data and settings of your own groups"
        ),
    }
    text = texts[lang]
    if group_lines:
        header = {
            "uz": "\n\nSiz boshqarayotgan guruhlar:\n",
            "ru": "\n\nВаши группы:\n",
            "en": "\n\nYour groups:\n",
        }[lang]
        text += header + "\n".join(group_lines)
        tail = {
            "uz": "\n\nQulay boshqaruv uchun /panel yuboring yoki /group_reg <group_id> on|off ishlating.",
            "ru": "\n\nДля удобного управления отправьте /panel или используйте /group_reg <group_id> on|off.",
            "en": "\n\nFor easier control, send /panel or use /group_reg <group_id> on|off.",
        }[lang]
        text += tail
    return text


def _extract_start_payload(message: Message) -> str | None:
    txt = (message.text or "").strip()
    if not txt.startswith("/start"):
        return None
    parts = txt.split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip()


def _extract_group_from_payload(payload: str | None) -> int | None:
    if not payload or not payload.startswith("reg_"):
        return None
    raw = payload[4:].strip()
    if not raw:
        return None
    sign = -1 if raw.startswith("-") else 1
    digits = raw[1:] if sign == -1 else raw
    if not digits.isdigit():
        return None
    return sign * int(digits)


async def _resolve_group_for_start(message: Message, state: FSMContext, ctx: AppContext) -> tuple[int | None, str | None]:
    payload = _extract_start_payload(message)
    group_from_payload = _extract_group_from_payload(payload)
    if group_from_payload is not None:
        grp = await ctx.groups.get_by_chat_id(group_from_payload)
        if grp:
            return grp.chat_id, grp.title
        # Deep link always has priority: even if groups row is not present yet,
        # continue registration for the exact target group from payload.
        return group_from_payload, str(group_from_payload)

    return None, None


async def _preview(ctx: AppContext, data: dict[str, Any], lang: str) -> str:
    confirm = await _t_mode(ctx, lang, "confirm_prompt", _is_dch_mode(data, ctx))
    lines = [
        confirm,
        "",
        f"Group ID: {data.get('group_chat_id', '-')}",
        f"Name: {data.get('full_name') or '-'}",
        f"Username: @{data.get('username')}" if data.get("username") else "Username: -",
        f"Telegram ID: {data.get('telegram_id', '-')}",
        f"Age: {data.get('age', '-')}",
        f"Profession: {data.get('profession', '-')}",
        f"Experience: {data.get('experience', '-')}",
        f"Language: {data.get('language', '-')}",
        f"Purpose: {data.get('purpose', '-')}",
    ]
    return "\n".join(lines)


@router.message(CommandStart(), F.chat.type == "private")
async def start_registration(message: Message, state: FSMContext, ctx: AppContext) -> None:
    telegram_id = message.from_user.id
    username = message.from_user.username
    start_lang = preferred_user_lang(message.from_user.language_code if message.from_user else None)

    if telegram_id in set(ctx.settings.admin_ids):
        await state.clear()
        text = "Admin panelga xush kelibsiz."
        is_dev = telegram_id in set(ctx.settings.dev_admin_ids) if ctx.settings.dev_admin_ids else (
            bool(ctx.settings.admin_ids) and telegram_id == ctx.settings.admin_ids[0]
        )
        await message.answer(_with_support(text, "uz"), reply_markup=admin_reply_main_keyboard(is_dev))
        return

    group_chat_id, group_title = await _resolve_group_for_start(message, state, ctx)
    if group_chat_id is None:
        owned_groups = await ctx.groups.list_owned_groups(telegram_id)
        reply_markup = add_bot_to_group_keyboard(ctx.settings.bot_username, start_lang) if ctx.settings.bot_username else None
        await message.answer(_with_support(_outside_start_text(start_lang, owned_groups), start_lang), reply_markup=reply_markup)
        return

    mode = group_mode(group_chat_id, ctx.settings)
    if mode != MODE_DCH:
        group_info = await ctx.groups.get_by_chat_id(group_chat_id)
        if group_info and not group_info.registration_enabled:
            await message.answer(
                _with_support(
                    "Bu guruhda ro'yxatdan o'tish tizimi admin tomonidan o'chirilgan. Faqat global spam himoyasi ishlaydi.",
                    "uz",
                )
            )
            return

    try:
        user = await ctx.users.get_by_group_and_telegram_id(group_chat_id, telegram_id)
    except Exception:
        ctx.logger.exception("db_error: start_registration lookup failed")
        await message.answer(_with_support(await ctx.texts.t("uz", "db_error"), "uz"))
        return

    if user:
        await ctx.users.update_username(group_chat_id, telegram_id, username)
        if user.status == "active":
            lang = user.language
            await message.answer(_with_support(await ctx.texts.t(lang, "already_active"), lang))
            return
        await message.answer(_with_support(await ctx.texts.t(user.language, "need_reregister"), user.language))

    await state.clear()
    await state.set_state(RegistrationStates.language)
    await state.update_data(
        group_chat_id=group_chat_id,
        group_title=group_title,
        telegram_id=telegram_id,
        username=username,
    )
    await touch_state(state)
    await message.answer(
        _with_support("Tilni tanlang / \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u044f\u0437\u044b\u043a / Choose language", "uz"),
        reply_markup=language_keyboard(),
    )


@router.callback_query(RegistrationStates.group, F.data.startswith("group:"))
async def group_selected(callback: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    raw = callback.data.split(":", 1)[1]
    sign = -1 if raw.startswith("-") else 1
    digits = raw[1:] if sign == -1 else raw
    if not digits.isdigit():
        await callback.answer()
        return
    group_chat_id = sign * int(digits)
    grp = await ctx.groups.get_by_chat_id(group_chat_id)
    if not grp:
        await callback.answer(await ctx.texts.t("uz", "group_not_found"), show_alert=True)
        return

    values = await state.get_data()
    await state.set_state(RegistrationStates.language)
    await state.update_data(
        group_chat_id=grp.chat_id,
        group_title=grp.title,
        telegram_id=values.get("telegram_id", callback.from_user.id),
        username=values.get("username", callback.from_user.username),
    )
    await touch_state(state)
    if callback.message:
        await callback.message.answer(
            "Tilni tanlang / \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u044f\u0437\u044b\u043a / Choose language",
            reply_markup=language_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("lang:"))
async def language_selected(callback: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    lang = callback.data.split(":", 1)[1]
    if lang not in {"uz", "ru", "en"}:
        await callback.answer()
        return
    values = await state.get_data()
    if "telegram_id" not in values:
        await state.update_data(telegram_id=callback.from_user.id)
    if "username" not in values:
        await state.update_data(username=callback.from_user.username)
    await state.update_data(language=lang)
    await state.set_state(RegistrationStates.name)
    await touch_state(state)
    data = await state.get_data()
    if callback.message:
        await callback.message.answer(
            await _t_mode(ctx, lang, "name_prompt", _is_dch_mode(data, ctx)),
            reply_markup=remove_reply_keyboard(),
        )
    await callback.answer()


@router.message(RegistrationStates.language)
async def language_invalid(message: Message) -> None:
    await message.answer("Iltimos tugma orqali tilni tanlang.", reply_markup=language_keyboard())


@router.message(RegistrationStates.name)
async def name_step(message: Message, state: FSMContext, ctx: AppContext) -> None:
    data = await state.get_data()
    lang = _get_lang(data)
    is_dch = _is_dch_mode(data, ctx)
    if not message.text:
        await message.answer(await _t_mode(ctx, lang, "name_invalid", is_dch))
        return
    raw = clean_text(message.text)
    full_name = raw
    if not is_valid_name(full_name):
        await message.answer(await _t_mode(ctx, lang, "name_invalid", is_dch))
        return
    await state.update_data(full_name=full_name)
    await state.set_state(RegistrationStates.phone)
    await touch_state(state)
    await message.answer(await _t_mode(ctx, lang, "phone_prompt", is_dch), reply_markup=contact_keyboard(lang))


@router.message(RegistrationStates.phone)
async def phone_step(message: Message, state: FSMContext, ctx: AppContext) -> None:
    data = await state.get_data()
    lang = _get_lang(data)
    is_dch = _is_dch_mode(data, ctx)
    if not message.contact or message.contact.user_id != message.from_user.id:
        await message.answer(await _t_mode(ctx, lang, "phone_invalid", is_dch))
        return
    await state.update_data(phone=message.contact.phone_number)
    await state.set_state(RegistrationStates.age)
    await touch_state(state)
    await message.answer(await _t_mode(ctx, lang, "age_prompt", is_dch), reply_markup=remove_reply_keyboard())


@router.message(RegistrationStates.age)
async def age_step(message: Message, state: FSMContext, ctx: AppContext) -> None:
    data = await state.get_data()
    lang = _get_lang(data)
    is_dch = _is_dch_mode(data, ctx)
    if not message.text or not message.text.strip().isdigit():
        await message.answer(await _t_mode(ctx, lang, "age_invalid", is_dch))
        return
    age = int(message.text.strip())
    if age < 12 or age > 70:
        await message.answer(await _t_mode(ctx, lang, "age_invalid", is_dch))
        return
    await state.update_data(age=age)
    await state.set_state(RegistrationStates.profession)
    await touch_state(state)
    await message.answer(await _t_mode(ctx, lang, "profession_prompt", is_dch), reply_markup=remove_reply_keyboard())


@router.message(RegistrationStates.profession)
async def profession_step(message: Message, state: FSMContext, ctx: AppContext) -> None:
    data = await state.get_data()
    lang = _get_lang(data)
    is_dch = _is_dch_mode(data, ctx)
    if not message.text:
        await message.answer(await _t_mode(ctx, lang, "profession_invalid", is_dch))
        return
    profession = clean_text(message.text)
    if not profession or len(profession) > 100:
        await message.answer(await _t_mode(ctx, lang, "profession_invalid", is_dch))
        return
    await state.update_data(profession=profession)
    await state.set_state(RegistrationStates.experience)
    await touch_state(state)
    await message.answer(await _t_mode(ctx, lang, "experience_prompt", is_dch), reply_markup=experience_keyboard(lang))


@router.callback_query(RegistrationStates.experience, F.data.startswith("exp:"))
async def experience_step(callback: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    data = await state.get_data()
    lang = _get_lang(data)
    exp_key = callback.data.split(":", 1)[1]
    if exp_key not in EXPERIENCE_BY_KEY:
        await callback.answer()
        return
    await state.update_data(experience=EXPERIENCE_BY_KEY[exp_key][lang])
    await state.set_state(RegistrationStates.purpose)
    await touch_state(state)
    if callback.message:
        await callback.message.answer(await _t_mode(ctx, lang, "purpose_prompt", _is_dch_mode(data, ctx)))
    await callback.answer()


@router.message(RegistrationStates.experience)
async def experience_invalid(message: Message, state: FSMContext, ctx: AppContext) -> None:
    data = await state.get_data()
    lang = _get_lang(data)
    await message.answer(
        await _t_mode(ctx, lang, "experience_prompt", _is_dch_mode(data, ctx)),
        reply_markup=experience_keyboard(lang),
    )


@router.message(RegistrationStates.purpose)
async def purpose_step(message: Message, state: FSMContext, ctx: AppContext) -> None:
    data = await state.get_data()
    lang = _get_lang(data)
    is_dch = _is_dch_mode(data, ctx)
    if not message.text:
        await message.answer(await _t_mode(ctx, lang, "purpose_invalid", is_dch))
        return

    raw_text = clean_text(message.text)
    purpose = None if raw_text == "-" else raw_text
    if purpose and (len(purpose) > ctx.settings.max_purpose_length or is_spam_text(purpose)):
        await message.answer(await _t_mode(ctx, lang, "purpose_invalid", is_dch))
        return

    await state.update_data(purpose=purpose)
    await state.set_state(RegistrationStates.confirm)
    await touch_state(state)
    snapshot = await state.get_data()
    await message.answer(await _preview(ctx, snapshot, lang), reply_markup=confirm_keyboard(lang))


@router.callback_query(RegistrationStates.confirm, F.data == "confirm:reset")
async def confirm_reset(callback: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    data = await state.get_data()
    lang = _get_lang(data)
    telegram_id = data.get("telegram_id")
    username = data.get("username")
    group_chat_id = data.get("group_chat_id")
    group_title = data.get("group_title")
    await state.clear()
    await state.set_state(RegistrationStates.language)
    await state.update_data(
        telegram_id=telegram_id,
        username=username,
        group_chat_id=group_chat_id,
        group_title=group_title,
    )
    await touch_state(state)
    if callback.message:
        await callback.message.answer(
            await _t_mode(ctx, lang, "restart_info", _is_dch_mode(data, ctx)),
            reply_markup=language_keyboard(),
        )
    await callback.answer()


@router.callback_query(RegistrationStates.confirm, F.data == "confirm:yes")
async def confirm_submit(callback: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    snapshot = await state.get_data()
    required = {"group_chat_id", "telegram_id", "phone", "age", "profession", "experience", "language"}
    if not required.issubset(set(snapshot.keys())):
        await state.clear()
        if callback.message:
            await callback.message.answer(await ctx.texts.t("uz", "session_expired"))
        await callback.answer()
        return
    lang = _get_lang(snapshot)
    payload = {
        "group_chat_id": int(snapshot["group_chat_id"]),
        "telegram_id": int(snapshot["telegram_id"]),
        "username": snapshot.get("username"),
        "full_name": snapshot.get("full_name"),
        "phone": snapshot["phone"],
        "age": int(snapshot["age"]),
        "profession": snapshot["profession"],
        "experience": snapshot["experience"],
        "language": snapshot["language"],
        "purpose": snapshot.get("purpose"),
    }

    mode = group_mode(payload["group_chat_id"], ctx.settings)
    if mode == MODE_DCH:
        user = await _submit_registration_dch(payload, callback, ctx, lang)
    else:
        user = await _submit_registration_other_groups(payload, callback, ctx, lang)
    if user is None:
        return

    await state.clear()
    if callback.message:
        await callback.message.answer(await _t_mode(ctx, lang, "confirm_success", mode == MODE_DCH))
    await callback.answer()
    await ctx.gates.unmark(user.group_chat_id, user.telegram_id)

    try:
        from aiogram.types import ChatPermissions

        perms = ChatPermissions(can_send_messages=True)
        ok = await with_retry(
            lambda: callback.bot.restrict_chat_member(
                user.group_chat_id,
                user.telegram_id,
                permissions=perms,
                until_date=0,
            )
        )
        if ok is None:
            raise RuntimeError("restrict_chat_member returned None")
    except TelegramBadRequest as exc:
        if "administrator" not in str(exc).lower():
            ctx.logger.exception("unrestrict_failed group=%s telegram_id=%s", user.group_chat_id, user.telegram_id)
    except Exception:
        ctx.logger.exception("unrestrict_failed group=%s telegram_id=%s", user.group_chat_id, user.telegram_id)

    if mode == MODE_DCH:
        await notify_group_admin_about_registration_dch(ctx, callback.bot, user)
    else:
        await notify_group_admin_about_registration_other_groups(ctx, callback.bot, user)

async def notify_group_admin_about_registration(ctx: AppContext, bot: Any, user: Any) -> None:
    from app.keyboards.common import admin_reject_keyboard

    grp = await ctx.groups.get_by_chat_id(user.group_chat_id)
    recipients = set(ctx.settings.admin_ids)
    if grp:
        recipients.add(grp.owner_telegram_id)
    primary_admin_id = ctx.settings.admin_ids[0] if ctx.settings.admin_ids else None

    for admin_id in recipients:
        lines = [
            "New User Registered",
            "",
            f"Group: {grp.title if grp else user.group_chat_id}",
            f"Name: {user.full_name or '-'}",
            f"Username: @{user.username}" if user.username else "Username: -",
            f"Telegram ID: {user.telegram_id}",
            f"Age: {user.age}",
            f"Profession: {user.profession}",
            f"Experience: {user.experience}",
            f"Language: {user.language}",
            f"Purpose: {user.purpose or '-'}",
        ]
        if primary_admin_id is not None and admin_id == primary_admin_id:
            lines.append(f"Phone: {user.phone}")
        text = "\n".join(lines)
        try:
            await with_retry(
                lambda admin=admin_id: bot.send_message(
                    admin,
                    text,
                    reply_markup=admin_reject_keyboard(user.group_chat_id, user.telegram_id),
                )
            )
        except TelegramBadRequest:
            logger.exception("registration_failed: admin message failed admin_id=%s", admin_id)


async def _submit_registration_dch(payload: dict[str, Any], callback: CallbackQuery, ctx: AppContext, lang: str):
    try:
        user = await ctx.users.upsert_user(payload)
        ctx.logger.info("registration_success mode=dch group=%s telegram_id=%s", user.group_chat_id, user.telegram_id)
        return user
    except Exception:
        ctx.logger.exception("registration_failed mode=dch")
        if callback.message:
            await callback.message.answer(await ctx.texts.t(lang, "db_error"))
        await callback.answer()
        return None


async def _submit_registration_other_groups(payload: dict[str, Any], callback: CallbackQuery, ctx: AppContext, lang: str):
    try:
        user = await ctx.users.upsert_user(payload)
        ctx.logger.info("registration_success mode=other_groups group=%s telegram_id=%s", user.group_chat_id, user.telegram_id)
        return user
    except Exception:
        ctx.logger.exception("registration_failed mode=other_groups")
        if callback.message:
            await callback.message.answer(await ctx.texts.t(lang, "db_error"))
        await callback.answer()
        return None


async def notify_group_admin_about_registration_dch(ctx: AppContext, bot: Any, user: Any) -> None:
    await notify_group_admin_about_registration(ctx, bot, user)


async def notify_group_admin_about_registration_other_groups(ctx: AppContext, bot: Any, user: Any) -> None:
    from app.keyboards.common import admin_reject_keyboard

    grp = await ctx.groups.get_by_chat_id(user.group_chat_id)
    recipients: set[int] = set()
    if ctx.settings.admin_ids:
        recipients.add(ctx.settings.admin_ids[0])
    if grp:
        recipients.add(grp.owner_telegram_id)
    if not recipients:
        return

    lines = [
        "New User Registered",
        "",
        f"Group: {grp.title if grp else user.group_chat_id}",
        f"Name: {user.full_name or '-'}",
        f"Username: @{user.username}" if user.username else "Username: -",
        f"Telegram ID: {user.telegram_id}",
        f"Age: {user.age}",
        f"Profession: {user.profession}",
        f"Experience: {user.experience}",
        f"Language: {user.language}",
        f"Purpose: {user.purpose or '-'}",
        f"Phone: {user.phone}",
    ]
    text = "\n".join(lines)
    for admin_id in recipients:
        try:
            await with_retry(
                lambda target=admin_id: bot.send_message(
                    target,
                    text,
                    reply_markup=admin_reject_keyboard(user.group_chat_id, user.telegram_id),
                )
            )
        except TelegramBadRequest:
            logger.exception("registration_failed: admin message failed admin_id=%s", admin_id)
