from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatPermissions, Message

from app.keyboards.common import registration_deeplink_keyboard
from app.services.context import AppContext
from app.services.modes import MODE_DCH, MODE_OTHER, group_mode
from app.utils.telegram_ops import safe_delete_message, with_retry

router = Router(name="group")


def _is_service_message(message: Message) -> bool:
    return any(
        [
            bool(message.new_chat_members),
            bool(message.left_chat_member),
            bool(message.group_chat_created),
            bool(message.supergroup_chat_created),
            bool(message.channel_chat_created),
            bool(message.pinned_message),
            bool(message.migrate_to_chat_id),
            bool(message.migrate_from_chat_id),
        ]
    )


def _user_ref(user_id: int, username: str | None, full_name: str) -> str:
    if username:
        return f"@{username}"
    return full_name


async def _send_group_registration_message(
    message: Message,
    ctx: AppContext,
    target_user_id: int,
    username: str | None,
    full_name: str,
    group_chat_id: int,
    group_lang: str,
    rejected: bool = False,
) -> int | None:
    mode = group_mode(group_chat_id, ctx.settings)
    group_title = message.chat.title or str(group_chat_id)
    user_ref = _user_ref(target_user_id, username, full_name)

    if rejected:
        intro = await ctx.texts.t(group_lang, "rejected_group", username=user_ref)
    elif mode == MODE_DCH:
        intro = await ctx.texts.t(group_lang, "dch_dm_register_intro", group_title=group_title, username=user_ref)
    else:
        intro = await ctx.texts.t(group_lang, "other_dm_register_intro", group_title=group_title, username=user_ref)

    fallback = await ctx.texts.t(group_lang, "not_registered_group", username=user_ref)
    text = (intro or fallback).strip()

    if not ctx.settings.bot_username:
        sent = await with_retry(lambda: message.bot.send_message(chat_id=group_chat_id, text=text))
        return int(sent.message_id) if sent else None

    sent = await with_retry(
        lambda: message.bot.send_message(
            chat_id=group_chat_id,
            text=text,
            reply_markup=registration_deeplink_keyboard(ctx.settings.bot_username, group_chat_id),
            disable_web_page_preview=True,
        )
    )
    return int(sent.message_id) if sent else None


def _delete_later(bot, chat_id: int, message_id: int, delay_seconds: int = 600) -> None:
    async def _job() -> None:
        await asyncio.sleep(delay_seconds)
        await safe_delete_message(bot, chat_id, message_id)

    asyncio.create_task(_job())


@router.message(F.chat.type.in_({"group", "supergroup"}), F.new_chat_members)
async def on_user_join(message: Message, ctx: AppContext) -> None:
    mode = group_mode(message.chat.id, ctx.settings)
    if mode == MODE_DCH:
        await _on_user_join_dch(message, ctx)
        return
    await _on_user_join_other_groups(message, ctx)


async def _on_user_join_dch(message: Message, ctx: AppContext) -> None:
    members = message.new_chat_members or []
    if not members:
        return

    group_chat_id = message.chat.id
    for member in members:
        if member.is_bot:
            continue
        if await ctx.spam.is_globally_banned_any(member.id):
            try:
                await _kick_user_immediate(message, group_chat_id, member.id)
            except Exception:
                ctx.logger.exception("global_spam_kick_failed join group=%s telegram_id=%s", group_chat_id, member.id)
            continue

        try:
            record = await ctx.users.get_by_group_and_telegram_id(group_chat_id, member.id)
        except Exception:
            ctx.logger.exception("db_error: join lookup failed group=%s telegram_id=%s", group_chat_id, member.id)
            continue

        if record and record.status == "active":
            continue

        await ctx.gates.mark(group_chat_id, member.id)

        try:
            perms = ChatPermissions(can_send_messages=False)
            await with_retry(lambda: message.bot.restrict_chat_member(group_chat_id, member.id, permissions=perms))
        except Exception:
            ctx.logger.exception("restrict_failed join group=%s telegram_id=%s", group_chat_id, member.id)

        lang = record.language if record else "uz"
        warn_message_id = await _send_group_registration_message(
            message,
            ctx,
            member.id,
            member.username,
            member.full_name,
            group_chat_id,
            lang,
            rejected=bool(record and record.status == "rejected"),
        )
        if warn_message_id:
            _delete_later(message.bot, group_chat_id, warn_message_id, 600)
        await safe_delete_message(message.bot, group_chat_id, message.message_id)


