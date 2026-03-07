from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.keyboards.common import spam_vote_keyboard
from app.services.context import AppContext
from app.services.modes import group_mode
from app.utils.telegram_ops import safe_delete_message, with_retry

router = Router(name="spam")


async def _kick_user(bot, chat_id: int, telegram_id: int) -> None:
    await with_retry(lambda: bot.ban_chat_member(chat_id, telegram_id, revoke_messages=True))


def _poll_text(target_name: str, threshold: int, timeout_seconds: int) -> str:
    minutes = max(1, int(timeout_seconds // 60))
    return (
        "Ushbu foydalanuvchini bloklaymizmi?\n\n"
        f"Target: {target_name}\n"
        f"Threshold: {threshold} ta ovoz\n"
        f"Timeout: {minutes} daqiqa"
    )


async def _finalize_ban(ctx: AppContext, bot, poll_id: int, reason: str) -> None:
    poll = await ctx.spam.close_poll(poll_id, "closed", reason)
    if not poll:
        return

    target_username = None
    source_group_title = None
    source_group_username = None
    try:
        target_chat = await with_retry(lambda: bot.get_chat(poll.target_telegram_id))
        if target_chat and getattr(target_chat, "username", None):
            target_username = target_chat.username
    except Exception:
        pass
    try:
        group_chat = await with_retry(lambda: bot.get_chat(poll.group_chat_id))
        if group_chat:
            source_group_title = getattr(group_chat, "title", None)
            source_group_username = getattr(group_chat, "username", None)
    except Exception:
        pass

    await ctx.spam.add_global_spam(
        mode=poll.mode,
        telegram_id=poll.target_telegram_id,
        source_group_id=poll.group_chat_id,
        source_poll_id=poll.id,
        reason="community_vote",
        target_username=target_username,
        source_group_title=source_group_title,
        source_group_username=source_group_username,
    )
    try:
        await _kick_user(bot, poll.group_chat_id, poll.target_telegram_id)
    except Exception:
        ctx.logger.exception("spam_kick_failed group=%s user=%s", poll.group_chat_id, poll.target_telegram_id)

    if poll.message_id:
        try:
            await with_retry(
                lambda: bot.edit_message_text(
                    chat_id=poll.group_chat_id,
                    message_id=poll.message_id,
                    text=f"Qaror: BAN. User {poll.target_telegram_id} global spam bazaga qo'shildi.",
                )
            )
        except Exception:
            pass


async def _finalize_no_ban(ctx: AppContext, bot, poll_id: int, reason: str) -> None:
    poll = await ctx.spam.close_poll(poll_id, "closed", reason)
    if not poll:
        return
    if poll.message_id:
        await safe_delete_message(bot, poll.group_chat_id, poll.message_id)


@router.message(Command("ban"), F.chat.type.in_({"group", "supergroup"}))
async def start_spam_vote(message: Message, ctx: AppContext) -> None:
    if not message.from_user or not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("Spam user xabariga reply qilib /ban yozing.")
        return

    mode = group_mode(message.chat.id, ctx.settings)
    settings = await ctx.spam.get_settings(mode)
    if not settings.global_enabled:
        await message.reply("Global spam tizimi hozir o'chirilgan.")
        return

    target = message.reply_to_message.from_user
    if target.is_bot:
        await message.reply("Botlar uchun /ban ishlamaydi.")
        return
    if target.id == message.from_user.id:
        await message.reply("O'zingizga ovoz bera olmaysiz.")
        return

    if await ctx.spam.is_globally_banned_any(target.id):
        await safe_delete_message(message.bot, message.chat.id, message.reply_to_message.message_id)
        try:
            await _kick_user(message.bot, message.chat.id, target.id)
        except Exception:
            ctx.logger.exception("spam_kick_failed immediate group=%s user=%s", message.chat.id, target.id)
        await message.reply("Bu user allaqachon global spam bazada. Guruhdan chiqarildi.")
        return

    existing = await ctx.spam.get_open_poll(mode, message.chat.id, target.id)
    if existing:
        await message.reply("Bu user uchun allaqachon poll ochilgan.")
        return

    poll = await ctx.spam.create_poll(
        mode=mode,
        group_chat_id=message.chat.id,
        target_telegram_id=target.id,
        initiator_telegram_id=message.from_user.id,
        threshold=settings.vote_threshold,
        timeout_seconds=settings.timeout_seconds,
    )
    display = f"@{target.username}" if target.username else target.full_name
    sent = await message.reply(
        _poll_text(display, poll.threshold, settings.timeout_seconds),
        reply_markup=spam_vote_keyboard(poll.id, poll.yes_votes, poll.no_votes),
    )
    await ctx.spam.set_poll_message_id(poll.id, sent.message_id)


@router.callback_query(F.data.startswith("spamvote:"))
async def on_spam_vote(callback: CallbackQuery, ctx: AppContext) -> None:
    if not callback.from_user:
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        await callback.answer()
        return
    poll_id = int(parts[1])
    vote_yes = parts[2] == "yes"
    ok, reason, poll = await ctx.spam.register_vote(poll_id, callback.from_user.id, vote_yes)
    if not ok:
        mapping = {
            "poll_not_found": "Poll topilmadi.",
            "poll_closed": "Bu poll yopilgan.",
            "self_vote": "O'zingizga ovoz bera olmaysiz.",
            "already_voted": "Siz allaqachon ovoz bergansiz.",
        }
        await callback.answer(mapping.get(reason, "Xatolik"), show_alert=True)
        return

    if not poll:
        await callback.answer("Xatolik", show_alert=True)
        return

    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=spam_vote_keyboard(poll.id, poll.yes_votes, poll.no_votes))
        except Exception:
            pass
    await callback.answer("Ovoz qabul qilindi")

    if poll.yes_votes >= poll.threshold:
        await _finalize_ban(ctx, callback.bot, poll.id, "threshold_yes")
        return
    if poll.no_votes >= poll.threshold:
        await _finalize_no_ban(ctx, callback.bot, poll.id, "threshold_no")
        return


async def process_expired_spam_polls(ctx: AppContext, bot) -> int:
    polls = await ctx.spam.list_expired_open_polls(limit=100)
    processed = 0
    for poll in polls:
        processed += 1
        if poll.yes_votes > poll.no_votes:
            await _finalize_ban(ctx, bot, poll.id, "timeout_yes_majority")
        elif poll.no_votes > poll.yes_votes:
            await _finalize_no_ban(ctx, bot, poll.id, "timeout_no_majority")
        else:
            await _finalize_no_ban(ctx, bot, poll.id, "timeout_tie")
    return processed
