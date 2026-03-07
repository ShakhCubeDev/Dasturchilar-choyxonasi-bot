from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatPermissions, Message, ReplyKeyboardRemove

from app.keyboards.common import (
    admin_reply_cancel_back_keyboard,
    admin_reply_confirm_cancel_keyboard,
    admin_reply_main_keyboard,
    admin_reply_on_off_keyboard,
    admin_reply_spam_actions_keyboard,
    group_admin_group_picker_keyboard,
    group_admin_panel_keyboard,
    registration_deeplink_keyboard,
)
from app.services.context import AppContext
from app.services.modes import MODE_DCH, MODE_OTHER
from app.utils.telegram_ops import with_retry

router = Router(name="admin")


def _is_admin(user_id: int, ctx: AppContext) -> bool:
    return user_id in ctx.settings.admin_ids


def _is_primary_admin(user_id: int, ctx: AppContext) -> bool:
    return bool(ctx.settings.admin_ids) and user_id == ctx.settings.admin_ids[0]


def _is_dev_admin(user_id: int, ctx: AppContext) -> bool:
    if ctx.settings.dev_admin_ids:
        return user_id in set(ctx.settings.dev_admin_ids)
    return bool(ctx.settings.admin_ids) and user_id == ctx.settings.admin_ids[0]


def _parse_mode(raw: str) -> str | None:
    v = raw.strip().lower()
    if v in {"dch", "special"}:
        return MODE_DCH
    if v in {"other", "others", "boshqa", "boshqa guruhlar"}:
        return MODE_OTHER
    return None


def _mode_label(mode: str) -> str:
    return "DCH" if mode == MODE_DCH else "Boshqa guruhlar"


def _parse_chat_id(raw: str) -> int | None:
    sign = -1 if raw.startswith("-") else 1
    digits = raw[1:] if sign == -1 else raw
    if not digits.isdigit():
        return None
    return sign * int(digits)


async def _is_group_admin(bot, group_chat_id: int, user_id: int) -> bool:
    try:
        member = await with_retry(lambda: bot.get_chat_member(group_chat_id, user_id))
    except Exception:
        return False
    if not member:
        return False
    return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


async def _can_reject_user(bot, ctx: AppContext, requester_id: int, group_chat_id: int) -> bool:
    if _is_admin(requester_id, ctx):
        return True
    grp = await ctx.groups.get_by_chat_id(group_chat_id)
    if grp and grp.owner_telegram_id == requester_id:
        return True
    return await _is_group_admin(bot, group_chat_id, requester_id)


async def _resolve_target_telegram_id(message: Message, raw: str) -> int | None:
    value = (raw or "").strip()
    if value.isdigit():
        return int(value)
    if value.startswith("@"):
        value = value[1:]
    if not value:
        return None
    try:
        chat = await with_retry(lambda: message.bot.get_chat("@" + value))
    except Exception:
        return None
    if not chat:
        return None
    return int(chat.id)


class AdminStates(StatesGroup):
    spam_mode_select = State()
    spam_mode_menu = State()
    spam_threshold_input = State()
    spam_timeout_input = State()
    spam_global_input = State()
    spam_unban_input = State()
    broadcast_text = State()
    broadcast_confirm = State()
    set_active_group = State()
    set_active_user = State()
    purge_user_input = State()
    group_panel_menu = State()
    group_panel_group_select = State()


def _panel_group_status_label(enabled: bool) -> str:
    return "YOQILGAN" if enabled else "O'CHIRILGAN"


def _parse_group_picker_value(raw: str) -> int | None:
    value = (raw or "").strip()
    if "|" not in value:
        return None
    return _parse_chat_id(value.rsplit("|", 1)[1].strip())


async def _list_panel_groups(bot, user_id: int, ctx: AppContext) -> list:
    groups = await ctx.groups.list_all_groups()
    result = []
    for group in groups:
        if group.chat_id == ctx.settings.special_group_id:
            continue
        if _is_admin(user_id, ctx) or group.owner_telegram_id == user_id:
            result.append(group)
            continue
        if await _is_group_admin(bot, group.chat_id, user_id):
            result.append(group)
    return result