async def _on_user_join_other_groups(message: Message, ctx: AppContext) -> None:
    # Isolated flow for "Boshqa guruhlar" so future changes don't touch DCH behavior.
    members = message.new_chat_members or []
    if not members:
        return

    group_chat_id = message.chat.id
    group_info = await ctx.groups.get_by_chat_id(group_chat_id)
    registration_enabled = group_info.registration_enabled if group_info else True
    for member in members:
        if member.is_bot:
            continue
        if await ctx.spam.is_globally_banned_any(member.id):
            try:
                await _kick_user_immediate(message, group_chat_id, member.id)
            except Exception:
                ctx.logger.exception("global_spam_kick_failed join group=%s telegram_id=%s", group_chat_id, member.id)
            continue
        if not registration_enabled:
            continue

        try:
            record = await ctx.users.get_by_group_and_telegram_id(group_chat_id, member.id)
        except Exception:
            ctx.logger.exception("db_error: join lookup failed group=%s telegram_id=%s", group_chat_id, member.id)
            continue

        if record and record.status == "active":
            continue

        await ctx.gates.mark(group_chat_id, member.id)
        try:
            perms = ChatPermissions(can_send_messages=False)
            await with_retry(lambda: message.bot.restrict_chat_member(group_chat_id, member.id, permissions=perms))
        except Exception:
            ctx.logger.exception("restrict_failed join group=%s telegram_id=%s", group_chat_id, member.id)

        lang = record.language if record else "uz"
        warn_message_id = await _send_group_registration_message(
            message,
            ctx,
            member.id,
            member.username,
            member.full_name,
            group_chat_id,
            lang,
            rejected=bool(record and record.status == "rejected"),
        )
        if warn_message_id:
            _delete_later(message.bot, group_chat_id, warn_message_id, 600)
        await safe_delete_message(message.bot, group_chat_id, message.message_id)


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def group_moderation(message: Message, ctx: AppContext) -> None:
    mode = group_mode(message.chat.id, ctx.settings)
    if mode == MODE_DCH:
        await _group_moderation_dch(message, ctx)
        return
    await _group_moderation_other_groups(message, ctx)


async def _group_moderation_dch(message: Message, ctx: AppContext) -> None:
    user = message.from_user
    if not user or user.is_bot:
        return
    if _is_service_message(message):
        return

    group_chat_id = message.chat.id
    if await ctx.spam.is_globally_banned_any(user.id):
        await safe_delete_message(message.bot, message.chat.id, message.message_id)
        try:
            await _kick_user_immediate(message, group_chat_id, user.id)
        except Exception:
            ctx.logger.exception("global_spam_kick_failed msg group=%s telegram_id=%s", group_chat_id, user.id)
        return
    try:
        record = await ctx.users.get_by_group_and_telegram_id(group_chat_id, user.id)
    except Exception:
        ctx.logger.exception("db_error: group moderation lookup failed")
        return
    if record and record.username != user.username:
        await ctx.users.update_username(group_chat_id, user.id, user.username)
    return


async def _group_moderation_other_groups(message: Message, ctx: AppContext) -> None:
    # Isolated flow for "Boshqa guruhlar" so future changes don't touch DCH behavior.
    user = message.from_user
    if not user or user.is_bot:
        return
    if _is_service_message(message):
        return

    group_chat_id = message.chat.id
    group_info = await ctx.groups.get_by_chat_id(group_chat_id)
    registration_enabled = group_info.registration_enabled if group_info else True
    if await ctx.spam.is_globally_banned_any(user.id):
        await safe_delete_message(message.bot, message.chat.id, message.message_id)
        try:
            await _kick_user_immediate(message, group_chat_id, user.id)
        except Exception:
            ctx.logger.exception("global_spam_kick_failed msg group=%s telegram_id=%s", group_chat_id, user.id)
        return
    if not registration_enabled:
        return
    try:
        record = await ctx.users.get_by_group_and_telegram_id(group_chat_id, user.id)
    except Exception:
        ctx.logger.exception("db_error: group moderation lookup failed")
        return
    if record and record.username != user.username:
        await ctx.users.update_username(group_chat_id, user.id, user.username)
    return


async def _kick_user_immediate(message: Message, group_chat_id: int, user_id: int) -> None:
    await with_retry(lambda: message.bot.ban_chat_member(group_chat_id, user_id, revoke_messages=True))
