from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.types import CallbackQuery, ChatMemberUpdated

from app.keyboards.common import registration_toggle_keyboard
from app.services.context import AppContext
from app.services.modes import MODE_DCH, group_mode
from app.utils.telegram_ops import with_retry

router = Router(name="monitoring")


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
        help_text = (
            f"Guruh: {grp.title}\n\n"
            "Ro'yxatdan o'tish tizimini yoqish/o'chirishni hozir tanlang.\n"
            "Keyin ham boshqarish mumkin: /group_reg "
            f"{grp.chat_id} on yoki /group_reg {grp.chat_id} off"
        )
        try:
            await with_retry(
                lambda: event.bot.send_message(
                    actor_id,
                    help_text,
                    reply_markup=registration_toggle_keyboard(grp.chat_id),
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
    if not ctx.settings.admin_ids or callback.from_user.id != ctx.settings.admin_ids[0]:
        await callback.answer("Bu amal faqat Asosiy Admin uchun.", show_alert=True)
        return

    try:
        member = await with_retry(lambda: callback.bot.get_chat_member(chat_id, callback.from_user.id))
    except Exception:
        await callback.answer("Guruh adminligini tekshirib bo'lmadi.", show_alert=True)
        return
    if not member or member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        await callback.answer("Bu amal faqat o'sha guruh adminlari uchun.", show_alert=True)
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
