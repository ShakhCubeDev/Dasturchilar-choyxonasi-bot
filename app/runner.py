from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import get_settings
from app.db import Database
from app.handlers import admin, group, monitoring, registration, spam
from app.middlewares.fsm_timeout import FSMTimeoutMiddleware
from app.repositories.groups import GroupRepository
from app.repositories.join_gates import JoinGateRepository
from app.repositories.multi import MultiGroupRepository, MultiJoinGateRepository, MultiUserRepository
from app.repositories.spam import SpamRepository
from app.repositories.texts import BotTextsRepository
from app.repositories.users import UserRepository
from app.services.cleanup import cleanup_watcher
from app.services.context import AppContext
from app.services.nsfw import OpenNSFWService
from app.services.spam_watcher import spam_poll_watcher
from app.services.texts import TextService
from app.storage.postgres import PostgresStorage
from app.text_defaults import TEXTS as DEFAULT_TEXTS
from app.utils.logging_setup import setup_logging


async def run_polling() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    logger = logging.getLogger("bot")

    db = Database(settings.database_url)
    await db.connect()
    await db.init_schema()

    special_db: Database | None = None
    if settings.special_database_url and settings.special_database_url.strip() and settings.special_database_url != settings.database_url:
        special_db = Database(settings.special_database_url)
        await special_db.connect()
        await special_db.init_schema()
        logger.info("Primary + special databases connected and schema initialized")
    else:
        logger.info("Primary database connected and schema initialized")

    primary_users = UserRepository(db.pool)
    primary_groups = GroupRepository(db.pool)
    primary_gates = JoinGateRepository(db.pool)

    special_users = UserRepository(special_db.pool) if special_db else None
    special_groups = GroupRepository(special_db.pool) if special_db else None
    special_gates = JoinGateRepository(special_db.pool) if special_db else None

    users = MultiUserRepository(primary_users, special_users, settings.special_group_id)
    groups = MultiGroupRepository(primary_groups, special_groups, settings.special_group_id)
    gates = MultiJoinGateRepository(primary_gates, special_gates, settings.special_group_id)
    spam_repo = SpamRepository(db.pool)

    texts_repo = BotTextsRepository(db.pool)
    await texts_repo.ensure_schema()
    await texts_repo.seed_defaults_if_missing(DEFAULT_TEXTS)

    def _has_cyrillic(value: str) -> bool:
        return any("\u0400" <= char <= "\u04FF" for char in value)

    # If existing DB texts still mention the old min age (14), upgrade the active texts.
    for lang in ("uz", "ru", "en"):
        for key in ("age_prompt", "age_invalid"):
            active = await texts_repo.get_active(lang, key)
            if not active:
                continue
            old = active.text or ""
            if "14" in old and "70" in old and "12" not in old:
                await texts_repo.ensure_active_text(lang, key, DEFAULT_TEXTS[lang][key])

    for lang in ("uz", "ru", "en"):
        for key in ("name_prompt", "name_invalid", "other_name_prompt", "other_name_invalid"):
            active = await texts_repo.get_active(lang, key)
            if not active:
                continue
            old = (active.text or "").lower()
            if any(token in old for token in ("optional", "ixtiyoriy", "neobyazatel", "необяз", "yuboring '-'", "send '-'")):
                await texts_repo.ensure_active_text(lang, key, DEFAULT_TEXTS[lang][key])

    for key in DEFAULT_TEXTS["ru"].keys():
        active = await texts_repo.get_active("ru", key)
        if not active:
            continue
        if not _has_cyrillic(active.text or ""):
            await texts_repo.ensure_active_text("ru", key, DEFAULT_TEXTS["ru"][key])

    texts = TextService(texts_repo)
    nsfw = OpenNSFWService(settings.nsfw_model_dir, settings.nsfw_profile_threshold, logger) if settings.nsfw_scan_on_join else None
    ctx = AppContext(settings=settings, users=users, groups=groups, gates=gates, spam=spam_repo, texts=texts, logger=logger, nsfw=nsfw)

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Persist FSM state in Postgres so registration doesn't break on restarts.
    await PostgresStorage.ensure_schema(db.pool)
    storage = PostgresStorage(db.pool)
    dp = Dispatcher(storage=storage, ctx=ctx)

    timeout_middleware = FSMTimeoutMiddleware(settings.registration_timeout_seconds)
    dp.message.middleware(timeout_middleware)
    dp.callback_query.middleware(timeout_middleware)

    dp.include_router(admin.router)
    dp.include_router(monitoring.router)
    dp.include_router(registration.router)
    dp.include_router(spam.router)
    dp.include_router(group.router)

    watcher_task = asyncio.create_task(spam_poll_watcher(ctx, bot))
    cleanup_task = asyncio.create_task(cleanup_watcher(db.pool, logger, interval_hours=24))
    try:
        await dp.start_polling(bot)
    finally:
        watcher_task.cancel()
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await watcher_task
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await cleanup_task
        if special_db:
            await special_db.disconnect()
        await db.disconnect()
        await bot.session.close()
