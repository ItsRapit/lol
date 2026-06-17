from __future__ import annotations

import asyncio
import logging
from aiogram import Bot
from app.db import Database
from app.utils import league_with_emoji, rank_with_emoji

logger = logging.getLogger(__name__)


async def animated_level_up(bot: Bot, user_id: int, level: int, rank: str) -> None:
    try:
        msg = await bot.send_message(user_id, "⬆️ داری لول آپ می‌کنی...")
        await asyncio.sleep(0.8)
        try:
            await msg.edit_text("⬆️⬆️ داری لول آپ می‌کنی...")
            await asyncio.sleep(0.8)
            await msg.edit_text(f"🎉 لول آپ! به سطح <b>{level}</b> رسیدی!\nرتبه‌ات: <b>{rank}</b>")
        except Exception:
            logger.exception("Level-up edit failed")
            await bot.send_message(user_id, f"🎉 لول آپ! به سطح <b>{level}</b> رسیدی!\nرتبه‌ات: <b>{rank}</b>")
    except Exception:
        logger.exception("Level-up notification failed")


async def animated_league_promotion(bot: Bot, user_id: int, league_name: str) -> None:
    try:
        msg = await bot.send_message(user_id, "🏆 داری ترفیع می‌گیری...")
        await asyncio.sleep(0.8)
        try:
            await msg.edit_text("🏆🏆 داری ترفیع می‌گیری...")
            await asyncio.sleep(0.8)
            await msg.edit_text(f"🥇 ترفیع! به <b>{league_name}</b> رسیدی!")
        except Exception:
            logger.exception("League promotion edit failed")
            await bot.send_message(user_id, f"🥇 ترفیع! به <b>{league_name}</b> رسیدی!")
    except Exception:
        logger.exception("League promotion notification failed")


async def league_demotion(bot: Bot, user_id: int, league_name: str) -> None:
    try:
        await bot.send_message(user_id, f"😔 این بار نشد...\nبه <b>{league_name}</b> برگشتی.\nولی هنوز وقت هست، بازم تلاش کن! 💪")
    except Exception:
        logger.exception("League demotion notification failed")


async def send_duel_transition_notifications(bot: Bot, db: Database, user_id: int, transition: dict) -> None:
    after = transition.get("after", {})
    if transition.get("level_up"):
        rank = await db.get_rank_title(int(after.get("level", 1)))
        await animated_level_up(bot, user_id, int(after.get("level", 1)), rank_with_emoji(rank))
        await asyncio.sleep(0.6)
    if transition.get("league_promoted"):
        await animated_league_promotion(bot, user_id, league_with_emoji(str(after.get("league_name", "لیگ جدید"))))
        await asyncio.sleep(0.6)
    if transition.get("league_demoted"):
        await league_demotion(bot, user_id, league_with_emoji(str(after.get("league_name", "لیگ پایین‌تر"))))


async def send_streak_notification(bot: Bot, user_id: int, reward: dict | None) -> None:
    if not reward:
        return
    try:
        day = int(reward.get("day", 0))
        coins = int(reward.get("coins", 0))
        balance = int(reward.get("balance", 0))
        await bot.send_message(
            user_id,
            f"🎁 کمک روزانه روز {day}: <b>{coins} سکه</b> به حسابت اضافه شد.\nموجودی فعلی: <b>{balance} سکه</b>",
        )
    except Exception:
        logger.exception("Daily aid notification failed")
