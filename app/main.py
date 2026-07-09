import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat

from app.config import get_settings
from app.db import Database
from app.logging_config import setup_logging
from app.handlers import common, duel, shop, questions, admin, group_quiz
from app.middlewares import ActiveDuelMenuGuardMiddleware
from app.scheduler import setup_scheduler

logger = logging.getLogger(__name__)

PUBLIC_COMMANDS = [
    BotCommand(command="start", description="شروع ربات"),
    BotCommand(command="help", description="راهنما"),
    BotCommand(command="cancel", description="لغو عملیات جاری"),
]

ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand(command="admin", description="پنل ادمین"),
    BotCommand(command="bulk", description="ثبت گروهی سوال"),
    BotCommand(command="maintenance", description="روشن/خاموش کردن حالت تعمیر"),
    BotCommand(command="deletequestions", description="حذف چند سوال با آیدی"),
    BotCommand(command="guide", description="راهنمای کامندهای ادمین"),
    BotCommand(command="user", description="جستجوی کاربر با آیدی"),
    BotCommand(command="version", description="نمایش نسخه فعال"),
    BotCommand(command="stats", description="آمار کامل ربات"),
    BotCommand(command="sync_defaults", description="همگام‌سازی تنظیمات پیش‌فرض"),
    BotCommand(command="migrate_xp_curve", description="اعمال منحنی XP جدید"),
    BotCommand(command="setlevel", description="تنظیم نام و ایموجی لول"),
    BotCommand(command="titles", description="مدیریت لقب‌ها"),
    BotCommand(command="deltitle", description="حذف لقب"),
    BotCommand(command="backup", description="دریافت بک‌آپ کامل دیتابیس"),
    BotCommand(command="backup_questions", description="بک‌آپ سوالات"),
    BotCommand(command="backup_users", description="بک‌آپ کاربران"),
    BotCommand(command="backup_settings", description="بک‌آپ تنظیمات"),
    BotCommand(command="upload_backup", description="آپلود و ذخیره فایل بک‌آپ روی Volume"),
]


async def setup_bot_commands(bot: Bot, db: Database) -> None:
    await bot.set_my_commands(PUBLIC_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in await db.all_admin_ids():
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            logger.exception("Could not set admin commands for %s", admin_id)


async def main() -> None:
    settings = get_settings()
    settings.ensure_data_dir()
    setup_logging(settings.log_level)

    db = Database(settings.database_path)
    await db.connect()
    await db.add_owner_admins(settings.owner_ids)
    if settings.reports_channel_id:
        await db.set_setting("reports_channel_id", str(settings.reports_channel_id))

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

    dp.include_router(group_quiz.router)
    dp.include_router(common.router)
    dp.include_router(admin.router)
    dp.include_router(shop.router)
    dp.include_router(questions.router)
    dp.include_router(duel.router)

    await setup_bot_commands(bot, db)

    logger.info("Bot started as @%s", bot_username)
    scheduler = setup_scheduler(bot, db)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