async def _show_group_panel(message: Message, state: FSMContext, ctx: AppContext, note: str | None = None) -> None:
    groups = await _list_panel_groups(message.bot, message.from_user.id, ctx)
    if not groups:
        await state.clear()
        await message.answer(
            "Sizga biriktirilgan boshqariladigan guruh topilmadi. Avval botni guruhingizga qo'shib admin qiling.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    data = await state.get_data()
    selected_gid = data.get("group_panel_selected_gid")
    selected = next((group for group in groups if group.chat_id == selected_gid), groups[0])
    await state.set_state(AdminStates.group_panel_menu)
    await state.update_data(group_panel_selected_gid=selected.chat_id)

    lines = [
        "Kichik admin panel",
        "",
        f"Tanlangan guruh: {selected.title}",
        f"Group ID: {selected.chat_id}",
        f"Ro'yxatdan o'tish majburiyligi: {_panel_group_status_label(selected.registration_enabled)}",
        "",
        "Tugmalar orqali holatni almashtirishingiz mumkin.",
    ]
    if note:
        lines.extend(["", note])
    await message.answer("\n".join(lines), reply_markup=group_admin_panel_keyboard())


async def _apply_group_registration_toggle(message: Message, state: FSMContext, ctx: AppContext, enabled: bool) -> None:
    groups = await _list_panel_groups(message.bot, message.from_user.id, ctx)
    if not groups:
        await state.clear()
        await message.answer("Siz uchun boshqariladigan guruh topilmadi.", reply_markup=ReplyKeyboardRemove())
        return

    data = await state.get_data()
    selected_gid = data.get("group_panel_selected_gid")
    selected = next((group for group in groups if group.chat_id == selected_gid), None)
    if selected is None:
        await _show_group_panel(message, state, ctx, "Avval guruhni qayta tanlang.")
        return

    if not (_is_primary_admin(message.from_user.id, ctx) or selected.owner_telegram_id == message.from_user.id):
        if not await _is_group_admin(message.bot, selected.chat_id, message.from_user.id):
            await message.answer("Bu amal faqat tanlangan guruh adminlari uchun.")
            return

    await ctx.groups.set_registration_enabled(selected.chat_id, enabled)
    await _show_group_panel(
        message,
        state,
        ctx,
        f"{selected.title} uchun ro'yxatdan o'tish majburiyligi {_panel_group_status_label(enabled)} qilindi.",
    )


async def _unrestrict_in_group(bot, group_chat_id: int, telegram_id: int) -> bool:
    try:
        perms = ChatPermissions(can_send_messages=True)
        res = await with_retry(
            lambda: bot.restrict_chat_member(group_chat_id, telegram_id, permissions=perms, until_date=0)
        )
        return res is not None
    except TelegramBadRequest as exc:
        if "administrator" in str(exc).lower():
            return True
        return False
    except Exception:
        return False


async def _show_main_menu(message: Message, ctx: AppContext) -> None:
    await message.answer(
        "Admin panel",
        reply_markup=admin_reply_main_keyboard(_is_dev_admin(message.from_user.id, ctx)),
    )


async def _show_mode_panel(message: Message, ctx: AppContext, mode: str) -> None:
    cfg_dch = await ctx.spam.get_settings(MODE_DCH)
    cfg_other = await ctx.spam.get_settings(MODE_OTHER)
    text = (
        "Global spam sozlamalari\n"
        f"DCH: threshold={cfg_dch.vote_threshold}, timeout={cfg_dch.timeout_seconds} sec, global={'on' if cfg_dch.global_enabled else 'off'}\n"
        f"Boshqa guruhlar: threshold={cfg_other.vote_threshold}, timeout={cfg_other.timeout_seconds} sec, global={'on' if cfg_other.global_enabled else 'off'}"
    )
    await message.answer(text, reply_markup=admin_reply_spam_actions_keyboard())


async def _show_spam_list(message: Message, ctx: AppContext) -> None:
    total = await ctx.spam.count_global_spam_all()
    rows = await ctx.spam.list_global_spam_all(limit=50)
    if not rows:
        await message.answer("Global spam ro'yxati bo'sh.", reply_markup=admin_reply_spam_actions_keyboard())
        return
    lines = [f"Global spam ro'yxati (jami: {total}, oxirgi {len(rows)} ta):", ""]
    for idx, row in enumerate(rows, start=1):
        u = f"@{row['target_username']}" if row.get("target_username") else "-"
        g_title = row.get("source_group_title") or "-"
        g_user = f"@{row['source_group_username']}" if row.get("source_group_username") else "-"
        lines.append(
            f"{idx}. user_id={row['telegram_id']} user={u} | group_id={row['source_group_id'] or '-'} group={g_title} {g_user} | reason={row['reason'] or '-'}"
        )
    await message.answer("\n".join(lines), reply_markup=admin_reply_spam_actions_keyboard())


async def _run_unblock_all(bot, ctx: AppContext, progress_message: Message) -> tuple[int, int, int]:
    pairs = await ctx.users.list_group_user_pairs_by_status("active")
    pairs = [(gid, tid) for (gid, tid) in pairs if tid not in set(ctx.settings.admin_ids)]
    ok = 0
    fail = 0
    for idx, (gid, tid) in enumerate(pairs, start=1):
        res = await _unrestrict_in_group(bot, gid, tid)
        if res:
            ok += 1
        else:
            fail += 1
        if idx % 25 == 0:
            await progress_message.edit_text(f"Unblock: {idx}/{len(pairs)} | ok={ok} fail={fail}")
        await asyncio.sleep(0.03)
    return (len(pairs), ok, fail)


@router.message(Command("admin"), F.chat.type == "private")
async def admin_panel_cmd(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        await message.answer(await ctx.texts.t("uz", "admin_only"))
        return
    await state.clear()
    await _show_main_menu(message, ctx)


@router.message(Command("panel"), F.chat.type == "private")
async def group_panel_cmd(message: Message, state: FSMContext, ctx: AppContext) -> None:
    groups = await _list_panel_groups(message.bot, message.from_user.id, ctx)
    if not groups:
        if _is_admin(message.from_user.id, ctx):
            await state.clear()
            await _show_main_menu(message, ctx)
            return
        await message.answer(
            "Siz uchun kichik admin panel hali mavjud emas. Avval botni guruhingizga qo'shib admin qiling.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await state.clear()
    await state.set_state(AdminStates.group_panel_menu)
    await state.update_data(group_panel_selected_gid=groups[0].chat_id)
    await _show_group_panel(message, state, ctx)


@router.message(F.chat.type == "private", F.text == "Guruhlarim")
async def group_panel_groups(message: Message, state: FSMContext, ctx: AppContext) -> None:
    groups = await _list_panel_groups(message.bot, message.from_user.id, ctx)
    if not groups:
        return
    await state.set_state(AdminStates.group_panel_group_select)
    await message.answer(
        "Boshqarmoqchi bo'lgan guruhni tanlang:",
        reply_markup=group_admin_group_picker_keyboard([(group.chat_id, group.title) for group in groups]),
    )


@router.message(AdminStates.group_panel_group_select, F.chat.type == "private", F.text == "Guruh paneliga qaytish")
async def group_panel_group_back(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await _show_group_panel(message, state, ctx)


@router.message(AdminStates.group_panel_group_select, F.chat.type == "private")
async def group_panel_group_select(message: Message, state: FSMContext, ctx: AppContext) -> None:
    groups = await _list_panel_groups(message.bot, message.from_user.id, ctx)
    if not groups:
        await state.clear()
        await message.answer("Siz uchun boshqariladigan guruh topilmadi.", reply_markup=ReplyKeyboardRemove())
        return
    gid = _parse_group_picker_value(message.text or "")
    selected = next((group for group in groups if group.chat_id == gid), None)
    if selected is None:
        await message.answer(
            "Iltimos, ro'yxatdan bir guruhni tanlang.",
            reply_markup=group_admin_group_picker_keyboard([(group.chat_id, group.title) for group in groups]),
        )
        return
    await state.update_data(group_panel_selected_gid=selected.chat_id)
    await _show_group_panel(message, state, ctx, f"Tanlangan guruh: {selected.title}")


@router.message(F.chat.type == "private", F.text == "Tanlangan guruh holati")
async def group_panel_status(message: Message, state: FSMContext, ctx: AppContext) -> None:
    groups = await _list_panel_groups(message.bot, message.from_user.id, ctx)
    if not groups:
        return
    await _show_group_panel(message, state, ctx)


@router.message(F.chat.type == "private", F.text == "Majburiylikni yoqish")
async def group_panel_enable(message: Message, state: FSMContext, ctx: AppContext) -> None:
    groups = await _list_panel_groups(message.bot, message.from_user.id, ctx)
    if not groups:
        return
    await _apply_group_registration_toggle(message, state, ctx, True)


@router.message(F.chat.type == "private", F.text == "Majburiylikni o'chirish")
async def group_panel_disable(message: Message, state: FSMContext, ctx: AppContext) -> None:
    groups = await _list_panel_groups(message.bot, message.from_user.id, ctx)
    if not groups:
        return
    await _apply_group_registration_toggle(message, state, ctx, False)


@router.message(F.chat.type == "private", F.text == "Panelni yopish")
async def group_panel_close(message: Message, state: FSMContext, ctx: AppContext) -> None:
    groups = await _list_panel_groups(message.bot, message.from_user.id, ctx)
    if not groups:
        return
    await state.clear()
    await message.answer("Kichik admin panel yopildi. Qayta ochish uchun /panel yuboring.", reply_markup=ReplyKeyboardRemove())


@router.message(F.chat.type == "private", F.text == "Spam Boshqaruv")
async def spam_manage_entry(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    await state.clear()
    await state.set_state(AdminStates.spam_mode_menu)
    await _show_mode_panel(message, ctx, MODE_OTHER)


@router.message(AdminStates.spam_mode_select, F.chat.type == "private")
async def spam_mode_select(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    await state.set_state(AdminStates.spam_mode_menu)
    await _show_mode_panel(message, ctx, MODE_OTHER)


@router.message(AdminStates.spam_mode_menu, F.chat.type == "private")
async def spam_mode_menu(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    text = (message.text or "").strip()
    if text == "Orqaga":
        await state.clear()
        await _show_main_menu(message, ctx)
        return
    if text == "Threshold O'zgartirish":
        await state.set_state(AdminStates.spam_threshold_input)
        await message.answer("Yangi threshold kiriting (2..50). Ikkala mode uchun bir xil bo'ladi:", reply_markup=admin_reply_cancel_back_keyboard())
        return
    if text == "Timeout O'zgartirish":
        await state.set_state(AdminStates.spam_timeout_input)
        await message.answer("Yangi timeout kiriting (30..3600 sec). Ikkala mode uchun bir xil bo'ladi:", reply_markup=admin_reply_cancel_back_keyboard())
        return
    if text == "Global On/Off":
        await state.set_state(AdminStates.spam_global_input)
        await message.answer("Global spam holatini tanlang:", reply_markup=admin_reply_on_off_keyboard())
        return
    if text == "Spamdan Chiqarish":
        await state.set_state(AdminStates.spam_unban_input)
        await message.answer("Spamdan chiqarish uchun telegram_id yoki @username yuboring:", reply_markup=admin_reply_cancel_back_keyboard())
        return
    if text == "Holatni Ko'rish":
        await _show_mode_panel(message, ctx, MODE_OTHER)
        return
    if text == "Spam Ro'yxati":
        await _show_spam_list(message, ctx)
        return

    await message.answer("Menyudan tanlang.", reply_markup=admin_reply_spam_actions_keyboard())


@router.message(AdminStates.spam_threshold_input, F.chat.type == "private")
async def spam_threshold_input(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    text = (message.text or "").strip()
    if text in {"Bekor qilish", "Orqaga"}:
        await state.set_state(AdminStates.spam_mode_menu)
        await _show_mode_panel(message, ctx, MODE_OTHER)
        return
    if not text.isdigit():
        await message.answer("Faqat son kiriting (2..50).")
        return
    value = int(text)
    if value < 2 or value > 50:
        await message.answer("2..50 oralig'ida bo'lsin.")
        return
    await ctx.spam.set_threshold(MODE_DCH, value)
    await ctx.spam.set_threshold(MODE_OTHER, value)
    await state.set_state(AdminStates.spam_mode_menu)
    await message.answer(f"Global threshold: {value}", reply_markup=admin_reply_spam_actions_keyboard())


@router.message(AdminStates.spam_timeout_input, F.chat.type == "private")
async def spam_timeout_input(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    text = (message.text or "").strip()
    if text in {"Bekor qilish", "Orqaga"}:
        await state.set_state(AdminStates.spam_mode_menu)
        await _show_mode_panel(message, ctx, MODE_OTHER)
        return
    if not text.isdigit():
        await message.answer("Faqat son kiriting (30..3600).")
        return
    value = int(text)
    if value < 30 or value > 3600:
        await message.answer("30..3600 oralig'ida bo'lsin.")
        return
    await ctx.spam.set_timeout_seconds(MODE_DCH, value)
    await ctx.spam.set_timeout_seconds(MODE_OTHER, value)
    await state.set_state(AdminStates.spam_mode_menu)
    await message.answer(f"Global timeout: {value} sec", reply_markup=admin_reply_spam_actions_keyboard())


@router.message(AdminStates.spam_global_input, F.chat.type == "private")
async def spam_global_input(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    text = (message.text or "").strip()
    if text in {"Bekor qilish", "Orqaga"}:
        await state.set_state(AdminStates.spam_mode_menu)
        await _show_mode_panel(message, ctx, MODE_OTHER)
        return
    if text not in {"Yoqish", "O'chirish"}:
        await message.answer("Yoqish yoki O'chirishni tanlang.", reply_markup=admin_reply_on_off_keyboard())
        return
    enabled = text == "Yoqish"
    await ctx.spam.set_global_enabled(MODE_DCH, enabled)
    await ctx.spam.set_global_enabled(MODE_OTHER, enabled)
    await state.set_state(AdminStates.spam_mode_menu)
    await message.answer(
        f"Global spam: {'on' if enabled else 'off'}",
        reply_markup=admin_reply_spam_actions_keyboard(),
    )


@router.message(AdminStates.spam_unban_input, F.chat.type == "private")
async def spam_unban_input(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    text = (message.text or "").strip()
    if text in {"Bekor qilish", "Orqaga"}:
        await state.set_state(AdminStates.spam_mode_menu)
        await _show_mode_panel(message, ctx, MODE_OTHER)
        return
    tid = await _resolve_target_telegram_id(message, text)
    if tid is None:
        await message.answer("Topilmadi. Telegram ID yoki @username kiriting.")
        return
    ok = await ctx.spam.remove_global_spam_any(tid)
    await state.set_state(AdminStates.spam_mode_menu)
    await message.answer(
        f"Global unban {tid}: {'ok' if ok else 'not found'}",
        reply_markup=admin_reply_spam_actions_keyboard(),
    )


@router.message(F.chat.type == "private", F.text == "Barchani Unblock")
async def unblock_all_message(message: Message, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    progress = await message.answer("Unblock boshlandi... (active userlar)")
    total, ok, fail = await _run_unblock_all(message.bot, ctx, progress)
    await progress.edit_text(f"Unblock tugadi. Jami={total} | ok={ok} fail={fail}")


@router.callback_query(F.data == "admin:unblock_all")
async def unblock_all_callback(callback: CallbackQuery, ctx: AppContext) -> None:
    if not _is_admin(callback.from_user.id, ctx):
        await callback.answer(await ctx.texts.t("uz", "admin_only"), show_alert=True)
        return
    await callback.answer()
    if not callback.message:
        return
    progress = await callback.message.answer("Unblock boshlandi... (active userlar)")
    total, ok, fail = await _run_unblock_all(callback.bot, ctx, progress)
    await progress.edit_text(f"Unblock tugadi. Jami={total} | ok={ok} fail={fail}")


@router.message(F.chat.type == "private", F.text == "Broadcast")
async def broadcast_start_message(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    await state.clear()
    await state.set_state(AdminStates.broadcast_text)
    await message.answer("Barchaga yuboriladigan habar matnini yuboring.", reply_markup=admin_reply_cancel_back_keyboard())


@router.callback_query(F.data == "admin:broadcast")
async def broadcast_start_callback(callback: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(callback.from_user.id, ctx):
        await callback.answer(await ctx.texts.t("uz", "admin_only"), show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(AdminStates.broadcast_text)
    if callback.message:
        await callback.message.answer("Barchaga yuboriladigan habar matnini yuboring.", reply_markup=admin_reply_cancel_back_keyboard())


@router.message(AdminStates.broadcast_text, F.chat.type == "private")
async def broadcast_collect_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    text = (message.html_text or message.text or "").strip()
    if text in {"Bekor qilish", "Orqaga", "/cancel"}:
        await state.clear()
        await _show_main_menu(message, ctx)
        return
    if not text:
        await message.answer("Matn bosh. Qayta yuboring.")
        return
    await state.update_data(broadcast_text=text)
    await state.set_state(AdminStates.broadcast_confirm)
    await message.answer("Quyidagi habar yuborilsinmi?\n\n" + text, reply_markup=admin_reply_confirm_cancel_keyboard())


@router.message(AdminStates.broadcast_confirm, F.chat.type == "private")
async def broadcast_confirm_message(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    text = (message.text or "").strip()
    if text == "Bekor qilish":
        await state.clear()
        await _show_main_menu(message, ctx)
        return
    if text != "Yuborish":
        await message.answer("Yuborish yoki Bekor qilishni tanlang.", reply_markup=admin_reply_confirm_cancel_keyboard())
        return
    data = await state.get_data()
    payload = (data.get("broadcast_text") or "").strip()
    await state.clear()
    if not payload:
        await _show_main_menu(message, ctx)
        return
    ids = await ctx.users.list_telegram_ids_by_status("active")
    ok = 0
    fail = 0
    progress = await message.answer(f"Yuborish boshlandi... Jami active={len(ids)}")
    for idx, tid in enumerate(ids, start=1):
        try:
            res = await with_retry(lambda t=payload, to=tid: message.bot.send_message(to, t, disable_web_page_preview=True))
            if res is None:
                fail += 1
            else:
                ok += 1
        except Exception:
            fail += 1
        if idx % 25 == 0:
            await progress.edit_text(f"Yuborilmoqda: {idx}/{len(ids)} | ok={ok} fail={fail}")
        await asyncio.sleep(0.03)
    await progress.edit_text(f"Yuborish tugadi. Jami={len(ids)} | ok={ok} fail={fail}")
    await _show_main_menu(message, ctx)


@router.callback_query(AdminStates.broadcast_confirm, F.data == "admin:broadcast_cancel")
async def broadcast_cancel_callback(callback: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(callback.from_user.id, ctx):
        await callback.answer(await ctx.texts.t("uz", "admin_only"), show_alert=True)
        return
    await callback.answer()
    await state.clear()
    if callback.message:
        await callback.message.answer("Bekor qilindi.", reply_markup=admin_reply_main_keyboard(_is_dev_admin(callback.from_user.id, ctx)))


@router.callback_query(AdminStates.broadcast_confirm, F.data == "admin:broadcast_confirm")
async def broadcast_send_callback(callback: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(callback.from_user.id, ctx):
        await callback.answer(await ctx.texts.t("uz", "admin_only"), show_alert=True)
        return
    await callback.answer()
    data = await state.get_data()
    text = (data.get("broadcast_text") or "").strip()
    await state.clear()
    if not text:
        return
    ids = await ctx.users.list_telegram_ids_by_status("active")
    ok = 0
    fail = 0
    progress = None
    if callback.message:
        progress = await callback.message.answer(f"Yuborish boshlandi... Jami active={len(ids)}")
    for idx, tid in enumerate(ids, start=1):
        try:
            res = await with_retry(lambda t=text, to=tid: callback.bot.send_message(to, t, disable_web_page_preview=True))
            if res is None:
                fail += 1
            else:
                ok += 1
        except Exception:
            fail += 1
        if idx % 25 == 0 and progress:
            await progress.edit_text(f"Yuborilmoqda: {idx}/{len(ids)} | ok={ok} fail={fail}")
        await asyncio.sleep(0.03)
    if progress:
        await progress.edit_text(f"Yuborish tugadi. Jami={len(ids)} | ok={ok} fail={fail}")


@router.message(F.chat.type == "private", F.text == "Set Active")
async def set_active_start(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    await state.clear()
    await state.set_state(AdminStates.set_active_group)
    await message.answer("Group chat id yuboring:", reply_markup=admin_reply_cancel_back_keyboard())


@router.message(AdminStates.set_active_group, F.chat.type == "private")
async def set_active_group_input(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    text = (message.text or "").strip()
    if text in {"Bekor qilish", "Orqaga"}:
        await state.clear()
        await _show_main_menu(message, ctx)
        return
    gid = _parse_chat_id(text)
    if gid is None:
        await message.answer("Group ID noto'g'ri. Masalan: -1001234567890")
        return
    await state.update_data(set_active_group=gid)
    await state.set_state(AdminStates.set_active_user)
    await message.answer("Endi telegram_id yuboring:", reply_markup=admin_reply_cancel_back_keyboard())


@router.message(AdminStates.set_active_user, F.chat.type == "private")
async def set_active_user_input(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    text = (message.text or "").strip()
    if text in {"Bekor qilish", "Orqaga"}:
        await state.clear()
        await _show_main_menu(message, ctx)
        return
    if not text.isdigit():
        await message.answer("Telegram ID son bo'lishi kerak.")
        return
    tid = int(text)
    data = await state.get_data()
    gid = int(data.get("set_active_group"))
    try:
        updated = await ctx.users.update_status(gid, tid, "active")
    except Exception:
        ctx.logger.exception("db_error set_active group=%s telegram_id=%s", gid, tid)
        await message.answer(await ctx.texts.t("uz", "db_error"))
        return
    if not updated:
        await message.answer(await ctx.texts.t("uz", "reject_not_found"))
        return
    ok = await _unrestrict_in_group(message.bot, gid, tid)
    await state.clear()
    await message.answer(f"User {tid} in {gid} -> active; unblock={'ok' if ok else 'failed'}")
    await _show_main_menu(message, ctx)


@router.message(F.chat.type == "private", F.text == "Holat")
async def global_status(message: Message, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    cfg_dch = await ctx.spam.get_settings(MODE_DCH)
    cfg_other = await ctx.spam.get_settings(MODE_OTHER)
    text = (
        "Global spam holati\n\n"
        f"DCH: threshold={cfg_dch.vote_threshold}, timeout={cfg_dch.timeout_seconds}, global={'on' if cfg_dch.global_enabled else 'off'}\n"
        f"Boshqa guruhlar: threshold={cfg_other.vote_threshold}, timeout={cfg_other.timeout_seconds}, global={'on' if cfg_other.global_enabled else 'off'}"
    )
    await message.answer(text, reply_markup=admin_reply_main_keyboard(_is_dev_admin(message.from_user.id, ctx)))


@router.message(F.chat.type == "private", F.text == "Dev Admin")
async def dev_admin_info(message: Message, ctx: AppContext) -> None:
    if not _is_dev_admin(message.from_user.id, ctx):
        return
    await message.answer("Dev Admin: Spamdan chiqarish funksiyasi Spam Boshqaruv -> Spamdan Chiqarish orqali ishlaydi.")


@router.message(F.chat.type == "private", F.text == "Userni To'liq O'chirish")
async def purge_user_start(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_dev_admin(message.from_user.id, ctx):
        return
    await state.clear()
    await state.set_state(AdminStates.purge_user_input)
    await message.answer(
        "O'chirish uchun telegram_id yoki @username yuboring.\nDiqqat: barcha guruhlardagi registratsiya ma'lumotlari o'chadi.",
        reply_markup=admin_reply_cancel_back_keyboard(),
    )


@router.message(AdminStates.purge_user_input, F.chat.type == "private")
async def purge_user_input(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_dev_admin(message.from_user.id, ctx):
        return
    text = (message.text or "").strip()
    if text in {"Bekor qilish", "Orqaga"}:
        await state.clear()
        await _show_main_menu(message, ctx)
        return

    tid = await _resolve_target_telegram_id(message, text)
    if tid is None:
        await message.answer("Topilmadi. Telegram ID yoki @username kiriting.")
        return

    deleted_users = await ctx.users.delete_all_by_telegram_id(tid)
    deleted_gates = await ctx.gates.delete_all_for_user(tid)
    await state.clear()
    await message.answer(
        f"User {tid} bo'yicha tozalandi.\nusers: {deleted_users}\njoin_gates: {deleted_gates}",
        reply_markup=admin_reply_main_keyboard(True),
    )


@router.message(F.chat.type == "private", F.text == "Orqaga")
async def back_to_main(message: Message, state: FSMContext, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        return
    await state.clear()
    await _show_main_menu(message, ctx)


@router.callback_query(F.data.startswith("reject:"))
async def reject_user(callback: CallbackQuery, ctx: AppContext) -> None:
    requester_id = callback.from_user.id
    if not callback.message:
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    raw_group = parts[1]
    raw_user = parts[2]
    group_chat_id = _parse_chat_id(raw_group)
    if group_chat_id is None or not raw_user.isdigit():
        await callback.answer()
        return
    target_telegram_id = int(raw_user)
    if not await _can_reject_user(callback.bot, ctx, requester_id, group_chat_id):
        await callback.answer(await ctx.texts.t("uz", "admin_only"), show_alert=True)
        return

    try:
        updated = await ctx.users.update_status(group_chat_id, target_telegram_id, "rejected")
    except Exception:
        ctx.logger.exception("reject_action failed: group=%s telegram_id=%s", group_chat_id, target_telegram_id)
        await callback.answer(await ctx.texts.t("uz", "db_error"), show_alert=True)
        return

    if not updated:
        await callback.answer(await ctx.texts.t("uz", "reject_not_found"), show_alert=True)
        return

    ctx.logger.info("reject_action admin_id=%s group=%s telegram_id=%s", requester_id, group_chat_id, target_telegram_id)
    if callback.message.reply_markup:
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(await ctx.texts.t("uz", "reject_done"), show_alert=True)

    try:
        perms = ChatPermissions(can_send_messages=False)
        await with_retry(lambda: callback.bot.restrict_chat_member(group_chat_id, target_telegram_id, permissions=perms))
    except Exception:
        ctx.logger.exception("restrict_failed reject group=%s telegram_id=%s", group_chat_id, target_telegram_id)

    reject_text = await ctx.texts.t(updated.language, "admin_reject_sent")
    if "/start" not in reject_text:
        reject_text = reject_text.rstrip() + "\n\n/start reg_" + str(group_chat_id)
    if ctx.settings.bot_username:
        await with_retry(
            lambda: callback.bot.send_message(
                target_telegram_id,
                reject_text,
                reply_markup=registration_deeplink_keyboard(ctx.settings.bot_username, group_chat_id, updated.language),
            )
        )
    else:
        await with_retry(lambda: callback.bot.send_message(target_telegram_id, reject_text))


# Command fallback (keyboard is primary UX)
@router.message(Command("set_active"), F.chat.type == "private")
async def set_active_cmd(message: Message, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        await message.answer(await ctx.texts.t("uz", "admin_only"))
        return
    args = (message.text or "").split(maxsplit=2)
    if len(args) != 3:
        await message.answer("Usage: /set_active group_chat_id telegram_id")
        return
    gid = _parse_chat_id(args[1])
    if gid is None or not args[2].isdigit():
        await message.answer("Usage: /set_active group_chat_id telegram_id")
        return
    tid = int(args[2])
    updated = await ctx.users.update_status(gid, tid, "active")
    if not updated:
        await message.answer(await ctx.texts.t("uz", "reject_not_found"))
        return
    ok = await _unrestrict_in_group(message.bot, gid, tid)
    await message.answer(f"User {tid} in {gid} -> active; unblock={'ok' if ok else 'failed'}")


@router.message(Command("group_reg"), F.chat.type == "private")
async def group_registration_control_cmd(message: Message, ctx: AppContext) -> None:
    # This command is for group admins (not only global bot admins).
    args = (message.text or "").split(maxsplit=2)
    if len(args) != 3:
        await message.answer("Usage: /group_reg group_chat_id on|off")
        return
    gid = _parse_chat_id(args[1])
    if gid is None:
        await message.answer("Group ID noto'g'ri.")
        return
    mode = args[2].strip().lower()
    if mode not in {"on", "off"}:
        await message.answer("Faqat on yoki off.")
        return

    group_mode = _parse_mode("dch" if gid == ctx.settings.special_group_id else "other")
    if group_mode == MODE_DCH:
        await message.answer("DCH uchun registration doim yoqilgan.")
        return

    if _is_primary_admin(message.from_user.id, ctx):
        pass
    else:
        grp = await ctx.groups.get_by_chat_id(gid)
        if grp and grp.owner_telegram_id == message.from_user.id:
            pass
        elif not await _is_group_admin(message.bot, gid, message.from_user.id):
            await message.answer("Bu amal faqat Asosiy Admin, guruh egasi yoki o'sha guruh adminlari uchun.")
            return

    enabled = mode == "on"
    await ctx.groups.set_registration_enabled(gid, enabled)
    state_label = "YOQILDI" if enabled else "O'CHIRILDI"
    await message.answer(
        f"Group {gid} uchun ro'yxatdan o'tish: {state_label}\n"
        f"Qayta o'zgartirish: /group_reg {gid} {'off' if enabled else 'on'}"
    )


@router.message(Command("spam_list"), F.chat.type == "private")
async def spam_list_cmd(message: Message, ctx: AppContext) -> None:
    if not _is_admin(message.from_user.id, ctx):
        await message.answer(await ctx.texts.t("uz", "admin_only"))
        return
    await _show_spam_list(message, ctx)


@router.message(Command("purge_user"), F.chat.type == "private")
async def purge_user_cmd(message: Message, ctx: AppContext) -> None:
    if not _is_dev_admin(message.from_user.id, ctx):
        await message.answer("Bu amal faqat Dev Admin uchun.")
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) != 2:
        await message.answer("Usage: /purge_user telegram_id|@username")
        return
    tid = await _resolve_target_telegram_id(message, args[1].strip())
    if tid is None:
        await message.answer("Topilmadi. Telegram ID yoki @username kiriting.")
        return
    deleted_users = await ctx.users.delete_all_by_telegram_id(tid)
    deleted_gates = await ctx.gates.delete_all_for_user(tid)
    await message.answer(f"User {tid} tozalandi. users={deleted_users}, join_gates={deleted_gates}")
