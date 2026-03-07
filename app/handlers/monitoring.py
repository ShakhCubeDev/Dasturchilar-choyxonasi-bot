from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.types import CallbackQuery, ChatMemberUpdated

from app.keyboards.common import registration_toggle_keyboard
from app.services.context import AppContext
from app.services.modes import MODE_DCH, group_mode
from app.utils.language import preferred_user_lang
from app.utils.telegram_ops import with_retry

router = Router(name="monitoring")


def _owner_onboarding_text(lang: str, group_title: str, group_chat_id: int, is_admin: bool) -> str:
    safe_title = escape(group_title)
    if lang == "ru":
        base = (
            f"Бот подключён к группе <b>{safe_title}</b>.\n\n"
            "Что вы получаете:\n"
            "• регистрацию новых участников через deep link\n"
            "• автоограничение незарегистрированных пользователей\n"
            "• глобальную spam-защиту\n"
            "• фильтр 18+ аватаров при входе\n"
            "• управление только своей группой\n\n"
        )
        if is_admin:
            return (
                base
                + "Сейчас у бота уже есть права администратора, значит все защитные функции активны.\n"
                + f"Регистрацию можно переключать кнопками ниже, командой /group_reg {group_chat_id} on|off или через /panel."
            )
        return (
            base
            + "Чтобы включить все защитные функции, выдайте боту права администратора.\n"
            + "После этого вы сможете управлять регистрацией только в своей группе."
        )
    if lang == "en":
        base = (
            f"The bot is connected to <b>{safe_title}</b>.\n\n"
            "What you get:\n"
            "• deep-link registration for new members\n"
            "• automatic restriction for unregistered users\n"
            "• global spam protection\n"
            "• 18+ avatar filtering on join\n"
            "• management limited to your own group\n\n"
        )
        if is_admin:
            return (
                base
                + "The bot already has admin rights, so all protection features are active.\n"
                + f"You can switch registration with the buttons below, with /group_reg {group_chat_id} on|off, or via /panel."
            )
        return (
            base
            + "Grant admin rights to activate all protection features.\n"
            + "After that you will manage registration only for your own group."
        )
    base = (
        f"Bot <b>{safe_title}</b> guruhiga ulandi.\n\n"
        "Nimalarga ega bo'lasiz:\n"
        "• yangi userlarni deep link orqali ro'yxatdan o'tkazish\n"
        "• ro'yxatdan o'tmagan userlarni avtomatik cheklash\n"
        "• global spam himoyasi\n"
        "• kirishda 18+ avatar filtratsiyasi\n"
        "• faqat o'zingizning guruhingizni boshqarish\n\n"
    )
    if is_admin:
        return (
            base
            + "Botda admin huquqi bor, demak himoya funksiyalari hozirning o'zida ishlaydi.\n"
            + f"Registration holatini pastdagi tugmalar, /group_reg {group_chat_id} on|off yoki /panel orqali boshqarishingiz mumkin."
        )
    return (
        base
        + "Himoya funksiyalarini to'liq yoqish uchun botga admin huquqi bering.\n"
        + "Shundan keyin faqat o'zingizning guruhingiz registration sozlamalarini boshqarasiz."
    )


@router.my_chat_member()
async def bot_chat_membership_changed(event: ChatMemberUpdated, ctx: AppContext) -> None:
    chat = event.chat
    if chat.type not in {"group", "supergroup"}:
        return

    new_status = event.new_chat_member.status
    old_status = event.old_chat_member.status
    is_admin = new_status == ChatMemberStatus.ADMINISTRATOR

    if new_status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
        await ctx.groups.set_bot_admin(chat.id, False)
        ctx.logger.warning("bot_removed chat_id=%s title=%s", chat.id, chat.title)
        text = f"Bot guruhdan chiqarildi. chat_id={chat.id}, title={chat.title}"
        for admin_id in ctx.settings.admin_ids:
            await with_retry(lambda admin=admin_id: event.bot.send_message(admin, text))
        return

    actor_id = event.from_user.id if event.from_user else 0
    mode = group_mode(chat.id, ctx.settings)
    registration_enabled_default = True
    try:
        grp = await ctx.groups.upsert_group_with_registration(
            chat_id=chat.id,
            title=chat.title or str(chat.id),
            owner_telegram_id=actor_id,
            bot_is_admin=is_admin,
            registration_enabled=registration_enabled_default,
        )
    except Exception:
        ctx.logger.exception("group_upsert_failed chat_id=%s", chat.id)
        return

    if actor_id and mode != MODE_DCH and old_status != new_status:
        owner_lang = preferred_user_lang(event.from_user.language_code if event.from_user else None)
        help_text = _owner_onboarding_text(owner_lang, grp.title, grp.chat_id, is_admin)
        try:
            await with_retry(
                lambda: event.bot.send_message(
                    actor_id,
                    help_text,
                    reply_markup=registration_toggle_keyboard(grp.chat_id, owner_lang) if is_admin else None,
                )
            )
        except Exception:
            ctx.logger.exception("group_owner_dm_failed chat_id=%s owner=%s", grp.chat_id, actor_id)

    if old_status != new_status:
        ctx.logger.info("bot_membership_update chat_id=%s old=%s new=%s admin=%s", chat.id, old_status, new_status, is_admin)


@router.callback_query(F.data.startswith("regctl:"))
async def registration_control_callback(callback: CallbackQuery, ctx: AppContext) -> None:
    if not callback.from_user:
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    raw_gid = parts[1]
    mode_raw = parts[2]
    sign = -1 if raw_gid.startswith("-") else 1
    digits = raw_gid[1:] if sign == -1 else raw_gid
    if not digits.isdigit() or mode_raw not in {"on", "off"}:
        await callback.answer()
        return
    chat_id = sign * int(digits)
    mode = group_mode(chat_id, ctx.settings)
    if mode == MODE_DCH:
        await callback.answer("DCH uchun bu sozlama o'zgarmaydi.", show_alert=True)
        return

    grp = await ctx.groups.get_by_chat_id(chat_id)
    if not grp:
        await callback.answer("Guruh topilmadi.", show_alert=True)
        return

    if callback.from_user.id not in set(ctx.settings.admin_ids) and callback.from_user.id != grp.owner_telegram_id:
        try:
            member = await with_retry(lambda: callback.bot.get_chat_member(chat_id, callback.from_user.id))
        except Exception:
            await callback.answer("Guruh adminligini tekshirib bo'lmadi.", show_alert=True)
            return
        if not member or member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
            await callback.answer("Bu amal faqat o'sha guruh egasi yoki adminlari uchun.", show_alert=True)
            return

    enabled = mode_raw == "on"
    await ctx.groups.set_registration_enabled(chat_id, enabled)
    await callback.answer("Saqlandi", show_alert=False)
    if callback.message:
        state_label = "YOQILDI" if enabled else "O'CHIRILDI"
        await callback.message.edit_text(
            f"Ro'yxatdan o'tish holati: {state_label}\n"
            f"Group: {chat_id}\n"
            f"Buyruq: /group_reg {chat_id} {'on' if enabled else 'off'}"
        )
