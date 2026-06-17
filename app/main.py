import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.config import get_settings
from app.db import Database
from app.logging_config import setup_logging
from app.handlers import common, duel, shop, questions, admin
from app.middlewares import ActiveDuelMenuGuardMiddleware

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    settings.ensure_data_dir()
    setup_logging(settings.log_level)

    db = Database(settings.database_path)
    await db.connect()
    await db.add_owner_admins(settings.owner_ids)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    bot_username = settings.bot_username or me.username or ""

    dp = Dispatcher(storage=MemoryStorage())
    guard = ActiveDuelMenuGuardMiddleware()
    dp.message.middleware(guard)
    dp.callback_query.middleware(guard)

    dp.workflow_data.update(
        db=db,
        bot_username=bot_username,
        admin_review_channel_id=settings.admin_review_channel_id,
        reports_channel_id=settings.reports_channel_id,
    )

    dp.include_router(common.router)
    dp.include_router(admin.router)
    dp.include_router(shop.router)
    dp.include_router(questions.router)
    dp.include_router(duel.router)

    await bot.set_my_commands([
        BotCommand(command="start", description="شروع"),
        BotCommand(command="admin", description="پنل ادمین"),
        BotCommand(command="guide", description="راهنمای کامندهای ادمین"),
        BotCommand(command="version", description="نمایش نسخه فعال"),
        BotCommand(command="sync_defaults", description="همگام‌سازی تنظیمات پیش‌فرض"),
        BotCommand(command="help", description="راهنما"),
        BotCommand(command="backup", description="دریافت بک‌آپ دیتابیس"),
        BotCommand(command="cancel", description="لغو عملیات جاری"),
    ])

    logger.info("Bot started as @%s", bot_username)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
