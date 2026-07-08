import logging
import random

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db import Database

logger = logging.getLogger(__name__)

QUEST_REMINDER_LINES = [
    "امروز {n} تا کوئست منتظرتن، یه سر بزن 🎯",
    "هنوز وقت داری امروز کوئست‌هارو کامل کنی، بجنب 🎯",
    "یادت نره امروز بازی کنی، کوئست‌هات باز مونده",
]

INACTIVE_GIFT_LINES = [
    "چند وقته پیدات نیست، {coins} سکه گذاشتم تو حسابت، بیا یه بازی بزن 🎮",
    "دلمون برات تنگ شده بود، {coins} سکه هدیه گذاشتم جیبت، پاشو یه دوئلی بزن",
    "خیلی وقته سر نزدی، {coins} سکه بهت اضافه کردیم، همین الان بازی کن 🎮",
]

async def send_daily_quest_reminders(bot: Bot, db: Database) -> None:
    try:
        users = await db.users_with_incomplete_quests_today()
        for u in users:
            try:
                summary = await db.quest_summary_line(u["telegram_id"])
                if summary:
                    text = f"کوئست روزانه🎯\n{summary}"
                else:
                    text = random.choice(QUEST_REMINDER_LINES).format(n=3)
                await bot.send_message(u["telegram_id"], text)
            except TelegramForbiddenError:
                if u["started_pv"]:
                    await db.execute_write("UPDATE users SET is_blocked=1 WHERE telegram_id=?", (u["telegram_id"],))
            except TelegramBadRequest:
                continue
            except Exception:
                logger.exception("Failed sending quest reminder to %s", u["telegram_id"])
    except Exception:
        logger.exception("Daily quest reminder job failed")


async def send_inactive_user_gifts(bot: Bot, db: Database) -> None:
    try:
        coins = await db.get_int("weekly_reward_coins", 30)
        users = await db.inactive_users_for_gift(days=7)
        for u in users:
            try:
                await db.change_coins(u["telegram_id"], coins, "inactive_weekly_gift")
                await db.mark_inactive_gift_sent(u["telegram_id"])
                text = random.choice(INACTIVE_GIFT_LINES).format(coins=coins)
                await bot.send_message(u["telegram_id"], text)
            except TelegramForbiddenError:
                if u["started_pv"]:
                    await db.execute_write("UPDATE users SET is_blocked=1 WHERE telegram_id=?", (u["telegram_id"],))
            except TelegramBadRequest:
                continue
            except Exception:
                logger.exception("Failed sending inactivity gift to %s", u["telegram_id"])
    except Exception:
        logger.exception("Inactive user gift job failed")


def setup_scheduler(bot: Bot, db: Database) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Tehran")
    scheduler.add_job(send_daily_quest_reminders, CronTrigger(hour=10, minute=0), args=[bot, db], id="daily_quest_reminder", replace_existing=True)
    scheduler.add_job(send_inactive_user_gifts, CronTrigger(hour=11, minute=0), args=[bot, db], id="weekly_inactive_gift", replace_existing=True)
    scheduler.start()
    return scheduler
