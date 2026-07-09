from __future__ import annotations

import logging
from aiogram import Bot
from app.db import Database
from app.utils import league_with_emoji

logger = logging.getLogger(__name__)


async def send_duel_transition_notifications(bot: Bot, db: Database, user_id: int, transition: dict) -> None:
    before = transition.get("before", {})
    after = transition.get("after", {})
    old_level = int(before.get("level", 1))
    new_level = int(after.get("level", old_level))
    level_up = bool(transition.get("level_up"))
    rank_up = bool(transition.get("league_promoted"))
    rank_down = bool(transition.get("league_demoted"))
    new_title = bool(transition.get("new_title"))
    new_title_name = str(after.get("title_name", "لقب جدید"))
    new_rank = league_with_emoji(str(after.get("league_name", "لیگ جدید")))

    try:
        if new_title:
            text = await db.get_setting(
                "new_title_message",
                "🏅 <b>لقب جدید!</b>\n━━━━━━━━━━━━━━\n✨ به {title} رسیدی ✨\n━━━━━━━━━━━━━━",
            )
            await bot.send_message(user_id, text.format(title=new_title_name, level=new_level, rank=new_rank))
        elif rank_up:
            text = await db.get_setting(
                "rank_up_message",
                "👑 <b>ترفیع رنک!</b>\n━━━━━━━━━━━━━━\n🏆 رسیدی به {rank}\n━━━━━━━━━━━━━━",
            )
            await bot.send_message(user_id, text.format(rank=new_rank, level=new_level))
        elif level_up:
            text = await db.get_setting(
                "level_up_message",
                "🎉 <b>لول‌آپ!</b>\n━━━━━━━━━━━━━━\n🚀 رسیدی به لول {level}\n━━━━━━━━━━━━━━",
            )
            await bot.send_message(user_id, text.format(level=new_level))
        elif rank_down:
            text = await db.get_setting(
                "rank_down_message",
                "📉 سقوط رنک\nرفتی تو {rank}\nولی هنوز وقت هست 💪",
            )
            await bot.send_message(user_id, text.format(rank=new_rank, level=new_level))
    except Exception:
        logger.exception("Duel transition notification failed for user=%s", user_id)


async def send_streak_notification(bot: Bot, user_id: int, reward: dict | None) -> None:
    if not reward:
        return
    try:
        day = int(reward.get("day", 0))
        coins = int(reward.get("coins", 0))
        balance = int(reward.get("balance", 0))
        await bot.send_message(user_id, f"🎁 کمک روزانه روز {day}\n<b>{coins} سکه</b> به حسابت اضافه شد\nموجودی فعلی <b>{balance} سکه</b>")
    except Exception:
        logger.exception("Daily aid notification failed")


async def send_quest_completed_notifications(bot: Bot, user_id: int, just_completed: list) -> None:
    if not just_completed:
        return
    try:
        for _ in just_completed:
            await bot.send_message(user_id, "🎯 تکمیل شد\nیکی از کوئست‌های امروزت رو زدی، برو از بخش کوئست روزانه جایزه‌ات رو بردار")
    except Exception:
        logger.exception("Quest completed notification failed")


async def send_quest_near_complete_notifications(bot: Bot, user_id: int, near_complete: list) -> None:
    if not near_complete:
        return
    try:
        from app.db import quest_reminder_line
        for q in near_complete:
            remaining = max(0, q["goal_count"] - q["progress"])
            line = quest_reminder_line(q["goal_type"], q["goal_count"], remaining)
            await bot.send_message(user_id, f"کوئست روزانه🎯\n{line}")
    except Exception:
        logger.exception("Quest near-complete notification failed")
